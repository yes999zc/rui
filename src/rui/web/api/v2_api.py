"""Console v2 API (``/api/v2/*``), the backend half of contract C-002/1.

The v2 console is device-centred: it renders whatever the installed plugs declare and
never branches on a model name. This module owns the contract shapes; the service
layer (config store, adapters, audio planes, secrets) stays where it was and is reused
through the callables :func:`install_v2_routes` receives from ``app.py``.

Design rules kept here on purpose:

* every response is built from live evidence (probe, plane metrics, files), never from
  an optimistic default;
* secrets are write-only: responses report ``configured`` and never a value, and the
  only endpoints that return a token are ``connection/reveal`` and ``token/rotate``;
* errors share one object (``{"error": {code, message, hint, details, retryable}}``)
  and carry a request id both in the body and in ``X-Request-ID``;
* the C-002 error table (contract §9) is the only source of stable codes.

Route order matters: the two catch-alls at the end of :func:`install_v2_routes`
(``/api/v2/{rest}`` and ``/v2/{full_path}``) are registered before ``app.py`` adds the
old console's ``/{full_path:path}`` shell, so a v2 path never falls into the v1 SPA and
an ``/api/v2`` typo stays JSON.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import ValidationError

from ... import __version__, secrets
from ... import setup_state as setup_store
from ...audio.kws import KwsUnavailable, model_files
from ...audio.probe import (
    WireCheck,
    WireFormat,
    check_wire_format,
    is_wire_mismatch,
    safe_probe,
)
from ...config.catalog import field_specs
from ...config.schema import (
    LED_STATES,
    STAGES,
    AckConfig,
    AecConfig,
    AudioConfig,
    CaptureConfig,
    Config,
    DeviceAudioOverride,
    DeviceConfig,
    PlaybackConfig,
    VoiceConfig,
    _explicit,
    capture_layout_neutral_fills,
    device_audio_conflicts,
    device_audio_defaults_info,
    effective_device_ack_echo_ignore_ms,
    effective_device_audio,
)
from ...config.store import ConfigError, explicit_audio_layers, load_config
from ...device.contract import Capability, DeviceInfo, Transport
from ...log_setup import LOG_FILE_NAME, tail_lines
from ...plugins import (
    ParamType,
    PlugCatalog,
    RuntimeMode,
    TrustLevel,
)
from ...providers import keycheck
from .device_rules import (
    effective_listen_port,
    effective_token_env,
)
from .device_write import (
    DeviceWriteError,
    checked_device_config,
)
from .device_write import (
    audio_blocks as _audio_blocks,
)
from .device_write import (
    config_fields as _config_fields,
)
from .device_write import (
    explicit_audio_mapping as _explicit_audio_mapping,
)
from .device_write import (
    field as _field,
)
from .device_write import (
    loc_path as _loc_path,
)
from .device_write import (
    validation_document as _validation_document,
)

__all__ = ["V2Error", "install_v2_routes"]

log = logging.getLogger(__name__)

#: Where the v2 shell is served from when the frontend build has not produced it.
V2_FALLBACK = """<!doctype html><html><head><meta charset="utf-8"><title>rui v2</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:640px;margin:12vh auto;padding:0 24px;color:#111}
code{background:#f4f4f5;padding:2px 6px;border-radius:4px}</style></head>
<body><h1>rui console v2</h1><p>The v2 console bundle is not built yet.</p>
<p><code>cd web &amp;&amp; npm run build</code>, then reload <code>/v2</code>.</p>
<p>The API is available at <code>/api/v2/meta</code>.</p></body></html>"""

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

#: C-001 capabilities, in the enum's definition order (contract §5.1 / §7.9).
#: The catalog is owned by the core, not by a plug: a plug only declares ids.
CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "id": "mic_array",
        "label": {"zh": "麦克风阵列", "en": "Microphone array"},
        "description": {"zh": "多路麦克风采集", "en": "More than one microphone channel"},
        "ui_hint": "none",
        "degraded_hint": {
            "zh": "未声明多麦阵列；文本回合不受影响。",
            "en": "No microphone array is declared; text turns are unaffected.",
        },
        "actions": [],
    },
    {
        "id": "reference",
        "label": {"zh": "播放参考回环", "en": "Playback reference loopback"},
        "description": {"zh": "把播放声音回送给 RUI AEC", "en": "Feeds our own playback back into RUI AEC"},
        "ui_hint": "none",
        "degraded_hint": {
            "zh": "未声明参考回环；RUI AEC 不可用，请使用设备侧 AEC。",
            "en": "No reference loopback is declared; RUI AEC is unavailable, use device-side AEC.",
        },
        "actions": [],
    },
    {
        "id": "playback",
        "label": {"zh": "播放", "en": "Playback"},
        "description": {"zh": "接收 PCM 并播放", "en": "Can receive PCM from the controller"},
        "ui_hint": "button",
        "degraded_hint": {
            "zh": "未声明播放能力；试一句、试音和停止不可用。",
            "en": "Playback is not declared; say/play/stop are unavailable.",
        },
        "actions": [
            {
                "id": "audio.say",
                "args_schema": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": ["reply", "audio_bytes"],
                    "properties": {
                        "reply": {"type": "string"},
                        "audio_bytes": {"type": "integer"},
                    },
                },
            },
            {
                "id": "audio.play",
                "args_schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "minLength": 1},
                        "loop": {"type": "boolean"},
                        "gain": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": ["playing"],
                    "properties": {"playing": {"type": "string"}},
                },
            },
            {
                "id": "audio.stop",
                "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "result_schema": {
                    "type": "object",
                    "required": ["playing"],
                    "properties": {"playing": {"type": "null"}},
                },
            },
        ],
    },
    {
        "id": "led",
        "label": {"zh": "灯环", "en": "LED ring"},
        "description": {"zh": "可控制的灯效", "en": "A controllable LED ring"},
        "ui_hint": "button",
        "degraded_hint": {"zh": "未声明灯环；灯效测试隐藏。", "en": "No LED ring is declared; the LED test is hidden."},
        "actions": [
            {
                "id": "led.set",
                "args_schema": {
                    "type": "object",
                    "required": ["effect"],
                    "properties": {"effect": {"type": "string", "enum": list(LED_STATES)}},
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": ["effect"],
                    "properties": {"effect": {"type": "string"}},
                },
            }
        ],
    },
    {
        "id": "keys",
        "label": {"zh": "物理按键", "en": "Physical keys"},
        "description": {"zh": "设备按键事件", "en": "The device reports its own keys"},
        "ui_hint": "none",
        "degraded_hint": {"zh": "未声明物理按键；只影响说明。", "en": "No physical keys are declared; display only."},
        "actions": [],
    },
    {
        "id": "volume",
        "label": {"zh": "音量", "en": "Volume"},
        "description": {"zh": "设备自己的混音器，0–255", "en": "The device mixer, 0–255"},
        "ui_hint": "slider",
        "degraded_hint": {
            "zh": "未声明音量能力；仍可在配置里设音量目标",
            "en": "Volume is not declared; the config volume target still applies",
        },
        "actions": [
            {
                "id": "volume.read",
                "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "result_schema": {
                    "type": "object",
                    "required": ["value", "range"],
                    "properties": {"value": {"type": "integer"}, "range": {"type": "string"}},
                },
            },
            {
                "id": "volume.set",
                "args_schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer", "minimum": 1, "maximum": 255}},
                    "additionalProperties": False,
                },
                "result_schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer"}},
                },
            },
        ],
    },
    {
        "id": "native_kws",
        "label": {"zh": "厂商唤醒", "en": "Vendor wake engine"},
        "description": {"zh": "已废弃：厂商自己的唤醒引擎", "en": "Deprecated: the vendor's own wake engine"},
        "ui_hint": "none",
        "degraded_hint": {
            "zh": "native_kws 已废弃；请改用 RUI KWS 或 device_wake。",
            "en": "native_kws is deprecated; use RUI KWS or device_wake.",
        },
        "actions": [],
    },
    {
        "id": "streaming_asr",
        "label": {"zh": "持续上行音频", "en": "Streaming ASR"},
        "description": {"zh": "可以持续上行音频做识别", "en": "Can push audio continuously for recognition"},
        "ui_hint": "none",
        "degraded_hint": {"zh": "未声明持续上行；唤醒/ASR 路径不同。", "en": "No streaming uplink is declared; the wake/ASR path differs."},
        "actions": [],
    },
    {
        "id": "device_wake",
        "label": {"zh": "设备侧唤醒", "en": "Device-side wake"},
        "description": {"zh": "设备检测到我们的唤醒词并上报", "en": "The device detects our wake word and reports it"},
        "ui_hint": "switch",
        "degraded_hint": {"zh": "未声明设备侧唤醒；唤醒来源显示 RUI KWS。", "en": "No device-side wake is declared; the wake source is RUI KWS."},
        "actions": [],
    },
    {
        "id": "barge_via_device",
        "label": {"zh": "设备侧打断", "en": "Device-side barge"},
        "description": {"zh": "设备可以上报打断建议", "en": "The device can report a barge suggestion"},
        "ui_hint": "switch",
        "degraded_hint": {
            "zh": "未声明设备侧打断；打断依赖 RUI AEC/参考回环。",
            "en": "No device-side barge is declared; barge depends on RUI AEC/reference.",
        },
        "actions": [],
    },
)

KNOWN_CAPABILITIES: tuple[str, ...] = tuple(item["id"] for item in CAPABILITIES)
_CAPABILITY_BY_ID = {item["id"]: item for item in CAPABILITIES}

#: Capabilities where the probe may down-vote a manifest declaration (contract §5.2).
_PROBE_BOUND = frozenset(
    {"mic_array", "reference", "playback", "device_wake", "barge_via_device"}
)
#: Capabilities the manifest alone decides (probe reports null for them).
_MANIFEST_ONLY = frozenset({"led", "keys", "volume"})
_DEPRECATED = frozenset({"native_kws"})

_ACTION_CAPABILITY = {
    "volume.read": "volume",
    "volume.set": "volume",
    "led.set": "led",
    "audio.say": "playback",
    "audio.play": "playback",
    "audio.stop": "playback",
}
_ACTION_ARGS = {
    action["id"]: action["args_schema"]
    for capability in CAPABILITIES
    for action in capability["actions"]
}
#: C-002 §5.4 step 4: actions that need the device's live link. `led.set` belongs here
#: too, so an unconnected device answers `409 device_not_connected` before args are
#: validated instead of leaking a `422 invalid_values`.
_LIVE_ACTION = frozenset(
    {"volume.read", "volume.set", "led.set", "audio.say", "audio.play", "audio.stop"}
)
#: Both `device_not_connected` refusals on the action path give the same advice.
_NO_LIVE_LINK_HINT = (
    "The device has no live link to the audio plane; "
    "start the device-side client and retry."
)
_ALLOWED_ACTIONS = tuple(sorted(_ACTION_CAPABILITY))

_PLAN_STEPS = setup_store.STEPS
_PLAN_ACTIONS: dict[str, set[str]] = {
    "keys": {"keys.save", "keys.test", "providers.save"},
    "models": set(),
    "prepare": {"manual.confirm"},
    "params": {"device.create", "device.update"},
    "wake": {"wake.set"},
    "connect": set(),
    "test": {"device.test"},
    "verify": {"verify.say"},
}
#: Steps that touch one device; add-mode house prerequisites gate these, not the
#: house-scoped steps that would otherwise be unable to unblock the plan (§7.24).
_DEVICE_STEPS = frozenset({"prepare", "params", "wake", "connect", "test", "verify"})
#: The setup actions that change a device's identity are the §7.13 immutable fields.
_IMMUTABLE_DEVICE_FIELDS = ("id", "plugin_id", "model_id", "adapter")

_STATUS_REASONS: dict[str, tuple[str, str]] = {
    "ready": ("connected", ""),
    "probe_failed": (
        "the adapter probe failed",
        "Check the service log; the device may still be reachable after a restart.",
    ),
    "device_disabled": ("the device is disabled", "Enable it on the device page."),
    "audio_config_mismatch": (
        "the configuration does not match the device",
        "Open the audio tab; both values, their source and the fix are listed.",
    ),
    "plug_unresolved": (
        "no installed plug describes this device",
        "Install the plug that owns this model, or remove and re-add the device.",
    ),
    "adapter_unavailable": (
        "the adapter is not registered in this process",
        "Check the service log for the adapter load error and restart.",
    ),
    "token_missing": (
        "no device token is configured",
        "Rotate a token on the device page, or set the token environment variable.",
    ),
    "audio_plane_error": (
        "the audio plane reported an error",
        "Open the service log; the plane error is also shown on the device page.",
    ),
    "audio_plane_stopped": (
        "the audio plane is not running",
        "Start the audio plane (restart it from the house page).",
    ),
    "not_connected": (
        "the device has not dialled in",
        "Start the device-side client and confirm it points at this server and port.",
    ),
}
_REASON_PRIORITY = (
    "ready",
    "device_disabled",
    "audio_config_mismatch",
    "probe_failed",
    "plug_unresolved",
    "adapter_unavailable",
    "token_missing",
    "audio_plane_error",
    "audio_plane_stopped",
    "not_connected",
)

_WARNING_HINTS: dict[str, str] = {
    "fleet_start_failed": "Check the service log for the audio-plane failure and restart.",
    "over_max_planes": "Disable a speaker or raise multi_device.max_planes.",
    "plane_not_running": "Check the audio-plane log; the port may be taken or the client is down.",
    "multiple_enabled_inbound": "Disable the extra inbound speaker or enable multi-device mode.",
    "duplicate_room": "Give every speaker its own room name.",
    "port_conflict": "Give one enabled speaker a different listen_port, or disable it.",
}


class V2Error(Exception):
    """A C-002 error, rendered by the handler installed in :func:`install_v2_routes`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}
        self.retryable = (status_code in (429, 502, 503)) if retryable is None else retryable
        self.headers = headers or {}


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "v2_request_id", "")
    return rid if isinstance(rid, str) and rid else f"req_{uuid.uuid4().hex}"


def _not_found(resource: str, resource_id: str) -> V2Error:
    return V2Error(
        404,
        "not_found",
        f"no such {resource}: {resource_id}",
        hint=f"Check the {resource} id; the list endpoints return the installed ids.",
        details={"resource": resource, "id": resource_id},
    )


async def _json_body(request: Request) -> dict[str, Any]:
    """A JSON object body, or the C-002 400 ``bad_request``.

    Reading the body here (instead of a Pydantic parameter) keeps the error shape stable:
    FastAPI's own validation error does not match contract §4.4.
    """
    raw = await request.body()
    if not raw:
        raise V2Error(400, "bad_request", "request body is required",
                      hint="Send a JSON object body.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise V2Error(400, "bad_request", "request body is not valid JSON",
                      hint="Send a JSON object body.",
                      details={"error": str(exc)[:200]}) from exc
    if not isinstance(payload, dict):
        raise V2Error(400, "bad_request", "request body must be a JSON object")
    return payload


def _pydantic_fields(exc: ValidationError, prefix: str = "") -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    for error in exc.errors(include_url=False):
        loc = _loc_path(tuple(error.get("loc", ())))
        path = ".".join([part for part in (prefix, loc) if part])
        kind = str(error.get("type", "invalid"))
        code = {
            "missing": "required",
            "value_error": "invalid",
            "greater_than": "out_of_range",
            "greater_than_equal": "out_of_range",
            "less_than": "out_of_range",
            "less_than_equal": "out_of_range",
            "string_too_long": "too_long",
            "string_too_short": "too_short",
        }.get(kind, kind)
        message = str(error.get("msg", "invalid value"))
        for noise in ("Value error, ", "Assertion failed, "):
            if message.startswith(noise):
                message = message[len(noise):]
        fields.append(_field(path, code, message))
    return fields or [_field(prefix, "invalid", "invalid values")]


def _invalid(fields: list[dict[str, str]], message: str = "invalid values", *,
             hint: str = "", details: dict[str, Any] | None = None) -> V2Error:
    payload = {"fields": fields}
    if details:
        payload.update(details)
    return V2Error(422, "invalid_values", message, hint=hint, details=payload)


def _wrap_warning(raw: dict[str, Any]) -> dict[str, Any]:
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    device_id = details.get("device") or raw.get("device_id")
    wrapped: dict[str, Any] = {
        "code": str(raw.get("code") or "warning"),
        "message": str(raw.get("message") or ""),
        "hint": _WARNING_HINTS.get(str(raw.get("code") or ""), ""),
    }
    if device_id:
        wrapped["device_id"] = str(device_id)
    return wrapped


def _missing_for_barge(effective_ids: list[str], *, device_id: str, adapter: str,
                       model: str, host: str) -> list[str]:
    """``DeviceInfo.missing_for_barge_in()`` over an already resolved capability set."""
    known = {cap.value for cap in Capability}
    capabilities = frozenset(Capability(item) for item in effective_ids if item in known)
    return [item.value for item in DeviceInfo(
        id=device_id, adapter=adapter, model=model, host=host,
        capabilities=capabilities,
    ).missing_for_barge_in()]


