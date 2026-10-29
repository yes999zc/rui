"""External (out-of-tree) music sources, named by the operator's environment.

The preset registry (`presets.py`) is the public list of sources rui ships adapters
for. A private source never enters that list; the operator names it instead::

    RUI_MUSIC_SOURCES="myid=mypkg.mysource:make[, other=...]"

This is an explicit allowlist, the same posture as ``RUI_ADAPTER_PLUGINS``: only code
the operator names in the environment (root/container owned) is imported, in-process.
Deliberately *not* a scan of ``<data_dir>/music_sources/*.py`` - "drop a file in the
data volume and it executes" is the one thing a data directory must never mean.

A module loaded this way has to declare two things:

* ``music_sources_compat`` - a version range using the plugin range grammar
  (``rui.plugins.semver``), e.g. ``">=0.8.6 <0.9.0"``;
* a factory (``module:factory``, dotted attributes allowed) that accepts one
  :class:`SourceContext` and returns an object implementing the five
  :class:`~rui.music.source.MusicSource` methods.

Every failure is a ``ValueError`` whose sentence names the source id and the original
problem, so the API can keep answering 422 and the log line is actionable. Runtime
assembly (app startup, config save) catches it and leaves music off instead of taking
the whole service down; see ``docs/music-private-sources.md``.
"""
from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ... import __version__ as RUI_VERSION
from ...env import get as env_get
from ...plugins.semver import VersionError, parse_range, parse_version
from ..source import MusicSource

log = logging.getLogger(__name__)

ENV_VAR = "RUI_MUSIC_SOURCES"
COMPAT_ATTR = "music_sources_compat"
REQUIRED_METHODS = ("search", "random", "favorites", "star", "resolve")
COMPAT_EXAMPLE = ">=0.8.6 <0.9.0"

__all__ = [
    "COMPAT_ATTR",
    "COMPAT_EXAMPLE",
    "ENV_VAR",
    "ExternalSourceSpec",
    "REQUIRED_METHODS",
    "SourceContext",
    "external_source_ids",
    "is_external_source",
    "load_external_source",
    "lookup_external",
    "parse_entry",
    "parse_registry",
]

_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_ATTR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")

# One warning per distinct malformed env value: panel/catalog rendering reads the
# registry several times per request and must not flood the log.
_warned: set[str] = set()


@dataclass(frozen=True)
class SourceContext:
    """The read-only facts one external factory may use.

    ``base_url``/``user``/``password``/``token``/``api_key`` carry the console's
    unsaved "test connection" draft when the caller uses fixed mode; in normal file
    mode they are empty and the source reads its own credentials from
    ``secrets_path`` (rui never parses that file's format for an external source).
    """

    source_id: str
    secrets_path: Path | None
    cache_dir: Path
    cache_tracks: int
    policy: Any = None
    base_url: str = ""
    user: str = ""
    password: str = ""
    token: str = ""
    api_key: str = ""


@dataclass(frozen=True)
class ExternalSourceSpec:
    """One parsed ``id=module:factory`` entry."""

    source_id: str
    module: str
    factory: str
    entry: str


