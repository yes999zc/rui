"""The music tools under the V-line contract (docs/interaction.md).

Every assertion goes through the same render path the turn runner uses:
handler -> ToolResult -> InteractionSpec.exec[status] -> PhraseBook. Asserting
against `phrases.book.render(...)` is the point - the words live in one place,
and these tests prove the tools no longer carry their own.
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

from house_ear.config.schema import Config
from house_ear.music.history import PlayHistory
from house_ear.music.model import Track
from house_ear.music.service import MusicService
from house_ear.music.source import SourceError
from house_ear.music.tools import build_music_tools
from house_ear.session import phrases


class FlakySource:
    name = "fake"
    down = False
    no_refill = False   # deterministic transport tests: the queue stays what it was

    async def search(self, term: str, limit: int) -> list[Track]:
        if self.down:
            raise SourceError("nas unreachable")
        if "晴天" not in term:
            return []
        return [Track(id="subsonic:1", title="晴天", artist="周杰伦", duration=269)]

    async def random(self, limit: int) -> list[Track]:
        if self.down:
            raise SourceError("nas unreachable")
        if self.no_refill:
            return []
        return [Track(id=f"subsonic:r{i}", title=f"R{i}", artist="A", duration=200)
                for i in range(limit)]

    async def favorites(self) -> list[Track]:
        if self.down:
            raise SourceError("nas unreachable")
        return [Track(id="subsonic:f1", title="Fav", artist="A", duration=200)]

    async def star(self, track_id: str, on: bool) -> bool:
        return True

    async def resolve(self, track: Track) -> Path:
        return Path(f"/tmp/music/{track.id}.pcm")


class Recorder:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def __call__(self, msg: object) -> None:
        self.sent.append(msg)


def make(tmp_path: Path, cfg: Config | None = None):
    cfg = cfg or Config()
    cfg.music.enabled = True
    source = FlakySource()
    history = PlayHistory(tmp_path / "h.json", keep=10)
    service = MusicService(source, history, Recorder(), cfg.music)
    tools = dict((spec.llm_name, handler)
                 for spec, handler in build_music_tools(cfg, service))
    specs = dict((spec.llm_name, spec) for spec, _ in build_music_tools(cfg, service))
    return service, source, tools, specs


def say(specs: dict, name: str, result: object) -> str:
    """The turn runner's render, verbatim: exec[status] -> PhraseBook."""
    from house_ear.tools.contract import ToolResult

    assert isinstance(result, ToolResult), f"{name} returned {type(result)}"
    spec = specs[name]
    assert spec.interaction is not None, f"{name} has no InteractionSpec"
    key = spec.interaction.exec.get(result.status)
    assert key, f"{name} has no exec mapping for status {result.status!r}"
    assert phrases.book.has(key), f"{name} points at unknown phrase {key!r}"
    return phrases.book.render(key, **result.payload)


def test_play_by_name_renders_the_started_phrase(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, tools, specs = make(tmp_path)
        out = say(specs, "music_play", await tools["music_play"](
            {"query": "帮我放周杰伦的晴天"}))
        assert out == phrases.book.render(
            "music.started_one", title="晴天", artist="周杰伦", count=1)
        assert service.state == "playing"

    asyncio.run(run())


def test_every_exec_key_exists_and_every_phrase_is_reachable(tmp_path: Path) -> None:
    """A typo in an exec map or the book fails here, not in a living room."""
    _, _, _, specs = make(tmp_path)
    for name, spec in specs.items():
        for status, key in spec.interaction.exec.items():  # type: ignore[union-attr]
            assert phrases.book.has(key), f"{name}.{status} -> missing {key}"


def test_a_dead_library_degrades_through_the_book(tmp_path: Path) -> None:
    async def run() -> None:
        _, source, tools, specs = make(tmp_path)
        source.down = True
        out = say(specs, "music_play", await tools["music_play"]({"query": "放首歌"}))
        assert out == phrases.book.render("music.unreachable")

        source.down = False
        out2 = say(specs, "music_play", await tools["music_play"]({"query": "从未收录"}))
        assert out2 == phrases.book.render("music.not_found", want="从未收录")

    asyncio.run(run())


def test_transports_render_their_system_phrases(tmp_path: Path) -> None:
    async def run() -> None:
        service, source, tools, specs = make(tmp_path)
        source.no_refill = True                       # a one-track queue stays one
        await tools["music_play"]({"query": "放周杰伦的晴天"})
        assert say(specs, "music_pause", await tools["music_pause"]({})) \
            == phrases.book.render("music.paused")
        assert say(specs, "music_resume", await tools["music_resume"]({})) \
            == phrases.book.render("music.resumed")
        assert say(specs, "music_next", await tools["music_next"]({})) \
            == phrases.book.render("music.last_track")
        assert say(specs, "music_stop", await tools["music_stop"]({})) \
            == phrases.book.render("music.stopped")
        assert service.state == "idle"

    asyncio.run(run())


def test_favorite_and_taste_render(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, tools, specs = make(tmp_path)
        assert say(specs, "music_favorite",
                   await tools["music_favorite"]({"action": "add"})) \
            == phrases.book.render("music.no_current")
        await tools["music_play"]({"query": "放周杰伦的晴天"})
        assert say(specs, "music_favorite",
                   await tools["music_favorite"]({"action": "add"})) \
            == phrases.book.render("music.starred", title="晴天")
        listed = say(specs, "music_favorite", await tools["music_favorite"]({"action": "list"}))
        assert "Fav" in listed and listed == phrases.book.render(
            "music.favorites_count", count=1, names="Fav - A")
        tasted = say(specs, "music_taste", await tools["music_taste"]({}))
        assert "周杰伦" in tasted

    asyncio.run(run())


def test_the_music_module_carries_no_wording_of_its_own() -> None:
    """V1's grep acceptance, as a test: no Chinese speech literal in music/tools.py.

    Tool *descriptions* (the `spec(...)` calls' description argument) are the
    model-facing contract, not speech - they stay. Everything else that reaches
    a user belongs in session/phrases.py.
    """
    source = Path("src/house_ear/music/tools.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed: set[int] = set()   # node ids of the description arguments
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "spec":
            if len(node.args) >= 2:
                allowed.add(id(node.args[1]))
            for kw in node.keywords:
                if kw.arg == "description":
                    allowed.add(id(kw.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in allowed):
            assert not re.search(r"[\u4e00-\u9fff]", node.value), (
                f"Chinese string literal at line {node.lineno}: {node.value[:30]!r} - "
                "speech belongs in session/phrases.py"
            )
