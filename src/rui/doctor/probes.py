"""Hardware probe. Measured where we can measure, labelled when we cannot.

The AEC real-time factor is computed with the real tap count, so it tracks the box.
The per-device CPU budget for KWS + streaming ASR is *not* measured yet (M1), so the
capacity number is reported together with the assumption it rests on rather than as a
fact. A number that silently encodes a guess is worse than no number.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import re
import shutil
import socket
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from .. import secrets
from ..audio.ack import ack_wav_path
from ..config.schema import (
    AecConfig,
    CaptureConfig,
    Config,
    device_audio_defaults,
    effective_audio,
)
from ..env import get as env_get

SUPPORTED_ARCH = {"x86_64", "AMD64", "arm64", "aarch64"}

# One speaker costs this much of one core for KWS + streaming ASR + VAD. Placeholder
# until it is measured on real hardware; the doctor prints it so nobody mistakes it for data.
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


# ---------------------------------------------------------------- 0.5.3 observability
#
# Three rows for the first multi-device production install (0.5.3). Each one names the
# evidence and the command that fixes it, and none of them ever prints a secret: the
# console token is compared, never echoed, and gateway errors are redacted of a
# Telegram bot token before they leave this module.

#: Adapter module constants that declare the rate a plug's wire actually carries. The
#: Dot 2 adapter exposes SPEAKER_RATE = 48000; a core adapter that resamples on its
#: own declares nothing and is reported as not_applicable instead of assumed.
_WIRE_RATE_ATTRS = ("SPEAKER_RATE", "PLAYBACK_RATE", "WIRE_RATE", "OUTPUT_RATE")

# Telegram's own URL shape is /bot<id>:<secret>, so the leading "bot" is a word
# character: anchor on "not a digit" instead of a word boundary.
_TELEGRAM_TOKEN_RE = re.compile(r"(?<!\d)\d{6,}:[A-Za-z0-9_-]{30,}\b")
_LOG_TAIL_BYTES = 64 * 1024


def _redact(text: object, limit: int = 240) -> str:
    """One line, bounded, and never a bot token: the log/exception may carry the URL."""
    line = " ".join(str(text).split())
    line = _TELEGRAM_TOKEN_RE.sub("<telegram-token>", line)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _wire_rate_from_module(module: object) -> tuple[int | None, str]:
    """(Hz, constant name) from an adapter module, or (None, "") when it declares none."""
    for attr in _WIRE_RATE_ATTRS:
        value = getattr(module, attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and 8000 <= value <= 96000:
            return value, attr
    return None, ""


def _wire_rate_from_entrypoint(
    entrypoint: str, source_dir: Path | None = None
) -> tuple[int | None, str]:
    """Read the wire rate a plug's in-process adapter declares as a module constant.

    The manifest's runtime.entrypoint is a string the loader never imports; the trusted
    adapter behind it is imported by the audio plane anyway. Doctor imports the module
    read-only to read a constant - it never calls the entrypoint - and a module that
    cannot be imported simply declares nothing (not_applicable), because a broken plug
    is already reported by the device/plane rows.

    ``source_dir`` is the plug directory: the image copies plugs to a directory on no
    import path (only the image's PYTHONPATH reaches the plug package), and doctor must
    still be able to read the constant. Doctor adds it for the import only.
    """
    module_name = str(entrypoint or "").split(":", 1)[0].strip()
    if not module_name:
        return None, ""
    added: list[str] = []
    if source_dir is not None:
        text = str(source_dir)
        for candidate in (text, str(Path(text).parent)):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
                added.append(candidate)
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - doctor must not fail because a plug cannot import
        return None, ""
    finally:
        for candidate in added:
            try:
                sys.path.remove(candidate)
            except ValueError:  # pragma: no cover - another thread removed it
                pass
    rate, attr = _wire_rate_from_module(module)
    return rate, (f"{module_name}.{attr}" if attr else "")


def _merged_audio(cfg: Config, device: object):
    """This device's merged audio: core < global < manifest hardware facts < override.

    Doctor rows must describe what the plane will run with. The global block alone is
    wrong as soon as a plug declares `audio_defaults` (the neutral core defaults are
    not an array speaker's 8 ch / 48 kHz) or a device sets its own playback rate (the
    0.5.3 D2 bug: the row said 16 kHz while the speaker heard 48 kHz). Since 0.8.0 the
    manifest wins for hardware facts; `capture.rate` stays the negotiated exception.
    """
    return effective_audio(
        cfg.audio,
        getattr(device, "audio", None),
        device_audio_defaults(device, cfg.system.data_dir),
    )


def _declared_wire_rate(cfg: Config, device: object) -> tuple[int | None, str]:
    """(Hz, where the plug declared it) for one configured device."""
    try:
        from ..plugins.resolve import resolve_device_plug

        resolved = resolve_device_plug(device, cfg.system.data_dir)
    except Exception:  # noqa: BLE001 - a missing/broken plug manifest is not a doctor crash
        return None, ""
    if resolved is None:
        return None, ""
    runtime = getattr(resolved.device, "runtime", None)
    return _wire_rate_from_entrypoint(
        str(getattr(runtime, "entrypoint", "") or ""),
        getattr(resolved.plugin, "source_dir", None),
    )


def _audio_rates_check(cfg: Config) -> Check:
    """Every enabled device's declared wire rate against its merged playback rate.

    A downstream plug's wire is 48 kHz mono; the image plug plays 16 kHz mono, so a device without its
    own rate produced a silent speaker (the 0.5.3 D2 bug). Since 0.6 M1 the effective
    rate is the three-layer merge (manifest audio_defaults < global config < device
    override), so a plug's declared playback default counts too - not just a
    devices[].audio override. Both numbers are printed and a mismatch degrades the
    verdict; the hint still names the override as the per-device fix.
    """
    enabled = [(index, device) for index, device in enumerate(cfg.devices)
               if device.enabled]
    if not enabled:
        return Check("audio_rates", "ok", "not_applicable (no enabled device)",
                     "add a speaker in the console's Devices page")
    parts: list[str] = []
    mismatched: list[tuple[int, int]] = []
    declared = 0
    for index, device in enabled:
        effective = _merged_audio(cfg, device).playback.rate
        wire, source = _declared_wire_rate(cfg, device)
        label = device.id + (f" ({device.room})" if device.room else "")
        if wire is None:
            parts.append(f"{label}: no declared wire rate, playback.rate {effective} Hz")
            continue
        declared += 1
        where = f" [{source}]" if source else ""
        if wire == effective:
            parts.append(f"{label}: wire {wire} Hz = playback.rate {effective} Hz{where}")
        else:
            parts.append(f"{label}: wire {wire} Hz != playback.rate {effective} Hz{where}")
            mismatched.append((index, wire))
    if mismatched:
        fixes = "; ".join(
            f"devices[{index}].audio.playback.rate: {wire}"
            for index, wire in mismatched
        )
        return Check("audio_rates", "warn", "mismatch: " + "; ".join(parts),
                     f"set {fixes}, then restart the audio plane (docs/deploy.md 3.1)")
    if not declared:
        return Check("audio_rates", "ok", "not_applicable (no plug declares a wire rate); "
                                            + "; ".join(parts),
                     "the adapter raises at speak time if the rate it receives is wrong")
    return Check("audio_rates", "ok", "; ".join(parts), "")


def _capture_layouts(cfg: Config) -> list[tuple[str, CaptureConfig]]:
    """(device id, merged capture block) for every enabled device (0.6).

    One row cannot describe a heterogeneous fleet, so the doctor lists each device's
    merged layout instead of a single global block: after M1 the core default is
    neutral (16 kHz / 1 ch / 0 ref) and a plug manifest supplies the device's own.
    """
    rows: list[tuple[str, CaptureConfig]] = []
    for device in cfg.devices:
        if not device.enabled:
            continue
        try:
            rows.append((device.id, _merged_audio(cfg, device).capture))
        except Exception:  # noqa: BLE001 - a broken override must not crash doctor
            continue
    return rows


def _channels_config(cfg: Config) -> tuple[Path, dict]:
    """The channels.json the API reads, plus its parsed object when readable."""
    path = Path(cfg.system.data_dir) / "channels.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return path, {}
    return path, data if isinstance(data, dict) else {}


def _last_gateway_error(path: Path) -> str:
    """The newest poll/start failure in the gateway log, or "".

    The supervisor's in-memory last_error only covers start failures; a Telegram 409
    Conflict happens inside the child's poll loop and is only written to this log, so
    doctor reads it as the persistent evidence. The tail is bounded so a long running
    gateway does not make the row slow.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    markers = ("telegram poll:", "start failed", "conflict", "409")
    for line in reversed(text.splitlines()):
        lowered = line.lower()
        if any(marker in lowered for marker in markers):
            # Drop the logging prefix so the row stays one short line; keep the message.
            message = line.split(":", 4)[-1].strip() if ":" in line else line.strip()
            return _redact(message or line)
    return ""


def _channels_check(cfg: Config, gateway_status: dict | None = None) -> Check:
    """Enabled channels plus the gateway's last error (Telegram 409 conflict included).

    gateway_status is the live ChannelSupervisor.status() passed by the console
    process; the CLI has no supervisor, so it falls back to the persistent gateway log.
    """
    path, saved = _channels_config(cfg)
    enabled = [name for name in ("telegram", "weixin")
               if bool((saved.get(name) or {}).get("enabled"))]
    if not enabled:
        return Check("channels", "ok", "not_applicable (no channel enabled)",
                     f"enable Telegram/WeChat on the console's Channels page; state: {path}")
    summary = ", ".join(enabled)
    evidence = ""
    state = ""
    if isinstance(gateway_status, dict):
        if gateway_status.get("last_error"):
            evidence = _redact(gateway_status["last_error"])
        elif gateway_status.get("running"):
            state = f"gateway running pid {gateway_status.get('pid')}"
        else:
            state = "gateway not running"
    log_error = _last_gateway_error(Path(cfg.system.data_dir) / "channels-gateway.log")
    if log_error and (not evidence or evidence == "gateway not running"):
        # The supervisor says "running" while the child's poll loop is failing (409);
        # the log is the evidence the in-memory status cannot carry.
        evidence = log_error
    if evidence:
        return Check("channels", "warn", f"{summary} enabled; {evidence}",
                     "one poller per bot token: a Telegram 409 Conflict means a second "
                     "getUpdates client uses the same token; stop it and restart the "
                     "gateway from the Channels page (docs/deploy.md 7.3)")
    if state == "gateway not running":
        return Check("channels", "warn", f"{summary} enabled; gateway not running",
                     "start it with POST /api/v1/channels/gateway/start or the Channels "
                     "page's Start button; rui-channels is one process per bot")
    detail = f"{summary} enabled; {state}" if state else (
        f"{summary} enabled; gateway status not visible from this process "
        "(run rui doctor through the console/API for the live status)")
    return Check("channels", "ok", detail, "")


def _console_token_candidates(cfg: Config) -> list[Path]:
    """Where a saved console token may live for this install.

    install.sh writes it next to the compose file (host ~/rui/console-token.txt), so
    the container usually cannot see it; the env override and the data dir are here for
    deployments that mount or copy it, and the config's grandparent covers a bare-metal
    ~/rui/config/config.yaml layout.
    """
    candidates: list[Path] = []
    override = env_get("RUI_CONSOLE_TOKEN_FILE")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path(cfg.system.data_dir).expanduser() / "console-token.txt")
    config_env = env_get("RUI_CONFIG")
    if config_env:
        candidates.append(
            Path(config_env).expanduser().resolve().parent.parent / "console-token.txt")
    candidates.append(Path.cwd() / "console-token.txt")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _console_token_check(cfg: Config) -> Check:
    """Does the saved console token still match web.auth_token? Never print either."""
    candidates = _console_token_candidates(cfg)
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        return Check("console_token", "ok", "not_applicable (no console-token.txt found)",
                     "save one with: docker compose exec -T rui rui token > "
                     "console-token.txt && chmod 600 console-token.txt")
    try:
        saved = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return Check("console_token", "warn",
                     f"cannot read {path.name}: {type(exc).__name__}",
                     "fix permissions or refresh it with: docker compose exec -T rui "
                     "rui token > console-token.txt")
    current = str(cfg.web.auth_token or "").strip()
    fix = ("refresh it with: docker compose exec -T rui rui token > console-token.txt "
           "&& chmod 600 console-token.txt")
    if not current:
        return Check("console_token", "warn",
                     f"{path.name} exists but web.auth_token is empty",
                     "the console is unauthenticated; set web.auth_token "
                     "(rui token --rotate) and run " + fix)
    if not saved:
        return Check("console_token", "warn",
                     f"{path.name} is empty; web.auth_token is set", fix)
    if saved == current:
        return Check("console_token", "ok",
                     f"{path.name} matches web.auth_token", "")
    return Check("console_token", "warn",
                 f"{path.name} does NOT match web.auth_token", fix)


