"""Text hygiene for what the model writes and what the transcriber hears.

Two jobs, both copied from the old deployment because both were learned the hard way there:

- `clean_reply`: when a model that wanted a tool call has no tools, DeepSeek writes the
  call as text (DSML). The old build once spoke 14 seconds of markup; any line carrying it
  is dropped.
- `strip_wake`: the pre-roll replay puts the wake phrase back in front of the request, so
  the transcript arrives as 「你好小瑞，现在几点」. The model must see 「现在几点」.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

# Markers a DeepSeek answer uses when it writes a tool call instead of making one.
MARKUP_HINTS = ("DSML", "invoke name=", "parameter name=", "<｜｜", "｜｜>")

# The old build had real ASR data for one wake word and kept a homophone class for it;
# a custom word is matched exactly. 小瑞 needs no class: it is not a homophone magnet.
_TAIL_ALIASES = {
    "小智": r"[小晓][智志知晓之]",
    "小志": r"[小晓][智志知晓之]",
    "小知": r"[小晓][智志知晓之]",
    "小晓": r"[小晓][智志知晓之]",
}


def has_markup(text: str) -> bool:
    return any(h in text for h in MARKUP_HINTS)


def clean_reply(text: str) -> str:
    """Drop any line carrying tool-call markup."""
    lines = [line for line in (text or "").splitlines() if not has_markup(line)]
    out = "\n".join(lines).strip()
    for junk in ("<｜｜DSML｜｜ calls>", "<｜DSML｜>"):
        out = out.replace(junk, "").strip()
    return out


def _word_pattern(word: str) -> str:
    """One wake word as a regex: the tail in its homophone class, punctuation tolerated.

    ASR writes 「你好，小瑞」 and 「你好小瑞」 for the same phrase, so the characters are
    joined by an optional separator. The tail is the only part that needs a homophone
    class, because that is where the old deployment had real recognition data.
    """
    word = word.strip()
    tail = word[-2:] if len(word) >= 2 else word
    head = word[: -len(tail)] if len(word) >= 2 else ""
    tail_pattern = _TAIL_ALIASES.get(tail, re.escape(tail))
    parts = [re.escape(ch) for ch in head] + [tail_pattern]
    sep = r"[，,。.！!？?\s]*"
    return sep.join(parts) + sep


def wake_pattern(wake_words: Sequence[str]) -> re.Pattern[str] | None:
    """The whole wake phrase, or its tail at the very start of the utterance.

    The tail alone is accepted because the pre-roll only catches the end of the phrase
    when the KWS fires late. It is *anchored* to the start on purpose: 「今天小瑞在吗」 is
    a real question, and searching for the name anywhere in the first characters would
    eat the beginning of it.
    """
    words = [w for w in (wake_words or []) if (w or "").strip()]
    if not words:
        return None
    alternatives = []
    for word in words:
        tail = word.strip()[-2:] if len(word.strip()) >= 2 else word.strip()
        alternatives.append("^" + _TAIL_ALIASES.get(tail, re.escape(tail)) + r"[，,。.！!？?\s]*")
        alternatives.append(_word_pattern(word))
    return re.compile("(?:" + "|".join(dict.fromkeys(alternatives)) + ")")



def strip_wake(text: str, wake_words: Sequence[str]) -> str:
    """Drop a wake word caught by the pre-roll so the model sees only the request.

    ASR mangles the splice, so the tail is looked for anywhere in the first few characters
    instead of anchoring on the whole phrase. Only the first characters are searched: a
    question that merely contains the name later on is a real question.
    """
    text = (text or "").strip()
    pattern = wake_pattern(wake_words)
    if pattern is None:
        return text
    match = pattern.search(text[:8])
    if not match:
        return text
    rest = text[match.end():].strip()
    return rest if len(rest) > 1 else ""
