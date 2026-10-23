"""File-backed override layer for the PhraseBook (C-UI P6 backend).

Layout: `data_dir/phrases.yaml`, overrides only. It sits next to `memory.json` /
`music_history.json` / `music_aliases.json` on purpose: wording is user content,
not deployment intent, so `house-ear setup --force` and a config restore never
wipe it (docs/phrasebook-editor.md §4).

This module owns the filesystem and the document shape; `session.phrases` owns
the templates and the slot rules. The console endpoint (owned by the control
plane) should use:

    load(data_dir)                     -> LoadResult, applies valid overrides
    save(data_dir, overrides)          -> LoadResult, validates + writes + applies
    clear(data_dir, keys=None)         -> LoadResult, resets one/all
    document(data_dir)                 -> dict for GET /api/v1/phrases

`save()` raises `phrases.PhraseOverrideError` (with `.errors` for a 422) and
never touches the file in that case. `load()` never raises: a broken file keeps
the defaults and reports the reason.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config.store import _yaml  # same ruamel writer as config.yaml (no second dialect)
from . import phrases

log = logging.getLogger(__name__)

FILE_NAME = "phrases.yaml"
VERSION = 1
BACKUPS = 1


@dataclass
class LoadResult:
    """What can be said about the file. `overrides` are the *valid* parsed ones."""

    path: str
    exists: bool = False
    overrides: dict[str, str] = field(default_factory=dict)
    errors: dict[str, list[str]] = field(default_factory=dict)
    unused: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "overrides": dict(self.overrides),
            "errors": self.errors,
            "unused": list(self.unused),
            "error": self.error,
            "ok": self.ok,
        }


def path_for(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / FILE_NAME


def read(path: str | Path) -> LoadResult:
    """Read and validate the file. Missing is not an error; invalid is."""
    target = Path(path).expanduser()
    result = LoadResult(path=str(target))
    if not target.is_file():
        return result
    result.exists = True
    try:
        data = _yaml.load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - reported, never raised (startup path)
        result.error = f"{target.name} 解析失败：{type(exc).__name__}: {exc}"
        return result
    if not isinstance(data, dict):
        result.error = f"{target.name} 顶层不是对象"
        return result
    raw = data.get("overrides")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        result.error = f"{target.name} 的 overrides 不是对象"
        return result
    for raw_key, raw_value in raw.items():
        key = str(raw_key)
        if key not in phrases.DEFAULTS:
            # A key the code no longer has is retained but ignored, never a reason
            # to reject the whole file (docs/phrasebook-editor.md §4).
            result.unused.append(key)
            continue
        if not isinstance(raw_value, str):
            result.errors[key] = ["话术必须是字符串"]
            continue
        problems = phrases.validate_override(key, raw_value)
        if problems:
            result.errors[key] = problems
        else:
            result.overrides[key] = raw_value
    result.unused = sorted(set(result.unused))
    return result


def _backup(target: Path) -> None:
    if not target.is_file():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_suffix(target.suffix + f".bak-{stamp}")
    try:
        shutil.copy2(target, backup)
    except OSError as exc:  # a failed backup must not block the save
        log.warning("phrasebook: backup failed: %s", exc)
        return
    existing = sorted(target.parent.glob(target.name + ".bak-*"))
    for old in existing[:-BACKUPS]:
        old.unlink(missing_ok=True)


def _write(target: Path, overrides: dict[str, str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _backup(target)
    payload = {
        "version": VERSION,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overrides": dict(sorted(overrides.items())),
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        _yaml.dump(payload, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def save(data_dir_or_path: str | Path,
         overrides: dict[str, Any]) -> LoadResult:
    """Validate, write atomically (0600 + one backup), then apply live.

    Raises `phrases.PhraseOverrideError` on any invalid key; the file is unchanged
    in that case (all-or-nothing).
    """
    problems = phrases.validate_overrides(overrides)
    if problems:
        raise phrases.PhraseOverrideError(problems)
    clean = {str(key): str(value) for key, value in overrides.items()}
    target = _target(data_dir_or_path)
    _write(target, clean)
    phrases.apply_overrides(clean)
    result = read(target)
    log.info("phrasebook: wrote %d override(s) -> %s", len(clean), target)
    return result


def clear(data_dir_or_path: str | Path, keys: list[str] | None = None) -> LoadResult:
    """Reset all (keys=None) or the named keys; unknown keys are ignored."""
    target = _target(data_dir_or_path)
    result = read(target)
    kept = dict(result.overrides)
    if keys is None:
        kept = {}
    else:
        for key in keys:
            kept.pop(str(key), None)
    # Rewriting also repairs a file that had invalid entries (they were never applied).
    return save(target, kept)


def load(data_dir_or_path: str | Path) -> LoadResult:
    """Read the file, apply valid overrides, return the report. Never raises."""
    target = _target(data_dir_or_path)
    result = read(target)
    if result.error or result.errors:
        phrases.reset()
        return result
    phrases.apply_overrides(result.overrides if result.exists else {})
    return result


def document(data_dir_or_path: str | Path) -> dict[str, Any]:
    """The console's GET payload: live templates + file metadata + validation."""
    target = _target(data_dir_or_path)
    result = read(target)
    doc = phrases.snapshot()
    doc.update({
        "file": str(target),
        "file_exists": result.exists,
        "file_error": result.error,
        "errors": result.errors,
        "unused": result.unused,
        "file_overrides": dict(result.overrides),
        "file_version": VERSION,
    })
    return doc


def _target(data_dir_or_path: str | Path) -> Path:
    """Accept either the data directory or the file path (both are useful to callers)."""
    path = Path(data_dir_or_path).expanduser()
    if path.suffix in (".yaml", ".yml"):
        return path
    return path / FILE_NAME
