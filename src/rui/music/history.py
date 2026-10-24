"""One persisted play history for dedup *and* taste, on purpose.

The old build had two unrelated "recent" lists: `MUSIC.recent` (in memory, 40-60
song ids, radio dedup, lost on every restart) and `taste.recent` (on disk, 40
entries with titles, statistics only). One file serves both here: the dedup window
and the spoken 「你最近爱听什么」 read the same truth, and a restart no longer
forgets what just played.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .model import Track

log = logging.getLogger(__name__)

MAX_ARTISTS = 200


class PlayHistory:
    """`data_dir/music_history.json`: {plays: {artist: n}, recent: [{id, title, artist, t}]}.

    Counts *starts*, not minutes - the old build's `taste.record_play` wording, kept
    because it is the number a person means by 「最近老放周杰伦」.
    """

    def __init__(self, path: Path, keep: int = 60) -> None:
        self.path = path
        self.keep = keep
        self.plays: dict[str, int] = {}
        self.recent: list[dict] = []
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.plays = {str(k): int(v) for k, v in (data.get("plays") or {}).items()}
            self.recent = list(data.get("recent") or [])
        except (OSError, ValueError):
            self.plays, self.recent = {}, []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"plays": self.plays, "recent": self.recent}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        os.chmod(self.path, 0o600)

    # ------------------------------------------------------------------ api
    def record(self, track: Track) -> None:
        """Count one start and remember the id; stats must never throw off playback."""
        try:
            if track.artist:
                self.plays[track.artist] = self.plays.get(track.artist, 0) + 1
                if len(self.plays) > MAX_ARTISTS:
                    keep = sorted(self.plays.items(), key=lambda kv: -kv[1])[:MAX_ARTISTS]
                    self.plays = dict(keep)
            self.recent.insert(0, {
                "id": track.id, "title": track.title, "artist": track.artist,
                "t": int(time.time()),
            })
            del self.recent[self.keep:]
            self._save()
        except (OSError, ValueError) as exc:
            log.info("history write failed (playback continues): %s", exc)

    def played_ids(self) -> set[str]:
        return {r.get("id", "") for r in self.recent if r.get("id")}

    def top(self, limit: int = 3) -> list[tuple[str, int]]:
        return sorted(self.plays.items(), key=lambda kv: -kv[1])[:limit]

    def last(self) -> dict | None:
        return self.recent[0] if self.recent else None
