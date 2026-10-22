"""S5c leftovers: the idle goodbye, its two wordings, and the music filler skip.

Everything here runs on a fake clock. The behaviour under test is "15 s of silence",
and a test that waits 15 s to prove it is a test nobody runs.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from house_ear.config.schema import AssistantConfig, Config, SessionConfig
from house_ear.providers.base import ChatChunk, Transcript
from house_ear.session.filler import is_music_tool, is_music_turn, should_speak_filler
from house_ear.session.idle import IdleWatch, farewell_line
from house_ear.session.turn import TurnRunner
from house_ear.tools.contract import Permission, ToolSpec
from house_ear.trace import TurnTrace


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---------------------------------------------------------------- the idle window


def test_the_default_window_is_fifteen_seconds_and_fires_once() -> None:
    clock = FakeClock()
    idle = IdleWatch(clock=clock)
    idle.touch()

    clock.advance(14.9)
    assert not idle.expired()
    assert idle.remaining() == pytest.approx(0.1)

    clock.advance(0.1)
    assert idle.expired()
    assert idle.tick() is True
    # one goodbye, not one per poll: the second tick cannot fire
    assert idle.tick() is False
    assert not idle.active
    assert idle.remaining() == float("inf")


def test_activity_moves_the_window_out() -> None:
    idle = IdleWatch(15.0, clock=FakeClock())
    idle.touch(now=0.0)
    idle.touch(now=10.0)
    assert not idle.expired(now=24.9)
    assert idle.expired(now=25.0)


def test_a_zero_timeout_never_fires() -> None:
    """0 is the console's way of turning the timeout off, beside `farewell: off`."""
    idle = IdleWatch(0.0, clock=FakeClock())
    idle.touch(now=0.0)
    assert not idle.expired(now=10_000.0)
    assert idle.tick(now=10_000.0) is False
    assert idle.remaining(now=10_000.0) == float("inf")


def test_no_session_open_means_nothing_to_expire() -> None:
    idle = IdleWatch(15.0, clock=FakeClock())
    assert idle.tick(now=1_000_000.0) is False
    assert idle.remaining(now=1_000_000.0) == float("inf")


def test_stop_closes_the_session_and_a_new_word_reopens_it() -> None:
    clock = FakeClock()
    idle = IdleWatch(15.0, clock=clock)
    idle.start()
    idle.stop()
    clock.advance(60.0)
    assert idle.tick() is False

    idle.touch(now=clock.t)   # the next request is a new session
    assert idle.active
    clock.advance(15.0)
    assert idle.tick() is True


# ---------------------------------------------------------------- the two goodbyes


def test_the_default_goodbye_is_the_short_line() -> None:
    session = SessionConfig()
    assert session.timeout_s == 15.0
    assert session.farewell == "short"
    assert farewell_line(session) == "没事的话，我先下啦。"


def test_the_full_goodbye_invites_a_follow_up() -> None:
    assert farewell_line(SessionConfig(farewell="full")) == "我先下啦，有事喊我。"


def test_the_goodbye_can_be_switched_off() -> None:
    assert farewell_line(SessionConfig(farewell="off")) == ""


def test_both_lines_are_config_not_code() -> None:
    assert farewell_line(SessionConfig(farewell_text="那我先撤。")) == "那我先撤。"
    full = SessionConfig(farewell="full", farewell_text_full="随时喊我。")
    assert farewell_line(full) == "随时喊我。"


def test_a_blank_goodbye_for_the_selected_mode_is_rejected() -> None:
    """A silent exit while the config says "short" is exactly how a config starts lying."""
    with pytest.raises(ValidationError):
        SessionConfig(farewell_text="   ")
    with pytest.raises(ValidationError):
        SessionConfig(farewell="full", farewell_text_full="")
    # the unused line may be blank: switching mode later will be validated then
    SessionConfig(farewell="short", farewell_text_full="")


# ---------------------------------------------------------------- runner wiring


class FakeAsr:
    def __init__(self, text: str = "今天天气怎么样") -> None:
        self.text = text

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text=self.text)


class FakeLlm:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.seen.append(messages)
        for c in self.chunks:
            yield ChatChunk(delta=c)


class FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield b"\x01\x00" * 320


def patch_chains(monkeypatch, *, asr, llms, ttss) -> None:
    import house_ear.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", asr)])
    monkeypatch.setattr(turn, "llm_chain", lambda providers: [(f"l{i}", p) for i, p in enumerate(llms)])
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [(f"t{i}", p) for i, p in enumerate(ttss)])


async def _discard(pcm: bytes) -> None:
    return None


def test_a_finished_turn_restarts_the_countdown_at_the_end(monkeypatch) -> None:
    """The window must start after the answer, not when the question arrived."""
    clock = FakeClock(0.0)

    class SlowLlm(FakeLlm):
        async def chat(self, messages: list[dict], tools: list[dict]):
            clock.advance(5.0)   # the turn itself takes five seconds
            for c in self.chunks:
                yield ChatChunk(delta=c)

    patch_chains(monkeypatch, asr=FakeAsr("现在几点"), llms=[SlowLlm(["一点了。"])], ttss=[FakeTts()])
    runner = TurnRunner(Config().providers, idle=IdleWatch(15.0, clock=clock))

    asyncio.run(runner.run(b"\x00\x00" * 800, _discard, TurnTrace(device_id="living")))

    assert runner.idle.active
    # touched at the end of the turn (clock 5), not at its beginning (clock 0)
    assert runner.idle.remaining() == pytest.approx(15.0)


def test_a_running_turn_is_not_an_idle_session(monkeypatch) -> None:
    """A 20 s tool lookup must not get a goodbye at 15 s while it is still working."""
    clock = FakeClock(0.0)
    tts = FakeTts()

    class PollingLlm(FakeLlm):
        async def chat(self, messages: list[dict], tools: list[dict]):
            clock.advance(20.0)   # longer than the 15 s window
            polled = await runner.check_idle(_discard, TurnTrace(device_id="living"))
            assert polled == "", "the poller said goodbye in the middle of a turn"
            for c in self.chunks:
                yield ChatChunk(delta=c)

    patch_chains(monkeypatch, asr=FakeAsr("现在几点"), llms=[PollingLlm(["一点了。"])], ttss=[tts])
    runner = TurnRunner(Config().providers, idle=IdleWatch(15.0, clock=clock))

    result = asyncio.run(runner.run(b"\x00\x00" * 800, _discard, TurnTrace(device_id="living")))

    assert result.reply == "一点了。"
    assert tts.texts == ["一点了。"]
    # the turn itself counted as activity, so the window is open again after it
    assert runner.idle.active and runner.idle.remaining() == pytest.approx(15.0)


def test_the_idle_timeout_says_goodbye_exactly_once(monkeypatch) -> None:
    clock = FakeClock(100.0)
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr("在吗"), llms=[FakeLlm(["在。"])], ttss=[tts])
    runner = TurnRunner(Config().providers, idle=IdleWatch(15.0, clock=clock))
    runner.idle.start()

    clock.advance(14.0)
    assert asyncio.run(runner.check_idle(_discard, TurnTrace(device_id="living"))) == ""

    clock.advance(1.0)
    said = asyncio.run(runner.check_idle(_discard, TurnTrace(device_id="living")))
    assert said == "没事的话，我先下啦。"
    assert tts.texts == ["没事的话，我先下啦。"]
    assert not runner.idle.active

    clock.advance(60.0)
    assert asyncio.run(runner.check_idle(_discard, TurnTrace(device_id="living"))) == ""
    assert tts.texts == ["没事的话，我先下啦。"]


def test_off_ends_the_session_without_a_word(monkeypatch) -> None:
    clock = FakeClock()
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr("在吗"), llms=[FakeLlm(["在。"])], ttss=[tts])
    runner = TurnRunner(
        Config().providers,
        session=SessionConfig(farewell="off"),
        idle=IdleWatch(15.0, clock=clock),
    )
    runner.idle.start()
    clock.advance(15.0)

    assert asyncio.run(runner.check_idle(_discard, TurnTrace(device_id="living"))) == ""
    assert tts.texts == []
    assert not runner.idle.active