def run_doctor(cfg: Config | None = None, *,
               channel_status: dict[str, Any] | None = None) -> Report:
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
    # 0.5.3: the saved token and the live one drifted apart before (rotate vs file), and
    # a second Telegram poller only shows up as a 409 in the gateway log - both are now
    # rows of their own. Evidence only: no token value is ever printed.
    checks.append(_console_token_check(cfg))
    checks.append(_channels_check(cfg, channel_status))

    key_check = _llm_key_check(cfg)
    if key_check is not None:
        checks.append(key_check)

    layouts = _capture_layouts(cfg)
    if layouts:
        cap_mics = max(block.mic_channels for _, block in layouts)
        capture_value = "; ".join(
            f"{label}: {block.rate} Hz / {block.channels} ch / S{block.bits}_LE "
            f"(mic {block.mic_channels} + ref {block.ref_channels}, "
            f">>{block.mic_shift}/>>{block.ref_shift})"
            for label, block in layouts
        )
        capture_hint = (
            "merged per device (manifest audio_defaults < config < devices[].audio); "
            "verify with: cat /proc/asound/card*/pcm*/sub0/hw_params"
        )
    else:
        cap = audio.capture
        cap_mics = cap.mic_channels
        capture_value = (
            f"core default: {cap.rate} Hz / {cap.channels} ch / S{cap.bits}_LE "
            f"(mic {cap.mic_channels} + ref {cap.ref_channels}, "
            f">>{cap.mic_shift}/>>{cap.ref_shift}); add a device to see its layout"
        )
        capture_hint = "no enabled device; this is the neutral core layout"
    checks.append(Check("capture_layout", "ok", capture_value, capture_hint))
    checks.append(Check(
        "playback_layout",
        "ok",
        f"{audio.playback.rate} Hz / {audio.playback.channels} ch, "
        f"queue {audio.playback.queue_ms} ms, fade {audio.playback.fade_out_ms} ms",
        f"barge-in target: silence within {audio.playback.target_silence_ms} ms",
    ))
    # 0.5.3 D2: a device whose wire rate differs from its effective playback rate used
    # to play silently; compare the plug's declared rate with the one that will play.
    checks.append(_audio_rates_check(cfg))

    aec_rtf = measure_aec_rtf(aec=audio.aec, rate=audio.voice.rate, frame_ms=audio.voice.frame_ms,
                              channels=cap_mics)
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

    enabled_devices = [device for device in cfg.devices if device.enabled]
    if len(enabled_devices) >= 2 and not cfg.multi_device.enabled:
        checks.append(Check(
            "multi_device", "warn",
            f"{len(enabled_devices)} enabled devices, multi_device disabled",
            "only the first configured device gets an audio plane; set "
            "multi_device.enabled: true and max_planes >= 2 (see config.example.yaml)",
        ))

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