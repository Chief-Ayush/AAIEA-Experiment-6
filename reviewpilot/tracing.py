"""Observability: trace ids, task ids, JSONL trace records (mcp_traces.jsonl) and the execution log (execution.log)."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from .config import PATHS

_LOG_LOCK = threading.Lock()


def _logger() -> logging.Logger:
    log = logging.getLogger("reviewpilot")
    if not log.handlers:
        h = logging.FileHandler(PATHS.execution_log, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


LOG = _logger()


def new_trace_id() -> str:
    return f"TRACE-{uuid.uuid4().hex[:8].upper()}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _short(v, n=300):
    s = json.dumps(v, default=str)
    return v if len(s) <= n else s[:n] + "..."


class Trace:
    """One workflow.  Hands out TASK-001, TASK-002 ... and persists every tool call as one JSON line."""

    def __init__(self, trace_id: str | None = None, persist: bool = True):
        self.trace_id = trace_id or new_trace_id()
        self.persist = persist
        self.records: list[dict] = []
        self._n = 0
        self._lock = threading.Lock()

    def next_task_id(self) -> str:
        with self._lock:
            self._n += 1
            return f"TASK-{self._n:03d}"

    def record(self, *, task_id: str, tool: str, server: str, arguments: dict, status: str, duration_ms: float, attempt: int,
               error: str | None = None, category: str | None = None, phase: int | None = None, agent: str | None = None, risk: str | None = None,
               started_ms: float | None = None) -> dict:
        rec = {"trace_id": self.trace_id, "task_id": task_id, "tool": tool, "server": server, "arguments": _short(arguments), "status": status,
               "execution_time_ms": round(duration_ms, 2), "timestamp": now_iso(), "error": error, "error_category": category, "attempt": attempt,
               "phase": phase, "agent": agent, "risk": risk, "started_ms": round(started_ms, 2) if started_ms is not None else None}
        with self._lock:
            self.records.append(rec)
        if self.persist:
            with _LOG_LOCK:
                with open(PATHS.trace_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
            (LOG.warning if status != "success" else LOG.info)(
                f"{self.trace_id} {task_id} {tool}@{server} {status} {duration_ms:.1f}ms attempt={attempt}" + (f" [{category}] {error}" if error else ""))
        return rec

    def event(self, kind: str, **kw) -> None:
        if self.persist:
            LOG.info(f"{self.trace_id} {kind} " + json.dumps(kw, default=str)[:500])
