#!/usr/bin/env python
"""Run N rounds of the whole turn pipeline with no microphone and no room.

    uv run python scripts/measure_turn_pipeline.py --config real-device.yaml \
        --wake tests/fixtures/wake-nihao-xiaorui.wav \
        --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav --rounds 20

A fake speaker dials the real adapter, streams the wake word and then the request as 8 ch
frames at device pace, and the reply comes back as PCM frames. Everything between is real:
AEC, KWS, the turn gate, ASR, LLM, TTS and playback. What it does *not* test is acoustics -
KWS recall over music, echo cancellation in a room - which is what the live device window
is for. Use this when the room or the Mac's audio output is not available, and say so.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import uvicorn
from websockets.asyncio.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.config import load_config, save_config  # noqa: E402
from house_ear.device.xiaoai import protocol  # noqa: E402
from house_ear.web.api.app import create_app  # noqa: E402


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

TOKEN_ENV = "HOUSE_EAR_DEVICE_TOKEN"


def read_pcm(path: Path, rate: int) -> bytes:
    with wave.open(str(path)) as w:
        if w.getframerate() != rate:
            raise SystemExit(f"{path} is {w.getframerate()} Hz, expected {rate}")
        return w.readframes(w.getnframes())


def frames(pcm: bytes, ms: int = 60) -> list[np.ndarray]:
    """Split mono int16 into device-sized blocks."""
    samples = np.frombuffer(pcm, dtype="<i2")
    step = 16 * ms  # samples per block at 16 kHz
    return [samples[i:i + step] for i in range(0, samples.size, step)]


def to_device_block(mono: np.ndarray, samples: int = 960) -> bytes:
    """One 8 ch frame: the signal on ch0, everything else (incl. the reference) zero.

    Zero reference means the canceller has nothing to subtract, which is exactly the state
    of a person talking to a silent speaker.
    """
    block = np.zeros((samples, 8), dtype="<i2")
    block[: mono.size, 0] = mono[:samples]
    return block.tobytes()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def stream(ws, pcm: bytes, rate: int, block_ms: int, speed: float) -> None:
    for chunk in frames(pcm, block_ms):
        await ws.send(protocol.stream("record", to_device_block(chunk)))
        await asyncio.sleep(block_ms / 1000.0 / speed)


async def silence(ws, ms: int, block_ms: int) -> None:
    for _ in range(max(1, ms // block_ms)):
        await ws.send(protocol.stream("record", to_device_block(np.zeros(block_ms * 16, dtype="<i2"))))
        await asyncio.sleep(block_ms / 1000.0)


async def play_round(ws, wake: bytes, command: bytes, rate: int, gap_ms: int, tail_ms: int,
                     wait_s: float) -> dict:
    started = time.monotonic()
    await stream(ws, wake, rate, 60, speed=3.0)
    await silence(ws, gap_ms, 60)
    await stream(ws, command, rate, 60, speed=3.0)
    await silence(ws, tail_ms, 60)

    played = b""
    deadline = time.monotonic() + wait_s
    idle_since: float | None = None
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except TimeoutError:
            idle_since = idle_since or time.monotonic()
            # the reply arrives in chunks; stop only after it has gone quiet
            if played and time.monotonic() - idle_since > 2.0:
                break
            continue
        idle_since = None
        # Two kinds of traffic on this wire: control Requests (JSON) and stream frames
        # (tag + payload). Only the stream carries audio.
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            message = None
        if isinstance(message, dict) and "Request" in message:
            command = message["Request"].get("command")
            if command == "stop_play":
                break
            continue
        tag, payload = protocol.decode_stream(raw)
        if tag == "play" and payload:
            played += payload
        elif tag == "stop_play":
            break
    return {"audio_bytes": len(played), "wall_s": round(time.monotonic() - started, 1)}


async def connect_with_retry(url: str, timeout_s: float = 20.0):
    """The plane binds its adapter a moment after the control plane is up; wait for it."""
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await connect(url, max_size=None)
        except OSError as exc:
            last = exc
            await asyncio.sleep(0.5)
    raise SystemExit(f"could not reach the adapter at {url}: {last}")


async def run(args: argparse.Namespace) -> int:
    load_env_file(Path(args.env))
    cfg = load_config(Path(args.config))
    rate = cfg.audio.voice.rate
    port = free_port()
    cfg.devices[0].listen_port = port
    os.environ[TOKEN_ENV] = "pipeline-token"

    # The audio plane is a separate process and only reads a config file from disk, so the
    # randomized port has to exist on disk too - not just in this process's object.
    tmp_dir = Path(tempfile.mkdtemp(prefix="house-ear-pipeline-"))
    run_config = tmp_dir / "config.yaml"
    cfg.system.data_dir = str(tmp_dir / "data")
    save_config(run_config, cfg)
    cfg = load_config(run_config)

    app = create_app(cfg, config_path=run_config, start_audio=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port + 1, log_level="info"))
    serve_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.1)
    supervisor = getattr(app.state, "audio", None)
    print(f"plane: {supervisor.status() if supervisor else 'not started'}", flush=True)

    wake = read_pcm(Path(args.wake), rate)
    command = read_pcm(Path(args.command), rate)
    rows: list[dict] = []
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        ws = await connect_with_retry(f"ws://127.0.0.1:{port}/?token=pipeline-token")
        if True:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert hello["Request"]["command"] == "start_recording", hello
            with out.open("w", encoding="utf-8") as fh:
                for i in range(1, args.rounds + 1):
                    before = app.state.last_turn
                    round_info = await play_round(
                        ws, wake, command, rate, args.gap_ms, args.tail_ms, args.wait_s
                    )
                    # the turn runs in a task; give it a moment to publish the result
                    deadline = time.monotonic() + args.wait_s
                    while time.monotonic() < deadline and app.state.last_turn is before:
                        await asyncio.sleep(0.2)
                    turn = app.state.last_turn or {}
                    trace = (app.state.trace_log.recent(1) or [{}])[0]
                    row = {
                        "round": i,
                        "transcript": turn.get("transcript", ""),
                        "reply": turn.get("reply", ""),
                        "error": turn.get("error", ""),
                        "audio_bytes": round_info["audio_bytes"],
                        "wall_s": round_info["wall_s"],
                        "providers": {k: (v or {}).get("used") for k, v in (turn.get("stages") or {}).items()},
                        "first_response_ms": trace.get("first_response_ms"),
                        "stages_ms": trace.get("stages_ms", {}),
                    }
                    rows.append(row)
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"{i:2d} {row['first_response_ms']} ms "
                          f"asr={row['stages_ms'].get('asr_done')} "
                          f"llm={row['stages_ms'].get('llm_first')} "
                          f"tts={row['stages_ms'].get('tts_first')} "
                          f"audio={row['audio_bytes'] / 32000:.1f}s "
                          f"| {row['transcript']} -> {row['reply'][:20]}", flush=True)
                    await asyncio.sleep(args.between)
    finally:
        server.should_exit = True
        await serve_task

    firsts = [r["first_response_ms"] for r in rows if r["first_response_ms"]]
    print("\n--- summary ---")
    if firsts:
        print(f"first response: p50 {statistics.median(firsts):.0f} ms, min {min(firsts):.0f}, "
              f"max {max(firsts):.0f}, n={len(firsts)}/{len(rows)}")
    empty = [r["round"] for r in rows if not r["reply"]]
    if empty:
        print(f"rounds with no reply: {empty}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="whole turn pipeline, fake speaker")
    p.add_argument("--config", required=True)
    p.add_argument("--wake", default="tests/fixtures/wake-nihao-xiaorui.wav")
    p.add_argument("--command", required=True)
    p.add_argument("--rounds", type=int, default=20)
    p.add_argument("--gap-ms", type=int, default=500)
    p.add_argument("--tail-ms", type=int, default=900)
    p.add_argument("--wait-s", type=float, default=25.0)
    p.add_argument("--between", type=float, default=1.0)
    p.add_argument("--out", default="/tmp/house-ear-pipeline.jsonl")
    p.add_argument("--env", default=str(Path.home() / ".hermes/.env"))
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
