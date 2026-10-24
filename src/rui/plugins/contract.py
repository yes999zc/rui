"""The Socket & Plug v1 contract test kit (P3.1).

This module is a **development/acceptance tool**, not part of the runtime. A
third-party speaker project points it at its plugin directory and gets a
``contract-report.json``-shaped answer; RUI core never imports or trusts the
adapter because of it.

Two layers are tested:

* **Manifest** - ``plugin.yaml`` + ``devices/*.yaml`` are read with the existing
  :func:`rui.plugins.loader.load_plugin` (read-only). The v1 capability/wake
  rules from ``docs/socket-plug-v1.md`` §2 are checked on top, so a manifest
  that loads but declares an inconsistent ``wake`` block is still rejected.
* **Adapter** - only when ``--adapter module:attr`` is given. The entry point is
  imported lazily, built with a fake device/config, then exercised against a
  fake audio/playback sink: probe, frames, play, stop, signal/wake polling, an
  injected ``DeviceWake`` and idempotent close. No hardware and no audio plane
  are involved.

The kit deliberately has no RUI turn/barge policy engine. The wake injection
check answers the contract-level question ("does a valid event reach the
idle-gate classification?") without calling ASR/LLM/TTS, which is the "only
where safe to simulate" boundary from the task/§5.

Run it as ``rui plug-test <dir> [--adapter module:attr] [--json]`` or
``rui-plug-test``. Exit code is ``0`` when every check is PASS/WARN/SKIP and
``1`` when any check is FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import inspect
import json
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ruamel.yaml import YAML

import rui

from .errors import PluginError
from .loader import load_plugin, running_version
from .models import (
    DeviceManifest,
    PluginManifest,
    RuntimeMode,
    TrustLevel,
)
from .semver import VersionError, parse_range, parse_version

__all__ = [
    "Check",
    "ContractReport",
    "main",
    "run_contract",
]

REPORT_SCHEMA = "contract-report/v1"
KIT_NAME = "rui-plug-test"

#: §2 normative capability values for v1.
SPEC_CAPABILITIES = frozenset(
    {
        "mic_array",
        "reference",
        "playback",
        "led",
        "keys",
        "volume",
        "device_wake",
        "barge_via_device",
    }
)
#: v0 values still parsed but deprecated or reserved in v1.
LEGACY_CAPABILITIES = frozenset({"native_kws", "streaming_asr"})
#: Capabilities that only exist in v1.
V1_ONLY_CAPABILITIES = frozenset({"device_wake", "barge_via_device"})
ALL_CAPABILITY_NAMES = SPEC_CAPABILITIES | LEGACY_CAPABILITIES
#: Capabilities where manifest ∩ probe() is authoritative (§2.2).
PROBE_CAPABILITIES = frozenset(
    {"mic_array", "reference", "playback", "device_wake", "barge_via_device"}
)

#: The DeviceAdapter surface v1 requires. ``drain_wakes`` is listed separately
#: below in error messages because it is the new v1 member.
REQUIRED_ADAPTER_METHODS = (
    "probe",
    "open",
    "close",
    "frames",
    "play",
    "stop_playback",
    "set_volume",
    "set_led",
    "drain_signals",
    "drain_wakes",
)
SYNC_DRAIN_METHODS = ("drain_signals", "drain_wakes")

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"
_STATUSES = (PASS, FAIL, WARN, SKIP)

MAX_MANIFEST_BYTES = 1 << 20
_MAX_ERRORS = 8
_FRAME_COUNT = 3
_PROBE_TIMEOUT_S = 5.0
_OPEN_TIMEOUT_S = 10.0
_FRAME_TIMEOUT_S = 5.0
_CALL_TIMEOUT_S = 5.0
#: drain_wakes is specified as <1 ms; leave CI room before warning.
_DRAIN_WARN_MS = 5.0
#: frames() SHOULD yield at least every 100 ms; FAIL only on a real stall.
_FRAME_GAP_WARN_MS = 100.0
_FRAME_GAP_FAIL_MS = 500.0
#: Several drains are checked; an unbounded queue is a contract smell.
_DRAIN_QUEUE_WARN = 32


@dataclass
class Check:
    """One PASS/FAIL/WARN/SKIP line in the contract report."""

    id: str
    status: str
    detail: str = ""
    file: str = ""
    field: str = ""

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"unknown check status {self.status!r}")
        self.id = str(self.id)
        self.detail = str(self.detail)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "detail": self.detail,
        }
        if self.file:
            data["file"] = self.file
        if self.field:
            data["field"] = self.field
        return data


@dataclass
class ContractReport:
    """The report returned by :func:`run_contract`."""

    root: Path
    adapter: str | None = None
    plugin_id: str = ""
    trust: str = ""
    loader: str = "core"
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        check_id: str,
        status: str,
        detail: str = "",
        *,
        file: str | Path = "",
        field: str = "",
    ) -> Check:
        check = Check(
            id=check_id,
            status=status,
            detail=detail,
            file=str(file) if file else "",
            field=field,
        )
        self.checks.append(check)
        return check

    def has_failed(self) -> bool:
        return any(check.status == FAIL for check in self.checks)

    @property
    def verdict(self) -> str:
        return FAIL.lower() if self.has_failed() else PASS.lower()

    @property
    def exit_code(self) -> int:
        return 1 if self.has_failed() else 0

    @property
    def passed(self) -> bool:
        return not self.has_failed()

    def counts(self) -> dict[str, int]:
        result = {status.lower(): 0 for status in _STATUSES}
        for check in self.checks:
            result[check.status.lower()] += 1
        return result

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "schema": REPORT_SCHEMA,
            "kit": KIT_NAME,
            "generated_at": datetime.now(UTC).isoformat(),
            "rui": rui.__version__,
            "plugin": {
                "dir": str(self.root),
                "id": self.plugin_id,
                "trust": self.trust,
                "loader": self.loader,
                "devices": self.devices,
            },
            "adapter": self.adapter,
            "checks": [check.as_dict() for check in self.checks],
            "summary": {
                **counts,
                "verdict": self.verdict,
            },
            "warnings": list(self.warnings),
        }

    def to_text(self) -> str:
        lines = [
            f"{KIT_NAME}: {self.root}",
            f"plugin: {self.plugin_id or '<unknown>'} "
            f"trust={self.trust or '<unknown>'} loader={self.loader}",
            f"adapter: {self.adapter or '<manifest only>'}",
            "",
        ]
        for check in self.checks:
            location = ""
            if check.file and check.field:
                location = f"{check.file}: {check.field}: "
            elif check.field:
                location = f"{check.field}: "
            elif check.file:
                location = f"{check.file}: "
            lines.append(f"{check.status:4} {check.id:30} {location}{check.detail}")
        counts = self.counts()
        lines.append("")
        lines.append(
            "summary: "
            f"pass={counts['pass']} fail={counts['fail']} "
            f"warn={counts['warn']} skip={counts['skip']} "
            f"-> {self.verdict.upper()}"
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Manifest loading
# --------------------------------------------------------------------------- #


class ContractFailure(Exception):
    """A manifest problem with a file/field/reason attached."""

    def __init__(self, reason: str, *, file: str | Path = "", field: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.file = str(file) if file else ""
        self.field = field


@dataclass
class DeviceView:
    """A device manifest flattened for the contract checks."""

    file: Path
    schema: str
    id: str
    capabilities: list[str]
    raw: dict[str, Any]
    wake: Any = None
    runtime_mode: str | None = None
    params_keys: list[str] = field(default_factory=list)
    install_steps: list[str] = field(default_factory=list)
    model: str = ""

    def capability_set(self) -> set[str]:
        return set(self.capabilities)


@dataclass
class PluginView:
    root: Path
    plugin_id: str
    trust: str
    devices: list[DeviceView]
    raw: dict[str, Any]
    loader: str = "core"


def _read_doc(path: Path) -> dict[str, Any]:
    """Safe, bounded YAML read with a file/field-shaped error."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ContractFailure(f"manifest not found: {path}", file=path) from None
    except OSError as exc:
        raise ContractFailure(f"cannot read manifest {path}: {_brief(exc)}", file=path) from None
    if size > MAX_MANIFEST_BYTES:
        raise ContractFailure(
            f"manifest is {size} bytes; the limit is {MAX_MANIFEST_BYTES}", file=path
        )
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ContractFailure(f"manifest not found: {path}", file=path) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ContractFailure(f"cannot read manifest {path}: {_brief(exc)}", file=path) from None
    if not text.strip():
        raise ContractFailure("manifest is empty", file=path)
    try:
        raw = YAML(typ="safe").load(text)
    except Exception as exc:  # noqa: BLE001 - any parser failure is user input
        raise ContractFailure(f"cannot parse manifest: {_brief(exc)}", file=path) from None
    if not isinstance(raw, dict):
        raise ContractFailure("expected a YAML mapping at the top level", file=path)
    return raw


