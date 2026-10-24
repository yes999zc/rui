from __future__ import annotations

import asyncio

from rui.device import Capability, MockAdapter
from rui.device.contract import AudioFrame

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
