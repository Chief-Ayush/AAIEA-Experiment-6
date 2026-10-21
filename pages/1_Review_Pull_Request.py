"""Pull requests: pick one, review it, decide what gets posted."""
import json

import pandas as pd
import streamlit as st

from ui.common import (agent_flow_html, badge, current_repo, diff_html, esc, github_status, html_table, hub, page_setup, phases_html,
                       summary_strip, timeline_chart, trace_frame, verdict_banner)

page_setup("Pull requests")

from reviewpilot import pipeline  # noqa: E402
from reviewpilot.config import PATHS, VERDICTS, load_settings  # noqa: E402
from reviewpilot.servers.fixes import AUTO_FIXABLE  # noqa: E402
from reviewpilot.workspace import normalize_pr_id, safe_name  # noqa: E402

h = hub()
repo = current_repo()
is_gh = "/" in repo
reviews = st.session_state.setdefault("reviews", {})

st.markdown(f"## Pull requests <span class='cq-muted' style='font-size:1rem;font-weight:400'>{esc(repo)}</span>", unsafe_allow_html=True)


def last_verdict(pid: str) -> str:
    p = PATHS.reviews / f"{safe_name(pid)}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["verdict"].replace("_", " ").title()
    except (OSError, ValueError, KeyError):
        return ""


res = h.call("list_pull_requests", {"repo": repo})
if not res.ok:
    st.error(res.error)
    if not github_status()["connected"]:
        st.page_link("pages/10_Settings.py", label="Connect GitHub in Settings")
    st.stop()
prs = res.data["pull_requests"]

c1, c2 = st.columns([3, 1.2])
pasted = c2.text_input("Open a PR", placeholder="Paste a PR link", label_visibility="collapsed")
if not prs and not pasted:
    c1.info("No open pull requests in this repository.")
    st.stop()

if prs:
    df = pd.DataFrame([{"PR": p["id"].split("#")[-1] if is_gh else p["id"], "Title": p["title"], "Author": p["author"],
                        "Updated": (p["updated"] or "")[:10] if is_gh else "", "Last review": last_verdict(p["id"])} for p in prs])
    if not is_gh:
        df = df.drop(columns=["Updated"])
    with c1:
        sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"prs_{repo}",
                           height=min(35 * (len(df) + 1) + 3, 330))
    rows = sel.selection.rows if sel and sel.selection else []
    chosen = prs[rows[0]]["id"] if rows else None
else:
    chosen = None
pid = normalize_pr_id(pasted) if pasted else (chosen or st.session_state.get(f"pr_{repo}"))
if not pid:
    st.caption("Select a pull request.")
    st.stop()
st.session_state[f"pr_{repo}"] = pid

# ------------------------------------------------------------------------------------------------ the selected PR
info = h.call("get_pull_request", {"pr_id": pid})
if not info.ok:
    st.error(info.error)
    st.stop()
pr = info.data
st.divider()
c1, c2, c3 = st.columns([5, 1.1, 1.1])
c1.markdown(f"### {esc(pr['title'])}\n<span class='cq-muted'>{esc(pr['id'])} · {esc(pr['author'])} · {len(pr['files'])} files · "
            f"+{pr['additions']} −{pr['deletions']}</span>", unsafe_allow_html=True)
if pr["url"]:
    c3.link_button("Open on GitHub", pr["url"], width="stretch")
defaults = load_settings()
with c2.popover("Options", width="stretch"):
    ask = st.toggle("Ask before posting", value=defaults.approval_mode != "auto", key="opt_ask")
    mode = st.radio("Run tools", ["parallel", "sequential"], index=0 if defaults.execution_mode == "parallel" else 1, horizontal=True, key="opt_mode")
state = reviews.get(pid)
if st.button("Review" if not state else "Review again", type="primary"):
    s = defaults.with_(approval_mode="manual" if ask else "auto", execution_mode=mode)
    with st.spinner("Reviewing - planning, calling MCP tools, running tests ..."):
        state = pipeline.review(pid, s)
    reviews[pid] = state
if not state:
    st.stop()
if state["status"] == "rejected":
    st.error(state["guard"].get("reason"))
    st.stop()

# ------------------------------------------------------------------------------------------------ result
v = state["verdict"]
human = state.get("human", {})
findings = state.get("final_findings") or state.get("validated", [])
verdict_banner(human.get("final_verdict") or v["verdict"], v["summary"], v["score"], v["confidence"])
for note in v["notes"]:
    st.caption(note)
if state.get("guard", {}).get("injection"):
    st.warning("The PR text tries to instruct the reviewer. It was ignored and flagged (SEC003).")


def finding_rows(items, fixed=()):
    return [[badge(f["severity"]) + (" " + badge("fixed", "good") if f["rule"] in fixed else ""),
             f"<b>{esc(f['title'])}</b><br><span class='cq-muted'>{esc(f['message'])}</span>",
             f"<code>{esc(f['path'])}{(':' + str(f['line'])) if f.get('line') else ''}</code>"] for f in items]