def _brief(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:300] + ("..." if len(text) > 300 else "")


def _safe_member(root: Path, entry: str, plugin_file: Path) -> Path:
    """Resolve a ``devices:`` entry without allowing escapes, like the loader."""
    if not isinstance(entry, str):
        raise ContractFailure("device manifest path must be a string", file=plugin_file,
                              field="devices")
    relative = Path(entry)
    if relative.is_absolute():
        raise ContractFailure(
            f"device manifest {entry!r} must be relative to the plugin directory",
            file=plugin_file,
            field="devices",
        )
    try:
        target = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractFailure(
            f"cannot resolve device manifest {entry!r}: {_brief(exc)}",
            file=plugin_file,
            field="devices",
        ) from None
    try:
        target.relative_to(root)
    except ValueError:
        raise ContractFailure(
            f"device manifest {entry!r} escapes the plugin directory",
            file=plugin_file,
            field="devices",
        ) from None
    return target


def _format_validation(exc: Any, source: Path, *, prefix: str = "") -> str:
    lines: list[str] = []
    errors = exc.errors()
    for error in errors[:_MAX_ERRORS]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        if prefix:
            location = f"{prefix}.{location}" if location != "<root>" else prefix
        line = f"{location}: {error['msg']}"
        raw = error.get("input")
        if isinstance(raw, (str, int, float, bool)):
            line += f" (got {raw!r})"
        lines.append(line)
    if len(errors) > _MAX_ERRORS:
        lines.append(f"... and {len(errors) - _MAX_ERRORS} more validation error(s)")
    return f"{source}: " + "\n".join(lines)


def _core_supports_v1() -> bool:
    """Whether the core models in this build know the v1 device schema.

    RUI 0.2.x predates v1 (``Capability.DEVICE_WAKE``, ``DeviceManifest.wake``).
    On that tree the kit still has to validate a v1 example, so it applies a
    small compatibility layer. Once the 0.3 models land, the real loader is the
    single source of truth and this returns True.
    """
    try:
        from ..device.contract import Capability as CoreCapability

        if "DEVICE_WAKE" in getattr(CoreCapability, "__members__", {}):
            return True
    except Exception:  # noqa: BLE001 - a future refactor must not break the kit
        pass
    try:
        fields = getattr(DeviceManifest, "model_fields", {})
        if "wake" in fields:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import models as core_models

        schema = getattr(core_models, "DEVICE_SCHEMA", "")
        if schema == "rui-device/v1":
            return True
        if isinstance(schema, (tuple, list, set, frozenset)) and "rui-device/v1" in schema:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _raw_is_v1(root: Path) -> bool:
    """Best-effort scan for a v1 device file; never raises."""
    try:
        plugin_file = root / "plugin.yaml"
        plugin = _read_doc(plugin_file)
        entries = plugin.get("devices")
        if not isinstance(entries, list):
            return False
        for entry in entries:
            if not isinstance(entry, str):
                continue
            try:
                device_file = _safe_member(root, entry, plugin_file)
                device = _read_doc(device_file)
            except ContractFailure:
                continue
            if device.get("schema") == "rui-device/v1":
                return True
    except Exception:  # noqa: BLE001 - fallback is best-effort by design
        return False
    return False


def _load_view(root: Path, report: ContractReport) -> PluginView | None:
    try:
        core_plugin = load_plugin(root)
    except PluginError as exc:
        if _raw_is_v1(root) and not _core_supports_v1():
            try:
                view = _fallback_load(root)
            except ContractFailure as failure:
                report.add(
                    "manifest.load",
                    FAIL,
                    failure.reason,
                    file=failure.file,
                    field=failure.field,
                )
                return None
            report.loader = "kit-v1-compat"
            report.add(
                "manifest.load",
                PASS,
                "validated with the kit's v1 compatibility layer "
                "(this RUI predates rui-device/v1; the 0.3 loader will replace it)",
            )
            return view
        report.loader = "core"
        report.add("manifest.load", FAIL, str(exc))
        return None

    report.loader = "core"
    report.add("manifest.load", PASS, f"loaded by rui.plugins.load_plugin ({rui.__version__})")
    return _view_from_core(core_plugin, Path(core_plugin.source_dir))


def _view_from_core(core_plugin: Any, root: Path) -> PluginView:
    """Flatten a loaded :class:`Plugin` and read raw docs for v1-only fields."""
    raw_plugin: dict[str, Any] = {}
    raw_devices: dict[str, tuple[Path, dict[str, Any]]] = {}
    try:
        plugin_file = root / "plugin.yaml"
        raw_plugin = _read_doc(plugin_file)
        entries = raw_plugin.get("devices") or []
        for entry in entries:
            if not isinstance(entry, str):
                continue
            try:
                device_file = _safe_member(root, entry, plugin_file)
                raw = _read_doc(device_file)
            except ContractFailure:
                continue
            if isinstance(raw.get("id"), str):
                raw_devices[raw["id"]] = (device_file, raw)
    except ContractFailure:
        pass

    devices: list[DeviceView] = []
    for device in core_plugin.devices:
        device_file, raw = raw_devices.get(device.id, (root / "devices" / f"{device.id}.yaml", {}))
        wake = _normalise_core_wake(device, raw)
        runtime = getattr(device, "runtime", None)
        mode = getattr(runtime, "mode", None) if runtime is not None else None
        devices.append(
            DeviceView(
                file=device_file,
                schema=str(getattr(device, "schema", raw.get("schema", "rui-device/v0"))),
                id=device.id,
                capabilities=[str(capability) for capability in device.capabilities],
                raw=raw,
                wake=wake,
                runtime_mode=str(mode) if mode is not None else None,
                params_keys=[param.key for param in device.params],
                install_steps=(
                    list(device.device_side.install_steps)
                    if getattr(device, "device_side", None) is not None
                    else []
                ),
                model=str(getattr(device, "model", "") or raw.get("model", "")),
            )
        )
    return PluginView(
        root=root,
        plugin_id=core_plugin.id,
        trust=str(core_plugin.trust),
        devices=devices,
        raw=raw_plugin,
        loader="core",
    )


def _normalise_core_wake(device: Any, raw: dict[str, Any]) -> dict[str, Any] | None:
    raw_wake = raw.get("wake") if isinstance(raw, dict) else None
    if isinstance(raw_wake, dict):
        return dict(raw_wake)
    wake = getattr(device, "wake", None)
    if wake is None:
        return None
    if isinstance(wake, dict):
        return dict(wake)
    return {
        "source": str(getattr(wake, "source", "rui")),
        "barge_via_device": bool(getattr(wake, "barge_via_device", False)),
    }


