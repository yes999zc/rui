"""Follow-up gate configuration: preserve the LX06 default, expose the device-AEC knob.

The gate opens one second after the reply drains and replays what the microphone heard
in between. That 1 s echo tail was measured on the LX06; a plug that does its own AEC
(no reference channel, RUI AEC off) keeps residual echo for longer, and the replayed
tail becomes a turn. The two new audio.voice fields let that plug tune the window
without inventing an adapter-side mute, while the defaults keep the LX06 path byte for
byte identical.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from rui.audio.process import AudioPlane
from rui.audio.protocol import FrameType
from rui.config import Config, load_config, save_config
from rui.config.schema import VoiceConfig


def _plane(**voice: object) -> AudioPlane:
    cfg = Config()
    for key, value in voice.items():
        setattr(cfg.audio.voice, key, value)
    return AudioPlane(cfg, "living", Path("/tmp/followup-window.sock"))


def test_defaults_preserve_the_lx06_followup_behaviour() -> None:
    cfg = Config()
    assert cfg.audio.voice.followup_delay_ms == 1000
    assert cfg.audio.voice.followup_replay is True
    assert VoiceConfig().followup_delay_ms == 1000


def test_followup_delay_is_bounded() -> None:
    assert VoiceConfig(followup_delay_ms=0).followup_delay_ms == 0
    assert VoiceConfig(followup_delay_ms=10000).followup_delay_ms == 10000
    with pytest.raises(ValidationError):
        VoiceConfig(followup_delay_ms=10001)
    with pytest.raises(ValidationError):
        VoiceConfig(followup_delay_ms=-1)


def test_replay_off_drops_the_gated_tail_without_feeding_the_vad() -> None:
    plane = _plane(followup_replay=False)
    plane._followup_capture = True
    plane._followup_tail.append((0.0, bytes(320)))
    plane._followup_samples = 320

    asyncio.run(plane._replay_followup_tail())

    assert plane._followup_samples == 0
    assert plane._followup_capture is False
    assert not plane._followup_tail


def test_the_capture_buffer_is_bounded_by_the_configured_window() -> None:
    plane = _plane(followup_delay_ms=2500)
    plane.gate = SimpleNamespace(armed=False)  # type: ignore[assignment]
    now = time.monotonic()
    plane._followup_capture = True
    plane._followup_start = now
    plane._followup_end = now + 2.5
    chunk = bytes(640)  # 20 ms of silence at 16 kHz (320 samples)
    for _ in range(175):  # 3.5 s offered
        plane._capture_followup_tail(chunk, 0.0)

    # The old hard-coded 2 s cap would have kept 32000 samples; the configured
    # 2.5 s window (plus its 0.25 s late-task margin) keeps more, and still bounds it.
    assert 32000 < plane._followup_samples <= 44000


def test_the_window_fields_survive_save_and_reload(tmp_path) -> None:
    cfg = Config()
    cfg.audio.voice.followup_delay_ms = 2500
    cfg.audio.voice.followup_replay = False
    cfg.audio.voice.followup_max_wait_ms = 5000
    path = tmp_path / "config.yaml"
    save_config(path, cfg)

    assert "followup_delay_ms: 2500" in path.read_text(encoding="utf-8")
    loaded = load_config(path)
    assert loaded.audio.voice.followup_delay_ms == 2500
    assert loaded.audio.voice.followup_replay is False
    assert loaded.audio.voice.followup_max_wait_ms == 5000


def test_an_old_config_without_the_fields_keeps_the_lx06_defaults(tmp_path) -> None:
    # Configs written before 0.4.1 have no followup keys; loading must not fail and
    # must keep the measured LX06 behaviour (1 s tail, replay on).
    path = tmp_path / "config.yaml"
    save_config(path, Config())
    text = path.read_text(encoding="utf-8")
    text = "".join(
        line for line in text.splitlines(keepends=True)
        if "followup_" not in line
    )
    path.write_text(text, encoding="utf-8")

    loaded = load_config(path)
    assert loaded.audio.voice.followup_delay_ms == 1000
    assert loaded.audio.voice.followup_replay is True


# ------------------------------------------------------- finite follow-up window (task-17)
#
# 2026-10-28 04:15: the farewell re-armed the follow-up gate for the whole session
# timeout (15 s), so speech 8+ seconds after "session closed" still opened a turn with
# no wake word. The window is now min(session.timeout_s, followup_max_wait_ms): finite,
# eight seconds by default, with 0 meaning "no follow-up at all".


def test_the_followup_window_is_finite_and_bounded() -> None:
    assert VoiceConfig().followup_max_wait_ms == 8000
    assert VoiceConfig(followup_max_wait_ms=0).followup_max_wait_ms == 0
    assert VoiceConfig(followup_max_wait_ms=60000).followup_max_wait_ms == 60000
    with pytest.raises(ValidationError):
        VoiceConfig(followup_max_wait_ms=-1)
    with pytest.raises(ValidationError):
        VoiceConfig(followup_max_wait_ms=60001)


class _RecordingGate:
    """Only the arm call matters: the plane's policy is what is under test."""

    armed = False

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def arm(self, **kwargs: object) -> None:
        self.armed = True
        self.calls.append(kwargs)

    def disarm(self) -> None:
        self.armed = False


