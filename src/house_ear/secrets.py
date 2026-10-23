"""Write-only secret storage, so a key can be typed into the console.

Two rules this exists to satisfy at once:

- a key typed in the browser must be usable by the providers and by the search tool
  without being copied into `config.yaml`, which is readable from the console and easy to
  paste into a bug report;
- the API must never read a value back. It can say `has_key: true` and nothing else.

Order of resolution: the environment first (a container keeps its keys there, and that must
keep winning), then this file. The file lives next to the rest of the runtime state
(`system.data_dir/secrets.yaml`) with 0600 and is replaced atomically, so a crash cannot
leave it half-written.
"""
from __future__ import annotations

import logging
import os
from io import StringIO
from pathlib import Path

from .config.store import _yaml  # same ruamel round-trip writer as the config file

log = logging.getLogger(__name__)

FILENAME = "secrets.yaml"
_name: str = ""          # set by `configure()` at startup; empty means "environment only"


def configure(data_dir: str | Path | None) -> None:
    """Point the store at a data directory. Called once, before providers are built."""
    global _name
    _name = str(Path(data_dir) / FILENAME) if data_dir else ""


def path() -> Path | None:
    return Path(_name) if _name else None


def _read() -> dict[str, str]:
    p = path()
    if p is None or not p.exists():
        return {}
    try:
        raw = _yaml.load(p) or {}
    except Exception as exc:  # noqa: BLE001 - a broken secrets file must not stop startup
        log.warning("secrets: cannot read %s: %s", p, exc)
        return {}
    return {str(k): str(v) for k, v in dict(raw).items() if v is not None}


def get(name: str) -> str:
    """Environment wins over the file: a container's keys are not on that volume."""
    value = os.environ.get(name, "")
    if value:
        return value
    return _read().get(name, "") if name else ""


def has(name: str) -> bool:
    return bool(get(name))


def put(name: str, value: str) -> None:
    """Store one key. Never logged with its value, never echoed by an API."""
    name = (name or "").strip()
    if not name or not name.replace("_", "").isalnum() or not name.isupper():
        raise ValueError("secret name must look like an env var: UPPER_CASE_WITH_UNDERSCORES")
    p = path()
    if p is None:
        raise ValueError("no data directory configured; cannot store a secret")
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _read()
    if value:
        data[name] = value
    else:
        data.pop(name, None)          # an empty value deletes it
    buf = StringIO()
    _yaml.dump(data, buf)
    tmp = p.with_suffix(".tmp")
    # Private before the first byte: a crash between write and replace must not leave
    # a readable plaintext secrets file behind, even for a moment.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(tmp, 0o600)          # before the first byte, umask-proof
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    log.info("secrets: %s %s", "set" if value else "cleared", name)


def stored_names() -> list[str]:
    """Names that have a value in the file. Values are never returned."""
    return sorted(_read())
