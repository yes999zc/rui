"""P2.9: reply start gate, audible-gap metrics, and the ordered pre-start stop.

The issue measured a transport that delivers the first reply PCM batch, then the rest
~400 ms later: the device drained the 250-320 ms the Pacer keeps in hand and the gap was
audible mid-reply. These tests drive `_speak_loop` with that shape directly - no device,
no TTS - and pin four things:

  (a) without a prebuffer the slow arrival is detected as a gap;
  (b) with `audio.playback.prebuffer_ms` set, the reply starts only after enough audio
      is queued and no gap is counted;
  (c) a reply shorter than the prebuffer still starts (bounded timeout, no deadlock);
  (d) the counters reach the metrics wire and a turn trace's meta.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import numpy as np
import pytest

from house_ear.audio.process import (
    PLAYBACK_GAP_MIN_MS,
    PREBUFFER_TIMEOUT_EXTRA_S,
    AudioPlane,
)
from house_ear.audio.protocol import Metrics, encode_json, parse_control
from house_ear.config import Config
from house_ear.trace import TurnTrace


class RecordingAdapter:
    """One timestamped event list: `play` and `stop` interleaving is the assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, float]] = []
        self.pcm: list[bytes] = []

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.events.append(("play", time.monotonic()))
        self.pcm.append(pcm)

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.events.append(("stop", time.monotonic()))

    def health(self) -> dict:
        return {"connected": True}

    @property
    def plays(self) -> int:
        return sum(1 for name, _ts in self.events if name == "play")

    @property
    def stops(self) -> int:
        return sum(1 for name, _ts in self.events if name == "stop")


