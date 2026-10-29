"""EXAMPLE ONLY - an external music source skeleton, not a real service.

This file exists so `docs/music-private-sources.md` has a loadable reference and the
test suite can exercise the `RUI_MUSIC_SOURCES` hook without a network or a third-party
library. It implements the five `MusicSource` methods with empty/fake data:

* `search` / `random` / `favorites` return no tracks;
* `star` always claims the server refused (False);
* `resolve` returns a path that does not exist (nothing is transcribed).

Do not point a real deployment at this source: it will answer 「曲库里没找到」. A real
private source belongs outside this repository (see the doc) - never commit one here.

Load it with::

    RUI_MUSIC_SOURCES="stub=examples.music_sources.stub_source:make"

The module-level `music_sources_compat` is mandatory; it uses the same range grammar
as plugin manifests (`rui.plugins.semver`), e.g. ``>=0.8.6 <0.9.0``.
"""
from __future__ import annotations

from pathlib import Path

from rui.music.model import Track
from rui.music.sources.external import SourceContext

# Required by the loader: this module only works with the 0.8.x music interface.
music_sources_compat = ">=0.8.6 <0.9.0"


class StubSource:
    """A no-op source: the shape of an adapter, none of the behaviour."""

    name = "stub"

    def __init__(self, context: SourceContext) -> None:
        self.context = context
        self.source_id = context.source_id

    async def search(self, term: str, limit: int) -> list[Track]:
        return []

    async def random(self, limit: int) -> list[Track]:
        return []

    async def favorites(self) -> list[Track]:
        return []

    async def star(self, track_id: str, on: bool) -> bool:
        return False

    async def resolve(self, track: Track) -> Path:
        # A real adapter would download/transcode into context.cache_dir. The stub
        # returns a path without producing audio, which is why it is example-only.
        return Path(self.context.cache_dir) / "stub-silence.pcm"


def make(context: SourceContext) -> StubSource:
    """The factory named in `RUI_MUSIC_SOURCES` (`...stub_source:make`)."""
    return StubSource(context)
