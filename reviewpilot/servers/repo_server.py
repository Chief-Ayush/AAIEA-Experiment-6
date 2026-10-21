"""MCP server: repository and pull-request access (LOW risk, read-only).

Tools: list_files, read_file, search_code, list_pull_requests, get_pull_request, get_diff
Resources: repo://{repo}/readme, pr://{pr_id}/description
"""
from __future__ import annotations

import fnmatch
import re
from typing_extensions import TypedDict  # pydantic needs typing_extensions.TypedDict on Python < 3.12

from mcp.server.fastmcp import FastMCP

from ..analysis import unified_diff
from ..workspace import InputError, base_files, head_files, list_prs, load_pr, safe_project_path, repo_root
from .common import READ_ONLY, risk_meta, run_blocking
from .fixes import branch_files

mcp = FastMCP("reviewpilot-repo", instructions="Read-only access to GitHub repositories, the bundled sample repositories and their pull requests.", log_level="WARNING")


class FileEntry(TypedDict):
    path: str
    lines: int
    size: int


class FileList(TypedDict):
    repo: str
    ref: str
    count: int
    files: list[FileEntry]


class FileContent(TypedDict):
    repo: str
    ref: str
    path: str
    lines: int
    start_line: int
    end_line: int
    content: str


class Match(TypedDict):
    path: str
    line: int
    text: str


class SearchResult(TypedDict):
    repo: str
    query: str
    count: int
    truncated: bool
    matches: list[Match]


class ChangedFile(TypedDict):
    path: str
    status: str
    additions: int
    deletions: int


class PullRequest(TypedDict):
    id: str
    repo: str
    number: int
    url: str
    source: str
    head_sha: str
    title: str
    description: str
    author: str
    commits: list[str]
    files: list[ChangedFile]
    additions: int
    deletions: int


class PRSummary(TypedDict):
    id: str
    repo: str
    title: str
    author: str
    files_changed: int
    url: str
    updated: str


class PRList(TypedDict):
    count: int
    pull_requests: list[PRSummary]


class DiffLine(TypedDict):
    line: int
    text: str


class FileDiff(TypedDict):
    path: str
    status: str
    additions: int
    deletions: int
    added_lines: list[DiffLine]
    removed_lines: list[DiffLine]
    hunks: list[str]
    patch: str


class Diff(TypedDict):
    pr_id: str
    additions: int
    deletions: int
    files: list[FileDiff]


def _files(repo: str, pr_id: str, ref: str) -> dict[str, str]:
    if ref == "base" or not pr_id:
        return base_files(repo, pr_id or None)
    if ref == "autofix":
        return branch_files(repo, pr_id)
    return head_files(repo, pr_id)


