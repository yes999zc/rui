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
    # Connections refused for a bad/missing token since the plane started. The setup
    # wizard uses this to tell "no client yet" from "the client's token is wrong".
    speaker_rejected: int = 0
    wake_count: int = 0
    # Socket & Plug v1 (docs/socket-plug-v1.md §5). `wake_count` stays the RUI KWS
    # count; a device-detected wake is a different event and is counted separately.
    # `device_wakes` is every valid event accepted for policy; the rest are the
    # outcomes, one counter each so a log line never has to be the only record.
    device_wakes: int = 0
    device_wake_turns: int = 0
    device_wake_barges: int = 0
    device_wake_debounced: int = 0
    device_wake_invalid: int = 0
    device_wake_suppressed: int = 0
    device_wake_barge_suppressed: int = 0
    device_wake_rate_limited: int = 0
    device_wake_conflicts: int = 0
    device_wake_interrupts: int = 0
    device_wake_no_vad: int = 0
    device_wake_fallbacks: int = 0
    device_wake_degraded: bool = False
    device_wake_last_score: float | None = None
    kws_enabled: bool = False
    kws_error: str = ""
    vad_enabled: bool = False
    utterances: int = 0
    # Why a wake produced no turn: still listening, or gave the segment up (too short,
    # nothing said). Without these two the console can only show "0".
    gate_armed: bool = False
    # Speech is being captured right now (not just "the gate is open waiting"): the
    # idle goodbye must not count a 15 s listening window as activity.
    gate_speaking: bool = False
    gate_discarded: int = 0
    gate_interrupted: int = 0
    playback_s: float = 0.0
    # How far ahead of real time the speaker's buffer is being fed. Zero (or a number
    # that collapses) is what an underrun looks like from here; the speak loop aims for
    # PREBUFFER_S of lead and never more than MAX_LEAD_S.
    playback_lead_ms: float = 0.0
    # P2.9 observability: audible underruns. A gap is counted when the reply queue is
    # empty, the player was active, and the next block arrives after the audio already
    # handed to the device had finished. `playback_max_gap_ms` is the worst one since the
    # plane started; a gap under 20 ms is scheduler noise, not an underrun.
    playback_gap_count: int = 0
    playback_max_gap_ms: float = 0.0
    barge_count: int = 0
    probe_hits: int = 0
    voice_peak_db: float | None = None
    # mysoftvol (0-255): the only knob that changes loudness on this device. None = the
    # shell channel could not read it.
    device_volume: int | None = None
    # Who holds the speaker right now: "" / "cue" / "dialogue" / "reminder" / "music".
    output_owner: str = ""
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
    # Socket & Plug v1: "rui" for RUI's own KWS, "device" for a DeviceWake. The score,
    # threshold and barge hint travel so the turn trace can record why it fired; they
    # are diagnostic only and never give the event authority.
    source: Literal["rui", "device"] = "rui"
    score: float | None = None
    threshold: float | None = None
    barge_hint: bool = False
    action: str = ""
    # Recorded in the turn trace. An accepted wake is by definition not debounced; the
    # field exists so a future path that reports a suppressed duplicate can say so.
    debounced: bool = False


class DeviceWakeMsg(BaseModel):
    """plane -> control: one validated device wake, before the plane's policy acts.

    Fields mirror `rui.device.contract.DeviceWake` and nothing here carries policy:
    the audio plane already validated/debounced it, and whether it becomes a turn, a
    barge or nothing is reported by the `Wake` that may follow. This channel exists so
    the control plane can observe events that were suppressed (no turn) without
    inventing them from counters.
    """

    type: Literal["device_wake"] = "device_wake"
    ts_ms: float
    score: float
    threshold: float
    barge: bool = False
    level: float | None = None
    source: Literal["device"] = "device"


class WakeIdle(BaseModel):
    """plane -> control: the wake heard nothing and the listening gate disarmed.

    The plane closes its own gate after `max_wait_ms`; without telling the control plane
    the session stayed "open" with the light in listening (measured 16:10:38: 「你好小瑞」
    then silence, and nothing ended). Closing is silent on purpose: the old build said
    goodbye 15 s after a silent wake and 9哥 asked for it to stop.
    """

    type: Literal["wake_idle"] = "wake_idle"
    waited_ms: float = 0.0


class VendorSignalMsg(BaseModel):
    """plane -> control: the device's own assistant woke, or finished its dialog.

    Not audio and not ours: the control plane decides whether to stay quiet. 小爱同学
    waking the vendor's assistant used to make both assistants answer the same sentence.
    """

    type: Literal["vendor_signal"] = "vendor_signal"
    kind: str          # "wake" | "dialog_end" | "playing"
    text: str = ""
    ts_ms: float = 0.0


