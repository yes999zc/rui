"""The tool layer: the wording IS the contract (a failure says 曲库里没找到,
never an exception), and playback starts inside the handler."""
from __future__ import annotations

import asyncio
from pathlib import Path

from house_ear.config.schema import Config
from house_ear.music import query as q  # noqa: F401 - plan used via tools
from house_ear.music.history import PlayHistory
from house_ear.music.model import Track
from house_ear.music.service import MusicService
from house_ear.music.source import SourceError
from house_ear.music.tools import build_music_tools


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


def make(cfg: Config | None = None, tmp: Path | None = None):
    cfg = cfg or Config()
    cfg.music.enabled = True
    source = FlakySource()
    history = PlayHistory(Path(tmp or "/tmp/he-test-hist.json"), keep=10)
    service = MusicService(source, history, Recorder(), cfg.music)
    tools = dict((spec.llm_name, handler) for spec, handler in build_music_tools(cfg, service))
    return service, source, tools


def test_play_by_name_answers_with_the_track_and_starts_it(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, tools = make(tmp=tmp_path)
        out = await tools["music_play"]({"query": "帮我放周杰伦的晴天"})
        assert out == "好，放《晴天》-周杰伦。", "one track does not get a count"
        assert service.state == "playing"

    asyncio.run(run())


def test_a_dead_library_is_a_sentence_not_an_exception(tmp_path: Path) -> None:
    async def run() -> None:
        service, source, tools = make(tmp=tmp_path)
        source.down = True
        assert "连不上" in await tools["music_play"]({"query": "放首歌"})
        source.down = False
        assert "没找到" in await tools["music_play"]({"query": "不存在的歌名"})

    asyncio.run(run())


def test_transport_controls_speak_short_lines(tmp_path: Path) -> None:
    async def run() -> None:
        service, source, tools = make(tmp=tmp_path)
        source.no_refill = True                        # the one-track queue stays one
        await tools["music_play"]({"query": "放周杰伦的晴天"})
        assert await tools["music_pause"]({}) == "好，先停这儿。"
        assert await tools["music_resume"]({}) == "接着放。"
        assert await tools["music_next"]({}) == "已经是最后一首了。"
        assert await tools["music_stop"]({}) == "好，不放了。"
        assert service.state == "idle"

    asyncio.run(run())


def test_favorite_stars_the_current_track(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, tools = make(tmp=tmp_path)
        assert "没有在播" in await tools["music_favorite"]({"action": "add"})
        await tools["music_play"]({"query": "放周杰伦的晴天"})
        assert await tools["music_favorite"]({"action": "add"}) == "已收藏《晴天》。"
        assert "比如" in await tools["music_favorite"]({"action": "list"})

    asyncio.run(run())


def test_taste_reads_the_persisted_history(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, tools = make(tmp=tmp_path)
        assert "还没怎么放过" in await tools["music_taste"]({})
        await tools["music_play"]({"query": "放周杰伦的晴天"})
        out = await tools["music_taste"]({})
        assert "周杰伦" in out and "《晴天》" in out

    asyncio.run(run())
