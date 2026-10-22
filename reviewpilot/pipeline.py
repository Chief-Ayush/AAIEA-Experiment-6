"""Facade used by the UI, the CLI and the evaluation suite."""
from __future__ import annotations

import threading

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .agents.base import register, release
from .config import Settings
from .graph import build_review_graph, build_workbench_graph
from .mcp_client import MCPHub, get_hub
from .tracing import Trace

_CHECKPOINTER = MemorySaver()
_LOCK = threading.Lock()
_GRAPHS: dict[str, object] = {}


def _graph(kind: str):
    with _LOCK:
        if kind not in _GRAPHS:
            _GRAPHS[kind] = build_review_graph(_CHECKPOINTER) if kind == "review" else build_workbench_graph(_CHECKPOINTER)
        return _GRAPHS[kind]


def _finish(kind: str, cfg: dict, trace: Trace) -> dict:
    g = _graph(kind)
    snap = g.get_state(cfg)
    state = dict(snap.values)
    pending = [i.value for t in snap.tasks for i in (t.interrupts or [])]
    state["trace_id"] = trace.trace_id
    state["trace"] = list(trace.records)
    if pending:
        state["status"] = "awaiting_human"
        state["interrupt"] = pending[0]
        state["thread_id"] = cfg["configurable"]["thread_id"]
    else:
        release(trace.trace_id)
    return state


def review(pr_id: str, settings: Settings | None = None, hub: MCPHub | None = None, persist: bool = True) -> dict:
    s = settings or Settings()
    trace = Trace(persist=persist)
    register(trace, hub or get_hub())
    cfg = {"configurable": {"thread_id": trace.trace_id}}
    trace.event("review_start", pr_id=pr_id, settings=s.to_dict())
    _graph("review").invoke({"trace_id": trace.trace_id, "pr_id": pr_id, "settings": s.to_dict()}, cfg)
    return _finish("review", cfg, trace)


def resume_review(trace_id: str, decision: dict) -> dict:
    from .agents.base import _TRACES
    trace = _TRACES.get(trace_id) or Trace(trace_id)
    cfg = {"configurable": {"thread_id": trace_id}}
    _graph("review").invoke(Command(resume=decision), cfg)
    return _finish("review", cfg, trace)


def workbench(request: str, default_repo: str | None = None, settings: Settings | None = None, hub: MCPHub | None = None,
              persist: bool = True) -> dict:
    s = settings or Settings()
    trace = Trace(persist=persist)
    register(trace, hub or get_hub())
    cfg = {"configurable": {"thread_id": trace.trace_id}}
    trace.event("workbench_start", request=request)
    _graph("workbench").invoke({"trace_id": trace.trace_id, "request": request, "default_repo": default_repo or "", "settings": s.to_dict()}, cfg)
    return _finish("workbench", cfg, trace)


def resume_workbench(trace_id: str, decision: dict) -> dict:
    from .agents.base import _TRACES
    trace = _TRACES.get(trace_id) or Trace(trace_id)
    cfg = {"configurable": {"thread_id": trace_id}}
    _graph("workbench").invoke(Command(resume=decision), cfg)
    return _finish("workbench", cfg, trace)
