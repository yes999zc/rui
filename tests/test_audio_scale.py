"""The plane's voice PCM must keep the input amplitude - a scale bug here is silent.

Found while wiring the wake word: feeding int16 magnitudes into a pipeline that expected
[-1, 1] clipped every output sample, which reads as noise to KWS and as "audio is fine"
to any byte-count assertion. These two cases pin the fix at both sample rates.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rui.audio.decimate import Decimator
from rui.audio.process import AudioPlane
from rui.config import Config
from rui.device.contract import AudioFrame


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


@pytest.mark.parametrize(("rate", "factor"), [(16000, 1), (48000, 3)])
def test_voice_pcm_keeps_the_amplitude(rate: int, factor: int) -> None:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/scale.sock"))
    frame = make_frame(rate)
    out = plane._frame_to_voice(frame, Decimator(factor=factor, channels=8))

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
