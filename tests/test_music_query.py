"""The query pipeline: every rule here was paid for by a real mishearing in the
old build (docs/music.md §2.3)."""
from __future__ import annotations

from house_ear.music import query as q


def test_wrappers_are_stripped() -> None:
    assert q.clean_query("帮我放首晴天吧") == "晴天"
    assert q.clean_query("放周杰伦的晴天") == "周杰伦的晴天"
    # 「音乐/歌」 itself is the random intent: the whole ask collapses to empty.
    assert q.clean_query("我想听点音乐") == ""
    assert q.clean_query("来一首歌") == ""


def test_random_intents_go_radio() -> None:
    assert q.plan("放首歌").random is True
    assert q.plan("随便放点歌").random is True
    assert q.plan("").random is True
    assert q.plan("放周杰伦的晴天").random is False


def test_artist_and_title_split() -> None:
    p = q.plan("帮我放周杰伦的晴天")
    assert (p.artist, p.title) == ("周杰伦", "晴天")
    p2 = q.plan("播放战车乐队的歌")
    # 「战车乐队的歌」: head 战车乐队 (4 chars) is a name, tail 歌 keeps the artist pool
    assert p2.artist == "战车乐队"


def test_quoted_title_wins_over_descriptions() -> None:
    p = q.plan("放点安静的《晴天》")
    assert p.title == "晴天"


def test_accepts_guards_against_subsonic_fuzz() -> None:
    # The old build's measured case: 「留在我身边」 answered by 《Without Me》.
    assert q.accepts("晴天 - 周杰伦", "晴天") is True
    assert q.accepts("Without Me - 谢霆锋", "留在我身边") is False
    assert q.accepts("晴天 live 版", "晴天") is True


def test_the_song_word_survives_asr_slips() -> None:
    """Measured 09:50:27: 〈周杰伦的歌〉 arrived as 〈…的节〉."""
    assert q.plan("播放林俊杰的节").artist == "林俊杰"
    assert q.plan("播放周杰伦的结").artist == "周杰伦"


def test_a_spoken_full_stop_does_not_become_a_title() -> None:
    """Measured 10:19:33: 「播放战车乐队的歌。」 planned title 「歌。」 and searched
    for that song. Sentence punctuation is stripped; 《》 still mark a title."""
    assert q.plan("播放战车乐队的歌。").artist == "战车乐队"
    assert q.plan("播放战车乐队的歌。").title == ""
    assert q.plan("播放周杰伦的歌！").artist == "周杰伦"
    assert q.plan("放点安静的《晴天》").title == "晴天"
