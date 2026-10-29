"""0.8.7 A card: aliases on the music path, console `names`, short-answer guard.

Four measured problems, one file:

* `AliasStore.apply` existed but had no caller (dead code): a nickname only worked
  when the *artist-only* search found nothing and the model was asked.
* the console's `/music/search` did not pass `names`, so it never saw aliases.
* after barge-in 「行」「嗯」「好」 were refused by the `music_request` guard as "not
  a song request", even though the user was answering the previous turn.
* `music_aliases.json` was written 0600 (and on fnOS was 000 via ACL).
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_turn import FakeAsr, FakeTts, ScriptedLlm, patch_chains

from rui.config.schema import Config
from rui.music import query as q
from rui.music.history import PlayHistory
from rui.music.model import Track
from rui.music.names import AliasStore, NameResolver
from rui.music.service import MusicService
from rui.music.tools import build_music_tools
from rui.providers.base import ChatChunk
from rui.session import rules
from rui.session.turn import TurnRunner
from rui.tools.contract import InteractionSpec, Permission, ToolResult, ToolSpec
from rui.trace import TurnTrace
from rui.web.api.app import create_app


class FakeSource:
    """Records every search term; answers from a fixed term map."""

    name = "fake"

    def __init__(self, songs: dict[str, list[Track]] | None = None) -> None:
        self.songs = songs or {}
        self.queries: list[str] = []

    async def search(self, term: str, limit: int) -> list[Track]:
        self.queries.append(term)
        return list(self.songs.get(term, []))

    async def random(self, limit: int) -> list[Track]:
        return []

    async def favorites(self) -> list[Track]:
        return []

    async def star(self, track_id: str, on: bool) -> bool:
        return True

    async def resolve(self, track: Track) -> Path:
        return Path("/tmp/rui-alias-test.pcm")


class FakeNames:
    """NameResolver stand-in: a fixed candidate map, no model call."""

    def __init__(self, mapping: dict[str, list[str]], tmp_path: Path) -> None:
        self.mapping = mapping
        self.aliases = AliasStore(tmp_path / "resolver_aliases.json")
        self.asked: list[str] = []

    async def candidates(self, name: str) -> list[str]:
        self.asked.append(name)
        return self.mapping.get(name, [])


def _service(tmp_path: Path, source: FakeSource,
             names: NameResolver | FakeNames | None = None) -> MusicService:
    sent: list[object] = []

    async def sender(message: object) -> None:
        sent.append(message)

    return MusicService(
        source, PlayHistory(tmp_path / "history.json"), sender, Config().music,
        names=names or NameResolver(AliasStore(tmp_path / "music_aliases.json")),
    )


def _console(tmp_path: Path, service: MusicService) -> TestClient:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    client.__enter__()
    client.app.state.music = service
    return client


# ---------------------------------------------------------------------- aliases

def test_apply_rewrites_longest_key_first(tmp_path: Path) -> None:
    store = AliasStore(tmp_path / "music_aliases.json")
    store.remember("薯条匠", "薯条酱")
    store.remember("薯条匠的歌", "薯条酱的歌")
    assert store.apply("放点薯条匠的歌") == "放点薯条酱的歌"
    # A shorter key must not fire inside the already-replaced longer one.
    assert store.apply("薯条匠的歌") == "薯条酱的歌"


def test_the_planner_alone_does_not_rewrite_anything() -> None:
    """Aliases are a music-entry behaviour, not a global text replacement."""
    assert q.plan("放点薯条匠的歌").artist == "薯条匠"


def test_voice_play_rewrites_before_planning(tmp_path: Path) -> None:
    source = FakeSource({"薯条酱": [
        Track(id="fake:1", title="薯条歌", artist="薯条酱", duration=200)]})
    service = _service(tmp_path, source)
    service.names.aliases.remember("薯条匠", "薯条酱")
    handler = {spec.name: fn for spec, fn in
               build_music_tools(Config(), service)}["local.music_play"]

    result = asyncio.run(handler({"query": "放点薯条匠的歌"}))

    assert result.status.startswith("ok"), result
    assert source.queries, "the library must have been asked"
    assert not any("薯条匠" in term for term in source.queries), source.queries
    assert any("薯条酱" in term for term in source.queries), source.queries


def test_console_search_uses_the_same_rewrite(tmp_path: Path) -> None:
    source = FakeSource({"薯条酱": [
        Track(id="fake:1", title="薯条歌", artist="薯条酱", duration=200)]})
    service = _service(tmp_path, source)
    service.names.aliases.remember("薯条匠", "薯条酱")
    client = _console(tmp_path, service)
    try:
        response = client.get("/api/v1/music/search", params={"q": "薯条匠"})
        assert response.status_code == 200, response.text
        assert [track["id"] for track in response.json()["tracks"]] == ["fake:1"]
        assert not any("薯条匠" in term for term in source.queries)
        assert any("薯条酱" in term for term in source.queries)
    finally:
        client.__exit__(None, None, None)


def test_console_search_passes_names_to_the_search_fallback(tmp_path: Path) -> None:
    """`_search(..., names=service.names)` - without it the console had no aliases."""
    source = FakeSource({"Rammstein": [
        Track(id="fake:2", title="Benzin", artist="Rammstein", duration=200)]})
    names = FakeNames({"战车乐队": ["Rammstein"]}, tmp_path)
    service = _service(tmp_path, source, names=names)
    client = _console(tmp_path, service)
    try:
        response = client.get("/api/v1/music/search",
                              params={"q": "播放战车乐队的歌"})
        assert response.status_code == 200, response.text
        assert [track["id"] for track in response.json()["tracks"]] == ["fake:2"]
        assert names.asked == ["战车乐队"]
        assert names.aliases.get("战车乐队") == "Rammstein"
    finally:
        client.__exit__(None, None, None)


# ------------------------------------------------------------- short answers

@pytest.mark.parametrize(("text", "state", "action"), [
    ("行", "playing", "hold"),
    ("嗯", "playing", "hold"),
    ("好", "paused", "resume"),
    ("继续", "paused", "resume"),
    ("别停", "playing", "hold"),
    ("换一首", "playing", "next"),
    ("换一首", "paused", "next"),
    ("停止", "playing", "stop"),
    # These contain a play/listen verb but are still answers when media is paused:
    # the guard checks the answer sets before `looks_like_music_request`.
    ("放吧", "paused", "resume"),
    ("听吧", "playing", "hold"),
    # No media context: an answer word is not a music command.
    ("行", "", ""),
    ("行", "idle", ""),
    # Not in the closed sets (or too long): still not an answer.
    ("今天天气", "playing", ""),
    ("好的时光", "playing", ""),
    ("行的", "playing", ""),
])
def test_music_answer_action(text: str, state: str, action: str) -> None:
    assert rules.music_answer_action(text, state) == action


def _tool_spec(name: str, **kw: object) -> ToolSpec:
    return ToolSpec(name=f"local.{name}", description=name, permission=Permission.READ,
                    interaction=InteractionSpec(**kw))  # type: ignore[arg-type]


class _Turn:
    """Run one guard turn with fake transport handlers and record what ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.llm: ScriptedLlm | None = None

    def run(self, monkeypatch: pytest.MonkeyPatch, transcript: str,
            media_state: str | None) -> tuple[str, list[dict]]:
        async def play(_args: dict) -> ToolResult:
            self.calls.append("play")
            return ToolResult(status="ok")

        def emit(which: str):
            async def handler(_args: dict) -> str:
                self.calls.append(which)
                return "好的。"
            return handler

        tools: list[tuple[ToolSpec, object]] = [
            (_tool_spec("music_play", exec={"ok": "music.started"},
                        answer="silent", guard="music_request"), play),
        ]
        for which in ("resume", "next", "stop"):
            tools.append((_tool_spec(f"music_{which}"), emit(which)))

        self.llm = ScriptedLlm([
            [ChatChunk(delta="", finish_reason="tool_calls", tool_calls=[
                {"id": "t1", "name": "music_play", "arguments": '{"query": "x"}'}])],
            [ChatChunk(delta="好，继续。")],
        ])
        patch_chains(monkeypatch, asr=FakeAsr(transcript), llms=[self.llm],
                     ttss=[FakeTts()])
        runner = TurnRunner(
            Config().providers, tools=tools,
            media_context=(lambda: media_state) if media_state is not None else None)
        result = asyncio.run(runner.run(
            b"\x00\x00" * 800, _speak, TurnTrace(device_id="living"),
            transcript=transcript))
        return result.reply, self.llm.seen[1]["messages"]


