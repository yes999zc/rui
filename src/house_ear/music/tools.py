"""The music tools: intent in, one short line out, playback started by the service.

Contract copied from the old build: the handler's return value is spoken verbatim,
so the wording is part of the tool - a failure says 「曲库里没找到」, never an
English exception. Playback starts inside the handler: the plane queues a
play_file behind the spoken line (dialogue owns the speaker), so the song begins
the moment the short confirm ends instead of colliding with it.

The online fallback is an interface stub on purpose this round: when a second
source lands, it slots into `_search` and nothing above this line changes.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from ..config.schema import Config
from ..tools.contract import Permission, ToolSpec
from . import query as q
from .model import Track
from .service import MusicService
from .source import MusicSource, SourceError

log = logging.getLogger(__name__)


async def _search(source: MusicSource, p: q.QueryPlan) -> list[Track]:
    """Where a second source will plug in: today only the NAS library."""
    try:
        if p.artist:
            hits = await source.search(p.title, 25)
            keep = [t for t in hits if q.accepts(t.artist, p.artist)]
            if keep:
                return keep
            pool = await source.search(p.artist, 25)
            if pool:
                return pool
            return [t for t in hits if q.accepts(t.label, p.title)]
        return [t for t in await source.search(p.term, 25) if q.accepts(t.label, p.title)]
    except SourceError as exc:
        log.info("music search failed: %s", exc)
        return []


def _announce(tracks: list[Track], started: Track | None) -> str:
    if started is None:
        return "歌都在，但一首也没取出来，过会儿再试一次吧。"
    more = f"，共 {len(tracks)} 首" if len(tracks) > 1 else ""
    return f"好，放《{started.title}》-{started.artist}{more}。"


def build_music_tools(cfg: Config, service: MusicService) -> list[tuple[ToolSpec, Any]]:
    """The LLM-facing tool set. Registered only when `music.enabled`."""
    source = service.source
    batch = cfg.music.queue_batch

    async def music_play(args: dict[str, Any]) -> str:
        p = q.plan(str(args.get("query") or ""))
        try:
            tracks = await source.favorites() if p.favorites else (
                await source.random(batch) if p.random else await _search(source, p))
        except SourceError as exc:
            log.info("music_play: %s", exc)
            return "曲库这会儿连不上，过一会儿再让我放。"
        if not tracks:
            want = p.title or p.term
            return f"曲库里没找到「{want}」。" if want else "曲库里现在没有能放的。"
        started = await service.play(tracks)
        return _announce(tracks, started)

    async def music_pause(_args: dict[str, Any]) -> str:
        return "好，先停这儿。" if await service.pause() else "现在没有在放的歌。"

    async def music_resume(_args: dict[str, Any]) -> str:
        return "接着放。" if await service.resume() else "现在没有暂停着的歌。"

    async def music_next(_args: dict[str, Any]) -> str:
        t = await service.next()
        return f"好，下一首《{t.title}》。" if t else "已经是最后一首了。"

    async def music_prev(_args: dict[str, Any]) -> str:
        t = await service.prev()
        return f"好，回到《{t.title}》。" if t else "已经是第一首了。"

    async def music_stop(_args: dict[str, Any]) -> str:
        await service.stop()
        return "好，不放了。"

    async def music_favorite(args: dict[str, Any]) -> str:
        action = str(args.get("action") or "")
        current = service.current
        if action == "list":
            try:
                favs = await source.favorites()
            except SourceError:
                return "收藏这会儿读不出来。"
            if not favs:
                return "还没有收藏的歌，播放时说「收藏这首歌」。"
            names = "、".join(t.label for t in favs[:5])
            return f"收藏了 {len(favs)} 首，比如：{names}。"
        if current is None:
            return "现在没有在播的歌，想收藏哪首就先放它。"
        on = action != "remove"
        ok = await source.star(current.id, on)
        return (f"已收藏《{current.title}》。" if on else f"取消收藏《{current.title}》。") \
            if ok else "收藏没成功，服务器没回应。"

    async def music_play_favorites(_args: dict[str, Any]) -> str:
        try:
            favs = await source.favorites()
        except SourceError:
            return "收藏这会儿读不出来。"
        if not favs:
            return "还没有收藏的歌，先说「收藏这首歌」。"
        random.shuffle(favs)
        started = await service.play(favs)
        return _announce(favs, started)

    async def music_taste(_args: dict[str, Any]) -> str:
        top = service.history.top(3)
        last = service.history.last()
        if not top:
            return "还没怎么放过歌，先来一首？"
        names = "、".join(f"{a} {n} 次" for a, n in top)
        tail = f"最近一次放的是《{last.get('title', '')}》。" if last else ""
        return f"你听得最多的是：{names}。{tail}"

    return [
        (ToolSpec(
            name="local.music_play", permission=Permission.WRITE,
            description="放歌/听音乐/来点音乐/放某歌手或某首歌（query 传用户原话整句，"
                        "不要先拆词也不要先用别的工具试探）。说「收藏的歌」时 action 传 favorites。",
            input_schema={"type": "object", "properties": {
                "query": {"type": "string", "description": "用户原话，如 帮我放周杰伦的晴天"},
                "action": {"type": "string", "enum": ["favorites"], "description": "播收藏列表"}},
                "required": ["query"]},
        ), music_play),
        (ToolSpec(
            name="local.music_pause", permission=Permission.WRITE,
            description="暂停音乐（「暂停/停一下（在放歌时）」）",
        ), music_pause),
        (ToolSpec(
            name="local.music_resume", permission=Permission.WRITE,
            description="继续播放暂停着的音乐（「继续/接着放」）",
        ), music_resume),
        (ToolSpec(
            name="local.music_next", permission=Permission.WRITE,
            description="切下一首（「下一首/换一首」）",
        ), music_next),
        (ToolSpec(
            name="local.music_prev", permission=Permission.WRITE,
            description="回上一首（「上一首」）",
        ), music_prev),
        (ToolSpec(
            name="local.music_stop", permission=Permission.WRITE,
            description="停止播放音乐（「别放了/停止播放/关掉音乐」）",
        ), music_stop),
        (ToolSpec(
            name="local.music_favorite", permission=Permission.WRITE,
            description="收藏/取消收藏当前在播的歌，或列出收藏（action: add/remove/list）",
            input_schema={"type": "object", "properties": {
                "action": {"type": "string", "enum": ["add", "remove", "list"]}},
                "required": ["action"]},
        ), music_favorite),
        (ToolSpec(
            name="local.music_play_favorites", permission=Permission.WRITE,
            description="放收藏的歌（「放我收藏的/来点我喜欢的」）",
        ), music_play_favorites),
        (ToolSpec(
            name="local.music_taste", permission=Permission.READ,
            description="最近爱听什么/最常听谁（播放统计）",
        ), music_taste),
    ]
