"""GitHub REST client used by the MCP servers (read pull requests, post reviews, push fix commits) and by the demo setup.

Authentication: GITHUB_TOKEN (environment or .env, saved from the Settings page) or, if absent, the GitHub CLI (`gh auth token`).
Repository snapshots are downloaded once per commit as a tarball and cached in storage/cache/github/ (commits are immutable).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from functools import lru_cache

import httpx

from .config import PATHS

API_VERSION = "2022-11-28"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PR_RE = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)$")
URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)")
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 4000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox", ".mypy_cache"}


class GitHubError(Exception):
    """category follows the lab manual: INPUT (not found / bad request), SECURITY (auth / permission), EXECUTION (network, 5xx, rate limit)."""

    def __init__(self, category: str, message: str, status: int | None = None):
        super().__init__(message)
        self.category, self.status = category, status


def api_url() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


# ----------------------------------------------------------------------------------------------------------- authentication
_GH_CLI: dict = {}


def _gh_cli_token() -> str | None:
    if "token" not in _GH_CLI:
        tok = None
        exe = shutil.which("gh")
        if exe:
            try:
                r = subprocess.run([exe, "auth", "token"], capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
                tok = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
            except (OSError, subprocess.SubprocessError):
                tok = None
        _GH_CLI["token"] = tok
    return _GH_CLI["token"]


def token_info() -> tuple[str | None, str]:
    """(token, source) - source is 'settings', 'GH_TOKEN', 'GitHub CLI' or 'none'."""
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"], "settings"
    if os.environ.get("GH_TOKEN"):
        return os.environ["GH_TOKEN"], "GH_TOKEN"
    tok = _gh_cli_token()
    return (tok, "GitHub CLI") if tok else (None, "none")


def reset_auth_cache() -> None:
    _GH_CLI.clear()
    _client.cache_clear()


# ----------------------------------------------------------------------------------------------------------- client
class Client:
    def __init__(self, token: str | None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": "ReviewPilot"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=api_url(), headers=headers, timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
        self.has_token = bool(token)

    def request(self, method: str, path: str, **kw):
        try:
            r = self.http.request(method, path, **kw)
        except httpx.TimeoutException as exc:
            raise GitHubError("EXECUTION", f"GitHub did not answer in time ({path})") from exc
        except httpx.HTTPError as exc:
            raise GitHubError("EXECUTION", f"cannot reach GitHub: {exc}") from exc
        if r.status_code < 300:
            return r
        try:
            msg = r.json().get("message", "")
            errs = r.json().get("errors")
            if errs:
                msg += " - " + "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errs)
        except (ValueError, AttributeError):
            msg = r.text[:200]
        if r.status_code == 401:
            raise GitHubError("SECURITY", "GitHub rejected the token (401). Update it in Settings.", 401)
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError("EXECUTION", "GitHub rate limit reached - try again in a few minutes", 403)
        if r.status_code == 403:
            raise GitHubError("SECURITY", f"GitHub denied access (403): {msg}", 403)
        if r.status_code == 404:
            raise GitHubError("INPUT", f"not found on GitHub: {path}" + ("" if self.has_token else " (no token - private repositories need one)"), 404)
        if r.status_code in (409, 422):
            raise GitHubError("INPUT", f"GitHub refused the request ({r.status_code}): {msg}", r.status_code)
        raise GitHubError("EXECUTION", f"GitHub error {r.status_code}: {msg}", r.status_code)

    def get(self, path: str, **params):
        return self.request("GET", path, params=params or None).json()

    def post(self, path: str, body: dict):
        return self.request("POST", path, json=body).json()

    def patch(self, path: str, body: dict):
        return self.request("PATCH", path, json=body).json()

    def paginate(self, path: str, limit: int = 1000, **params):
        out, page = [], 1
        while len(out) < limit:
            items = self.get(path, per_page=100, page=page, **params)
            if not isinstance(items, list) or not items:
                break
            out += items
            if len(items) < 100:
                break
            page += 1
        return out[:limit]


@lru_cache(maxsize=1)
def _client() -> Client:
    return Client(token_info()[0])


def client() -> Client:
    return _client()


# ----------------------------------------------------------------------------------------------------------- identifiers
def is_github_repo(repo: str) -> bool:
    return isinstance(repo, str) and bool(REPO_RE.match(repo))


def is_github_pr(pr_id: str) -> bool:
    return isinstance(pr_id, str) and bool(PR_RE.match(pr_id))


def split_pr(pr_id: str) -> tuple[str, int]:
    m = PR_RE.match(pr_id or "")
    if not m:
        raise GitHubError("INPUT", f"'{pr_id}' is not a GitHub pull request (owner/repo#number)")
    return m.group(1), int(m.group(2))


def normalize_pr_ref(text: str) -> str:
    """Accept owner/repo#12, a pull-request URL, or anything else unchanged."""
    t = (text or "").strip()
    m = URL_RE.search(t)
    if m:
        return f"{m.group(1)}#{m.group(2)}"
    return t


