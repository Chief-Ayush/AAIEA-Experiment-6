"""MCP Inspector: the protocol view - sessions, tools/list, schemas, resources, and a console for calling any tool directly."""
import json

import pandas as pd
import streamlit as st

from ui.common import badge, esc, html_table, hub, page_setup, section, summary_strip

page_setup("MCP Inspector")

from reviewpilot.config import SERVERS, Settings  # noqa: E402
from reviewpilot.tracing import Trace  # noqa: E402

EXAMPLE_ARGS = {"list_files": {"repo": "calculator"}, "read_file": {"repo": "calculator", "path": "calculator.py", "start_line": 1, "end_line": 20},
                "search_code": {"repo": "inventory", "query": "def ", "regex": False}, "list_pull_requests": {"repo": "textkit"},
                "get_pull_request": {"pr_id": "INV-09"}, "get_diff": {"pr_id": "INV-09"}, "analyze_python": {"repo": "calculator", "path": "calculator.py"},
                "compare_versions": {"pr_id": "INV-09", "path": "inventory/reports.py"}, "find_usages": {"repo": "inventory", "symbol": "apply_discount"},
                "run_tests": {"repo": "calculator", "targets": ["test_calculator.py"]}, "lint": {"repo": "textkit"}, "scan_secrets": {"repo": "inventory", "pr_id": "INV-03"},
                "scan_security": {"repo": "inventory", "pr_id": "INV-06"}, "create_autofix_branch": {"pr_id": "CALC-06", "rules": ["QLT001"]},
                "delete_autofix_branch": {"pr_id": "CALC-06"}, "post_review": {"pr_id": "CALC-09", "verdict": "APPROVE", "summary": "Docs only."},
                "get_review": {"pr_id": "CALC-09"}}

h = hub()
st.markdown("## MCP inspector")

section("Sessions")
st_ = h.server_status()
rows = []
for s in st_:
    c = h.conns[s["server"]]
    caps = ", ".join(k for k in c.capabilities) or "-"
    rows.append([f"<b>{esc(s['server'])}</b>", esc(s.get("server_name", "-")), esc(s.get("server_version", "-")), esc(s.get("server_protocol", "-")),
                 esc(caps), s["tools"], s["connects"], f"{s['connect_ms']:.0f}", badge("alive" if s["alive"] else "down")])
st.markdown(html_table(["Server", "serverInfo.name", "Version", "Protocol", "Capabilities", "Tools", "Connects", "Connect (ms)", "Session"], rows,
                       num_cols=(5, 6, 7)), unsafe_allow_html=True)
c1, c2, _ = st.columns([1.2, 1, 3])
which = c1.selectbox("Server", list(SERVERS), label_visibility="collapsed")
if c2.button("Restart server"):
    h.run(h._reconnect(which, force=True))
    st.rerun()
summary_strip([("Tool calls this session", h.stats["calls"]), ("Reconnects", h.stats["reconnects"]), ("Fresh connections", h.stats["fresh_connections"])])

section("Tools")
tools = h.tools()
df = pd.DataFrame([{"tool": t.name, "server": t.server, "risk": t.risk, "read only": t.annotations.get("readOnlyHint"),
                    "destructive": t.annotations.get("destructiveHint"), "inputs": ", ".join(t.input_schema.get("properties", {})),
                    "required": ", ".join(t.input_schema.get("required", [])), "structured output": bool(t.output_schema)} for t in tools.values()])
st.dataframe(df.sort_values(["server", "tool"]), hide_index=True, width="stretch")

section("Call a tool")
c1, c2 = st.columns([1, 1.4])
with c1:
    tool = st.selectbox("Tool", sorted(tools), index=sorted(tools).index("read_file"))
    info = tools[tool]
    st.markdown(f"{badge(info.risk)} <span class='cq-muted'>{esc(info.server)} server</span><br>{esc(info.description)}", unsafe_allow_html=True)
    args_txt = st.text_area("Arguments (JSON)", json.dumps(EXAMPLE_ARGS.get(tool, {}), indent=1), height=150, key=f"args_{tool}")
    validate = st.toggle("Client-side schema validation", value=True)
    go = st.button("Send tools/call", type="primary")
with c2:
    t1, t2 = st.tabs(["Input schema", "Output schema"])
    t1.json(info.input_schema, expanded=2)
    t2.json(info.output_schema or {}, expanded=1)
if go:
    try:
        args = json.loads(args_txt or "{}")
    except json.JSONDecodeError as exc:
        st.error(f"Invalid JSON: {exc}")
        st.stop()
    tr = Trace()
    r = h.call(tool, args, settings=Settings(validate_arguments=validate), trace=tr, agent="Inspector")
    summary_strip([("Result", badge("success" if r.ok else "failed")), ("Category", esc(r.category or "-")), ("Attempts", r.attempts),
                   ("Time", f"{r.duration_ms:.1f} ms"), ("Output schema", badge("valid") if r.output_valid else ("-" if r.output_valid is None else badge("schema mismatch"))),
                   ("Task", esc(f"{tr.trace_id} {r.task_id}"))])
    if r.ok:
        st.json(r.data, expanded=2)
    else:
        st.error(r.error)

section("Resources")
res = h.resources()
st.markdown(html_table(["Server", "URI", "Kind", "Description"], [[esc(x["server"]), f"<code>{esc(x['uri'])}</code>", esc(x["kind"]), esc(x["description"] or "")]
                                                                 for x in res]), unsafe_allow_html=True)
c1, c2 = st.columns([1, 3])
uri = c1.text_input("URI", "autofix://rules")
srv = next((x["server"] for x in res if x["uri"].split("://")[0] == uri.split("://")[0]), "automation")
if c1.button("resources/read"):
    try:
        c2.code(h.read_resource(srv, uri), language=None)
    except Exception as exc:  # noqa: BLE001
        c2.error(str(exc))
