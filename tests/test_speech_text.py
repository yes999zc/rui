"""What the vendor is asked to say (measured on the real speaker, 2026-10-23).

讯飞 spells a URL out character by character - colon included (4.17 s for
https://example.com, against 1.08 s for 网址) - and reads a Markdown "## " as a
symbol name (1.67 s vs 1.07 s). That is the spoken 「冒号」 people hear. The fix is
one normalisation right before synthesis; these tests pin it and the clause gap.
"""
from __future__ import annotations

import asyncio

from rui.config.schema import Config
from rui.providers.base import ChatChunk, Transcript
from rui.session.speech_text import gap_after, prepare_for_speech
from rui.session.turn import TurnRunner
from rui.trace import TurnTrace


class FakeAsr:
    def __init__(self, text: str = "问一句") -> None:
        self.text = text

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:  # noqa: ARG002
        return Transcript(text=self.text)


class FakeLlm:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def chat(self, messages: list[dict], tools: list[dict]):  # noqa: ARG002
        for chunk in self.chunks:
            yield ChatChunk(delta=chunk)


class FakeTts:
    def __init__(self) -> None:
        self.chunk = b"\x01\x00" * 320
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):  # noqa: ARG002
        self.texts.append(text)
        yield self.chunk


def patch_chains(monkeypatch, *, asr, llms, ttss) -> None:
    import rui.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", asr)])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [(f"l{i}", p) for i, p in enumerate(llms)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [(f"t{i}", p) for i, p in enumerate(ttss)])


def test_urls_and_markdown_never_reach_the_vendor() -> None:
    assert prepare_for_speech("看这里 https://a.cn/x 就行。") == "看这里 就行。"
    assert "http" not in prepare_for_speech("来源：https://example.com。")
    assert prepare_for_speech("# 标题") == "标题"
    assert prepare_for_speech("**重点**") == "重点"
    assert prepare_for_speech("[页面](https://a.cn) 讲得好。") == "页面 讲得好。"


def test_symbols_become_pauses_not_words() -> None:
    assert prepare_for_speech("注意：以下三点。") == "注意，以下三点。"
    assert prepare_for_speech("第一；第二。") == "第一。第二。"
    assert prepare_for_speech("他说“好”。") == "他说好。"
    assert prepare_for_speech("温度（可选）：十五度。") == "温度可选，十五度。"


def test_unambiguous_numbers_are_read_as_words() -> None:
    assert prepare_for_speech("75% 的人。") == "百分之七十五 的人。"
    assert prepare_for_speech("12:30 见。") == "十二点三十分 见。"
    assert prepare_for_speech("9℃ 到 16℃。") == "9度 到 16度。"
    assert prepare_for_speech("1~3 个。") == "1到3 个。"


def test_unknown_characters_are_removed_not_spoken() -> None:
    assert prepare_for_speech("🎵 放歌。") == "放歌。"
    assert prepare_for_speech("   ") == ""
    assert prepare_for_speech("") == ""


def test_only_sentence_ends_get_the_350_ms_silence() -> None:
    # A clause split already carries the vendor's comma pause; a second hard pause is
    # what made mid-sentence speech sound stitched together.
    assert gap_after("今天沈阳多云转晴最高二十度，") == 0
    assert gap_after("注意：") == 0
    assert gap_after("明天有雨。") == 350
    assert gap_after("走不走？") == 350
    assert gap_after("") == 0


def test_a_url_in_a_reply_is_not_spoken_as_a_url(monkeypatch) -> None:
    asr = FakeAsr("查一下")
    llm = FakeLlm(["看这里 https://a.cn/x 就行。"])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=asr, llms=[llm], ttss=[tts])
    spoken: list[bytes] = []

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:  # noqa: ARG001
        spoken.append(pcm)

    asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 1600, speak, TurnTrace(device_id="living")))
    assert tts.texts == ["看这里 就行。"]
    assert all(b"http" not in text.encode() for text in tts.texts)


def test_a_clause_split_does_not_add_a_second_pause(monkeypatch) -> None:
    asr = FakeAsr("今天天气")
    llm = FakeLlm(["今天沈阳多云转晴最高二十度，明天有雨。"])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=asr, llms=[llm], ttss=[tts])
    spoken: list[bytes] = []

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:  # noqa: ARG001
        spoken.append(pcm)

    asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 1600, speak, TurnTrace(device_id="living")))
    gap = b"\x00\x00" * (16000 * 350 // 1000)
    # clause piece (no gap) -> sentence piece + one gap
    assert tts.texts == ["今天沈阳多云转晴最高二十度，", "明天有雨。"]
    assert spoken == [tts.chunk, tts.chunk, gap]