# ----------------------------------------------------------------------------------------------------------- reads
_TTL: dict = {}
_LOCK = threading.Lock()


def _ttl_cache(key, seconds, fn):
    now = time.time()
    with _LOCK:
        hit = _TTL.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
    val = fn()
    with _LOCK:
        _TTL[key] = (now, val)
    return val


def clear_cache() -> None:
    with _LOCK:
        _TTL.clear()


def viewer() -> dict:
    return _ttl_cache(("viewer",), 300, lambda: client().get("/user"))


def list_repos() -> list[dict]:
    def go():
        items = client().paginate("/user/repos", limit=300, sort="updated", affiliation="owner,collaborator,organization_member")
        return [{"full_name": r["full_name"], "private": r["private"], "default_branch": r["default_branch"], "url": r["html_url"],
                 "updated": r.get("pushed_at") or r.get("updated_at"), "description": r.get("description") or ""} for r in items]
    return _ttl_cache(("repos",), 60, go)


def repo_info(repo: str) -> dict:
    return _ttl_cache(("repo", repo), 120, lambda: client().get(f"/repos/{repo}"))


def branch_sha(repo: str, branch: str | None = None) -> str:
    def go():
        b = branch or repo_info(repo)["default_branch"]
        return client().get(f"/repos/{repo}/git/ref/heads/{b}")["object"]["sha"]
    return _ttl_cache(("branch", repo, branch), 30, go)


def list_pulls(repo: str) -> list[dict]:
    def go():
        items = client().paginate(f"/repos/{repo}/pulls", limit=100, state="open", sort="updated", direction="desc")
        return [{"id": f"{repo}#{p['number']}", "number": p["number"], "repo": repo, "title": p["title"], "author": p["user"]["login"],
                 "description": p.get("body") or "", "updated": p["updated_at"], "url": p["html_url"], "draft": p.get("draft", False),
                 "head_sha": p["head"]["sha"]} for p in items]
    return _ttl_cache(("pulls", repo), 20, go)


def _status(f: dict) -> str:
    return {"removed": "deleted", "added": "added", "copied": "added"}.get(f["status"], "modified")


def pull(pr_id: str) -> dict:
    """Normalized pull request: metadata, merge-base sha, head sha and the changed files (renames = delete + add)."""
    repo, num = split_pr(pr_id)

    def go():
        c = client()
        p = c.get(f"/repos/{repo}/pulls/{num}")
        head, base_tip = p["head"]["sha"], p["base"]["sha"]
        try:
            merge_base = c.get(f"/repos/{repo}/compare/{base_tip}...{head}")["merge_base_commit"]["sha"]
        except GitHubError:
            merge_base = base_tip
        files = []
        for f in c.paginate(f"/repos/{repo}/pulls/{num}/files", limit=3000):
            if f["status"] == "renamed" and f.get("previous_filename"):
                files.append({"path": f["previous_filename"], "status": "deleted", "additions": 0, "deletions": 0})
                files.append({"path": f["filename"], "status": "added", "additions": f["additions"], "deletions": f["deletions"]})
            else:
                files.append({"path": f["filename"], "status": _status(f), "additions": f["additions"], "deletions": f["deletions"]})
        commits = [x["commit"]["message"].splitlines()[0] for x in c.paginate(f"/repos/{repo}/pulls/{num}/commits", limit=250)]
        head_repo = (p["head"].get("repo") or {}).get("full_name") or repo
        return {"id": f"{repo}#{num}", "repo": repo, "number": num, "title": p["title"], "description": p.get("body") or "",
                "author": p["user"]["login"], "commits": commits or [p["title"]], "url": p["html_url"], "state": p["state"],
                "draft": p.get("draft", False), "base_ref": p["base"]["ref"], "base_sha": merge_base, "head_ref": p["head"]["ref"],
                "head_sha": head, "head_repo": head_repo, "files": files, "source": "github"}
    return _ttl_cache(("pull", pr_id), 15, go)


def snapshot(repo: str, sha: str) -> dict[str, str]:
    """All text files of `repo` at commit `sha` (tarball download, cached per sha on disk and in memory)."""
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha or ""):
        raise GitHubError("INPUT", f"invalid commit sha '{sha}'")
    return _snapshot(repo, sha)


@lru_cache(maxsize=24)
def _snapshot(repo: str, sha: str) -> dict[str, str]:
    d = PATHS.cache / "github" / repo.replace("/", "__")
    f = d / f"{sha}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    r = client().request("GET", f"/repos/{repo}/tarball/{sha}")
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:*") as tar:
        for m in tar:
            if not m.isfile() or m.size > MAX_FILE_BYTES:
                continue
            parts = m.name.split("/")[1:]                     # strip the "owner-repo-sha/" top directory
            if not parts or set(parts[:-1]) & SKIP_DIRS:
                continue
            fh = tar.extractfile(m)
            if fh is None:
                continue
            raw = fh.read()
            if b"\x00" in raw[:4096]:
                continue                                      # binary
            try:
                files["/".join(parts)] = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(files) >= MAX_FILES:
                break
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{sha}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(files), encoding="utf-8")
    os.replace(tmp, f)
    return files


