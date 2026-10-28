"""提醒与定时：只算「什么时候该响」，不管「怎么响」。

职责边界（这一层刻意不做出声）
--------------------------------
本模块做三件事：把模型给的时间词（``+10s`` / ``+10m`` / ``+1h30m`` / ``07:30`` / ISO）
换算成上海时区的绝对时刻；把提醒原子地落盘到 ``system.data_dir/reminders.json``；用一个
纯内存调度器回答「现在有哪些到期了」，并保证同一条只交出去一次。

到点之后怎么主动出声，是**下一层**（回合编排 / 音频面）的决定，不是这里的遗漏：

1. 同一台音箱只有一个写者。播报要拿设备写入锁，还要排在正在播的那一句之后；在工具处理
   器里直接合成 TTS，会和当前回合的语音叠在一起。
2. 用户正在说话时不能插播——两路 TTS 会同时进 AEC 的参考通道，回声消除直接失效。
3. 出声会失败（设备重连、TTS 超时）。失败要重试，而重试需要知道「哪条已经播过」，那是
   调度器加播报队列的状态，不是一次工具调用的状态。

约定：下一层每 1–2 s 调一次 :meth:`DueScheduler.collect_due`（老项目也是 2 s 轮询），
拿到 :class:`Reminder` 后自己加锁、排队、合成、播报。``collect_due`` 在回调**之前**就把
``fired_at`` 落盘，所以进程在播报中途崩掉不会重播；代价是那一次会丢，恢复策略
（:meth:`ReminderStore.requeue`）留给下一层按自己的重试预算决定。

时区
----
容器默认 UTC，``datetime.now()`` 会差 8 小时，这是这个模块唯一不能含糊的地方：所有时刻都
是 ``Asia/Shanghai`` 的 aware datetime，入参里的 naive ISO 也按上海时间解释。镜像里没有
tzdata 时退回固定 ``+08:00``（中国 1991 年后不再实行夏令时，两者等价）。

存储
----
``data_dir/reminders.json``，临时文件 + ``os.replace`` 原子替换，目录按需创建。整个文件
最多几十条，每 2 s 读一次可以接受；条目变多再换 SQLite 是以后的事。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config.schema import Config
from .contract import Permission, ToolSpec

log = logging.getLogger(__name__)

STORE_FILENAME = "reminders.json"
STORE_VERSION = 1

MAX_ITEMS = 20          # 待发的上限；再多也念不完
MAX_TEXT = 80           # 念出来的一句话，不是备忘录
GRACE_SECONDS = 300.0   # 迟到超过 5 分钟就不再补播（设备离线一整晚的提醒没有意义）
KEEP_FIRED_HOURS = 24   # 已播记录保留一天，重启后靠它证明「这条响过了」
MAX_FIRED = 50


@dataclass(frozen=True)
class ToolContext:
    """The turn-side facts a tool handler may need: who is asking, from which room.

    The room is resolved once by the control plane (the same resolver the
    fixed-sentence reminder path uses) and scoped to the turn; a handler reads it
    through :func:`current_tool_context`. It is a ContextVar rather than a handler
    field because one runner serves every room: two concurrent turns must not see
    each other's room, and a handler must not guess "where am I" from the output
    owner or the last active room (task-32: 「一分钟后提醒我喝水」 came from 次卧 but
    stored room=None and rang in 客厅).
    """

    device_id: str = ""
    room: str = ""


_TOOL_CONTEXT: ContextVar[ToolContext | None] = ContextVar(
    "rui_tool_context", default=None
)


def current_tool_context() -> ToolContext:
    """The context of the tool call in flight (empty outside a turn)."""
    return _TOOL_CONTEXT.get() or ToolContext()


@contextmanager
def use_tool_context(context: ToolContext) -> Iterator[ToolContext]:
    """Scope ``context`` to this turn. ContextVar copies are task-local."""
    token = _TOOL_CONTEXT.set(context)
    try:
        yield context
    finally:
        _TOOL_CONTEXT.reset(token)


def _shanghai() -> dt.tzinfo:
    """The one timezone this deployment speaks. Never the process's local time."""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:  # slim image without /usr/share/zoneinfo
        log.warning("tzdata missing; falling back to a fixed +08:00 offset")
        return dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


# China has had no DST since 1991, so the fallback above is exact, not approximate.
ZONE = _shanghai()


def now() -> dt.datetime:
    """Current time, always aware, always Shanghai. Test code should inject its own."""
    return dt.datetime.now(ZONE)


