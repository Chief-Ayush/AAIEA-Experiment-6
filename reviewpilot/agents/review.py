"""Agents of the pull-request review workflow (LangGraph nodes).

IntakeGuard -> Planner -> Supervisor -> PolicyGuard -> Executor -> {DiffAnalyst, TestImpactAgent, SecurityReviewer,
QualityReviewer} -> Validator -> VerdictAgent -> HumanApproval -> (FixAgent) -> Reporter
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from langgraph.types import interrupt

from ..analysis import scan_injection
from ..config import PATHS, VERDICTS
from ..executor import execute
from ..planning import NO_UNIT_TESTS, RUNTIME_ARTIFACT, is_generated, is_test_path, plan_review
from ..rules import RULES, make_finding, sev_rank, verdict_for
from ..workspace import SecurityError, normalize_pr_id, repo_root, safe_project_path
from .base import agent, hub_of, settings_of, trace_of

PR_ID_FORMAT = re.compile(r"^[A-Z]{3,4}-\d{1,3}$|^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+$")
PATH_ARGS = ("path",)
EXEMPT_LINE_RULES = {"TST001", "TST002", "TST003", "QLT004", "SEC003", "DIF001", "DIF002"}


def _result(state: dict, tool: str) -> list[dict]:
    return [r for r in state.get("results", {}).values() if r["tool"] == tool]


def _data(state: dict, tool: str) -> list:
    out = []
    for r in _result(state, tool):
        for c in r.get("calls", []):
            if c.get("ok") and c.get("data") is not None:
                out.append(c["data"])
    return out


def _added(state: dict) -> dict[str, set[int]]:
    diffs = _data(state, "get_diff")
    if not diffs:
        return {}
    return {f["path"]: {x["line"] for x in f["added_lines"]} for f in diffs[0]["files"]}


def _added_text(state: dict) -> dict[str, dict[int, str]]:
    diffs = _data(state, "get_diff")
    if not diffs:
        return {}
    return {f["path"]: {x["line"]: x["text"] for x in f["added_lines"]} for f in diffs[0]["files"]}


# ======================================================================================================== 1. IntakeGuard
@agent("IntakeGuard")
def intake_guard(state: dict) -> dict:
    """Validates the request, fetches the pull request over MCP and screens its text for instructions aimed at the reviewer."""
    pid = normalize_pr_id(state.get("pr_id") or "")
    s = settings_of(state)
    if not PR_ID_FORMAT.match(pid):
        return {"status": "rejected", "guard": {"ok": False, "category": "INPUT",
                                                "reason": f"'{pid or '(empty)'}' is not a pull request (owner/repo#12, a GitHub PR link, or a sample id such as INV-03)"},
                "_summary": "rejected: malformed pull-request id"}
    res = hub_of(state).call("get_pull_request", {"pr_id": pid}, settings=s, trace=trace_of(state), agent="IntakeGuard", phase=0)
    if not res.ok:
        return {"status": "rejected", "guard": {"ok": False, "category": res.category, "reason": res.error},
                "_summary": f"rejected: {res.category} {res.error}"}
    pr = res.data
    text = "\n".join([pr["title"], pr["description"], *pr["commits"]])
    inj = scan_injection(text)
    path_problems = []
    for f in pr["files"]:
        try:
            safe_project_path(repo_root(pr["repo"]), f["path"])
        except (SecurityError, Exception) as exc:  # noqa: BLE001
            path_problems.append(f"{f['path']}: {exc}")
    if path_problems:
        return {"status": "rejected", "pr": pr, "guard": {"ok": False, "category": "SECURITY", "reason": "; ".join(path_problems), "injection": inj},
                "_summary": "rejected: changed file outside the repository"}
    guard = {"ok": True, "injection": inj, "quarantined": bool(inj),
             "note": "PR text treated as untrusted data; it is never used as an instruction" + (f" - {len(inj)} injection pattern(s) found" if inj else "")}
    return {"pr_id": pid, "pr": pr, "guard": guard, "status": "running",
            "_summary": f"{pid} '{pr['title']}' - {len(pr['files'])} files, +{pr['additions']}/-{pr['deletions']}" + (f", {len(inj)} injection hit(s)" if inj else "")}


# ======================================================================================================== 2. Planner
@agent("Planner")
def planner(state: dict) -> dict:
    s = settings_of(state)
    pr = state["pr"]
    tasks = plan_review(pr, full_suite=s.full_suite_after_affected)
    ctx = {"changed_test_files": [f["path"] for f in pr["files"] if f["status"] != "deleted" and is_test_path(f["path"]) and f["path"].endswith(".py")]}
    return {"plan": tasks, "plan_ctx": ctx,
            "_summary": f"{len(tasks)} tasks: " + ", ".join(sorted({t['tool'] for t in tasks}))}


# ======================================================================================================== 3. Supervisor
@agent("Supervisor")
def supervisor(state: dict) -> dict:
    """Checks the plan against a fresh tools/list: every tool must exist and every concrete argument set must match its input schema."""
    hub = hub_of(state)
    listed = {t["name"]: t for t in hub.discover()}
    checks, approved = [], []
    for t in state["plan"]:
        if t["tool"] not in listed:
            checks.append({"task": t["id"], "tool": t["tool"], "result": "rejected", "detail": "tool not offered by any connected MCP server"})
            continue
        if t.get("resolver"):
            checks.append({"task": t["id"], "tool": t["tool"], "result": "deferred", "detail": f"arguments resolved at run time ({t['resolver']}); validated before the call"})
            approved.append(t)
            continue
        problem = hub.validate_arguments(t["tool"], t["args"])
        if problem:
            checks.append({"task": t["id"], "tool": t["tool"], "result": "rejected", "detail": problem})
            continue
        checks.append({"task": t["id"], "tool": t["tool"], "result": "valid", "detail": f"{listed[t['tool']]['server']} server, schema ok"})
        approved.append(t)
    rejected = [c for c in checks if c["result"] == "rejected"]
    return {"plan": approved, "supervisor": {"checks": checks, "tools_listed": len(listed), "rejected": len(rejected)},
            "_summary": f"{len(listed)} tools listed; {len(approved)} tasks approved, {len(rejected)} rejected"}


# ======================================================================================================== 4. PolicyGuard
def policy_decide(task: dict, risk: str | None, settings, repo: str | None, approved_high: bool = False) -> tuple[str, str]:
    if risk is None:
        return "deny", "unknown tool"
    if settings.enforce_policy:
        args = task.get("args", {})
        paths = [args[k] for k in PATH_ARGS if isinstance(args.get(k), str)] + [p for p in args.get("paths", []) if isinstance(p, str)]
        if repo:
            for p in paths:
                try:
                    safe_project_path(repo_root(repo), p)
                except Exception as exc:  # noqa: BLE001
                    return "deny", f"path security: {exc}"
        for tgt in args.get("targets", []) if isinstance(args.get("targets"), list) else []:
            if ".." in tgt or not re.match(r"^[A-Za-z0-9_./:\[\]-]+$", tgt):
                return "deny", f"unsafe test target '{tgt}'"
    order = ["LOW", "MEDIUM", "HIGH"]
    if order.index(risk) >= order.index(settings.require_approval_for) and settings.enforce_policy:
        if approved_high:
            return "allow", f"{risk} risk - approved by a human"
        return "needs_approval", f"{risk} risk - requires human approval"
    if risk == "MEDIUM":
        return "allow", "MEDIUM risk - allowed with timeout protection and an isolated checkout"
    return "allow", "LOW risk - read-only"


@agent("PolicyGuard")
def policy_guard(state: dict) -> dict:
    s = settings_of(state)
    tools = hub_of(state).tools()
    decisions, denied = [], []
    for t in state["plan"]:
        info = tools.get(t["tool"])
        d, why = policy_decide(t, info.risk if info else None, s, state["pr"]["repo"])
        if d == "needs_approval":          # nothing in a review plan writes; a HIGH tool here would wait for the human gate
            d, why = "deny", why + " (not part of an automatic review)"
        decisions.append({"task": t["id"], "tool": t["tool"], "risk": info.risk if info else "?", "decision": d, "reason": why})
        if d == "deny":
            denied.append(t["id"])
    counts = {r: sum(1 for x in decisions if x["risk"] == r) for r in ("LOW", "MEDIUM", "HIGH")}
    return {"policy": {"decisions": decisions, "denied": denied, "risk_counts": counts},
            "_summary": f"LOW {counts['LOW']}, MEDIUM {counts['MEDIUM']}, HIGH {counts['HIGH']}; {len(denied)} denied"}


# ======================================================================================================== 5. Executor
@agent("Executor")
def executor(state: dict) -> dict:
    s = settings_of(state)
    hub = hub_of(state)
    out = hub.run(execute(hub, state["plan"], s, trace_of(state), dict(state.get("plan_ctx", {})), set(state["policy"]["denied"])))
    m = out["metrics"]
    return {"results": out["results"], "timeline": out["timeline"], "exec_metrics": m, "plan_ctx": out["context"],
            "_summary": f"{m['tool_calls']} MCP calls in {m['phases']} phases, {m['wall_ms']:.0f} ms wall (DOP {m['dop']}, speed-up x{m['speedup']})"}


# ======================================================================================================== 6a. DiffAnalyst
@agent("DiffAnalyst")
def diff_analyst(state: dict) -> dict:
    s = settings_of(state)
    diffs = _data(state, "get_diff")
    if not diffs:
        return {"diff_summary": {"available": False}, "_summary": "diff unavailable"}
    d = diffs[0]
    files = [{"path": f["path"], "status": f["status"], "additions": f["additions"], "deletions": f["deletions"],
              "kind": "test" if is_test_path(f["path"]) else ("code" if f["path"].endswith(".py") else "docs/other")} for f in d["files"]]
    size = d["additions"] + d["deletions"]
    bucket = "XS" if size < 10 else "S" if size < 50 else "M" if size < 200 else "L" if size < s.large_pr_lines else "XL"
    counted = [f for f in d["files"] if not is_generated(f["path"])]
    reviewable = sum(f["additions"] + f["deletions"] for f in counted)
    findings = []
    if reviewable > s.large_pr_lines:
        biggest = max(counted, key=lambda f: f["additions"] + f["deletions"])
        skipped = size - reviewable
        findings.append(make_finding("DIF001", biggest["path"], None, f"{reviewable} changed lines of code"
                                     + (f" (+{skipped} in generated files)" if skipped else "") + " - consider splitting the pull request"))
    artifacts = [f["path"] for f in d["files"] if f["status"] == "added" and RUNTIME_ARTIFACT.search(f["path"])]
    for path in artifacts[:5]:
        findings.append(make_finding("DIF002", path, None, "this looks like a generated or runtime file (cache, log, environment, editor data) - add it to .gitignore"))
    kinds = {f["kind"] for f in files}
    summary = {"available": True, "files": files, "additions": d["additions"], "deletions": d["deletions"], "size": bucket,
               "touches_tests": "test" in kinds, "code_only": kinds == {"code"}, "docs_only": kinds <= {"docs/other"}}
    return {"diff_summary": summary, "findings": findings,
            "_summary": f"{len(files)} files, +{d['additions']}/-{d['deletions']} (size {bucket})" + (", tests touched" if summary["touches_tests"] else "")}


# ======================================================================================================== 6b. TestImpactAgent
def _test_path(test_id: str, known: list[str]) -> str:
    cls = test_id.split("::")[0] if "::" in test_id else test_id        # a collection error is reported as just the module name
    parts = cls.split(".")
    for start in range(len(parts)):
        for i in range(len(parts), start, -1):
            cand = "/".join(parts[start:i]) + ".py"
            if cand in known:
                return cand
    return "/".join(parts) + ".py" if parts and parts[0] else (known[0] if known else cls)


@agent("TestImpactAgent")
def test_impact(state: dict) -> dict:
    findings = []
    pr = state["pr"]
    runs = [(r, r.get("data")) for r in _result(state, "run_tests")]
    affected = state.get("plan_ctx", {}).get("affected_tests", [])
    all_tests = sorted({*affected, *[f["path"] for f in pr["files"] if is_test_path(f["path"])]})
    from ..workspace import head_files
    known = sorted(p for p in head_files(pr["repo"], pr["id"]) if is_test_path(p) and not p.endswith(("__init__.py", "conftest.py")))
    test_runs = []
    failed_files = set()
    is_base = lambda r: (r.get("args") or {}).get("ref") == "base"  # noqa: E731
    base_fail, base_ok, base_error = set(), False, None
    for r, d in runs:
        if is_base(r) and r.get("ok") and d:
            base_ok = True
            base_fail = {f["test"] for f in d["failures"]}
            if d["status"] == "error":
                base_error = (d["failures"] or [{"message": ""}])[0]["message"]
    preexisting, env_problem = [], None
    for r, d in runs:
        label = "base" if is_base(r) else "PR"
        if r["status"] == "skipped":
            test_runs.append({"task": r["id"], "on": label, "status": "skipped", "reason": r.get("reason", ""), "targets": None})
            continue
        if not r.get("ok") or not d:
            test_runs.append({"task": r["id"], "on": label, "status": "error", "reason": r.get("error"), "targets": None})
            continue
        test_runs.append({"task": r["id"], "on": label, "status": d["status"], "targets": d["targets"] or "full suite", "passed": d["passed"],
                          "failed": d["failed"], "errors": d["errors"], "duration_s": d["duration_s"]})
        if is_base(r) or d["status"] not in ("failed", "error"):
            continue
        if d["status"] == "error" and base_error is not None:
            env_problem = base_error[:200]                       # collection fails on the base too: environment, not the PR
            continue
        for f in d["failures"]:
            if base_ok and f["test"] in base_fail:
                preexisting.append(f["test"])
                continue
            p = _test_path(f["test"], known)
            if p not in failed_files:
                failed_files.add(p)
                what = "cannot be imported or collected" if "::" not in f["test"] else "fails"
                findings.append(make_finding("TST001", p, None, f"{f['test'].lstrip(':')} {what} on the PR head" + (" (passes on the base)" if base_ok else "")
                                             + (f": {f['message'][:160]}" if what != "fails" else ""), f["message"][:200]))
    # removed / skipped tests
    for c in _data(state, "compare_versions"):
        if not is_test_path(c["path"]):
            continue
        lost = c["test_functions_base"] - c["test_functions_head"]
        skips = c["skip_markers_head"] - c["skip_markers_base"]
        if lost > 0 or skips > 0:
            msg = ", ".join(x for x in [f"{lost} test(s) removed" if lost > 0 else "", f"{skips} skip marker(s) added" if skips > 0 else ""] if x)
            findings.append(make_finding("TST002", c["path"], None, msg + " - coverage of existing behaviour drops"))
    # new or changed public functions that no test references
    usage = {}
    for r in _result(state, "find_usages"):
        for call in r.get("calls", []):
            if call.get("ok"):
                usage[call["data"]["symbol"]] = call["data"]
    for name, path, line in state.get("plan_ctx", {}).get("changed_functions", []):
        u = usage.get(name)
        if u is not None and not u["tested"] and not u.get("indirect") and path.endswith(".py") and not NO_UNIT_TESTS.search(path):
            findings.append(make_finding("TST003", path, line, f"{name}() is new or changed but no test references it"))
    tested = sum(1 for u in usage.values() if u["tested"] or u.get("indirect"))
    return {"test_report": {"runs": test_runs, "affected_tests": affected, "functions_checked": len(usage), "functions_tested": tested,
                            "known_test_files": all_tests, "preexisting_failures": sorted(set(preexisting)), "environment_problem": env_problem},
            "findings": findings,
            "_summary": f"{len(test_runs)} test run(s): " + ", ".join(f"{t['status']}" for t in test_runs) + f"; {tested}/{len(usage)} changed functions tested"}


# ======================================================================================================== 6c. SecurityReviewer
@agent("SecurityReviewer")
def security_reviewer(state: dict) -> dict:
    added = _added(state)
    findings = []
    for scan in _data(state, "scan_secrets"):
        for h in scan["findings"]:
            if h["line"] in added.get(h["path"], set()):
                if h.get("context", "code") == "code":
                    findings.append(make_finding("SEC001", h["path"], h["line"], f"{h['kind']} committed in source ({h['confidence']} confidence)",
                                                 f"{h['kind']}: {h['match']}"))
                else:
                    where = "a test, sample or docs file" if h["context"] == "fixture" else "a multi-line string (template or test data)"
                    findings.append(make_finding("SEC005", h["path"], h["line"], f"{h['kind']} inside {where} - check that it is not a real credential",
                                                 f"{h['kind']}: {h['match']}"))
    for scan in _data(state, "scan_security"):
        for h in scan["findings"]:
            if h["line"] in added.get(h["path"], set()):
                rule = "SEC004" if h["kind"] == "sql_injection" else "SEC002"
                findings.append(make_finding(rule, h["path"], h["line"], h["detail"], h["call"]))
    for hit in state.get("guard", {}).get("injection", []):
        findings.append(make_finding("SEC003", "PR description", None, f"{hit['kind'].replace('_', ' ')} in the pull-request text - ignored, flagged for a human",
                                     hit["text"]))
        break
    for path, lines in _added_text(state).items():
        for ln, text in lines.items():
            if "#" in text or '"""' in text or "'''" in text:
                hit = scan_injection(text)
                if hit:
                    findings.append(make_finding("SEC003", path, ln, "comment addressed to the reviewer - ignored, flagged for a human", hit[0]["text"]))
    by = {}
    for f in findings:
        by[f["rule"]] = by.get(f["rule"], 0) + 1
    return {"findings": findings, "_summary": ", ".join(f"{k} x{v}" for k, v in sorted(by.items())) or "no security issues on added lines"}