if state["status"] == "awaiting_human":
    with st.form("decide"):
        if findings:
            ed = st.data_editor(pd.DataFrame([{"Ignore": False, "Severity": f["severity"], "Finding": f["title"],
                                               "Where": f"{f['path']}{(':' + str(f['line'])) if f.get('line') else ''}", "Detail": f["message"]}
                                              for f in findings]),
                                hide_index=True, width="stretch", disabled=["Severity", "Finding", "Where", "Detail"], key=f"ed_{state['trace_id']}",
                                column_config={"Ignore": st.column_config.CheckboxColumn(width="small"), "Detail": st.column_config.TextColumn(width="large")})
        else:
            st.success("No findings.")
        fixable = v["fixable_rules"]
        c1, c2 = st.columns(2)
        fix = c1.multiselect("Fix automatically", fixable, format_func=lambda r: AUTO_FIXABLE.get(r, r), disabled=not fixable,
                             placeholder="Nothing to fix" if not fixable else "Choose fixes",
                             help="Tests run on the fixed code first" + ("; then the fix is pushed to the PR branch." if is_gh else "."))
        verdict = c2.selectbox("Verdict", VERDICTS, index=VERDICTS.index(v["verdict"]), format_func=lambda x: x.replace("_", " ").title())
        note = st.text_input("Note", placeholder="Optional message for the author")
        b1, b2, _ = st.columns([1.3, 1, 4])
        post = b1.form_submit_button("Post to GitHub" if is_gh else "Post review", type="primary")
        skip = b2.form_submit_button("Don't post")
    if post or skip:
        dec = {"action": "approve" if post else "reject", "autofix": fix if post else [], "note": note, "by": github_status()["login"] or "reviewer",
               "dismiss": [i for i, x in enumerate(ed["Ignore"]) if x] if findings else []}
        if verdict != v["verdict"]:
            dec["verdict_override"] = verdict
        with st.spinner("Posting ..." if post else "Closing ..."):
            reviews[pid] = pipeline.resume_review(state["trace_id"], dec)
        st.rerun()
else:
    posted, fx = state.get("posted") or {}, state.get("fix") or {}
    if human.get("action") == "reject":
        st.info("Not posted.")
    elif posted and "error" in posted:
        st.error(f"Posting failed: {posted['error']}")
    elif posted:
        url = posted.get("url")
        st.success("Review posted" + (f" · [view on GitHub]({url})" if url else ""))
    if fx.get("status") == "pushed":
        st.success(f"Fix pushed as commit `{fx['commit'][:7]}` · [view]({fx['commit_url']})")
    elif fx.get("status") in ("created", "created_tests_still_failing"):
        st.info(f"Fix prepared on branch `{fx['branch']}` (tests: {fx.get('tests', {}).get('status')}).")
    elif fx.get("status"):
        st.warning(f"Fix not applied: {fx['status'].replace('_', ' ')}" + (f" - {fx.get('error')}" if fx.get("error") else ""))
    if findings:
        fixed = fx.get("rules", []) if fx.get("status") == "pushed" else []
        st.markdown(html_table(["", "Finding", "Where"], finding_rows(findings, fixed)), unsafe_allow_html=True)

# ------------------------------------------------------------------------------------------------ details
t1, t2, t3 = st.tabs(["Changes", "Tests", "How it ran"])
with t1:
    diff = next((r["data"] for r in state["results"].values() if r["tool"] == "get_diff" and r.get("ok")), None)
    flagged = {}
    for f in findings:
        if f.get("line"):
            flagged.setdefault(f["path"], set()).add(f["line"])
    for fd in (diff or {}).get("files", []):
        st.markdown(diff_html(fd, flagged.get(fd["path"])), unsafe_allow_html=True)
with t2:
    tr = state.get("test_report", {})
    st.markdown(html_table(["On", "Tests", "Result", "Passed", "Failed", "Seconds"],
                           [[esc(r.get("on", "PR")), esc(", ".join(r["targets"]) if isinstance(r.get("targets"), list) else (r.get("targets") or "-")),
                             badge(r["status"]), r.get("passed", "-"), r.get("failed", "-"), r.get("duration_s", "-")] for r in tr.get("runs", [])],
                           num_cols=(3, 4, 5)), unsafe_allow_html=True)
    for r in state["results"].values():
        if r["tool"] == "run_tests" and r.get("ok") and r["data"]["failures"] and (r.get("args") or {}).get("ref") != "base":
            st.dataframe(pd.DataFrame(r["data"]["failures"]), hide_index=True, width="stretch")
with t3:
    m = state["exec_metrics"]
    summary_strip([("MCP calls", len(state["trace"])), ("Phases", m["phases"]), ("Parallel calls (DOP)", m["dop"]), ("Time", f"{m['wall_ms']:.0f} ms"),
                   ("Retries", m["retries"])])
    st.markdown(phases_html(state["results"], state["timeline"]), unsafe_allow_html=True)
    ch = timeline_chart(state["trace"], executor_only=True)
    if ch is not None:
        st.altair_chart(ch, width="stretch")
    st.markdown(agent_flow_html(state["steps"]), unsafe_allow_html=True)
    with st.expander("Agents"):
        st.markdown(html_table(["Agent", "ms", "Output"], [[f"<b>{esc(s['agent'])}</b>", f"{s['ms']:.0f}", esc(s["summary"])] for s in state["steps"]],
                               num_cols=(1,)), unsafe_allow_html=True)
    with st.expander("Policy"):
        st.markdown(html_table(["Tool", "Risk", "Decision"], [[f"<code>{esc(d['tool'])}</code>", badge(d["risk"]), badge(d["decision"])]
                                                              for d in state["policy"]["decisions"]]), unsafe_allow_html=True)
    with st.expander("Trace"):
        st.dataframe(trace_frame(state["trace"]), hide_index=True, width="stretch")
