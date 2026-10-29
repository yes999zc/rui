"""P2-2 (task-19): a wake must silence the previous turn's queued speech.

2026-10-26 07:12 (`/tmp/rui-fleet3.log`): the farewell was decided at 30.080, the wake
landed at 30.489 while its TTS was still streaming, and 1.4-2.0 s of goodbye chunks
entered the plane after the wake. `turn.abort()` only stops later chunks; the queued
audio still played and its echo became the user's turn (`没事的话，我先下啦` + `下一首`).
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from rui.audio.process import AudioPlane
from rui.audio.protocol import CONTROL, FrameType, WakeIdle, parse_header
from rui.audio.vad import GateConfig
from rui.audio.vad import UtteranceGate as RealGate
from rui.config import Config


class StubAdapter:
    def __init__(self) -> None:
        self.played = 0
        self.stops = 0

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.played += 1

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1

    def health(self) -> dict:
        return {"connected": True}


class StubWriter:
    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


class RecordingWriter(StubWriter):
    """Keeps what `_feed_gate` writes: a WakeIdle must really reach the plane."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def messages(self) -> list[object]:
        out: list[object] = []
        for data in self.writes:
            if not data:
                continue
            kind, length = parse_header(data[:5])
            if kind is FrameType.CONTROL:
                out.append(CONTROL.validate_json(data[5:5 + length]))
        return out

    def frame_types(self) -> list[FrameType]:
        return [parse_header(data[:5])[0] for data in self.writes if data]


class UtteranceGate:
    """Closes one utterance on the next feed call; can simulate a silent timeout."""

    armed = False
    interrupted = 0
    speech_ms = 0.0
    idle_events = 0

    def __init__(self) -> None:
        self.cfg = type("Cfg", (), {"max_wait_ms": 5000})()
        self.silent = False

    def arm(self, **_kwargs: object) -> None:
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def set_echo_reference(self, samples: object, rate: int) -> None:  # noqa: ARG002
        return None

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:  # noqa: ARG002
        return None

    def feed(self, pcm: bytes, ts_ms: float = 0.0):
        if self.silent:
            self.idle_events += 1  # the timeout closes the listening window
            return None
        return pcm, ts_ms


def _plane() -> AudioPlane:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "dot2", Path("/tmp/stale-speech.sock"))
    plane.adapter = StubAdapter()  # type: ignore[assignment]
    plane.gate = UtteranceGate()  # type: ignore[assignment]
    return plane


def _chunk(payload: bytes) -> bytes:
    from rui.audio.protocol import FrameType, encode_pcm

    return encode_pcm(FrameType.PLAY_PCM, 0, 0.0, payload)


def _wake(plane: AudioPlane) -> None:
    hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
    connected = asyncio.Event()
    connected.set()
    asyncio.run(plane._on_wake(hit, StubWriter(), connected))  # type: ignore[arg-type]


def test_a_plain_wake_drops_reply_chunks_queued_before_and_after_it() -> None:
    plane = _plane()
    # A farewell's chunks already sitting in the queue when the wake lands.
    stale = np.zeros(1600, dtype="<i2").tobytes()
    for _ in range(5):
        plane._speak_queue.put_nowait((stale, False))
        plane._speak_queued_s += 0.1

    _wake(plane)

    assert plane._speak_queued_s == 0.0, "queued goodbye audio survived the wake"
    assert plane._speak_queue.empty()
    # Chunks that arrive after the wake (the stream was still being produced) are
    # ignored too, so they cannot sound and be captured.
    plane._accept_play_pcm(_chunk(stale))
    assert plane._speak_queued_s == 0.0
    assert plane._speak_queue.empty()

    # The user's utterance for this turn unblocks the reply that follows it.
    connected = asyncio.Event()
    connected.set()
    asyncio.run(plane._feed_gate(b"\x00\x00" * 160, 1.0, StubWriter(), connected))
    assert plane._stale_speech_blocked is False
    plane._accept_play_pcm(_chunk(stale))
    assert plane._speak_queued_s > 0.0


