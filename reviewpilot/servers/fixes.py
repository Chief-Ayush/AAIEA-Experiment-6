"""Auto-fix branches: safe, mechanical fixes prepared on a local review branch.  The tests run on that branch; only then can the
fix be pushed to the pull request on GitHub (automation server, push_autofix_commit)."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone

from ..analysis import analyze_source, broad_excepts, scan_secrets_text, unified_diff
from ..config import PATHS
from ..workspace import InputError, base_files, head_files, load_pr, safe_name

AUTO_FIXABLE = {"QLT001": "remove debug print / breakpoint lines", "QLT004": "remove newly unused imports",
                "QLT002": "replace bare except with except Exception", "SEC001": "move a hard-coded secret to an environment variable"}


def _branch_path(pr_id: str):
    return PATHS.review_branches / f"{safe_name(pr_id)}.json"


def branch_files(repo: str, pr_id: str) -> dict[str, str]:
    files = head_files(repo, pr_id)
    p = _branch_path(pr_id)
    if p.exists():
        b = json.loads(p.read_text(encoding="utf-8"))
        files.update(b["files"])
    return files


def branch_info(pr_id: str) -> dict | None:
    p = _branch_path(pr_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _added_lines(base: str | None, head: str, path: str) -> set[int]:
    return {x["line"] for x in unified_diff(base, head, path)["added_lines"]}


def _fix_file(path: str, base: str | None, head: str, rules: set[str]) -> tuple[str, list[dict]]:
    added = _added_lines(base, head, path)
    lines = head.splitlines()
    applied, drop = [], set()
    info = analyze_source(head, path)
    if "QLT001" in rules and not path.split("/")[-1].startswith("test_"):
        for d in info["debug_calls"]:
            if d["line"] in added and lines[d["line"] - 1].strip().startswith(d["call"] + "("):
                drop.add(d["line"])
                applied.append({"rule": "QLT001", "path": path, "line": d["line"], "change": f"removed {d['call']}() call"})
    if "QLT004" in rules:
        base_unused = {u["name"] for u in analyze_source(base, path)["unused_imports"]} if base else set()
        for u in info["unused_imports"]:
            if u["name"] in base_unused:
                continue
            ln = lines[u["line"] - 1]
            if re.match(rf"^\s*import\s+{re.escape(u['name'])}\s*$", ln):
                drop.add(u["line"])
            elif re.match(r"^\s*from\s+\S+\s+import\s+", ln):
                names = [n.strip() for n in ln.split("import", 1)[1].split(",")]
                keep = [n for n in names if n.split(" as ")[-1].strip() != u["name"]]
                if keep:
                    lines[u["line"] - 1] = ln.split("import", 1)[0] + "import " + ", ".join(keep)
                else:
                    drop.add(u["line"])
            else:
                continue
            applied.append({"rule": "QLT004", "path": path, "line": u["line"], "change": f"removed unused import {u['name']}"})
    if "QLT002" in rules:
        for e in broad_excepts(head):
            if e["kind"] == "bare except" and e["line"] in added:
                lines[e["line"] - 1] = re.sub(r"except\s*:", "except Exception:", lines[e["line"] - 1])
                applied.append({"rule": "QLT002", "path": path, "line": e["line"], "change": "bare except -> except Exception"})
    if "SEC001" in rules:
        needs_os = False
        for s in scan_secrets_text(head):
            ln = lines[s["line"] - 1]
            m = re.match(r"^(\s*)([A-Z][A-Z0-9_]*)\s*=\s*(.+)$", ln)
            if s["line"] in added and m and s["kind"] != "private_key":
                lines[s["line"] - 1] = f'{m.group(1)}{m.group(2)} = os.environ.get("{m.group(2)}", "")'
                needs_os = True
                applied.append({"rule": "SEC001", "path": path, "line": s["line"], "change": f"{m.group(2)} now read from the environment"})
            elif s["line"] in added and "os.environ.get(" in ln:
                lines[s["line"] - 1] = re.sub(r",\s*['\"][^'\"]+['\"]\)", ', "")', ln)
                applied.append({"rule": "SEC001", "path": path, "line": s["line"], "change": "removed the secret default value"})
        if needs_os and not re.search(r"(?m)^import os\b", "\n".join(lines)):
            insert_at = 0
            try:
                tree = ast.parse(head)
                if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant):
                    insert_at = tree.body[0].end_lineno
            except SyntaxError:
                pass
            lines.insert(insert_at, "import os")
            drop = {d + 1 if d > insert_at else d for d in drop}
    new = "\n".join(l for i, l in enumerate(lines, 1) if i not in drop)
    if head.endswith("\n"):
        new += "\n"
    return new, applied


def create_fix_branch(pr_id: str, rules: list[str]) -> dict:
    unknown = [r for r in rules if r not in AUTO_FIXABLE]
    if unknown:
        raise InputError(f"not auto-fixable: {unknown}; supported: {sorted(AUTO_FIXABLE)}")
    pr = load_pr(pr_id)
    base = base_files(pr["repo"], pr_id)
    files, applied, patches = {}, [], []
    for path, content in pr["changes"].items():
        if content is None or not path.endswith(".py"):
            continue
        new, ap = _fix_file(path, base.get(path), content, set(rules))
        if ap and new != content:
            files[path] = new
            applied += ap
            patches.append(unified_diff(content, new, path)["patch"])
    rec = {"pr_id": pr_id, "repo": pr["repo"], "branch": f"reviewpilot/autofix-{pr_id.lower()}", "rules": rules, "files": files, "applied": applied,
           "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if files:
        _branch_path(pr_id).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return {"branch": rec["branch"], "files_changed": len(files), "applied": applied, "patch": "\n".join(patches)}


def delete_fix_branch(pr_id: str) -> bool:
    p = _branch_path(pr_id)
    if p.exists():
        p.unlink()
        return True
    return False
