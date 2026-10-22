"""One device stream, one owner.

Measured 2026-10-22: a reply spoken while 嘻唰唰 was playing interleaved two PCM streams
into the same player, and the words came out stuttering. The priority ladder is what stops
that from coming back with music, reminders and cues in the mix.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import numpy as np

from house_ear.audio.ack import tone
from house_ear.audio.process import AudioPlane
from house_ear.audio.protocol import PlayFile
from house_ear.config import Config


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


def make_plane(tmp_path: Path) -> tuple[AudioPlane, StubAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.system.data_dir = str(tmp_path)
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    adapter = StubAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def write_wav(path: Path, seconds: float = 1.0) -> None:
    import wave

    rate = 16000
    pcm = (np.zeros(int(rate * seconds), dtype="<i2")).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def test_a_cue_preempts_music_and_a_reply_waits_for_the_cue(tmp_path: Path) -> None:
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        assert plane._claim("music") is True

        # The cue is 320 ms and must be heard over the song.
        plane._start_ack(tone(plane.cfg))
        assert plane._owner == "cue"

        # A reply must not talk over the cue, and must not be refused forever either.
        assert plane._claim("dialogue") is False
        plane._release("cue")
        assert plane._claim("dialogue") is True

        plane._ack_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await plane._ack_task

    asyncio.run(run())


def test_music_asked_for_during_a_reply_starts_when_the_reply_ends(tmp_path: Path) -> None:
    async def run() -> None:
        plane, _ = make_plane(tmp_path)
        song = tmp_path / "song.wav"
        write_wav(song)

        assert plane._claim("dialogue") is True
        plane._start_playback(PlayFile(path=str(song), loop=False, gain=1.0))
        assert plane._owner == "dialogue", "the reply keeps the speaker"
        assert plane._pending_play is not None, "the request is remembered, not dropped"

        plane._release("dialogue")
        assert plane._owner == "music"
        assert plane._playback_task is not None
        plane._stop.set()
        plane._playback_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await plane._playback_task

    asyncio.run(run())


def test_flushing_an_answer_hands_the_speaker_back(tmp_path: Path) -> None:
    plane, _ = make_plane(tmp_path)
    plane._claim("dialogue")
    plane._flush_speech()
    assert plane._owner is None


def test_the_owner_is_a_metric_not_a_log_line(tmp_path: Path) -> None:
    plane, _ = make_plane(tmp_path)
    plane._claim("music")

    async def read() -> str:
        return plane.metrics().output_owner

    assert asyncio.run(read()) == "music"
