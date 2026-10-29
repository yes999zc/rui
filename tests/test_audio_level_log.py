"""task-5/9: the [level] line must describe a person, not the room.

`_build_metrics` reports `voice_peak_db=None` when a 2 s window carried no audible
samples. With the old `level_peak = 0.0` initial value, an idle window kept that 0.0
(0 dBFS = full scale), and `0.0 > -55` logged a clipping-looking line every 30 s at
standby. task-5 fixed the value; task-9 fixed the trigger: production ambient peaks
(-35..-49 dBFS) still produced 3-4 lines a minute per device. A window is now reported
only when someone interacted in it (`wake_count`/`utterances`/`device_wakes` moved, or
the VAD gate saw speech), so pure ambient stays silent while an interaction still
produces exactly one aggregate line per LEVEL_LOG_EVERY ticks.
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest

from rui.audio import process as process_module
from rui.audio.process import (
    LEVEL_FLOOR_DBFS,
    LEVEL_LOG_EVERY,
    LEVEL_LOG_MIN_DBFS,
    METRICS_EVERY_S,
    AudioPlane,
)
from rui.audio.protocol import Metrics

# `Metrics` has a handful of required counters the loop never reads; keep the fake
# focused on the field under test.
_METRICS_REQUIRED: dict[str, float | int] = {
    "uptime_s": 0.0,
    "frames_in": 0,
    "frames_out": 0,
    "frames_dropped": 0,
    "no_consumer": 0,
    "jitter_p50_ms": 0.0,
    "jitter_p99_ms": 0.0,
    "cpu_s": 0.0,
    "audio_s": 0.0,
    "rtf": 0.0,
}


class _FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        return None


class _FakeGate:
    def __init__(self) -> None:
        self.speaking = False


class _FakePlane:
    """Stand-in `self`: `_metrics_loop` touches only these members."""

    def __init__(
        self,
        peaks: list[float | None],
        *,
        interact_until: int = 0,
        speaking_until: int = 0,
    ) -> None:
        assert peaks
        self._stop = asyncio.Event()
        self._peaks = peaks
        self._index = 0
        self.volume_reads = 0
        # task-9 interaction evidence read by the loop at every window boundary.
        self.wake_count = 0
        self.utterances = 0
        self.device_wakes = 0
        self.gate = _FakeGate()
        self._interact_until = interact_until
        self._speaking_until = speaking_until

    async def _read_device_volume(self) -> None:
        self.volume_reads += 1

    def metrics(self) -> Metrics:
        peak = self._peaks[self._index]
        self._index += 1
        if self._index <= self._interact_until:
            # One wake per simulated tick is enough to mark the window as interacted.
            self.wake_count += 1
        self.gate.speaking = self._index <= self._speaking_until
        if self._index >= len(self._peaks):
            self._stop.set()
        return Metrics(voice_peak_db=peak, **_METRICS_REQUIRED)


def _drive(
    peaks: list[float | None],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    interact_until: int = 0,
    speaking_until: int = 0,
) -> tuple[_FakePlane, _FakeWriter]:
    """Run the real metrics loop with an instant sleep and N simulated ticks."""

    async def instant_sleep(_seconds: float) -> None:
        return None

    # The loop is the only user of `asyncio` in this module; replacing the module
    # reference keeps the shim local to this call (monkeypatch reverts it).
    monkeypatch.setattr(process_module, "asyncio", types.SimpleNamespace(sleep=instant_sleep))
    plane = _FakePlane(peaks, interact_until=interact_until,
                       speaking_until=speaking_until)
    writer = _FakeWriter()
    connected = asyncio.Event()
    connected.set()
    caplog.set_level(logging.INFO, logger="rui.audio.process")
    asyncio.run(AudioPlane._metrics_loop(plane, writer, connected))  # type: ignore[arg-type]
    return plane, writer


def _peak_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "rui.audio.process" and "voice peak" in record.getMessage()
    ]


def test_silent_standby_logs_no_voice_peak(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # 60 s of idle frames: two full 30 s windows, every tick's voice_peak_db is None.
    _drive([None] * (2 * LEVEL_LOG_EVERY), monkeypatch, caplog)
    assert _peak_lines(caplog) == []


def test_a_window_with_voice_logs_the_real_peak_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    peaks: list[float | None] = [None] * 5 + [-20.5] * 3 + [None] * 7
    _drive(peaks, monkeypatch, caplog, interact_until=LEVEL_LOG_EVERY)

    lines = _peak_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("[level] voice peak ")
    assert "-20.5 dBFS" in line
    assert "0.0 dBFS" not in line
    assert line.endswith(f"(active {3 * METRICS_EVERY_S:.0f} s)")


def test_the_peak_does_not_leak_into_the_next_window(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # First window speaks, second is silent: only the first may be reported.
    _drive([-18.0] * LEVEL_LOG_EVERY + [None] * LEVEL_LOG_EVERY, monkeypatch, caplog,
           interact_until=LEVEL_LOG_EVERY)
    assert len(_peak_lines(caplog)) == 1


def test_cadence_stays_one_line_per_window_and_every_tick_is_published(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    plane, writer = _drive([-12.0] * (2 * LEVEL_LOG_EVERY), monkeypatch, caplog,
                           interact_until=2 * LEVEL_LOG_EVERY)

    lines = _peak_lines(caplog)
    assert len(lines) == 2
    assert all("(active 30 s)" in line for line in lines)
    # The metrics stream itself is still one snapshot per tick; only the log is
    # aggregated (LEVEL_LOG_EVERY is untouched).
    assert plane._index == 2 * LEVEL_LOG_EVERY
    assert len(writer.chunks) == 2 * LEVEL_LOG_EVERY


def test_energy_below_the_reporting_threshold_is_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # An interaction window whose level never reaches the reporting floor stays quiet.
    _drive([LEVEL_LOG_MIN_DBFS - 1.0] * LEVEL_LOG_EVERY, monkeypatch, caplog,
           interact_until=LEVEL_LOG_EVERY)
    assert _peak_lines(caplog) == []


def test_pure_ambient_logs_no_voice_peak(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """task-9: production ambient (-35..-49 dBFS) is not speech, even if it is loud."""
    _drive([-40.0] * (2 * LEVEL_LOG_EVERY), monkeypatch, caplog)
    assert _peak_lines(caplog) == []


def test_an_interaction_in_ambient_logs_one_line_and_never_zero_dbfs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _drive([-40.0] * LEVEL_LOG_EVERY, monkeypatch, caplog,
           interact_until=LEVEL_LOG_EVERY)
    lines = _peak_lines(caplog)
    assert len(lines) == 1
    assert "voice peak -40.0 dBFS" in lines[0]
    assert "voice peak 0.0 dBFS" not in lines[0]


def test_a_speaking_gate_is_interaction_evidence(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No wake/utterance counter moved, but the VAD gate heard speech in the window."""
    _drive([-30.0] * LEVEL_LOG_EVERY, monkeypatch, caplog,
           speaking_until=LEVEL_LOG_EVERY)
    assert len(_peak_lines(caplog)) == 1


def test_the_aggregate_floor_is_not_full_scale() -> None:
    # The initial value must never be 0.0 dBFS; -120 dBFS is the 16-bit floor.
    assert LEVEL_FLOOR_DBFS == -120.0
    assert LEVEL_FLOOR_DBFS < LEVEL_LOG_MIN_DBFS
