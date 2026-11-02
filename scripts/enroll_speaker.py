#!/usr/bin/env python3
"""One-shot speaker enrollment through the device's own microphone.

Why this exists
---------------
Enrolling a voiceprint took four failed attempts on 2026-11-02 (phone recordings,
timed windows, "similarity looks high" false positives). The only flow that
works is a fixed 10 s device-side capture with a beep cue and a volume squash,
because the TTS acknowledgement otherwise lands inside the sample. That sequence
is scripted here so it is not re-derived by hand under time pressure.

Flow (do not reorder)
---------------------
1. ``POST /api/v2/speakers/enroll`` (placeholder name, 10 s) and *simultaneously*
   play ``beep2.wav`` so the person knows when to start talking.
2. At 1.7 s squash the device volume to 1 (the ack TTS is a big distorted blob
   in the far field recording).
3. When the recording finishes, copy ``enroll/<id>.wav`` out and restore volume.
4. Drop the beep (first 1.5 s) plus leading/trailing silence; require enough
   speech left, otherwise abort instead of committing a noise profile.
5. Arm a second 1 s session under the real name, overwrite its wav and sidecar
   with the clean sample (``commit`` needs ``speech_ms >= min_speech_ms``), commit.
6. Restart the container so the planes load the new profile, restore the
   volume the room had before (a restart applies ``devices[].volume`` instead).

The device file is short-lived (30 s TTL) and the commit path re-reads the wav,
so everything here is: get in, get the audio out, get out.

Usage (on the NAS host, where /vol1/rui is reachable)
-----------------------------------------------------
    python3 enroll_speaker.py --name 妈妈 --device living
    python3 enroll_speaker.py --name 妈妈 --device living --dry-run   # capture only

``--dry-run`` stops after the trimmed sample is written to ``--out`` so it can be
checked before it is committed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from array import array
from pathlib import Path

DEFAULT_HOST = "http://localhost:8080"
DEFAULT_TOKEN = "WqA9hzgVOdxl6-TcDMbPhrlRy2pTk6Jd"  # console token, local only
DATA_DIR = Path("/vol1/rui/data")          # host path == container /var/lib/rui/data
BEEP = "/var/lib/rui/data/beep2.wav"       # two 700 Hz beeps, 0.81 s
CONTAINER = "rui"

BEEP_CUT_S = 1.5      # the cue itself lives in the first ~1.5 s
VOLUME_AT_S = 1.7     # squash after the beep has audibly finished
FRAME_MS = 50
MIN_SPEECH_S = 2.0    # below this the profile is not worth committing
GOOD_SPEECH_S = 6.0   # the two profiles that passed on 2026-11-02 had 8.4 / 8.5 s


# --------------------------------------------------------------------------- http
def _call(method: str, path: str, body: dict | None = None, *, host: str, token: str,
          timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(host + path, data=data, method=method)
    req.add_header("X-Auth-Token", token)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def api(method: str, path: str, body: dict | None = None, **kw) -> dict:
    status, payload = _call(method, path, body, **kw)
    if status >= 400:
        raise SystemExit(f"ERROR {method} {path} -> {status}: {json.dumps(payload, ensure_ascii=False)}")
    return payload


# ------------------------------------------------------------------------- volume
def get_volume(host: str, token: str, device: str) -> int:
    body = api("GET", f"/api/v1/device/volume?device={device}", host=host, token=token)
    return int(body.get("volume") or 0)


def set_volume(value: int, host: str, token: str, device: str) -> None:
    api("PUT", "/api/v1/device/volume", {"value": int(value), "device": device},
        host=host, token=token)


# --------------------------------------------------------------------------- wav
def read_mono(path: Path) -> tuple[array, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SystemExit(f"ERROR {path}: expected mono 16-bit, got "
                             f"{wav.getnchannels()}ch/{8 * wav.getsampwidth()}bit")
        rate = wav.getframerate()
        samples = array("h")
        samples.frombytes(wav.readframes(wav.getnframes()))
    return samples, rate


def write_mono(path: Path, samples: array, rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def frame_rms(samples: array, rate: int, frame_ms: int = FRAME_MS) -> list[float]:
    step = max(1, rate * frame_ms // 1000)
    out = []
    for start in range(0, len(samples) - step + 1, step):
        block = samples[start:start + step]
        out.append((sum(float(s) * s for s in block) / len(block)) ** 0.5 / 32768.0)
    return out


def trim_speech(samples: array, rate: int, *, cut_s: float = BEEP_CUT_S) -> tuple[array, float]:
    """Drop the cue and the dead air; return (samples, speech_seconds).

    Threshold is relative to the recording's own noise floor: fixed thresholds
    failed on both quiet rooms (cut the speech) and noisy ones (kept the room).
    """
    start = min(len(samples), int(cut_s * rate))
    body = samples[start:]
    rms = frame_rms(body, rate)
    if not rms:
        return array("h"), 0.0
    ordered = sorted(rms)
    floor = ordered[len(ordered) // 10]
    peak = ordered[-1]
    thr = max(0.008, floor * 2.5, peak * 0.12)
    idx = [i for i, value in enumerate(rms) if value >= thr]
    if not idx:
        return array("h"), 0.0
    step = max(1, rate * FRAME_MS // 1000)
    pad = int(0.12 * rate)
    begin = max(0, idx[0] * step - pad)
    end = min(len(body), (idx[-1] + 1) * step + pad)
    kept = body[begin:end]
    return kept, len(kept) / float(rate)


# -------------------------------------------------------------------------- steps
def capture(name: str, device: str, seconds: int, host: str, token: str,
            out: Path) -> tuple[Path, int]:
    before = get_volume(host, token, device)
    print(f"[1/3] volume before capture: {before}")

    started, session = _call("POST", "/api/v2/speakers/enroll",
                             {"name": "_cap", "device_id": device, "seconds": seconds},
                             host=host, token=token)
    if started >= 400:
        raise SystemExit(f"ERROR arm -> {started}: {session}")
    enroll_id = session["enroll_id"]
    print(f"      armed {enroll_id} ({seconds}s) — playing cue now")

    # The cue must be one message behind the arm, not after it: the person starts
    # talking when they hear the second beep, so the two requests race on purpose.
    _call("POST", "/api/v1/debug/play", {"device": device, "file": BEEP}, host=host, token=token)

    t0 = time.monotonic()
    squashed = False
    while time.monotonic() - t0 < seconds + 6:
        elapsed = time.monotonic() - t0
        if not squashed and elapsed >= VOLUME_AT_S:
            try:
                set_volume(1, host, token, device)
                squashed = True
                print(f"      {elapsed:.1f}s volume -> 1 (ack TTS muted)")
            except SystemExit as exc:  # a busy mixer must not kill the capture
                print(f"      volume squash failed (continuing): {exc}")
        side = DATA_DIR / "enroll" / f"{enroll_id}.json"
        wav = DATA_DIR / "enroll" / f"{enroll_id}.wav"
        if side.is_file() and wav.is_file():
            shutil.copyfile(wav, out)
            break
        time.sleep(0.15)
    else:
        raise SystemExit("ERROR recording never completed (no sidecar within the window)")

    dur = 0.0
    try:
        dur = json.loads((DATA_DIR / "enroll" / f"{enroll_id}.json").read_text()).get("ms", 0) / 1000.0
    except Exception:  # noqa: BLE001 - informational only
        pass
    print(f"      captured {out} ({dur:.1f}s)")

    try:
        set_volume(before, host, token, device)
        print(f"      volume restored to {before}")
    except SystemExit as exc:
        print(f"      WARNING volume not restored: {exc}")
    return out, before


def commit(name: str, device: str, host: str, token: str, sample_wav: Path,
           speech_s: float) -> dict:
    seconds = 1
    for attempt in range(5):
        status, session = _call("POST", "/api/v2/speakers/enroll",
                                {"name": name, "device_id": device, "seconds": seconds},
                                host=host, token=token)
        if status < 400:
            break
        wait = 2.0 + attempt
        print(f"      arm for {name} -> {status}; retry in {wait:.0f}s (a session is still open)")
        time.sleep(wait)
    else:
        raise SystemExit("ERROR could not arm a commit session (device busy)")

    enroll_id = session["enroll_id"]
    time.sleep(seconds + 1.5)  # let the plane finish and write its own pair
    target_wav = DATA_DIR / "enroll" / f"{enroll_id}.wav"
    target_side = DATA_DIR / "enroll" / f"{enroll_id}.json"
    shutil.copyfile(sample_wav, target_wav)
    payload = {
        "enroll_id": enroll_id,
        "device_id": device,
        "path": str(target_wav),
        "ms": int(round(speech_s * 1000)),
        "speech_ms": int(round(speech_s * 1000)),
    }
    target_side.write_text(json.dumps(payload))
    print(f"      sample planted as {enroll_id} ({speech_s:.2f}s speech)")

    result = api("POST", f"/api/v2/speakers/enroll/{enroll_id}/commit", host=host, token=token)
    return result


def restart_and_restore(volume: int, device: str, host: str, token: str) -> None:
    print(f"[3/3] restarting {CONTAINER} so the planes reload the library")
    subprocess.run(["docker", "restart", CONTAINER], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            health = api("GET", "/health", host=host, token=token)
            if health.get("status") == "ok" and health.get("speaker_connected"):
                break
        except SystemExit:
            pass
        time.sleep(2)
    else:
        print("      WARNING health did not come back in 120 s")
        return
    print(f"      healthy: {health.get('version')} devices={health.get('devices')} "
          f"errors={health.get('device_errors')}")
    try:
        current = get_volume(host, token, device)
        if current != volume:
            set_volume(volume, host, token, device)
            print(f"      volume {current} -> {volume} (restart applied the config value)")
    except SystemExit as exc:
        print(f"      WARNING volume not restored: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Enroll one speaker through a device's mic")
    ap.add_argument("--name", required=True, help="profile name, e.g. 妈妈")
    ap.add_argument("--device", default="living", help="room id (default living)")
    ap.add_argument("--seconds", type=int, default=10, help="capture window (default 10)")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--token", default=DEFAULT_TOKEN)
    ap.add_argument("--out", default="/tmp/enroll-sample.wav")
    ap.add_argument("--dry-run", action="store_true", help="capture + trim only, do not commit")
    ap.add_argument("--no-restart", action="store_true", help="skip the container restart")
    args = ap.parse_args()

    print(f"== enrolling {args.name} on {args.device} ==")
    raw = Path("/tmp/enroll-raw.wav")
    capture(args.name, args.device, args.seconds, args.host, args.token, raw)
    print("[2/3] trimming cue + silence")
    samples, rate = read_mono(raw)
    kept, speech_s = trim_speech(samples, rate)
    if speech_s < MIN_SPEECH_S:
        raise SystemExit(f"ERROR only {speech_s:.2f}s of speech after trimming "
                         f"(need >= {MIN_SPEECH_S:.1f}s) — record again, do not commit this")
    out = Path(args.out)
    write_mono(out, kept, rate)
    print(f"      clean sample {out}: {speech_s:.2f}s speech @ {rate} Hz")
    if speech_s < GOOD_SPEECH_S:
        print(f"      NOTE only {speech_s:.1f}s of speech; the profiles that verified well "
              f"today were 8.4-8.5 s. Re-record if the person can speak longer.")

    if args.dry_run:
        print("dry run: nothing committed. Listen to the sample, then re-run without --dry-run.")
        return 0

    result = commit(args.name, args.device, args.host, args.token, out, speech_s)
    print(f"      commit: {json.dumps(result, ensure_ascii=False)[:400]}")

    if args.no_restart:
        print("skipped restart: run `docker restart rui` yourself before verifying")
    else:
        volume = get_volume(args.host, args.token, args.device)
        restart_and_restore(volume, args.device, args.host, args.token)

    print("\nNow verify from 3 m: say 你好小宝, then read the log tail for")
    print("  `wake admitted: speaker score=… best=…`  (>= 0.70 is solid, 0.55 is the floor)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
