"""The queue contract: advance on PlayFinished only, refill at boundaries, skip
silently, leave nothing behind, and the explicit pause nobody may lift."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rui.audio.protocol import LedState, PauseMusic, PlayFile, StopAudio
from rui.config.schema import MusicConfig
from rui.music.history import PlayHistory
from rui.music.model import Track
from rui.music.service import MusicService
from rui.music.source import SourceError


class FakeSource:
    name = "fake"

    def __init__(self, fail_ids: set[str] | None = None) -> None:
        self.fail = fail_ids or set()
        self.random_calls = 0

    async def search(self, term: str, limit: int) -> list[Track]:
        return [Track(id=f"subsonic:{term}", title=term, artist="A", duration=200)]

    async def random(self, limit: int) -> list[Track]:
        self.random_calls += 1
        return [Track(id=f"subsonic:r{self.random_calls}-{i}", title=f"R{i}",
                      artist="A", duration=200) for i in range(limit)]

    async def favorites(self) -> list[Track]:
        return [Track(id="subsonic:f1", title="Fav", artist="A", duration=200)]

    async def star(self, track_id: str, on: bool) -> bool:
        return True

    async def resolve(self, track: Track) -> Path:
        if track.id in self.fail:
            raise SourceError(f"{track.id} broken")
        return Path(f"/tmp/music/{track.id}.pcm")


class Recorder:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def __call__(self, msg: object) -> None:
        self.sent.append(msg)


def make_service(tmp_path: Path, fail_ids: set[str] | None = None,
                 cfg: MusicConfig | None = None) -> tuple[MusicService, FakeSource, Recorder]:
    source = FakeSource(fail_ids)
    history = PlayHistory(tmp_path / "h.json", keep=10)
    rec = Recorder()
    return MusicService(source, history, rec, cfg or MusicConfig()), source, rec


def played_files(rec: Recorder) -> list[str]:
    return [m.path for m in rec.sent if isinstance(m, PlayFile)]


def test_play_sends_one_file_and_records_history(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, rec = make_service(tmp_path)
        tracks = [Track(id="subsonic:1", title="T1", artist="A", duration=100),
                  Track(id="subsonic:2", title="T2", artist="A", duration=100)]
        started = await service.play(tracks)
        assert started is not None and started.title == "T1"
        assert played_files(rec) == ["/tmp/music/subsonic:1.pcm"]
        assert any(isinstance(m, LedState) and m.effect == "music" for m in rec.sent)
        assert service.history.last()["id"] == "subsonic:1"

    asyncio.run(run())


def test_broken_tracks_are_skipped_silently(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, rec = make_service(tmp_path, fail_ids={"subsonic:1"})
        tracks = [Track(id="subsonic:1", title="Broken", artist="A", duration=100),
                  Track(id="subsonic:2", title="Good", artist="A", duration=100)]
        started = await service.play(tracks)
        assert started is not None and started.title == "Good"
        assert played_files(rec) == ["/tmp/music/subsonic:2.pcm"]

    asyncio.run(run())


def test_the_queue_advances_only_on_its_own_finish(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, rec = make_service(tmp_path)
        tracks = [Track(id=f"subsonic:{i}", title=f"T{i}", artist="A", duration=100)
                  for i in range(3)]
        await service.play(tracks)

        # A foreign file's finish (debug playback) must not advance the queue.
        await service.on_finished("/tmp/other.wav")
        assert service.current is not None and service.current.title == "T0"

        await service.on_finished("/tmp/music/subsonic:0.pcm")
        assert service.current is not None and service.current.title == "T1"
        assert played_files(rec) == ["/tmp/music/subsonic:0.pcm",
                                     "/tmp/music/subsonic:1.pcm"]

        # The last one: queue finished, nothing left behind (old build's dead play key).
        await service.on_finished("/tmp/music/subsonic:1.pcm")
        await service.on_finished("/tmp/music/subsonic:2.pcm")
        assert service.state == "idle" and service.queue == []
        assert any(isinstance(m, LedState) and m.effect == "off" for m in rec.sent)

    asyncio.run(run())


def test_radio_refills_at_the_boundary_with_dedup(tmp_path: Path) -> None:
    async def run() -> None:
        cfg = MusicConfig(queue_min_tracks=2, queue_batch=3)
        service, source, rec = make_service(tmp_path, cfg=cfg)
        await service.play([Track(id="subsonic:only", title="Only", artist="A", duration=100)])
        await asyncio.sleep(0.1)   # let the top-up task run

        # Fresh random ids were appended; already-queued and already-played are not.
        ids = [t.id for t in service.queue]
        assert len(ids) == 1 + 3, ids
        assert len(set(ids)) == len(ids), "the refill repeated a track"

    asyncio.run(run())


def test_explicit_pause_survives_and_resume_plays_on(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, rec = make_service(tmp_path)
        await service.play([Track(id="subsonic:1", title="T1", artist="A", duration=100)])

        assert await service.pause() is True
        assert any(isinstance(m, PauseMusic) for m in rec.sent)
        assert any(isinstance(m, LedState) and m.effect == "music_paused" for m in rec.sent)
        assert await service.pause() is False, "double pause is not a pause"

        assert await service.resume() is True
        assert await service.resume() is False, "nothing paused any more"

    asyncio.run(run())


def test_stop_clears_everything(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, rec = make_service(tmp_path)
        await service.play([Track(id="subsonic:1", title="T1", artist="A", duration=100)])
        await service.stop()
        assert service.state == "idle" and service.queue == []
        assert any(isinstance(m, StopAudio) for m in rec.sent)

    asyncio.run(run())


def test_history_persists_across_instances(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _ = make_service(tmp_path)
        await service.play([Track(id="subsonic:1", title="T1", artist="周", duration=100)])

        again = PlayHistory(tmp_path / "h.json", keep=10)
        assert again.last()["id"] == "subsonic:1"
        assert again.top(1) == [("周", 1)]
        data = json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))
        assert data["plays"] == {"周": 1}

    asyncio.run(run())


def test_the_vendor_parks_the_song_and_gives_it_back(tmp_path: Path) -> None:
    """V4: the vendor takes the line mid-song - park, then hand back (0.6 s tail
    or the 4 s silent-wake watchdog, both the old build's numbers)."""
    async def run() -> None:
        service, _, rec = make_service(tmp_path)
        await service.play([Track(id="subsonic:1", title="T1", artist="A", duration=100),
                            Track(id="subsonic:2", title="T2", artist="A", duration=100)])

        assert await service.park_for_vendor() is True
        assert service.state == "paused" and service.vendor_parked is True
        assert any(isinstance(m, PauseMusic) for m in rec.sent)

        # double-park is not a park; nothing playing any more
        assert await service.park_for_vendor() is False

        # the vendor's dialog ends: the song comes back
        assert await service.give_back_if_parked(delay_s=0.0) is True
        assert service.state == "playing" and service.vendor_parked is False
        assert await service.give_back_if_parked() is False   # not parked any more

    asyncio.run(run())


def test_stop_clears_the_park(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, rec = make_service(tmp_path)
        await service.play([Track(id="subsonic:1", title="T1", artist="A", duration=100)])
        await service.park_for_vendor()
        await service.stop()
        assert service.vendor_parked is False and service.state == "idle"

    asyncio.run(run())
