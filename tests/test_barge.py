"""Barge-in: a wake word while the speaker is talking must stop the talking.

The old deployment got this for free because its client killed `aplay` on the key press.
Here the plane has to drop what it queued and tell the device to stop, and it has to tell
the control plane too - otherwise the flushed queue refills from the answer that is still
being synthesised and the speaker talks over the new turn.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from house_ear.audio.process import AudioPlane
from house_ear.config import Config


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


def make_plane() -> tuple[AudioPlane, StubAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/barge.sock"))
    adapter = StubAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def queue_seconds(plane: AudioPlane, seconds: float) -> None:
    chunk = np.zeros(int(16000 * 0.1), dtype="<i2").tobytes()
    for _ in range(int(seconds / 0.1)):
        plane._speak_queued_s += 0.1
        plane._speak_queue.put_nowait((chunk, True))


def run_wake(plane: AudioPlane) -> list[dict]:
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
    asyncio.run(plane._on_wake(hit, writer, connected))  # type: ignore[arg-type]
    # Frames carry a 5-byte header (type + length); only control frames are JSON here.
    return [json.loads(f[5:].decode("utf-8")) for f in writer.frames if f[0] == 1]  # FrameType.CONTROL


def test_a_wake_during_playback_stops_the_speaker() -> None:
    plane, adapter = make_plane()
    queue_seconds(plane, 3.0)

    frames = run_wake(plane)

    assert adapter.stops >= 1, "the device was never told to stop"
    assert plane.barges == 1
    assert plane._speak_queued_s == 0.0, "queued audio survived the barge"
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True
    assert wake["barged_s"] == pytest.approx(3.0, abs=0.05)


def test_a_wake_in_silence_is_not_a_barge() -> None:
    plane, adapter = make_plane()

    frames = run_wake(plane)

    assert adapter.stops == 0
    assert plane.barges == 0
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is False


def test_the_lead_is_small_enough_for_300_ms_to_be_reachable() -> None:
    # Everything already handed to the device has to play out; the old build measured
    # its own lag as 0.55 s. Keeping our lead under 0.35 s leaves the device buffer as
    # the only thing between a barge and silence.
    from house_ear.audio.process import MAX_LEAD_S, PREBUFFER_S

    assert PREBUFFER_S <= 0.25
    assert MAX_LEAD_S <= 0.35
