"""Per-song language / genre index for the home library, built once and cached.

Why this exists (old build `server/library_index.py`, docs/library-index.md):
「放几首日文歌」 used to mean `getSongsByGenre("J-Pop")` - 16 tagged songs out of
5738, so every Japanese track whose file was not tagged J-Pop, and every kana
title filed under Pop, was invisible. A language is a property of the *song*, not
of a tag someone typed into the file. So this module decides it once per song
from the song's own metadata (title / artist / album / genre), caches it, and
answers later requests without touching the network.

Two halves, like the old build:
  `build()`  - walks the library once (network) and writes the cache atomically
  `pick()`   - answers from the cache; never touches the network

The old build's measured rules are kept verbatim: the title outranks the artist,
the album ranks last, kana/hangul win outright, `・`/`ー` are punctuation rather
than kana, and latin-only titles default to `en` (not `unknown`, which would make
「放几首英文歌」 return nothing). The known weakness is honest: kanji-only
Japanese and Chinese are indistinguishable, so rule 5 says `zh` at 0.6 and the
artist override table (stored inside the cache file, editable without code) is
the fix for the artists we actually own.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import Track
from .source import MusicSource, SourceError

log = logging.getLogger(__name__)

INDEX_VERSION = 1
# Stale after a week -> the next ensure() rebuilds it; pick() keeps serving the old
# one, because a voice request must not wait on the NAS to be re-indexed.
CACHE_TTL_S = 7 * 24 * 3600
PAGE_SIZE = 500
# Old build's clip filter (tools/music.py:395): short clips pollute a radio batch.
MIN_DURATION_S = 45

LANGUAGES = ("ja", "ko", "zh", "en", "other", "unknown")

# ---------------------------------------------------------------------------
# script detection
# ---------------------------------------------------------------------------
# Ranges, not unicodedata names: a title is short and the categories we care
# about are script blocks. ・ (U+30FB) and ー (U+30FC) sit inside the katakana
# block but are punctuation - counting them made 张学友「你的名字・我的姓氏」Japanese.
_KANA = re.compile("[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fd-\u30ff\uff66-\uff9d]")
_HANGUL = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\ua960-\ua97f\ud7b0-\ud7ff]")
_HAN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
_LATIN = re.compile("[A-Za-z\u00c0-\u024f\u1e00-\u1e9f]")
_OTHER_SCRIPTS = {
    "cyrillic": re.compile("[\u0400-\u052f]"),
    "thai": re.compile("[\u0e00-\u0e7f]"),
    "arabic": re.compile("[\u0600-\u06ff\u0750-\u077f]"),
    "hebrew": re.compile("[\u0590-\u05ff]"),
    "greek": re.compile("[\u0370-\u03ff\u1f00-\u1fff]"),
    "devanagari": re.compile("[\u0900-\u097f]"),
    "armenian": re.compile("[\u0530-\u058f]"),
    "georgian": re.compile("[\u10a0-\u10ff]"),
    "tamil": re.compile("[\u0b80-\u0bff]"),
}


def scripts(text: str) -> set[str]:
    """Which writing systems appear in `text`."""
    out: set[str] = set()
    if not text:
        return out
    if _KANA.search(text):
        out.add("kana")
    if _HANGUL.search(text):
        out.add("hangul")
    if _HAN.search(text):
        out.add("han")
    if _LATIN.search(text):
        out.add("latin")
    for name, rx in _OTHER_SCRIPTS.items():
        if rx.search(text):
            out.add(name)
    return out


# ---------------------------------------------------------------------------
# artist overrides - stored in the cache JSON, editable without a code change
# ---------------------------------------------------------------------------
# Keys are artist names as Subsonic spells them; values are language codes. This
# is artist-level, not lyric-level: 宇多田ヒカル "First Love" is filed `ja` even
# though the words are English, because 「放几首日文歌」 means that shelf.
# Measured against the same NAS the old build indexed.
ARTIST_OVERRIDES: dict[str, str] = {
    # Japanese acts with kanji-only titles, which rule 5 would call Chinese.
    "中島みゆき": "ja",
    "中島美嘉": "ja",
    "谷村新司": "ja",
    "高橋洋子": "ja",
    "玉置浩二": "ja",
    "宇多田ヒカル": "ja",
    "米津玄師": "ja",
    "サザンオールスターズ": "ja",
    "喜納昌吉&チャンプルーズ": "ja",
    "青山テルマ": "ja",
    "松原みき": "ja",
    "近藤真彦": "ja",
    "山口百恵": "ja",
    "KOKIA": "ja",
    "DAISHI DANCE": "ja",
    # Chinese / Cantonese acts with romanised names.
    "S.H.E": "zh",
    "S.H.E.": "zh",
    "F.I.R.飞儿乐团": "zh",
    "G.E.M.邓紫棋": "zh",
    "Beyond": "zh",
    "GALA": "zh",
    "陈婧霏": "zh",
    "岑宁儿": "zh",
    "群星": "zh",
    # Western / English acts.
    "Rammstein": "en",
    "Guns N' Roses": "en",
    "Westlife": "en",
    "Linkin Park": "en",
    "Beyoncé": "en",
    "Adele": "en",
    "Celine Dion": "en",
    "Bon Jovi": "en",
    "Coldplay": "en",
    "Eagles": "en",
    "Alan Walker": "en",
    "Gregorian": "en",
    "Carpenters": "en",
}

# Genres that in practice mean "western, English-singing" in this library. Only
# used to raise confidence on a latin-only title, never to overrule a script: a
# Chinese song tagged Pop stays zh because its title is CJK.
EN_GENRES: tuple[str, ...] = (
    "pop", "rock", "pop-rock", "rock music", "britpop", "英伦摇滚", "indie",
    "alternative", "alternative & indie", "hip-hop", "rap", "r&b", "soul",
    "dance", "house", "techno", "electronic", "électronique", "metal", "punk",
    "folk", "country", "blues", "jazz", "classical", "soundtrack", "world",
    "a cappella", "international", "goa",
)

CONF = {
    "override": 0.92,
    "kana": 0.95,
    "hangul": 0.90,
    "hangul_mixed": 0.60,   # hangul + han in one title (林俊杰「사랑해요只对你说」)
    "cjk_title": 0.60,      # kanji-only titles are indistinguishable from Chinese
    "cjk_artist": 0.45,     # only the artist is CJK, the title is latin
    "latin_genre": 0.80,
    "latin": 0.50,
    "other_script": 0.70,
    "none": 0.0,
}


def _norm_artist(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def _norm_genre(genre: str | None) -> str:
    return re.sub(r"\s+", " ", (genre or "").strip()).casefold()


_EN_GENRES_SET = frozenset(_norm_genre(g) for g in EN_GENRES)


def classify(title: str, artist: str = "", album: str = "", genre: str = "",
             overrides: dict[str, str] | None = None) -> tuple[str, str, float]:
    """Guess one song's language. Returns (language, lang_source, confidence).

    Order matters and is documented in docs/library-index.md:
      1. artist override map                        (exact, normalised)
      2. kana anywhere                              -> ja
      3. hangul anywhere                            -> ko (lower conf if han too)
      4. a non-latin, non-CJK script in the *title* -> other
      5. han in the title (or only in the artist)   -> zh, deliberately low conf
      6. latin title                               -> en, higher conf if genre agrees
      7. nothing above                             -> unknown
    The title outranks the artist on purpose; the album ranks last (a CJK
    compilation name must not turn an English act Chinese).
    """
    ov = ARTIST_OVERRIDES if overrides is None else overrides
    key = _norm_artist(artist)
    if key:
        for name, lang in ov.items():
            if _norm_artist(name) == key:
                return lang, "override", CONF["override"]

    title_s = scripts(title)
    artist_s = scripts(artist)
    album_s = scripts(album)
    everything = title_s | artist_s | album_s

    if "kana" in everything:
        return "ja", "kana", CONF["kana"]
    if "hangul" in everything:
        mixed = "han" in title_s or "han" in artist_s
        return "ko", "hangul", CONF["hangul_mixed"] if mixed else CONF["hangul"]

    others = (title_s - {"latin"}) & set(_OTHER_SCRIPTS)
    if others:
        return "other", "script:" + sorted(others)[0], CONF["other_script"]

    if "han" in title_s:
        return "zh", "cjk", CONF["cjk_title"]
    if "han" in artist_s:
        return "zh", "cjk", CONF["cjk_artist"]
    if "latin" in title_s:
        if _norm_genre(genre) in _EN_GENRES_SET:
            return "en", "latin_genre", CONF["latin_genre"]
        return "en", "latin", CONF["latin"]
    if "latin" in artist_s:
        return "en", "latin", CONF["latin"] - 0.2
    return "unknown", "none", CONF["none"]


# ---------------------------------------------------------------------------
# mood: BPM first, genre keywords second (old build music.py:408-431)
# ---------------------------------------------------------------------------
# A tag-poor library still has to answer 「来点安静的歌」: the caller relaxes to
# "not the opposite mood" and finally to anything, never to an empty list.
CALM_GENRES = ("ballad", "acoustic", "folk", "jazz", "classical", "piano", "ambient",
               "lofi", "民谣", "轻音乐", "钢琴", "古典", "acoustic")
HOT_GENRES = ("rock", "metal", "punk", "electronic", "dance", "hip", "rap", "house",
              "摇滚", "金属", "电子", "舞曲")


def mood_of(genre: str = "", bpm: int = 0) -> str:
    """'calm' | 'hot' | '' - mid-tempo and untagged songs are not a mood signal."""
    if bpm > 0:
        return "calm" if bpm <= 92 else ("hot" if bpm >= 118 else "")
    tag = (genre or "").casefold()
    if any(g in tag for g in HOT_GENRES):
        return "hot"
    if any(g in tag for g in CALM_GENRES):
        return "calm"
    return ""


class LibraryIndexMissing(RuntimeError):
    """No usable cache: the caller must build() before asking for songs."""


class LibraryIndex:
    """The index file plus the small API the voice layer uses.

    `source` is only needed for `build()`: `pick()`/`stats()` must keep working
    with the NAS unplugged, which is the whole point of caching the answer.
    """

    def __init__(self, path: Path, source: MusicSource | None = None,
                 ttl_s: float = CACHE_TTL_S,
                 overrides: dict[str, str] | None = None) -> None:
        self.path = path
        self.source = source
        self.ttl_s = ttl_s
        self.overrides = dict(ARTIST_OVERRIDES if overrides is None else overrides)
        self._data: dict[str, Any] | None = None
        self._lock = None   # created lazily: an asyncio.Lock at import ties us to one loop

    # ------------------------------------------------------------------ storage
    def _read(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("songs"), list):
            return None
        return raw

    def load(self, required: bool = True) -> dict[str, Any]:
        """The cached index. Raises when missing unless the caller can degrade."""
        if self._data is not None:
            return self._data
        data = self._read()
        if data is None:
            if not required:
                return {"version": INDEX_VERSION, "songs": [], "count": 0,
                        "artist_overrides": dict(self.overrides), "missing": True}
            raise LibraryIndexMissing(
                f"library index cache missing or unreadable: {self.path} "
                "- call build() to create it")
        self._data = data
        return data

    def available(self) -> bool:
        try:
            self.load()
        except LibraryIndexMissing:
            return False
        return True

    def is_fresh(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        return age < self.ttl_s

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)   # atomic: a reader never sees half a file
        os.chmod(self.path, 0o600)

    # ------------------------------------------------------------------ build
    def _async_lock(self) -> Any:
        if self._lock is None:
            import asyncio

            self._lock = asyncio.Lock()
        return self._lock

    async def ensure(self, force: bool = False) -> bool:
        """Build when the cache is missing or older than the TTL. Never raises.

        A failed rebuild keeps serving the old cache: the NAS being slow is not a
        reason to answer 「曲库里没找到」.
        """
        if not force and self.is_fresh():
            return True
        try:
            await self.build(force=force)
            return True
        except (SourceError, OSError, ValueError) as exc:
            log.info("library index: build failed (%s: %s)", type(exc).__name__, exc)
            return self.available()

    async def build(self, force: bool = False) -> dict[str, Any]:
        """(Re)build the cache. Returns the index dict; TTL reuse unless force."""
        async with self._async_lock():
            if not force and self.is_fresh():
                data = self._read()
                if data is not None:
                    self._data = data
                    return data
            started = time.time()
            songs, how = await self._fetch_songs()
            albums = await self._album_meta()
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for song in songs:
                row = self._row(song, albums)
                if row is None or row["id"] in seen:
                    continue
                seen.add(row["id"])
                rows.append(row)
            rows.sort(key=lambda r: str(r["id"]))
            data = {
                "version": INDEX_VERSION,
                "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "built_ts": int(time.time()),
                "build_seconds": round(time.time() - started, 2),
                "source": how,
                "count": len(rows),
                "artist_overrides": dict(self.overrides),
                "en_genres": list(EN_GENRES),
                "songs": rows,
            }
            self._write(data)
            self._data = data
            log.info("library index: %d songs via %s in %.2fs -> %s",
                     len(rows), how, time.time() - started, self.path)
            return data

    async def _fetch_songs(self) -> tuple[list[dict[str, Any]], str]:
        """Every song, cheapest first: search3 paging, then the album walk."""
        page = getattr(self.source, "songs_page", None)
        if self.source is not None and page is not None:
            songs: list[dict[str, Any]] = []
            offset = 0
            try:
                while True:
                    batch = await page(offset, PAGE_SIZE)
                    songs.extend(batch)
                    if len(batch) < PAGE_SIZE:
                        break
                    offset += PAGE_SIZE
            except SourceError as exc:
                log.info("library index: search3 paging failed (%s); trying albums", exc)
                songs = []
            if songs:
                return songs, "search3"
        walk = await self._walk_albums()
        if walk:
            return walk, "albums"
        raise SourceError("library returned no songs")

    async def _walk_albums(self) -> list[dict[str, Any]]:
        albums_page = getattr(self.source, "albums_page", None)
        album_songs = getattr(self.source, "album_songs", None)
        if albums_page is None or album_songs is None:
            return []
        songs: list[dict[str, Any]] = []
        offset = 0
        try:
            while True:
                batch = await albums_page(offset, PAGE_SIZE)
                for album in batch:
                    aid = str(album.get("id") or "")
                    if not aid:
                        continue
                    for song in await album_songs(aid):
                        if not song.get("album"):
                            song["album"] = album.get("name")
                        if not song.get("genre"):
                            song["genre"] = album.get("genre")
                        if not song.get("year"):
                            song["year"] = album.get("year")
                        songs.append(song)
                if len(batch) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
        except SourceError as exc:
            log.info("library index: album walk failed (%s)", exc)
            return songs
        return songs

    async def _album_meta(self) -> dict[str, dict[str, Any]]:
        """albumId -> {name, genre, year}, for rows whose song tags are thin.

        579 albums on the measured library = 2 requests; failing here only means
        the song's own tags are used, never a failed build.
        """
        albums_page = getattr(self.source, "albums_page", None)
        if albums_page is None:
            return {}
        out: dict[str, dict[str, Any]] = {}
        offset = 0
        try:
            while True:
                batch = await albums_page(offset, PAGE_SIZE)
                for album in batch:
                    aid = str(album.get("id") or "")
                    if aid:
                        out[aid] = {"name": album.get("name"),
                                    "genre": album.get("genre"),
                                    "year": album.get("year")}
                if len(batch) < PAGE_SIZE:
                    return out
                offset += PAGE_SIZE
        except SourceError as exc:
            log.info("library index: album metadata failed (%s)", exc)
            return out

    def _row(self, song: dict[str, Any],
             albums: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        sid = str(song.get("id") or "")
        if not sid:
            return None
        title = str(song.get("title") or "")
        artist = str(song.get("artist") or song.get("displayArtist") or "")
        meta = albums.get(str(song.get("albumId") or "")) or {}
        album = str(song.get("album") or meta.get("name") or "")
        genre = str(song.get("genre") or meta.get("genre") or "")
        year = song.get("year") or meta.get("year")
        lang, source, conf = classify(title, artist, album, genre, self.overrides)
        return {
            "id": sid,
            "title": title,
            "artist": artist,
            "album": album,
            "genre": genre,
            "year": _year_int(year),
            "duration": int(song.get("duration") or 0),
            "bpm": _int(song.get("bpm")),
            "language": lang,
            "lang_source": source,
            "lang_conf": conf,
        }

    # ------------------------------------------------------------------ query
    def pick(self, language: str | list[str] | None = None,
             genre: str | list[str] | None = None,
             keywords: str | list[str] | None = None,
             moods: str | list[str] | None = None,
             years: tuple[int, int] | None = None,
             limit: int = 20, seed: Any = None,
             include_unknown: bool = False) -> list[dict[str, Any]]:
        """Choose candidate songs from the index. Deterministic for a given seed.

        Every argument is a *filter*, not a branch: language, genre, keyword, mood
        and year apply together. When their intersection is empty, the weakest
        filter is dropped and the query retried in this order - mood, genre, year,
        keywords - so 「日文摇滚」 with two tagged hits plays those two, while a
        genre the library never tagged falls back to the language filter. A
        non-empty intersection always wins over a bigger pool: two right songs
        beat fifty random ones. Language is never dropped.

        genre/keywords/moods accept one value or a list; a list means "any of".
        genre is matched inside the row's genre, so `rock` hits Rock and Pop-Rock
        while `j-pop` does not hit the whole Pop shelf.
        """
        limit = int(limit)
        if limit <= 0:
            return []
        data = self.load()
        rows = data.get("songs") or []
        want_langs = _want_langs(language)
        active: dict[str, Any] = {
            "genre": genre, "keywords": keywords, "moods": moods, "years": years,
        }
        matched: list[dict[str, Any]] = []
        if want_langs is None and not any(active.values()):
            matched = list(rows)   # no filter at all: the whole index is the pool
        else:
            # Drop the weakest signal first. Keywords are the most literal (a title
            # or artist word) and language the strongest, so they are relaxed last.
            # Never relax into "everything": with no language filter, dropping the
            # last filter would turn 「来点摇滚」 into a random batch when the library
            # has no rock tag - an honest empty pool is better, and the caller says so.
            for drop in (None, "moods", "genre", "years", "keywords"):
                if drop:
                    active.pop(drop, None)
                if want_langs is None and not any(active.values()):
                    break
                matched = [r for r in rows
                           if _match_lang(r, want_langs, include_unknown)
                           and _match_row(r, active)]
                if matched or drop == "keywords":
                    break
        matched.sort(key=lambda r: str(r.get("id") or ""))
        rnd = random.Random(seed) if seed is not None else random.Random()
        rnd.shuffle(matched)
        return [_public(r) for r in matched[:limit]]

    # ------------------------------------------------------------------ helpers
    def to_tracks(self, rows: list[dict[str, Any]],
                  limit: int | None = None) -> list[Track]:
        """Public index rows -> playable Tracks, ids prefixed like the source's."""
        tracks: list[Track] = []
        for row in rows:
            sid = str(row.get("id") or "")
            if not sid:
                continue
            tracks.append(Track(
                id=sid if sid.startswith("subsonic:") else f"subsonic:{sid}",
                title=str(row.get("title") or ""),
                artist=str(row.get("artist") or ""),
                duration=_int(row.get("duration")),
                genre=str(row.get("genre") or ""),
                bpm=_int(row.get("bpm")),
            ))
        good = [t for t in tracks if t.duration >= MIN_DURATION_S]
        batch = good if (good or not tracks) else tracks
        return batch[:limit] if limit is not None else batch

    def stats(self) -> dict[str, Any]:
        """Counts per language, per source and per genre, plus cache age/size."""
        data = self.load()
        rows = data.get("songs") or []
        langs = {k: 0 for k in LANGUAGES}
        sources: dict[str, int] = {}
        genres: dict[str, list[Any]] = {}
        for row in rows:
            lang = str(row.get("language") or "unknown")
            langs[lang] = langs.get(lang, 0) + 1
            src = str(row.get("lang_source") or "none")
            sources[src] = sources.get(src, 0) + 1
            raw = str(row.get("genre") or "").strip()
            key = _norm_genre(raw) or "(untagged)"
            slot = genres.setdefault(key, [raw or "(untagged)", 0])
            slot[1] += 1
            if raw and len(raw) < len(str(slot[0])):
                slot[0] = raw   # prefer the shortest spelling: Pop > POP
        try:
            size = self.path.stat().st_size
            age = int(time.time() - self.path.stat().st_mtime)
        except OSError:
            size, age = 0, None
        return {
            "total": len(rows),
            "built_at": data.get("built_at"),
            "build_seconds": data.get("build_seconds"),
            "cache_bytes": size,
            "cache_age_seconds": age,
            "languages": langs,
            "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])),
            "genres": {display: count for display, count in
                       sorted(genres.values(), key=lambda x: -x[1])},
        }