class LedState(BaseModel):
    """control -> plane: paint the light ring. Fire-and-forget by design.

    The shell request shares the websocket with the audio stream. Awaiting an 18-frame
    animation inside the control loop would queue every PLAY_PCM behind it - the old
    build measured a 3 s resume delay from exactly that. The plane hands this to the
    adapter as a task and never waits.
    """

    type: Literal["led"] = "led"
    effect: str
    # Force a repaint of the same state: the vendor's LED service repaints its idle
    # colour after our audio ends, so "listening" must be re-asserted, not assumed.
    repaint: bool = False


class SpeakStart(BaseModel):
    """control -> plane: the next PLAY_PCM frame begins a new sentence.

    The boundary is declared, not inferred: seq restarts on every speak() call, and
    speak() runs once per TTS chunk - the vendor streams one sentence in several
    300-500 ms pieces, so treating seq == 0 as a sentence start faded the waveform in
    a few times a second, mid-word. That ramp was the pop between chunks
    (docs/playback-path.md, fault B). SpeakStart is the only fade site.
    """

    type: Literal["speak_start"] = "speak_start"


class ReplyFinished(BaseModel):
    """control -> plane: this turn has nothing more to say (once per turn).

    SpeakFinished fires per TTS chunk; this fires when the runner has returned, so the
    plane can wait for the speaker to drain before opening the follow-up gate. Arming on
    "queue empty" was wrong: TTS gaps empty the queue mid-answer, and a 9.6 s reply armed
    the gate at 2.4 s, then closed it again 5 s later - the window missed the real end.
    """

    type: Literal["reply_finished"] = "reply_finished"
    # Seconds to hold a waiting song after this reply, replacing the default
    # follow-up window (None = the window length). 0 = bring the music back the
    # moment the reply drains - a volume confirmation wants the song immediately.
    hold_music_s: float | None = None


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


class ShellCommand(BaseModel):
    """control -> plane: run a shell command on the speaker and send back stdout.

    The device is the only place some things can be read: the mixer value (`mysoftvol`)
    decides loudness, `mphelper mute_stat` decides whether the vendor is playing. Without
    this channel every such question was a guess.
    """

    type: Literal["shell"] = "shell"
    request_id: str
    script: str


class ShellResult(BaseModel):
    """plane -> control: the answer to one ShellCommand (or why there is none)."""

    type: Literal["shell_result"] = "shell_result"
    request_id: str
    stdout: str = ""
    error: str = ""


class DeviceVolume(BaseModel):
    """control -> plane: read or write the speaker's own mixer.

    A message instead of a shell string on purpose: the ALSA detail (numid=177) belongs to
    the device module, and the API layer must not know it to show a number.
    """

    type: Literal["device_volume"] = "device_volume"
    action: Literal["read", "set"] = "read"
    value: int | None = None


class DeviceVolumeResult(BaseModel):
    type: Literal["device_volume_result"] = "device_volume_result"
    volume: int | None = None
    error: str = ""


class PlayFinished(BaseModel):
    """plane -> control: a non-looping play_file reached its natural end.

    A cancelled one (stop, barge, pause) says nothing - only the music queue advances
    on this event, and a pause must not look like a finish. The path identifies which
    request ended, so debug playback and the queue cannot mix.
    """

    type: Literal["play_finished"] = "play_finished"
    path: str


class PauseMusic(BaseModel):
    """control -> plane: the user said 「暂停」 - park the track, keep the position.

    Distinct from the speech duck (which `_speak_loop` grants and takes back): an
    explicit pause survives answers and idle timeouts; only ResumeMusic (or a new
    play_file) lifts it. Old build: `pause(for_speech=False)`, never auto-resumed.
    """

    type: Literal["pause_music"] = "pause_music"


class ResumeMusic(BaseModel):
    """control -> plane: the user said 「继续」 - play on from where it parked."""

    type: Literal["resume_music"] = "resume_music"


ControlMessage = Annotated[
    Hello | Ready | Metrics | QueryMetrics | PlayFile | StopAudio | Wake | LedState
    | SpeakStart | SpeakFinished | ReplyFinished | VendorSignalMsg | WakeIdle | Error | Shutdown | ShellCommand
    | ShellResult | DeviceVolume | DeviceVolumeResult | PlayFinished | PauseMusic | ResumeMusic
    | DeviceWakeMsg,
    Field(discriminator="type"),
]
CONTROL = TypeAdapter(ControlMessage)


def parse_control(payload: bytes) -> ControlMessage:
    return CONTROL.validate_json(payload)
