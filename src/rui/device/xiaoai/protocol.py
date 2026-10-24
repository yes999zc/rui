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
from dataclasses import dataclass
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


def playback_payload(rate: int, channels: int = 1) -> dict[str, Any]:
    """What we ask the speaker's player to open: 60 ms period, 300 ms buffer.

    The old deployment's geometry: big enough to absorb wifi jitter without
    underruns, small enough that stopping still feels immediate.
    """
    period = max(1, rate * 60 // 1000)
    return {
        "pcm": "default",
        "channels": channels,
        "bits_per_sample": 16,
        "sample_rate": rate,
        "period_size": period,
        "buffer_size": period * 5,
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


# --- vendor signals ---------------------------------------------------------
# The client mirrors what the *vendor's* assistant is doing as `{"Event": {...}}` frames.
# The one that matters: 小爱同学 wakes the vendor, it answers, and without this we would
# answer too - two assistants replying to one sentence (the old project hit exactly this
# and fixed it by yielding to the vendor for a window).


@dataclass(frozen=True)
class VendorSignal:
    kind: str        # "wake" | "dialog_end" | "playing" | "instruction"
    text: str = ""
    ts: float = 0.0


def _unwrap_new_line(data) -> dict | None:
    """`data.NewLine` arrives as a dict or as JSON in a string, sometimes nested twice."""
    raw = data
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    line: Any = raw.get("NewLine", raw)
    # Each level can be a string of JSON or a dict that wraps the next one; the old build
    # unwrapped exactly twice and blew up on the third shape it met.
    for _ in range(3):
        if isinstance(line, str):
            try:
                line = json.loads(line)
            except (ValueError, TypeError):
                return None
            continue
        if isinstance(line, dict) and "NewLine" in line:
            line = line["NewLine"]
            continue
        break
    return line if isinstance(line, dict) else None


def parse_vendor_signal(event: dict, ts: float = 0.0) -> VendorSignal | None:
    """What the vendor's own assistant is doing, from one `Event` frame body."""
    kind = str(event.get("event") or "")
    data = event.get("data")
    if kind == "playing":
        return VendorSignal(kind="playing", text=str(data or "").lower(), ts=ts)
    if kind != "instruction":
        return None
    line = _unwrap_new_line(data)
    if line is None:
        return None
    header = line.get("header") or {}
    payload = line.get("payload") or {}
    namespace = str(header.get("namespace") or "")
    name = str(header.get("name") or "")
    if namespace == "Dialog" and name == "Finish":
        # The vendor's dialog is over. Its `playing idle` event is not always sent
        # (measured 08:49:14-15: Playing arrived, Idle never did), so this is the
        # reliable end signal for giving music back (V4).
        return VendorSignal(kind="dialog_end", ts=ts)
    if namespace != "SpeechRecognizer":
        return None
    if name == "RecognizeResult":
        results = payload.get("results") or [{}]
        text = str((results[0] or {}).get("text") or "")
        if payload.get("is_final"):
            # A final recognition means the vendor wake fired; StopCapture is its
            # dialog ending and would re-trigger us mid-session.
            return VendorSignal(kind="wake", text=text, ts=ts) if text else None
        # An intermediate result means the vendor's recognizer is already hearing
        # someone - 0.7 s after its wake, ~2.5 s before the final transcript
        # (measured 08:58:29). The music should get out of the way right then;
        # this signal is deliberately weaker: it parks music and nothing else
        # (no 12 s quiet window, which could swallow our own turn).
        return VendorSignal(kind="vendor_listening", text=text, ts=ts)
    if name == "StartCapture":
        # The vendor's recognizer opens the microphone the moment its wake fires. This
        # arrives before its final transcription, which was too late: both assistants
        # answered the same sentence (9哥, 2026-10-22 18:51).
        return VendorSignal(kind="wake", ts=ts)
    if name == "StopCapture":
        return VendorSignal(kind="dialog_end", ts=ts)
    return None
