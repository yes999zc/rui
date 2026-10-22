#!/usr/bin/env python
"""Measure TTS first-byte latency, one synthesis per round, no speaker and no files.

    uv run python scripts/measure_tts_latency.py --config real-device.yaml --rounds 10

The old build reported 0.44-0.60 s to first byte for 13-23 character sentences; this build
has one 861 ms sample. Ten sentences through the configured chain give the p50/p95 that can
be compared with that number.

Only the configured TTS provider is called: no microphone, no device, no playback, no file
is written. The chain is built exactly as the turn pipeline builds it (`tts_chain`), so a
fallback is exercised the same way and the row says which provider actually spoke.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.config import load_config  # noqa: E402
from house_ear.log_setup import setup_logging  # noqa: E402
from house_ear.providers.base import TtsProvider  # noqa: E402
from house_ear.providers.chain import tts_chain  # noqa: E402

log = logging.getLogger("measure_tts_latency")

# 8-30 characters, mixed short/medium, and one sentence with English proper nouns. They are
# ordinary things a person says to the speaker, not tongue twisters: the number should
# describe the sentences the assistant actually synthesises.
DEFAULT_TEXTS = (
    "今天天气怎么样呀？",
    "帮我把客厅的灯关掉。",
    "现在几点了，小瑞？",
    "帮我定一个明天早上七点的闹钟。",
    "播放一首周杰伦的《安静》。",
    "帮我打开 GitHub 上的 house-ear 仓库。",
    "帮我查一下从沈阳到北京的高铁。",
    "冰箱里还有没有牛奶？",
    "请用一句话解释什么是回声消除。",
    "提醒我晚上九点给妈妈打个电话。",
)


@dataclass
class Sample:
    index: int
    text: str
    first_ms: float
    total_ms: float
    audio_bytes: int
    provider: str
    note: str = ""


def load_env_file(path: Path) -> None:
    """Provider keys live in ~/.hermes/.env; a harness must not depend on the caller's shell."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile, the same definition numpy uses by default."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def parse_texts(raw: str) -> list[str]:
    """Split --texts on ASCII commas; empty means the built-in list."""
    texts = [text.strip() for text in raw.split(",") if text.strip()] if raw else []
    if not texts:
        return list(DEFAULT_TEXTS)
    for text in texts:
        if not 8 <= len(text) <= 30:
            print(f"warning: {text!r} is {len(text)} chars, outside the 8-30 band",
                  file=sys.stderr)
    return texts


async def measure_once(index: int, text: str, chain: list[tuple[str, TtsProvider]]) -> Sample:
    """Synthesise one sentence and time the first non-empty chunk.

    The clock starts before the first provider, so a failed primary's timeout is part of the
    latency the user would have felt. If audio starts and the vendor then fails mid-sentence,
    the first-byte sample is still valid and the row carries the error as a note.
    """
    started = time.perf_counter()
    failures: list[str] = []
    for name, provider in chain:
        first_ms: float | None = None
        last_ms: float | None = None
        audio_bytes = 0
        try:
            async with contextlib.aclosing(provider.synthesize(text, "")) as stream:
                async for chunk in stream:
                    if not chunk:
                        continue
                    now = (time.perf_counter() - started) * 1000
                    if first_ms is None:
                        first_ms = now
                    last_ms = now
                    audio_bytes += len(chunk)
        except Exception as exc:  # noqa: BLE001 - the fallback chain exists for exactly this
            log.warning("tts: %s failed: %s", name, exc)
            note = f"{name}: {type(exc).__name__}: {exc}"
            if first_ms is None or last_ms is None:
                failures.append(note)
                continue
            return Sample(index, text, first_ms, last_ms, audio_bytes, name, note)
        if first_ms is None or last_ms is None:
            log.warning("tts: %s produced no audio", name)
            failures.append(f"{name}: produced no audio")
            continue
        return Sample(index, text, first_ms, last_ms, audio_bytes, name)
    raise RuntimeError("; ".join(failures) or "empty TTS chain")


