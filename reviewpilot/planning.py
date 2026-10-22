"""Planner logic.

* plan_review   - pull request -> tool tasks with dependencies (phase 1 facts in parallel, phase 2 test impact, phase 3 full suite)
* plan_request  - free-form developer request (Developer Workbench) -> tool tasks, rule-based and deterministic
* schedule      - topological layering of tasks into phases (tasks in one phase are independent and may run in parallel)
"""
from __future__ import annotations

import re

from .workspace import base_files, list_repos

TEST_FILE = re.compile(r"(^|/)test_[^/]*\.py$|(^|/)tests/")


def is_test_path(path: str) -> bool:
    return bool(TEST_FILE.search(path))


def task(tid, tool, args, purpose, depends_on=(), agent="ReviewWorkflow", resolver=None, condition=None):
    return {"id": tid, "tool": tool, "args": args, "purpose": purpose, "depends_on": list(depends_on), "agent": agent,
            "resolver": resolver, "condition": condition}


# ----------------------------------------------------------------------------------------------------------- review plan
def plan_review(pr: dict, full_suite: bool = True) -> list[dict]:
    """pr = get_pull_request output.  Phase 1 gathers facts in parallel; dependent test tasks are resolved at run time."""
    repo, pid = pr["repo"], pr["id"]
    files = pr["files"]
    alive = [f["path"] for f in files if f["status"] != "deleted"]
    py_alive = [p for p in alive if p.endswith(".py")]
    py_all = [f["path"] for f in files if f["path"].endswith(".py")]
    tasks = [task("T01", "get_diff", {"pr_id": pid}, "added and removed lines of every changed file", agent="DiffAnalyst")]
    n = 2
    for p in py_all:
        tasks.append(task(f"T{n:02d}", "compare_versions", {"pr_id": pid, "path": p}, f"functions, complexity and imports of {p}: main vs PR",
                          agent="QualityReviewer"))
        n += 1
    if alive:
        tasks.append(task(f"T{n:02d}", "scan_secrets", {"repo": repo, "pr_id": pid, "paths": alive}, "hard-coded credentials in changed files",
                          agent="SecurityReviewer"))
        n += 1
    if py_alive:
        tasks.append(task(f"T{n:02d}", "scan_security", {"repo": repo, "pr_id": pid, "paths": py_alive}, "dangerous calls in changed Python files",
                          agent="SecurityReviewer"))
        n += 1
        tasks.append(task(f"T{n:02d}", "lint", {"repo": repo, "pr_id": pid, "paths": py_alive}, "static checks of changed Python files",
                          agent="QualityReviewer"))
        n += 1
    compare_ids = [t["id"] for t in tasks if t["tool"] == "compare_versions"]
    if py_all:
        # phase 2: which tests exercise the changed functions (arguments resolved from the compare_versions results)
        tasks.append(task(f"T{n:02d}", "find_usages", {"repo": repo, "pr_id": pid, "symbol": "<changed public functions>"},
                          "tests that reference each changed public function", depends_on=compare_ids, agent="TestImpactAgent", resolver="usages_of_changed"))
        usage_id = f"T{n:02d}"
        n += 1
        tasks.append(task(f"T{n:02d}", "run_tests", {"repo": repo, "pr_id": pid, "targets": "<affected tests>"},
                          "run the tests affected by the change", depends_on=[usage_id], agent="TestImpactAgent", resolver="affected_tests"))
        affected_id = f"T{n:02d}"
        n += 1
        test_ids = [affected_id]
        if full_suite:
            tasks.append(task(f"T{n:02d}", "run_tests", {"repo": repo, "pr_id": pid, "targets": []}, "full test suite (only after the affected tests pass)",
                              depends_on=[affected_id], agent="TestImpactAgent", condition="affected_tests_passed"))
            test_ids.append(f"T{n:02d}")
            n += 1
        # phase 4/5: only when something failed - the same tests on the base, so failures that already existed are not blamed on the PR
        tasks.append(task(f"T{n:02d}", "run_tests", {"repo": repo, "pr_id": pid, "ref": "base", "targets": "<failing tests>"},
                          "re-run failing tests on the base branch (new failure or already broken?)", depends_on=test_ids, agent="TestImpactAgent",
                          resolver="baseline_tests", condition="head_tests_failed"))
    return tasks