async def _speak(_pcm: bytes, *, segment_start: bool = False) -> None:
    return None


def test_guard_resumes_paused_music_for_a_short_yes(monkeypatch) -> None:
    turn = _Turn()
    reply, _messages = turn.run(monkeypatch, "行", "paused")
    assert turn.calls == ["resume"], turn.calls
    assert reply == "好，继续。"


def test_guard_holds_playing_music_for_a_short_yes(monkeypatch) -> None:
    turn = _Turn()
    _reply, messages = turn.run(monkeypatch, "好", "playing")
    assert turn.calls == [], "already playing: nothing must be re-played"
    assert any("应答上一轮" in str(message.get("content", ""))
               for message in messages if message.get("role") == "tool")


@pytest.mark.parametrize(("text", "state", "expected"), [
    ("换一首", "playing", "next"),
    ("停止", "paused", "stop"),
])
def test_guard_routes_next_and_stop(monkeypatch, text: str, state: str,
                                   expected: str) -> None:
    turn = _Turn()
    turn.run(monkeypatch, text, state)
    assert turn.calls == [expected], turn.calls


def test_paused_fang_ba_resumes_instead_of_replacing_the_queue(monkeypatch) -> None:
    """「放吧」 contains 放, so a looks-like-first guard would play a random batch."""
    turn = _Turn()
    turn.run(monkeypatch, "放吧", "paused")
    assert turn.calls == ["resume"], turn.calls


