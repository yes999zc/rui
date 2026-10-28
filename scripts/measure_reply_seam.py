#!/usr/bin/env python
"""Measure the short-reply TTS seam that `playback_gap_count` cannot see (task-30).

    uv run python scripts/measure_reply_seam.py

Fake ASR/LLM/TTS, real `TurnRunner` streaming/split/TTS path. The TTS fake is shaped
like a vendor: every request is `pad + tone + pad` (80/120/80 ms), so two requests put
160 ms of zero PCM at the seam even though the audio plane never underruns.

Two groups are printed:

* the literal 5-char replies from the report - these are one sentence and already reach
  TTS once (the reported gap, if any, is inside that one rendition); and
* the same replies with the newline the model actually emits, delivered with a 400 ms
  pause mid-reply - this is the shape that used to split into two requests after the
  300 ms short-reply hold expired (backlog d.10 residual risk). The `HEAD` row disables
  the short-reply hold to reproduce the pre-task-30 split; `task-30` runs the current
  policy.

The seam reported per junction is: previous rendition's trailing silence + producer
stall + next rendition's leading silence.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rui.session.turn as turn  # noqa: E402
from rui.config import Config  # noqa: E402
from rui.providers.base import ChatChunk, Transcript  # noqa: E402
from rui.session.turn import TurnRunner  # noqa: E402
from rui.trace import TurnTrace  # noqa: E402

RATE = 16000
PAD_MS = 80
TONE_MS = 60
COMMA_PAUSE_MS = 120
SILENCE_PEAK = 200


class FakeAsr:
    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text="你好")


class SlowLlm:
    def __init__(self, chunks: list[str], delays: list[float]) -> None:
        self.chunks = chunks
        self.delays = delays

    async def chat(self, messages: list[dict], tools: list[dict]):
        for chunk, delay in zip(self.chunks, self.delays, strict=False):
            if delay:
                await asyncio.sleep(delay)
            yield ChatChunk(delta=chunk)


class PaddedTts:
    """Vendor-shaped: head/tail silence on every request, plus a 120 ms tone."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield _silence(PAD_MS)
        yield _tone(TONE_MS)
        if "，" in text or "," in text:
            # The vendor renders its own pause at the comma. It is *inside* one
            # rendition, so it shows up as `inner_ms`, never as a segment seam.
            yield _silence(COMMA_PAUSE_MS)
            yield _tone(TONE_MS)
        yield _silence(PAD_MS)


