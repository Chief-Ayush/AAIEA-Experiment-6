"""ReviewPilot: `streamlit run Home.py`."""
import streamlit as st

st.set_page_config(page_title="ReviewPilot", layout="wide")

from ui.common import page_setup, sidebar  # noqa: E402

page_setup()
sidebar()

nav = st.navigation({
    "": [st.Page("pages/1_Review_Pull_Request.py", url_path="pulls", title="Pull requests", icon=":material/rate_review:", default=True),
         st.Page("pages/2_Developer_Workbench.py", url_path="workbench", title="Workbench", icon=":material/terminal:"),
         st.Page("pages/3_Repositories.py", url_path="code", title="Code", icon=":material/code:"),
         st.Page("pages/10_Settings.py", url_path="settings", title="Settings", icon=":material/settings:")],
    "Lab": [st.Page("pages/0_Overview.py", url_path="how-it-works", title="How it works", icon=":material/account_tree:"),
            st.Page("pages/5_Batch_Testing.py", url_path="batch-testing", title="Batch testing", icon=":material/playlist_play:"),
            st.Page("pages/7_Evaluation_Report.py", url_path="evaluation", title="Evaluation", icon=":material/analytics:"),
            st.Page("pages/6_Evaluation_Suite.py", url_path="run-evaluation", title="Run evaluation", icon=":material/fact_check:"),
            st.Page("pages/8_Security_Testing.py", url_path="security", title="Security tests", icon=":material/shield:"),
            st.Page("pages/4_MCP_Inspector.py", url_path="inspector", title="MCP inspector", icon=":material/hub:"),
            st.Page("pages/9_Traces_and_Logs.py", url_path="traces", title="Traces", icon=":material/receipt_long:")],
})
nav.run()
