"""Repositories and pull requests on disk, plus the path-security check used by every MCP server.

A repository is a base snapshot (data/repos/<repo>/).  A pull request (data/prs/<repo>/<id>.json) stores its metadata and
the full content of every changed file (None = deleted).  The PR head is the base overlaid with those changes.
"""
from __future__ import annotations

import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

from . import github as gh
from .config import PATHS

IGNORED = {"__pycache__", ".pytest_cache", ".git"}


class SecurityError(Exception):
    """A request tried to leave the project directory or touch something it may not."""


class InputError(Exception):
    """A request referenced something that does not exist or used an invalid argument."""


def safe_project_path(root: Path, relative: str) -> Path:
    """Resolve `relative` inside `root`; reject absolute paths, '..' escapes, NUL bytes and symlinks that leave the root."""
    if relative is None or not isinstance(relative, str):
        raise InputError("path must be a string")
    if "\x00" in relative:
        raise SecurityError("NUL byte in path")
    rel = relative.replace("\\", "/").strip()
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise SecurityError(f"Path outside project: absolute path '{relative}'")
    root_r = Path(root).resolve()
    target = (root_r / rel).resolve()
    if target != root_r and root_r not in target.parents:
        raise SecurityError(f"Path outside project: '{relative}'")
    return target


def list_repos() -> list[str]:
    """Local sample repositories (GitHub repositories are named owner/repo and listed through the GitHub API)."""
    return sorted(p.name for p in PATHS.repos.iterdir() if p.is_dir())


def is_github(repo_or_pr: str) -> bool:
    return gh.is_github_repo(repo_or_pr) or gh.is_github_pr(repo_or_pr)


def normalize_pr_id(text: str) -> str:
    """'INV-03', 'owner/repo#12' or a GitHub pull-request URL -> canonical id."""
    t = gh.normalize_pr_ref(text)
    return t if gh.is_github_pr(t) else t.upper()


def repo_root(repo: str) -> Path:
    """Directory used as the confinement root for path checks (the snapshot folder for GitHub repositories)."""
    if gh.is_github_repo(repo):
        d = PATHS.cache / "github" / repo.replace("/", "__")
        d.mkdir(parents=True, exist_ok=True)
        return d
    if not isinstance(repo, str) or repo not in list_repos():
        raise InputError(f"unknown repository '{repo}'")
    return PATHS.repos / repo


def _local_base(repo: str) -> dict[str, str]:
    root = repo_root(repo)
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not (set(p.relative_to(root).parts) & IGNORED) and p.suffix != ".pyc":
            out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    return out


def base_files(repo: str, pr_id: str | None = None) -> dict[str, str]:
    """Files on the base: the PR's merge base when pr_id is given (GitHub), otherwise the default branch / sample snapshot."""
    if gh.is_github_repo(repo):
        if pr_id:
            pr = load_pr(pr_id)
            if pr["repo"] != repo:
                raise InputError(f"pull request {pr_id} belongs to {pr['repo']}, not {repo}")
            return dict(gh.snapshot(repo, pr["base_sha"]))
        return dict(gh.snapshot(repo, gh.branch_sha(repo)))
    return _local_base(repo)


@lru_cache(maxsize=256)
def _pr_cached(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_prs(repo: str | None = None) -> list[dict]:
    if repo and gh.is_github_repo(repo):
        return [{k: p[k] for k in ("id", "repo", "title", "author", "description", "url", "updated")} | {"files_changed": -1} for p in gh.list_pulls(repo)]
    out = []
    for f in sorted(PATHS.prs.glob("*/*.json")):
        d = _pr_cached(str(f), f.stat().st_mtime)
        if repo is None or d["repo"] == repo:
            out.append({k: d[k] for k in ("id", "repo", "title", "author", "description")} | {"files_changed": len(d["changes"]), "url": "", "updated": ""})
    return out


def load_pr(pr_id: str) -> dict:
    """Pull request with `changes` = {path: head content | None for deleted}."""
    if not isinstance(pr_id, str):
        raise InputError("pr_id must be a string")
    if gh.is_github_pr(pr_id):
        p = gh.pull(pr_id)
        head = gh.snapshot(p["repo"], p["head_sha"])
        changes = {f["path"]: (None if f["status"] == "deleted" else head.get(f["path"], "")) for f in p["files"]}
        return p | {"changes": changes}
    for f in PATHS.prs.glob(f"*/{pr_id}.json"):
        d = _pr_cached(str(f), f.stat().st_mtime)
        return d | {"number": 0, "url": "", "source": "local", "head_sha": "", "base_sha": ""}
    raise InputError(f"unknown pull request '{pr_id}'")


def head_files(repo: str, pr_id: str | None) -> dict[str, str]:
    if pr_id and gh.is_github_pr(pr_id):
        pr = load_pr(pr_id)
        if pr["repo"] != repo:
            raise InputError(f"pull request {pr_id} belongs to {pr['repo']}, not {repo}")
        return dict(gh.snapshot(repo, pr["head_sha"]))
    files = base_files(repo)
    if pr_id:
        pr = load_pr(pr_id)
        if pr["repo"] != repo:
            raise InputError(f"pull request {pr_id} belongs to {pr['repo']}, not {repo}")
        for path, content in pr["changes"].items():
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
    return files


def safe_name(pr_id: str) -> str:
    """File-system safe name for a PR id (owner/repo#12 -> owner__repo--12)."""
    return pr_id.replace("/", "__").replace("#", "--")


def materialize(repo: str, pr_id: str | None, dest: Path) -> Path:
    """Write the PR head (or the base when pr_id is None) to `dest` (used for running tests in isolation)."""
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    for path, content in head_files(repo, pr_id).items():
        target = safe_project_path(dest, path) if dest.exists() else dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))           # exact bytes: write_text would turn CRLF into CR CR LF on Windows
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def env_for_subprocess() -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    return env
