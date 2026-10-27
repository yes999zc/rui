"""Reconnect-failure logging: one INFO per state change, not one per attempt.

`mcp_client.MCPClient` (the HA bridge) and `mcp_manager.ManagedServer` (generic
`tools.mcp_servers` entries) are two separate reconnect loops, not two copies of one
log line: a server runs one of them, never both. Both used to emit
`mcp <name>: down (...)` on every retry (~15 s apart), so an unreachable Home
Assistant filled the log. These tests pin the replacement rule: the first failure,
a changed reason and the recovery stay INFO; identical repeats are DEBUG; and the
rule is wired into both loops, not only into the helper.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace

import pytest

from rui.config.schema import McpServerConfig
from rui.tools import mcp_client as client_module
from rui.tools import mcp_manager as manager_module
from rui.tools.mcp_client import DOWN_LOG_INTERVAL_S, DownLogThrottle, MCPClient
from rui.tools.mcp_manager import ManagedServer

DOWN = ": down ("


# ------------------------------------------------------------------- helpers

class _Clock:
    """Injectable time source: crossing the throttle interval costs nothing."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _StopLoop(Exception):
    """Ends a reconnect loop under test once it has produced enough attempts."""


def _cfg(name: str = "ha") -> McpServerConfig:
    return McpServerConfig.model_validate({
        "name": name, "transport": "http", "enabled": True,
        "url": "http://127.0.0.1:9/mcp",
    })


def _down_infos(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records
            if record.levelno == logging.INFO and DOWN in record.getMessage()]


def _asyncio_shim(sleep) -> object:  # noqa: ANN001
    """Module-local stand-in: only this module's `asyncio.sleep` is replaced."""

    class _Shim:
        def sleep(self, seconds: float):  # noqa: ANN201
            return sleep(seconds)

        def __getattr__(self, name: str) -> object:
            return getattr(asyncio, name)

    return _Shim()


# ------------------------------------------------------------- the throttle

def test_twelve_identical_failures_keep_a_single_info_line(caplog) -> None:
    """The noise case: same server, same reason, every 15 s for three minutes."""
    clock = _Clock()
    throttle = DownLogThrottle("ha", clock=clock)

    with caplog.at_level(logging.DEBUG):
        for _ in range(12):
            throttle.record_down("ConnectError: refused", 1.0)
            clock.advance(15.0)

    infos = _down_infos(caplog)
    # 12 attempts inside one five-minute window: the first is INFO, the rest DEBUG.
    assert len(infos) == 1
    message = infos[0].getMessage()
    assert "ha" in message
    assert "ConnectError: refused" in message
    assert "reconnect in 1s" in message
    # Demoted, not dropped: debug logging still shows every attempt.
    still_down = [record for record in caplog.records
                  if record.levelno == logging.DEBUG and "still down" in record.getMessage()]
    assert len(still_down) == 11


def test_an_unchanged_reason_is_info_again_after_five_minutes(caplog) -> None:
    assert DOWN_LOG_INTERVAL_S == 300.0  # documented default cadence
    clock = _Clock()
    throttle = DownLogThrottle("ha", clock=clock)

    with caplog.at_level(logging.DEBUG):
        throttle.record_down("ConnectError: refused", 15.0)
        clock.advance(299.0)
        throttle.record_down("ConnectError: refused", 15.0)
        assert len(_down_infos(caplog)) == 1
        clock.advance(1.0)
        throttle.record_down("ConnectError: refused", 15.0)

    infos = _down_infos(caplog)
    assert len(infos) == 2
    assert infos[0].getMessage() == infos[1].getMessage()


def test_a_changed_reason_is_info_immediately(caplog) -> None:
    """A new error must never be hidden behind the throttle for the old one."""
    clock = _Clock()
    throttle = DownLogThrottle("ha", clock=clock)

    with caplog.at_level(logging.DEBUG):
        for _ in range(5):
            throttle.record_down("TimeoutError: handshake timed out", 1.0)
            clock.advance(15.0)
        for _ in range(5):
            throttle.record_down("ConnectError: refused", 1.0)
            clock.advance(15.0)

    infos = _down_infos(caplog)
    assert len(infos) == 2
    assert "TimeoutError: handshake timed out" in infos[0].getMessage()
    assert "ConnectError: refused" in infos[1].getMessage()


def test_a_recovery_rearms_the_info_streak(caplog) -> None:
    """`reset()` is what the loops call on a successful connect."""
    clock = _Clock()
    throttle = DownLogThrottle("ha", clock=clock)

    with caplog.at_level(logging.DEBUG):
        throttle.record_down("ConnectError: refused", 1.0)
        throttle.record_down("ConnectError: refused", 2.0)
        throttle.reset()          # connected
        throttle.record_down("ConnectError: refused", 1.0)
        throttle.record_down("ConnectError: refused", 2.0)

    # One INFO per outage, not one for the whole process lifetime.
    assert len(_down_infos(caplog)) == 2