def _fallback_load(root: Path) -> PluginView:
    """Validate v1 on a pre-v1 core without importing or executing anything.

    The existing pydantic models validate every v0 field; the two v1 additions
    (``model`` and ``wake``) are stripped before that call and checked by the
    same semantic rules the 0.3 loader will use. This keeps the kit runnable on
    the 0.2.2 tree without editing ``plugins/models.py``.
    """
    try:
        resolved = root.expanduser().resolve(strict=True)
    except FileNotFoundError:
        raise ContractFailure(f"plugin directory not found: {root}", file=root) from None
    except (OSError, RuntimeError, ValueError) as exc:
        raise ContractFailure(f"cannot open plugin directory {root}: {_brief(exc)}",
                              file=root) from None
    if not resolved.is_dir():
        raise ContractFailure(f"plugin path is not a directory: {root}", file=root)

    plugin_file = resolved / "plugin.yaml"
    raw_plugin = _read_doc(plugin_file)
    try:
        manifest = PluginManifest.model_validate(raw_plugin)
    except Exception as exc:  # pydantic ValidationError, kept duck-typed
        raise ContractFailure(_format_validation(exc, plugin_file), file=plugin_file) from None

    running = running_version()
    try:
        required = parse_range(manifest.rui_compat)
    except VersionError as exc:
        raise ContractFailure(str(exc), file=plugin_file, field="rui_compat") from None
    if not required.matches(running):
        raise ContractFailure(
            f"plugin {manifest.id!r} requires RUI {manifest.rui_compat}, "
            f"but this build is {running}",
            file=plugin_file,
            field="rui_compat",
        )

    trust = str(manifest.trust)
    devices: list[DeviceView] = []
    seen_ids: set[str] = set()
    for entry in manifest.devices:
        device_file = _safe_member(resolved, entry, plugin_file)
        raw = _read_doc(device_file)
        schema = raw.get("schema")
        if schema not in {"rui-device/v0", "rui-device/v1"}:
            raise ContractFailure(
                f"schema must be 'rui-device/v0' or 'rui-device/v1' (got {schema!r})",
                file=device_file,
                field="schema",
            )
        if schema == "rui-device/v0":
            for v1_field in ("wake", "model"):
                if v1_field in raw:
                    raise ContractFailure(
                        f"schema rui-device/v0 must not declare {v1_field}; "
                        "use rui-device/v1",
                        file=device_file,
                        field=v1_field,
                    )
        if "model" in raw and not isinstance(raw["model"], str):
            raise ContractFailure(
                f"model must be a string (got {type(raw['model']).__name__})",
                file=device_file,
                field="model",
            )

        raw_caps = raw.get("capabilities")
        if not isinstance(raw_caps, list):
            raise ContractFailure("capabilities must be a list", file=device_file,
                                  field="capabilities")
        for capability in raw_caps:
            if not isinstance(capability, str) or capability not in ALL_CAPABILITY_NAMES:
                raise ContractFailure(
                    f"unknown capability {capability!r}; allowed values are "
                    + ", ".join(sorted(ALL_CAPABILITY_NAMES)),
                    file=device_file,
                    field="capabilities",
                )

        clean = dict(raw)
        clean["schema"] = "rui-device/v0"
        clean.pop("wake", None)
        clean.pop("model", None)
        # The 0.2.2 enum does not know the two v1 capabilities. They are checked
        # semantically below; strip them only for the v0-shaped base validation.
        clean["capabilities"] = [
            capability
            for capability in raw_caps
            if capability not in V1_ONLY_CAPABILITIES
        ]
        try:
            device = DeviceManifest.model_validate(clean)
        except Exception as exc:  # pydantic ValidationError, kept duck-typed
            raise ContractFailure(_format_validation(exc, device_file), file=device_file) from None

        if device.id in seen_ids:
            raise ContractFailure(f"duplicate device id {device.id!r}", file=device_file,
                                  field="id")
        seen_ids.add(device.id)

        _check_resolved_runtime(device, trust, device_file)
        if device.compatibility and device.compatibility.rui_min:
            try:
                minimum = parse_version(device.compatibility.rui_min, allow_partial=True)
            except VersionError as exc:
                raise ContractFailure(str(exc), file=device_file,
                                      field="compatibility.rui_min") from None
            if running < minimum:
                raise ContractFailure(
                    f"device {device.id!r} needs RUI >= {minimum} "
                    f"(compatibility.rui_min), but this build is {running}",
                    file=device_file,
                    field="compatibility.rui_min",
                )

        wake = raw.get("wake")
        devices.append(
            DeviceView(
                file=device_file,
                schema=str(schema),
                id=device.id,
                capabilities=[str(capability) for capability in raw_caps],
                raw=raw,
                wake=wake,
                runtime_mode=(
                    str(device.runtime.mode) if device.runtime and device.runtime.mode else None
                ),
                params_keys=[param.key for param in device.params],
                install_steps=(
                    list(device.device_side.install_steps)
                    if device.device_side is not None
                    else []
                ),
                model=str(raw.get("model", "")),
            )
        )

    return PluginView(
        root=resolved,
        plugin_id=manifest.id,
        trust=trust,
        devices=devices,
        raw=raw_plugin,
        loader="kit-v1-compat",
    )


def _check_resolved_runtime(device: Any, trust: str, device_file: Path) -> None:
    runtime = device.runtime
    if runtime is None:
        return
    mode = runtime.mode
    if mode is None:
        mode = (
            RuntimeMode.SIDECAR
            if trust == str(TrustLevel.THIRD_PARTY)
            else RuntimeMode.IN_PROCESS
        )
    if trust == str(TrustLevel.THIRD_PARTY) and mode is RuntimeMode.IN_PROCESS:
        raise ContractFailure(
            "third-party plugins may not use runtime.mode 'in-process'; "
            "use 'sidecar' or get the plugin reviewed as builtin/trusted",
            file=device_file,
            field="runtime.mode",
        )
    if mode is RuntimeMode.SIDECAR and not runtime.image:
        raise ContractFailure("runtime.image is required for mode 'sidecar'",
                              file=device_file, field="runtime.image")
    if mode is RuntimeMode.IN_PROCESS and not runtime.entrypoint:
        raise ContractFailure("runtime.entrypoint is required for mode 'in-process'",
                              file=device_file, field="runtime.entrypoint")


# --------------------------------------------------------------------------- #
# Manifest semantic checks
# --------------------------------------------------------------------------- #


def _check_manifest(view: PluginView, report: ContractReport) -> None:
    _check_devices(view, report)
    _check_compatibility(view, report)
    _check_capabilities(view, report)
    _check_wake(view, report)
    _check_runtime(view, report)
    _check_wake_words(view, report)


def _check_devices(view: PluginView, report: ContractReport) -> None:
    ids = [device.id for device in view.devices]
    duplicates = sorted({device_id for device_id in ids if ids.count(device_id) > 1})
    if not view.devices:
        report.add("manifest.devices", FAIL, "the plugin declares no devices",
                   file=view.root / "plugin.yaml", field="devices")
    elif duplicates:
        report.add(
            "manifest.devices",
            FAIL,
            "duplicate device id(s): " + ", ".join(duplicates),
            file=view.root / "plugin.yaml",
            field="devices",
        )
    else:
        report.add(
            "manifest.devices",
            PASS,
            f"{len(view.devices)} device(s): " + ", ".join(ids),
        )


