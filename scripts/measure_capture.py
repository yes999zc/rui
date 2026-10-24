#!/usr/bin/env python
"""Record from a speaker through the real adapter and report what actually arrived.

    # real device (the speaker will connect as soon as you point its server.txt at us)
    uv run python scripts/measure_capture.py --config config.yaml --device living --seconds 60

    # desk check with a synthetic speaker: 48 kHz / 8 ch / tone on mic 0, "music" on ref
    uv run python scripts/measure_capture.py --config config.yaml --device living \
        --seconds 5 --fake

Prints per-channel RMS, frame-interval percentiles and frame size, and writes a WAV of
the raw capture - that file is the S3 fixture for AEC and KWS work.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rui.config import Config, load_config  # noqa: E402
from rui.env import get as env_get  # noqa: E402
from rui.device import build_adapters  # noqa: E402
from rui.device.xiaoai import protocol  # noqa: E402
from rui.device.xiaoai.adapter import XiaoaiAdapter  # noqa: E402


async def fake_speaker(port: int, token_env: str, rate: int, channels: int, seconds: float) -> None:
    """A stand-in for the real client: same protocol, known signal."""
    import os

    from websockets.asyncio.client import connect

    token = env_get(token_env)
    uri = f"ws://127.0.0.1:{port}/?token={token}"
    await asyncio.sleep(0.4)  # let the server bind
    async with connect(uri, max_size=None) as ws:
        await ws.recv()  # start_recording
        samples = rate // 50
        seq = 0
        deadline = time.monotonic() + seconds
        rng = np.random.default_rng(3)
        while time.monotonic() < deadline:
            t = (np.arange(samples) + seq * samples) / rate
            block = rng.standard_normal((samples, channels)).astype(np.float32) * 300
            block[:, 0] += 6000 * np.sin(2 * np.pi * 1000 * t)     # speech-ish tone
            block[:, 6:] = 25000 * np.sin(2 * np.pi * 440 * t)[:, None]  # loud "music" ref
            await ws.send(protocol.stream("record", block.astype(np.int16).tobytes()))
            seq += 1
            await asyncio.sleep(0.02)


async def capture(config: Path, device: str, seconds: float, out: Path, fake: bool,
                  connect_timeout: float) -> dict:
    cfg: Config = load_config(config)
    adapters = build_adapters(cfg)
    if device in adapters.errors:
        raise SystemExit(adapters.errors[device])
    adapter = adapters.adapters[device]
    if not isinstance(adapter, XiaoaiAdapter):
        raise SystemExit(f"{device} is not an xiaoai device")
    await adapter.open()

    fake_task = None
    if fake:
        fake_task = asyncio.create_task(
            fake_speaker(adapter.listen_port, adapter.dev.token_env,
                         adapter.capture.rate, adapter.capture.channels, seconds + 1)
        )

    try:
        wait_until = time.monotonic() + connect_timeout
        while not adapter.health()["connected"]:
            if time.monotonic() > wait_until:
                raise SystemExit(f"no speaker connected on :{adapter.listen_port} "
                                 f"within {connect_timeout:.0f}s")
            await asyncio.sleep(0.1)
        print(f"speaker connected: {adapter.capture.rate} Hz / {adapter.capture.channels} ch", flush=True)

        frames: list[bytes] = []
        intervals: list[float] = []
        last = None
        deadline = time.monotonic() + seconds
        async for frame in adapter.frames():
            frames.append(frame.pcm)
            if last is not None:
                intervals.append(frame.ts_ms - last)
            last = frame.ts_ms
            if time.monotonic() >= deadline:
                break
    finally:
        # stop listening first: closing the socket politely beats cancelling the peer
        await adapter.close()
        if fake_task is not None:
            fake_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await fake_task

    channels = adapter.capture.channels
    pcm = b"".join(frames)
    audio = np.frombuffer(pcm, dtype="<i2").reshape(-1, channels).astype(np.float32) / 32768.0
    rms = np.sqrt((audio ** 2).mean(axis=0)) if audio.size else np.zeros(channels)
    peak = np.abs(audio).max(axis=0) if audio.size else np.zeros(channels)
    iv = np.asarray(intervals, dtype=np.float32)

    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(adapter.capture.rate)
        w.writeframes(pcm)

    return {
        "frames": len(frames),
        "samples_per_channel_per_frame": audio.shape[0] // max(1, len(frames)),
        "audio_s": round(audio.shape[0] / adapter.capture.rate, 2),
        "interval_p50_ms": round(float(np.percentile(iv, 50)), 2) if iv.size else None,
        "interval_p99_ms": round(float(np.percentile(iv, 99)), 2) if iv.size else None,
        "channels": [
            {"ch": i, "rms_dbfs": round(20 * float(np.log10(max(r, 1e-9))), 1),
             "peak_dbfs": round(20 * float(np.log10(max(p, 1e-9))), 1)}
            for i, (r, p) in enumerate(zip(rms, peak, strict=True))
        ],
        "clipped_samples": int(np.count_nonzero(np.abs(audio) > 0.999)),
        "health": adapter.health(),
        "wav": str(out),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="capture raw frames through the LX06 adapter")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", default="/tmp/rui-capture.wav")
    parser.add_argument("--fake", action="store_true", help="synthesize a speaker instead of waiting")
    parser.add_argument("--connect-timeout", type=float, default=120.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(capture(Path(args.config), args.device, args.seconds, Path(args.out),
                                 args.fake, args.connect_timeout))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"\ncaptured {result['frames']} frames, {result['audio_s']} s, "
          f"{result['samples_per_channel_per_frame']} samples/ch/frame")
    if result["interval_p50_ms"] is not None:
        print(f"frame interval p50 {result['interval_p50_ms']} ms, p99 {result['interval_p99_ms']} ms")
    print("channel RMS / peak (dBFS):")
    for ch in result["channels"]:
        role = "ref" if ch["ch"] >= 6 else f"mic {ch['ch']}"
        print(f"  ch{ch['ch']} {role:6} {ch['rms_dbfs']:7.1f} / {ch['peak_dbfs']:7.1f}")
    print(f"clipped samples: {result['clipped_samples']}")
    print(f"wav: {result['wav']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