# ----------------------------------------------------------------------------------------------------------- writes
GH_EVENT = {"APPROVE": "APPROVE", "COMMENT": "COMMENT", "REQUEST_CHANGES": "REQUEST_CHANGES", "BLOCK": "REQUEST_CHANGES"}
SEV_ICON = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Info"}


def review_body(verdict: str, summary: str, comments: list[dict], inline: set[int]) -> str:
    lines = [f"### ReviewPilot: {verdict.replace('_', ' ').title()}", "", summary]
    rest = [c for i, c in enumerate(comments) if i not in inline]
    if comments:
        lines += ["", "| Severity | Rule | Where | Finding |", "|---|---|---|---|"]
        for c in comments:
            where = f"`{c['path']}`" + (f" line {c['line']}" if c.get("line") else "")
            lines.append(f"| {SEV_ICON.get(c['severity'], c['severity'])} | {c['rule']} | {where} | {c['message'].replace('|', '/')} |")
    if rest and inline:
        lines += ["", f"{len(inline)} finding(s) are also attached to the changed lines."]
    lines += ["", "<sub>Reviewed by ReviewPilot (MCP multi-agent reviewer) and approved by a human before posting.</sub>"]
    return "\n".join(lines)


def post_review(pr_id: str, verdict: str, summary: str, comments: list[dict], added_lines: dict[str, set[int]]) -> dict:
    """Post a pull-request review.  Inline comments go on added lines; everything else is in the body.
    GitHub does not let anyone approve or reject their own PR, so the event falls back to COMMENT in that case."""
    repo, num = split_pr(pr_id)
    p = pull(pr_id)
    event = GH_EVENT.get(verdict, "COMMENT")
    own = False
    try:
        own = viewer()["login"].lower() == p["author"].lower()
    except GitHubError:
        pass
    if own and event != "COMMENT":
        event = "COMMENT"
    inline_idx = {i for i, c in enumerate(comments) if c.get("line") and c["line"] in added_lines.get(c["path"], set())}
    inline = [{"path": comments[i]["path"], "line": comments[i]["line"], "side": "RIGHT",
               "body": f"**{comments[i]['rule']}** ({comments[i]['severity']}): {comments[i]['message']}"} for i in sorted(inline_idx)]
    body = {"commit_id": p["head_sha"], "event": event, "body": review_body(verdict, summary, comments, inline_idx)}
    if inline:
        body["comments"] = inline
    c = client()
    try:
        r = c.post(f"/repos/{repo}/pulls/{num}/reviews", body)
    except GitHubError as exc:
        if exc.status == 422 and inline:                     # a line outside the diff - post the summary only
            body.pop("comments")
            body["body"] = review_body(verdict, summary, comments, set())
            r = c.post(f"/repos/{repo}/pulls/{num}/reviews", body)
            inline = []
        else:
            raise
    return {"url": r.get("html_url", p["url"]), "event": event, "inline_comments": len(inline), "own_pr": own}


def commit_files(repo: str, parent_sha: str, files: dict[str, str | None], message: str) -> str:
    """Create a commit on top of parent_sha with the given file contents (None deletes).  Returns the new commit sha."""
    c = client()
    base_tree = c.get(f"/repos/{repo}/git/commits/{parent_sha}")["tree"]["sha"]
    entries = []
    for path, content in files.items():
        if content is None:
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        else:
            blob = c.post(f"/repos/{repo}/git/blobs", {"content": content, "encoding": "utf-8"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = c.post(f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": entries})
    return c.post(f"/repos/{repo}/git/commits", {"message": message, "tree": tree["sha"], "parents": [parent_sha]})["sha"]


def push_to_pr(pr_id: str, files: dict[str, str], message: str) -> dict:
    """Commit fixed files on top of the PR head and fast-forward the PR branch (fails if the branch moved since the review)."""
    p = pull(pr_id)
    clear_cache()
    fresh = pull(pr_id)
    if fresh["head_sha"] != p["head_sha"]:
        raise GitHubError("INPUT", "the pull request changed since it was reviewed - review it again before fixing")
    target = fresh["head_repo"]
    sha = commit_files(target, fresh["head_sha"], files, message)
    client().patch(f"/repos/{target}/git/refs/heads/{fresh['head_ref']}", {"sha": sha, "force": False})
    clear_cache()
    return {"commit": sha, "branch": fresh["head_ref"], "repo": target, "url": f"{fresh['url']}/commits/{sha}"}