# ----------------------------------------------------------------------------------------------------------- scheduling
def schedule(tasks: list[dict], dependency_aware: bool = True) -> list[list[dict]]:
    """Group tasks into phases.  Dependency-aware: a task runs in the first phase after all its dependencies.
    Without dependency awareness every task waits for the previous one (a linear chain)."""
    if not dependency_aware:
        return [[t] for t in tasks]
    level: dict[str, int] = {}
    remaining = {t["id"]: t for t in tasks}
    for _ in range(len(tasks) + 1):
        progressed = False
        for tid, t in list(remaining.items()):
            deps = [d for d in t["depends_on"] if d in {x["id"] for x in tasks}]
            if all(d in level for d in deps):
                level[tid] = 1 + max((level[d] for d in deps), default=-1)
                del remaining[tid]
                progressed = True
        if not progressed:
            break
    if remaining:
        raise ValueError("cyclic task dependencies: " + ", ".join(remaining))
    phases: list[list[dict]] = []
    for t in tasks:
        lv = level[t["id"]]
        while len(phases) <= lv:
            phases.append([])
        phases[lv].append(t)
    return phases


# ----------------------------------------------------------------------------------------------------------- workbench planner
PR_ID = re.compile(r"\b([A-Z]{3,4}-\d{1,3})\b")
PATHLIKE = re.compile(r"(?<![\w/.-])((?:\.{1,2}/|/)?[\w./\\-]*[\w-]+\.(?:py|md|txt|json|cfg|toml|ini))\b|((?:\.\./)+[\w./-]+)|(?<![\w.])(/(?:[\w.-]+/)*[\w.-]+)(?=\s|$|[,;?!])")
IDENT_AFTER = re.compile(r"(?:usages?|uses|references?|callers?|calls?) (?:of|to|for)\s+`?([A-Za-z_][A-Za-z0-9_.]*)`?|where (?:is|are) `?([A-Za-z_][A-Za-z0-9_]*)`? (?:used|called|referenced)|who calls `?([A-Za-z_][A-Za-z0-9_]*)", re.I)

INTENTS = [
    ("autofix", re.compile(r"(?i)\bauto[- ]?fix\b|\bfix (the )?(debug|unused imports?|imports|secrets?|bare except)")),
    ("review", re.compile(r"(?i)\breview\b.*\b(pr|pull request)\b|\b(pr|pull request)\b.*\breview\b|\bmerge[- ]ready")),
    ("diff", re.compile(r"(?i)\b(diff|what changed|changes in|changed files)\b")),
    ("pr_info", re.compile(r"(?i)\b(describe|details of|show|summari[sz]e)\b.*\b(pr|pull request)\b")),
    ("list", re.compile(r"(?i)\blist\b.*\bfiles?\b|\b(show|what are) (the |all )?files\b|\bproject (structure|files)\b|\bwhich files\b")),
    ("read", re.compile(r"(?i)\b(read|open|show me|display|print|cat|view)\b(?!.*\bfiles\b)")),
    ("analyze", re.compile(r"(?i)\banaly[sz]e\b|\bcomplexity\b|\bast\b|\bfunctions? (are )?in\b|\bclasses in\b|\binspect\b|\bhow many (functions|classes|methods)\b")),
    ("usages", IDENT_AFTER),
    ("search", re.compile(r"(?i)\bsearch\b|\bfind\b|\bgrep\b|\blook for\b|\blocate\b|\bdefinitions\b|\boccurrences\b|\bTODOs?\b")),
    ("secrets", re.compile(r"(?i)\bsecrets?\b|\bcredentials?\b|\bapi keys?\b|\btokens?\b leak|\bhard-?coded (keys?|passwords?)\b")),
    ("security", re.compile(r"(?i)\bsecurity\b|\bvulnerab|\bdangerous\b|\binjection\b|\bunsafe\b")),
    ("lint", re.compile(r"(?i)\blint\b|\bcode quality\b|\bcode smells?\b|\bunused imports?\b|\bstatic (analysis|checks?)\b")),
    ("tests", re.compile(r"(?i)\b(run|execute|launch)\b[^.]*\btests?\b|\btest suite\b|\bpytest\b|\bunit tests\b")),
]