def _check_path(repo: str, path: str) -> str:
    safe_project_path(repo_root(repo), path)          # raises SecurityError for ../ escapes and absolute paths
    rel = path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_files(repo: str, pr_id: str = "", pattern: str = "*", ref: str = "head") -> FileList:
    """List the files of a repository (at the pull-request head when pr_id is given). `pattern` is a glob such as *.py."""
    def work():
        files = _files(repo, pr_id, ref)
        sel = [{"path": p, "lines": len(c.splitlines()), "size": len(c.encode())} for p, c in sorted(files.items()) if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p.split("/")[-1], pattern)]
        return {"repo": repo, "ref": ref if pr_id else "base", "count": len(sel), "files": sel}
    return await run_blocking("list_files", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def read_file(repo: str, path: str, pr_id: str = "", ref: str = "head", start_line: int = 1, end_line: int = 0) -> FileContent:
    """Read a project file (optionally a line range). Paths are confined to the repository by safe_project_path()."""
    def work():
        rel = _check_path(repo, path)
        files = _files(repo, pr_id, ref)
        if rel not in files:
            raise InputError(f"File not found: {path}")
        lines = files[rel].splitlines()
        s = max(1, start_line)
        e = len(lines) if end_line <= 0 else min(len(lines), end_line)
        return {"repo": repo, "ref": ref if pr_id else "base", "path": rel, "lines": len(lines), "start_line": s, "end_line": e, "content": "\n".join(lines[s - 1:e])}
    return await run_blocking("read_file", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def search_code(repo: str, query: str, pr_id: str = "", regex: bool = False, path_glob: str = "*.py", max_results: int = 100) -> SearchResult:
    """Search source files for a text or regular expression; returns path, line number and line text for every match."""
    def work():
        if not query:
            raise InputError("query must not be empty")
        try:
            rx = re.compile(query if regex else re.escape(query))
        except re.error as exc:
            raise InputError(f"invalid regular expression: {exc}") from exc
        matches = []
        for p, c in sorted(_files(repo, pr_id, "head").items()):
            if not (fnmatch.fnmatch(p, path_glob) or fnmatch.fnmatch(p.split("/")[-1], path_glob)):
                continue
            for i, line in enumerate(c.splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": p, "line": i, "text": line.strip()[:200]})
        return {"repo": repo, "query": query, "count": len(matches), "truncated": len(matches) > max_results, "matches": matches[:max_results]}
    return await run_blocking("search_code", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def list_pull_requests(repo: str = "") -> PRList:
    """List open pull requests of a repository (owner/repo on GitHub, or a sample repository; empty = all samples)."""
    def work():
        prs = list_prs(repo or None)
        return {"count": len(prs), "pull_requests": [{k: p[k] for k in ("id", "repo", "title", "author", "files_changed", "url", "updated")} for p in prs]}
    return await run_blocking("list_pull_requests", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def get_pull_request(pr_id: str) -> PullRequest:
    """Pull-request metadata (title, description, author, commits) and the changed files with line counts.
    pr_id is a sample id (INV-03) or a GitHub pull request (owner/repo#12)."""
    def work():
        pr = load_pr(pr_id)
        base = base_files(pr["repo"], pr_id)
        files, add, dele = [], 0, 0
        for path, new in sorted(pr["changes"].items()):
            d = unified_diff(base.get(path), new, path)
            files.append({"path": path, "status": d["status"], "additions": d["additions"], "deletions": d["deletions"]})
            add += d["additions"]
            dele += d["deletions"]
        return {"id": pr["id"], "repo": pr["repo"], "number": pr["number"], "url": pr["url"], "source": pr["source"], "head_sha": pr["head_sha"],
                "title": pr["title"], "description": pr["description"], "author": pr["author"],
                "commits": pr["commits"], "files": files, "additions": add, "deletions": dele}
    return await run_blocking("get_pull_request", work)


@mcp.tool(annotations=READ_ONLY, meta=risk_meta("LOW"))
async def get_diff(pr_id: str, path: str = "") -> Diff:
    """Unified diff of a pull request against its merge base, with the added and removed lines of every changed file."""
    def work():
        pr = load_pr(pr_id)
        base = base_files(pr["repo"], pr_id)
        files = []
        for p, new in sorted(pr["changes"].items()):
            if path and p != path:
                continue
            files.append(unified_diff(base.get(p), new, p))
        if path and not files:
            raise InputError(f"{path} is not changed by {pr_id}")
        return {"pr_id": pr_id, "additions": sum(f["additions"] for f in files), "deletions": sum(f["deletions"] for f in files), "files": files}
    return await run_blocking("get_diff", work)


@mcp.resource("repo://{repo}/readme", mime_type="text/markdown", description="README of a sample repository")
def repo_readme(repo: str) -> str:
    return base_files(repo).get("README.md", "")


@mcp.resource("pr://{pr_id}/description", mime_type="text/plain", description="Untrusted pull-request description")
def pr_description(pr_id: str) -> str:
    pr = load_pr(pr_id)
    return f"{pr['title']}\n\n{pr['description']}"


if __name__ == "__main__":
    mcp.run()
