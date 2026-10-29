"""task-5: the [level] line must not report an idle window as full scale.

`_build_metrics` reports `voice_peak_db=None` when a 2 s window carried no audible
samples. With the old `level_peak = 0.0` initial value, an idle window kept that 0.0
(0 dBFS = full scale), and `0.0 > -55` logged a clipping-looking line every 30 s at
standby. These cases drive `AudioPlane._metrics_loop` over simulated ticks: silence
must stay silent, speech must produce one line per LEVEL_LOG_EVERY ticks with the
real peak, and the peak must not leak into the next window.
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


class _FakePlane:
    """Stand-in `self`: `_metrics_loop` touches only these members."""

    def __init__(self, peaks: list[float | None]) -> None:
        assert peaks
        self._stop = asyncio.Event()
        self._peaks = peaks
        self._index = 0
        self.volume_reads = 0

    async def _read_device_volume(self) -> None:
        self.volume_reads += 1

    def metrics(self) -> Metrics:
        peak = self._peaks[self._index]
        self._index += 1
        if self._index >= len(self._peaks):
            self._stop.set()
        return Metrics(voice_peak_db=peak, **_METRICS_REQUIRED)


def _drive(
    peaks: list[float | None],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> tuple[_FakePlane, _FakeWriter]:
    """Run the real metrics loop with an instant sleep and N simulated ticks."""

    async def instant_sleep(_seconds: float) -> None:
        return None

    # The loop is the only user of `asyncio` in this module; replacing the module
    # reference keeps the shim local to this call (monkeypatch reverts it).
    monkeypatch.setattr(process_module, "asyncio", types.SimpleNamespace(sleep=instant_sleep))
    plane = _FakePlane(peaks)
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
    _drive(peaks, monkeypatch, caplog)

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
    _drive([-18.0] * LEVEL_LOG_EVERY + [None] * LEVEL_LOG_EVERY, monkeypatch, caplog)
    assert len(_peak_lines(caplog)) == 1


def test_cadence_stays_one_line_per_window_and_every_tick_is_published(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    plane, writer = _drive([-12.0] * (2 * LEVEL_LOG_EVERY), monkeypatch, caplog)

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
    # A measured but very quiet window (noise floor) keeps the old -55 dB floor.
    _drive([LEVEL_LOG_MIN_DBFS - 1.0] * LEVEL_LOG_EVERY, monkeypatch, caplog)
    assert _peak_lines(caplog) == []


def test_the_aggregate_floor_is_not_full_scale() -> None:
    # The initial value must never be 0.0 dBFS; -120 dBFS is the 16-bit floor.
    assert LEVEL_FLOOR_DBFS == -120.0
    assert LEVEL_FLOOR_DBFS < LEVEL_LOG_MIN_DBFS
