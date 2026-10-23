"""Descriptive music requests -> a filter plan, then a candidate pool.

Ported from the old build's `semantic.py` + `lang_pool`/`semantic_pool`
(tools/music.py:180-243, 664-696) and docs/library-index.md. The problem it
solves is measured: a literal search for 「放几首日文歌」 happily returned the one
track whose title contains 日文 and stopped there, hiding the Japanese shelf
behind it. A request for a *kind* of music is not a title, so it is translated
into filters instead of searched.

Changes from the old build, each deliberate:

* **Filters combine instead of excluding each other.** The old `semantic_pool`
  tried genres first and, if any genre matched, never looked at artists or
  keywords. Here language, genre, keyword, mood and year are one intersection
  (`library_index.pick`), relaxed only when the intersection is too small.
* **No model in the play path.** The old build asked DeepSeek for a JSON plan on
  every descriptive request. The vocabulary that actually reaches a speaker is
  closed and small (languages, a dozen genres, two moods, decades), so mapping it
  locally is faster, works with no API key, and cannot invent a genre the library
  does not have. The library still decides what to play.
* **Language is a song property.** When the index exists, a language ask is
  `pick(language=...)`; the old J-Pop genre tag is only the fallback, because a
  language is not a tag someone typed into a file (docs/library-index.md).
"""
from __future__ import annotations

import logging
import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .library_index import MIN_DURATION_S, LibraryIndex, mood_of
from .model import Track
from .source import MusicSource, SourceError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# vocabulary (data, not speech: nothing here is ever spoken)
# ---------------------------------------------------------------------------
# Language words -> language codes. A code is what the per-song index answers.
LANGUAGE_WORDS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("日文", "日语", "日語", "日本歌", "日系", "japanese", "jpop", "j-pop"), ("ja",)),
    (("韩文", "韩语", "韓語", "韩国歌", "korean", "kpop", "k-pop"), ("ko",)),
    (("英文", "英语", "英語", "欧美", "歐美", "english"), ("en",)),
    (("粤语", "粵語", "国语", "國語", "华语", "華語", "中文歌", "中文", "汉语",
      "普通话", "mandarin", "cantonese", "chinese"), ("zh",)),
    (("小语种", "小語種", "other"), ("other",)),
)

# The old build's language -> genre fallback (semantic._fallback + lang_pool),
# kept only for when the per-song index is not built yet.
LANGUAGE_GENRES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("日文", "日语", "日本歌", "日系"), ("J-Pop", "JPop", "Japanese")),
    (("韩文", "韩语", "韩国歌", "k-pop", "kpop"), ("K-Pop", "KPop", "Korean")),
    (("英文歌", "英文", "英语", "欧美"), ("Pop", "Rock")),
    (("粤语", "国语", "华语", "中文歌", "普通话"), ("Pop", "华语")),
)
_LANG_FALLBACK: dict[str, tuple[str, ...]] = {
    "ja": ("J-Pop", "JPop", "Japanese"),
    "ko": ("K-Pop", "KPop", "Korean"),
    "en": ("Pop", "Rock"),
    "zh": ("Pop", "华语"),
}