def _check_compatibility(view: PluginView, report: ContractReport) -> None:
    plugin_file = view.root / "plugin.yaml"
    compat = view.raw.get("rui_compat")
    if not isinstance(compat, str) or not compat.strip():
        report.add("manifest.compatibility", FAIL, "rui_compat is missing",
                   file=plugin_file, field="rui_compat")
        return
    try:
        required = parse_range(compat)
        running = running_version()
    except (VersionError, PluginError) as exc:
        report.add("manifest.compatibility", FAIL, str(exc),
                   file=plugin_file, field="rui_compat")
        return
    if required.matches(running):
        report.add(
            "manifest.compatibility",
            PASS,
            f"requires {compat}; this build is {running}",
        )
    else:
        report.add(
            "manifest.compatibility",
            FAIL,
            f"requires RUI {compat}, but this build is {running}",
            file=plugin_file,
            field="rui_compat",
        )


def _check_capabilities(view: PluginView, report: ContractReport) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    first_file: Path | None = None
    for device in view.devices:
        caps = device.capabilities
        location = f"{device.file}: capabilities"
        if len(set(caps)) != len(caps):
            duplicates = sorted({cap for cap in caps if caps.count(cap) > 1})
            errors.append(f"{location}: duplicate value(s): {', '.join(duplicates)}")
            first_file = first_file or device.file
        for capability in caps:
            if capability not in ALL_CAPABILITY_NAMES:
                errors.append(f"{location}: unknown capability {capability!r}")
                first_file = first_file or device.file
        capability_set = set(caps)
        if "barge_via_device" in capability_set and not (
            {"device_wake", "playback"} <= capability_set
        ):
            errors.append(
                f"{location}: barge_via_device requires both device_wake and playback"
            )
            first_file = first_file or device.file
        if device.schema == "rui-device/v1":
            if "native_kws" in capability_set:
                errors.append(
                    f"{location}: native_kws is deprecated in rui-device/v1; "
                    "use device_wake (or keep the vendor wake in drain_signals)"
                )
                first_file = first_file or device.file
            if "streaming_asr" in capability_set:
                warnings.append(
                    f"{device.file}: capabilities: streaming_asr is reserved in v1 and "
                    "has no defined behavior"
                )
        else:
            v1_only = sorted(capability_set & V1_ONLY_CAPABILITIES)
            if v1_only:
                errors.append(
                    f"{location}: rui-device/v0 must not declare {', '.join(v1_only)}"
                )
                first_file = first_file or device.file
    if errors:
        report.add(
            "manifest.capabilities",
            FAIL,
            "\n".join(errors),
            file=first_file or view.root / "plugin.yaml",
            field="capabilities",
        )
    elif warnings:
        report.warnings.extend(warnings)
        report.add("manifest.capabilities", WARN, "\n".join(warnings))
    else:
        report.add("manifest.capabilities", PASS, "capabilities match the v1 vocabulary")


def _wake_block(device: DeviceView) -> Any:
    raw_wake = device.raw.get("wake") if isinstance(device.raw, dict) else None
    if raw_wake is not None:
        return raw_wake
    return device.wake


def _wake_values(device: DeviceView) -> tuple[str, bool, list[str]]:
    """Return (source, barge, errors) with v1 defaults applied."""
    wake = _wake_block(device)
    if wake is None:
        return "rui", False, []
    if not isinstance(wake, dict):
        return "rui", False, [
            f"{device.file}: wake: must be a mapping (got {type(wake).__name__})"
        ]
    errors: list[str] = []
    allowed = {"source", "barge_via_device"}
    for key in wake:
        if key not in allowed:
            errors.append(f"{device.file}: wake.{key}: unknown field (allowed: source, barge_via_device)")
    for key in wake:
        lowered = str(key).lower()
        if any(word in lowered for word in ("token", "secret", "password")) or lowered in {
            "ip",
            "host",
            "address",
        }:
            errors.append(
                f"{device.file}: wake.{key}: the wake block must not carry tokens, "
                "addresses or secrets; use params.host"
            )
    source = wake.get("source", "rui")
    if not isinstance(source, str) or source not in {"device", "rui"}:
        errors.append(
            f"{device.file}: wake.source: must be 'device' or 'rui' (got {source!r})"
        )
        source = "rui"
    barge = wake.get("barge_via_device", False)
    if not isinstance(barge, bool):
        errors.append(
            f"{device.file}: wake.barge_via_device: must be a boolean (got {barge!r})"
        )
        barge = False
    return source, barge, errors


def _check_wake(view: PluginView, report: ContractReport) -> None:
    errors: list[str] = []
    first_file: Path | None = None
    for device in view.devices:
        source, barge, block_errors = _wake_values(device)
        if block_errors:
            errors.extend(block_errors)
            first_file = first_file or device.file
        capability_set = device.capability_set()
        if device.schema == "rui-device/v0" and _wake_block(device) is not None:
            errors.append(
                f"{device.file}: wake: rui-device/v0 must not declare wake; use rui-device/v1"
            )
            first_file = first_file or device.file
            continue
        if source == "device":
            if "device_wake" not in capability_set:
                errors.append(
                    f"{device.file}: wake.source: 'device' requires capabilities.device_wake"
                )
                first_file = first_file or device.file
            if barge:
                missing = [
                    capability
                    for capability in ("device_wake", "barge_via_device", "playback")
                    if capability not in capability_set
                ]
                if missing:
                    errors.append(
                        f"{device.file}: wake.barge_via_device: true requires "
                        f"capabilities {', '.join(missing)} (plus source: device)"
                    )
                    first_file = first_file or device.file
        else:
            forbidden = sorted(capability_set & V1_ONLY_CAPABILITIES)
            if forbidden:
                errors.append(
                    f"{device.file}: wake.source: 'rui' forbids capabilities "
                    f"{', '.join(forbidden)}"
                )
                first_file = first_file or device.file
            if barge:
                errors.append(
                    f"{device.file}: wake.barge_via_device: true requires wake.source: device"
                )
                first_file = first_file or device.file
        if "device_wake" in capability_set and source != "device":
            errors.append(
                f"{device.file}: capabilities.device_wake requires wake.source: device"
            )
            first_file = first_file or device.file
        if "barge_via_device" in capability_set and not (
            {"device_wake", "playback"} <= capability_set
        ):
            # Already reported under capabilities; keep this one for the wake
            # section so a reader looking at wake sees the dependency too.
            errors.append(
                f"{device.file}: capabilities.barge_via_device requires device_wake + playback"
            )
            first_file = first_file or device.file
    if errors:
        report.add(
            "manifest.wake",
            FAIL,
            "\n".join(errors),
            file=first_file or view.root / "plugin.yaml",
            field="wake",
        )
    else:
        report.add("manifest.wake", PASS, "capability/wake dependencies are consistent")


def _declares_wake_words(device: DeviceView) -> bool:
    if "wake_words" in device.params_keys:
        return True
    for step in device.install_steps:
        lowered = step.lower()
        if "唤醒" in step or "wake word" in lowered or "wake-word" in lowered:
            return True
    return False