def test_no_media_context_keeps_the_old_refusal(monkeypatch) -> None:
    turn = _Turn()
    _reply, messages = turn.run(monkeypatch, "行", None)
    assert turn.calls == [], "no media context: 行 must not start music"
    tool_text = " ".join(str(message.get("content", "")) for message in messages
                         if message.get("role") == "tool")
    assert "没有点歌动词" in tool_text
    assert "应答上一轮" not in tool_text


def test_a_weather_question_is_still_refused_with_music_playing(monkeypatch) -> None:
    turn = _Turn()
    _reply, messages = turn.run(monkeypatch, "今天天气", "playing")
    assert turn.calls == []
    tool_text = " ".join(str(message.get("content", "")) for message in messages
                         if message.get("role") == "tool")
    assert "没有点歌动词" in tool_text


# ---------------------------------------------------------------- permissions

def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_remember_writes_the_alias_file_0640(tmp_path: Path) -> None:
    path = tmp_path / "music_aliases.json"
    AliasStore(path).remember("薯条匠", "薯条酱")
    assert json.loads(path.read_text(encoding="utf-8")) == {"薯条匠": "薯条酱"}
    assert _mode(path) == 0o640


def test_loading_repairs_a_legacy_0600_file(tmp_path: Path) -> None:
    path = tmp_path / "music_aliases.json"
    path.write_text('{"a": "b"}', encoding="utf-8")
    os.chmod(path, 0o600)
    assert AliasStore(path).get("a") == "b"
    assert _mode(path) == 0o640


def test_a_000_file_is_repaired_before_reading(tmp_path: Path) -> None:
    """fnOS left the file 000: read-then-chmod lost every alias (and rewrote empty)."""
    path = tmp_path / "music_aliases.json"
    path.write_text('{"薯条匠": "薯条酱"}', encoding="utf-8")
    os.chmod(path, 0o000)
    store = AliasStore(path)
    assert store.get("薯条匠") == "薯条酱", "the old aliases must survive the repair"
    assert store.apply("放点薯条匠的歌") == "放点薯条酱的歌"
    assert _mode(path) == 0o640
    # A later remember must not overwrite the loaded table with an empty one.
    store.remember("战车乐队", "Rammstein")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"薯条匠": "薯条酱", "战车乐队": "Rammstein"}


def test_a_failed_chmod_only_logs(tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "music_aliases.json"
    path.write_text('{"a": "b"}', encoding="utf-8")

    def boom(_path: object, _mode: int) -> None:
        raise OSError("read-only fs")

    monkeypatch.setattr("rui.music.names.os.chmod", boom)
    store = AliasStore(path)   # must not raise
    assert store.get("a") == "b"


def test_the_runbook_records_the_permission_requirement() -> None:
    doc = (Path(__file__).resolve().parents[1]
           / "docs" / "music-alias-0.8.7.md").read_text(encoding="utf-8")
    assert "chmod 640" in doc
    assert "music_aliases.json" in doc
    # Container default system.data_dir, and the compose mount it corresponds to.
    assert "/var/lib/rui/music_aliases.json" in doc
    assert "./data/music_aliases.json" in doc
