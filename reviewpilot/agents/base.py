"""Shared agent plumbing: run-time registry (trace objects live outside the checkpointed state) and step logging."""
from __future__ import annotations

import functools
import time

from ..config import Settings
from ..mcp_client import MCPHub, get_hub
from ..tracing import LOG, Trace

_TRACES: dict[str, Trace] = {}
_HUBS: dict[str, MCPHub] = {}


def register(trace: Trace, hub: MCPHub | None = None) -> None:
    _TRACES[trace.trace_id] = trace
    if hub is not None:
        _HUBS[trace.trace_id] = hub


def trace_of(state: dict) -> Trace:
    tid = state["trace_id"]
    if tid not in _TRACES:
        _TRACES[tid] = Trace(tid)
    return _TRACES[tid]


def hub_of(state: dict) -> MCPHub:
    return _HUBS.get(state["trace_id"]) or get_hub()


def settings_of(state: dict) -> Settings:
    return Settings(**state.get("settings", {}))


def release(trace_id: str) -> None:
    _TRACES.pop(trace_id, None)
    _HUBS.pop(trace_id, None)


def agent(name: str):
    """Decorator for LangGraph nodes: times the step and appends {agent, ms, summary} to state['steps'] (a list reducer)."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state: dict):
            t0 = time.perf_counter()
            out = fn(state) or {}
            summary = out.pop("_summary", "")
            ms = round((time.perf_counter() - t0) * 1000, 1)
            step = {"agent": name, "ms": ms, "summary": summary}
            out["steps"] = [step]
            if state.get("trace_id") and trace_of(state).persist:
                LOG.info(f"{state['trace_id']} agent {name} {ms}ms - {summary}")
            return out
        wrapper.agent_name = name
        return wrapper
    return deco