def _resolve_path(token: str, files: list[str]) -> str:
    t = token.replace("\\", "/")
    if t in files or t.startswith(("../", "/")) or "/../" in t:
        return t
    hits = [f for f in files if f.endswith("/" + t) or f == t]
    return hits[0] if hits else t


GH_REPO_IN_TEXT = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b(?!\.\w)")


def _repo_for(text: str, paths: list[str], default: str | None) -> str | None:
    low = text.lower()
    from . import github as gh
    if default and gh.is_github_repo(default):
        m = GH_REPO_IN_TEXT.search(text)
        if m and gh.is_github_repo(m.group(1)) and not m.group(1).endswith((".py", ".md")) and "/" in m.group(1) \
                and not any(m.group(1) in p for p in paths):
            return m.group(1)
        return default                                    # a GitHub repository is selected: file names refer to it
    for r in list_repos():
        if re.search(rf"\b{re.escape(r)}\b", low):
            return r
    m = IDENT_AFTER.search(text)
    sym = next((g for g in m.groups() if g), None) if m else None
    if sym:
        short = sym.split(".")[-1]
        for r in list_repos():
            if any(re.search(rf"^\s*(def|class)\s+{re.escape(short)}\b", c, re.M) for c in base_files(r).values()):
                return r
    for r in list_repos():
        names = base_files(r)
        if any(p in names or any(n.endswith("/" + p) for n in names) for p in paths):
            return r
    return default


def _search_query(text: str) -> tuple[str, bool]:
    q = re.search(r"[\"'`]([^\"'`]+)[\"'`]", text)
    if q:
        return q.group(1), False
    low = text.lower()
    if "function definition" in low or "functions defined" in low or "all functions" in low:
        return r"^\s*(async\s+)?def\s+\w+", True
    if "class definition" in low or "classes" in low:
        return r"^\s*class\s+\w+", True
    if "todo" in low or "fixme" in low:
        return r"TODO|FIXME", True
    if "import" in low:
        return r"^\s*(import|from)\s+", True
    m = re.search(r"(?i)\b(?:for|of)\s+([A-Za-z_][A-Za-z0-9_.]*)\b", text)
    if m and m.group(1).lower() not in {"all", "the", "a", "an", "every"}:
        return m.group(1), False
    return "", False


# ----------------------------------------------------------------------------------------------------------- natural questions
STOPWORDS = set("""a an the this that these those is are was were be been being do does did can could should would will shall may might must have has had
what which who whom whose when where why how whether if then than so as of in on at to for from by with without about into over under between
and or not no yes any all each every some more most other such only own same too very just also still it its i me my we our you your they them
their there here please tell show give explain describe find look get let know want need like make use used using work works working
code repo repository project module modules file files function functions method methods class classes thing things""".split())
COVERAGE = re.compile(r"(?i)\b(covered|coverage|tested|untested|has tests?|have tests?)\b")
OVERVIEW = re.compile(r"(?i)\b(overview|summari[sz]e (this|the|my)|what is this|what does this (repo|repository|project)|about this (repo|repository|project)|"
                      r"project structure|architecture|high[- ]level)\b")
PR_LIST = re.compile(r"(?i)\b(open )?(prs|pull requests)\b")
DEF_RX = re.compile(r"(?m)^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")


def _def_span(source: str, name: str) -> tuple[int, int] | None:
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return node.lineno, node.end_lineno
    return None


