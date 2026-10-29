"""The plane's voice PCM must keep the input amplitude - a scale bug here is silent.

Found while wiring the wake word: feeding int16 magnitudes into a pipeline that expected
[-1, 1] clipped every output sample, which reads as noise to KWS and as "audio is fine"
to any byte-count assertion. These two cases pin the fix at both sample rates.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from rui.audio.decimate import Decimator
from rui.audio.process import AudioPlane
from rui.config import Config
from rui.device.contract import AudioFrame, DeviceInfo


def make_frame(rate: int, amplitude: float = 8000.0) -> AudioFrame:
    samples = rate // 50
    t = np.arange(samples) / rate
    block = np.zeros((samples, 8), dtype=np.int16)
    # All six microphones, like the device: they are correlated 0.93-0.97, so the array
    # mix preserves the level. A tone in one channel alone would be averaged down and
    # the test would be measuring the mixer, not the scale.
    tone = (amplitude * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
    block[:, :6] = tone[:, None]
    return AudioFrame(
        device_id="living",
        seq=1,
        ts_ms=0.0,
        pcm=block.tobytes(),
        channels=8,
        sample_rate=rate,
        reference_channels=2,
    )


@pytest.mark.parametrize(
    ("rate", "factor", "channels"),
    [(16000, 1, 8), (16000, 1, 7), (48000, 3, 8), (48000, 3, 7)],
)
def test_voice_pcm_keeps_the_amplitude(rate: int, factor: int, channels: int) -> None:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/scale.sock"))
    frame = make_frame(rate)
    out = plane._frame_to_voice(frame, Decimator(factor=factor, channels=channels))

    pcm = np.frombuffer(out, dtype="<i2").astype(np.float32)
    assert pcm.size == rate // 50 // factor
    assert np.abs(pcm).max() == pytest.approx(8000, rel=0.1)


def test_echo_cancelled_path_keeps_the_amplitude_too(monkeypatch) -> None:
    pytest.importorskip("numpy")
    from rui.audio import aec as aec_module

    if not aec_module.speex_available():
        pytest.skip("libspeexdsp not installed")

    cfg = Config()
    cfg.audio.aec.enabled = True
    plane = AudioPlane(cfg, "living", Path("/tmp/scale.sock"))
    plane._build_aec(mic_channels=6, ref_channels=2)
    assert plane.aec is not None

    frame = make_frame(16000)
    out = plane._frame_to_voice(frame, Decimator(factor=1, channels=8))
    pcm = np.frombuffer(out, dtype="<i2").astype(np.float32)
    # reference is silent, so AEC passes the microphone through (high-passed at 90 Hz);
    # a 1 kHz tone must survive at roughly its original level, not as clipped noise
    assert 4000 < np.abs(pcm).max() < 9000


# ------------------------------------------------- capture-path narrowing (0.8.9)
def test_the_voice_path_reads_only_the_columns_it_uses() -> None:
    """6 mic + 1 ref is all `_frame_to_voice` consumes; the spare ref must not matter.

    The wire carries 8 columns (6 mics + 2 references) but the AEC reference is
    `block[:, mic_channels]`, so a filter sized for 7 columns has to produce exactly the
    bytes a filter sized for all 8 does - that is what lets the plane stop converting and
    decimating a column nobody reads.
    """
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/scale.sock"))
    frame = make_frame(48000)

    full = plane._frame_to_voice(frame, Decimator(factor=3, channels=8))
    used = plane._frame_to_voice(frame, Decimator(factor=3, channels=7))
    assert full == used
    assert len(full) == (48000 // 50 // 3) * 2


def test_aec_reference_index_survives_the_7_column_slice() -> None:
    """Narrowing must not move the ref: with AEC on, 7-col and 8-col bytes are identical.

    `_frame_to_voice` feeds the canceller `block[:, mic_channels]`; a slice that dropped
    or shifted the reference would make the two sizes diverge. A fresh plane per size
    keeps the adaptive filter state identical, so the comparison is only about the block.
    """
    from rui.audio import aec as aec_module

    if not aec_module.speex_available():
        pytest.skip("libspeexdsp not installed")

    frame = make_frame(48000)
    outputs = []
    for channels in (8, 7):
        cfg = Config()
        cfg.audio.aec.enabled = True
        plane = AudioPlane(cfg, "living", Path("/tmp/scale.sock"))
        plane._build_aec(mic_channels=6, ref_channels=2)
        assert plane.aec is not None
        outputs.append(plane._frame_to_voice(frame, Decimator(factor=3, channels=channels)))

    assert outputs[0], "the AEC path produced no audio; the comparison would be vacuous"
    assert outputs[0] == outputs[1]


def test_capture_sizes_the_decimator_for_the_columns_the_voice_path_reads(monkeypatch, tmp_path) -> None:
    """`_capture_frames` must not build a filter for the unused trailing reference."""
    from rui.audio import process as process_module

    seen: dict = {}
    real = process_module.Decimator

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(process_module, "Decimator", spy)

    class OneFrameAdapter:
        def __init__(self, frame: AudioFrame) -> None:
            self._frame = frame

        async def frames(self):
            yield self._frame

    class Writer:
        def write(self, data: bytes) -> None:
            raise AssertionError("no vendor signal is expected in this frame")

        async def drain(self) -> None:
            return None

    async def run() -> None:
        plane = AudioPlane(Config(), "living", tmp_path / "plane.sock")
        plane.adapter = OneFrameAdapter(make_frame(48000))  # type: ignore[assignment]
        info = DeviceInfo(
            id="living", adapter="lx06", model="lx06", host="192.0.2.10",
            channels=8, sample_rate=48000, mic_channels=6, ref_channels=2,
        )
        await plane._capture_frames(info, asyncio.Event(), Writer())  # type: ignore[arg-type]

    asyncio.run(run())
    assert seen["factor"] == 3
    assert seen["channels"] == 7
