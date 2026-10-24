"""P1-2/P1-3: a broken adapter must degrade the plane, never make it silently deaf."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rui.audio import process
from rui.audio.process import MAX_SIGNAL_TEXT, AudioPlane
from rui.config import Config
from rui.device.contract import AudioFrame
from rui.device.mock import MockAdapter


class StubWriter:
    """The slice of StreamWriter the capture loop touches."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def messages(self) -> list[dict]:
        return [
            json.loads(frame[5:].decode("utf-8"))
            for frame in self.frames
            if frame and frame[0] == 1
        ]


class LoopAdapter(MockAdapter):
    """A mock adapter whose failure mode is chosen by the test."""

    def __init__(
        self,
        *,
        fail_frames_after: int | None = None,
        fail_frames_always: bool = False,
        raise_signals: bool = False,
        signals: object | None = None,
    ) -> None:
        super().__init__("living")
        self.fail_frames_after = fail_frames_after
        self.fail_frames_always = fail_frames_always
        self.raise_signals = raise_signals
        self.signals = signals
        self.attempts = 0
        self.failed_once = False

    def drain_signals(self):
        if self.raise_signals:
            raise RuntimeError("vendor signal boom")
        if self.signals is not None:
            return self.signals
        return []

    async def frames(self):  # type: ignore[override]
        self.attempts += 1
        count = 0
        while True:
            count += 1
            if self.fail_frames_always:
                raise RuntimeError("decoder boom")
            if (
                self.fail_frames_after is not None
                and not self.failed_once
                and count > self.fail_frames_after
            ):
                self.failed_once = True
                raise RuntimeError("decoder boom once")
            self._seq += 1
            yield AudioFrame(
                device_id=self.info.id,
                seq=self._seq,
                ts_ms=time.monotonic() * 1000.0,
                pcm=b"\x00\x00" * (240 * self.info.channels),
                channels=self.info.channels,
                sample_rate=self.info.sample_rate,
                reference_channels=self.info.ref_channels,
            )
            await asyncio.sleep(0.002)


def make_plane(adapter: LoopAdapter) -> AudioPlane:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.ack.mode = "none"
    plane = AudioPlane(cfg, "living", Path("/tmp/supervised.sock"))
    plane.adapter = adapter  # type: ignore[assignment]
    return plane


async def run_capture(
    plane: AudioPlane,
    adapter: LoopAdapter,
    *,
    seconds: float = 0.2,
) -> StubWriter:
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    task = asyncio.create_task(plane._capture(connected, writer, adapter.info))
    try:
        await asyncio.sleep(seconds)
        # Recorded before the teardown sets `_stop`, so a test can tell "the failure
        # stopped the plane" from "the test stopped it".
        plane._stopped_early = plane._stop.is_set()
    finally:
        plane._stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    return writer


def fast_restarts(monkeypatch, *, retries: int = 2) -> None:
    monkeypatch.setattr(process, "CAPTURE_MAX_RESTARTS", retries)
    monkeypatch.setattr(process, "CAPTURE_RESTART_BACKOFF_S", 0.01)
    monkeypatch.setattr(process, "CAPTURE_RESTART_BACKOFF_MAX_S", 0.02)


def test_a_raising_drain_signals_does_not_stop_capture() -> None:
    adapter = LoopAdapter(raise_signals=True)
    plane = make_plane(adapter)

    asyncio.run(run_capture(plane, adapter, seconds=0.15))

    assert plane.frames_in >= 5, "capture stopped when drain_signals() raised"
    assert plane.signal_errors >= 1
    assert "vendor signal boom" in plane.signal_last_error
    assert plane._stopped_early is False
    assert plane.capture_errors == 0
    assert plane.capture_degraded is False


def test_non_list_and_malformed_signals_are_contained() -> None:
    plane = make_plane(LoopAdapter())
    adapter = plane.adapter
    assert isinstance(adapter, LoopAdapter)

    adapter.signals = 42  # not iterable
    assert plane._drain_signals() == []
    assert plane.signal_invalid == 1

    adapter.signals = [SimpleNamespace()]  # no kind
    assert plane._drain_signals() == []
    assert plane.signal_invalid == 2

    adapter.signals = [
        SimpleNamespace(kind="playing", text="x" * (MAX_SIGNAL_TEXT + 100), ts=1.0),
        SimpleNamespace(kind="wake", text="ok", ts=float("nan")),
    ]
    events = plane._drain_signals()

    assert len(events) == 1
    assert events[0].kind == "playing"
    assert len(events[0].text) == MAX_SIGNAL_TEXT
    assert plane.signal_invalid == 3


def test_frames_exception_restarts_and_keeps_capturing(monkeypatch) -> None:
    fast_restarts(monkeypatch)
    adapter = LoopAdapter(fail_frames_after=5)
    plane = make_plane(adapter)

    asyncio.run(run_capture(plane, adapter, seconds=0.25))

    assert plane.capture_errors == 1
    assert plane.capture_restarts == 1
    assert plane.frames_in > 5, "frames stopped after the one decoder failure"
    assert plane.capture_degraded is False, "a recovered capture must not stay degraded"
    assert plane._stopped_early is False


def test_frames_failure_budget_surfaces_a_plane_error(monkeypatch) -> None:
    fast_restarts(monkeypatch, retries=2)
    adapter = LoopAdapter(fail_frames_always=True)
    plane = make_plane(adapter)

    writer = asyncio.run(run_capture(plane, adapter, seconds=0.5))

    assert plane.capture_errors == 3, "2 retries + the original failure"
    assert plane.capture_degraded is True
    assert plane._stopped_early is True, "the plane must not stay up and deaf"
    errors = [m for m in writer.messages() if m.get("type") == "error"]
    assert errors and "capture failed" in errors[0]["message"]
    assert writer.closed is True, "the control socket must close so the supervisor rebuilds"


def test_a_task_that_dies_while_the_socket_is_open_stops_the_plane() -> None:
    async def run() -> StubWriter:
        adapter = LoopAdapter()
        plane = make_plane(adapter)
        writer = StubWriter()

        async def dead() -> None:
            raise RuntimeError("metrics boom")

        task = asyncio.create_task(dead(), name="metrics")
        plane._watch_task("metrics", task, writer)  # type: ignore[arg-type]
        try:
            await task
        except RuntimeError:
            pass
        await asyncio.sleep(0)
        return writer

    writer = asyncio.run(run())

    assert writer.messages(), "a dead side task must be reported"
    assert writer.messages()[0]["type"] == "error"
    assert writer.closed is True


def test_a_raising_probe_cannot_start_capture_silently() -> None:
    """P1-3: capture used to call probe() unguarded; a failure now surfaces."""

    class BadProbe(LoopAdapter):
        async def probe(self):  # type: ignore[override]
            raise RuntimeError("probe boom")

    adapter = BadProbe()
    plane = make_plane(adapter)

    async def run() -> None:
        writer, connected = StubWriter(), asyncio.Event()
        connected.set()
        # `info=None` is the direct-call path; the plane's handle() probes first and
        # sends an Error instead, but neither may leave a task that never started.
        with pytest.raises(RuntimeError, match="probe failed"):
            await plane._capture(connected, writer)  # type: ignore[arg-type]

    asyncio.run(run())
