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


@pytest.fixture()
def make_real_gate(tmp_path, monkeypatch):
    """A real UtteranceGate on a fake Silero model (the production timeout path)."""
    module = types.ModuleType("sherpa_onnx")

    class _Cfg:
        def __init__(self) -> None:
            self.silero_vad = types.SimpleNamespace()
            self.sample_rate = 16000

    class _Vad:
        def __init__(self, config, buffer_size_in_seconds: int = 30) -> None:  # noqa: ARG002
            self.config = config

        def reset(self) -> None:
            return None

        def accept_waveform(self, samples: object) -> None:  # noqa: ARG002
            return None  # never speech: only the timeout can close the window

        def is_speech_detected(self) -> bool:
            return False

    module.VadModelConfig = _Cfg
    module.VoiceActivityDetector = _Vad
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"fake")
    return RealGate(GateConfig(max_wait_ms=100), model)


def test_a_timeout_disarm_without_a_wake_idle_clears_the_block(make_real_gate) -> None:
    """Real vad path: a cue-excised silent timeout disarms WITHOUT an idle event.

    `vad.UtteranceGate` only increments `idle_events` for a plain silent wake; with
    `_cue_excised` set it disarms silently (task-15), so the old idle-event-only clear
    left every later reminder/debug `PLAY_PCM` dropped.
    """
    plane = _plane()
    plane.gate = make_real_gate
    _wake(plane)
    assert plane._stale_speech_blocked is True
    plane.gate._cue_excised = True  # the task-15 path: disarm without WakeIdle
    # Skip the cue hold and the wake-phrase skip: this test is about the timeout.
    plane.gate._hold_ms = 0.0
    plane.gate._hold_skip_ms = 0.0
    plane.gate._skipping = 0

    silence = b"\x00\x00" * 160
    for i in range(20):  # 100 ms max_wait; blocks are 10 ms of audio
        connected = asyncio.Event()
        connected.set()
        asyncio.run(plane._feed_gate(silence, float(i) * 20.0, StubWriter(), connected))
        if not plane.gate.armed:
            break

    assert plane.gate.armed is False
    assert plane._stale_speech_blocked is False
    plane._accept_play_pcm(_chunk(np.zeros(1600, dtype="<i2").tobytes()))
    assert plane._speak_queued_s > 0.0, "a reminder after the timeout was dropped"


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