# ======================================================================================================== 6d. QualityReviewer
LINT_RULE = {"debug_call": "QLT001", "broad_except": "QLT002", "complexity": "QLT003", "missing_docstring": "QLT005", "todo": "QLT006",
             "mutable_default": "QLT007"}


@agent("QualityReviewer")
def quality_reviewer(state: dict) -> dict:
    added = _added(state)
    findings = []
    comps = {c["path"]: c for c in _data(state, "compare_versions")}
    for res in _data(state, "lint"):
        for i in res["issues"]:
            rule = LINT_RULE.get(i["rule"])
            if not rule:
                continue
            if i["line"] not in added.get(i["path"], set()):
                if rule == "QLT003":    # complexity counts if the function body changed and got more complex
                    c = comps.get(i["path"])
                    if not c or not any(f["line"] == i["line"] and f["delta"] > 0 for f in c["functions_changed"]):
                        continue
                else:
                    continue
            findings.append(make_finding(rule, i["path"], i["line"], i["message"]))
    for c in comps.values():
        for u in c["new_unused_imports"]:
            findings.append(make_finding("QLT004", c["path"], u["line"], f"'{u['name']}' imported but never used"))
    by = {}
    for f in findings:
        by[f["rule"]] = by.get(f["rule"], 0) + 1
    return {"findings": findings, "_summary": ", ".join(f"{k} x{v}" for k, v in sorted(by.items())) or "no quality issues on added lines"}


