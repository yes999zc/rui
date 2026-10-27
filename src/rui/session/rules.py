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
import time

from . import pinyin

# From the old build's `conversation.stop_words`, plus the two it added later.
# 关掉 / 关闭 / 关机 are deliberately absent: they are the verbs of home-automation
# commands (「关掉客厅灯」「关闭窗帘」) and the old build deleted 关机 after exactly that
# accident. The match rule below would keep those commands out anyway, but a word that
# must never mean "go away" does not belong in the default list.
STOP_WORDS = (
    "停止", "停下来", "停下", "停一下", "停掉", "别说了", "别放", "暂停",
    "别唱", "不听了", "退下吧", "退下", "不聊了", "够了",
)

# Short replies to a stop: one word, spoken immediately. Rotated so the same word five
# times in a row does not sound like a machine.
STOP_REPLIES = ("好。", "行，我退了。", "好，有需要再叫我。")

# Measured ASR slips, written down instead of guessed at with an edit distance. This is
# now the *only* tolerance: matching is exact-after-alias (see `stop_intent`), because a
# fuzzy match also fired on commands that merely started with a stop word.
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

# A stop word may gather this many extra characters and still be the whole request:
# 「停止播放」 and 「别说了吧」 are the same action, 「关掉客厅灯」 is not (it is five
# characters around a verb that is not a stop word any more).
_STOP_TAIL_CHARS = 2

# A stop word said while music plays means two different things: 「停止」「别放」 ends
# the sound, 「退下吧」 dismisses the conversation. D1 split the old single stop path;
# `stop_scope` below is the pure decision the app wires (D1c ③).
DISMISS_WORDS = ("退下吧", "退下", "不聊了", "关机")

# The two outcomes of a stop word. Kept as constants so the app cannot typo one.
MEDIA_STOP = "media_stop"   # a song owns the request: stop the sound, keep listening
BARE_STOP = "bare_stop"     # nothing is playing: the stop word ends the conversation


def is_dismiss(transcript: str) -> bool:
    """True when the stop word sends the assistant away rather than the sound.

    An ASR slip of a dismissal is still a dismissal: 「对一下吧」 was measured for
    「退下吧」, and `stop_scope` must not call that a media stop just because a song
    happens to be playing.
    """
    t = normalize(transcript)
    t = ALIASES.get(t, t)
    return any(word in t for word in DISMISS_WORDS)


# Music transport: deterministic words that must reach the player without a model
# round trip. Measured 22:06: the model agreed with 「下一首」 in words
# (「切到下一首了。」) and never called the tool - a transport that only *sounds*
# executed is worse than none. Short-only, like stop_intent: 「暂停以后告诉我天气」
# is a compound request and belongs to the model.
MUSIC_TRANSPORT: dict[str, tuple[str, ...]] = {
    "pause": ("暂停", "停一下", "先停这儿", "停会儿", "停住", "停在这"),
    "resume": ("继续", "接着放", "接着播", "恢复播放"),
    "next": ("下一首", "换一首", "切歌", "切一首", "换歌"),
    "prev": ("上一首", "前一首"),
}


def music_transport(transcript: str) -> str:
    """One of pause/resume/next/prev when the whole request is a transport word."""
    t = normalize(transcript)
    if not t or len(t) > 6:
        return ""
    for action, words in MUSIC_TRANSPORT.items():
        if any(word in t for word in words):
            return action
    return ""


# 「放首歌」 style: the whole request is a random-play ask with nothing to parse.
# Deterministic on purpose, like the transports: measured twice tonight (22:12,
# 22:24), the model sometimes refused the exact same prompt out of context -
# 「没有接音乐来源」 - while the library was reachable. The old build's play key
# went straight to random music for the same reason.
MUSIC_RANDOM_WORDS = (
    "放首歌", "放点歌", "放个歌", "放音乐", "来点音乐", "来首音乐", "来首歌",
    "随便放点", "随便来点", "听点音乐", "听音乐", "放歌",
    # 0.7.8 field log: 「唱首歌」 was parsed as a title ("唱首"), so add the sing/come
    # variants. The 8-char cap in is_music_random still keeps long requests as titles.
    "唱首歌", "唱个歌", "唱一首", "来一首", "来一段", "唱一段",
)


# Volume words, deterministic for the same reason as the transports: measured
# 09:03, 「音量调到50%」 and 「30%」 each got 「音量调到50%了」 with zero tool calls.
_VOLUME_UP = ("大一点", "大点", "大声", "调大", "大些", "响一点")
_VOLUME_DOWN = ("小一点", "小点", "小声", "调小", "小些", "轻一点")


def volume_level(transcript: str) -> str | None:
    """up | down | mute | a 0-100 string | "" (query) | None (not a volume ask)."""
    t = normalize(transcript)
    if not t or len(t) > 10:
        return None
    if not any(w in t for w in ("音量", "声音", "喇叭", "静音")):
        return None
    if "静音" in t:
        return "mute"
    if any(w in t for w in _VOLUME_UP):
        return "up"
    if any(w in t for w in _VOLUME_DOWN):
        return "down"
    digits = re.search(r"(\d{1,3})", t)
    if digits:
        return digits.group(1)
    if any(w in t for w in ("多少", "多大", "几")):
        return ""
    return None


