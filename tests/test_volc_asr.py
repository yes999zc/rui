"""Volcano streaming ASR 2.0: wire format and a full transcribe against a fake socket."""
from __future__ import annotations

import asyncio
import json
import struct

import pytest

from house_ear.config import Config
from house_ear.config.schema import ProviderConfig
from house_ear.providers import volc_asr
from house_ear.providers.chain import build_stage


def test_header_layout() -> None:
    frame = volc_asr.header(0x1, 0x0, b"abc")
    assert frame == b"\x11\x10\x10\x00" + struct.pack(">I", 3) + b"abc"
    last = volc_asr.header(0x2, 0x2, b"\x00\x00", serialization=0)
    assert last[1] == 0x22 and last[2] == 0x00


def test_parse_response_skips_the_sequence_number() -> None:
    payload = json.dumps({"result": {"text": "你好"}}).encode()
    raw = bytes([0x11, 0x91, 0x10, 0x00]) + struct.pack(">I", 7) + struct.pack(">I", len(payload)) + payload
    msg_type, flags, body = volc_asr.parse_response(raw)
    assert msg_type == 9 and flags == 1
    assert json.loads(body)["result"]["text"] == "你好"


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        payload = json.dumps({"result": {"text": "你好小瑞"}, "audio_info": {"duration": 1200}}).encode()
        self.frames = [
            bytes([0x11, 0x91, 0x10, 0x00]) + struct.pack(">I", 1) + struct.pack(">I", len(payload)) + payload,
        ]
        self.index = 0

    async def send(self, frame: bytes) -> None:
        self.sent.append(frame)

    async def recv(self):
        if self.index >= len(self.frames):
            raise TimeoutError
        frame = self.frames[self.index]
        self.index += 1
        return frame


class FakeConnect:
    def __init__(self, ws: FakeWs) -> None:
        self.ws = ws

    async def __aenter__(self) -> FakeWs:
        return self.ws

    async def __aexit__(self, *exc) -> bool:
        return False


def _cfg(**kw) -> ProviderConfig:
    base = dict(kind="volc_asr", api_key_env="VOLC_API_KEY",
                model="volc.seedasr.sauc.duration", timeout_s=5)
    base.update(kw)
    return ProviderConfig(**base)


def test_transcribe_sends_packets_and_returns_the_text(monkeypatch) -> None:
    ws = FakeWs()
    monkeypatch.setattr("house_ear.providers.volc_asr.secrets.get", lambda name: "test-key")
    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *a, **kw: FakeConnect(ws))
    pcm = b"\x00\x00" * 16000  # 1 s
    transcript = asyncio.run(volc_asr.VolcAsr(_cfg(), 16000).transcribe(pcm, 16000))
    assert transcript.text == "你好小瑞"
    assert transcript.duration_ms == 1200
    # one full client request + ceil(32000/6400) = 5 audio packets
    assert len(ws.sent) == 6
    assert ws.sent[0][1] == 0x10  # full client request, JSON
    assert ws.sent[-1][1] == 0x22  # last audio packet


def test_a_bad_sample_rate_is_a_clear_error() -> None:
    from house_ear.providers.openai_compat import ProviderError

    with pytest.raises(ProviderError):
        asyncio.run(volc_asr.VolcAsr(_cfg(), 16000).transcribe(b"\x00\x00" * 100, 8000))


def test_chain_builds_the_provider() -> None:
    cfg = Config()
    cfg.providers.catalog["volc-asr"] = _cfg()
    cfg.providers.asr.chain = ["volc-asr"]
    chain = build_stage(cfg.providers, "asr")
    assert isinstance(chain[0][1], volc_asr.VolcAsr)
