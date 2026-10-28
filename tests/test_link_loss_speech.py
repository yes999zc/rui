"""P0b-A: a lost speaker link must drop the queued half sentence, not replay it.

The bridge already resets its own frame buffer and stream state when the link drops
(house-ear commit 9065d19). The plane is the other half: `_speak_queue` holds the answer
the control plane pushed ahead of real time, and those chunks would otherwise be played
into the next session after the speaker reconnects. The rule pinned here is the
transition, not the state: live -> lost purges once; a plane that starts disconnected
(or is merely idle) does not.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from rui.audio.process import AudioPlane
from rui.config import Config


class FlappingAdapter:
    """A speaker whose bridge-side `connected` can flip under the plane."""

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.pcm: list[bytes] = []
        self.stops = 0

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.pcm.append(pcm)

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1

    def health(self) -> dict:
        return {"connected": self.connected}


def make_plane(tmp_path: Path, *, connected: bool = True) -> tuple[AudioPlane, FlappingAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.system.data_dir = str(tmp_path)
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    adapter = FlappingAdapter(connected)
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def pcm(ms: int = 200, value: int = 1000) -> bytes:
    """A non-silent block: a replayed prefix would be audible, and is visible here."""
    return np.full(16000 * ms // 1000, value, dtype="<i2").tobytes()


def enqueue(plane: AudioPlane, chunk: bytes, *, segment: bool = False) -> None:
    """What the control-plane socket handler does for one PLAY_PCM frame."""
    seconds = len(chunk) / 2 / plane.cfg.audio.voice.rate
    plane._speak_queued_s += seconds
    plane._speak_finished = False
    plane._speak_queue.put_nowait((chunk, segment))


def test_losing_the_link_drops_queued_speech(tmp_path: Path, caplog) -> None:
    plane, adapter = make_plane(tmp_path)
    plane.metrics()  # a live link observed once is the baseline

    enqueue(plane, pcm(500), segment=True)
    enqueue(plane, pcm(500))
    assert plane._speak_queued_s == pytest.approx(1.0)

    caplog.set_level(logging.INFO, logger="rui.audio.process")
    adapter.connected = False
    plane.metrics()

    assert plane._speak_queue.empty(), "the half sentence must be dropped"
    assert plane._speak_queued_s == 0.0
    assert adapter.stops == 0 and adapter.pcm == [], "nothing is sent to a dead link"
    lost = [r.getMessage() for r in caplog.records if "link lost" in r.getMessage()]
    assert len(lost) == 1, caplog.text
    assert "living" in lost[0], "the line names the room"
    assert "2" in lost[0] and "1.0" in lost[0], "and what it dropped"


def test_a_reply_after_the_link_loss_starts_with_only_new_audio(tmp_path: Path) -> None:
    plane, adapter = make_plane(tmp_path)
    plane.metrics()

    old = pcm(600, 1111)
    enqueue(plane, old, segment=True)
    adapter.connected = False
    plane.metrics()

    new = pcm(300, -2222)
    enqueue(plane, new, segment=True)
    queued = [plane._speak_queue.get_nowait() for _ in range(plane._speak_queue.qsize())]

    assert [chunk for chunk, _segment in queued] == [new], "no old prefix replays"
    assert plane._speak_queued_s == pytest.approx(0.3, abs=0.01)
    # The generation bump discards a chunk already pulled by `_speak_loop` on the
    # prebuffer path (the same guard barge uses). With the schema default
    # `prebuffer_ms=0`, one already-pulled chunk can still reach the dead link.
    assert plane._speak_generation >= 1


def test_a_disconnected_startup_is_not_a_link_loss(tmp_path: Path, caplog) -> None:
    plane, _adapter = make_plane(tmp_path, connected=False)
    caplog.set_level(logging.INFO, logger="rui.audio.process")
    plane.metrics()  # the empty-queue startup reading, seen before any speech
    plane.metrics()
    assert not caplog.records

    queued = pcm(200)
    enqueue(plane, queued)
    for _ in range(3):  # the metrics loop ticks every 2 s, never "one" false reading
        plane.metrics()

    assert not plane._speak_queue.empty(), "only a live -> lost transition purges"
    assert plane._speak_queued_s > 0.0
    assert not [r for r in caplog.records if "link lost" in r.getMessage()]
