"""The music queue: one owner of "what plays next", no speaker of its own.

Division of labour, learned from the old build running this exact flow for weeks:

* The **plane** owns the speaker (pause position, ducking, the wake barge). This
  service owns the *queue*: which track is current, what was played (persisted),
  when to top the radio up, and which track to resolve next.
* Advancing happens on the plane's `PlayFinished` - a natural end only. A paused
  or barged-away track must not look finished, so the service never advances on
  anything else.
* Topping up and prefetching run at track boundaries only (old build `_top_up`,
  assistant_v3.py:1617): a ~1 s library fetch mid-song is heard as a gap; between
  songs it is heard as nothing.
* A queue that ends must leave nothing behind (old build: the play key went dead
  after every song because `start` refused on a non-empty queue).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..audio.protocol import LedState, PauseMusic, PlayFile, ResumeMusic, StopAudio
from ..config.schema import MusicConfig
from .history import PlayHistory
from .model import Track
from .names import AliasStore, NameResolver
from .source import MusicSource, SourceError

log = logging.getLogger(__name__)

Sender = Callable[[object], Awaitable[None]]


class MusicService:
    def __init__(self, source: MusicSource, history: PlayHistory,
                 sender: Sender, cfg: MusicConfig,
                 names: NameResolver | None = None) -> None:
        self.source = source
        self.history = history
        self.sender = sender
        self.cfg = cfg
        # Nickname -> real name (「战车乐队」 -> Rammstein) and the alias table.
        self.names = names or NameResolver(AliasStore(Path("/tmp/house-ear-aliases.json")))
        self.queue: list[Track] = []
        self.idx = 0
        # "playing" (or waiting for the speaker) | "paused" (the user's hand) | "idle"
        self.state = "idle"
        # The song is parked because the vendor took the line; its dialog ending
        # (or staying silent 4 s) hands the speaker back.
        self.vendor_parked = False
        self._current_path: Path | None = None
        self._prefetch: asyncio.Task | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def current(self) -> Track | None:
        if 0 <= self.idx < len(self.queue):
            return self.queue[self.idx]
        return None

    def _remaining(self) -> int:
        return len(self.queue) - self.idx

    async def _send(self, msg: object) -> None:
        try:
            await self.sender(msg)
        except Exception as exc:  # noqa: BLE001 - a dropped message must not kill a tool call
            log.info("music: send failed: %s", exc)

    # ------------------------------------------------------------------ control
    async def play(self, tracks: list[Track], start: int = 0) -> Track | None:
        """Replace the queue and start at `start`. Returns the track now playing.

        A track that cannot be resolved is skipped silently (old build: fetch failed ->
        idx += 1, no words); if nothing resolves, the caller gets None and says so.
        """
        await self._teardown()
        self.queue = list(tracks)
        self.idx = max(0, start)
        self.state = "playing" if self.queue else "idle"
        track = await self._start_current()
        self._schedule_topup()
        return track

    async def _start_current(self) -> Track | None:
        """Resolve + hand the current track to the plane, skipping what cannot resolve."""
        while self.current is not None:
            track = self.current
            try:
                path = await self.source.resolve(track)
            except SourceError as exc:
                log.info("music: skip %s (%s)", track.label, exc)
                self.idx += 1
                continue
            self._current_path = path
            self.history.record(track)
            await self._send(PlayFile(path=str(path), loop=False))
            await self._send(LedState(effect="music"))
            self._schedule_prefetch()
            return track
        # Everything failed to resolve, or the queue was empty: leave nothing behind.
        await self._teardown()
        return None

    async def on_finished(self, path: str) -> None:
        """The plane reached a file's natural end: advance (or end) the queue."""
        if self._current_path is None or Path(path) != self._current_path:
            return   # debug playback or a stale event; not ours
        self.idx += 1
        self._current_path = None
        if self.current is None:
            await self._teardown()
            await self._send(LedState(effect="off"))
            log.info("music: queue finished")
            return
        await self._start_current()
        self._schedule_topup()

    async def next(self) -> Track | None:
        if self.idx + 1 >= len(self.queue):
            return None
        self.idx += 1
        self._current_path = None
        self.state = "playing"
        track = await self._start_current()
        self._schedule_topup()
        return track

    async def prev(self) -> Track | None:
        if self.idx <= 0:
            return None
        self.idx -= 1
        self._current_path = None
        self.state = "playing"
        return await self._start_current()

    async def pause(self) -> bool:
        if self.state != "playing":
            return False
        self.state = "paused"
        await self._send(PauseMusic())
        await self._send(LedState(effect="music_paused"))
        return True

    async def park_for_vendor(self) -> bool:
        """The vendor took the line (its wake, or its own player over key 114):
        park the song where it is and remember to give it back (old build
        `vendor_take_music`, assistant_v3.py:2171 - pause + armed give-back)."""
        if self.state != "playing":
            return False
        parked = await self.pause()
        self.vendor_parked = parked
        if parked:
            log.info("music: parked for the vendor")
        return parked

    async def give_back_if_parked(self, delay_s: float = 0.6) -> bool:
        """The vendor's dialog ended (or never spoke): hand the line back.

        The 0.6 s tail is the old build's number (resume_music_after_vendor) -
        the vendor's TTS rings out a beat after its `idle` event."""
        if not self.vendor_parked:
            return False
        self.vendor_parked = False
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        back = await self.resume()
        if back:
            log.info("music: resumed after the vendor")
        return back

    async def resume(self) -> bool:
        if self.state != "paused":
            return False
        self.state = "playing"
        await self._send(ResumeMusic())
        await self._send(LedState(effect="music"))
        return True

    async def stop(self) -> None:
        await self._teardown()
        await self._send(StopAudio())
        await self._send(LedState(effect="off"))

    async def _teardown(self) -> None:
        if self._prefetch is not None:
            self._prefetch.cancel()
            self._prefetch = None
        # A finished queue must leave nothing behind (old build, test_music_sink).
        self.queue, self.idx, self._current_path = [], 0, None
        self.state = "idle"
        self.vendor_parked = False

    # ------------------------------------------------------------------ radio
    def _schedule_topup(self) -> None:
        """Between-track refill, never mid-song: the fetch delay must not be heard."""
        if self._remaining() > self.cfg.queue_min_tracks:
            return
        asyncio.create_task(self._topup(), name="music-topup")

    async def _topup(self) -> None:
        try:
            fresh = await self.source.random(self.cfg.queue_batch)
        except SourceError as exc:
            # The library is down: the current song keeps playing, nothing else.
            log.info("music: refill failed (%s)", exc)
            return
        have = self.history.played_ids() | {t.id for t in self.queue[self.idx:]}
        add = [t for t in fresh if t.id not in have]
        if add:
            self.queue.extend(add)
            self._schedule_prefetch()
            log.info("music: refilled +%d (queue %d)", len(add), self._remaining())

    def _schedule_prefetch(self) -> None:
        """Resolve the next track while this one plays, so the boundary is seamless."""
        nxt = self.queue[self.idx + 1] if self.idx + 1 < len(self.queue) else None
        if nxt is None or self._prefetch is not None:
            return

        async def _fetch() -> None:
            try:
                await self.source.resolve(nxt)
            except SourceError as exc:
                log.info("music: prefetch %s failed (%s)", nxt.label, exc)

        self._prefetch = asyncio.create_task(_fetch(), name="music-prefetch")

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        track = self.current
        return {
            "state": self.state,
            "track": {"title": track.title, "artist": track.artist, "id": track.id}
            if track else None,
            "queue_left": max(0, self._remaining()),
        }