def _capability_rows(declared: list[str], probed: list[str] | None,
                     manifest_resolved: bool) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """(rows, effective ids, unknown ids) for one device (contract §5.2)."""
    declared_set = set(declared)
    probe_set = set(probed or ())
    unknown = sorted(item for item in declared_set if item not in _CAPABILITY_BY_ID)
    rows: list[dict[str, Any]] = []
    effective: list[str] = []

    if not manifest_resolved:
        # Documented dev/mock case: with no manifest the probe is authoritative for
        # every capability, and nothing is reported as "declared".
        for cap in KNOWN_CAPABILITIES:
            if probed is None:
                continue
            is_on = cap in probe_set
            if not is_on:
                continue
            effective.append(cap)
            rows.append({"id": cap, "declared": False, "probed": True,
                         "effective": True, "known": True, "reason": "ok"})
        return rows, effective, unknown

    for cap in KNOWN_CAPABILITIES:
        is_declared = cap in declared_set
        probe_reports = cap in probe_set or (probed is not None and is_declared)
        if cap in _PROBE_BOUND:
            probed_value = None if probed is None else (cap in probe_set)
        elif cap in _MANIFEST_ONLY:
            # Contract §5.2: led/keys/volume are never decided by a probe; null is the
            # honest value even when an adapter happens to report them.
            probed_value = None
        else:
            probed_value = None if probed is None else (cap in probe_set)
        if cap in _DEPRECATED:
            if not is_declared:
                continue
            rows.append({"id": cap, "declared": True, "probed": probed_value,
                         "effective": False, "known": True, "reason": "deprecated"})
            continue
        if not is_declared and not probe_reports:
            continue
        if cap in _MANIFEST_ONLY:
            is_effective = is_declared
        else:
            is_effective = is_declared and probe_set.__contains__(cap)
        if is_effective:
            reason = "ok" if cap in _PROBE_BOUND else "manifest_only"
            effective.append(cap)
        elif is_declared and probed is None:
            reason = "probe_missing"
        elif is_declared:
            reason = "probe_missing"
        else:
            reason = "not_declared"
        rows.append({"id": cap, "declared": is_declared, "probed": probed_value,
                     "effective": is_effective, "known": True, "reason": reason})
    for cap in unknown:
        rows.append({"id": cap, "declared": True, "probed": None,
                     "effective": False, "known": False, "reason": "unknown_capability"})
    return rows, effective, unknown


def _device_pid(app: FastAPI, device_id: str) -> int | None:
    """The audio plane process id for one room; ``null`` when no plane reports one."""
    from . import app as app_module

    fleet = app_module._audio_fleet(app)
    try:
        if fleet is not None:
            status = fleet.supervisor(device_id).status()
            pid = status.get("pid")
        else:
            supervisor = getattr(app.state, "audio", None)
            pid = supervisor.status().get("pid") if supervisor is not None else None
    except Exception:  # noqa: BLE001 - a display field must never fail the page
        return None
    return int(pid) if isinstance(pid, (int, float)) and not isinstance(pid, bool) else None


def _reported_capture_wire(info: DeviceInfo | None, probe_error: str) -> WireFormat | None:
    """What the device probe says it sends, with the same inbound-only rule as the plane.

    ``DeviceInfo`` describes the capture wire only (there is no playback field), and an
    outbound adapter owns its own wire, so its effective capture block is advisory.
    """
    if info is None or probe_error:
        return None
    if getattr(info, "transport", None) is not Transport.INBOUND:
        return None
    rate = int(getattr(info, "sample_rate", 0) or 0) or None
    channels = int(getattr(info, "channels", 0) or 0) or None
    mic_channels = int(getattr(info, "mic_channels", 0) or 0)
    ref_channels = int(getattr(info, "ref_channels", 0) or 0)
    if rate is None and channels is None and not mic_channels and not ref_channels:
        return None
    return WireFormat(
        rate=rate,
        channels=channels,
        mic_channels=mic_channels,
        ref_channels=ref_channels,
        source=(
            f"probe ({getattr(info, 'adapter', '?')} "
            f"{getattr(info, 'model', '?')})"
        ).strip(),
    )


def _audio_provenance(cfg: Config, device: DeviceConfig,
                      *, reported: WireFormat | None = None) -> dict[str, Any]:
    """The audio layers per field, with the source that won and every conflict.

    Values come from the schema's own merge functions; this function only reports
    where each value came from. The manifest is authoritative for the model's
    hardware facts (0.8.0), so an explicit global value only wins for the negotiated
    ``capture.rate`` - and each disagreement appears in ``conflicts`` so the console
    can show "global X vs model Y, using Y" instead of silently applying X. A capture
    layout the manifest declares also owns the fields it omits: those carry the
    neutral core value, so their source is ``device-manifest`` even though the layer's
    own value is absent (see ``capture_layout_neutral_fills``).

    ``reported`` is the device probe's capture wire (task-25). Each capture layer then
    carries ``reported``/``reported_source`` besides manifest/global, so the device page
    can show the three values side by side; a disagreement with the effective value is
    also listed in ``reported_conflicts`` (never folded into the manifest-vs-global
    ``conflicts`` - a probe value is evidence, not a config layer).
    """
    conflicts: list = []
    effective = effective_device_audio(cfg, device, conflicts=conflicts)
    ack_value = effective_device_ack_echo_ignore_ms(cfg, device, conflicts=conflicts)
    info = device_audio_defaults_info(device, cfg.system.data_dir)
    defaults = info.defaults
    # Fields the manifest's declared capture layout neutralizes: they are the model's
    # facts (neutral core values), not the global template's, so a `global-config`
    # source here would contradict `conflicts` in the same payload.
    neutral_fills = capture_layout_neutral_fills(defaults, cfg.audio)
    # A matched plug is `plug_id`-bearing even when it declares no audio_defaults
    # (an old plug): that is the difference from an unresolved catalog entry.
    resolved = bool(info.plug_id) and not info.error
    blocks = {
        "capture": (CaptureConfig, cfg.audio.capture),
        "voice": (VoiceConfig, cfg.audio.voice),
        "playback": (PlaybackConfig, cfg.audio.playback),
        "aec": (AecConfig, cfg.audio.aec),
    }
    effective_dump = effective.model_dump(mode="json")
    sources: dict[str, str] = {}
    layers: dict[str, dict[str, Any]] = {}
    override = device.audio
    reported_values = {
        "capture.rate": getattr(reported, "rate", None) if reported else None,
        "capture.channels": getattr(reported, "channels", None) if reported else None,
        "capture.mic_channels": (getattr(reported, "mic_channels", None)
                                 if reported else None),
        "capture.ref_channels": (getattr(reported, "ref_channels", None)
                                 if reported else None),
    }

    def add(path: str, *, value: Any, core: Any, manifest: Any,
            global_value: Any, device_value: Any, global_explicit: bool) -> None:
        if device_value is not None:
            source = "device-override"
        elif manifest is not None:
            # The manifest is authoritative, except for the negotiated capture rate:
            # an explicit global wire wins there (the client resamples) and the
            # conflict list names it.
            if (path == "capture.rate" and global_explicit
                    and global_value != manifest):
                source = "global-config"
            else:
                source = "device-manifest"
        elif path in neutral_fills:
            # The manifest declares the capture layout but omitted this field, so the
            # merge filled the neutral core value. Attribute it to the manifest (with
            # the neutral value as its layer) - the same winner `conflicts` reports -
            # never to the global value it replaced.
            manifest = neutral_fills[path]
            source = "device-manifest"
        elif global_explicit:
            source = "global-config"
        elif not resolved:
            source = "unresolved"
        else:
            source = "core-default"
        sources[path] = source
        layers[path] = {
            "effective": value,
            "source": source,
            "core": core,
            "manifest": manifest,
            "global": global_value,
            "global_explicit": global_explicit,
            "device": device_value,
            # Task-25: the device-reported probe value, when the path is one the
            # probe describes. `None` means "not reported", never "equals effective".
            "reported": reported_values.get(path),
            "reported_source": (reported.source
                                if reported is not None
                                and reported_values.get(path) is not None else ""),
        }

    for name, (block_cls, global_block) in blocks.items():
        core_block = block_cls()
        override_block = getattr(override, name, None) if override is not None else None
        default_block = getattr(defaults, name, None) if defaults is not None else None
        for field in block_cls.model_fields:
            path = f"{name}.{field}"
            add(
                path,
                value=effective_dump[name][field],
                core=getattr(core_block, field),
                manifest=(getattr(default_block, field, None)
                          if default_block is not None else None),
                global_value=getattr(global_block, field),
                device_value=(getattr(override_block, field, None)
                              if override_block is not None else None),
                global_explicit=_explicit(global_block, field, core_block),
            )

    ack_core = AckConfig()
    ack_override = (override.voice.ack_echo_ignore_ms
                    if override is not None and override.voice is not None else None)
    add(
        "ack.echo_ignore_ms",
        value=ack_value,
        core=ack_core.echo_ignore_ms,
        manifest=(defaults.ack_echo_ignore_ms if defaults is not None else None),
        global_value=cfg.ack.echo_ignore_ms,
        device_value=ack_override,
        global_explicit=_explicit(cfg.ack, "echo_ignore_ms", ack_core),
    )

    overrides: list[str] = []
    if override is not None:
        raw = override.model_dump(mode="json", exclude_none=True)
        for block, values in raw.items():
            if isinstance(values, dict):
                overrides.extend(f"{block}.{key}" for key, value in values.items()
                                 if value is not None)
            else:
                overrides.append(block)

    reported_conflicts: list[dict[str, Any]] = []
    if reported is not None:
        wire = check_wire_format(
            capture_configured=WireFormat(
                rate=effective.capture.rate,
                channels=effective.capture.channels,
                mic_channels=effective.capture.mic_channels,
                ref_channels=effective.capture.ref_channels,
                source="effective config (audio.capture + devices[].audio)",
            ),
            capture_reported=reported,
        )
        reported_conflicts = [
            {
                "path": problem.path,
                "effective": problem.configured,
                "reported": problem.actual,
                "source": problem.source,
                "message": (f"{problem.path}: effective {problem.configured} vs "
                            f"device-reported {problem.actual} (source: "
                            f"{problem.source})"),
            }
            for problem in wire.problems
        ]

    return {
        "effective": {**effective_dump, "ack_echo_ignore_ms": ack_value},
        "sources": sources,
        "layers": layers,
        "overrides": sorted(overrides),
        "manifest_defaults": (defaults.model_dump(mode="json", exclude_none=True)
                              if defaults is not None else {}),
        "plug_resolved": resolved,
        # Additive 0.8.0: a cold-start catalog miss must be visible, not look like a
        # model that declares nothing.
        "manifest_error": info.error,
        "conflicts": [conflict.as_dict() for conflict in conflicts],
        # Task-25: the third column the device page shows. `device_reported` is the
        # whole probe wire (null when the device did not report or is outbound);
        # `reported_conflicts` names every field where it disagrees with `effective`.
        "device_reported": ({
            "rate": reported.rate,
            "channels": reported.channels,
            "mic_channels": reported.mic_channels,
            "ref_channels": reported.ref_channels,
            "source": reported.source,
        } if reported is not None else None),
        "reported_conflicts": reported_conflicts,
    }


def _audio_restart_fields(cfg: Config) -> list[str]:
    """Audio/ack catalogue paths whose next change needs an audio-plane restart."""
    paths: list[str] = []
    for spec in field_specs(cfg):
        path = str(spec.get("path") or "")
        if spec.get("effect") != "restart":
            continue
        if path.startswith(("audio.capture.", "audio.voice.", "audio.playback.",
                           "audio.aec.")) or path == "ack.echo_ignore_ms":
            if ".keywords." in path:
                continue
            paths.append(path)
    return paths


