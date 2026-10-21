"""Fast unit tests: path security, planning, scheduling, rules."""
import pytest

from reviewpilot.planning import plan_request, schedule, task
from reviewpilot.rules import make_finding, verdict_for
from reviewpilot.workspace import SecurityError, repo_root, safe_project_path


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini", "a/../../x", "calc.py\x00.txt"])
def test_safe_project_path_rejects(bad):
    with pytest.raises(SecurityError):
        safe_project_path(repo_root("calculator"), bad)


def test_safe_project_path_accepts():
    assert safe_project_path(repo_root("calculator"), "calculator.py").name == "calculator.py"


def test_schedule_layers_dependencies():
    ts = [task("A", "x", {}, ""), task("B", "x", {}, ""), task("C", "x", {}, "", depends_on=["A", "B"]), task("D", "x", {}, "", depends_on=["C"])]
    assert [[t["id"] for t in p] for p in schedule(ts)] == [["A", "B"], ["C"], ["D"]]
    assert len(schedule(ts, dependency_aware=False)) == 4


def test_schedule_detects_cycles():
    with pytest.raises(ValueError):
        schedule([task("A", "x", {}, "", depends_on=["B"]), task("B", "x", {}, "", depends_on=["A"])])


def test_lab_manual_requests():
    p = plan_request("Analyze calculator.py and run its tests", "calculator")
    assert [t["tool"] for t in p["tasks"]] == ["analyze_python", "run_tests"]
    assert p["tasks"][1]["depends_on"] == ["T01"]
    assert p["tasks"][1]["args"]["targets"] == ["test_calculator.py"]
    p = plan_request("Analyze calculator.py and search for all function definitions", "calculator")
    assert [t["tool"] for t in p["tasks"]] == ["analyze_python", "search_code"]


def test_usage_request_finds_repo():
    p = plan_request("Where is apply_discount used?", "calculator")
    assert p["repo"] == "inventory" and p["tasks"][0]["tool"] == "find_usages"


def test_verdict_policy():
    assert verdict_for([])[0] == "APPROVE"
    assert verdict_for([make_finding("QLT001", "a.py", 1, "x")])[0] == "APPROVE"
    assert verdict_for([make_finding("QLT002", "a.py", 1, "x")])[0] == "COMMENT"
    assert verdict_for([make_finding("TST001", "a.py", None, "x")])[0] == "REQUEST_CHANGES"
    assert verdict_for([make_finding("SEC001", "a.py", 1, "x")]) == ("BLOCK", 40, "critical")
