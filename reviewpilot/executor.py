"""Executor: runs a scheduled plan over MCP.

Phase by phase.  Inside a phase the tool calls are independent and run concurrently with asyncio.gather (bounded by a
semaphore = max_parallel); in sequential mode they run one after another.  Tasks with a *resolver* get their arguments
from earlier results at run time (e.g. run_tests on the tests that reference the changed functions); tasks with a
*condition* are skipped when the condition is false (e.g. the full suite only runs once the affected tests pass).
"""
from __future__ import annotations

import asyncio
import time

from .config import Settings
from .mcp_client import MCPHub
from .planning import is_test_path, schedule
from .tracing import Trace


# ----------------------------------------------------------------------------------------------------------- resolvers
def _changed_public_functions(results: dict) -> list[tuple[str, str, int]]:
    out = []
    for r in results.values():
        if r["tool"] != "compare_versions" or not r.get("ok"):
            continue
        data = r["data"]
        if is_test_path(data["path"]):
            continue
        for f in data["functions_added"] + data["functions_changed"]:
            short = f["name"].split(".")[-1]
            if f["is_public"] and not short.startswith("_") and short not in ("main",):
                out.append((short, data["path"], f["line"]))
    seen, uniq = set(), []
    for s in out:
        if s[0] not in seen:
            seen.add(s[0])
            uniq.append(s)
    return uniq


def resolve(task: dict, results: dict, plan_ctx: dict) -> list[dict] | None:
    """Return the concrete argument sets for a task (a resolver may expand into several calls) or None to skip."""
    r = task.get("resolver")
    if not r:
        return [task["args"]]
    if r == "usages_of_changed":
        funcs = _changed_public_functions(results)
        plan_ctx["changed_functions"] = funcs
        base = {k: v for k, v in task["args"].items() if k != "symbol"}
        return [base | {"symbol": name} for name, _, _ in funcs] or None
    if r == "affected_tests":
        tests = set()
        for res in results.values():
            if res["tool"] == "find_usages" and res.get("ok"):
                for call in res.get("calls", [res]):
                    if call.get("ok"):
                        tests.update(call["data"]["test_files"])
        tests.update(p for p in plan_ctx.get("changed_test_files", []))
        plan_ctx["affected_tests"] = sorted(tests)
        if not tests:
            return None
        return [task["args"] | {"targets": sorted(tests)}]
    if r == "baseline_tests":
        failed = [res for res in results.values() if res["tool"] == "run_tests" and res.get("ok") and res.get("args", {}).get("ref", "head") != "base"
                  and res["data"]["status"] in ("failed", "error")]
        if not failed:
            return None
        if any(not f["data"]["targets"] for f in failed):          # the full suite failed -> run the full suite on the base
            return [task["args"] | {"targets": []}]
        return [task["args"] | {"targets": sorted({t for f in failed for t in f["data"]["targets"]})}]
    raise ValueError(f"unknown resolver {r}")


def condition_holds(task: dict, results: dict, settings: Settings) -> tuple[bool, str]:
    c = task.get("condition")
    if not c or not settings.dependency_aware:
        return True, ""
    if c == "affected_tests_passed":
        dep = results.get(task["depends_on"][0]) if task["depends_on"] else None
        if dep is None or dep.get("status") == "skipped":
            return True, "no affected tests were selected, so the whole suite runs"
        if dep.get("ok") and dep["data"]["status"] in ("passed", "no_tests"):
            return True, "affected tests passed"
        return False, "affected tests failed - the full suite would only repeat the failure"
    if c == "head_tests_failed":
        bad = [r for r in results.values() if r["tool"] == "run_tests" and r.get("ok") and r["data"]["status"] in ("failed", "error")]
        return (True, "tests failed on the PR") if bad else (False, "all tests passed - no baseline needed")
    return True, ""


