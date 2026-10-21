"""Agents of the Developer Workbench workflow: a free-form developer request becomes MCP tool calls.

RequestGuard -> TaskPlanner -> Supervisor -> PolicyGuard (+ human approval for HIGH risk) -> Executor -> ResultValidator -> Summarizer
"""
from __future__ import annotations

import re

from langgraph.types import interrupt

from ..executor import execute
from ..planning import plan_request
from .base import agent, hub_of, settings_of, trace_of
from .review import policy_decide

DESTRUCTIVE = re.compile(r"(?i)\b(rm\s+-rf|delete|remove|drop|wipe|format|overwrite|chmod|sudo|push|deploy|install)\b")
MAX_LEN = 500


@agent("RequestGuard")
def request_guard(state: dict) -> dict:
    text = (state.get("request") or "").strip()
    if not text:
        return {"status": "rejected", "guard": {"ok": False, "category": "INPUT", "reason": "empty request"}, "_summary": "rejected: empty"}
    if len(text) > MAX_LEN:
        return {"status": "rejected", "guard": {"ok": False, "category": "INPUT", "reason": f"request longer than {MAX_LEN} characters"},
                "_summary": "rejected: too long"}
    m = DESTRUCTIVE.search(text)
    if m and not re.search(r"(?i)\bauto[- ]?fix\b", text):
        return {"status": "rejected", "guard": {"ok": False, "category": "SECURITY",
                                                "reason": f"'{m.group(0)}' is not something the workbench does - its tools read, analyse and test code"},
                "_summary": f"rejected: destructive verb '{m.group(0)}'"}
    return {"status": "running", "guard": {"ok": True}, "_summary": "request accepted"}


@agent("TaskPlanner")
def task_planner(state: dict) -> dict:
    p = plan_request(state["request"], state.get("default_repo"))
    if not p["tasks"]:
        return {"status": "not_understood", "plan": [], "plan_info": p, "_summary": "; ".join(p["notes"]) or "no plan"}
    return {"plan": p["tasks"], "plan_info": p, "repo": p["repo"],
            "_summary": f"intents {', '.join(p.get('intents', []))} -> {len(p['tasks'])} task(s) on {p['repo']}"}


@agent("Supervisor")
def wb_supervisor(state: dict) -> dict:
    hub = hub_of(state)
    listed = {t["name"]: t for t in hub.discover()}
    checks, ok = [], []
    for t in state["plan"]:
        if t["tool"] not in listed:
            checks.append({"task": t["id"], "tool": t["tool"], "result": "rejected", "detail": "unknown tool"})
            continue
        problem = hub.validate_arguments(t["tool"], t["args"])
        checks.append({"task": t["id"], "tool": t["tool"], "result": "rejected" if problem else "valid", "detail": problem or "schema ok"})
        if not problem:
            ok.append(t)
    return {"plan": ok, "supervisor": {"checks": checks, "tools_listed": len(listed)},
            "_summary": f"{len(ok)}/{len(checks)} tasks valid"}


@agent("PolicyGuard")
def wb_policy(state: dict) -> dict:
    s = settings_of(state)
    tools = hub_of(state).tools()
    decisions, denied, pending = [], [], []
    for t in state["plan"]:
        info = tools.get(t["tool"])
        d, why = policy_decide(t, info.risk if info else None, s, state.get("repo"))
        if d == "needs_approval":
            pending.append(t["id"])
        decisions.append({"task": t["id"], "tool": t["tool"], "risk": info.risk if info else "?", "decision": d, "reason": why})
        if d == "deny":
            denied.append(t["id"])
    if pending:
        if s.approval_mode == "manual":
            answer = interrupt({"kind": "tool_approval", "tasks": [d for d in decisions if d["task"] in pending],
                                "question": "These tool calls change files on a review branch. Allow them?"})
            allow = bool((answer or {}).get("approve"))
        else:
            allow = False
        for d in decisions:
            if d["task"] in pending:
                d["decision"] = "allow" if allow else "deny"
                d["reason"] += " - approved by a human" if allow else " - no human approval"
                if not allow:
                    denied.append(d["task"])
    return {"policy": {"decisions": decisions, "denied": denied},
            "_summary": f"{len(decisions) - len(denied)} allowed, {len(denied)} denied"}


