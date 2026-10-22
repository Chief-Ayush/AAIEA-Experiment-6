"""Pure analysis functions used by the MCP servers (AST analysis, diffing, lint, secret and security scanning).

Nothing here talks MCP; the servers wrap these functions as tools.  All results are plain JSON-serialisable dicts.
"""
from __future__ import annotations

import ast
import difflib
import io
import math
import re
import tokenize
from collections import Counter

# ----------------------------------------------------------------------------------------------------------- diff
def unified_diff(old: str | None, new: str | None, path: str) -> dict:
    a = (old or "").splitlines()
    b = (new or "").splitlines()
    status = "added" if old is None else ("deleted" if new is None else "modified")
    hunks, added, removed = [], [], []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for k in range(i1, i2):
            removed.append({"line": k + 1, "text": a[k]})
        for k in range(j1, j2):
            added.append({"line": k + 1, "text": b[k]})
    text = "\n".join(difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2))
    for h in re.split(r"(?m)^(?=@@)", text):
        if h.startswith("@@"):
            hunks.append(h.rstrip("\n"))
    return {"path": path, "status": status, "additions": len(added), "deletions": len(removed), "added_lines": added,
            "removed_lines": removed, "hunks": hunks, "patch": text}


# ----------------------------------------------------------------------------------------------------------- AST
_BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.AsyncWith, ast.IfExp, ast.Assert, ast.comprehension)


def cyclomatic_complexity(node: ast.AST) -> int:
    """McCabe-style complexity: 1 + decision points (branches, boolean operators, comprehension conditions)."""
    c = 1
    for n in ast.walk(node):
        if isinstance(n, _BRANCHES):
            c += 1
            if isinstance(n, ast.comprehension):
                c += len(n.ifs)
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
        elif isinstance(n, ast.match_case) if hasattr(ast, "match_case") else False:
            c += 1
    return c


def _mutable_default(d: ast.AST) -> bool:
    return isinstance(d, (ast.List, ast.Dict, ast.Set)) or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in {"list", "dict", "set"})


def _comments(source: str):
    """(line, text) of every real comment - '# TODO' inside a string literal is data, not a comment."""
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], tok.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        for i, line in enumerate(source.splitlines(), 1):
            j = line.find("#")
            if j >= 0:
                yield i, line[j:]


