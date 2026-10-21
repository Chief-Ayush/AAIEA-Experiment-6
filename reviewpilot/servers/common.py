"""Helpers shared by the MCP servers: error categories, blocking-work offloading and fault injection for tests."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..config import PATHS
from ..github import GitHubError
from ..workspace import InputError, SecurityError

FAULT_FILE = PATHS.logs / "faults.json"
_LOCK = threading.Lock()

# Risk levels are declared by every server through MCP tool annotations; the client-side policy layer reads them at discovery.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
RUNS_PROCESS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def risk_meta(level: str) -> dict:
    return {"risk": level}


def tagged(category: str, message: str) -> ToolError:
    """Errors carry a [CATEGORY] prefix so the client can classify them (input / security / timeout / tool / execution)."""
    return ToolError(f"[{category}] {message}")


def consume_fault(tool: str) -> None:
    """Fault injection for the failure-handling tests.  storage/mcp_logs/faults.json maps tool -> {kind, count, delay_s}.
    kinds: transient (raise a retryable execution error), slow (sleep), crash (terminate the server process), tool_error."""
    if not FAULT_FILE.exists():
        return
    with _LOCK:
        try:
            faults = json.loads(FAULT_FILE.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            return
        f = faults.get(tool)
        if not f or f.get("count", 0) <= 0:
            return
        f["count"] -= 1
        try:
            FAULT_FILE.write_text(json.dumps(faults), encoding="utf-8")
        except OSError:                        # another server process holds the file (Windows) - the fault still fires once
            pass
    kind = f.get("kind")
    if kind == "slow":
        time.sleep(float(f.get("delay_s", 2.0)))
    elif kind == "transient":
        raise tagged("EXECUTION", f"{tool}: transient failure (injected)")
    elif kind == "tool_error":
        raise tagged("TOOL", f"{tool}: tool returned an error result (injected)")
    elif kind == "crash":
        sys.stderr.write(f"{tool}: server crash (injected)\n")
        sys.stderr.flush()
        os._exit(17)


async def run_blocking(tool: str, fn, *args, **kwargs):
    """Run blocking work in a worker thread so one server can serve parallel requests; map our exceptions to tagged errors."""
    def call():
        consume_fault(tool)
        return fn(*args, **kwargs)
    try:
        return await anyio.to_thread.run_sync(lambda: call())
    except ToolError:
        raise
    except GitHubError as exc:
        raise tagged(exc.category, str(exc)) from exc
    except SecurityError as exc:
        raise tagged("SECURITY", str(exc)) from exc
    except (InputError, FileNotFoundError, KeyError) as exc:
        raise tagged("INPUT", str(exc).strip("'\"")) from exc
    except TimeoutError as exc:
        raise tagged("TIMEOUT", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise tagged("EXECUTION", f"{type(exc).__name__}: {exc}") from exc


def check_ref(ref: str) -> str:
    if ref not in ("base", "head", "autofix"):
        raise InputError("ref must be 'base', 'head' or 'autofix'")
    return ref
