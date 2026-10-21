"""Hardware probe. Static facts plus a measured audio-DSP load, not a guess."""
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
    rtf: float = 0.0                   # real-time factor for the AEC load
    cpu_share: float = 0.0             # percent of one core at full load
    device_capacity: int = 0           # how many speakers this box can run
    measured_at: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "rtf": round(self.rtf, 3),
            "cpu_share": round(self.cpu_share, 1),
            "device_capacity": self.device_capacity,
            "measured_at": self.measured_at,
            "checks": [c.__dict__ for c in self.checks],
        }

    def to_text(self) -> str:
        icon = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}
        lines = [f"house-ear doctor  verdict={self.verdict}"]
        for c in self.checks:
            lines.append(f"  [{icon[c.status]:4}] {c.name}: {c.value}" + (f"  -> {c.hint}" if c.hint else ""))
        lines.append(f"  AEC load: rtf={self.rtf:.2f}  ({self.cpu_share:.1f}% of one core)")
        lines.append(f"  device capacity: ~{self.device_capacity} speaker(s)")
        return "\n".join(lines)


def measure_aec_rtf(aec: AecConfig, sample_rate: int = 16000, frame_ms: int = 20, seconds: float = 3.0) -> float:
    """Estimate the multichannel MDF echo-canceller cost with the real tap count.

    Same arithmetic the C library does per block (FFT, one complex multiply-add per
    partition), so the number tracks the hardware rather than the language.
    """
    import numpy as np

    frame = max(1, int(sample_rate * frame_ms / 1000))
    nfft = 2 * frame
    bins = nfft // 2 + 1
    parts = max(1, aec.filter_len // frame)
    blocks = int(seconds * sample_rate / frame)
    weights = np.ones((parts, bins), dtype=np.complex64)
    acc = np.zeros(bins, dtype=np.complex64)
    rng = np.random.default_rng(7)
    frames = rng.standard_normal((parts, frame)).astype(np.float32)

    for b in range(min(20, blocks)):   # warm up: numpy import + FFT plan setup
        x = np.fft.rfft(frames[b % parts], nfft).astype(np.complex64)
        acc[:] = 0
        for k in range(parts):
            acc += weights[k] * x

    t0 = time.perf_counter()
    for b in range(blocks):
        x = np.fft.rfft(frames[b % parts], nfft).astype(np.complex64)
        acc[:] = 0
        for k in range(parts):
            acc += weights[k] * x
    elapsed = time.perf_counter() - t0
    audio_seconds = blocks * frame / sample_rate
    per_channel = elapsed / audio_seconds
    return per_channel * aec.channels if aec.enabled else 0.0


def run_doctor(cfg: Config | None = None) -> Report:
    cfg = cfg or Config()
    checks: list[Check] = []

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

    rtf = measure_aec_rtf(cfg.audio.aec, cfg.audio.sample_rate, cfg.audio.frame_ms)
    cpu_share = rtf * 100
    if rtf <= 0.5:
        status = "ok"
    elif rtf <= 0.9:
        status = "warn"
    else:
        status = "fail"
    checks.append(Check("aec_rtf", status, f"{rtf:.2f}",
                        "" if status == "ok" else "reduce aec.filter_len or run single-mic mode"))

    reachable = 0
    for dev in cfg.devices:
        if not dev.enabled:
            continue
        try:
            with socket.create_connection((dev.host, 4399), timeout=1.0):
                reachable += 1
            checks.append(Check(f"device:{dev.id}", "ok", f"{dev.host}:4399"))
        except OSError as exc:
            checks.append(Check(f"device:{dev.id}", "warn", f"{dev.host}:4399", str(exc)))

    # Measured AEC is cheap (block frequency-domain MDF): the per-device budget is
    # dominated by KWS + streaming ASR overhead, which M1 measures on real hardware.
    per_device = max(0.35, rtf * 2)
    capacity = min(8, max(1, int(cores * 0.8 / per_device)))
    verdict = "ok"
    if any(c.status == "fail" for c in checks):
        verdict = "unsupported"
    elif any(c.status == "warn" for c in checks):
        verdict = "degraded"

    return Report(
        verdict=verdict,
        checks=checks,
        rtf=rtf,
        cpu_share=cpu_share,
        device_capacity=capacity,
        measured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