@agent("Executor")
def wb_executor(state: dict) -> dict:
    s = settings_of(state)
    hub = hub_of(state)
    out = hub.run(execute(hub, state["plan"], s, trace_of(state), {}, set(state["policy"]["denied"])))
    m = out["metrics"]
    return {"results": out["results"], "timeline": out["timeline"], "exec_metrics": m,
            "_summary": f"{m['tool_calls']} calls, {m['phases']} phase(s), {m['wall_ms']:.0f} ms"}


@agent("ResultValidator")
def wb_validator(state: dict) -> dict:
    checks = []
    for r in state["results"].values():
        for c in r.get("calls", []):
            checks.append({"task": c["task_id"], "tool": c["tool"], "ok": c["ok"], "schema": c.get("output_valid"), "category": c.get("category"),
                           "attempts": c["attempts"]})
        if r["status"] in ("denied", "skipped"):
            checks.append({"task": r["id"], "tool": r["tool"], "ok": False, "schema": None, "category": r.get("category") or r["status"].upper(), "attempts": 0})
    good = sum(1 for c in checks if c["ok"] and c["schema"] is not False)
    return {"validation": {"checks": checks, "valid": good, "total": len(checks)}, "_summary": f"{good}/{len(checks)} results valid"}


def _line(r: dict) -> str:
    t, d = r["tool"], r.get("data")
    if r["status"] != "success":
        return f"{t}: {r['status']} - {r.get('error') or r.get('reason', '')}"
    if t == "list_files":
        return f"list_files: {d['count']} files"
    if t == "read_file":
        return f"read_file: {d['path']} ({d['lines']} lines)"
    if t == "search_code":
        return f"search_code: {d['count']} match(es) for {d['query']!r}"
    if t == "analyze_python":
        return f"analyze_python: {d['path']} - {d['function_count']} functions, {d['class_count']} classes, max complexity {d['max_complexity']}"
    if t == "run_tests":
        return f"run_tests: {d['status']} - {d['passed']} passed, {d['failed']} failed ({d['duration_s']} s)"
    if t == "find_usages":
        return f"find_usages: {d['symbol']} referenced {d['count']} time(s); tested: {'yes' if d['tested'] else 'no'}"
    if t in ("scan_secrets", "scan_security", "lint"):
        return f"{t}: {d['count']} finding(s) in {d['files']} file(s)"
    if t == "get_diff":
        return f"get_diff: {len(d['files'])} files, +{d['additions']}/-{d['deletions']}"
    if t == "create_autofix_branch":
        return f"create_autofix_branch: {len(d['applied'])} fix(es) on {d['branch']}"
    return f"{t}: ok"


@agent("Summarizer")
def summarizer(state: dict) -> dict:
    status = state.get("status")
    if status in ("rejected", "not_understood"):
        reason = state.get("guard", {}).get("reason") if status == "rejected" else "; ".join(state.get("plan_info", {}).get("notes", []))
        return {"answer": reason, "status": status, "_summary": status}
    lines = [_line(r) for r in state["results"].values()]
    failed = [r for r in state["results"].values() if r["status"] != "success"]
    return {"answer": "\n".join(lines), "status": "completed" if not failed else "completed_with_errors",
            "_summary": f"{len(lines) - len(failed)}/{len(lines)} tasks succeeded"}


def route_after_guard(state: dict) -> str:
    return "summarizer" if state.get("status") == "rejected" else "task_planner"


def route_after_plan(state: dict) -> str:
    return "summarizer" if state.get("status") == "not_understood" else "wb_supervisor"
