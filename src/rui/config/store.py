"""Atomic config writes with backups; never rewrite a file in place."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML

from ..env import get as env_get
from .schema import (
    CONFIG_VERSION,
    AckConfig,
    AudioConfig,
    Config,
    ConfigError,
    MultiDeviceConfig,
    config_warnings,
)

log = logging.getLogger(__name__)

_yaml = YAML()
_yaml.default_flow_style = False


def default_config_path() -> Path:
    configured = env_get("RUI_CONFIG")
    if configured:
        return Path(configured)
    return Path.home() / ".config" / "rui" / "config.yaml"


def migrate(raw: dict) -> dict:
    """Upgrade an older on-disk config in memory. Returns a dict for the current schema.

    v1 -> v2: the flat audio block is split into capture/voice/playback, and the three
    provider blocks become catalog entries behind a one-name chain. A v1 file is never
    rewritten by this function; it is only interpreted, so a downgrade keeps working
    until the first write.
    """
    version = int(raw.get("version") or 1)
    if version >= CONFIG_VERSION:
        return raw
    if version == 2:
        # v3 only adds optional fields. A v2 file is read as-is, never rewritten: a
        # rollback to a 0.4.x image must keep working until a v3-only field is used.
        return raw
    if version != 1:
        raise ConfigError(f"no migration path from config version {version}")

    old_audio = dict(raw.get("audio") or {})
    old_aec = dict(old_audio.get("aec") or {})
    mic_channels = int(old_aec.get("channels") or 6)
    ref_channels = 2

    audio = {
        "capture": {
            "rate": 48000,
            "channels": mic_channels + ref_channels,
            "mic_channels": mic_channels,
            "ref_channels": ref_channels,
            "bits": 32,
            "mic_shift": int(old_aec.get("mic_shift", 8)),
            "ref_shift": int(old_aec.get("ref_shift", 13)),
        },
        "voice": {
            "rate": int(old_audio.get("sample_rate", 16000)),
            "frame_ms": int(old_audio.get("frame_ms", 20)),
            "vad_min_speech_ms": int(old_audio.get("vad_min_speech_ms", 200)),
            "vad_end_silence_ms": int(old_audio.get("vad_end_silence_ms", 600)),
            "barge_in": bool(old_audio.get("barge_in", True)),
        },
        "playback": {"rate": 48000, "channels": 2},
        "aec": {
            "enabled": bool(old_aec.get("enabled", True)),
            "filter_len": int(old_aec.get("filter_len", 4096)),
        },
    }

    old_providers = dict(raw.get("providers") or {})
    catalog: dict[str, dict] = {}
    stages: dict[str, dict] = {}
    for stage in ("asr", "tts", "llm"):
        block = dict(old_providers.get(stage) or {})
        fallback = str(block.pop("fallback", "") or "")
        # v1 called this "name of another provider block"; there were no named blocks,
        # so a non-empty value only survives if it names one of the three v1 stages.
        chain = [stage]
        if fallback and fallback != stage and fallback in old_providers:
            chain.append(fallback)
            fb = dict(old_providers.get(fallback) or {})
            fb.pop("fallback", None)
            catalog.setdefault(fallback, fb)
        catalog.setdefault(stage, block)
        stages[stage] = {"chain": chain}

    if not old_providers:
        catalog, stages = {}, {}

    raw = dict(raw)
    raw["version"] = 2
    raw["audio"] = audio
    if old_providers:
        raw["providers"] = {"catalog": catalog, **stages}
    else:
        raw.pop("providers", None)
    log.warning("config migrated from version 1 to 2 in memory; save it to persist")
    return raw


def load_config(path: str | Path) -> Config:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {p}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as ConfigError on purpose
        raise ConfigError(f"cannot read {p}: {exc}") from exc
    # A truncated write leaves a 0-byte file. Treating that as "use defaults" hides
    # the failure and boots the service with an empty auth token; raise instead so
    # load_with_recovery can fall back to last-good/defaults.
    if not text.strip():
        raise ConfigError(
            f"config file is empty: {p} (0 bytes or whitespace only); "
            "restore the last-good config or schema defaults"
        )
    try:
        raw = _yaml.load(text)
    except Exception as exc:  # noqa: BLE001 - surfaced as ConfigError on purpose
        raise ConfigError(f"cannot parse {p}: {exc}") from exc
    if raw is None:
        raise ConfigError(
            f"config file contains no YAML document (null): {p}; "
            "restore the last-good config or schema defaults"
        )
    try:
        cfg = Config.model_validate(migrate(dict(raw)))
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
    for warning in config_warnings(cfg):
        log.warning("config: %s", warning)
    return cfg


def _uses_v3(cfg: Config) -> bool:
    """True when the config relies on a v3-only field (K5, multi_device, overrides).

    `audio.aec.who=controller` is the v2 behaviour, so the default value alone does
    not force an upgrade. Everything else that cannot be expressed in v2 does.
    """
    if cfg.audio.aec.who != "controller":
        return True
    if cfg.multi_device != MultiDeviceConfig():
        return True
    return any(dev.audio is not None for dev in cfg.devices)


_AUDIO_BLOCKS = ("capture", "voice", "playback", "aec")


def _persisted_layers(path: Path) -> tuple[set[str] | None, bool]:
    """The merge-layer fields the existing file states itself.

    Returns the dotted `audio.*` fields plus whether the file states
    `ack.echo_ignore_ms`. `None` for the audio set means "no usable previous file".

    `save_config` uses this to keep "not configured" distinct from "configured at the
    default value". Since 0.6 a device plug's manifest `audio_defaults` is the bottom
    layer of the audio and cue-window merges; writing the neutral core defaults into
    every saved file would turn them into explicit choices and shadow the plug on the
    next boot (an LX06 would come back as 1-channel audio after any console save).
    """
    try:
        raw = _yaml.load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken/absent file is not a write error
        return None, False
    if not isinstance(raw, dict):
        return None, False
    audio = raw.get("audio")
    present: set[str] = set()
    if isinstance(audio, dict):
        for name in _AUDIO_BLOCKS:
            block = audio.get(name)
            if isinstance(block, dict):
                present.update(f"{name}.{key}" for key in block)
    elif audio is not None:
        return None, False
    ack = raw.get("ack")
    return present, isinstance(ack, dict) and "echo_ignore_ms" in ack


def _audio_for_disk(audio: AudioConfig, previous: set[str] | None) -> dict[str, dict]:
    """The `audio` mapping to persist: what the file already states or what changed.

    A field is written when the previous file already had it (an explicit choice stays
    explicit) or when it differs from the neutral core default (an operator/code change).
    Fields that are merely at the core default drop out, so the device manifest keeps
    supplying the layout the config never chose.
    """
    full = audio.model_dump(mode="json")
    out: dict[str, dict] = {}
    for name in _AUDIO_BLOCKS:
        block = getattr(audio, name)
        core = type(block)().model_dump(mode="json")
        part = {
            key: value
            for key, value in full[name].items()
            if (previous is not None and f"{name}.{key}" in previous)
            or value != core.get(key)
        }
        if part:
            out[name] = part
    return out


def save_config(path: str | Path, cfg: Config, keep_backups: int = 5) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = p.with_suffix(p.suffix + f".bak-{stamp}")
        shutil.copy2(p, backup)
        # config.yaml can name secrets and device tokens; a backup must not be more
        # readable than the file it copies.
        os.chmod(backup, 0o600)
        backups = sorted(p.parent.glob(p.name + ".bak-*"))
        for old in backups[:-keep_backups]:
            old.unlink(missing_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    # Create the temp file private before a single byte is written: an aborted write
    # must never leave a world-readable partial config behind.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(tmp, 0o600)          # chmod before the first byte, umask-proof
        data = cfg.model_dump(mode="json")
        # Audio (and the cue window) are the blocks with a layer below the config (the
        # device manifest's audio_defaults), so their core defaults must not be
        # materialized on every write.
        previous_audio, previous_ack_window = _persisted_layers(p)
        audio_data = _audio_for_disk(cfg.audio, previous_audio)
        if audio_data:
            data["audio"] = audio_data
        else:
            data.pop("audio", None)
        ack_data = data.get("ack")
        if (
            isinstance(ack_data, dict)
            and not previous_ack_window
            and cfg.ack.echo_ignore_ms == AckConfig().echo_ignore_ms
        ):
            ack_data.pop("echo_ignore_ms", None)
        # An older file only becomes v3 once a v3-only field is written, so a config
        # that still parses as v2 stays readable by a 0.4.x image after a rollback.
        if data.get("version", 0) < CONFIG_VERSION and _uses_v3(cfg):
            data["version"] = CONFIG_VERSION
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _yaml.dump(data, fh)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, p)  # atomic on POSIX and Windows
    os.chmod(p, 0o600)


def redacted(cfg: Config) -> dict:
    """Config safe for the browser: no tokens, no keys, only their presence.

    Presence is answered from both sources - the environment and the write-only secrets
    file - because a key typed into the console lives in the file, and a page that says
    "not configured" right after saving one would be lying.
    """
    from .. import secrets  # local import: secrets already imports this module's writer

    data = cfg.model_dump(mode="json")
    token = data.get("web", {}).pop("auth_token", "")
    data["web"]["auth_required"] = bool(token)
    for block in data.get("providers", {}).get("catalog", {}).values():
        env_name = block.get("api_key_env") or ""
        if env_name:
            block["has_key"] = bool(env_get(env_name)) or secrets.has(env_name)
    for dev in data.get("devices", []):
        env_name = dev.get("token_env") or ""
        dev["has_device_token"] = bool(env_name) and (
            bool(env_get(env_name)) or secrets.has(env_name)
        )
    # The search tool has its own key slot, and it is the one a person actually types in.
    search = (data.get("tools") or {}).get("search") or {}
    if search.get("api_key_env"):
        search["has_key"] = bool(env_get(search["api_key_env"])) or secrets.has(
            search["api_key_env"]
        )
    return data
