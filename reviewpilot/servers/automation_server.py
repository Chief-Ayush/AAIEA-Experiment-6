"""MCP server: automation (writes).  The HIGH-risk tools are only called after a human approved them.

Tools: create_autofix_branch (HIGH), push_autofix_commit (HIGH, GitHub), delete_autofix_branch (MEDIUM), post_review (MEDIUM), get_review (LOW)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing_extensions import TypedDict  # pydantic needs typing_extensions.TypedDict on Python < 3.12

from mcp.server.fastmcp import FastMCP

from .. import github as gh
from ..analysis import unified_diff
from ..config import PATHS, VERDICTS
from ..workspace import InputError, base_files, load_pr, safe_name
from .common import READ_ONLY, RUNS_PROCESS, WRITES, risk_meta, run_blocking
from .fixes import AUTO_FIXABLE, branch_info, create_fix_branch, delete_fix_branch

mcp = FastMCP("reviewpilot-automation", instructions="Writes: auto-fix branches, fix commits pushed to GitHub pull requests, and reviews. Every write is HIGH or MEDIUM risk.", log_level="WARNING")


class AppliedFix(TypedDict):
    rule: str
    path: str
    line: int
    change: str


class FixBranch(TypedDict):
    branch: str
    files_changed: int
    applied: list[AppliedFix]
    patch: str


class Deleted(TypedDict):
    pr_id: str
    deleted: bool


class Comment(TypedDict):
    path: str
    line: int
    severity: str
    rule: str
    message: str


class PostedReview(TypedDict):
    pr_id: str
    verdict: str
    comments: int
    posted_at: str
    location: str
    url: str
    event: str
    inline_comments: int


class PushedFix(TypedDict):
    pr_id: str
    commit: str
    branch: str
    url: str
    files_changed: int


class StoredReview(TypedDict):
    pr_id: str
    found: bool
    review: dict


@mcp.tool(annotations=WRITES, meta=risk_meta("HIGH"))
async def create_autofix_branch(pr_id: str, rules: list[str]) -> FixBranch:
    """Apply safe mechanical fixes (rules QLT001, QLT002, QLT004, SEC001) to the pull request on a separate review branch."""
    return await run_blocking("create_autofix_branch", create_fix_branch, pr_id, list(rules))


@mcp.tool(annotations=RUNS_PROCESS, meta=risk_meta("MEDIUM"))
async def delete_autofix_branch(pr_id: str) -> Deleted:
    """Delete (revert) the auto-fix branch of a pull request."""
    def work():
        load_pr(pr_id)
        return {"pr_id": pr_id, "deleted": delete_fix_branch(pr_id)}
    return await run_blocking("delete_autofix_branch", work)


@mcp.tool(annotations=WRITES, meta=risk_meta("HIGH"))
async def push_autofix_commit(pr_id: str) -> PushedFix:
    """Push the prepared auto-fix branch as one commit onto the GitHub pull request's branch (fast-forward only)."""
    def work():
        if not gh.is_github_pr(pr_id):
            raise InputError("only GitHub pull requests can receive a pushed fix; sample pull requests keep the local review branch")
        info = branch_info(pr_id)
        if not info or not info.get("files"):
            raise InputError(f"no auto-fix branch prepared for {pr_id} - call create_autofix_branch first")
        rules = ", ".join(info["rules"])
        r = gh.push_to_pr(pr_id, info["files"], f"ReviewPilot auto-fix ({rules})\n\nApproved by a reviewer; the test suite passed on the fixed code.")
        return {"pr_id": pr_id, "commit": r["commit"], "branch": r["branch"], "url": r["url"], "files_changed": len(info["files"])}
    return await run_blocking("push_autofix_commit", work)


@mcp.tool(annotations=RUNS_PROCESS, meta=risk_meta("MEDIUM"))
async def post_review(pr_id: str, verdict: str, summary: str, comments: list[Comment] = []) -> PostedReview:  # noqa: B006
    """Post the review (verdict, summary and inline comments).  GitHub pull requests receive a real review; findings on added lines
    become inline comments.  Sample pull requests are stored in storage/reviews."""
    def work():
        pr = load_pr(pr_id)
        if verdict not in VERDICTS:
            raise InputError(f"verdict must be one of {VERDICTS}")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        url, event, inline = "", verdict, 0
        if gh.is_github_pr(pr_id):
            base = base_files(pr["repo"], pr_id)
            added = {p: {x["line"] for x in unified_diff(base.get(p), c, p)["added_lines"]} for p, c in pr["changes"].items() if c is not None}
            res = gh.post_review(pr_id, verdict, summary, [dict(c) for c in comments], added)
            url, event, inline = res["url"], res["event"], res["inline_comments"]
        rec = {"pr_id": pr_id, "verdict": verdict, "summary": summary, "comments": list(comments), "posted_at": now, "url": url, "event": event}
        p = PATHS.reviews / f"{safe_name(pr_id)}.json"
        p.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        return {"pr_id": pr_id, "verdict": verdict, "comments": len(comments), "posted_at": now, "location": url or p.relative_to(PATHS.root).as_posix(),
                "url": url, "event": event, "inline_comments": inline}
    return await run_blocking("post_review", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def get_review(pr_id: str) -> StoredReview:
    """Return the review posted for a pull request, if any."""
    def work():
        p = PATHS.reviews / f"{safe_name(pr_id)}.json"
        return {"pr_id": pr_id, "found": p.exists(), "review": json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}}
    return await run_blocking("get_review", work)


@mcp.resource("autofix://rules", mime_type="application/json", description="Rules the automation server can fix automatically")
def autofix_rules() -> str:
    return json.dumps(AUTO_FIXABLE, indent=1)


if __name__ == "__main__":
    mcp.run()