# ======================================================================================================== 7. Validator
@agent("Validator")
def validator(state: dict) -> dict:
    """Checks every tool result (status, output schema) and every finding (known rule, cites a changed file and an added line)."""
    tool_checks, gaps = [], []
    for r in state.get("results", {}).values():
        for c in r.get("calls", []):
            ok = c["ok"] and c.get("output_valid") is not False
            tool_checks.append({"task": c["task_id"], "tool": c["tool"], "status": "valid" if ok else ("schema mismatch" if c["ok"] else "error"),
                                "attempts": c["attempts"], "category": c.get("category")})
            if not ok:
                gaps.append(f"{c['tool']} ({c.get('category') or 'invalid output'})")
        if r["status"] == "denied":
            gaps.append(f"{r['tool']} denied by policy")
    pr = state["pr"]
    changed = {f["path"] for f in pr["files"]}
    added = _added(state)
    kept, dropped, seen = [], [], set()
    for f in state.get("findings", []):
        reason = None
        if f["rule"] not in RULES:
            reason = "unknown rule"
        elif f["path"] not in changed and not (f["rule"] == "SEC003" and f["path"] == "PR description") and f["rule"] != "TST001":
            reason = "cites a file outside the pull request"
        elif f["rule"] not in EXEMPT_LINE_RULES and f["line"] not in added.get(f["path"], set()):
            reason = "cites a line the pull request did not add"
        key = (f["rule"], f["path"], f["line"])
        if reason is None and key in seen:
            reason = "duplicate"
        if reason:
            dropped.append(f | {"dropped_because": reason})
            continue
        seen.add(key)
        kept.append(f)
    kept.sort(key=lambda f: (-sev_rank(f["severity"]), f["rule"], f["path"], f["line"] or 0))
    return {"validated": kept, "validation": {"tool_checks": tool_checks, "dropped": dropped, "gaps": gaps,
                                              "tools_valid": sum(1 for t in tool_checks if t["status"] == "valid"), "tools_total": len(tool_checks)},
            "_summary": f"{len(kept)} findings kept, {len(dropped)} dropped; {sum(1 for t in tool_checks if t['status'] == 'valid')}/{len(tool_checks)} tool results valid"}