# ---------------------------------------------------------------- device intent
# Ported from the old build (assistant_v3.py:1908-2151). Two measured failures:
# a bare device name was read as a command and the model guessed the direction
# (5/5 runs turned the lights ON for 「餐厅灯。」), and a reply claimed success
# with zero tool calls behind it (2 of 3 identical runs).
DEVICE_CMD = re.compile(
    r"(打开|关闭|关掉|开一下|关一下|帮我开|帮我关|把.{0,8}(打开|关闭|关掉|开|关)"
    r"|调到|调成|调高|调低|音量|温度|亮度|风速|档位)")
DEVICE_NOUN = re.compile(
    r"(灯|空调|窗帘|插座|风扇|加湿|扫地|音量|温度|亮度|风速|暖气|热水器|电视|投影|门锁)")
CLAIM_DONE = re.compile(
    r"(已经|已|好了|搞定|完成了|打开了|关上了|关掉了|关闭了|关了|开了"
    r"|调到了|调到|调成|开好了|关好了|停好了)")
VERBLESS_SKIP = re.compile(r"(坏|不亮|闪|刺眼|太亮|太暗|暗|修|换|装|打不开|关不上)")
VERBLESS_ASK = re.compile(r"(什么|几|哪|怎么|是不是|状态|开着|关着|亮着|了吗|没有)")


def verbless_device_name(transcript: str) -> bool:
    """A bare device name: the ASR ate the verb. Ask which way, never guess.

    Questions and complaints are excluded by their own patterns: 「灯开着吗」 is a
    question, 「灯太暗」 is a complaint, 「把灯关掉」 has its verb.
    """
    t = normalize(transcript)
    if not t or len(t) > 8:
        return False
    if DEVICE_CMD.search(t):
        return False
    if t.endswith(("吗", "呢", "么")) or VERBLESS_ASK.search(t):
        return False
    if VERBLESS_SKIP.search(t):
        return False
    return bool(DEVICE_NOUN.search(t))


def claims_device_success(transcript: str, reply: str) -> bool:
    """A device action claimed in words with no tool call behind it."""
    if not CLAIM_DONE.search(reply or ""):
        return False
    return bool(DEVICE_CMD.search(transcript or "") or DEVICE_NOUN.search(transcript or ""))


# A reply that claims a song started. Measured 13:47:36: the music guard refused
# music_play (the transcript had lost its verb) and the model still answered
# 「好，放《告白气球》。」 - a false claim the user acts on.
# Only an announcement with the song title marker counts. The 16:51 clarification
# 「想听周杰伦哪首？说个歌名，我就给你放。」 matched 给你放 and triggered a bogus retry.
_MUSIC_CLAIM = re.compile(
    r"(?:放|播放)\s*《|正在放(?:着)?|开始放(?:了)?|已经放上|这就放(?:上)?|放上了")
_MUSIC_QUESTION = re.compile(r"[？?]|哪首|歌名|想听|要不要|要放吗|说个|说一首")


def claims_music_success(transcript: str, reply: str) -> bool:
    """A song claimed as playing with no tool call behind it."""
    text = reply or ""
    if _MUSIC_QUESTION.search(text):
        return False
    return bool(_MUSIC_CLAIM.search(text))


CONTEXT_OFF_WORDS = ("关闭上下文", "别记着前面", "不记上下文", "忘掉前面", "不要上下文",
                     "别记上下文", "每句独立")
CONTEXT_ON_WORDS = ("打开上下文", "记住上下文", "接着前面聊", "带上上下文", "记着上下文")


def context_command(transcript: str) -> str:
    """on | off | "" - whether to keep multi-turn context from now on."""
    t = normalize(transcript)
    if not t or len(t) > 10:
        return ""
    if any(w in t for w in CONTEXT_OFF_WORDS):
        return "off"
    if any(w in t for w in CONTEXT_ON_WORDS):
        return "on"
    return ""


# Words that make an utterance a music ask at all. The model is allowed to call
# music_play only when one of these is in what the user actually said.
_MUSIC_INTENT = ("放", "播", "歌", "音乐", "听", "来点", "来首", "曲", "唱")


def looks_like_music_request(transcript: str) -> bool:
    t = normalize(transcript)
    return bool(t) and any(word in t for word in _MUSIC_INTENT)


def is_music_random(transcript: str) -> bool:
    t = normalize(transcript)
    if not t or len(t) > 8:
        return False
    return any(word in t for word in MUSIC_RANDOM_WORDS)


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