def _check_runtime(view: PluginView, report: ContractReport) -> None:
    errors: list[str] = []
    warnings: list[str] = []
    first_file: Path | None = None
    trust = view.trust
    for device in view.devices:
        mode = device.runtime_mode
        runtime = device.raw.get("runtime") if isinstance(device.raw, dict) else None
        runtime = runtime if isinstance(runtime, dict) else {}
        if trust == str(TrustLevel.THIRD_PARTY) and mode == str(RuntimeMode.IN_PROCESS):
            errors.append(
                f"{device.file}: runtime.mode: third-party plugins must use 'sidecar', "
                "not 'in-process'"
            )
            first_file = first_file or device.file
        if mode == str(RuntimeMode.SIDECAR) and not runtime.get("image"):
            errors.append(f"{device.file}: runtime.image: required for mode 'sidecar'")
            first_file = first_file or device.file
        if mode == str(RuntimeMode.IN_PROCESS) and not runtime.get("entrypoint"):
            errors.append(f"{device.file}: runtime.entrypoint: required for mode 'in-process'")
            first_file = first_file or device.file
        if report.adapter and mode == str(RuntimeMode.SIDECAR):
            warnings.append(
                f"{device.file}: runtime.mode is 'sidecar'; the kit imported the adapter "
                "in-process only for this smoke test (the real plugin stays in its container)"
            )
        if report.adapter and mode is None and trust == str(TrustLevel.THIRD_PARTY):
            warnings.append(
                f"{device.file}: third-party plugin has no runtime block; the adapter "
                "smoke test is a local in-process check, not a runtime trust decision"
            )
    if errors:
        report.add(
            "manifest.runtime",
            FAIL,
            "\n".join(errors),
            file=first_file or view.root / "plugin.yaml",
            field="runtime",
        )
    elif warnings:
        report.warnings.extend(warnings)
        report.add("manifest.runtime", WARN, "\n".join(warnings))
    else:
        report.add("manifest.runtime", PASS, "trust and runtime mode are consistent")


def _check_wake_words(view: PluginView, report: ContractReport) -> None:
    notes = []
    for device in view.devices:
        source, _barge, _errors = _wake_values(device)
        if source == "device" and not _declares_wake_words(device):
            notes.append(
                f"{device.file}: wake.source: device should declare params.wake_words "
                "or install steps that configure the same words on the device"
            )
    if notes:
        report.add("manifest.wake_words", WARN, "\n".join(notes))
    else:
        report.add("manifest.wake_words", PASS, "device wake words are declared or configured")


# --------------------------------------------------------------------------- #
# Adapter loader and smoke contract
# --------------------------------------------------------------------------- #


def _split_adapter(spec: str) -> tuple[str, str]:
    module, separator, attribute = spec.rpartition(":")
    if not separator or not module or not attribute:
        raise ContractFailure(
            f"adapter {spec!r} must look like 'module:attr' or 'path/to/file.py:attr'",
            field="adapter",
        )
    if ":" in attribute:
        raise ContractFailure(f"adapter attribute {attribute!r} is not a single name",
                              field="adapter")
    return module, attribute


def _import_target(module: str, attribute: str, root: Path) -> Any:
    """Import lazily; a filesystem path is supported for standalone examples."""
    if module.endswith(".py") or "/" in module or "\\" in module:
        path = Path(module).expanduser()
        if not path.is_absolute():
            candidate = Path.cwd() / path
            if candidate.exists():
                path = candidate
            else:
                path = root / path
        try:
            path = path.resolve(strict=True)
        except FileNotFoundError:
            raise ContractFailure(f"adapter file not found: {module}", field="adapter") from None
        module_name = f"_rui_plug_contract_{abs(hash(path))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ContractFailure(f"cannot load adapter module from {path}", field="adapter")
        loaded = importlib.util.module_from_spec(spec)
        # A path-loaded adapter may import a sibling module from its directory.
        old_path = list(sys.path)
        sys.path.insert(0, str(path.parent))
        try:
            spec.loader.exec_module(loaded)
        finally:
            sys.path[:] = old_path
        target = getattr(loaded, attribute, None)
    else:
        try:
            loaded = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            # A plugin usually keeps its adapter next to plugin.yaml. Retry only
            # when the missing module is the requested one (or its package), not
            # when a *dependency* of an installed module is missing.
            missing_top = str(exc.name or "")
            if missing_top != module and not module.startswith(missing_top + "."):
                raise ContractFailure(
                    f"cannot import adapter module {module!r}: "
                    f"{type(exc).__name__}: {exc}",
                    field="adapter",
                ) from None
            importlib.invalidate_caches()
            old_path = list(sys.path)
            sys.path.insert(0, str(root))
            try:
                loaded = importlib.import_module(module)
            except Exception as retry_exc:  # noqa: BLE001
                raise ContractFailure(
                    f"cannot import adapter module {module!r} "
                    f"(tried sys.path and {root}): "
                    f"{type(retry_exc).__name__}: {retry_exc}",
                    field="adapter",
                ) from None
            finally:
                sys.path[:] = old_path
        except Exception as exc:  # noqa: BLE001 - report import failures, do not crash
            raise ContractFailure(
                f"cannot import adapter module {module!r}: {type(exc).__name__}: {exc}",
                field="adapter",
            ) from None
        target = getattr(loaded, attribute, None)
    if target is None:
        raise ContractFailure(
            f"adapter entry point {module}:{attribute} does not exist", field="adapter"
        )
    return target


def _fake_device(device: DeviceView) -> SimpleNamespace:
    return SimpleNamespace(
        id=device.id,
        adapter=device.id,
        model=device.model,
        host="127.0.0.1",
        room="contract-test",
        params={"host": "127.0.0.1"},
        device_id=device.id,
    )


def _fake_config(root: Path) -> SimpleNamespace:
    return SimpleNamespace(data_dir=str(root), config_path=None, devices=[], plugs_dir=str(root))


def _build_adapter(target: Any, device: DeviceView, root: Path) -> Any:
    if not callable(target):
        return target
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        signature = None
    required = 0
    positional = 0
    if signature is not None:
        for parameter in signature.parameters.values():
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                positional += 1
                if parameter.default is inspect.Parameter.empty:
                    required += 1
    fake_device = _fake_device(device)
    fake_config = _fake_config(root)
    if signature is not None:
        if required > 2:
            raise ContractFailure(
                f"adapter factory takes {required} required positional argument(s); "
                "use build() or build(device) or build(device, config)",
                field="adapter",
            )
        if positional >= 2:
            result = target(fake_device, fake_config)
        elif positional == 1:
            result = target(fake_device)
        else:
            result = target()
    else:  # pragma: no cover - exotic callables
        try:
            result = target()
        except TypeError:
            try:
                result = target(fake_device)
            except TypeError:
                result = target(fake_device, fake_config)
    return result


def _adapter_methods(adapter: Any) -> tuple[list[str], list[str]]:
    missing = [
        name for name in REQUIRED_ADAPTER_METHODS if not callable(getattr(adapter, name, None))
    ]
    wrong = [
        name
        for name in SYNC_DRAIN_METHODS
        if callable(getattr(adapter, name, None))
        and inspect.iscoroutinefunction(getattr(adapter, name))
    ]
    return missing, wrong


async def _call(fn: Any, *args: Any, timeout: float = _CALL_TIMEOUT_S, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=timeout)
    return result


