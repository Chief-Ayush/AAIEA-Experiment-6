"""Evaluation Report: results of a stored evaluation run."""
import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, badge, bar_chart, confusion_chart, esc, grouped_bar, html_table, page_setup, section, stat_tiles, status_bar

page_setup("Evaluation Report")

from reviewpilot.evaluation.runner import list_reports, load_report  # noqa: E402

st.markdown("## Evaluation")
reps = list_reports()
if not reps:
    st.info("No evaluation yet.")
    st.page_link("pages/6_Evaluation_Suite.py", label="Run evaluation")
    st.stop()
run = st.selectbox("Report", [r["run"] for r in reps], format_func=lambda r: next(f"{x['run']} · quality index {x['headline'].get('quality_index')}"
                                                                                    for x in reps if x["run"] == r))
rep = load_report(run)
h, S, T = rep["headline"], rep["summaries"], {k: pd.DataFrame(v) for k, v in rep["tables"].items()}


def pct(k):
    v = h.get(k)
    return "-" if v is None else f"{v:.1f}%"


stat_tiles([("Quality index", h.get("quality_index"), "weighted, 0-100"), ("Verdict accuracy", pct("verdict_accuracy_pct"), "62 pull requests"),
            ("Finding F1", pct("finding_f1_pct"), f"P {pct('finding_precision_pct')} · R {pct('finding_recall_pct')}"),
            ("Workbench plans", pct("workbench_plan_accuracy_pct"), f"outcomes {pct('workbench_outcome_accuracy_pct')}"),
            ("Security", pct("security_pass_pct")), ("Failure handling", pct("failure_pass_pct")), ("HITL", pct("hitl_pass_pct"))])
stat_tiles([("Unsafe merges", pct("unsafe_merge_rate_pct"), "should-not-merge PRs approved"), ("BLOCK recall", pct("block_recall_pct"), "critical PRs blocked"),
            ("False alarms", pct("clean_pr_false_alarm_pct"), "clean PRs with any finding"), ("Failing tests caught", pct("failing_tests_caught_pct")),
            ("Tool results valid", pct("tool_validity_pct")), ("Avg review", f"{h.get('avg_latency_s') or 0:.2f} s", f"p95 {h.get('p95_latency_s') or 0:.2f} s")])
st.caption(f"Generated {rep['generated_at']} · {rep['total_s']} s · {rep['report_dir']}")

tabs = st.tabs(["Review benchmark", "Workbench", "Security", "Failure handling", "Human in the loop", "Performance", "Optimisation"])
with tabs[0]:
    if "benchmark" in T:
        b, df = S["benchmark"], T["benchmark"]
        c1, c2 = st.columns([1, 1.2])
        with c1:
            section("Verdict confusion matrix")
            st.altair_chart(confusion_chart(b["confusion"]), width="stretch")
        with c2:
            section("Findings by rule")
            pr = T["per_rule"]
            long = pr.melt(id_vars="rule", value_vars=["precision_pct", "recall_pct"], var_name="metric", value_name="pct")
            long["metric"] = long["metric"].map({"precision_pct": "precision", "recall_pct": "recall"})
            st.altair_chart(grouped_bar(long, "rule", "metric", "pct", y_title="%"), width="stretch")
        section("Accuracy by category")
        cat = pd.DataFrame([{"category": k, "accuracy": v} for k, v in b["by_category"].items()])
        st.altair_chart(bar_chart(cat, "category", "accuracy", domain=(0, 105), x_title=None, y_title="verdict accuracy (%)"), width="stretch")
        section("Where the review disagrees with the label")
        bad = df[(~df["verdict_ok"]) | (df["fn"] > 0) | (df["fp"] > 0)]
        st.markdown(html_table(["PR", "Category", "Expected", "ReviewPilot", "Missed", "Extra"],
                               [[f"<b>{esc(r.pr_id)}</b>", esc(r.category), badge(r.expected_verdict), badge(r.verdict), esc(r.missed or "-"), esc(r.extra or "-")]
                                for r in bad.itertuples()]), unsafe_allow_html=True)
        with st.expander("All 62 reviews"):
            st.dataframe(df, hide_index=True, width="stretch")
