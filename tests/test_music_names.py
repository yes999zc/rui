"""Nickname resolution: the model translates, the library still decides."""
from __future__ import annotations

import asyncio
from pathlib import Path

from rui.music import query as q
from rui.music.model import Track
from rui.music.names import AliasStore, _clean
from rui.music.tools import _search


class FakeSource:
    name = "fake"

    def __init__(self, songs: dict[str, list[Track]]) -> None:
        self.songs = songs

    async def search(self, term: str, limit: int) -> list[Track]:
        return list(self.songs.get(term, []))


class FakeNames:
    def __init__(self, mapping: dict[str, list[str]], tmp: Path) -> None:
        self.mapping = mapping
        self.aliases = AliasStore(tmp / "aliases.json")
        self.asked: list[str] = []

    async def candidates(self, name: str) -> list[str]:
        self.asked.append(name)
        return self.mapping.get(name, [])


def test_a_nickname_reaches_the_library_name_and_is_remembered(tmp_path: Path) -> None:
    """Measured on the old build: 「战车乐队」 is filed as Rammstein."""
    source = FakeSource({"Rammstein": [
        Track(id="subsonic:1", title="Benzin", artist="Rammstein", duration=200)]})
    names = FakeNames({"战车乐队": ["Rammstein"]}, tmp_path)

    async def run() -> list[Track]:
        return await _search(source, q.plan("播放战车乐队的歌"), names)

    tracks = asyncio.run(run())
    assert [t.title for t in tracks] == ["Benzin"]
    assert names.asked == ["战车乐队"]
    # next time the table answers, no model call
    assert names.aliases.get("战车乐队") == "Rammstein"
    assert names.aliases.apply("播放战车乐队的歌") == "播放Rammstein的歌"


def test_a_translation_that_does_not_match_is_not_used(tmp_path: Path) -> None:
    """The library decides: a candidate whose artist is wrong plays nothing."""
    source = FakeSource({"Rammstein": [
        Track(id="subsonic:1", title="Benzin", artist="陈奕迅", duration=200)]})
    names = FakeNames({"战车乐队": ["Rammstein"]}, tmp_path)
    assert asyncio.run(_search(source, q.plan("播放战车乐队的歌"), names)) == []


def test_the_candidate_parser_survives_model_wrappers() -> None:
    assert _clean('```json\n["Rammstein"]\n```') == ["Rammstein"]
    assert _clean('好的，应该是 ["战车乐队", "Rammstein"]。') == ["战车乐队", "Rammstein"]
    assert _clean("我不知道") == []