def _run_adapter(view: PluginView, spec: str, report: ContractReport) -> None:
    try:
        module, attribute = _split_adapter(spec)
        target = _import_target(module, attribute, view.root)
    except ContractFailure as failure:
        report.add("adapter.import", FAIL, failure.reason, field=failure.field or "adapter")
        return
    except Exception as exc:  # noqa: BLE001 - adapter import is user code
        report.add(
            "adapter.import",
            FAIL,
            f"{type(exc).__name__}: {exc}",
            field="adapter",
        )
        return
    report.add("adapter.import", PASS, f"{spec} imported lazily")

    try:
        adapter = _build_adapter(target, view.devices[0], view.root)
    except ContractFailure as failure:
        report.add("adapter.construct", FAIL, failure.reason, field="adapter")
        return
    except Exception as exc:  # noqa: BLE001 - adapter factory is user code
        report.add("adapter.construct", FAIL, f"{type(exc).__name__}: {exc}", field="adapter")
        return
    if inspect.isawaitable(adapter):
        try:
            adapter = asyncio.run(adapter)
        except Exception as exc:  # noqa: BLE001
            report.add("adapter.construct", FAIL, f"{type(exc).__name__}: {exc}",
                       field="adapter")
            return
    report.add("adapter.construct", PASS, f"built {type(adapter).__name__}")

    missing, wrong = _adapter_methods(adapter)
    if missing or wrong:
        details = []
        if missing:
            details.append("missing callable method(s): " + ", ".join(missing))
        if wrong:
            details.append(
                "must be synchronous (not async): " + ", ".join(wrong)
            )
        report.add("adapter.methods", FAIL, "; ".join(details), field="adapter")
        # Still run what is safe; methods that are missing are skipped below.
    else:
        report.add("adapter.methods", PASS, "DeviceAdapter surface incl. drain_wakes")
    try:
        asyncio.run(_smoke(adapter, view, report))
    except RuntimeError as exc:
        # e.g. called from an existing event loop. The CLI never does this.
        report.add("adapter.smoke", FAIL, f"cannot run async smoke test: {exc}",
                   field="adapter")
    except Exception as exc:  # noqa: BLE001 - an adapter must not crash the kit
        report.add("adapter.smoke", FAIL, f"{type(exc).__name__}: {exc}", field="adapter")


def _caps_of(info: Any) -> set[str] | None:
    capabilities = getattr(info, "capabilities", None)
    if capabilities is None:
        return None
    try:
        return {str(capability) for capability in capabilities}
    except TypeError:
        return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


async def _smoke(adapter: Any, view: PluginView, report: ContractReport) -> None:
    device = view.devices[0]
    manifest_caps = device.capability_set()
    info: Any = None
    opened = False
    try:
        # --- probe ---------------------------------------------------------
        try:
            info = await _call(adapter.probe, timeout=_PROBE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            report.add("device.ready", FAIL, f"probe() raised {type(exc).__name__}: {exc}")
        else:
            info_id = str(getattr(info, "id", ""))
            transport = str(getattr(info, "transport", ""))
            channels = getattr(info, "channels", None)
            sample_rate = getattr(info, "sample_rate", None)
            if not info_id:
                report.add("device.ready", FAIL, "probe() returned no device id")
            elif not isinstance(channels, int) or channels <= 0:
                report.add("device.ready", FAIL, f"probe() channels={channels!r} is invalid")
            elif not isinstance(sample_rate, int) or sample_rate <= 0:
                report.add(
                    "device.ready", FAIL, f"probe() sample_rate={sample_rate!r} is invalid"
                )
            else:
                report.add(
                    "device.ready",
                    PASS,
                    f"id={info_id} transport={transport or '?'} "
                    f"{channels}ch @ {sample_rate} Hz",
                )
        if info is not None:
            _check_probe_layout(info, view, report)
        if any(
            check.id == "device.ready" and check.status == FAIL for check in report.checks
        ):
            return

        # --- open ----------------------------------------------------------
        try:
            await _call(adapter.open, timeout=_OPEN_TIMEOUT_S)
            opened = True
        except Exception as exc:  # noqa: BLE001
            report.add("device.open", FAIL, f"open() raised {type(exc).__name__}: {exc}")
            return
        report.add("device.open", PASS, "open() completed")

        # --- frames --------------------------------------------------------
        await _check_frames(adapter, info, report)

        # --- playback ------------------------------------------------------
        await _check_playback(adapter, info, manifest_caps, report)

        # --- polls ---------------------------------------------------------
        await _check_polls(adapter, report)

        # --- wake injection ------------------------------------------------
        await _check_wake_injection(adapter, view, info, report)

        # --- close ---------------------------------------------------------
        close_error = None
        try:
            await _call(adapter.close)
            await _call(adapter.close)
        except Exception as exc:  # noqa: BLE001
            close_error = f"close() raised {type(exc).__name__}: {exc}"
        if close_error:
            report.add("lifecycle.close", FAIL, close_error)
        else:
            report.add("lifecycle.close", PASS, "close() is idempotent")
            drain = getattr(adapter, "drain_wakes", None)
            if callable(drain):
                try:
                    after = drain()
                except Exception as exc:  # noqa: BLE001
                    report.add(
                        "lifecycle.after_close",
                        FAIL,
                        f"drain_wakes() after close raised {type(exc).__name__}: {exc}",
                    )
                else:
                    if after:
                        report.add(
                            "lifecycle.after_close",
                            FAIL,
                            f"drain_wakes() returned {len(after)} event(s) after close()",
                        )
                    else:
                        report.add(
                            "lifecycle.after_close",
                            PASS,
                            "drain_wakes() is empty after close()",
                        )
    finally:
        if opened and not any(
            check.id == "lifecycle.close" and check.status == PASS for check in report.checks
        ):
            try:
                await _call(adapter.close)
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass


def _check_probe_layout(info: Any, view: PluginView, report: ContractReport) -> None:
    device = view.devices[0]
    manifest_caps = device.capability_set()
    probe_caps = _caps_of(info)
    if probe_caps is None:
        report.add("device.probe_capabilities", WARN, "probe() has no capabilities set")
        return
    channels = getattr(info, "channels", 0)
    mic_channels = getattr(info, "mic_channels", None)
    ref_channels = getattr(info, "ref_channels", None)
    notes: list[str] = []
    failures: list[str] = []
    if isinstance(mic_channels, int):
        if mic_channels <= 0:
            failures.append(f"mic_channels={mic_channels}")
        if isinstance(ref_channels, int) and isinstance(channels, int):
            if mic_channels + ref_channels != channels:
                notes.append(
                    f"mic_channels({mic_channels}) + ref_channels({ref_channels}) "
                    f"!= channels({channels})"
                )
        if "mic_array" in manifest_caps and mic_channels <= 1:
            notes.append("manifest declares mic_array but probe reports a single mic")
    if isinstance(ref_channels, int) and "reference" in manifest_caps and ref_channels <= 0:
        notes.append("manifest declares reference but probe has ref_channels=0")
    missing = sorted((manifest_caps & PROBE_CAPABILITIES) - probe_caps)
    if missing:
        notes.append(
            "declared capability not in probe(): "
            + ", ".join(missing)
            + " (RUI degrades at runtime)"
        )
    extra = sorted((probe_caps & PROBE_CAPABILITIES) - manifest_caps)
    if extra:
        notes.append(
            "probe() reports undeclared capability: "
            + ", ".join(extra)
            + " (ignored, no escalation)"
        )
    if failures:
        report.add(
            "device.probe_layout",
            FAIL,
            "; ".join(failures),
            file=device.file,
            field="capabilities",
        )
    elif notes:
        report.add("device.probe_layout", WARN, "; ".join(notes))
    else:
        report.add("device.probe_layout", PASS, "probe() layout matches the manifest")
    if device.schema == "rui-device/v1" and "device_wake" in manifest_caps and (
        "device_wake" not in probe_caps
    ):
        report.add(
            "device.device_wake_effective",
            WARN,
            "manifest enables device_wake but probe() does not report it; "
            "RUI will fall back to KWS",
        )


async def _check_frames(adapter: Any, info: Any, report: ContractReport) -> None:
    frames = []
    gaps: list[float] = []
    errors: list[str] = []
    try:
        agen = adapter.frames()
    except Exception as exc:  # noqa: BLE001
        report.add("capture.frames", FAIL, f"frames() raised {type(exc).__name__}: {exc}")
        return
    if not hasattr(agen, "__anext__"):
        report.add("capture.frames", FAIL, "frames() did not return an async iterator")
        return
    try:
        for _index in range(_FRAME_COUNT):
            started = time.perf_counter()
            try:
                frame = await asyncio.wait_for(agen.__anext__(), timeout=_FRAME_TIMEOUT_S)
            except StopAsyncIteration:
                errors.append(f"frames() ended after {len(frames)} frame(s)")
                break
            except TimeoutError:
                errors.append(
                    f"frames() produced no frame within {_FRAME_TIMEOUT_S:g}s"
                )
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"frames() raised {type(exc).__name__}: {exc}")
                break
            gaps.append((time.perf_counter() - started) * 1000.0)
            frames.append(frame)
            _validate_frame(frame, info, errors)
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - best effort
            pass
    if errors:
        report.add("capture.frames", FAIL, "; ".join(errors))
        return
    durations = []
    for frame in frames:
        duration = getattr(frame, "duration_ms", None)
        if isinstance(duration, (int, float)):
            durations.append(float(duration))
    detail = f"{len(frames)} frame(s), "
    if durations:
        detail += f"~{sum(durations) / len(durations):.1f} ms each"
    else:
        detail += "async iterator alive"
    report.add("capture.frames", PASS, detail)
    max_gap = max(gaps) if gaps else 0.0
    if max_gap > _FRAME_GAP_FAIL_MS:
        report.add(
            "capture.cadence",
            FAIL,
            f"frames() blocked for {max_gap:.0f} ms (limit {_FRAME_GAP_FAIL_MS:.0f} ms)",
        )
    elif max_gap > _FRAME_GAP_WARN_MS:
        report.add(
            "capture.cadence",
            WARN,
            f"max frame gap {max_gap:.0f} ms; §4.1 says yield at least every 100 ms",
        )
    else:
        report.add("capture.cadence", PASS, f"max frame gap {max_gap:.0f} ms")


