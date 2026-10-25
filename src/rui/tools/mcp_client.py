"""The MCP client: one background connection per server, callers never see transport.

The connection pattern is the old build's (`xiaoai-voice-entry/server/ha.py`),
which ran this exact shape against Home Assistant for weeks: a single long-lived
session inside its own task, every caller queues a request and awaits a future,
and any transport death surfaces as one typed error (`MCPUnavailable`) that the
tool layer turns into a degrade phrase - never a stack trace, never a hang.

Reconnect is exponential up to 15 s; a dead server degrades its tools, the rest
of the assistant (voice, music, local tools) is untouched.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from ..config.schema import McpServerConfig
from ..security.netguard import OutboundPolicy, guarded_async_client

log = logging.getLogger(__name__)

RECONNECT_MAX_S = 15.0


def _describe(exc: BaseException) -> str:
    """A message an operator can act on for a transport failure.

    The MCP SDK runs the handshake inside an anyio task group, so a connection
    refusal or a 401 arrives as ``ExceptionGroup("unhandled errors in a TaskGroup
    (1 sub-exception)", [...])``. That string names neither the status nor the
    host; unwrap the leaf and keep its type and message.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class MCPUnavailable(RuntimeError):
    """The server could not answer: the tool layer degrades, never raises."""


class MCPClient:
    """One configured MCP server. `start()` connects; `call()` queues."""

    def __init__(self, cfg: McpServerConfig, headers: dict[str, str] | None = None,
                 policy: OutboundPolicy | None = None) -> None:
        self.cfg = cfg
        self._headers = headers or {}
        # None still means "guard with the schema defaults"; a caller that needs an
        # explicit policy (the app, from `security.outbound`) passes one.
        self.policy = policy or OutboundPolicy()
        self._tools: list[dict] = []
        self._queue: asyncio.Queue[tuple[dict, asyncio.Future]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self.last_error = ""
        self.latency_ms: float | None = None

    # ------------------------------------------------------------------ session
    async def start(self) -> bool:
        """Spawn the session task and wait for the first handshake (or fail fast)."""
        if self._task is not None:
            return True
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.cfg.name}")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
            return True
        except TimeoutError:
            self.last_error = "handshake timed out"
            return False

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._connected.clear()

    @property
    def tools(self) -> list[dict]:
        """The server's tool list, as the registry's from_mcp expects it."""
        return list(self._tools)

    # ------------------------------------------------------------------ calls
    async def call(self, tool: str, args: dict[str, Any] | None = None,
                   timeout_s: float = 8.0) -> Any:
        """One tool call through the shared session; MCPUnavailable on any failure."""
        if self._task is None:
            raise MCPUnavailable(f"{self.cfg.name}: not started")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(({"tool": tool, "args": args or {}}, future))
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            raise MCPUnavailable(f"{self.cfg.name}.{tool}: timed out") from exc

    # ------------------------------------------------------------------ loop
    async def _run(self) -> None:
        """The session task: connect, serve queued calls, reconnect on death."""
        backoff = 1.0
        while True:
            try:
                from mcp import ClientSession
                from mcp.client.streamable_http import streamable_http_client

                # The new SDK takes a configured httpx client, not headers. The guard
                # event hook runs on the MCP handshake and on every tool request.
                http = guarded_async_client(self.policy, headers=self._headers, timeout=30)
                try:
                    async with streamable_http_client(self.cfg.url, http_client=http) as streams:
                        read, write = streams[0], streams[1]
                        async with ClientSession(read, write) as s:
                            await s.initialize()
                            listed = await s.list_tools()
                            self._tools = [t.model_dump(exclude_none=True) for t in listed.tools]
                            self._connected.set()
                            backoff = 1.0
                            self.latency_ms = None
                            log.info("mcp %s: connected, %d tools", self.cfg.name, len(self._tools))
                            while True:
                                request, future = await self._queue.get()
                                if future.done():
                                    continue
                                t0 = time.monotonic()
                                try:
                                    result = await s.call_tool(request["tool"],
                                                               request["args"])
                                    self.latency_ms = (time.monotonic() - t0) * 1000
                                    if not future.done():
                                        future.set_result(result)
                                except Exception as exc:  # noqa: BLE001 - one call's death
                                    self.last_error = str(exc)
                                    if not future.done():
                                        future.set_exception(MCPUnavailable(
                                            f"{self.cfg.name}.{request['tool']}: {exc}"))
                finally:
                    with contextlib.suppress(Exception):
                        await http.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the session died: drain, wait, retry
                detail = _describe(exc)
                self.last_error = detail
                self._connected.clear()
                log.info("mcp %s: down (%s), reconnect in %.0fs",
                         self.cfg.name, detail, backoff)
                self._drain_queue()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_S)

    def _drain_queue(self) -> None:
        """Nobody is listening: fail the waiters now, not at their timeouts."""
        while not self._queue.empty():
            request, future = self._queue.get_nowait()
            if not future.done():
                future.set_exception(
                    MCPUnavailable(f"{self.cfg.name}: connection down"))
