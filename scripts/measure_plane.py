#!/usr/bin/env python
"""Measure the audio plane: run it for N seconds and print what it actually did.

    uv run python scripts/measure_plane.py --config config.yaml --device living --seconds 300

Every S-step number in the plan comes from this script, so a claim about jitter, drops
or rtf can be reproduced by anyone with the same config and the same device.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from house_ear.audio.client import AudioPlaneError, AudioPlaneSupervisor  # noqa: E402
from house_ear.config import load_config  # noqa: E402


async def measure(config: Path, device: str, seconds: float, interval: float) -> dict:
    cfg = load_config(config)
    supervisor = AudioPlaneSupervisor(cfg, config_path=config, device_id=device)
    await supervisor.start()
    samples: list[dict] = []
    try:
        elapsed = 0.0
        while elapsed < seconds:
            await asyncio.sleep(min(interval, seconds - elapsed))
            elapsed += min(interval, seconds - elapsed)
            if supervisor.client is None:
                break
            metrics = await supervisor.client.request_metrics()
            if metrics is None:
                continue
            row = metrics.model_dump()
            row["voice_frames"] = supervisor.client.voice_frames
            samples.append(row)
            print(
                f"t={row['uptime_s']:6.1f}s  in={row['frames_in']:6d}  out={row['frames_out']:6d}"
                f"  drop={row['frames_dropped']:4d}  jitter p50={row['jitter_p50_ms']:6.2f}"
                f" p99={row['jitter_p99_ms']:7.2f}  rtf={row['rtf']:.4f}",
                flush=True,
            )
    finally:
        await supervisor.stop()

    final = samples[-1] if samples else {}
    return {
        "device": device,
        "seconds": seconds,
        "final": final,
        "samples": len(samples),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="measure the house-ear audio plane")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = asyncio.run(measure(Path(args.config), args.device, args.seconds, args.interval))
    except AudioPlaneError as exc:
        print(f"audio plane failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    final = result["final"]
    if not final:
        print("no metrics received", file=sys.stderr)
        return 1
    print("\nsummary")
    for key in ("frames_in", "frames_out", "frames_dropped", "jitter_p50_ms", "jitter_p99_ms", "cpu_s", "audio_s", "rtf"):
        print(f"  {key:16} {final.get(key)}")
    ok = final.get("rtf", 1.0) <= 0.5 and final.get("frames_dropped", 1) == 0
    print(f"  verdict          {'ok' if ok else 'failed'} (rtf<=0.5, no drops)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
