"""The music tools: intent in, a structured result out, zero wording of their own.

V-line contract (docs/interaction.md §3): every handler returns `ToolResult`
slots and every sentence is rendered from the PhraseBook through the tool's
InteractionSpec - the V1 acceptance is literal, no Chinese speech lives in this
file. Playback starts inside the handler; the plane queues the play_file behind
the spoken line (dialogue owns the speaker), so the song begins the moment the
short confirm ends.

Statuses beyond the base five (`ok_star`, `ok_one`, ...) are wording variants of
success: the exec map is the single place a status becomes a phrase, so two
successes that say different things are two statuses, not a payload hack.

The online fallback stays an interface stub this round: a second source plugs
into `_search` and nothing above this line changes.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from ..config.schema import Config
from ..session import phrases
from ..tools.contract import InteractionSpec, Permission, ToolResult, ToolSpec
from . import query as q
from . import semantic
from .library_index import LibraryIndex
from .model import Track
from .names import NameResolver
from .service import MusicService
from .source import MusicSource, SourceError

log = logging.getLogger(__name__)

PLAY_MAP = {"ok": "music.started", "ok_one": "music.started_one",
            "empty": "music.not_found", "unreachable": "music.unreachable",
            "unresolved": "music.unresolved"}


async def _search(source: MusicSource, p: q.QueryPlan,
                  names: NameResolver | None = None) -> list[Track]:
    """Where a second source will plug in: today only the NAS library."""
    try:
        if p.artist and not p.title:
            # An artist-only ask: search the artist, then keep only that artist.
            # Subsonic's fuzzy search answered 「周杰伦」 with 陈奕迅《十年》 first
            # (measured 09:45) - playing a different artist is worse than saying
            # nothing was found.
            hits = await source.search(p.artist, 40)
            keep = [t for t in hits if q.accepts(t.artist, p.artist)
                    or q.accepts(t.label, p.artist)]
            if keep or names is None:
                return keep
            # Nothing under that name: it may be a nickname (「战车乐队」 is filed
            # as Rammstein). The model translates, the library still decides.
            for alt in await names.candidates(p.artist):
                alt_hits = await source.search(alt, 40)
                matched = [t for t in alt_hits if q.accepts(t.artist, alt)
                           or q.accepts(t.label, alt)]
                if matched:
                    names.aliases.remember(p.artist, alt)
                    return matched
            return []
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


def _started(tracks: list[Track], started: Track | None) -> ToolResult:
    """The queue started (or could not): one track omits the count, by ear.

    `unresolved` is not `unreachable`: the library answered, the tracks were
    there, and not one of them could be fetched - different apology.

    The candidate list rides on `options` (the choose primitive's channel): the
    old build returned only the first track's brief to the model, so it could
    never see that one title matched five versions or report which artist
    played. The exec phrase stays short; the list is for the system layer, not
    the ear.
    """
    if started is None:
        return ToolResult(status="unresolved")
    slots = {"title": started.title, "artist": started.artist, "count": len(tracks)}
    result = ToolResult(status="ok" if len(tracks) > 1 else "ok_one", payload=slots)
    result.options = [{"id": t.id, "title": t.title, "artist": t.artist}
                      for t in tracks[:8]]
    return result


def _started_or(tracks: list[Track], started: Track | None,
                empty: ToolResult) -> ToolResult:
    return _started(tracks, started) if tracks else empty


def build_music_tools(cfg: Config, service: MusicService) -> list[tuple[ToolSpec, Any]]:
    """The LLM-facing tool set. Registered only when `music.enabled`."""
    source = service.source
    batch = cfg.music.queue_batch
    # Per-song language index: built on demand (7-day TTL) by the first language
    # or descriptive ask, then answered from disk. Lives in data_dir, not the PCM
    # cache - losing it means one ~0.6 s walk of 5736 songs, not a re-download.
    index = LibraryIndex(Path(cfg.system.data_dir) / "library_index.json", source)

    def spec(name: str, description: str, exec_map: dict[str, str], *,
             choose: str = "first", filler: str = "none",
             answer: str = "text", guard: str = "",
             permission: Permission = Permission.WRITE,
             input_schema: dict[str, Any] | None = None) -> ToolSpec:
        return ToolSpec(
            name=name, description=description, permission=permission,
            input_schema=input_schema or {},
            interaction=InteractionSpec(exec=exec_map, choose=choose, filler=filler,
                                        answer=answer, guard=guard),
        )

    async def music_play(args: dict[str, Any]) -> ToolResult:
        raw = str(args.get("query") or "")
        p = q.plan(raw)
        # None = this is a name/title/random ask; the literal search handles it.
        desc = semantic.plan(raw) if raw.strip() else None
        try:
            if p.favorites:
                tracks = await source.favorites()
            elif p.random:
                tracks = await source.random(batch)
            else:
                tracks: list[Track] = []
                if q.has_quoted_title(raw):
                    # 《》 always wins, even when the same sentence says 日文 or 摇滚.
                    tracks = await _search(source, p, service.names)
                elif desc is not None:
                    # A title inside a mixed ask beats its descriptor: 「日文歌曲
                    # 留在我身边」 must search the name before playing the shelf
                    # (old build's title-wins rule, music.py:588).
                    if p.title and p.title != q.clean_query(raw):
                        tracks = await _search(
                            source, q.QueryPlan(raw=raw, title=p.title, term=p.title),
                            service.names)
                        if tracks:
                            log.info("music: %r -> title %r beats descriptor %s",
                                     raw, p.title, desc.summary())
                    if not tracks:
                        # Language / genre / keyword / mood / year are one filter
                        # set, not an if-chain; the index answers it without a
                        # literal search (docs/library-index.md).
                        await index.ensure()
                        tracks = await semantic.pool(desc, source, index, limit=batch)
                if not tracks:
                    # The old genre-pool fallback, kept for a not-yet-built index
                    # and for language words the planner does not know.
                    genres = q.language_genres(p.term) if len(p.term) <= 6 else []
                    by_genre = getattr(source, "by_genre", None)
                    if genres and by_genre is not None:
                        for genre in genres:
                            tracks = await by_genre(genre, limit=batch)
                            if tracks:
                                log.info("music: %r -> genre %s (%d tracks)",
                                         p.raw, genre, len(tracks))
                                break
                    if not tracks:
                        tracks = await _search(source, p, service.names)
        except SourceError as exc:
            log.info("music_play: %s", exc)
            return ToolResult(status="unreachable")
        started = await service.play(tracks) if tracks else None
        return _started_or(tracks, started,
                           ToolResult(status="empty").slot(want=p.title or p.term))

    async def music_search(args: dict[str, Any]) -> ToolResult:
        """Read-only look-up: the candidate list the model can actually see.

        The old build had this tool for one reason (tools/music.py:472-489): a
        model asked to decide between five versions of one title needs the list,
        not a play result that only says the queue started. It returns a
        structured payload with no exec phrase - the turn runner hands the JSON
        to the model verbatim, which is the candidate-return channel
        docs/interaction.md §6 listed as missing.
        """
        raw = str(args.get("query") or "").strip()
        if not raw:
            return ToolResult(status="empty", payload={"want": ""})
        if semantic.plan(raw) is not None and not q.has_quoted_title(raw):
            # Not a name: point the model back at the tool that understands it
            # (the old build answered this instead of searching a language word
            # as if it were a title).
            return ToolResult(status="descriptive", payload={
                "want": q.clean_query(raw) or raw, "suggest": "local.music_play"})
        tracks = await _search(source, q.plan(raw), service.names)
        if not tracks:
            return ToolResult(status="empty", payload={"want": raw})
        candidates = [{"id": t.id, "title": t.title, "artist": t.artist}
                      for t in tracks[:8]]
        return ToolResult(status="ok", options=candidates, payload={
            "want": raw, "count": len(tracks), "candidates": candidates})


    async def music_pause(_args: dict[str, Any]) -> ToolResult:
        return ToolResult(status="ok") if await service.pause() else ToolResult(status="empty")

    async def music_resume(_args: dict[str, Any]) -> ToolResult:
        return ToolResult(status="ok") if await service.resume() else ToolResult(status="empty")

    async def music_next(_args: dict[str, Any]) -> ToolResult:
        t = await service.next()
        return ToolResult(status="ok").slot(title=t.title) if t else ToolResult(status="empty")

    async def music_prev(_args: dict[str, Any]) -> ToolResult:
        t = await service.prev()
        return ToolResult(status="ok").slot(title=t.title) if t else ToolResult(status="empty")

    async def music_stop(_args: dict[str, Any]) -> ToolResult:
        await service.stop()
        return ToolResult(status="ok")

    async def music_favorite(args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")
        current = service.current
        if action == "list":
            try:
                favs = await source.favorites()
            except SourceError:
                return ToolResult(status="unreachable")
            if not favs:
                return ToolResult(status="empty_list")
            names = "、".join(t.label for t in favs[:5])
            return ToolResult(status="ok_list").slot(count=len(favs), names=names)
        if current is None:
            return ToolResult(status="empty")
        on = action != "remove"
        ok = await source.star(current.id, on)
        if not ok:
            return ToolResult(status="unreachable")
        return ToolResult(status="ok_star" if on else "ok_unstar").slot(title=current.title)

    async def music_play_favorites(_args: dict[str, Any]) -> ToolResult:
        try:
            favs = await source.favorites()
        except SourceError:
            return ToolResult(status="unreachable")
        random.shuffle(favs)
        started = await service.play(favs) if favs else None
        return _started_or(favs, started, ToolResult(status="empty"))

    async def music_taste(_args: dict[str, Any]) -> ToolResult:
        top = service.history.top(3)
        last = service.history.last()
        if not top:
            return ToolResult(status="empty")
        # Item wording comes from the book too; the tool only joins (the scan test
        # would flag a single Chinese character back in here, and it should).
        names = "、".join(phrases.book.render("music.taste_item", artist=a, plays=n)
                         for a, n in top)
        tail = phrases.book.render("music.taste_tail",
                                   title=(last or {}).get("title", "")) if last else ""
        return ToolResult(status="ok_taste").slot(names=names, tail=tail)

    return [
        (spec("local.music_play",
              "放歌/听音乐/来点音乐/放某歌手或某首歌（query 传用户原话整句，"
              "不要先拆词也不要先用别的工具试探）。描述式点歌（语言/流派/心情/年代，"
              "如「放几首日文歌」「来点安静的钢琴」）也直接调本工具，query 传原话。"
              "说「收藏的歌」时 action 传 favorites。",
              PLAY_MAP, answer="silent", guard="music_request"),
         music_play),
        (spec("local.music_search",
              "在家庭音乐库里查一首具体的歌或一个歌手在不在，只查不播，返回候选列表。"
              "用户问「有没有这首歌」「搜一下」时用。点歌不要用本工具——直接调 "
              "local.music_play 把原话传进去；描述式点歌也不要逐条搜。",
              {}, choose="top3", permission=Permission.READ,
              input_schema={"type": "object", "properties": {
                  "query": {"type": "string",
                            "description": "song title or artist name"}},
                  "required": ["query"]}),
         music_search),
        (spec("local.music_pause",
              "音乐播放中说「暂停/停一下/先停这儿」时**必须调用本工具**真正暂停，"
              "禁止只口头说已暂停。",
              {"ok": "music.paused", "empty": "music.not_playing"}),
         music_pause),
        (spec("local.music_resume",
              "「继续/接着放/恢复播放」时**必须调用本工具**，禁止只口头确认。",
              {"ok": "music.resumed", "empty": "music.not_paused"}),
         music_resume),
        (spec("local.music_next",
              "「下一首/换一首/切歌」时**必须调用本工具**，禁止只口头确认。",
              {"ok": "music.next", "empty": "music.last_track"}),
         music_next),
        (spec("local.music_prev",
              "「上一首/前一首」时**必须调用本工具**，禁止只口头确认。",
              {"ok": "music.prev", "empty": "music.first_track"}),
         music_prev),
        (spec("local.music_stop",
              "「别放了/停止播放/关掉音乐/不放了」时**必须调用本工具**真正停止。",
              {"ok": "music.stopped"}),
         music_stop),
        (spec("local.music_favorite",
              "收藏/取消收藏当前在播的歌，或列出收藏（action: add/remove/list）。"
              "列表为空时说收藏为空；没在播时提示先放一首。",
              {"ok_star": "music.starred", "ok_unstar": "music.unstarred",
               "ok_list": "music.favorites_count", "empty": "music.no_current",
               "empty_list": "music.favorites_empty",
               "unreachable": "music.star_failed"},
              input_schema={"type": "object", "properties": {
                  "action": {"type": "string", "enum": ["add", "remove", "list"]}},
                  "required": ["action"]}),
         music_favorite),
        (spec("local.music_play_favorites", "放收藏的歌（「放我收藏的/来点我喜欢的」）",
              {**PLAY_MAP, "empty": "music.favorites_empty",
               "unreachable": "music.favorites_read_failed"},
              answer="silent", guard="music_request"),
         music_play_favorites),
        (spec("local.music_taste", "最近爱听什么/最常听谁（播放统计）",
              {"ok_taste": "music.taste_top", "empty": "music.taste_empty"},
              permission=Permission.READ),
         music_taste),
    ]
