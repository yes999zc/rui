"""Socket & Plug v1 policy: device-detected wakes, gated exactly like RUI's own.

The adapter's event is advice. These tests pin the §5 decisions: idle + valid wake arms
the VAD gate; playing + barge=true goes through RUI's barge gate (voice.barge_in,
capability, cue/echo hold); duplicates are debounced; invalid events never form a turn;
and a broken adapter falls back to RUI KWS without taking the frame loop down.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from rui.audio.process import (
    DEVICE_WAKE_MAX_INVALID,
    DEVICE_WAKE_RATE_MAX,
    AudioPlane,
)
from rui.audio.protocol import Wake
from rui.config import Config
from rui.device.contract import Capability, DeviceInfo, DeviceWake
from rui.device.mock import MockAdapter
from rui.trace import TurnTrace, now_ms

WAKE_CAPS = frozenset(
    {
        Capability.MIC_ARRAY,
        Capability.REFERENCE,
        Capability.PLAYBACK,
        Capability.LED,
        Capability.VOLUME,
        Capability.DEVICE_WAKE,
        Capability.BARGE_VIA_DEVICE,
    }
)


class StubWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class FakeGate:
    """The policy only touches these members; the real VAD is exercised elsewhere."""

    def __init__(self) -> None:
        self.armed = False
        self.speaking = False
        self.speech_ms = 0.0
        self.interrupted = 0
        self.discarded = 0
        self.idle_events = 0
        self.arm_calls: list[dict] = []
        self.hold_calls: list[tuple[float, float]] = []
        self.cfg = SimpleNamespace(max_wait_ms=5000)

    def arm(self, **kwargs) -> None:
        self.armed = True
        self.speaking = False
        self.arm_calls.append(kwargs)

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:
        self.hold_calls.append((ms, skip_ms))

    def disarm(self) -> None:
        self.armed = False


class WakeAdapter(MockAdapter):
    def __init__(self, capabilities: frozenset[Capability] = WAKE_CAPS) -> None:
        super().__init__("living", capabilities=capabilities)
        self.stops = 0
        self.vendor_playing = False
        self.vendor_playing_ts = 0.0

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1


def make_plane(
    *, gate: FakeGate | None = None, capabilities: frozenset[Capability] = WAKE_CAPS
) -> tuple[AudioPlane, WakeAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.ack.mode = "none"
    plane = AudioPlane(cfg, "living", Path("/tmp/device-wake.sock"))
    adapter = WakeAdapter(capabilities)
    plane.adapter = adapter  # type: ignore[assignment]
    plane.gate = gate  # type: ignore[assignment]
    plane._configure_device_wake(adapter.info)
    return plane, adapter


def messages(writer: StubWriter) -> list[dict]:
    # 5-byte frame header; FrameType.CONTROL == 1.
    return [
        json.loads(frame[5:].decode("utf-8"))
        for frame in writer.frames
        if frame and frame[0] == 1
    ]


def drain(plane: AudioPlane, *, vendor_wake: bool = False) -> list[dict]:
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    asyncio.run(
        plane._drain_device_wakes(
            vendor_wake=vendor_wake, writer=writer, connected=connected
        )
    )
    return messages(writer)


# --- activation / fallback ----------------------------------------------------------


def test_probe_without_device_wake_stays_on_the_0_2_path() -> None:
    plane, _ = make_plane(capabilities=frozenset({Capability.PLAYBACK}))
    assert plane.device_wake_active is False
    assert plane.device_wake_fallbacks == 0


def test_capability_without_drain_wakes_falls_back_to_kws() -> None:
    cfg = Config()
    plane = AudioPlane(cfg, "living", Path("/tmp/device-wake.sock"))
    info = DeviceInfo(
        id="dot2", adapter="dot2", model="dot2", host="127.0.0.1",
        capabilities=frozenset({Capability.DEVICE_WAKE}),
    )
    plane.adapter = SimpleNamespace(info=info)  # type: ignore[assignment]
    plane._configure_device_wake(info)
    assert plane.device_wake_active is False
    assert plane.device_wake_fallbacks == 1


def test_a_raising_drain_is_contained_and_degrades_after_repeats() -> None:
    class Broken(WakeAdapter):
        def drain_wakes(self) -> list[DeviceWake]:
            raise RuntimeError("adapter bug")

    plane, adapter = make_plane()
    plane.adapter = Broken()  # type: ignore[assignment]
    drain(plane)  # must not raise
    assert plane.device_wake_fallbacks == 1
    assert plane.device_wake_active is True


# --- idle wake ----------------------------------------------------------------------


def test_an_idle_device_wake_arms_the_gate_and_sends_a_device_sourced_wake() -> None:
    gate = FakeGate()
    plane, adapter = make_plane(gate=gate)
    adapter.inject_wake(score=0.91, threshold=0.5)

    frames = drain(plane)

    assert [frame["type"] for frame in frames] == ["device_wake", "wake"]
    device_wake, wake = frames
    assert device_wake["score"] == 0.91
    assert wake["source"] == "device"
    assert wake["action"] == "turn"
    assert wake["barge"] is False
    assert wake["score"] == 0.91
    assert wake["threshold"] == 0.5
    assert gate.armed is True
    assert gate.arm_calls[-1]["skip_after_wake_ms"] == 0, (
        "a device wake must not skip 300 ms of the request"
    )
    assert plane.device_wakes == 1
    assert plane.device_wake_turns == 1
    assert plane.device_wake_last_score == 0.91


def test_a_second_device_wake_interrupts_an_open_turn() -> None:
    gate = FakeGate()
    gate.armed = True
    plane, adapter = make_plane(gate=gate)
    adapter.inject_wake()

    frames = drain(plane)

    wake = next(frame for frame in frames if frame["type"] == "wake")
    assert wake["action"] == "interrupt"
    assert plane.device_wake_interrupts == 1
    assert gate.interrupted == 1


def test_without_vad_a_wake_never_forms_a_turn() -> None:
    plane, adapter = make_plane(gate=None)
    adapter.inject_wake()

    frames = drain(plane)

    assert frames == [frame for frame in frames if frame["type"] != "wake"]
    assert plane.device_wake_no_vad == 1
    assert plane.device_wake_turns == 0


# --- barge --------------------------------------------------------------------------


def test_playing_plus_barge_allowed_stops_the_speaker() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    plane._speak_queued_s = 1.0
    adapter.inject_wake(barge=True)

    frames = drain(plane)

    wake = next(frame for frame in frames if frame["type"] == "wake")
    assert wake["barge"] is True
    assert wake["action"] == "barge"
    assert wake["barge_hint"] is True
    assert adapter.stops == 1
    assert plane.device_wake_barges == 1
    assert plane.barges == 1
    assert plane.last_barge_stop_ms is not None
    assert plane._speak_queued_s == 0.0


def test_playing_without_a_barge_hint_is_ignored() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    plane._speak_queued_s = 1.0
    adapter.inject_wake(barge=False)

    frames = drain(plane)

    assert all(frame["type"] != "wake" for frame in frames)
    assert adapter.stops == 0
    assert plane.device_wake_suppressed == 1
    assert plane.device_wake_turns == 0


def test_barge_in_off_suppresses_the_device_hint() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    plane.cfg.audio.voice.barge_in = False
    plane._speak_queued_s = 1.0
    adapter.inject_wake(barge=True)

    frames = drain(plane)

    assert all(frame["type"] != "wake" for frame in frames)
    assert adapter.stops == 0
    assert plane.device_wake_suppressed == 1
    assert plane.device_wake_barge_suppressed == 1


def test_a_barge_hint_inside_the_cue_hold_is_suppressed() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    plane._speak_queued_s = 1.0
    plane._wake_hold_until = time.monotonic() + 1.0
    adapter.inject_wake(barge=True)

    frames = drain(plane)

    assert all(frame["type"] != "wake" for frame in frames)
    assert adapter.stops == 0


# --- validation / debounce / rate limit / vendor conflict ---------------------------


def test_duplicate_events_inside_the_debounce_window_are_dropped() -> None:
    gate = FakeGate()
    plane, adapter = make_plane(gate=gate)
    first = now_ms() - 1000.0
    adapter.inject_wake(ts_ms=first)
    adapter.inject_wake(ts_ms=first + 100.0)

    frames = drain(plane)

    assert sum(1 for frame in frames if frame["type"] == "wake") == 1
    assert plane.device_wakes == 2
    assert plane.device_wake_debounced == 1
    assert plane.device_wake_turns == 1


def test_low_score_stale_and_out_of_range_events_are_invalid() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    adapter.inject_wake(score=0.2, threshold=0.8)          # below threshold
    adapter.inject_wake(ts_ms=now_ms() - 5000.0)          # stale
    adapter.inject_wake(ts_ms=now_ms() + 5000.0)          # future
    adapter.inject_wake(score=1.5, threshold=0.5)         # outside 0-1

    frames = drain(plane)

    assert all(frame["type"] != "wake" for frame in frames)
    assert plane.device_wake_invalid == 4
    assert plane.device_wake_turns == 0


def test_too_many_invalid_events_degrade_to_kws() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    for _ in range(DEVICE_WAKE_MAX_INVALID):
        adapter.inject_wake(score=0.0, threshold=1.0)

    drain(plane)

    assert plane.device_wake_invalid == DEVICE_WAKE_MAX_INVALID
    assert plane.device_wake_degraded is True
    assert plane.device_wake_active is False


def test_the_rate_limit_drops_without_falling_back_to_kws() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    now = time.monotonic()
    plane._device_wake_accept_times.extend([now] * DEVICE_WAKE_RATE_MAX)
    adapter.inject_wake()

    frames = drain(plane)

    assert all(frame["type"] != "wake" for frame in frames)
    assert plane.device_wake_rate_limited == 1
    assert plane.device_wake_active is True, "a rate limit must not disable device wakes"


def test_a_vendor_wake_in_the_same_tick_wins() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    adapter.inject_wake()

    frames = drain(plane, vendor_wake=True)

    assert all(frame["type"] != "wake" for frame in frames)
    assert plane.device_wake_conflicts == 1
    assert plane.device_wake_turns == 0


# --- metrics / trace ---------------------------------------------------------------


def test_metrics_expose_the_device_wake_counters() -> None:
    plane, adapter = make_plane(gate=FakeGate())
    adapter.inject_wake()
    drain(plane)

    metrics = plane.metrics()
    assert metrics.device_wakes == 1
    assert metrics.device_wake_count == 1
    assert metrics.device_barge_count == 0
    assert metrics.device_wake_turns == 1
    assert metrics.device_wake_barges == 0
    assert metrics.device_wake_debounced == 0
    assert metrics.device_wake_invalid == 0
    assert metrics.device_wake_suppressed == 0
    assert metrics.device_wake_fallbacks == 0
    assert metrics.device_wake_last_score == 0.9
    assert metrics.wake_count == 0, "wake_count stays the RUI KWS counter"


def test_turn_trace_records_the_device_wake_source_and_decision() -> None:
    trace = TurnTrace(device_id="living")
    trace.note_wake(
        Wake(
            ts_ms=1.0,
            keyword="",
            source="device",
            score=0.92345,
            threshold=0.5,
            barge_hint=True,
            action="barge",
        )
    )

    assert trace.meta["wake_source"] == "device"
    assert trace.meta["device_wake_score"] == 0.9234
    assert trace.meta["device_wake_threshold"] == 0.5
    assert trace.meta["device_wake_barge_hint"] is True
    assert trace.meta["device_wake_action"] == "barge"
    assert trace.meta["device_wake_debounced"] is False

    kws = TurnTrace(device_id="living")
    kws.note_wake(Wake(ts_ms=1.0, keyword="你好小瑞"))
    assert kws.meta["wake_source"] == "rui"
    assert "device_wake_score" not in kws.meta


def test_a_manifest_without_device_wake_is_the_upper_bound(tmp_path: Path) -> None:
    # The LX06 plug resolves for adapter "xiaoai" and declares no device_wake. Even an
    # adapter that advertises the capability must not enable it: the manifest is the
    # upper bound (§2), and the fallback is RUI KWS.
    from rui.config import DeviceConfig

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.devices = [DeviceConfig(id="living", adapter="xiaoai", host="192.0.2.10")]
    plane = AudioPlane(cfg, "living", Path("/tmp/device-wake.sock"))
    adapter = WakeAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    plane._configure_device_wake(adapter.info)

    assert plane.device_wake_active is False
    assert plane.device_wake_fallbacks == 1


def test_manifest_declares_device_wake_but_probe_does_not(
    tmp_path: Path, monkeypatch
) -> None:
    # §4.3: the manifest is the upper bound, but probe() must also verify it. A miss
    # is a runtime downgrade with one warning, never a silent half-working path.
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    plane = AudioPlane(cfg, "living", Path("/tmp/device-wake.sock"))
    adapter = WakeAdapter(frozenset({Capability.PLAYBACK}))
    plane.adapter = adapter  # type: ignore[assignment]
    monkeypatch.setattr(AudioPlane, "_manifest_wake_source", lambda self: "device")

    plane._configure_device_wake(adapter.info)

    assert plane.device_wake_active is False
    assert plane.device_wake_fallbacks == 1


def test_a_sidecar_manifest_degrades_to_kws(monkeypatch) -> None:
    # Frozen Q8: v1 only supports device_wake for in-process/trusted adapters; a
    # sidecar has no standardised wake RPC and must not pretend it does.
    cfg = Config()
    cfg.ack.mode = "none"
    plane = AudioPlane(cfg, "living", Path("/tmp/device-wake.sock"))
    adapter = WakeAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    plane.gate = FakeGate()  # type: ignore[assignment]
    monkeypatch.setattr(AudioPlane, "_manifest_wake_source", lambda self: "sidecar")

    plane._configure_device_wake(adapter.info)

    assert plane.device_wake_active is False
    assert plane.device_wake_fallbacks == 1


def test_a_barge_hint_inside_the_vendor_quiet_window_is_suppressed() -> None:
    # The device may claim barge while the vendor's assistant is answering; the plane
    # mirrors VendorWindow locally so a hint cannot cut the vendor off.
    plane, adapter = make_plane(gate=FakeGate())
    plane._speak_queued_s = 1.0
    plane._vendor_quiet_until = time.monotonic() + 5.0
    adapter.inject_wake(barge=True)

    frames = drain(plane)

    assert all(frame["type"] != "wake" for frame in frames)
    assert adapter.stops == 0
    assert plane.device_wake_barge_suppressed == 1
