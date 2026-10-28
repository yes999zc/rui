"""C1: the spoken reminder entry, the fixed time grammar, and room routing.

The LLM tool path (`local.set_reminder`) keeps its four time shapes; this file pins the
*voice* path that runs before the model: a deliberately narrow sentence grammar
(trigger + 今天/明天/后天 or N秒/分钟后 + 早上/晚上 + 七点半 + action + optional 在<room>),
and the three-tier delivery rule from backlog §e.2:

    specified room (online)  >  default_output  >  default_output + 「<room>那台没在线」

Everything runs on a fixed Shanghai clock. 「23 点 59 分」 has its own test because that
is the boundary where "today" stops meaning "later today". The action must be **one
sentence**: a name claim or second instruction after it (「我是小志」) goes to the model.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from rui.config.schema import Config, DeviceConfig, implicit_default_output_id
from rui.tools import reminders as reminders_module
from rui.tools.reminders import (
    ZONE,
    DueScheduler,
    Reminder,
    ReminderStore,
    VoiceParseError,
    humanize,
    parse_voice,
)
from rui.trace import TraceLog, TurnTrace
from rui.web.api import app as app_module

REF = dt.datetime(2026, 10, 28, 9, 0, tzinfo=ZONE)
ROOMS = ("客厅", "书房")


def moment(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def parsed(sentence: str, now: dt.datetime = REF, rooms: tuple[str, ...] = ROOMS):
    return parse_voice(sentence, rooms=rooms, now=now)


# ------------------------------------------------------------------ time table
@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        # Explicit day: 今天 / 明天 / 后天.
        ("提醒我明天早上七点吃药", moment(29, 7)),
        ("叫我后天晚上八点关火", moment(30, 20)),
        ("提醒我今天下午三点半开会", moment(28, 15, 30)),
        # Period-free hour is read literally; 中午/下午/晚上 shift the clock.
        ("提醒我上午十点十五分拿快递", moment(28, 10, 15)),
        ("提醒我中午十二点吃饭", moment(28, 12)),
        ("提醒我下午一点午休", moment(28, 13)),
        ("提醒我晚上九点零五分关灯", moment(28, 21, 5)),
        ("提醒我晚上九点整关灯", moment(28, 21)),
        # Arabic digits and 半.
        ("提醒我明天7点吃药", moment(29, 7)),
        ("叫我18点半下班", moment(28, 18, 30)),
        ("提醒我23点59分睡觉", moment(28, 23, 59)),
        # The HH:MM form goes through the same period rules.
        ("提醒我明天21:30睡觉", moment(29, 21, 30)),
        ("提醒我明天晚上9:30睡觉", moment(29, 21, 30)),
        # A period plus an explicit 13-23 hour is already 24h: never add 12 again and
        # never answer "什么时候?" (C1/task-20: these raised VoiceParseError("time")).
        ("提醒我明天下午15点开会", moment(29, 15)),
        ("提醒我明天下午13点开会", moment(29, 13)),
        ("提醒我明天晚上21:30睡觉", moment(29, 21, 30)),
        ("提醒我晚上22点关灯", moment(28, 22)),
        # 24:00 is the midnight that ends the named day.
        ("提醒我24:00睡觉", moment(29, 0)),
        ("提醒我明天24:00睡觉", moment(30, 0)),
    ],
)
def test_fixed_time_table(sentence: str, expected: dt.datetime) -> None:
    assert parsed(sentence).at == expected


def test_quarter_words_are_fifteen_and_forty_five_minutes() -> None:
    """C1 review: 「一刻」/「三刻」 were stored as 1/3 minutes with 「刻吃药」 as text."""
    for sentence, expected in (
        ("提醒我明天七点一刻吃药", moment(29, 7, 15)),
        ("提醒我明天七点三刻吃药", moment(29, 7, 45)),
        ("提醒我明天七点一刻钟吃药", moment(29, 7, 15)),
    ):
        item = parsed(sentence)
        assert (item.at, item.text) == (expected, "吃药")
    # 半 keeps its 30 minutes.
    assert parsed("提醒我明天七点半吃药").at == moment(29, 7, 30)


def test_a_time_that_already_passed_without_a_day_rolls_to_tomorrow() -> None:
    """「晚上八点」 at 21:00 means tomorrow, the same rule `parse_at` uses."""
    late = moment(28, 21)
    assert parsed("提醒我晚上八点吃药", now=late).at == moment(29, 20)
    # A morning hour said in the evening is tomorrow morning.
    assert parsed("提醒我早上七点吃药", now=late).at == moment(29, 7)


def test_today_means_today_and_a_past_today_is_an_error() -> None:
    with pytest.raises(VoiceParseError) as raised:
        parsed("提醒我今天早上七点吃药")
    assert raised.value.reason == "past"


def test_2359_is_the_last_minute_of_today() -> None:
    """At 23:59:30 the minute has passed; at 09:00 it is still tonight."""
    assert parsed("提醒我23点59分睡觉").at == moment(28, 23, 59)
    with pytest.raises(VoiceParseError) as raised:
        parsed("提醒我今天23点59分睡觉", now=moment(28, 23, 59) + dt.timedelta(seconds=30))
    assert raised.value.reason == "past"
    # Without 今天 the same sentence is simply tomorrow's 23:59.
    assert parsed("提醒我23点59分睡觉",
                  now=moment(28, 23, 59) + dt.timedelta(seconds=30)).at == moment(29, 23, 59)


def test_chinese_numbers_up_to_twenty_four() -> None:
    assert parsed("提醒我明天二十三点睡觉").at == moment(29, 23)
    assert parsed("提醒我明天十一点吃药").at == moment(29, 11)
    assert parsed("提醒我明天十二点吃饭").at == moment(29, 12)


def test_wan_shang_twelve_is_midnight_after_tonight() -> None:
    assert parsed("提醒我晚上十二点睡觉").at == moment(29, 0)


# --------------------------------------------------------- action and room
def test_action_and_room_after_the_time() -> None:
    item = parsed("提醒我明天早上七点在客厅吃药")
    assert item.text == "吃药"
    assert item.room == "客厅"
    assert item.at == moment(29, 7)


def test_time_first_sentence_with_the_trigger_in_the_middle() -> None:
    item = parsed("明天早上七点叫我在书房看书")
    assert item.room == "书房"
    assert item.text == "看书"
    assert item.at == moment(29, 7)


def test_a_room_name_must_match_a_configured_room_exactly() -> None:
    item = parsed("提醒我明天七点在卧室吃药")
    assert item.room is None
    assert "在卧室" in item.text  # not a configured room, so it stays part of the action
    # The room may carry the usual 「里」 suffix and is still exact.
    assert parsed("提醒我明天七点在书房里看书").room == "书房"


def test_no_room_means_none() -> None:
    assert parsed("提醒我明天早上七点吃药").room is None


def test_unclaimed_relative_times_stay_on_the_llm_tool_path() -> None:
    """Hour/day/week and interval shapes stay with the model (`parse_at` has +10m).

    B (0.8.5): the standard 「N分钟(之)后」 shape is now claimed (see the table below);
    these shapes are the ones the narrow grammar still must not answer "什么时候?" to.
    """
    assert parsed("提醒我半小时后关火") is None
    assert parsed("提醒我1个半小时以后关火") is None
    assert parsed("提醒我过20分钟关火") is None
    assert parsed("提醒我3小时后关火") is None
    assert parsed("提醒我3天后关火") is None
    # 「七点以后」 is an interval, not a moment.
    assert parsed("提醒我明天七点以后关火") is None


@pytest.mark.parametrize(
    ("sentence", "minutes", "text", "room"),
    [
        # B (0.8.5): 「一分钟后提醒我喝水。」 used to reach the model even though the
        # sentence already said when. The grammar now claims the standard shape and
        # computes it with the tool's own `parse_at("+Nm")`.
        ("一分钟后提醒我喝水。", 1, "喝水", None),
        ("一分钟后提醒我喝水", 1, "喝水", None),
        ("提醒我一分钟后喝水", 1, "喝水", None),
        ("提醒我十分钟后关火", 10, "关火", None),
        ("提醒我十分钟之后喝水", 10, "喝水", None),
        ("提醒我10分钟以后喝水", 10, "喝水", None),
        ("十分钟后提醒我关火。", 10, "关火", None),
        ("提醒我三十分钟后在客厅喝水", 30, "喝水", "客厅"),
        ("三十分钟后提醒我在书房关火", 30, "关火", "书房"),
        ("提醒我九十九分钟后喝水", 99, "喝水", None),
    ],
)
def test_voice_minutes_are_claimed_and_mean_minutes(
    sentence: str, minutes: int, text: str, room: str | None,
) -> None:
    item = parsed(sentence)
    assert item is not None, sentence
    assert item.at - REF == dt.timedelta(minutes=minutes), sentence
    # The old bug class: the unit must never be re-read as the other unit.
    assert item.at - REF != dt.timedelta(seconds=minutes), sentence
    assert item.text == text
    assert item.room == room


def test_voice_minutes_use_the_same_parser_as_the_tool(monkeypatch) -> None:
    """B: both entry points ask `parse_at`, not two arithmetic copies."""
    seen: list[str] = []
    real = reminders_module.parse_at

    def spy(spec: str, now: dt.datetime | None = None) -> dt.datetime:
        seen.append(spec)
        return real(spec, now=now)

    monkeypatch.setattr(reminders_module, "parse_at", spy)
    item = parsed("一分钟后提醒我喝水。")
    assert item is not None and item.at == REF + dt.timedelta(minutes=1)
    assert seen == ["+1m"]


def test_the_minute_unit_does_not_match_a_doubled_zhong() -> None:
    """「十分钟钟后」 is a typo, not 「十分钟后」: never compute it as ten minutes.

    It is still a time cue, so the whole sentence is handed to the model (None)
    instead of the fixed grammar answering "什么时候?".
    """
    assert reminders_module._MINUTES_REL.match("十分钟钟后喝水") is None
    assert parsed("提醒我十分钟钟后喝水") is None


def test_a_second_sentence_after_the_action_goes_to_the_model() -> None:
    """B boundary: the action is one sentence, so a name claim is not reminder text.

    The task-32 real-machine sentence carried a name claim. Claiming it here would
    store 「喝水。我是小志」 and speak it at due time; the model owns that utterance
    (it keeps the room from `_tool_context`, see test_app_fleet).
    """
    assert parsed("一分钟后提醒我喝水。我是小志。") is None
    assert parsed("提醒我十秒后喝水。我是小志。") is None
    assert parsed("七点提醒我吃药。别忘了带伞") is None
    assert parsed("提醒我七点吃药；顺便关灯") is None
    # A single sentence, however heavily punctuated at its end, is still ours.
    assert parsed("一分钟后提醒我喝水。").text == "喝水"
    assert parsed("一分钟后提醒我喝水！！").text == "喝水"


@pytest.mark.parametrize(
    ("sentence", "seconds", "text", "room"),
    [
        # task-29: 「10秒钟之后」 used to fall through to the model, which answered
        # with +10m. Seconds now belong to the fixed grammar and never touch an LLM.
        ("提醒我10秒钟之后喝水", 10, "喝水", None),
        ("提醒我十秒之后喝水", 10, "喝水", None),
        ("提醒我十秒钟后喝水", 10, "喝水", None),
        ("提醒我10秒以后喝水", 10, "喝水", None),
        ("10秒钟之后提醒我喝水", 10, "喝水", None),
        ("十秒之后提醒我喝水", 10, "喝水", None),
        ("提醒我30秒后喝水", 30, "喝水", None),
        ("提醒我九十秒后喝水", 90, "喝水", None),
        ("提醒我20秒之后在客厅喝水", 20, "喝水", "客厅"),
        ("20秒后提醒我在客厅喝水", 20, "喝水", "客厅"),
    ],
)
def test_voice_seconds_are_claimed_and_mean_seconds(
    sentence: str, seconds: int, text: str, room: str | None,
) -> None:
    item = parsed(sentence)
    assert item is not None, sentence
    assert item.at - REF == dt.timedelta(seconds=seconds), sentence
    # The bug: ten seconds was stored as ten minutes while the reply said ten seconds.
    assert item.at - REF != dt.timedelta(minutes=seconds), sentence
    assert item.text == text
    assert item.room == room


def test_voice_seconds_use_the_same_parser_as_the_tool(monkeypatch) -> None:
    """task-29: both entry points must ask `parse_at`, not two arithmetic copies."""
    seen: list[str] = []
    real = reminders_module.parse_at

    def spy(spec: str, now: dt.datetime | None = None) -> dt.datetime:
        seen.append(spec)
        return real(spec, now=now)

    monkeypatch.setattr(reminders_module, "parse_at", spy)
    item = parsed("提醒我十秒之后喝水")
    assert item is not None and item.at == REF + dt.timedelta(seconds=10)
    assert seen == ["+10s"]
    assert parsed("30秒后提醒我喝水").at == real("+30s", now=REF)


def test_voice_seconds_do_not_change_the_minutes_path() -> None:
    """A minute-scale relative time is claimed by the grammar, not read as seconds."""
    item = parsed("提醒我2分钟后喝水")
    assert item is not None
    assert item.at - REF == dt.timedelta(minutes=2)
    assert item.at - REF != dt.timedelta(seconds=2)


def test_voice_seconds_at_2359_cross_midnight() -> None:
    late = dt.datetime(2026, 10, 28, 23, 59, 45, tzinfo=ZONE)
    item = parsed("30秒之后提醒我喝水", now=late)
    assert item is not None
    assert item.at == dt.datetime(2026, 10, 29, 0, 0, 15, tzinfo=ZONE)


def test_voice_seconds_without_an_action_asks_for_the_text() -> None:
    with pytest.raises(VoiceParseError) as raised:
        parsed("提醒我十秒之后")
    assert raised.value.reason == "text"


def test_time_shaped_sentences_are_left_to_the_model() -> None:
    """C1 review: a sentence that already said when must not be asked "什么时候?".

    The fixed grammar only claims the exact shapes it can compute. Anything else that
    still names a time belongs to the LLM tool, which has ``+10m`` / ISO shapes; a
    wrong clarify here reads as "I did not listen".
    """
    for sentence in (
        "提醒我明早八点吃药",
        "提醒我今早八点吃药",
        "提醒我今晚八点吃药",
        "提醒我明晚八点吃药",
        "提醒我3天后吃药",
        "提醒我三天后吃药",
        "提醒我每周一吃药",
        "提醒我星期三吃药",
        "提醒我礼拜一吃药",
        "提醒我七点零一刻吃药",  # an unhandled quarter shape, never 07:01
        "提醒我七点差一刻吃药",  # 6:45, not 7:00 with a 「差一刻」 action
        "提醒我七点过一刻吃药",  # 7:15, not 7:00 with a 「过一刻」 action
    ):
        assert parsed(sentence) is None, sentence


def test_a_weak_trigger_without_a_time_is_not_a_reminder() -> None:
    """C1 review: 「叫我」 is also a rename, so it needs a parseable time to qualify."""
    assert parsed("叫我小王") is None
    assert parsed("叫我怎么相信你") is None
    assert parsed("叫我吃饭") is None
    # With a real time it still is a reminder; a missing action asks what to say.
    assert parsed("叫我明天七点吃药").text == "吃药"
    with pytest.raises(VoiceParseError) as raised:
        parsed("叫我明天七点")
    assert raised.value.reason == "text"


def test_optional_trigger_forms_and_punctuation() -> None:
    for sentence in (
        "提醒明天早上七点吃药",
        "提醒我，明天早上七点，吃药。",
        "提醒我明天早上七点吃药。",
    ):
        assert parsed(sentence).text == "吃药"


def test_missing_action_or_time_is_a_clear_error() -> None:
    with pytest.raises(VoiceParseError) as raised:
        parsed("提醒我明天早上七点")
    assert raised.value.reason == "text"
    with pytest.raises(VoiceParseError) as raised:
        parsed("提醒我吃药")
    assert raised.value.reason == "time"


def test_a_sentence_without_a_trigger_is_not_ours() -> None:
    assert parsed("今天天气怎么样") is None
    assert parsed("明天早上七点开会") is None  # no trigger: leave it to the model
    assert parsed("") is None


# ------------------------------------------------------------------ persistence
def test_room_survives_a_store_restart(tmp_path) -> None:
    first = ReminderStore(tmp_path / "data")
    item = first.add("吃药", moment(29, 7), now=REF, room="客厅")
    raw = json.loads(first.path.read_text(encoding="utf-8"))
    assert raw["items"][0]["room"] == "客厅"

    restarted = ReminderStore(tmp_path / "data")
    assert restarted.pending(now=REF)[0].room == "客厅"
    assert restarted.pending(now=REF)[0].id == item.id


def test_a_reminder_without_a_room_reads_back_as_none(tmp_path) -> None:
    s = ReminderStore(tmp_path / "data")
    s.add("吃药", moment(29, 7), now=REF)
    raw = json.loads(s.path.read_text(encoding="utf-8"))
    assert "room" in raw["items"][0] and raw["items"][0]["room"] is None
    assert ReminderStore(tmp_path / "data").pending(now=REF)[0].room is None


def test_old_json_without_a_room_field_is_still_valid(tmp_path) -> None:
    """Backward compatibility: 0.8.0 files have no `room` key at all."""
    s = ReminderStore(tmp_path / "data")
    s.dir.mkdir(parents=True)
    s.path.write_text(json.dumps({"version": 1, "items": [{
        "id": "abc123", "text": "关火", "at": "2026-10-29T07:00:00+08:00",
        "created_at": "2026-10-28T09:00:00+08:00", "fired_at": None,
    }]}), encoding="utf-8")
    item = s.pending(now=REF)[0]
    assert item.room is None and item.text == "关火"


# ------------------------------------------------------------------ routing
def _cfg(**devices: str) -> Config:
    return Config(devices=[
        DeviceConfig(id=device_id, adapter="mock", host="127.0.0.1", room=room)
        for device_id, room in devices.items()
    ])


def test_unset_default_output_prefers_living_then_the_living_room() -> None:
    """D-013: an empty default_output still means the living-room speaker.

    The implicit default is a device id `living` first, then any enabled device whose
    room is 客厅; only a house without one falls back to recency (and warns).
    """
    assert implicit_default_output_id(_cfg(living="客厅", study="书房")) == "living"
    assert implicit_default_output_id(_cfg(salon="客厅", study="书房")) == "salon"
    assert implicit_default_output_id(_cfg(study="书房", bedroom="卧室")) == ""
    disabled = _cfg(living="客厅", study="书房")
    disabled.devices[0].enabled = False
    assert implicit_default_output_id(disabled) == ""


def test_device_for_room_is_an_exact_match_and_prefers_an_enabled_device() -> None:
    cfg = Config(devices=[
        DeviceConfig(id="living", adapter="mock", host="127.0.0.1", room="客厅"),
        DeviceConfig(id="study", adapter="mock", host="127.0.0.1", room="书房"),
    ])
    assert app_module._device_for_room(cfg, "书房") == "study"
    assert app_module._device_for_room(cfg, "客厅") == "living"
    assert app_module._device_for_room(cfg, "卧室") == ""
    assert app_module._device_for_room(cfg, "") == ""


def test_device_room_is_the_exact_inverse_of_device_for_room() -> None:
    """task-31: a device id resolves to its configured room, never a guess."""
    cfg = _cfg(living="客厅", bedroom="次卧", bare="")
    assert app_module._device_room(cfg, "bedroom") == "次卧"
    assert app_module._device_room(cfg, "living") == "客厅"
    assert app_module._device_room(cfg, "bare") == ""
    assert app_module._device_room(cfg, "missing") == ""
    assert app_module._device_room(cfg, "") == ""
    assert app_module._device_for_room(cfg, "次卧") == "bedroom"


def test_speaking_device_id_reads_the_client_or_its_ready_message() -> None:
    assert app_module._speaking_device_id(_FakeLink("dot2-b")) == "dot2-b"
    ready = SimpleNamespace(ready=SimpleNamespace(device_id="dot2"))
    assert app_module._speaking_device_id(ready) == "dot2"
    assert app_module._speaking_device_id(SimpleNamespace()) == ""
    assert app_module._speaking_device_id(SimpleNamespace(ready=None)) == ""


def _fake_app(cfg: Config) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(cfg=cfg))


def test_an_online_named_room_wins(monkeypatch) -> None:
    app = _fake_app(_cfg(living="客厅", study="书房"))
    monkeypatch.setattr(app_module, "_reminder_client_for_room",
                        lambda app, room: f"client:{room}" if room == "客厅" else None)
    monkeypatch.setattr(app_module, "_default_output_client", lambda app: "client:living")
    assert app_module._reminder_route(app, "客厅") == ("client:客厅", "")


def test_an_offline_named_room_falls_back_and_names_itself(monkeypatch) -> None:
    app = _fake_app(_cfg(living="客厅", study="书房"))
    monkeypatch.setattr(app_module, "_reminder_client_for_room", lambda app, room: None)
    monkeypatch.setattr(app_module, "_default_output_client", lambda app: "client:living")
    assert app_module._reminder_route(app, "书房") == ("client:living", "书房")


def test_no_room_uses_the_default_output(monkeypatch) -> None:
    app = _fake_app(_cfg(living="客厅", study="书房"))
    monkeypatch.setattr(app_module, "_default_output_client", lambda app: "client:living")
    assert app_module._reminder_route(app, None) == ("client:living", "")


# ------------------------------------------------------------ the due line
# 0.8.5: an online due reminder is no longer a bare 「起床」. The stored text is the
# action, wrapped in the time-is-up sentence; the setter's trigger verb is stripped
# because the wrapper already supplies the announcement.
def _due_item(text: str) -> Reminder:
    return Reminder(id="abc123", text=text, at=moment(29, 7), created_at=REF)


def test_the_online_due_line_is_the_time_is_up_template() -> None:
    assert app_module._reminder_line(_due_item("喝水"), "") == "时间到了，喝水。"


def test_the_online_due_line_keeps_a_long_text_with_commas() -> None:
    text = "下午三点在客厅开周会，记得带上笔记本和充电器"
    assert len(text) > 20
    assert app_module._reminder_line(_due_item(text), "") == f"时间到了，{text}。"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The trigger the user said while setting it is not part of the announcement.
        ("叫你起床", "时间到了，起床。"),
        ("提醒我 吃药", "时间到了，吃药。"),
        ("让我关火", "时间到了，关火。"),
        # One sentence-final mark only, and no second period from the template.
        ("吃药。", "时间到了，吃药。"),
        ("吃药！", "时间到了，吃药。"),
        ("吃药？", "时间到了，吃药。"),
        ("吃药!?", "时间到了，吃药。"),
        # Degenerate text never speaks 「时间到了，。」 and never raises.
        ("", "时间到了。"),
        ("   ", "时间到了。"),
        ("提醒我", "时间到了。"),
    ],
)
def test_the_online_due_line_normalises_the_text(text: str, expected: str) -> None:
    assert app_module._reminder_line(_due_item(text), "") == expected


def test_the_offline_line_is_unchanged_and_still_names_the_room() -> None:
    assert (app_module._reminder_line(_due_item("吃药"), "客厅")
            == "客厅那台没在线，我在这儿提醒你：吃药")


# ------------------------------------------------------------ spoken entry
class _FakeIdle:
    def __init__(self) -> None:
        self.touched = 0

    def touch(self) -> None:
        self.touched += 1


class _FakeLink:
    def __init__(self, device_id: str = "living") -> None:
        self.device_id = device_id

    async def set_led(self, effect: str, repaint: bool = False) -> None:
        return None

    async def speak(self, pcm: bytes, **kwargs: object) -> None:
        return None


class _FakeRunner:
    def __init__(self) -> None:
        self.idle = _FakeIdle()
        self.spoken: list[str] = []

    async def say(self, text: str, speak, trace) -> int:
        self.spoken.append(text)
        return 100


def _voice_app(tmp_path, clock: dt.datetime = REF, **devices: str):
    cfg = _cfg(**(devices or {"living": "客厅", "study": "书房"}))
    cfg.system.data_dir = str(tmp_path / "data")
    scheduler = DueScheduler(ReminderStore(cfg.system.data_dir), clock=lambda: clock)
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=cfg, reminders=scheduler, led_epochs={}, led_closed={},
        led_tasks=set(), turn_device="living",
    ))
    return app, scheduler


def _run_voice(app, runner, sentence: str, link: _FakeLink | None = None) -> bool:
    link = link or _FakeLink()
    traces = TraceLog()
    return asyncio.run(app_module._voice_reminder_command(
        app, sentence, runner, link, TurnTrace(device_id=link.device_id), traces,
    ))


def test_voice_entry_stores_the_room_and_answers_with_the_time(tmp_path) -> None:
    app, scheduler = _voice_app(tmp_path)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "提醒我明天早上七点在客厅吃药") is True
    item = scheduler.store.pending(now=REF)[0]
    assert (item.text, item.room, item.at) == ("吃药", "客厅", moment(29, 7))
    assert runner.spoken and "明天07点" in runner.spoken[0]
    assert "客厅" in runner.spoken[0]
    assert runner.idle.touched == 1


def test_voice_entry_without_a_named_room_uses_the_speaking_devices_room(tmp_path) -> None:
    """task-31: the device that heard the sentence is the source room.

    An unnamed reminder used to store ``room=None`` and fall back to living at due
    time, so a 次卧 reminder spoke in 客厅 and left the 次卧 song playing. The room is
    now the speaking device's configured room; the confirmation stays as short as
    before because the user never named a room.
    """
    app, scheduler = _voice_app(tmp_path, living="客厅", study="书房", bedroom="次卧")
    runner = _FakeRunner()
    assert _run_voice(app, runner, "提醒我明天早上七点吃药",
                      link=_FakeLink("bedroom")) is True
    item = scheduler.store.pending(now=REF)[0]
    assert (item.text, item.room) == ("吃药", "次卧")
    # The source room is stored, not announced as if the user had named it.
    assert runner.spoken[0] == "好，明天07点提醒你吃药。"


def test_a_named_room_still_beats_the_speaking_devices_room(tmp_path) -> None:
    """The sentence's explicit 「在客厅」 wins over the device that heard it."""
    app, scheduler = _voice_app(tmp_path, living="客厅", bedroom="次卧")
    runner = _FakeRunner()
    assert _run_voice(app, runner, "提醒我明天早上七点在客厅吃药",
                      link=_FakeLink("bedroom")) is True
    assert scheduler.store.pending(now=REF)[0].room == "客厅"


