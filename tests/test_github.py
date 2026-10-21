"""End-to-end GitHub integration against the in-memory fake GitHub API (tests/fake_github.py):
demo setup, PR listing, review over MCP, posting the review, auto-fix pushed to the PR branch, workbench on a GitHub repo."""
import os

import pytest

from reviewpilot import github as gh
from reviewpilot import pipeline
from reviewpilot.config import Settings

from . import fake_github as fake


@pytest.fixture(scope="module")
def ghub():
    url, server = fake.serve()
    old = {k: os.environ.get(k) for k in ("GITHUB_API_URL", "GITHUB_TOKEN", "NO_PROXY", "no_proxy")}
    os.environ.update({"GITHUB_API_URL": url, "GITHUB_TOKEN": "fake-token", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    gh.reset_auth_cache()
    gh.clear_cache()
    gh._snapshot.cache_clear()
    from reviewpilot.mcp_client import MCPHub
    hub = MCPHub()                       # servers inherit the fake API URL and token
    from reviewpilot.github_setup import create_demo
    demo = create_demo(prefix="rp-")
    yield hub, demo
    hub.close()
    server.should_exit = True
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    gh.reset_auth_cache()
    gh.clear_cache()


def _pr(demo, case):
    p = next(x for x in demo["pull_requests"] if x["id"] == case)
    return f"{p['repo']}#{p['number']}"


def test_demo_setup_is_idempotent(ghub):
    _, demo = ghub
    assert len(demo["repos"]) == 3 and len(demo["pull_requests"]) == 13
    from reviewpilot.github_setup import create_demo
    again = create_demo(prefix="rp-")
    assert not any(r["created"] for r in again["repos"]) and not any(p["created"] for p in again["pull_requests"])


def test_list_and_load_pull_request(ghub):
    hub, demo = ghub
    prs = hub.call("list_pull_requests", {"repo": "tester/rp-inventory"}).data["pull_requests"]
    assert len(prs) == 5 and all(p["id"].startswith("tester/rp-inventory#") for p in prs)
    pr = hub.call("get_pull_request", {"pr_id": _pr(demo, "INV-09")}).data
    assert pr["source"] == "github" and {f["path"] for f in pr["files"]} == {"inventory/notify.py", "inventory/reports.py"}


def test_review_matches_offline_benchmark(ghub):
    hub, demo = ghub
    off = Settings(approval_mode="off")
    for case in ("INV-09", "CALC-03", "INV-02", "TXT-07"):
        g = pipeline.review(_pr(demo, case), off, hub=hub, persist=False)
        local = pipeline.review(case, off, hub=hub, persist=False)
        assert g["human"]["final_verdict"] == local["human"]["final_verdict"], case
        assert {(f["rule"], f["path"]) for f in g["final_findings"]} == {(f["rule"], f["path"]) for f in local["final_findings"]}, case


def test_pr_url_is_accepted(ghub):
    hub, demo = ghub
    pid = _pr(demo, "CALC-01")
    repo, n = pid.split("#")
    st = pipeline.review(f"https://github.com/{repo}/pull/{n}", Settings(approval_mode="off"), hub=hub, persist=False)
    assert st["pr_id"] == pid and st["human"]["final_verdict"] == "APPROVE"


def test_secret_demo_blocks(ghub):
    hub, demo = ghub
    st = pipeline.review(_pr(demo, "INV-S1"), Settings(approval_mode="off"), hub=hub, persist=False)
    assert st["human"]["final_verdict"] == "BLOCK"


def test_post_review_and_push_fix(ghub):
    hub, demo = ghub
    pid = _pr(demo, "INV-09")
    before = gh.pull(pid)["head_sha"]
    st = pipeline.review(pid, Settings(approval_mode="manual"), hub=hub, persist=False)
    assert st["status"] == "awaiting_human" and "QLT004" in st["interrupt"]["fixable_rules"]
    st = pipeline.resume_review(st["trace_id"], {"action": "approve", "autofix": ["QLT004"], "note": "thanks"})
    assert st["fix"]["status"] == "pushed", st["fix"]
    gh.clear_cache()
    after = gh.pull(pid)
    assert after["head_sha"] != before and after["head_sha"] == st["fix"]["commit"]
    fixed = gh.snapshot(after["repo"], after["head_sha"])["inventory/reports.py"]
    assert "import os" not in fixed.split("\n")[1]                      # the newly unused import is gone
    review = fake.S.reviews[-1]
    assert review["event"] == "COMMENT"                                  # own PR: GitHub forbids REQUEST_CHANGES
    assert "Request Changes" in review["body"] and "pushed to this branch" in review["body"]
    assert any(c["path"] == "inventory/notify.py" for c in review.get("comments", []))
    assert st["posted"]["url"].startswith("https://github.test/")


def test_workbench_on_github_repo(ghub):
    hub, _ = ghub
    st = pipeline.workbench("Analyze calculator.py and run its tests", "tester/rp-calculator", Settings(approval_mode="off"), hub=hub, persist=False)
    assert st["status"] == "completed" and "6 passed" in st["answer"]


def test_errors_are_classified(ghub):
    hub, _ = ghub
    r = hub.call("get_pull_request", {"pr_id": "tester/rp-calculator#999"})
    assert not r.ok and r.category == "INPUT" and r.attempts == 1
    r = hub.call("read_file", {"repo": "tester/rp-calculator", "path": "../../etc/passwd"})
    assert r.category == "SECURITY"
