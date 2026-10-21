"""Batch Testing: run many pull requests or developer requests through the real workflow, compare executors, keep every batch."""
import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, badge, esc, html_table, hub, page_setup, section, stat_tiles, status_bar

page_setup("Batch Testing")

from reviewpilot.config import Settings  # noqa: E402
from reviewpilot.evaluation import batch as B  # noqa: E402
from reviewpilot.evaluation.suites import labels  # noqa: E402
from reviewpilot.evaluation.workbench_cases import CASES  # noqa: E402
from reviewpilot.workspace import list_repos  # noqa: E402

h = hub()
st.markdown("## Batch testing")

labs = labels()
kind = st.segmented_control("Batch type", ["Pull requests", "Developer requests"], default="Pull requests") or "Pull requests"

if kind == "Pull requests":
    repo_now = st.session_state.get("repo", "")
    sources = ["Benchmark set", "Pick pull requests", "Upload CSV"] + ([f"Open PRs of {repo_now}"] if "/" in repo_now else [])
    src = st.radio("Source", sources, horizontal=True)
    items = []
    if src == "Benchmark set":
        c1, c2 = st.columns([1, 2])
        repos = c1.multiselect("Repositories", list_repos(), default=list_repos())
        cats = sorted({l["category"] for l in labs.values()})
        chosen = c2.multiselect("Categories", cats, default=cats)
        items = [{"pr_id": k} for k, l in labs.items() if l["repo"] in repos and l["category"] in chosen]
    elif src == "Pick pull requests":
        ids = st.multiselect("Pull requests", list(labs), default=["CALC-03", "INV-03", "INV-14", "TXT-01", "TXT-07", "TXT-16"])
        items = [{"pr_id": i} for i in ids]
    elif src.startswith("Open PRs"):
        prs = h.call("list_pull_requests", {"repo": repo_now})
        items = [{"pr_id": p["id"]} for p in prs.data["pull_requests"]] if prs.ok else []
    else:
        st.download_button("Download CSV template", B.template_csv(), file_name="reviewpilot_batch_template.csv")
        up = st.file_uploader("CSV with pr_id[, expected_verdict, expected_rules]", type=["csv"])
        if up is not None:
            try:
                items = B.parse_pr_csv(up.getvalue().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    c1, c2, c3 = st.columns([1.3, 1, 2])
    compare = c1.toggle("Also run with the sequential executor", value=False)
    workers = c2.number_input("Concurrent reviews", 1, 4, 1, help="Reviews running at the same time (latency rises with contention)")
    c3.caption(f"{len(items)} selected")
    if st.button("Run batch", type="primary", disabled=not items):
        results = {}
        modes = ["parallel"] + (["sequential"] if compare else [])
        bar = st.progress(0.0)
        for i, mode in enumerate(modes):
            results[mode] = B.run_pr_batch(items, Settings(approval_mode="auto", execution_mode=mode), workers=int(workers),
                                           progress=lambda f, m, i=i: bar.progress((i + f) / len(modes), text=f"{mode}: {m}"))
        bar.empty()
        name = B.save_batch(results, {"type": "pull_requests", "source": src, "items": len(items)})
        st.session_state["batch_open"] = name
else:
    default = "\n".join(c["request"] for c in CASES[:10])
    text = st.text_area("Requests", default, height=200)
    c1, c2 = st.columns([1, 3])
    repo = c1.selectbox("Default repository", list_repos())
    if c2.button("Run batch", type="primary"):
        items = B.parse_requests(text)
        lab = {c["request"]: c for c in CASES}
        for it in items:
            if it["request"] in lab and not it["tools"]:
                it["tools"], it["outcome"], it["repo"] = lab[it["request"]]["tools"] or None, lab[it["request"]]["outcome"], lab[it["request"]]["repo"]
        bar = st.progress(0.0)
        df = B.run_request_batch(items, repo, progress=lambda f, m: bar.progress(f, text=m))
        bar.empty()
        st.session_state["batch_open"] = B.save_batch({"requests": df}, {"type": "requests", "items": len(items)})

# ------------------------------------------------------------------------------------------------ results
batches = B.list_batches()
if not batches:
    st.stop()
names = [b["name"] for b in batches]
open_name = st.session_state.get("batch_open") if st.session_state.get("batch_open") in names else names[0]
section("Results")
sel = st.selectbox("Batch", names, index=names.index(open_name),
                   format_func=lambda n: next(f"{b['name']} · {b.get('type')} · {b.get('items')} items" for b in batches if b["name"] == n))
meta = next(b for b in batches if b["name"] == sel)
data = B.load_batch(sel)

if "requests" in data:
    df = data["requests"]
    tiles = [("Requests", len(df)), ("Completed", int((df["outcome"] == "completed").sum())), ("Denied / rejected", int(df["outcome"].isin(["denied", "rejected"]).sum())),
             ("Avg time", f"{df['elapsed_s'].mean():.2f} s"), ("MCP calls", int(df["tool_calls"].sum()))]
    if "plan_ok" in df and df["plan_ok"].notna().any():
        tiles.append(("Plan accuracy", f"{100 * df['plan_ok'].dropna().astype(bool).mean():.1f}%", "labelled requests"))
    stat_tiles(tiles)
    st.dataframe(df, hide_index=True, width="stretch")
    st.stop()

summ = {k: B.pr_batch_summary(v) for k, v in data.items()}
main = data.get("parallel", next(iter(data.values())))
s = summ.get("parallel", next(iter(summ.values())))
tiles = [("Pull requests", s["prs"]), ("Avg review time", f"{s['avg_time_s']:.2f} s", f"p95 {s['p95_time_s']:.2f} s"),
         ("MCP calls / PR", s.get("avg_tool_calls", "-")), ("Tool results valid", f"{s.get('tool_validity_pct', 0):.1f}%")]
if "verdict_accuracy_pct" in s:
    tiles.insert(1, ("Verdict accuracy", f"{s['verdict_accuracy_pct']:.1f}%", f"{s['labelled']} labelled"))
if "finding_f1_pct" in s:
    tiles.insert(2, ("Finding F1", f"{s['finding_f1_pct']:.1f}%", f"P {s['finding_precision_pct']:.0f}% · R {s['finding_recall_pct']:.0f}%"))
stat_tiles(tiles)

c1, c2 = st.columns([1, 1.3])
with c1:
    order = ["APPROVE", "COMMENT", "REQUEST_CHANGES", "BLOCK", "rejected"]
    vc = main["verdict"].replace("", "rejected").fillna("rejected").value_counts().to_dict()
    st.altair_chart(status_bar({k: vc[k] for k in order if k in vc}, "Verdicts"), width="stretch")
with c2:
    if len(data) > 1:
        rows = [{"metric": m, "mode": mode, "value": summ[mode].get(k)} for mode in data for m, k in
                (("avg review time (s)", "avg_time_s"), ("avg executor time (s)", "avg_executor_ms"))]
        for r in rows:
            if r["metric"].startswith("avg executor") and r["value"] is not None:
                r["value"] = r["value"] / 1000
        cdf = pd.DataFrame(rows)
        st.altair_chart(alt.Chart(cdf).mark_bar(cornerRadiusEnd=3).encode(
            y=alt.Y("metric:N", title=None, axis=alt.Axis(labelLimit=220)), yOffset="mode:N", x=alt.X("value:Q", title="seconds"),
            color=alt.Color("mode:N", scale=alt.Scale(domain=list(data), range=SERIES[:len(data)]), legend=alt.Legend(title=None)),
            tooltip=["metric", "mode", alt.Tooltip("value:Q", format=".3f")]).properties(title="Parallel vs sequential executor", height=alt.Step(16)),
            width="stretch")
        st.markdown(html_table(["Mode", "Avg review (s)", "Avg executor (ms)", "Avg DOP", "Verdict accuracy"],
                               [[esc(m), summ[m]["avg_time_s"], summ[m].get("avg_executor_ms", "-"), summ[m].get("avg_dop", "-"),
                                 f"{summ[m].get('verdict_accuracy_pct', '-')}%"] for m in data], num_cols=(1, 2, 3, 4)), unsafe_allow_html=True)
    else:
        lat = main[["pr_id", "elapsed_s"]].copy()
        st.altair_chart(alt.Chart(lat).mark_bar(color=SERIES[0], cornerRadiusEnd=3).encode(
            x=alt.X("pr_id:N", sort=None, title=None, axis=alt.Axis(labelAngle=-60)), y=alt.Y("elapsed_s:Q", title="review time (s)"),
            tooltip=["pr_id", "elapsed_s"]).properties(title="Review time per pull request", height=230), width="stretch")

wrong = main[main["verdict_ok"].astype(str) == "False"] if "verdict_ok" in main else pd.DataFrame()
if len(wrong):
    st.markdown("**Disagreements with the label**")
    st.markdown(html_table(["PR", "Expected", "ReviewPilot", "Missed", "Extra"], [[f"<b>{esc(r.pr_id)}</b>", badge(r.expected_verdict), badge(r.verdict),
                                                                                  esc(r.missed or "-"), esc(r.extra or "-")] for r in wrong.itertuples()]),
                unsafe_allow_html=True)
cols = [c for c in ["pr_id", "repo", "category", "status", "verdict", "expected_verdict", "verdict_ok", "score", "rules", "missed", "extra", "tool_calls",
                    "retries", "phases", "dop", "wall_ms", "elapsed_s", "trace_id"] if c in main]
st.dataframe(main[cols], hide_index=True, width="stretch")
st.download_button("Download results CSV", main.to_csv(index=False), file_name=f"{sel}.csv")
