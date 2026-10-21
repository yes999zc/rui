#!/usr/bin/env python
"""Echo-cancellation benchmark: ERLE and CPU for the real chain, offline.

    uv run python scripts/bench_aec.py --seconds 20

Synthesises a music-like reference, pushes it through two room impulse responses
(short and long tail), mixes six microphones with different gains plus a noise floor,
then runs the same AecChain the plane runs and reports:

  * ERLE over the whole run and over the second half (converged)
  * the channel the chain settled on
  * CPU time and real-time factor for six filters at 4096 taps

This is the offline number. The hardware number needs the speaker playing, which is
`house-ear debug-play` plus a device window.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.audio.aec import AecChain, speex_available  # noqa: E402

RATE = 16000
BLOCK = 320


def room_ir(tail_ms: float, seed: int, direct_ms: float = 2.4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ir = np.zeros(int(tail_ms / 1000 * RATE), dtype=np.float32)
    direct = int(direct_ms / 1000 * RATE)
    ir[direct] = 1.0
    decay = max(1.0, tail_ms / 1000 * RATE / 4)  # energy halves a few times over the tail
    for k in range(direct + 1, ir.size):
        ir[k] = rng.standard_normal() * 0.02 * np.exp(-(k - direct) / decay)
    return ir


def music_reference(seconds: float, seed: int = 3) -> np.ndarray:
    """Band-limited noise with a slow envelope: enough structure for the filter to fit."""
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    x = rng.standard_normal(n).astype(np.float32) * 0.1
    kernel = np.hamming(9).astype(np.float32)
    kernel /= kernel.sum()
    for _ in range(4):
        x = np.convolve(x, kernel, mode="same").astype(np.float32)
    t = np.arange(n) / RATE
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t)
    return (x * envelope).astype(np.float32)


def bench(seconds: float, tail_ms: float, channels: int = 6) -> dict:
    ref = music_reference(seconds)
    ir = room_ir(tail_ms, seed=int(tail_ms))
    rng = np.random.default_rng(7)
    echo = np.convolve(ref, ir, mode="full")[: ref.size].astype(np.float32)
    gains = np.linspace(1.0, 0.5, channels).astype(np.float32)
    mic = np.stack([echo * g for g in gains], axis=1)
    mic += rng.standard_normal(mic.shape).astype(np.float32) * 0.0005

    chain = AecChain(channels=channels, rate=RATE, taps=4096)
    blocks = mic.shape[0] // BLOCK
    t0 = time.perf_counter()
    out = [chain.process(mic[i * BLOCK:(i + 1) * BLOCK], ref[i * BLOCK:(i + 1) * BLOCK])
           for i in range(blocks)]
    elapsed = time.perf_counter() - t0
    out = np.concatenate(out)
    half = len(out) // 2

    def db(x: np.ndarray) -> float:
        return 10 * float(np.log10(max(float(np.mean(np.square(x.astype(np.float64)))), 1e-20)))

    audio_s = out.size / RATE
    return {
        "tail_ms": tail_ms,
        "audio_s": round(audio_s, 1),
        "chosen_channel": chain.selected,
        "erle_whole_db": round(db(mic[: len(out), 0]) - db(out), 1),
        "erle_converged_db": round(db(mic[len(mic) // 2: len(mic) // 2 + len(out) - half, 0])
                                   - db(out[half:]), 1),
        "chain_erle_db": chain.erle_db,
        "cpu_s": round(elapsed, 2),
        "rtf": round(elapsed / audio_s, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="benchmark the echo canceller")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    if not speex_available():
        print("libspeexdsp not found: AEC cannot run here "
              "(brew install speexdsp / apt install libspeexdsp1)")
        return 2

    print(f"chunk {BLOCK} samples, 6 filters, 4096 taps, {args.seconds:.0f} s of audio\n")
    rows = [bench(args.seconds, tail_ms) for tail_ms in (50.0, 200.0)]
    header = f"{'room tail':>10} {'ERLE all':>9} {'ERLE conv':>10} {'chain EMA':>10} {'rtf':>6} {'ch':>3}"
    print(header)
    for r in rows:
        print(f"{r['tail_ms']:>8.0f}ms {r['erle_whole_db']:>8.1f}dB {r['erle_converged_db']:>9.1f}dB "
              f"{str(r['chain_erle_db']):>10} {r['rtf']:>6.3f} {r['chosen_channel']:>3}")
    print("\nerle_whole includes the filter's convergence; the converged column is the "
          "number that matters once a song has been playing for a few seconds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
