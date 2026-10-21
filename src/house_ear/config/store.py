"""Atomic config writes with backups; never rewrite a file in place."""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML

from .schema import Config, ConfigError

_yaml = YAML()
_yaml.default_flow_style = False


def default_config_path() -> Path:
    env = os.environ.get("HOUSE_EAR_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".config" / "house-ear" / "config.yaml"


def load_config(path: str | Path) -> Config:
    p = Path(path)
    try:
        raw = _yaml.load(p.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"config not found: {p}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as ConfigError on purpose
        raise ConfigError(f"cannot parse {p}: {exc}") from exc
    try:
        return Config.model_validate(raw)
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
    """Config safe for the browser: no tokens, no keys, only their presence."""
    data = cfg.model_dump(mode="json")
    token = data.get("web", {}).pop("auth_token", "")
    data["web"]["auth_required"] = bool(token)
    for block in data.get("providers", {}).values():
        if block.get("api_key_env"):
            block["has_key"] = bool(os.environ.get(block["api_key_env"]))
    return data