def test_an_unknown_or_roomless_device_does_not_invent_a_room(tmp_path) -> None:
    """No configured room for the speaking device keeps the old None behaviour."""
    app, scheduler = _voice_app(tmp_path, living="客厅", spare="")
    runner = _FakeRunner()
    assert _run_voice(app, runner, "提醒我明天早上七点吃药",
                      link=_FakeLink("spare")) is True
    assert scheduler.store.pending(now=REF)[0].room is None
    assert _run_voice(app, runner, "提醒我明天早上八点吃药",
                      link=_FakeLink("not-a-device")) is True
    assert {item.room for item in scheduler.store.pending(now=REF)} == {None}


def test_voice_minute_reminder_keeps_the_speaking_devices_room(tmp_path) -> None:
    """B: the new minute claim goes through the same source-room resolver as seconds."""
    app, scheduler = _voice_app(tmp_path, living="客厅", bedroom="次卧")
    runner = _FakeRunner()
    assert _run_voice(app, runner, "一分钟后提醒我喝水。",
                      link=_FakeLink("bedroom")) is True
    item = scheduler.store.pending(now=REF)[0]
    assert (item.text, item.room, item.at) == (
        "喝水", "次卧", REF + dt.timedelta(minutes=1))
    assert runner.spoken[0] == "好，1 分钟后提醒你喝水。"


