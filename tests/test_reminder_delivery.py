"""C2: a due reminder preempts only its target room and failures are visible.

The reminder entry and routing are pinned in ``test_reminder_voice.py``. This file
covers the *delivery* half of backlog §f.2:

* a target room that is playing music (or answering) is silenced first, and the song is
  not resumed after the announcement;
* other rooms are not stopped, not spoken into, and their session is not closed;
* an offline target speaks on ``default_output`` with 「<room>那台没在线」;
* a delivery that produced no audio increments a counter that ``/health`` exposes -
  it is never a silent ``log.warning``;
* the announcement closes the session shop: LED ends on ``off`` and no
  ``reply_finished`` is sent, so the plane arms no follow-up window.

The fake clients are the same shape the fleet tests use: ``send`` records control
messages, ``speak`` records PCM, ``set_led`` records the ring.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from types import SimpleNamespace

from rui.audio.protocol import ResumeMusic, StopAudio
from rui.config.schema import Config
from rui.tools.reminders import ZONE, Reminder
from rui.web.api import app as app_module

REF = dt.datetime(2026, 10, 28, 9, 0, tzinfo=ZONE)


def moment(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, 29, hour, minute, tzinfo=ZONE)


class FakeMetrics:
    def __init__(self) -> None:
        self.output_owner = ""
        self.gate_speaking = False
        self.speaker_connected = True


class FakeCoordinator:
    """The one-turn latch. A reminder that does not own the turn must not touch it."""

    def __init__(self) -> None:
        self.arbiter = SimpleNamespace(state=SimpleNamespace(turn_open=True))
        self.released = 0
        self.touched = 0

    def set_turn_open(self, open_: bool, device_id: str | None = None) -> None:
        self.released += 1

    def touch(self, room: str = "") -> None:
        self.touched += 1


class FakeClient:
    def __init__(self, device_id: str, coordinator: FakeCoordinator | None = None) -> None:
        self.device_id = device_id
        self.coordinator = coordinator
        self.ready = None
        self.metrics = FakeMetrics()
        self.sent: list[object] = []
        self.spoken: list[bytes] = []
        self.led: list[str] = []
        self.replies = 0
        self.released = 0

    async def send(self, msg: object) -> None:
        self.sent.append(msg)

    async def speak(self, pcm: bytes, **kwargs: object) -> None:
        self.spoken.append(pcm)

    async def reply_finished(self, *args: object, **kwargs: object) -> None:
        self.replies += 1

    async def set_led(self, effect: str, repaint: bool = False) -> None:
        self.led.append(effect)

    def release_turn(self) -> None:
        self.released += 1
        if self.coordinator is not None:
            # Production's _PlaneLink.release_turn -> _end_turn clears the house latch.
            self.coordinator.set_turn_open(False)
            self.coordinator.touch(self.device_id)


class FakeMusic:
    def __init__(self, state: str = "idle") -> None:
        self.state = state
        self.stops = 0
        self.resumes = 0

    async def stop(self) -> None:
        self.stops += 1
        self.state = "idle"

    async def resume(self) -> bool:
        self.resumes += 1
        self.state = "playing"
        return True


class FakeIdle:
    def __init__(self, active: bool = False) -> None:
        self.active = active
        self.stopped = 0
        self.touched = 0

    def stop(self) -> None:
        self.stopped += 1
        self.active = False

    def touch(self) -> None:
        self.touched += 1


class FakeRunner:
    def __init__(self, *, audio_bytes: int = 3200, fail: Exception | None = None) -> None:
        self.idle = FakeIdle()
        self.busy = False
        self.audio_bytes = audio_bytes
        self.fail = fail
        self.texts: list[str] = []
        self.aborted = 0

    async def say(self, text: str, speak, _trace) -> int:
        self.texts.append(text)
        if self.fail is not None:
            raise self.fail
        if self.audio_bytes:
            await speak(b"\0" * self.audio_bytes, segment_start=True)
        return self.audio_bytes

    def abort(self) -> None:
        self.aborted += 1


def item(room: str | None = "书房", text: str = "吃药") -> Reminder:
    return Reminder(
        id="abc123", text=text, at=moment(7),
        created_at=REF, room=room,
    )


def fake_app(*, music: FakeMusic | None = None, turn_device: str = "") -> SimpleNamespace:
    state = SimpleNamespace(
        cfg=Config(),
        music=music,
        music_room="",
        output_owner="",
        turn_device=turn_device,
        last_active_room="",
        reminder_stats=None,
        reminder_preempted={},
        wake_coordinator=None,
        led_epochs={},
        led_closed={},
        led_after_tasks={},
        led_tasks=set(),
    )
    return SimpleNamespace(state=state)


def run_delivery(app, reminder: Reminder, runner: FakeRunner, *, settle_s: float = 0.0) -> bool:
    async def scenario() -> bool:
        delivered = await app_module._deliver_reminder(app, reminder, runner)
        if settle_s:
            await asyncio.sleep(settle_s)
        return delivered

    return asyncio.run(scenario())


def test_a_playing_target_room_is_stopped_only_there_and_never_resumed(monkeypatch) -> None:
    """The song belongs to 书房: stop it, announce, leave 客厅's session alone."""
    study, living = FakeClient("study"), FakeClient("living")
    music = FakeMusic("playing")
    runner = FakeRunner()
    app = fake_app(music=music, turn_device="living")
    app.state.music_room = "study"
    app.state.output_owner = "study"
    study.metrics.output_owner = "music"
    runner.idle.active = True          # 客厅 has an open session; do not close it
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (study, ""))

    assert run_delivery(app, item(), runner, settle_s=1.0) is True

    # The song queue was stopped, the target plane was flushed, and no resume followed.
    assert music.stops == 1
    assert any(isinstance(msg, StopAudio) for msg in study.sent)
    assert not any(isinstance(msg, StopAudio) for msg in living.sent)
    assert not any(isinstance(msg, ResumeMusic) for msg in study.sent + living.sent)
    assert music.resumes == 0
    # The announcement went to the target room, and the other room said nothing.
    assert runner.texts == ["吃药"]
    assert study.spoken and not living.spoken
    # Other room's session was untouched; the reminder opened no reply/follow-up.
    assert runner.idle.stopped == 0
    assert study.replies == 0 and living.replies == 0
    # Last ring event is off (the after-audio paint is a task).
    assert study.led[-1] == "off"
    stats = app_module._reminder_health(app)
    assert (stats["fired"], stats["spoken"], stats["failed"], stats["offline"]) == (
        0, 1, 0, 0)


