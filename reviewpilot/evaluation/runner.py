"""EvaluationRunner: runs the suites, writes CSV/JSON under storage/reports/run_*/ and keeps latest_report.json."""
from __future__ import annotations

import json
import time
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import PATHS
from . import suites as S

ALL_SECTIONS = ["benchmark", "workbench", "security", "failure", "hitl", "performance", "optimization"]
SECTION_INFO = {
    "benchmark": "Review benchmark - 62 labelled pull requests: verdict accuracy, finding precision / recall per rule, false alarms on clean PRs",
    "workbench": "Workbench planner - 32 developer requests: tool selection, plan accuracy and expected outcome (completed / denied / rejected)",
    "security": "Security - path traversal, command injection, risk policy, protocol validation, prompt injection in PRs, secret masking",
    "failure": "Failure handling - fault injection per error category: input, tool, security, timeout, execution, server crash, dependency failure",
    "hitl": "Human in the loop - pause / approve / reject / override / dismiss, approved auto-fix with test gate and rollback",
    "performance": "Performance - Variation 9 (4 independent calls x 5 runs), review workflow sequential vs parallel, connection reuse",
    "optimization": "Optimisation - basic vs optimised for the lab manual's nine techniques (+ connection reuse)",
}
WEIGHTS = [("verdict_accuracy_pct", 0.20), ("finding_f1_pct", 0.20), ("workbench_plan_accuracy_pct", 0.10), ("workbench_outcome_accuracy_pct", 0.05),
           ("security_pass_pct", 0.15), ("failure_pass_pct", 0.10), ("hitl_pass_pct", 0.10), ("tool_validity_pct", 0.10)]


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def quality_index(h: dict) -> float:
    num = den = 0.0
    for k, w in WEIGHTS:
        if h.get(k) is not None:
            num += w * h[k]
            den += w
    return round(num / den, 1) if den else 0.0


def run_all(sections: list[str] | None = None, progress=None, workers: int = 1) -> dict:
    sections = [s for s in ALL_SECTIONS if s in (sections or ALL_SECTIONS)]
    t0 = time.time()
    tables, summaries, timings = {}, {}, {}
    n, done = len(sections), 0

    def tick(frac, msg):
        if progress:
            progress(min(1.0, (done + frac) / n), msg)

    perf = None
    for sec in sections:
        s0 = time.time()
        tick(0, f"running {sec} ...")
        if sec == "benchmark":
            df, rules, _ = S.run_benchmark(progress=lambda f, m: tick(f, m), workers=workers)
            tables["benchmark"] = df
            tables["per_rule"] = pd.DataFrame(rules)
            summaries["benchmark"] = S.benchmark_summary(df, rules)
        elif sec == "workbench":
            df = S.run_workbench(progress=lambda f, m: tick(f, m))
            tables["workbench"] = df
            summaries["workbench"] = S.workbench_summary(df)
        elif sec in ("security", "failure", "hitl"):
            df = {"security": S.run_security, "failure": S.run_failure, "hitl": S.run_hitl}[sec]()
            tables[sec] = df
            summaries[sec] = S.pass_summary(df)
        elif sec == "performance":
            df, perf = S.run_performance(progress=lambda f, m: tick(f, m))
            tables["performance"] = df
            summaries["performance"] = perf
        elif sec == "optimization":
            tables["optimization"] = S.run_optimization(perf)
        timings[sec] = round(time.time() - s0, 1)
        done += 1
    b, w = summaries.get("benchmark", {}), summaries.get("workbench", {})
    h = {k: b.get(k) for k in ("verdict_accuracy_pct", "finding_precision_pct", "finding_recall_pct", "finding_f1_pct", "clean_pr_false_alarm_pct",
                                "block_recall_pct", "unsafe_merge_rate_pct", "failing_tests_caught_pct", "tool_validity_pct", "avg_latency_s",
                                "p95_latency_s", "avg_tool_calls", "avg_dop")}
    h["workbench_plan_accuracy_pct"] = w.get("plan_accuracy_pct")
    h["workbench_outcome_accuracy_pct"] = w.get("outcome_accuracy_pct")
    h["workbench_tool_f1_pct"] = w.get("tool_selection_f1_pct")
    for sec in ("security", "failure", "hitl"):
        if sec in summaries:
            h[f"{sec}_pass_pct"] = summaries[sec]["pass_rate_pct"]
    if perf:
        h["var9_speedup"] = perf["var9_speedup"]
        h["review_speedup"] = perf["review_speedup"]
    h["quality_index"] = quality_index(h)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "sections": sections, "headline": _clean(h), "summaries": _clean(summaries),
              "timings_s": timings, "total_s": round(time.time() - t0, 1), "tables": {k: _clean(v.to_dict("records")) for k, v in tables.items()}}
    run_dir = PATHS.reports / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for k, v in tables.items():
        v.to_csv(run_dir / f"{k}.csv", index=False)
    report["report_dir"] = run_dir.relative_to(PATHS.root).as_posix()
    txt = json.dumps(report, indent=1, default=str)
    (run_dir / "report.json").write_text(txt, encoding="utf-8")
    (PATHS.reports / "latest_report.json").write_text(txt, encoding="utf-8")
    return report


def list_reports() -> list[dict]:
    out = []
    for d in sorted(PATHS.reports.glob("run_*"), reverse=True):
        try:
            r = json.loads((d / "report.json").read_text(encoding="utf-8"))
            out.append({"run": d.name, "generated_at": r.get("generated_at"), "sections": r.get("sections"), "total_s": r.get("total_s"),
                        "headline": r.get("headline", {})})
        except Exception:  # noqa: BLE001
            continue
    return out


def load_report(run: str | None = None) -> dict | None:
    p = PATHS.reports / run / "report.json" if run else PATHS.reports / "latest_report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