def test_a_silent_wake_restores_playback_for_a_later_reminder() -> None:
    """A wake that hears nothing must not leave later non-turn playback muted.

    `wake without speech` closes the listening window; the next reminder/console
    PLAY_PCM has no turn behind it and would be dropped forever if the flag stayed set.
    """
    plane = _plane()
    _wake(plane)
    assert plane._stale_speech_blocked is True

    # Five seconds of nothing: the gate reports the timeout (`idle_events` + 1).
    plane.gate.silent = True  # type: ignore[union-attr]
    plane.gate.idle_events = 0  # type: ignore[union-attr]
    connected = asyncio.Event()
    connected.set()
    asyncio.run(plane._feed_gate(b"\x00\x00" * 160, 1.0, StubWriter(), connected))

    assert plane._stale_speech_blocked is False
    chunk = np.zeros(1600, dtype="<i2").tobytes()
    plane._accept_play_pcm(_chunk(chunk))
    assert plane._speak_queued_s > 0.0, "a reminder after a silent wake was dropped"


class FakeSilero:
    """Silero stand-in: tests toggle `speech`, the gate owns the end point."""

    speech = False

    def __init__(self, config, buffer_size_in_seconds: int = 30) -> None:  # noqa: ARG002
        self.config = config

    def reset(self) -> None:
        return None

    def accept_waveform(self, samples: object) -> None:  # noqa: ARG002
        return None

    def is_speech_detected(self) -> bool:
        return FakeSilero.speech


@pytest.fixture()
def make_real_gate(tmp_path, monkeypatch):
    """A real UtteranceGate on a fake Silero model (the production paths)."""

    class _Cfg:
        def __init__(self) -> None:
            self.silero_vad = types.SimpleNamespace()
            self.sample_rate = 16000

    module = types.ModuleType("sherpa_onnx")
    module.VadModelConfig = _Cfg
    module.VoiceActivityDetector = FakeSilero
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"fake")
    FakeSilero.speech = False
    # A 100 ms end point keeps the utterance test short; the timeout path only reads
    # `max_wait_ms`.
    return RealGate(GateConfig(max_wait_ms=100, end_silence_ms=100), model)


def _real_gate_timeout_setup(plane: AudioPlane) -> None:
    """Skip the cue hold and the wake-phrase skip: these tests are about the counters."""
    plane.gate._hold_ms = 0.0  # type: ignore[union-attr]
    plane.gate._hold_skip_ms = 0.0  # type: ignore[union-attr]
    plane.gate._skipping = 0  # type: ignore[union-attr]


def _feed(plane: AudioPlane, block: bytes, writer: RecordingWriter, ts_ms: float) -> None:
    connected = asyncio.Event()
    connected.set()
    asyncio.run(plane._feed_gate(block, ts_ms, writer, connected))  # type: ignore[arg-type]


def _run_until_disarmed(plane: AudioPlane, writer: RecordingWriter) -> None:
    """Feed 10 ms silence blocks until the 100 ms `max_wait_ms` disarms the gate."""
    silence = b"\x00\x00" * 160
    for i in range(20):
        _feed(plane, silence, writer, float(i) * 10.0)
        if not plane.gate.armed:
            return
    raise AssertionError("the silent gate never disarmed")


def _assert_silent_timeout_reported(plane: AudioPlane, writer: RecordingWriter) -> None:
    assert plane.gate.armed is False
    assert plane.gate.idle_events == 1, "the silent timeout was not counted as an idle event"
    idles = [m for m in writer.messages() if isinstance(m, WakeIdle)]
    assert len(idles) == 1, f"no WakeIdle reached the control plane: {writer.messages()}"
    assert plane._stale_speech_blocked is False
    plane._accept_play_pcm(_chunk(np.zeros(1600, dtype="<i2").tobytes()))
    assert plane._speak_queued_s > 0.0, "a reminder after the timeout was dropped"


