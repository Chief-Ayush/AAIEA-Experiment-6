"""End-to-end workflow tests (LangGraph + MCP)."""
from reviewpilot import pipeline
from reviewpilot.config import PATHS, Settings
from reviewpilot.servers.fixes import delete_fix_branch

OFF = Settings(approval_mode="off")


def rules(st):
    return {(f["rule"], f["path"]) for f in st["final_findings"]}


def test_review_dangerous_call():
    st = pipeline.review("INV-09", OFF, persist=False)
    assert st["human"]["final_verdict"] == "REQUEST_CHANGES"
    assert ("SEC002", "inventory/notify.py") in rules(st) and ("QLT004", "inventory/reports.py") in rules(st)
    assert st["exec_metrics"]["phases"] >= 3 and st["exec_metrics"]["dop"] >= 4


def test_review_failing_tests_skips_full_suite():
    st = pipeline.review("CALC-02", OFF, persist=False)
    assert ("TST001", "test_calculator.py") in rules(st)
    runs = {r["purpose"].split(" ")[0]: r for r in st["results"].values() if r["tool"] == "run_tests"}
    assert runs["full"]["status"] == "skipped"
    assert runs["re-run"]["status"] == "success" and runs["re-run"]["data"]["status"] == "passed"   # passes on the base -> new failure


def test_review_secret_blocks_and_masks():
    st = pipeline.review("INV-03", OFF, persist=False)
    assert st["human"]["final_verdict"] == "BLOCK"
    assert all("*" in f["evidence"] for f in st["final_findings"] if f["rule"] == "SEC001")


def test_prompt_injection_is_data():
    st = pipeline.review("CALC-07", OFF, persist=False)
    assert ("SEC003", "PR description") in rules(st) and st["human"]["final_verdict"] != "APPROVE"


def test_clean_pr_approved():
    st = pipeline.review("TXT-01", OFF, persist=False)
    assert st["human"]["final_verdict"] == "APPROVE" and not st["final_findings"]


def test_rejected_input():
    assert pipeline.review("../x", OFF, persist=False)["status"] == "rejected"


def test_human_in_the_loop_autofix():
    st = pipeline.review("CALC-06", Settings(approval_mode="manual"), persist=False)
    assert st["status"] == "awaiting_human" and st["interrupt"]["fixable_rules"] == ["QLT001"]
    st = pipeline.resume_review(st["trace_id"], {"action": "approve", "autofix": ["QLT001"]})
    try:
        assert st["status"] == "completed" and st["fix"]["status"] == "created" and st["fix"]["tests"]["status"] == "passed"
    finally:
        delete_fix_branch("CALC-06")
        (PATHS.reviews / "CALC-06.json").unlink(missing_ok=True)


def test_workbench_lab_request():
    st = pipeline.workbench("Analyze calculator.py and run its tests", "calculator", OFF, persist=False)
    assert st["status"] == "completed" and len(st["exec_metrics"] and st["timeline"]) == 2


def test_workbench_denies_traversal_and_high_risk():
    st = pipeline.workbench("Read ../../etc/passwd", "calculator", OFF, persist=False)
    assert st["policy"]["denied"] and not st["trace"]
    st = pipeline.workbench("Auto-fix the debug prints in CALC-06", "calculator", Settings(approval_mode="auto"), persist=False)
    assert st["policy"]["denied"]