def test_a_console_edit_reaches_the_window_and_the_wording() -> None:
    runner = TurnRunner(Config().providers)
    assert runner.idle.timeout_s == 15.0

    runner.apply_session(SessionConfig(timeout_s=30.0, farewell="full"))
    assert runner.idle.timeout_s == 30.0
    assert farewell_line(runner.session) == "我先下啦，有事喊我。"


# ---------------------------------------------------------------- filler skip hook


def test_music_tools_are_matched_by_namespace_and_by_name() -> None:
    assert is_music_tool("music_play")
    assert is_music_tool("Music_Search")   # vendor names are case-insensitive here
    assert is_music_tool("play_music")     # listed alias, no prefix
    assert not is_music_tool("weather")
    assert not is_music_tool("")
    assert is_music_turn(["weather", "music_next"])
    assert not is_music_turn([])


def test_the_filler_policy_is_one_predicate() -> None:
    base = {"delay_s": 1.8, "text": "我查一下。"}
    assert should_speak_filler(**base)
    assert not should_speak_filler(**{**base, "audio_started": True})
    assert not should_speak_filler(**{**base, "tool_names": ["music_play"]})
    assert not should_speak_filler(**{**base, "delay_s": 0.0})
    assert not should_speak_filler(**{**base, "text": "   "})
    # no music tool exists in this build yet: ordinary turns behave as before
    assert should_speak_filler(**base, tool_names=["weather", "web_search", "news"])


class ScriptedLlm:
    """One scripted round per call: (delay, chunks). No network, no wall-clock waiting."""

    def __init__(self, rounds: list[tuple[float, list[ChatChunk]]]) -> None:
        self.rounds = list(rounds)

    async def chat(self, messages: list[dict], tools: list[dict]):
        delay, chunks = self.rounds.pop(0)
        if delay:
            await asyncio.sleep(delay)
        for chunk in chunks:
            yield chunk


def _slow_tool_turn(monkeypatch, tool_name: str, transcript: str = "随便") -> FakeTts:
    """A turn whose first round calls `tool_name`, and whose tool takes 250 ms."""
    spec = ToolSpec(name=f"local.{tool_name}", description="x", permission=Permission.READ)

    async def handler(args: dict) -> str:
        await asyncio.sleep(0.25)
        return "ok"

    call = ChatChunk(
        delta="", finish_reason="tool_calls",
        tool_calls=[{"id": "t1", "name": tool_name, "arguments": "{}"}],
    )
    llm = ScriptedLlm([(0.02, [call]), (0.0, [ChatChunk(delta="好了。")])])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr(transcript), llms=[llm], ttss=[tts])
    runner = TurnRunner(
        Config().providers,
        tools=[(spec, handler)],
        assistant=AssistantConfig(filler_after_s=0.05, filler_text="我查一下。"),
    )
    result = asyncio.run(runner.run(b"\x00\x00" * 800, _discard, TurnTrace(device_id="living")))
    assert result.tools == [tool_name]
    return tts


def test_a_music_tool_round_never_hears_the_filler(monkeypatch) -> None:
    """A song starts ~1 s after the call; 「我查一下」 at 1.8 s would land on the first bars."""
    tts = _slow_tool_turn(monkeypatch, "music_play")
    assert tts.texts == ["好了。"]


def test_an_ordinary_slow_tool_round_still_gets_the_filler(monkeypatch) -> None:
    """The skip must be about music only - the weather turn is exactly why filler exists."""
    tts = _slow_tool_turn(monkeypatch, "weather", transcript="今天天气怎么样")
    assert tts.texts[0] == "我查一下。"
    assert "好了。" in tts.texts


def test_the_filler_follows_the_request_not_one_fixed_line(monkeypatch) -> None:
    """9哥's chain: 叮 → 我问 → 我查一下/我想想/好的我看看 → 回答."""
    assert _slow_tool_turn(monkeypatch, "weather", transcript="帮我把客厅灯打开").texts[0] == "好的，我看看。"
    assert _slow_tool_turn(monkeypatch, "weather", transcript="讲个笑话").texts[0] == "我想想。"
    assert _slow_tool_turn(monkeypatch, "weather", transcript="今天新闻").texts[0] == "我查一下。"
