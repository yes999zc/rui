#!/usr/bin/env python
"""Capture-path CPU benchmark: 48 kHz / 8 ch / 20 ms frames, before vs after polyphase.

    uv run python scripts/bench_decimate.py            # 10 s of audio, 5 repeats
    uv run python scripts/bench_decimate.py --seconds 30 --repeats 7

Three tables:

1. `Decimator.process` alone, old vs new. `old` is `Decimator._reference_decimate`, the
   verbatim pre-0.8.9 flow kept as the equivalence oracle; `new` is `Decimator.process`.
   Input blocks are the float32 [-1, 1] the filter gets from `_frame_to_voice`.
2. int16 -> float32 conversion, all 8 wire columns vs the 7 the voice path reads.
3. Per-stage cost of the core-side capture path: bytes -> int16, column slice + float32,
   Decimator, AEC (skipped without libspeexdsp), clip + tobytes, and the voice-rate ring
   copies. Everything downstream of `_frame_to_voice` is 16 kHz mono, so the second
   column (a 16 kHz wire) isolates what the capture rate actually changes.

All numbers are `time.process_time()` (pure CPU, no I/O wait); the head-line gate is
`TARGET_CPU_S` seconds per 10 s of audio and scales with `--seconds`.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rui.audio.decimate import Decimator  # noqa: E402
from rui.audio.process import AudioPlane  # noqa: E402
from rui.config import Config  # noqa: E402

SAMPLE_RATE = 48000
VOICE_RATE = 16000
CHANNELS = 8
MIC_CHANNELS = 6
VOICE_CHANNELS = MIC_CHANNELS + 1   # 6 mics + the single AEC reference `_frame_to_voice` reads
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FACTOR = SAMPLE_RATE // VOICE_RATE
TARGET_CPU_S = 0.2          # hard gate per 10 s of audio
PROFILE_PASSES = 5


def make_frames(seconds: float, seed: int = 0) -> list[np.ndarray]:
    """20 ms blocks of 8-channel float32 audio at the capture rate."""
    rng = np.random.default_rng(seed)
    blocks = max(1, int(seconds * SAMPLE_RATE / FRAME_SAMPLES))
    return [
        (rng.standard_normal((FRAME_SAMPLES, CHANNELS)) * 0.1).astype(np.float32)
        for _ in range(blocks)
    ]


def make_pcm_frames(rate: int, seconds: float, seed: int = 1) -> list[bytes]:
    """20 ms blocks of interleaved int16 as the adapter hands them to `_frame_to_voice`."""
    frame_samples = rate * FRAME_MS // 1000
    blocks = max(1, int(seconds * rate / frame_samples))
    rng = np.random.default_rng(seed + rate)
    return [
        rng.integers(-8000, 8000, size=frame_samples * CHANNELS, dtype=np.int16).tobytes()
        for _ in range(blocks)
    ]


def run(method: str, frames: list[np.ndarray]) -> None:
    decimator = Decimator(factor=FACTOR, channels=frames[0].shape[1])
    emit = getattr(decimator, method)
    for block in frames:
        emit(block)


def measure(method: str, frames: list[np.ndarray], repeats: int) -> list[float]:
    times = []
    for _ in range(repeats):
        started = time.process_time()
        run(method, frames)
        times.append(time.process_time() - started)
    return times


def profile(method: str, frames: list[np.ndarray]) -> str:
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(PROFILE_PASSES):
        run(method, frames)
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(10)
    return stream.getvalue()


def best(fn: Callable[[], None], repeats: int) -> float:
    times = []
    for _ in range(repeats):
        started = time.process_time()
        fn()
        times.append(time.process_time() - started)
    return min(times)


def rate_stages(rate: int, seconds: float, repeats: int) -> dict[str, float]:
    """Core-side capture stages for one wire rate, in CPU seconds per `seconds` of audio."""
    pcms = make_pcm_frames(rate, seconds)
    blocks = [np.frombuffer(pcm, dtype="<i2").reshape(-1, CHANNELS) for pcm in pcms]
    factor = rate // VOICE_RATE

    def decode() -> None:
        for pcm in pcms:
            np.frombuffer(pcm, dtype="<i2")

    def convert_8() -> None:
        for block in blocks:
            block.astype(np.float32) / 32768.0

    def convert_7() -> None:
        for block in blocks:
            block[:, :VOICE_CHANNELS].astype(np.float32) / 32768.0

    converted = [block[:, :VOICE_CHANNELS].astype(np.float32) / 32768.0 for block in blocks]

    def decimate() -> None:
        decimator = Decimator(factor=factor, channels=VOICE_CHANNELS)
        for block in converted:
            decimator.process(block)

    voices = []
    decimator = Decimator(factor=factor, channels=VOICE_CHANNELS)
    for block in converted:
        voices.append(decimator.process(block))
    mono = [voice[:, 0] for voice in voices]

    def clip_tobytes() -> None:
        for samples in mono:
            np.clip(samples * 32768.0, -32768, 32767).astype("<i2").tobytes()

    voice_pcm = [
        np.clip(samples * 32768.0, -32768, 32767).astype("<i2").tobytes() for samples in mono
    ]

    cfg = Config()
    cfg.audio.aec.enabled = True
    plane = AudioPlane(cfg, "bench", Path(tempfile.gettempdir()) / "bench.sock")
    plane._build_aec(MIC_CHANNELS, 2)
    mic_blocks = [voice[:, :MIC_CHANNELS] for voice in voices]
    ref_blocks = [voice[:, MIC_CHANNELS] for voice in voices]

    def aec() -> None:
        assert plane.aec is not None
        for mic, ref in zip(mic_blocks, ref_blocks, strict=True):
            plane.aec.process(mic, ref)

    def rings() -> None:
        seq = 0
        for pcm in voice_pcm:
            ts_ms = seq * FRAME_MS
            plane._keep_preroll(pcm, ts_ms)
            plane.ring.push(seq, ts_ms, pcm)
            plane.ring.pop()
            seq += 1

    stages = {
        "bytes -> int16 (frombuffer)": best(decode, repeats),
        "int16 -> float32, 7 of 8 columns": best(convert_7, repeats),
        "Decimator.process": best(decimate, repeats),
        "clip + tobytes": best(clip_tobytes, repeats),
        "voice-rate ring copies (16 kHz mono)": best(rings, repeats),
        "(reference) int16 -> float32, 8 columns": best(convert_8, repeats),
    }
    if plane.aec is not None:
        stages["AEC (speex, 6 mic)"] = best(aec, repeats)
    else:
        stages["AEC (speex, 6 mic)"] = float("nan")
    return stages


def print_stage_table(seconds: float, repeats: int) -> None:
    fast = rate_stages(SAMPLE_RATE, seconds, repeats)
    slow = rate_stages(VOICE_RATE, seconds, repeats)
    print(f"\n--- core-side capture path per stage, {seconds:.2f} s of audio, "
          f"best of {repeats} (pure CPU) ---")
    print(f"{'stage':<46} {'48k wire (f3)':>14} {'16k wire (f1)':>14}")
    for name in fast:
        left, right = fast[name], slow[name]
        skip = np.isnan(left) and np.isnan(right)
        print(f"{name:<46} {'skipped' if skip else f'{left:14.4f}'}"
              f" {'skipped' if skip else f'{right:14.4f}'}")
    total_48 = sum(value for name, value in fast.items()
                   if not name.startswith("(reference)") and not np.isnan(value))
    total_16 = sum(value for name, value in slow.items()
                   if not name.startswith("(reference)") and not np.isnan(value))
    print(f"{'sum (excludes the reference row)':<46} {total_48:>14.4f} {total_16:>14.4f}")
    if np.isnan(fast["AEC (speex, 6 mic)"]):
        print("note: AEC skipped (libspeexdsp not installed)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=10.0, help="audio length per run")
    parser.add_argument("--repeats", type=int, default=5, help="timed repeats per method")
    args = parser.parse_args(argv)

    frames = make_frames(args.seconds)
    audio_s = len(frames) * FRAME_MS / 1000.0
    print(
        f"bench: {len(frames)} frames x {FRAME_SAMPLES} samples x {CHANNELS} ch "
        f"@ {SAMPLE_RATE} Hz ({audio_s:.2f} s of audio), factor={FACTOR}"
    )

    cases = (
        ("_reference_decimate", CHANNELS, "old full-rate (8 ch)"),
        ("process", CHANNELS, "new polyphase (8 ch)"),
        ("process", VOICE_CHANNELS, f"new polyphase ({VOICE_CHANNELS} ch = production)"),
    )
    results: dict[str, list[float]] = {}
    for method, channels, label in cases:
        frames_for = frames
        if channels != CHANNELS:
            # `_frame_to_voice` hands the filter a contiguous slice of 6 mic + 1 ref.
            frames_for = [np.ascontiguousarray(block[:, :channels]) for block in frames]
        results[label] = measure(method, frames_for, args.repeats)

    print()
    print(f"{'implementation':<36} {'median s':>10} {'best s':>10} "
          f"{'median %core':>13} {'best %core':>11}")
    for _, _, label in cases:
        times = results[label]
        median = statistics.median(times)
        print(f"{label:<36} {median:>10.4f} {min(times):>10.4f} "
              f"{100.0 * median / audio_s:>12.1f}% {100.0 * min(times) / audio_s:>10.1f}%")

    old_median = statistics.median(results["old full-rate (8 ch)"])
    new8 = statistics.median(results["new polyphase (8 ch)"])
    new7 = statistics.median(results[f"new polyphase ({VOICE_CHANNELS} ch = production)"])
    print(f"\nspeedup (median, 8 ch): {old_median / new8:.2f}x "
          f"| 7 ch vs old 8 ch: {old_median / new7:.2f}x")

    limit = TARGET_CPU_S * audio_s / 10.0
    gate = "PASS" if max(new8, new7) <= limit else "FAIL"
    print(f"gate: new median {new8:.4f} s (8 ch) / {new7:.4f} s (7 ch) <= {limit:.4f} s "
          f"({TARGET_CPU_S:.2f} s per 10 s of audio, {audio_s:.1f} s run) -> {gate}")

    print_stage_table(audio_s, args.repeats)

    for method, _, label in cases[:2]:
        print(f"\n--- cProfile top 10, {label}, {PROFILE_PASSES} passes ---")
        print(profile(method, frames))

    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