# Genre words -> tags to look for in a row's genre. Several spellings because
# the measured library carries both Chinese and English tags ("摇滚" and "Rock").
# The third element adds the word to the keyword filter as well: a piano request
# may be tagged Piano, or may only say 钢琴 in the title/album.
GENRE_WORDS: tuple[tuple[tuple[str, ...], tuple[str, ...], bool], ...] = (
    (("摇滚", "搖滾", "rock"), ("Rock", "摇滚"), False),
    (("民谣", "民謠", "folk"), ("Folk", "民谣"), False),
    (("电子", "電子", "electronic", "edm"), ("Electronic", "电子"), False),
    (("爵士", "jazz"), ("Jazz", "爵士"), False),
    (("古典", "classical"), ("Classical", "古典"), False),
    (("说唱", "說唱", "嘻哈", "rap", "hiphop", "hip-hop"), ("Hip-Hop", "Rap", "说唱"), False),
    (("流行", "pop"), ("Pop", "流行"), False),
    (("轻音乐", "輕音樂", "lightmusic"), ("轻音乐", "Light Music", "Classical"), False),
    (("钢琴", "鋼琴", "piano"), ("Piano", "钢琴"), True),
    (("吉他", "guitar"), ("Guitar", "吉他"), True),
    (("乡村", "country"), ("Country", "乡村"), False),
    (("蓝调", "blues"), ("Blues", "蓝调"), False),
    (("雷鬼", "reggae"), ("Reggae",), False),
    (("金属", "金屬", "metal"), ("Metal", "金属"), False),
    (("朋克", "punk"), ("Punk",), False),
    (("灵魂", "靈魂", "soul"), ("Soul",), False),
    (("原声", "soundtrack", "ost"), ("Soundtrack",), False),
)

MOOD_WORDS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("安静", "安靜", "舒缓", "舒緩", "轻柔", "輕柔", "放松", "放鬆", "治愈", "治癒",
      "睡前", "轻一点", "輕一點", "chill", "calm", "quiet"), ("calm",)),
    (("嗨", "劲爆", "勁爆", "有劲", "有勁", "燃", "热闹", "熱鬧", "欢快", "歡快",
      "蹦迪", "party"), ("hot",)),
)

# Decade words -> inclusive year range. The index stores the song/album year, so
# this is a real filter rather than a vibe.
YEAR_WORDS: tuple[tuple[tuple[str, ...], tuple[int, int]], ...] = (
    (("八十年代", "80年代", "80s", "1980s"), (1980, 1989)),
    (("九十年代", "90年代", "90s", "1990s"), (1990, 1999)),
    (("零零年代", "00年代", "2000年代"), (2000, 2009)),
    (("一零年代", "10年代", "2010年代"), (2010, 2019)),
    (("二零年代", "20年代", "2020年代"), (2020, 2029)),
)

# Leftovers that are grammar, not a topic: 「…的歌手」、「来点…」. Only whole
# tokens are listed here: single characters like 和/来/放 also start real titles,
# and strip_descriptors is allowed to shorten a residual title on purpose.
_FILLERS = (
    "这首歌", "这首", "那个", "歌曲", "首歌", "歌", "曲子", "曲", "音乐",
    "来一首", "来点", "来首", "放的", "听的", "放点", "几首", "一首", "一些",
    "一点", "老歌", "新歌", "歌手", "的", "了",
)

# Ask wrappers, for the residual keyword only: 「放点摇滚」 leaves 「放点」 once the
# genre word is removed, which is not a topic to search for.
_WRAPPERS = (
    "帮我", "麻烦", "给我", "我想要", "我想", "我要", "请", "想要", "想",
    "播放", "放点", "放", "来点", "来一首", "来首", "来", "听点", "听",
)


