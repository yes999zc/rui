from __future__ import annotations

from rui.session.router import WakeArbiter, WakeEvent, decide


def test_earliest_wins() -> None:
    events = [WakeEvent("b", 120.0), WakeEvent("a", 100.0)]
    assert decide(events) == "a"


def test_ties_break_by_device_id() -> None:
    assert decide([WakeEvent("b", 100.0), WakeEvent("a", 100.0)]) == "a"


def test_late_arrival_outside_window_is_ignored() -> None:
    events = [WakeEvent("a", 0.0), WakeEvent("b", 500.0)]
    assert decide(events, window_ms=200) == "a"


def test_arbiter_resets_between_turns() -> None:
    arb = WakeArbiter(window_ms=200)
    assert arb.offer(WakeEvent("a", 0.0))
    assert not arb.offer(WakeEvent("b", 400.0))
    assert arb.resolve() == "a"
    assert arb.resolve() is None
