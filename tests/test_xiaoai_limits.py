"""P1-8: the inbound LX06 websocket is bounded (frame, record and text sizes)."""
from __future__ import annotations

import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from rui.config.schema import CaptureConfig, DeviceConfig, VoiceConfig
from rui.device.xiaoai import protocol
from rui.device.xiaoai.adapter import (
    MAX_TEXT_CHARS,
    MAX_WS_MESSAGE_BYTES,
    XiaoaiAdapter,
)

TOKEN_ENV = "RUI_TEST_DEVICE_TOKEN"
TOKEN = "test-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_adapter(port: int) -> XiaoaiAdapter:
    dev = DeviceConfig(
        id="living", adapter="xiaoai", host="192.0.2.10", listen_port=port,
        token_env=TOKEN_ENV, allow_no_token=False,
    )
    return XiaoaiAdapter(dev, CaptureConfig(rate=48000), VoiceConfig())


@pytest.fixture()
def device_token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return TOKEN


def record_frame(adapter: XiaoaiAdapter) -> bytes:
    expected = adapter._max_record_bytes
    return protocol.stream("record", b"\x01\x00" * (expected // 2))


def test_an_oversized_websocket_frame_is_rejected_and_the_server_survives(
    device_token,
) -> None:
    async def run() -> None:
        port = free_port()
        adapter = make_adapter(port)
        await adapter.open()
        url = f"ws://127.0.0.1:{port}/?token={TOKEN}"
        try:
            async with connect(url, max_size=None) as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                assert first["Request"]["command"] == "start_recording"
                try:
                    await ws.send("x" * (MAX_WS_MESSAGE_BYTES + 1))
                    await asyncio.wait_for(ws.recv(), timeout=2)
                except (ConnectionClosed, OSError):
                    pass  # 1009 is the expected outcome

            # The adapter is still listening and a normal client still works.
            async with connect(url, max_size=None) as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                assert first["Request"]["command"] == "start_recording"
                await ws.send(record_frame(adapter))
                await asyncio.sleep(0.1)
            assert adapter.health()["frames_received"] == 1
        finally:
            await adapter.close()

    asyncio.run(run())


def test_an_oversized_record_payload_is_counted_not_queued(device_token) -> None:
    async def run() -> None:
        port = free_port()
        adapter = make_adapter(port)
        await adapter.open()
        try:
            async with connect(
                f"ws://127.0.0.1:{port}/?token={TOKEN}", max_size=None
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=2)  # start_recording
                too_big = b"\x00" * (adapter._max_record_bytes * 3)
                await ws.send(protocol.stream("record", too_big))
                await asyncio.sleep(0.1)
                assert adapter.health()["bad_frames"] == 1
                assert adapter.health()["frames_received"] == 0

                await ws.send(record_frame(adapter))
                await asyncio.sleep(0.1)
                assert adapter.health()["frames_received"] == 1
        finally:
            await adapter.close()

    asyncio.run(run())


def test_oversized_and_malformed_text_never_drops_the_connection(device_token) -> None:
    async def run() -> None:
        port = free_port()
        adapter = make_adapter(port)
        await adapter.open()
        try:
            async with connect(
                f"ws://127.0.0.1:{port}/?token={TOKEN}", max_size=None
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=2)  # start_recording
                huge = json.dumps(
                    {"Event": {"event": "instruction",
                               "data": "x" * (MAX_TEXT_CHARS + 10)}}
                )
                await ws.send(huge)
                # Malformed shapes used to raise out of `_on_text` and end the read loop.
                await ws.send(json.dumps(
                    {"Event": {"event": "instruction",
                               "data": {"header": "not-a-mapping",
                                        "payload": {"results": {"x": 1}}}}}
                ))
                await ws.send(json.dumps(
                    {"Event": {"event": "consumed", "data": {"n": 1}}}
                ))
                await ws.send(record_frame(adapter))
                await asyncio.sleep(0.1)

                assert adapter.health()["bad_text"] >= 1
                assert adapter.health()["frames_received"] == 1, (
                    "a bad text frame must not close the socket"
                )
                assert ws.close_code is None
        finally:
            await adapter.close()

    asyncio.run(run())
