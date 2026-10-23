"""Generic MCP servers: every configured `tools.mcp_servers` entry, isolated.

Home Assistant already had its own bridge (`tools/ha.py`): a whitelist, entity
context and a hard refusal for dangerous domains. Everything else that speaks MCP -
a filesystem server, a Git server, a notes server - had a config shape and no
implementation (`generic_wired: false`). This module is that implementation.

Design rules, each one learned from the HA client next door:

* **one task per server.** A connection, its queue and its reconnect loop belong to
  one server. A dead server fails *its* calls; the other servers, HA, music and the
  voice loop are untouched.
* **degrade, never raise.** `MCPUnavailable` means "this tool cannot answer right
  now"; the tool layer renders a phrase. A server that dies mid-turn must not take
  the turn down.
* **stable names.** The registry only accepts `source.tool` (one dot), so a tool is
  registered as `mcp.<server>_<tool>` (both halves slugged). The model-facing name
  is the part after the dot (`<server>_<tool>`), which is unique per server+tool -
  `registry.from_mcp`'s `<server>.<tool>` would collide in the runner's handler map
  when two servers share a tool name.
* **untrusted data.** Tool descriptions and results come from another process and
  are labelled/stripped exactly like HA entity names (`tools.ha`'s sanitizer), so a
  malicious server cannot write instructions into the prompt through a tool name.
* **hot re-apply.** `apply()` diffs by name+transport+command/url+enabled, keeps
  unchanged sessions alive and reconnects only what changed.
* **transports.** Streamable HTTP and stdio (the MCP SDK's own transports). stdio
  runs a local command: treat adding one exactly like running a program from the
  console. It is behind `tools.mcp_allow_stdio` (default false) and the child gets a
  *minimal* environment - never the service's `.env` with provider/HA/device keys.
  `McpServerConfig` has no `env` field yet; if a future schema adds one it is merged
  into the minimal env, and it never replaces it with the full `os.environ`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from ..config.schema import McpServerConfig
from .contract import InteractionSpec, Permission, ToolResult, ToolSpec
from .ha import _clean_untrusted, _untrusted  # one sanitizer for all MCP-sourced text
from .mcp_client import RECONNECT_MAX_S, MCPUnavailable

log = logging.getLogger(__name__)


class _SessionLost(RuntimeError):
    """The transport died during a call: reconnect. Tool-level MCP errors do not raise."""


HANDSHAKE_TIMEOUT_S = 8.0
CALL_TIMEOUT_S = 8.0
MAX_TOOL_TEXT = 1500
MAX_DESCRIPTION = 500
MAX_TOOL_NAMES_IN_STATUS = 50

_SECRET_QUERY_RE = re.compile(
    r"([?&](?:p|t|password|token|api_key|key|u)=)[^&\s]+", re.IGNORECASE)


def _slug(value: str) -> str:
    """Registry-safe token: lower case, non-alphanumerics collapsed to underscores."""
    out = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return out or "x"


def tool_name(server: str, tool: str) -> str:
    """`mcp.<server>_<tool>` - one dot because `ToolRegistry` accepts exactly one."""
    return f"mcp.{_slug(server)}_{_slug(tool)}"


def _leaf_error(exc: BaseException) -> BaseException:
    """The useful exception inside a TaskGroup wrapper.

    The SDK runs its transports in anyio task groups, so a refused connection surfaces
    as `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)` - true and
    useless in a status row. The leaf says `ConnectError: ...`.
    """
    groups = getattr(exc, "exceptions", None)
    if groups:
        return _leaf_error(groups[0])
    return exc


def _safe_error(exc: BaseException) -> str:
    """An exception sentence with credentials in query strings masked."""
    leaf = _leaf_error(exc)
    text = f"{type(leaf).__name__}: {leaf}"
    return _SECRET_QUERY_RE.sub(r"\1***", text)


def _is_protocol_error(exc: BaseException) -> bool:
    """True for an MCP-level error the server answered (session is still alive)."""
    with contextlib.suppress(ImportError):
        from mcp.shared.exceptions import MCPError

        return isinstance(exc, MCPError)
    return False


def _signature(server: McpServerConfig) -> tuple:
    """What "the same server" means for hot re-apply: a change reconnects it."""
    return (
        server.name,
        str(server.transport),
        str(server.url or "").strip(),
        tuple(str(part) for part in (server.command or [])),
        bool(server.enabled),
        str(getattr(server, "cwd", "") or ""),
        json.dumps(getattr(server, "env", None) or {}, sort_keys=True),
    )


# What a stdio child is allowed to see. Deliberately a whitelist, not os.environ:
# the service environment holds every provider/HA/device key (`.env` is loaded into
# it), and a third-party MCP server must not be handed the whole keyring just because
# the operator wanted a filesystem tool (security audit round 2, P0-1).
STDIO_BASIC_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TERM")
STDIO_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin"
STDIO_FALLBACK_HOME = "/tmp"


def _stdio_env(server: McpServerConfig) -> dict[str, str]:
    """Minimal child environment: service basics + what the server config declares."""
    env = {key: os.environ[key] for key in STDIO_BASIC_ENV if os.environ.get(key)}
    env.setdefault("PATH", STDIO_FALLBACK_PATH)
    env.setdefault("HOME", STDIO_FALLBACK_HOME)
    declared = getattr(server, "env", None)
    if isinstance(declared, dict):
        env.update({str(key): str(value) for key, value in declared.items()})
    return env


def _stdio_parameters(server: McpServerConfig) -> tuple[str, list[str], dict[str, str]]:
    """Split the configured command into (command, args, minimal env).

    `command` is a list by schema (`["npx", "-y", "..."]`). No shell is involved on
    purpose: a shell would turn a config typo into arbitrary code execution.
    """
    command = [str(part) for part in (server.command or []) if str(part).strip()]
    if not command:
        raise MCPUnavailable(f"{server.name}: stdio transport needs a command")
    return command[0], command[1:], _stdio_env(server)


@asynccontextmanager
async def open_session(server: McpServerConfig) -> AsyncIterator[Any]:
    """One initialized `ClientSession` over the configured transport.

    Kept as a module-level async context manager so tests can replace it with a
    fake session: the manager only needs `initialize()`, `list_tools()` and
    `call_tool()`, which is exactly the SDK surface this function exposes.
    """
    from mcp import ClientSession

    transport = str(getattr(server, "transport", "http") or "http")
    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command, args, env = _stdio_parameters(server)
        params = StdioServerParameters(
            command=command, args=args, env=env,
            cwd=getattr(server, "cwd", None),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                yield session
        return

    from mcp.client.streamable_http import streamable_http_client

    url = str(getattr(server, "url", "") or "").strip()
    if not url:
        raise MCPUnavailable(f"{server.name}: http transport needs a url")
    headers = getattr(server, "headers", None)
    http_client = None
    if isinstance(headers, dict) and headers:
        import httpx

        http_client = httpx.AsyncClient(headers=dict(headers), timeout=30)
    try:
        async with streamable_http_client(url, http_client=http_client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                yield session
    finally:
        if http_client is not None:
            with contextlib.suppress(Exception):
                await http_client.aclose()


def _permission(raw: dict) -> tuple[Permission, bool]:
    """MCP annotations -> registry permission. Unknown tools are WRITE, never auto-dang."""
    annotations = raw.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
    if annotations.get("destructiveHint") is True:
        return Permission.DANGEROUS, True
    if annotations.get("readOnlyHint") is True:
        return Permission.READ, False
    return Permission.WRITE, False


def _description(server: str, raw: dict) -> str:
    """A tool description is external text: sanitize, cap, never inject verbatim."""
    text = _clean_untrusted(str(raw.get("description") or "")).strip()
    if not text:
        text = f"{server}: {raw.get('name') or 'tool'}"
    return text[:MAX_DESCRIPTION]


def _result_text(result: Any) -> str:
    """MCP CallToolResult -> one model-facing string, labelled as untrusted data."""
    parts: list[str] = []
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
            else:
                parts.append("[non-text content omitted]")
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        with contextlib.suppress(TypeError, ValueError):
            parts.append(json.dumps(structured, ensure_ascii=False))
    if not parts:
        parts.append(str(result)[:MAX_TOOL_TEXT])
    return _untrusted("\n".join(parts)[:MAX_TOOL_TEXT]).rstrip()


class ManagedServer:
    """One configured generic MCP server: session task, queue, status.

    Mirrors `MCPClient`, but with both transports and a per-server status the console
    can render. `start()` never raises: a handshake failure returns False and the
    background loop keeps retrying with backoff.
    """

    def __init__(self, cfg: McpServerConfig,
                 on_change: Callable[[], None] | None = None) -> None:
        self.cfg = cfg
        self._on_change = on_change
        self._tools: list[dict] = []
        self._queue: asyncio.Queue[tuple[dict, asyncio.Future]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        # Set by the first connect *or* the first failure: `start()` must not block for
        # the whole handshake timeout when a server is simply not there.
        self._outcome = asyncio.Event()
        self._stopped = False
        self.connected = False
        self.last_error = ""
        self.latency_ms: float | None = None
        self.last_seen: float | None = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    async def start(self, timeout_s: float = HANDSHAKE_TIMEOUT_S) -> bool:
        """Spawn the session task and wait for the first handshake (or time out)."""
        if self._task is not None:
            return self.connected
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.cfg.name}")
        try:
            await asyncio.wait_for(self._outcome.wait(), timeout=timeout_s)
        except TimeoutError:
            if not self.last_error:
                self.last_error = "handshake timed out"
            return False
        return self.connected

    async def stop(self) -> None:
        self._stopped = True
        self.connected = False
        self._connected.clear()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._drain_queue()

    # ------------------------------------------------------------------ calls
    async def call(self, tool: str, args: dict[str, Any] | None = None,
                   timeout_s: float = CALL_TIMEOUT_S) -> Any:
        if self._task is None:
            raise MCPUnavailable(f"{self.cfg.name}: not started")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(({"tool": tool, "args": args or {}}, future))
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            raise MCPUnavailable(f"{self.cfg.name}.{tool}: timed out") from exc

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "name": self.cfg.name,
            "transport": str(self.cfg.transport),
            "url": self.cfg.url,
            "command": list(self.cfg.command or []),
            "enabled": bool(self.cfg.enabled),
            "connected": bool(self.connected),
            "tools": len(self._tools),
            "tool_names": [str(t.get("name") or "") for t in self._tools][
                :MAX_TOOL_NAMES_IN_STATUS],
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "error": self.last_error,
            "last_seen": self.last_seen,
        }

    # ------------------------------------------------------------------ loop
    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception as exc:  # noqa: BLE001 - a registration bug must not kill the session
            log.warning("mcp %s: tool-change callback failed: %s", self.cfg.name, exc)

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                connected_at = time.monotonic()
                async with open_session(self.cfg) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    handshake_ms = (time.monotonic() - connected_at) * 1000
                    tools = [tool.model_dump(exclude_none=True)
                             for tool in getattr(listed, "tools", []) or []]
                    changed = tools != self._tools
                    self._tools = tools
                    self.connected = True
                    self.last_error = ""
                    # Time to initialize + list tools; calls overwrite it with call RTT.
                    self.latency_ms = handshake_ms
                    self.last_seen = time.time()
                    self._connected.set()
                    self._outcome.set()
                    backoff = 1.0
                    log.info("mcp %s (%s): connected, %d tool(s)",
                             self.cfg.name, self.cfg.transport, len(self._tools))
                    if changed:
                        self._notify()
                    while not self._stopped:
                        request, future = await self._queue.get()
                        if future.done():
                            continue
                        started = time.monotonic()
                        try:
                            result = await session.call_tool(
                                request["tool"], request["args"])
                            self.latency_ms = (time.monotonic() - started) * 1000
                            self.last_seen = time.time()
                            if not future.done():
                                future.set_result(result)
                        except Exception as exc:  # noqa: BLE001 - one call's death
                            self.last_error = _safe_error(exc)
                            if not future.done():
                                future.set_exception(MCPUnavailable(
                                    f"{self.cfg.name}.{request['tool']}: "
                                    f"{self.last_error}"))
                            # A JSON-RPC error (unknown tool, bad arguments) is an
                            # answer from a live session; anything else is the transport
                            # dying, and the session must be reopened.
                            if _is_protocol_error(exc):
                                continue
                            raise _SessionLost(self.last_error) from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the session died: drain, wait, retry
                was_connected = self._connected.is_set()
                self.connected = False
                self._connected.clear()
                self.last_error = _safe_error(exc)
                if not was_connected:
                    self._outcome.set()   # first attempt failed: report, keep retrying
                log.info("mcp %s: down (%s), reconnect in %.0fs",
                         self.cfg.name, self.last_error, backoff)
                self._drain_queue()
                if self._stopped:
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            _request, future = self._queue.get_nowait()
            if not future.done():
                future.set_exception(
                    MCPUnavailable(f"{self.cfg.name}: connection down"))


class MCPManager:
    """All generic servers, started together and re-applied from config.

    `tool_pairs()` is what the app registers; the manager never touches the registry
    itself, so the runner's tool list stays the single wiring point (same shape as
    `HaBridge.tool_specs()`).
    """

    def __init__(self, servers: Sequence[McpServerConfig] = (),
                 on_change: Callable[[], None] | None = None,
                 allow_stdio: bool = False) -> None:
        self._configs: list[McpServerConfig] = list(servers)
        self._servers: dict[str, ManagedServer] = {}
        self._signatures: dict[str, tuple] = {}
        self.on_change = on_change
        # `tools.mcp_allow_stdio`: false means stdio entries are configured but never
        # spawned (and the console/API says why). Toggle at runtime via `apply` callers.
        self.allow_stdio = bool(allow_stdio)
        self._lock = asyncio.Lock()

    def _allowed(self, server: McpServerConfig) -> bool:
        return server.enabled and (self.allow_stdio or str(server.transport) != "stdio")

    # ------------------------------------------------------------------ lifecycle
    async def apply(self, servers: Sequence[McpServerConfig]) -> dict:
        """Make the running set match `servers`: keep unchanged, reconnect changed."""
        async with self._lock:
            self._configs = list(servers)
            wanted = {server.name: server for server in servers if self._allowed(server)}
            signatures = {name: _signature(server) for name, server in wanted.items()}
            for name, managed in list(self._servers.items()):
                if name in signatures and self._signatures.get(name) == signatures[name]:
                    continue
                with contextlib.suppress(Exception):
                    await managed.stop()
                self._servers.pop(name, None)
                self._signatures.pop(name, None)
            to_start = [wanted[name] for name in wanted if name not in self._servers]
            if to_start:
                # Concurrent: one slow handshake must not serialise the other servers.
                await asyncio.gather(*(self._start_one(server) for server in to_start),
                                     return_exceptions=True)
            for name in wanted:
                self._signatures[name] = signatures[name]
        return self.status()

    async def start(self) -> dict:
        """Start whatever was configured at construction time."""
        return await self.apply(self._configs)

    async def stop(self) -> None:
        async with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
            self._signatures.clear()
        if servers:
            # Clean shutdown: child processes (stdio) must not survive the service.
            await asyncio.gather(*(managed.stop() for managed in servers),
                                 return_exceptions=True)

    async def _start_one(self, server: McpServerConfig) -> None:
        managed = ManagedServer(server, on_change=self.on_change)
        self._servers[server.name] = managed
        try:
            ok = await managed.start()
        except Exception as exc:  # noqa: BLE001 - start() itself should not raise
            managed.last_error = _safe_error(exc)
            ok = False
        if not ok:
            # Keep the object: its reconnect loop retries, and the status page says why.
            log.warning("mcp %s: not connected at startup (%s) - will keep retrying",
                        server.name, managed.last_error or "unknown")

    # ------------------------------------------------------------------ tools
    def tool_pairs(self) -> list[tuple[ToolSpec, Any]]:
        """(spec, handler) for every tool of every known server (connected or not).

        A disconnected server keeps its last tool list registered, exactly like the HA
        bridge: calls degrade to an unreachable phrase instead of the model silently
        losing a capability it was told it had.
        """
        pairs: list[tuple[ToolSpec, Any]] = []
        seen_names: set[str] = set()
        seen_llm: set[str] = set()
        for managed in sorted(self._servers.values(), key=lambda item: item.cfg.name):
            for raw in managed.tools:
                name = str(raw.get("name") or "").strip()
                if not name:
                    continue
                spec_name = tool_name(managed.cfg.name, name)
                permission, needs_confirm = _permission(raw)
                spec = ToolSpec(
                    name=spec_name,
                    description=_description(managed.cfg.name, raw),
                    input_schema=raw.get("inputSchema") or raw.get("input_schema") or {},
                    permission=permission,
                    source=f"mcp:{managed.cfg.name}",
                    needs_confirm=needs_confirm,
                    interaction=InteractionSpec(exec={"unreachable": "device.unreachable"}),
                )
                if spec.name in seen_names or spec.llm_name in seen_llm:
                    # Two raw names that slug to the same registry/model name would
                    # overwrite each other in the runner's handler map; keep the first
                    # and say so instead of failing the whole sync.
                    log.warning("mcp %s: tool name collision on %r (%s) - skipped",
                                managed.cfg.name, name, spec.name)
                    continue
                seen_names.add(spec.name)
                seen_llm.add(spec.llm_name)
                pairs.append((spec, _handler_for(managed, name)))
        return pairs

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        servers = [managed.status() for managed in
                   sorted(self._servers.values(), key=lambda item: item.cfg.name)]
        # Disabled servers are configured but deliberately not started: show them too,
        # otherwise the console cannot tell "off" from "missing".
        known = {server["name"] for server in servers}
        for server in self._configs:
            if server.name in known:
                continue
            if not server.enabled:
                error = ""
            elif str(server.transport) == "stdio" and not self.allow_stdio:
                error = "stdio MCP is disabled: set tools.mcp_allow_stdio: true"
            else:
                error = "not started"
            servers.append({
                "name": server.name, "transport": str(server.transport),
                "url": server.url, "command": list(server.command or []),
                "enabled": bool(server.enabled), "connected": False, "tools": 0,
                "tool_names": [], "latency_ms": None, "error": error,
                "last_seen": None,
            })
        servers.sort(key=lambda item: str(item.get("name") or ""))
        return {
            "generic_wired": True,
            "stdio_enabled": self.allow_stdio,
            "enabled": sum(1 for server in servers if server["enabled"]),
            # "connected" counts the servers actually running, not gated ones.
            "connected": sum(1 for server in servers if server["connected"]),
            "tools": sum(int(server["tools"]) for server in servers),
            "servers": servers,
        }

    def status_for(self, name: str) -> dict:
        for server in self.status()["servers"]:
            if server["name"] == name:
                return server
        return {}


def _handler_for(managed: ManagedServer, tool: str) -> Callable[[dict], Any]:
    """A tool handler that converts MCP results into the one ToolResult shape."""

    async def handler(args: dict[str, Any]) -> ToolResult:
        try:
            result = await managed.call(tool, args)
        except MCPUnavailable:
            return ToolResult(status="unreachable").slot(device=managed.cfg.name)
        except Exception as exc:  # noqa: BLE001 - one tool's failure is one phrase
            log.warning("mcp %s.%s failed: %s", managed.cfg.name, tool, _safe_error(exc))
            return ToolResult(status="unreachable").slot(device=managed.cfg.name)
        if bool(getattr(result, "isError", False)):
            # The server marked this as an error: say "did not execute", never let a
            # tool error read as a successful fact.
            return ToolResult(status="unreachable").slot(device=managed.cfg.name)
        return ToolResult.say(_result_text(result))

    return handler


async def test_server(server: McpServerConfig,
                      timeout_s: float = HANDSHAKE_TIMEOUT_S,
                      *,
                      allow_stdio: bool = False) -> dict:
    """Connect one throwaway server, list its tools, disconnect. Nothing is saved.

    A stdio server is never spawned while `tools.mcp_allow_stdio` is off: the config is
    validated by the caller and the response says why nothing was attempted.
    """
    base = {"name": server.name, "transport": str(server.transport)}
    if str(server.transport) == "stdio" and not allow_stdio:
        return {
            **base, "ok": False, "attempted": False, "connected": False,
            "tools": 0, "tool_names": [], "latency_ms": None,
            "error": "stdio MCP is disabled: set tools.mcp_allow_stdio: true",
        }
    started = time.monotonic()
    managed = ManagedServer(server)
    try:
        ok = await managed.start(timeout_s)
        elapsed = round((time.monotonic() - started) * 1000)
        connected = bool(ok and managed.connected)
        return {
            "ok": connected,
            "attempted": True,
            "connected": connected,
            "transport": str(server.transport),
            "name": server.name,
            "tools": len(managed.tools),
            "tool_names": [str(tool.get("name") or "") for tool in managed.tools][
                :MAX_TOOL_NAMES_IN_STATUS],
            "latency_ms": round(managed.latency_ms, 1)
            if managed.latency_ms is not None else elapsed,
            "error": "" if connected else (managed.last_error or "handshake timed out"),
        }
    except Exception as exc:  # noqa: BLE001 - the endpoint reports, it does not raise
        return {
            "ok": False, "attempted": True, "connected": False,
            "transport": str(server.transport), "name": server.name,
            "tools": 0, "tool_names": [],
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": _safe_error(exc),
        }
    finally:
        with contextlib.suppress(Exception):
            await managed.stop()


__all__ = [
    "HANDSHAKE_TIMEOUT_S",
    "ManagedServer",
    "MCPManager",
    "MCPUnavailable",
    "open_session",
    "test_server",
    "tool_name",
]
