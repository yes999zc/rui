"""The daily briefing as data, not as a model answer.

Old-build provenance (all line numbers refer to `xiaoai-voice-entry/server/`):

* ``tools/news.py:126-136`` ``_briefing(count)`` built the spoken script as
  ``{date, count, source, text}``: a dated head, ``第一，…。第二，…。`` body and an
  optional ``最后一句微语``.
* ``assistant_v3.py:906-913`` the tool returned an ``@@BRIEF`` marker. The tool
  dispatcher put the payload into ``lat["brief"]`` and handed the *model* only
  ``播报已准备好（N 条，…），直接念给用户听。`` - the text itself never entered the
  model turn.
* ``assistant_v3.py:1017-1024`` the reply path returned the script verbatim and
  appended ``user transcript`` + ``assistant text[:800]`` to history, so a follow-up
  like 「第三条再说说」 could still be answered from what was actually read out.
* ``assistant_v3.py:3492-3512`` the speech loop streamed that text as written
  (comment there: "read the daily briefing as written"), barge-in included.
* ``prompts.py:24`` only tells the model to *call* the news tool; re-summarising a
  68 s briefing into a 40-character reply is what made the old news tool useless
  (``assistant_v3.py:906``).

This module is the pure half of the new wiring (D1c ④): build the data, list the
history messages, resolve 「第 N 条」. It does not speak, fetch, or touch a session -
the app/runner change is phase B. The invariant to keep when wiring: **the briefing
text is never part of a prompt for the same turn**; it only reaches the model later,
as the assistant message history above.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

# A ToolResult status the system layer can intercept for the direct path.
BRIEFING_STATUS = "briefing"
SOURCE_60S = "60s"
MAX_ITEMS = 10
# Old build's history cap (assistant_v3.py:1021: content[:800]).
HISTORY_LIMIT = 800
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_NUMS = "一二三四五六七八九十"
_END_PUNCT = ("。", "！", "？", "；", ".", "!", "?", ";")


@dataclass(frozen=True)
class Briefing:
    """One day's spoken script plus the metadata the session layer needs."""

    date: str                      # ISO date, e.g. 2026-10-23
    items: tuple[str, ...]         # ordered, already punctuated for speech
    tip: str = ""
    source: str = SOURCE_60S

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def speech_text(self) -> str:
        """Exactly what TTS reads. Never sent to the model for this turn."""
        day = _parse_day(self.date)
        head = (f"今天是{day.month}月{day.day}日，{WEEKDAYS[day.weekday()]}。"
                "给你念几条新闻。")
        body = "".join(f"第{_NUM_WORDS[i]}，{item}"
                       for i, item in enumerate(self.items[:MAX_ITEMS]))
        tail = f"最后一句微语：{self.tip}" if self.tip else ""
        return head + body + tail

    def to_payload(self) -> dict[str, Any]:
        """ToolResult payload for the direct path.

        Deliberately **no** ``text`` key: a payload that reaches
        ``TurnRunner._render_tool_result`` is JSON-dumped to the model, and the
        briefing must not be re-read as prompt material (old ``lat["brief"]``
        contract). The app holds the `Briefing` object and speaks `speech_text`.
        """
        return {"status": BRIEFING_STATUS, "date": self.date,
                "count": self.count, "source": self.source}

    def model_notice(self) -> str:
        """What the model may see instead of the script (old dispatcher wording)."""
        return f"播报已准备好（{self.count} 条，{self.date}），直接念给用户听。"

    def history_messages(self, transcript: str,
                         limit: int = HISTORY_LIMIT) -> list[dict[str, str]]:
        """The pair appended after the briefing is spoken (old :1017-1024).

        The assistant content is the real script (truncated to the old 800-char
        cap), which is what makes 「第三条再说说」 answerable next turn.
        """
        return [
            {"role": "user", "content": transcript},
            {"role": "assistant", "content": self.speech_text[:limit]},
        ]

    def item_at(self, index: int) -> str:
        """1-based item, or "" when out of range."""
        if 1 <= index <= self.count:
            return self.items[index - 1]
        return ""

    def item_index(self, transcript: str) -> int | None:
        """「第三条再说说」 -> 3; anything else, or out of range, -> None.

        Pure and deterministic so a future wiring can answer the follow-up without
        a model round trip; the history path above still covers free-form asks.
        """
        match = _ITEM_REF.search(re.sub(r"\s+", "", transcript or ""))
        if not match:
            return None
        number = _to_int(match.group(1))
        return number if number and 1 <= number <= self.count else None


def from_news(news: Any, *, date: dt.date | str | None = None, tip: str = "",
              source: str = SOURCE_60S, limit: int = 5) -> Briefing | None:
    """Build a `Briefing` from a 60s digest item list (or the whole digest dict).

    Accepts the shapes the sources actually return: a list of strings (our news
    tool) or of dicts with ``title`` (the old build's ``_titles``), or the whole
    digest ``{"news": [...], "tip": ...}``. Whitespace is collapsed, a closing full
    stop is added when missing (old ``_briefing``), and an empty feed returns None
    so the caller can say "没取到" instead of reading a header with no items.
    """
    payload = news
    if isinstance(news, dict):
        payload = news.get("news") or []
        if not tip:
            tip = str(news.get("tip") or "")
    items = _titles(payload)[:max(1, min(MAX_ITEMS, int(limit)))]
    if not items:
        return None
    day = _coerce_day(date)
    return Briefing(date=day.isoformat(), items=tuple(items), tip=tip.strip(),
                    source=source)


# ---------------------------------------------------------------------------
# helpers (pure)
# ---------------------------------------------------------------------------
_ITEM_REF = re.compile(r"第([一二三四五六七八九十]+|\d{1,2})条")
_NUM_WORDS = tuple(_NUMS)
_CN_VALUES = {ch: i + 1 for i, ch in enumerate(_NUMS)}


def _titles(payload: Any) -> list[str]:
    out: list[str] = []
    for item in payload or []:
        title = item.get("title") if isinstance(item, dict) else item
        text = " ".join(str(title or "").split())
        if not text:
            continue
        if not text.endswith(_END_PUNCT):
            text += "。"
        out.append(text)
    return out


def _coerce_day(day: dt.date | str | None) -> dt.date:
    if isinstance(day, dt.date):
        return day
    if isinstance(day, str) and day.strip():
        try:
            return dt.date.fromisoformat(day.strip())
        except ValueError:
            pass
    return dt.date.today()


def _parse_day(day: str) -> dt.date:
    return _coerce_day(day)


def _to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    # 十, 十一 ... 十九 (items are capped at ten, but a caller may pass 第十二条).
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2 and text[1] in _CN_VALUES:
        return 10 + _CN_VALUES[text[1]]
    if len(text) == 1:
        return _CN_VALUES.get(text)
    return None

