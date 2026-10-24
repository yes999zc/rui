"""Pinyin matching, ported from the old build's measured cases."""
from __future__ import annotations

from rui.music import query as q
from rui.session import pinyin, rules


def test_simplified_and_traditional_are_the_same_sound() -> None:
    assert pinyin.same_sound("周杰伦", "周杰倫")
    assert pinyin.same_sound("节", "结")


def test_one_wrong_syllable_is_tolerated_only_when_asked() -> None:
    assert not pinyin.same_sound("我想想", "我在想")
    assert pinyin.same_sound("我想想", "我想想", tolerance=1)


def test_the_measured_stop_word_slip() -> None:
    """「对一下吧」 for 「退下吧」 (old build, 2026-10-20); 「记下吧」 is not that."""
    assert pinyin.same_rhyme("对一下吧", "退下吧")
    assert not pinyin.same_rhyme("记下吧", "退下吧")
    assert rules.stop_intent("对一下吧")
    assert not rules.stop_intent("记下吧")


def test_a_playable_artist_survives_a_character_slip() -> None:
    """The library may hold 周杰倫 while the ASR writes 周杰伦."""
    assert q.accepts("周杰倫 - 晴天", "周杰伦")
    assert not q.accepts("陈奕迅 - 十年", "周杰伦")
