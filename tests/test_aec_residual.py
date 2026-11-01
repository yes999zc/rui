"""Residual echo suppression and the wake hold.

The linear MDF is only half of the vendor's chain: its `AECProcess` calls
`speex_echo_cancellation` and then `speex_preprocess_run` on the same block. These
tests pin the two new behaviours - the second stage exists and helps, and the wake
window turns it off so the command spoken over music survives.
"""
from __future__ import annotations

import ctypes
import time

import numpy as np
import pytest

from rui.audio.aec import (
    SPEEX_PREPROCESS_GET_ECHO_STATE,
    AecChain,
    EchoCanceller,
    speex_available,
)

pytestmark = pytest.mark.skipif(
    not speex_available(), reason="libspeexdsp is not installed on this box"
)
RATE = 16000
BLOCK = 320  # 20 ms, what the plane feeds


def synth(seconds: float = 6.0, seed: int = 11):
    """Reference, a room tail, and six microphones that hear it at different gains."""
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    ref = rng.standard_normal(n).astype(np.float32) * 0.1
    shape = np.array([0.2, 0.2, 0.2], dtype=np.float32)
    for _ in range(3):
        ref = np.convolve(ref, shape, mode="same").astype(np.float32)

    ir = np.zeros(int(0.2 * RATE), dtype=np.float32)
    direct = int(0.0024 * RATE)  # the speaker's reference leads the mics by 2.4 ms
    ir[direct] = 1.0
    for k in range(direct + 1, ir.size):
        ir[k] = rng.standard_normal() * 0.02 * np.exp(-(k - direct) / (0.05 * RATE))
    echo = np.convolve(ref, ir, mode="full")[:n].astype(np.float32)
    gains = (1.0, 0.8, 0.7, 0.75, 0.6, 0.5)
    mic = np.stack([echo * g for g in gains], axis=1)
    mic += rng.standard_normal(mic.shape).astype(np.float32) * 0.0005
    return ref, mic


