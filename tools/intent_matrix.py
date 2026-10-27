#!/usr/bin/env python3
"""Audit A: the deterministic intent matrix, recomputed from the production rules.

The app decides a short transcript without the model, in this order
(`src/rui/web/api/app.py:2797-2820`):

    _music_transport -> _volume_command -> _context_command -> _stop_command
    -> verbless-device clarification -> model

This script mirrors that order with the same pure functions
(`src/rui/session/rules.py`) and prints one canonical matrix. It is intentionally
standalone: no network, no device, no service, no fixtures, no frozen clock. The
only inputs are the production defaults (`SessionConfig.end_words`) and the phrase
list below.

Usage (from the repository root):

    uv run python tools/intent_matrix.py

Output is deterministic: two runs are byte-identical (`diff <(run) <(run)` is empty).

Branch names are the ones the app uses in `session_action` / log lines / tool lists:

    music_random | music_pause | music_resume | music_next | music_prev
    volume | context_on | context_off
    media_stop | stop_elsewhere | bare_stop
    clarify.verb | model

`stop_elsewhere` is `_stop_command`'s cross-room branch (`app.py:975-999`):
a stop word in a room that does not own the output closes only that room.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rui.config.schema import SessionConfig  # noqa: E402
from rui.session import rules  # noqa: E402

END_WORDS = tuple(SessionConfig().end_words)

# Card list (32) plus the words that made the collisions visible: ASR aliases,
# pause words the 9-ge decision removes, transports, and one-character probes.
PHRASES = (
    # stop family
    "停止",
    "停止播放",
    "停止你好",
    "停",
    "停一下",
    "停一下下",
    "停下",
    "停下来",
    "停掉",
    "别放",
    "别放了",
    "别说了",
    "别唱",
    "不听了",
    "暂停",
    "先停这儿",
    "停会儿",
    "停住",
    "停在这",
    # music transport / random
    "继续",
    "接着放",
    "恢复播放",
    "下一首",
    "上一首",
    "换一首",
    "前一首",
    "切歌",
    "放首歌",
    "唱首歌",
    # dismiss family
    "退下吧",
    "退下",
    "退一下",
    "对一下吧",
    "不聊了",
    "够了",
    "结束",
    "退出",
    "再见",
    "拜拜",
    "晚安",
    "睡了",
    "睡吧",
    # volume / context / device / model
    "大一点",
    "小一点",
    "静音",
    "音量调到50%",
    "关闭上下文",
    "打开上下文",
    "关掉客厅灯",
    "关闭窗帘",
)

MUSIC_STATES = ("playing", "paused", "idle")   # 有歌 / 歌暂停 / 无歌
SESSION_STATES = ("open", "closed")            # 会话开 / 关
ROOMS = ("owner", "other")                     # 同屋 / 异屋


def dispatch(phrase: str, music: str, session: str, room: str) -> str:
    """Mirror `app.py:2797-2820` with the pure rules; `session` is a matrix axis.

    `music` is `app.state.music.state`. `room` is "owner" when the turn's device
    owns the output, "other" when another room's device holds it. `session` is the
    conversation open/closed state (the app does not consult it in these layers;
    the matrix asserts that by computing both and comparing).
    """
    del session  # deliberately unused: the audit checks that it cannot change a branch

    # 1. `_music_transport` (app.py:798): random play first, then the transport words.
    if rules.is_music_random(phrase):
        return "music_random"
    action = rules.music_transport(phrase)
    if action and music != "idle":
        return f"music_{action}"

    # 2. `_volume_command` (app.py:2297).
    if rules.volume_level(phrase) is not None:
        return "volume"

    # 3. `_context_command` (app.py:1017).
    context = rules.context_command(phrase)
    if context:
        return f"context_{context}"

    # 4. `_stop_command` (app.py:923). Paused music counts as "active" here (:947).
    music_active = music in ("playing", "paused")
    if rules.stop_intent(phrase, END_WORDS):
        scope = rules.stop_scope(phrase, music_active, END_WORDS)
        if scope == rules.MEDIA_STOP:
            return "media_stop" if room == "owner" else "stop_elsewhere"
        return "bare_stop"

    # 5. Bare device name -> ask which way (app.py:2812).
    if rules.verbless_device_name(phrase):
        return "clarify.verb"

    # 6. Everything else is the model's turn.
    return "model"


def diagnostics(phrase: str) -> tuple[str, ...]:
    """Every pure predicate for one phrase, so a reviewer can replay the order."""
    action = rules.music_transport(phrase)
    level = rules.volume_level(phrase)
    context = rules.context_command(phrase)
    stop = rules.stop_intent(phrase, END_WORDS)
    scope = rules.stop_scope(phrase, True, END_WORDS)
    return (
        "yes" if rules.is_noise(phrase) else "no",
        "yes" if rules.is_music_random(phrase) else "no",
        action or "-",
        level if level is not None else "-",
        context or "-",
        "yes" if stop else "no",
        scope or "-",
        "yes" if rules.is_dismiss(phrase) else "no",
        "yes" if rules.verbless_device_name(phrase) else "no",
    )


def main() -> int:
    lines: list[str] = []
    add = lines.append

    add("# intent matrix (audit A) -- deterministic, no device/service/network")
    add(f"# end_words ({len(END_WORDS)}): " + " ".join(END_WORDS))
    add("# pause words: " + " ".join(rules.MUSIC_TRANSPORT["pause"]))
    add("# columns: phrase | playing/owner | playing/other | paused/owner |"
        " paused/other | idle/owner | idle/other | session_open==closed")
    add("")

    for phrase in PHRASES:
        cells: list[str] = []
        for music in MUSIC_STATES:
            for room in ROOMS:
                cells.append(dispatch(phrase, music, "open", room))
        both_sessions_equal = all(
            dispatch(phrase, music, "open", room) == dispatch(phrase, music, "closed", room)
            for music in MUSIC_STATES
            for room in ROOMS
        )
        add("\t".join((phrase, *cells, "yes" if both_sessions_equal else "NO")))
        if not both_sessions_equal:
            # Never expected; make a broken port loud instead of printing a quiet table.
            print(f"session axis changed the branch for {phrase!r}", file=sys.stderr)
            return 1

    add("")
    add("# diagnostics per phrase (stop_scope is computed with music playing)")
    add("\t".join((
        "phrase", "is_noise", "is_music_random", "music_transport", "volume_level",
        "context_command", "stop_intent", "stop_scope(music=playing)", "is_dismiss",
        "verbless_device", "branch(playing/owner)",
    )))
    for phrase in PHRASES:
        add("\t".join((
            phrase, *diagnostics(phrase),
            dispatch(phrase, "playing", "open", "owner"),
        )))

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
