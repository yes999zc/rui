#!/usr/bin/env python
"""Run N rounds of the whole turn pipeline with no microphone and no room.

    uv run python scripts/measure_turn_pipeline.py --config real-device.yaml \
        --wake tests/fixtures/wake-nihao-xiaorui.wav \
        --command /tmp/house-ear-scratch/house-ear-s4/commands/01-weather.wav --rounds 20

A fake speaker dials the real adapter, streams the wake word and then the request as 8 ch
frames at device pace, and the reply comes back as PCM frames. Everything between is real:
AEC, KWS, the turn gate, ASR, LLM, TTS and playback. What it does *not* test is acoustics -
KWS recall over music, echo cancellation in a room - which is what the live device window
is for. Use this when the room or the Mac's audio output is not available, and say so.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
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
from house_ear.log_setup import setup_logging  # noqa: E402
from house_ear.web.api.app import create_app  # noqa: E402


def load_env_file(path: Path) -> None:
    """Provider keys live in $HOUSE_EAR_ENV_FILE (default: .env); a harness must not depend on the caller's shell."""
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


def trim_silence(pcm: bytes, rate: int, thresh_db: float = -45.0) -> bytes:
    """Trim leading/trailing near-silence so two clips can be spoken in one breath.

    Without this the fixtures carry 200-400 ms of digital silence at each edge, the turn
    gate's 300 ms skip eats silence instead of speech, and the first-word-loss bug this
    whole measurement exists for cannot reproduce. A person does not pause there.
    """
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    win = max(1, rate // 100)  # 10 ms
    usable = x.size // win * win
    if usable < win * 4:
        return pcm
    frames_ = x[:usable].reshape(-1, win)
    rms = np.sqrt(np.square(frames_).mean(axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    loud = np.where(db > thresh_db)[0]
    if loud.size == 0:
        return pcm
    start = max(0, (int(loud[0]) - 5) * win)
    end = min(x.size, (int(loud[-1]) + 2) * win)
    return (np.clip(x[start:end], -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


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


async def stream(ws, pcm: bytes, rate: int, block_ms: int, speed: float,
                 watch=None, state: dict | None = None) -> float | None:
    """Send blocks at device pace. `watch()` is polled after each block; the elapsed time
    of the first truthy answer is returned (used to time the KWS hit).

    A send that takes more than a couple of seconds is not a slow network, it is the peer
    not reading: say so out loud instead of hanging the whole run on it.
    """
    started = time.monotonic()
    hit_at: float | None = None
    sent = 0
    for chunk in frames(pcm, block_ms):
        try:
            await asyncio.wait_for(ws.send(protocol.stream("record", to_device_block(chunk))), 2.0)
        except TimeoutError:
            print(f"!! record frame {sent} blocked >2 s: the plane stopped reading", flush=True)
            raise
        sent += 1
        if state is not None:
            state["frames_sent"] = state.get("frames_sent", 0) + 1
        await asyncio.sleep(block_ms / 1000.0 / speed)
        if watch is not None and hit_at is None and watch():
            hit_at = time.monotonic() - started
    return hit_at


async def silence(ws, ms: int, block_ms: int, speed: float = 1.0,
                  state: dict | None = None) -> None:
    for _ in range(max(1, ms // block_ms)):
        await ws.send(protocol.stream("record", to_device_block(np.zeros(block_ms * 16, dtype="<i2"))))
        if state is not None:
            state["frames_sent"] = state.get("frames_sent", 0) + 1
        await asyncio.sleep(block_ms / 1000.0 / speed)


async def _consume(ws, state: dict) -> None:
    """Read the device socket for the whole round, like a real client does.

    Never stop reading early: the plane writes voice frames and cue audio back over this
    socket, and if nobody drains them `writer.drain()` blocks the plane's event loop -
    which shows up much later as "keepalive ping timeout" and looks like a crash.
    """
    try:
        while True:
            raw = await ws.recv()
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                message = None
            if isinstance(message, dict) and "Request" in message:
                command = message["Request"].get("command")
                if command in {"stop_play", "start_play"}:
                    state[command] = state.get(command, 0) + 1
                continue
            try:
                tag, payload = protocol.decode_stream(raw)
            except protocol.StreamError:
                continue
            if tag == "play" and payload:
                state["audio_bytes"] = state.get("audio_bytes", 0) + len(payload)
    except (asyncio.CancelledError, OSError):
        return
    except Exception as exc:  # noqa: BLE001 - a closed socket ends the drain, it is not a failure
        # Say it out loud: a reader that dies quietly leaves the socket filling up, and the
        # next thing you see is the *sender* blocking with no explanation.
        print(f"!! reader stopped: {type(exc).__name__}: {exc}", flush=True)
        return


async def play_round(ws, wake: bytes, command: bytes, rate: int, gap_ms: int, tail_ms: int,
                     wait_s: float, speed: float = 1.0, wake_counter=None,
                     turn_done=None, reader: asyncio.Task | None = None,
                     state: dict | None = None) -> dict:
    started = time.monotonic()
    entry_frames = state.get("frames_sent", 0) if state else 0
    own_reader = reader is None
    if own_reader:
        state = {}
        reader = asyncio.create_task(_consume(ws, state), name="consume")
    else:
        # The caller owns one reader for the whole run: a gap with nobody reading fills the
        # socket, the plane blocks writing the reply into it, and its device reader stops
        # taking frames - which looks like "the gate stopped closing turns" and is really
        # "nobody was listening for 1.3 s between rounds".
        assert state is not None
    baseline = wake_counter() if wake_counter else 0
    watch = (lambda: wake_counter() > baseline) if wake_counter else None
    hit_after_wake = await stream(ws, wake, rate, 60, speed=speed, watch=watch, state=state)
    await silence(ws, gap_ms, 60, speed=speed, state=state)
    # The request is always spoken, hit or no hit yet: a person does not stop talking
    # because the keyword spotter was quick. Skipping it when the hit landed inside the
    # wake clip meant the request never reached the microphone at all, and the gate
    # correctly produced no turn - which read as "only the first round ever lands".
    command_hit = await stream(ws, command, rate, 60, speed=speed, watch=watch, state=state)
    if hit_after_wake is None and command_hit is not None:
        hit_after_wake = command_hit + len(wake) / 2 / rate + gap_ms / 1000.0
    print(f"   [round] wake_clip={len(frames(wake, 60))} gap={max(1, gap_ms // 60)} "
          f"cmd_clip={len(frames(command, 60))} tail={max(1, tail_ms // 60)} "
          f"hit={'yes' if hit_after_wake is not None else 'NO'} "
          f"wake_counter={wake_counter() if wake_counter else '-'}", flush=True)
    if hit_after_wake is not None:
        await silence(ws, tail_ms, 60, speed=speed, state=state)

    # Wait for the turn to land, then give the reply a moment to start arriving.
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        if turn_done is not None and turn_done():
            await asyncio.sleep(0.3)
            break
    if own_reader:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
    print(f"   [sent] this round = {state.get('frames_sent', 0) - entry_frames}", flush=True)
    return {
        "frames_sent": state.get("frames_sent", 0),
        "audio_bytes": state.get("audio_bytes", 0),
        "wall_s": round(time.monotonic() - started, 1),
        # Wall-clock elapsed scaled back to audio time: --speed changes the feed rate, and
        # an unscaled number would silently mean something else at every speed.
        "wake_lag_ms": None if hit_after_wake is None else round(hit_after_wake * 1000 * speed),
    }


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
    if args.ack_mode:
        cfg.ack.mode = args.ack_mode
    if args.ack_text:
        cfg.ack.text = args.ack_text
    if args.preroll_ms is not None:
        cfg.audio.voice.wake_preroll_ms = args.preroll_ms
    if args.gate_preroll_ms is not None:
        cfg.audio.voice.vad_preroll_ms = args.gate_preroll_ms
    if args.wake_skip_ms is not None:
        cfg.audio.voice.wake_skip_ms = args.wake_skip_ms
    if args.tail_ms is None:
        # Silero reports silence later than it happens, so the gate needs roughly twice
        # `vad_end_silence_ms` before it closes a turn. A fixed 1800 only suits the default
        # config and recreated the "every round scores zero with no error" bug once already.
        args.tail_ms = 2 * cfg.audio.voice.vad_end_silence_ms + 600
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
    setup_logging(cfg.system.log_level)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port + 1, log_level="info"))
    serve_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.1)
    supervisor = getattr(app.state, "audio", None)
    print(f"plane: {supervisor.status() if supervisor else 'not started'}", flush=True)
    print(f"ack: mode={cfg.ack.mode} chime={cfg.ack.chime}/{cfg.ack.chime_ms}ms "
          f"cap={cfg.ack.max_ms}ms preroll={cfg.audio.voice.wake_preroll_ms}ms "
          f"speed={args.speed} gap={args.gap_ms}ms", flush=True)
    if args.asr_only:
        # First-word loss is an ASR question, not an LLM one. Stopping after the
        # transcript keeps 20 rounds per configuration cheap enough to sweep gaps.
        runner = app.state.runner
        from house_ear.session.turn import TurnResult

        async def transcribe_only(pcm: bytes, speak, trace) -> TurnResult:  # noqa: ARG001
            result = TurnResult()
            text, report = await runner.transcribe(pcm, trace)
            result.transcript = text
            if report is not None:
                result.stages["asr"] = report
            if not text.strip():
                result.error = "empty transcript"
            return result

        app.state.runner.run = transcribe_only  # type: ignore[method-assign]

    wake = read_pcm(Path(args.wake), rate)
    command = read_pcm(Path(args.command), rate)
    if args.tight:
        wake = trim_silence(wake, rate)
        command = trim_silence(command, rate)
        print(f"tight: wake {len(wake) / 2 / rate:.2f}s, command "
              f"{len(command) / 2 / rate:.2f}s (edges trimmed)", flush=True)
    rows: list[dict] = []
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        ws = await connect_with_retry(f"ws://127.0.0.1:{port}/?token=pipeline-token")
        if True:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert hello["Request"]["command"] == "start_recording", hello
            # One reader for the whole run: a real client never stops reading, and neither
            # does this one (see `play_round`).
            shared: dict = {}
            reader_task = asyncio.create_task(_consume(ws, shared), name="consume")
            with out.open("w", encoding="utf-8") as fh:
                for i in range(1, args.rounds + 1):
                    before = app.state.last_turn
                    audio_before = shared.get("audio_bytes", 0)
                    frames_before = shared.get("frames_sent", 0)
                    round_info = await play_round(
                        ws, wake, command, rate, args.gap_ms, args.tail_ms, args.wait_s,
                        speed=args.speed,
                        wake_counter=(lambda: supervisor.client.wakes)
                        if supervisor is not None and supervisor.client is not None else None,
                        turn_done=(lambda b=before: app.state.last_turn is not b),
                        reader=reader_task, state=shared,
                    )
                    # the turn runs in a task; give it a moment to publish the result
                    deadline = time.monotonic() + args.wait_s
                    while time.monotonic() < deadline and app.state.last_turn is before:
                        await asyncio.sleep(0.2)
                    turn = app.state.last_turn or {}
                    trace = (app.state.trace_log.recent(1) or [{}])[0]
                    metrics = None
                    if supervisor is not None and supervisor.client is not None:
                        metrics = await supervisor.client.request_metrics(timeout_s=1.0)
                    row = {
                        "round": i,
                        "transcript": turn.get("transcript", ""),
                        "ack_plays": getattr(metrics, "ack_plays", None),
                        "ack_mode": getattr(metrics, "ack_mode", None),
                        "ack_error": getattr(metrics, "ack_error", ""),
                        "preroll_replayed_ms": getattr(metrics, "preroll_replayed_ms", None),
                        "utterances": getattr(metrics, "utterances", None),
                        "gate_armed": getattr(metrics, "gate_armed", None),
                        "gate_discarded": getattr(metrics, "gate_discarded", None),
                        "reply": turn.get("reply", ""),
                        "error": turn.get("error", ""),
                        "frames_sent": max(0, round_info["frames_sent"] - frames_before),
                        "client_wakes": getattr(supervisor.client, "wakes", None)
                        if supervisor is not None and supervisor.client is not None else None,
                        "client_voice_frames": getattr(supervisor.client, "voice_frames", None)
                        if supervisor is not None and supervisor.client is not None else None,
                        "client_id": id(supervisor.client)
                        if supervisor is not None and supervisor.client is not None else None,
                        "audio_bytes": max(0, round_info["audio_bytes"] - audio_before),
                        "wall_s": round_info["wall_s"],
                        "wake_lag_ms": round_info["wake_lag_ms"],
                        "providers": {k: (v or {}).get("used") for k, v in (turn.get("stages") or {}).items()},
                        "first_response_ms": trace.get("first_response_ms"),
                        "stages_ms": trace.get("stages_ms", {}),
                    }
                    if round_info["wake_lag_ms"] is None:
                        row["error"] = row.get("error") or "wake word never fired"
                    rows.append(row)
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    play_rate = cfg.audio.playback.rate
                    print(f"{i:2d} {row['first_response_ms']} ms "
                          f"asr={row['stages_ms'].get('asr_done')} "
                          f"llm={row['stages_ms'].get('llm_first')} "
                          f"tts={row['stages_ms'].get('tts_first')} "
                          f"frames={row['frames_sent']} wakes={row['client_wakes']} "
                          f"voice={row['client_voice_frames']} "
                          f"audio={row['audio_bytes'] / 2 / play_rate:.1f}s "
                          f"| {row['transcript']} -> {row['reply'][:20]}", flush=True)
                    await asyncio.sleep(args.between)
    finally:
        task = locals().get("reader_task")
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        server.should_exit = True
        await serve_task

    # The plane's stderr is a pipe nobody drains during the run, so anything it said is
    # still sitting in the buffer. Print the tail: a gate that never closes a turn leaves no
    # other trace, and "it printed nothing" is not a diagnosis.
    supervisor = getattr(app.state, "audio", None)
    proc = getattr(supervisor, "proc", None)
    stderr = getattr(proc, "stderr", None)
    if stderr is not None:
        try:
            raw = await asyncio.wait_for(stderr.read(), timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            raw = b""
        text = raw.decode(errors="replace").strip()
        if text:
            print("\n--- audio plane log (tail) ---")
            print(text[-4000:])

    firsts = [r["first_response_ms"] for r in rows if r["first_response_ms"]]
    print("\n--- summary ---")
    if firsts:
        print(f"first response: p50 {statistics.median(firsts):.0f} ms, min {min(firsts):.0f}, "
              f"max {max(firsts):.0f}, n={len(firsts)}/{len(rows)}")
    if args.asr_only:
        heard = [r for r in rows if r["transcript"].strip()]
        same = [r for r in heard if r["transcript"].strip() == heard[0]["transcript"].strip()]
        print(f"transcripts: {len(heard)}/{len(rows)} non-empty, "
              f"{len(same)} identical to the first one")
        for r in rows:
            print(f"  {r['round']:2d} lag={r['wake_lag_ms']}ms utterances={r['utterances']} armed={r['gate_armed']} "
                  f"discarded={r['gate_discarded']} "
                  f"preroll={r['preroll_replayed_ms']}ms ack={r['ack_mode']} "
                  f"/{r['ack_plays']} | {r['transcript']}")
    dead = [r["round"] for r in rows if r.get("error") == "wake word never fired"]
    if dead:
        print(f"rounds where the wake word never fired: {dead}")
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
    p.add_argument("--gap-ms", type=int, default=500,
                   help="silence between the wake word and the request; 0 = one breath")
    p.add_argument("--speed", type=float, default=1.0,
                   help="frame feed rate; keep 1.0 when measuring ack timing")
    p.add_argument("--ack-mode", choices=["none", "earcon", "tts"], default="")
    p.add_argument("--ack-text", default="")
    p.add_argument("--gate-preroll-ms", type=int, default=None,
                   help="override audio.voice.vad_preroll_ms (the gate's own onset buffer)")
    p.add_argument("--wake-skip-ms", type=int, default=None,
                   help="override audio.voice.wake_skip_ms (wake phrase tail dropped)")
    p.add_argument("--preroll-ms", type=int, default=None,
                   help="override audio.voice.wake_preroll_ms (0 = no replay, the baseline)")
    p.add_argument("--tight", action="store_true",
                   help="trim clip edges so wake+request is one breath (reproduces the loss)")
    p.add_argument("--asr-only", action="store_true",
                   help="stop after transcription (first-word loss, no LLM/TTS cost)")
    # The gate closes on `vad_end_silence_ms` (600) *of VAD-detected* silence, and Silero
    # needs its own min_silence_duration before it reports silence at all: 900 ms of
    # trailing quiet left the turn open, so every round scored 0 with no error anywhere.
    p.add_argument("--tail-ms", type=int, default=None,
                   help="trailing silence per round; default 2*vad_end_silence_ms + 600")
    p.add_argument("--wait-s", type=float, default=25.0)
    p.add_argument("--between", type=float, default=1.0)
    p.add_argument("--out", default="/tmp/house-ear-pipeline.jsonl")
    p.add_argument("--env", default=os.environ.get("HOUSE_EAR_ENV_FILE", ".env"))
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
