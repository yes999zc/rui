"""The bridging phrase a slow turn is allowed to say - and when it must stay quiet.

`assistant.filler_after_s = 1.8` exists because 9-11 s of silence reads as "it did not
hear me". Music is the one exception the old deployment measured: a music tool answers in
about a second and the song starts right behind it, so a filler spoken at 1.8 s lands on
top of the first bars instead of covering a wait. Their `filler_phrase()` therefore
returned "" for 「放几首日文歌」 (`server/test_filler.py`).

Their filter read the *request text*. This one reads the tool call instead: the model's
own decision is the thing that makes the answer fast, and a request that merely contains
「歌」 but ends up as a weather question should still get its filler. So the rule is
"a music-* tool ran this turn", evaluated at the moment the filler would speak.

**DORMANT in this build.** No music tool is registered yet (`tools/` has weather, news
and web_search), so `is_music_turn()` is False for every real turn and behaviour is
exactly what it was before this file existed. It is a hook, not a stub: register a tool
whose LLM name starts with `music` (that is the old build's whole namespace:
`music_play`, `music_next`, `music_stop`, ...) or whose name is listed in `MUSIC_TOOLS`,
and the skip takes effect with no other change. The one thing that must not happen is the
inverse - a tool that *is* music but is named `play_song`, so both the prefix and a name
list are checked.

Trade-off accepted: a music turn whose answer really is slow (a lookup that stalls) will
now be silent. The old build made the same trade, and talking over the song is the worse
of the two failures - it sounds like an interruption, not like thinking.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

# Any name starting with this is music, whatever follows it.
MUSIC_TOOL_PREFIX = "music"

# Names from the old deployment's `server/tools/music.py`, kept for tools that do not
# carry the prefix. The prefixed ones are matched by `MUSIC_TOOL_PREFIX` already; listing
# them again costs nothing and documents the namespace.
MUSIC_TOOLS = frozenset({
    "music_search", "music_play", "music_favorite", "music_play_favorites", "music_taste",
    "music_next", "music_prev", "music_stop", "music_resume", "music_volume", "music_save",
    # un-prefixed aliases a future tool layer may pick instead
    "play_music", "stop_music", "pause_music", "play_song", "next_song", "prev_song",
    "play_playlist", "play_favorites",
})


def is_music_tool(name: str) -> bool:
    """Is this tool call one that puts a song in front of the reply?"""
    clean = (name or "").strip().lower()
    if not clean:
        return False
    return clean.startswith(MUSIC_TOOL_PREFIX) or clean in MUSIC_TOOLS


def is_music_turn(tool_names: Iterable[str]) -> bool:
    """True if any tool called in this turn is music-related.

    Takes every call seen so far, not just the last one: a turn that searched the library
    and then played a song is still a music turn.
    """
    return any(is_music_tool(name) for name in tool_names)


def should_speak_filler(
    *,
    delay_s: float,
    text: str,
    tool_names: Sequence[str] = (),
    audio_started: bool = False,
) -> bool:
    """The whole policy in one pure function, so it is testable without a speaker.

    Every reason to stay silent is here and nowhere else: the feature is off (delay <= 0
    or no text), the answer already started talking, or this turn is a music turn.
    """
    if delay_s <= 0 or not text.strip() or audio_started:
        return False
    return not is_music_turn(tool_names)
