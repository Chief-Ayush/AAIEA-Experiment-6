"""One-click demo setup: creates the three sample projects as repositories in the signed-in GitHub account and opens realistic
pull requests on them (taken from the labelled benchmark), so ReviewPilot can be tried on real GitHub pull requests.

Idempotent: existing repositories, branches and pull requests are reused.  Secrets in the demo use a generic password pattern
so GitHub push protection does not block the push.
"""
from __future__ import annotations

import time
from typing import Callable

from . import github as gh
from .benchmark.build import apply_edits
from .benchmark.catalog import PRS
from .workspace import base_files, list_repos

DEMO_PRS = ["CALC-01", "CALC-03", "CALC-06", "CALC-07", "INV-01", "INV-02", "INV-09", "INV-14", "TXT-04", "TXT-07", "TXT-19", "TXT-21"]
EXTRA = [{"id": "INV-S1", "repo": "inventory", "title": "Connect to the staging database",
          "description": "Adds the staging database password so the import job can run from CI.",
          "edits": [("modify", "inventory/config.py", 'API_TOKEN = os.environ.get("INVENTORY_API_TOKEN", "")',
                     'API_TOKEN = os.environ.get("INVENTORY_API_TOKEN", "")\nDB_PASSWORD = "r7Kq2Lm9Xv4Tz8Pw3Ns6"')]}]


def demo_name(prefix: str, repo: str) -> str:
    return f"{prefix}{repo}"


def _ensure_repo(owner: str, name: str, private: bool, description: str) -> tuple[dict, bool]:
    c = gh.client()
    try:
        return c.get(f"/repos/{owner}/{name}"), False
    except gh.GitHubError as exc:
        if exc.status != 404:
            raise
    return c.post("/user/repos", {"name": name, "description": description, "private": private, "auto_init": True}), True


def _branch_exists(full: str, branch: str) -> str | None:
    try:
        return gh.client().get(f"/repos/{full}/git/ref/heads/{branch}")["object"]["sha"]
    except gh.GitHubError as exc:
        if exc.status in (404, 409):              # 409 = repository still empty
            return None
        raise


def create_demo(prefix: str = "reviewpilot-", private: bool = True, progress: Callable[[float, str], None] | None = None) -> dict:
    tick = progress or (lambda f, m: None)
    me = gh.viewer()["login"]
    catalog = {p["id"]: p for p in PRS}
    plan = [catalog[i] for i in DEMO_PRS] + EXTRA
    steps = len(list_repos()) + len(plan)
    done, repos, prs = 0, [], []
    c = gh.client()
    for repo in list_repos():
        name = demo_name(prefix, repo)
        tick(done / steps, f"repository {name}")
        info, created = _ensure_repo(me, name, private, f"ReviewPilot demo: the '{repo}' sample project")
        full, branch = info["full_name"], info["default_branch"]
        head = _branch_exists(full, branch)
        for _ in range(10):                       # a freshly created repository needs a moment before its first commit is visible
            if head or not created:
                break
            time.sleep(1.0)
            head = _branch_exists(full, branch)
        files = base_files(repo)
        current = gh.snapshot(full, head) if head else {}
        missing = {p: c_ for p, c_ in files.items() if current.get(p) != c_}
        if missing and head:
            sha = gh.commit_files(full, head, missing, f"Import the {repo} sample project")
            c.patch(f"/repos/{full}/git/refs/heads/{branch}", {"sha": sha, "force": False})
        repos.append({"repo": full, "url": info["html_url"], "created": created, "default_branch": branch})
        done += 1
    gh.clear_cache()
    by_repo = {r["repo"].split("/")[1][len(prefix):]: r for r in repos}
    for p in plan:
        r = by_repo[p["repo"]]
        full, base_branch = r["repo"], r["default_branch"]
        tick(done / steps, f"pull request {p['id']}: {p['title']}")
        branch = f"reviewpilot-demo/{p['id'].lower()}"
        open_prs = c.get(f"/repos/{full}/pulls", state="open", head=f"{full.split('/')[0]}:{branch}", per_page=5)
        if open_prs:
            prs.append({"id": p["id"], "repo": full, "number": open_prs[0]["number"], "title": p["title"], "url": open_prs[0]["html_url"], "created": False})
            done += 1
            continue
        if not _branch_exists(full, branch):
            main_sha = _branch_exists(full, base_branch)
            changes = apply_edits(base_files(p["repo"]), p["edits"])
            sha = gh.commit_files(full, main_sha, changes, p["title"])
            c.post(f"/repos/{full}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        body = p["description"] + f"\n\n<sub>Demo pull request created by ReviewPilot (benchmark case {p['id']}).</sub>"
        pr = c.post(f"/repos/{full}/pulls", {"title": p["title"], "head": branch, "base": base_branch, "body": body})
        prs.append({"id": p["id"], "repo": full, "number": pr["number"], "title": p["title"], "url": pr["html_url"], "created": True})
        done += 1
    gh.clear_cache()
    tick(1.0, "done")
    return {"owner": me, "repos": repos, "pull_requests": prs}
