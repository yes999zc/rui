"""Tiny persisted state for the install wizard: ``<data_dir>/setup.json``.

The wizard must survive a refresh without a database. Only confirmations and action
timestamps live here; every ``done`` decision is still recomputed from real evidence
(secrets, files, the live plane, turns), so a stale entry never claims success.

The file is written atomically with mode 0600 because it names local devices and their
timestamps, and contains no secrets by construction (callers only pass booleans, ids
and ISO timestamps).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FILENAME = "setup.json"

# The UI's eight steps, in order. ``verify`` is optional: a working speaker must not
# stay in the wizard forever because nobody typed a test sentence.
STEPS: tuple[str, ...] = (
    "keys",
    "models",
    "prepare",
    "params",
    "wake",
    "connect",
    "test",
    "verify",
)
OPTIONAL_STEPS: frozenset[str] = frozenset({"verify"})

_LOCK = threading.Lock()


def setup_path(data_dir: str | Path | None) -> Path | None:
    raw = str(data_dir or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser() / FILENAME


def read(data_dir: str | Path | None) -> dict[str, Any]:
    """Read the state dict; a missing or broken file means "nothing confirmed yet"."""
    path = setup_path(data_dir)
    if path is None or not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("setup: cannot read %s: %s", path, exc)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write(data_dir: str | Path | None, data: dict[str, Any]) -> bool:
    """Write ``data`` atomically with 0600. Returns False when nothing changed."""
    path = setup_path(data_dir)
    if path is None:
        return False
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == payload:
            # A copied-in file may be 0644; idempotency must not preserve that.
            os.chmod(path, 0o600)
            return False
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{FILENAME}.", suffix=".tmp"
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, path)   # atomic on POSIX and Windows
            os.chmod(path, 0o600)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        # A read-only data dir must not crash the wizard: evidence still works.
        log.warning("setup: cannot write %s: %s", path, exc)
        return False
    return True


def update(data_dir: str | Path | None, mutate: Callable[[dict[str, Any]], bool]) -> bool:
    """Read-modify-write under one process lock. ``mutate`` returns "changed"."""
    with _LOCK:
        data = read(data_dir)
        if not mutate(data):
            return False
        return write(data_dir, data)


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def epoch(value: object) -> float | None:
    """Parse an ISO timestamp written by :func:`iso_now` (None when unusable)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def ensure_started(data_dir: str | Path | None) -> str:
    """Record (once) when this setup attempt began; used to scope verify turns."""
    started = str(read(data_dir).get("_meta", {}).get("started_at") or "")
    if started:
        return started
    now = iso_now()
    update(data_dir, lambda data: _set_default(data, ("_meta", "started_at"), now))
    return str(read(data_dir).get("_meta", {}).get("started_at") or now)


def set_value(data: dict[str, Any], path: tuple[str, ...], value: Any) -> bool:
    """Set a nested key; returns False when it already has exactly this value."""
    node: dict[str, Any] = data
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    if node.get(path[-1]) == value:
        return False
    node[path[-1]] = value
    return True


def _set_default(data: dict[str, Any], path: tuple[str, ...], value: Any) -> bool:
    node: dict[str, Any] = data
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    if node.get(path[-1]):
        return False
    node[path[-1]] = value
    return True


def recent_marker() -> float:
    """A monotonic-ish wall clock value for in-memory turn scoping."""
    return time.time()
