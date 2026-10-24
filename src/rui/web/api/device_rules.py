"""Pure device-management rules shared by the console routes and the wizard.

P3.4 adds enable/disable and per-device ports. The control plane runs ONE inbound
audio plane, so two enabled inbound devices that resolve to the same listen port can
never both work: the second bind would fail. The rule lives here (no FastAPI import)
so ``app.py`` and ``setup_api.py`` reject the write before anything reaches disk or the
audio plane.

A null ``listen_port`` means "adapter default" (4412 for the LX06), so null and an
explicit 4412 are the same port for conflict purposes. Outbound devices do not bind a
listener and are ignored.
"""
from __future__ import annotations

from pathlib import Path

from ...config.schema import DeviceConfig
from ...plugins import PlugCatalog

__all__ = [
    "DEFAULT_LISTEN_PORT",
    "conflict_detail",
    "effective_listen_port",
    "is_inbound",
    "port_conflict",
]

# The inbound WS port an LX06 client dials when devices[].listen_port is unset.
# Pinned by tests/test_setup.py to the adapter's own constant.
DEFAULT_LISTEN_PORT = 4412


def effective_listen_port(device: DeviceConfig) -> int:
    """The port this device actually asks the plane to bind."""
    return int(device.listen_port or DEFAULT_LISTEN_PORT)


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
    devices never conflict; null and default ports compare equal.
    """
    catalog = PlugCatalog(data_dir)
    seen: dict[int, DeviceConfig] = {}
    for device in devices:
        if not device.enabled or not is_inbound(device, catalog):
            continue
        port = effective_listen_port(device)
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
