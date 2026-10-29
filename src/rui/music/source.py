"""The source interface: the whole point of D1's redesign.

The old build's `music_play` was a 200-line if-chain with the source choice
(Subsonic vs online) fused into the intent parsing, and the online fallback had to
be hand-written into five separate places. Here a source is four async calls;
adding the second source touches nothing above this file.
"""
from __future__ import annotations

from collections.abc import Awaitable
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .model import Track


class SourceError(RuntimeError):
    """A source that cannot answer: the caller says 「曲库里没找到」, never raises."""


class StarStatus(StrEnum):
    """A source's answer to "star/unstar this track", beyond a bare bool.

    A boolean cannot separate "the server refused" from "this source has no
    favourites at all": online adapters answer `UNSUPPORTED`, and the old `False`
    made the tool say 「服务器没回应」 for a source that never could star. Sources
    that only know a boolean keep returning `True`/`False`; the tool maps both
    forms (`music_favorite`).
    """

    OK = "ok"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


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

    def star(self, track_id: str, on: bool) -> Awaitable[bool | StarStatus]:
        """Star/unstar; False/REJECTED when the server refused, UNSUPPORTED when the
        source cannot star at all (online adapters). A bool-only source is still
        valid."""
        ...

    def resolve(self, track: Track) -> Awaitable[Path]:
        """One track as 16 kHz mono s16 PCM on disk (cached)."""
        ...
