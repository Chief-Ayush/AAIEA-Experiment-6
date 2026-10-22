"""Workbench: one developer request -> a plan of MCP tool calls -> policy -> (parallel) execution -> validated results."""
import pandas as pd
import streamlit as st

from ui.common import (WORKBENCH_EXAMPLES, badge, code_html, current_repo, esc, html_table, hub, page_setup, phases_html, stat_tiles)

page_setup("Workbench")

from reviewpilot import pipeline  # noqa: E402
from reviewpilot.config import load_settings  # noqa: E402

hub()
repo = current_repo()
st.markdown(f"## Workbench <span class='cq-muted' style='font-size:1rem;font-weight:400'>{esc(repo)}</span>", unsafe_allow_html=True)
st.caption("Type a developer request. It is planned into MCP tool calls, checked by the Supervisor and PolicyGuard, executed (independent calls in "
           "parallel) and validated. Every step is shown below and written to the trace log.")


def results_view(state: dict) -> None:
    for x in state["results"].values():
        if x["status"] != "success":
            continue
        d, t = x["data"], x["tool"]
        st.markdown(f"**{esc(t)}** <span class='cq-muted'>{esc(x['purpose'])}</span>", unsafe_allow_html=True)
        if t == "read_file":
            st.markdown(code_html(d["content"], d["start_line"]), unsafe_allow_html=True)
        elif t == "analyze_python":
            st.dataframe(pd.DataFrame(d["functions"])[["name", "line", "complexity", "docstring"]], hide_index=True, width="stretch")
        elif t == "search_code":
            st.dataframe(pd.DataFrame(d["matches"]), hide_index=True, width="stretch") if d["matches"] else st.caption("no matches")
        elif t == "list_files":
            st.dataframe(pd.DataFrame(d["files"]), hide_index=True, width="stretch")
        elif t == "list_pull_requests":
            st.dataframe(pd.DataFrame(d["pull_requests"]), hide_index=True, width="stretch")
        elif t == "run_tests":
            st.markdown(f"{badge(d['status'])} {d['passed']} passed, {d['failed']} failed, {d['errors']} errors in {d['duration_s']} s", unsafe_allow_html=True)
            if d["failures"]:
                st.dataframe(pd.DataFrame(d["failures"]), hide_index=True, width="stretch")
        elif t == "find_usages":
            st.dataframe(pd.DataFrame(d["usages"]), hide_index=True, width="stretch") if d["usages"] else st.caption("no usages")
        elif t == "lint":
            st.dataframe(pd.DataFrame(d["issues"]), hide_index=True, width="stretch") if d["issues"] else st.caption("no issues")
        elif t in ("scan_secrets", "scan_security"):
            st.dataframe(pd.DataFrame(d["findings"]), hide_index=True, width="stretch") if d["findings"] else st.caption("nothing found")
        elif t == "get_diff":
            for fd in d["files"]:
                st.code(fd["patch"], language="diff")
        else:
            st.json(d, expanded=False)