def _silence(ms: int) -> bytes:
    return np.zeros(RATE * ms // 1000, dtype="<i2").tobytes()


def _tone(ms: int) -> bytes:
    return np.full(RATE * ms // 1000, 1200, dtype="<i2").tobytes()


def _edge_silence_ms(pcm: bytes, *, leading: bool) -> float:
    samples = np.frombuffer(pcm, dtype="<i2")
    frame = RATE // 100  # 10 ms
    if samples.size == 0:
        return 0.0
    frames = [samples[i:i + frame] for i in range(0, samples.size, frame)]
    if not leading:
        frames = frames[::-1]
    total = 0.0
    for block in frames:
        if int(np.max(np.abs(block.astype(np.int32)))) > SILENCE_PEAK:
            break
        total += block.size / RATE * 1000.0
    return total


def _inner_silence_ms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2")
    frame = RATE // 100
    run = best = 0.0
    seen = False
    for start in range(0, samples.size, frame):
        block = samples[start:start + frame]
        silent = int(np.max(np.abs(block.astype(np.int32)))) <= SILENCE_PEAK
        if silent and seen:
            run += block.size / RATE * 1000.0
        elif not silent:
            best = max(best, run)
            run = 0.0
            seen = True
    return best


def _head_split(real_split, buffer, force=False, clauses=True, *, hold_short=False,
                short_wait_ms=0.0):
    """The pre-task-30 release: hold, then SPLIT once the 300 ms cap expires."""
    if (hold_short and not force and turn._short_reply_pending(buffer)
            and short_wait_ms < turn.SHORT_REPLY_HOLD_MS):
        return [], buffer
    return real_split(buffer, force=force, clauses=clauses, hold_short=False)


async def run_case(chunks: list[str], delays: list[float], mode: str):
    tts = PaddedTts()
    turn.asr_chain = lambda providers: [("a", FakeAsr())]
    turn.llm_chain = lambda providers: [("l", SlowLlm(chunks, delays))]
    turn.tts_chain = lambda providers, rate: [("t", tts)]
    old_chars, old_split = turn.SHORT_REPLY_CHARS, turn.split_sentences
    if mode == "HEAD":
        turn.SHORT_REPLY_CHARS = 8
        turn.split_sentences = lambda *a, **kw: _head_split(old_split, *a, **kw)
    events: list[tuple[bytes, bool, float]] = []
    started = time.monotonic()

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        events.append((pcm, segment_start, time.monotonic()))

    trace = TurnTrace(device_id="seam")
    try:
        result = await TurnRunner(Config().providers).run(
            b"\x00\x00" * 800, speak, trace
        )
    finally:
        turn.SHORT_REPLY_CHARS, turn.split_sentences = old_chars, old_split

    segments: list[dict] = []
    for pcm, start, at in events:
        if start or not segments:
            segments.append({"pcm": bytearray(), "start": at})
        segments[-1]["pcm"] += pcm
    stats = []
    for index, segment in enumerate(segments):
        audio = bytes(segment["pcm"])
        seam = None
        gap = 0.0
        if index:
            previous = segments[index - 1]
            previous_ms = len(previous["pcm"]) / 2 / RATE * 1000.0
            gap = max(
                0.0, (segment["start"] - previous["start"]) * 1000.0 - previous_ms
            )
            seam = (
                _edge_silence_ms(bytes(previous["pcm"]), leading=False)
                + gap
                + _edge_silence_ms(audio, leading=True)
            )
        stats.append({
            "audio_ms": len(audio) / 2 / RATE * 1000.0,
            "seam_ms": seam,
            "inner_ms": _inner_silence_ms(audio),
            "gap_ms": gap,
        })
    return {
        "texts": tts.texts,
        "reply": result.reply,
        "trace_count": trace.meta.get("tts_call_count"),
        "first_audio_ms": (events[0][2] - started) * 1000.0 if events else None,
        "segments": stats,
    }


def _shape(chunks: list[str], delays: list[float]) -> str:
    return " | ".join(
        f"{chunk!r}" + (f"+{int(delay * 1000)}ms" if delay else "")
        for chunk, delay in zip(chunks, delays, strict=False)
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold-ms", type=float, default=60.0)
    args = parser.parse_args()
    cases = [
        ("literal 在，你说。", ["在，你说。"], [0.0]),
        ("literal 好，稍等。", ["好，稍等。"], [0.0]),
        ("literal 嗯，好的。", ["嗯，好的。"], [0.0]),
        ("newline 在，\\n你说。 (slow)", ["在，\n", "你说。"], [0.0, 0.4]),
        ("newline 好，\\n稍等。 (slow)", ["好，\n", "稍等。"], [0.0, 0.4]),
        ("newline 嗯，\\n好的。 (slow)", ["嗯，\n", "好的。"], [0.0, 0.4]),
    ]
    print(
        f"vendor-shaped fake: {PAD_MS} ms pad + {TONE_MS} ms tone + "
        f"{COMMA_PAUSE_MS} ms comma pause + {TONE_MS} ms tone + {PAD_MS} ms pad"
    )
    print(f"confirm threshold: seam > {args.threshold_ms:.0f} ms\n")
    failures = 0
    for label, chunks, delays in cases:
        for mode in ("HEAD", "task-30"):
            outcome = await run_case(chunks, delays, mode)
            seams = [
                f"{item['seam_ms']:.0f}" if item["seam_ms"] is not None else "-"
                for item in outcome["segments"]
            ]
            inner = [f"{item['inner_ms']:.0f}" for item in outcome["segments"]]
            audible = [
                item["seam_ms"] for item in outcome["segments"]
                if item["seam_ms"] is not None
            ]
            confirmed = any(value > args.threshold_ms for value in audible)
            if mode == "HEAD" and audible and confirmed:
                failures += 1
            print(
                f"{label:32s} {mode:7s} calls={len(outcome['texts'])} "
                f"trace_count={outcome['trace_count']} "
                f"first_audio_ms={outcome['first_audio_ms']:.0f} "
                f"audio_ms={[round(item['audio_ms']) for item in outcome['segments']]} "
                f"seam_ms=[{', '.join(seams)}] inner_ms=[{', '.join(inner)}] "
                f"seam>{args.threshold_ms:.0f}ms={'YES' if confirmed else 'no'}"
            )
            print(f"    shape: {_shape(chunks, delays)}")
            print(f"    tts texts: {outcome['texts']}")
        print()
    print(f"HEAD split seams confirmed over {args.threshold_ms:.0f} ms: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
