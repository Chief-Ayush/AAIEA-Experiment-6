"""Traces and Logs: mcp_traces.jsonl, execution.log and developer_automation_report.json (lab manual artefacts in storage/mcp_logs)."""
import json

import altair as alt
import pandas as pd
import streamlit as st

from ui.common import SERIES, html_table, esc, page_setup, section, stat_tiles, status_bar, timeline_chart, trace_frame

page_setup("Traces and Logs")

from reviewpilot.config import PATHS  # noqa: E402

st.markdown("## Traces and Logs")


@st.cache_data(ttl=5)
def load_traces(mtime: float) -> pd.DataFrame:
    rows = []
    with open(PATHS.trace_file, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.DataFrame(rows)


if not PATHS.trace_file.exists():
    st.info("No traces yet.")
    st.stop()
df = load_traces(PATHS.trace_file.stat().st_mtime)
stat_tiles([("Trace records", len(df)), ("Workflows", df["trace_id"].nunique()), ("Failed calls", int((df["status"] != "success").sum())),
            ("Retried calls", int((df["attempt"] > 1).sum())), ("Median call", f"{df['execution_time_ms'].median():.0f} ms"),
            ("File", f"{PATHS.trace_file.stat().st_size / 1024:.0f} KB")])

c1, c2 = st.columns([1, 1.4])
with c1:
    errs = df[df["status"] != "success"]["error_category"].fillna("?").value_counts().to_dict()
    if errs:
        st.altair_chart(status_bar(errs, "Failed calls by error category", {"INPUT": "#eda100", "SECURITY": "#d03b3b", "TOOL": "#ec835a",
                                                                            "TIMEOUT": "#2a78d6", "EXECUTION": "#6250d6"}), width="stretch")
with c2:
    per = df.groupby("tool")["execution_time_ms"].median().reset_index().sort_values("execution_time_ms", ascending=False)
    st.altair_chart(alt.Chart(per).mark_bar(color=SERIES[0], cornerRadiusEnd=3).encode(
        y=alt.Y("tool:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=200)), x=alt.X("execution_time_ms:Q", title="median execution time (ms)"),
        tooltip=["tool", alt.Tooltip("execution_time_ms:Q", format=".1f")]).properties(title="Median time per tool", height=alt.Step(16)), width="stretch")

section("mcp_traces.jsonl")
c1, c2, c3 = st.columns([1.4, 1, 1])
traces = list(dict.fromkeys(df["trace_id"][::-1]))
tid = c1.selectbox("Trace", ["all"] + traces)
tool = c2.selectbox("Tool", ["all"] + sorted(df["tool"].unique()))
status = c3.selectbox("Status", ["all"] + sorted(df["status"].unique()))
view = df
if tid != "all":
    view = view[view["trace_id"] == tid]
if tool != "all":
    view = view[view["tool"] == tool]
if status != "all":
    view = view[view["status"] == status]
recs = view.tail(500).to_dict("records")
st.dataframe(trace_frame(recs[::-1]), hide_index=True, width="stretch")
if tid != "all":
    ch = timeline_chart(recs)
    if ch is not None:
        st.altair_chart(ch, width="stretch")

section("execution.log")
if PATHS.execution_log.exists():
    lines = PATHS.execution_log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    st.code("\n".join(lines), language=None)
section("developer_automation_report.json")
p = PATHS.logs / "developer_automation_report.json"
if p.exists():
    rep = json.loads(p.read_text(encoding="utf-8"))
    st.markdown(html_table(["", ""], [["Trace", esc(rep.get("trace_id"))], ["Pull request", esc(f"{rep.get('pr_id')} {rep.get('title', '')}")],
                                      ["Status", esc(rep.get("status"))], ["Final verdict", esc(rep.get("final_verdict", "-"))],
                                      ["Findings", len(rep.get("findings", []))], ["Generated", esc(rep.get("generated"))]]), unsafe_allow_html=True)
    st.download_button("Download", p.read_text(encoding="utf-8"), file_name="developer_automation_report.json")
    with st.expander("JSON"):
        st.json(rep, expanded=1)
