"""Central configuration.  Every tunable lives in Settings so experiments only override fields."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def load_env_file() -> None:
    """Minimal .env loader (KEY=value lines).  Real environment variables win over the file."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def save_env_value(key: str, value: str | None) -> None:
    """Write (or remove) one KEY=value in .env and in the current process environment."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith(f"{key}=")]
    if value:
        lines.append(f"{key}={value}")
        os.environ[key] = value
    else:
        os.environ.pop(key, None)
    ENV_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


load_env_file()


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    repos: Path = ROOT / "data" / "repos"            # base snapshots (the "main" branch of each sample repository)
    prs: Path = ROOT / "data" / "prs"                # pull requests: metadata + changed files
    eval_data: Path = ROOT / "data" / "eval"
    storage: Path = ROOT / "storage"
    logs: Path = ROOT / "storage" / "mcp_logs"       # execution.log, mcp_traces.jsonl, reports (lab manual 10.1)
    worktrees: Path = ROOT / "storage" / "worktrees"  # temporary PR checkouts used by run_tests
    review_branches: Path = ROOT / "storage" / "review_branches"  # auto-fix branches written by the automation server
    reviews: Path = ROOT / "storage" / "reviews"      # posted reviews
    reports: Path = ROOT / "storage" / "reports"      # evaluation reports
    batches: Path = ROOT / "storage" / "batches"
    cache: Path = ROOT / "storage" / "cache"          # GitHub snapshots (one JSON per commit sha)

    def ensure(self) -> "Paths":
        for p in (self.logs, self.worktrees, self.review_branches, self.reviews, self.reports, self.batches, self.eval_data, self.cache):
            p.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def trace_file(self) -> Path:
        return self.logs / "mcp_traces.jsonl"

    @property
    def execution_log(self) -> Path:
        return self.logs / "execution.log"


PATHS = Paths().ensure()

# MCP servers: name -> module run with `python -m` over stdio.
SERVERS = {
    "repo": {"module": "reviewpilot.servers.repo_server", "title": "Repository server", "purpose": "files, code search and pull-request data"},
    "analysis": {"module": "reviewpilot.servers.analysis_server", "title": "Analysis server", "purpose": "AST analysis, complexity, symbol usage"},
    "quality": {"module": "reviewpilot.servers.quality_server", "title": "Quality server", "purpose": "pytest, lint, secret and security scanning"},
    "automation": {"module": "reviewpilot.servers.automation_server", "title": "Automation server", "purpose": "auto-fix branches and posting reviews"},
}

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
VERDICTS = ["APPROVE", "COMMENT", "REQUEST_CHANGES", "BLOCK"]


@dataclass(frozen=True)
class Settings:
    # execution strategy
    execution_mode: str = "parallel"        # parallel | sequential
    dependency_aware: bool = True           # phase scheduling; False = naive "run everything, then check"
    max_parallel: int = 8                   # degree of parallelism cap (asyncio semaphore)
    # reliability
    retry_policy: str = "controlled"        # controlled | naive | none
    max_retries: int = 2
    retry_backoff_s: float = 0.05
    tool_timeout_s: float = 20.0
    test_timeout_s: float = 30.0            # timeout protection for pytest
    # protocol
    validate_arguments: bool = True         # client-side JSON-schema validation before tools/call
    reuse_connections: bool = True          # keep MCP sessions open between workflows
    # safety
    enforce_policy: bool = True
    require_approval_for: str = "HIGH"      # risk level that needs a human
    # human in the loop
    approval_mode: str = "auto"             # auto | manual | off
    interactive: bool = False
    # review policy
    complexity_threshold: int = 10
    large_pr_lines: int = 400
    full_suite_after_affected: bool = True

    def with_(self, **kw) -> "Settings":
        return replace(self, **{k: v for k, v in kw.items() if v is not None})

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- user preferences (Settings page)
PREFS_FILE = PATHS.storage / "settings.json"


def load_settings() -> Settings:
    """Settings saved from the Settings page (unknown keys ignored)."""
    try:
        data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"approval_mode": "manual"}          # the app asks before posting unless told otherwise
    names = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in names})


def save_settings(s: Settings) -> None:
    PREFS_FILE.write_text(json.dumps(s.to_dict(), indent=1), encoding="utf-8")


# data/ (sample repositories, pull requests, labels) is generated and git-ignored: build it on first start.
from .benchmark.build import ensure_data  # noqa: E402

ensure_data()
