# Experiment 6 report - note

Deliverables: Experiment_6.docx, Experiment_6.pdf (13 pages), images/, scripts (diagrams.py, shots3-5.py, build_doc.py), eval_output.txt.

## What was rebuilt
reviewpilot/benchmark/ (repos.py, catalog.py, build.py, __init__.py) was missing (git-ignored, never committed). It was reconstructed from the code,
tests and stored reports: 3 sample projects (all tests pass on main) and 62 labelled PRs (15 CALC, 24 INV, 23 TXT), labels written by hand.
`python -m reviewpilot.benchmark.build` creates data/repos, data/prs, data/eval/pr_labels.json. The secrets in the PRs are fake strings.
The reconstruction is NOT the original benchmark; the report says so.

## Commands run
pip install -r requirements.txt python-docx matplotlib playwright pymupdf; python -m playwright install chromium
python -m reviewpilot.benchmark.build
python -m pytest                     -> 34 passed
python -m reviewpilot.cli eval       -> storage/reports/run_20261022_125905 (248 s)
python -m reviewpilot.cli review CALC-01   (trace TRACE-C2F9EF2C for the trace table)
streamlit run Home.py + Playwright screenshots; python diagrams.py; python build_doc.py; soffice --headless --convert-to pdf

## Numbers (own run)
quality index 97.1; verdict accuracy 95.2%; finding P/R/F1 100/95.5/97.7; workbench plan/outcome 93.8%, tool F1 97.2%; security 96.2% (25/26, SEC-23 fails);
failure 100% (16/16); HITL 100% (12/12); Variation 9: 73.5 ms sequential vs 59.1 ms parallel = 1.24x; review avg DOP 5.06 (benchmark) / 5.5 (sampled PRs).
Expected misses: INV-15, TXT-12, TXT-16; workbench WB-28, WB-32.
Differences from your figures (98.0 / 100% workbench / 1.19x / DOP 5.15): your original benchmark and code state differ from this reconstruction.
