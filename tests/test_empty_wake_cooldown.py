"""0.8.16 ⑤: repeated empty wakes park the device's KWS for a cooldown.

dot2-b woke 7–9 times in four minutes, each time playing 「在呢」 for a room that never
spoke. The first empty wake is free; at `empty_wake_cooldown_after` consecutive empty
wakes the device ignores KWS hits for `empty_wake_cooldown_s`. A real turn resets the
streak. The suppression happens before the speaker gate and leaves only a counter and
one log line.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rui.audio.kws import WakeHit
from rui.audio.process import AudioPlane
from rui.config.schema import Config


class FakeIdleGate:
    """A VAD gate that closes its window without forming a turn."""

    def __init__(self) -> None:
        self.armed = True
        self.speaking = False
        self.idle_events = 0
        self.discarded = 0
        self.interrupted = 0
        self.speech_ms = 0.0
        self.silent_wakes = 0
        self.silent_wakes_session = 0
        self.cfg = SimpleNamespace(max_wait_ms=5000)

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.armed = False
        self.idle_events += 1
        return None


class FakeFinishedGate(FakeIdleGate):
    def __init__(self, finished: tuple[bytes, float]) -> None:
        super().__init__()
        self.finished = finished

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.armed = False
        return self.finished


class FakeWriter:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None


class StubSpeakerGate:
    def __init__(self) -> None:
        self.calls = 0

    def clear_anchor(self) -> None:
        return None

    def set_anchor(self, embedding, name=None) -> None:  # noqa: ARG002
        return None

    def evaluate_wake(self, pcm, rate, *, barge=False):  # noqa: ARG002
        self.calls += 1
        from rui.audio.speaker_gate import WakeDecision

        return WakeDecision(True, 0.9, "dad", "match", 0.1)


def _plane(
    tmp_path: Path, *, cooldown: float = 120.0, after: int = 2
) -> AudioPlane:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.audio.voice.empty_wake_cooldown_s = cooldown
    cfg.audio.voice.empty_wake_cooldown_after = after
    plane = AudioPlane(cfg, "living", tmp_path / "plane.sock")
    plane.gate = FakeIdleGate()  # type: ignore[assignment]
    return plane


def _feed_idle(plane: AudioPlane) -> FakeWriter:
    """Run one armed wake window to its silent timeout."""
    plane.gate.armed = True  # type: ignore[union-attr]
    plane._wake_armed = True
    writer, connected = FakeWriter(), asyncio.Event()
    connected.set()
    asyncio.run(
        plane._feed_gate(b"\x00\x00" * 320, ts_ms=1000.0, writer=writer,
                         connected=connected)
    )
    return writer


def test_the_first_empty_wake_does_not_cool_down(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    _feed_idle(plane)
    assert plane.empty_wakes == 1
    assert plane._cooldown_until == 0.0
    assert plane._cooldown_allows_wake() is True


def test_consecutive_empty_wakes_arm_the_cooldown(tmp_path: Path) -> None:
    plane = _plane(tmp_path, cooldown=120.0, after=2)
    _feed_idle(plane)
    _feed_idle(plane)
    assert plane.empty_wakes == 2
    assert plane._cooldown_until > time.monotonic()
    remaining = plane._cooldown_until - time.monotonic()
    assert 100.0 < remaining <= 120.0


def test_hits_are_dropped_before_the_speaker_gate(tmp_path: Path, caplog) -> None:
    plane = _plane(tmp_path)
    _feed_idle(plane)
    _feed_idle(plane)
    stub = StubSpeakerGate()
    plane.speaker_gate = stub  # type: ignore[assignment]
    writer, connected = FakeWriter(), asyncio.Event()
    connected.set()

    async def run() -> None:
        with caplog.at_level("INFO", logger="rui.audio.process"):
            await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), writer, connected)

    asyncio.run(run())
    assert plane.wake_rejections == {"cooldown": 1}
    assert stub.calls == 0, "a cooled-down device must not even score the wake"
    assert writer.written == b"", "no cue, no wake event, no turn"
    assert any(
        "wake suppressed: cooldown" in r.getMessage()
        and "left (empty wakes=2)" in r.getMessage()
        for r in caplog.records
    )
    assert plane._build_metrics().wake_rejected == {"cooldown": 1}


def test_the_cooldown_expires(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    _feed_idle(plane)
    _feed_idle(plane)
    plane._cooldown_until = time.monotonic() - 1.0
    assert plane._cooldown_allows_wake() is True
    assert plane.wake_rejections == {}


def test_a_real_turn_resets_the_streak(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    _feed_idle(plane)
    assert plane.empty_wakes == 1
    plane.gate = FakeFinishedGate((b"\x01\x00" * 320, 2000.0))  # type: ignore[assignment]
    writer, connected = FakeWriter(), asyncio.Event()
    connected.set()
    asyncio.run(
        plane._feed_gate(b"\x00\x00" * 320, ts_ms=2000.0, writer=writer,
                         connected=connected)
    )
    assert plane.utterances == 1
    assert plane.empty_wakes == 0, "a real turn means the room is not empty"


def test_a_barge_idle_is_not_an_empty_wake(tmp_path: Path) -> None:
    """Barge over music: the VAD often misses the person talking, but that is not "empty"."""
    plane = _plane(tmp_path, after=2)
    plane._active_wake_barge = True
    _feed_idle(plane)
    _feed_idle(plane)
    plane._active_wake_barge = True
    _feed_idle(plane)
    assert plane.empty_wakes == 0, "barge windows must never arm the cooldown"
    assert plane._cooldown_until == 0.0

    # ...and the very next plain wake still counts, so the flag is the discriminator.
    plane._active_wake_barge = False
    _feed_idle(plane)
    assert plane.empty_wakes == 1


def test_a_follow_up_idle_is_not_an_empty_wake(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane._wake_armed = False  # a follow-up window, not a wake
    writer, connected = FakeWriter(), asyncio.Event()
    connected.set()
    asyncio.run(
        plane._feed_gate(b"\x00\x00" * 320, ts_ms=1000.0, writer=writer,
                         connected=connected)
    )
    assert plane.empty_wakes == 0
    assert plane._cooldown_until == 0.0


def test_cooldown_zero_disables_the_punishment(tmp_path: Path) -> None:
    plane = _plane(tmp_path, cooldown=0.0, after=1)
    _feed_idle(plane)
    _feed_idle(plane)
    assert plane.empty_wakes == 2
    assert plane._cooldown_until == 0.0
    assert plane._cooldown_allows_wake() is True


def test_threshold_one_cools_down_on_the_first_empty_wake(tmp_path: Path) -> None:
    plane = _plane(tmp_path, after=1)
    _feed_idle(plane)
    assert plane.empty_wakes == 1
    assert plane._cooldown_until > time.monotonic()


def test_out_of_range_values_are_rejected() -> None:
    from rui.config.schema import VoiceConfig

    with pytest.raises(ValueError):
        VoiceConfig(empty_wake_cooldown_s=601)
    with pytest.raises(ValueError):
        VoiceConfig(empty_wake_cooldown_s=-1)
    with pytest.raises(ValueError):
        VoiceConfig(empty_wake_cooldown_after=0)
    with pytest.raises(ValueError):
        VoiceConfig(empty_wake_cooldown_after=11)