def _entries(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def parse_entry(text: str) -> ExternalSourceSpec:
    """Parse one ``id=module:factory`` entry; errors quote the raw entry."""
    raw = str(text or "")
    source_id, separator, target = raw.partition("=")
    source_id, target = source_id.strip(), target.strip()
    if not separator:
        raise ValueError(
            f"invalid entry in {ENV_VAR}: {raw!r}; expected <id>=<module>:<factory>")
    if not source_id:
        raise ValueError(f"invalid entry in {ENV_VAR}: {raw!r}; the source id is empty")
    if not _ID_RE.fullmatch(source_id):
        raise ValueError(
            f"invalid entry in {ENV_VAR}: {raw!r}; source id {source_id!r} may only use "
            "letters, digits, '_', '.' and '-'")
    module, colon, factory = target.partition(":")
    module, factory = module.strip(), factory.strip()
    if not colon:
        raise ValueError(
            f"invalid entry in {ENV_VAR}: {raw!r}; expected <module>:<factory>, "
            f"got module {module!r} without a factory")
    if not module or not _MODULE_RE.fullmatch(module):
        raise ValueError(
            f"invalid entry in {ENV_VAR}: {raw!r}; invalid module {module!r}")
    if not factory:
        raise ValueError(
            f"invalid entry in {ENV_VAR}: {raw!r}; missing factory after ':'")
    if not _ATTR_RE.fullmatch(factory):
        raise ValueError(
            f"invalid entry in {ENV_VAR}: {raw!r}; invalid factory {factory!r} "
            "(use a dotted attribute path like make or build.source)")
    return ExternalSourceSpec(
        source_id=source_id, module=module, factory=factory, entry=raw)


def parse_registry(value: str | None = None) -> dict[str, ExternalSourceSpec]:
    """Every registered id, keyed by the normalized (lowercase) id.

    ``value`` defaults to ``RUI_MUSIC_SOURCES`` (``HOUSE_EAR_MUSIC_SOURCES`` is read
    as the rename fallback by ``rui.env``). Unset or blank means "no external
    sources", which is exactly the 0.8.6 behaviour. An invalid entry raises
    ``ValueError`` quoting the raw entry; a duplicate id is invalid too.
    """
    raw = env_get(ENV_VAR) if value is None else value
    specs: dict[str, ExternalSourceSpec] = {}
    for entry in _entries(raw):
        spec = parse_entry(entry)
        key = spec.source_id.strip().lower()
        if key in specs:
            raise ValueError(
                f"duplicate music source id {spec.source_id!r} in {ENV_VAR}: {entry!r}")
        specs[key] = spec
    return specs


def external_source_ids(value: str | None = None) -> tuple[str, ...]:
    """Registered ids, sorted. A malformed env is ignored with one warning."""
    try:
        return tuple(sorted(parse_registry(value)))
    except ValueError as exc:
        _warn_once(f"music sources: ignoring {ENV_VAR}: {exc}")
        return ()


def lookup_external(source_id: str, value: str | None = None) -> ExternalSourceSpec | None:
    """The spec for one id, or None. Raises for a malformed env (see parse_registry)."""
    return parse_registry(value).get(str(source_id or "").strip().lower())


def is_external_source(source_id: str, value: str | None = None) -> bool:
    """Whether the env registers this id. Never raises: a bad env reads as "no"."""
    try:
        return lookup_external(source_id, value) is not None
    except ValueError as exc:
        _warn_once(f"music sources: ignoring {ENV_VAR}: {exc}")
        return False


def load_external_source(spec: ExternalSourceSpec, context: SourceContext) -> MusicSource:
    """Import, check the version contract, call the factory and validate the result.

    All failures are ``ValueError`` (the API's 422 contract) and keep the original
    exception as ``__cause__`` where there is one.
    """
    module = _import_module(spec)
    _check_compat(spec, module)
    factory = _resolve_factory(spec, module)
    try:
        source = factory(context)
    except Exception as exc:  # noqa: BLE001 - any factory failure is the operator's line
        raise ValueError(
            f"music source {spec.source_id!r}: factory {spec.factory!r} from module "
            f"{spec.module!r} raised {type(exc).__name__}: {exc}") from exc
    if source is None:
        raise ValueError(
            f"music source {spec.source_id!r}: factory {spec.factory!r} from module "
            f"{spec.module!r} returned None; expected a MusicSource instance")
    _validate_source(spec, source)
    return source


def _import_module(spec: ExternalSourceSpec):
    try:
        return importlib.import_module(spec.module)
    except Exception as exc:  # noqa: BLE001 - a broken plugin is an operator problem
        raise ValueError(
            f"music source {spec.source_id!r}: cannot import module {spec.module!r}: "
            f"{type(exc).__name__}: {exc}") from exc


def _check_compat(spec: ExternalSourceSpec, module: object) -> None:
    declared = getattr(module, COMPAT_ATTR, None)
    where = f"music source {spec.source_id!r} (module {spec.module!r})"
    if declared is None:
        raise ValueError(
            f"{where} does not declare {COMPAT_ATTR}; add "
            f'{COMPAT_ATTR} = "{COMPAT_EXAMPLE}"')
    if not isinstance(declared, str) or not declared.strip():
        raise ValueError(
            f"{where} declares {COMPAT_ATTR} = {declared!r}; expected a non-empty "
            f'version range string such as "{COMPAT_EXAMPLE}"')
    try:
        required = parse_range(declared)
    except VersionError as exc:
        raise ValueError(
            f"{where} declares {COMPAT_ATTR} = {declared!r}, which is not a version "
            f"range: {exc}") from None
    try:
        running = parse_version(RUI_VERSION)
    except VersionError as exc:  # pragma: no cover - a broken build, not a plugin
        raise ValueError(
            f"this rui build's version {RUI_VERSION!r} is not semver: {exc}") from None
    if not required.matches(running):
        raise ValueError(
            f"{where} is incompatible: it declares {COMPAT_ATTR} = {declared!r}, "
            f"but this rui build is {RUI_VERSION}")


def _resolve_factory(spec: ExternalSourceSpec, module: object):
    target: Any = module
    for part in spec.factory.split("."):
        try:
            target = getattr(target, part)
        except AttributeError:
            raise ValueError(
                f"music source {spec.source_id!r}: module {spec.module!r} has no "
                f"attribute {spec.factory!r} (the factory)") from None
    if not callable(target):
        raise ValueError(
            f"music source {spec.source_id!r}: factory {spec.factory!r} in module "
            f"{spec.module!r} is not callable")
    return target


def _validate_source(spec: ExternalSourceSpec, source: object) -> None:
    """Require the five protocol methods by name; never fall through to AttributeError."""
    where = f"music source {spec.source_id!r} from {spec.module}:{spec.factory}"
    for name in REQUIRED_METHODS:
        try:
            attribute = getattr(source, name)
        except AttributeError:
            raise ValueError(f"{where} is missing MusicSource method: {name}") from None
        if not callable(attribute):
            raise ValueError(
                f"{where}: MusicSource method {name!r} is not callable")


def _warn_once(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    log.warning("%s", message)