def _natural_plan(text: str, repo: str, contents: dict[str, str], base: dict, pr_id: str) -> list[tuple[str, dict, str]]:
    """Fallback for questions without an explicit command ('What does the pricing module do?', 'Is divide covered by tests?',
    'How is the discount calculated?').  Names mentioned in the question are matched against the repository (modules, functions,
    classes, words in the code) and turned into ordinary MCP tool calls - the same supervisor, policy and executor apply."""
    tokens = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text) if t.lower() not in STOPWORDS and len(t) > 2]
    py = {p: c for p, c in contents.items() if p.endswith(".py")}
    is_test = lambda p: bool(TEST_FILE.search(p))  # noqa: E731
    defined: dict[str, tuple[str, str]] = {}
    for p, c in sorted(py.items(), key=lambda kv: is_test(kv[0])):          # production code wins over tests
        for m in DEF_RX.finditer(c):
            defined.setdefault(m.group(1).lower(), (p, m.group(1)))
    stems: dict[str, str] = {}
    for p in sorted(contents, key=is_test):
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if stem and stem != "__init__" and not is_test(p):
            stems.setdefault(stem, p)
    plan: list[tuple[str, dict, str]] = []
    symbols = [defined[t.lower()] for t in tokens if t.lower() in defined]
    mods = [stems[t.lower()] for t in tokens if t.lower() in stems and t.lower() not in defined]
    if PR_LIST.search(text) and not pr_id:
        return [("list_pull_requests", {"repo": repo}, "open pull requests")]
    if OVERVIEW.search(text) and not symbols and not mods:
        plan.append(("list_files", dict(base), "list the project files"))
        readme = next((p for p in contents if p.lower() in ("readme.md", "readme.rst", "readme.txt")), None)
        if readme:
            plan.append(("read_file", base | {"path": readme}, f"read {readme}"))
        return plan
    if COVERAGE.search(text) and symbols:
        return [("find_usages", base | {"symbol": symbols[0][1]}, f"usages of {symbols[0][1]} (and whether tests use it)")]
    for path, name in symbols[:2]:
        span = _def_span(contents[path], name)
        args = base | {"path": path} | ({"start_line": span[0], "end_line": span[1]} if span else {})
        plan.append(("read_file", args, f"read {name} in {path}"))
    for path in dict.fromkeys(mods):
        plan.append(("read_file", base | {"path": path}, f"read {path}"))
    if plan:
        return plan[:3]
    # no module or symbol named: search the repository for the words of the question that occur in it
    corpus = "\n".join(contents.values()).lower()
    words = sorted({t.lower() for t in tokens if t.lower() in corpus}, key=len, reverse=True)[:3]
    if words:
        return [("search_code", base | {"query": "(?i)" + "|".join(re.escape(w) for w in words), "regex": True, "path_glob": "*"},
                 f"search for {', '.join(words)}")]
    return []