# ----------------------------------------------------------------------------------------------------------- execution
async def execute(hub: MCPHub, tasks: list[dict], settings: Settings, trace: Trace, plan_ctx: dict | None = None,
                  denied: set[str] | None = None) -> dict:
    plan_ctx = plan_ctx if plan_ctx is not None else {}
    denied = denied or set()
    phases = schedule(tasks, settings.dependency_aware)
    results: dict[str, dict] = {}
    timeline: list[dict] = []
    sem = asyncio.Semaphore(max(1, settings.max_parallel))
    t_start = time.perf_counter()

    async def one(task: dict, args: dict, phase_no: int) -> dict:
        if task["tool"] == "run_tests" and "timeout_s" not in args:
            args = args | {"timeout_s": int(settings.test_timeout_s)}
        async with sem:
            res = await hub.acall(task["tool"], args, settings=settings, trace=trace, phase=phase_no, agent=task.get("agent"))
        d = res.public()
        d["args"] = args
        d["started_s"] = None
        return d

    for i, phase in enumerate(phases, 1):
        p0 = time.perf_counter()
        jobs: list[tuple[dict, list[dict]]] = []
        for t in phase:
            base = {"id": t["id"], "tool": t["tool"], "purpose": t["purpose"], "agent": t.get("agent"), "phase": i, "depends_on": t["depends_on"]}
            if t["id"] in denied:
                results[t["id"]] = base | {"status": "denied", "ok": False, "error": "denied by policy", "category": "SECURITY", "args": t["args"]}
                continue
            deps = [d for d in t["depends_on"] if d in results]
            failed_deps = [d for d in deps if results[d]["status"] in ("failed", "denied")]
            if deps and len(failed_deps) == len(deps) and settings.dependency_aware and not t.get("condition"):
                results[t["id"]] = base | {"status": "skipped", "ok": False, "reason": f"dependency failed: {', '.join(failed_deps)}", "args": t["args"]}
                continue
            ok, why = condition_holds(t, results, settings)
            if not ok:
                results[t["id"]] = base | {"status": "skipped", "ok": False, "reason": why, "args": t["args"]}
                continue
            try:
                arg_sets = resolve(t, results, plan_ctx)
            except Exception as exc:  # noqa: BLE001
                results[t["id"]] = base | {"status": "failed", "ok": False, "error": f"[INPUT] {exc}", "category": "INPUT", "args": t["args"]}
                continue
            if arg_sets is None:
                results[t["id"]] = base | {"status": "skipped", "ok": False, "reason": "nothing to resolve (no changed public functions / no affected tests)",
                                           "args": t["args"]}
                continue
            results[t["id"]] = base | {"note": why} if why else base
            jobs.append((t, arg_sets))
        calls = [(t, a) for t, sets in jobs for a in sets]
        rel0 = (time.perf_counter() - t_start) * 1000
        if settings.execution_mode == "sequential":
            outs = []
            for t, a in calls:
                outs.append(await one(t, a, i))
        else:
            outs = await asyncio.gather(*(one(t, a, i) for t, a in calls))
        for (t, a), out in zip(calls, outs):
            r = results[t["id"]]
            r.setdefault("calls", []).append(out)
        for t, sets in jobs:
            r = results[t["id"]]
            cs = r["calls"]
            r["ok"] = all(c["ok"] for c in cs)
            r["status"] = "success" if r["ok"] else "failed"
            r["duration_ms"] = round(sum(c["duration_ms"] for c in cs), 2)
            r["attempts"] = sum(c["attempts"] for c in cs)
            r["error"] = next((c["error"] for c in cs if not c["ok"]), None)
            r["category"] = next((c["category"] for c in cs if not c["ok"]), None)
            r["output_valid"] = all(c["output_valid"] is not False for c in cs)
            r["task_ids"] = [c["task_id"] for c in cs]
            r["args"] = sets[0] if len(sets) == 1 else {"expanded": len(sets), "first": sets[0]}
            r["data"] = cs[0]["data"] if len(cs) == 1 else [c["data"] for c in cs]
        wall = (time.perf_counter() - p0) * 1000
        timeline.append({"phase": i, "tasks": [t["id"] for t in phase], "calls": len(calls), "start_ms": round(rel0, 1), "wall_ms": round(wall, 1),
                         "busy_ms": round(sum(o["duration_ms"] for o in outs), 1)})
    total = (time.perf_counter() - t_start) * 1000
    n_calls = sum(p["calls"] for p in timeline)
    busy = sum(p["busy_ms"] for p in timeline)
    used = [p for p in timeline if p["calls"]]
    metrics = {"mode": settings.execution_mode, "dependency_aware": settings.dependency_aware, "phases": len(used), "tool_calls": n_calls,
               "wall_ms": round(total, 1), "busy_ms": round(busy, 1),
               "dop": max((p["calls"] for p in used), default=0) if settings.execution_mode == "parallel" else min(1, n_calls),
               "avg_dop": round(n_calls / len(used), 2) if used else 0.0,
               "dos": len(used) if settings.execution_mode == "parallel" else n_calls,
               "speedup": round(busy / total, 2) if total else 1.0,
               "retries": sum(max(0, c["attempts"] - 1) for r in results.values() for c in r.get("calls", [])),
               "skipped": sum(1 for r in results.values() if r["status"] == "skipped"),
               "failed": sum(1 for r in results.values() if r["status"] == "failed")}
    return {"results": results, "timeline": timeline, "metrics": metrics, "context": plan_ctx}
