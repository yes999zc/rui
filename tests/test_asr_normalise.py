"""A quiet clip from the far field must be normalised before it is decoded."""
from __future__ import annotations

import numpy as np
import pytest

from house_ear.providers.sherpa_asr import normalise


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
