"""Load a Socket & Plug v0 plugin directory without executing any of it.

Security posture (deliberate, documented in ``docs/socket-plug.md`` §7):

* ``entrypoint`` and ``image`` are **strings only**. This module never imports,
  resolves, pulls or runs them.
* Device manifest paths must resolve inside the plugin directory. ``..``,
  absolute paths and symlinks that point outside are rejected.
* ``trust=third-party`` may not request ``runtime.mode: in-process``. It is
  rejected rather than silently coerced, because running third-party code in the
  RUI process is a security decision the user must see. Omitting ``runtime`` is
  allowed: that is a declaration-only plug.
* Every failure is a :class:`PluginError` whose message names the file, field
  and offending scalar; raw pydantic/YAML tracebacks never reach the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

import house_ear

from .errors import PluginError
from .models import (
    DeviceManifest,
    Plugin,
    PluginManifest,
    RuntimeMode,
    RuntimeSpec,
    TrustLevel,
)
from .semver import Version, VersionError, parse_range, parse_version

__all__ = ["PluginScan", "discover_plugins", "load_plugin", "running_version"]

_MAX_MANIFEST_BYTES = 1 << 20  # manifests are kilobytes; refuse a hostile 1 GiB read
_MAX_ERRORS = 8

ModelT = TypeVar("ModelT", bound=BaseModel)


def running_version() -> Version:
    """The RUI version this process is running."""
    version = house_ear.__version__
    try:
        return parse_version(version)
    except VersionError as exc:
        raise PluginError(f"this build's version {version!r} is not semver: {exc}") from None


def load_plugin(path: Path) -> Plugin:
    """Read ``plugin.yaml`` and every referenced ``devices/*.yaml``.

    Raises :class:`PluginError` for a missing/unreadable directory, malformed
    YAML, a schema violation, an unknown capability, an incompatible
    ``rui_compat``, a path that escapes the plugin directory, or a third-party
    plugin asking to run in-process.
    """
    root = _plugin_root(Path(path))
    plugin_file = root / "plugin.yaml"
    manifest = _validate(PluginManifest, _read_mapping(plugin_file), plugin_file)

    running = running_version()
    required = parse_range(manifest.rui_compat)
    if not required.matches(running):
        raise PluginError(
            f"{plugin_file}: plugin {manifest.id!r} is incompatible: it requires RUI "
            f"{manifest.rui_compat}, but this build is {running}; refusing to load it"
        )

    devices: list[DeviceManifest] = []
    seen_ids: set[str] = set()
    for entry in manifest.devices:
        device_file = _resolve_member(root, entry, plugin_file)
        device = _validate(DeviceManifest, _read_mapping(device_file), device_file)
        device = _resolve_device(device, manifest.trust, device_file, running)
        if device.id in seen_ids:
            raise PluginError(f"{device_file}: duplicate device id {device.id!r}")
        seen_ids.add(device.id)
        devices.append(device)

    data = manifest.model_dump()
    data.pop("devices", None)  # replaced by the validated DeviceManifest objects
    return Plugin(**data, devices=devices, source_dir=root)


def _plugin_root(path: Path) -> Path:
    try:
        root = path.expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise PluginError(f"plugin directory not found: {path}") from None
    except (OSError, RuntimeError, ValueError) as exc:
        raise PluginError(f"cannot open plugin directory {path}: {_brief(exc)}") from None
    if not root.is_dir():
        raise PluginError(f"plugin path is not a directory: {path}")
    return root


def _resolve_member(root: Path, entry: str, plugin_file: Path) -> Path:
    """Resolve one ``devices:`` entry, refusing anything outside ``root``."""
    try:
        relative = Path(entry)
    except (OSError, ValueError) as exc:
        raise PluginError(
            f"{plugin_file}: invalid device manifest path {entry!r}: {_brief(exc)}"
        ) from None
    if relative.is_absolute():
        raise PluginError(
            f"{plugin_file}: device manifest {entry!r} must be a relative path "
            "inside the plugin directory"
        )
    try:
        target = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PluginError(
            f"{plugin_file}: cannot resolve device manifest {entry!r}: {_brief(exc)}"
        ) from None
    try:
        target.relative_to(root)
    except ValueError:
        raise PluginError(
            f"{plugin_file}: device manifest {entry!r} escapes the plugin directory "
            f"({root})"
        ) from None
    return target


def _read_mapping(path: Path) -> dict:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise PluginError(f"manifest not found: {path}") from None
    except OSError as exc:
        raise PluginError(f"cannot read manifest {path}: {_brief(exc)}") from None
    if size > _MAX_MANIFEST_BYTES:
        raise PluginError(
            f"manifest {path} is {size} bytes; the limit is {_MAX_MANIFEST_BYTES}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PluginError(f"manifest not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise PluginError(f"cannot read manifest {path}: {_brief(exc)}") from None
    if not text.strip():
        raise PluginError(f"manifest is empty: {path}")
    try:
        # A fresh loader per file: ruamel's YAML object is stateful, and this
        # function will later be called from web request threads.
        raw = YAML(typ="safe").load(text)
    except Exception as exc:  # noqa: BLE001 - any parser failure is user input
        raise PluginError(f"cannot parse {path}: {_brief(exc)}") from None
    if not isinstance(raw, dict):
        raise PluginError(f"{path}: expected a YAML mapping at the top level")
    return raw


def _validate(model: type[ModelT], raw: dict, source: Path, *, prefix: str = "") -> ModelT:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise PluginError(_format_validation(exc, source, prefix=prefix)) from None


def _format_validation(exc: ValidationError, source: Path, *, prefix: str = "") -> str:
    lines: list[str] = []
    errors = exc.errors()
    for error in errors[:_MAX_ERRORS]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        if prefix:
            location = f"{prefix}.{location}" if location != "<root>" else prefix
        line = f"{source}: {location}: {error['msg']}"
        raw = error.get("input")
        if isinstance(raw, (str, int, float, bool)):
            line += f" (got {raw!r})"
        lines.append(line)
    if len(errors) > _MAX_ERRORS:
        lines.append(f"{source}: ... and {len(errors) - _MAX_ERRORS} more validation error(s)")
    return "\n".join(lines)


def _resolve_device(
    device: DeviceManifest,
    trust: TrustLevel,
    device_file: Path,
    running: Version,
) -> DeviceManifest:
    """Apply trust/runtime defaults and check the device's own RUI floor."""
    if device.compatibility and device.compatibility.rui_min:
        minimum = parse_version(device.compatibility.rui_min, allow_partial=True)
        if running < minimum:
            raise PluginError(
                f"{device_file}: device {device.id!r} needs RUI >= {minimum} "
                f"(compatibility.rui_min), but this build is {running}"
            )

    runtime = device.runtime
    if runtime is None:
        return device

    mode = runtime.mode
    if mode is None:
        mode = (
            RuntimeMode.SIDECAR
            if trust is TrustLevel.THIRD_PARTY
            else RuntimeMode.IN_PROCESS
        )
    if trust is TrustLevel.THIRD_PARTY and mode is RuntimeMode.IN_PROCESS:
        raise PluginError(
            f"{device_file}: device {device.id!r} asks for runtime.mode 'in-process', "
            "but the plugin trust level is 'third-party'; third-party code must run as "
            "'sidecar'. Set mode: sidecar, or have the plugin reviewed as builtin/trusted."
        )

    # Re-validate with the resolved mode so the mode-specific requirement
    # (entrypoint vs image) is enforced here, not assumed.
    try:
        resolved = RuntimeSpec.model_validate({**runtime.model_dump(), "mode": mode})
    except ValidationError as exc:
        raise PluginError(_format_validation(exc, device_file, prefix="runtime")) from None
    return device.model_copy(update={"runtime": resolved})


def _brief(exc: Exception) -> str:
    """One line, bounded, for a YAML/OS error message."""
    text = " ".join(str(exc).split())
    return text[:300] + ("..." if len(text) > 300 else "")


@dataclass(frozen=True)
class PluginScan:
    """Best-effort scan of ``<data_dir>/plugs/*/plugin.yaml``.

    ``errors`` is a list of readable one-line strings, never an exception: one broken
    user plug must not stop startup or hide the plugs that do load.
    """

    plugins: list[Plugin] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def discover_plugins(data_dir: str | Path | None) -> PluginScan:
    """Load optional user plugs from ``<data_dir>/plugs/<dir>/plugin.yaml``.

    Directories without a ``plugin.yaml`` are ignored (they are not plugs), symlinks are
    skipped, and every failure is collected in :attr:`PluginScan.errors`. Duplicate ids
    (including duplicates of an earlier user plug) are reported and skipped.
    """
    if data_dir is None or not str(data_dir).strip():
        return PluginScan()
    root = Path(data_dir).expanduser() / "plugs"
    try:
        if not root.is_dir():
            return PluginScan()
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        return PluginScan(errors=[f"cannot read plug directory {root}: {_brief(exc)}"])

    plugins: list[Plugin] = []
    errors: list[str] = []
    seen: set[str] = set()
    for child in children:
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            manifest = child / "plugin.yaml"
            if not manifest.is_file():
                continue
            plugin = load_plugin(child)
        except PluginError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - never let one plug stop the scan
            errors.append(f"{child}: unexpected error while loading plug: {_brief(exc)}")
            continue
        if plugin.id in seen:
            errors.append(f"{child}: duplicate plugin id {plugin.id!r}; skipped")
            continue
        seen.add(plugin.id)
        plugins.append(plugin)
    return PluginScan(plugins=plugins, errors=errors)