def _explicit_audio_paths(cfg: Config) -> list[str]:
    paths: list[str] = []
    for name, (block_cls, block) in _audio_blocks(cfg.audio).items():
        core = block_cls()
        for field in block_cls.model_fields:
            if _explicit(block, field, core):
                paths.append(f"audio.{name}.{field}")
    if _explicit(cfg.ack, "echo_ignore_ms", AckConfig()):
        paths.append("ack.echo_ignore_ms")
    return paths


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into ``base``; ``null`` removes the key."""
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _restart_obj(scope: str, *, required: bool, auto: bool, status: str,
                 affected: list[str], duration_ms: int, error: str,
                 restart_fields: list[str] | None = None) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "scope": scope,
        "required": required,
        "auto": auto,
        "status": status,
        "affected_devices": affected,
        "duration_ms": duration_ms,
        "error": error,
    }
    if restart_fields is not None:
        obj["restart_fields"] = restart_fields
    return obj


def _plug_error(plugin: object) -> str:
    """Why this build cannot install the plug; "" means available."""
    if getattr(plugin, "trust", None) not in (TrustLevel.BUILTIN, TrustLevel.TRUSTED):
        return "third_party_not_supported"
    for device in getattr(plugin, "devices", []) or []:
        runtime = getattr(device, "runtime", None)
        if runtime is not None and getattr(runtime, "mode", None) is RuntimeMode.SIDECAR:
            return "sidecar_not_supported"
    return ""


def _param_schema(spec: object) -> dict[str, Any]:
    """One manifest param, minus anything that could carry a secret value."""
    kind = str(getattr(spec, "type", ""))
    entry: dict[str, Any] = {
        "key": str(getattr(spec, "key", "")),
        "label": str(getattr(spec, "label", "")),
        "type": kind,
        "required": bool(getattr(spec, "required", False)),
    }
    if kind == "secret":
        # C-001 forbids a secret default; never echo one (or a placeholder that looks
        # like one) even if a hand-written manifest slipped through.
        return entry
    default = getattr(spec, "default", None)
    if default is not None:
        entry["default"] = default
    placeholder = str(getattr(spec, "placeholder", "") or "")
    if placeholder:
        entry["placeholder"] = placeholder
    help_text = str(getattr(spec, "help", "") or "")
    if help_text:
        entry["help"] = help_text
    maximum = getattr(spec, "max", None)
    if maximum is not None:
        entry["max"] = int(maximum)
    options = list(getattr(spec, "options", []) or [])
    if options:
        entry["options"] = options
    return entry


def _runtime_public(runtime: object | None) -> dict[str, Any] | None:
    if runtime is None:
        return None
    return {
        "mode": str(runtime.mode) if runtime.mode is not None else None,
        # `entrypoint` is deliberately absent: it is executable detail behind the
        # plug trust boundary (contract §7.8).
        "image": str(runtime.image or ""),
        "listen_port": int(runtime.listen_port),
        "token_env": str(runtime.token_env or ""),
    }


def _device_manifest_public(device: object) -> dict[str, Any]:
    wake = getattr(device, "wake", None)
    return {
        "model_id": str(device.id),
        "model": str(getattr(device, "model", "") or ""),
        "name": str(device.name),
        "vendor": str(device.vendor),
        "class": str(device.device_class),
        "transport": str(device.transport),
        "capabilities": [str(cap) for cap in device.capabilities],
        "params": [_param_schema(spec) for spec in device.params],
        "audio_defaults": (device.audio_defaults.model_dump(mode="json", exclude_none=True)
                           if getattr(device, "audio_defaults", None) is not None else {}),
        "wake": {
            "source": str(wake.source) if wake is not None else str(device.wake_source),
            "barge_via_device": bool(wake.barge_via_device) if wake is not None else False,
        },
        "device_side": (device.device_side.model_dump(mode="json", exclude_none=True)
                        if getattr(device, "device_side", None) is not None else None),
    }


def _plugin_public(plugin: object, source: str, error: str,
                   *, origin: str = "", shadowed: list[dict[str, Any]] | None = None
                   ) -> dict[str, Any]:
    first = plugin.devices[0]
    capabilities: list[str] = []
    for device in plugin.devices:
        for cap in device.capabilities:
            if str(cap) not in capabilities:
                capabilities.append(str(cap))
    return {
        "id": str(plugin.id),
        "version": str(plugin.version),
        "name": str(plugin.name),
        "vendor": str(first.vendor),
        "class": str(first.device_class),
        "trust": str(plugin.trust),
        "transport": str(first.transport),
        "capabilities": capabilities,
        "available": not error,
        "error": error or None,
        "source": source,
        # 0.8.0 task-25: the version/source above describe the manifest this process
        # actually loaded. `source_path` names the directory, and `shadowed` lists the
        # same-id candidates that lost the explicit adapter > builtin > user rule.
        "source_path": origin,
        "shadowed": list(shadowed or []),
        "devices": [_device_manifest_public(device) for device in plugin.devices],
    }


def _device_test_record(saved: dict[str, Any],
                        device: DeviceConfig | None) -> dict[str, Any]:
    """The stored ``device.test`` record, but only when it belongs to ``device``.

    The lookup lives in :mod:`rui.setup_state`: it prefers this device's own record
    and falls back to the legacy single slot only when that record names the same
    device, so a probe for another room never becomes evidence here and the test order
    no longer decides what a device's card shows. A draft (``device is None``) owns no
    record at all, and corrupt layers read as "no record" instead of raising.
    """
    if device is None:
        return {}
    return setup_store.device_test_record(saved, device.id)


def _catalog_entries(data_dir: str | Path | None
                     ) -> tuple[dict[str, tuple[Any, str, str, str, list[dict]]],
                                list[str]]:
    """(plugin id -> (plugin, source, error, source_path, shadowed)) + load errors.

    One catalog for every v2 route, so a plug's version/source is the one the shared
    resolver would use: the loaded adapter's manifest, then the image, then user plugs.
    """
    catalog = PlugCatalog(data_dir)
    entries: dict[str, tuple[Any, str, str, str, list[dict]]] = {}
    for plugin_id, entry in catalog.entries.items():
        shadowed = [
            {
                "id": str(loser.plugin.id),
                "source": loser.source,
                "version": str(loser.plugin.version),
                "source_path": loser.origin,
            }
            for loser in catalog.shadowed.get(plugin_id, ())
        ]
        entries[plugin_id] = (
            entry.plugin, entry.source, _plug_error(entry.plugin),
            entry.origin, shadowed,
        )
    # A broken manifest stays data (the log names it) and never hides the plugs that
    # did load; v2 lists installable entries only because its schema requires an id.
    # Since task-25 this includes broken builtin/image manifests, so a parse failure is
    # visible in the list response instead of only in the service log.
    return entries, list(catalog.errors)


def install_v2_routes(
    app: FastAPI,
    auth: Any,
    *,
    config_path: Path | None,
    current: Any,
    write_devices: Any,
    write_config: Any,
    restart_audio: Any,
    run_debug_say: Any,
    probe_host: Any,
    host_value: Any,
    room_value: Any,
    wake_words_value: Any,
    ws_url_for: Any,
    dist: Path,
) -> None:
    """Register every C-002 endpoint plus the ``/v2`` shell hosting.

    All service-layer callables are injected by ``app.py``: this module must not build a
    second config writer, restart path or turn runner.
    """

    # ------------------------------------------------------------------ errors, auth
    @app.exception_handler(V2Error)
    async def _v2_error_handler(request: Request, exc: V2Error) -> JSONResponse:
        body = {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "hint": exc.hint,
                "details": exc.details,
                "retryable": exc.retryable,
            },
            "request_id": _request_id(request),
        }
        headers = dict(exc.headers)
        headers["X-Request-ID"] = _request_id(request)
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

    @app.middleware("http")
    async def _v2_request_id(request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith("/api/v2"):
            return await call_next(request)
        incoming = request.headers.get("X-Request-ID") or ""
        rid = incoming if _REQUEST_ID_RE.fullmatch(incoming) else f"req_{uuid.uuid4().hex}"
        request.state.v2_request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    def v2_auth(request: Request, x_auth_token: str | None = Header(default=None)) -> None:
        """The v1 middle-ware semantics, with the C-002 error body."""
        try:
            auth(request, x_auth_token)
        except Exception as exc:  # noqa: BLE001 - only HTTPException is expected here
            status = int(getattr(exc, "status_code", 401) or 401)
            if status == 429:
                raise V2Error(
                    429, "rate_limited", "too many bad tokens; wait a minute",
                    hint="Wait for the retry window, then send the correct X-Auth-Token.",
                    details={"retry_after_s": 60}, retryable=True,
                    headers={"Retry-After": "60"},
                ) from exc
            raise V2Error(
                401, "unauthorized", "bad or missing X-Auth-Token",
                hint="Send the console token in the X-Auth-Token header.",
            ) from exc

    # ------------------------------------------------------------------ local helpers
    def cfg_now() -> Config:
        return current()

    def require_config() -> Config:
        if config_path is None or not Path(config_path).exists():
            raise V2Error(409, "no_config", "there is no config file on disk to write",
                          hint="Run the service with a config file (RUI_CONFIG).")
        try:
            return load_config(Path(config_path))
        except ConfigError as exc:
            raise V2Error(409, "no_config", f"config is not readable: {exc}") from exc

    def device_or_404_req(request: Request, device_id: str, config: Config) -> DeviceConfig:
        for device in config.devices:
            if device.id == device_id:
                return device
        raise _not_found("device", device_id)

    def resolve(device: DeviceConfig, config: Config) -> Any:
        from . import app as app_module

        catalog = PlugCatalog(config.system.data_dir)
        return catalog.resolve(
            plug_id=app_module._device_plug_id(device, None), adapter=device.adapter)

    def token_env_of(device: DeviceConfig, resolved: Any) -> str:
        return effective_token_env(device, resolved)

    def token_configured(device: DeviceConfig, env: str) -> bool:
        return bool(secrets.resolve(env)) or bool(device.allow_no_token)

    def token_managed_by_adapter(device: DeviceConfig, config: Config) -> bool:
        """True when the device is live but its credential lives in the adapter.

        dot2 #7: an out-of-tree adapter may read its token from its own env/file, so rui
        cannot see it. A live link is the proof that whatever the adapter used worked;
        claiming `token_missing` there would tell the operator to fix a working device.
        """
        from . import app as app_module

        resolved = resolve(device, config)
        env = token_env_of(device, resolved)
        if secrets.resolve(env) or device.allow_no_token:
            return False
        adapters_now = getattr(app.state, "adapters", None)
        adapter = (adapters_now.adapters.get(device.id)
                   if adapters_now is not None else None)
        if adapter is None:
            return False
        plane = app_module._device_plane(app, device.id)
        health = app_module._adapter_health(adapter)
        return bool(health.get("connected") is True or (
            plane.get("speaker_connected")
            and plane.get("speaker_device") in ("", device.id)))

    def public_params(device: DeviceConfig, resolved: Any) -> dict[str, Any]:
        from . import app as app_module

        return app_module._device_public_params(device, resolved)

    def house_warnings(config: Config) -> list[dict[str, Any]]:
        from . import app as app_module

        raw = [_wrap_warning(item) for item in app_module._device_warnings(app, config)]
        rooms: dict[str, list[str]] = {}
        for device in config.devices:
            if device.room:
                rooms.setdefault(device.room, []).append(device.id)
        for room, ids in sorted(rooms.items()):
            if len(ids) > 1:
                raw.append({
                    "code": "duplicate_room",
                    "device_id": ids[0],
                    "message": f"room {room!r} has more than one device: {', '.join(ids)}",
                    "hint": _WARNING_HINTS["duplicate_room"],
                })
        return raw

    def device_warnings_for(device_id: str, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [item for item in warnings
                if item.get("device_id") in (None, "", device_id)]

    def _unknown_params_warning(keys: list[str]) -> list[dict[str, Any]]:
        if not keys:
            return []
        return [{
            "code": "unknown_params_ignored",
            "message": f"the plug does not declare these params: {', '.join(keys)}",
            "params": list(keys),
            "hint": "They are kept for compatibility but have no effect; remove them or "
                    "declare them in the plug manifest.",
        }]

    def _audio_conflict_warnings(config: Config,
                                 device: DeviceConfig) -> list[dict[str, Any]]:
        """Surface "global X vs model Y" in the device warnings (0.8.0 P0).

        The merge itself is still the only place that decides the value; this only
        makes the disagreement visible on the list/detail cards without opening the
        audio tab.
        """
        return [
            {
                "code": "audio_conflict",
                "path": conflict.path,
                "global": conflict.global_value,
                "manifest": conflict.manifest_value,
                "effective": conflict.effective,
                "message": conflict.message,
                "hint": "The model manifest owns hardware facts. Override it per device "
                        "under devices[].audio if this room needs a different value.",
            }
            for conflict in device_audio_conflicts(config, device)
        ]

    def _device_wire_check(
        config: Config,
        device: DeviceConfig,
        reported: WireFormat | None,
        *,
        summary_audio: dict[str, Any],
    ) -> WireCheck:
        """The same comparison the plane refuses on (task-24), computed independently.

        The console must be able to say "配置与设备不符" even when the plane is stopped,
        so this does not read plane state: the probe's capture wire and the model
        manifest (playback wire) are compared with the effective config the API already
        merged. ``reported`` is computed once by :func:`_reported_capture_wire` and
        shared with the provenance payload so the page and the refusal cannot disagree.
        """
        defaults_info = device_audio_defaults_info(device, config.system.data_dir)
        defaults = defaults_info.defaults
        capture = summary_audio["capture"]
        playback = summary_audio["playback"]
        expected_playback = None
        if defaults is not None and defaults.playback is not None:
            expected_playback = WireFormat(
                rate=defaults.playback.rate,
                channels=defaults.playback.channels,
                source=(
                    f"model manifest {defaults_info.model_id or '?'} "
                    f"({defaults_info.plug_id or '?'})"
                ),
            )
        return check_wire_format(
            capture_configured=WireFormat(
                rate=capture["rate"],
                channels=capture["channels"],
                mic_channels=capture["mic_channels"],
                ref_channels=capture["ref_channels"],
                source="effective config (audio.capture + devices[].audio)",
            ),
            capture_reported=reported,
            playback_configured=WireFormat(
                rate=playback["rate"],
                channels=playback["channels"],
                source="effective config (audio.playback + devices[].audio)",
            ),
            playback_expected=expected_playback,
        )

    def _wire_warning(wire: WireCheck) -> list[dict[str, Any]]:
        if wire.ok:
            return []
        return [{
            "code": "audio_wire_mismatch",
            "message": wire.as_error(),
            "hint": " ".join(problem.fix for problem in wire.problems),
        }]

    def _unknown_params(device: DeviceConfig, resolved: Any) -> list[str]:
        declared = ({spec.key for spec in resolved.device.params}
                    if resolved is not None else set())
        return sorted(key for key in (device.params or {})
                      if key != "plug_id" and key not in declared)

    def _merge_ignored_warning(payload: dict[str, Any], ignored: list[str]) -> None:
        """Name the request params the manifest ignored (C-002 §7.11/§7.13)."""
        if not ignored:
            return
        already = {
            name
            for warning in payload.get("warnings", []) or []
            if warning.get("code") == "unknown_params_ignored"
            for name in (warning.get("params") or [])
        }
        extra = [name for name in ignored if name not in already]
        if extra:
            payload["warnings"] = [
                *(payload.get("warnings") or []),
                *_unknown_params_warning(extra),
            ]

    async def device_view(device: DeviceConfig, config: Config, warnings: list[dict[str, Any]],
                          *, detail: bool) -> dict[str, Any]:
        from . import app as app_module

        resolved = resolve(device, config)
        plane = app_module._device_plane(app, device.id)
        info = None
        probed: list[str] | None = None
        probe_error = ""
        adapter = None
        if device.enabled:
            adapters_now = getattr(app.state, "adapters", None)
            adapter = adapters_now.adapters.get(device.id) if adapters_now is not None else None
            if adapter is not None:
                probe = await safe_probe(adapter, device_id=device.id,
                                         adapter_name=device.adapter)
                info = probe.info
                probed = [str(cap) for cap in info.capabilities]
                probe_error = probe.error

        if not device.enabled:
            status, connected, last_seen, latency = "disabled", False, None, None
        elif adapter is None:
            status, connected, last_seen, latency = "unavailable", False, None, None
        else:
            link = app_module._device_link(info, adapter, plane)
            status = str(link["status"])
            connected = link["connected"]
            last_seen = link["last_seen"]
            latency = link["latency_ms"]
            if probe_error:
                status = "error"

        declared = ([str(cap) for cap in resolved.device.capabilities]
                    if resolved is not None else [])
        rows, effective, unknown = _capability_rows(
            declared, probed, resolved is not None)

        # Task-25: one probe-derived wire shared by the refusal check and the
        # three-value provenance table, so the page never shows a different
        # device-reported value than the one the plane refuses on.
        reported = _reported_capture_wire(info, probe_error)
        summary_audio = app_module._device_audio_summary(config, device)
        # task-24: the plane refuses on this exact comparison before Ready; the
        # console computes it independently so a stopped plane still explains why.
        wire = (_device_wire_check(
            config, device, reported,
            summary_audio=summary_audio,
        ) if device.enabled else WireCheck())
        codes: list[str] = []
        env = token_env_of(device, resolved)
        token_ok = token_configured(device, env)
        token_managed = bool(connected and not token_ok)
        if not device.enabled:
            codes.append("device_disabled")
        if probe_error:
            codes.append("probe_failed")
        if resolved is None:
            codes.append("plug_unresolved")
        if device.enabled and adapter is None:
            codes.append("adapter_unavailable")
        if not token_ok and not token_managed:
            codes.append("token_missing")
        if device.enabled and not plane["running"]:
            codes.append("audio_plane_stopped")
        if plane.get("error"):
            codes.append("audio_plane_error")
        if (device.enabled and token_ok and plane["running"] and connected is False):
            codes.append("not_connected")
        # The plane may know what this API cannot recompute (probe or manifest
        # missing): its own refusal carries the shared prefix, often wrapped by the
        # supervisor ("audio plane refused: ...", "AudioPlaneError: ..."). Match the
        # code anywhere in the text, never at the start.
        plane_error = str(plane.get("error") or "")
        plane_refusal = str(plane.get("refusal") or "")
        refused = is_wire_mismatch(plane_error) or (
            not plane["running"] and is_wire_mismatch(plane_refusal))
        if not wire.ok or refused:
            # A wrong wire is a configuration error, not an offline speaker: it
            # outranks probe/plane noise because no amount of waiting fixes it.
            status = "error"
            codes.append("audio_config_mismatch")
        if not codes and device.enabled and status == "ready":
            codes.append("ready")
        if not codes:
            codes.append("not_connected")
        ready = bool(device.enabled and status == "ready")
        reason_code = next((item for item in _REASON_PRIORITY if item in codes),
                           codes[0])
        message, hint = _STATUS_REASONS[reason_code]

        model = str(getattr(resolved.device, "model", "") if resolved is not None
                    else (getattr(info, "model", "") if info is not None else ""))
        transport = (str(resolved.device.transport) if resolved is not None
                     else str(getattr(info, "transport", "inbound")))
        payload: dict[str, Any] = {
            "id": device.id,
            "room": device.room,
            "plugin_id": (app_module._device_plug_id(device, resolved) or ""),
            "model_id": (str(resolved.device.id) if resolved is not None else ""),
            "model": model,
            "vendor": (str(resolved.device.vendor) if resolved is not None else ""),
            "transport": transport,
            "enabled": bool(device.enabled),
            "ready": ready,
            "status": status,
            "connected": connected,
            "status_reason": {"code": reason_code, "message": message, "hint": hint},
            "last_seen": last_seen,
            "latency_ms": latency,
            "listen_port": effective_listen_port(device, resolved),
            "capabilities": effective,
            "unknown_capabilities": unknown,
            "audio_summary": {
                "capture_rate": summary_audio["capture"]["rate"],
                "playback_rate": summary_audio["playback"]["rate"],
                "aec_who": summary_audio["aec"]["who"],
                "ack_echo_ignore_ms": summary_audio["ack_echo_ignore_ms"],
            },
            "warnings": [
                *device_warnings_for(device.id, warnings),
                *_audio_conflict_warnings(config, device),
                *_wire_warning(wire),
            ],
        }
        if not detail:
            return payload

        payload.update({
            "host": device.host,
            # Additive 0.6.x (C-002 Addendum): `listen_port` above is what the plane
            # binds; these two say whether the config chose it or inherited it from
            # the plug manifest, which is what the device form has to render.
            "listen_port_stored": (int(device.listen_port)
                                   if device.listen_port is not None else None),
            "listen_port_explicit": device.listen_port is not None,
            "wake_words": list(device.wake_words),
            "volume_target": int(device.volume),
            "params": public_params(device, resolved),
            "unknown_params": _unknown_params(device, resolved),
            "capabilities": rows,
            "effective_capabilities": effective,
            "missing_for_barge_in": _missing_for_barge(
                effective, device_id=device.id, adapter=device.adapter,
                model=model, host=device.host),
            "audio": _audio_provenance(config, device, reported=reported),
            # task-24: same values/source/fix the plane refuses on, machine-readable.
            "audio_wire": wire.as_dict(),
            "plane": {
                "running": bool(plane["running"]),
                "speaker_connected": bool(plane["speaker_connected"]),
                "speaker_rejected": int(plane.get("speaker_rejected") or 0),
                "restarts": int(plane.get("restarts") or 0),
                "pid": _device_pid(app, device.id),
                "error": str(plane.get("error") or ""),
                "refusal": str(plane.get("refusal") or ""),
                # task-24: bridge `play()` rejections, cumulative.
                "playback_errors": int(plane.get("playback_errors") or 0),
                "playback_error": str(plane.get("playback_error") or ""),
                "wake_candidates": int(plane.get("wake_candidates") or 0),
                "wake_admitted": int(plane.get("wake_admitted") or 0),
                "wake_rejected": dict(plane.get("wake_rejected") or {}),
                "utterances": int(plane.get("utterances") or 0),
            },
            "setup": _setup_summary(config, device),
            "warnings": [
                *device_warnings_for(device.id, warnings),
                *_audio_conflict_warnings(config, device),
                *_unknown_params_warning(_unknown_params(device, resolved)),
                *_wire_warning(wire),
            ],
        })
        return payload

    def _setup_summary(config: Config, device: DeviceConfig) -> dict[str, Any]:
        stored = setup_store.read(config.system.data_dir)
        # Only this device's own record may show up on its detail card (C-002 A-5);
        # the per-device store keeps A and B independent of test order. Corrupt
        # layers read as "no record", and only the JSON boolean true is a pass.
        record = _device_test_record(stored, device)
        resolved = resolve(device, config)
        return {
            "configured": bool(device.host and device.wake_words and resolved is not None),
            "last_test_ok": record.get("ok") is True,
            "last_test_at": str(record.get("at") or "") if record else "",
        }

    async def device_rows(config: Config, *, detail: bool = False) -> list[dict[str, Any]]:
        warnings = house_warnings(config)
        rows = [await device_view(device, config, warnings, detail=detail)
                for device in config.devices]
        rows.sort(key=lambda row: (row["status"] == "ready", row["room"], row["id"]))
        return rows

    def house_payload(config: Config, rows: list[dict[str, Any]]) -> dict[str, Any]:
        from . import app as app_module

        ready = sum(1 for row in rows if row["ready"])
        rooms: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rooms.setdefault(row["room"], []).append(row)
        default_output = str(config.multi_device.default_output or "")
        room_rows = [
            {
                "room": room,
                "device_ids": [row["id"] for row in items],
                "online": sum(1 for row in items if row["ready"]),
                "default_output": bool(default_output and default_output in
                                       [row["id"] for row in items]),
            }
            for room, items in sorted(rooms.items(), key=lambda pair: pair[0])
        ]
        fleet = app_module._audio_fleet(app)
        planes_running = 0
        if fleet is not None:
            with contextlib.suppress(Exception):
                statuses = fleet.status()
                planes_running = sum(1 for row in statuses.values() if row.get("running"))
        elif app_module._plane_snapshot(app)["running"]:
            planes_running = 1
        stored = setup_store.read(config.system.data_dir)
        verify = stored.get("verify", {}).get("manual", {})
        turn = getattr(app.state, "last_turn", None)
        verified = bool(isinstance(verify, dict) and verify.get("confirm"))
        if not verified and isinstance(turn, dict) and int(turn.get("audio_bytes") or 0) > 0:
            verified = True
        any_ready = any(row["ready"] for row in rows)
        if not rows:
            next_step = "keys"
        elif not any_ready:
            next_step = "params" if not config.devices else "connect"
        elif not verified:
            next_step = "verify"
        else:
            next_step = "done"
        return {
            "version": {"rui": __version__, "config": int(config.version)},
            "devices": {
                "total": len(rows),
                "enabled": sum(1 for row in rows if row["enabled"]),
                "online": ready,
                "ready": ready,
            },
            "multi_device": {
                "enabled": bool(config.multi_device.enabled),
                "max_planes": int(config.multi_device.max_planes),
                "default_output": default_output,
                "room_priority": list(config.multi_device.arbitration.room_priority),
                "planes_running": planes_running,
            },
            "rooms": room_rows,
            "warnings": house_warnings(config),
            "setup": {
                "complete": any_ready,
                "verified": verified,
                "next_step": next_step,
            },
        }

    # ------------------------------------------------------------------ meta / house
    @app.get("/api/v2/meta", dependencies=[Depends(v2_auth)])
    async def meta() -> dict[str, Any]:
        return {
            "contract": "C-002/1",
            "api_version": "2.0",
            "rui_version": __version__,
            "compat": {"min": "0.7.0", "max_exclusive": "0.9.0"},
            "features": {
                "house": True,
                "house_audio": True,
                "plugins": True,
                "capabilities": True,
                "devices": True,
                "device_create": True,
                "device_audio_provenance": True,
                "device_actions": True,
                "device_activity": True,
                "setup_plan": True,
            },
            "capabilities": list(KNOWN_CAPABILITIES),
        }

    @app.get("/api/v2/house", dependencies=[Depends(v2_auth)])
    async def house() -> dict[str, Any]:
        config = cfg_now()
        return house_payload(config, await device_rows(config))

    # ------------------------------------------------------------------ plugins
    @app.get("/api/v2/plugins", dependencies=[Depends(v2_auth)])
    async def plugins() -> dict[str, Any]:
        entries, errors = _catalog_entries(cfg_now().system.data_dir)
        items = [
            _plugin_public(plugin, source, error, origin=origin, shadowed=shadowed)
            for plugin, source, error, origin, shadowed in entries.values()
        ]
        # `errors` is additive (C-002 §7.7): a manifest that exists but cannot be
        # parsed is reported here instead of silently disappearing from the list.
        return {"items": items, "total": len(items), "errors": errors}

    @app.get("/api/v2/plugins/{plugin_id}", dependencies=[Depends(v2_auth)])
    async def plugin_detail(request: Request, plugin_id: str) -> dict[str, Any]:
        entries, _errors = _catalog_entries(cfg_now().system.data_dir)
        found = entries.get(plugin_id)
        if found is None:
            raise _not_found("plugin", plugin_id)
        plugin, source, error, origin, shadowed = found
        payload = _plugin_public(plugin, source, error, origin=origin,
                                 shadowed=shadowed)
        first = plugin.devices[0]
        payload.update({
            "schema": str(getattr(plugin, "schema", "rui-plugin/v0")),
            "rui_compat": str(getattr(plugin, "rui_compat", "")),
            "license": str(getattr(plugin, "license", "")),
            "maintainer": str(getattr(plugin, "maintainer", "")),
            "homepage": str(getattr(plugin, "homepage", "")),
            "compatibility": (first.compatibility.model_dump(mode="json")
                              if first.compatibility is not None else None),
            "protocol": first.protocol.model_dump(mode="json"),
            "runtime": _runtime_public(first.runtime),
            "device_side": (first.device_side.model_dump(mode="json")
                            if first.device_side is not None else None),
            "acceptance": (first.acceptance.model_dump(mode="json")
                           if first.acceptance is not None else None),
            "devices": [
                {**_device_manifest_public(device),
                 "protocol": device.protocol.model_dump(mode="json"),
                 "runtime": _runtime_public(device.runtime)}
                for device in plugin.devices
            ],
        })
        return payload

    @app.get("/api/v2/capabilities", dependencies=[Depends(v2_auth)])
    async def capabilities() -> dict[str, Any]:
        return {"items": [dict(item) for item in CAPABILITIES]}

    # ------------------------------------------------------------------ devices (read)
    @app.get("/api/v2/devices", dependencies=[Depends(v2_auth)])
    async def devices() -> dict[str, Any]:
        config = cfg_now()
        rows = await device_rows(config)
        return {"items": rows, "total": len(rows)}

    @app.get("/api/v2/devices/{device_id}", dependencies=[Depends(v2_auth)])
    async def device_detail(request: Request, device_id: str) -> dict[str, Any]:
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        return await device_view(device, config, house_warnings(config), detail=True)

    @app.get("/api/v2/devices/{device_id}/audio", dependencies=[Depends(v2_auth)])
    async def device_audio(request: Request, device_id: str) -> dict[str, Any]:
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        # Task-25: the standalone audio read carries the same device-reported wire as
        # the device detail. A failed/absent probe is "not reported" (null), never an
        # invented value.
        reported = None
        if device.enabled:
            adapters_now = getattr(app.state, "adapters", None)
            adapter = (adapters_now.adapters.get(device.id)
                       if adapters_now is not None else None)
            if adapter is not None:
                probe = await safe_probe(adapter, device_id=device.id,
                                         adapter_name=device.adapter)
                reported = _reported_capture_wire(probe.info, probe.error)
        return _audio_provenance(config, device, reported=reported)

    @app.get("/api/v2/devices/{device_id}/activity", dependencies=[Depends(v2_auth)])
    async def device_activity(request: Request, device_id: str) -> dict[str, Any]:
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        limit_raw = request.query_params.get("limit", "50")
        kind = (request.query_params.get("type") or "all").strip().lower()
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            raise _invalid([_field("limit", "invalid", "limit must be an integer")],
                           "invalid query") from None
        if not 1 <= limit <= 200:
            raise _invalid([_field("limit", "out_of_range",
                                   "limit must be between 1 and 200")], "invalid query")
        if kind not in ("all", "turn", "log"):
            raise _invalid([_field("type", "invalid",
                                   "type must be all, turn or log")], "invalid query")
        items = _turn_events(device) + _log_events(config, device)
        if kind != "all":
            items = [item for item in items if item["type"] == kind]
        items.sort(key=lambda item: (item["ts"], item["type"] == "turn"), reverse=True)
        # `total`/`truncated` describe the filtered collection the caller asked for: a
        # `type=log` page must not claim turns it is not returning.
        return {"items": items[:limit], "total": len(items),
                "truncated": len(items) > limit}

    def _turn_events(device: DeviceConfig) -> list[dict[str, Any]]:
        ring = list(getattr(app.state, "recent_turns", None) or [])
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, turn in enumerate(ring):
            if not isinstance(turn, dict):
                continue
            if str(turn.get("device_id") or "") != device.id:
                continue
            ts = turn.get("ts_epoch")
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                ts = 0.0
            event_id = f"turn:{turn.get('turn_id') or f'{int(float(ts) * 1000)}-{index}'}"
            if event_id in seen:
                continue
            seen.add(event_id)
            events.append({
                "id": event_id,
                "ts": float(ts),
                "type": "turn",
                "level": "error" if turn.get("error") else "info",
                "title": str(turn.get("transcript") or ""),
                "detail": {
                    "reply": str(turn.get("reply") or ""),
                    "tools": list(turn.get("tools") or []),
                    "error": str(turn.get("error") or ""),
                },
                "matched_by": "device_id",
            })
        return events

    def _log_events(config: Config, device: DeviceConfig) -> list[dict[str, Any]]:
        path = Path(config.system.data_dir) / LOG_FILE_NAME
        lines = tail_lines(path, 2000)
        needles = [device.id]
        if device.room:
            needles.append(device.room)
        mtime = 0.0
        with contextlib.suppress(OSError):
            mtime = path.stat().st_mtime
        events: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            lowered = line.lower()
            if not any(needle.lower() in lowered for needle in needles if needle):
                continue
            ts = _log_ts(line) or mtime
            upper = line.upper()
            level = ("error" if "ERROR" in upper else
                     "warn" if "WARNING" in upper or " WARN " in upper else "info")
            events.append({
                "id": f"log:{int(ts * 1000)}:{index}",
                "ts": float(ts),
                "type": "log",
                "level": level,
                "title": line.strip()[:500],
                "detail": {"line": line.rstrip("\n")},
                "matched_by": "literal",
            })
        return events

    def _log_ts(line: str) -> float | None:
        for fmt, size in (("%Y-%m-%d %H:%M:%S,%f", 23),
                          ("%Y-%m-%d %H:%M:%S", 19)):
            chunk = line[:size]
            try:
                return datetime.strptime(chunk, fmt).timestamp()
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------ connection info
    def _connection_payload(request: Request, device: DeviceConfig, config: Config,
                            *, reveal: bool) -> dict[str, Any]:
        from . import app as app_module

        resolved = resolve(device, config)
        env = token_env_of(device, resolved)
        token = secrets.get(env) if env else ""
        ws_url, _host, host_source, loopback = ws_url_for(device, request, token)
        allow_no_token = bool(device.allow_no_token)
        transport = (str(resolved.device.transport) if resolved is not None else "inbound")
        plane = app_module._device_plane(app, device.id)
        adapters_now = getattr(app.state, "adapters", None)
        adapter = (adapters_now.adapters.get(device.id)
                   if adapters_now is not None else None)
        health = app_module._adapter_health(adapter) if adapter is not None else {}
        connected = bool(health.get("connected") is True or (
            plane.get("speaker_connected")
            and plane.get("speaker_device") in ("", device.id)))
        token_managed = bool(connected and not token)
        configured = bool(token) or allow_no_token or token_managed
        if reveal:
            hint = {
                "zh": "把 ws_url 填到设备侧；token 已包含。" if token else
                      ("当前允许无 token 连接，同一网络里任何人都能打开麦克风。"
                       if allow_no_token else "还没有设备 token，音箱连不上。"),
                "en": "Paste ws_url into the device client; the token is included." if token
                      else ("allow_no_token is on: anyone on the LAN can open the microphone."
                            if allow_no_token
                            else "No device token is configured; the speaker cannot connect."),
            }
            return {
                "device_id": device.id,
                "ws_url": ws_url,
                "token": token,
                "token_env": env,
                "configured": configured,
                "allow_no_token": allow_no_token,
                "host_source": host_source,
                "hint": hint,
            }
        if token:
            masked: str | None = ws_url.replace(
                f"token={quote(token, safe='')}", "token=\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")
        else:
            masked = ws_url.split("?", 1)[0]
        if transport == "outbound":
            masked = None
        if token_managed:
            hint = {
                "zh": "token 由设备适配器管理，请在设备侧轮换。",
                "en": "The device token is managed by its adapter; rotate it on the "
                      "device side.",
            }
        elif transport == "outbound":
            hint = {
                "zh": "服务器主动拨号，无需客户端 URL。",
                "en": "The server dials the device; no client URL is needed.",
            }
        elif not token and not allow_no_token:
            # `configured` is false here; "the token is included" would be a lie.
            hint = {
                "zh": "还没有设备 token：先生成 token，再把 ws_url 填到设备侧。",
                "en": "No device token yet; rotate one first, then paste ws_url into "
                      "the device client.",
            }
        else:
            hint = {
                "zh": "把 ws_url 填到设备侧；token 已包含。",
                "en": "Paste ws_url into the device client; the token is included.",
            }
        host_hint = None
        if loopback:
            host_hint = {
                "zh": "控制台是通过回环地址打开的；ws_url 使用了音箱可达的地址。",
                "en": "The console was opened via loopback; ws_url uses an address the "
                      "speaker can reach.",
            }
        return {
            "device_id": device.id,
            "transport": transport,
            "configured": configured,
            "token_configured": bool(token) or allow_no_token,
            "token_managed_by_adapter": token_managed,
            "token_env": env,
            "allow_no_token": allow_no_token,
            "listen_port": effective_listen_port(device, resolved),
            "ws_url_masked": masked,
            "host_source": host_source,
            "hint": hint,
            "host_hint": host_hint,
        }

    @app.get("/api/v2/devices/{device_id}/connection", dependencies=[Depends(v2_auth)])
    async def device_connection(request: Request, device_id: str) -> dict[str, Any]:
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        return _connection_payload(request, device, config, reveal=False)

    @app.post("/api/v2/devices/{device_id}/connection/reveal",
              dependencies=[Depends(v2_auth)])
    async def device_reveal(request: Request, device_id: str) -> JSONResponse:
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        resolved = resolve(device, config)
        env = token_env_of(device, resolved)
        token = secrets.get(env) if env else ""
        if token_managed_by_adapter(device, config):
            raise V2Error(
                409, "token_managed_by_adapter",
                "the device token is managed by its adapter",
                hint="Rotate the token on the device side; rui cannot read or change it.",
                details={"device_id": device.id, "token_env": env},
            )
        if not token and not device.allow_no_token:
            raise V2Error(
                409, "token_missing", "no device token is configured",
                hint=f"Set {env} or allow tokenless connections, then retry.",
                details={"device_id": device.id, "token_env": env},
            )
        limit = _reveal_limit(device.id)
        if limit is not None:
            raise V2Error(
                429, "rate_limited", "too many reveal requests for this device",
                hint="Wait a minute before revealing the connection again.",
                details={"retry_after_s": limit}, retryable=True,
                headers={"Retry-After": str(limit)},
            )
        payload = _connection_payload(request, device, config, reveal=True)
        return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})

    def _reveal_limit(device_id: str) -> int | None:
        """Per-device 10/min budget; returns seconds to wait, or None when allowed."""
        now = time.monotonic()
        buckets = getattr(app.state, "v2_reveals", None)
        if not isinstance(buckets, dict):
            buckets = {}
            app.state.v2_reveals = buckets
        recent = [seen for seen in buckets.get(device_id, []) if now - seen < 60.0]
        if len(recent) >= 10:
            buckets[device_id] = recent[-20:]
            return max(1, int(60.0 - (now - recent[0])) + 1)
        recent.append(now)
        buckets[device_id] = recent[-20:]
        return None

    # ------------------------------------------------------------------ write helpers
    def _from_http(exc: Any) -> V2Error:
        """Translate a legacy ``HTTPException`` into the C-002 error object."""
        status = int(getattr(exc, "status_code", 500) or 500)
        detail = getattr(exc, "detail", "")
        if isinstance(detail, dict) and detail.get("code"):
            return V2Error(status, str(detail["code"]),
                           str(detail.get("message") or ""),
                           details=detail.get("details") if isinstance(
                               detail.get("details"), dict) else {})
        text = str(detail)
        if status == 409:
            # The legacy 409s on this path mean "nothing to drive" (no plane, a turn in
            # flight) or "no config file"; `no_config` is the code for the latter only.
            lowered = text.lower()
            if "config" in lowered:
                code = "no_config"
            elif "already running" in lowered:
                code = "step_in_progress"
            else:
                code = "device_not_connected"
        else:
            code = {
                400: "bad_request",
                401: "unauthorized",
                403: "path_not_allowed",
                404: "not_found",
                422: "invalid_values",
                429: "rate_limited",
                502: "action_failed",
            }.get(status, "internal" if status >= 500 else "bad_request")
        hint = ("Start the device-side client and retry."
                if code == "device_not_connected" else "")
        details = {"step": "turn"} if code == "step_in_progress" else {}
        return V2Error(status if status < 500 else 500, code, text, hint=hint,
                       details=details)

    async def _write_devices_checked(updated: Config) -> str:
        try:
            return await write_devices(updated)
        except V2Error:
            raise
        except Exception as exc:  # noqa: BLE001 - legacy HTTPException or a config error
            raise _from_http(exc) from exc

    async def _write_config_checked(updated: Config) -> None:
        try:
            await write_config(updated)
        except V2Error:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _from_http(exc) from exc

    def _validate_config(merged: dict[str, Any], *,
                         layers_from: Config | None = None) -> Config:
        try:
            updated = Config.model_validate(merged)
        except ValidationError as exc:
            raise _invalid(_config_fields(exc, merged), "invalid configuration",
                           hint="Fix the highlighted fields and retry.") from exc
        if layers_from is not None:
            # A full model dump marks every nested field as "set". Keeping the loaded
            # layer objects makes sure the neutral audio/ack defaults do not become
            # explicit choices (which would shadow the plug manifest) just because an
            # unrelated field was written.
            updated = updated.model_copy(
                update={"audio": layers_from.audio, "ack": layers_from.ack})
        return updated

    def _checked_device_config(config: Config, devices: list[DeviceConfig], *,
                               check_ports: bool = True) -> Config:
        """Validate a device-list write before anything reaches the disk.

        The invariant itself lives in `device_write.py` so v1 and the wizard cannot
        drift from it; this wrapper only maps its console-neutral rejection onto the
        C-002 error object. ``check_ports=False`` mirrors v1's DELETE: removing a
        device cannot add a listener, and a config that already has a conflict must
        stay repairable by deleting one of the colliding devices.
        """
        try:
            return checked_device_config(config, devices, check_ports=check_ports)
        except DeviceWriteError as exc:
            raise V2Error(exc.status_code, exc.code, exc.message,
                          hint=exc.hint, details=exc.details) from exc

    def _enabled_ids(config: Config) -> list[str]:
        return [device.id for device in config.devices if device.enabled]

    def _restart_payload(config: Config, error: str, duration_ms: int, *,
                         scope: str = "fleet", auto: bool = True,
                         required: bool = True,
                         restart_fields: list[str] | None = None) -> dict[str, Any]:
        affected = _enabled_ids(config)
        if error:
            status = "failed"
        elif not affected:
            status = "stopped"
        else:
            status = "ok"
        return _restart_obj(scope, required=required, auto=auto, status=status,
                            affected=affected, duration_ms=duration_ms,
                            error=error, restart_fields=restart_fields)

    def _check_param_value(spec: Any, key: str, value: Any) -> Any:
        kind = spec.type
        if kind is ParamType.STRING:
            if not isinstance(value, str):
                raise _invalid([_field(f"params.{key}", "type", "must be a string")])
            return value.strip()
        if kind is ParamType.INT:
            if isinstance(value, bool) or not isinstance(value, int):
                raise _invalid([_field(f"params.{key}", "type", "must be an integer")])
            return value
        if kind is ParamType.BOOL:
            if not isinstance(value, bool):
                raise _invalid([_field(f"params.{key}", "type", "must be a boolean")])
            return value
        if kind is ParamType.LIST:
            if not isinstance(value, list) or not all(isinstance(item, str)
                                                      for item in value):
                raise _invalid([_field(f"params.{key}", "type",
                                       "must be a list of strings")])
            if spec.max is not None and len(value) > spec.max:
                raise _invalid([_field(f"params.{key}", "too_long",
                                       f"allows at most {spec.max} items")])
            return [item.strip() for item in value]
        if kind is ParamType.CHOICE:
            if not isinstance(value, str) or value not in spec.options:
                raise _invalid([_field(f"params.{key}", "enum",
                                       f"must be one of {list(spec.options)}")])
            return value
        raise _invalid([_field(f"params.{key}", "type",
                               f"unsupported param type {kind}")])

    def _merge_device_params(base_params: dict[str, Any], params_patch: Any,
                             manifest: Any) -> tuple[dict[str, Any], list[str]]:
        """Validate a manifest-schema params patch.

        Lead ruling 2026-10-25: v2 never writes a device secret. A device token is
        written by `POST /devices/{id}/token/rotate`, which targets
        `runtime.token_env`; a manifest-declared secret param has no v2 write path
        yet, so sending one is an explicit 400 instead of a silent `secrets.yaml` write.
        """
        if params_patch is not None and not isinstance(params_patch, dict):
            raise _invalid([_field("params", "type", "params must be an object")])
        specs = {spec.key: spec for spec in manifest.params
                 if spec.key not in ("host", "room", "wake_words")}
        params = dict(base_params or {})
        unknown_params: list[str] = []

        for raw_key, value in (params_patch or {}).items():
            key = str(raw_key)
            if key == "plug_id":
                continue
            spec = specs.get(key)
            if spec is None:
                # C-002 §7.11/§7.13: a param the manifest does not declare is ignored -
                # not 422, not stored, and not a reason to delete an already stored
                # legacy value (`mic_gain_db` from the v1 wizard). The caller reports it
                # as `unknown_params_ignored` instead.
                unknown_params.append(key)
                continue
            if spec.type is ParamType.SECRET:
                raise V2Error(
                    400, "secret_rejected",
                    f"param {key!r} is a secret and cannot be written through v2",
                    hint="Device secrets have no v2 write path; use token/rotate "
                         "for a device token.",
                    details={"key": key},
                )
            if value is None:
                params.pop(key, None)
                continue
            params[key] = _check_param_value(spec, key, value)

        missing_public = [
            spec.key for spec in manifest.params
            if spec.required and spec.type is not ParamType.SECRET
            and spec.key not in ("host", "room", "wake_words")
            and params.get(spec.key) in (None, "", [])
        ]
        if missing_public:
            raise _invalid([_field(f"params.{key}", "required",
                                   "this param is required") for key in missing_public])
        return params, sorted(set(unknown_params))

    def _audio_override(current_override: DeviceAudioOverride | None,
                        patch: Any) -> dict[str, Any] | None:
        if patch is None:
            return None
        if not isinstance(patch, dict):
            raise _invalid([_field("audio", "type", "audio must be an object or null")])
        base = (current_override.model_dump(mode="json", exclude_none=True)
                if current_override is not None else {})
        merged = _deep_merge(base, patch)
        try:
            return DeviceAudioOverride.model_validate(merged).model_dump(
                mode="json", exclude_none=True)
        except ValidationError as exc:
            raise _invalid(_pydantic_fields(exc, "audio"),
                           "invalid audio override") from exc

    def _select_manifest(plugin: Any, model_id: str | None) -> Any:
        if model_id:
            for manifest in plugin.devices:
                if manifest.id == model_id:
                    return manifest
            raise V2Error(
                409, "plug_unavailable",
                f"plug {plugin.id!r} has no model {model_id!r}",
                hint="Pick a model id declared by the plug.",
                details={"plugin_id": plugin.id, "model_id": model_id,
                         "models": [item.id for item in plugin.devices]},
            )
        if len(plugin.devices) == 1:
            return plugin.devices[0]
        models = [item.id for item in plugin.devices]
        raise V2Error(
            422, "model_required",
            f"plug {plugin.id!r} declares several models; model_id is required",
            hint="Send model_id from the plug's devices list.",
            details={"plugin_id": plugin.id, "models": models,
                     "fields": [_field("model_id", "required",
                                       f"model_id must be one of {models}")]},
        )

    def _build_device(config: Config, values: dict[str, Any], *,
                      plugin_id: str | None = None,
                      base: DeviceConfig | None = None) -> tuple[DeviceConfig, list[str]]:
        """Validate a create/patch body into a device; nothing is written here."""
        allowed = {"id", "room", "plugin_id", "model_id", "host", "listen_port",
                   "wake_words", "volume_target", "enabled", "params", "audio"}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise _invalid([_field(str(key), "unknown_field", "unknown field")
                            for key in unknown])
        params_patch = values.get("params")
        if params_patch is not None and not isinstance(params_patch, dict):
            raise _invalid([_field("params", "type", "params must be an object")])
        params_patch = dict(params_patch or {})
        mapped_params = {key: params_patch.pop(key) for key in
                         ("host", "room", "wake_words") if key in params_patch}
        entries, _errors = _catalog_entries(config.system.data_dir)
        wanted_plugin = str(values.get("plugin_id") or plugin_id or "").strip()
        resolved = resolve(base, config) if base is not None else None
        if base is not None:
            if resolved is None and not wanted_plugin:
                raise V2Error(
                    409, "plug_unavailable",
                    f"no installed plug describes device {base.id!r}",
                    hint="Install the plug that owns this model, or delete and re-add "
                         "the device.",
                    details={"plugin_id": "", "device_id": base.id},
                )
            if resolved is not None:
                wanted_plugin = wanted_plugin or resolved.plugin.id
        if not wanted_plugin:
            raise _invalid([_field("plugin_id", "required",
                                   "plugin_id is required")])
        found = entries.get(wanted_plugin)
        if found is None:
            raise V2Error(
                409, "plug_unavailable", f"no such plug: {wanted_plugin}",
                hint="Install the plug or pick one from /api/v2/plugins.",
                details={"plugin_id": wanted_plugin, "error": "not_found"},
            )
        plugin, _source, plug_error, _origin, _shadowed = found
        if plug_error:
            raise V2Error(
                409, "plug_unavailable",
                f"plug {wanted_plugin!r} is not usable in this build: {plug_error}",
                details={"plugin_id": wanted_plugin, "error": plug_error},
            )
        if base is not None and resolved is not None:
            # id/plugin_id/model_id are immutable: keep the manifest the device already
            # resolved to instead of asking for model_id again.
            manifest = resolved.device
        else:
            manifest = _select_manifest(
                plugin, str(values.get("model_id") or "").strip() or None)

        device_id = str(values.get("id") or (base.id if base is not None else "")).strip()
        if base is None:
            if not _ID_RE.fullmatch(device_id):
                raise _invalid([_field("id", "invalid",
                                       "id must match ^[a-z0-9][a-z0-9_-]{0,31}$")])
            if any(item.id == device_id for item in config.devices):
                raise V2Error(
                    409, "device_exists", f"device {device_id!r} already exists",
                    hint="Pick another id or edit the existing device.",
                    details={"id": device_id},
                )

        def field_error(name: str, message: str) -> V2Error:
            return _invalid([_field(name, "invalid", message)])

        if "host" in values or "host" in mapped_params:
            try:
                host = host_value(values.get("host", mapped_params.get("host")))
            except Exception as exc:  # noqa: BLE001 - legacy HTTPException
                raise field_error("host", str(getattr(exc, "detail", exc))) from exc
        else:
            host = base.host if base is not None else ""
        if not host:
            raise _invalid([_field("host", "required", "host is required")])

        if "room" in values or "room" in mapped_params:
            try:
                room = room_value(values.get("room", mapped_params.get("room")))
            except Exception as exc:  # noqa: BLE001
                raise field_error("room", str(getattr(exc, "detail", exc))) from exc
        else:
            room = base.room if base is not None else ""
        if config.multi_device.enabled and not room.strip():
            raise _invalid(
                [_field("room", "required",
                        "room is required when multi-device is enabled")],
                hint="Set a room for every enabled speaker, or disable multi-device.",
            )

        volume = (base.volume if base is not None else 60)
        if "volume_target" in values:
            raw_volume = values.get("volume_target")
            if isinstance(raw_volume, bool) or not isinstance(raw_volume, int) \
                    or not 0 <= raw_volume <= 100:
                raise field_error("volume_target", "volume_target must be an int 0..100")
            volume = raw_volume

        listen_port = base.listen_port if base is not None else None
        if "listen_port" in values:
            raw_port = values.get("listen_port")
            if raw_port is not None and (
                    isinstance(raw_port, bool) or not isinstance(raw_port, int)
                    or not 1 <= raw_port <= 65535):
                raise field_error("listen_port",
                                  "listen_port must be an int 1..65535 or null")
            listen_port = raw_port

        enabled = base.enabled if base is not None else True
        if "enabled" in values:
            if not isinstance(values.get("enabled"), bool):
                raise field_error("enabled", "enabled must be a boolean")
            enabled = bool(values["enabled"])

        if "wake_words" in values or "wake_words" in mapped_params:
            raw_words = values.get("wake_words", mapped_params.get("wake_words"))
            try:
                words = wake_words_value({"wake_words": raw_words})
            except Exception as exc:  # noqa: BLE001
                raise field_error("wake_words", str(getattr(exc, "detail", exc))) from exc
            words = words or ["你好小瑞"]
        else:
            words = list(base.wake_words) if base is not None else ["你好小瑞"]

        base_params = public_params(base, resolve(base, config)) if base is not None else {}
        params, unknown_params = _merge_device_params(
            base_params, params_patch, manifest)
        params["plug_id"] = plugin.id

        payload: dict[str, Any] = {
            "id": device_id,
            "adapter": base.adapter if base is not None else manifest.id,
            "host": host,
            "room": room,
            "enabled": enabled,
            "wake_words": words,
            "volume": volume,
            "listen_port": listen_port,
            "params": params,
            "audio": _audio_override(base.audio if base is not None else None,
                                     values.get("audio")) if "audio" in values
                     else (base.audio.model_dump(mode="json", exclude_none=True)
                           if base is not None and base.audio is not None else None),
        }
        if base is not None:
            payload["token_env"] = base.token_env
            payload["allow_no_token"] = base.allow_no_token
            payload["led"] = base.led.model_dump(mode="json")
        else:
            # A fresh device with an LED ring starts lit; leaving the schema default off
            # made a new install look broken. The capability comes from the manifest.
            if "led" in {str(cap) for cap in manifest.capabilities}:
                payload["led"] = {"enabled": True}
        try:
            device = DeviceConfig.model_validate(payload)
        except ValidationError as exc:
            raise _invalid(_pydantic_fields(exc), "invalid device fields") from exc
        return device, unknown_params

    def _config_with_device(config: Config, device: DeviceConfig) -> Config:
        devices = [device if item.id == device.id else item for item in config.devices]
        if not any(item.id == device.id for item in config.devices):
            devices.append(device)
        return _checked_device_config(config, devices)

    # ------------------------------------------------------------------ house writes
    _MULTI_FIELDS = ("enabled", "max_planes", "default_output", "room_priority")

    def _multi_changes(before: Config, after: Config) -> list[str]:
        changes: list[str] = []
        old = before.multi_device
        new = after.multi_device
        for name in _MULTI_FIELDS:
            if name == "room_priority":
                if list(old.arbitration.room_priority) != list(new.arbitration.room_priority):
                    changes.append("multi_device.arbitration.room_priority")
                continue
            if getattr(old, name) != getattr(new, name):
                changes.append(f"multi_device.{name}")
        return changes

    @app.patch("/api/v2/house", dependencies=[Depends(v2_auth)])
    async def patch_house(request: Request) -> dict[str, Any]:
        payload = await _json_body(request)
        unknown = sorted(set(payload) - {"multi_device"})
        if unknown:
            raise _invalid([_field(key, "unknown_field", "unknown field") for key in unknown])
        multi = payload.get("multi_device")
        if not isinstance(multi, dict):
            raise _invalid([_field("multi_device", "required",
                                   "multi_device must be an object")])
        unknown_multi = sorted(set(multi) - set(_MULTI_FIELDS))
        if unknown_multi:
            raise _invalid([_field(f"multi_device.{key}", "unknown_field", "unknown field")
                            for key in unknown_multi])
        config = require_config()
        merged = _validation_document(config)
        current_multi = merged.get("multi_device") or {}
        update: dict[str, Any] = {}
        fields: list[dict[str, str]] = []
        if "enabled" in multi:
            if not isinstance(multi["enabled"], bool):
                fields.append(_field("multi_device.enabled", "type", "must be a boolean"))
            else:
                update["enabled"] = multi["enabled"]
        if "max_planes" in multi:
            value = multi["max_planes"]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
                fields.append(_field("multi_device.max_planes", "out_of_range",
                                     "must be an int 1..8"))
            else:
                update["max_planes"] = value
        if "default_output" in multi:
            value = multi["default_output"]
            if value is not None and not isinstance(value, str):
                fields.append(_field("multi_device.default_output", "type",
                                     "must be a device id or null"))
            else:
                update["default_output"] = value
        if "room_priority" in multi:
            value = multi["room_priority"]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                fields.append(_field("multi_device.room_priority", "type",
                                     "must be a list of device ids"))
            else:
                update.setdefault("arbitration", {})["room_priority"] = value
        if fields:
            raise _invalid(fields)
        merged["multi_device"] = {**current_multi, **update}
        if "arbitration" in update:
            merged["multi_device"]["arbitration"] = {
                **(current_multi.get("arbitration") or {}),
                **update["arbitration"],
            }
        updated = _validate_config(merged, layers_from=config)
        changed = _multi_changes(config, updated)
        await _write_config_checked(updated)
        config = cfg_now()
        return {
            "house": house_payload(config, await device_rows(config)),
            "restart": _restart_obj(
                "service", required=True, auto=False, status="pending",
                affected=[], duration_ms=0, error="",
                restart_fields=changed,
            ),
        }

    @app.get("/api/v2/house/audio", dependencies=[Depends(v2_auth)])
    async def get_house_audio() -> dict[str, Any]:
        config = cfg_now()
        return {
            "audio": config.audio.model_dump(mode="json"),
            "ack": {"echo_ignore_ms": int(config.ack.echo_ignore_ms)},
            "explicit": _explicit_audio_paths(config),
            "effects": {
                "restart": "audio_plane",
                "fields": _audio_restart_fields(config),
            },
        }

    def _audio_changes(before: Config, after: Config) -> list[str]:
        changes: list[str] = []
        old = before.audio.model_dump(mode="json")
        new = after.audio.model_dump(mode="json")
        for block in ("capture", "voice", "playback", "aec"):
            for field in old.get(block, {}):
                if old[block][field] != new[block][field]:
                    changes.append(f"audio.{block}.{field}")
        if before.ack.echo_ignore_ms != after.ack.echo_ignore_ms:
            changes.append("ack.echo_ignore_ms")
        return changes

    @app.patch("/api/v2/house/audio", dependencies=[Depends(v2_auth)])
    async def patch_house_audio(request: Request) -> dict[str, Any]:
        payload = await _json_body(request)
        unknown = sorted(set(payload) - {"audio", "ack"})
        if unknown:
            raise _invalid([_field(key, "unknown_field", "unknown field") for key in unknown])
        if "audio" not in payload and "ack" not in payload:
            raise V2Error(400, "bad_request",
                          "send at least one of audio or ack",
                          hint="Both fields are optional, but a patch needs one.")
        audio_patch = payload.get("audio", {})
        if not isinstance(audio_patch, dict):
            raise _invalid([_field("audio", "type", "audio must be an object")])
        ack_patch = payload.get("ack", {})
        if not isinstance(ack_patch, dict):
            raise _invalid([_field("ack", "type", "ack must be an object")])
        unknown_ack = sorted(set(ack_patch) - {"echo_ignore_ms"})
        if unknown_ack:
            raise _invalid([_field(f"ack.{key}", "unknown_field", "unknown field")
                            for key in unknown_ack])
        if "echo_ignore_ms" in ack_patch:
            value = ack_patch["echo_ignore_ms"]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or not 0 <= value <= 3000):
                raise _invalid([_field("ack.echo_ignore_ms", "out_of_range",
                                       "must be an int 0..3000 or null")])
        config = require_config()
        # Merge onto the explicit layer only: a full dump would restate every field at
        # its core default, and the save would then freeze the neutral layout over the
        # plug manifest. A null still removes the key, so the field falls back.
        audio_mapping = _deep_merge(_explicit_audio_mapping(config.audio), audio_patch)
        # `ack.echo_ignore_ms` is the one ack field with a manifest layer below it: only
        # carry it when the config already chose it, and let the patch set or remove it.
        ack_mapping = config.ack.model_dump(mode="json")
        ack_mapping.pop("echo_ignore_ms", None)
        if "echo_ignore_ms" not in ack_patch and _explicit(
                config.ack, "echo_ignore_ms", AckConfig()):
            ack_mapping["echo_ignore_ms"] = config.ack.echo_ignore_ms
        ack_mapping = _deep_merge(ack_mapping, ack_patch)
        merged = config.model_dump(mode="json")
        merged["audio"] = audio_mapping
        merged["ack"] = ack_mapping
        try:
            new_audio = AudioConfig.model_validate(audio_mapping)
        except ValidationError as exc:
            raise _invalid(_config_fields(exc, merged, "audio"),
                           "invalid audio configuration",
                           hint="Fix the highlighted fields and retry.") from exc
        try:
            new_ack = AckConfig.model_validate(ack_mapping)
        except ValidationError as exc:
            raise _invalid(_config_fields(exc, merged, "ack"),
                           "invalid ack configuration",
                           hint="Fix the highlighted fields and retry.") from exc
        # Cross-model rules (a device override merged onto the new global block) still
        # need the whole config; validation only, the layer objects stay truthful.
        _validate_config(merged, layers_from=None)
        updated = config.model_copy(update={"audio": new_audio, "ack": new_ack})
        changed = _audio_changes(config, updated)
        # `save_config` speaks `"capture.rate"` (a block.field path); the API's
        # `_explicit_audio_paths` speaks `"audio.capture.rate"`.
        explicit_audio = {path.removeprefix("audio.") for path in _explicit_audio_paths(updated)
                          if path.startswith("audio.")}
        ack_explicit = _explicit(new_ack, "echo_ignore_ms", AckConfig())
        with explicit_audio_layers(explicit_audio, ack_explicit):
            await _write_config_checked(updated)
        config = cfg_now()
        return {
            "audio": config.audio.model_dump(mode="json"),
            "ack": {"echo_ignore_ms": int(config.ack.echo_ignore_ms)},
            "explicit": _explicit_audio_paths(config),
            "effects": {
                "restart": "audio_plane",
                "fields": _audio_restart_fields(config),
            },
            "restart": _restart_obj(
                "audio_plane", required=True, auto=False, status="pending",
                affected=[], duration_ms=0, error="", restart_fields=changed,
            ),
        }

    @app.post("/api/v2/house/audio/restart", dependencies=[Depends(v2_auth)])
    async def restart_house_audio() -> dict[str, Any]:
        from . import app as app_module

        config = cfg_now()
        if getattr(app.state, "audio", None) is None and app_module._audio_fleet(app) is None:
            return {
                "restart": _restart_obj(
                    "fleet", required=True, auto=True, status="failed",
                    affected=[], duration_ms=0, error="audio plane not started",
                ),
            }
        started = time.perf_counter()
        error = ""
        try:
            error = await restart_audio(config)
        except Exception as exc:  # noqa: BLE001 - a plane failure is a response, not a 500
            error = _from_http(exc).message
        duration = int((time.perf_counter() - started) * 1000)
        log_line = f"v2 audio restart: {error or 'ok'}"
        __import__("logging").getLogger(__name__).info(log_line)
        return {"restart": _restart_payload(config, error, duration)}

    # ------------------------------------------------------------------ device writes
    @app.post("/api/v2/devices", status_code=201, dependencies=[Depends(v2_auth)])
    async def create_device(request: Request) -> dict[str, Any]:
        payload = await _json_body(request)
        config = require_config()
        device, ignored_params = _build_device(config, payload)
        updated = _config_with_device(config, device)
        started = time.perf_counter()
        error = await _write_devices_checked(updated)
        duration = int((time.perf_counter() - started) * 1000)
        fresh = cfg_now()
        row = await device_view(device, fresh, house_warnings(fresh), detail=True)
        _merge_ignored_warning(row, ignored_params)
        # C-002 §7.11 says "201 + DeviceDetail + restart"; the explicit envelope keeps
        # the device shape exactly §7.12 and the restart object §8.4.
        return {"device": row, "restart": _restart_payload(fresh, error, duration)}

    @app.patch("/api/v2/devices/{device_id}", dependencies=[Depends(v2_auth)])
    async def patch_device(request: Request, device_id: str) -> dict[str, Any]:
        payload = await _json_body(request)
        immutable = sorted(set(payload) & {"id", "plugin_id", "model_id", "adapter"})
        if immutable:
            raise V2Error(
                422, "immutable_field",
                f"{immutable[0]} cannot be changed after creation",
                hint="Delete the device and add it again to change its identity.",
                details={"fields": [_field(
                    immutable[0], "immutable",
                    "this field cannot be changed after creation")]},
            )
        config = require_config()
        device = device_or_404_req(request, device_id, config)
        updated_device, ignored_params = _build_device(config, payload, base=device)
        # The shared write invariant drops a `default_output` that would point at a
        # disabled device. That repair is silent in the file, so the response says it
        # out loud (additive warning, C-002 §7.12) and names the fallback.
        cleared_default_output = bool(
            device.enabled and not updated_device.enabled
            and config.multi_device.default_output == device.id)
        updated = _config_with_device(config, updated_device)
        started = time.perf_counter()
        error = await _write_devices_checked(updated)
        duration = int((time.perf_counter() - started) * 1000)
        fresh = cfg_now()
        row = await device_view(updated_device, fresh, house_warnings(fresh), detail=True)
        _merge_ignored_warning(row, ignored_params)
        if cleared_default_output and fresh.multi_device.default_output is None:
            row["warnings"] = [*(row.get("warnings") or []), {
                "code": "default_output_cleared",
                "message": (f"device {device.id!r} was the default output; disabling it "
                            "cleared multi_device.default_output"),
                "hint": ("Notifications without a source room prefer device 'living' "
                         "(客厅) while it is enabled, then the most recently active "
                         "room. Pick a new default output in the house settings to pin "
                         "one."),
            }]
        return {"device": row, "restart": _restart_payload(fresh, error, duration)}

    @app.delete("/api/v2/devices/{device_id}", dependencies=[Depends(v2_auth)])
    async def delete_device(request: Request, device_id: str) -> dict[str, Any]:
        config = require_config()
        device = device_or_404_req(request, device_id, config)
        confirm = request.query_params.get("confirm")
        if confirm != device_id:
            raise V2Error(
                409, "confirm_required", "confirm the device id to delete",
                hint="Retry with ?confirm=<device id>.",
                details={"impact": [
                    "The audio fleet restarts.",
                    "The device will not re-register by itself.",
                    "Removing the last enabled device stops the audio plane.",
                ]},
            )
        updated = _checked_device_config(
            config, [item for item in config.devices if item.id != device_id],
            check_ports=False)
        await _write_devices_checked(updated)
        return {"removed": True, "device_id": device.id}

    @app.patch("/api/v2/devices/{device_id}/audio", dependencies=[Depends(v2_auth)])
    async def patch_device_audio(request: Request, device_id: str) -> dict[str, Any]:
        payload = await _json_body(request)
        unknown = sorted(set(payload) - {"audio"})
        if unknown:
            raise _invalid([_field(key, "unknown_field", "unknown field") for key in unknown])
        if "audio" not in payload:
            raise V2Error(400, "bad_request", "audio is required",
                          hint="Send {\"audio\": {...}} or {\"audio\": null}.")
        config = require_config()
        device = device_or_404_req(request, device_id, config)
        override = _audio_override(device.audio, payload.get("audio"))
        changed = device.model_copy(update={"audio": (
            DeviceAudioOverride.model_validate(override) if override is not None else None)})
        updated = _config_with_device(config, changed)
        started = time.perf_counter()
        error = await _write_devices_checked(updated)
        duration = int((time.perf_counter() - started) * 1000)
        fresh = cfg_now()
        fresh_device = device_or_404_req(request, device_id, fresh)
        row = await device_view(fresh_device, fresh, house_warnings(fresh), detail=True)
        return {
            # device_view already computed the provenance with the device-reported
            # wire; reuse it so the PATCH response and the detail cannot disagree.
            "audio": row["audio"],
            "device": row,
            "restart": _restart_payload(fresh, error, duration),
        }

    # ------------------------------------------------------------------ token / test
    @app.post("/api/v2/devices/{device_id}/token/rotate", dependencies=[Depends(v2_auth)])
    async def token_rotate(request: Request, device_id: str) -> dict[str, Any]:
        import os

        config = require_config()
        device = device_or_404_req(request, device_id, config)
        resolved = resolve(device, config)
        env = token_env_of(device, resolved)
        if token_managed_by_adapter(device, config):
            raise V2Error(
                409, "token_managed_by_adapter",
                "the device token is managed by its adapter",
                hint="Rotate the token on the device side; writing secrets.yaml here "
                     "would not reach the adapter.",
                details={"device_id": device.id, "token_env": env},
            )
        resolution = secrets.resolve(env)
        if resolution is not None and resolution.source == "environment":
            raise V2Error(
                409, "token_from_environment",
                f"{env} is provided by the process environment; a rotated value in "
                "secrets.yaml would not take effect",
                hint="Update the environment and restart, or remove it there first.",
                details={"token_env": env},
            )
        now = time.monotonic()
        # Lead ruling 2026-10-25: one 10 s cooldown shared with v1 (app.py:2067).
        rotations = getattr(app.state, "token_rotations", None)
        if not isinstance(rotations, dict):
            rotations = {}
            app.state.token_rotations = rotations
        last = rotations.get(device.id)
        retry_after = 10.0 - (now - float(last)) if last else 0.0
        if retry_after > 0:
            raise V2Error(
                429, "rate_limited", "token rotation was requested too recently",
                hint="Wait for the retry window before rotating again.",
                details={"device_id": device.id,
                         "retry_after_s": round(retry_after, 1)},
                retryable=True,
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        token = os.urandom(32).hex()
        try:
            secrets.put(env, token)
        except ValueError as exc:
            raise V2Error(400, "secret_rejected", str(exc),
                          details={"token_env": env}) from exc
        rotations[device.id] = now
        ws_url, _host, _host_source, _loopback = ws_url_for(device, request, token)
        __import__("logging").getLogger(__name__).info(
            "v2 token rotated for device %s (token_env=%s)", device.id, env)
        return {"token": token, "ws_url": ws_url, "token_env": env}

    async def device_test(config: Config, device: DeviceConfig) -> dict[str, Any]:
        """Evidence-only connection test (contract §7.20); writes setup evidence only."""
        from . import app as app_module

        resolved = resolve(device, config)
        transport = (str(resolved.device.transport) if resolved is not None else "inbound")
        outbound = transport == "outbound"
        listen_port = effective_listen_port(device, resolved)
        info = None
        probed: list[str] | None = None
        adapter = None
        adapters_now = getattr(app.state, "adapters", None)
        if device.enabled and adapters_now is not None:
            adapter = adapters_now.adapters.get(device.id)
            if adapter is not None:
                probe = await safe_probe(adapter, device_id=device.id,
                                         adapter_name=device.adapter)
                info = probe.info
                probed = [str(cap) for cap in info.capabilities]
        waited_ms = 0
        usable = False
        if not outbound:
            waited_ms = await app_module._wait_for_device_link(
                app, device.id, app_module.DEVICE_TEST_WAIT_S)
        plane = app_module._device_plane(app, device.id)
        plane_ok = bool(plane["speaker_connected"]) and plane["speaker_device"] in ("", device.id)
        env = token_env_of(device, resolved)
        token_ok = bool(env and secrets.get(env)) or bool(device.allow_no_token)
        token_managed = bool(plane_ok and not token_ok)
        token_pass = bool(token_ok or token_managed)
        declared = ([str(cap) for cap in resolved.device.capabilities]
                    if resolved is not None else [])
        _rows, effective, _unknown = _capability_rows(declared, probed,
                                                      resolved is not None)
        checks: list[dict[str, Any]] = []
        if not device.enabled:
            # The same code the detail gets from `status_reason` and the actions get
            # from their guard: one disabled speaker, one primary reason everywhere.
            checks.append({
                "id": "device",
                "status": "fail",
                "code": "device_disabled",
                "detail": {"enabled": False},
                "hint": "Enable it on the device page.",
            })
        checks.append({
            "id": "plugin",
            "status": "ok" if resolved is not None else "fail",
            "code": "plug_resolved" if resolved is not None else "plug_unresolved",
            "detail": {"plugin_id": (resolved.plugin.id if resolved is not None else ""),
                       "model_id": (resolved.device.id if resolved is not None else "")},
            "hint": "" if resolved is not None else "Install the plug that owns this model.",
        })
        if outbound:
            host_probe = await probe_host(device.host)
            usable = bool(host_probe.get("usable"))
            checks.append({
                "id": "link",
                "status": "ok" if usable else "fail",
                "code": "host_reachable" if usable else "device_unreachable",
                "detail": {"transport": "outbound", "host": device.host,
                           "ssh": host_probe.get("ssh") or {}},
                "hint": str(host_probe.get("hint") or ""),
            })
            checks.append({
                "id": "plane", "status": "not_applicable", "code": "not_applicable",
                "detail": {"transport": "outbound"}, "hint": "",
            })
        else:
            checks.append({
                "id": "link",
                "status": "ok" if plane_ok else "fail",
                "code": "connected" if plane_ok else "device_not_connected",
                "detail": {"connected": plane_ok,
                           "plane_running": bool(plane["running"]),
                           "listen_port": listen_port},
                "hint": "" if plane_ok else _STATUS_REASONS["not_connected"][1],
            })
        checks.append({
            "id": "token",
            "status": "ok" if token_pass else "fail",
            "code": "token_configured" if token_pass else "token_missing",
            "detail": {"token_env": env, "managed_by_adapter": token_managed,
                       "allow_no_token": bool(device.allow_no_token)},
            "hint": "" if token_pass else "Rotate a token on the device page.",
        })
        checks.append({
            "id": "capabilities",
            "status": "ok" if resolved is not None else "fail",
            "code": "capabilities_declared" if resolved is not None else "capabilities_unknown",
            "detail": {"effective": effective,
                       "missing_for_barge_in": _missing_for_barge(
                           effective, device_id=device.id, adapter=device.adapter,
                           model=(str(info.model) if info is not None else device.id),
                           host=device.host)},
            "hint": "",
        })
        ok = all(check["status"] in ("ok", "not_applicable", "skipped") for check in checks)
        errors = [{"code": check["code"], "message": check["detail"].get("error", check["id"]),
                   "hint": check["hint"]}
                  for check in checks if check["status"] == "fail"]
        checked_at = time.time()
        ready = bool(device.enabled and (usable if outbound else plane_ok)) \
            if outbound else bool(device.enabled and plane_ok)
        result = {
            "ok": ok,
            "ready": ready,
            "waited_ms": waited_ms,
            "checked_at": checked_at,
            "transport": transport,
            "checks": checks,
            "errors": errors,
        }
        entry = {"ok": ok, "at": setup_store.iso_now(), "device_id": device.id,
                 "transport": transport, "checks": checks}
        setup_store.update(
            config.system.data_dir,
            lambda data, entry=entry: setup_store.record_device_test(
                data, device.id, entry),
        )
        return result

    @app.post("/api/v2/devices/{device_id}/test", dependencies=[Depends(v2_auth)])
    async def test_device(request: Request, device_id: str) -> dict[str, Any]:
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        return await device_test(config, device)

    # ------------------------------------------------------------------ actions
    async def _live_action_state(device: DeviceConfig,
                                 config: Config) -> tuple[list[str], list[str], bool]:
        """(declared, effective, connected) for one device (C-002 §5.2)."""
        from . import app as app_module

        adapter = None
        probed: list[str] | None = None
        adapters_now = getattr(app.state, "adapters", None)
        if adapters_now is not None:
            adapter = adapters_now.adapters.get(device.id)
        if adapter is not None:
            probe = await safe_probe(adapter, device_id=device.id,
                                     adapter_name=device.adapter)
            probed = [str(cap) for cap in probe.info.capabilities]
        resolved = resolve(device, config)
        declared = ([str(cap) for cap in resolved.device.capabilities]
                    if resolved is not None else [])
        _rows, effective, _unknown = _capability_rows(declared, probed,
                                                      resolved is not None)
        plane = app_module._device_plane(app, device.id)
        connected = bool(plane["speaker_connected"]) and plane["speaker_device"] in (
            "", device.id)
        return declared, effective, connected

    async def _guard_action(device: DeviceConfig, config: Config,
                            action: str) -> tuple[list[str], list[str], bool]:
        """C-002 §5.4 gates: enabled, then capability, then the live link.

        Shared by `POST /devices/{id}/actions` and the setup `verify.say` step so the
        wizard cannot bypass a gate the action endpoint enforces.
        """
        capability = _ACTION_CAPABILITY[action]
        # `device_disabled` is the primary reason and must win every other gate: an
        # operator looking at a disabled speaker has one fix, and a probe-bound
        # capability can be missing only because a disabled device has no adapter to
        # probe. The capability and live-link gates therefore run only once the device
        # is enabled, so the same device answers the same code on /test, detail and
        # every action endpoint.
        if not device.enabled:
            raise V2Error(409, "device_disabled", f"device {device.id!r} is disabled",
                          hint="Enable the device first.",
                          details={"id": device.id})
        declared, effective, connected = await _live_action_state(device, config)
        if capability not in effective:
            raise V2Error(
                409, "capability_unsupported",
                f"device {device.id!r} does not declare {capability!r}",
                hint="The action is hidden by the UI when the capability is missing.",
                details={"capability": capability, "declared": declared},
            )
        if action in _LIVE_ACTION and not connected:
            raise V2Error(
                409, "device_not_connected",
                f"device {device.id!r} has no live link",
                hint=_NO_LIVE_LINK_HINT,
                details={"id": device.id},
            )
        return declared, effective, connected

    @app.post("/api/v2/devices/{device_id}/actions", dependencies=[Depends(v2_auth)])
    async def device_action(request: Request, device_id: str) -> dict[str, Any]:
        from . import app as app_module

        payload = await _json_body(request)
        action = str(payload.get("action") or "").strip()
        args = payload.get("args", {})
        if action not in _ACTION_CAPABILITY:
            raise V2Error(
                400, "unknown_action", f"unknown action: {action!r}",
                hint="Use one of the ids advertised by /api/v2/capabilities.",
                details={"action": action, "allowed": list(_ALLOWED_ACTIONS)},
            )
        if not isinstance(args, dict):
            raise _invalid([_field("args", "type", "args must be an object")])
        config = cfg_now()
        device = device_or_404_req(request, device_id, config)
        _declared, _effective, connected = await _guard_action(device, config, action)
        _validate_action_args(action, args)
        client = app_module._client_for(app, device.id)
        if client is None:
            raise V2Error(
                409, "device_not_connected",
                f"device {device.id!r} has no audio plane client",
                hint=_NO_LIVE_LINK_HINT,
                details={"id": device.id},
            )
        try:
            result, turn = await _execute_action(action, args, device, config, client)
        except V2Error:
            raise
        except Exception as exc:  # noqa: BLE001 - the adapter owns the failure text
            raise V2Error(
                502, "action_failed", f"action {action!r} failed",
                hint="The device is online; retry, then check the service log.",
                details={"action": action, "error": app_module._redact_error(exc)},
                retryable=True,
            ) from exc
        ready = bool(device.enabled and connected)
        response: dict[str, Any] = {
            "action": action,
            "ok": True,
            "code": "ok",
            "result": result,
            "device_status": {"ready": ready, "connected": connected},
        }
        if turn is not None:
            response["turn"] = turn
        return response

    def _validate_action_args(action: str, args: dict[str, Any]) -> None:
        schema = _ACTION_ARGS[action]
        fields: list[dict[str, str]] = []
        properties = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in args:
                fields.append(_field(f"args.{key}", "required", "this argument is required"))
        for key, value in args.items():
            prop = properties.get(key)
            if prop is None:
                fields.append(_field(f"args.{key}", "unknown_field",
                                     "unknown argument"))
                continue
            kind = prop.get("type")
            if kind == "string":
                if not isinstance(value, str):
                    fields.append(_field(f"args.{key}", "type", "must be a string"))
                elif prop.get("minLength") and len(value) < int(prop["minLength"]):
                    fields.append(_field(f"args.{key}", "too_short", "is too short"))
                elif prop.get("maxLength") and len(value) > int(prop["maxLength"]):
                    fields.append(_field(f"args.{key}", "too_long", "is too long"))
                elif prop.get("enum") and value not in prop["enum"]:
                    fields.append(_field(f"args.{key}", "enum",
                                         f"must be one of {list(prop['enum'])}"))
            elif kind == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    fields.append(_field(f"args.{key}", "type", "must be an integer"))
                elif "minimum" in prop and value < int(prop["minimum"]):
                    fields.append(_field(f"args.{key}", "out_of_range", "is below the minimum"))
                elif "maximum" in prop and value > int(prop["maximum"]):
                    fields.append(_field(f"args.{key}", "out_of_range", "is above the maximum"))
            elif kind == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    fields.append(_field(f"args.{key}", "type", "must be a number"))
            elif kind == "boolean":
                if not isinstance(value, bool):
                    fields.append(_field(f"args.{key}", "type", "must be a boolean"))
        if fields:
            raise _invalid(fields, "invalid action arguments")

    async def _execute_action(action: str, args: dict[str, Any], device: DeviceConfig,
                              config: Config, client: Any) -> tuple[dict[str, Any],
                                                                    dict[str, Any] | None]:
        from ...audio.protocol import PlayFile, StopAudio

        if action == "volume.read":
            value = await client.device_volume()
            if value is None:
                raise V2Error(502, "action_failed", "the device did not report a mixer value",
                              details={"action": action, "error": "no_value"},
                              retryable=True)
            return {"value": int(value), "range": "0-255"}, None
        if action == "volume.set":
            applied = await client.set_device_volume(int(args["value"]))
            return {"value": int(applied)}, None
        if action == "led.set":
            await client.set_led(str(args["effect"]))
            return {"effect": str(args["effect"])}, None
        if action == "audio.say":
            result = await run_debug_say(str(args["text"]), device.id)
            return ({
                "reply": str(result.get("reply") or ""),
                "audio_bytes": int(result.get("audio_bytes") or 0),
                "tools": list(result.get("tools") or []),
                "error": str(result.get("error") or ""),
            }, {"turn_id": f"turn_{uuid.uuid4().hex[:12]}", "device_id": device.id})
        if action == "audio.play":
            path = str(args["file"])
            resolved_path = Path(path).resolve()
            roots = [Path(config.system.data_dir).resolve()]
            cache = str(getattr(config.music, "cache_dir", "") or "")
            if cache:
                roots.append(Path(cache).resolve())
            inside = any(resolved_path == root or root in resolved_path.parents
                         for root in roots)
            if not resolved_path.is_file() or not inside:
                raise V2Error(
                    403, "path_not_allowed",
                    "the file must be inside data_dir or the music cache",
                    hint="Copy the file into the data directory first.",
                    details={"file": path},
                )
            await client.send(PlayFile(path=path, loop=bool(args.get("loop", False)),
                                       gain=float(args.get("gain", 1.0))))
            return {"playing": path}, None
        if action == "audio.stop":
            await client.send(StopAudio())
            return {"playing": None}, None
        raise V2Error(400, "unknown_action", f"unknown action: {action!r}",
                      details={"action": action, "allowed": list(_ALLOWED_ACTIONS)})

    # ------------------------------------------------------------------ setup plan
    def _keys_state(config: Config) -> tuple[dict[str, bool], list[str]]:
        stages: dict[str, bool] = {}
        missing: set[str] = set()
        for stage in ("asr", "tts", "llm"):
            ok, stage_missing = keycheck.stage_satisfied(config, stage)
            stages[stage] = ok
            missing.update(stage_missing)
        return stages, sorted(missing)

    def _models_state(config: Config) -> dict[str, bool]:
        asr_dir: Path | None = None
        for name in config.providers.asr.chain:
            provider = config.providers.catalog.get(name)
            if provider is None or str(getattr(provider, "kind", "")) != "sherpa":
                continue
            model = str(getattr(provider, "model", "") or "")
            if model:
                asr_dir = Path(model).expanduser()
                break
        asr_required = asr_dir is not None
        asr_ok = bool(
            asr_dir is not None and asr_dir.is_dir()
            and (asr_dir / "tokens.txt").is_file()
            and any((asr_dir / name).is_file()
                    for name in ("model.int8.onnx", "model.onnx"))
        )
        voice = config.audio.voice
        vad = Path(voice.vad_model_path).expanduser() if voice.vad_model_path else None
        kws = Path(voice.kws_model_dir).expanduser() if voice.kws_model_dir else None
        try:
            kws_files = model_files(kws) if kws is not None else {}
        except KwsUnavailable:
            kws_files = {}
        return {
            "asr": asr_ok if asr_required else True,
            "vad": bool(vad and vad.is_file()),
            "kws": len(kws_files) == 4,
        }

    def _keywords_path(config: Config) -> Path | None:
        configured = str(config.audio.voice.kws_keywords_file or "").strip()
        if configured:
            return Path(configured).expanduser()
        if config_path is not None:
            return Path(config_path).resolve().parent / "keywords.txt"
        return None

    def _turn_after_setup(started: str) -> bool:
        start = setup_store.epoch(started)
        turns = list(getattr(app.state, "recent_turns", None) or [])
        last = getattr(app.state, "last_turn", None)
        if isinstance(last, dict):
            turns.append(last)
        for turn in turns:
            if not isinstance(turn, dict) or int(turn.get("audio_bytes") or 0) <= 0:
                continue
            stamp = turn.get("ts_epoch")
            if start is not None and isinstance(stamp, (int, float)) \
                    and float(stamp) < start:
                continue
            return True
        return False

    def _plan_context(config: Config, *, plugin_id: str | None,
                      device_id: str | None, model_id: str | None) -> dict[str, Any]:
        wanted_device = (device_id or "").strip()
        if wanted_device and not _ID_RE.fullmatch(wanted_device):
            raise _invalid([_field("device_id", "invalid",
                                   "device_id must match ^[a-z0-9][a-z0-9_-]{0,31}$")])
        existing = next((item for item in config.devices if item.id == wanted_device), None)
        mode = "edit" if existing is not None else (
            "first-run" if not config.devices else "add")
        entries, _errors = _catalog_entries(config.system.data_dir)
        resolved = resolve(existing, config) if existing is not None else None
        wanted_plugin = (plugin_id or "").strip()
        plugin = None
        manifest = None
        source = ""
        blocked: dict[str, Any] | None = None
        if existing is not None:
            if wanted_plugin and (resolved is None or resolved.plugin.id != wanted_plugin):
                raise V2Error(
                    409, "plug_mismatch",
                    f"device {existing.id!r} belongs to a different plug",
                    hint="Omit plugin_id for an existing device.",
                    details={"plugin_id": wanted_plugin, "device_id": existing.id},
                )
            if resolved is None:
                # §7.23: an unusable plug is a global blocker in the plan body, not a
                # failed plan request. The UI shows `blocked` and offers the fix.
                blocked = {"code": "plug_unavailable",
                           "message": f"no installed plug describes device {existing.id!r}",
                           "device_id": existing.id}
            else:
                plugin = resolved.plugin
                manifest = resolved.device
                source = resolved.source
        else:
            if not wanted_plugin:
                raise _invalid([_field("plugin_id", "required",
                                       "plugin_id is required for a new device")])
            found = entries.get(wanted_plugin)
            if found is None:
                blocked = {"code": "plug_unavailable",
                           "message": f"no such plug: {wanted_plugin}",
                           "plugin_id": wanted_plugin}
            else:
                plugin, source, error, _origin, _shadowed = found
                if error:
                    blocked = {"code": "plug_unavailable",
                               "message": f"plug {wanted_plugin!r} is not usable in "
                                          f"this build: {error}",
                               "plugin_id": wanted_plugin}
                else:
                    manifest = _select_manifest(
                        plugin, (model_id or "").strip() or None)
        start = str(getattr(app.state, "setup_started_at", "") or "")
        if not start:
            start = setup_store.ensure_started(config.system.data_dir)
            app.state.setup_started_at = start
        return {
            "mode": mode,
            "device_id": wanted_device or (existing.id if existing is not None else ""),
            "requested_plugin": wanted_plugin,
            "device": existing,
            "plugin": plugin,
            "manifest": manifest,
            "source": source,
            "started": start,
            "saved": setup_store.read(config.system.data_dir),
            "resolved": resolved,
            "blocked": blocked,
        }

    def _blocked_plan_payload(config: Config, ctx: dict[str, Any]) -> dict[str, Any]:
        """The 200 body for a plan that cannot resolve its plug (contract §7.23)."""
        keys_stages, keys_missing = _keys_state(config)
        keys_done = all(keys_stages.get(stage, False) for stage in ("asr", "tts", "llm"))
        models = _models_state(config)
        steps = [
            {
                "id": "keys", "scope": "house", "required": True,
                "status": "done" if keys_done else "pending",
                "evidence": {"stages": keys_stages, "missing": keys_missing},
                "actions": ["keys.save", "keys.test", "providers.save"],
            },
            {
                "id": "models", "scope": "house", "required": True,
                "status": "done" if all(models.values()) else "pending",
                "evidence": models,
                "actions": [],
            },
        ]
        steps.extend({
            "id": name, "scope": "device", "required": name != "verify",
            "status": "pending",
            # A blocked plan has no evidence, but the test step keeps the §7.23 shape
            # so clients that read `tested`/`ok` do not have to special-case it.
            "evidence": ({"ok": False, "tested": False, "checks": [], "at": ""}
                         if name == "test" else {}),
            "actions": [],
        } for name in ("prepare", "params", "wake", "connect", "test", "verify"))
        return {
            "mode": ctx["mode"],
            "device_id": ctx["device_id"] or None,
            "plugin": {"id": ctx.get("requested_plugin") or "", "version": "",
                       "name": ""},
            "steps": steps,
            "next_step": "keys" if not keys_done else (
                "models" if not all(models.values()) else None),
            "complete": False,
            "verified": False,
            "blocked": ctx["blocked"],
        }

    def _plan_payload(config: Config, ctx: dict[str, Any],
                      request: Request) -> dict[str, Any]:
        from . import app as app_module

        if ctx["plugin"] is None or ctx["manifest"] is None:
            return _blocked_plan_payload(config, ctx)
        plugin = ctx["plugin"]
        manifest = ctx["manifest"]
        device: DeviceConfig | None = ctx["device"]
        mode = ctx["mode"]
        saved = ctx["saved"]
        keys_stages, keys_missing = _keys_state(config)
        keys_done = all(keys_stages.get(stage, False) for stage in ("asr", "tts", "llm"))
        models = _models_state(config)
        models_done = all(models.values())
        plane_error = str(getattr(app.state, "plane_error", "") or "")

        manual = saved.get("prepare", {}).get("manual", {})
        if not isinstance(manual, dict):
            manual = {}
        artifact = str(manifest.device_side.artifact) if manifest.device_side else "none"
        needs_rollback = str(manifest.device_class) == "B"
        confirmed = bool(manual.get("confirm"))
        rollback_ack = bool(manual.get("rollback_ack"))
        prepare_skipped = artifact == "none"
        prepare_done = prepare_skipped or (
            confirmed and (rollback_ack or not needs_rollback))
        device_side = manifest.device_side
        prepare_step = {
            "id": "prepare",
            "scope": "device",
            "required": True,
            "status": "skipped" if prepare_skipped else (
                "done" if prepare_done else "pending"),
            "evidence": {"artifact": artifact, "confirm": confirmed,
                         "rollback_ack": rollback_ack,
                         "rollback_required": needs_rollback},
            "detail": {
                "links": ([{"label": "Device-side setup guide",
                            "url": device_side.source}]
                          if device_side is not None and device_side.source else []),
                "install_steps": (list(device_side.install_steps)
                                  if device_side is not None else []),
                "name": device_side.name if device_side is not None else "",
                "rollback": device_side.rollback if device_side is not None else "",
            },
            "actions": [] if prepare_skipped else ["manual.confirm"],
        }

        missing_params: list[str] = []
        if device is not None:
            for spec in manifest.params:
                if not spec.required:
                    continue
                if spec.type is ParamType.SECRET:
                    if secrets.resolve(spec.key.upper()) is None:
                        missing_params.append(spec.key)
                    continue
                if spec.key == "host":
                    value: Any = device.host
                elif spec.key == "room":
                    value = device.room
                elif spec.key == "wake_words":
                    value = device.wake_words
                else:
                    value = (device.params or {}).get(spec.key)
                if value in (None, "", []):
                    missing_params.append(spec.key)
        params_ready = bool(
            device is not None and device.enabled and device.host
            and ctx["resolved"] is not None
            and ctx["resolved"].plugin.id == plugin.id
            and not missing_params
        )
        params_status = "done" if params_ready else "pending"
        if params_ready and plane_error:
            params_status = "error"
        defaults = {
            spec.key: spec.default for spec in manifest.params
            if spec.type is not ParamType.SECRET and spec.default is not None
        }
        if device is not None and device.room:
            defaults["room"] = device.room
        params_step = {
            "id": "params",
            "scope": "device",
            "required": True,
            "status": params_status,
            "evidence": {"device_id": device.id if device is not None else None,
                         "enabled": bool(device and device.enabled),
                         "missing": missing_params,
                         "plane_error": plane_error},
            "detail": {"params_schema": [_param_schema(spec) for spec in manifest.params],
                       "defaults": defaults},
            "actions": ["device.update"] if mode == "edit" else ["device.create"],
        }

        words = list(device.wake_words) if device is not None else []
        keywords = _keywords_path(config)
        exists = bool(keywords and keywords.is_file())
        text = ""
        if exists and keywords is not None:
            with contextlib.suppress(OSError):
                text = keywords.read_text(encoding="utf-8")
        words_in_file = bool(text) and all(f"@{word}" in text for word in words) if words \
            else False
        wake_done = bool(words and exists and words_in_file)
        wake_step = {
            "id": "wake",
            "scope": "device",
            "required": True,
            "status": ("done" if wake_done else "pending") if not (
                wake_done and plane_error) else "error",
            "evidence": {"words": words, "keywords_exists": exists,
                         "keywords_file": str(keywords) if keywords else ""},
            "actions": ["wake.set"],
        }

        transport = (str(ctx["resolved"].device.transport)
                     if ctx["resolved"] is not None else "inbound")
        outbound = transport == "outbound"
        # `_plane_snapshot` aggregates the fleet: a connected living room must not turn
        # this draft or this other room's link hint green. Ask the device's own plane,
        # and give a draft (no device yet) no link at all.
        if device is not None:
            device_plane = app_module._device_plane(app, device.id)
            connected = bool(device_plane.get("speaker_connected")) and str(
                device_plane.get("speaker_device") or "") in ("", device.id)
        else:
            connected = False
        env = (token_env_of(device, ctx["resolved"]) if device is not None
               else (manifest.runtime.token_env if manifest.runtime is not None
                     else "RUI_DEVICE_TOKEN"))
        configured = bool(secrets.resolve(env)) or bool(device and device.allow_no_token)
        ws_masked: str | None = None
        if device is not None and not outbound:
            connection = _connection_payload(request, device, config, reveal=False)
            ws_masked = connection.get("ws_url_masked")
        connect_step = {
            "id": "connect",
            "scope": "device",
            "required": True,
            "status": "skipped" if outbound else ("done" if connected else "pending"),
            "evidence": {"configured": configured, "ws_url_masked": ws_masked,
                         "transport": transport, "token_env": env},
            "actions": [],
        }

        # Only this device's own record counts (per-device store, with the legacy
        # single slot as fallback), and a draft (device is None) owns no record at
        # all (C-002 A-5).
        stored_test = _device_test_record(saved, device)
        # `device.test` always writes this record (ok/at/checks), so a non-empty dict
        # means a probe ran. `ok=false` is a result, not a missing one: the live link
        # must never overwrite a failed test, otherwise a connected speaker with a bad
        # token/capability would look "done" and the wizard would skip the failing
        # checks. The link is only a hint for the never-tested case (inbound only).
        tested = bool(stored_test)
        # Only the JSON boolean true is a pass; a corrupted `"ok": "false"` string or a
        # missing key is a failed (or incomplete) record, never a pass.
        stored_ok = stored_test.get("ok") is True
        if tested:
            test_done = stored_ok
            test_ok = stored_ok
        else:
            test_done = bool(connected and not outbound)
            test_ok = test_done
        test_step = {
            "id": "test",
            "scope": "device",
            "required": True,
            "status": "done" if test_done else "pending",
            # Additive (C-002 Addendum A-5): `ok` alone could not tell "passed" from
            # "never tested but the link is up"; `tested` makes the evidence explicit.
            "evidence": {"ok": test_ok,
                         "tested": tested,
                         "checks": list(stored_test.get("checks") or []),
                         "at": str(stored_test.get("at") or "")},
            "actions": ["device.test"],
        }

        verify_manual = saved.get("verify", {}).get("manual", {})
        if not isinstance(verify_manual, dict):
            verify_manual = {}
        verified = bool(verify_manual.get("confirm")) or _turn_after_setup(ctx["started"])
        verify_step = {
            "id": "verify",
            "scope": "device",
            "required": False,
            "status": "done" if verified else "pending",
            "evidence": {"text_tested": verified},
            "actions": ["verify.say"],
        }

        steps = [
            {
                "id": "keys", "scope": "house", "required": True,
                "status": "done" if keys_done else "pending",
                "evidence": {"stages": keys_stages, "missing": keys_missing},
                "actions": ["keys.save", "keys.test", "providers.save"],
            },
            {
                "id": "models", "scope": "house", "required": True,
                "status": "done" if models_done else "pending",
                "evidence": models,
                "actions": [],
            },
            prepare_step, params_step, wake_step, connect_step, test_step, verify_step,
        ]
        next_step = next(
            (step["id"] for step in steps
             if step["id"] not in setup_store.OPTIONAL_STEPS
             and step["status"] not in ("done", "skipped")),
            None,
        )
        required_done = all(
            step["status"] in ("done", "skipped")
            for step in steps if step["id"] not in setup_store.OPTIONAL_STEPS)
        blocked = ctx.get("blocked")
        if blocked is None and mode == "add" and not (keys_done and models_done):
            missing_steps = [step for step, done in
                             (("keys", keys_done), ("models", models_done)) if not done]
            blocked = {"code": "house_setup_incomplete",
                       "message": "finish the house setup steps before adding a device",
                       "missing_steps": missing_steps}
        return {
            "mode": mode,
            "device_id": ctx["device_id"] or None,
            "plugin": {"id": str(plugin.id), "version": str(plugin.version),
                       "name": str(plugin.name)},
            "steps": steps,
            "next_step": next_step,
            "complete": required_done,
            "verified": verified,
            "blocked": blocked,
        }

    @app.get("/api/v2/setup/plan", dependencies=[Depends(v2_auth)])
    async def setup_plan(request: Request) -> dict[str, Any]:
        config = cfg_now()
        ctx = _plan_context(
            config,
            plugin_id=request.query_params.get("plugin_id"),
            device_id=request.query_params.get("device_id"),
            model_id=request.query_params.get("model_id"),
        )
        return _plan_payload(config, ctx, request)

    @app.post("/api/v2/setup/plan/steps/{step}", dependencies=[Depends(v2_auth)])
    async def setup_step(request: Request, step: str) -> dict[str, Any]:
        payload = await _json_body(request)
        if step not in _PLAN_STEPS:
            raise V2Error(
                400, "unknown_step", f"no such setup step: {step!r}",
                details={"steps": list(_PLAN_STEPS)},
            )
        action = str(payload.get("action") or "").strip()
        allowed = _PLAN_ACTIONS.get(step, set())
        if action not in allowed:
            raise V2Error(
                400, "action_not_allowed",
                f"action {action!r} is not allowed for step {step!r}",
                details={"step": step, "action": action, "allowed": sorted(allowed)},
            )
        values = payload.get("values", {})
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise _invalid([_field("values", "type", "values must be an object")])
        plugin_id = str(payload.get("plugin_id") or "").strip() or None
        device_id = str(payload.get("device_id") or "").strip() or None
        lock = app.state.setup_locks[step]
        if lock.locked():
            raise V2Error(
                409, "step_in_progress", f"step {step!r} is already running",
                hint="Wait for the other request to finish.",
                details={"step": step},
            )
        async with lock:
            config = require_config()
            ctx = _plan_context(config, plugin_id=plugin_id, device_id=device_id,
                               model_id=str(payload.get("model_id") or "") or None)
            if ctx["plugin"] is None or ctx["manifest"] is None:
                blocked = ctx.get("blocked") or {}
                raise V2Error(
                    409, "plug_unavailable",
                    str(blocked.get("message") or "the plug is not available"),
                    hint="Fix the plug selection before running a setup step.",
                    details={"plugin_id": ctx.get("requested_plugin") or "",
                             "device_id": device_id or ""},
                )
            plan = _plan_payload(config, ctx, request)
            # Only the device-scoped steps depend on the house keys/models: the keys and
            # models steps themselves are how an add-mode plan is unblocked (§7.24).
            if ctx["mode"] == "add" and plan["blocked"] and step in _DEVICE_STEPS:
                raise V2Error(
                    409, "house_setup_incomplete", plan["blocked"]["message"],
                    hint="Finish the keys and models steps first.",
                    details={"missing_steps": plan["blocked"]["missing_steps"]},
                )
            restart: dict[str, Any] | None = None
            result: dict[str, Any] | None = None
            target_device = ctx["device"]
            ignored_params: list[str] = []
            if action == "keys.save":
                raw = values.get("values", values)
                if not isinstance(raw, dict) or not raw:
                    raise _invalid([_field("values", "required",
                                           "values must be a non-empty object")])
                for name, value in raw.items():
                    if not isinstance(value, str):
                        raise _invalid([_field(f"values.{name}", "type",
                                               "secret values must be strings")])
                    try:
                        secrets.put(str(name), value)
                    except ValueError as exc:
                        raise V2Error(400, "secret_rejected", str(exc),
                                      details={"key": str(name)}) from exc
                setup_store.update(
                    config.system.data_dir,
                    lambda data: setup_store.set_value(
                        data, ("keys", "last_saved_at"), setup_store.iso_now()),
                )
            elif action == "keys.test":
                stage = str(values.get("stage") or "").strip().lower()
                if stage not in STAGES:
                    raise _invalid([_field("values.stage", "enum",
                                           f"stage must be one of {list(STAGES)}")])
                provider = str(values.get("provider") or "").strip() or None
                raw_keys = values.get("keys", values.get("values"))
                ephemeral: dict[str, str] | None = None
                if raw_keys is not None:
                    if not isinstance(raw_keys, dict):
                        raise _invalid([_field("values.keys", "type",
                                               "keys must be an object")])
                    ephemeral = {str(name): str(value) for name, value in raw_keys.items()}
                try:
                    result = await keycheck.test_provider(
                        config, stage, provider, keys=ephemeral)
                except keycheck.KeyCheckError as exc:
                    raise V2Error(400, exc.code, str(exc), details=exc.details) from exc
                if ephemeral is None:
                    setup_store.update(
                        config.system.data_dir,
                        lambda data, result=result: setup_store.set_value(
                            data, ("keys", "last_test"), result),
                    )
            elif action == "providers.save":
                stage = str(values.get("stage") or "").strip().lower()
                if stage not in STAGES:
                    raise _invalid([_field("values.stage", "enum",
                                           f"stage must be one of {list(STAGES)}")])
                provider = str(values.get("provider") or "").strip()
                if provider.startswith(f"{stage}:"):
                    provider = provider.split(":", 1)[1].strip()
                offered = keycheck.stage_provider_names(config, stage)
                if not provider or provider not in offered:
                    raise _invalid([_field("values.provider", "enum",
                                           f"provider must be one of {offered}")],
                                   details={"stage": stage, "available": offered})
                merged = _validation_document(config)
                previous_chain = list(getattr(config.providers, stage).chain)
                merged["providers"][stage]["chain"] = [provider] + [
                    name for name in previous_chain if name != provider]
                voice = values.get("voice")
                if stage == "tts" and voice is not None:
                    if not isinstance(voice, str):
                        raise _invalid([_field("values.voice", "type",
                                               "voice must be a string")])
                    merged["providers"]["catalog"][provider]["voice"] = voice.strip()
                updated = _validate_config(merged, layers_from=config)
                if updated.providers.model_dump(mode="json") != \
                        config.providers.model_dump(mode="json"):
                    await _write_config_checked(updated)
            elif action == "manual.confirm":
                confirm = values.get("confirm", True)
                rollback = values.get("rollback_ack", False)
                if not isinstance(confirm, bool) or not isinstance(rollback, bool):
                    raise _invalid([_field("values", "type",
                                           "confirm/rollback_ack must be booleans")])
                if step == "prepare" and str(ctx["manifest"].device_class) == "B" \
                        and confirm and not rollback:
                    raise _invalid([_field("values.rollback_ack", "required",
                                           "class B plugs require rollback_ack=true")])
                def confirm_state(data: dict, *, confirm: bool = confirm,
                                  rollback: bool = rollback) -> bool:
                    branch = "prepare" if step == "prepare" else "verify"
                    changed = setup_store.set_value(
                        data, (branch, "manual", "confirm"), confirm)
                    if changed:
                        setup_store.set_value(
                            data, (branch, "manual", "at"), setup_store.iso_now())
                    if branch == "prepare":
                        setup_store.set_value(
                            data, (branch, "manual", "rollback_ack"), rollback)
                    return changed

                setup_store.update(config.system.data_dir, confirm_state)
            elif action == "device.create":
                if ctx["mode"] not in ("add", "first-run"):
                    raise V2Error(
                        400, "action_not_allowed",
                        f"device.create is not allowed in {ctx['mode']} mode",
                        hint="Edit the existing device with device.update.",
                        details={"step": step, "action": action,
                                 "allowed": ["device.update"], "mode": ctx["mode"]},
                    )
                create_values = dict(values)
                create_values.setdefault("plugin_id", ctx["plugin"].id)
                if "id" not in create_values and device_id:
                    create_values["id"] = device_id
                device, ignored_params = _build_device(config, create_values)
                updated = _config_with_device(config, device)
                started = time.perf_counter()
                error = await _write_devices_checked(updated)
                duration = int((time.perf_counter() - started) * 1000)
                fresh = cfg_now()
                target_device = device
                restart = _restart_payload(fresh, error, duration)
            elif action == "device.update":
                if ctx["mode"] != "edit" or ctx["device"] is None:
                    raise V2Error(
                        400, "action_not_allowed",
                        f"device.update is not allowed in {ctx['mode']} mode",
                        hint="Create the device first.",
                        details={"step": step, "action": action,
                                 "allowed": ["device.create"], "mode": ctx["mode"]},
                    )
                immutable = sorted(set(values) & set(_IMMUTABLE_DEVICE_FIELDS))
                if immutable:
                    raise V2Error(
                        422, "immutable_field",
                        f"{immutable[0]} cannot be changed after creation",
                        hint="Delete the device and add it again to change its identity.",
                        details={"fields": [
                            _field(name, "immutable",
                                   "this field cannot be changed after creation")
                            for name in immutable
                        ]},
                    )
                update_values = dict(values)
                update_values.setdefault("id", ctx["device"].id)
                device, ignored_params = _build_device(
                    config, update_values, base=ctx["device"])
                updated = _config_with_device(config, device)
                started = time.perf_counter()
                error = await _write_devices_checked(updated)
                duration = int((time.perf_counter() - started) * 1000)
                fresh = cfg_now()
                target_device = device
                restart = _restart_payload(fresh, error, duration)
            elif action == "wake.set":
                target = ctx["device"]
                wanted = str(values.get("device_id") or device_id
                             or (target.id if target is not None else ""))
                if not wanted:
                    raise V2Error(409, "plug_unavailable",
                                  "no device to set wake words on",
                                  hint="Create the device first.")
                try:
                    words = wake_words_value(values)
                except Exception as exc:  # noqa: BLE001
                    raise _invalid([_field("values.wake_words", "invalid",
                                           str(getattr(exc, "detail", exc)))]) from exc
                if not words:
                    raise _invalid([_field("values.wake_words", "required",
                                           "wake_words is required")])
                current_device = next(
                    (item for item in config.devices if item.id == wanted), None)
                if current_device is None:
                    raise _not_found("device", wanted)
                changed_device = current_device.model_copy(update={"wake_words": words})
                updated = _config_with_device(config, changed_device)
                started = time.perf_counter()
                error = await _write_devices_checked(updated)
                duration = int((time.perf_counter() - started) * 1000)
                fresh = cfg_now()
                target_device = changed_device
                restart = _restart_payload(fresh, error, duration)
            elif action == "device.test":
                target = ctx["device"]
                if target is None:
                    raise _not_found("device", device_id or "")
                result = await device_test(config, target)
            elif action == "verify.say":
                target = ctx["device"]
                if target is None:
                    raise _not_found("device", device_id or "")
                # §7.24 says this walks §7.21 `audio.say`, so the same gates and the
                # same argument schema apply - a wizard must not reach a device the
                # action endpoint would refuse.
                raw_text = values.get("text")
                if not isinstance(raw_text, str):
                    raise _invalid([_field("values.text", "type",
                                           "text must be a string")])
                text = raw_text.strip()
                if not text:
                    raise _invalid([_field("values.text", "required",
                                           "text is required")])
                if len(text) > 500:
                    raise _invalid([_field("values.text", "too_long",
                                           "text must be at most 500 characters")])
                await _guard_action(target, config, "audio.say")
                try:
                    spoken = await run_debug_say(text, target.id)
                except V2Error:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise _from_http(exc) from exc
                if int(spoken.get("audio_bytes") or 0) > 0:
                    def mark_verified(data: dict) -> bool:
                        changed = setup_store.set_value(
                            data, ("verify", "manual", "confirm"), True)
                        if changed:
                            setup_store.set_value(
                                data, ("verify", "manual", "at"),
                                setup_store.iso_now())
                        return changed

                    setup_store.update(config.system.data_dir, mark_verified)

            fresh = cfg_now()
            fresh_ctx = _plan_context(
                fresh, plugin_id=plugin_id or getattr(ctx["plugin"], "id", None),
                device_id=(device_id or (target_device.id if target_device else None)),
                model_id=None,
            )
            device_payload = None
            if target_device is not None:
                current_target = next(
                    (item for item in fresh.devices if item.id == target_device.id), None)
                if current_target is not None:
                    device_payload = await device_view(
                        current_target, fresh, house_warnings(fresh), detail=True)
                    _merge_ignored_warning(device_payload, ignored_params)
            response: dict[str, Any] = {
                "plan": _plan_payload(fresh, fresh_ctx, request),
                "device": device_payload,
                "restart": restart,
            }
            if result is not None:
                response["result"] = result
            return response

    # ------------------------------------------------------------------ static hosting
    shell = Path(dist) / "v2.html"

    @app.get("/v2/assets/{asset_path:path}")
    async def v2_asset(asset_path: str) -> FileResponse:
        root = (Path(dist) / "assets").resolve()
        candidate = (root / asset_path).resolve()
        if not candidate.is_file() or (candidate != root and root not in candidate.parents):
            raise _not_found("asset", asset_path)
        return FileResponse(
            candidate,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/v2", response_class=HTMLResponse)
    @app.get("/v2/{full_path:path}", response_class=HTMLResponse)
    async def v2_shell(full_path: str = "") -> Any:
        # History-mode routes ("/v2/devices/living") must survive a refresh, but the
        # shell must never be served for the API: "/api/*" is a different prefix and
        # the API catch-all below already answers its typos with JSON.
        if full_path.startswith("api/"):
            raise _not_found("endpoint", f"/v2/{full_path}")
        if shell.is_file():
            return FileResponse(shell)
        return HTMLResponse(V2_FALLBACK)

    # An unmatched /api/v2 path is JSON, never the SPA shell (contract §2.2).
    @app.api_route("/api/v2/{rest:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def v2_missing(rest: str) -> dict[str, Any]:
        raise _not_found("endpoint", f"/api/v2/{rest}")
