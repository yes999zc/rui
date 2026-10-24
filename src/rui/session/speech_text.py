"""Turn written text into something a TTS vendor will say, not spell out.

Measured on the real speaker with the primary vendor (讯飞, 2026-10-23):

- a URL is read character by character, colon included: ``https://example.com``
  took 4.17 s of audio, ``看这里 https://a.cn/x 就行`` 4.43 s. That is where a
  spoken 「冒号」 comes from.
- ``# 标题`` took 1.67 s against 1.07 s for ``标题``: Markdown headings are read
  as a symbol too.
- punctuation used as punctuation is fine, but only when it stays punctuation:
  the clause splitter can hand the vendor a piece that ends in a colon, and some
  fallback vendors (CosyVoice2) read symbols aloud instead of pausing.

So the text is normalised once, right before synthesis: URLs and Markdown removed,
quotes/brackets dropped, symbols replaced by the pauses a speaker would make, and
the few readings that are unambiguous (75%, 12:30, 9℃) expanded into words.

This is deliberately conservative: anything uncertain is left alone, because a
wrong expansion sounds worse than a pause. It never changes what is logged or
shown on the console - only what the vendor synthesises.
"""
from __future__ import annotations

import re

_URL = re.compile(r"https?://[^\s，。！？；、）】」』\"']+|www\.[^\s，。！？；、）】」』\"']+")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]")
_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*[:：]\s*([0-5]\d)(?!\d)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
_DEGREE = re.compile(r"(?<=\d)\s*(?:℃|°C|°c)")
_RANGE = re.compile(r"(?<=\d)\s*[~～]\s*(?=\d)")
_DATE_DASH = re.compile(r"(?<=\d{4})-(?=\d)")
_CLAUSE_CHARS = "，,、：:"
_SENTENCE_CHARS = "。！？!?；;"
_QUOTES = "“”‘’「」『』\"'《》〈〉【】[]{}（）()<>"


def _chinese_number(value: str) -> str:
    """Read a short number the way Mandarin does: 75 -> 七十五, 3.5 -> 三点五."""
    digits = "零一二三四五六七八九"
    if "." in value:
        head, _, tail = value.partition(".")
        return _chinese_number(head) + "点" + "".join(digits[int(ch)] for ch in tail if ch.isdigit())
    number = int(value)
    if number < 10:
        return digits[number]
    if number < 20:
        return "十" + (digits[number % 10] if number % 10 else "")
    if number < 100:
        return digits[number // 10] + "十" + (digits[number % 10] if number % 10 else "")
    if number < 1000:
        rest = number % 100
        return (digits[number // 100] + "百"
                + (("零" + digits[rest // 10]) if 0 < rest < 10 else _chinese_number(rest) if rest else ""))
    if number < 10000:
        rest = number % 1000
        return (digits[number // 1000] + "千"
                + (("零" + _chinese_number(rest)) if 0 < rest < 100 else _chinese_number(rest) if rest else ""))
    return value


def _expand_time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 24:
        return match.group(0)
    head = _chinese_number(str(hour)) + "点"
    if minute == 0:
        return head
    if minute < 10:
        return head + "零" + _chinese_number(str(minute)) + "分"
    return head + _chinese_number(str(minute)) + "分"


def prepare_for_speech(text: str) -> str:
    """Text that a TTS vendor speaks instead of spelling symbols out."""
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n")
    cleaned = _MD_LINK.sub(r"\1", cleaned)
    cleaned = _URL.sub("", cleaned)
    cleaned = _EMOJI.sub("", cleaned)
    cleaned = _TIME.sub(_expand_time, cleaned)
    cleaned = _PERCENT.sub(lambda m: "百分之" + _chinese_number(m.group(1)), cleaned)
    cleaned = _DEGREE.sub("度", cleaned)
    cleaned = _RANGE.sub("到", cleaned)
    # keep dates intact: 2026-10-23 must not become a range
    cleaned = _DATE_DASH.sub("年", cleaned)
    for char in _QUOTES:
        cleaned = cleaned.replace(char, "")
    # Markdown / list furniture: spoken, it is either a symbol name or nothing.
    for char in ("#", "*", "`", "_", ">"):
        cleaned = cleaned.replace(char, "")
    for char in ("—", "–", "——", "……", "…", "···", "．．．"):
        cleaned = cleaned.replace(char, "。")
    cleaned = cleaned.replace("．", "。")
    cleaned = cleaned.replace("&", "和").replace("/", "，").replace("·", "，")
    cleaned = cleaned.replace("\n", "。")
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Every colon/semicolon/顿号 becomes a spoken pause; leftover ASCII punctuation
    # is safe, but a colon can be a symbol name on some vendors.
    for char in "：:":
        cleaned = cleaned.replace(char, "，")
    for char in "；;":
        cleaned = cleaned.replace(char, "。")
    cleaned = cleaned.replace("、", "，")
    cleaned = re.sub(r"([，,])\s*[，,]+", "，", cleaned)
    cleaned = re.sub(r"([。！？!?])\s*[，,]+", r"\1", cleaned)
    cleaned = re.sub(r"([，,])\s*[。！？!?]", r"\1", cleaned)
    cleaned = re.sub(r"[。]{2,}", "。", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Keep the closing punctuation: it is the vendor's cue for a finished
    # sentence, and the gap logic keys off it. Only leading pauses are noise.
    return cleaned.lstrip("，,、；;：: \t\n")


def gap_after(text: str) -> int:
    """Milliseconds of true silence to write after a synthesised piece.

    The old deployment used 350 ms after every sentence, and that is right at a
    sentence end: it gives the speaker's own reference channel a clean window.
    After a *clause* split (comma / colon / 顿号) the vendor already renders the
    pause, and adding another 350 ms of silence is what made mid-sentence speech
    sound stitched together (measured 2026-10-23).
    """
    stripped = (text or "").strip()
    return 350 if stripped and stripped[-1] in _SENTENCE_CHARS else 0
