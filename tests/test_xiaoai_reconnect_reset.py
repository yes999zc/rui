"""rui#25: replacing the speaker socket must reset the player generation."""
from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from types import SimpleNamespace

from xiaoai_plug.adapter import XiaoaiAdapter

from rui.config.schema import CaptureConfig, DeviceConfig, VoiceConfig

TOKEN_ENV = "RUI_TEST_DEVICE_TOKEN"
_STOP = object()


def _adapter() -> XiaoaiAdapter:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return XiaoaiAdapter(
        DeviceConfig(id="living", adapter="xiaoai", host="192.0.2.10",
                     listen_port=port, token_env=TOKEN_ENV),
        CaptureConfig(),
        VoiceConfig(),
    )


class FakeConn:
    """Enough of a ServerConnection for _handle()/play(); never opens a socket."""

    def __init__(self, adapter: XiaoaiAdapter) -> None:
        self.adapter = adapter
        self.sent: list[str] = []
        self.remote_address = ("192.0.2.10", 50000)
        self.closed = False
        self._inbox: asyncio.Queue[object] = asyncio.Queue()
        # _token_ok() reads the query string; a real client dials /?token=...
        self.request = SimpleNamespace(path=f"/?token={adapter.token}")

    async def send(self, raw: str) -> None:
        self.sent.append(raw)
        try:
            request = json.loads(raw).get("Request") or {}
        except (TypeError, ValueError):
            return
        future = self.adapter._pending.get(str(request.get("id")))
        if future is not None and not future.done():
            future.set_result({"Response": {"id": request.get("id"), "ok": True}})

    async def close(self, **_kwargs) -> None:
        self.closed = True

    def release(self) -> None:
        self._inbox.put_nowait(_STOP)

    def push(self, message: str) -> None:
        self._inbox.put_nowait(message)

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self):
        message = await self._inbox.get()
        if message is _STOP:
            raise StopAsyncIteration
        return message


async def _wait_for_conn(adapter: XiaoaiAdapter, conn: FakeConn) -> None:
    for _ in range(50):
        if adapter._conn is conn:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("connection was not installed")


def test_replacing_a_mid_play_connection_resets_the_player(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    adapter = _adapter()
    old, new = FakeConn(adapter), FakeConn(adapter)
    adapter._conn = old
    adapter._playing = True
    adapter.consumed = 9
    adapter.sink_generation = 3

    async def scenario() -> None:
        task = asyncio.create_task(adapter._handle(new))
        await _wait_for_conn(adapter, new)
        assert adapter._playing is False
        assert adapter.consumed == 0
        assert adapter.sink_generation == 4
        await adapter.play(b"\x00\x00" * 1600, sample_rate=16000, channels=1)
        assert any("start_play" in raw for raw in new.sent)
        new.release()
        await task

    asyncio.run(scenario())


def test_old_handler_finally_does_not_clear_the_new_connection(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    adapter = _adapter()
    old, new = FakeConn(adapter), FakeConn(adapter)

    async def scenario() -> None:
        old_task = asyncio.create_task(adapter._handle(old))
        await _wait_for_conn(adapter, old)
        new_task = asyncio.create_task(adapter._handle(new))
        await _wait_for_conn(adapter, new)
        old.release()
        await old_task
        assert adapter._conn is new
        new.release()
        await new_task

    asyncio.run(scenario())


def test_a_play_suspended_while_replaced_does_not_resurrect_the_old_player(
    monkeypatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    adapter = _adapter()
    old, new = FakeConn(adapter), FakeConn(adapter)
    adapter._conn = old
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_call(command, payload=None, timeout=3.0):
        if command == "start_play":
            started.set()
            await release.wait()
            return {}
        return {}

    async def scenario() -> None:
        adapter.call = slow_call  # type: ignore[method-assign]
        task = asyncio.create_task(
            adapter.play(b"\x00\x00" * 1600, sample_rate=16000, channels=1)
        )
        await started.wait()
        adapter._conn = new
        adapter._reset_media_state()
        release.set()
        await task
        del adapter.call
        assert adapter._playing is False
        assert adapter.sink_generation == 1
        await adapter.play(b"\x00\x00" * 1600, sample_rate=16000, channels=1)
        assert any("start_play" in raw for raw in new.sent)

    asyncio.run(scenario())


def test_a_replaced_socket_cannot_advance_the_new_player(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    adapter = _adapter()
    old, new = FakeConn(adapter), FakeConn(adapter)

    async def scenario() -> None:
        old_task = asyncio.create_task(adapter._handle(old))
        await _wait_for_conn(adapter, old)
        new_task = asyncio.create_task(adapter._handle(new))
        await _wait_for_conn(adapter, new)
        old.push(json.dumps({"Event": {"event": "consumed", "data": {"n": 5}}}))
        await asyncio.sleep(0.05)
        assert adapter.consumed == 0
        new.release()
        await new_task
        old.release()
        await old_task

    asyncio.run(scenario())
