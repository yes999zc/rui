"""Session-level rules that must not go through the model.

Two failures measured on the real speaker (2026-10-22) and in the old deployment:

- 「你好小瑞」followed by a soft 「嗯。」 became a whole turn: the model answered
  「嗯，你说。」, which is both noise and a wasted TTS round.
- 「停一下」/「退下吧」 are session actions. Letting the model decide meant it sometimes
  replied politely and kept listening (the old build hit the same thing on 2026-10-20:
  「停止播放」 with nothing playing was a request to end the conversation).

Both are cheap to answer without a round trip, and both are wrong in a way a person
notices immediately. Everything here is pure so it can be tested without a speaker.
"""
from __future__ import annotations

import difflib
import re

# From the old build's `conversation.stop_words`, plus the two it added later.
STOP_WORDS = (
    "停止", "停下来", "停下", "停一下", "停掉", "别说了", "别放", "关掉", "关闭", "暂停",
    "别唱", "不听了", "退下吧", "退下", "关机", "不聊了", "够了",
)

# Short replies to a stop: one word, spoken immediately. Rotated so the same word five
# times in a row does not sound like a machine.
STOP_REPLIES = ("好。", "行，我退了。", "好，有需要再叫我。")

# Measured ASR slips, written down instead of guessed at with an edit distance. The old
# build used pinyin comparison for this (「退下吧」 heard as 「对一下吧」, 2026-10-20);
# without pulling in pypinyin, the slips we have actually seen are listed here.
ALIASES = {
    "对一下吧": "退下吧",
    "退一下吧": "退下吧",
    "推一下吧": "退下吧",
    "退下把": "退下吧",
    "不听了啊": "不听了",
    "停一下下": "停一下",
}

# What a transcript may be and still be noise: fillers, a single character, punctuation.
_FILLER = re.compile(r"^[嗯啊呃哦噢唉诶哎哟呀吧呢哈嘿]?[。，、！？\.\!\?\s]*$")
_PUNCT = re.compile(r"[\s。，、！？；：,.!?;:…—\-]")

# Words that carry a session action only when they are the whole request: 「关掉」 alone
# ends the session, 「关掉客厅灯」 is a device command (S5e's problem, not this one).
_STOP_MAX_CHARS = 6


def normalize(text: str) -> str:
    """Strip punctuation and spaces so 「停 止。」 and 「停止」 compare equal."""
    return _PUNCT.sub("", text or "")


def is_noise(transcript: str) -> bool:
    """True for 「嗯」「啊？」「。。。」: nothing a person asked for.

    Kept deliberately narrow. A one-character answer like 「要」 can be a real answer to a
    question we asked, so only pure fillers and punctuation-free single syllables count.
    """
    t = normalize(transcript)
    if not t:
        return True
    if _FILLER.match(t):
        return True
        # 「嗯嗯」「啊啊」 are the same noise, repeated
    return bool(len(t) <= 2 and all(ch in "嗯啊呃哦噢唉诶哎哟呀吧呢哈嘿" for ch in t))


def stop_intent(transcript: str) -> bool:
    """True when the request is "stop / dismiss" rather than a question.

    Only short utterances and only when the stop word covers the whole request: 「停止播放」
    counts (nothing is playing in v1, so it means dismiss), 「把音乐停了以后告诉我天气」
    does not. A 3-4 character stop word tolerates one ASR slip, because 「退下吧」 was
    measured arriving as 「对一下吧」.
    """
    t = normalize(transcript)
    if not t or len(t) > _STOP_MAX_CHARS:
        return False
    t = ALIASES.get(t, t)
    for word in sorted(STOP_WORDS, key=len, reverse=True):
        if word in t:
            return True
        if 3 <= len(word) <= 4 and len(t) <= len(word) + 1:
            if difflib.SequenceMatcher(None, t, word).ratio() >= 0.75:
                return True
    return False


def stop_reply(index: int = 0) -> str:
    return STOP_REPLIES[index % len(STOP_REPLIES)]
