"""The PhraseBook: every sentence the system layer speaks, in one place.

The V-line's single rule (docs/interaction.md): an application-layer tool never
carries its own wording. It returns a structured `ToolResult` and declares an
`InteractionSpec`; this table is the only place the words live. The old build
proved why: four tool families grew four speech dialects (`@@` markers for
music, error strings for HA, notices for search, its own set for reminders), and
the unification attempt only ever reached the prompt text layer
(REQUIRED_HINTS), never the return values.

Render slots use `{title}`-style braces; a missing slot renders empty rather
than raising - a wording bug must not eat a turn.
"""
from __future__ import annotations

# The built-in table. Keys are namespaced `domain.what`; the status templates an
# InteractionSpec points at must exist here (a typo fails loud and early in tests).
DEFAULTS: dict[str, str] = {
    # --- music (V1's migration set) -----------------------------------------
    "music.started": "好，放《{title}》-{artist}，共 {count} 首。",
    "music.started_one": "好，放《{title}》-{artist}。",
    "music.not_found": "曲库里没找到「{want}」。",
    "music.empty": "曲库里现在没有能放的。",
    "music.unreachable": "曲库这会儿连不上，过一会儿再让我放。",
    "music.unresolved": "歌都在，但一首也没取出来，过会儿再试一次吧。",
    "music.paused": "好，先停这儿。",
    "music.resumed": "接着放。",
    "music.not_playing": "现在没有在放的歌。",
    "music.not_paused": "现在没有暂停着的歌。",
    "music.next": "好，《{title}》。",
    "music.last_track": "已经是最后一首了。",
    "music.prev": "好，回到《{title}》。",
    "music.first_track": "已经是第一首了。",
    "music.stopped": "好，不放了。",
    "music.starred": "已收藏《{title}》。",
    "music.unstarred": "取消收藏《{title}》。",
    "music.star_failed": "收藏没成功，服务器没回应。",
    "music.no_current": "现在没有在播的歌，想收藏哪首就先放它。",
    "music.favorites_empty": "还没有收藏的歌，播放时说「收藏这首歌」。",
    "music.favorites_read_failed": "收藏这会儿读不出来。",
    "music.favorites_count": "收藏了 {count} 首，比如：{names}。",
    "music.taste_empty": "还没怎么放过歌，先来一首？",
    "music.taste_top": "你听得最多的是：{names}。{tail}",
    "music.taste_item": "{artist} {plays} 次",
    "music.taste_tail": "最近一次放的是《{title}》。",
    # --- turn-level degrade (the system layer's own fallbacks) ---------------
    "turn.failed": "我没弄成，你再说一遍？",
    "turn.stuck": "我这卡了一下，你再说一遍。",
    # --- volume (the speaker's own mixer) -----------------------------------
    "volume.set": "音量调到 {pct}%了。",
    "volume.read": "现在音量 {pct}%。",
    "volume.failed": "音量没调成，音箱没回应。",
    "volume.unknown": "没听清要调到多少，现在是 {pct}%。",
    # --- devices over HA (V2's first application) ----------------------------
    "device.done": "好，{device}{action}。",
    "device.unreachable": "{device}这会儿连不上，没能执行，别当我成功了。",
    "device.excluded": "{device}这类操作我不做。",
    "device.missing_target": "没听清要操作哪个设备。",
    "ha.entity_list": "家里的设备（名字=实体ID，状态要现查，别用过期状态）：\n{list}\n{note}",
    "ha.list_stale": "（这份清单是上次缓存的，控制可能失败。）",
}

_MISSING = object()


class PhraseBook:
    """Immutable defaults plus an optional override layer (the console writes one)."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._table = {**DEFAULTS, **(overrides or {})}

    def render(self, key: str, **slots: object) -> str:
        text = self._table.get(key, _MISSING)
        if text is _MISSING:
            return key   # an unknown key is a wiring bug; say it, never crash a turn
        try:
            return text.format(**{k: "" if v is None else v for k, v in slots.items()})
        except (KeyError, IndexError, ValueError):
            # A slot mismatch degrades to the bare template - wording bugs must
            # not eat the turn (same rule as the persona format in prompts).
            return _strip_slots(text)

    def has(self, key: str) -> bool:
        return key in self._table

    def keys(self) -> list[str]:
        return sorted(self._table)


def _strip_slots(text: str) -> str:
    """Drop `{...}` placeholders a payload did not fill."""
    out, depth = [], 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).replace("  ", " ").strip()


# module-level singleton: the runner and the deterministic paths share one book
book = PhraseBook()
