"""Minimal config recovery: last-known-good + explicit restore.

The console already rejects bad writes and `save_config` keeps rolling backups. This
module covers the remaining case: someone edits `config.yaml` by hand, an upgrade changes
the schema, and the service cannot start. It never overwrites the broken file silently;
it keeps a `.failed-*` copy, starts from `config.last-good.yaml` when possible, and exposes
a marker the console can show with an explicit restore action.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .schema import Config
from .store import ConfigError, load_config, save_config

log = logging.getLogger(__name__)
MARKER_NAME = "config-recovery.json"
LAST_GOOD_SUFFIX = ".last-good.yaml"


@dataclass
class RecoveryInfo:
    mode: str                 # "last-good" | "defaults"
    config_path: str
    bad_path: str = ""
    last_good_path: str = ""
    at: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def last_good_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.stem + LAST_GOOD_SUFFIX)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def remember_last_good(config_path: Path | None) -> Path | None:
    """Snapshot a config that just started successfully. Best effort, never fatal."""
    if config_path is None or not config_path.is_file():
        return None
    target = last_good_path(config_path)
    try:
        _write_private(target, config_path.read_bytes())
        return target
    except Exception as exc:  # noqa: BLE001 - a recovery convenience must not stop boot
        log.warning("config recovery: cannot write %s: %s", target, exc)
        return None


def _marker_path(cfg: Config) -> Path:
    return Path(cfg.system.data_dir) / MARKER_NAME


def read_marker(cfg: Config) -> RecoveryInfo | None:
    path = _marker_path(cfg)
    try:
        raw = json.loads(path.read_text())
        return RecoveryInfo(**raw)
    except Exception:  # noqa: BLE001 - no/corrupt marker means normal mode
        return None


def clear_marker(cfg: Config) -> None:
    try:
        _marker_path(cfg).unlink(missing_ok=True)
    except OSError:
        pass


def write_marker(cfg: Config, info: RecoveryInfo) -> None:
    try:
        _write_private(_marker_path(cfg), json.dumps(info.to_dict(), ensure_ascii=False,
                                                    indent=2).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("config recovery: cannot write marker: %s", exc)


def load_with_recovery(config_path: Path) -> tuple[Config, RecoveryInfo | None]:
    """Load config; on failure keep the bad file and fall back to last-good/defaults."""
    if not config_path.exists():
        return Config(), None
    try:
        return load_config(config_path), None
    except ConfigError as exc:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bad = config_path.with_name(config_path.name + f".failed-{stamp}")
        try:
            shutil.copy2(config_path, bad)
            os.chmod(bad, 0o600)
        except OSError:
            bad = Path("")
        note = str(exc)[:500]
        candidate = last_good_path(config_path)
        cfg: Config
        mode: str
        last_good = ""
        if candidate.is_file():
            try:
                cfg = load_config(candidate)
                mode = "last-good"
                last_good = str(candidate)
            except Exception:  # noqa: BLE001 - fall through to schema defaults
                cfg = Config()
                mode = "defaults"
        else:
            cfg = Config()
            mode = "defaults"
        info = RecoveryInfo(mode=mode, config_path=str(config_path),
                            bad_path=str(bad) if bad else "", last_good_path=last_good,
                            at=time.time(), note=note)
        write_marker(cfg, info)
        log.error("config recovery: %s; running from %s", note, mode)
        return cfg, info


def restore(config_path: Path, cfg: Config, mode: str) -> str:
    """Explicit operator restore. Returns a short status; restart is still required."""
    if mode not in {"last-good", "defaults"}:
        raise ValueError("mode must be last-good or defaults")
    if mode == "last-good":
        source = last_good_path(config_path)
        if not source.is_file():
            raise FileNotFoundError("no last-good config available")
        # Validate before replacing the live file.
        load_config(source)
        if config_path.exists():
            shutil.copy2(config_path, config_path.with_name(
                config_path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        shutil.copy2(source, config_path)
        os.chmod(config_path, 0o600)
        clear_marker(cfg)
        return "restored last-good config; restart the service"
    save_config(config_path, Config())
    clear_marker(cfg)
    return "restored schema defaults; restart the service"
