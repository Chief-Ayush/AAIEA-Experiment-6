"""Regressions found by reviewing a real pull request (a Windows-authored repository pushed to GitHub)."""
import xml.etree.ElementTree as ET

from reviewpilot import workspace
from reviewpilot.agents.review import _test_path
from reviewpilot.analysis import analyze_source, scan_secrets_text
from reviewpilot.rules import make_finding, merge_score, verdict_for
from reviewpilot.planning import NO_UNIT_TESTS, RUNTIME_ARTIFACT, is_generated
from reviewpilot.servers.analysis_server import _indirect_coverage
from reviewpilot.servers.quality_server import _reason


def test_checkout_keeps_crlf_files_byte_for_byte(tmp_path, monkeypatch):
    src = "def f():\r\n    return (1,\r\n            2)\r\n"
    monkeypatch.setattr(workspace, "head_files", lambda repo, pr: {"m.py": src})
    out = workspace.materialize("x", None, tmp_path / "co")
    assert (out / "m.py").read_bytes() == src.encode()                      # write_text() would have produced \r\r\n
    compile((out / "m.py").read_text(encoding="utf-8"), "m.py", "exec")


def test_future_import_is_not_unused():
    a = analyze_source("from __future__ import annotations\nimport os\n\nx = os.sep\n")
    assert a["unused_imports"] == []


def test_todo_only_in_real_comments():
    a = analyze_source('s = "# TODO: not a comment\\n"\nt = """\n# FIXME also data\n"""\nx = 1  # TODO: real\n')
    assert [t["line"] for t in a["todos"]] == [5]


def test_print_is_output_in_scripts_but_debugging_in_modules():
    lib = analyze_source("def f():\n    print('x')\n")
    script = analyze_source("def f():\n    print('x')\n\n\nif __name__ == '__main__':\n    f()\n")
    assert [d["call"] for d in lib["debug_calls"]] == ["print"] and script["debug_calls"] == []
    assert analyze_source("def f():\n    breakpoint()\n\n\nif __name__ == '__main__':\n    pass\n")["debug_calls"][0]["call"] == "breakpoint"


def test_documentation_keys_and_templates_are_not_secrets():
    assert scan_secrets_text('KEY = "AKIAIOSFODNN7EXAMPLE"\n') == []                      # the AWS documentation key
    assert scan_secrets_text('x = f"""\nGITHUB_TOKEN = "{GITHUB_TOKEN}"\n"""\n', "gen.py") == []   # template placeholder
    assert scan_secrets_text('API_TOKEN = "${API_TOKEN}"\n') == []


def test_secret_context_separates_real_leaks_from_test_data():
    real = 'DB_PASSWORD = "r7Kq2Lm9Xv4Tz8Pw3Ns6"\n'
    assert scan_secrets_text(real, "app/config.py")[0]["context"] == "code"
    assert scan_secrets_text(real, "tests/test_db.py")[0]["context"] == "fixture"
    assert scan_secrets_text(real, "docs/setup.md")[0]["context"] == "fixture"
    embedded = 'TEMPLATE = """\nDB_PASSWORD = "r7Kq2Lm9Xv4Tz8Pw3Ns6"\n"""\n'
    assert scan_secrets_text(embedded, "gen.py")[0]["context"] == "embedded"


def test_collection_error_reason_and_path():
    tc = ET.fromstring('<error message="collection failure">tests/test_x.py:3: in &lt;module&gt;\nE   SyntaxError: bad\nE     File "a.py", line 2</error>')
    r = _reason(tc)
    assert r.startswith("collection failure:") and "SyntaxError: bad" in r
    known = ["tests/test_github.py", "tests/test_units.py"]
    assert _test_path("tests.test_github", known) == "tests/test_github.py"          # was attributed to tests/__init__.py
    assert _test_path("tests.test_units::test_a", known) == "tests/test_units.py"


def test_indirect_coverage_follows_callers():
    files = [("pkg/build.py", "def apply_edits():\n    return 1\n\n\ndef build_all():\n    return apply_edits()\n"),
             ("pkg/config.py", "from .build import build_all\n\nbuild_all()\n"),
             ("tests/test_cfg.py", "from pkg.config import PATHS\n\n\ndef test_x():\n    assert PATHS\n")]
    ok, chain = _indirect_coverage("apply_edits", files)
    assert ok and chain
    assert _indirect_coverage("apply_edits", files[:1])[0] is False                    # nothing reaches it from a test


def test_generated_and_ui_files_are_recognised():
    assert is_generated("storage/mcp_logs/execution.log") and is_generated("package-lock.json") and not is_generated("src/app.py")
    assert RUNTIME_ARTIFACT.search("pkg/__pycache__/x.pyc") and RUNTIME_ARTIFACT.search(".env") and not RUNTIME_ARTIFACT.search("src/env.py")
    assert NO_UNIT_TESTS.search("pages/2_Workbench.py") and NO_UNIT_TESTS.search("ui/common.py") and not NO_UNIT_TESTS.search("pkg/core.py")


def test_repeated_findings_of_one_rule_weigh_less():
    many_docstrings = [make_finding("QLT005", f"a{i}.py", 1, "x") for i in range(9)]
    different = [make_finding(r, "a.py", 1, "x") for r in ("QLT005", "QLT003", "QLT007", "QLT002", "TST003")]
    assert merge_score(many_docstrings) > 70 > merge_score(different)
    assert verdict_for([make_finding("SEC001", "a.py", 1, "x")]) == ("BLOCK", 40, "critical")
