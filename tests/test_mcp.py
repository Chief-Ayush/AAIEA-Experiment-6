"""MCP protocol tests against the four live stdio servers."""
from reviewpilot.config import Settings
from reviewpilot.tracing import Trace


def test_discovery(hub):
    tools = hub.tools()
    assert len(tools) == 18
    assert {t.server for t in tools.values()} == {"repo", "analysis", "quality", "automation"}
    assert tools["read_file"].risk == "LOW" and tools["run_tests"].risk == "MEDIUM" and tools["create_autofix_branch"].risk == "HIGH" and tools["push_autofix_commit"].risk == "HIGH"
    assert all(t.output_schema for t in tools.values())


def test_structured_call(hub):
    r = hub.call("analyze_python", {"repo": "calculator", "path": "calculator.py"})
    assert r.ok and r.output_valid and r.data["function_count"] == 5


def test_error_categories(hub):
    assert hub.call("read_file", {"repo": "calculator", "path": "../../etc/passwd"}).category == "SECURITY"
    assert hub.call("read_file", {"repo": "calculator", "path": "missing.py"}).category == "INPUT"
    r = hub.call("read_file", {"repo": "calculator", "path": 3})
    assert r.category == "INPUT" and r.attempts == 0
    assert hub.call("no_such_tool", {}).category == "INPUT"


def test_retry_and_reconnect(hub):
    hub.set_faults({"lint": {"kind": "transient", "count": 1}})
    r = hub.call("lint", {"repo": "calculator"})
    assert r.ok and r.attempts == 2
    hub.set_faults({"find_usages": {"kind": "crash", "count": 1}})
    r = hub.call("find_usages", {"repo": "calculator", "symbol": "add"})
    hub.set_faults(None)
    assert r.ok and r.attempts == 2


def test_trace_fields(hub):
    tr = Trace(persist=False)
    hub.call("list_files", {"repo": "calculator"}, trace=tr)
    rec = tr.records[0]
    assert rec["trace_id"].startswith("TRACE-") and rec["task_id"] == "TASK-001"
    for k in ("tool", "arguments", "status", "execution_time_ms", "timestamp", "error"):
        assert k in rec


def test_pytest_target_injection_blocked(hub):
    r = hub.call("run_tests", {"repo": "calculator", "targets": ["x.py; rm -rf /"]}, settings=Settings())
    assert r.category == "SECURITY"