def analyze_source(source: str, path: str = "") -> dict:
    """Functions (with complexity, docstring, arguments), classes, imports, unused imports, TODOs and debug calls."""
    out = {"path": path, "lines": len(source.splitlines()), "functions": [], "classes": [], "imports": [], "unused_imports": [],
           "todos": [], "debug_calls": [], "syntax_error": None, "test_functions": 0, "skip_markers": 0}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        out["syntax_error"] = f"{exc.msg} (line {exc.lineno})"
        return out
    for i, comment in _comments(source):
        m = re.match(r"#\s*(TODO|FIXME|XXX)\b(.*)", comment)
        if m:
            out["todos"].append({"line": i, "text": (m.group(1) + m.group(2)).strip()})
    main_ranges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            main_ranges.append((node.lineno, node.end_lineno))

    def in_main(line):
        return any(a <= line <= b for a, b in main_ranges)

    def visit_funcs(body, cls=None):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{cls}.{node.name}" if cls else node.name
                args = [a.arg for a in node.args.args]
                defaults = node.args.defaults + [d for d in node.args.kw_defaults if d is not None]
                out["functions"].append({
                    "name": name, "short_name": node.name, "line": node.lineno, "end_line": node.end_lineno, "args": args,
                    "complexity": cyclomatic_complexity(node), "docstring": bool(ast.get_docstring(node)),
                    "is_public": not node.name.startswith("_"), "mutable_default": any(_mutable_default(d) for d in defaults),
                    "is_test": node.name.startswith("test_"),
                    "decorators": [ast.unparse(d) for d in node.decorator_list],
                })
                if node.name.startswith("test_"):
                    out["test_functions"] += 1
                    if any("skip" in ast.unparse(d) for d in node.decorator_list):
                        out["skip_markers"] += 1
            elif isinstance(node, ast.ClassDef):
                out["classes"].append({"name": node.name, "line": node.lineno, "end_line": node.end_lineno,
                                       "methods": sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in node.body)})
                visit_funcs(node.body, node.name)

    visit_funcs(tree.body)
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                nm = (a.asname or a.name).split(".")[0]
                imported[nm] = node.lineno
                out["imports"].append({"module": a.name, "name": nm, "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if node.module == "__future__":
                    continue
                nm = a.asname or a.name
                imported[nm] = node.lineno
                out["imports"].append({"module": f"{'.' * node.level}{node.module or ''}", "name": nm, "line": node.lineno})
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            exported |= {e.value for e in getattr(node.value, "elts", []) if isinstance(e, ast.Constant)}
    is_init = path.endswith("__init__.py") or path.endswith("conftest.py")
    for nm, line in imported.items():
        if nm not in used and nm not in exported and not is_init and nm != "*":
            out["unused_imports"].append({"name": nm, "line": line})
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.id if isinstance(f, ast.Name) else (f"{ast.unparse(f.value)}.{f.attr}" if isinstance(f, ast.Attribute) else "")
            is_script = bool(main_ranges)                     # a module with an `if __name__ == "__main__"` block is a script: print() is its output
            if fname in ("print", "breakpoint", "pdb.set_trace", "ipdb.set_trace") and not in_main(node.lineno) and not (fname == "print" and is_script):
                out["debug_calls"].append({"line": node.lineno, "call": fname})
    return out


def broad_excepts(source: str) -> list[dict]:
    """Bare `except:` and `except Exception:` handlers whose body only swallows the error (pass / return constant / continue)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            bare = node.type is None
            broad = isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
            swallow = all(isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Return) and (s.value is None or isinstance(s.value, (ast.Constant, ast.UnaryOp))))
                          for s in node.body)
            if bare or (broad and swallow):
                out.append({"line": node.lineno, "kind": "bare except" if bare else "broad except that swallows the error"})
    return out


def referenced_names(source: str) -> set[str]:
    """Every identifier referenced (names, attributes, imported names) - used to decide whether tests exercise a function."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {a.asname or a.name.split(".")[-1] for a in n.names}
    return names


# ----------------------------------------------------------------------------------------------------------- secrets
SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{40}['\"]")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("stripe_live_key", re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b")),
    ("pypi_token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
]
_GENERIC = re.compile(r"(?i)\b([A-Z0-9_]*(?:api_?key|secret|token|passw(?:or)?d|access_key)[A-Z0-9_]*)\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]")
PLACEHOLDER = re.compile(r"(?i)(your[-_ ]|example\.|changeme|placeholder|dummy|<[^>]+>|x{6,}|\*{4,}|fake|test[-_]?key|here$)")
DOC_KEY = re.compile(r"EXAMPLE|(?i:dummy|fake|placeholder|sample[-_]?key)")             # AKIAIOSFODNN7EXAMPLE and friends are documentation values
INTERPOLATION = re.compile(r"^\{.*\}$|^\$\{|^\$\(|^%[sd(]|^\{\{|^<.*>$")               # {TOKEN}, ${TOKEN}, %s: a template, not a value
FIXTURE_PATH = re.compile(r"(^|/)(tests?|fixtures?|mocks?|samples?|examples?|docs?|testdata|benchmark|__snapshots__)(/|$)|(^|/)(test_[^/]*|conftest\.py)$|\.(md|rst)$")


def _string_lines(source: str) -> set[int]:
    """Lines that lie inside a multi-line string literal (triple-quoted data, templates, docstrings) of a Python file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Constant, ast.JoinedStr)) and (isinstance(n, ast.JoinedStr) or isinstance(n.value, str)) \
                and getattr(n, "end_lineno", None) and n.end_lineno > n.lineno:
            out.update(range(n.lineno, n.end_lineno + 1))
    return out


def entropy(s: str) -> float:
    if not s:
        return 0.0
    c = Counter(s)
    return -sum(v / len(s) * math.log2(v / len(s)) for v in c.values())


def mask(value: str) -> str:
    return value[:4] + "*" * max(4, len(value) - 8) + value[-4:] if len(value) > 10 else "****"


def scan_secrets_text(text: str, path: str = "") -> list[dict]:
    """Hard-coded credentials.  Each hit carries a `context`: 'code' (a real assignment), 'fixture' (test / sample / docs file) or 'embedded'
    (inside a multi-line string literal: generated code, templates, test data) - only 'code' is a leaked secret."""
    out = []
    fixture = bool(FIXTURE_PATH.search(path.replace("\\", "/")))
    embedded = _string_lines(text) if path.endswith(".py") or not path else set()
    for i, line in enumerate(text.splitlines(), 1):
        ctx = "fixture" if fixture else ("embedded" if i in embedded else "code")
        hit = False
        for kind, rx in SECRET_PATTERNS:
            m = rx.search(line)
            if m:
                if not DOC_KEY.search(m.group(0)):
                    out.append({"line": i, "kind": kind, "match": mask(m.group(0)), "confidence": "high", "context": ctx})
                hit = True
                break
        if hit:
            continue
        m = _GENERIC.search(line)
        if m and not PLACEHOLDER.search(m.group(2)) and not INTERPOLATION.search(m.group(2)) and entropy(m.group(2)) >= 3.3:
            out.append({"line": i, "kind": f"generic secret ({m.group(1)})", "match": mask(m.group(2)), "confidence": "medium", "context": ctx})
    return out


# ----------------------------------------------------------------------------------------------------------- security
SQL = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\b(from|into|set|where)\b")


def scan_security_source(source: str) -> list[dict]:
    """Dangerous calls (code execution, shell, unsafe deserialisation) and SQL built from strings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else (f"{ast.unparse(f.value)}.{f.attr}" if isinstance(f, ast.Attribute) else "")
        kws = {k.arg: k.value for k in node.keywords if k.arg}
        issue = None
        if name in ("eval", "exec"):
            issue = ("code_execution", f"{name}() executes arbitrary code")
        elif name in ("os.system", "os.popen", "commands.getoutput"):
            issue = ("shell_command", f"{name}() runs a shell command built from a string")
        elif name.startswith("subprocess.") and isinstance(kws.get("shell"), ast.Constant) and kws["shell"].value is True:
            issue = ("shell_command", f"{name}(..., shell=True) allows shell injection")
        elif name in ("pickle.load", "pickle.loads", "marshal.loads", "dill.loads", "cPickle.loads"):
            issue = ("unsafe_deserialisation", f"{name}() can execute code from untrusted data")
        elif name == "yaml.load" and "Loader" not in kws:
            issue = ("unsafe_deserialisation", "yaml.load() without a safe Loader")
        elif name.endswith(".execute") or name.endswith(".executemany"):
            arg = node.args[0] if node.args else None
            built = isinstance(arg, ast.JoinedStr) or (isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Mod, ast.Add))) or \
                (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "format")
            if built and SQL.search(ast.unparse(arg)):
                issue = ("sql_injection", f"{name}() with SQL built by string formatting")
        if issue:
            out.append({"line": node.lineno, "kind": issue[0], "call": name, "detail": issue[1]})
    return out


# ----------------------------------------------------------------------------------------------------------- prompt injection in PR text
INJECTION_PATTERNS = [
    ("instruction_override", re.compile(r"(?i)\bignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|your|earlier)\s+(instructions|rules|review rules|guidelines)")),
    ("reviewer_targeting", re.compile(r"(?i)\b(ai|automated|llm|bot)\s+reviewer\b|\bnote to the (automated|ai) reviewer\b|^\s*(assistant|system)\s*:", re.M)),
    ("forced_verdict", re.compile(r"(?i)\b(approve this (pr|pull request|change)|mark (this|the) (pr|pull request) as approved|pre-?approved|must (approve|be approved))\b")),
    ("prompt_extraction", re.compile(r"(?i)\b(reveal|print|show)\s+(your\s+)?(system\s+prompt|hidden\s+rules|instructions)\b")),
]


def scan_injection(text: str) -> list[dict]:
    out = []
    for kind, rx in INJECTION_PATTERNS:
        m = rx.search(text or "")
        if m:
            out.append({"kind": kind, "text": m.group(0).strip()})
    return out


# ----------------------------------------------------------------------------------------------------------- who references what (indirect coverage)
_REF_CACHE: dict[tuple, dict] = {}


def reference_index(source: str) -> dict[str, set[str]]:
    """name -> the functions (or '<module>') that reference it.  Cached per file content."""
    key = (len(source), hash(source))
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    refs: dict[str, set[str]] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return refs

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            sc = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
            if isinstance(child, ast.Name):
                refs.setdefault(child.id, set()).add(scope)
            elif isinstance(child, ast.Attribute):
                refs.setdefault(child.attr, set()).add(scope)
            visit(child, sc)
    visit(tree, "<module>")
    if len(_REF_CACHE) > 2000:
        _REF_CACHE.clear()
    _REF_CACHE[key] = refs
    return refs


def module_parts(source: str) -> set[str]:
    """Module names a file imports (every dotted part) - 'from reviewpilot.config import X' -> {reviewpilot, config}."""
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            out.update(n.module.split("."))
        elif isinstance(n, ast.Import):
            for a in n.names:
                out.update(a.name.split("."))
    return out
