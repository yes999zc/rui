#!/usr/bin/env python
"""Wake-word check on the real speaker: play music, listen, print every detection.

    # music on, say the wake word a few times
    uv run python scripts/measure_wake.py --config cfg.yaml --device living \
        --music /tmp/he-music-30s.wav --gain 1.0 --seconds 60

    # quiet room, same thing without playback
    uv run python scripts/measure_wake.py --config cfg.yaml --device living --seconds 20

Deliberately not a recall benchmark: the old deployment already wakes reliably at high
volume with these exact parameters, so this is a smoke test - did the wake word come
through the real pipeline, and how long after the phrase did it fire. Every hit is
printed with the elapsed time so the person speaking can line their attempts up.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.audio.client import AudioPlaneError, AudioPlaneSupervisor  # noqa: E402
from house_ear.audio.protocol import PlayFile, StopAudio  # noqa: E402
from house_ear.config import load_config  # noqa: E402


async def run(config: Path, device: str, seconds: float, music: Path | None, gain: float,
              wait_for_speaker: float, save_voice: Path | None) -> int:
    cfg = load_config(config)
    if not cfg.audio.voice.kws_enabled:
        print("kws is disabled in this config (audio.voice.kws_enabled: false)")
        return 2

    supervisor = AudioPlaneSupervisor(cfg, config_path=config, device_id=device)
    try:
        await supervisor.start()
    except AudioPlaneError as exc:
        print(f"audio plane did not start: {exc}")
        return 2
    client = supervisor.client
    assert client is not None
    try:
        deadline = time.monotonic() + wait_for_speaker
        while time.monotonic() < deadline:
            metrics = await client.request_metrics()
            if metrics is not None and metrics.speaker_connected:
                break
            await asyncio.sleep(0.3)
        else:
            print("speaker never connected")
            return 3

        metrics = await client.request_metrics()
        if metrics is not None and not metrics.wake_count and metrics.aec_enabled is False:
            print("note: AEC is off; wake may be harder while music plays")

        if save_voice is not None:
            client.record_voice = True
        if music is not None:
            await client.send(PlayFile(path=str(music), loop=True, gain=gain))
            print(f"playing {music.name} at gain {gain} (loop) - say the wake word now")

        started = time.monotonic()
        seen = 0
        while time.monotonic() - started < seconds:
            await asyncio.sleep(0.2)
            if client.last_wake is not None and client.wakes > seen:
                for _ in range(client.wakes - seen):
                    hit = client.last_wake
                    elapsed = time.monotonic() - started
                    print(f"  [{elapsed:6.1f}s] WAKE {hit.keyword} "
                          f"erle={hit.erle_db} dB ref_active={hit.ref_active}")
                seen = client.wakes

        await client.send(StopAudio())
        metrics = await client.request_metrics()
        if save_voice is not None and client.voice_audio:
            with wave.open(str(save_voice), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(cfg.audio.voice.rate)
                w.writeframes(bytes(client.voice_audio))
            data = np.frombuffer(bytes(client.voice_audio), dtype="<i2").astype(np.float32) / 32768.0
            step = cfg.audio.voice.rate * 5
            levels = [
                round(20 * float(np.log10(max(float(np.sqrt(np.mean(data[i:i + step] ** 2))), 1e-9))), 1)
                for i in range(0, len(data), step)
            ]
            print(f"voice stream saved: {save_voice} ({data.size / cfg.audio.voice.rate:.1f} s)")
            print(f"per-5s rms dBFS: {levels}")
        print(f"\nwakes: {client.wakes} in {seconds:.0f}s")
        if metrics is not None:
            print(f"aec={metrics.aec_enabled} erle={metrics.aec_erle_db} dB "
                  f"ref_active={metrics.ref_active} rtf={metrics.rtf} "
                  f"frames_in={metrics.frames_in} dropped={metrics.frames_dropped}")
        return 0 if client.wakes else 1
    finally:
        await supervisor.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="wake-word smoke test on the speaker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--music", default=None, help="wav to play while listening")
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--wait", type=float, default=60.0)
    parser.add_argument("--save-voice", default=None,
                        help="write the exact stream AEC/KWS saw (debugging)")
    args = parser.parse_args()
    music = Path(args.music) if args.music else None
    voice = Path(args.save_voice) if args.save_voice else None
    return asyncio.run(run(Path(args.config), args.device, args.seconds, music, args.gain,
                           args.wait, voice))


if __name__ == "__main__":
    raise SystemExit(main())