async def run(args: argparse.Namespace) -> int:
    load_env_file(Path(args.env))
    cfg = load_config(Path(args.config))
    setup_logging(cfg.system.log_level)
    rate = cfg.audio.voice.rate
    try:
        chain = tts_chain(cfg.providers, rate)
    except Exception as exc:  # noqa: BLE001 - a broken chain cannot produce a measurement
        print(f"ERROR: cannot build the TTS chain: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    texts = parse_texts(args.texts)
    if len(texts) > args.rounds:
        print(f"warning: only the first {args.rounds} of {len(texts)} texts fit in "
              f"--rounds {args.rounds}; raise --rounds to measure them all", file=sys.stderr)

    names = " -> ".join(name for name, _ in chain)
    print(f"config    : {args.config}")
    print(f"tts chain : {names} (sample rate {rate} Hz)")
    print(f"texts     : {len(texts)}, rounds {args.rounds}"
          f"{' (cycling)' if len(texts) < args.rounds else ''}")
    print(f"{'#':>3} {'chars':>5} {'first':>7} {'total':>7} {'bytes':>8}  "
          f"{'provider':<16} text", flush=True)

    samples: list[Sample] = []
    for i in range(1, args.rounds + 1):
        text = texts[(i - 1) % len(texts)]
        try:
            sample = await measure_once(i, text, chain)
        except Exception as exc:  # noqa: BLE001 - one bad sentence must not stop the run
            print(f"{i:3d} {len(text):5d} {'-':>7} {'-':>7} {'-':>8}  "
                  f"{'-':<16} {text}", flush=True)
            print(f"    !! {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            continue
        samples.append(sample)
        print(f"{i:3d} {len(text):5d} {sample.first_ms:7.0f} {sample.total_ms:7.0f} "
              f"{sample.audio_bytes:8d}  {sample.provider:<16} {text}", flush=True)
        if sample.note:
            print(f"    !! {sample.note}", file=sys.stderr, flush=True)

    print("\n--- summary ---")
    if not samples:
        print(f"ERROR: no successful TTS samples in {args.rounds} attempt(s)",
              file=sys.stderr)
        return 2

    firsts = [sample.first_ms for sample in samples]
    totals = [sample.total_ms for sample in samples]
    used: dict[str, int] = {}
    for sample in samples:
        used[sample.provider] = used.get(sample.provider, 0) + 1
    print(f"samples    : {len(samples)}/{args.rounds} ({args.rounds - len(samples)} failed)")
    print("providers  : " + ", ".join(
        f"{name} ({count}/{len(samples)})" for name, count in used.items()))
    print(f"first byte : min {min(firsts):.0f}  p50 {percentile(firsts, 50):.0f}  "
          f"p95 {percentile(firsts, 95):.0f}  max {max(firsts):.0f} ms")
    print(f"total      : min {min(totals):.0f}  p50 {percentile(totals, 50):.0f}  "
          f"p95 {percentile(totals, 95):.0f}  max {max(totals):.0f} ms")
    audio_bytes = sum(sample.audio_bytes for sample in samples)
    print(f"audio      : {audio_bytes} bytes total ({audio_bytes / 2 / rate:.2f} s of PCM)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="measure TTS first-byte latency with no speaker and no files"
    )
    parser.add_argument("--config", required=True, help="config yaml with providers.tts")
    parser.add_argument("--rounds", type=int, default=10,
                        help="number of synthesis requests, default 10; the sentence list "
                             "cycles when shorter")
    parser.add_argument("--texts", default="",
                        help="comma-separated sentences (default: 10 built-in Chinese ones)")
    parser.add_argument("--env", default=str(Path.home() / ".hermes/.env"),
                        help="KEY=VALUE file with provider keys, loaded without overwriting "
                             "the caller's environment")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
