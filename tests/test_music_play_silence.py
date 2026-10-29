"""T2: a failed song request is spoken; silence belongs to success only.

The 2026-10-29 field case: 「放一首叫紫色的雨夜航线第三号的歌」 started an
unrelated cover, the rendered 「曲库里没找到…」 never reached the speaker, and the
turn returned an empty reply. Root cause was the silence rule counting every call
whose spec says `answer="silent"`, without looking at the tool status, so
`empty` / `unreachable` / `unresolved` ended the turn with no speech.

These tests run the real music tools through the real turn runner: the model's
second round reads the tool sentence out verbatim, so `tts.texts` proves the
phrase actually reached the speaker (and the silence test proves it did not).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from test_turn import FakeAsr, FakeTts, patch_chains

from rui.config.schema import Config
from rui.music.history import PlayHistory
from rui.music.model import Track
from rui.music.names import AliasStore
from rui.music.service import MusicService
from rui.music.source import SourceError
from rui.music.tools import build_music_tools
from rui.providers.base import ChatChunk
from rui.session.turn import TurnRunner
from rui.tools.contract import Permission, ToolResult, ToolSpec
from rui.trace import TurnTrace

# The measured utterance: it split into artist 「叫紫色」 + title 「雨夜航线第三号的歌」,
# and the library answered the artist half with an unrelated 云紫色.
FIELD_ASK = "放一首叫紫色的雨夜航线第三号的歌"


class NoNames:
    """No model-side nickname lookup: a failure must stay a failure in a test."""

    def __init__(self, tmp_path: Path) -> None:
        self.aliases = AliasStore(tmp_path / "names.json")

    async def candidates(self, name: str) -> list[str]:
        return []


class ModeSource:
    """One switch per outcome: ok / not_found / unreachable / unresolved."""

    name = "fake"

    def __init__(self) -> None:
        self.mode = "ok"

    async def search(self, term: str, limit: int) -> list[Track]:
        if self.mode == "unreachable":
            raise SourceError("nas unreachable")
        if self.mode == "not_found":
            return [Track(id="fake:x", title="云紫色", artist="云紫", duration=200)]
        return [Track(id="fake:1", title="雨夜航线第三号的歌", artist="叫紫色",
                      duration=200)]

    async def random(self, limit: int) -> list[Track]:
        if self.mode == "unreachable":
            raise SourceError("nas unreachable")
        return []

    async def favorites(self) -> list[Track]:
        return []

    async def star(self, track_id: str, on: bool) -> bool:
        return True

    async def resolve(self, track: Track) -> Path:
        if self.mode == "unresolved":
            raise SourceError("not one track could be fetched")
        return Path("/tmp/rui-t2.pcm")


class Weather:
    name = "local.weather"

    def __init__(self) -> None:
        self.spec = ToolSpec(name="local.weather", description="查天气",
                             permission=Permission.READ)

    async def __call__(self, _args: dict) -> ToolResult:
        return ToolResult.say("沈阳今天晴，1到15度。")


class EchoToolLlm:
    """Round 1 calls the tools; round 2 reads the last tool sentence out verbatim.

    That is the normal reply path a failed play must stay on: whatever the tool
    rendered reaches the model, and the model says it.
    """

    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls
        self.rounds = 0
        self.seen: list[list[dict]] = []

    async def chat(self, messages: list[dict], tools: list[dict]):
        self.rounds += 1
        self.seen.append([dict(m) for m in messages])
        if self.rounds == 1:
            yield ChatChunk(delta="", finish_reason="tool_calls", tool_calls=self.calls)
            return
        content = next((str(m.get("content", "")) for m in reversed(messages)
                        if m.get("role") == "tool"), "")
        yield ChatChunk(delta=content or "没查到。")


def _call(name: str = "music_play", call_id: str = "t1") -> dict:
    return {"id": call_id, "name": name, "arguments": "{}"}


def _turn(tmp_path: Path, monkeypatch, *, mode: str, calls: list[dict],
          transcript: str = FIELD_ASK, weather: bool = False):
    cfg = Config()
    cfg.music.enabled = True
    source = ModeSource()
    source.mode = mode
    sent: list[object] = []

    async def sender(message: object) -> None:
        sent.append(message)

    service = MusicService(source, PlayHistory(tmp_path / "history.json"), sender,
                           cfg.music, names=NoNames(tmp_path))
    played: list[list[str]] = []
    original = service.play

    async def spy(tracks: list[Track], start: int = 0) -> Track | None:
        played.append([t.id for t in tracks])
        return await original(tracks, start=start)

    service.play = spy  # type: ignore[method-assign]
    tools = build_music_tools(cfg, service)
    if weather:
        w = Weather()
        tools.append((w.spec, w))
    llm = EchoToolLlm(calls)
    tts = FakeTts()
    patch_chains(monkeypatch, asr=FakeAsr(transcript), llms=[llm], ttss=[tts])
    runner = TurnRunner(Config().providers, tools=tools)

    async def speak(_pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    result = asyncio.run(runner.run(b"\x00\x00" * 800, speak,
                                    TurnTrace(device_id="living")))
    return result, tts, played, service, llm


def test_not_found_is_spoken_and_nothing_is_played(tmp_path: Path, monkeypatch) -> None:
    result, tts, played, service, llm = _turn(
        tmp_path, monkeypatch, mode="not_found", calls=[_call()])

    spoken = "".join(tts.texts)
    assert spoken, "a failed song lookup must not be a silent turn"
    assert "曲库里没找到" in spoken
    assert "曲库里没找到" in result.reply, result.reply
    # The sentence the model was given is the phrase, not the tool's own wording.
    tool_messages = [m for m in llm.seen[1] if m.get("role") == "tool"]
    assert tool_messages and "曲库里没找到" in tool_messages[-1]["content"]
    assert played == [], "a failed lookup must not reach the player"
    assert service.state == "idle"
    assert service.current is None


def test_unreachable_is_spoken_and_nothing_is_played(tmp_path: Path,
                                                     monkeypatch) -> None:
    # The random shelf reaches the source directly; a title search goes through
    # `_search`, which deliberately degrades a dead library to "not found".
    result, tts, played, service, _llm = _turn(
        tmp_path, monkeypatch, mode="unreachable", calls=[_call()],
        transcript="来点歌")

    spoken = "".join(tts.texts)
    assert "连不上" in spoken, spoken
    assert "连不上" in result.reply
    assert played == []
    assert service.state == "idle"


def test_unresolved_is_spoken_after_the_play_attempt(tmp_path: Path,
                                                     monkeypatch) -> None:
    """`unresolved` is the one status that needs `play()` to be known: the tracks
    were there and every fetch failed. What matters is that the failure is spoken
    and the queue is left empty (no old song replaced by a silent failure)."""
    result, tts, played, service, _llm = _turn(
        tmp_path, monkeypatch, mode="unresolved", calls=[_call()])

    assert played, "the failure is only knowable by trying to resolve"
    spoken = "".join(tts.texts)
    assert "取出来" in spoken, spoken
    assert "取出来" in result.reply
    assert service.state == "idle"
    assert service.current is None


def test_a_started_song_is_still_silent(tmp_path: Path, monkeypatch) -> None:
    result, tts, played, service, _llm = _turn(
        tmp_path, monkeypatch, mode="ok", calls=[_call()])

    assert played and played[0] == ["fake:1"]
    assert service.state == "playing"
    assert result.reply == "", "the song is the answer: no confirmation"
    assert tts.texts == [], "nothing may be synthesised for a started song"


def test_a_failed_song_beside_weather_keeps_the_weather_answer(tmp_path: Path,
                                                               monkeypatch) -> None:
    call = _call()
    weather_call = {"id": "t2", "name": "weather", "arguments": "{}"}
    result, tts, played, _service, _llm = _turn(
        tmp_path, monkeypatch, mode="not_found",
        calls=[call, weather_call], transcript="放首歌，顺便查下今天天气", weather=True)

    assert played == []
    assert "沈阳今天晴" in result.reply, "the weather answer must not be swallowed"
    assert "沈阳今天晴" in "".join(tts.texts)


def test_call_tool_exposes_the_structured_status_not_the_phrase() -> None:
    """The silent rule reads `ToolResult.status`; the rendered sentence is still
    what the model gets. Nothing reverse-parses the wording."""
    from rui.session import phrases
    from rui.tools.contract import InteractionSpec, ToolSpec

    async def handler(_args: dict) -> ToolResult:
        return ToolResult(status="empty").slot(want="紫色")

    async def plain(_args: dict) -> str:
        return "自由文本"

    spec = ToolSpec(name="local.music_play", description="demo",
                    interaction=InteractionSpec(exec={"empty": "music.not_found"},
                                                answer="silent"))
    plain_spec = ToolSpec(name="local.plain", description="demo")
    runner = TurnRunner(Config().providers, tools=[(spec, handler),
                                                  (plain_spec, plain)])

    out = asyncio.run(runner._call_tool({"name": "music_play", "arguments": "{}"}))
    assert out == phrases.book.render("music.not_found", want="紫色")
    assert runner._last_tool_status == "empty"

    assert asyncio.run(runner._call_tool({"name": "plain", "arguments": "{}"})) == "自由文本"
    assert runner._last_tool_status is None
