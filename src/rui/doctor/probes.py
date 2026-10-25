"""Hardware probe. Measured where we can measure, labelled when we cannot.

The AEC real-time factor is computed with the real tap count, so it tracks the box.
The per-device CPU budget for KWS + streaming ASR is *not* measured yet (M1), so the
capacity number is reported together with the assumption it rests on rather than as a
fact. A number that silently encodes a guess is worse than no number.
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import socket
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .. import secrets
from ..audio.ack import ack_wav_path
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
        lines = [f"rui doctor  verdict={self.verdict}"]
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


def _llm_key_check(cfg: Config) -> Check | None:
    """Whether the primary LLM has a key, and whether a legacy alias is carrying it.

    The provider itself reads through `secrets.get()`, which now accepts the old
    `DEEPSEEK_API_KEY` name. That fallback must not be silent: an install that reuses an
    old secrets file would answer fine and keep the deprecated name forever.
    """
    chain = list(cfg.providers.llm.chain)
    if not chain:
        return None
    provider = cfg.providers.catalog.get(chain[0])
    if provider is None:
        return None
    env_name = str(getattr(provider, "api_key_env", "") or "")
    if not env_name:
        return Check("llm_key", "warn", "no api_key_env",
                     f"providers.catalog.{chain[0]} has no key variable, so the LLM "
                     "cannot answer")
    legacy = secrets.aliases(env_name)
    resolution = secrets.resolve(env_name)
    if resolution is None:
        also = f" (also tried legacy {', '.join(legacy)})" if legacy else ""
        return Check(
            "llm_key", "warn", f"missing {env_name}{also}",
            "the assistant will not answer: set it in .env or the console's Secrets page",
        )
    if resolution.from_alias:
        return Check(
            "llm_key", "warn",
            f"{env_name} unset; using legacy {resolution.found_as} ({resolution.source})",
            f"copy it to {env_name} and remove {resolution.found_as}: the old name still "
            "works as a fallback",
        )
    return Check("llm_key", "ok", f"{env_name} from {resolution.source}")


_SHERPA_DETAIL_LIMIT = 320
_SHERPA_REQUIRED = ("VoiceActivityDetector", "KeywordSpotter")


def _brief_exc(exc: BaseException, limit: int = _SHERPA_DETAIL_LIMIT) -> str:
    """One line, bounded: an ImportError can carry a multi-kilobyte loader dump."""
    text = " ".join(str(exc).split())
    if not text:
        text = type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sherpa_runtime_check(cfg: Config) -> Check:
    """Whether `sherpa_onnx` really imports, not just whether paths are configured.

    A Debian install can keep the wheel and still fail at import when the C extension
    cannot load `libonnxruntime.so`; the plane then runs with KWS/VAD off and only logs a
    generic "not installed". This check reports the loader's own exception text, so the
    missing library is visible without reading site-packages by hand (issue #2).
    """
    try:
        sherpa = importlib.import_module("sherpa_onnx")
    except ModuleNotFoundError as exc:
        missing = (exc.name or "").split(".")[0]
        if missing in ("", "sherpa_onnx", "sherpa_onnx_core"):
            return Check(
                "wake_runtime", "fail", f"not installed: {_brief_exc(exc)}",
                "KWS/VAD stay off: run `uv sync --extra kws` (or use the Docker image)",
            )
        return Check(
            "wake_runtime", "fail",
            f"installed but failed to load: missing dependency {missing} ({_brief_exc(exc)})",
            "the wheel is present but its dependency is missing; reinstall with "
            "`uv sync --extra kws`",
        )
    except (ImportError, OSError) as exc:
        return Check(
            "wake_runtime", "fail", f"installed but failed to load: {_brief_exc(exc)}",
            "the wheel is present but its C extension/onnxruntime cannot load; see "
            "docs/runbook.md §14 Linux bare-metal (issue #2), or deploy with Docker",
        )
    except Exception as exc:  # a half-installed native extension can raise anything
        return Check(
            "wake_runtime", "fail", f"installed but failed to load: {_brief_exc(exc)}",
            "the wheel is present but its C extension is broken; reinstall the extra",
        )

    version = str(getattr(sherpa, "__version__", "") or "unknown")
    absent = [name for name in _SHERPA_REQUIRED if not hasattr(sherpa, name)]
    if absent:
        return Check(
            "wake_runtime", "fail",
            f"sherpa-onnx {version} imported but lacks {', '.join(absent)}",
            "the Python package is incomplete; reinstall with `uv sync --extra kws`",
        )

    voice = cfg.audio.voice
    model = str(voice.vad_model_path or "")
    loaded = False
    if model:
        path = Path(model).expanduser()
        # A missing model is the path check's job (a download, not a broken install).
        # Load only when there is a readable file: otherwise doctor would crash on a
        # half-fetched model during install.
        if path.is_file() and os.access(path, os.R_OK):
            try:
                vad_cfg = sherpa.VadModelConfig()
                vad_cfg.silero_vad.model = str(path)
                vad_cfg.sample_rate = voice.rate
                sherpa.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=30)
            except Exception as exc:
                return Check(
                    "wake_runtime", "fail",
                    f"sherpa-onnx {version} imported, but VAD model failed to load: "
                    f"{_brief_exc(exc)}",
                    "check that the file is a valid silero_vad.onnx; re-run "
                    "`rui fetch-models`",
                )
            loaded = True

    detail = f"sherpa-onnx {version} imported"
    if loaded:
        detail += "; silero VAD loaded"
    return Check(
        "wake_runtime", "ok", detail,
        "" if loaded else "VAD/KWS classes present; no readable VAD model to load",
    )


def _models_check(cfg: Config) -> Check:
    """ASR + VAD + KWS model files that are configured and actually on disk.

    dot2 #14: a fresh install has model paths configured by the production template even
    when fetch-models has not run. With no devices, /health stays green, so local ASR
    silently falls back to the cloud and the VAD gate / wake word never start. This row
    is the degraded signal (warn, not fail: a cloud-only install or a fresh wizard boot
    is allowed to be incomplete).
    """
    from ..providers.sherpa_asr import model_paths

    voice = cfg.audio.voice
    present: list[str] = []
    missing: list[str] = []
    checked: list[str] = []
    locations: list[str] = []

    for name in cfg.providers.asr.chain:
        provider = cfg.providers.catalog.get(name)
        if provider is None or str(provider.kind) != "sherpa":
            continue
        checked.append("ASR")
        if not provider.model:
            missing.append("ASR (no model path)")
            break
        model_dir = Path(str(provider.model)).expanduser()
        locations.append(f"ASR {model_dir}")
        onnx, tokens = model_paths(model_dir)
        if onnx.is_file():
            present.append("ASR model")
        else:
            missing.append("ASR model")
        if tokens.is_file():
            present.append("ASR tokens")
        else:
            missing.append("ASR tokens")
        break  # the chain's first local ASR is the one the plane loads

    if voice.vad_model_path:
        checked.append("VAD")
        vad = Path(voice.vad_model_path).expanduser()
        locations.append(f"VAD {vad}")
        if vad.is_file():
            present.append("VAD")
        else:
            missing.append("VAD")

    if voice.kws_enabled:
        checked.append("KWS")
        if not voice.kws_model_dir:
            missing.append("KWS (no model dir)")
        else:
            kws = Path(voice.kws_model_dir).expanduser()
            locations.append(f"KWS {kws}")
            if (kws / "tokens.txt").is_file():
                present.append("KWS")
            else:
                missing.append("KWS")

    if not checked:
        return Check(
            "models", "ok", "no local models configured",
            "cloud ASR only; run 'rui fetch-models' to add local ASR, VAD and the wake model",
        )
    if missing:
        return Check(
            "models", "warn", "missing " + ", ".join(missing),
            "local model readiness: " + "; ".join(locations)
            + "; run 'rui fetch-models' (or re-run deploy/install.sh), then restart",
        )
    return Check(
        "models", "ok", " + ".join(present) + " present",
        "local ASR, the VAD gate and the wake word can start",
    )


def run_doctor(cfg: Config | None = None) -> Report:
    cfg = cfg or Config()
    checks: list[Check] = []
    audio = cfg.audio
    # `doctor` is a one-shot process: unlike serve/audio, nothing else has pointed the
    # secret store at this config's data_dir yet.
    secrets.configure(cfg.system.data_dir)

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
                        "" if docker else (
                            "none is expected inside the rui image (it ships no docker CLI);"
                            " install Docker on the host only for the one-command deploy")))

    if not cfg.web.auth_token and cfg.web.host not in {"127.0.0.1", "localhost", "::1"}:
        checks.append(Check("web_auth", "warn", f"{cfg.web.host}:{cfg.web.port} without a token",
                            "anyone on the LAN can read the console; set web.auth_token"))

    key_check = _llm_key_check(cfg)
    if key_check is not None:
        checks.append(key_check)

    cap = audio.capture
    checks.append(Check(
        "capture_layout",
        "ok",
        f"{cap.rate} Hz requested / {cap.channels} ch / S{cap.bits}_LE on the device "
        f"(mic {cap.mic_channels} + ref {cap.ref_channels}, >>{cap.mic_shift}/>>{cap.ref_shift})",
        "hardware runs 48000 Hz; verify with: cat /proc/asound/card*/pcm*/sub0/hw_params",
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

    # --- the wake path: a missing model or an unwritable data volume makes S5a inert
    # while every number elsewhere still looks healthy. Runtime first: a configured
    # path says nothing about whether `import sherpa_onnx` works (issue #2).
    checks.append(_sherpa_runtime_check(cfg))
    gate_on = bool(audio.voice.vad_model_path)
    if not gate_on and (cfg.ack.mode != "none" or audio.voice.wake_preroll_ms > 0):
        checks.append(Check(
            "wake_path", "warn", "no vad_model_path",
            "the turn gate never starts, so the wake cue, the pre-wake replay and the "
            "gate metrics are inert; run `rui fetch-models`",
        ))
    elif gate_on:
        model = Path(audio.voice.vad_model_path).expanduser()
        if not model.is_file():
            checks.append(Check(
                "wake_path", "warn", f"vad_model_path not found: {model}",
                "a configured path is not a model on disk: the turn gate never starts; "
                "run `rui fetch-models`",
            ))
        elif not os.access(model, os.R_OK):
            checks.append(Check(
                "wake_path", "warn", f"vad_model_path not readable: {model}",
                "the service user cannot read the model; fix permissions or re-run "
                "`rui fetch-models`",
            ))
        else:
            checks.append(Check("wake_path", "ok", f"preroll {audio.voice.wake_preroll_ms} ms",
                                f"ack mode {cfg.ack.mode}; silero {model}"))
    else:
        checks.append(Check("wake_path", "ok", f"preroll {audio.voice.wake_preroll_ms} ms",
                            f"ack mode {cfg.ack.mode}"))
    # dot2 #14: with no devices /health is green even with the models missing, so the
    # doctor has to name the degradation (cloud ASR, no gate, no wake word).
    checks.append(_models_check(cfg))
    if cfg.ack.mode == "tts":
        path = ack_wav_path(cfg)
        if not path.is_file():
            checks.append(Check("ack_wav", "warn", str(path),
                                "not rendered yet: the plane plays a tone until it exists"))
        else:
            try:
                with wave.open(str(path)) as w:
                    ms = w.getnframes() * 1000.0 / w.getframerate()
            except (OSError, wave.Error) as exc:
                checks.append(Check("ack_wav", "warn", str(path), f"unreadable: {exc}"))
            else:
                status = "ok" if ms <= cfg.ack.max_ms else "warn"
                checks.append(Check("ack_wav", status, f"{ms:.0f} ms", 
                                    "" if status == "ok" else
                                    f"over the {cfg.ack.max_ms} ms cap: the plane plays a tone"))

    for dev in cfg.devices:
        if not dev.enabled:
            continue
        # Adapters probe the device over SSH (arch, /proc/asound); the audio path itself
        # is dialed by the device. Reaching 22 is what `rui doctor` can verify.
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