def run(chain: AecChain, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
    out = [
        chain.process(mic[i:i + BLOCK], ref[i:i + BLOCK])
        for i in range(0, mic.shape[0] - BLOCK + 1, BLOCK)
    ]
    return np.concatenate(out)


def power_db(x: np.ndarray) -> float:
    return 10.0 * np.log10(max(float(np.mean(np.square(x.astype(np.float64)))), 1e-20))


def test_residual_suppression_is_available_and_optional():
    with_pp = EchoCanceller(taps=1024, rate=RATE, residual=True)
    without = EchoCanceller(taps=1024, rate=RATE, residual=False)
    try:
        assert with_pp.pp, "a speexdsp build with the preprocessor must attach it"
        assert without.pp is None
    finally:
        with_pp.close()
        without.close()


def test_the_preprocessor_holds_the_same_echo_state_the_filter_runs():
    """The wiring is only real if speex kept our pointer, not a stack temporary."""
    canceller = EchoCanceller(taps=1024, rate=RATE, residual=True)
    try:
        assert canceller.pp
        # GET writes through *ptr, so byref is right here even though SET takes the
        # pointer itself.
        back = ctypes.c_void_p()
        rc = canceller.lib.speex_preprocess_ctl(
            canceller.pp, SPEEX_PREPROCESS_GET_ECHO_STATE, ctypes.byref(back)
        )
        assert rc == 0
        assert back.value == canceller.handle, (
            "the preprocessor must cancel against the filter's own echo state"
        )
    finally:
        canceller.close()


def test_the_residual_stage_actually_suppresses():
    """+2 dB is the floor: with the echo state attached it measures ~+4.9 dB here.

    A weaker assertion passes while the stage is a pass-through: speexdsp gates the
    whole spectral gain on denoise, so SET_DENOISE=0 leaves this at 0.0 dB.
    """
    ref, mic = synth()
    linear = AecChain(channels=6, rate=RATE, taps=4096, residual=False)
    full = AecChain(channels=6, rate=RATE, taps=4096, residual=True)
    try:
        out_linear = run(linear, mic, ref)
        out_full = run(full, mic, ref)
        linear_erle = power_db(mic[:, 0]) - power_db(out_linear)
        full_erle = power_db(mic[:, 0]) - power_db(out_full)
    finally:
        linear.close()
        full.close()
    assert linear_erle > 12.0, f"the MDF itself must cancel: {linear_erle:.1f} dB"
    assert full_erle >= linear_erle + 2.0, (
        f"the residual stage must add suppression, not be a pass-through: "
        f"{full_erle:.1f} vs {linear_erle:.1f} dB"
    )
    assert full.erle_db is not None and full.erle_db > 12.0


def test_the_hold_lets_near_end_speech_through():
    """With the hold on, a burst over the playback survives; without it, it is eaten.

    The burst is near-end only (not in the reference), so the linear stage has no
    business touching it: the held chain must match the linear one, while the chain
    that keeps the suppressor running must lose a clearly measurable amount.
    """
    ref, mic = synth(seconds=2.4)
    start, stop = int(0.6 * RATE), int(1.8 * RATE)
    rng = np.random.default_rng(23)
    burst = np.convolve(
        rng.standard_normal(mic.shape[0]).astype(np.float32),
        np.array([0.3, 0.3, 0.3], dtype=np.float32),
        mode="same",
    ) * 0.008
    mic = mic.copy()
    mic[start:stop] += burst[start:stop, None]

    linear = AecChain(channels=6, rate=RATE, taps=4096, residual=False)
    held = AecChain(channels=6, rate=RATE, taps=4096, residual=True, wake_hold_ms=5000)
    running = AecChain(channels=6, rate=RATE, taps=4096, residual=True)
    try:
        held.hold_for()  # a wake was just admitted: 5000 ms of protection
        out_linear = run(linear, mic, ref)
        out_held = run(held, mic, ref)
        out_running = run(running, mic, ref)
    finally:
        linear.close()
        held.close()
        running.close()

    # Skip the first frames of the burst: the linear filter is still adapting to it.
    window = slice(start + 2 * BLOCK, stop - BLOCK)
    linear_db = power_db(out_linear[window])
    held_db = power_db(out_held[window])
    running_db = power_db(out_running[window])
    assert abs(held_db - linear_db) <= 2.0, (
        f"with the hold on, the burst must survive as it did linearly: "
        f"{held_db:.1f} vs {linear_db:.1f} dB"
    )
    assert held_db - running_db >= 6.0, (
        f"without the hold the suppressor must eat the burst: "
        f"{held_db:.1f} vs {running_db:.1f} dB (measured ~8.6 dB here)"
    )


def test_the_wake_hold_switches_the_suppressor_off_then_expires():
    chain = AecChain(channels=6, rate=RATE, taps=1024, residual=True, wake_hold_ms=80)
    ref, mic = synth(seconds=1.0)
    try:
        chain.process(mic[:BLOCK], ref[:BLOCK])
        assert chain.mix_canceller.pp is not None
        assert chain.mix_canceller.hold is False, "no wake yet: the suppressor runs"

        chain.hold_for()
        chain.process(mic[BLOCK:2 * BLOCK], ref[BLOCK:2 * BLOCK])
        assert chain.mix_canceller.hold is True, "the wake window must pause it"

        time.sleep(0.09)
        chain.process(mic[2 * BLOCK:3 * BLOCK], ref[2 * BLOCK:3 * BLOCK])
        assert chain.mix_canceller.hold is False, "the hold must expire on its own"
    finally:
        chain.close()


def test_a_zero_hold_means_never_pause_the_suppressor():
    chain = AecChain(channels=6, rate=RATE, taps=1024, residual=True, wake_hold_ms=0)
    try:
        chain.hold_for()
        assert chain._hold_until == 0.0
    finally:
        chain.close()
