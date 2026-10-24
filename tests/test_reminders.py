"""Reminders: the four time shapes, the timezone, and firing exactly once.

Everything here runs on a fixed clock. A reminder store that reads the wall clock is not
testable, and the two bugs this file exists to prevent both come from the clock:

- `datetime.now()` in a container is UTC, so "07:30" would land eight hours late;
- a fired reminder that stays pending gets spoken again on the next tick (or after a
  restart), which is worse than not firing at all.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from rui.config import Config
from rui.tools.reminders import (
    CANCEL_SPEC,
    LIST_SPEC,
    SET_SPEC,
    ZONE,
    DueScheduler,
    ReminderStore,
    TimeParseError,
    build_scheduler,
    build_tools,
    humanize,
    parse_at,
)

REF = dt.datetime(2026, 10, 22, 9, 0, tzinfo=ZONE)


def moment(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def store(tmp_path) -> ReminderStore:
    return ReminderStore(tmp_path / "data")


def call(handler, args: dict) -> str:
    return asyncio.run(handler(args))


def handlers(cfg: Config) -> dict:
    return {spec.llm_name: handler for spec, handler in build_tools(cfg)}


def config_for(tmp_path) -> Config:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    return cfg


# ------------------------------------------------------------------ parse_at
def test_relative_minutes() -> None:
    assert parse_at("+10m", now=REF) == REF + dt.timedelta(minutes=10)


def test_relative_hours_and_minutes() -> None:
    assert parse_at("+1h30m", now=REF) == REF + dt.timedelta(hours=1, minutes=30)
    assert parse_at("+1h30m", now=REF) - REF == dt.timedelta(minutes=90)


def test_relative_seconds_and_optional_plus() -> None:
    assert parse_at("+90s", now=REF) == REF + dt.timedelta(seconds=90)
    # A model sometimes drops the "+"; the unit is what makes it unambiguous.
    assert parse_at("10m", now=REF) == REF + dt.timedelta(minutes=10)


def test_clock_time_later_today() -> None:
    assert parse_at("15:30", now=REF) == moment(22, 15, 30)


def test_clock_time_that_already_passed_is_tomorrow() -> None:
    assert parse_at("08:00", now=REF) == moment(23, 8, 0)


def test_ten_past_midnight_is_tomorrow_not_today() -> None:
    """23:50 + 「00:10」 is 20 minutes away, not 23 hours and 40 minutes."""
    late = moment(22, 23, 50)
    target = parse_at("00:10", now=late)
    assert target == moment(23, 0, 10)
    assert target - late == dt.timedelta(minutes=20)


def test_iso_without_offset_is_read_as_shanghai() -> None:
    parsed = parse_at("2026-10-23T07:30", now=REF)
    assert parsed == moment(23, 7, 30)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == dt.timedelta(hours=8)


def test_iso_with_space_and_seconds() -> None:
    assert parse_at("2026-10-23 07:30:15", now=REF) == dt.datetime(
        2026, 10, 23, 7, 30, 15, tzinfo=ZONE
    )


def test_iso_with_offset_is_converted_to_shanghai() -> None:
    parsed = parse_at("2026-10-22T12:00:00+00:00", now=REF)
    assert parsed == moment(22, 20, 0)
    assert parsed.astimezone(dt.UTC).hour == 12


def test_iso_with_z_suffix() -> None:
    assert parse_at("2026-10-22T00:00:00Z", now=REF) == moment(22, 8, 0)


def test_a_date_without_a_time_is_refused() -> None:
    """Midnight is never what 「10月23日」 means, so it must not be guessed."""
    with pytest.raises(TimeParseError):
        parse_at("2026-10-23", now=REF)


def test_unknown_time_is_refused_with_the_accepted_shapes() -> None:
    for spec in ("明天早上", "", "25:00", "7点半"):
        with pytest.raises(TimeParseError):
            parse_at(spec, now=REF)


def test_a_date_that_does_not_exist_is_refused() -> None:
    with pytest.raises(TimeParseError):
        parse_at("2026-02-30T07:30", now=REF)


# ------------------------------------------------------------------ timezone
def test_shanghai_is_eight_hours_ahead_of_utc_for_the_same_instant() -> None:
    same = moment(22, 12)
    utc = same.astimezone(dt.UTC)
    assert same.utcoffset() == dt.timedelta(hours=8)
    assert utc.hour == 4
    assert same.hour - utc.hour == 8  # the wall clocks differ by the offset, not the instant


def test_iso_naive_is_not_read_as_utc() -> None:
    """The bug this pins: 07:30 read in UTC is 15:30 in Shanghai, eight hours late."""
    parsed = parse_at("2026-10-22T12:00:00", now=REF)
    assert parsed.hour == 12
    assert parsed.astimezone(dt.UTC).hour == 4


def test_a_utc_now_is_converted_before_the_clock_rule_runs() -> None:
    # 08:00 UTC is 16:00 in Shanghai, so 09:00 has already gone by and means tomorrow.
    utc_now = dt.datetime(2026, 10, 22, 8, 0, tzinfo=dt.UTC)
    assert parse_at("09:00", now=utc_now) == moment(23, 9)


def test_the_zone_has_no_dst_to_get_wrong() -> None:
    winter = dt.datetime(2026, 1, 15, 12, tzinfo=ZONE)
    summer = dt.datetime(2026, 7, 15, 12, tzinfo=ZONE)
    assert winter.utcoffset() == summer.utcoffset() == dt.timedelta(hours=8)


# ------------------------------------------------------------------ humanize
def test_humanize_minutes_then_clock() -> None:
    assert humanize(REF + dt.timedelta(seconds=20), now=REF) == "马上"
    assert humanize(REF + dt.timedelta(minutes=10), now=REF) == "10 分钟后"
    assert humanize(moment(22, 17), now=REF) == "今天17点"
    assert humanize(moment(23, 7, 30), now=REF) == "明天07点30分"
    assert humanize(moment(25, 7, 30), now=REF) == "10月25日07点30分"


# ------------------------------------------------------------------ the store
def test_add_persists_and_a_restart_still_sees_it(tmp_path) -> None:
    first = store(tmp_path)
    item = first.add("关火", moment(22, 10), now=REF)
    assert item.id and item.text == "关火"
    assert first.path == tmp_path / "data" / "reminders.json"
    raw = json.loads(first.path.read_text(encoding="utf-8"))
    assert raw["items"][0]["at"] == "2026-10-22T10:00:00+08:00"

    restarted = ReminderStore(tmp_path / "data")
    pending = restarted.pending(now=REF)
    assert [(it.id, it.text, it.at) for it in pending] == [(item.id, "关火", moment(22, 10))]


def test_an_empty_data_dir_is_refused() -> None:
    with pytest.raises(ValueError):
        ReminderStore("  ")


def test_data_dir_is_created_on_the_first_write(tmp_path) -> None:
    deep = ReminderStore(tmp_path / "nested" / "data")
    assert not (tmp_path / "nested").exists()
    deep.add("吃药", moment(22, 10), now=REF)
    assert deep.path.exists()


def test_a_corrupt_file_reads_as_empty_instead_of_raising(tmp_path) -> None:
    s = store(tmp_path)
    s.dir.mkdir(parents=True)
    s.path.write_text("{ not json", encoding="utf-8")
    assert s.load(at=REF) == []
    s.add("关火", moment(22, 10), now=REF)  # and is overwritten cleanly
    assert len(s.pending(now=REF)) == 1


def test_junk_rows_are_dropped_and_good_rows_kept(tmp_path) -> None:
    s = store(tmp_path)
    s.dir.mkdir(parents=True)
    s.path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {"id": "", "text": "no id", "at": "2026-10-22T10:00:00+08:00"},
                    {"id": "abc123", "text": "", "at": "2026-10-22T10:00:00+08:00"},
                    {"id": "def456", "text": "关火", "at": "not-a-time"},
                    {"id": "ghi789", "text": "吃药", "at": "2026-10-22T10:00:00+08:00"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert [it.id for it in s.pending(now=REF)] == ["ghi789"]


def test_empty_text_is_refused_and_whitespace_is_collapsed(tmp_path) -> None:
    s = store(tmp_path)
    with pytest.raises(ValueError):
        s.add("   ", moment(22, 10), now=REF)
    assert s.add("  关   火  ", moment(22, 10), now=REF).text == "关 火"


def test_the_oldest_due_item_makes_room_when_the_store_is_full(tmp_path) -> None:
    s = store(tmp_path)
    for i in range(21):
        s.add(f"第{i}条", moment(22, 10) + dt.timedelta(minutes=i), now=REF)
    texts = [it.text for it in s.pending(now=REF)]
    assert len(texts) == 20
    assert "第0条" not in texts  # earliest due dropped, newest asks kept


def test_cancel_removes_only_that_item(tmp_path) -> None:
    s = store(tmp_path)
    keep = s.add("吃药", moment(22, 10), now=REF)
    drop = s.add("关火", moment(22, 11), now=REF)
    assert s.cancel(f"#{drop.id}") is not None  # models like to prefix the id with '#'
    assert [it.id for it in s.pending(now=REF)] == [keep.id]
    assert s.cancel("nope") is None


def test_mark_fired_is_idempotent(tmp_path) -> None:
    s = store(tmp_path)
    item = s.add("关火", moment(22, 10), now=REF)
    assert [it.id for it in s.mark_fired([item.id], moment(22, 10))] == [item.id]
    assert s.mark_fired([item.id], moment(22, 10)) == []
    assert s.pending(now=REF) == []


# --------------------------------------------------------------- the scheduler
def test_collect_due_fires_once_even_across_a_restart(tmp_path) -> None:
    s = store(tmp_path)
    s.add("关火", REF - dt.timedelta(seconds=1), now=REF - dt.timedelta(minutes=5))
    seen: list[str] = []
    sched = DueScheduler(s)
    sched.add_listener(lambda item: seen.append(item.text))

    assert [it.text for it in sched.collect_due(REF)] == ["关火"]
    assert sched.collect_due(REF) == []
    assert seen == ["关火"]  # the listener saw it exactly once

    restarted = DueScheduler(ReminderStore(tmp_path / "data"))
    assert restarted.collect_due(REF) == []  # fired_at is on disk, not just in memory


def test_nothing_fires_before_its_time(tmp_path) -> None:
    s = store(tmp_path)
    s.add("关火", REF + dt.timedelta(seconds=1), now=REF)
    assert DueScheduler(s).collect_due(REF) == []
    assert len(s.pending(now=REF)) == 1


def test_a_pending_reminder_survives_a_restart_and_fires_on_time(tmp_path) -> None:
    store(tmp_path).add("吃药", moment(22, 10), now=REF)
    restarted = DueScheduler(ReminderStore(tmp_path / "data"))
    assert [it.text for it in restarted.pending(REF)] == ["吃药"]
    assert restarted.collect_due(REF) == []
    assert [it.text for it in restarted.collect_due(moment(22, 10))] == ["吃药"]


def test_the_injected_clock_drives_an_argumentless_tick(tmp_path) -> None:
    s = store(tmp_path)
    s.add("关火", REF, now=REF - dt.timedelta(minutes=1))
    sched = DueScheduler(s, clock=lambda: REF)
    assert [it.text for it in sched.collect_due()] == ["关火"]


def test_an_old_overdue_reminder_is_skipped_not_read_out_at_breakfast(tmp_path) -> None:
    s = store(tmp_path)
    s.add("昨晚的关火", REF - dt.timedelta(hours=8), now=REF - dt.timedelta(hours=9))
    sched = DueScheduler(s)
    assert sched.collect_due(REF) == []
    assert s.pending(now=REF) == []  # consumed, so it cannot fire later either


def test_grace_can_be_widened_for_a_deliberate_late_delivery(tmp_path) -> None:
    s = store(tmp_path)
    s.add("关火", REF - dt.timedelta(hours=8), now=REF - dt.timedelta(hours=9))
    fired = DueScheduler(s).collect_due(REF, grace=24 * 3600)
    assert [it.text for it in fired] == ["关火"]


def test_one_broken_listener_does_not_block_the_others(tmp_path) -> None:
    s = store(tmp_path)
    s.add("关火", REF, now=REF - dt.timedelta(minutes=1))
    seen: list[str] = []

    def boom(_item) -> None:
        raise RuntimeError("playback exploded")

    sched = DueScheduler(s)
    sched.add_listener(boom)
    sched.add_listener(lambda item: seen.append(item.id))
    assert len(sched.collect_due(REF)) == 1
    assert len(seen) == 1


def test_a_failed_playback_can_be_requeued(tmp_path) -> None:
    s = store(tmp_path)
    item = s.add("关火", REF, now=REF - dt.timedelta(minutes=1))
    DueScheduler(s).collect_due(REF)
    assert s.requeue(item.id) is not None
    assert [it.text for it in DueScheduler(s).collect_due(REF)] == ["关火"]


def test_fired_receipts_are_pruned_after_a_day(tmp_path) -> None:
    s = store(tmp_path)
    s.add("关火", REF, now=REF - dt.timedelta(minutes=1))
    DueScheduler(s).collect_due(REF)
    assert s.load(at=REF + dt.timedelta(hours=1)) != []
    assert s.load(at=REF + dt.timedelta(hours=25)) == []


# ------------------------------------------------------------------ tool surface
def test_set_then_list_then_cancel_through_the_tool_handlers(tmp_path) -> None:
    h = handlers(config_for(tmp_path))

    reply = call(h["set_reminder"], {"text": "关火", "when": "+10m"})
    assert reply.startswith("好，") and "关火" in reply

    listed = call(h["list_reminders"], {})
    assert "关火" in listed
    rid = listed.split("编号 ")[1].split("）")[0]

    assert call(h["cancel_reminder"], {"id": rid}).startswith("取消了")
    assert call(h["list_reminders"], {}) == "现在没有待办的提醒。"


def test_the_store_lives_in_the_configured_data_dir(tmp_path) -> None:
    cfg = config_for(tmp_path)
    call(handlers(cfg)["set_reminder"], {"text": "关火", "when": "+10m"})
    assert (tmp_path / "data" / "reminders.json").exists()


def test_an_unparseable_time_is_refused_without_creating_a_reminder(tmp_path) -> None:
    h = handlers(config_for(tmp_path))
    reply = call(h["set_reminder"], {"text": "关火", "when": "明天早上"})
    assert "没看懂" in reply
    assert not (tmp_path / "data" / "reminders.json").exists()
    assert call(h["list_reminders"], {}) == "现在没有待办的提醒。"


def test_missing_text_or_time_asks_instead_of_guessing(tmp_path) -> None:
    h = handlers(config_for(tmp_path))
    assert "提醒什么" in call(h["set_reminder"], {"when": "+10m"})
    assert "什么时候" in call(h["set_reminder"], {"text": "关火"})
    assert store(tmp_path).pending(now=REF) == []


def test_cancel_without_an_id_asks_for_one(tmp_path) -> None:
    h = handlers(config_for(tmp_path))
    assert "编号" in call(h["cancel_reminder"], {})
    assert "没找到" in call(h["cancel_reminder"], {"id": "zzzzzz"})


def test_llm_names_and_schemas_are_what_the_model_calls() -> None:
    specs = {spec.llm_name: spec for spec in (SET_SPEC, LIST_SPEC, CANCEL_SPEC)}
    assert set(specs) == {"set_reminder", "list_reminders", "cancel_reminder"}
    tool = specs["set_reminder"].as_llm_tool()
    assert tool["name"] == "set_reminder"
    assert tool["parameters"]["required"] == ["text", "when"]
    assert "when" in tool["parameters"]["properties"]


def test_build_local_tools_keeps_the_information_tools_and_adds_reminders(tmp_path) -> None:
    from rui.tools import build_local_tools

    names = {spec.llm_name for spec, _ in build_local_tools(config_for(tmp_path))}
    assert {"weather", "news", "set_reminder", "list_reminders", "cancel_reminder"} <= names


def test_build_scheduler_reads_the_same_file_the_tools_write(tmp_path) -> None:
    cfg = config_for(tmp_path)
    call(handlers(cfg)["set_reminder"], {"text": "关火", "when": "+1m"})
    sched = build_scheduler(cfg)
    assert [it.text for it in sched.pending()] == ["关火"]
