"""The source interface: the whole point of D1's redesign.

The old build's `music_play` was a 200-line if-chain with the source choice
(Subsonic vs online) fused into the intent parsing, and the online fallback had to
be hand-written into five separate places. Here a source is four async calls;
adding the second source touches nothing above this file.
"""
from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol, runtime_checkable

from .model import Track


class SourceError(RuntimeError):
    """A source that cannot answer: the caller says 「曲库里没找到」, never raises."""


@runtime_checkable
class MusicSource(Protocol):
    """Everything the queue and the tools need from one music backend."""

    name: str

    def search(self, term: str, limit: int) -> Awaitable[list[Track]]:
        """Search by free text (title, artist, or both). Empty term = server random."""
        ...

    def random(self, limit: int) -> Awaitable[list[Track]]:
        """A ready-to-play batch for radio mode."""
        ...

    def favorites(self) -> Awaitable[list[Track]]:
        """The user's starred tracks."""
        ...

    def star(self, track_id: str, on: bool) -> Awaitable[bool]:
        """Star/unstar; False when the server refused."""
        ...

    def resolve(self, track: Track) -> Awaitable[Path]:
        """One track as 16 kHz mono s16 PCM on disk (cached)."""
        ...
