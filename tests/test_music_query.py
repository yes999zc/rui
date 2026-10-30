"""The query pipeline: every rule here was paid for by a real mishearing in the
old build (docs/music.md §2.3)."""
from __future__ import annotations

from rui.music import query as q


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


def test_language_asks_map_to_genre_tags() -> None:
    """The old build's first answer to 「放几首日文歌」 was a genre (J-Pop), before
    its per-song language index. Ours starts there too."""
    assert q.language_genres("放几首日文歌") == ["J-Pop", "JPop", "Japanese"]
    assert q.language_genres("来点韩语歌")[0] == "K-Pop"
    assert q.language_genres("播放周杰伦的歌") == []


def test_chinese_numerals_are_numbers() -> None:
    """「三十首」 is a count, not a title. Measured 2026-10-30: 「播放三十首周杰伦。」
    planned the title 「三十首周杰伦」 and searched for it - 0 hits, every time."""
    assert q.cn_number("一") == 1
    assert q.cn_number("五") == 5
    assert q.cn_number("十") == 10
    assert q.cn_number("十一") == 11
    assert q.cn_number("十九") == 19
    assert q.cn_number("二十") == 20
    assert q.cn_number("三十") == 30
    assert q.cn_number("四十") == 40
    assert q.cn_number("五十") == 50
    assert q.cn_number("一百") == 100
    assert q.cn_number("20") == 20
    # Not a number: a name's own digits must stay a name.
    assert q.cn_number("二三") is None
    assert q.cn_number("生") is None
    assert q.cn_number("") is None


def test_the_count_is_stripped_and_the_rest_is_the_search_term() -> None:
    """The four spoken shapes, both scripts, one rule: count off, name stays."""
    # 「播放三十首周杰伦」 - the count leads the artist ask.
    p = q.plan("播放三十首周杰伦。")
    assert (p.limit, p.term) == (30, "周杰伦")
    assert p.random is False
    # 「放20首周杰伦的歌」 - digits, and the generic tail keeps the artist pool.
    p = q.plan("放20首周杰伦的歌。")
    assert (p.limit, p.artist, p.title) == (20, "周杰伦", "")
    # 「来二十首歌」 - nothing left but the song word: a random ask of 20.
    p = q.plan("来二十首歌")
    assert (p.limit, p.random) == (20, True)
    # 「听五首」 - a bare count is a random ask of that many.
    p = q.plan("听五首")
    assert (p.limit, p.random) == (5, True)
    assert q.plan("放五首").limit == 5
    assert q.plan("放十五首周杰伦").limit == 15
    assert q.plan("放三十首周杰伦的歌").artist == "周杰伦"


def test_a_huge_count_is_clamped_not_refused() -> None:
    """「一百首」 means "lots": the answer is the queue cap (schema.py:1395), never
    a crash and never 100 tracks."""
    assert q.MAX_TRACKS == 50
    assert q.plan("放一百首周杰伦").limit == 50
    assert q.plan("放99首周杰伦").limit == 50


def test_titles_that_contain_numerals_are_not_counts() -> None:
    """The guard the digits path relied on - 首 has to follow the numeral - is the
    same for both scripts."""
    assert q.split_count("一生中最爱") == (None, "一生中最爱")
    assert q.plan("放一生中最爱").limit is None
    # The wrapper no longer eats the 一 of a title: 放 + 一 + 生 is not 放一首.
    assert q.plan("放一生中最爱").term == "一生中最爱"
    assert q.plan("放三套车").limit is None
    assert q.plan("放三套车").term == "三套车"
    # A quoted name wins outright: 《三首歌》 is the song, not an order for three.
    assert q.plan("放《三首歌》").limit is None
    assert q.plan("帮我放首晴天吧").limit is None
