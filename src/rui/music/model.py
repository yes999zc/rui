"""The shapes every music source agrees on.

`Track` is the old build's `_brief` (tools/music.py:156-159): id, title, artist,
duration, and a label to speak. Ids are prefixed by the source that produced them
(`subsonic:<id>`, later `online:<sid>`, `kuwo:<rid>` - the old build's scheme), so a
queue can hold tracks from several sources and `resolve` routes by prefix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Track:
    id: str
    title: str = ""
    artist: str = ""
    duration: int = 0   # seconds; 0 = unknown
    # Carried for the descriptive-song path: a mood filter ("来点安静的") needs the
    # genre tag and BPM the source already returned. Both are optional - an online
    # source may know neither, and the filter then falls back to "anything".
    genre: str = ""
    bpm: int = 0

    @property
    def label(self) -> str:
        return f"{self.title} - {self.artist or '未知'}"


@dataclass(frozen=True)
class ResolvedTrack:
    """A track with its audio on disk: 16 kHz mono s16, whole file.

    Whole-file, not streamed, is deliberate - the old build compared the two and
    kept "download fully, then play" (docs/plan-online-music.md): streaming URLs
    expire and jitter, and the plane's chunk pacing wants a known length.
    """

    track: Track
    pcm_path: Path
    sample_rate: int = 16000


@dataclass
class QueryPlan:
    """What `music_play` actually wants, after the query pipeline cleaned it.

    The fields are tried in order: an extracted title with an artist beats a bare
    term; `random=True` means "just play something" and ignores the rest.
    """

    raw: str = ""
    title: str = ""
    artist: str = ""
    term: str = ""          # the cleaned search term for a source
    random: bool = False
    favorites: bool = False
    # How many tracks the user asked for (「三十首」). None = no count was spoken
    # and the queue batch decides; already clamped by `query.split_count`.
    limit: int | None = None
    notes: list[str] = field(default_factory=list)
