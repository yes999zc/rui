"""Pure device-management rules shared by the console routes and the wizard.

P3.4 adds enable/disable and per-device ports. The control plane runs ONE inbound
audio plane, so two enabled inbound devices that resolve to the same listen port can
never both work: the second bind would fail. The rule lives here (no FastAPI import)
so ``app.py`` and ``setup_api.py`` reject the write before anything reaches disk or the
audio plane.

A null ``listen_port`` means "adapter default", resolved from the plug manifest's
runtime block (then the loader's cached manifest default). There is no core fallback
port since 0.8.0: a device whose config and manifest both omit it has no resolvable
port, and the status row reports that instead of inventing 4412. Two devices are only
in conflict when both resolve to a known, equal port; outbound devices do not bind a
listener and are ignored.
"""
from __future__ import annotations

from pathlib import Path

from ...config.schema import DeviceConfig
from ...plugins import PlugCatalog

__all__ = [
    "DEFAULT_TOKEN_ENV",
    "conflict_detail",
    "effective_listen_port",
    "effective_token_env",
    "is_inbound",
    "port_conflict",
]

# The transport-token secret name used when neither the config nor the plug names one.
DEFAULT_TOKEN_ENV = "RUI_DEVICE_TOKEN"


def _runtime_default(adapter: str, key: str) -> object | None:
    """Manifest runtime defaults cached by the adapter loader at startup.

    device/loader.py records these when it gates a co-located/user manifest and
    build_adapters applies them when the config leaves a value unset. Reading the same
    cache keeps the management API truthful even when the plug catalog cannot see a
    co-located manifest (no duplicate copy under data_dir/plugs).
    """
    try:
        from ...device.registry import RUNTIME_DEFAULTS
    except Exception:  # pragma: no cover - import cycle guard
        return None
    return RUNTIME_DEFAULTS.get(str(adapter or ""), {}).get(key)


def effective_listen_port(device: DeviceConfig,
                          resolved: object | None = None) -> int | None:
    """The port this device actually asks the plane to bind, or None when unknown.

    devices[].listen_port wins; otherwise the resolved plug manifest's
    runtime.listen_port is the default the adapter builder applies (dot2: 8870,
    the config keeps None on purpose); otherwise the loader's cached manifest
    default. 0.8.0 has no core fallback: None means neither the config nor a
    manifest states a port, which is a configuration error the caller reports
    instead of guessing.
    """
    if device.listen_port:
        return int(device.listen_port)
    runtime = getattr(getattr(resolved, "device", None), "runtime", None)
    port = getattr(runtime, "listen_port", None) if runtime is not None else None
    if port:
        return int(port)
    cached = _runtime_default(device.adapter, "listen_port")
    return int(cached) if cached else None


def effective_token_env(device: DeviceConfig,
                        resolved: object | None = None) -> str:
    """The secret name the device's transport token actually lives under.

    Mirrors device/registry.py:_apply_manifest_runtime: an unset/default
    devices[].token_env is replaced by the plug manifest's runtime.token_env
    (dot2: DOT2_EM_TOKEN); an explicit operator value wins. Display/diagnostic only:
    the token value is still never echoed.
    """
    name = str(getattr(device, "token_env", "") or "").strip()
    runtime = getattr(getattr(resolved, "device", None), "runtime", None)
    if runtime is None:
        # Callers that already hold a DeviceManifest (the setup wizard) pass it directly.
        runtime = getattr(resolved, "runtime", None)
    manifest = str(getattr(runtime, "token_env", "") or "").strip()
    if not manifest:
        manifest = str(_runtime_default(device.adapter, "token_env") or "").strip()
    if (not name or name == DEFAULT_TOKEN_ENV) and manifest:
        return manifest
    return name or DEFAULT_TOKEN_ENV


def is_inbound(device: DeviceConfig, catalog: PlugCatalog | None = None) -> bool:
    """True when the device expects a listener (an unresolvable device is treated as inbound).

    A legacy/unknown plug must not slip past the conflict check: until a manifest says
    ``outbound``, assuming a listener is the conservative choice.
    """
    if catalog is None:
        return True
    params = device.params if isinstance(device.params, dict) else {}
    resolved = catalog.resolve(
        plug_id=str(params.get("plug_id") or "").strip() or None,
        adapter=device.adapter,
    )
    if resolved is None:
        return True
    return str(resolved.device.transport) == "inbound"


def port_conflict(
    devices: list[DeviceConfig], data_dir: str | Path | None = None,
) -> dict | None:
    """The first conflict between enabled inbound devices, as response details.

    ``devices`` is the *resulting* list (the candidate edit already merged in), so the
    caller can validate a PUT/upsert/enable without a second code path. Disabled
    devices never conflict; null and manifest-default ports compare equal. A device
    with no resolvable port is skipped: it cannot open a listener, and its status row
    already says the port is missing.
    """
    catalog = PlugCatalog(data_dir)
    seen: dict[int, DeviceConfig] = {}
    for device in devices:
        if not device.enabled or not is_inbound(device, catalog):
            continue
        params = device.params if isinstance(device.params, dict) else {}
        resolved = catalog.resolve(
            plug_id=str(params.get("plug_id") or "").strip() or None,
            adapter=device.adapter)
        port = effective_listen_port(device, resolved)
        if port is None:
            continue
        first = seen.get(port)
        if first is None:
            seen[port] = device
            continue
        return {
            "port": port,
            "listen_port": port,
            "device_ids": [first.id, device.id],
            "devices": [
                {
                    "id": first.id,
                    "host": first.host,
                    "listen_port": first.listen_port,
                    "effective_port": port,
                },
                {
                    "id": device.id,
                    "host": device.host,
                    "listen_port": device.listen_port,
                    "effective_port": port,
                },
            ],
            "used_default": first.listen_port is None or device.listen_port is None,
        }
    return None


def conflict_detail(conflict: dict) -> dict:
    """The object every writer puts in a 422 ``detail`` field."""
    ids = ", ".join(str(item) for item in conflict.get("device_ids") or [])
    return {
        "code": "port_conflict",
        "message": (
            f"enabled devices share inbound port {conflict.get('port')}: {ids}. "
            "Give one of them a different listen_port or disable it."
        ),
        "details": conflict,
    }
