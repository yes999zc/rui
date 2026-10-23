"""Server-side reply ceiling: the three console budgets must bound what is spoken.

Measured on the real speaker 2026-10-23: the model ignored the prompt's 300-char search
budget and answered 478 chars over 90 s. TTS starts on the first finished sentence, so a
cap applied after the model has finished would leave that whole answer spoken and only
shorten the text we report. These tests pin both the pure cut (`enforce_reply_cap`) and
the streaming stop that actually bounds the turn.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from house_ear.config.schema import AssistantConfig, Config
from house_ear.providers.base import ChatChunk, Transcript
from house_ear.session.turn import (
    DirectSpeech,
    TurnRunner,
    enforce_reply_cap,
    reply_cap_for,
)
from house_ear.trace import TurnTrace


class FakeAsr:
    def __init__(self, text: str = "现在几点了") -> None:
        self.text = text

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text=self.text)


class FakeLlm:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append([dict(m) for m in messages])
        for chunk in self.chunks:
            yield ChatChunk(delta=chunk)


class ScriptedLlm:
    """One pre-built chunk list per round, so a tool round is a data shape."""

    def __init__(self, rounds: list[list[ChatChunk]]) -> None:
        self.rounds = list(rounds)
        self.seen: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append({"messages": [dict(m) for m in messages], "tools": list(tools)})
        for chunk in self.rounds.pop(0):
            yield chunk


class FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield b"\x01\x00" * 320


def _patch_chains(monkeypatch, llm: Any, tts: FakeTts) -> None:
    import house_ear.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", FakeAsr())])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [("l", llm)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [("t", tts)])


def _run_turn(
    monkeypatch,
    *,
    llm: Any,
    tts: FakeTts | None = None,
    assistant: AssistantConfig | None = None,
    tools: tuple = (),
    transcript: str = "现在几点了",
    direct_hook=None,
):
    tts = tts or FakeTts()
    _patch_chains(monkeypatch, llm, tts)
    kwargs: dict[str, Any] = {}
    if assistant is not None:
        kwargs["assistant"] = assistant
    if direct_hook is not None:
        kwargs["direct_hook"] = direct_hook
    runner = TurnRunner(Config().providers, tools=tools, **kwargs)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(
        runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living"),
                   transcript=transcript)
    )
    return runner, result, tts


def _cap_logs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "reply capped" in r.getMessage()]


# --------------------------------------------------------------------------- pure cut


def test_a_reply_that_fits_is_returned_unchanged() -> None:
    assert enforce_reply_cap("好。", 10) == ("好。", False)
    # exactly the cap is not a truncation
    exact = "第一句话。第二句话。"
    assert len(exact) == 10
    assert enforce_reply_cap(exact, 10) == (exact, False)


def test_empty_text_and_a_useless_cap_are_left_alone() -> None:
    assert enforce_reply_cap("", 10) == ("", False)
    assert enforce_reply_cap("", 0) == ("", False)
    assert enforce_reply_cap("好。", 0) == ("好。", False)
    assert enforce_reply_cap("好。", -3) == ("好。", False)


def test_the_cut_lands_on_the_last_sentence_end_within_the_cap() -> None:
    assert enforce_reply_cap("第一句话。第二句话。第三句话。", 12) == (
        "第一句话。第二句话。", True,
    )


def test_without_a_sentence_end_the_cut_falls_back_to_the_last_comma() -> None:
    cut, truncated = enforce_reply_cap("今天辽宁多云转晴，最高二十度，明天可能有雨", 12)
    assert truncated is True
    assert cut == "今天辽宁多云转晴，"


def test_without_any_punctuation_the_cut_is_hard_at_the_cap() -> None:
    assert enforce_reply_cap("abcdefghij", 4) == ("abcd", True)
    assert enforce_reply_cap("没有标点也没有句号只能一直说下去", 4) == ("没有标点", True)
    # a hard cut does not leave trailing spaces behind
    assert enforce_reply_cap("abc   def", 5) == ("abc", True)


def test_english_punctuation_counts_as_a_sentence_end() -> None:
    assert enforce_reply_cap("It is sunny. Is it warm? Yes!", 24) == (
        "It is sunny. Is it warm?", True,
    )
    assert enforce_reply_cap("Really?! Fine.", 9) == ("Really?!", True)


# ------------------------------------------------------------------------ cap choice


def test_the_three_budgets_come_from_the_three_console_fields() -> None:
    assistant = AssistantConfig(
        reply_max_chars=120, reply_max_chars_search=300, reply_max_chars_long=900
    )
    assert reply_cap_for(assistant, "现在几点了") == (120, "default")
    assert reply_cap_for(assistant, "搜一下今天的新闻", info_used=True) == (300, "search")
    # an info question before any tool ran is still the everyday budget
    assert reply_cap_for(assistant, "搜一下今天的新闻") == (120, "default")


def test_an_explicit_request_to_expand_gets_the_long_budget() -> None:
    assistant = AssistantConfig()
    for ask in ("背一下", "念全文", "完整地讲", "详细说说", "展开讲讲",
                "再讲一下", "念一遍", "读一遍", "多说点", "系统地讲"):
        assert reply_cap_for(assistant, ask) == (900, "long"), ask
    # it also wins over the search budget: the request is the stronger signal
    assert reply_cap_for(assistant, "详细讲讲搜到的内容", info_used=True) == (900, "long")


# ------------------------------------------------------------- streaming enforcement


def test_a_short_answer_is_spoken_whole(monkeypatch) -> None:
    runner, result, tts = _run_turn(monkeypatch, llm=FakeLlm(["好。", "现在十点。"]))
    assert result.reply == "好。现在十点。"
    assert tts.texts == ["好。", "现在十点。"]
    assert runner.history[-1] == {"role": "assistant", "content": "好。现在十点。"}


def test_an_answer_exactly_at_the_cap_is_not_touched(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger="house_ear.session.turn")
    assistant = AssistantConfig(reply_max_chars=10)
    _runner, result, tts = _run_turn(
        monkeypatch, llm=FakeLlm(["第一句话。第二句话。"]), assistant=assistant
    )
    assert result.reply == "第一句话。第二句话。"
    assert "".join(tts.texts) == result.reply
    assert not _cap_logs(caplog)


def test_the_everyday_cap_stops_the_speech_not_only_the_reported_reply(
    monkeypatch, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="house_ear.session.turn")
    assistant = AssistantConfig(reply_max_chars=12)
    runner, result, tts = _run_turn(
        monkeypatch,
        llm=FakeLlm(["第一句话。第二句话。第三句话。第四句话。"]),
        assistant=assistant,
    )
    # the next complete sentence is dropped whole; nothing after it is synthesised
    assert tts.texts == ["第一句话。", "第二句话。"]
    assert result.reply == "第一句话。第二句话。"
    assert result.error == ""            # a capped answer is not a failed turn
    assert runner.history[-1] == {"role": "assistant", "content": result.reply}
    logs = _cap_logs(caplog)
    assert logs and "20 -> 10" in logs[0] and "cap 12 from default" in logs[0]


def test_an_expansion_request_gets_the_long_budget(monkeypatch) -> None:
    assistant = AssistantConfig(reply_max_chars=10, reply_max_chars_long=100)
    text = "第一句话。第二句话。第三句话。第四句话。"   # 20 chars: over 10, under 100
    _runner, result, tts = _run_turn(
        monkeypatch, llm=FakeLlm([text]), assistant=assistant, transcript="背一下全文"
    )
    assert result.reply == text
    assert "".join(tts.texts) == text


def test_a_searched_answer_gets_the_search_budget(monkeypatch) -> None:
    from house_ear.tools.contract import Permission, ToolSpec

    spec = ToolSpec(name="local.web_search", description="搜索", permission=Permission.READ)

    async def handler(_args: dict) -> str:
        return "结果一；结果二"

    call = ChatChunk(delta="", finish_reason="tool_calls",
                     tool_calls=[{"id": "t1", "name": "web_search", "arguments": "{}"}])
    answer = "这是搜索到的第一个结果。这是第二个结果。这是第三个结果。"   # 36 chars
    llm = ScriptedLlm([[call], [ChatChunk(delta=answer)]])
    assistant = AssistantConfig(reply_max_chars=10, reply_max_chars_search=40)
    _runner, result, tts = _run_turn(
        monkeypatch, llm=llm, assistant=assistant, tools=((spec, handler),),
        transcript="帮我搜一下今天的新闻",
    )
    assert result.tools == ["web_search"]
    assert result.reply == answer        # the everyday 10 would have cut it
    assert "".join(tts.texts) == answer


def test_a_run_on_answer_is_hard_cut_at_the_cap(monkeypatch) -> None:
    assistant = AssistantConfig(reply_max_chars=10)
    _runner, result, tts = _run_turn(
        monkeypatch,
        llm=FakeLlm(["没有标点也没有句号只能一直说下去"]),
        assistant=assistant,
    )
    assert result.reply == "没有标点也没有句号只"
    assert tts.texts == [result.reply]


def test_direct_speech_keeps_its_own_420_char_path(monkeypatch) -> None:
    """The briefing path truncates at 420 in `app.py`; the turn cap must not cut twice."""
    from house_ear.tools.contract import Permission, ToolSpec

    spec = ToolSpec(name="local.news", description="新闻", permission=Permission.READ)

    async def handler(_args: dict) -> str:
        return "ignored"

    script = "新闻第一条。" * 12         # 72 chars, over the everyday 10
    call = ChatChunk(delta="", finish_reason="tool_calls",
                     tool_calls=[{"id": "t1", "name": "news", "arguments": "{}"}])
    llm = ScriptedLlm([[call], [ChatChunk(delta="不应该被问到。")]])

    def hook(name: str, transcript: str, output: object) -> DirectSpeech | None:
        return DirectSpeech(text=script)

    runner, result, tts = _run_turn(
        monkeypatch, llm=llm, assistant=AssistantConfig(reply_max_chars=10),
        tools=((spec, handler),), transcript="念一下今天的新闻", direct_hook=hook,
    )
    assert result.reply == script
    assert "".join(tts.texts) == script
    assert runner.history[-1] == {"role": "assistant", "content": script}


def test_a_read_aloud_request_gets_the_long_budget() -> None:
    """16:41: 「帮我朗读岳阳楼记」 missed the expand list and was cut at 120 chars."""
    from house_ear.config import Config
    from house_ear.session.turn import reply_cap_for

    for transcript in ("帮我朗读岳阳楼记", "背诵出师表", "念全文", "朗诵一首诗"):
        cap, source = reply_cap_for(Config().assistant, transcript, [])
        assert source == "long", transcript
        assert cap == Config().assistant.reply_max_chars_long