def test_voice_and_tool_paths_share_the_room_resolver(tmp_path, monkeypatch) -> None:
    """B: one resolver (`_reminder_room`) for the fixed sentence and `_tool_context`.

    The LLM end-to-end case (a `set_reminder` tool call from 次卧 stores room=次卧) is
    pinned in tests/test_app_fleet.py; this test proves both entry points *call* the
    same function instead of carrying a copy of the device->room lookup.
    """
    app, scheduler = _voice_app(tmp_path, living="客厅", bedroom="次卧")
    cfg = app.state.cfg
    seen: list[str | None] = []
    real = app_module._reminder_room

    def spy(cfg_, client, named=None):
        seen.append(named)
        return real(cfg_, client, named)

    monkeypatch.setattr(app_module, "_reminder_room", spy)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "一分钟后提醒我喝水。",
                      link=_FakeLink("bedroom")) is True
    context = app_module._tool_context(cfg, _FakeLink("bedroom"))
    assert context.room == "次卧"
    # Fixed path: source room + (no) named room; tool path: source room. One function.
    assert seen == [None, None, None]
    assert scheduler.store.pending(now=REF)[0].room == "次卧"


def test_a_failed_parse_speaks_and_creates_nothing(tmp_path) -> None:
    app, scheduler = _voice_app(tmp_path)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "提醒我吃药") is True
    assert scheduler.store.pending(now=REF) == []
    assert runner.spoken and "什么时候" in runner.spoken[0]


