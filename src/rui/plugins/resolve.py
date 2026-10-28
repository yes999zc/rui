"""Resolve a configured device to the plug manifest that describes it (P2.4).

``devices[].params.plug_id`` is the explicit answer; a device written before that key
existed is resolved through its adapter (the adapter name is the plug manifest's device
id). 0.7.6 removed the in-tree alias; a legacy spelling is rewritten in config.store.
config keeps resolving to the same plug. The catalog is read-only and uses the same
loader/builtin registry as the wizard, so an unknown or broken plug simply resolves to
``None`` instead of raising.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .builtin import builtin_plugins
from .loader import discover_plugins
from .models import DeviceManifest, Plugin

__all__ = [
    "PlugCatalog",
    "PlugEntry",
    "ResolvedPlug",
    "adapter_plugins",
    "clear_adapter_plugins",
    "register_adapter_plugin",
    "resolve_device_plug",
]

#: Manifests the adapter loader found next to an in-process adapter module
#: (``RUI_ADAPTER_PLUGINS`` / entry points). ``PlugCatalog`` only scans the image and
#: ``<data_dir>/plugs``, so without this a co-located manifest backs a working adapter
#: while the audio resolver reports ``plug_unresolved`` - the 0.8.0 cold-start split
#: between "the plane can drive the device" and "its hardware facts are known".
#:
#: Keyed by plug id; first registration wins so a later duplicate cannot replace the
#: manifest the adapter was gated against. Reset only by tests.
_ADAPTER_PLUGINS: dict[str, Plugin] = {}


def register_adapter_plugin(plugin: Plugin) -> None:
    """Remember a manifest that the adapter loader accepted (idempotent)."""
    _ADAPTER_PLUGINS.setdefault(str(plugin.id), plugin)


def adapter_plugins() -> tuple[Plugin, ...]:
    return tuple(_ADAPTER_PLUGINS.values())


def clear_adapter_plugins() -> None:
    """Test hook: forget co-located manifests (the registry has no such cache)."""
    _ADAPTER_PLUGINS.clear()


@dataclass(frozen=True)
class PlugEntry:
    """One loaded plug and where it came from (``builtin`` or ``user``)."""

    plugin: Plugin
    source: str


@dataclass(frozen=True)
class ResolvedPlug:
    """The plug + device manifest a configured device resolves to."""

    plugin: Plugin
    device: DeviceManifest
    source: str
    plug_id: str
    matched_by: str          # "plug_id" | "adapter"

    @property
    def capabilities(self) -> list[str]:
        return sorted(str(capability) for capability in self.device.capabilities)


class PlugCatalog:
    """Image plus optional user plugs, loaded once per request.

    Image (builtin) ids win over a user plug with the same id (the image's plug is
    what ships); ``discover_plugins`` already keeps a broken plug as data. Since 0.6
    the image plugs come from the plug directory (plugs/ in a checkout,
    /opt/rui-plugins in the image), not from an in-code table.
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        plugin_dirs: list[Path] | tuple[Path, ...] | None = None,
    ) -> None:
        entries: dict[str, PlugEntry] = {
            plugin.id: PlugEntry(plugin, "builtin")
            for plugin in builtin_plugins(plugin_dirs)
        }
        scan = discover_plugins(data_dir)
        for plugin in scan.plugins:
            entries.setdefault(plugin.id, PlugEntry(plugin, "user"))
        # Co-located manifests the adapter loader already gated: the plane can load
        # the adapter, so its hardware facts must resolve from the first read.
        for plugin in adapter_plugins():
            entries.setdefault(plugin.id, PlugEntry(plugin, "adapter"))
        self.entries = entries
        self.errors = list(scan.errors)

    def get(self, plug_id: str) -> PlugEntry | None:
        return self.entries.get((plug_id or "").strip())

    def resolve(self, *, plug_id: str | None = None,
                adapter: str | None = None) -> ResolvedPlug | None:
        """Explicit ``plug_id`` first, then adapter -> device-manifest id.

        Returns ``None`` when neither resolves; an unknown explicit id is not an
        error here because read endpoints must keep rendering a legacy config.
        """
        explicit = (plug_id or "").strip()
        wanted_adapter = (adapter or "").strip()
        if explicit:
            entry = self.get(explicit)
            if entry is None or not entry.plugin.devices:
                return None
            device = next(
                (item for item in entry.plugin.devices if item.id == wanted_adapter),
                entry.plugin.devices[0],
            )
            return ResolvedPlug(entry.plugin, device, entry.source, entry.plugin.id, "plug_id")
        if not wanted_adapter:
            return None
        for wanted in (wanted_adapter,):
            if not wanted:
                continue
            for entry in self.entries.values():
                for device in entry.plugin.devices:
                    if device.id == wanted:
                        return ResolvedPlug(
                            entry.plugin, device, entry.source, entry.plugin.id, "adapter")
        return None


def resolve_device_plug(device: object, data_dir: str | Path | None = None) -> ResolvedPlug | None:
    """Resolve a ``DeviceConfig``-shaped object (``adapter`` + ``params``), or ``None``."""
    params = getattr(device, "params", None)
    plug_id = str(params.get("plug_id") or "") if isinstance(params, dict) else ""
    return PlugCatalog(data_dir).resolve(
        plug_id=plug_id, adapter=str(getattr(device, "adapter", "") or ""))