def show(state: dict) -> None:
    status = state["status"]
    if status == "awaiting_human":
        t = state["interrupt"]["tasks"][0]
        st.warning(f"`{t['tool']}` changes code ({t['risk']} risk) and needs a human decision before it runs.")
        c1, c2, _ = st.columns([1, 1, 5])
        if c1.button("Allow", type="primary"):
            st.session_state["wb_state"] = pipeline.resume_workbench(state["trace_id"], {"approve": True})
            st.rerun()
        if c2.button("Deny"):
            st.session_state["wb_state"] = pipeline.resume_workbench(state["trace_id"], {"approve": False})
            st.rerun()
        return
    st.markdown(f"{badge(status)} &nbsp; <span class='cq-muted'>trace <code>{esc(state['trace_id'])}</code></span>", unsafe_allow_html=True)
    if status in ("rejected", "not_understood"):
        st.info(state.get("answer") or "The request was not accepted.")
        steps = state.get("steps", [])
        if steps:
            st.dataframe(pd.DataFrame(steps), hide_index=True, width="stretch")
        return
    m = state.get("exec_metrics", {})
    stat_tiles([("Tool calls", m.get("tool_calls", 0)), ("Phases (DOS)", m.get("dos", m.get("phases", 0))), ("Max parallel (DOP)", m.get("dop", 0)),
                ("Wall time", f"{m.get('wall_ms', 0):.0f} ms"), ("Speed-up", f"x{m.get('speedup', 1)}")])
    for line in (state.get("answer") or "").splitlines():
        st.markdown(f"- {esc(line)}")
    plan_tab, policy_tab, run_tab, out_tab, val_tab = st.tabs(["1 Plan", "2 Supervisor & policy", "3 Execution", "4 Results", "5 Validation"])
    with plan_tab:
        info = state.get("plan_info", {})
        st.caption(f"Intents: {', '.join(info.get('intents', [])) or '-'} · repository: {info.get('repo')}")
        st.markdown(html_table(["Task", "Tool", "Arguments", "Depends on"], [[esc(t["id"]), f"<code>{esc(t['tool'])}</code>", f"<code>{esc(t['args'])}</code>",
                                                                            esc(", ".join(t["depends_on"]) or "-")] for t in state["plan"]]), unsafe_allow_html=True)
    with policy_tab:
        st.markdown("**Supervisor**: does the tool exist and do the arguments match its input schema?")
        st.markdown(html_table(["Task", "Tool", "Result", "Detail"], [[esc(c["task"]), f"<code>{esc(c['tool'])}</code>", badge(c["result"]), esc(c["detail"])]
                                                                      for c in state["supervisor"]["checks"]]), unsafe_allow_html=True)
        st.markdown("**PolicyGuard**: path security and risk level.")
        st.markdown(html_table(["Task", "Tool", "Risk", "Decision", "Reason"], [[esc(d["task"]), f"<code>{esc(d['tool'])}</code>", badge(d["risk"]), badge(d["decision"]),
                                                                                esc(d["reason"])] for d in state["policy"]["decisions"]]), unsafe_allow_html=True)
    with run_tab:
        st.markdown(phases_html(state["results"], state["timeline"]), unsafe_allow_html=True)
        st.caption(f"Busy time {m.get('busy_ms', 0):.0f} ms across {m.get('tool_calls', 0)} calls; wall time {m.get('wall_ms', 0):.0f} ms. "
                   f"Retries: {m.get('retries', 0)}, skipped: {m.get('skipped', 0)}, failed: {m.get('failed', 0)}.")
    with out_tab:
        results_view(state)
    with val_tab:
        v = state.get("validation", {})
        st.caption(f"{v.get('valid', 0)} of {v.get('total', 0)} tool results valid (status + output schema).")
        st.dataframe(pd.DataFrame(v.get("checks", [])), hide_index=True, width="stretch")


with st.form("request"):
    example = st.selectbox("Example", ["(write your own)"] + list(WORKBENCH_EXAMPLES), help="Lab-manual requests, normal tasks and attacks the guards must stop")
    text = st.text_input("Request", value="", placeholder="e.g. Analyze calculator.py and run its tests")
    mode = st.radio("Execution", ["parallel", "sequential"], horizontal=True, help="Independent tool calls run with asyncio.gather in parallel mode")
    go = st.form_submit_button("Run", type="primary")
if go:
    q = text.strip() or (WORKBENCH_EXAMPLES.get(example) or "")
    if q:
        s = load_settings().with_(approval_mode="manual", execution_mode=mode)
        with st.spinner("Planning and running MCP tools ..."):
            st.session_state["wb_state"] = pipeline.workbench(q, repo, s)
        st.session_state["wb_request"] = q
    else:
        st.warning("Choose an example or type a request.")
if st.session_state.get("wb_state"):
    st.markdown(f"**Request:** {esc(st.session_state.get('wb_request', ''))}")
    show(st.session_state["wb_state"])