def test_the_plane_arms_the_followup_gate_for_the_configured_cap() -> None:
    plane = _plane()
    gate = _RecordingGate()
    plane.gate = gate  # type: ignore[assignment]

    asyncio.run(plane._arm_followup_later(0.0))

    assert gate.calls == [{"followup": True, "max_wait_ms": 8000}]


def test_the_session_timeout_still_caps_a_longer_followup_setting() -> None:
    plane = _plane(followup_max_wait_ms=20000)
    plane.cfg.session.timeout_s = 5.0
    gate = _RecordingGate()
    plane.gate = gate  # type: ignore[assignment]

    asyncio.run(plane._arm_followup_later(0.0))

    assert gate.calls == [{"followup": True, "max_wait_ms": 5000}]


def test_a_disabled_session_timeout_keeps_the_eight_second_followup_cap() -> None:
    """`session.timeout_s=0` is "never time out", not a zero-length window.

    `min(0 * 1000, 8000)` armed the gate for 0 ms (i.e. not at all): turning the idle
    goodbye off silently disabled every follow-up turn.
    """
    plane = _plane()
    plane.cfg.session.timeout_s = 0.0
    gate = _RecordingGate()
    plane.gate = gate  # type: ignore[assignment]

    asyncio.run(plane._arm_followup_later(0.0))

    assert gate.calls == [{"followup": True, "max_wait_ms": 8000}]


def test_zero_means_no_followup_window_at_all(caplog) -> None:
    caplog.set_level(logging.INFO, logger="rui.audio.process")
    plane = _plane(followup_max_wait_ms=0)
    gate = _RecordingGate()
    plane.gate = gate  # type: ignore[assignment]

    asyncio.run(plane._arm_followup_later(0.0))

    assert gate.calls == [], "a zero cap still opened a listening window"
    assert "follow-up window disabled" in caplog.text


class _SilentVad:
    """The gate's own timeout/arming state machine, without the optional model."""

    def reset(self) -> None:
        return None

    def accept_waveform(self, samples: object) -> None:  # noqa: ARG002
        return None

    def is_speech_detected(self) -> bool:
        return False


class _LevelVad:
    """Speech iff the block is loud: a non-vacuous stand-in for the model.

    `_SilentVad` can never produce an utterance, so asserting `writer.frames == []`
    with it proved nothing about the gate. This stub reports speech for full-scale
    blocks and silence for zero blocks, so the real gate can open and close a turn.
    """

    def __init__(self) -> None:
        self._speech = False

    def reset(self) -> None:
        self._speech = False

    def accept_waveform(self, samples: np.ndarray) -> None:
        self._speech = bool(samples.size) and float(np.max(np.abs(samples))) > 0.5

    def is_speech_detected(self) -> bool:
        return self._speech


def _real_gate(vad: object | None = None) -> object:
    from rui.audio.vad import GateConfig, UtteranceGate

    gate = UtteranceGate.__new__(UtteranceGate)
    gate.cfg = GateConfig()
    gate.rate = gate.cfg.rate
    gate.armed = False
    gate.turns = 0
    gate.discarded = 0
    gate.idle_events = 0
    gate._vad = vad if vad is not None else _SilentVad()
    gate._cue_lag_ms = 0.0
    gate._cue_lag_seen = False
    gate._last_match_offset_ms = None
    gate._reset_state()
    return gate


class _StubWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