def _key(text: str) -> str:
    """NFKC + no spaces + casefold: 「Ｊ-ＰＯＰ」 and 「j-pop」 compare equal."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def _take(text: str, words: tuple[str, ...]) -> tuple[str, bool]:
    """Remove every word from `text` (longest first); True if any was present."""
    found = False
    out = text
    for word in sorted(words, key=len, reverse=True):
        key = _key(word)
        if key and key in out:
            out = out.replace(key, " ")
            found = True
    return out, found


def _strip_filler(text: str) -> str:
    out = text.strip()
    changed = True
    while changed and out:
        changed = False
        for word in sorted(_FILLERS, key=len, reverse=True):
            key = _key(word)
            if out.startswith(key):
                out, changed = out[len(key):], True
                break
            if out.endswith(key):
                out, changed = out[:-len(key)], True
                break
    return out.strip()


def _strip_wrappers(text: str) -> str:
    out = text.strip()
    changed = True
    while changed and out:
        changed = False
        for word in sorted(_WRAPPERS, key=len, reverse=True):
            key = _key(word)
            if out.startswith(key):
                out, changed = out[len(key):], True
                break
    return out.strip()


def language_genres(term: str) -> list[str]:
    """Genres to try for a language ask, [] when the term is not about language.

    Only the fallback path uses this (no per-song index yet); the index answers
    a language ask by the song's own language.
    """
    key = _key(term)
    for words, genres in LANGUAGE_GENRES:
        if any(_key(w) in key for w in words):
            return list(genres)
    return []


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
@dataclass
class SemanticPlan:
    """What a descriptive request asks for. Every field is a filter, not a branch."""

    raw: str = ""
    languages: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    moods: list[str] = field(default_factory=list)
    years: tuple[int, int] | None = None

    @property
    def strong(self) -> bool:
        """A real descriptor was recognised (leftover keywords alone are not)."""
        return bool(self.languages or self.genres or self.moods or self.years)

    def summary(self) -> str:
        return (f"lang={self.languages} genre={self.genres} "
                f"kw={self.keywords} mood={self.moods} years={self.years}")


def _extract_words(text: str, groups: tuple, plan: SemanticPlan,
                   attr: str) -> str:
    out = text
    for words, values in groups:
        out, found = _take(out, words)
        if found:
            for value in values:
                if value not in getattr(plan, attr):
                    getattr(plan, attr).append(value)
    return out


def plan(text: str) -> SemanticPlan | None:
    """Raw utterance -> a filter plan, or None when it is not descriptive.

    None means "this is a name, a title or random" and the literal search should
    handle it: 「Rammstein」 and 「雪の華」 must not turn into a genre pool.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    p = SemanticPlan(raw=raw)
    rest = _key(raw)
    rest = _extract_words(rest, LANGUAGE_WORDS, p, "languages")
    for words, years in YEAR_WORDS:
        rest, found = _take(rest, words)
        if found and p.years is None:
            p.years = years
    rest = _extract_words(rest, MOOD_WORDS, p, "moods")
    for words, genres, also_keyword in GENRE_WORDS:
        rest, found = _take(rest, words)
        if not found:
            continue
        for genre in genres:
            if genre not in p.genres:
                p.genres.append(genre)
        if also_keyword:
            p.keywords.append(words[0])
    rest = _strip_filler(rest.replace(" ", ""))
    rest = _strip_filler(_strip_wrappers(rest))
    if rest and 2 <= len(rest) <= 12:
        p.keywords.append(rest)
    if not p.strong:
        return None
    return p


def looks_descriptive(text: str) -> bool:
    return plan(text) is not None


def strip_descriptors(text: str) -> str:
    """Remove the descriptor words from a mixed ask, keeping a title.

    「播放日文歌曲留在我身边」 is a title *and* a descriptor; the title wins
    (old build extract_title, music.py:283-313). The caller searches the result
    before falling back to the descriptor.
    """
    rest = _key(text)
    for groups in (LANGUAGE_WORDS, GENRE_WORDS, MOOD_WORDS):
        for words, *_ in groups:
            rest, _ = _take(rest, words)
    for words, _years in YEAR_WORDS:
        rest, _ = _take(rest, words)
    return _strip_filler(rest.replace(" ", ""))


# A request that is only a mood ("来点安静的歌") must never answer "not found":
# the measured library is tag-poor (0/200 random songs had BPM), so the old build
# relaxed to "not the opposite mood" and finally to anything. Same ladder here.
_MOOD_MIN = 5


def _shuffle(tracks: list[Track], limit: int, seed: Any = None) -> list[Track]:
    rnd = random.Random(seed) if seed is not None else random.Random()
    out = list(tracks)
    rnd.shuffle(out)
    return out[:limit]


