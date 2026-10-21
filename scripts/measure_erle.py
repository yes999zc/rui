#!/usr/bin/env python
"""Hardware ERLE test: play a wav at the speaker while recording the array.

    # speaker's client must be pointed at us; see docs/xiaoai-protocol.md
    uv run python scripts/measure_erle.py --config cfg.yaml --device living \
        --file /tmp/he-music-30s.wav --seconds 30 --gain 1.0

One connection does both jobs - playing and recording - because the speaker only accepts
one client. That is also why this script exists instead of reusing debug-play: the number
worth trusting is ERLE computed offline from the same recording, not a live EMA.

Prints per-microphone ERLE, the reference level while playing, clipping, and saves a WAV
of the raw 8-channel capture for later analysis.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.audio.aec import AecChain, speex_available  # noqa: E402
from house_ear.audio.process import read_wav_mono  # noqa: E402
from house_ear.audio.resample import resample  # noqa: E402
from house_ear.config import load_config  # noqa: E402
from house_ear.device import build_adapters  # noqa: E402
from house_ear.device.xiaoai.adapter import XiaoaiAdapter  # noqa: E402

CHUNK_MS = 100


async def play_loop(adapter: XiaoaiAdapter, audio: np.ndarray, rate: int, gain: float) -> None:
    step = rate * CHUNK_MS // 1000
    while True:
        for start in range(0, audio.size, step):
            chunk = np.clip(audio[start:start + step] * gain, -1.0, 1.0)
            await adapter.play(np.round(chunk * 32767.0).astype("<i2").tobytes(),
                               sample_rate=rate, channels=1)
            await asyncio.sleep(CHUNK_MS / 1000)


async def record_and_play(cfg, device: str, wav: Path, seconds: float, gain: float,
                          connect_timeout: float) -> dict:
    adapters = build_adapters(cfg)
    if device in adapters.errors:
        raise SystemExit(adapters.errors[device])
    adapter = adapters.adapters[device]
    if not isinstance(adapter, XiaoaiAdapter):
        raise SystemExit("this measurement needs the xiaoai adapter")
    await adapter.open()
    play_task = None
    frames: list[bytes] = []
    try:
        deadline = time.monotonic() + connect_timeout
        while not adapter.health()["connected"]:
            if time.monotonic() > deadline:
                raise SystemExit("speaker never connected")
            await asyncio.sleep(0.2)
        pcm, wav_rate = read_wav_mono(wav)
        play_rate = cfg.audio.playback.rate
        audio = resample(pcm, wav_rate, play_rate)
        play_task = asyncio.create_task(play_loop(adapter, audio, play_rate, gain))
        await asyncio.sleep(1.0)  # let the player open and the echo path settle

        end = time.monotonic() + seconds
        async for frame in adapter.frames():
            frames.append(frame.pcm)
            if time.monotonic() >= end:
                break
    finally:
        if play_task is not None:
            play_task.cancel()
            try:
                await play_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await adapter.stop_playback()
        except Exception:
            pass
        await adapter.close()

    channels = adapter.capture.channels
    rate = adapter.capture.rate
    data = np.frombuffer(b"".join(frames), dtype="<i2").reshape(-1, channels)
    out = Path(str(wav.with_suffix("")) + "-capture.wav")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())

    result: dict = {
        "seconds": round(data.shape[0] / rate, 2),
        "rate": rate,
        "channels": channels,
        "clipped": int(np.count_nonzero(np.abs(data) >= 32767)),
        "capture_wav": str(out),
        "per_channel": [],
    }
    mic = data[:, :6].astype(np.float32) / 32768.0
    ref = data[:, 6].astype(np.float32) / 32768.0

    def db(x: np.ndarray) -> float:
        return round(20 * float(np.log10(max(float(np.sqrt(np.mean(x.astype(np.float64) ** 2))), 1e-9))), 1)

    result["ref_dbfs"] = db(ref)
    result["ref_peak_dbfs"] = round(20 * float(np.log10(max(float(np.abs(ref).max()), 1e-9))), 1)

    if not speex_available():
        result["error"] = "libspeexdsp missing: no ERLE computed"
        return result

    block = max(1, rate * 20 // 1000)
    for ch in range(min(6, mic.shape[1])):
        chain = AecChain(channels=1, rate=rate, taps=4096)
        residual = []
        for i in range(0, mic.shape[0] - block, block):
            residual.append(chain.process(mic[i:i + block, ch:ch + 1], ref[i:i + block]))
        res = np.concatenate(residual) if residual else np.zeros(1, dtype=np.float32)
        raw = mic[: res.size, ch]
        tail = slice(len(res) // 2, len(res))
        merged = chain.erle_db
        chain.close()
        result["per_channel"].append({
            "ch": ch,
            "raw_dbfs": db(raw),
            "residual_dbfs": db(res),
            "erle_all_db": round(db(raw) - db(res), 1),
            "erle_second_half_db": round(db(raw[tail]) - db(res[tail]), 1),
            "chain_erle_db": merged,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="measure ERLE on the real speaker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--connect-timeout", type=float, default=90.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    result = asyncio.run(record_and_play(cfg, args.device, Path(args.file), args.seconds,
                                        args.gain, args.connect_timeout))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"captured {result['seconds']} s at {result['rate']} Hz, {result['channels']} ch, "
          f"clipped {result['clipped']}")
    print(f"reference: rms {result.get('ref_dbfs')} dBFS, peak {result.get('ref_peak_dbfs')} dBFS")
    if "error" in result:
        print(result["error"])
        return 1
    print(f"{'ch':>3} {'raw':>8} {'residual':>9} {'ERLE all':>9} {'ERLE 2nd':>9} {'EMA':>6}")
    for row in result["per_channel"]:
        print(f"{row['ch']:>3} {row['raw_dbfs']:>7.1f} {row['residual_dbfs']:>9.1f} "
              f"{row['erle_all_db']:>8.1f} {row['erle_second_half_db']:>8.1f} "
              f"{str(row['chain_erle_db']):>6}")
    print(f"capture: {result['capture_wav']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
