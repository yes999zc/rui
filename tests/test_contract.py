from __future__ import annotations

import asyncio
import math

import pytest

from rui.device import Capability, MockAdapter
from rui.device.contract import (
    DEVICE_VOLUME_MAX,
    MAX_PENDING_WAKES,
    PROBE_BOUND_CAPABILITIES,
    AudioFrame,
    BoundedWakeQueue,
    DeviceAdapter,
    DeviceInfo,
    DeviceWake,
    device_volume_to_percent,
    effective_capabilities,
    percent_to_device_volume,
    probe_only_capabilities,
    wake_validation_error,
)

FRAME_MS = 20


def test_frame_reports_its_own_layout() -> None:
    pcm = b"\x00" * (960 * 8 * 2)  # 20 ms at 48 kHz, 8 channels
    frame = AudioFrame(device_id="living", seq=1, ts_ms=0.0, pcm=pcm, channels=8,
                       sample_rate=48000, reference_channels=2)
    assert frame.mic_channels == 6
    assert frame.frame_samples == 960
    assert round(frame.duration_ms) == 20


def test_mock_matches_the_device_it_stands_in_for() -> None:
    adapter = MockAdapter("living")
    info = asyncio.run(adapter.probe())
    assert info.sample_rate == 48000
    assert info.channels == 8
    assert info.mic_channels == 6
    assert info.ref_channels == 2
    assert info.supports(Capability.REFERENCE)
    assert info.missing_for_barge_in() == []


def test_mock_frames_are_8ch_48k_and_carry_reference_channels() -> None:
    async def first_frame() -> AudioFrame:
        adapter = MockAdapter("living")
        await adapter.open()
        async for frame in adapter.frames(FRAME_MS):
            await adapter.close()
            return frame
        raise AssertionError("no frame produced")

    frame = asyncio.run(first_frame())
    assert frame.channels == 8
    assert frame.reference_channels == 2
    assert frame.sample_rate == 48000
    assert frame.frame_samples == 960
    assert len(frame.pcm) == 960 * 8 * 2


def test_play_requires_the_playback_layout() -> None:
    adapter = MockAdapter("living")
    asyncio.run(adapter.play(b"\x00" * 4, sample_rate=48000, channels=2))
    assert adapter.play_calls == [(48000, 2)]


# --- Socket & Plug v1: DeviceWake / drain_wakes -------------------------------------



def test_device_wake_defaults_and_is_immutable() -> None:
    wake = DeviceWake(ts_ms=1234.0, score=0.8, threshold=0.5)
    assert wake.barge is False
    assert wake.level is None
    assert wake.source == "device"
    try:
        wake.score = 0.1  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover - frozen dataclass
        raise AssertionError("DeviceWake must be frozen")


def test_capabilities_are_the_v1_spelling() -> None:
    assert Capability.DEVICE_WAKE.value == "device_wake"
    assert Capability.BARGE_VIA_DEVICE.value == "barge_via_device"


def test_an_adapter_without_device_wake_drains_nothing() -> None:
    class Bare(DeviceAdapter):
        async def probe(self) -> DeviceInfo:
            return DeviceInfo(id="x", adapter="bare", model="x", host="127.0.0.1")

        async def open(self) -> None: ...
        async def close(self) -> None: ...
        def frames(self):  # pragma: no cover - protocol shape only
            return iter(())

        async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None: ...
        async def stop_playback(self, fade_ms: int = 120) -> None: ...
        async def set_volume(self, volume: int) -> None: ...
        async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None: ...

    assert Bare().drain_wakes() == []


def test_mock_can_inject_wakes_and_drains_each_event_once() -> None:
    adapter = MockAdapter("living", capabilities={Capability.MIC_ARRAY, Capability.DEVICE_WAKE})
    assert adapter.drain_wakes() == []
    first = adapter.inject_wake(ts_ms=100.0, score=0.9, threshold=0.5, barge=True)
    second = adapter.inject_wake(ts_ms=700.0, score=0.7, threshold=0.5)
    assert isinstance(first, DeviceWake) and first.barge is True
    pending = adapter.drain_wakes()
    assert [wake.ts_ms for wake in pending] == [100.0, 700.0]
    assert adapter.drain_wakes() == [], "events must not repeat"
    adapter.inject_wake(ts_ms=900.0)
    asyncio.run(adapter.close())
    assert adapter.drain_wakes() == [], "close() discards pending events"
    assert second.source == "device"


def test_mock_wake_queue_is_bounded() -> None:
    adapter = MockAdapter("living")
    for i in range(MAX_PENDING_WAKES + 8):
        adapter.inject_wake(ts_ms=float(i), score=0.9, threshold=0.5)
    pending = adapter.drain_wakes()
    assert len(pending) == MAX_PENDING_WAKES
    assert pending[0].ts_ms == 8.0, "the oldest events are dropped"


# --- Socket & Plug v1: volume / capability / wake validation ------------------------


def test_volume_has_one_mapping_between_percent_and_the_device_mixer() -> None:
    assert percent_to_device_volume(0) == 0
    assert percent_to_device_volume(50) == 128
    assert percent_to_device_volume(100) == DEVICE_VOLUME_MAX
    assert device_volume_to_percent(0) == 0
    assert device_volume_to_percent(128) == 50
    assert device_volume_to_percent(DEVICE_VOLUME_MAX) == 100
    for bad in (-1, 101, 1.5, True):
        with pytest.raises(ValueError):
            percent_to_device_volume(bad)  # type: ignore[arg-type]
    for bad in (-1, 256, 1.5, False):
        with pytest.raises(ValueError):
            device_volume_to_percent(bad)  # type: ignore[arg-type]