def _aware(moment: dt.datetime) -> dt.datetime:
    """Coerce to Shanghai time. A naive value is read as Shanghai, never as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=ZONE)
    return moment.astimezone(ZONE)


class TimeParseError(ValueError):
    """The model handed over something that is not a moment we can compute."""


_REL = re.compile(r"^\+?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_at(spec: str, now: dt.datetime | None = None) -> dt.datetime:
    """`+10s` / `+10m` / `+1h30m` / `HH:MM` / ISO -> an aware Shanghai datetime.

    Four accepted shapes, in the order they are tried:

    - relative: ``+10s``, ``+1m30s``, ``+90s``, ``+10m``, ``+1h30m``, ``10m`` (the ``+``
      is optional because a model sometimes drops it; the unit is not optional, so
      ``10`` stays an error). Seconds are part of the grammar because "十秒钟后" is a
      different moment from "十分钟后", not a rounded one.
    - clock time: ``07:30`` — *the next* 07:30 in Shanghai. A time that already passed
      today means tomorrow, which is what "七点半叫我" means at 23:50 (00:10 is covered by
      the same rule: it rolls to the next day).
    - ISO local: ``2026-10-23T07:30`` / ``2026-10-23 07:30[:15]`` — read as Shanghai.
    - ISO with an offset: ``...T07:30:00+08:00`` or ``...Z`` — converted to Shanghai, so
      callers never have to think about which form the model chose.

    A date without a time is rejected on purpose: ``2026-10-23`` would silently become
    midnight, and a reminder that fires at 00:00 is worse than one that asks again.
    """
    base = _aware(now) if now is not None else dt.datetime.now(ZONE)
    raw = (spec or "").strip()
    compact = raw.replace(" ", "")

    m = _REL.match(compact)
    if m and any(m.groups()):
        hours, minutes, seconds = (int(part or 0) for part in m.groups())
        return base + dt.timedelta(hours=hours, minutes=minutes, seconds=seconds)

    m = _CLOCK.match(raw)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            raise TimeParseError(f"没有 {hour}:{minute:02d} 这个时刻：{raw}")
        target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= base:  # a bare clock time already gone today = tomorrow
            target += dt.timedelta(days=1)
        return target

    if len(raw) == 10 and raw[4:5] == "-":
        raise TimeParseError(f"只有日期没有时间：{raw}（至少写到 07:30）")

    text = raw[:-1] + "+00:00" if raw[-1:] in ("Z", "z") else raw
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise TimeParseError(
            f"听不懂这个时间：{spec}（用 +10s / +1m30s / +10m / +1h30m / 07:30 / "
            "2026-10-23T07:30）"
        ) from exc
    return parsed.replace(tzinfo=ZONE) if parsed.tzinfo is None else parsed.astimezone(ZONE)


def humanize(at: dt.datetime, now: dt.datetime | None = None) -> str:
    """What the answer says back: 「10 秒后」「1 分 30 秒后」「10 分钟后」「明天07点30分」."""
    at = _aware(at)
    base = _aware(now) if now is not None else dt.datetime.now(ZONE)
    # Round once, up front: a reply rendered a few hundred microseconds after the store
    # would otherwise turn a 10 s reminder into 「9 秒后」.
    delta = int(round((at - base).total_seconds()))
    if delta < 60:
        # A moment that already passed never reads as a negative number of seconds.
        return f"{max(0, delta)} 秒后"
    if delta < 3600:
        minutes, seconds = divmod(delta, 60)
        if seconds == 0:
            return f"{minutes} 分钟后"
        return f"{minutes} 分 {seconds} 秒后"
    day = at.date()
    today = base.date()
    if day == today:
        label = "今天"
    elif day == today + dt.timedelta(days=1):
        label = "明天"
    elif day == today + dt.timedelta(days=2):
        label = "后天"
    else:
        label = f"{at.month}月{at.day}日"
    if at.minute == 0:
        return f"{label}{at.hour:02d}点"
    return f"{label}{at.hour:02d}点{at.minute:02d}分"


class VoiceParseError(ValueError):
    """A sentence that *is* a reminder request but cannot be turned into one.

    ``reason`` is the stable part the caller maps to a spoken line; the message is for
    the log. The three cases are deliberately separate: "ask for a time", "ask what
    to remind about", and "that time is already gone" need different answers.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class VoiceReminder:
    """The three elements a fixed-sentence reminder may carry (backlog §f.2)."""

    text: str
    at: dt.datetime
    room: str | None = None