# ---------------------------------------------------------------------------
# row matching / public shape
# ---------------------------------------------------------------------------
_LANG_ALIASES = {
    "ja": "ja", "japanese": "ja", "jp": "ja", "日文": "ja", "日语": "ja", "日語": "ja",
    "日本": "ja",
    "ko": "ko", "korean": "ko", "kr": "ko", "韩文": "ko", "韩语": "ko", "韓文": "ko",
    "zh": "zh", "chinese": "zh", "cn": "zh", "mandarin": "zh", "cantonese": "zh",
    "中文": "zh", "国语": "zh", "华语": "zh", "粤语": "zh", "粵語": "zh",
    "en": "en", "english": "en", "英文": "en", "英语": "en",
    "other": "other", "unknown": "unknown",
}


def _want_langs(language: str | list[str] | None) -> set[str] | None:
    if language is None:
        return None
    items = [language] if isinstance(language, str) else list(language)
    out: set[str] = set()
    for item in items:
        key = str(item).strip().casefold()
        if key:
            out.add(_LANG_ALIASES.get(key, key))
    return out or None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    return [str(v).strip() for v in items if str(v).strip()]


def _match_lang(row: dict[str, Any], want: set[str] | None,
                include_unknown: bool) -> bool:
    if want is None:
        return True
    lang = str(row.get("language") or "unknown")
    if lang in want:
        return True
    return bool(include_unknown and lang == "unknown")


def _match_row(row: dict[str, Any], active: dict[str, Any]) -> bool:
    genre = active.get("genre")
    if genre:
        have = _norm_genre(row.get("genre"))
        if not have or not any(_norm_genre(g) in have for g in _as_list(genre)):
            return False
    keywords = active.get("keywords")
    if keywords:
        blob = " ".join(str(row.get(k) or "") for k in ("title", "artist", "album"))
        blob = blob.casefold()
        if not any(w.casefold() in blob for w in _as_list(keywords)):
            return False
    moods = active.get("moods")
    if moods:
        mood = mood_of(str(row.get("genre") or ""), _int(row.get("bpm")))
        if mood not in _as_list(moods):
            return False
    years = active.get("years")
    if years:
        year = row.get("year")
        if not isinstance(year, int) or not (int(years[0]) <= year <= int(years[1])):
            return False
    return True


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """The shape the tools/player already expect (old build's `_public`)."""
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or ""),
        "album": str(row.get("album") or ""),
        "genre": str(row.get("genre") or ""),
        "duration": _int(row.get("duration")),
        "bpm": _int(row.get("bpm")),
    }


def _year_int(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 2999 else None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
