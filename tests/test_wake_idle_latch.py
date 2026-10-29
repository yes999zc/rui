"""A silent wake's WakeIdle must release the house's one-turn latch at once.

Production 2026-10-29: three silent wakes (「你好小宝」, no reply) each left the latch
open for `TURN_STALE_S` (20 s), so every wake in the same room was rejected
`rejected: turn_open` until the watchdog fired. The audio side already emits WakeIdle
on the silent timeout (`vad.UtteranceGate` + `AudioPlane._feed_gate`); these tests pin
the control-plane half: `_PlaneLink.on_wake_idle` closes the turn on the spot and the
next wake is admitted, while the idle countdown (and its farewell) stays untouched.
"""
from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
from test_app_fleet import FakeFleet, _app, _candidate, _pronounce

from rui.audio.protocol import Wake, WakeIdle


def test_wake_idle_releases_the_latch_and_the_next_wake_is_admitted(
        tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        coordinator = app.state.wake_coordinator

        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))
        assert coordinator.arbiter.state.turn_open is True
        assert coordinator.turn_open_for_s() is not None

        # The gate closed without speech: the turn is over immediately, not after 20 s.
        client.portal.call(living.on_wake_idle, WakeIdle(waited_ms=5000))
        assert coordinator.turn_open_for_s() is None, (
            "WakeIdle left the latch open: the next wake would be rejected turn_open")

        client.portal.call(living.on_wake_candidate, _candidate("w-after-idle"))
        time.sleep(0.25)
        assert living.sent[-1].type == "wake_admit", (
            f"the wake after WakeIdle was rejected: {living.sent[-1]}")


def test_wake_idle_keeps_the_idle_countdown_and_says_nothing_early(
        tmp_path, monkeypatch) -> None:
    """No new/early farewell: only the light goes off, the session clock keeps running."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        runner = app.state.runner

        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))
        assert runner.idle.active is True  # started by on_wake

        client.portal.call(living.on_wake_idle, WakeIdle(waited_ms=5000))

        assert runner.idle.active is True, (
            "WakeIdle closed the session early: the idle farewell belongs to idle_watch")
        time.sleep(0.05)  # let the fire-and-forget light paint run
        assert living.led[-1] == "off", living.led
        assert living.spoken == [], "a silent wake spoke a farewell before the idle timeout"


def test_a_wake_idle_during_a_live_turn_is_ignored(tmp_path, monkeypatch) -> None:
    """A stale WakeIdle must not cut a turn that is provably still running.

    The gate's idle report and the utterance travel on different paths: a late
    WakeIdle can land after the next turn already heard speech and opened the latch.
    Releasing it (or painting the ring off / touching the idle clock / speaking) there
    would cut the live turn; its own wrapper releases the latch when it ends.
    """
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        coordinator = app.state.wake_coordinator
        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))
        said = _pronounce(app, monkeypatch)
        touches: list[object] = []
        monkeypatch.setattr(app.state.runner.idle, "touch", touches.append)

        async def slow_transcribe(pcm: bytes, trace: object) -> tuple[str, None]:
            await asyncio.sleep(2)  # the turn is in flight when the WakeIdle lands
            return "现在几点", None

        monkeypatch.setattr(app.state.runner, "transcribe", slow_transcribe)

        async def start() -> asyncio.Task:
            return asyncio.create_task(living.on_utterance(b"\x00\x00"))

        task = client.portal.call(start)
        time.sleep(0.15)  # the handler has opened the latch and registered itself
        assert coordinator.arbiter.state.turn_open is True
        led_before = list(living.led)

        client.portal.call(living.on_wake_idle, WakeIdle(waited_ms=5000, barge=True))
        time.sleep(0.1)

        assert coordinator.arbiter.state.turn_open is True, (
            "a stale WakeIdle released the latch of a live turn")
        assert touches == [], "the stale WakeIdle touched the idle clock"
        assert living.led == led_before, "the ring was painted off under a live turn"
        assert said == [], "a stale barge WakeIdle spoke over the live turn"
        assert living.spoken == []

        # The live turn's own end still releases the latch (the guard is not a leak).
        client.portal.call(task.cancel)
        time.sleep(0.15)
        assert coordinator.arbiter.state.turn_open is False
