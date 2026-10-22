"""Barge-in: a wake word while the speaker is talking must stop the talking.

The old deployment got this for free because its client killed `aplay` on the key press.
Here the plane has to drop what it queued and tell the device to stop, and it has to tell
the control plane too - otherwise the flushed queue refills from the answer that is still
being synthesised and the speaker talks over the new turn.
"""
from __future__ import annotations

import asyncio
import json
import time
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


def test_a_wake_while_the_vendor_talks_pauses_its_player() -> None:
    """Taking the line from 小爱 mid-answer: our wake also pauses the vendor player.

    The old build's mic was gated shut while the vendor played (assistant_v3.py:3115), so
    its wake never faced this; ours hears 你好小瑞 mid-answer and must cut the vendor off,
    or both assistants talk at once.
    """

    async def run(*, playing: bool, fresh: bool = True) -> list[str]:
        plane, adapter = make_plane()
        adapter.vendor_playing = playing
        adapter.vendor_playing_ts = time.monotonic() if fresh else time.monotonic() - 100.0
        pauses: list[str] = []

        async def pause(reason: str) -> None:
            pauses.append(reason)

        adapter.pause_vendor_player = pause  # type: ignore[method-assign]
        writer, connected = StubWriter(), asyncio.Event()
        connected.set()
        hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
        await plane._on_wake(hit, writer, connected)  # type: ignore[arg-type]
        await asyncio.sleep(0)  # the pause is fire-and-forget; give the task its turn
        return pauses

    assert asyncio.run(run(playing=True)) == ["wake takeover"]
    assert asyncio.run(run(playing=True, fresh=False)) == [], "a stale playing state is ignored"
    assert asyncio.run(run(playing=False)) == [], "nothing to take over"


def test_a_wake_in_silence_is_not_a_barge() -> None:
    plane, adapter = make_plane()

    frames = run_wake(plane)

    assert adapter.stops == 0
    assert plane.barges == 0
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is False


def test_the_lead_is_bounded_and_the_barge_flushes_the_device() -> None:
    # The lead used to be kept under 0.35 s on the theory that it was barge latency, but
    # it is not: stop_play makes the client drop what it holds (measured 420 ms end to
    # end). The lead's job is the opposite - the client only opens its player at ~300 ms,
    # and a 200 ms lead stuttered on the first word. So: fill past the start threshold,
    # and keep the cap tight enough that a long answer never runs a second ahead.
    from house_ear.audio.process import MAX_LEAD_S, PREBUFFER_S

    assert 0.2 <= PREBUFFER_S <= 0.35
    assert MAX_LEAD_S <= 0.4
