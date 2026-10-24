from __future__ import annotations

import wave

import numpy as np
import pytest

from rui.audio.process import read_wav_mono
from rui.audio.resample import resample


def sine(freq: float, seconds: float, rate: int) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def test_unity_rate_is_a_passthrough() -> None:
    x = sine(440, 0.1, 48000)
    assert resample(x, 48000, 48000) is not None
    assert np.array_equal(resample(x, 48000, 48000), x)


def test_16k_to_48k_triples_the_length() -> None:
    x = sine(440, 0.5, 16000)
    y = resample(x, 16000, 48000)
    assert abs(len(y) - 3 * len(x)) <= 1
    assert np.corrcoef(y, sine(440, len(y) / 48000, 48000))[0, 1] > 0.99


def test_44k1_to_48k_keeps_the_tone() -> None:
    x = sine(1000, 0.5, 44100)
    y = resample(x, 44100, 48000)
    assert abs(len(y) - int(0.5 * 48000)) <= 2
    ref = sine(1000, len(y) / 48000, 48000)
    assert np.corrcoef(y, ref)[0, 1] > 0.99


def test_read_wav_mono_downmixes_and_scales(tmp_path) -> None:
    path = tmp_path / "stereo.wav"
    left = (np.sin(2 * np.pi * 500 * np.arange(1600) / 16000) * 16000).astype("<i2")
    stereo = np.stack([left, left], axis=1).reshape(-1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(stereo.tobytes())

    data, rate = read_wav_mono(path)
    assert rate == 16000
    assert data.size == 1600
    assert np.abs(data).max() == pytest.approx(16000 / 32768, abs=1e-3)

