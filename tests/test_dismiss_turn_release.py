"""A dismissal must release the house's one-turn latch.

Measured on the Dot 2 (2026-10-26): 「退一下吧。」 closed the session at 12:26:15 and
every wake for the next five minutes was rejected `turn_open` until the container was
restarted. The dismissal deliberately skips `reply_finished` (no follow-up window after
the goodbye), and that call was also the turn's only latch release.

These tests drive the real `_stop_command` and `_PlaneLink` with a real `WakeCoordinator`,
then ask the arbiter for the next wake: it must be admitted, not rejected turn_open.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import rui.web.api.app as app_module
from rui.audio.arbiter import ArbiterDevice, WakeArbiter
from rui.audio.coordinator import WakeCoordinator
from rui.config import Config
from rui.config.schema import ArbitrationConfig, MultiDeviceConfig
from rui.trace import TurnTrace

WINDOW_MS = 100


class _Music:
    def __init__(self, state: str) -> None:
        self.state = state
        self.stops = 0

    async def stop(self) -> None:
        self.stops += 1
        self.state = "idle"


class _Handle:
    """Records the close finish: `followup=True` would open a wake-free window."""

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.followups: list[bool] = []
        self.metrics = SimpleNamespace(output_owner="")

    async def send(self, msg: object) -> None:
        self.sent.append(msg)

    async def speak(self, pcm: bytes, **kwargs: object) -> None:  # noqa: ARG002
        return None

    async def reply_finished(self, **kwargs: object) -> None:
        self.followups.append(bool(kwargs.get("followup", True)))


class _Runner:
    def __init__(self) -> None:
        self.dismissed = 0
        self.idle = SimpleNamespace(touch=lambda: None)

    async def dismiss(self, speak, trace) -> int:  # noqa: ANN001, ARG002
        self.dismissed += 1
        return 0

    async def say(self, line: str, speak, trace) -> int:  # noqa: ANN001, ARG002
        return 0


class _Traces:
    def __init__(self) -> None:
        self.items: list[object] = []

    def add(self, trace: object) -> None:
        self.items.append(trace)


def _coordinator() -> tuple[WakeCoordinator, dict]:
    cfg = MultiDeviceConfig(
        arbitration=ArbitrationConfig(candidate_merge_window_ms=WINDOW_MS))
    arbiter = WakeArbiter(cfg, [ArbiterDevice(device_id="living", room="客厅", index=0)])
    sent: dict[str, list] = {"living": []}

    async def send(message: object) -> None:
        sent["living"].append(message)

    return WakeCoordinator(arbiter, {"living": send}, rooms={"living": "客厅"}), sent


def _candidate(wake_id: str) -> SimpleNamespace:
    return SimpleNamespace(wake_id=wake_id, source="rui", ts_ms=1.0, keyword="你好小瑞",
                           score=None, threshold=None, barge=False, playing=False)


def _env(monkeypatch, *, music_state: str, output_owner: str):
    monkeypatch.setattr(app_module, "_light_after_audio", lambda *a, **k: None)
    coordinator, sent = _coordinator()
    handle = _Handle()
    app = SimpleNamespace(state=SimpleNamespace(
        cfg=Config(), music=_Music(music_state), last_turn=None,
        turn_device="living", output_owner=output_owner,
        wake_coordinator=coordinator,
    ))
    link = app_module._PlaneLink(app, "living", handle, coordinator)
    return coordinator, sent, handle, app, link


def _run_wake(coordinator: WakeCoordinator, sent: dict, wake_id: str) -> list[str]:
    """One candidate through the real coordinator; returns the verdict class names."""
    async def scenario() -> None:
        await coordinator.on_candidate("living", _candidate(wake_id))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)

    asyncio.run(scenario())
    return [m.__class__.__name__ for m in sent["living"]]


def test_a_dismissal_releases_the_turn_latch(monkeypatch) -> None:
    """Production sequence (pre9-a1p, 2026-10-26), 13 s apart and still rejected:

        04:38:48  wake candidate dot2-… admitted after 409 ms
        04:38:52  stop intent: '退下吧。' -> session closed
        04:39:05  wake candidate dot2-… rejected: turn_open

    The latch never expires on its own: only a restart cleared it (the earlier report
    had eleven rejections over five minutes). The wake word works again as soon as the
    dismissal releases the turn.
    """
    coordinator, sent, handle, app, link = _env(
        monkeypatch, music_state="idle", output_owner="living")

    # 04:38:48: the first wake is admitted and opens the turn (app.py's on_wake).
    assert "WakeAdmit" in _run_wake(coordinator, sent, "dot2-t38348")
    coordinator.set_turn_open(True)  # app.py:2694, exactly what on_wake does
    coordinator.touch("living")

    # 04:38:52: 「退下吧。」 closes the session before its goodbye.
    handled = asyncio.run(app_module._stop_command(
        app, "退下吧", _Runner(), link, TurnTrace(device_id="living"), _Traces()))
    assert handled is True
    # The close finish still reaches the plane (drain + mute), but it says
    # followup=False: no wake-free window after a closed session.
    assert handle.followups == [False], handle.followups

    # 04:39:05: the next wake must be admitted, not rejected turn_open.
    assert coordinator.arbiter.state.turn_open is False, (
        "the dismissal left turn_open set: every later wake is rejected until a restart")
    # The 13 s gap also outlived the same-word duplicate window; only the turn latch
    # could still reject the word at this point.
    coordinator.arbiter.state.admitted_words.clear()
    checkpoint = len(sent["living"])
    _run_wake(coordinator, sent, "dot2-t39005")
    verdicts = sent["living"][checkpoint:]
    assert any(m.__class__.__name__ == "WakeAdmit" for m in verdicts), (
        "the wake 13 s after the dismissal was rejected")
    assert "turn_open" not in [getattr(m, "reason", "") for m in verdicts]


def test_a_dismissal_mid_song_in_another_room_also_releases_the_latch(monkeypatch) -> None:
    """A stop word in a room that does not own the song closes only that session."""
    from rui.audio.protocol import StopAudio

    coordinator, _sent, handle, app, link = _env(
        monkeypatch, music_state="playing", output_owner="study")
    coordinator.set_turn_open(True)

    handled = asyncio.run(app_module._stop_command(
        app, "停止播放", _Runner(), link, TurnTrace(device_id="living"), _Traces()))

    assert handled is True
    assert app.state.last_turn["session_action"] == "stop_elsewhere"
    assert coordinator.arbiter.state.turn_open is False, "the closed session left the latch open"
    assert any(isinstance(msg, StopAudio) for msg in handle.sent)
    assert handle.followups == [False], "the refusal opened a follow-up window"


class _ReplyHandle:
    def __init__(self) -> None:
        self.replied = 0
        self.followups: list[bool] = []

    async def reply_finished(self, **kwargs: object) -> None:
        self.replied += 1
        self.followups.append(bool(kwargs.get("followup", True)))


def test_a_normal_reply_still_releases_the_latch_through_reply_finished(monkeypatch) -> None:
    """The one-turn rule is unchanged for an ordinary turn, window included."""
    coordinator, _sent, _handle, app, _link = _env(
        monkeypatch, music_state="idle", output_owner="living")
    coordinator.set_turn_open(True)

    handle = _ReplyHandle()
    link = app_module._PlaneLink(app, "living", handle, coordinator)
    asyncio.run(link.reply_finished())

    assert handle.replied == 1
    assert handle.followups == [True], "an ordinary answer must keep its follow-up window"
    assert coordinator.arbiter.state.turn_open is False


def test_the_idle_farewell_finishes_without_a_followup_window(monkeypatch) -> None:
    """The idle goodbye is a close path too: drain/mute, then no listening window.

    `idle_watch` used to call `reply_finished()` with no arguments, which armed the
    wake-free gate for `followup_max_wait_ms`. This drives the helper that call site
    now uses; the plane-side "no arm" behaviour is pinned in test_followup_window.
    """
    coordinator, _sent, handle, app, link = _env(
        monkeypatch, music_state="idle", output_owner="living")
    coordinator.set_turn_open(True)

    asyncio.run(app_module._finish_close_path(link))

    assert handle.followups == [False], "the farewell armed a wake-free window"
    assert coordinator.arbiter.state.turn_open is False