def make_plane(tmp_path: Path, prebuffer_ms: int = 0) -> tuple[AudioPlane, RecordingAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.audio.playback.prebuffer_ms = prebuffer_ms
    cfg.system.data_dir = str(tmp_path)
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    adapter = RecordingAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def pcm(ms: int = 100, rate: int = 16000) -> bytes:
    """A deterministic non-silent block, so a dropped chunk would be visible."""
    return np.zeros(rate * ms // 1000, dtype="<i2").tobytes()


def enqueue(plane: AudioPlane, chunk: bytes, *, segment: bool = False) -> None:
    """What the control-plane socket handler does for one PLAY_PCM frame."""
    seconds = len(chunk) / 2 / plane.cfg.audio.voice.rate
    plane._speak_queued_s += seconds
    plane._speak_finished = False
    plane._speak_queue.put_nowait((chunk, segment))


async def wait_until(predicate, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


async def run_reply(
    plane: AudioPlane, adapter: RecordingAdapter, *, gap_ms: int = 0,
) -> Metrics:
    """First batch, a `gap_ms` stall, then the rest - the issue's arrival shape."""
    task = asyncio.create_task(plane._speak_loop())
    try:
        enqueue(plane, pcm(), segment=True)
        if plane.cfg.audio.playback.prebuffer_ms == 0:
            # Without a gate the first block is on its way before the stall; the gap is
            # measured against the audio the device already has.
            await wait_until(lambda: adapter.plays >= 1)
        if gap_ms:
            await asyncio.sleep(gap_ms / 1000.0)
        for i in range(3):
            enqueue(plane, pcm(), segment=(i == 0))
        plane._speak_finished = True
        await wait_until(lambda: adapter.plays >= 4)
        await asyncio.sleep(0.05)
        return plane.metrics()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_a_slow_source_is_counted_as_a_gap_without_prebuffer(tmp_path: Path) -> None:
    plane, adapter = make_plane(tmp_path, prebuffer_ms=0)

    metrics = asyncio.run(run_reply(plane, adapter, gap_ms=400))

    assert metrics.playback_gap_count == 1, metrics.playback_gap_count
    # The first block covers 100 ms; the rest arrives 400 ms later, so ~300 ms is dry.
    assert metrics.playback_max_gap_ms >= 200.0
    assert metrics.playback_max_gap_ms > PLAYBACK_GAP_MIN_MS


def test_prebuffer_covers_the_gap_and_delays_the_first_block(tmp_path: Path) -> None:
    plane, adapter = make_plane(tmp_path, prebuffer_ms=450)

    started = time.monotonic()
    metrics = asyncio.run(run_reply(plane, adapter, gap_ms=400))
    elapsed = time.monotonic() - started

    # The gate held the first block until the second batch filled the prebuffer: the
    # first play can only happen after the ~400 ms stall.
    assert adapter.events[0][0] == "play"
    assert adapter.events[0][1] - started >= 0.35
    assert metrics.playback_gap_count == 0, metrics.playback_gap_count
    assert metrics.playback_max_gap_ms == 0.0
    # Sanity: the test actually spent the stall, not a shortened timeout.
    assert elapsed >= 0.4


def test_a_short_reply_starts_after_the_bounded_timeout(tmp_path: Path) -> None:
    """A 100 ms reply can never fill a 500 ms prebuffer; the bounded wait must release."""
    plane, adapter = make_plane(tmp_path, prebuffer_ms=500)

    async def run() -> tuple[float, float]:
        started = time.monotonic()
        task = asyncio.create_task(plane._speak_loop())
        try:
            enqueue(plane, pcm(), segment=True)
            await wait_until(lambda: adapter.plays >= 1, timeout_s=2.0)
            return time.monotonic() - started, time.monotonic()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    waited, _ = asyncio.run(run())

    # Long enough to show the prebuffer path was taken, short enough to prove it is
    # bounded (prebuffer + extra, plus scheduling slack).
    assert 0.5 + PREBUFFER_TIMEOUT_EXTRA_S * 0.5 <= waited < 1.5
    assert adapter.plays == 1


def test_gap_counters_reach_the_metrics_wire_and_the_turn_trace(tmp_path: Path) -> None:
    plane, adapter = make_plane(tmp_path, prebuffer_ms=0)

    metrics = asyncio.run(run_reply(plane, adapter, gap_ms=400))

    # The control plane receives these over the socket; they cannot be extra fields
    # that pydantic silently drops.
    decoded = parse_control(encode_json(metrics)[5:])
    assert isinstance(decoded, Metrics)
    assert decoded.playback_gap_count >= 1
    assert decoded.playback_max_gap_ms >= 200.0

    trace = TurnTrace(device_id="living")
    trace.note_playback_metrics(decoded)
    entry = trace.to_dict()
    assert entry["meta"]["playback_gap_count"] == decoded.playback_gap_count
    assert entry["meta"]["playback_max_gap_ms"] == decoded.playback_max_gap_ms


def test_the_pre_start_stop_is_skipped_when_the_player_is_already_stopped(
    tmp_path: Path,
) -> None:
    """The smallest ordered-start improvement: no redundant control frame before PCM.

    A reply after a barge/StopAudio already follows an explicit stop. Sending another
    one buys nothing on LX06 (play() opens the player anyway) and on a split
    control/data transport it is exactly the frame that races the first PCM.
    """
    plane, adapter = make_plane(tmp_path)

    async def run() -> None:
        # Nothing has been played: the device player is already stopped.
        assert await plane._prepare_reply_player(adapter) is False
        assert adapter.stops == 0

        # After a cue or a reply, audio is in flight: the stop is needed and ordered
        # before the first play().
        plane._device_audio_pending = True
        adapter.events.clear()
        assert await plane._prepare_reply_player(adapter) is True
        assert [name for name, _ts in adapter.events] == ["stop"]
        assert plane._device_audio_pending is False
        assert await plane._prepare_reply_player(adapter) is False
        assert adapter.stops == 1

    asyncio.run(run())


def test_the_first_reply_of_a_fresh_plane_does_not_emit_a_stop(tmp_path: Path) -> None:
    plane, adapter = make_plane(tmp_path)

    async def run() -> None:
        task = asyncio.create_task(plane._speak_loop())
        try:
            enqueue(plane, pcm(), segment=True)
            await wait_until(lambda: adapter.plays >= 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(run())

    assert adapter.stops == 0
    assert adapter.events[0][0] == "play"


@pytest.mark.parametrize("value", [0, 450, 1000])
def test_prebuffer_config_is_bounded_and_used(value: int, tmp_path: Path) -> None:
    plane, _ = make_plane(tmp_path, prebuffer_ms=value)
    assert plane.cfg.audio.playback.prebuffer_ms == value