def _validate_frame(frame: Any, info: Any, errors: list[str]) -> None:
    pcm = getattr(frame, "pcm", None)
    if not isinstance(pcm, (bytes, bytearray, memoryview)) or not pcm:
        errors.append("frame.pcm is empty or not bytes")
        return
    channels = getattr(frame, "channels", None)
    rate = getattr(frame, "sample_rate", None)
    if not isinstance(channels, int) or channels <= 0:
        errors.append(f"frame.channels={channels!r} is invalid")
        return
    if not isinstance(rate, int) or rate <= 0:
        errors.append(f"frame.sample_rate={rate!r} is invalid")
        return
    ts = _finite_number(getattr(frame, "ts_ms", None))
    if ts is None:
        errors.append(f"frame.ts_ms={getattr(frame, 'ts_ms', None)!r} is not finite")
    if len(pcm) % (2 * channels) != 0:
        errors.append("frame.pcm is not interleaved int16 for frame.channels")
    reference = getattr(frame, "reference_channels", 0)
    if not isinstance(reference, int) or reference < 0 or reference > channels:
        errors.append(f"frame.reference_channels={reference!r} is invalid")
    info_channels = getattr(info, "channels", None) if info is not None else None
    if isinstance(info_channels, int) and info_channels != channels:
        errors.append(
            f"frame.channels={channels} differs from probe().channels={info_channels}"
        )
    samples = getattr(frame, "frame_samples", None)
    if samples is not None and (not isinstance(samples, int) or samples <= 0):
        errors.append(f"frame.frame_samples={samples!r} is invalid")


