"""P2-2: a barge keeps the pre-wake ring after the skip (the person's first word).

The 2026-10-26 07:12 farewell case is NOT a barge: the wake landed while our own
goodbye was still being synthesised (`/tmp/rui-fleet3.log:1110-1147`), and is fixed in
the control plane by aborting that TTS on every wake (`app.on_wake`). Reverting the
ring on a barge would cut the user's first word (the KWS reports the hit after the
phrase), so this file guards that the ring is still replayed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np

from rui.audio.process import AudioPlane
from rui.config import Config

MUSIC = np.full(16000, 0.2, dtype=np.float32)          # 1 s of low-frequency music
MUSIC_PCM = (MUSIC * 32768.0).astype("<i2").tobytes()
USER = np.zeros(16000, dtype=np.float32)                # 1 s of user speech placeholder
USER[::4] = 0.3
USER_PCM = (USER * 32768.0).astype("<i2").tobytes()


class StubAdapter:
    def __init__(self) -> None:
        self.stops = 0
        self.played = 0

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.played += 1

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1

    def health(self) -> dict:
        return {"connected": True}


class StubWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class RecordingGate:
    """A gate that records exactly which blocks the plane hands it."""

    def __init__(self) -> None:
        self.armed = False
        self.interrupted = 0
        self.speech_ms = 0.0
        self.idle_events = 0
        self.fed: list[bytes] = []

    def arm(self, **_kwargs: object) -> None:
        self.armed = True

    def set_echo_reference(self, samples: object, rate: int) -> None:  # noqa: ARG002
        return None

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:  # noqa: ARG002
        return None

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.fed.append(pcm)
        return None


def _plane() -> tuple[AudioPlane, StubAdapter, RecordingGate]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "dot2", Path("/tmp/music-barge.sock"))
    adapter = StubAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    gate = RecordingGate()
    plane.gate = gate  # type: ignore[assignment]
    return plane, adapter, gate


def _queue_playback(plane: AudioPlane, seconds: float = 1.0) -> None:
    chunk = np.zeros(int(16000 * 0.1), dtype="<i2").tobytes()
    for _ in range(int(seconds / 0.1)):
        plane._speak_queued_s += 0.1
        plane._speak_queue.put_nowait((chunk, True))


def _run_wake(plane: AudioPlane, *, preroll_pcm: bytes) -> tuple[list[dict], RecordingGate]:
    gate = plane.gate
    plane._preroll.append((0.9, preroll_pcm))  # the wake phrase ring: stopped music
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
    asyncio.run(plane._on_wake(hit, writer, connected))  # type: ignore[arg-type]
    frames = [json.loads(f[5:].decode()) for f in writer.frames if f[0] == 1]
    return frames, gate


def test_a_barge_still_replays_the_ring_after_the_wake_skip() -> None:
    """The ring is the wake phrase's context; only the gate's 300 ms skip trims it."""
    ring = MUSIC_PCM + USER_PCM
    plane, adapter, gate = _plane()
    _queue_playback(plane)

    frames, gate = _run_wake(plane, preroll_pcm=ring)

    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True
    assert adapter.stops >= 1
    assert any(USER_PCM in block for block in gate.fed), (
        "the ring's user onset was dropped with the stopped playback")
    assert plane.preroll_replayed_ms > 0
    # Live speech after the wake is still fed, so the command itself is not lost.
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    asyncio.run(plane._feed_gate(USER_PCM, 1.0, writer, connected))
    assert USER_PCM in gate.fed


def test_a_non_barge_wake_still_replays_the_preroll() -> None:
    """Regression: the cue/normal path keeps its pre-wake replay unchanged."""
    plane, _adapter, gate = _plane()
    # No queued playback: this wake is a turn, not a barge.

    frames, gate = _run_wake(plane, preroll_pcm=MUSIC_PCM)

    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is False
    assert MUSIC_PCM in gate.fed, "the normal wake lost its pre-wake replay"


def test_barge_still_reports_the_stop_within_7ms() -> None:
    plane, _adapter, _gate = _plane()
    _queue_playback(plane)

    _run_wake(plane, preroll_pcm=MUSIC_PCM)

    assert plane.last_barge_stop_ms is not None
    assert plane.last_barge_stop_ms <= 7.0, plane.last_barge_stop_ms
