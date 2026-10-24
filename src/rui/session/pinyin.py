"""Pinyin comparison for ASR slips - the old build's answer to homophones.

Ported from `assistant_v3.py:1969-2085` (xiaoai-voice-entry), where it solved two
measured problems and failed at nothing else:

* **Our own voice coming back.** The array hears our playback through the echo
  gate; `_edit(_py(t), _py(said)) <= 1` recognised 「我想想」 returning as
  「我想想」 with one syllable wrong.
* **Stop words said with a slipped initial.** 「退下吧」 arrived as 「对一下吧」.
  Whole-pinyin distance was too loose there (「记下吧」 is two edits away), so the
  old build compared *finals*: ui/ia/a vs i/ia/a keeps dui-xia-ba and drops
  ji-xia-ba.

It also covers a case this build has and the old one did not: the library stores
traditional characters (周杰倫) while the ASR writes simplified (周杰伦). Both
have the same pinyin, so an artist match can be made on sound instead of bytes.
"""
from __future__ import annotations

import functools


@functools.lru_cache(maxsize=4096)
def syllables(text: str) -> tuple[str, ...]:
    """Pinyin syllables of the han characters; empty when pypinyin is absent."""
    try:
        from pypinyin import lazy_pinyin
    except Exception:  # noqa: BLE001 - pinyin is a nicety, not a dependency
        return ()
    return tuple(lazy_pinyin(text, errors="ignore"))


@functools.lru_cache(maxsize=4096)
def finals(text: str) -> tuple[str, ...]:
    """The 韵母 of each syllable - the part an ASR rarely gets wrong."""
    try:
        from pypinyin import Style, lazy_pinyin
    except Exception:  # noqa: BLE001
        return ()
    return tuple(lazy_pinyin(text, style=Style.FINALS, errors="ignore"))


def edit_distance(a: tuple[str, ...] | list[str],
                  b: tuple[str, ...] | list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def same_sound(a: str, b: str, *, tolerance: int = 0) -> bool:
    """True when two different strings share their pinyin sequence.

    Zero tolerance is exact homophony (周杰伦 / 周杰倫, 节 / 结); 1 allows one
    syllable to differ (the old build's echo rule).
    """
    if not a or not b or a == b:
        return a == b
    pa, pb = syllables(a), syllables(b)
    if not pa or not pb:
        return False
    return edit_distance(pa, pb) <= tolerance


def contains_sound(candidate: str, want: str) -> bool:
    """True when `want`'s syllables appear contiguously inside `candidate`'s.

    The artist case: candidate 「周杰倫 - 晴天」 (zhou jie lun qing tian) contains
    the wanted 「周杰伦」 (zhou jie lun) even though the characters differ.
    """
    pc, pw = syllables(candidate), syllables(want)
    if not pc or not pw or len(pw) > len(pc):
        return False
    return any(pc[i:i + len(pw)] == pw for i in range(len(pc) - len(pw) + 1))


def same_rhyme(a: str, b: str) -> bool:
    """Finals comparison with a one-syllable insertion allowed.

    「对一下吧」 vs 「退下吧」: fa = uo/ia/a, fb = ui/ia/a is not equal, but the
    measured slip 「对一下吧」 carries an extra syllable - strip any one and the
    rest must match. 「记下吧」 (i/ia/a) fails both checks.
    """
    fa, fb = finals(a), finals(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return a != b
    if len(fa) == len(fb) + 1:
        return any(fa[:i] + fa[i + 1:] == fb for i in range(len(fa)))
    return False
