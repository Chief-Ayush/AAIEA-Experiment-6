"""How it works: MCP architecture, the review workflow and every agent."""
import streamlit as st

from ui.common import badge, esc, html_table, hub, page_setup, stat_tiles

page_setup("How it works")

from reviewpilot.agent_catalog import AGENTS, SERVERS_INFO, STAGES  # noqa: E402
from reviewpilot.config import SERVERS  # noqa: E402
from reviewpilot.evaluation.runner import load_report  # noqa: E402
from reviewpilot.graph import graph_dot  # noqa: E402
from reviewpilot.mcp_client import CATEGORY_ACTION  # noqa: E402
from reviewpilot.rules import RULES  # noqa: E402

h = hub()
st.markdown("## How it works")
st.caption("Experiment 6 · Developer automation over the Model Context Protocol")

rep = load_report()
if rep:
    hd = rep["headline"]
    stat_tiles([("Verdict accuracy", f"{hd.get('verdict_accuracy_pct', 0):.1f}%"), ("Finding F1", f"{hd.get('finding_f1_pct', 0):.1f}%"),
                ("Workbench plans", f"{hd.get('workbench_plan_accuracy_pct', 0):.1f}%"), ("Security", f"{hd.get('security_pass_pct', 0):.1f}%"),
                ("Failure handling", f"{hd.get('failure_pass_pct', 0):.1f}%"), ("Quality index", f"{hd.get('quality_index', 0):.1f}")])

tools = h.tools()
st.markdown("#### MCP servers")
st.markdown(html_table(["Server", "Does", "Tools", ""],
                       [[f"<b>{esc(SERVERS[s['server']]['title'])}</b>", esc(SERVERS_INFO[s["server"]]),
                         " ".join(f"<code>{esc(t.name)}</code>" for t in sorted(tools.values(), key=lambda t: t.name) if t.server == s["server"]),
                         badge("alive" if s["alive"] else "down")] for s in h.server_status()]), unsafe_allow_html=True)

st.markdown("#### Review workflow")
c1, c2 = st.columns([1.5, 1])
with c1:
    st.markdown(html_table(["#", "Agent", "Runs", "Does"], [[s["stage"], f"<b>{esc(s['agent'])}</b>", esc(s["execution"]), esc(s["step"])] for s in STAGES],
                           num_cols=(0,)), unsafe_allow_html=True)
with c2:
    kind = st.segmented_control("Workflow", ["review", "workbench"], default="review", label_visibility="collapsed")
    st.graphviz_chart(graph_dot(kind or "review"), width="stretch")

st.markdown("#### Agents")
for a in AGENTS:
    with st.expander(f"{a['name']} · {a['purpose']}"):
        c1, c2 = st.columns([1.4, 1])
        c1.markdown("\n".join(f"- {r}" for r in a["responsibilities"]))
        c2.markdown(f"**Tools** {', '.join(f'`{t}`' for t in a['tools']) or '-'}  \n**Produces** {a['produces']}  \n**On failure** {a['on_failure']}")

c1, c2 = st.columns(2)
with c1:
    st.markdown("#### Failure handling")
    st.markdown(html_table(["Error", "Action"], [[badge(k, "warn" if k in ("INPUT", "TOOL") else "bad" if k == "SECURITY" else "info"), esc(v)]
                                                 for k, v in CATEGORY_ACTION.items()]), unsafe_allow_html=True)
with c2:
    st.markdown("#### Tool risk")
    st.markdown(html_table(["Risk", "Tools"], [[badge(r), " ".join(f"<code>{esc(t.name)}</code>" for t in tools.values() if t.risk == r)]
                                               for r in ("LOW", "MEDIUM", "HIGH")]), unsafe_allow_html=True)
with st.expander("Review rules"):
    st.markdown(html_table(["Rule", "Severity", "Finding", "Auto-fix"], [[f"<b>{r}</b>", badge(v[0]), esc(v[1]), "yes" if v[3] else ""]
                                                                       for r, v in RULES.items()]), unsafe_allow_html=True)