# ------------------------------------------------- wired into both real loops

def test_mcp_client_loop_throttles_the_down_line(caplog, monkeypatch) -> None:
    """The HA bridge's loop (the production noise source) really uses the throttle."""
    attempts = 0
    real_sleep = asyncio.sleep

    class FakeHttp:
        async def aclose(self) -> None:
            pass

    @asynccontextmanager
    async def failing_transport(url, http_client=None):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        raise ConnectionError("refused")
        yield  # pragma: no cover - unreachable, marks this as a generator

    async def fake_sleep(seconds: float) -> None:
        await real_sleep(0)
        if attempts >= 12:
            raise _StopLoop

    monkeypatch.setattr(client_module, "guarded_async_client",
                        lambda *args, **kwargs: FakeHttp())
    monkeypatch.setattr(client_module, "asyncio", _asyncio_shim(fake_sleep))
    # `_run` imports the transport inside the loop, so patching the module works.
    import mcp.client.streamable_http as streamable_http
    monkeypatch.setattr(streamable_http, "streamable_http_client", failing_transport)

    client = MCPClient(_cfg("ha"))
    client._down_log = DownLogThrottle("ha", clock=_Clock())

    async def run() -> None:
        with pytest.raises(_StopLoop):
            await client._run()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())

    assert attempts == 12
    assert "refused" in client.last_error
    infos = _down_infos(caplog)
    assert len(infos) == 1
    assert "ha" in infos[0].getMessage()
    assert any(record.levelno == logging.DEBUG and "still down" in record.getMessage()
               for record in caplog.records)


def test_managed_server_loop_throttles_the_down_line(caplog, monkeypatch) -> None:
    """Same rule for generic `tools.mcp_servers` entries."""
    attempts = 0
    real_sleep = asyncio.sleep

    @asynccontextmanager
    async def failing_opener(server_cfg, policy=None):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        raise ConnectionError("refused")
        yield  # pragma: no cover

    async def fake_sleep(seconds: float) -> None:
        await real_sleep(0)
        if attempts >= 12:
            raise _StopLoop

    monkeypatch.setattr(manager_module, "open_session", failing_opener)
    monkeypatch.setattr(manager_module, "asyncio", _asyncio_shim(fake_sleep))

    server = ManagedServer(_cfg("notes"))
    server._down_log = DownLogThrottle("notes", clock=_Clock(), logger=manager_module.log)

    async def run() -> None:
        with pytest.raises(_StopLoop):
            await server._run()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())

    assert attempts == 12
    infos = _down_infos(caplog)
    assert len(infos) == 1
    assert "notes" in infos[0].getMessage()
    # Attribution: production constructs this throttle with the manager logger. Without
    # this line the test would still pass if `logger=manager_module.log` were dropped,
    # leaving the "records belong to rui.tools.mcp_manager" fix unguarded.
    assert infos[0].name == "rui.tools.mcp_manager", (
        f"the down line came from {infos[0].name!r}, not the manager logger")


def test_a_successful_reconnect_is_one_info_line_not_a_burst(caplog, monkeypatch) -> None:
    """Recovery: the existing `connected` line, exactly once, and only after the outage."""
    opens = 0
    real_sleep = asyncio.sleep

    class FakeSession:
        async def initialize(self) -> None:
            pass

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[])

    @asynccontextmanager
    async def flaky_opener(server_cfg, policy=None):  # noqa: ANN001
        nonlocal opens
        opens += 1
        if opens <= 2:
            raise ConnectionError("refused")
        yield FakeSession()

    async def fake_sleep(seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(manager_module, "open_session", flaky_opener)
    monkeypatch.setattr(manager_module, "asyncio", _asyncio_shim(fake_sleep))

    server = ManagedServer(_cfg("notes"))
    server._down_log = DownLogThrottle("notes", clock=_Clock(), logger=manager_module.log)

    async def run() -> None:
        task = asyncio.create_task(server._run())
        try:
            for _ in range(200):
                if server.connected:
                    break
                await real_sleep(0)
            # Give the loop a few more turns: nothing about recovery may repeat.
            for _ in range(5):
                await real_sleep(0)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())

    assert server.connected is True
    assert opens == 3
    downs = _down_infos(caplog)
    assert len(downs) == 1   # one line for the outage, repeats are DEBUG
    assert downs[0].name == "rui.tools.mcp_manager", (
        f"the down line came from {downs[0].name!r}, not the manager logger")
    connected = [record for record in caplog.records
                 if record.levelno == logging.INFO and "connected" in record.getMessage()]
    assert len(connected) == 1
