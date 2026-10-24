from __future__ import annotations

import asyncio

from rui.device import Capability, MockAdapter
from rui.device.contract import (
    AudioFrame,
    DeviceAdapter,
    DeviceInfo,
    DeviceWake,
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
    from rui.device.mock import MAX_PENDING_WAKES

    adapter = MockAdapter("living")
    for i in range(MAX_PENDING_WAKES + 8):
        adapter.inject_wake(ts_ms=float(i), score=0.9, threshold=0.5)
    pending = adapter.drain_wakes()
    assert len(pending) == MAX_PENDING_WAKES
    assert pending[0].ts_ms == 8.0, "the oldest events are dropped"