def test_a_cue_excised_silent_timeout_reports_wake_idle(make_real_gate) -> None:
    """A silent wake reports WakeIdle even when a cue was excised.

    Production 2026-10-29: three silent wakes after 「你好小宝」 each left the control
    plane's one-turn latch open for TURN_STALE_S (20 s), because vad.py swallowed the
    idle event whenever `_cue_excised` was set. The cue is our own voice, not an
    answer: the wake heard nothing and must close the turn.
    """
    plane = _plane()
    plane.gate = make_real_gate
    writer = RecordingWriter()
    _wake(plane)
    assert plane._stale_speech_blocked is True
    plane.gate._cue_excised = True  # the cue/echo window was discarded
    _real_gate_timeout_setup(plane)

    _run_until_disarmed(plane, writer)

    _assert_silent_timeout_reported(plane, writer)


def test_a_plain_silent_timeout_still_reports_wake_idle(make_real_gate) -> None:
    """The pre-existing silent-wake behaviour is unchanged."""
    plane = _plane()
    plane.gate = make_real_gate
    writer = RecordingWriter()
    _wake(plane)
    assert plane._stale_speech_blocked is True
    assert plane.gate._cue_excised is False
    _real_gate_timeout_setup(plane)

    _run_until_disarmed(plane, writer)

    _assert_silent_timeout_reported(plane, writer)


def test_a_spoken_turn_is_not_reported_as_wake_idle(make_real_gate) -> None:
    """A turn that hears speech is untouched: no idle event, no WakeIdle."""
    plane = _plane()
    plane.gate = make_real_gate
    writer = RecordingWriter()
    _wake(plane)
    _real_gate_timeout_setup(plane)

    speech = b"\x01\x00" * 160
    silence = b"\x00\x00" * 160
    FakeSilero.speech = True
    for i in range(25):  # 250 ms spoken, past `min_speech_ms`
        _feed(plane, speech, writer, float(i) * 10.0)
    FakeSilero.speech = False
    for i in range(25, 40):  # enough silence for the 100 ms end point
        _feed(plane, silence, writer, float(i) * 10.0)
        if not plane.gate.armed:
            break

    assert plane.gate.armed is False, "the utterance never closed"
    assert plane.gate.idle_events == 0, "speech was counted as a silent wake"
    assert not any(isinstance(m, WakeIdle) for m in writer.messages()), (
        "a spoken turn sent WakeIdle: the control plane would close it early")
    assert FrameType.UTTERANCE_PCM in writer.frame_types(), (
        "the utterance frame was not written")


def test_our_own_announcement_disarms_an_armed_gate_and_plays() -> None:
    """Fifth path: a reminder disarms the gate directly (no `_feed_gate`, no idle)."""
    plane = _plane()
    _wake(plane)
    plane.gate.armed = True  # type: ignore[union-attr]  # the wake is still listening
    assert plane._stale_speech_blocked is True

    plane._disarm_gate_for_our_audio()  # what `_speak`/`_play_file` do

    assert plane.gate.armed is False
    assert plane._stale_speech_blocked is False
    plane._accept_play_pcm(_chunk(np.zeros(1600, dtype="<i2").tobytes()))
    assert plane._speak_queued_s > 0.0, "the announcement that disarmed the gate was dropped"


def test_a_newer_off_supersedes_a_slow_listening_paint() -> None:
    """The ring applies the newest state, not "whatever task finishes last".

    LedState is fire-and-forget, so every paint used to be its own task. An adapter
    whose "listening" script takes a second (the vendor's ring animations do) could
    then finish *after* the "off" a closed session sent, leaving the ring green -
    measured 2026-10-28 04:29 as "led: listening ... repaint stuck". The newest state
    is the only one worth painting, so the old task is cancelled when a new one lands.
    """

    class SlowAdapter(StubAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()
            self.led_states: list[str] = []

        async def set_led(self, effect: str, color: str = "",
                          *, repaint: bool = False) -> None:  # noqa: ARG002
            if effect == "listening":
                await self.release.wait()
            self.led_states.append(effect)

    async def scenario() -> list[str]:
        plane = _plane()
        adapter = SlowAdapter()
        plane.adapter = adapter  # type: ignore[assignment]
        plane._request_led("listening")
        await asyncio.sleep(0)          # the listening paint is now awaiting the ring
        plane._request_led("off")
        adapter.release.set()           # let the old paint finish if it still can
        await asyncio.sleep(0.05)
        return adapter.led_states

    states = asyncio.run(scenario())

    assert states == ["off"], f"a stale listening paint outlived the off: {states}"