# ======================================================================================================== 8. VerdictAgent
@agent("VerdictAgent")
def verdict_agent(state: dict) -> dict:
    findings = state.get("validated", [])
    verdict, score, top = verdict_for(findings)
    gaps = state.get("validation", {}).get("gaps", [])
    notes = []
    tr = state.get("test_report", {})
    if tr.get("environment_problem"):
        gaps = gaps + ["tests cannot run in this environment"]
        notes.append(f"the test suite does not start on the base either ({tr['environment_problem'][:120]}) - install the project's dependencies")
    if tr.get("preexisting_failures"):
        notes.append(f"{len(tr['preexisting_failures'])} test(s) already fail on the base branch and were not counted against this PR")
    test_gap = any("run_tests" in g or "tests cannot run" in g for g in gaps)
    if gaps:
        notes.append("incomplete evidence: " + ", ".join(gaps))
    if verdict == "APPROVE" and test_gap:
        verdict = "COMMENT"
        notes.append("tests could not be run, so the review does not approve")
    confidence = round(max(0.3, 1.0 - 0.15 * len(gaps)), 2)
    counts = {sev: sum(1 for f in findings if f["severity"] == sev) for sev in ("critical", "high", "medium", "low", "info")}
    head = {"APPROVE": "Looks good to merge.", "COMMENT": "Mergeable, with suggestions worth addressing.",
            "REQUEST_CHANGES": "Changes are needed before this can merge.", "BLOCK": "Do not merge - a critical problem must be fixed first."}[verdict]
    parts = [f"{n} {sev}" for sev, n in counts.items() if n]
    summary = head + (" Findings: " + ", ".join(parts) + "." if parts else " No findings.")
    fixable = sorted({f["rule"] for f in findings if f["autofix"]})
    return {"verdict": {"verdict": verdict, "score": score, "top_severity": top, "confidence": confidence, "counts": counts, "notes": notes,
                        "summary": summary, "fixable_rules": fixable},
            "_summary": f"{verdict} (score {score}, confidence {confidence})"}