def stop_intent(transcript: str, words: tuple[str, ...] | list[str] | None = None) -> bool:
    """True when the request is "stop / dismiss" rather than a question.

    Whole-request matching only: the normalised transcript either equals a stop word, or
    contains it with at most two extra characters (「停止播放」, 「别说了吧」). Substring
    matching was measured destroying home automation: 「关掉客厅灯」 and 「关闭窗帘」 are
    five characters around a verb, so a "contains 关掉/关闭" rule dismissed the session
    before HA ever saw the command (audit 2026-10-23; the 13:22 5/5 only passed because
    「儿童房的灯」 was long enough to escape the old length cap).

    ASR slips are handled by `ALIASES` before matching - exact-after-alias, no fuzzy
    fallback: pinyin/difflib tolerance was how a device command could drift into a
    dismissal. Trailing tolerance stays small on purpose; len(t) <= len(word) + 2.
    """
    t = normalize(transcript)
    if not t:
        return False
    t = ALIASES.get(t, t)
    for raw in sorted(words or STOP_WORDS, key=len, reverse=True):
        word = normalize(raw)
        if not word:
            continue
        if t == word:
            return True
        if word in t and len(t) <= len(word) + _STOP_TAIL_CHARS:
            return True
    return False


def stop_reply(index: int = 0) -> str:
    return STOP_REPLIES[index % len(STOP_REPLIES)]


def stop_scope(transcript: str, music_playing: bool,
               words: tuple[str, ...] | list[str] | None = None) -> str:
    """Which of the two stop meanings this request has: media_stop or bare_stop.

    The measured rule (old build, 2026-10-20; docs/music.md §2.4):

    * a song is playing -> ``media_stop``: 「停止播放」「别放了」 stops the sound; the
      conversation stays open, so the next sentence still reaches us;
    * **no song is playing -> ``bare_stop``: 「停止播放」 means end the conversation**
      (散场). There is nothing to stop, so the only useful reading is "go away".

    Dismiss words (「退下吧」「关机」「不聊了」) are ``bare_stop`` either way: they
    name the conversation, not the speaker. Whether that path also silences a playing
    song is the caller's business (the old build measured that it must - a song
    coming back 12 s after 「退下吧」 was heard as a bug, 2026-10-21 09:17).

    Returns "" when the transcript is not a stop request at all (a question that
    contains 「关掉」, a compound request, a long sentence). Pure: no speaker, no
    service, no config - `words` is the configured end-word list.
    """
    if not stop_intent(transcript, words):
        return ""
    if is_dismiss(transcript):
        return BARE_STOP
    return MEDIA_STOP if music_playing else BARE_STOP



class SpokenMemory:
    """What we have said recently, so the microphone cannot turn it into a user turn.

    Read from the old build (`assistant_v3.py:2005`, fixed there on 2026-10-22 10:11:47):
    its array heard its own filler 「我想想」 through the echo gate and answered
    「行，想好了再说。」 - the assistant talking to itself. AEC gives 19-21 dB of echo return
    loss on this speaker, not silence, so the tail is speech-shaped and ASR transcribes it.

    Two things the old build got right that a "compare with the last reply" guard does not:
    it remembers **everything** spoken (answers, acknowledgements, fillers - the filler is
    the case that actually fired), and it keeps a short rolling list instead of one entry,
    because a reply is spoken as several sentences.

    The 10 s window is theirs. Longer would eat real interruptions ("你说今天晴？" asked
    while the answer is still playing is a person, not an echo); shorter would let a long
    answer's own tail back in.
    """

    WINDOW_S = 10.0
    MAX_ITEMS = 12

    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self._spoken: list[tuple[float, str]] = []

    def remember(self, text: str, now: float | None = None) -> None:
        t = normalize(text)
        if not t:
            return
        self._spoken.append((now if now is not None else time.monotonic(), t))
        del self._spoken[: -self.MAX_ITEMS]

    def is_echo(self, transcript: str, now: float | None = None) -> bool:
        """True when the transcript is something we said within the window.

        Exact and containment cover a clean hearing and a fragment of it. The fuzzy pass
        covers the ASR slip that let the 2026-10-22 20:16 self-talk through: the spoken
        「…我先下啦」 came back as 「…我先下吧」 - one tone apart, no shared substring.
        The 0.75 tolerance is the stop-word one (`stop_intent`): one slip in four
        characters, two in eight. A real question about the answer ("明天呢") sits far
        below it.
        """
        t = normalize(transcript)
        if not t:
            return False
        now = now if now is not None else time.monotonic()
        for ts, said in reversed(self._spoken):
            if now - ts > self.window_s:
                break
            if t == said:
                return True
            if len(t) >= 3 and (t in said or said in t):
                return True
            if len(t) >= 4 and difflib.SequenceMatcher(None, t, said).ratio() >= 0.75:
                return True
            # Our own words returning with a homophone slip: the old build's
            # whole-pinyin distance (「我想想」 heard back as 「我想想」 with one
            # syllable wrong) and the simplified/traditional pair (周杰伦/周杰倫).
            if len(t) >= 3 and pinyin.same_sound(t, said, tolerance=1):
                return True
        return False

    def clear(self) -> None:
        self._spoken.clear()
