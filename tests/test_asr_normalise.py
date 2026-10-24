"""A quiet clip from the far field must be normalised before it is decoded."""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from rui.providers.sherpa_asr import normalise


def _tone(peak: float, n: int = 1601) -> np.ndarray:
    """A float32 sine whose largest sample is exactly `peak`."""
    wave = np.sin(np.linspace(0.0, 2.0 * np.pi, n))
    wave = wave / float(np.abs(wave).max())
    return (wave * peak).astype(np.float32)


def test_a_quiet_clip_is_brought_up_to_a_sane_peak() -> None:
    # The old deployment measured real speech at rms ~0.005 after AEC and the tail
    # syllables got mangled without this.
    quiet = (np.sin(np.linspace(0, 100, 1600)) * 0.02).astype(np.float32)
    out = normalise(quiet)
    assert float(np.abs(out).max()) == pytest.approx(0.8, rel=1e-5)


def test_a_loud_clip_is_left_alone() -> None:
    loud = (np.sin(np.linspace(0, 100, 1600)) * 0.5).astype(np.float32)
    assert np.array_equal(normalise(loud), loud)


def test_silence_is_left_alone() -> None:
    silent = np.zeros(1600, dtype=np.float32)
    assert np.array_equal(normalise(silent), silent)


@pytest.mark.parametrize("peak", [0.005, 1e-7], ids=["measured-far-field", "barely-audible"])
def test_a_quiet_clip_is_lifted_to_the_target_peak_without_clipping(peak: float) -> None:
    quiet = _tone(peak)
    out = normalise(quiet)

    out_peak = float(np.abs(out).max())
    # raised all the way to the target, not merely made louder
    assert out_peak == pytest.approx(0.8, rel=1e-5)
    # 0.8 is a target, not a ceiling that hides clipping: nothing hits full scale
    assert out_peak < 1.0
    assert np.isfinite(out).all()
    assert out.dtype == np.float32

    # the gain is uniform, so the waveform shape (and therefore the syllables) survives
    heard = quiet != 0
    np.testing.assert_allclose(out[heard] / quiet[heard], 0.8 / peak, rtol=1e-5)


@pytest.mark.parametrize("peak", [0.3, 0.95, 1.0], ids=["mid-level", "near-full", "full-scale"])
def test_a_loud_clip_is_not_amplified_and_is_never_pushed_into_clipping(peak: float) -> None:
    loud = _tone(peak)
    out = normalise(loud)

    # a peak above the 0.25 gate is returned untouched - amplifying it would lift the room
    assert np.array_equal(out, loud)
    assert float(np.abs(out).max()) == pytest.approx(peak, rel=1e-5)
    assert float(np.abs(out).max()) <= 1.0


def test_silence_is_finite_and_does_not_divide_by_zero() -> None:
    for silent in (np.zeros(1600, dtype=np.float32), np.zeros(0, dtype=np.float32)):
        # a numpy divide-by-zero warning would be raised as an error here
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = normalise(silent)

        assert out.size == silent.size
        assert out.dtype == np.float32
        assert np.isfinite(out).all()
        assert np.array_equal(out, silent)


def test_normalisation_keeps_the_sample_count_and_dtype() -> None:
    # the production path: int16 pcm -> float32 in [-1, 1] -> normalise (see _decode)
    pcm = np.round(_tone(0.1, 1600) * 32767.0).astype(np.int16)
    pcm_before = pcm.copy()
    samples = np.frombuffer(pcm.tobytes(), dtype="<i2").astype("float32") / 32768.0
    out = normalise(samples)

    assert out.size == samples.size == pcm.size
    assert out.dtype == np.float32
    assert float(np.abs(out).max()) == pytest.approx(0.8, rel=1e-4)

    # an int16 buffer handed in unconverted is a no-op (its peak is in int units, far
    # above the float gate), but it must still keep its length and dtype
    raw_out = normalise(pcm)
    assert raw_out.size == pcm.size
    assert raw_out.dtype == np.int16

    # the caller's buffer is never rewritten in place
    assert np.array_equal(pcm, pcm_before)


def test_a_short_clip_is_normalised_by_the_same_rule() -> None:
    # 800 samples at 16 kHz: 50 ms
    short = _tone(0.02, 800)
    out = normalise(short)

    assert out.size == 800
    assert float(np.abs(out).max()) == pytest.approx(0.8, rel=1e-5)
    assert float(np.abs(out).max()) <= 1.0
    assert np.isfinite(out).all()
