"""Wire protocol spoken by the LX06's own client (the tier-2 seam).

Everything here is *observed* from the client the speaker actually runs, not invented:

  server -> device, TEXT frame, one command:
      {"Request": {"id": "<uuid>", "command": "start_recording", "payload": {...}}}

  device -> server, TEXT frame:
      {"Response": {"id": "<uuid>", "code": 0, "message": "..."}}
      {"Event": {"id": "<uuid>", "event": "key_event", "data": {...}}}

  audio, either direction, BINARY frame carrying JSON:
      {"id": "<uuid>", "tag": "record" | "play", "bytes": [...] | "<base64>", "data": null}

`record` payload is int16 little-endian, interleaved, `channels` per sample, already
shifted per channel by the client (`mic_shift` for ch0..n-1, `ref_shift` for the
reference channels) - the device does the S32 -> S16 conversion, we never see S32.

The client serialises raw bytes as a JSON array of integers. The fork we run also
accepts base64 for `play`; `decode_stream` accepts both so a stock client and the
fork both work.
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from typing import Any


def request(command: str, payload: dict[str, Any] | None = None) -> str:
    """One TEXT control frame."""
    return json.dumps(
        {"Request": {"id": str(uuid.uuid4()), "command": command, "payload": payload}},
        ensure_ascii=False,
    )


def stream(tag: str, pcm: bytes, *, base64_bytes: bool = False) -> bytes:
    """One BINARY audio frame. Default matches the client: a JSON array of bytes."""
    payload: Any = base64.b64encode(pcm).decode("ascii") if base64_bytes else list(pcm)
    return json.dumps(
        {"id": str(uuid.uuid4()), "tag": tag, "bytes": payload, "data": None},
        separators=(",", ":"),
    ).encode("utf-8")


class StreamError(ValueError):
    pass


def decode_stream(message: bytes | str) -> tuple[str, bytes]:
    """(tag, raw bytes) from a stream frame; raises StreamError on anything else."""
    try:
        body = json.loads(message)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StreamError("stream frame is not JSON") from exc
    if not isinstance(body, dict):
        raise StreamError("stream frame is not an object")
    tag = body.get("tag")
    if not isinstance(tag, str) or not tag:
        raise StreamError("stream frame has no tag")
    raw = body.get("bytes")
    if isinstance(raw, str):
        try:
            return tag, base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise StreamError("stream frame bytes are not base64") from exc
    if isinstance(raw, list):
        try:
            return tag, bytes(bytearray(raw))
        except (TypeError, ValueError) as exc:
            raise StreamError("stream frame bytes are not byte values") from exc
    raise StreamError(f"stream frame bytes have an unsupported type: {type(raw).__name__}")


def start_recording_payload(
    *,
    rate: int,
    channels: int,
    frame_ms: int,
    mic_shift: int,
    ref_shift: int,
) -> dict[str, Any]:
    """What we ask the device to capture.

    `period_size` / `buffer_size` are in samples per channel: 20 ms of audio per read,
    40 ms per websocket frame. Bigger frames mean fewer, larger JSON messages.
    """
    period = max(1, rate * frame_ms // 1000)
    return {
        "pcm": "noop",
        "channels": channels,
        "bits_per_sample": 16,
        "sample_rate": rate,
        "period_size": period,
        "buffer_size": period * 2,
        "mic_shift": mic_shift,
        "ref_shift": ref_shift,
    }


def parse_reply(message: str) -> dict[str, Any] | None:
    """Response/Event body, or None for anything we do not handle yet."""
    try:
        body = json.loads(message)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    for key in ("Response", "Event"):
        value = body.get(key)
        if isinstance(value, dict):
            return {key: value}
    return None
