"""IPC between the control plane and the audio plane.

One framing rule on the socket, so neither side ever scans for delimiters (a PCM
payload can contain any byte, including a newline):

    type (1 byte) | length (4 bytes, big endian) | payload

  type 0x01  control   UTF-8 JSON, one message per frame
  type 0x02  voice PCM audio plane -> control plane, mono at `voice.rate`
  type 0x03  play PCM  control plane -> audio plane, at `playback.rate`
  type 0x04  utterance  audio plane -> control plane: one turn's speech, mono `voice.rate`

PCM payload = seq (uint32) | ts_ms (float64) | s16le samples, header big endian.
The frame carries its own sequence number and timestamp because the audio plane is
the only side that knows when a block was actually captured.

This module is the seam: replacing the audio plane (Rust, C++) means reimplementing
these twenty lines plus the message models, and nothing else in the repo changes.
"""
from __future__ import annotations

import asyncio
import struct
from enum import IntEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

HEADER = struct.Struct(">BI")     # type, payload length
PCM_HEADER = struct.Struct(">Id")  # seq, ts_ms
MAX_JSON = 64 * 1024
MAX_PCM = 1024 * 1024


class FrameType(IntEnum):
    CONTROL = 0x01
    VOICE_PCM = 0x02
    PLAY_PCM = 0x03
    UTTERANCE_PCM = 0x04


def encode_frame(frame_type: FrameType, payload: bytes = b"") -> bytes:
    return HEADER.pack(int(frame_type), len(payload)) + payload


def encode_json(msg: BaseModel) -> bytes:
    return encode_frame(FrameType.CONTROL, msg.model_dump_json().encode("utf-8"))


def encode_pcm(frame_type: FrameType, seq: int, ts_ms: float, pcm: bytes) -> bytes:
    return encode_frame(frame_type, PCM_HEADER.pack(seq, ts_ms) + pcm)


def decode_pcm(payload: bytes) -> tuple[int, float, bytes]:
    if len(payload) < PCM_HEADER.size:
        raise ValueError("PCM frame shorter than its header")
    seq, ts_ms = PCM_HEADER.unpack_from(payload)
    return seq, ts_ms, payload[PCM_HEADER.size:]


def parse_header(header: bytes) -> tuple[FrameType, int]:
    frame_type, length = HEADER.unpack(header)
    try:
        kind = FrameType(frame_type)
    except ValueError as exc:
        raise ValueError(f"unknown frame type: {frame_type}") from exc
    limit = MAX_PCM if kind is not FrameType.CONTROL else MAX_JSON
    if length > limit:
        raise ValueError(f"{kind.name} frame of {length} bytes exceeds {limit}")
    return kind, length


async def read_frame(reader: asyncio.StreamReader) -> tuple[FrameType, bytes]:
    """Read exactly one frame: header first, then the declared payload length."""
    header = await reader.readexactly(HEADER.size)
    kind, length = parse_header(header)
    payload = await reader.readexactly(length) if length else b""
    return kind, payload


class Hello(BaseModel):
    """control -> plane: which device to run and how the consumer wants audio."""

    type: Literal["hello"] = "hello"
    device_id: str
    voice_rate: int = 16000
    frame_ms: int = 20


class Ready(BaseModel):
    """plane -> control: the adapter is open and this is what it really reports."""

    type: Literal["ready"] = "ready"
    pid: int
    device_id: str
    capture_rate: int
    capture_channels: int
    mic_channels: int
    reference_channels: int
    voice_rate: int


