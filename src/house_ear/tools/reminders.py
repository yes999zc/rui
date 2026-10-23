"""提醒与定时：只算「什么时候该响」，不管「怎么响」。

职责边界（这一层刻意不做出声）
--------------------------------
本模块做三件事：把模型给的时间词（``+10m`` / ``+1h30m`` / ``07:30`` / ISO）换算成上海
时区的绝对时刻；把提醒原子地落盘到 ``system.data_dir/reminders.json``；用一个纯内存调度
器回答「现在有哪些到期了」，并保证同一条只交出去一次。

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
from collections.abc import Awaitable, Callable, Iterable
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
    """`+10m` / `+1h30m` / `HH:MM` / ISO -> an aware Shanghai datetime.

    Four accepted shapes, in the order they are tried:

    - relative: ``+10m``, ``+1h30m``, ``+90s``, ``10m`` (the ``+`` is optional because a
      model sometimes drops it; the unit is not optional, so ``10`` stays an error).
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
            f"听不懂这个时间：{spec}（用 +10m / +1h30m / 07:30 / 2026-10-23T07:30）"
        ) from exc
    return parsed.replace(tzinfo=ZONE) if parsed.tzinfo is None else parsed.astimezone(ZONE)


def humanize(at: dt.datetime, now: dt.datetime | None = None) -> str:
    """What the answer says back: 「10 分钟后」「今天08点」「明天07点30分」."""
    at = _aware(at)
    base = _aware(now) if now is not None else dt.datetime.now(ZONE)
    delta = (at - base).total_seconds()
    if delta <= 30:
        return "马上"
    if delta < 3600:
        return f"{max(1, round(delta / 60))} 分钟后"
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


@dataclass(frozen=True)
class Reminder:
    """One pending (or once-fired) reminder. Frozen: state changes go through the store."""

    id: str
    text: str
    at: dt.datetime
    created_at: dt.datetime
    fired_at: dt.datetime | None = None

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
        os.replace(tmp, self.path)

    def add(self, text: str, at: dt.datetime, *, now: dt.datetime | None = None) -> Reminder:
        clean = " ".join((text or "").split())[:MAX_TEXT]
        if not clean:
            raise ValueError("提醒内容不能为空")
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
        item = Reminder(id=rid, text=clean, at=_aware(at), created_at=base)
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
        "定闹钟、提醒、计时。用户说「十分钟后提醒我关火」「半小时后叫我」「明早八点提醒我吃药」"
        "就用这个。when 必须换算成这四种之一：+10m（十分钟后）、+1h30m（一个半小时后）、"
        "07:30（下一个七点半）、2026-10-23T07:30（指定时刻）。"
        "text 是到点要说的话，尽量短，比如「关火」「吃药」。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "到点要提醒的事，简短，例如「关火」"},
            "when": {
                "type": "string",
                "description": "时间：+10m / +1h30m / 07:30 / 2026-10-23T07:30",
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
            item = store.add(text, at)
        except ValueError:
            return "提醒内容是空的：直接问用户要提醒什么。"
        except OSError as exc:  # data_dir not writable: say so, do not promise a reminder
            log.warning("reminders: cannot save to %s: %s", store.path, exc)
            return "提醒没能存下来（数据目录不可写），直接说这次定不了，别答应用户。"
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