def _dedupe(tracks: list[Track]) -> list[Track]:
    seen: set[str] = set()
    out: list[Track] = []
    for track in tracks:
        if track.id and track.id not in seen:
            seen.add(track.id)
            out.append(track)
    return out


def _filter_mood(tracks: list[Track], moods: list[str]) -> list[Track]:
    if not moods:
        return tracks
    picked = [t for t in tracks if mood_of(t.genre, t.bpm) in moods]
    if len(picked) >= _MOOD_MIN:
        return picked
    # Tag-poor library: exclude the opposite mood rather than play everything.
    avoid = {"calm": "hot", "hot": "calm"}
    opposite = {avoid[m] for m in moods if m in avoid}
    relaxed = [t for t in tracks if mood_of(t.genre, t.bpm) not in opposite]
    return relaxed if len(relaxed) >= _MOOD_MIN else tracks


def _from_index(p: SemanticPlan, index: LibraryIndex | None,
                limit: int, seed: Any) -> list[Track]:
    if index is None or not index.available():
        return []
    try:
        rows = index.pick(
            language=p.languages or None,
            genre=p.genres or None,
            keywords=p.keywords or None,
            moods=p.moods or None,
            years=p.years,
            limit=max(limit * 3, 40),
            seed=seed,
        )
    except Exception as exc:  # noqa: BLE001 - a cache read must never kill a turn
        log.info("semantic: index pick failed (%s: %s)", type(exc).__name__, exc)
        return []
    return index.to_tracks(rows)


async def _from_source(p: SemanticPlan, source: MusicSource | None,
                       limit: int) -> list[Track]:
    """The no-index fallback: genre tags, literal keyword search, mood batch."""
    if source is None:
        return []
    out: list[Track] = []
    by_genre = getattr(source, "by_genre", None)
    if by_genre is not None:
        genres = list(p.genres)
        for lang in p.languages:
            genres.extend(_LANG_FALLBACK.get(lang, ()))
        for genre in _dedupe_strings(genres):
            try:
                out.extend(await by_genre(genre, limit=max(limit, 20)))
            except SourceError as exc:
                log.info("semantic: by_genre %s failed (%s)", genre, exc)
    for keyword in p.keywords:
        try:
            out.extend(await source.search(keyword, max(limit, 20)))
        except SourceError as exc:
            log.info("semantic: keyword %r failed (%s)", keyword, exc)
    out = _filter_mood(_dedupe(out), p.moods)
    return out


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = _key(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


async def _mood_random(p: SemanticPlan, source: MusicSource | None,
                       limit: int) -> list[Track]:
    if source is None or not p.moods:
        return []
    try:
        batch = await source.random(200)
    except SourceError as exc:
        log.info("semantic: mood batch failed (%s)", exc)
        return []
    return _filter_mood(batch, p.moods)[:limit]


async def pool(p: SemanticPlan | None, source: MusicSource | None = None,
               index: LibraryIndex | None = None, limit: int = 20,
               seed: Any = None) -> list[Track]:
    """A ready-to-play candidate pool for a descriptive request.

    The index answers with every filter applied; the source is used only when
    the cache does not exist yet or no indexed song matches. The result is
    shuffled so two identical requests do not play the same five songs, and
    deduplicated because a song may match both the genre and the keyword filter.
    """
    if p is None or limit <= 0:
        return []
    tracks = _from_index(p, index, limit, seed)
    if not tracks:
        # The index knows the library; the source is only for "no cache yet" or
        # "the library has no song that matches". Mixing both would add a
        # genre-tag song to two correctly classified ones.
        tracks = _dedupe(await _from_source(p, source, limit))
    if not tracks and p.moods:
        tracks = await _mood_random(p, source, limit)
    tracks = [t for t in tracks if t.duration <= 0 or t.duration >= MIN_DURATION_S]
    if not tracks:
        return []
    log.info("semantic: %r -> %s | %d tracks", p.raw, p.summary(), len(tracks))
    return _shuffle(tracks, limit, seed)