def test_a_past_time_speaks_and_creates_nothing(tmp_path) -> None:
    app, scheduler = _voice_app(tmp_path)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "提醒我今天早上七点吃药") is True
    assert scheduler.store.pending(now=REF) == []
    assert runner.spoken and "过了" in runner.spoken[0]


def test_a_non_reminder_sentence_is_left_to_the_model(tmp_path) -> None:
    app, scheduler = _voice_app(tmp_path)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "今天天气怎么样") is False
    assert runner.spoken == []
    assert scheduler.store.pending(now=REF) == []


def test_voice_ten_seconds_stores_ten_seconds_and_speaks_the_stored_at(tmp_path) -> None:
    """task-29 end to end: no model, ten seconds stored, words derived from `item.at`."""
    app, scheduler = _voice_app(tmp_path)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "10秒钟之后提醒我喝水") is True
    item = scheduler.store.pending(now=REF)[0]
    assert item.at - REF == dt.timedelta(seconds=10)
    assert item.text == "喝水"
    assert runner.spoken[0] == f"好，{humanize(item.at, now=REF)}提醒你喝水。"
    assert runner.spoken[0] == "好，10 秒后提醒你喝水。"
    assert "10 分钟" not in runner.spoken[0]


def test_voice_ninety_seconds_speaks_one_minute_thirty_seconds(tmp_path) -> None:
    """The wording must match the stored moment to the second, not round to 「2 分钟后」."""
    app, scheduler = _voice_app(tmp_path)
    runner = _FakeRunner()
    assert _run_voice(app, runner, "九十秒后提醒我关火") is True
    item = scheduler.store.pending(now=REF)[0]
    assert item.at - REF == dt.timedelta(seconds=90)
    assert runner.spoken[0] == f"好，{humanize(item.at, now=REF)}提醒你关火。"
    assert runner.spoken[0] == "好，1 分 30 秒后提醒你关火。"
