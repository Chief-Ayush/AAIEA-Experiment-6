"""Batch testing: run many pull requests (or workbench requests) through the real workflow, score them when labels exist,
compare execution modes and keep every batch on disk so it can be reopened later."""
from __future__ import annotations

import io
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

from .. import pipeline
from ..config import PATHS, Settings
from .metrics import percentile, prf, score_review
from .suites import EVAL, labels


# ----------------------------------------------------------------------------------------------------------- input parsing
def parse_pr_csv(text: str) -> list[dict]:
    """Columns: pr_id (required), expected_verdict, expected_rules (space separated 'RULE@path' or 'RULE')."""
    df = pd.read_csv(io.StringIO(text)).fillna("")
    if "pr_id" not in df.columns:
        raise ValueError("CSV needs a 'pr_id' column")
    out = []
    for _, r in df.iterrows():
        item = {"pr_id": str(r["pr_id"]).strip().upper()}
        if r.get("expected_verdict", ""):
            item["expected_verdict"] = str(r["expected_verdict"]).strip().upper()
        if r.get("expected_rules", ""):
            item["expected_rules"] = str(r["expected_rules"]).split()
        out.append(item)
    return out


def template_csv() -> str:
    return "pr_id,expected_verdict,expected_rules\nCALC-03,REQUEST_CHANGES,SEC002@app.py TST003@app.py\nINV-03,BLOCK,SEC001@inventory/config.py\nTXT-01,APPROVE,\n"


def _label_for(item: dict, labs: dict) -> dict | None:
    if "expected_verdict" in item or "expected_rules" in item:
        fs = []
        for x in item.get("expected_rules", []):
            rule, _, path = x.partition("@")
            fs.append({"rule": rule, "path": path or "*"})
        return {"verdict": item.get("expected_verdict", ""), "findings": fs, "category": "custom"}
    return labs.get(item["pr_id"])


def _score(st: dict, lab: dict | None) -> dict:
    if lab and any(f["path"] == "*" for f in lab["findings"]):       # rule-only expectations
        got = {f["rule"] for f in st.get("final_findings", [])}
        exp = {f["rule"] for f in lab["findings"]}
        row = score_review(st, None)
        row |= {"expected_verdict": lab["verdict"], "verdict_ok": (row["verdict"] == lab["verdict"]) if lab["verdict"] else None,
                "tp": len(got & exp), "fp": len(got - exp), "fn": len(exp - got), "missed": " ".join(sorted(exp - got)), "extra": " ".join(sorted(got - exp)),
                "category": "custom"}
        return row
    row = score_review(st, lab)
    if lab and not lab["verdict"]:
        row["verdict_ok"] = None
    return row


# ----------------------------------------------------------------------------------------------------------- running
def run_pr_batch(items: list[dict], settings: Settings | None = None, workers: int = 1, progress=None) -> pd.DataFrame:
    s = settings or EVAL
    labs = labels()

    def work(item):
        t0 = time.perf_counter()
        st = pipeline.review(item["pr_id"], s, persist=True)
        row = _score(st, _label_for(item, labs)) if st.get("status") != "rejected" else {"pr_id": item["pr_id"], "status": "rejected",
                                                                                          "verdict": None, "trace_id": st["trace_id"]}
        row["elapsed_s"] = round(time.perf_counter() - t0, 3)
        row["mode"] = s.execution_mode
        return row

    rows = []
    if workers > 1:
        with ThreadPoolExecutor(workers) as ex:
            for i, row in enumerate(ex.map(work, items)):
                rows.append(row)
                if progress:
                    progress((i + 1) / len(items), row["pr_id"])
    else:
        for i, it in enumerate(items):
            rows.append(work(it))
            if progress:
                progress((i + 1) / len(items), it["pr_id"])
    return pd.DataFrame(rows)


