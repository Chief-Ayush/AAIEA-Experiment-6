"""MCP server: Python source analysis (LOW risk, read-only).

Tools: analyze_python (AST), compare_versions (base vs pull-request head), find_usages
"""
from __future__ import annotations

import ast
from typing_extensions import TypedDict  # pydantic needs typing_extensions.TypedDict on Python < 3.12

from mcp.server.fastmcp import FastMCP

from ..analysis import module_parts, reference_index,  analyze_source, referenced_names
from ..workspace import InputError, base_files, head_files, load_pr, repo_root, safe_project_path
from .common import READ_ONLY, risk_meta, run_blocking
from .fixes import branch_files

mcp = FastMCP("reviewpilot-analysis", instructions="AST-based analysis of Python source files.", log_level="WARNING")


class FunctionInfo(TypedDict):
    name: str
    short_name: str
    line: int
    end_line: int
    args: list[str]
    complexity: int
    docstring: bool
    is_public: bool
    mutable_default: bool
    is_test: bool
    decorators: list[str]


class ClassInfo(TypedDict):
    name: str
    line: int
    end_line: int
    methods: int


class Analysis(TypedDict):
    repo: str
    path: str
    ref: str
    lines: int
    syntax_error: str | None
    function_count: int
    class_count: int
    functions: list[FunctionInfo]
    classes: list[ClassInfo]
    imports: list[dict]
    unused_imports: list[dict]
    todos: list[dict]
    debug_calls: list[dict]
    max_complexity: int
    test_functions: int


class FunctionChange(TypedDict):
    name: str
    line: int
    complexity_base: int
    complexity_head: int
    delta: int
    is_public: bool
    docstring: bool
    mutable_default: bool


class Comparison(TypedDict):
    pr_id: str
    path: str
    status: str
    functions_added: list[FunctionChange]
    functions_removed: list[str]
    functions_changed: list[FunctionChange]
    new_unused_imports: list[dict]
    test_functions_base: int
    test_functions_head: int
    skip_markers_base: int
    skip_markers_head: int


class Usage(TypedDict):
    path: str
    line: int
    in_tests: bool


class Usages(TypedDict):
    repo: str
    symbol: str
    count: int
    tested: bool
    indirect: bool
    callers: list[str]
    test_files: list[str]
    usages: list[Usage]


def _source(repo, path, pr_id, ref):
    safe_project_path(repo_root(repo), path)
    files = base_files(repo, pr_id or None) if ref == "base" or not pr_id else (branch_files(repo, pr_id) if ref == "autofix" else head_files(repo, pr_id))
    if path not in files:
        raise InputError(f"File not found: {path}")
    if not path.endswith(".py"):
        raise InputError(f"{path} is not a Python file")
    return files[path]


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def analyze_python(repo: str, path: str, pr_id: str = "", ref: str = "head") -> Analysis:
    """AST analysis of one Python file: functions (complexity, docstring, arguments), classes, imports, unused imports, TODOs, debug calls."""
    def work():
        a = analyze_source(_source(repo, path, pr_id, ref), path)
        return {"repo": repo, "path": path, "ref": ref if pr_id else "base", "lines": a["lines"], "syntax_error": a["syntax_error"],
                "function_count": len(a["functions"]), "class_count": len(a["classes"]), "functions": a["functions"], "classes": a["classes"],
                "imports": a["imports"], "unused_imports": a["unused_imports"], "todos": a["todos"], "debug_calls": a["debug_calls"],
                "max_complexity": max((f["complexity"] for f in a["functions"]), default=0), "test_functions": a["test_functions"]}
    return await run_blocking("analyze_python", work)


def _fc(f, base_c):
    return {"name": f["name"], "line": f["line"], "complexity_base": base_c, "complexity_head": f["complexity"], "delta": f["complexity"] - base_c,
            "is_public": f["is_public"], "docstring": f["docstring"], "mutable_default": f["mutable_default"]}


