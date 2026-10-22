"""Atomic config writes with backups; never rewrite a file in place."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML

from .schema import CONFIG_VERSION, Config, ConfigError

log = logging.getLogger(__name__)

_yaml = YAML()
_yaml.default_flow_style = False


def default_config_path() -> Path:
    env = os.environ.get("HOUSE_EAR_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".config" / "house-ear" / "config.yaml"


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
        raw = _yaml.load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {p}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as ConfigError on purpose
        raise ConfigError(f"cannot parse {p}: {exc}") from exc
    try:
        return Config.model_validate(migrate(dict(raw)))
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def save_config(path: str | Path, cfg: Config, keep_backups: int = 5) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak-{stamp}"))
        backups = sorted(p.parent.glob(p.name + ".bak-*"))
        for old in backups[:-keep_backups]:
            old.unlink(missing_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _yaml.dump(cfg.model_dump(mode="json"), fh)
    os.replace(tmp, p)  # atomic on POSIX and Windows


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
            block["has_key"] = bool(os.environ.get(env_name)) or secrets.has(env_name)
    for dev in data.get("devices", []):
        env_name = dev.get("token_env") or ""
        dev["has_device_token"] = bool(env_name) and (
            bool(os.environ.get(env_name)) or secrets.has(env_name)
        )
    # The search tool has its own key slot, and it is the one a person actually types in.
    search = (data.get("tools") or {}).get("search") or {}
    if search.get("api_key_env"):
        search["has_key"] = bool(os.environ.get(search["api_key_env"])) or secrets.has(
            search["api_key_env"]
        )
    return data
