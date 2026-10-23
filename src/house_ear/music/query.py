"""The query pipeline: raw utterance in, QueryPlan out.

Ported from the old build's `tools/music.py` (clean_query / extract_title /
_accepts), where each rule was paid for by a real mishearing: the wrappers because
the model forwards 「帮我放一首...」 verbatim, the title extraction because a
description plus a name must play the name, and `accepts` because Subsonic's fuzzy
search happily answered 「留在我身边」 with 谢霆锋《Without Me》.
"""
from __future__ import annotations

import re

from ..session import pinyin
from . import semantic
from .model import QueryPlan

# Wrappers people put around a song ask. Old build clean_query (music.py:267-282).
# Every alternative is anchored: an unanchored 「歌」 alternative once ate the tail of
# 「战车乐队的歌」 and turned an artist ask into a name ask.
_WRAP = re.compile(
    r"^(?:"
    r"(?:请|帮我|麻烦|给我|我要|我想|想要?|想)"          # 我想要…（听 belongs to the verb below）
    r"|(?:播放|放|来|听)一?[点首些下]?"                    # 放 / 来一首 / 听点
    r"|(?:几首|一首|一点)"                                 # bare measure words
    r"|(?:曲库里?|音乐|歌)(?:里)?的?"                      # the topic itself
    r")"
)
_TAIL = re.compile(r"[的吧啊呗嘞呀哦了]$")
# Sentence punctuation on either edge; 《》 deliberately excluded (a quoted title).
_SENT_PUNCT = re.compile(r"^[，。！？；：、,.!?;:…\s]+|[，。！？；：、,.!?;:…\s]+$")
_PUNCT = re.compile(r"[\s，。、！？；：,.!?;:…—《》「」‘’’\"（）()]")


# Measured ASR slips in music asks: 「周杰伦的歌」 heard as 「…的节」/「…的结」.
_MUSIC_WORD = re.compile(r"的[节结接]$")


def normalize(text: str) -> str:
    return _PUNCT.sub("", text or "")


def clean_query(q: str) -> str:
    """Strip the wrappers a spoken request arrives in: 「帮我放首X吧」 -> 「X」."""
    # ASR often keeps a full stop: 「播放战车乐队的歌。」 split into artist 战车乐队
    # and title 「歌。」, which searched for a song called 歌。 (measured 10:19:33).
    # Only sentence punctuation is stripped - 《》 still mark a title.
    t = _SENT_PUNCT.sub("", q.strip())
    t = _MUSIC_WORD.sub("的歌", t)
    changed = True
    while changed and t:
        changed = False
        for _ in range(3):
            stripped = _WRAP.sub("", t).strip()
            if stripped != t:
                t, changed = stripped, True
        tail = _TAIL.sub("", t).strip()
        if tail != t:
            t, changed = tail, True
    return t


_QUOTED = re.compile(r"[《「『]([^》」』]+)[》」』]")


def has_quoted_title(text: str) -> bool:
    """True when the user quoted a name: 《》/「」 mark a title, not a genre word."""
    return bool(_QUOTED.search(text or ""))


def extract_title(q: str) -> str:
    """《》「」quoted names win outright; otherwise strip the descriptive words.

    A mixed ask keeps its title: 「播放日文歌曲留在我身边」 must search
    「留在我身边」 rather than play a J-Pop shuffle and drop the name the user
    said (old build music.py:283-313). Only when a descriptor was actually
    removed are the leftover 「歌曲/歌」 edges cleaned - 「周杰伦的歌」 is an
    artist ask and must stay whole for split_artist.
    """
    m = _QUOTED.search(q)
    if m:
        return m.group(1).strip()
    t = clean_query(q)
    if not semantic.looks_descriptive(t):
        return t
    return semantic.strip_descriptors(t)


# A tail that is just "songs" says "this artist's songs", not "a song called 歌".
_GENERIC_TAIL = ("歌", "歌曲", "的歌", "音乐", "的歌吧", "的歌曲")


def split_artist(term: str) -> tuple[str, str]:
    """「周杰伦的晴天」 -> (周杰伦, 晴天); head must look like a name (2-12 chars).

    Old build `_artist_hint` (music.py:320-327): shorter is noise, longer is a
    sentence, and both make worse searches than the bare term. A generic tail
    (「周杰伦的歌」) means the artist alone - searching the title 「歌」 found
    nothing (measured 09:20:59).
    """
    if "的" not in term:
        return "", term
    head, _, tail = term.partition("的")
    if not (2 <= len(head) <= 12) or not tail.strip():
        return "", term
    head, tail = head.strip(), tail.strip()
    if tail in _GENERIC_TAIL:
        return head, ""
    return head, tail


def accepts(candidate: str, want: str) -> bool:
    """A search hit only counts if the asked-for words are actually in it.

    Subsonic ranks by token overlap, so 「Stay With Me」 is a fine answer to
    「留在我身边」 by its own scoring. Containment of the normalized ask is the
    old build's guard (music.py:330-343) and remains the cheapest honest one.

    Sound is the second guard: the library may store 周杰倫 while the ASR writes
    周杰伦, and 周杰伦 vs 周杰輪 is one homophone - containment of characters
    alone would drop a correct hit.
    """
    w, c = normalize(want), normalize(candidate)
    if not w:
        return True
    if w in c:
        return True
    return pinyin.contains_sound(c, w)


# Language asks have no title or artist to search: the old build's first answer was
# a genre tag (J-Pop: 16 songs of 5738), later replaced by a per-song language index
# (`music/library_index.py`). The genre mapping survives only as the fallback used
# when the index has not been built yet - it lives in `semantic.py` so the voice
# layer and the index agree on what 「日文」 means.
def language_genres(term: str) -> list[str]:
    """Genres to try for a language ask, [] when the term is not about language."""
    return semantic.language_genres(term)


def plan(raw: str) -> QueryPlan:
    """Raw utterance -> what to search for. The order is the priority."""
    cleaned = clean_query(raw)
    if not cleaned:
        return QueryPlan(raw=raw, random=True)
    if any(w in cleaned for w in ("随便", "随机", "来点歌", "来点音乐", "放点歌", "放歌")) \
            and len(cleaned) <= 8:
        return QueryPlan(raw=raw, random=True)
    title = extract_title(cleaned)
    artist, rest = split_artist(title)
    if artist and rest:
        return QueryPlan(raw=raw, title=rest, artist=artist, term=f"{rest} {artist}")
    if artist:
        # 《周杰伦的歌》 - the artist's pool, no title to match.
        return QueryPlan(raw=raw, artist=artist, term=artist)
    # 「周杰伦歌曲」 / 「陈奕迅歌」 without 的: the same artist-pool ask.
    m = re.match(r"^(.{2,12}?)(?:歌曲|的歌|歌)$", title)
    if m:
        return QueryPlan(raw=raw, artist=m.group(1), term=m.group(1))
    return QueryPlan(raw=raw, title=title, term=title or cleaned)
