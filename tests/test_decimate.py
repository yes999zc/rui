from __future__ import annotations

import numpy as np
import pytest

from rui.audio.decimate import Decimator, lowpass

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


# ---------------------------------------------------------------- polyphase (0.8.9)
REFERENCE_TOL = 1e-5


def random_blocks(seed: int, channels: int, sizes: tuple[int, ...]) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    return [rng.uniform(-1.0, 1.0, size=(n, channels)).astype(np.float32) for n in sizes]


@pytest.mark.parametrize("factor", [2, 3, 4, 5])
@pytest.mark.parametrize("channels", [1, 3, 8])
def test_polyphase_matches_the_pre_0_8_9_convolution(factor: int, channels: int) -> None:
    """The polyphase path must be the old full-rate convolution, sample for sample.

    Block sizes deliberately include 1 and odd lengths: they shift the phase grid that
    `_next_out` rides on, which is exactly where a decimator redesign breaks quietly.
    """
    blocks = random_blocks(
        100 + 10 * factor + channels, channels, (960, 960, 743, 1, 1000, 517, 960)
    )
    new = Decimator(factor=factor, channels=channels)
    old = Decimator(factor=factor, channels=channels)
    got = np.concatenate([new.process(block) for block in blocks])
    want = np.concatenate([old._reference_decimate(block) for block in blocks])
    assert want.size, "the oracle emitted nothing; the comparison would be vacuous"
    assert got.shape == want.shape
    assert np.allclose(got, want, atol=REFERENCE_TOL, rtol=0.0)


@pytest.mark.parametrize("factor", [2, 3, 4])
def test_streaming_equals_one_shot_for_every_factor(factor: int) -> None:
    x = random_blocks(7 + factor, 2, (48000,))[0]
    one_shot = Decimator(factor=factor, channels=2).process(x)

    stream = Decimator(factor=factor, channels=2)
    streamed = np.concatenate(
        [stream.process(x[i:i + 960]) for i in range(0, len(x), 960)]
    )
    assert len(streamed) == len(one_shot)
    assert np.allclose(streamed, one_shot, atol=REFERENCE_TOL, rtol=0.0)


@pytest.mark.parametrize("factor", [2, 3, 4])
def test_a_warm_frame_emits_exactly_n_over_factor(factor: int) -> None:
    """Steady state, which is what the plane relies on for its 20 ms cadence."""
    out = Decimator(factor=factor, channels=4).process(np.zeros((960, 4), dtype=np.float32))
    assert out.shape == (960 // factor, 4)
    assert out.dtype == np.float32


def test_short_and_empty_blocks_keep_the_old_fixed_latency() -> None:
    """The history starts zeroed, so a short first frame still completes windows.

    That is 0.8.8 behaviour (first output lands 48 input samples in) and it is preserved:
    only a block that cannot fill a window at all - an empty one, given the zeroed
    history - returns (0, channels) without advancing the output index.
    """
    new = Decimator(factor=3, channels=2)
    old = Decimator(factor=3, channels=2)
    empty = np.zeros((0, 2), dtype=np.float32)
    assert new.process(empty).shape == (0, 2)
    assert old._reference_decimate(empty).shape == (0, 2)

    tiny = np.ones((5, 2), dtype=np.float32)
    got = new.process(tiny)
    want = old._reference_decimate(tiny)
    assert got.shape == want.shape
    assert np.allclose(got, want, atol=REFERENCE_TOL, rtol=0.0)


def test_factor_one_never_designs_a_filter_or_a_history(monkeypatch) -> None:
    """16 kHz wire: the voice rate is the capture rate, so nothing is filtered."""
    from rui.audio import decimate as decimate_module

    def bomb(*args, **kwargs):
        raise AssertionError("lowpass must not be designed for factor == 1")

    monkeypatch.setattr(decimate_module, "lowpass", bomb)
    decimator = decimate_module.Decimator(factor=1, channels=8)
    assert decimator.fir is None
    assert decimator._hist is None

    x = (np.arange(960, dtype=np.int16).reshape(120, 8) * 3)
    assert np.array_equal(decimator.process(x), x.astype(np.float32))
    assert decimator.process(np.zeros((0, 8), dtype=np.float32)).shape == (0, 8)


def test_factor_one_does_no_windowed_convolution(monkeypatch) -> None:
    counter = {"calls": 0}
    real = np.lib.stride_tricks.sliding_window_view

    def counting(*args, **kwargs):
        counter["calls"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(np.lib.stride_tricks, "sliding_window_view", counting)
    decimator = Decimator(factor=1, channels=8)
    for _ in range(10):
        decimator.process(np.ones((960, 8), dtype=np.float32))
    assert counter["calls"] == 0
