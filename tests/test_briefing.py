"""The daily briefing passthrough: script shape, history, 「第 N 条」 refs.

D1c ④'s pure half. The old build's measured contract (docs/d1c-notes.md §7):
the script is spoken **as written**, the model only gets a "ready" notice, and the
verbatim script goes into history so 「第三条再说说」 still works next turn.
"""
from __future__ import annotations

import datetime as dt

from house_ear.session import briefing
from house_ear.session.briefing import BRIEFING_STATUS, Briefing, from_news


def test_the_script_keeps_the_old_build_shape() -> None:
    """Old tools/news.py:126-136: dated head, 第一/第二 body, optional 微语."""
    brief = from_news(["今天下雨", "明天转晴"], date=dt.date(2026, 10, 23),
                      tip="周末愉快")
    assert brief is not None
    assert brief.count == 2
    assert brief.speech_text == (
        "今天是10月23日，星期五。给你念几条新闻。"
        "第一，今天下雨。第二，明天转晴。最后一句微语：周末愉快")


def test_from_news_normalises_the_shapes_the_sources_return() -> None:
    # Strings and {"title": ...} dicts both occur (ours vs the old _titles()).
    items = ["  今天   下雨 ", {"title": "明天转晴"}, {"title": ""}, "已经有句号。"]
    brief = from_news(items, date="2026-10-22", limit=3)
    assert brief is not None
    assert brief.items == ("今天 下雨。", "明天转晴。", "已经有句号。")
    # the whole digest dict is accepted too, tip included
    digest = {"news": [{"title": "A"}, {"title": "B"}], "tip": "晚安"}
    whole = from_news(digest, date="2026-10-22")
    assert whole is not None
    assert whole.tip == "晚安" and whole.count == 2
    # limit is clamped to 1..10 by the caller contract
    many = [f"item {i}" for i in range(20)]
    assert from_news(many, limit=99).count == briefing.MAX_ITEMS  # type: ignore[union-attr]
    assert from_news(many, limit=0).count == 1                    # type: ignore[union-attr]


def test_an_empty_feed_is_none_not_an_empty_header() -> None:
    assert from_news([]) is None
    assert from_news({"news": [], "tip": "x"}) is None
    assert from_news([{"title": ""}, "   "]) is None


def test_the_payload_carries_no_script_so_the_model_cannot_re_read_it() -> None:
    """Old assistant_v3.py:906-913: the model saw a notice, never the text."""
    brief = from_news(["今天下雨", "明天转晴"], date=dt.date(2026, 10, 23))
    assert brief is not None
    payload = brief.to_payload()
    assert payload == {"status": BRIEFING_STATUS, "date": "2026-10-23",
                       "count": 2, "source": "60s"}
    assert brief.speech_text not in str(payload)
    notice = brief.model_notice()
    assert "2 条" in notice and "直接念给用户听" in notice
    assert brief.speech_text not in notice


def test_history_keeps_the_verbatim_script_for_third_item_questions() -> None:
    """Old assistant_v3.py:1017-1024: follow-ups read the real script from history."""
    brief = from_news(["今天下雨", "明天转晴", "第三条是这条"], date="2026-10-23")
    assert brief is not None
    messages = brief.history_messages("念一下今天的新闻")
    assert messages == [
        {"role": "user", "content": "念一下今天的新闻"},
        {"role": "assistant", "content": brief.speech_text},
    ]
    assert "第三，第三条是这条。" in messages[1]["content"]
    assert brief.item_index("第三条再说说") == 3
    assert brief.item_at(3) == "第三条是这条。"


def test_history_is_capped_at_the_old_800_characters() -> None:
    items = [f"第{i}条新闻的内容" * 20 for i in range(1, 11)]
    brief = from_news(items)
    assert brief is not None and len(brief.speech_text) > briefing.HISTORY_LIMIT
    content = brief.history_messages("念新闻")[1]["content"]
    assert len(content) == briefing.HISTORY_LIMIT
    assert content == brief.speech_text[:briefing.HISTORY_LIMIT]


def test_item_index_understands_spoken_and_digit_ordinals() -> None:
    brief = from_news([f"新闻{i}" for i in range(1, 11)], limit=10)
    assert brief is not None
    assert brief.item_index("第三条再说说") == 3
    assert brief.item_index("第 三 条 再 说 说") == 3
    assert brief.item_index("第3条是什么") == 3
    assert brief.item_index("第十条") == 10
    assert brief.item_index("第八条") == 8
    assert brief.item_index("第十一条") is None      # out of range
    assert brief.item_index("三条") is None          # no 第...条 marker
    assert brief.item_index("今天天气怎么样") is None
    assert brief.item_at(0) == "" and brief.item_at(11) == ""


def test_date_defaults_to_today_and_can_be_overridden() -> None:
    today = dt.date.today()
    brief = from_news(["新闻"], tip="")
    assert brief is not None
    assert brief.date == today.isoformat()
    assert brief.speech_text.startswith(
        f"今天是{today.month}月{today.day}日，{briefing.WEEKDAYS[today.weekday()]}。")
    assert from_news(["新闻"], date="not-a-date").date == today.isoformat()  # type: ignore[union-attr]
    assert from_news(["新闻"], source="aihot").source == "aihot"             # type: ignore[union-attr]
    assert isinstance(brief, Briefing)