def test_a_paused_target_queue_is_stopped_so_it_cannot_come_back(monkeypatch) -> None:
    """A paused song under the target is a queue the plane could hand back; C2 ends it."""
    study = FakeClient("study")
    music = FakeMusic("paused")
    runner = FakeRunner()
    app = fake_app(music=music)
    app.state.music_room = "study"
    app.state.output_owner = "study"
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (study, ""))

    assert run_delivery(app, item(), runner, settle_s=1.0) is True

    assert music.stops == 1
    assert music.resumes == 0
    assert study.spoken


def test_music_playing_in_another_room_is_not_touched(monkeypatch) -> None:
    """The named room is 书房; 客厅 keeps its song, its owner and its session."""
    study, living = FakeClient("study"), FakeClient("living")
    music = FakeMusic("playing")
    runner = FakeRunner()
    app = fake_app(music=music, turn_device="living")
    app.state.music_room = "living"
    app.state.output_owner = "living"
    living.metrics.output_owner = "music"
    runner.idle.active = True
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (study, ""))

    assert run_delivery(app, item(), runner, settle_s=1.0) is True

    assert music.stops == 0
    assert not any(isinstance(msg, StopAudio) for msg in living.sent)
    assert living.spoken == []
    assert runner.idle.stopped == 0
    assert study.spoken


def test_an_offline_target_falls_back_and_names_the_room(monkeypatch) -> None:
    """Target offline: default_output speaks the text plus 「<room>那台没在线」."""
    living = FakeClient("living")
    runner = FakeRunner()
    app = fake_app(turn_device="")
    monkeypatch.setattr(app_module, "_reminder_client_for_room", lambda _app, _room: None)
    monkeypatch.setattr(app_module, "_default_output_client", lambda _app: living)

    assert run_delivery(app, item(), runner, settle_s=1.0) is True

    assert living.spoken
    assert "书房那台没在线" in runner.texts[0] and "吃药" in runner.texts[0]
    stats = app_module._reminder_health(app)
    assert stats["spoken"] == 1
    assert stats["offline"] == 1
    # An offline target is a visible failure, even though the fallback spoke.
    assert stats["failed"] == 1
    assert "书房" in stats["last_error"]
    assert living.replies == 0
    assert living.led[-1] == "off"


