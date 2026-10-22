"""Turn orchestration with fake providers: fallback, streaming and latency marks."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from house_ear.config.schema import AssistantConfig, Config, ProviderConfig
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

    spoken: list[tuple[bytes, bool]] = []

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        spoken.append((pcm, segment_start))

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 1600, speak, trace))

    assert result.transcript == "今天天气怎么样"
    assert result.reply == "今天多云转晴，最高二十度。明天也是晴天。"
    assert result.audio_bytes == 2 * 640          # one fake chunk per sentence; gap not counted
    assert result.stages["llm"].used == "l0"
    assert result.stages["tts"].used == "t0"
    # both sentences were synthesised, not just the first one
    assert tts.texts == ["今天多云转晴，最高二十度。", "明天也是晴天。"]
    # each sentence: its chunk marked as a segment start, then 350 ms of true silence
    gap = b"\x00\x00" * (16000 * 350 // 1000)
    assert [pcm for pcm, _ in spoken] == [tts.chunk, gap, tts.chunk, gap]
    assert [start for _, start in spoken] == [True, False, True, False]
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

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
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

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        raise AssertionError("nothing should be spoken")

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 800, speak, trace))
    assert result.error == "empty transcript"
    assert result.audio_bytes == 0


def test_history_carries_context_between_turns(monkeypatch) -> None:
    class TwoTurns(FakeAsr):
        def __init__(self) -> None:
            super().__init__("第一句")
            self.texts = ["第一句", "第二句"]

        async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
            self.calls += 1
            return Transcript(text=self.texts[self.calls - 1])

    llm = FakeLlm(["好的。", "再见。"])
    patch_chains(monkeypatch, asr=TwoTurns(), llms=[llm], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    second = llm.seen[-1]
    roles = [m["role"] for m in second]
    assert roles[-3:] == ["user", "assistant", "user"]
    assert second[-1]["content"] == "第二句"
    # the first question is still context for the second one
    assert any(m["content"] == "第一句" for m in second)


def test_the_wake_word_from_the_preroll_never_reaches_the_model(monkeypatch) -> None:
    """The pre-roll replay is deliberate, so the transcript can start with the wake word."""
    llm = FakeLlm(["一点了。"])
    patch_chains(monkeypatch, asr=FakeAsr("你好小瑞，现在几点了？"), llms=[llm], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers, wake_words=["你好小瑞"])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    assert result.transcript == "现在几点了？"
    assert llm.seen[-1][-1]["content"] == "现在几点了？"
    # the model also gets a clean turn: only the request, no wake phrase
    assert runner.history[0] == {"role": "user", "content": "现在几点了？"}


def test_markup_is_never_spoken_nor_stored(monkeypatch) -> None:
    """A tool call written as text would otherwise be read aloud (the old 14 s bug)."""
    llm = FakeLlm(["好的。", '<|invoke name="web_search">', "十二度。"])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr("现在几度"), llms=[llm], ttss=[tts])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    runner = TurnRunner(Config().providers)
    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    # the tag itself is dropped; the answer around it still gets spoken
    assert tts.texts == ["好的。", "十二度。"]
    assert result.reply == "好的。十二度。"
    assert all("invoke" not in str(m["content"]) for m in runner.history)


def test_the_prompt_is_assembled_per_turn_and_history_is_six_rounds(monkeypatch) -> None:
    llm = FakeLlm(["好。"])
    patch_chains(monkeypatch, asr=FakeAsr("好"), llms=[llm], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers, wake_words=["你好小瑞"])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    assert runner.history_turns == 6
    asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    system = llm.seen[-1][0]
    assert system["role"] == "system"
    assert "不超过120个字" in system["content"]
    assert "查证工具" in system["content"]
    # a renamed assistant shows up in the next turn's prompt, not only at startup
    runner.assistant.name = "小瑞二号"
    asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    assert "小瑞二号" in llm.seen[-1][0]["content"]



def test_history_window_shape_and_the_repeat_drop(monkeypatch) -> None:
    """A repeated question must not come back to the model with its own answer attached."""
    patch_chains(monkeypatch, asr=FakeAsr("好"), llms=[FakeLlm(["好。"] * 8)], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers, history_turns=3)
    runner.history = [
        {"role": "user", "content": "今天天气怎么样"},
        {"role": "assistant", "content": "查不到。"},
        {"role": "user", "content": "你都能做什么"},
        {"role": "assistant", "content": "聊天。"},
        {"role": "user", "content": "今天天气怎么样"},
        {"role": "assistant", "content": "查不到天气。"},
    ]
    # both copies of the repeated question go: its own answer is what the model imitates
    assert runner.history_messages("今天天气怎么样") == [
        {"role": "user", "content": "你都能做什么"},
        {"role": "assistant", "content": "聊天。"},
    ]
    # an unrelated question keeps the whole window as context (3 rounds = all 6 messages)
    assert runner.history_messages("现在几点了") == runner.history
    # history_rounds: 0 means no history at all (history[-0:] used to mean all of it)
    runner.history_turns = 0
    assert runner.history_messages("现在几点了") == []


def test_missing_key_fails_the_stage_with_a_reason(monkeypatch) -> None:
    """No key configured must read as a stage failure, not as silence."""
    cfg = Config()
    cfg.providers.catalog["asr"].api_key_env = "HOUSE_EAR_NOT_SET_ANYWHERE"
    cfg.providers.catalog["asr"].base_url = "http://127.0.0.1:9/v1"
    monkeypatch.delenv("HOUSE_EAR_NOT_SET_ANYWHERE", raising=False)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    trace = TurnTrace(device_id="living")
    result = asyncio.run(TurnRunner(cfg.providers).run(b"\x00\x00" * 800, speak, trace))
    assert result.transcript == ""
    assert result.error == "empty transcript"   # ASR failed, so the turn had nothing to say


def test_a_flaky_provider_is_retried_before_the_next_one(monkeypatch) -> None:
    """SiliconFlow ASR returns 400 ms or a 20 s stall for the same clip; one retry is cheaper."""
    from house_ear.config.schema import ProviderConfig
    from house_ear.providers.chain import call_stage

    class Flaky(FakeAsr):
        def __init__(self) -> None:
            super().__init__("重试之后成功")
            self.fail = True

        async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
            self.calls += 1
            if self.fail:
                self.fail = False
                raise TimeoutError("first attempt stalls")
            return Transcript(text=self.text)

    flaky = Flaky()
    flaky.cfg = ProviderConfig(retries=1)  # type: ignore[attr-defined]
    transcript, report = asyncio.run(
        call_stage("asr", [("flaky", flaky)], "transcribe", b"\x00\x00" * 800, 16000)
    )
    assert transcript.text == "重试之后成功"
    assert report.used == "flaky"
    assert flaky.calls == 2
    assert report.attempts[0]["error"].startswith("TimeoutError")
    assert report.attempts[1]["error"] == ""


LOCAL_MODEL = Path("/data/models/sensevoice")
MAC_MODEL = Path.home() / (
    "AI_Workspace/03_Data/Models/sherpa-onnx/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
)


def sample_model_dir() -> Path | None:
    for candidate in (LOCAL_MODEL, MAC_MODEL):
        if (candidate / "model.int8.onnx").exists() or (candidate / "model.onnx").exists():
            return candidate
    return None


@pytest.mark.skipif(sample_model_dir() is None, reason="sensevoice model not installed")
def test_local_asr_reads_the_command_fixture() -> None:
    from house_ear.providers.sherpa_asr import SherpaAsr

    model = sample_model_dir()
    assert model is not None
    asr = SherpaAsr(ProviderConfig(kind="sherpa", model=str(model), threads=2))
    import wave

    with wave.open(str(Path(__file__).parent / "fixtures" / "command-weather.wav")) as w:
        pcm = w.readframes(w.getnframes())
    transcript = asyncio.run(asr.transcribe(pcm, 16000))
    assert "天气" in transcript.text


def test_a_missing_local_model_falls_back_to_the_cloud(monkeypatch) -> None:
    """No model on disk must mean a cloud answer, not a dead assistant."""
    from house_ear.providers.chain import call_stage
    from house_ear.providers.sherpa_asr import SherpaAsr

    monkeypatch.setattr(
        "house_ear.providers.sherpa_asr.model_paths",
        lambda _dir: (Path("/nonexistent/model.int8.onnx"), Path("/nonexistent/tokens.txt")),
    )
    local = SherpaAsr(ProviderConfig(kind="sherpa", model="/nonexistent"))
    cloud = FakeAsr("云端兜底")
    transcript, report = asyncio.run(
        call_stage("asr", [("local", local), ("cloud", cloud)], "transcribe",
                   b"\x00\x00" * 800, 16000)
    )
    assert transcript.text == "云端兜底"
    assert report.used == "cloud"
    assert "ModelMissing" in report.attempts[0]["error"]


def test_chat_payload_carries_the_latency_knobs() -> None:
    from house_ear.providers.openai_compat import chat_payload

    cfg = ProviderConfig(model="deepseek-flash", max_tokens=3000, temperature=0.4,
                         thinking="disabled")
    payload = chat_payload(cfg, [{"role": "user", "content": "在吗"}], [{"name": "light_on"}])
    assert payload["model"] == "deepseek-flash"
    assert payload["max_tokens"] == 3000
    assert payload["temperature"] == 0.4
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is True
    assert payload["tools"][0]["function"]["name"] == "light_on"

    plain = chat_payload(ProviderConfig(model="x"), [], [])
    assert "thinking" not in plain and "tools" not in plain


class ScriptedLlm:
    """Replays pre-built chunk lists, so a tool round is a data shape, not a network call."""

    def __init__(self, rounds: list[list[ChatChunk]]) -> None:
        self.rounds = list(rounds)
        self.seen: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append({"messages": [dict(m) for m in messages], "tools": list(tools)})
        for chunk in self.rounds.pop(0):
            yield chunk


def test_a_tool_round_runs_the_tool_then_answers(monkeypatch) -> None:
    """Model asks for weather -> handler runs -> the second round gets the result."""
    from house_ear.tools.contract import Permission, ToolSpec

    spec = ToolSpec(name="local.weather", description="查天气", permission=Permission.READ)
    calls: list[dict] = []

    async def handler(args: dict) -> str:
        calls.append(args)
        return "沈阳：今天晴，1到15度。"

    llm = ScriptedLlm([
        [ChatChunk(delta="", finish_reason="tool_calls",
                   tool_calls=[{"id": "t1", "name": "weather", "arguments": '{"city": "沈阳"}'}])],
        [ChatChunk(delta="沈阳今天晴，1到15度。")],
    ])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr("今天天气怎么样"), llms=[llm], ttss=[tts])
    runner = TurnRunner(Config().providers, tools=[(spec, handler)])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    assert calls == [{"city": "沈阳"}]
    assert result.tools == ["weather"]
    assert result.reply.startswith("沈阳今天晴")
    # the wire name has no dot (DeepSeek rejects it), and the second round sees the result
    assert llm.seen[0]["tools"][0]["name"] == "weather"
    second = llm.seen[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "t1"
    assert second[-1] == {"role": "tool", "tool_call_id": "t1", "content": "沈阳：今天晴，1到15度。"}
    # weather is not an INFO_TOOL: no long-answer hint was appended
    assert "最多" not in second[0]["content"]


def test_a_model_that_keeps_asking_for_tools_still_answers(monkeypatch) -> None:
    from house_ear.tools.contract import Permission, ToolSpec

    spec = ToolSpec(name="local.web_search", description="搜索", permission=Permission.READ)

    async def handler(args: dict) -> str:
        return "搜索结果：无关的东西"

    call = [ChatChunk(delta="", finish_reason="tool_calls",
                      tool_calls=[{"id": "t1", "name": "web_search", "arguments": '{"query": "x"}'}])]
    llm = ScriptedLlm([call, call, [ChatChunk(delta="不知道。")]])
    patch_chains(monkeypatch, asr=FakeAsr("查一下"), llms=[llm], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers, tools=[(spec, handler)])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    # third round is forced: no tools=, so the model has to talk
    assert llm.seen[2]["tools"] == []
    assert result.reply == "不知道。"
    # a searched answer gets the long budget, once
    assert "最多" in llm.seen[1]["messages"][0]["content"]


def test_a_failing_tool_becomes_a_sentence_not_an_exception(monkeypatch) -> None:
    from house_ear.tools.contract import Permission, ToolSpec

    spec = ToolSpec(name="local.weather", description="查天气", permission=Permission.READ)

    async def handler(args: dict) -> str:
        raise RuntimeError("upstream down")

    llm = ScriptedLlm([
        [ChatChunk(delta="", finish_reason="tool_calls",
                   tool_calls=[{"id": "t1", "name": "weather", "arguments": "{}"}])],
        [ChatChunk(delta="天气查不到。")],
    ])
    patch_chains(monkeypatch, asr=FakeAsr("天气"), llms=[llm], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers, tools=[(spec, handler)])

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    assert result.reply == "天气查不到。"
    assert "工具出错了" in llm.seen[1]["messages"][-1]["content"]


def test_a_slow_turn_says_something_small_first(monkeypatch) -> None:
    """1.8 s of silence reads as "it did not hear me" (measured: 9-11 s with tools)."""
    import time as _time

    class SlowLlm(FakeLlm):
        async def chat(self, messages: list[dict], tools: list[dict]):
            if self.fail:
                raise RuntimeError("llm down")
            self.seen.append(messages)
            await asyncio.sleep(0.08)
            for c in self.chunks:
                yield ChatChunk(delta=c)

    llm = SlowLlm(["沈阳今天晴。"])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr("今天天气怎么样"), llms=[llm], ttss=[tts])
    assistant = AssistantConfig(filler_after_s=0.01, filler_text="我查一下。")
    runner = TurnRunner(Config().providers, assistant=assistant)

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    started = _time.monotonic()
    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak, TurnTrace(device_id="living")))
    assert tts.texts[0] == "我查一下。", tts.texts
    assert "沈阳今天晴。" in tts.texts
    assert result.reply  # the real answer still landed
    assert _time.monotonic() - started < 2.0
