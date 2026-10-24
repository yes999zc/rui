"""Volcano (豆包) large-model streaming ASR 2.0 over its WebSocket endpoint.

The pipeline hands us one finished utterance (16 kHz mono s16), so this provider opens the
bidirectional stream, sends the audio in 200 ms packets (the vendor's recommended size),
sends the negative/last packet, and returns the final transcript. Measured 2026-10-23 with
a 3.6 s sentence: incremental text correct from the first 200 ms, final text exact.

Protocol (documented): binary frames = 4-byte header, optional 4-byte sequence, 4-byte
payload size (big endian), JSON payload. Message types: 0x1 full client request, 0x2
audio-only, 0x9 full server response, 0xf error. Header flags: bit0 = sequence present,
bit1 = last packet.

Auth is the new-console speech API key (`X-Api-Key`); the resource id selects the product
(`volc.seedasr.sauc.duration` = Doubao streaming ASR 2.0 hourly, `.concurrent` = concurrency
version, `volc.bigasr.sauc.*` = 1.0). Resolution goes through `secrets.get` so the console's
Secrets page works.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import uuid
from collections.abc import AsyncIterator  # noqa: F401 - mirrors the other providers

from .. import secrets
from ..config.schema import ProviderConfig
from .openai_compat import MissingKey, ProviderError

log = logging.getLogger(__name__)

ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
DEFAULT_RESOURCE = "volc.seedasr.sauc.duration"
PACKET_BYTES = 6400  # 200 ms of 16 kHz s16 mono, the size the vendor recommends


def header(msg_type: int, flags: int, payload: bytes, *, serialization: int = 1,
           compression: int = 0) -> bytes:
    return (bytes([0x11, (msg_type << 4) | flags, (serialization << 4) | compression, 0x00])
            + struct.pack(">I", len(payload)) + payload)


def parse_response(raw: bytes) -> tuple[int, int, bytes]:
    """(message type, flags, payload) for one server frame."""
    msg_type = raw[1] >> 4
    flags = raw[1] & 0x0F
    compression = raw[2] & 0x0F
    offset = 4
    if flags & 0x1:
        offset += 4  # sequence number, not needed here
    size = struct.unpack(">I", raw[offset:offset + 4])[0]
    offset += 4
    payload = raw[offset:offset + size]
    if compression == 1:
        import gzip

        payload = gzip.decompress(payload)
    return msg_type, flags, payload


class VolcAsr:
    def __init__(self, cfg: ProviderConfig, sample_rate: int = 16000) -> None:
        self.cfg = cfg
        self.sample_rate = sample_rate

    def _api_key(self) -> str:
        name = self.cfg.api_key_env
        key = secrets.get(name) if name else ""
        if not key:
            raise MissingKey(f"no value for {name or '(unset)'} (environment or secrets.yaml)")
        return key

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000):
        import websockets

        from .base import Transcript

        if not pcm:
            return Transcript(text="", duration_ms=0)
        if sample_rate != 16000:
            raise ProviderError(
                f"volc streaming asr needs 16 kHz audio, got {sample_rate} Hz")
        try:
            return await self._transcribe(pcm, websockets)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - the chain falls back to local ASR
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

    async def _transcribe(self, pcm: bytes, websockets):
        from .base import Transcript

        url = self.cfg.base_url or ASR_URL
        headers = {
            "X-Api-Key": self._api_key(),
            "X-Api-Resource-Id": self.cfg.model or DEFAULT_RESOURCE,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        timeout = max(3.0, float(self.cfg.timeout_s))
        async with websockets.connect(url, additional_headers=headers, max_size=None,
                                      open_timeout=timeout) as ws:
            config = {
                "user": {"uid": "rui"},
                "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1,
                          "codec": "raw"},
                "request": {"model_name": "bigmodel", "enable_itn": True,
                            "enable_punc": True, "show_utterances": True,
                            "result_type": "full"},
            }
            await ws.send(header(0x1, 0x0, json.dumps(config).encode()))
            # The first response is the session acknowledgement; the audio can follow
            # immediately, so both are sent before reading.
            for start in range(0, len(pcm), PACKET_BYTES):
                chunk = pcm[start:start + PACKET_BYTES]
                last = start + PACKET_BYTES >= len(pcm)
                await ws.send(header(0x2, 0x2 if last else 0x0, chunk,
                                     serialization=0))
            text = ""
            duration_ms = len(pcm) // 2 * 1000 // 16000
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except TimeoutError:
                    break
                except Exception as exc:  # noqa: BLE001
                    # ConnectionClosedOK is the normal end after the last packet.
                    if type(exc).__name__ in ("ConnectionClosedOK", "ConnectionClosed"):
                        break
                    raise
                if isinstance(raw, str):
                    continue
                msg_type, _flags, payload = parse_response(raw)
                if msg_type == 0xF:
                    raise ProviderError(f"volc asr error: {payload[:200]!r}")
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                result = obj.get("result") or {}
                if result.get("text"):
                    text = result["text"]
                audio_info = obj.get("audio_info") or {}
                if audio_info.get("duration"):
                    duration_ms = int(audio_info["duration"])
            return Transcript(text=text.strip(), duration_ms=duration_ms)