def test_an_offline_target_does_not_release_a_busy_fallback_turn(monkeypatch) -> None:
    """The fallback room is mid-answer: speak there, but leave its turn latch alone.

    Regression (C2): `_finish_reminder_session` released the fallback client even when
    the reminder owned no turn. `release_turn()` clears the house's ``turn_open`` latch
    and claim, which would let a concurrent wake start while the fallback answer is
    still draining.
    """
    coordinator = FakeCoordinator()
    living = FakeClient("living", coordinator)
    runner = FakeRunner()
    runner.busy = True
    runner.idle.active = True
    app = fake_app(turn_device="living")
    app.state.wake_coordinator = coordinator
    living.metrics.output_owner = "reply"
    monkeypatch.setattr(app_module, "_reminder_client_for_room", lambda _app, _room: None)
    monkeypatch.setattr(app_module, "_default_output_client", lambda _app: living)

    assert run_delivery(app, item(), runner, settle_s=1.0) is True

    # The announcement used the fallback speaker...
    assert living.spoken
    assert "书房那台没在线" in runner.texts[0]
    # ...without closing its session, aborting its answer or releasing its turn.
    assert runner.idle.stopped == 0
    assert runner.aborted == 0
    assert living.released == 0
    assert coordinator.released == 0 and coordinator.touched == 0
    assert living.replies == 0
    # The requested room is still reported as an offline delivery.
    stats = app_module._reminder_health(app)
    assert (stats["spoken"], stats["failed"], stats["offline"]) == (1, 1, 1)


def test_all_speakers_offline_is_counted_not_only_logged(monkeypatch, caplog) -> None:
    """A named room offline with no default output was an offline delivery."""
    runner = FakeRunner()
    app = fake_app()
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (None, "书房"))

    with caplog.at_level(logging.ERROR):
        assert run_delivery(app, item(), runner) is False

    stats = app_module._reminder_health(app)
    assert stats["spoken"] == 0
    assert stats["failed"] == 1
    # The requested room really was offline, so the offline counter must see it too.
    assert stats["offline"] == 1
    assert stats["last_room"] == "书房"
    assert "no live" in stats["last_error"].lower()
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors and "reminder not spoken" in errors[-1].getMessage()
    assert runner.texts == []


def test_an_unrouted_reminder_with_no_speaker_counts_only_failed(monkeypatch, caplog) -> None:
    """No room was named, so there is nothing to call offline: only `failed` grows."""
    runner = FakeRunner()
    app = fake_app()
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (None, ""))

    with caplog.at_level(logging.ERROR):
        assert run_delivery(app, item(room=None), runner) is False

    stats = app_module._reminder_health(app)
    assert stats["failed"] == 1
    assert stats["offline"] == 0
    assert stats["last_room"] == ""


def test_synthesis_failure_is_counted_and_logged(monkeypatch, caplog) -> None:
    """A TTS exception must not disappear into a warning-only log line."""
    study = FakeClient("study")
    runner = FakeRunner(fail=RuntimeError("tts down"))
    app = fake_app()
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (study, ""))

    with caplog.at_level(logging.ERROR):
        assert run_delivery(app, item(), runner) is False

    stats = app_module._reminder_health(app)
    assert stats["failed"] == 1 and stats["spoken"] == 0
    assert "tts down" in stats["last_error"]
    assert any(record.levelno >= logging.ERROR and "reminder not spoken" in
               record.getMessage() for record in caplog.records)
    assert study.replies == 0


def test_an_empty_announcement_is_a_visible_failure(monkeypatch, caplog) -> None:
    """`say()` returning 0 bytes means the room heard nothing; that is not success."""
    study = FakeClient("study")
    runner = FakeRunner(audio_bytes=0)
    app = fake_app()
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (study, ""))

    with caplog.at_level(logging.ERROR):
        assert run_delivery(app, item(), runner) is False

    stats = app_module._reminder_health(app)
    assert stats["failed"] == 1 and stats["spoken"] == 0
    assert "nothing" in stats["last_error"].lower()
    assert study.replies == 0


def test_a_responding_target_is_interrupted_then_closed_without_a_followup(
    monkeypatch,
) -> None:
    """Target mid-answer: flush it, announce, close the session, arm no follow-up."""
    study = FakeClient("study")
    music = FakeMusic("idle")
    runner = FakeRunner()
    runner.busy = True
    app = fake_app(music=music, turn_device="study")
    runner.idle.active = True
    study.metrics.output_owner = "reply"
    monkeypatch.setattr(app_module, "_reminder_route", lambda _app, _room: (study, ""))

    assert run_delivery(app, item(), runner, settle_s=1.0) is True

    assert any(isinstance(msg, StopAudio) for msg in study.sent)
    assert runner.aborted == 1
    assert runner.idle.stopped == 1
    # No reply_finished: the plane must not open a wake-free follow-up window.
    assert study.replies == 0
    assert study.led[-1] == "off"
    stats = app_module._reminder_health(app)
    assert stats["spoken"] == 1 and stats["failed"] == 0


def test_health_counters_start_at_zero_and_keep_their_shape() -> None:
    app = fake_app()
    stats = app_module._reminder_health(app)
    assert set(stats) == {
        "fired", "spoken", "failed", "offline",
        "last_error", "last_failure_at", "last_room",
    }
    assert stats["fired"] == stats["spoken"] == stats["failed"] == stats["offline"] == 0
