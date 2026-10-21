from __future__ import annotations

import numpy as np

from house_ear.audio.decimate import Decimator, lowpass

RATE_IN = 48000
RATE_OUT = 16000


def sine(freq: float, seconds: float, amplitude: float = 10000.0) -> np.ndarray:
    t = np.arange(int(RATE_IN * seconds)) / RATE_IN
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def test_filter_has_unity_dc_gain() -> None:
    assert abs(float(lowpass(3).sum()) - 1.0) < 1e-6


def test_speech_band_survives_decimation() -> None:
    x = sine(1000.0, 0.5).reshape(-1, 1)
    y = Decimator(factor=3, channels=1).process(x)
    assert y.shape[1] == 1
    assert len(y) == len(x) // 3
    assert 0.95 < rms(y) / rms(x) < 1.05


def test_high_band_is_filtered_not_folded() -> None:
    """12 kHz at 48 kHz would alias to 4 kHz if decimated without a filter."""
    x = sine(12000.0, 0.5).reshape(-1, 1)
    y = Decimator(factor=3, channels=1).process(x)
    assert rms(y) / rms(x) < 0.01


def test_streaming_matches_one_shot() -> None:
    x = np.concatenate([sine(440.0, 0.3), sine(6500.0, 0.2)]).reshape(-1, 1)
    one_shot = Decimator(factor=3, channels=1).process(x)

    stream = Decimator(factor=3, channels=1)
    blocks = [stream.process(x[i:i + 960]) for i in range(0, len(x), 960)]
    streamed = np.concatenate(blocks)

    assert len(streamed) == len(one_shot)
    assert np.allclose(streamed, one_shot, atol=1e-3)


def test_channels_stay_separate() -> None:
    left = sine(1000.0, 0.2)
    right = sine(3000.0, 0.2)
    x = np.stack([left, right], axis=1)
    y = Decimator(factor=3, channels=2).process(x)
    assert y.shape[1] == 2
    # each channel keeps its own tone instead of averaging into one signal
    assert not np.allclose(y[:, 0], y[:, 1])


def test_factor_one_is_a_passthrough() -> None:
    x = sine(1000.0, 0.05).reshape(-1, 1)
    y = Decimator(factor=1, channels=1).process(x)
    assert np.array_equal(y, x)


def test_wrong_channel_count_is_rejected() -> None:
    try:
        Decimator(factor=3, channels=2).process(np.zeros((480, 1), dtype=np.float32))
    except ValueError as exc:
        assert "expected" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("mismatched channel count was accepted")