def pr_batch_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    s = {"prs": len(df), "verdicts": df["verdict"].fillna("rejected").value_counts().to_dict(),
         "avg_time_s": round(float(df["elapsed_s"].mean()), 3), "p95_time_s": round(percentile(df["elapsed_s"], 95), 3),
         "total_time_s": round(float(df["elapsed_s"].sum()), 2)}
    if "tool_calls" in df:
        s |= {"avg_tool_calls": round(float(df["tool_calls"].mean()), 1), "retries": int(df["retries"].fillna(0).sum()),
              "failed_calls": int(df["failed_calls"].fillna(0).sum()), "avg_dop": round(float(df["dop"].mean()), 2),
              "avg_executor_ms": round(float(df["wall_ms"].mean()), 1),
              "tool_validity_pct": round(100 * df["tools_valid"].sum() / max(1, df["tools_total"].sum()), 1)}
    if "verdict_ok" in df and df["verdict_ok"].notna().any():
        s["verdict_accuracy_pct"] = round(100 * df["verdict_ok"].dropna().astype(bool).mean(), 1)
        s["labelled"] = int(df["verdict_ok"].notna().sum())
    if "tp" in df and df["tp"].notna().any():
        p, r, f = prf(int(df["tp"].sum()), int(df["fp"].sum()), int(df["fn"].sum()))
        s |= {"finding_precision_pct": round(100 * p, 1), "finding_recall_pct": round(100 * r, 1), "finding_f1_pct": round(100 * f, 1)}
    return {k: (float(v) if isinstance(v, np.floating) else int(v) if isinstance(v, np.integer) else v) for k, v in s.items()}


def parse_requests(text: str) -> list[dict]:
    """One request per line, or a CSV with request[,repo,expected_tools,expected_outcome]."""
    text = (text or "").strip()
    if text.lower().startswith("request"):
        df = pd.read_csv(io.StringIO(text)).fillna("")
        return [{"request": r["request"], "repo": r.get("repo") or None, "tools": str(r.get("expected_tools", "")).split() or None,
                 "outcome": r.get("expected_outcome") or None} for _, r in df.iterrows()]
    return [{"request": ln.strip(), "repo": None, "tools": None, "outcome": None} for ln in text.splitlines() if ln.strip()]


def run_request_batch(items: list[dict], default_repo: str, settings: Settings | None = None, progress=None) -> pd.DataFrame:
    s = settings or EVAL
    rows = []
    for i, it in enumerate(items):
        t0 = time.perf_counter()
        st = pipeline.workbench(it["request"], it.get("repo") or default_repo, s, persist=True)
        planned = [t["tool"] for t in st.get("plan_info", {}).get("tasks", [])] if st.get("plan_info") else []
        outcome = ("rejected" if st["status"] == "rejected" else "not_understood" if st["status"] == "not_understood"
                   else "denied" if st.get("policy", {}).get("denied") else "completed")
        row = {"request": it["request"], "repo": st.get("repo") or it.get("repo") or default_repo, "planned_tools": " ".join(planned), "outcome": outcome,
               "status": st["status"], "tool_calls": len(st.get("trace", [])), "elapsed_s": round(time.perf_counter() - t0, 3),
               "answer": (st.get("answer") or "")[:300], "trace_id": st["trace_id"]}
        if it.get("tools"):
            row["expected_tools"] = " ".join(it["tools"])
            row["plan_ok"] = Counter(it["tools"]) == Counter(planned)
        if it.get("outcome"):
            row["expected_outcome"] = it["outcome"]
            row["outcome_ok"] = it["outcome"] == outcome
        rows.append(row)
        if progress:
            progress((i + 1) / len(items), it["request"][:40])
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------------------- persistence
def save_batch(results: dict[str, pd.DataFrame], meta: dict) -> str:
    name = f"batch_{datetime.now():%Y%m%d_%H%M%S}"
    d = PATHS.batches / name
    d.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for key, df in results.items():
        df.to_csv(d / f"results_{key}.csv", index=False)
        summaries[key] = pr_batch_summary(df) if "pr_id" in df else {"requests": len(df), "avg_time_s": round(float(df["elapsed_s"].mean()), 3)}
    (d / "meta.json").write_text(json.dumps({"name": name, "created": datetime.now().isoformat(timespec="seconds"), **meta, "summaries": summaries},
                                            indent=1, default=str), encoding="utf-8")
    return name


def list_batches() -> list[dict]:
    out = []
    for d in sorted(PATHS.batches.glob("batch_*"), reverse=True):
        try:
            out.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_batch(name: str) -> dict[str, pd.DataFrame]:
    d = PATHS.batches / name
    out = {}
    for f in sorted(d.glob("results_*.csv")):
        df = pd.read_csv(f)
        obj = [c for c in df.columns if df[c].dtype == object and c != "verdict_ok"]
        df[obj] = df[obj].fillna("")
        out[f.stem.replace("results_", "")] = df
    return out