def test_mock_read_volume_comes_from_services_through_the_default_method() -> None:
    adapter = MockAdapter("living")
    assert asyncio.run(adapter.read_volume()) == adapter.volume
    asyncio.run(adapter.services.set_volume(200))
    assert asyncio.run(adapter.read_volume()) == 200


def test_effective_capabilities_is_manifest_intersect_probe() -> None:
    declared = {
        Capability.MIC_ARRAY,
        Capability.PLAYBACK,
        Capability.DEVICE_WAKE,
        Capability.LED,
        Capability.VOLUME,
    }
    probed = {
        Capability.PLAYBACK,
        Capability.DEVICE_WAKE,
        Capability.BARGE_VIA_DEVICE,
        Capability.REFERENCE,
    }
    effective = effective_capabilities(declared, probed)

    # Probe-bound: both sides must agree.
    assert Capability.PLAYBACK in effective
    assert Capability.DEVICE_WAKE in effective
    assert Capability.MIC_ARRAY not in effective, "probe omitted the mic array"
    assert Capability.REFERENCE not in effective, "the manifest did not declare it"
    assert Capability.BARGE_VIA_DEVICE not in effective, "manifest is the upper bound"
    # Services-backed capabilities are the manifest's call; the probe need not list
    # them (the LX06 v0 probe deliberately does not).
    assert {Capability.LED, Capability.VOLUME} <= effective
    assert probe_only_capabilities(declared, probed) == {
        Capability.BARGE_VIA_DEVICE,
        Capability.REFERENCE,
    }
    assert PROBE_BOUND_CAPABILITIES == {
        Capability.MIC_ARRAY,
        Capability.REFERENCE,
        Capability.PLAYBACK,
        Capability.DEVICE_WAKE,
        Capability.BARGE_VIA_DEVICE,
    }


def test_effective_capabilities_without_a_manifest_is_the_probe() -> None:
    probed = {Capability.PLAYBACK, Capability.DEVICE_WAKE}
    assert effective_capabilities(None, probed) == probed
    assert effective_capabilities(None, probed) == frozenset(probed)


def test_device_wake_rejects_a_non_bool_barge_at_construction() -> None:
    for bad in ("false", 1, None):
        with pytest.raises(TypeError):
            DeviceWake(ts_ms=1.0, score=0.9, threshold=0.5, barge=bad)  # type: ignore[arg-type]
    assert DeviceWake(ts_ms=1.0, score=0.9, threshold=0.5, barge=True).barge is True


def test_wake_validation_error_covers_shape_and_ranges() -> None:
    assert wake_validation_error(DeviceWake(1.0, 0.9, 0.5)) is None
    assert wake_validation_error("nope") is not None
    assert "score" in (wake_validation_error(DeviceWake(1.0, 1.5, 0.5)) or "")
    assert "score" in (wake_validation_error(DeviceWake(1.0, 0.4, 0.5)) or "")
    assert "threshold" in (wake_validation_error(DeviceWake(1.0, 0.9, 0.0)) or "")
    assert "finite" in (
        wake_validation_error(DeviceWake(1.0, math.nan, 0.5)) or ""
    )
    assert "stale" in (
        wake_validation_error(DeviceWake(100.0, 0.9, 0.5), now_ms=2000.0) or ""
    )
    assert "future" in (
        wake_validation_error(DeviceWake(5000.0, 0.9, 0.5), now_ms=1000.0) or ""
    )
    assert "level" in (
        wake_validation_error(DeviceWake(1.0, 0.9, 0.5, level=math.inf)) or ""
    )


def test_bounded_wake_queue_drops_the_oldest_and_counts() -> None:
    queue = BoundedWakeQueue(2)
    a = DeviceWake(1.0, 0.9, 0.5)
    b = DeviceWake(2.0, 0.9, 0.5)
    c = DeviceWake(3.0, 0.9, 0.5)
    queue.push(a)
    queue.push(b)
    queue.push(c)
    assert queue.dropped == 1
    assert [wake.ts_ms for wake in queue.drain()] == [2.0, 3.0]
    assert queue.drain() == []
    queue.push(a)
    queue.clear()
    assert len(queue) == 0
    with pytest.raises(ValueError):
        BoundedWakeQueue(0)


def test_missing_for_barge_in_accepts_the_device_detected_path() -> None:
    device_side = DeviceInfo(
        id="dot2",
        adapter="dot2",
        model="RS03QR",
        host="127.0.0.1",
        capabilities=frozenset(
            {
                Capability.PLAYBACK,
                Capability.DEVICE_WAKE,
                Capability.BARGE_VIA_DEVICE,
            }
        ),
    )
    assert device_side.missing_for_barge_in() == []
    reference_side = DeviceInfo(
        id="lx06",
        adapter="xiaoai",
        model="LX06",
        host="127.0.0.1",
        capabilities=frozenset({Capability.REFERENCE, Capability.PLAYBACK}),
    )
    assert reference_side.missing_for_barge_in() == []
    neither = DeviceInfo(
        id="basic",
        adapter="basic",
        model="basic",
        host="127.0.0.1",
        capabilities=frozenset({Capability.PLAYBACK}),
    )
    assert neither.missing_for_barge_in() == [Capability.REFERENCE]


def test_device_wake_has_only_the_six_v1_fields() -> None:
    fields = set(DeviceWake.__dataclass_fields__)
    assert fields == {"ts_ms", "score", "threshold", "barge", "level", "source"}
    for extra in ("device_id", "room", "playing", "last_active", "last_active_ms"):
        with pytest.raises(TypeError):
            DeviceWake(ts_ms=1.0, score=0.9, threshold=0.5, **{extra: 1})
