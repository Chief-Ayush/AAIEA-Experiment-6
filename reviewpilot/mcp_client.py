"""MCP client layer.

`MCPHub` connects to every ReviewPilot MCP server over stdio (one server process each, `ClientSession` from the official
MCP Python SDK), discovers their tools with `session.list_tools()` and calls them with `session.call_tool()`.

* Sessions live on a dedicated asyncio loop in a background thread, so synchronous code (agents, Streamlit, tests) can use
  them and independent calls still run concurrently (asyncio.gather on that loop).
* Each connection is owned by its own long-lived task (the SDK's stdio transport must be opened and closed in the same task).
* Results are normalised into ToolResult; failures are classified with the lab-manual categories
  (input, tool, security, timeout, execution) and retried only when the category is retryable.
* A crashed server is detected (closed transport), reconnected and the call retried - recovery, not just retry.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

import anyio
import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from .config import PATHS, ROOT, SERVERS, Settings
from .tracing import LOG, Trace
from .workspace import env_for_subprocess

RETRYABLE = {"TIMEOUT", "EXECUTION"}
CATEGORY_ACTION = {"INPUT": "stop (no retry)", "TOOL": "stop / report", "SECURITY": "stop (no retry)", "TIMEOUT": "retry (limited)", "EXECUTION": "retry (limited)"}


@dataclass
class ToolInfo:
    name: str
    server: str
    title: str | None
    description: str
    input_schema: dict
    output_schema: dict | None
    annotations: dict
    risk: str

    def public(self) -> dict:
        return asdict(self)


@dataclass
class ToolResult:
    tool: str
    server: str
    ok: bool
    data: Any = None
    error: str | None = None
    category: str | None = None
    duration_ms: float = 0.0
    attempts: int = 1
    task_id: str | None = None
    output_valid: bool | None = None

    def public(self) -> dict:
        return asdict(self)


def risk_of(meta: dict | None, ann: dict) -> str:
    if meta and meta.get("risk") in ("LOW", "MEDIUM", "HIGH"):
        return meta["risk"]
    if ann.get("destructiveHint"):
        return "HIGH"
    if ann.get("readOnlyHint"):
        return "LOW"
    return "MEDIUM"


def classify(message: str) -> str:
    m = message or ""
    for tag in ("SECURITY", "INPUT", "TIMEOUT", "TOOL", "EXECUTION"):
        if f"[{tag}]" in m:
            return tag
    low = m.lower()
    if "unknown tool" in low or "validation error" in low or "input should be" in low or "missing required" in low or "field required" in low:
        return "INPUT"
    if "timed out" in low or "timeout" in low:
        return "TIMEOUT"
    return "EXECUTION"


class ServerConnection:
    def __init__(self, name: str):
        self.name = name
        self.module = SERVERS[name]["module"]
        self.session: ClientSession | None = None
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.tools: list[ToolInfo] = []
        self.error: str | None = None
        self.connects = 0
        self.connected_at: float | None = None
        self.connect_ms: float = 0.0
        self._task: asyncio.Task | None = None
        self._close = asyncio.Event()
        self._ready = asyncio.Event()

    def params(self) -> StdioServerParameters:
        return StdioServerParameters(command=sys.executable, args=["-m", self.module], cwd=str(ROOT), env=env_for_subprocess())

    async def start(self) -> None:
        self._close, self._ready = asyncio.Event(), asyncio.Event()
        self.error = None
        self._task = asyncio.create_task(self._runner(), name=f"mcp-{self.name}")
        await self._ready.wait()
        if self.error:
            raise ConnectionError(f"{self.name}: {self.error}")

    async def _runner(self) -> None:
        t0 = time.perf_counter()
        errlog = open(PATHS.logs / f"server_{self.name}.log", "a", encoding="utf-8")
        try:
            async with stdio_client(self.params(), errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    listed = await session.list_tools()
                    self.server_info = {"name": init.serverInfo.name, "version": init.serverInfo.version, "protocol": init.protocolVersion,
                                        "instructions": init.instructions}
                    self.capabilities = init.capabilities.model_dump(exclude_none=True)
                    self.tools = []
                    for t in listed.tools:
                        ann = t.annotations.model_dump(exclude_none=True) if t.annotations else {}
                        self.tools.append(ToolInfo(t.name, self.name, t.title or ann.get("title"), t.description or "", t.inputSchema, t.outputSchema, ann,
                                                   risk_of(t.meta, ann)))
                    self.session = session
                    self.connects += 1
                    self.connected_at = time.time()
                    self.connect_ms = round((time.perf_counter() - t0) * 1000, 1)
                    self._ready.set()
                    await self._close.wait()
        except BaseException as exc:  # noqa: BLE001 - includes cancellation and transport errors
            if not self._ready.is_set():
                while getattr(exc, "exceptions", None):           # unwrap anyio / asyncio exception groups
                    exc = exc.exceptions[0]
                self.error = (f"{type(exc).__name__}: {exc} - the server process did not start; its traceback is in "
                              f"storage/mcp_logs/server_{self.name}.log")
        finally:
            self.session = None
            self._ready.set()
            errlog.close()

    @property
    def alive(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def stop(self) -> None:
        self._close.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 5)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self._task.cancel()
        self.session = None


class MCPHub:
    """Multi-server MCP client.  Thread-safe facade: every public method can be called from synchronous code."""

    def __init__(self, servers: list[str] | None = None):
        self.names = servers or list(SERVERS)
        # stdio transport starts the servers as subprocesses.  On Windows only the Proactor loop supports subprocesses, and some hosts
        # (e.g. web servers) switch the global policy to the Selector loop, so the hub creates its own Proactor loop explicitly.
        self.loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="mcp-hub", daemon=True)
        self._thread.start()
        self.conns: dict[str, ServerConnection] = {}
        self._reconnect_locks: dict[str, asyncio.Lock] = {}
        self.stats = {"calls": 0, "reconnects": 0, "fresh_connections": 0}
        self.run(self._connect_all())

    # ---------------------------------------------------------------- loop plumbing
    def run(self, coro, timeout: float | None = 600):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _connect_all(self):
        async def one(n):
            c = ServerConnection(n)
            self.conns[n] = c
            self._reconnect_locks[n] = asyncio.Lock()
            await c.start()
        await asyncio.gather(*(one(n) for n in self.names))
        LOG.info("MCP hub connected: " + ", ".join(f"{n} ({len(c.tools)} tools, {c.connect_ms} ms)" for n, c in self.conns.items()))

    def close(self):
        async def stop_all():
            await asyncio.gather(*(c.stop() for c in self.conns.values()), return_exceptions=True)
        try:
            self.run(stop_all(), timeout=15)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)

    # ---------------------------------------------------------------- discovery
    def tools(self) -> dict[str, ToolInfo]:
        return {t.name: t for c in self.conns.values() for t in c.tools}

    def discover(self) -> list[dict]:
        """Fresh tools/list on every server (what the Supervisor validates plans against)."""
        async def go():
            out = []
            for n, c in self.conns.items():
                if not c.alive:
                    await self._reconnect(n)
                listed = await c.session.list_tools()
                for t in listed.tools:
                    ann = t.annotations.model_dump(exclude_none=True) if t.annotations else {}
                    out.append({"name": t.name, "server": n, "description": t.description, "risk": risk_of(t.meta, ann), "annotations": ann,
                                "input_schema": t.inputSchema, "output_schema": t.outputSchema})
            return out
        return self.run(go())

    def server_status(self) -> list[dict]:
        return [{"server": n, "alive": c.alive, "tools": len(c.tools), "connects": c.connects, "connect_ms": c.connect_ms, "module": c.module,
                 **{f"server_{k}": v for k, v in c.server_info.items() if k != "instructions"}} for n, c in self.conns.items()]

    def resources(self) -> list[dict]:
        async def go():
            out = []
            for n, c in self.conns.items():
                if not c.alive:
                    await self._reconnect(n)
                try:
                    tpl = await c.session.list_resource_templates()
                    out += [{"server": n, "uri": t.uriTemplate, "description": t.description, "kind": "template"} for t in tpl.resourceTemplates]
                    res = await c.session.list_resources()
                    out += [{"server": n, "uri": str(r.uri), "description": r.description, "kind": "resource"} for r in res.resources]
                except (McpError, anyio.ClosedResourceError):
                    pass
            return out
        return self.run(go())

    def read_resource(self, server: str, uri: str) -> str:
        async def go():
            r = await self.conns[server].session.read_resource(uri)
            return "\n".join(getattr(c, "text", "") for c in r.contents)
        return self.run(go())

    # ---------------------------------------------------------------- calls
    async def _reconnect(self, server: str, force: bool = False, seen_connects: int | None = None) -> None:
        async with self._reconnect_locks[server]:
            c = self.conns[server]
            if c.alive and not force:
                return
            if force and seen_connects is not None and c.connects > seen_connects and c.alive:
                return                                  # another task already reconnected this server
            await c.stop()
            await c.start()
            self.stats["reconnects"] += 1
            LOG.warning(f"MCP server '{server}' reconnected")

    def validate_arguments(self, tool: str, arguments: dict) -> str | None:
        info = self.tools().get(tool)
        if info is None:
            return f"Unknown tool: {tool}"
        try:
            jsonschema.validate(arguments, info.input_schema)
        except jsonschema.ValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "arguments"
            return f"invalid argument {where}: {exc.message}"
        return None

    @staticmethod
    def validate_output(info: ToolInfo | None, data: Any) -> bool | None:
        if info is None or not info.output_schema or data is None:
            return None
        try:
            jsonschema.validate(data, info.output_schema)
            return True
        except jsonschema.ValidationError:
            return False

    async def _once(self, tool: str, arguments: dict, timeout: float, fresh: bool) -> tuple[Any, str | None]:
        info = self.tools()[tool]
        if fresh:
            self.stats["fresh_connections"] += 1
            c = ServerConnection(info.server)
            with open(PATHS.logs / f"server_{info.server}.log", "a", encoding="utf-8") as errlog:
                async with stdio_client(c.params(), errlog=errlog) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        res = await asyncio.wait_for(s.call_tool(tool, arguments), timeout)
        else:
            conn = self.conns[info.server]
            if not conn.alive:
                await self._reconnect(info.server)
                conn = self.conns[info.server]
            res = await asyncio.wait_for(conn.session.call_tool(tool, arguments), timeout)
        text = "\n".join(getattr(c, "text", "") for c in res.content)
        if res.isError:
            return None, text
        data = res.structuredContent
        if data is None:
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                data = {"text": text}
        return data, None

    async def acall(self, tool: str, arguments: dict | None = None, *, settings: Settings | None = None, trace: Trace | None = None,
                    task_id: str | None = None, phase: int | None = None, agent: str | None = None) -> ToolResult:
        s = settings or Settings()
        args = dict(arguments or {})
        trace = trace or Trace(persist=False)
        task_id = task_id or trace.next_task_id()
        info = self.tools().get(tool)
        server = info.server if info else "?"
        risk = info.risk if info else None
        self.stats["calls"] += 1
        if info is None:
            r = ToolResult(tool, server, False, error=f"Unknown tool: {tool}", category="INPUT", task_id=task_id)
            trace.record(task_id=task_id, tool=tool, server=server, arguments=args, status="rejected", duration_ms=0, attempt=0, error=r.error,
                         category="INPUT", phase=phase, agent=agent)
            return r
        if s.validate_arguments:
            problem = self.validate_arguments(tool, args)
            if problem:
                trace.record(task_id=task_id, tool=tool, server=server, arguments=args, status="rejected", duration_ms=0, attempt=0, error=problem,
                             category="INPUT", phase=phase, agent=agent, risk=risk)
                return ToolResult(tool, server, False, error=problem, category="INPUT", task_id=task_id, attempts=0)
        max_attempts = 1 + (0 if s.retry_policy == "none" else (4 if s.retry_policy == "naive" else s.max_retries))
        timeout = s.test_timeout_s + 10 if tool == "run_tests" else s.tool_timeout_s
        attempt, result = 0, None
        while attempt < max_attempts:
            attempt += 1
            t0 = time.perf_counter()
            started = t0 * 1000
            seen = self.conns[server].connects
            try:
                data, err = await self._once(tool, args, timeout, fresh=not s.reuse_connections)
                cat = classify(err) if err else None
            except asyncio.TimeoutError:
                data, err, cat = None, f"[TIMEOUT] no response from {tool} within {timeout:.0f}s", "TIMEOUT"
            except (McpError, anyio.ClosedResourceError, anyio.BrokenResourceError, ConnectionError, OSError) as exc:
                data, err, cat = None, f"[EXECUTION] transport failure: {type(exc).__name__}: {exc}", "EXECUTION"
                if s.reuse_connections:
                    try:
                        await self._reconnect(server, force=True, seen_connects=seen)
                    except Exception as rexc:  # noqa: BLE001
                        err += f"; reconnect failed: {rexc}"
            except Exception as exc:  # noqa: BLE001
                data, err, cat = None, f"[EXECUTION] {type(exc).__name__}: {exc}", "EXECUTION"
            dur = (time.perf_counter() - t0) * 1000
            ok = err is None
            trace.record(task_id=task_id, tool=tool, server=server, arguments=args, status="success" if ok else "failed", duration_ms=dur,
                         attempt=attempt, error=err, category=cat, phase=phase, agent=agent, risk=risk, started_ms=started)
            result = ToolResult(tool, server, ok, data=data, error=err, category=cat, duration_ms=round(dur, 2), attempts=attempt, task_id=task_id,
                                output_valid=self.validate_output(info, data) if ok else None)
            if ok:
                break
            retryable = s.retry_policy == "naive" or cat in RETRYABLE
            if not retryable or attempt >= max_attempts:
                break
            if s.retry_policy == "controlled":
                await asyncio.sleep(s.retry_backoff_s * 2 ** (attempt - 1))
        return result

    def call(self, tool: str, arguments: dict | None = None, **kw) -> ToolResult:
        return self.run(self.acall(tool, arguments, **kw))

    # ---------------------------------------------------------------- fault injection (failure-handling tests)
    @staticmethod
    def set_faults(faults: dict | None) -> None:
        p = PATHS.logs / "faults.json"
        if faults:
            p.write_text(json.dumps(faults), encoding="utf-8")
        elif p.exists():
            p.unlink()


_HUB: MCPHub | None = None
_HUB_LOCK = threading.Lock()


def get_hub() -> MCPHub:
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = MCPHub()
        return _HUB


def reset_hub() -> None:
    """Stop all MCP servers so the next get_hub() starts them again (e.g. after the GitHub token changed)."""
    global _HUB
    with _HUB_LOCK:
        if _HUB is not None:
            try:
                _HUB.close()
            except Exception:  # noqa: BLE001
                pass
        _HUB = None
