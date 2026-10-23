#!/usr/bin/env python
"""Run N spoken rounds at a live speaker and record what each one cost.

    uv run python scripts/measure_turns.py --url http://127.0.0.1:8091 \
        --wake tests/fixtures/wake-nihao-xiaorui.wav \
        --command-dir /tmp/house-ear-scratch/house-ear-s4/commands --rounds 20

The wake word and the request are played *from this machine*, not from the speaker: the
speaker's own playback is the AEC reference, so anything it says about itself is cancelled
before KWS sees it. One round = wake wav, a short gap, a command wav, then wait for the
reply to finish and read the trace the control plane recorded for that turn.

Writes one JSON line per round and a summary table, so a bad round can be re-read later
instead of remembered.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx


def play(path: Path) -> None:
    subprocess.run(["afplay", str(path)], check=False)


def metrics(client: httpx.Client, url: str) -> dict:
    return client.get(f"{url}/api/v1/audio").json().get("metrics", {})


def last_trace(client: httpx.Client, url: str) -> dict:
    turns = client.get(f"{url}/api/v1/traces", params={"limit": 1}).json().get("turns", [])
    return turns[0] if turns else {}


def last_turn(client: httpx.Client, url: str) -> dict:
    return client.get(f"{url}/api/v1/turns").json()


def wait_reply_done(client: httpx.Client, url: str, timeout: float = 40.0) -> None:
    """Wait until playback stops growing for 1.5 s, or the timeout passes."""
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last = None
    while time.monotonic() < deadline:
        played = metrics(client, url).get("playback_s", 0.0)
        if last is not None and played == last:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since > 1.5:
                return
        else:
            stable_since = None
        last = played
        time.sleep(0.25)


def run(args: argparse.Namespace) -> int:
    commands = sorted(Path(args.command_dir).glob("*.wav"))
    if not commands:
        print(f"no command wavs in {args.command_dir}", file=sys.stderr)
        return 2
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    with httpx.Client(timeout=15.0) as client, out_path.open("w", encoding="utf-8") as fh:
        base = metrics(client, args.url)
        if not base.get("speaker_connected"):
            print("speaker is not connected", file=sys.stderr)
            return 2
        print(f"start: wakes={base['wake_count']} utterances={base['utterances']} "
              f"frames_in={base['frames_in']}", flush=True)

        for i in range(1, args.rounds + 1):
            command = commands[(i - 1) % len(commands)]
            before = metrics(client, args.url)
            started = time.monotonic()
            play(Path(args.wake))
            time.sleep(args.gap)
            play(command)

            woke = False
            spoken = False
            deadline = time.monotonic() + 25.0
            while time.monotonic() < deadline:
                now = metrics(client, args.url)
                woke = woke or now["wake_count"] > before["wake_count"]
                if now["utterances"] > before["utterances"]:
                    spoken = True
                    break
                time.sleep(0.2)
            wait_reply_done(client, args.url)
            trace = last_trace(client, args.url)
            turn = last_turn(client, args.url)
            row = {
                "round": i,
                "command": command.name,
                "woke": woke,
                "utterance": spoken,
                "first_response_ms": trace.get("first_response_ms"),
                "stages_ms": trace.get("stages_ms", {}),
                "transcript": turn.get("transcript", ""),
                "reply": turn.get("reply", ""),
                "error": turn.get("error", ""),
                "providers": {k: v.get("used") for k, v in (turn.get("stages") or {}).items()},
                "wall_s": round(time.monotonic() - started, 1),
            }
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"{i:2d} wake={int(woke)} utt={int(spoken)} "
                  f"first_response={row['first_response_ms']} ms "
                  f"asr={row['stages_ms'].get('asr_done')} "
                  f"llm={row['stages_ms'].get('llm_first')} "
                  f"tts={row['stages_ms'].get('tts_first')} "
                  f"| {row['transcript']} -> {row['reply'][:24]}", flush=True)
            time.sleep(args.between)

    wakes = sum(1 for r in rows if r["woke"])
    spoken = sum(1 for r in rows if r["utterance"])
    firsts = [r["first_response_ms"] for r in rows if r["first_response_ms"]]
    print("\n--- summary ---")
    print(f"rounds {len(rows)}, woke {wakes}/{len(rows)}, utterances {spoken}/{len(rows)}")
    if firsts:
        print(f"first response: p50 {statistics.median(firsts):.0f} ms, "
              f"min {min(firsts):.0f}, max {max(firsts):.0f}, n={len(firsts)}")
    errors = [r for r in rows if r["error"]]
    if errors:
        print(f"errors: {[e['error'] for e in errors]}")
    print(f"json lines: {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="N spoken rounds against a live speaker")
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--wake", default="tests/fixtures/wake-nihao-xiaorui.wav")
    parser.add_argument("--command-dir", required=True)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--gap", type=float, default=0.6, help="silence between wake and request")
    parser.add_argument("--between", type=float, default=1.5, help="silence between rounds")
    parser.add_argument("--out", default="/tmp/house-ear-turns.jsonl")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