def plan_request(text: str, default_repo: str | None = None) -> dict:
    """Natural-language developer request -> {intent, repo, pr_id, tasks, notes}.  Unknown requests return no tasks."""
    notes: list[str] = []
    from . import github as gh
    raw_paths = [m.group(1) or m.group(2) or m.group(3) for m in PATHLIKE.finditer(text or "")]
    url = gh.URL_RE.search(text or "")
    if url:
        text = text.replace(url.group(0), "")
        raw_paths = [p for p in raw_paths if p not in url.group(0)]
    pr_m = PR_ID.search(text or "")
    pr_id = pr_m.group(1) if pr_m else ""
    repo = _repo_for(text or "", raw_paths, default_repo)
    if url:
        pr_id = f"{url.group(1)}#{url.group(2)}"
    else:
        num = re.search(r"(?:\bPR\s*|pull request\s*|(?<![\w/])#)(\d+)\b", text or "", re.I)
        if num and not pr_id and repo and gh.is_github_repo(repo):
            pr_id = f"{repo}#{num.group(1)}"
    if pr_id:
        try:
            from .workspace import load_pr
            repo = load_pr(pr_id)["repo"]
        except Exception:  # noqa: BLE001
            notes.append(f"pull request {pr_id} not found")
    contents = base_files(repo) if repo else {}
    files = list(contents)
    paths = [_resolve_path(p, files) for p in raw_paths]
    found = []
    for name, rx in INTENTS:
        m = rx.search(text or "")
        if m:
            found.append((m.start(), name))
    names = [n for _, n in sorted(found)]
    if "review" in names:
        return {"intent": "review", "repo": repo, "pr_id": pr_id, "tasks": [], "notes": notes + ["pull-request reviews run the full review workflow"]}
    if "read" in names and ("analyze" in names or "search" in names or "list" in names or "usages" in names) and not paths:
        names.remove("read")
    if "usages" in names and "search" in names:
        names.remove("search")
    if not repo:
        return {"intent": "unknown", "repo": None, "pr_id": pr_id, "tasks": [], "notes": notes + ["no repository could be identified"]}
    tasks, n, analyze_ids = [], 1, []

    def add(tool, args, purpose, deps=(), agent="Workbench"):
        nonlocal n
        tid = f"T{n:02d}"
        tasks.append(task(tid, tool, args, purpose, deps, agent=agent))
        n += 1
        return tid

    base = {"repo": repo} | ({"pr_id": pr_id} if pr_id else {})
    py_paths = [p for p in paths if p.endswith(".py")] or paths
    for name in names:
        if name == "list":
            add("list_files", dict(base), "list the project files")
        elif name == "read":
            for p in paths or []:
                add("read_file", base | {"path": p}, f"read {p}")
            if not paths:
                notes.append("read requested but no file named")
        elif name == "analyze":
            targets = py_paths or [f for f in files if f.endswith(".py") and not is_test_path(f)][:3]
            for p in targets:
                analyze_ids.append(add("analyze_python", base | {"path": p}, f"AST analysis of {p}"))
        elif name == "search":
            q, rx = _search_query(text)
            if q:
                add("search_code", base | {"query": q, "regex": rx}, f"search for {q!r}")
            else:
                notes.append("search requested but no search term found")
        elif name == "usages":
            m = IDENT_AFTER.search(text)
            sym = next(g for g in m.groups() if g)
            add("find_usages", base | {"symbol": sym}, f"usages of {sym}")
        elif name == "secrets":
            add("scan_secrets", dict(base), "scan for hard-coded secrets")
        elif name == "security":
            add("scan_security", dict(base), "scan for dangerous calls")
        elif name == "lint":
            add("lint", base | ({"paths": py_paths} if py_paths else {}), "static code checks")
        elif name == "tests":
            targets = []
            if re.search(r"(?i)\bits tests\b|\btests for\b|\btests of\b", text) and py_paths:
                for p in py_paths:
                    stem = p.split("/")[-1][:-3]
                    cand = [f for f in files if f.split("/")[-1] in (f"test_{stem}.py",)]
                    targets += cand
            add("run_tests", base | {"targets": sorted(set(targets))}, "run the automated tests" + (f" ({', '.join(targets)})" if targets else ""),
                deps=analyze_ids)
        elif name == "diff" and pr_id:
            add("get_diff", {"pr_id": pr_id}, f"diff of {pr_id}")
        elif name == "pr_info" and pr_id:
            add("get_pull_request", {"pr_id": pr_id}, f"details of {pr_id}")
        elif name == "autofix" and pr_id:
            rules = []
            low = text.lower()
            if "debug" in low or "print" in low:
                rules.append("QLT001")
            if "import" in low:
                rules.append("QLT004")
            if "except" in low:
                rules.append("QLT002")
            if "secret" in low:
                rules.append("SEC001")
            add("create_autofix_branch", {"pr_id": pr_id, "rules": rules or ["QLT001", "QLT004"]}, "write safe fixes to a review branch")
    if not tasks and repo:
        for tool, args, purpose in _natural_plan(text or "", repo, contents, base, pr_id):
            add(tool, args, purpose)
        if tasks:
            notes.append("no explicit command found - planned from the names in the question")
    if not tasks:
        notes.append("request not understood - try: 'Analyze calculator.py and run its tests'")
    return {"intent": "workbench" if tasks else "unknown", "repo": repo, "pr_id": pr_id, "tasks": tasks, "notes": notes, "intents": names}
