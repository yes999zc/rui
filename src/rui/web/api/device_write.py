"""The device-list write invariant shared by the v1 and v2 consoles.

Every console write of ``devices[]`` - the v1 routes in ``app.py``, the v2 routes in
``v2_api.py`` and the wizard's ``device.upsert`` - funnels through
``app._write_devices``. ``save_config`` persists whatever model it is given, so a
delete or disable that dangles a ``multi_device`` reference (or removes the last
enabled speaker) would leave a file ``load_config`` refuses at the next boot.

This module owns that check once: reject a removed adapter spelling, repair what can
be repaired, revalidate the document a reload would read, and reject a port conflict
before any byte reaches disk. Errors are console-neutral (:class:`DeviceWriteError`);
v1 raises ``HTTPException`` from it and v2 converts it to its C-002 error object.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ...config.removed_adapters import removed_adapters_error
from ...config.schema import (
    AecConfig,
    AudioConfig,
    CaptureConfig,
    Config,
    DeviceConfig,
    PlaybackConfig,
    VoiceConfig,
    _explicit,
)
from .device_rules import port_conflict

__all__ = [
    "DeviceWriteError",
    "audio_blocks",
    "check_device_write",
    "checked_device_config",
    "config_fields",
    "explicit_audio_mapping",
    "field",
    "loc_path",
    "repair_device_writes",
    "validation_document",
]

log = logging.getLogger(__name__)


class DeviceWriteError(Exception):
    """A device write that must not reach disk, in a console-neutral shape."""

    def __init__(self, status_code: int, code: str, message: str, *,
                 hint: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.hint = hint
        self.details = dict(details or {})

    def http_detail(self) -> dict[str, Any]:
        """The v1 ``HTTPException.detail`` (v2 reads the attributes instead)."""
        detail: dict[str, Any] = {
            "code": self.code, "message": self.message, "details": self.details,
        }
        if self.hint:
            detail["hint"] = self.hint
        return detail


def field(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def loc_path(loc: tuple[Any, ...]) -> str:
    """A Pydantic error loc as a C-002 path: `devices[0].room`, not `devices.0.room`."""
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            text = str(part)
            path = f"{path}.{text}" if path else text
    return path


#: Cross-field messages whose dotted leaf the browser should highlight (C-002 §4.4).
_CONFIG_PATHS: tuple[tuple[str, str], ...] = (
    ("multi_device.default_output", "multi_device.default_output"),
    ("multi_device.arbitration.room_priority", "multi_device.arbitration.room_priority"),
    ("multi_device.enabled", "multi_device.enabled"),
    ("duplicate device id", "devices[0].id"),
    ("duplicate mcp server name", "tools.mcp_servers.name"),
    ("config version", "version"),
)


def config_error_path(message: str, merged: dict[str, Any], path: str) -> str:
    """Give a model-level validation error the dotted leaf the browser can highlight.

    Pydantic reports a `@model_validator` failure at the model's root (`""`,
    `"audio.capture"`, ...) because the value is only wrong in combination. C-002 §4.4
    still requires `details.fields[].path`, so the known cross-field rules map to the
    field an operator has to change.
    """
    devices = merged.get("devices")
    devices = devices if isinstance(devices, list) else []

    def device_index(device_id: str) -> int:
        for index, item in enumerate(devices):
            if isinstance(item, dict) and str(item.get("id")) == device_id:
                return index
        return 0

    if "must equal channels(" in message:
        return f"{path}.channels" if path else "audio.capture.channels"
    missing_room = message.split("requires devices[].room", 1)
    if len(missing_room) == 2:
        missing = missing_room[1].split("missing:", 1)[-1]
        first = next((part.strip() for part in missing.split(",") if part.strip()), "")
        return f"devices[{device_index(first)}].room"
    merged_audio = message.split("].audio does not merge", 1)
    if len(merged_audio) == 2 and message.startswith("devices["):
        device_id = message[len("devices["):].split("]", 1)[0]
        return f"devices[{device_index(device_id)}].audio"
    if message.startswith("duplicate device id:"):
        device_id = message.split(":", 1)[1].strip()
        for index, item in enumerate(devices):
            if isinstance(item, dict) and str(item.get("id")) == device_id:
                return f"devices[{index}].id"
        return "devices[0].id"
    if path:
        return path
    for needle, leaf in _CONFIG_PATHS:
        if needle in message:
            return leaf
    return path


def config_fields(exc: ValidationError, merged: dict[str, Any],
                  prefix: str = "") -> list[dict[str, str]]:
    """`details.fields[]` for a Config/audio-block validation error (C-002 §4.4)."""
    fields: list[dict[str, str]] = []
    for error in exc.errors(include_url=False):
        loc = loc_path(tuple(error.get("loc", ())))
        path = ".".join([part for part in (prefix, loc) if part])
        message = str(error.get("msg", "invalid value"))
        for noise in ("Value error, ", "Assertion failed, "):
            if message.startswith(noise):
                message = message[len(noise):]
        path = config_error_path(message, merged, path)
        kind = str(error.get("type", "invalid"))
        fields.append(field(path, "invalid" if kind == "value_error" else kind, message))
    return fields or [field(prefix, "invalid", "invalid values")]


def audio_blocks(audio: AudioConfig) -> dict[str, tuple[Any, Any]]:
    return {
        "capture": (CaptureConfig, audio.capture),
        "voice": (VoiceConfig, audio.voice),
        "playback": (PlaybackConfig, audio.playback),
        "aec": (AecConfig, audio.aec),
    }


def explicit_audio_mapping(audio: AudioConfig) -> dict[str, dict[str, Any]]:
    """The `audio` mapping a write must carry: only the fields actually chosen.

    `Config.model_dump()` would restate every field at its core default; saving that
    would freeze the neutral layout over the plug manifest.
    """
    out: dict[str, dict[str, Any]] = {}
    for name, (block_cls, block) in audio_blocks(audio).items():
        core = block_cls()
        part = {field: getattr(block, field) for field in block_cls.model_fields
                if _explicit(block, field, core)}
        if part:
            out[name] = part
    return out


def validation_document(config: Config) -> dict[str, Any]:
    """`config.model_dump()` with the audio block reduced to its explicit layer.

    `Config.model_validate` resolves every per-device override over the global block.
    A full dump marks every audio field as chosen, which would shadow the plug manifest
    while validating (and could reject a layout that a reload accepts). The saved file
    only holds the explicit layer, so that is the document a write must validate.
    """
    document = config.model_dump(mode="json")
    document["audio"] = explicit_audio_mapping(config.audio)
    if not document["audio"]:
        document.pop("audio")
    return document


def repair_device_writes(config: Config) -> Config:
    """The device-list write about to be persisted, made loadable first (§4.5).

    A delete or a disable can remove the last enabled speaker. The schema rejects
    `multi_device.enabled=true` without one, so persisting that state would leave a
    file `load_config` cannot boot. References to the removed device are dropped and
    multi-device turns itself off; the caller still answers 200 with a stopped plane.
    """
    multi = config.multi_device
    ids = {device.id for device in config.devices}
    enabled = {device.id for device in config.devices if device.enabled}
    changes: dict[str, Any] = {}
    if multi.default_output is not None and multi.default_output not in enabled:
        changes["default_output"] = None
    if multi.enabled and not enabled:
        changes["enabled"] = False
    priority = [item for item in multi.arbitration.room_priority if item in ids]
    if priority != list(multi.arbitration.room_priority):
        changes["arbitration"] = multi.arbitration.model_copy(
            update={"room_priority": priority})
    if not changes:
        return config
    log.info("device write repaired multi_device references: %s", sorted(changes))
    return config.model_copy(update={"multi_device": multi.model_copy(update=changes)})


def check_device_write(config: Config) -> Config:
    """The config a device write may persist: repaired, then reloadable.

    This is the invariant itself, not a route helper: `save_config` does not validate,
    so every writer must pass through here. Raises :class:`DeviceWriteError` (422
    `invalid_values`) when the repair still leaves a document `load_config` rejects.

    A removed adapter spelling is checked first. It is a plain string in
    `DeviceConfig` (the core never guesses a model), so Pydantic alone would accept
    it; only the read path used to reject it, and a write could therefore persist a
    file the next boot refuses.
    """
    rejected = removed_adapters_error(config.devices)
    if rejected is not None:
        index, message = rejected
        raise DeviceWriteError(
            422, "invalid_values", message,
            hint="Change devices[].adapter to the plug manifest's device id.",
            details={"fields": [field(f"devices[{index}].adapter", "invalid", message)]},
        )
    repaired = repair_device_writes(config)
    document = validation_document(repaired)
    try:
        updated = Config.model_validate(document)
    except ValidationError as exc:
        raise DeviceWriteError(
            422, "invalid_values", "invalid configuration",
            hint="Fix the highlighted fields and retry.",
            details={"fields": config_fields(exc, document)},
        ) from exc
    # A full dump marks every nested field as "set". Keeping the loaded layer objects
    # makes sure the neutral audio/ack defaults do not become explicit choices (which
    # would shadow the plug manifest) just because a device was written.
    return updated.model_copy(update={"audio": repaired.audio, "ack": repaired.ack})


def checked_device_config(config: Config, devices: list[DeviceConfig], *,
                          check_ports: bool = True) -> Config:
    """Validate one candidate device list before it reaches the disk.

    `check_ports=False` is for writers that only remove or disable devices: a delete
    cannot create a listener collision, and a hand-edited config that already has one
    must stay fixable. Messages and details are shared with C-002 §9: a port conflict
    keeps `code: "port_conflict"` with the unified `details.fields[]` shape.
    """
    updated = check_device_write(config.model_copy(update={"devices": devices}))
    if not check_ports:
        return updated
    conflict = port_conflict(updated.devices, updated.system.data_dir)
    if conflict is None:
        return updated
    port = conflict.get("port")
    ids = conflict.get("device_ids") or []
    message = f"enabled devices share inbound port {port}: {', '.join(map(str, ids))}"
    raise DeviceWriteError(
        422, "port_conflict", message,
        hint="Give one device a different listen_port or disable it.",
        details={
            **conflict,
            "fields": [field("host", "port_conflict", message)],
        },
    )
