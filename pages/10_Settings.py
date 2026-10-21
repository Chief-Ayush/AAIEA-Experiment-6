"""Settings: GitHub connection, demo repositories and review defaults."""
import pandas as pd
import streamlit as st

from ui.common import badge, esc, github_status, page_setup, restart_hub

page_setup("Settings")

from reviewpilot import github as gh  # noqa: E402
from reviewpilot.config import load_settings, save_env_value, save_settings  # noqa: E402

st.markdown("## Settings")

# ------------------------------------------------------------------------------------------------ GitHub
st.markdown("#### GitHub")
gs = github_status()
src = {"settings": "saved token", "GH_TOKEN": "GH_TOKEN variable", "GitHub CLI": "GitHub CLI"}.get(gs["source"], "")
if gs["connected"]:
    st.markdown(f"{badge('connected', 'good')} &nbsp; **@{esc(gs['login'])}** <span class='cq-muted'>via {esc(src)}</span>", unsafe_allow_html=True)
elif gs["error"]:
    st.markdown(f"{badge('error', 'bad')} &nbsp; {esc(gs['error'])}", unsafe_allow_html=True)
else:
    st.markdown(badge("not connected", "warn"), unsafe_allow_html=True)

with st.form("token"):
    tok = st.text_input("Personal access token", type="password", placeholder="ghp_... or github_pat_...",
                        help="Classic token with the 'repo' scope, or a fine-grained token with Contents, Pull requests and Administration (for "
                             "the demo) read & write. Stored only in this folder's .env file.")
    c1, c2, _ = st.columns([1, 1, 4])
    save = c1.form_submit_button("Save", type="primary")
    remove = c2.form_submit_button("Remove")
st.caption("[Create a token](https://github.com/settings/tokens/new?scopes=repo&description=ReviewPilot) · "
           "or run `gh auth login` - the GitHub CLI token is used when no token is saved.")
if save and tok.strip():
    save_env_value("GITHUB_TOKEN", tok.strip())
    restart_hub()
    st.rerun()
if remove:
    save_env_value("GITHUB_TOKEN", None)
    restart_hub()
    st.rerun()

# ------------------------------------------------------------------------------------------------ demo
st.markdown("#### Demo repositories")
st.caption("Creates the three sample projects in your account with 13 open pull requests to review.")
c1, c2, c3 = st.columns([1.4, 1, 1.2])
prefix = c1.text_input("Name prefix", "reviewpilot-", label_visibility="collapsed")
private = c2.toggle("Private", value=True)
if c3.button("Create demo", type="primary", disabled=not gs["connected"], width="stretch"):
    from reviewpilot.github_setup import create_demo
    bar = st.progress(0.0)
    try:
        out = create_demo(prefix=prefix.strip() or "reviewpilot-", private=private, progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
        st.session_state["demo"] = out
    except gh.GitHubError as exc:
        st.error(str(exc))
    bar.empty()
    restart_hub()
out = st.session_state.get("demo")
if out:
    st.success(f"{len(out['repos'])} repositories, {len(out['pull_requests'])} pull requests. Pick a repository in the sidebar.")
    st.dataframe(pd.DataFrame([{"Repository": p["repo"], "PR": p["number"], "Title": p["title"], "Link": p["url"]} for p in out["pull_requests"]]),
                 hide_index=True, width="stretch", column_config={"Link": st.column_config.LinkColumn(display_text="open")})

# ------------------------------------------------------------------------------------------------ defaults
st.markdown("#### Reviews")
s = load_settings()
with st.form("defaults"):
    ask = st.toggle("Ask before posting", value=s.approval_mode != "auto")
    full = st.toggle("Run the full test suite after the affected tests pass", value=s.full_suite_after_affected)
    par = st.toggle("Run independent tools in parallel", value=s.execution_mode == "parallel")
    timeout = st.slider("Test timeout (seconds)", 10, 120, int(s.test_timeout_s), step=5)
    if st.form_submit_button("Save"):
        save_settings(s.with_(approval_mode="manual" if ask else "auto", full_suite_after_affected=full,
                              execution_mode="parallel" if par else "sequential", test_timeout_s=float(timeout)))
        st.toast("Saved")
