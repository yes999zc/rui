#!/usr/bin/env python
"""Measure reply quality: the same question N times, through the real ASR and LLM.

    uv run python scripts/measure_reply_quality.py --config real-device.yaml \
        --command /tmp/rui-scratch/rui-s4/commands/01-weather.wav --rounds 20 \
        --out /tmp/rui-scratch/rui-s5b/baseline-weather.jsonl

Turn quality is not an audio question: the transcript is the same in every round, so what
is being measured is the prompt, the history window and the reply shaping. This harness
therefore stops before TTS unless `--tts` is given, which keeps 20 rounds cheap and makes
the reply text the only thing that can move the numbers.

One `TurnRunner` for all rounds on purpose: history accumulating across rounds is exactly
what turns a repeated question into a repeated answer (`HANDOFF.md` §6: rounds 13+ came
back word for word). A fresh runner per round would hide the bug.

Reported per run:
  distinct      how many of the N replies differ from each other (and the longest run of
                identical replies in a row)
  digits        rounds whose reply contains a number (a weather answer with no tool has no
                way to know one, so this is the fabrication count)
  weather       rounds whose reply states a weather fact at all (晴/雨/雪/多云/阴/度/风力/空气)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rui import secrets  # noqa: E402
from rui.config import load_config  # noqa: E402
from rui.env import get as env_get  # noqa: E402
from rui.session.turn import TurnRunner  # noqa: E402
from rui.tools.info import build_tools  # noqa: E402
from rui.trace import TurnTrace  # noqa: E402

DIGITS = re.compile(r"[0-9０-９]|零|一|二|三|四|五|六|七|八|九|十|两")
WEATHER = re.compile(r"晴|雨|雪|多云|阴天|度|风力|空气|霾|雾")
# Numbers that are part of a stock phrase and not a claim about the world.
DIGIT_EXEMPT = ("一", "两句", "万一")


def load_env_file(path: Path) -> None:
    """Provider keys live in $RUI_ENV_FILE (default: .env); a harness must not depend on the caller's shell."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def has_digits(text: str) -> bool:
    stripped = text
    for phrase in DIGIT_EXEMPT:
        stripped = stripped.replace(phrase, "")
    return bool(DIGITS.search(stripped))


def longest_identical_run(replies: list[str]) -> int:
    best = run = 0
    prev = None
    for reply in replies:
        run = run + 1 if reply == prev else 1
        prev = reply
        best = max(best, run)
    return best


async def run(args: argparse.Namespace) -> int:
    load_env_file(Path(args.env))
    cfg = load_config(Path(args.config))
    rate = cfg.audio.voice.rate
    with wave.open(str(args.command)) as w:
        if w.getframerate() != rate:
            raise SystemExit(f"{args.command} is {w.getframerate()} Hz, expected {rate}")
        pcm = w.readframes(w.getnframes())

    prompt = args.prompt if args.prompt is not None else None
    history_turns = (
        args.history_turns if args.history_turns is not None else cfg.session.history_rounds
    )
    secrets.configure(cfg.system.data_dir)
    tools = build_tools(cfg) if args.tools else []
    runner = TurnRunner(
        cfg.providers,
        sample_rate=rate,
        system_prompt=prompt,
        assistant=cfg.assistant,
        history_turns=history_turns,
        wake_words=[w for d in cfg.devices for w in d.wake_words],
        tools=tools,
    )
    if tools:
        print("tools: " + ", ".join(spec.llm_name for spec, _ in tools), flush=True)
    print(f"prompt: {len(runner.build_prompt())} chars"
          f"{' (override)' if prompt is not None else ' (assembled)'}, "
          f"history_turns={runner.history_turns}, "
          f"tts={'on' if args.tts else 'off'}, rounds={args.rounds}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    with out.open("w", encoding="utf-8") as fh:
        for i in range(1, args.rounds + 1):
            spoken = 0

            async def speak(chunk: bytes) -> None:
                nonlocal spoken
                spoken += len(chunk)

            trace = TurnTrace(device_id="quality")
            started = time.perf_counter()
            trace.mark("wake")
            result = await runner.run(pcm, speak if args.tts else _null_speak, trace)
            elapsed = (time.perf_counter() - started) * 1000
            row = {
                "round": i,
                "transcript": result.transcript,
                "reply": result.reply,
                "chars": len(result.reply),
                "has_digits": has_digits(result.reply),
                "has_weather_claim": bool(WEATHER.search(result.reply)),
                "error": result.error,
                "turn_ms": round(elapsed),
                "llm_first_ms": trace.to_dict()["stages_ms"].get("llm_first"),
                "audio_bytes": spoken,
                "tools_called": result.tools,
            }
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"{i:2d} {row['turn_ms']:5d} ms {row['chars']:3d} chars "
                  f"digits={'Y' if row['has_digits'] else '-'} "
                  f"weather={'Y' if row['has_weather_claim'] else '-'} | {row['reply'][:60]}",
                  flush=True)

    replies = [r["reply"] for r in rows]
    distinct = len(dict.fromkeys(replies))
    print("\n--- summary ---")
    print(f"rounds            : {len(rows)}")
    print(f"non-empty replies : {sum(1 for r in replies if r.strip())}")
    print(f"distinct replies  : {distinct}/{len(rows)} "
          f"({distinct / max(1, len(rows)) * 100:.0f}% different)")
    print(f"longest identical run: {longest_identical_run(replies)}")
    print(f"replies with a number : {sum(1 for r in rows if r['has_digits'])}/{len(rows)}")
    print(f"replies claiming weather: "
          f"{sum(1 for r in rows if r['has_weather_claim'])}/{len(rows)}")
    turns = [r["turn_ms"] for r in rows if r["reply"]]
    if turns:
        print(f"turn ms: p50 {statistics.median(turns):.0f}, max {max(turns)}")
    print(f"rows: {out}")
    return 0


async def _null_speak(chunk: bytes) -> None:  # noqa: ARG001 - TTS off: reply text only
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="reply quality for one repeated question")
    p.add_argument("--config", required=True)
    p.add_argument("--command", required=True, help="one wav, repeated every round")
    p.add_argument("--rounds", type=int, default=20)
    p.add_argument("--history-turns", type=int, default=None,
                   help="history window handed to the model (default: session.history_rounds)")
    p.add_argument("--prompt", default=None, help="override the system prompt")
    p.add_argument("--no-tools", dest="tools", action="store_false",
                   help="run without the information tools (reproduces the S5b baseline)")
    p.add_argument("--tts", action="store_true", help="also synthesise (costs time, no data)")
    p.add_argument("--out", default="/tmp/rui-quality.jsonl")
    p.add_argument("--env", default=env_get("RUI_ENV_FILE", ".env"))
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
