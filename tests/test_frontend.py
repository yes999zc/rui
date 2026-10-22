"""The array front end: who gets weight, and when the weights are allowed to move."""
from __future__ import annotations

import numpy as np
import pytest

from house_ear.audio.frontend import ChannelMixer

RATE = 16000


def block(levels: tuple[float, ...], seconds: float = 0.02, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    return (rng.standard_normal((n, len(levels))) * np.array(levels)).astype(np.float32)


def test_a_silent_block_does_not_poison_the_floor() -> None:
    # The old implementation turned every later block into NaN and went permanently
    # deaf with no error: an empty block passed the "length" guard and mean() of nothing
    # is NaN.
    mixer = ChannelMixer(6)
    mixer.update(block((0.01,) * 6))
    weights = mixer.update(np.zeros((0, 6), dtype=np.float32))
    assert np.all(np.isfinite(weights))
    assert weights.sum() == pytest.approx(1.0)


def test_the_channel_that_stands_out_gets_the_weight() -> None:
    mixer = ChannelMixer(6)
    for i in range(40):
        mixer.update(block((0.002,) * 6, seed=i))       # learn the floor
    quiet = block((0.002, 0.002, 0.002, 0.002, 0.002, 0.002))
    speaking = block((0.002, 0.002, 0.09, 0.002, 0.002, 0.002))
    mixer.update(quiet)
    weights = mixer.update(speaking)
    assert int(np.argmax(weights)) == 2
    assert weights[2] > 0.3


def test_weights_are_frozen_while_the_speaker_is_emitting() -> None:
    mixer = ChannelMixer(6)
    for i in range(40):
        mixer.update(block((0.002,) * 6, seed=i))
    before = mixer.update(block((0.002, 0.09, 0.002, 0.002, 0.002, 0.002))).copy()
    during = mixer.update(block((0.09, 0.002, 0.002, 0.002, 0.002, 0.002)), freeze=True)
    assert np.array_equal(before, during)
    assert mixer.frozen is True
    # ...and they move again once playback stops.
    after = mixer.update(block((0.09, 0.002, 0.002, 0.002, 0.002, 0.002)), freeze=False)
    assert int(np.argmax(after)) == 0


def test_the_mix_preserves_a_coherent_signal() -> None:
    # Speech reaches all six microphones: same waveform, slightly different gains.
    mixer = ChannelMixer(6)
    rng = np.random.default_rng(1)
    n = int(RATE * 0.02)
    one = rng.standard_normal(n).astype(np.float32) * 0.02
    voice = np.stack([one * g for g in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5)], axis=1)
    weights = mixer.update(voice)
    mixed = mixer.mix(voice, weights)
    # Sum of weights is 1, so a coherent signal survives; the spread of gains costs a
    # couple of dB, which the noise averaging pays back.
    ratio = np.sqrt(np.mean(mixed**2)) / np.sqrt(np.mean(voice[:, 0] ** 2))
    assert 0.5 < ratio <= 1.05


def test_a_floor_that_only_ever_climbs_still_discriminates() -> None:
    # A fan switching on: the floor rises 0.05 % per block, so for about a minute the
    # weights still favour the stale (too low) floor. That is the old build's trade-off,
    # and this test pins the direction rather than pretending it is instant.
    mixer = ChannelMixer(6)
    for i in range(50):
        mixer.update(block((0.002,) * 6, seed=i))
    loud_room = block((0.02,) * 6, seed=99)
    weights = mixer.update(loud_room)
    assert np.allclose(weights, 1.0 / 6, atol=0.02)   # nothing stands out: uniform