with tabs[1]:
    if "workbench" in T:
        w, df = S["workbench"], T["workbench"]
        stat_tiles([("Requests", w["requests"]), ("Plan accuracy", f"{w['plan_accuracy_pct']:.1f}%"), ("Tool selection F1", f"{w['tool_selection_f1_pct']:.1f}%"),
                    ("Outcome accuracy", f"{w['outcome_accuracy_pct']:.1f}%"), ("Avg time", f"{w['avg_latency_s']:.2f} s")])
        st.dataframe(df[["id", "request", "expected_tools", "planned_tools", "plan_ok", "expected_outcome", "outcome", "outcome_ok", "tool_calls", "elapsed_s", "note"]],
                     hide_index=True, width="stretch")
for i, sec in ((2, "security"), (3, "failure"), (4, "hitl")):
    with tabs[i]:
        if sec in T:
            df = T[sec]
            sm = S[sec]
            c1, c2 = st.columns([1, 2])
            with c1:
                st.altair_chart(status_bar(df["result"].value_counts().to_dict(), f"{sm['passed']}/{sm['tests']} passed"), width="stretch")
                if "by_category" in sm:
                    st.markdown(html_table(["Category", "Pass rate"], [[esc(k), f"{v:.0f}%"] for k, v in sm["by_category"].items()], num_cols=(1,)),
                                unsafe_allow_html=True)
            with c2:
                cols = [c for c in ["id", "category", "failure_type", "test", "scenario", "expected", "observed", "result", "detail"] if c in df]
                st.dataframe(df[cols], hide_index=True, width="stretch", height=min(38 * (len(df) + 1), 720))
with tabs[5]:
    if "performance" in T:
        p, df = S["performance"], T["performance"].astype({"run": str})
        stat_tiles([("Variation 9 speed-up", f"{p['var9_speedup']}x", f"{p['var9_sequential_ms']} -> {p['var9_parallel_ms']} ms"),
                    ("4 pytest runs speed-up", f"{p.get('pytest4_speedup')}x", f"{p.get('pytest4_sequential_ms')} -> {p.get('pytest4_parallel_ms')} ms"),
                    ("Review executor speed-up", f"{p['review_speedup']}x", f"DOP {p['review_avg_dop']} · DOS {p['review_avg_dos']}"),
                    ("Connection reuse", f"{p['connection_reuse_speedup']}x", f"{p['fresh_connection_ms']} -> {p['reused_connection_ms']} ms")])
        v9 = df[df["experiment"].str.startswith("Variation")]
        c1, c2 = st.columns(2)
        with c1:
            section("Variation 9")
            st.altair_chart(alt.Chart(v9).mark_line(point=True).encode(x=alt.X("run:N", sort=None, title="run"), y=alt.Y("wall_ms:Q", title="wall time (ms)"),
                                                                       color=alt.Color("mode:N", scale=alt.Scale(range=SERIES[:2]), legend=alt.Legend(title=None)),
                                                                       tooltip=["mode", "run", "wall_ms"]).properties(height=240), width="stretch")
        with c2:
            section("Review workflow")
            rv = df[df["experiment"] == "Review workflow"].copy()
            rv["run"] = rv["run"].astype(str)
            st.altair_chart(grouped_bar(rv, "run", "mode", "wall_ms", y_title="executor time (ms)"), width="stretch")
        st.dataframe(df, hide_index=True, width="stretch")
with tabs[6]:
    if "optimization" in T:
        section("Basic vs optimised")
        df = T["optimization"]
        st.markdown(html_table(["Technique", "Metric", "Basic approach", "Optimised approach", "Basic", "Optimised", "Improvement"],
                               [[f"<b>{esc(r.technique)}</b>", esc(r.metric), esc(r.basic_approach), esc(r.optimised_approach), r.basic_value, r.optimised_value,
                                 "-" if pd.isna(r.improvement_pct) else f"{r.improvement_pct:.1f}%"] for r in df.itertuples()], num_cols=(4, 5, 6)),
                    unsafe_allow_html=True)
