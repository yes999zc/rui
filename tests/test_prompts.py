"""Prompt assembly and text hygiene, with the old deployment's rules pinned.

These are the parts of S5b that are decisions rather than measurements: which words the
model receives, and what never leaves the speaker.
"""
from __future__ import annotations

from house_ear.config.schema import AssistantConfig
from house_ear.session import prompts
from house_ear.session.text import (
    clean_reply,
    has_markup,
    sanitize_spoken,
    strip_wake,
    wake_pattern,
)


def test_prompt_carries_the_length_bands_and_the_no_tools_note() -> None:
    text = prompts.build(AssistantConfig())
    # the bands come from assistant.reply_max_chars*: raising the numbers without these
    # sentences did nothing in the old build
    assert "不超过120个字" in text
    assert "可以说到900字" in text
    assert "信息量优先" in text
    # the acknowledgement is played by the speaker, so the first sentence must not greet
    assert "不要再打招呼" in text
    # no tool layer yet: the model must be told, not left to promise a search
    assert "查证工具" in text
    assert "不要给具体数字" in text
    # ... and it must not advertise devices it cannot drive (measured: it listed
    # 开关灯/调空调/放音乐 as things it does, with zero tools wired)
    assert "设备还没接上" in text

    a = AssistantConfig(reply_max_chars=40, reply_max_chars_long=200)
    short = prompts.build(a)
    assert "不超过40个字" in short and "可以说到200字" in short


def test_persona_is_appended_and_tools_ok_drops_the_no_tools_note() -> None:
    a = AssistantConfig(persona="说话像个老兵，简短。")
    text = prompts.build(a)
    assert text.rstrip().endswith("不要假装刚刚查过。）") or "说话像个老兵" in text
    assert "说话像个老兵" in text
    # with tools wired the old rules apply instead of the offline note
    with_tools = prompts.build(a, tools_ok=True)
    assert "查证工具" not in with_tools

    empty = prompts.build(AssistantConfig(persona=""))
    assert "家里的小助理" not in empty       # persona is what carries it, not the template


def test_a_hand_edited_prompt_with_braces_does_not_raise() -> None:
    a = AssistantConfig(system_prompt="只有一句 {name} {unknown}", persona="")
    assert prompts.build(a)            # KeyError is swallowed, raw text is used


def test_clean_reply_drops_markup_lines() -> None:
    raw = (
        "好的。\n<｜｜DSML｜｜ calls>\n<|invoke name=\"web_search\">\n"
        "现在十二度。"
    )
    assert clean_reply(raw) == "好的。\n现在十二度。"
    assert clean_reply("") == ""
    assert has_markup(raw) and not has_markup("好的。")


def test_strip_wake_drops_a_wake_word_caught_by_the_preroll() -> None:
    words = ["你好小瑞"]
    assert strip_wake("你好小瑞，现在几点了？", words) == "现在几点了？"
    assert strip_wake("你好，小瑞。现在几点了？", words) == "现在几点了？"
    # ASR mangles the splice; the tail alone is enough
    assert strip_wake("小瑞现在几点了", words) == "现在几点了"
    # ... and one character of junk in front, which is the old build's recorded case
    # (「王小智快几点了」). Exactly one: two characters is a real sentence.
    assert strip_wake("王小瑞快几点了", words) == "快几点了"
    assert strip_wake("喂小瑞，现在几点了", words) == "现在几点了"
    # a question that merely mentions the name later is a real question
    assert strip_wake("今天小瑞在吗", words) == "今天小瑞在吗"
    # only the wake word: nothing left to answer
    assert strip_wake("你好小瑞", words) == ""
    # no wake words configured -> untouched
    assert strip_wake("今天天气怎么样", []) == "今天天气怎么样"


def test_sanitize_spoken_drops_markup_fragments_between_sentences() -> None:
    """The streaming split can cut a DSML tag in half; the half must not be spoken."""
    assert sanitize_spoken('<|invoke name="web_search">') == ""
    assert sanitize_spoken("parameter>") == ""
    assert sanitize_spoken("好的，parameter>") == "好的，"
    assert sanitize_spoken("灯已经打开了。") == "灯已经打开了。"
    assert sanitize_spoken("") == ""


def test_the_token_budget_can_hold_the_long_band() -> None:
    """max_tokens is a ceiling, not a target - but 220 cut the 900-character band in half."""
    from house_ear.config.schema import ProviderConfig

    assert ProviderConfig().max_tokens >= 1200


def test_the_old_wake_word_still_works_for_the_old_homophones() -> None:
    """「小智」has real ASR data behind it; a custom word is matched exactly."""
    pattern = wake_pattern(["你好小智"])
    assert pattern is not None
    assert pattern.search("你好小志，几点了")
    assert wake_pattern([]) is None


def test_session_rules_keep_noise_and_stop_words_out_of_the_model() -> None:
    from house_ear.session import rules

    # noise: the real-device 「嗯。」 that became a whole turn
    for text in ("嗯。", "嗯嗯", "啊？", "。。。", "", "  "):
        assert rules.is_noise(text), text
    # a real one-character answer is not noise
    assert not rules.is_noise("要")
    assert not rules.is_noise("停")

    # stop: session actions, including the ASR slip measured on the device
    for text in ("停止", "停一下", "退下吧", "对一下吧", "关闭", "不听了", "关机"):
        assert rules.stop_intent(text), text
    # a sentence that merely contains a stop word is a question, not a dismissal
    assert not rules.stop_intent("把灯关掉以后告诉我天气怎么样")
    assert not rules.stop_intent("现在几点了")
    assert rules.stop_reply(0) != rules.stop_reply(1)


def test_what_we_just_said_cannot_become_a_turn() -> None:
    """Ported from the old build's `_is_self_echo`, whose filler case started this."""
    from house_ear.session.rules import SpokenMemory

    m = SpokenMemory()
    m.remember("我想想。")                     # the incident: our own filler came back
    assert m.is_echo("我想想")
    assert not m.is_echo("今天天气怎么样")

    m.remember("沈阳今天晴，气温1到15度，降水概率是零，不会下雨。")
    assert m.is_echo("沈阳今天晴，气温1到15度降。")   # the device log, verbatim
    assert m.is_echo("降水概率是零不会下雨")           # a fragment is still ours

    # 2026-10-22 20:16: the farewell's own echo, one tone-slip away from the spoken line
    m.remember("没事的话，我先下啦。")
    assert m.is_echo("没事的话，我先下吧。")
    # a real question about a fresh topic is not an echo, fuzzy pass included
    assert not m.is_echo("明天天气怎么样")

    # the window closes: a person asking about the answer is not an echo
    import time as _time

    assert not m.is_echo("我想想", now=_time.monotonic() + 30)


def test_without_a_music_tool_it_says_it_cannot_play_music() -> None:
    """Measured 2026-10-22: with a song playing it answered 「好，接着放。」 with no tool."""
    text = prompts.build(tool_names=["local.weather", "local.news"])
    assert "没有播放音乐" in text


def test_the_note_disappears_once_a_music_tool_exists() -> None:
    text = prompts.build(tool_names=["local.weather", "music.play"])
    assert "没有播放音乐" not in text
