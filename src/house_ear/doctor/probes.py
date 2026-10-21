"""Hardware probe. Measured where we can measure, labelled when we cannot.

The AEC real-time factor is computed with the real tap count, so it tracks the box.
The per-device CPU budget for KWS + streaming ASR is *not* measured yet (M1), so the
capacity number is reported together with the assumption it rests on rather than as a
fact. A number that silently encodes a guess is worse than no number.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from dataclasses import dataclass, field

import psutil

from ..config.schema import AecConfig, Config

SUPPORTED_ARCH = {"x86_64", "AMD64", "arm64", "aarch64"}

# One speaker costs this much of one core for KWS + streaming ASR + VAD. Placeholder
# until M1 measures it on the LX06; the doctor prints it so nobody mistakes it for data.
ASSUMED_PER_DEVICE_CPU = 0.35
CPU_HEADROOM = 0.8  # leave a fifth of the box for the control plane, OS and bursts


@dataclass
class Check:
    name: str
    status: str          # ok | warn | fail
    value: str
    hint: str = ""


@dataclass
class Report:
    verdict: str                       # ok | degraded | unsupported
    checks: list[Check] = field(default_factory=list)
    aec_rtf: float = 0.0               # measured, AEC only
    cpu_share: float = 0.0             # percent of one core at full load
    device_capacity: int = 0           # speakers this box can run under the stated basis
    capacity_basis: str = ""
    measured_at: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "aec_rtf": round(self.aec_rtf, 3),
            "cpu_share": round(self.cpu_share, 1),
            "device_capacity": self.device_capacity,
            "capacity_basis": self.capacity_basis,
            "measured_at": self.measured_at,
            "checks": [c.__dict__ for c in self.checks],
        }

    def to_text(self) -> str:
        icon = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}
        lines = [f"house-ear doctor  verdict={self.verdict}"]
        for c in self.checks:
            lines.append(f"  [{icon[c.status]:4}] {c.name}: {c.value}" + (f"  -> {c.hint}" if c.hint else ""))
        lines.append(f"  AEC load: rtf={self.aec_rtf:.3f}  ({self.cpu_share:.1f}% of one core)")
        lines.append(f"  device capacity: ~{self.device_capacity} speaker(s)  [{self.capacity_basis}]")
        return "\n".join(lines)


def measure_aec_rtf(
    aec: AecConfig,
    rate: int = 16000,
    frame_ms: int = 20,
    channels: int = 6,
    seconds: float = 3.0,
) -> float:
    """Estimate the multichannel MDF echo-canceller cost with the real tap count.

    Same arithmetic the C library does per block (FFT, one complex multiply-add per
    partition), so the number tracks the hardware rather than the language.

    `rate` must be the rate the filter runs at - the voice rate (16 kHz), not the
    capture rate (48 kHz): taps are counted in samples of the filter's own stream.
    """
    if not aec.enabled:
        return 0.0

    import numpy as np

    frame = max(1, int(rate * frame_ms / 1000))
    nfft = 2 * frame
    bins = nfft // 2 + 1
    parts = max(1, aec.filter_len // frame)
    blocks = max(1, int(seconds * rate / frame))
    weights = np.ones((parts, bins), dtype=np.complex64)
    acc = np.zeros(bins, dtype=np.complex64)
    rng = np.random.default_rng(7)
    frames = rng.standard_normal((parts, frame)).astype(np.float32)

    def one_block(b: int, acc: np.ndarray) -> None:
        # acc is passed in, not captured: `acc += ...` on a closure variable would
        # rebind it locally and raise UnboundLocalError.
        x = np.fft.rfft(frames[b % parts], nfft).astype(np.complex64)
        acc[:] = 0
        for k in range(parts):
            acc += weights[k] * x

    for b in range(min(20, blocks)):   # warm up: numpy import + FFT plan setup
        one_block(b, acc)

    t0 = time.perf_counter()
    for b in range(blocks):
        one_block(b, acc)
    elapsed = time.perf_counter() - t0
    audio_seconds = blocks * frame / rate
    return elapsed / audio_seconds * channels


def run_doctor(cfg: Config | None = None) -> Report:
    cfg = cfg or Config()
    checks: list[Check] = []
    audio = cfg.audio

    arch = platform.machine()
    checks.append(Check("arch", "ok" if arch in SUPPORTED_ARCH else "fail", arch,
                        "" if arch in SUPPORTED_ARCH else "only x86_64/aarch64 are tested"))

    cores = os.cpu_count() or 1
    checks.append(Check("cpu_cores", "ok" if cores >= 4 else "warn", str(cores),
                        "" if cores >= 4 else "under 4 cores: run one speaker, expect contention"))

    ram_gb = psutil.virtual_memory().total / 1024**3
    checks.append(Check("ram_gb", "ok" if ram_gb >= 4 else "warn", f"{ram_gb:.1f}",
                        "" if ram_gb >= 4 else "4 GB is the practical floor"))

    data_dir = cfg.system.data_dir
    try:
        probe_path = os.path.expanduser("~") if not os.path.isdir(data_dir) else data_dir
        free_gb = psutil.disk_usage(probe_path).free / 1024**3
        checks.append(Check("disk_free_gb", "ok" if free_gb >= 2 else "warn", f"{free_gb:.1f}",
                            "" if free_gb >= 2 else "keep 2 GB free for models and logs"))
    except OSError as exc:
        checks.append(Check("disk_free_gb", "warn", "unknown", str(exc)))

    docker = shutil.which("docker") or shutil.which("podman")
    checks.append(Check("container_runtime", "ok" if docker else "warn", docker or "none",
                        "" if docker else "install Docker for the one-command deploy"))

    if not cfg.web.auth_token and cfg.web.host not in {"127.0.0.1", "localhost", "::1"}:
        checks.append(Check("web_auth", "warn", f"{cfg.web.host}:{cfg.web.port} without a token",
                            "anyone on the LAN can read the console; set web.auth_token"))

    cap = audio.capture
    checks.append(Check(
        "capture_layout",
        "ok",
        f"{cap.rate} Hz / {cap.channels} ch / {cap.bits} bit "
        f"(mic {cap.mic_channels} + ref {cap.ref_channels}, >>{cap.mic_shift}/>>{cap.ref_shift})",
        "verify on the device: cat /proc/asound/card*/pcm*/sub0/hw_params",
    ))
    checks.append(Check(
        "playback_layout",
        "ok",
        f"{audio.playback.rate} Hz / {audio.playback.channels} ch, "
        f"queue {audio.playback.queue_ms} ms, fade {audio.playback.fade_out_ms} ms",
        f"barge-in target: silence within {audio.playback.target_silence_ms} ms",
    ))

    aec_rtf = measure_aec_rtf(aec=audio.aec, rate=audio.voice.rate, frame_ms=audio.voice.frame_ms,
                              channels=cap.mic_channels)
    cpu_share = aec_rtf * 100
    if aec_rtf <= 0.5:
        status = "ok"
    elif aec_rtf <= 0.9:
        status = "warn"
    else:
        status = "fail"
    checks.append(Check("aec_rtf", status, f"{aec_rtf:.3f}",
                        "" if status == "ok" else "reduce aec.filter_len or run single-mic mode"))

    for dev in cfg.devices:
        if not dev.enabled:
            continue
        # Adapters probe the device over SSH (arch, /proc/asound); the audio path itself
        # is dialed by the device. Reaching 22 is what `house-ear doctor` can verify.
        try:
            with socket.create_connection((dev.host, 22), timeout=1.0):
                checks.append(Check(f"device:{dev.id}", "ok", f"{dev.host}:22 ssh"))
        except OSError as exc:
            checks.append(Check(f"device:{dev.id}", "warn", f"{dev.host}:22 ssh", str(exc)))

    per_device = max(ASSUMED_PER_DEVICE_CPU, aec_rtf * 2)
    capacity = max(1, int(cores * CPU_HEADROOM / per_device))

    verdict = "ok"
    if any(c.status == "fail" for c in checks):
        verdict = "unsupported"
    elif any(c.status == "warn" for c in checks):
        verdict = "degraded"

    basis = f"assumed {per_device:.2f} core/device, KWS+ASR not measured yet"
    return Report(
        verdict=verdict,
        checks=checks,
        aec_rtf=aec_rtf,
        cpu_share=cpu_share,
        device_capacity=capacity,
        capacity_basis=basis,
        measured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
