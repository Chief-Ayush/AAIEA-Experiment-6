"""Code: browse the selected repository through the MCP repository and analysis servers."""
import streamlit as st

from ui.common import badge, code_html, current_repo, esc, hub, page_setup, summary_strip

page_setup("Code")

h = hub()
repo = current_repo()
st.markdown(f"## Code <span class='cq-muted' style='font-size:1rem;font-weight:400'>{esc(repo)}</span>", unsafe_allow_html=True)

res = h.call("list_files", {"repo": repo})
if not res.ok:
    st.error(res.error)
    st.stop()
paths = [f["path"] for f in res.data["files"]]
c1, c2 = st.columns([1, 2.6])
with c1:
    q = st.text_input("Filter", placeholder="Filter files", label_visibility="collapsed")
    shown = [p for p in paths if q.lower() in p.lower()] or paths
    default = "README.md" if "README.md" in shown else shown[0]
    path = st.radio("Files", shown, index=shown.index(default), label_visibility="collapsed")
    if st.button("Run tests", width="stretch"):
        with st.spinner("Running pytest ..."):
            st.session_state[f"tests_{repo}"] = h.call("run_tests", {"repo": repo})
    t = st.session_state.get(f"tests_{repo}")
    if t is not None:
        st.markdown(badge(t.data["status"]) + f" {t.data['passed']}/{t.data['collected']} passed" if t.ok else badge("error"), unsafe_allow_html=True)
with c2:
    r = h.call("read_file", {"repo": repo, "path": path})
    if not r.ok:
        st.error(r.error)
        st.stop()
    if path.endswith(".py"):
        a = h.call("analyze_python", {"repo": repo, "path": path})
        if a.ok:
            d = a.data
            summary_strip([("Lines", d["lines"]), ("Functions", d["function_count"]), ("Classes", d["class_count"]),
                           ("Max complexity", d["max_complexity"]), ("Unused imports", len(d["unused_imports"]))])
    if path.endswith(".md"):
        st.markdown(r.data["content"])
    else:
        st.markdown(code_html(r.data["content"]), unsafe_allow_html=True)
