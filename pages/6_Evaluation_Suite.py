"""Evaluation Suite: run the evaluation sections and store a report."""
import pandas as pd
import streamlit as st

from ui.common import esc, html_table, hub, page_setup, section, stat_tiles

page_setup("Evaluation Suite")

from reviewpilot.evaluation.runner import ALL_SECTIONS, SECTION_INFO, WEIGHTS, list_reports, run_all  # noqa: E402

hub()
st.markdown("## Run evaluation")

section("Sections")
chosen = []
for s in ALL_SECTIONS:
    if st.checkbox(s.capitalize(), value=True, key=f"sec_{s}", help=SECTION_INFO[s]):
        chosen.append(s)
c1, c2 = st.columns([1, 3])
workers = c1.number_input("Concurrent reviews (benchmark)", 1, 4, 1)
c2.caption("All sections take about 4 minutes.")
if st.button("Run evaluation", type="primary", disabled=not chosen):
    bar = st.progress(0.0)
    rep = run_all(chosen, progress=lambda f, m: bar.progress(f, text=m), workers=int(workers))
    bar.empty()
    h = rep["headline"]
    st.success(f"Finished in {rep['total_s']} s · report {rep['report_dir']}")
    stat_tiles([("Quality index", h.get("quality_index")), ("Verdict accuracy", f"{h.get('verdict_accuracy_pct') or 0:.1f}%"),
                ("Finding F1", f"{h.get('finding_f1_pct') or 0:.1f}%"), ("Security", f"{h.get('security_pass_pct') or 0:.1f}%"),
                ("Failure handling", f"{h.get('failure_pass_pct') or 0:.1f}%"), ("HITL", f"{h.get('hitl_pass_pct') or 0:.1f}%")])

section("Quality index")
st.markdown(html_table(["Metric", "Weight"], [[esc(k.replace("_pct", " (%)").replace("_", " ")), f"{w:.2f}"] for k, w in WEIGHTS], num_cols=(1,)),
            unsafe_allow_html=True)

section("Previous runs")
reps = list_reports()
if reps:
    st.dataframe(pd.DataFrame([{"run": r["run"], "generated": r["generated_at"], "sections": ", ".join(r["sections"] or []), "time (s)": r["total_s"],
                                "quality index": r["headline"].get("quality_index"), "verdict accuracy": r["headline"].get("verdict_accuracy_pct"),
                                "finding F1": r["headline"].get("finding_f1_pct"), "security": r["headline"].get("security_pass_pct")} for r in reps]),
                 hide_index=True, width="stretch")
else:
    st.caption("No evaluation has been run yet.")