async def _check_playback(
    adapter: Any, info: Any, manifest_caps: set[str], report: ContractReport
) -> None:
    probe_caps = _caps_of(info) if info is not None else None
    if probe_caps is not None:
        playback = "playback" in probe_caps
    else:
        playback = "playback" in manifest_caps
    if not playback:
        report.add("playback.sink", SKIP, "no playback capability declared")
        return
    rate = getattr(info, "sample_rate", 16000) if info is not None else 16000
    # DeviceInfo describes the capture layout (LX06: 8 channels). Playback is a
    # separate sink and the common layout is stereo; use mono only for mono devices.
    capture_channels = getattr(info, "channels", 1) if info is not None else 1
    channels = 2 if isinstance(capture_channels, int) and capture_channels >= 2 else 1
    if not isinstance(rate, int) or rate <= 0:
        rate = 16000
    pcm = b"\x00" * (rate // 50 * channels * 2)
    before = getattr(adapter, "played", None)
    before_count = len(before) if isinstance(before, list) else None
    try:
        await _call(adapter.play, pcm, sample_rate=rate, channels=channels)
        await _call(adapter.stop_playback)
        await _call(adapter.stop_playback)
    except Exception as exc:  # noqa: BLE001
        report.add("playback.sink", FAIL, f"play()/stop_playback() raised "
                   f"{type(exc).__name__}: {exc}")
        return
    detail = f"accepted {len(pcm)} bytes at {rate} Hz/{channels}ch; stop_playback twice"
    after = getattr(adapter, "played", None)
    if before_count is not None and isinstance(after, list):
        if len(after) <= before_count:
            report.add(
                "playback.sink",
                FAIL,
                f"play() completed but the fake sink did not receive PCM "
                f"(played {before_count} -> {len(after)})",
            )
            return
        if after[-1] != pcm:
            report.add("playback.sink", FAIL, "the fake sink received different PCM bytes")
            return
        detail = f"sink received {len(pcm)} bytes; stop_playback twice"
    report.add("playback.sink", PASS, detail)


async def _check_polls(adapter: Any, report: ContractReport) -> None:
    for name in SYNC_DRAIN_METHODS:
        fn = getattr(adapter, name, None)
        if not callable(fn):
            report.add(f"poll.{name}", SKIP, f"{name}() is missing")
            continue
        started = time.perf_counter()
        events = None
        error = None
        for _ in range(2):
            try:
                events = fn()
            except Exception as exc:  # noqa: BLE001
                error = f"{name}() raised {type(exc).__name__}: {exc}"
                break
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if error:
            report.add(f"poll.{name}", FAIL, error)
            continue
        if not isinstance(events, (list, tuple)):
            report.add(
                f"poll.{name}", FAIL, f"{name}() must return a list (got {type(events).__name__})"
            )
            continue
        detail = f"returned {len(events)} event(s) twice in {elapsed_ms:.1f} ms"
        if len(events) > _DRAIN_QUEUE_WARN:
            report.add(
                f"poll.{name}",
                WARN,
                detail + f"; queue exceeds the {_DRAIN_QUEUE_WARN}-event guideline",
            )
        elif elapsed_ms > _DRAIN_WARN_MS:
            report.add(
                f"poll.{name}",
                WARN,
                detail + "; §4.2 expects <1 ms per call",
            )
        else:
            report.add(f"poll.{name}", PASS, detail)


def _event_field(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _validate_wake_event(event: Any, now_ms: float) -> list[str]:
    errors: list[str] = []
    ts = _finite_number(_event_field(event, "ts_ms"))
    if ts is None:
        errors.append("ts_ms is not a finite number")
    elif ts < now_ms - 1500 or ts > now_ms + 500:
        errors.append(f"ts_ms={ts:.1f} is outside the ±500/1500 ms window")
    score = _finite_number(_event_field(event, "score"))
    threshold = _finite_number(_event_field(event, "threshold"))
    if score is None or not 0.0 <= score <= 1.0:
        errors.append(f"score={_event_field(event, 'score')!r} is not in 0..1")
    if threshold is None or threshold <= 0:
        errors.append(f"threshold={_event_field(event, 'threshold')!r} is not > 0")
    if score is not None and threshold is not None and score < threshold:
        errors.append(f"score={score:.2f} is below threshold={threshold:.2f}")
    source = _event_field(event, "source", "device")
    if source != "device":
        errors.append(f"source={source!r} is not 'device'")
    barge = _event_field(event, "barge", False)
    if not isinstance(barge, bool):
        errors.append(f"barge={barge!r} is not a bool")
    level = _event_field(event, "level")
    if level is not None and _finite_number(level) is None:
        errors.append(f"level={level!r} is not finite")
    return errors


def _classify_wake(*, valid: bool, state: str, barge: bool, barge_allowed: bool) -> str:
    if not valid:
        return "ignore"
    if state == "idle":
        return "arm_gate"
    if state == "playing":
        if not barge:
            return "ignore"
        return "barge_stop" if barge_allowed else "suppress"
    return "ignore"


def _barge_allowed(view: PluginView, info: Any) -> bool:
    device = view.devices[0]
    source, barge, _errors = _wake_values(device)
    if source != "device" or not barge:
        return False
    required = {"device_wake", "barge_via_device", "playback"}
    if not required <= device.capability_set():
        return False
    probe_caps = _caps_of(info) if info is not None else None
    if probe_caps is not None and not required <= probe_caps:
        return False
    return True


async def _inject(adapter: Any, **values: Any) -> None:
    fn = getattr(adapter, "inject_wake", None)
    if not callable(fn):
        raise ContractFailure("adapter exposes no inject_wake() hook", field="inject_wake")
    result = fn(**values)
    if inspect.isawaitable(result):
        await result


async def _check_wake_injection(
    adapter: Any, view: PluginView, info: Any, report: ContractReport
) -> None:
    device = view.devices[0]
    manifest_caps = device.capability_set()
    source, barge_enabled, _errors = _wake_values(device)
    probe_caps = _caps_of(info) if info is not None else None
    wake_enabled = (
        "device_wake" in manifest_caps
        and source == "device"
        and (probe_caps is None or "device_wake" in probe_caps)
    )
    inject = getattr(adapter, "inject_wake", None)
    if not wake_enabled:
        report.add(
            "wake.inject",
            SKIP,
            "device_wake is not effective (manifest/probe); no safe wake injection",
        )
        return
    if not callable(inject):
        report.add(
            "wake.inject",
            SKIP,
            "adapter exposes no inject_wake() hook; use the fake fixture or add one "
            "(kit-only hook, not part of DeviceAdapter)",
        )
        return
    now_ms = time.monotonic() * 1000.0
    try:
        await _inject(
            adapter,
            ts_ms=now_ms,
            score=0.9,
            threshold=0.5,
            barge=False,
            level=-30.0,
        )
    except Exception as exc:  # noqa: BLE001
        report.add("wake.inject", FAIL, f"inject_wake() raised {type(exc).__name__}: {exc}")
        return
    report.add("wake.inject", PASS, "queued one valid idle DeviceWake")
    try:
        events = adapter.drain_wakes()
    except Exception as exc:  # noqa: BLE001
        report.add("wake.idle_trigger", FAIL, f"drain_wakes() raised {type(exc).__name__}: {exc}")
        return
    if not isinstance(events, list) or not events:
        report.add("wake.idle_trigger", FAIL, "drain_wakes() returned no event after injection")
        return
    event = events[-1]
    errors = _validate_wake_event(event, now_ms)
    action = _classify_wake(
        valid=not errors, state="idle", barge=bool(_event_field(event, "barge", False)),
        barge_allowed=False,
    )
    if errors:
        report.add("wake.idle_trigger", FAIL, "; ".join(errors))
    elif action != "arm_gate":
        report.add("wake.idle_trigger", FAIL, f"idle event classified as {action!r}")
    else:
        report.add(
            "wake.idle_trigger",
            PASS,
            "valid event -> arm_gate (VAD gate; no LLM/ASR/TTS is called by the kit)",
        )
    try:
        second = adapter.drain_wakes()
    except Exception as exc:  # noqa: BLE001
        report.add("wake.drain_once", FAIL, f"second drain_wakes() raised "
                   f"{type(exc).__name__}: {exc}")
    else:
        if second:
            report.add("wake.drain_once", FAIL, f"second drain returned {len(second)} event(s)")
        else:
            report.add("wake.drain_once", PASS, "events are returned once and cleared")

    if not barge_enabled:
        report.add("wake.barge_policy", SKIP, "wake.barge_via_device is not enabled")
        return
    allowed = _barge_allowed(view, info)
    if not allowed:
        report.add(
            "wake.barge_policy",
            SKIP,
            "barge is declared but not effective in probe(); runtime would suppress it",
        )
        return
    try:
        await _inject(
            adapter,
            ts_ms=time.monotonic() * 1000.0 + 10.0,
            score=0.92,
            threshold=0.5,
            barge=True,
            level=-20.0,
        )
        events = adapter.drain_wakes()
    except Exception as exc:  # noqa: BLE001
        report.add("wake.barge_policy", FAIL, f"barge injection failed: "
                   f"{type(exc).__name__}: {exc}")
        return
    if not isinstance(events, list) or not events:
        report.add("wake.barge_policy", FAIL, "drain_wakes() returned no barge event")
        return
    event = events[-1]
    errors = _validate_wake_event(event, time.monotonic() * 1000.0)
    action = _classify_wake(
        valid=not errors, state="playing",
        barge=bool(_event_field(event, "barge", False)), barge_allowed=True,
    )
    if errors:
        report.add("wake.barge_policy", FAIL, "; ".join(errors))
    elif action != "barge_stop":
        report.add("wake.barge_policy", FAIL, f"playing barge event classified as {action!r}")
    else:
        report.add(
            "wake.barge_policy",
            PASS,
            "barge=true during playback -> barge_stop (a suggestion; RUI still owns policy)",
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def run_contract(plugin_dir: str | Path, adapter: str | None = None) -> ContractReport:
    """Run manifest + optional adapter checks and return a report."""
    report = ContractReport(root=Path(plugin_dir).expanduser(), adapter=adapter)
    try:
        view = _load_view(report.root, report)
    except Exception as exc:  # noqa: BLE001 - a bad manifest must not traceback
        report.add(
            "manifest.load",
            FAIL,
            f"{type(exc).__name__}: {exc}",
            file=report.root,
        )
        return report
    if view is None:
        return report
    report.plugin_id = view.plugin_id
    report.trust = view.trust
    report.devices = [
        {
            "id": device.id,
            "file": str(device.file),
            "schema": device.schema,
            "capabilities": list(device.capabilities),
            "wake": dict(device.wake) if isinstance(device.wake, dict) else device.wake,
            "runtime": device.runtime_mode,
        }
        for device in view.devices
    ]
    try:
        _check_manifest(view, report)
    except Exception as exc:  # noqa: BLE001 - keep the report usable
        report.add("kit.error", FAIL, f"{type(exc).__name__}: {exc}")
        return report
    if adapter:
        if report.has_failed():
            report.add(
                "adapter.skipped",
                SKIP,
                "manifest checks failed; fix the manifest before testing the adapter",
            )
        else:
            _run_adapter(view, adapter, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=KIT_NAME,
        description="Validate a Socket & Plug v1 manifest and optional adapter contract",
    )
    parser.add_argument("plugin_dir", help="plugin directory containing plugin.yaml")
    parser.add_argument(
        "--adapter",
        default=None,
        metavar="MODULE:ATTR",
        help="lazy in-process adapter entry point, e.g. my_plug:build or ./adapter.py:build",
    )
    parser.add_argument("--json", action="store_true", help="emit contract-report/v1 JSON")
    args = parser.parse_args(argv)
    try:
        report = run_contract(args.plugin_dir, adapter=args.adapter)
    except KeyboardInterrupt:
        return 1
    except Exception as exc:  # noqa: BLE001 - the kit must always answer with a code
        if args.json:
            print(json.dumps({"kit": KIT_NAME, "error": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False, indent=2))
        else:
            print(f"{KIT_NAME}: error: {type(exc).__name__}: {exc}")
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_text())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