def test_speech_one_second_in_is_inside_the_window_twenty_is_outside() -> None:
    """A normal reply +1 s is a follow-up; +20 s needs the wake word again.

    The window is shot in audio time (the real gate disarms itself after max_wait_ms),
    so this exercises the same clock the speaker feeds, without sleeping. This is the
    ordinary-reply window; a close path never arms it (see the 0.8.1 tests below).
    """
    plane = _plane()
    gate = _real_gate()
    plane.gate = gate  # type: ignore[assignment]
    writer = _StubWriter()
    connected = asyncio.Event()
    connected.set()

    async def feed(seconds: float) -> None:
        block = bytes(640)  # 20 ms at 16 kHz
        for _ in range(int(seconds * 50)):
            await plane._feed_gate(block, 0.0, writer, connected)

    async def scenario() -> None:
        await plane._arm_followup_later(0.0)
        assert gate.armed, "the reply never opened its follow-up window"
        await feed(1.0)
        assert gate.armed, "speech at +1 s would have needed a wake word"
        # Twenty seconds of device audio later the gate has closed on its own. That is
        # the whole point of the finite window: +1 s is a follow-up, +20 s is not.
        await feed(19.0)
        assert not gate.armed, "the follow-up window outlived its eight seconds"

    asyncio.run(scenario())


def test_speech_nine_seconds_in_is_outside_the_eight_second_window() -> None:
    """The discriminating edge: +9 s is inside the old 15 s timeout but past the cap.

    The +1 s / +20 s test above passed on the pre-task-17 code as well (20 s > 15 s);
    nine seconds is the smallest choice that only the finite 8 s window rejects.
    """
    plane = _plane()
    gate = _real_gate()
    plane.gate = gate  # type: ignore[assignment]
    writer = _StubWriter()
    connected = asyncio.Event()
    connected.set()

    async def scenario() -> None:
        await plane._arm_followup_later(0.0)
        assert gate.armed, "the reply never opened its follow-up window"
        for _ in range(int(9.0 * 50)):  # 9 s of device audio
            await plane._feed_gate(bytes(640), 0.0, writer, connected)
        assert not gate.armed, "9 s after the reply still opened a follow-up turn"

    asyncio.run(scenario())


def test_speech_after_the_window_cannot_become_an_utterance() -> None:
    """No gate, no UTTERANCE_PCM: the control plane never sees a turn to start."""
    plane = _plane()
    gate = _real_gate(_LevelVad())
    plane.gate = gate  # type: ignore[assignment]
    writer = _StubWriter()
    connected = asyncio.Event()
    connected.set()

    async def scenario() -> None:
        await plane._arm_followup_later(0.0)
        for _ in range(int(8.2 * 50)):
            await plane._feed_gate(bytes(640), 0.0, writer, connected)
        assert not gate.armed
        writer.frames.clear()
        # The device still reports a (loud) frame after the window closed. A disarmed
        # gate drops it, so no utterance frame is written and no turn can start.
        loud = b"\xff\x7f" * 320
        for _ in range(25):
            await plane._feed_gate(loud, 0.0, writer, connected)

    asyncio.run(scenario())

    assert writer.frames == [], (
        "audio after the follow-up window reached the control plane as an utterance")


def test_an_armed_gate_writes_an_utterance_and_a_closed_one_does_not() -> None:
    """Positive control for the empty-writer assertion: the gate really can write.

    `_LevelVad` reports the loud frames as speech, so an open window must yield one
    UTTERANCE_PCM frame; the same audio after the window must yield none. Without the
    positive half, `frames == []` would also pass if `_feed_gate` never wrote at all.
    """
    plane = _plane()
    gate = _real_gate(_LevelVad())
    plane.gate = gate  # type: ignore[assignment]
    writer = _StubWriter()
    connected = asyncio.Event()
    connected.set()
    loud = b"\xff\x7f" * 320  # full-scale 20 ms at 16 kHz
    silent = bytes(640)

    async def feed(block: bytes, seconds: float) -> None:
        for _ in range(int(seconds * 50)):
            await plane._feed_gate(block, 0.0, writer, connected)

    async def scenario() -> None:
        await plane._arm_followup_later(0.0)
        assert gate.armed
        # Positive control: one second of speech plus the follow-up end silence closes
        # the turn and reaches the control plane as UTTERANCE_PCM.
        await feed(loud, 1.0)
        await feed(silent, 0.6)
        assert not gate.armed, "the utterance should have closed the gate"
        assert [frame[0] for frame in writer.frames] == [FrameType.UTTERANCE_PCM]

        # Negative: the same loud audio after the window is dropped by the gate.
        writer.frames.clear()
        await feed(loud, 1.0)
        await feed(silent, 0.6)
        assert writer.frames == []

    asyncio.run(scenario())


# ------------------------------------------------- 0.8.1: close paths arm nothing
#
# The window above belongs to an ordinary answer. A stop word ("退下") and the idle
# farewell close the session instead: the plane still drains the goodbye and applies
# the post-playback mute, but it must not arm a wake-free window. The measured
# failure was a turn started 15-20 s after "session closed" with no wake word.


