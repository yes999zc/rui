"""rui#28: the WeChat channel must survive one bad message and cap voice payloads."""
from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest

from rui.channels import weixin
from rui.channels.weixin import (
    MAX_VOICE_BYTES,
    WeixinAdapter,
    _encrypt,
)


@pytest.fixture()
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("RUI_WEIXIN_TEST", "1")
    inst = WeixinAdapter(tmp_path)
    inst.account = {"token": "t"}
    return inst


def test_cursor_is_saved_even_when_a_message_raises(adapter, monkeypatch) -> None:
    calls = 0

    async def fake_post(path, payload, *, timeout=45.0):
        nonlocal calls
        calls += 1
        if calls == 1:
            adapter._stop.set()  # make the retry backoff return immediately
            return {
                "msgs": [{"message_id": "m1", "from_user_id": "u1", "message_type": 1}],
                "get_updates_buf": "cursor-2",
            }
        return {}

    async def boom(msg):
        raise RuntimeError("bad message")

    monkeypatch.setattr(adapter, "_post", fake_post)
    monkeypatch.setattr(adapter, "_handle_message", boom)
    asyncio.run(adapter._poll())
    assert adapter.store.load_sync() == "cursor-2"


def test_oversized_voice_response_is_dropped(adapter, caplog) -> None:
    body = b"x" * (MAX_VOICE_BYTES + 1)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-length": str(len(body))})

    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    voice = {
        "media": {
            "encrypt_query_param": "p",
            "aes_key": base64.b64encode(b"0" * 16).decode(),
        }
    }
    with caplog.at_level("WARNING"):
        assert asyncio.run(adapter._download_voice(voice)) is None
    assert "too large" in caplog.text


def test_decode_runs_off_the_event_loop(adapter, monkeypatch) -> None:
    key = b"1" * 16
    encrypted = _encrypt(b"fake-silk-bytes", key)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=encrypted)

    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def slow_decode(plain: bytes) -> bytes:
        time.sleep(0.2)
        return b"pcm"

    monkeypatch.setattr(weixin, "_decode_voice_sync", slow_decode)
    voice = {
        "media": {
            "encrypt_query_param": "p",
            "aes_key": base64.b64encode(key).decode(),
        }
    }

    async def scenario() -> int:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        tick = asyncio.create_task(ticker())
        try:
            assert await adapter._download_voice(voice) == b"pcm"
        finally:
            tick.cancel()
            with pytest.raises(asyncio.CancelledError):
                await tick
        return ticks

    assert asyncio.run(scenario()) >= 5
