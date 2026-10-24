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

The project used to be `house-ear`, so an install that reuses an older `secrets.yaml`
(or `.env`) can still spell a key `HOUSE_EAR_LLM_KEY`; an even older one spells it
`DEEPSEEK_API_KEY`. Both are read as fallbacks only: the canonical `RUI_*` name always
wins, and an explicit `RUI_*` value is never shadowed by a legacy one.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from .config.store import _yaml  # same ruamel round-trip writer as the config file
from .env import canonical_name, legacy_name

log = logging.getLogger(__name__)

FILENAME = "secrets.yaml"
_name: str = ""          # set by `configure()` at startup; empty means "environment only"

# Configured name -> names used by older installs. Keep this list tiny: it is a bridge
# for one rename, not a general alias mechanism.
LEGACY_ALIASES: dict[str, tuple[str, ...]] = {
    "RUI_LLM_KEY": ("DEEPSEEK_API_KEY",),
}


@dataclass(frozen=True)
class Resolution:
    """Where a secret came from. The value itself is excluded from `repr` on purpose."""

    name: str                         # the name the config asked for
    found_as: str                     # the name that actually had a value
    source: str                       # "environment" or "secrets.yaml"
    value: str = field(repr=False)

    @property
    def from_alias(self) -> bool:
        return self.found_as != self.name


def aliases(name: str) -> tuple[str, ...]:
    """Older names to try after `name` itself (empty for names with no rename)."""
    canonical = canonical_name(name)
    candidates = [legacy_name(canonical), *LEGACY_ALIASES.get(canonical, ())]
    return tuple(c for c in candidates if c and c != canonical)


def _candidates(name: str) -> tuple[str, ...]:
    """Lookup order: canonical `RUI_*`, then the old prefix, then older aliases."""
    canonical = canonical_name(name)
    return (canonical, *aliases(canonical))


def resolve(name: str) -> Resolution | None:
    """The value and its origin, or None if neither the name nor an alias is set.

    Environment before file, as everywhere else. Within one source the canonical
    `RUI_*` name is tried before `HOUSE_EAR_*` and older aliases such as
    `DEEPSEEK_API_KEY`, so an explicit canonical value is never shadowed.
    """
    if not name:
        return None
    candidates = _candidates(name)
    canonical = candidates[0]
    for candidate in candidates:
        value = os.environ.get(candidate, "")
        if value:
            return Resolution(name=canonical, found_as=candidate,
                              source="environment", value=value)
    data = _read()
    for candidate in candidates:
        value = data.get(candidate, "")
        if value:
            return Resolution(name=canonical, found_as=candidate,
                              source="secrets.yaml", value=value)
    return None


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
    """Environment wins over the file: a container's keys are not on that volume.

    A legacy alias is consulted only when the configured name has no value, so the
    provider code (which reads every key through `get`) keeps working after the rename.
    """
    resolution = resolve(name)
    return resolution.value if resolution else ""


def has(name: str) -> bool:
    return resolve(name) is not None


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
