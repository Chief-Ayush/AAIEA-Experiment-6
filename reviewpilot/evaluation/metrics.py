"""Scoring of one review against its label, and small metric helpers."""
from __future__ import annotations

from collections import Counter

import numpy as np


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def percentile(xs, q):
    xs = [x for x in xs if x is not None]
    return float(np.percentile(xs, q)) if xs else None


def score_review(state: dict, label: dict | None) -> dict:
    """One benchmark row: what the workflow did, how fast, and (with a label) whether it was right."""
    human = state.get("human", {})
    verdict = human.get("final_verdict") or state.get("verdict", {}).get("verdict")
    findings = state.get("final_findings", state.get("validated", []))
    m = state.get("exec_metrics", {})
    trace = state.get("trace", [])
    val = state.get("validation", {})
    got = {(f["rule"], f["path"]) for f in findings}
    row = {
        "pr_id": state.get("pr_id"), "repo": state.get("pr", {}).get("repo"), "status": state.get("status"), "verdict": verdict,
        "score": state.get("verdict", {}).get("score"), "confidence": state.get("verdict", {}).get("confidence"),
        "findings": len(findings), "rules": " ".join(sorted({f["rule"] for f in findings})),
        "tool_calls": len(trace), "retries": sum(1 for t in trace if t.get("attempt", 1) > 1),
        "failed_calls": sum(1 for t in trace if t["status"] != "success"),
        "wall_ms": m.get("wall_ms"), "busy_ms": m.get("busy_ms"), "phases": m.get("phases"), "dop": m.get("dop"), "speedup": m.get("speedup"),
        "tools_valid": val.get("tools_valid"), "tools_total": val.get("tools_total"), "dropped": len(val.get("dropped", [])),
        "trace_id": state.get("trace_id"),
    }
    if label:
        exp = {(f["rule"], f["path"]) for f in label["findings"]}
        tp, fp, fn = len(got & exp), len(got - exp), len(exp - got)
        row |= {"category": label.get("category"), "expected_verdict": label["verdict"], "verdict_ok": verdict == label["verdict"],
                "tp": tp, "fp": fp, "fn": fn, "missed": " ".join(sorted(f"{r}@{p}" for r, p in exp - got)),
                "extra": " ".join(sorted(f"{r}@{p}" for r, p in got - exp)),
                "expected_rules": " ".join(sorted({r for r, _ in exp})), "tests_fail": label.get("tests_fail", False),
                "tst001_found": any(r == "TST001" for r, _ in got)}
    return row


def per_rule(rows: list[dict], labels: dict[str, dict], states: dict[str, dict]) -> list[dict]:
    tp, fp, fn = Counter(), Counter(), Counter()
    for pid, st in states.items():
        got = {(f["rule"], f["path"]) for f in st.get("final_findings", st.get("validated", []))}
        exp = {(f["rule"], f["path"]) for f in labels[pid]["findings"]}
        for r, _ in got & exp:
            tp[r] += 1
        for r, _ in got - exp:
            fp[r] += 1
        for r, _ in exp - got:
            fn[r] += 1
    out = []
    for rule in sorted(set(tp) | set(fp) | set(fn)):
        p, r, f = prf(tp[rule], fp[rule], fn[rule])
        out.append({"rule": rule, "tp": tp[rule], "fp": fp[rule], "fn": fn[rule], "precision_pct": round(100 * p, 1), "recall_pct": round(100 * r, 1),
                    "f1_pct": round(100 * f, 1)})
    return out