# ======================================================================================================== 9. HumanApproval
@agent("HumanApproval")
def human_approval(state: dict) -> dict:
    """Human-in-the-loop gate.  manual: pauses the graph (LangGraph interrupt) until the reviewer decides.
    auto: the verdict is accepted, but nothing HIGH-risk (auto-fix) runs without a person.  off: no gate (evaluation only)."""
    s = settings_of(state)
    v = state["verdict"]
    if s.approval_mode == "manual":
        decision = interrupt({"pr_id": state["pr_id"], "verdict": v["verdict"], "score": v["score"], "summary": v["summary"],
                              "findings": state.get("validated", []), "fixable_rules": v["fixable_rules"],
                              "options": {"action": ["approve", "reject"], "verdict_override": VERDICTS, "dismiss": "finding indexes",
                                          "autofix": "list of rules (HIGH risk: writes a review branch)"}})
    elif s.approval_mode == "auto":
        decision = {"action": "approve", "by": "auto (no reviewer present)", "autofix": []}
    else:
        decision = {"action": "approve", "by": "gate disabled", "autofix": []}
    decision = dict(decision or {})
    action = decision.get("action", "approve")
    findings = list(state.get("validated", []))
    dismissed = []
    for idx in sorted(set(decision.get("dismiss", []) or []), reverse=True):
        if 0 <= idx < len(findings):
            dismissed.append(findings.pop(idx))
    final_verdict = v["verdict"]
    if dismissed:
        final_verdict = verdict_for(findings)[0]
    if decision.get("verdict_override") in VERDICTS:
        final_verdict = decision["verdict_override"]
    fix_rules = [r for r in (decision.get("autofix") or []) if r in v["fixable_rules"]]
    human = {"action": action, "by": decision.get("by", "reviewer"), "note": decision.get("note", ""), "final_verdict": final_verdict,
             "dismissed": dismissed, "autofix_approved": fix_rules, "overridden": final_verdict != v["verdict"],
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return {"human": human, "final_findings": findings,
            "_summary": f"{action} by {human['by']}; verdict {final_verdict}" + (f"; auto-fix approved for {', '.join(fix_rules)}" if fix_rules else "")}


def route_after_human(state: dict) -> str:
    h = state.get("human", {})
    if h.get("action") == "approve" and h.get("autofix_approved"):
        return "fix_agent"
    return "reporter"


# ======================================================================================================== 10. FixAgent
@agent("FixAgent")
def fix_agent(state: dict) -> dict:
    """Writes the approved safe fixes to a review branch (HIGH risk, approved by the human gate), re-runs the test suite on that
    branch, rolls the branch back if anything fails and - for GitHub pull requests - pushes the fix as a commit to the PR branch."""
    s = settings_of(state)
    hub, trace = hub_of(state), trace_of(state)
    rules = state["human"]["autofix_approved"]
    info = hub.tools().get("create_autofix_branch")
    decision, why = policy_decide({"args": {}}, info.risk if info else None, s, None, approved_high=True)
    out = {"rules": rules, "policy": {"tool": "create_autofix_branch", "risk": info.risk if info else "?", "decision": decision, "reason": why}}
    if decision != "allow":
        out["status"] = "denied"
        return {"fix": out, "_summary": f"denied: {why}"}
    r = hub.call("create_autofix_branch", {"pr_id": state["pr_id"], "rules": rules}, settings=s, trace=trace, agent="FixAgent", phase=90)
    if not r.ok:
        out |= {"status": "failed", "error": r.error}
        return {"fix": out, "_summary": f"branch failed: {r.error}"}
    out |= {"branch": r.data["branch"], "applied": r.data["applied"], "files_changed": r.data["files_changed"], "patch": r.data["patch"]}
    if not r.data["files_changed"]:
        out["status"] = "nothing_to_fix"
        return {"fix": out, "_summary": "no applicable fixes"}
    t = hub.call("run_tests", {"repo": state["pr"]["repo"], "pr_id": state["pr_id"], "ref": "autofix", "targets": []}, settings=s, trace=trace,
                 agent="FixAgent", phase=91)
    out["tests"] = t.data if t.ok else {"status": "error", "error": t.error}
    passed = t.ok and t.data["status"] in ("passed", "no_tests")
    pr_tests_failed = any(f["rule"] == "TST001" for f in state.get("final_findings", []))
    if passed and state["pr"].get("source") == "github":
        p = hub.call("push_autofix_commit", {"pr_id": state["pr_id"]}, settings=s, trace=trace, agent="FixAgent", phase=93)
        if p.ok:
            out |= {"status": "pushed", "commit": p.data["commit"], "commit_url": p.data["url"], "pushed_to": p.data["branch"]}
        else:
            out |= {"status": "push_failed", "error": p.error}
    elif passed or (pr_tests_failed and t.ok and t.data["status"] == "failed"):
        out["status"] = "created" if passed else "created_tests_still_failing"
    else:
        d = hub.call("delete_autofix_branch", {"pr_id": state["pr_id"]}, settings=s, trace=trace, agent="FixAgent", phase=92)
        out["status"] = "rolled_back"
        out["rollback"] = d.ok
    return {"fix": out, "_summary": f"{out['status']}: {len(out.get('applied', []))} fix(es) on {out.get('branch')}"}


# ======================================================================================================== 11. Reporter
@agent("Reporter")
def reporter(state: dict) -> dict:
    s = settings_of(state)
    hub, trace = hub_of(state), trace_of(state)
    status = state.get("status")
    posted = None
    if status == "rejected":
        report = {"trace_id": state["trace_id"], "pr_id": state.get("pr_id"), "status": "rejected", "guard": state.get("guard"),
                  "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        h = state.get("human", {})
        findings = state.get("final_findings", state.get("validated", []))
        verdict = h.get("final_verdict") or state["verdict"]["verdict"]
        if h.get("action") == "approve" and s.approval_mode != "off":
            comments = [{"path": f["path"], "line": f["line"] or 0, "rule": f["rule"], "severity": f["severity"], "message": f["message"]} for f in findings][:50]
            fx = state.get("fix") or {}
            body = state["verdict"]["summary"]
            if h.get("overridden"):
                body = (f"Reviewer decision: {verdict.replace('_', ' ').title()} (overrides the automated assessment "
                        f"{state['verdict']['verdict'].replace('_', ' ').title()}). Automated assessment: {body}")
            if fx.get("status") == "pushed":
                body += f" Safe fixes ({', '.join(fx['rules'])}) were pushed to this branch as commit {fx['commit'][:7]} after the tests passed."
            elif fx.get("status", "").startswith("created"):
                body += f" Safe fixes are on the review branch {fx['branch']}."
            if h.get("note"):
                body += f"\n\nReviewer note: {h['note']}"
            r = hub.call("post_review", {"pr_id": state["pr_id"], "verdict": verdict, "summary": body, "comments": comments}, settings=s, trace=trace,
                         agent="Reporter", phase=99)
            posted = r.data if r.ok else {"error": r.error}
        status = "completed" if h.get("action") == "approve" else "rejected_by_reviewer"
        report = {
            "trace_id": state["trace_id"], "pr_id": state["pr_id"], "repo": state["pr"]["repo"], "title": state["pr"]["title"], "status": status,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "settings": state.get("settings"),
            "guard": state.get("guard"), "plan": state.get("plan"), "supervisor": state.get("supervisor"), "policy": state.get("policy"),
            "execution": {"timeline": state.get("timeline"), "metrics": state.get("exec_metrics"),
                          "tasks": [{k: r.get(k) for k in ("id", "tool", "agent", "phase", "status", "duration_ms", "attempts", "error", "category", "reason", "task_ids")}
                                    for r in state.get("results", {}).values()]},
            "diff": state.get("diff_summary"), "tests": state.get("test_report"), "validation": state.get("validation"),
            "verdict": state.get("verdict"), "human": h, "final_verdict": verdict, "findings": findings, "fix": state.get("fix"), "posted": posted,
            "agents": state.get("steps", []),
        }
    path = None
    if trace.persist:                      # evaluation runs (persist=False) leave the user's logs untouched
        runs = PATHS.logs / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        path = runs / f"{state['trace_id']}.json"
        txt = json.dumps(report, indent=1, default=str)
        path.write_text(txt, encoding="utf-8")
        (PATHS.logs / "developer_automation_report.json").write_text(txt, encoding="utf-8")
        trace.event("report", path=path.relative_to(PATHS.root).as_posix(), status=status)
    return {"status": status, "report_path": str(path) if path else None, "posted": posted,
            "_summary": f"{status}; " + (f"report {path.name}" if path else "report not persisted (evaluation run)") + ("; review posted" if posted and "error" not in posted else "")}


def route_after_intake(state: dict) -> str:
    return "reporter" if state.get("status") == "rejected" else "planner"
