"""Descriptive-song requests: plan (filters, not branches), then a candidate pool.

The old build's `semantic_pool` tried genres first and, when a genre matched,
never looked at artists or keywords (tools/music.py:225-243). These tests pin
the replacement: language / genre / keyword / mood / year are one filter set
over the per-song index, and the source is only the fallback path.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rui.music import semantic
from rui.music.library_index import LibraryIndex
from rui.music.model import Track
from rui.music.source import SourceError


def song(sid: str, title: str, artist: str = "", genre: str = "",
         bpm: int = 0, year: int = 2000, duration: int = 200) -> dict[str, Any]:
    return {"id": sid, "title": title, "artist": artist, "album": gene_album(genre),
            "genre": genre, "bpm": bpm, "year": year, "duration": duration}


def gene_album(genre: str) -> str:
    return f"{genre} album" if genre else ""


class FakeLibrary:
    name = "fake"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def songs_page(self, offset: int, limit: int) -> list[dict[str, Any]]:
        return self.rows[offset:offset + limit]


def make_index(tmp_path: Path, rows: list[dict[str, Any]]) -> LibraryIndex:
    index = LibraryIndex(tmp_path / "library_index.json", FakeLibrary(rows))
    asyncio.run(index.build(force=True))
    return index


class FakeSource:
    """The no-index fallback: genre tags, keyword search, random batch."""

    name = "fake"

    def __init__(self, by_genre: dict[str, list[Track]] | None = None,
                 search: dict[str, list[Track]] | None = None,
                 random: list[Track] | None = None,
                 fail: bool = False) -> None:
        self.genres = by_genre or {}
        self.hits = search or {}
        self.batch = random or []
        self.fail = fail
        self.random_calls = 0

    async def by_genre(self, genre: str, limit: int = 40) -> list[Track]:
        if self.fail:
            raise SourceError("down")
        return list(self.genres.get(genre, []))[:limit]

    async def search(self, term: str, limit: int) -> list[Track]:
        if self.fail:
            raise SourceError("down")
        return list(self.hits.get(term, []))[:limit]

    async def random(self, limit: int) -> list[Track]:
        if self.fail:
            raise SourceError("down")
        self.random_calls += 1
        return list(self.batch)[:limit]


def track(tid: str, title: str, genre: str = "", bpm: int = 0) -> Track:
    return Track(id=f"subsonic:{tid}", title=title, artist="A", duration=200,
                 genre=genre, bpm=bpm)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def test_looks_descriptive() -> None:
    for text in ("日文", "放几首日文歌", "来点安静的钢琴", "九十年代的粤语老歌",
                 "来点嗨的", "放点摇滚"):
        assert semantic.looks_descriptive(text), text
    for text in ("Rammstein", "雪の華", "周杰伦", "晴天", "",
                 "放几首coldplay的歌", "播放战车乐队的歌"):
        assert not semantic.looks_descriptive(text), text


def test_plan_maps_every_filter() -> None:
    p = semantic.plan("放几首日文歌")
    assert p is not None and p.languages == ["ja"]

    piano = semantic.plan("来点安静的钢琴")
    assert piano is not None
    assert piano.moods == ["calm"]
    assert "Piano" in piano.genres and "钢琴" in piano.genres
    assert piano.keywords == ["钢琴"], "an instrument is also a literal keyword"

    old = semantic.plan("九十年代的粤语老歌")
    assert old is not None
    assert old.languages == ["zh"] and old.years == (1990, 1999)

    hot = semantic.plan("来点嗨的")
    assert hot is not None and hot.moods == ["hot"]

    rock = semantic.plan("放点摇滚")
    assert rock is not None and rock.genres == ["Rock", "摇滚"]
    assert rock.keywords == [], "ask wrappers are not a search keyword"

    # a title inside a descriptive ask survives as the keyword
    mixed = semantic.plan("播放日文歌曲留在我身边")
    assert mixed is not None
    assert mixed.languages == ["ja"] and mixed.keywords == ["留在我身边"]


def test_strip_descriptors_keeps_the_title() -> None:
    assert semantic.strip_descriptors("日文歌曲留在我身边") == "留在我身边"
    assert semantic.strip_descriptors("安静的钢琴") == ""
    assert semantic.strip_descriptors("九十年代的粤语老歌") == ""


def test_language_genre_fallback_matches_the_old_mapping() -> None:
    assert semantic.language_genres("放几首日文歌") == ["J-Pop", "JPop", "Japanese"]
    assert semantic.language_genres("来点韩语歌")[0] == "K-Pop"
    assert semantic.language_genres("播放周杰伦的歌") == []


# ---------------------------------------------------------------------------
# pool
# ---------------------------------------------------------------------------
def test_pool_applies_language_and_mood_together(tmp_path: Path) -> None:
    rows = [
        *[song(f"jb{i}", f"曲{i}", "中島みゆき", genre="Ballad", bpm=70)
          for i in range(5)],
        *[song(f"jr{i}", f"燃{i}", "中島みゆき", genre="Rock", bpm=140)
          for i in range(5)],
        *[song(f"zb{i}", f"歌{i}", "陈奕迅", genre="Ballad", bpm=70)
          for i in range(5)],
    ]
    index = make_index(tmp_path, rows)
    tracks = asyncio.run(semantic.pool(semantic.plan("来点安静的日文歌"), None, index,
                                       limit=20, seed=1))
    assert {t.id for t in tracks} == {f"subsonic:jb{i}" for i in range(5)}


def test_pool_combines_language_genre_and_keyword(tmp_path: Path) -> None:
    rows = [
        *[song(f"jr{i}", f"Rock {i}", "中島みゆき", genre="Rock") for i in range(2)],
        *[song(f"jp{i}", f"Pop {i}", "中島みゆき", genre="J-Pop") for i in range(3)],
        *[song(f"zp{i}", f"Pop {i}", "C", genre="Pop") for i in range(3)],
    ]
    index = make_index(tmp_path, rows)
    tracks = asyncio.run(semantic.pool(semantic.plan("日文摇滚"), None, index, seed=2))
    assert {t.id for t in tracks} == {"subsonic:jr0", "subsonic:jr1"}
    # the keyword filter is literal: a title word narrows the same pool
    rows[0] = song("jr0", "夏夜摇滚", "中島みゆき", genre="Rock")
    rows[1] = song("jr1", "冬天摇滚", "中島みゆき", genre="Rock")
    index = make_index(tmp_path, rows)
    tracks = asyncio.run(semantic.pool(
        semantic.plan("日文摇滚夏夜"), None, index, seed=2))
    assert {t.id for t in tracks} == {"subsonic:jr0"}


def test_pool_without_an_index_uses_the_language_genre_fallback() -> None:
    jpop = [track("j1", "雪の華", "J-Pop"), track("j2", "First Love", "J-Pop")]
    source = FakeSource(by_genre={"J-Pop": jpop})
    tracks = asyncio.run(semantic.pool(semantic.plan("放几首日文歌"), source, None))
    assert {t.id for t in tracks} == {"subsonic:j1", "subsonic:j2"}
    # no method at all: the pool is empty, never an exception
    assert asyncio.run(semantic.pool(semantic.plan("放点摇滚"), None, None)) == []


def test_mood_only_request_falls_back_to_a_random_batch() -> None:
    calm = [track(f"c{i}", f"C{i}", "Ballad") for i in range(8)]
    hot = [track(f"h{i}", f"H{i}", "Rock") for i in range(8)]
    source = FakeSource(random=calm + hot)
    tracks = asyncio.run(semantic.pool(semantic.plan("来点安静的"), source, None))
    assert {t.id for t in tracks} == {f"subsonic:c{i}" for i in range(8)}
    assert source.random_calls == 1


def test_a_tag_poor_library_still_answers_a_mood() -> None:
    untagged = [track(f"u{i}", f"U{i}") for i in range(8)]
    source = FakeSource(random=untagged)
    tracks = asyncio.run(semantic.pool(semantic.plan("来点安静的"), source, None))
    assert len(tracks) == 8, "no mood tags must not mean an empty answer"


def test_pool_falls_back_when_the_index_has_no_match(tmp_path: Path) -> None:
    rows = [song(f"zb{i}", f"歌{i}", "陈奕迅", genre="Pop") for i in range(3)]
    index = make_index(tmp_path, rows)
    fallback = FakeSource(by_genre={"Rock": [track("r1", "Rock", "Rock")]})
    tracks = asyncio.run(semantic.pool(semantic.plan("来点摇滚"), fallback, index))
    assert {t.id for t in tracks} == {"subsonic:r1"}


def test_pool_dedupes_and_survives_a_dead_source() -> None:
    same = track("x", "X", "Rock")
    source = FakeSource(by_genre={"Rock": [same, same]})
    tracks = asyncio.run(semantic.pool(semantic.plan("放点摇滚"), source, None))
    assert [t.id for t in tracks] == ["subsonic:x"]
    assert asyncio.run(semantic.pool(semantic.plan("放点摇滚"),
                                     FakeSource(fail=True), None)) == []
