#!/usr/bin/env python
"""Run one real turn through the three providers and print the stage table.

    uv run python scripts/smoke_turn.py --config cfg.yaml \
        --wav tests/fixtures/command-weather.wav --out /tmp/reply.wav

No speaker, no microphone: this exercises ASR -> LLM -> TTS exactly as the control plane
does, with the reply PCM written to a wav so the audio can be listened to. It is the
cheapest way to tell whether a slow turn is the vendor, the network or our code.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.config import load_config  # noqa: E402
from house_ear.session.turn import TurnRunner  # noqa: E402
from house_ear.trace import TurnTrace  # noqa: E402


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs without overwriting what is already in the environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def run(config: Path, wav: Path, out: Path, prompt: str = "") -> int:
    cfg = load_config(config)
    rate = cfg.audio.voice.rate
    with wave.open(str(wav)) as w:
        if w.getframerate() != rate:
            print(f"warning: {wav} is {w.getframerate()} Hz, the pipeline runs at {rate}")
        pcm = w.readframes(w.getnframes())

    spoken: list[bytes] = []

    async def speak(chunk: bytes) -> None:
        spoken.append(chunk)

    runner = TurnRunner(cfg.providers, sample_rate=rate,
                        **({"system_prompt": prompt} if prompt else {}))
    trace = TurnTrace(device_id="smoke")
    started = time.perf_counter()
    trace.mark("wake")
    result = await runner.run(pcm, speak, trace)
    elapsed = (time.perf_counter() - started) * 1000

    audio = b"".join(spoken)
    if audio:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(audio)

    print(f"transcript : {result.transcript!r}")
    print(f"reply      : {result.reply!r}")
    if result.error:
        print(f"error      : {result.error}")
    for stage in ("asr", "llm", "tts"):
        report = result.stages.get(stage)
        if report is None:
            continue
        tries = ", ".join(
            f"{a['provider']}{'(failed)' if a['error'] else ''}" for a in report.attempts
        )
        print(f"{stage:>3}        : used {report.used} [{tries}] {report.elapsed_ms:.0f} ms")
    marks = {k: v for k, v in trace.to_dict()["stages_ms"].items()}
    print(f"marks      : {marks}")
    print(f"first reply audio at {trace.first_response_ms} ms "
          f"({len(audio) / 2 / rate:.2f} s of speech), whole turn {elapsed:.0f} ms")
    print(f"reply wav  : {out}")
    return 0 if result.reply else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="one real turn through ASR/LLM/TTS")
    parser.add_argument("--config", required=True)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--out", default="/tmp/turn-reply.wav")
    parser.add_argument("--env", default=os.environ.get("HOUSE_EAR_ENV_FILE", ".env"))
    parser.add_argument("--prompt", default="", help="override the spoken-reply system prompt")
    args = parser.parse_args()
    load_env_file(Path(args.env))
    return asyncio.run(run(Path(args.config), Path(args.wav), Path(args.out), args.prompt))


if __name__ == "__main__":
    raise SystemExit(main())
