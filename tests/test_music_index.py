"""The per-song language index: classifier rules, cache build, pick filters.

Offline only: a fake library source stands in for Navidrome, so nothing here
touches the NAS. Every rule asserted below was paid for in the old build
(docs/library-index.md); the build/paging halves are new code.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from rui.music import library_index as li
from rui.music.library_index import (
    LibraryIndex,
    LibraryIndexMissing,
    classify,
    mood_of,
)
from rui.music.source import SourceError


class FakeLibrary:
    """A Subsonic-shaped source: pages of raw song dicts."""

    name = "fake"

    def __init__(self, songs: list[dict[str, Any]],
                 albums: list[dict[str, Any]] | None = None,
                 fail: bool = False) -> None:
        self.songs = songs
        self.albums = albums or []
        self.fail = fail
        self.page_calls = 0
        self.album_calls = 0

    async def songs_page(self, offset: int, limit: int) -> list[dict[str, Any]]:
        self.page_calls += 1
        if self.fail:
            raise SourceError("search3 refused")
        return self.songs[offset:offset + limit]

    async def albums_page(self, offset: int, limit: int) -> list[dict[str, Any]]:
        self.album_calls += 1
        return self.albums[offset:offset + limit]

    async def album_songs(self, album_id: str) -> list[dict[str, Any]]:
        for album in self.albums:
            if album.get("id") == album_id:
                return list(album.get("songs") or [])
        return []


def song(sid: str, title: str, artist: str = "", album: str = "",
         genre: str = "", year: int | None = 2000, duration: int = 200,
         bpm: int = 0) -> dict[str, Any]:
    return {"id": sid, "title": title, "artist": artist, "album": album,
            "genre": genre, "year": year, "duration": duration, "bpm": bpm}


def sample_songs() -> list[dict[str, Any]]:
    rows = []
    for i in range(12):
        rows.append(song(f"j{i:02d}", f"曲{i}", "だれか", genre="J-Pop", bpm=70))
    for i in range(8):
        rows.append(song(f"z{i:02d}", f"歌{i}", "陈奕迅", genre="Pop", bpm=140))
    for i in range(10):
        rows.append(song(f"e{i:02d}", f"Rock {i}", "Eagles", genre="Rock", bpm=0))
    for i in range(4):
        rows.append(song(f"k{i:02d}", f"노래{i}", "아이유", genre="K-Pop", bpm=0))
    for i in range(3):
        rows.append(song(f"u{i:02d}", f'{i}!!!', "", genre="", year=None))
    return rows


def index_at(tmp_path: Path, songs: list[dict[str, Any]] | None = None,
             **kwargs: Any) -> LibraryIndex:
    return LibraryIndex(tmp_path / "library_index.json", FakeLibrary(songs or []),
                        **kwargs)


def build(tmp_path: Path, songs: list[dict[str, Any]] | None = None,
          **kwargs: Any) -> LibraryIndex:
    idx = index_at(tmp_path, songs, **kwargs)
    asyncio.run(idx.build())
    return idx


# ---------------------------------------------------------------------------
# classifier
# ---------------------------------------------------------------------------
def test_kana_is_japanese() -> None:
    for title in ("雪の華", "残酷な天使のテーゼ", "ﾃｰｾﾞ", "そばにいるね", "スーパー"):
        lang, source, conf = classify(title, "Someone")
        assert (lang, source) == ("ja", "kana"), title
        assert conf >= 0.9
    # kana only in the album name still counts
    assert classify("First Love", "Someone", album="ファースト・ラブ")[0] == "ja"
    # ・ and ー are punctuation that sit inside the katakana block, not kana:
    # 张学友「你的名字・我的姓氏」 must stay Chinese (old build measured this).
    lang, source, _ = classify("你的名字・我的姓氏(Live)", "张学友")
    assert (lang, source) == ("zh", "cjk")


def test_hangul_is_korean() -> None:
    lang, source, conf = classify("노래", "아이유")
    assert (lang, source) == ("ko", "hangul")
    assert conf >= 0.85
    # hangul + han in the same title: still ko, but honestly unsure
    lang2, _, conf2 = classify("사랑해요只对你说", "林俊杰")
    assert lang2 == "ko"
    assert conf2 < conf


def test_latin_with_english_genre() -> None:
    lang, source, conf = classify("Hotel California", "Some Western Band", genre="Rock")
    assert (lang, source) == ("en", "latin_genre")
    assert conf >= 0.75
    assert classify("Someone Like You", "Some Singer", genre="POP")[0] == "en"


def test_latin_without_genre_is_weak_english() -> None:
    lang, source, conf = classify("Diva", "Some Singer")
    assert (lang, source) == ("en", "latin")
    assert 0 < conf < li.CONF["latin_genre"]


def test_cjk_title_is_low_confidence_chinese() -> None:
    lang, source, conf = classify("海阔天空", "Some Cantopop Band")
    assert (lang, source) == ("zh", "cjk")
    assert conf == li.CONF["cjk_title"]


def test_cjk_artist_with_latin_title() -> None:
    # 「Lydia」 by a Chinese act: the title alone would say en
    lang, source, conf = classify("Lydia", "飞儿乐团")
    assert (lang, source) == ("zh", "cjk")
    assert conf < li.CONF["cjk_title"]
    # a CJK album/compilation name must NOT drag an English act to zh
    assert classify("My Love", "Some Western Band", album="真爱精选")[0] == "en"


def test_override_precedence() -> None:
    # kanji-only Japanese title -> override rescues it from zh
    lang, source, conf = classify("幸せ", "中島みゆき")
    assert (lang, source) == ("ja", "override")
    assert conf == li.CONF["override"]
    # override beats the latin->en rule, and matching is normalised
    assert classify("Du hast", "Rammstein")[0] == "en"
    assert classify("Numb", "  linkin park ")[1] == "override"
    assert classify("涙そうそう", "Custom Act", overrides={"Custom Act": "zh"})[:2] \
        == ("zh", "override")


def test_other_script_and_unknown() -> None:
    lang, source, _ = classify("ลีลาวดี", "Sara Nusara Poongprasert")
    assert (lang, source) == ("other", "script:thai")
    assert classify("Подмосковные вечера", "Хор")[0] == "other"
    assert classify("", "") == ("unknown", "none", 0.0)
    assert classify("1984", "")[0] == "unknown"
    # no letters in the title but a latin act: weak en, not unknown
    assert classify("!!!", "Eagles")[0] == "en"


def test_language_codes_are_a_closed_set() -> None:
    for args in (("雪の華", ""), ("노래", ""), ("歌", ""), ("Song", ""),
                 ("ลีลาวดี", ""), ("", "")):
        assert classify(*args)[0] in li.LANGUAGES


def test_mood_is_bpm_then_genre() -> None:
    assert mood_of(bpm=70) == "calm"
    assert mood_of(bpm=140) == "hot"
    assert mood_of(bpm=105) == ""
    assert mood_of(genre="Rock") == "hot"
    assert mood_of(genre="民谣") == "calm"
    assert mood_of(genre="Pop") == ""
    assert mood_of() == ""


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def test_build_walks_pages_dedupes_and_writes_atomically(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(li, "PAGE_SIZE", 2)
    songs = sample_songs() + [song("j00", "duplicate", "中島みゆき")]
    src = FakeLibrary(songs)
    idx = LibraryIndex(tmp_path / "library_index.json", src)
    data = asyncio.run(idx.build())
    assert data["count"] == len(sample_songs())
    assert data["source"] == "search3"
    assert src.page_calls == (len(songs) // 2) + 1
    assert idx.path.is_file()
    assert not (tmp_path / "library_index.json.tmp").exists()
    # rows carry the metadata the query side needs
    row = next(r for r in data["songs"] if r["id"] == "j00")
    assert row["language"] == "ja" and row["lang_source"] == "kana"
    assert row["duration"] == 200 and row["bpm"] == 70


def test_a_fresh_cache_is_reused_without_touching_the_library(tmp_path: Path) -> None:
    src = FakeLibrary(sample_songs())
    idx = LibraryIndex(tmp_path / "library_index.json", src)
    asyncio.run(idx.build())
    calls = src.page_calls
    again = asyncio.run(idx.build())
    assert again["count"] == len(sample_songs())
    assert src.page_calls == calls, "a fresh build must not re-page the NAS"
    # ensure() on a fresh cache is also a no-op
    assert asyncio.run(idx.ensure()) is True
    assert src.page_calls == calls


def test_build_falls_back_to_the_album_walk(tmp_path: Path) -> None:
    albums = [{"id": "a1", "name": "Album", "genre": "Rock",
               "songs": [song("s1", "Song One", "Band")]}]
    src = FakeLibrary([], albums=albums, fail=True)
    idx = LibraryIndex(tmp_path / "index.json", src)
    data = asyncio.run(idx.build())
    assert data["source"] == "albums"
    assert [r["id"] for r in data["songs"]] == ["s1"]
    # album metadata fills the thin song tags
    row = data["songs"][0]
    assert row["album"] == "Album" and row["genre"] == "Rock"
    assert row["language"] == "en"


def test_a_failed_build_keeps_the_old_cache_and_never_raises(tmp_path: Path) -> None:
    build(tmp_path, sample_songs())
    src = FakeLibrary([], fail=True)
    stale = LibraryIndex(tmp_path / "library_index.json", src, ttl_s=0)
    assert asyncio.run(stale.ensure()) is True          # old cache still answers
    assert stale.pick(language="ja", limit=3)
    missing = LibraryIndex(tmp_path / "missing.json", FakeLibrary([], fail=True))
    assert asyncio.run(missing.ensure()) is False


# ---------------------------------------------------------------------------
# pick
# ---------------------------------------------------------------------------
def test_pick_requires_a_cache(tmp_path: Path) -> None:
    idx = index_at(tmp_path)
    for call in (lambda: idx.pick(language="ja"), idx.stats):
        with pytest.raises(LibraryIndexMissing):
            call()
    idx.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LibraryIndexMissing):
        idx.pick(language="ja")
    assert idx.load(required=False)["missing"] is True


def test_pick_shape_filters_and_aliases(tmp_path: Path) -> None:
    idx = build(tmp_path, sample_songs())
    by_id = {r["id"]: r for r in idx.load()["songs"]}
    got = idx.pick(language="ja", limit=5, seed=7)
    assert len(got) == 5
    for row in got:
        assert {"id", "title", "artist", "album", "genre", "duration", "bpm"} <= set(row)
        assert by_id[row["id"]]["language"] == "ja"
    # the voice layer passes what the user said
    assert {r["id"] for r in idx.pick(language="日文", limit=20, seed=7)} == \
           {r["id"] for r in idx.pick(language="ja", limit=20, seed=7)}
    assert {r["id"] for r in idx.pick(language=["ja", "ko"], limit=40, seed=7)} == \
           {r["id"] for r in idx.pick(language=["ja", "ko"], limit=40, seed=7)}
    assert len(idx.pick(language=["ja", "ko"], limit=40)) == 16
    # genre is matched inside the row's genre, one-way
    assert len(idx.pick(genre="rock", limit=50)) == 10
    assert len(idx.pick(genre="J-Pop", limit=50)) == 12
    # keywords hit title, artist or album
    assert len(idx.pick(keywords="曲3", limit=50)) == 1
    assert len(idx.pick(keywords=["Eagles"], limit=50)) == 10
    assert idx.pick(keywords="no-such-thing", limit=50) == []
    # known rows can be included/excluded explicitly
    assert len(idx.pick(language="unknown", limit=50)) == 3
    assert len(idx.pick(language="ja", include_unknown=True, limit=50)) == 15


def test_filters_combine_and_relax_in_order(tmp_path: Path) -> None:
    rows = [
        *[song(f"p{i}", f"Pop {i}", "A", genre="Pop", year=1990 + i) for i in range(5)],
        *[song(f"r{i}", f"Rock {i}", "B", genre="Rock", bpm=140, year=2001)
          for i in range(2)],
        *[song(f"b{i}", f"Ballad {i}", "C", genre="Ballad", bpm=70, year=2010)
          for i in range(4)],
    ]
    idx = build(tmp_path, rows)
    # two right songs beat relaxing the filter to the whole library
    assert len(idx.pick(genre="Rock", limit=50)) == 2
    # an untagged genre must not relax into "everything": the semantic layer decides
    # whether to fall back to the source, not the index
    assert idx.pick(genre="Metal", limit=50) == []
    # genre + mood is an intersection while it can be satisfied
    calm = idx.pick(genre=["Rock", "Ballad"], moods=["calm"], limit=50)
    assert {r["genre"] for r in calm} == {"Ballad"}
    # no calm rock exists: mood relaxes first, the genre stays
    rock_calm = idx.pick(genre="Rock", moods=["calm"], limit=50)
    assert len(rock_calm) == 2
    assert {r["genre"] for r in rock_calm} == {"Rock"}
    # the year filter is real, not a vibe
    assert {r["id"] for r in idx.pick(years=(2000, 2005), limit=50)} == {"r0", "r1"}
    # a keyword is a literal match inside title/artist/album
    assert {r["id"] for r in idx.pick(keywords="Ballad", limit=50)} == \
           {"b0", "b1", "b2", "b3"}


def test_pick_limit_and_determinism(tmp_path: Path) -> None:
    idx = build(tmp_path, sample_songs())
    assert idx.pick(limit=0) == []
    assert idx.pick(limit=-3) == []
    assert len(idx.pick(limit=1)) == 1
    assert len(idx.pick(limit=1000)) == len(sample_songs())
    a = [r["id"] for r in idx.pick(limit=10, seed=7)]
    b = [r["id"] for r in idx.pick(limit=10, seed=7)]
    assert a == b
    assert [r["id"] for r in idx.pick(limit=10, seed=8)] != a
    assert [r["id"] for r in idx.pick(limit=10, seed="abc")] == \
           [r["id"] for r in idx.pick(limit=10, seed="abc")]


def test_to_tracks_prefixes_ids_and_drops_clips(tmp_path: Path) -> None:
    idx = index_at(tmp_path)
    rows = [song("a", "Long", duration=200), song("b", "Clip", duration=10)]
    tracks = idx.to_tracks(rows)
    assert [t.id for t in tracks] == ["subsonic:a"]
    assert tracks[0].genre == "" and tracks[0].bpm == 0
    # if every row is short, keep them rather than answer nothing
    only_clips = idx.to_tracks([song("c", "Clip", duration=3)])
    assert [t.id for t in only_clips] == ["subsonic:c"]


def test_stats_come_from_the_cache(tmp_path: Path) -> None:
    idx = build(tmp_path, sample_songs())
    stats = idx.stats()
    assert stats["total"] == len(sample_songs())
    assert stats["languages"]["ja"] == 12
    assert stats["languages"]["zh"] == 8
    assert stats["languages"]["en"] == 10
    assert stats["languages"]["ko"] == 4
    assert stats["languages"]["unknown"] == 3
    assert stats["genres"]["Pop"] == 8
    assert stats["cache_bytes"] > 0
    # the cache file is the documented shape
    raw = json.loads(idx.path.read_text(encoding="utf-8"))
    assert raw["version"] == li.INDEX_VERSION
    assert raw["artist_overrides"]
    assert raw["count"] == raw["version"] * 0 + len(raw["songs"])
