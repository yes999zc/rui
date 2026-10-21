from __future__ import annotations

from house_ear.trace import TraceLog, TurnTrace


def test_marks_are_relative_to_turn_start() -> None:
    trace = TurnTrace(device_id="living")
    trace.mark("wake", at_ms=trace.t0_ms)
    trace.mark("end_of_speech", at_ms=trace.t0_ms + 900)
    trace.mark("asr_done", at_ms=trace.t0_ms + 1200)
    trace.mark("play_start", at_ms=trace.t0_ms + 2400)

    assert trace.at("wake") == 0.0
    assert trace.at("end_of_speech") == 900.0
    assert trace.delta("wake", "play_start") == 2400.0
    assert trace.first_response_ms == 2400.0


def test_barge_in_silence_is_measured_from_the_interrupt() -> None:
    trace = TurnTrace(device_id="living")
    trace.mark("play_start", at_ms=trace.t0_ms)
    trace.mark("barge_in", at_ms=trace.t0_ms + 5000)
    trace.mark("silence", at_ms=trace.t0_ms + 5240)
    assert trace.silence_after_ms == 240.0


def test_missing_marks_yield_none_not_zero() -> None:
    trace = TurnTrace(device_id="living")
    trace.mark("wake")
    assert trace.first_response_ms is None
    assert trace.silence_after_ms is None
    assert trace.to_dict()["stages_ms"] == {"wake": 0.0}


def test_trace_log_keeps_a_bounded_recent_window() -> None:
    log = TraceLog(capacity=3)
    for i in range(5):
        trace = TurnTrace(device_id=f"dev{i}")
        trace.mark("wake", at_ms=trace.t0_ms)
        log.add(trace)

    recent = log.recent(10)
    assert len(recent) == 3
    assert [t["device_id"] for t in recent] == ["dev2", "dev3", "dev4"]
    assert log.recent(0) == []