class _SpeakAdapter:
    """Only the calls the speak loop makes; the gate decision is what is under test."""

    def __init__(self) -> None:
        self.played = 0

    async def play(self, pcm: bytes, sample_rate: int = 48000,
                   channels: int = 1) -> None:
        self.played += 1

    async def stop_playback(self, fade_ms: int = 120) -> None:
        return None


async def _speak_one_reply(plane: AudioPlane) -> None:
    """Queue one short reply block (the goodbye is a single canned line)."""
    plane._speak_queue.put_nowait((np.zeros(1600, dtype="<i2").tobytes(), True))
    plane._speak_queued_s = 0.1


async def _wait_until(predicate: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_an_ordinary_reply_arms_the_window_and_accepts_speech_one_second_later() -> None:
    """"在，你说。" then a second sentence: the wake-free follow-up still works."""
    async def scenario() -> None:
        plane = _plane(followup_delay_ms=300, post_playback_mute_ms=0)
        plane.adapter = _SpeakAdapter()  # type: ignore[assignment]
        gate = _real_gate(_LevelVad())
        plane.gate = gate  # type: ignore[assignment]
        writer = _StubWriter()
        connected = asyncio.Event()
        connected.set()
        loud = b"\xff\x7f" * 320
        silent = bytes(640)

        plane._reply_finished = True          # ReplyFinished with the default followup
        await _speak_one_reply(plane)
        task = asyncio.create_task(plane._speak_loop())
        try:
            assert await _wait_until(lambda: gate.armed, 3.0), (
                "an ordinary reply must still open the follow-up window")
            for _ in range(50):               # ~1 s after the reply drained
                await plane._feed_gate(loud, 0.0, writer, connected)
            for _ in range(60):               # VAD end silence closes the segment
                await plane._feed_gate(silent, 0.0, writer, connected)
            assert [frame[0] for frame in writer.frames] == [FrameType.UTTERANCE_PCM], (
                "speech ~1 s after a normal reply must become a turn")
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_the_dismissal_finish_never_arms_and_twenty_seconds_hear_nothing() -> None:
    """"退下" is the end: drain + mute, no window, and +20 s is not a turn.

    This drives the plane's real speak loop with `ReplyFinished(followup=False)`, the
    message the app now sends for a dismissal and for the idle farewell. The mute is
    still started (the goodbye's own tail must not be heard as a request), but the
    gate stays shut, so the loud audio the device keeps reporting cannot become an
    utterance.
    """
    async def scenario() -> None:
        plane = _plane(followup_delay_ms=300, post_playback_mute_ms=500)
        plane.adapter = _SpeakAdapter()  # type: ignore[assignment]
        gate = _real_gate(_LevelVad())
        plane.gate = gate  # type: ignore[assignment]
        writer = _StubWriter()
        connected = asyncio.Event()
        connected.set()
        loud = b"\xff\x7f" * 320
        silent = bytes(640)

        plane._reply_finished = True
        plane._reply_followup = False         # ReplyFinished(followup=False)
        await _speak_one_reply(plane)
        # Lower bound for the mute deadline: the speak loop has not started yet, and the
        # finish is consumed no earlier than this instant.
        started = time.monotonic()
        task = asyncio.create_task(plane._speak_loop())
        try:
            assert await _wait_until(lambda: not plane._reply_finished, 3.0), (
                "the speak loop never consumed the close finish")
            assert not gate.armed, "a dismissal opened a wake-free window"
            # The mute starts at the drain. Assert the deadline, not `_mic_is_muted()`:
            # comparing against `time.monotonic()` was wall-clock fragile, because a
            # starved 20 ms poll (full-suite run) can observe the plane >500 ms after it
            # consumed the finish, when the 0.5 s window had already expired for real.
            # `_followup_delay_from` sets `max(now, pacer.due) + mute_s` after `started`,
            # so a real 0.5 s close-path mute always lands at or past `started + 0.5`,
            # and a lost mute leaves the initial 0.0 behind.
            assert plane._mic_mute_until >= started + 0.5, (
                "the close path lost the post-playback mute")
            # Then give the old code's delayed `_arm_followup_later` time to land
            # (asserting immediately would pass on the pre-0.8.1 build only because
            # its arm task had not run yet - a false red/green).
            await asyncio.sleep(0.8)
            assert not gate.armed, "a dismissal opened a wake-free window"
            # Twenty seconds of full-scale input, then the VAD end silence: no gate,
            # no UTTERANCE_PCM, so the control plane never starts a turn.
            for _ in range(20 * 50):
                await plane._feed_gate(loud, 0.0, writer, connected)
            for _ in range(60):
                await plane._feed_gate(silent, 0.0, writer, connected)
            assert writer.frames == [], (
                "audio 20 s after a dismissal reached the control plane")
            assert not gate.armed
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
