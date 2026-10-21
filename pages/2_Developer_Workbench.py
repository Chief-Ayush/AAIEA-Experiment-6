"""Ask: code tasks in plain language, answered through MCP tool calls."""
import pandas as pd
import streamlit as st

from ui.common import badge, code_html, current_repo, esc, html_table, hub, page_setup, phases_html

page_setup("Ask")

from reviewpilot import pipeline  # noqa: E402
from reviewpilot.config import load_settings  # noqa: E402

hub()
repo = current_repo()
chat = st.session_state.setdefault("chat", [])
st.markdown(f"## Ask <span class='cq-muted' style='font-size:1rem;font-weight:400'>{esc(repo)}</span>", unsafe_allow_html=True)

EXAMPLES = ["Analyze calculator.py and run its tests", "Find all function definitions", "Scan for hard-coded secrets", "Run the test suite",
            "Lint the project", "List the files"]


def details(st_):
    r = st_["results"]
    for x in r.values():
        if x["status"] != "success":
            continue
        d, t = x["data"], x["tool"]
        if t == "read_file":
            st.markdown(code_html(d["content"], d["start_line"]), unsafe_allow_html=True)
        elif t == "analyze_python":
            st.dataframe(pd.DataFrame(d["functions"])[["name", "line", "complexity", "docstring"]], hide_index=True, width="stretch")
        elif t == "search_code" and d["matches"]:
            st.dataframe(pd.DataFrame(d["matches"]), hide_index=True, width="stretch")
        elif t == "list_files":
            st.dataframe(pd.DataFrame(d["files"]), hide_index=True, width="stretch")
        elif t == "run_tests" and d["failures"]:
            st.dataframe(pd.DataFrame(d["failures"]), hide_index=True, width="stretch")
        elif t == "find_usages" and d["usages"]:
            st.dataframe(pd.DataFrame(d["usages"]), hide_index=True, width="stretch")
        elif t == "lint" and d["issues"]:
            st.dataframe(pd.DataFrame(d["issues"]), hide_index=True, width="stretch")
        elif t in ("scan_secrets", "scan_security") and d["findings"]:
            st.dataframe(pd.DataFrame(d["findings"]), hide_index=True, width="stretch")
        elif t == "get_diff":
            for fd in d["files"]:
                st.code(fd["patch"], language="diff")
    with st.expander("How it ran"):
        st.markdown(html_table(["Tool", "Arguments", "Policy"], [[f"<code>{esc(d['tool'])}</code>",
                                                                  f"<code>{esc(next((t['args'] for t in st_['plan'] if t['id'] == d['task']), ''))}</code>",
                                                                  badge(d["decision"])] for d in st_["policy"]["decisions"]]), unsafe_allow_html=True)
        st.markdown(phases_html(st_["results"], st_["timeline"]), unsafe_allow_html=True)


def show(i, item):
    with st.chat_message("user"):
        st.write(item["q"])
    s = item["state"]
    with st.chat_message("assistant"):
        if s["status"] == "awaiting_human":
            t = s["interrupt"]["tasks"][0]
            st.warning(f"`{t['tool']}` changes code ({t['risk']} risk). Allow it?")
            c1, c2, _ = st.columns([1, 1, 5])
            if c1.button("Allow", key=f"allow{i}", type="primary"):
                item["state"] = pipeline.resume_workbench(s["trace_id"], {"approve": True})
                st.rerun()
            if c2.button("Deny", key=f"deny{i}"):
                item["state"] = pipeline.resume_workbench(s["trace_id"], {"approve": False})
                st.rerun()
            return
        if s["status"] in ("rejected", "not_understood"):
            st.write(s.get("answer") or "I can't do that.")
            return
        for line in (s.get("answer") or "").splitlines():
            st.markdown(f"- {esc(line)}")
        if i == len(chat) - 1:
            details(s)


for i, item in enumerate(chat):
    show(i, item)

pick = st.pills("Try", EXAMPLES, label_visibility="collapsed") if not chat else None
q = st.chat_input("Ask about the code, e.g. 'Where is divide used?'") or pick
if q:
    s = load_settings().with_(approval_mode="manual")
    with st.spinner("Working ..."):
        res = pipeline.workbench(q, repo, s)
    chat.append({"q": q, "state": res})
    st.rerun()
if chat and st.button("Clear", type="tertiary"):
    chat.clear()
    st.rerun()
