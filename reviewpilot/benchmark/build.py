"""Builds the offline data set from `repos.py` and `catalog.py`:

    data/repos/<repo>/...          base snapshots
    data/prs/<repo>/<ID>.json      pull requests (metadata + full content of every changed file, None = deleted)
    data/eval/pr_labels.json       expected verdict and findings of every pull request

Run `python -m reviewpilot.benchmark.build` (the app also calls `ensure_data()` when `data/` is missing).
"""
from __future__ import annotations

import json
import shutil

from ..config import PATHS


def apply_edits(files: dict[str, str], edits: list[tuple]) -> dict[str, str | None]:
    """Apply edits to a copy of `files`; returns {path: new content, None = deleted} for every path that changed.

    ("modify", path, old, new)   replace the single occurrence of `old`
    ("add", path, content)       new file
    ("append", path, text)       add text at the end of a file
    ("delete", path)             remove a file
    """
    work = dict(files)
    for edit in edits:
        op, path = edit[0], edit[1]
        if op == "modify":
            old, new = edit[2], edit[3]
            if path not in work or work[path].count(old) != 1:
                raise ValueError(f"modify {path}: expected exactly one occurrence of {old!r}")
            work[path] = work[path].replace(old, new)
        elif op == "add":
            if path in work:
                raise ValueError(f"add {path}: file exists")
            work[path] = edit[2]
        elif op == "append":
            work[path] = work[path] + edit[2]
        elif op == "delete":
            if path not in work:
                raise ValueError(f"delete {path}: no such file")
            work[path] = None
        else:
            raise ValueError(f"unknown edit operation {op!r}")
    out = {}
    for path, content in work.items():
        if content is None:
            if path in files:
                out[path] = None
        elif files.get(path) != content:
            out[path] = content
    return out


def build_all(verbose: bool = False) -> dict:
    from .catalog import PRS, label_of
    from .repos import REPOS

    for d in (PATHS.repos, PATHS.prs):
        if d.exists():
            shutil.rmtree(d)
    for repo, files in REPOS.items():
        for rel, content in files.items():
            target = PATHS.repos / repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    labels = []
    for p in PRS:
        changes = apply_edits(REPOS[p["repo"]], p["edits"])
        target = PATHS.prs / p["repo"] / f"{p['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {k: p[k] for k in ("id", "repo", "title", "author", "description", "commits")} | {"changes": changes}
        target.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        labels.append(label_of(p))
    PATHS.eval_data.mkdir(parents=True, exist_ok=True)
    (PATHS.eval_data / "pr_labels.json").write_text(json.dumps(labels, indent=1), encoding="utf-8")
    if verbose:
        print(f"{len(REPOS)} repositories, {len(PRS)} pull requests, {len(labels)} labels -> {PATHS.root / 'data'}")
    return {"repos": len(REPOS), "prs": len(PRS)}


def ensure_data() -> None:
    """Create data/ on first start (it is git-ignored and generated)."""
    if not (PATHS.eval_data / "pr_labels.json").exists() or not PATHS.repos.is_dir() or not any(PATHS.repos.iterdir()):
        build_all()


if __name__ == "__main__":
    build_all(verbose=True)
