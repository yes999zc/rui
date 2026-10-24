"""Echo cancellation: does it actually remove echo, and does it stay honest?"""
from __future__ import annotations

import numpy as np
import pytest

from rui.audio.aec import AecChain, AecUnavailable, EchoCanceller, speex_available

pytestmark = pytest.mark.skipif(
    not speex_available(), reason="libspeexdsp is not installed on this box"
)
RATE = 16000
BLOCK = 320  # 20 ms, what the plane feeds


def synth_echo(seconds: float = 12.0, seed: int = 5):
    """Reference, room impulse response, and six microphones that hear the echo."""
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    ref = rng.standard_normal(n).astype(np.float32) * 0.1
    shape = np.array([0.2, 0.2, 0.2], dtype=np.float32)
    for _ in range(3):  # band-limit it so it looks like music, not white noise
        ref = np.convolve(ref, shape, mode="same").astype(np.float32)

    ir = np.zeros(int(0.2 * RATE), dtype=np.float32)
    direct = int(0.0024 * RATE)  # the speaker's reference leads the mics by 2.4 ms
    ir[direct] = 1.0
    for k in range(direct + 1, ir.size):
        ir[k] = rng.standard_normal() * 0.02 * np.exp(-(k - direct) / (0.05 * RATE))

    echo = np.convolve(ref, ir, mode="full")[:n].astype(np.float32)
    gains = (1.0, 0.8, 0.7, 0.75, 0.6, 0.5)
    mic = np.stack([echo * g for g in gains], axis=1)
    mic += rng.standard_normal(mic.shape).astype(np.float32) * 0.0005  # room noise floor
    return ref, mic


def power_db(x: np.ndarray) -> float:
    return 10.0 * np.log10(max(float(np.mean(np.square(x.astype(np.float64)))), 1e-20))


def run_chain(mic: np.ndarray, ref: np.ndarray, blocks: int = 6) -> tuple[np.ndarray, AecChain]:
    chain = AecChain(channels=blocks, rate=RATE, taps=4096)
    out = [chain.process(mic[i:i + BLOCK], ref[i:i + BLOCK]) for i in range(0, mic.shape[0], BLOCK)]
    return np.concatenate(out), chain


def test_echo_is_actually_cancelled() -> None:
    ref, mic = synth_echo()
    out, chain = run_chain(mic, ref)

    whole = power_db(mic[:, 0]) - power_db(out)
    second_half = power_db(mic[len(mic) // 2:, 0]) - power_db(out[len(out) // 2:])
    assert whole > 12.0, f"ERLE over the whole run was only {whole:.1f} dB"
    assert second_half > 18.0, f"converged ERLE was only {second_half:.1f} dB"
    assert chain.erle_db is not None and chain.erle_db > 12.0
    assert chain.selected == 0  # the loudest microphone wins


def test_output_never_gets_louder_than_the_microphone() -> None:
    rng = np.random.default_rng(9)
    mic = rng.standard_normal((RATE * 3, 6)).astype(np.float32) * 0.05
    ref = rng.standard_normal(RATE * 3).astype(np.float32) * 0.5  # unrelated to the mics
    out, _ = run_chain(mic, ref)
    assert power_db(out) <= power_db(mic[:, 0]) + 0.5


def test_a_silent_reference_still_mixes_the_whole_array() -> None:
    """Six channels, one stream - and a coherent voice keeps its level.

    This used to hand one channel through, which is the array gain thrown away: the
    device's microphones are correlated 0.93-0.97, so summing them preserves the voice
    while independent noise adds up more slowly.
    """
    n = RATE * 2
    t = np.arange(n) / RATE
    voice = (0.02 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    gains = (1.0, 0.8, 0.7, 0.75, 0.6, 0.5)
    mic = np.stack([voice * g for g in gains], axis=1)
    ref = np.zeros(n, dtype=np.float32)

    out, chain = run_chain(mic, ref)

    assert chain.ref_active is False
    assert chain.erle_db is None          # no echo, no ERLE claim
    # The microphones hear the same voice at different gains, so the weighted mean sits
    # below the loudest one - that loss is paid back by the noise averaging below.
    assert power_db(out) == pytest.approx(power_db(mic[:, 0]), abs=4.0)


def test_independent_room_noise_is_averaged_down() -> None:
    """The array's only real gain: uncorrelated noise sums slower than coherent speech."""
    rng = np.random.default_rng(11)
    n = RATE * 2
    mic = rng.standard_normal((n, 6)).astype(np.float32) * 0.02
    ref = np.zeros(n, dtype=np.float32)

    out, _ = run_chain(mic, ref)

    drop = power_db(mic[:, 0]) - power_db(out)
    assert drop > 5.0, f"six independent channels only bought {drop:.1f} dB"


def test_a_diverged_filter_resets_instead_of_passing_garbage() -> None:
    canceller = EchoCanceller(taps=4096, rate=RATE)
    mic = np.full(BLOCK, np.nan, dtype=np.float32)
    ref = np.zeros(BLOCK, dtype=np.float32)
    out = canceller.process(mic, ref)
    assert np.isfinite(out).all()
    canceller.close()


def test_aec_reports_missing_library_instead_of_silently_doing_nothing(monkeypatch) -> None:
    import rui.audio.aec as aec

    monkeypatch.setattr(aec, "_LIB_CANDIDATES", ("/nonexistent/libspeexdsp.dylib",))
    with pytest.raises(AecUnavailable):
        aec.load_speexdsp()