# The voice grammar is intentionally tiny: 「提醒我 / 叫我 / 提醒」 + day + period +
# hour + action + optional 在<room>. Anything outside it still reaches the LLM tool.
_STRONG_TRIGGERS = ("提醒我", "提醒一下", "提醒")
# 「叫我」 is also a rename ("叫我小王"): it qualifies only with a parseable time.
_WEAK_TRIGGERS = ("叫我",)
_TRIGGERS = (*_STRONG_TRIGGERS, *_WEAK_TRIGGERS)
_DAY_WORDS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}
_PERIODS = ("凌晨", "早晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "夜里")
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
# 一刻 = 15 min, 三刻 = 45 min. 二刻 follows the same scale (30); 四刻 would be the
# next hour and is deliberately not claimed.
_QUARTERS = {"一": 15, "二": 30, "三": 45}
_NUM = r"[0-9零〇一二两三四五六七八九十]{1,3}"
_VOICE_TIME = re.compile(
    rf"(?:(?P<day>{'|'.join(_DAY_WORDS)}))?\s*"
    rf"(?:(?P<period>{'|'.join(_PERIODS)}))?\s*"
    rf"(?:"
    rf"(?P<hour>{_NUM})\s*[点时]"
    rf"(?:\s*(?:(?P<half>半)|(?P<quarter>[一二三])刻(?:钟)?"
    rf"|(?P<minute>{_NUM})\s*分?|(?P<zheng>整)))?"
    rf"|(?P<clock_h>\d{{1,2}})[:：](?P<clock_m>\d{{2}})"
    rf")"
)
_LEADING_JUNK = " \t，,、：:。.！!？?；;"
_TRAILING_JUNK = " \t，,、：:。.！!？?；;"
# Relative times the strict grammar does not compute still belong to the LLM tool
# (`parse_at` takes +10m etc.); a sentence that already said when must not be answered
# with "什么时候?". Minutes are claimed only in the exact 「N分钟(之)后」 shape below;
# 过N分钟 / 半小时 / 小时 / 天 / 周 / 刻 / 区间 land here and are deferred. A doubled
# 「分钟钟」 is a typo, not a claim: it still cues a time, so it is deferred too.
_RELATIVE_TIME = re.compile(
    r"(?:分钟钟|分钟|秒钟|秒|小时|钟头)\s*(?:之?后|以后)"
    r"|过\s*(?:\d+|[零〇一二两三四五六七八九十百]+|几)\s*"
    r"(?:分钟|秒钟|秒|小时|钟头)"
)
# task-29: 「10秒钟之后」 is an exact moment the grammar can compute, so it must not
# reach the model (which used to answer +10m). The arithmetic is delegated to
# `parse_at`; the voice path and the tool path therefore cannot disagree.
_SECONDS_REL = re.compile(
    rf"(?P<seconds>{_NUM})\s*(?:秒钟|秒)\s*(?:之?后|以后)"
)
# task-B (0.8.5): 「一分钟后提醒我喝水。」 is the same exact moment, one unit up; it used
# to fall to the model while 「一分钟后」 already said when. Only the standard
# N分钟(之)后 shape is claimed - not 过N分钟 / N个半小时 / hours / days / weeks /
# quarters / intervals. 「分钟钟」 must not match: after the unit only 后/之后/以后 is
# accepted, so the doubled 钟 falls through (deferred as a time cue above, never stored
# as minutes).
_MINUTES_REL = re.compile(
    rf"(?P<minutes>{_NUM})\s*分钟\s*(?:之?后|以后)"
)
# Any of these means the sentence already said when, even when the narrow grammar
# cannot compute that shape. Asking "什么时候?" there is a wrong clarify; the LLM
# tool (parse_at) owns those forms. Note this is only consulted when the strict
# grammar fails, so 「七点一刻」 is parsed below rather than deferred.
_TIME_CUE = re.compile(
    r"今天|今日|明天|明日|后天|明早|明晚|今早|今晚|"
    r"每天|每晚|每日|"
    r"凌晨|早晨|早上|上午|中午|下午|傍晚|晚上|夜里|"
    r"(?:\d+|[零〇一二两三四五六七八九十百]+|几)\s*天\s*(?:之?后|以后)|"
    r"周|星期|礼拜|刻"
)


def _looks_like_time(text: str) -> bool:
    """True when the sentence names a time the fixed grammar did not claim."""
    return bool(_TIME_CUE.search(text) or _RELATIVE_TIME.search(text))


# The action is one sentence. A sentence-final mark with more text after it means the
# utterance carries a second sentence - typically a name claim (「我是小志」), sometimes
# another instruction. This narrow grammar cannot structure that tail, and pasting it
# into the reminder's text would speak it at due time; the model owns such sentences.
_SENTENCE_END = re.compile(r"[。！？!?；;]")


def _has_second_sentence(action: str) -> bool:
    """True when ``action`` is more than one sentence (trailing marks excluded)."""
    body = action.strip(_TRAILING_JUNK)
    match = _SENTENCE_END.search(body)
    if match is None:
        return False
    return bool(body[match.end():].strip(_LEADING_JUNK + _TRAILING_JUNK))


def _lstrip_junk(text: str) -> str:
    return text.lstrip(_LEADING_JUNK)


def _cn_number(token: str) -> int | None:
    """`七` / `十五` / `二十三` / `零五` / `23` -> int, None when not a number."""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_DIGITS:
        return _CN_DIGITS[token]
    if token[0] in ("零", "〇") and len(token) == 2 and token[1] in _CN_DIGITS:
        return _CN_DIGITS[token[1]]
    if "十" not in token:
        return None
    left, _, right = token.partition("十")
    if "十" in right:
        return None
    tens = _CN_DIGITS.get(left) if left else 1
    ones = _CN_DIGITS.get(right) if right else 0
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def _take_trigger(text: str) -> tuple[str, str] | None:
    """Consume a leading trigger and return ``(trigger, rest)``, or None when absent."""
    for trigger in _TRIGGERS:
        if text.startswith(trigger):
            rest = _lstrip_junk(text[len(trigger):])
            # 「提醒一下我…」 / 「提醒我一下…」: both fillers are optional.
            if rest.startswith("一下"):
                rest = _lstrip_junk(rest[2:])
            if rest.startswith("我"):
                rest = _lstrip_junk(rest[1:])
            return trigger, rest
    return None


def _take_room(rest: str, rooms: Sequence[str]) -> tuple[str | None, str]:
    """`在<room>` at the start of the action, matched exactly against configured rooms.

    An unknown room is *not* an error: 「在卧室关灯」 is a valid action, and guessing a
    room from a near-match would route a reminder somewhere the user did not ask for.
    Longest names win, so 「客厅」 and 「客厅阳台」 cannot shadow each other.
    """
    if not rest.startswith("在"):
        return None, rest
    body = rest[1:]
    known = sorted({room.strip() for room in rooms if room and room.strip()},
                   key=len, reverse=True)
    for room in known:
        if not body.startswith(room):
            continue
        tail = body[len(room):]
        for suffix in ("那边", "那台", "里"):
            if tail.startswith(suffix):
                tail = tail[len(suffix):]
                break
        return room, _lstrip_junk(tail)
    return None, rest


def _apply_period(period: str | None, hour: int) -> tuple[int, int]:
    """Normalise a clock hour + Chinese period to 24h, plus a midnight day shift."""
    if hour == 24:
        return 0, 1
    if period is None:
        if not 0 <= hour <= 23:
            raise VoiceParseError("time")
        return hour, 0
    if period == "凌晨":
        if hour == 12:
            return 0, 0
        if not 0 <= hour <= 6:
            raise VoiceParseError("time")
        return hour, 0
    if period in ("早晨", "早上", "上午"):
        if not 0 <= hour <= 12:
            raise VoiceParseError("time")
        return hour, 0
    if period == "中午":
        if hour in (11, 12):
            return hour, 0
        if 1 <= hour <= 3:
            return hour + 12, 0
        raise VoiceParseError("time")
    if period in ("下午", "傍晚"):
        if hour == 12:
            return 12, 0
        if 1 <= hour <= 11:
            return hour + 12, 0
        if 13 <= hour <= 23:
            # 「下午15点」 already names 24h: adding 12 would invent 27:00.
            return hour, 0
        raise VoiceParseError("time")
    # 晚上 / 夜里: 12 is the midnight after this evening.
    if hour == 12:
        return 0, 1
    if hour == 0:
        return 0, 0
    if 1 <= hour <= 11:
        return hour + 12, 0
    if 13 <= hour <= 23:
        # 「晚上21:30」 / 「晚上22点」 are already 24h; keep them as spoken.
        return hour, 0
    raise VoiceParseError("time")


def _voice_moment(match: re.Match[str], base: dt.datetime) -> dt.datetime:
    """Turn one regex match into an aware Shanghai datetime (past explicit days fail)."""
    if match.group("clock_h") is not None:
        raw_hour, minute = int(match.group("clock_h")), int(match.group("clock_m"))
        if raw_hour > 24 or minute > 59 or (raw_hour == 24 and minute != 0):
            raise VoiceParseError("time")
        hour, day_shift = _apply_period(match.group("period"), raw_hour)
    else:
        parsed_hour = _cn_number(match.group("hour") or "")
        if parsed_hour is None:
            raise VoiceParseError("time")
        if match.group("quarter") is not None:
            minute = _QUARTERS[match.group("quarter")]
        elif match.group("minute") is not None:
            parsed_minute = _cn_number(match.group("minute"))
            if parsed_minute is None:
                raise VoiceParseError("time")
            minute = parsed_minute
        else:
            minute = 30 if match.group("half") else 0
        if not 0 <= minute <= 59:
            raise VoiceParseError("time")
        hour, day_shift = _apply_period(match.group("period"), parsed_hour)

    day_word = match.group("day")
    explicit = day_word is not None
    offset = _DAY_WORDS[day_word] if explicit else 0
    target = dt.datetime.combine(
        base.date() + dt.timedelta(days=offset + day_shift),
        dt.time(hour, minute),
        ZONE,
    )
    if explicit:
        if target <= base:
            raise VoiceParseError("past")
        return target
    if target <= base:
        target += dt.timedelta(days=1)
    return target


def parse_voice(
    sentence: str,
    *,
    rooms: Sequence[str] = (),
    now: dt.datetime | None = None,
) -> VoiceReminder | None:
    """The fixed-sentence reminder grammar (backlog §f.2), or None when not ours.

    Accepted shapes::

        提醒我明天早上七点吃药
        叫我后天晚上八点半在客厅关火
        下午三点提醒我在书房开会
        明天早上七点叫我在客厅吃药
        10秒钟之后提醒我喝水
        提醒我十秒后在客厅关火
        一分钟后提醒我喝水
        提醒我十分钟之后在客厅关火

    Time words: 今天/明天/后天 (optional; absent means "the next occurrence"),
    早上/上午/中午/下午/晚上, an hour with Chinese or Arabic digits, and 半 / 一刻 /
    三刻 / N分; plus relative times of seconds and minutes (``N秒后`` / ``N秒钟之后`` /
    ``N秒以后`` / ``N分钟后`` / ``N分钟之后`` / ``N分钟以后``, Chinese or Arabic digits),
    whose arithmetic comes from :func:`parse_at`. The action is the remainder; a leading
    ``在<room>`` is a routing target only when the room equals a configured
    ``devices[].room`` exactly.

    The action must be **one sentence**. A sentence-final mark (。！？!?；;) with more
    text after it means the utterance carries a second sentence - a name claim
    (「我是小志」) or another instruction - which this grammar cannot structure; the
    model handles it instead of the reminder text swallowing the tail.

    Returns None for a sentence without a trigger (the LLM path owns it), and for a
    triggered sentence that names a time shape this grammar does not compute
    (「明早八点」「3天后」「每周一」「七点零一刻」「半小时后」「过20分钟」): a wrong
    "什么时候?" is worse than handing it to the model. Multi-sentence utterances are
    deferred for the same reason. The weak trigger 「叫我」 also returns None without a
    parseable time, because it is commonly a rename (「叫我小王」).

    Raises :class:`VoiceParseError` for a sentence that *is* a reminder but is missing
    a usable time or an action, or names a past time: the caller must answer clearly
    and must not create anything.
    """
    raw = " ".join((sentence or "").split()).strip()
    if not raw:
        return None
    base = _aware(now) if now is not None else dt.datetime.now(ZONE)

    taken = _take_trigger(raw)
    time_first = taken is None
    trigger, rest = ("", raw) if taken is None else taken
    relative = _SECONDS_REL.match(rest)
    if relative is None:
        relative = _MINUTES_REL.match(rest)
    match = None if relative is not None else _VOICE_TIME.match(rest)
    if relative is None and match is None:
        if time_first:
            return None  # no trigger at the start and no leading time: not a reminder
        if _looks_like_time(rest):
            # 「提醒我今晚八点吃药」「提醒我过20分钟关火」: the LLM tool handles these.
            return None
        if trigger in _WEAK_TRIGGERS:
            # 「叫我小王」「叫我怎么相信你」: a rename or other intent, not a reminder.
            return None
        raise VoiceParseError("time")
    end = relative.end() if relative is not None else match.end()
    after_time = _lstrip_junk(rest[end:])
    if time_first:
        triggered = _take_trigger(after_time)
        if triggered is None:
            return None  # a bare time is not a reminder request
        _, after_time = triggered
    if relative is None and (
            after_time.startswith("刻")
            or re.match(r"(?:差|过)[一二三]刻", after_time)):
        # An unhandled quarter shape (七点零一刻 / 差一刻 / 过一刻) matched the hour
        # but left the quarter in the action. Storing 07:01 or 07:00 would be wrong;
        # let the model parse the sentence instead.
        return None
    if after_time.startswith(("以后", "之后", "以前", "之前")):
        # 「七点以后」 is an interval, not a moment: let the LLM ask or convert it.
        return None

    if relative is not None:
        # One arithmetic for both entry points: the tool passes "+10s" / "+10m" here too.
        groups = relative.groupdict()
        unit = "s" if "seconds" in groups else "m"
        amount = _cn_number(groups.get("seconds") or groups.get("minutes") or "")
        if amount is None:
            raise VoiceParseError("time")
        at = parse_at(f"+{amount}{unit}", now=base)
    else:
        at = _voice_moment(match, base)

    room, action = _take_room(after_time, rooms)
    if _has_second_sentence(action):
        # B (0.8.5): a name claim or a second instruction after the action is not
        # reminder text; the model owns the whole utterance (it used to store
        # 「喝水。我是小志」 as the reminder and speak it at due time).
        return None
    text = " ".join(action.strip(_TRAILING_JUNK).split())
    if not text:
        raise VoiceParseError("text")
    return VoiceReminder(text=text, at=at, room=room)


@dataclass(frozen=True)
class Reminder:
    """One pending (or once-fired) reminder. Frozen: state changes go through the store."""

    id: str
    text: str
    at: dt.datetime
    created_at: dt.datetime
    fired_at: dt.datetime | None = None
    # The room the user named out loud; None means "wherever default_output is" (§e.2).
    room: str | None = None

    @property
    def fired(self) -> bool:
        return self.fired_at is not None


def _to_json(item: Reminder) -> dict[str, Any]:
    return {
        "id": item.id,
        "text": item.text,
        "at": item.at.isoformat(),
        "created_at": item.created_at.isoformat(),
        "fired_at": item.fired_at.isoformat() if item.fired_at else None,
        # Written as null for an unrouted reminder. A 0.8.0 file has no key at all;
        # `_from_json` reads both as None (backward compatible).
        "room": item.room,
    }


def _from_json(entry: Any) -> Reminder | None:
    """A junk row is dropped, never raised: a corrupt file must not mute the assistant."""
    if not isinstance(entry, dict):
        return None
    rid = str(entry.get("id") or "").strip()
    text = str(entry.get("text") or "").strip()
    if not rid or not text:
        return None
    try:
        at = dt.datetime.fromisoformat(str(entry["at"]))
        created = dt.datetime.fromisoformat(str(entry.get("created_at") or entry["at"]))
    except (KeyError, TypeError, ValueError):
        return None
    fired: dt.datetime | None = None
    raw_fired = entry.get("fired_at")
    if raw_fired:
        try:
            fired = dt.datetime.fromisoformat(str(raw_fired))
        except ValueError:
            fired = None
    return Reminder(
        id=rid,
        text=text,
        at=_aware(at),
        created_at=_aware(created),
        fired_at=_aware(fired) if fired else None,
        room=str(entry.get("room") or "").strip() or None,
    )


def _prune(items: list[Reminder], base: dt.datetime) -> list[Reminder]:
    """Drop receipts that are too old to matter, and cap how many survive a restart."""
    cutoff = base - dt.timedelta(hours=KEEP_FIRED_HOURS)
    live = [it for it in items if not it.fired or (it.fired_at is not None and it.fired_at > cutoff)]
    fired = sorted((it for it in live if it.fired), key=lambda it: it.fired_at or it.at)
    if len(fired) > MAX_FIRED:
        drop = {it.id for it in fired[: len(fired) - MAX_FIRED]}
        live = [it for it in live if it.id not in drop]
    return live


class ReminderStore:
    """JSON-backed reminders under one data directory.

    The file is the shared state, so the tool handlers and the scheduler do not have to
    pass objects around: two stores pointed at the same directory see the same reminders.
    Writes are single-writer (the control plane's event loop); ``os.replace`` is what keeps
    a crash in the middle of a save from truncating the file.
    """

    def __init__(self, data_dir: str | Path) -> None:
        if not str(data_dir).strip():
            # An empty data_dir would resolve to the process's cwd. Refuse loudly at
            # construction instead of scattering reminders.json into whatever was mounted.
            raise ValueError("data_dir is empty: reminders need system.data_dir")
        self.dir = Path(data_dir).expanduser()

    @property
    def path(self) -> Path:
        return self.dir / STORE_FILENAME

    def load(self, at: dt.datetime | None = None,
             prune: bool = True) -> list[Reminder]:
        """Items on disk; `prune=False` keeps fired receipts past their day.

        The retry hook (requeue) must not race the housekeeping: a receipt fired
        a day ago is pruned by the clock, and a retry that asked later than that
        would silently find nothing (this exact test failed the morning after
        its fixed reference date).
        """
        base = _aware(at) if at is not None else dt.datetime.now(ZONE)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning("reminders: cannot read %s: %s", self.path, exc)
            return []
        entries = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return []
        seen: set[str] = set()
        items: list[Reminder] = []
        for entry in entries:
            item = _from_json(entry)
            if item is None or item.id in seen:
                continue
            seen.add(item.id)
            items.append(item)
        return _prune(items, base) if prune else items

    def _write(self, items: list[Reminder]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": STORE_VERSION, "items": [_to_json(it) for it in items]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def add(self, text: str, at: dt.datetime, *, now: dt.datetime | None = None,
            room: str | None = None) -> Reminder:
        clean = " ".join((text or "").split())[:MAX_TEXT]
        if not clean:
            raise ValueError("提醒内容不能为空")
        clean_room = str(room or "").strip() or None
        base = _aware(now) if now is not None else dt.datetime.now(ZONE)
        items = self.load(base)
        pending = [it for it in items if not it.fired]
        if len(pending) >= MAX_ITEMS:
            # Old build's rule: drop the earliest-due pending item, keep the newest asks.
            drop = min(pending, key=lambda it: it.at)
            log.info("reminders: dropping %s to make room", drop.id)
            items = [it for it in items if it.id != drop.id]
        taken = {it.id for it in items}
        rid = uuid.uuid4().hex[:6]
        while rid in taken:
            rid = uuid.uuid4().hex[:6]
        item = Reminder(id=rid, text=clean, at=_aware(at), created_at=base, room=clean_room)
        items.append(item)
        self._write(items)
        return item

    def pending(self, now: dt.datetime | None = None) -> list[Reminder]:
        """Everything not yet fired, earliest first (overdue entries included)."""
        return sorted((it for it in self.load(now) if not it.fired), key=lambda it: it.at)

    def upcoming(self, now: dt.datetime | None = None) -> list[Reminder]:
        base = _aware(now) if now is not None else dt.datetime.now(ZONE)
        return [it for it in self.pending(base) if it.at > base]

    def peek_due(self, now: dt.datetime | None = None) -> list[Reminder]:
        """Due items **without** marking them: what the scheduler is about to fire."""
        base = _aware(now) if now is not None else dt.datetime.now(ZONE)
        return [it for it in self.pending(base) if it.at <= base]

    def mark_fired(self, ids: Iterable[str], now: dt.datetime | None = None) -> list[Reminder]:
        """Stamp `fired_at` and persist. Returns the items that actually changed.

        An id that is already fired is left alone, so a second call returns nothing — this
        is the durable half of "one reminder fires once", including across a restart.
        """
        wanted = {str(rid) for rid in ids if rid}
        if not wanted:
            return []
        base = _aware(now) if now is not None else dt.datetime.now(ZONE)
        items = self.load(base)
        changed: list[Reminder] = []
        for i, item in enumerate(items):
            if item.id in wanted and not item.fired:
                items[i] = replace(item, fired_at=base)
                changed.append(items[i])
        if changed:
            self._write(items)
        return sorted(changed, key=lambda it: it.at)

    def cancel(self, rid: str) -> Reminder | None:
        """Remove a pending reminder by id (``#`` prefix tolerated: models add it)."""
        key = (rid or "").strip().lstrip("#")
        if not key:
            return None
        items = self.load()
        target = next((it for it in items if it.id == key and not it.fired), None)
        if target is None:
            return None
        self._write([it for it in items if it.id != key])
        return target

    def requeue(self, rid: str) -> Reminder | None:
        """Put a fired reminder back in the queue — the next layer's retry hook.

        Used when the "speak it" step failed (device offline, TTS down): the receipt is
        cleared and the item becomes due again on the next tick.
        """
        key = (rid or "").strip().lstrip("#")
        items = self.load(prune=False)
        for i, item in enumerate(items):
            if item.id == key and item.fired:
                items[i] = replace(item, fired_at=None)
                self._write(items)
                return items[i]
        return None


Listener = Callable[[Reminder], None]


class DueScheduler:
    """Pure in-memory scheduler: no asyncio task, no threads, no speech.

    Call :meth:`collect_due` from whatever loop already exists (the next layer's 1–2 s
    tick). Everything is driven by the ``now`` argument, so a unit test can walk a whole
    evening in microseconds.

    Listeners are synchronous and are called after the fired state is on disk. A listener
    that raises is logged and skipped: one broken consumer must not stop the others.
    """

    def __init__(
        self, store: ReminderStore, clock: Callable[[], dt.datetime] | None = None
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: dt.datetime.now(ZONE))
        self._listeners: list[Listener] = []
        self._collecting = False

    def now(self) -> dt.datetime:
        return _aware(self._clock())

    def add_listener(self, listener: Listener) -> None:
        """Register the callback that hands a due reminder to the playback layer."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def peek_due(self, at: dt.datetime | None = None) -> list[Reminder]:
        return self.store.peek_due(at if at is not None else self.now())

    def pending(self, at: dt.datetime | None = None) -> list[Reminder]:
        return self.store.pending(at if at is not None else self.now())

    def collect_due(
        self,
        at: dt.datetime | None = None,
        *,
        grace: float = GRACE_SECONDS,
    ) -> list[Reminder]:
        """Fire what is due, once. Returns the items handed to the listeners.

        Idempotent: `fired_at` is written before any listener runs, so calling this twice
        in a row — or restarting the process and calling it again — yields nothing new.

        Items overdue by more than `grace` seconds are marked fired without being
        announced: a reminder that came due while the box was off all night should not be
        read out at breakfast. Pass a huge grace to keep them.
        """
        if self._collecting:  # a listener re-entered; let the next tick handle new work
            return []
        moment = _aware(at) if at is not None else self.now()
        self._collecting = True
        try:
            due = self.store.peek_due(moment)
            if not due:
                return []
            cutoff = moment - dt.timedelta(seconds=grace)
            stale = [it for it in due if it.at < cutoff]
            if stale:
                self.store.mark_fired([it.id for it in stale], moment)
                log.warning("reminders: %d 条过期超过 %.0f s，跳过不播", len(stale), grace)
            fired = self.store.mark_fired([it.id for it in due if it.at >= cutoff], moment)
            for item in fired:
                for listener in tuple(self._listeners):
                    try:
                        listener(item)
                    except Exception:  # noqa: BLE001 - one consumer cannot kill the tick
                        log.exception("reminders: listener failed for %s", item.id)
            return fired
        finally:
            self._collecting = False


# ---------------------------------------------------------------- tool surface
SET_SPEC = ToolSpec(
    name="local.set_reminder",
    description=(
        "定闹钟、提醒、计时。用户说「十秒钟后提醒我喝水」「十分钟后提醒我关火」"
        "「半小时后叫我」「明早八点提醒我吃药」就用这个。when 必须换算成这几种之一："
        "+10s（十秒后）、+1m30s（一分半后）、+10m（十分钟后）、+1h30m（一个半小时后）、"
        "07:30（下一个七点半）、2026-10-23T07:30（指定时刻）。"
        "秒和分不能混：「十秒钟」是 +10s，不是 +10m。"
        "text 是到点要说的话，尽量短，比如「喝水」「关火」「吃药」。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "到点要提醒的事，简短，例如「关火」"},
            "when": {
                "type": "string",
                "description": (
                    "时间：+10s / +1m30s / +90s / +10m / +1h30m / 07:30 / "
                    "2026-10-23T07:30；「十秒钟」用 +10s，不要写成 +10m"
                ),
            },
        },
        "required": ["text", "when"],
    },
    permission=Permission.WRITE,
)

LIST_SPEC = ToolSpec(
    name="local.list_reminders",
    description="列出还没响的提醒。用户问「我有哪些提醒」「刚才那个闹钟几点」时用。",
    input_schema={"type": "object", "properties": {}},
    permission=Permission.READ,
)

CANCEL_SPEC = ToolSpec(
    name="local.cancel_reminder",
    description=(
        "取消一个提醒。用户说「取消提醒」「不用叫我了」时用；id 从 list_reminders 的结果里拿，"
        "不要自己编。"
    ),
    input_schema={
        "type": "object",
        "properties": {"id": {"type": "string", "description": "提醒编号，例如 a1b2c3"}},
        "required": ["id"],
    },
    permission=Permission.WRITE,
)

Handler = Callable[[dict[str, Any]], Awaitable[str]]


def _set_handler(store: ReminderStore) -> Handler:
    async def handle(args: dict[str, Any]) -> str:
        text = str(args.get("text") or "").strip()
        when = str(args.get("when") or args.get("at") or "").strip()
        if not text:
            return "没说提醒什么：直接问用户要提醒什么事，不要自己编一个。"
        if not when:
            return "没说什么时候提醒：直接问用户是几分钟后还是几点。"
        try:
            at = parse_at(when)
        except TimeParseError:
            return (
                f"时间「{when}」没看懂：直接跟用户确认是几分钟后还是几点，"
                "不要猜一个时间定下去。"
            )
        try:
            # The device that asked is the source room (task-32): an unnamed tool
            # reminder must ring where the person is standing, exactly like the
            # fixed-sentence path. Outside a turn the context is empty -> None,
            # which keeps the historical "wherever default_output is" behaviour.
            source_room = current_tool_context().room or None
            item = store.add(text, at, room=source_room)
        except ValueError:
            return "提醒内容是空的：直接问用户要提醒什么。"
        except OSError as exc:  # data_dir not writable: say so, do not promise a reminder
            log.warning("reminders: cannot save to %s: %s", store.path, exc)
            return "提醒没能存下来（数据目录不可写），直接说这次定不了，别答应用户。"
        log.info("reminders: tool set %s at %s for room %r", item.text,
                 item.at.isoformat(), item.room)
        return f"好，{humanize(item.at)}提醒你{item.text}。"

    return handle


def _list_handler(store: ReminderStore) -> Handler:
    async def handle(_args: dict[str, Any]) -> str:
        items = store.pending()
        if not items:
            return "现在没有待办的提醒。"
        parts = [f"{humanize(it.at)}：{it.text}（编号 {it.id}）" for it in items[:5]]
        tail = f"；另外还有 {len(items) - 5} 个" if len(items) > 5 else ""
        return "有这些提醒：" + "；".join(parts) + tail

    return handle


def _cancel_handler(store: ReminderStore) -> Handler:
    async def handle(args: dict[str, Any]) -> str:
        rid = str(args.get("id") or "").strip()
        if not rid:
            return "没给提醒编号：先列出提醒，再取消用户指的那个。"
        item = store.cancel(rid)
        if item is None:
            return f"没找到编号 {rid.lstrip('#')} 的待办提醒，直接说没找到。"
        return f"取消了：{item.text}。"

    return handle


def build_tools(cfg: Config) -> list[tuple[ToolSpec, Handler]]:
    """Reminder tools, stored under `system.data_dir` (created on first write)."""
    store = ReminderStore(cfg.system.data_dir)
    return [
        (SET_SPEC, _set_handler(store)),
        (LIST_SPEC, _list_handler(store)),
        (CANCEL_SPEC, _cancel_handler(store)),
    ]


def build_scheduler(cfg: Config) -> DueScheduler:
    """The scheduler the next layer ticks; it reads the same file the tools wrote."""
    return DueScheduler(ReminderStore(cfg.system.data_dir))