def _body(src: str, name: str) -> str | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name.split(".")[-1]:
            return ast.dump(node)
    return None


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def compare_versions(pr_id: str, path: str) -> Comparison:
    """Compare a file between main and the pull-request head: functions added / removed / changed, complexity deltas,
    newly unused imports and the number of test functions and skip markers."""
    def work():
        pr = load_pr(pr_id)
        repo = pr["repo"]
        safe_project_path(repo_root(repo), path)
        base = base_files(repo, pr_id).get(path)
        head = head_files(repo, pr_id).get(path)
        if base is None and head is None:
            raise InputError(f"{path} exists neither on main nor in {pr_id}")
        ab = analyze_source(base or "", path) if base is not None else None
        ah = analyze_source(head or "", path) if head is not None else None
        bf = {f["name"]: f for f in (ab["functions"] if ab else [])}
        hf = {f["name"]: f for f in (ah["functions"] if ah else [])}
        added = [_fc(f, 0) for n, f in hf.items() if n not in bf]
        removed = [n for n in bf if n not in hf]
        changed = [_fc(f, bf[n]["complexity"]) for n, f in hf.items() if n in bf and _body(base, n) != _body(head, n)]
        base_unused = {u["name"] for u in (ab["unused_imports"] if ab else [])}
        new_unused = [u for u in (ah["unused_imports"] if ah else []) if u["name"] not in base_unused]
        return {"pr_id": pr_id, "path": path, "status": "added" if base is None else ("deleted" if head is None else "modified"),
                "functions_added": added, "functions_removed": removed, "functions_changed": changed, "new_unused_imports": new_unused,
                "test_functions_base": ab["test_functions"] if ab else 0, "test_functions_head": ah["test_functions"] if ah else 0,
                "skip_markers_base": ab["skip_markers"] if ab else 0, "skip_markers_head": ah["skip_markers"] if ah else 0}
    return await run_blocking("compare_versions", work)


def _indirect_coverage(name: str, files: list[tuple[str, str]]) -> tuple[bool, list[str]]:
    """Is `name` reached from code that tests exercise?  Follows callers up to four levels (a helper called by a tested function, or by
    module-level code of a module the tests import, is covered even if no test mentions its name)."""
    test_names: set[str] = set()
    prod: dict[str, set[tuple[str, str]]] = {}
    for p, c in files:
        if not p.endswith(".py"):
            continue
        if p.split("/")[-1].startswith("test_") or "/tests/" in f"/{p}":
            test_names |= referenced_names(c) | module_parts(c)
            continue
        for ref, scopes in reference_index(c).items():
            for sc in scopes:
                prod.setdefault(ref, set()).add((p, sc))
    seen, frontier, chain = {name}, {name}, []
    for _ in range(4):
        nxt = set()
        for n in frontier:
            for p, sc in sorted(prod.get(n, ())):
                stem = p.rsplit("/", 1)[-1][:-3]
                key = stem if sc == "<module>" else sc
                if key in test_names:
                    chain.append(f"{p}:{sc}")
                    return True, chain[:5]
                if sc != "<module>" and sc not in seen:
                    seen.add(sc)
                    nxt.add(sc)
                    chain.append(f"{p}:{sc}")
        frontier = nxt
        if not frontier:
            break
    return False, []


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def find_usages(repo: str, symbol: str, pr_id: str = "") -> Usages:
    """Where a function or class name is referenced (AST-based, ignores comments and strings); tells whether tests use it."""
    def work():
        if not symbol or not symbol.replace("_", "a").replace(".", "a").isalnum():
            raise InputError("symbol must be a Python identifier")
        name = symbol.split(".")[-1]
        usages, test_files = [], set()
        for p, c in sorted((head_files(repo, pr_id) if pr_id else base_files(repo)).items()):
            if not p.endswith(".py") or name not in c or name not in referenced_names(c):
                continue
            is_test = p.split("/")[-1].startswith("test_") or "/tests/" in f"/{p}"
            for i, line in enumerate(c.splitlines(), 1):
                stripped = line.split("#")[0]
                if name in stripped and not stripped.lstrip().startswith(("def ", "async def ", "class ")):
                    usages.append({"path": p, "line": i, "in_tests": is_test})
                    if is_test:
                        test_files.add(p)
        indirect, callers = False, []
        if not test_files:
            indirect, callers = _indirect_coverage(name, list((head_files(repo, pr_id) if pr_id else base_files(repo)).items()))
        return {"repo": repo, "symbol": symbol, "count": len(usages), "tested": bool(test_files), "indirect": indirect, "callers": callers,
                "test_files": sorted(test_files), "usages": usages[:200]}
    return await run_blocking("find_usages", work)


if __name__ == "__main__":
    mcp.run()
