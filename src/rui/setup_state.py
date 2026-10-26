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


# The device-test record lives in two places for one release: ``test/device/test`` is
# the pre-0.6 single house-wide slot (the last test of any room; v1 still reads it),
# and ``test/device/by_id/<id>`` is the per-device history the v2 detail card and the
# setup plan read. Both writers go through :func:`record_device_test` so the two stay
# in step; readers go through :func:`device_test_record` so an old file still works.
_DEVICE_TEST: tuple[str, ...] = ("test", "device", "test")
_DEVICE_TEST_BY_ID: tuple[str, ...] = ("test", "device", "by_id")


def _dict(value: object) -> dict[str, Any]:
    """A JSON layer as a dict; anything else is "nothing stored" (setup.json is editable)."""
    return value if isinstance(value, dict) else {}


def device_test_record(data: dict[str, Any], device_id: str) -> dict[str, Any]:
    """The stored ``device.test`` record for one device; ``{}`` when there is none.

    The per-device record wins. The map key is the identity, and the record must agree
    with it: a hand-edited entry naming another device is corrupt, not evidence for
    this one. The legacy single slot is the fallback for files written before the
    per-device map existed, and only when its record names the same device -- a record
    from another room is never evidence for this one. Every layer is checked with
    :func:`_dict` because setup.json is user-editable: a non-dict layer means "no
    record", not an exception.
    """
    device_id = str(device_id or "").strip()
    if not device_id:
        return {}
    node = _dict(_dict(_dict(data.get("test")).get("device")).get("by_id"))
    record = node.get(device_id)
    if isinstance(record, dict) and record:
        if str(record.get("device_id") or "").strip() == device_id:
            return record
        return {}
    legacy = _dict(_dict(_dict(data.get("test")).get("device")).get("test"))
    if legacy and str(legacy.get("device_id") or "").strip() == device_id:
        return legacy
    return {}


def record_device_test(data: dict[str, Any], device_id: str,
                       entry: dict[str, Any]) -> bool:
    """Store ``entry`` as ``device_id``'s test result and as the legacy last-test slot.

    Both consoles write here (the v2 routes and the v1 wizard), so whichever one ran
    last, the device's own record is the newest for that device. Keeping the legacy
    slot authoritative for "the last test in this house" preserves the v1 device
    list's behaviour (it has no device dimension); the per-device map is what makes
    the v2 detail card and setup plan independent of test order. Returns whether
    anything changed, as :func:`update` expects.
    """
    device_id = str(device_id or "").strip()
    if not device_id:
        return False
    # set_value replaces a corrupt intermediate layer with a dict, so a hand-edited
    # ``"device": []`` cannot stop the write.
    changed = set_value(data, _DEVICE_TEST, entry)
    changed = set_value(data, _DEVICE_TEST_BY_ID + (device_id,), entry) or changed
    return changed


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
