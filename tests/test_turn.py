"""Turn orchestration with fake providers: fallback, streaming and latency marks."""
from __future__ import annotations

import asyncio

from house_ear.config.schema import Config
from house_ear.providers.base import ChatChunk, Transcript
from house_ear.session.turn import TurnRunner, split_sentences
from house_ear.trace import TurnTrace


class FakeAsr:
    def __init__(self, text: str = "今天天气怎么样", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        self.calls += 1
        if self.fail:
            raise RuntimeError("asr down")
        return Transcript(text=self.text)


class FakeLlm:
    def __init__(self, chunks: list[str], fail: bool = False) -> None:
        self.chunks = chunks
        self.fail = fail
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        if self.fail:
            raise RuntimeError("llm down")
        self.seen.append(messages)
        for c in self.chunks:
            yield ChatChunk(delta=c)


class FakeTts:
    def __init__(self, chunk: bytes = b"\x01\x00" * 320, fail: bool = False) -> None:
        self.chunk = chunk
        self.fail = fail
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        if self.fail:
            raise RuntimeError("tts down")
        yield self.chunk


def patch_chains(monkeypatch, *, asr, llms, ttss) -> None:
    import house_ear.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", asr)])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [(f"l{i}", p) for i, p in enumerate(llms)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [(f"t{i}", p) for i, p in enumerate(ttss)])


def test_sentences_split_on_punctuation_and_length() -> None:
    sentences, rest = split_sentences("今天多云转晴，最高二十度。明天也是晴天。后面还有")
    assert sentences == ["今天多云转晴，最高二十度。", "明天也是晴天。"]
    assert rest == "后面还有"

    long_text = "没有标点" * 20
    sentences, rest = split_sentences(long_text)
    assert sentences and all(len(s) <= 45 for s in sentences)
    assert rest

    # one short sentence is still a sentence: waiting for more text would only add latency
    assert split_sentences("好。") == (["好。"], "")

    # a long clause speaking early is the point; a short one is not
    long_clause, rest = split_sentences("今天沈阳多云转晴最高二十度，明天有雨")
    assert long_clause == ["今天沈阳多云转晴最高二十度，"]
    assert rest == "明天有雨"
    assert split_sentences("好，") == ([], "好，")
    assert split_sentences("今天沈阳多云转晴最高二十度，", clauses=False) == ([], "今天沈阳多云转晴最高二十度，")


def test_turn_runs_asr_llm_tts_and_marks_latency(monkeypatch) -> None:
    asr = FakeAsr("今天天气怎么样")
    llm = FakeLlm(["今天", "多云转晴，最高二十度。", "明天也是晴天。"])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=asr, llms=[llm], ttss=[tts])

    spoken: list[bytes] = []

    async def speak(pcm: bytes) -> None:
        spoken.append(pcm)

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 1600, speak, trace))

    assert result.transcript == "今天天气怎么样"
    assert result.reply == "今天多云转晴，最高二十度。明天也是晴天。"
    assert result.audio_bytes == 2 * 640          # one fake chunk per sentence
    assert result.stages["llm"].used == "l0"
    assert result.stages["tts"].used == "t0"
    # both sentences were synthesised, not just the first one
    assert tts.texts == ["今天多云转晴，最高二十度。", "明天也是晴天。"]
    assert len(spoken) == 2
    for mark in ("end_of_speech", "asr_done", "llm_first", "tts_first", "play_start", "llm_done"):
        assert trace.at(mark) is not None, f"missing mark {mark}"
    tts_first, llm_first = trace.at("tts_first"), trace.at("llm_first")
    assert tts_first is not None and llm_first is not None
    assert tts_first >= llm_first


def test_a_dead_stage_falls_back_to_the_next_provider(monkeypatch) -> None:
    asr2 = FakeAsr("开灯")
    llm = FakeLlm(["好的，已开灯。"], fail=True)
    llm2 = FakeLlm(["好的，已开灯。"])
    tts = FakeTts(fail=True)
    tts2 = FakeTts()
    patch_chains(monkeypatch, asr=asr2, llms=[llm, llm2], ttss=[tts, tts2])

    async def speak(pcm: bytes) -> None:
        return None

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 1600, speak, trace))

    assert result.error == ""
    assert result.stages["llm"].used == "l1"
    assert result.stages["tts"].used == "t1"
    assert result.stages["llm"].attempts[0]["error"].startswith("RuntimeError")
    assert result.audio_bytes > 0


def test_empty_transcript_ends_the_turn_early(monkeypatch) -> None:
    patch_chains(monkeypatch, asr=FakeAsr("  "), llms=[FakeLlm(["不会用到"])], ttss=[FakeTts()])

    async def speak(pcm: bytes) -> None:
        raise AssertionError("nothing should be spoken")

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 800, speak, trace))
    assert result.error == "empty transcript"
    assert result.audio_bytes == 0


def test_history_carries_context_between_turns(monkeypatch) -> None:
    llm = FakeLlm(["好的。", "再见。"])
    patch_chains(monkeypatch, asr=FakeAsr("第一句"), llms=[llm], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers)

    async def speak(pcm: bytes) -> None:
        return None

    asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    second = llm.seen[-1]
    roles = [m["role"] for m in second]
    assert roles[-3:] == ["user", "assistant", "user"]
    assert second[-1]["content"] == "第一句"


def test_missing_key_fails_the_stage_with_a_reason(monkeypatch) -> None:
    """No key configured must read as a stage failure, not as silence."""
    cfg = Config()
    cfg.providers.catalog["asr"].api_key_env = "HOUSE_EAR_NOT_SET_ANYWHERE"
    cfg.providers.catalog["asr"].base_url = "http://127.0.0.1:9/v1"
    monkeypatch.delenv("HOUSE_EAR_NOT_SET_ANYWHERE", raising=False)

    async def speak(pcm: bytes) -> None:
        return None

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(cfg.providers).run(b"\x00\x00" * 800, speak, trace))
    assert result.transcript == ""
    assert result.error == "empty transcript"   # ASR failed, so the turn had nothing to say