class Metrics(BaseModel):
    """plane -> control, every couple of seconds. Numbers, not impressions."""

    type: Literal["metrics"] = "metrics"
    uptime_s: float
    aec_enabled: bool = False
    aec_erle_db: float | None = None
    aec_channel: int = 0
    ref_active: bool = False
    speaker_connected: bool = False
    wake_count: int = 0
    kws_enabled: bool = False
    kws_error: str = ""
    vad_enabled: bool = False
    utterances: int = 0
    # Why a wake produced no turn: still listening, or gave the segment up (too short,
    # nothing said). Without these two the console can only show "0".
    gate_armed: bool = False
    gate_discarded: int = 0
    gate_interrupted: int = 0
    playback_s: float = 0.0
    # How far ahead of real time the speaker's buffer is being fed. Zero (or a number
    # that collapses) is what an underrun looks like from here; the speak loop aims for
    # PREBUFFER_S of lead and never more than MAX_LEAD_S.
    playback_lead_ms: float = 0.0
    barge_count: int = 0
    # Wake acknowledgement (S5a). `ack_mode` is what actually played, not what was asked.
    ack_mode: str = ""
    ack_attempts: int = 0
    ack_plays: int = 0
    ack_skipped: int = 0
    ack_failed: int = 0
    ack_error: str = ""
    preroll_replayed_ms: float = 0.0
    preroll_replays: int = 0
    voice_rms_db: float | None = None
    ref_rms_db: float | None = None
    frames_in: int
    frames_out: int
    frames_dropped: int
    no_consumer: int
    jitter_p50_ms: float
    jitter_p99_ms: float
    cpu_s: float
    audio_s: float
    rtf: float


class Wake(BaseModel):
    """plane -> control: the wake word fired. The turn clock starts here."""

    type: Literal["wake"] = "wake"
    ts_ms: float
    keyword: str
    erle_db: float | None = None
    ref_active: bool = False
    playback_s: float = 0.0
    # True when this wake stopped audio that was still coming out of the speaker.
    # The control plane must then drop the rest of the answer it was synthesising:
    # without it the flushed queue refills and the speaker talks over the new turn.
    barge: bool = False
    barged_s: float = 0.0
    # Levels at the hit. Pydantic drops unknown fields silently, so these used to be
    # built in the plane and then vanish on the wire - the console showed nothing.
    voice_rms_db: float | None = None
    ref_rms_db: float | None = None
    # Which acknowledgement actually played ("none" / "earcon" / "tts"): the config asks
    # for a mode, the plane reports what it could deliver.
    ack_mode: str = ""


class VendorSignalMsg(BaseModel):
    """plane -> control: the device's own assistant woke, or finished its dialog.

    Not audio and not ours: the control plane decides whether to stay quiet. 小爱同学
    waking the vendor's assistant used to make both assistants answer the same sentence.
    """

    type: Literal["vendor_signal"] = "vendor_signal"
    kind: str          # "wake" | "dialog_end" | "playing"
    text: str = ""
    ts_ms: float = 0.0


class SpeakFinished(BaseModel):
    """control -> plane: the last PLAY_PCM frame of this reply has been sent."""

    type: Literal["speak_finished"] = "speak_finished"


class Error(BaseModel):
    type: Literal["error"] = "error"
    message: str


class Shutdown(BaseModel):
    type: Literal["shutdown"] = "shutdown"


class PlayFile(BaseModel):
    """control -> plane: play a local wav at the speaker (debug playback, CLI only)."""

    type: Literal["play_file"] = "play_file"
    path: str
    loop: bool = False
    gain: float = Field(default=1.0, ge=0.0, le=4.0)


class StopAudio(BaseModel):
    type: Literal["stop_audio"] = "stop_audio"


class QueryMetrics(BaseModel):
    """control -> plane: send a fresh Metrics now (the periodic one is 2 s stale)."""

    type: Literal["query_metrics"] = "query_metrics"


ControlMessage = Annotated[
    Hello | Ready | Metrics | QueryMetrics | PlayFile | StopAudio | Wake | SpeakFinished
    | VendorSignalMsg | Error | Shutdown,
    Field(discriminator="type"),
]
CONTROL = TypeAdapter(ControlMessage)


def parse_control(payload: bytes) -> ControlMessage:
    return CONTROL.validate_json(payload)
