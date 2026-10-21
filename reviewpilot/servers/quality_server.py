"""MCP server: quality gates.

Tools: run_tests (MEDIUM - starts a pytest process, with timeout protection), lint, scan_secrets, scan_security (LOW).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from typing_extensions import TypedDict  # pydantic needs typing_extensions.TypedDict on Python < 3.12

from mcp.server.fastmcp import FastMCP

from ..analysis import analyze_source, broad_excepts, scan_secrets_text, scan_security_source
from ..config import PATHS
from ..workspace import InputError, SecurityError, base_files, env_for_subprocess, head_files, repo_root, safe_project_path
from .common import READ_ONLY, RUNS_PROCESS, risk_meta, run_blocking
from .fixes import branch_files

mcp = FastMCP("reviewpilot-quality", instructions="Runs tests and static checks on a repository or pull request.", log_level="WARNING")

SAFE_TARGET = re.compile(r"^[A-Za-z0-9_./-]+(::[A-Za-z0-9_\[\]-]+)*$")


class Failure(TypedDict):
    test: str
    message: str


class TestRun(TypedDict):
    repo: str
    ref: str
    targets: list[str]
    status: str
    collected: int
    passed: int
    failed: int
    errors: int
    skipped: int
    returncode: int
    duration_s: float
    failures: list[Failure]


class Issue(TypedDict):
    path: str
    line: int
    rule: str
    message: str


class LintResult(TypedDict):
    repo: str
    files: int
    count: int
    issues: list[Issue]


class SecretHit(TypedDict):
    path: str
    line: int
    kind: str
    match: str
    confidence: str


class SecretScan(TypedDict):
    repo: str
    files: int
    count: int
    findings: list[SecretHit]


class SecurityHit(TypedDict):
    path: str
    line: int
    kind: str
    call: str
    detail: str


class SecurityScan(TypedDict):
    repo: str
    files: int
    count: int
    findings: list[SecurityHit]


def _files(repo, pr_id, ref="head"):
    if not pr_id or ref == "base":
        return base_files(repo, pr_id or None)
    return branch_files(repo, pr_id) if ref == "autofix" else head_files(repo, pr_id)


def _select(repo, files, paths):
    if not paths:
        return {p: c for p, c in files.items() if p.endswith(".py")}
    out = {}
    for p in paths:
        safe_project_path(repo_root(repo), p)
        if p not in files:
            raise InputError(f"File not found: {p}")
        out[p] = files[p]
    return out


def _parse_junit(path):
    tree = ET.parse(path)
    cases, failures = 0, []
    counts = {"failed": 0, "errors": 0, "skipped": 0}
    for tc in tree.iter("testcase"):
        cases += 1
        name = f"{tc.get('classname', '')}::{tc.get('name')}"
        for tag, key in (("failure", "failed"), ("error", "errors"), ("skipped", "skipped")):
            el = tc.find(tag)
            if el is not None:
                counts[key] += 1
                if tag != "skipped":
                    failures.append({"test": name, "message": (el.get("message") or el.text or "")[:300]})
    return cases, counts, failures


def _run_tests(repo, pr_id, ref, targets, timeout_s):
    for t in targets:
        if not SAFE_TARGET.match(t) or ".." in t:
            raise SecurityError(f"unsafe test target '{t}'")
    if not 1 <= timeout_s <= 120:
        raise InputError("timeout_s must be between 1 and 120")
    wt = PATHS.worktrees / f"run-{uuid.uuid4().hex[:10]}"
    try:
        files = _files(repo, pr_id, ref)
        for p, c in files.items():
            dest = wt / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(c, encoding="utf-8")
        if not any(n in files for n in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")):
            (wt / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")     # pin rootdir to the checkout
        for t in targets:
            if not (wt / t.split("::")[0]).exists():
                raise InputError(f"test target not found: {t}")
        junit = wt / ".junit.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *targets]
        t0 = time.perf_counter()
        try:
            r = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=timeout_s, env=env_for_subprocess(), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"pytest exceeded {timeout_s}s") from exc
        dur = round(time.perf_counter() - t0, 3)
        cases, counts, failures = _parse_junit(junit) if junit.exists() else (0, {"failed": 0, "errors": 0, "skipped": 0}, [])
        if r.returncode == 5:
            status = "no_tests"
        elif r.returncode == 0:
            status = "passed"
        elif r.returncode == 1:
            status = "failed"
        else:
            status = "error"
            if not failures:
                failures = [{"test": "pytest", "message": (r.stdout + r.stderr)[-300:]}]
        passed = cases - counts["failed"] - counts["errors"] - counts["skipped"]
        return {"repo": repo, "ref": ref if pr_id else "base", "targets": targets, "status": status, "collected": cases, "passed": passed,
                "failed": counts["failed"], "errors": counts["errors"], "skipped": counts["skipped"], "returncode": r.returncode, "duration_s": dur,
                "failures": failures}
    finally:
        shutil.rmtree(wt, ignore_errors=True)


@mcp.tool(annotations=RUNS_PROCESS, meta=risk_meta("MEDIUM"))
async def run_tests(repo: str, pr_id: str = "", ref: str = "head", targets: list[str] = [], timeout_s: int = 30) -> TestRun:  # noqa: B006
    """Run pytest on a repository, a pull-request head or its auto-fix branch, in an isolated temporary checkout.
    `targets` are test files or node ids (e.g. tests/test_store.py::test_reserve); empty = the whole suite. Timeout protected."""
    return await run_blocking("run_tests", _run_tests, repo, pr_id, ref, list(targets), timeout_s)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def lint(repo: str, pr_id: str = "", paths: list[str] = []) -> LintResult:  # noqa: B006
    """Static checks: debug calls, unused imports, bare/broad excepts, mutable default arguments, TODOs, missing docstrings, complexity."""
    def work():
        sel = _select(repo, _files(repo, pr_id), list(paths))
        issues = []
        for p, c in sel.items():
            a = analyze_source(c, p)
            if a["syntax_error"]:
                issues.append({"path": p, "line": 1, "rule": "syntax_error", "message": a["syntax_error"]})
                continue
            test_file = p.split("/")[-1].startswith("test_")
            for d in a["debug_calls"]:
                if not test_file:
                    issues.append({"path": p, "line": d["line"], "rule": "debug_call", "message": f"{d['call']}() left in code"})
            for u in a["unused_imports"]:
                issues.append({"path": p, "line": u["line"], "rule": "unused_import", "message": f"'{u['name']}' imported but unused"})
            for e in broad_excepts(c):
                issues.append({"path": p, "line": e["line"], "rule": "broad_except", "message": e["kind"]})
            for f in a["functions"]:
                if f["mutable_default"]:
                    issues.append({"path": p, "line": f["line"], "rule": "mutable_default", "message": f"{f['name']} has a mutable default argument"})
                if f["is_public"] and not f["docstring"] and not f["is_test"] and not test_file:
                    issues.append({"path": p, "line": f["line"], "rule": "missing_docstring", "message": f"public function {f['name']} has no docstring"})
                if f["complexity"] > 10:
                    issues.append({"path": p, "line": f["line"], "rule": "complexity", "message": f"{f['name']} has cyclomatic complexity {f['complexity']}"})
            for t in a["todos"]:
                issues.append({"path": p, "line": t["line"], "rule": "todo", "message": t["text"]})
        return {"repo": repo, "files": len(sel), "count": len(issues), "issues": issues}
    return await run_blocking("lint", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def scan_secrets(repo: str, pr_id: str = "", paths: list[str] = []) -> SecretScan:  # noqa: B006
    """Detect hard-coded credentials (cloud keys, tokens, private keys, high-entropy password assignments). Values are masked."""
    def work():
        files = _files(repo, pr_id)
        sel = {p: c for p, c in files.items() if not paths or p in paths}
        for p in paths:
            safe_project_path(repo_root(repo), p)
        hits = [{"path": p, **h} for p, c in sel.items() for h in scan_secrets_text(c)]
        return {"repo": repo, "files": len(sel), "count": len(hits), "findings": hits}
    return await run_blocking("scan_secrets", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def scan_security(repo: str, pr_id: str = "", paths: list[str] = []) -> SecurityScan:  # noqa: B006
    """Detect dangerous calls: eval/exec, shell commands, unsafe deserialisation (pickle, yaml.load) and SQL built from strings."""
    def work():
        sel = _select(repo, _files(repo, pr_id), list(paths))
        hits = [{"path": p, **h} for p, c in sel.items() for h in scan_security_source(c)]
        return {"repo": repo, "files": len(sel), "count": len(hits), "findings": hits}
    return await run_blocking("scan_security", work)


if __name__ == "__main__":
    mcp.run()
