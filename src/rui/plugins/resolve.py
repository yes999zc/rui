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

from .builtin import builtin_plugin_errors, builtin_plugins
from .loader import discover_plugins
from .models import DeviceManifest, Plugin

__all__ = [
    "PLUG_SOURCE_PRIORITY",
    "PlugCatalog",
    "PlugEntry",
    "ResolvedPlug",
    "adapter_entries",
    "adapter_plugins",
    "clear_adapter_plugins",
    "register_adapter_plugin",
    "resolve_device_plug",
]

#: Same-id selection rule, highest priority first (0.8.0 task-25): the manifest that
#: backed the adapter this process actually loaded wins, then the image/builtin catalog,
#: then an optional user plug under ``<data_dir>/plugs``. ``RUI_ADAPTER_PLUGINS`` /
#: ``PYTHONPATH`` can load code from a mounted directory the catalog does not scan; if
#: that directory also ships a manifest, reporting the image's older same-id manifest
#: would make ``/api/v2/plugins`` describe a plug that is not running.
PLUG_SOURCE_PRIORITY: dict[str, int] = {"adapter": 0, "builtin": 1, "user": 2}


@dataclass(frozen=True)
class PlugEntry:
    """One loaded plug, where it came from, and the manifest directory."""

    plugin: Plugin
    source: str
    origin: str = ""


#: Manifests the adapter loader found and gated an in-process adapter against
#: (``RUI_ADAPTER_PLUGINS`` / entry points, including a co-located ``plugin.yaml``).
#: ``PlugCatalog`` only scans the image and ``<data_dir>/plugs``, so without this a
#: co-located manifest backs a working adapter while the audio resolver reports
#: ``plug_unresolved`` - the 0.8.0 cold-start split between "the plane can drive the
#: device" and "its hardware facts are known".
#:
#: Keyed by plug id; first registration wins so a later duplicate cannot replace the
#: manifest the adapter was gated against. Reset only by tests.
_ADAPTER_PLUGINS: dict[str, PlugEntry] = {}


def register_adapter_plugin(
    plugin: Plugin,
    *,
    source: str = "adapter",
    origin: str = "",
) -> None:
    """Remember a manifest that the adapter loader accepted (idempotent).

    ``source`` is ``adapter`` for an out-of-catalog (PYTHONPATH / mounted) manifest,
    ``builtin`` when the loaded adapter lives inside an image plug directory, and
    ``user`` for ``<data_dir>/plugs``. ``origin`` is the manifest directory, exposed so
    the API can show where the version it reports actually came from.
    """
    plugin_id = str(plugin.id)
    if plugin_id in _ADAPTER_PLUGINS:
        return
    _ADAPTER_PLUGINS[plugin_id] = PlugEntry(plugin, source, origin)


def adapter_entries() -> tuple[PlugEntry, ...]:
    return tuple(_ADAPTER_PLUGINS.values())


def adapter_plugins() -> tuple[Plugin, ...]:
    """Backwards-compatible view: just the plugins (no provenance)."""
    return tuple(entry.plugin for entry in _ADAPTER_PLUGINS.values())


def clear_adapter_plugins() -> None:
    """Test hook: forget co-located manifests (the registry has no such cache)."""
    _ADAPTER_PLUGINS.clear()


def _same_manifest_file(left: PlugEntry, right: PlugEntry) -> bool:
    """Whether two candidates are the same manifest directory (source label aside)."""
    if not left.origin or not right.origin:
        return False
    if str(left.plugin.version) != str(right.plugin.version):
        return False
    try:
        return Path(left.origin).resolve() == Path(right.origin).resolve()
    except (OSError, RuntimeError, ValueError):
        return left.origin == right.origin


def _dedupe_errors(messages: list[str]) -> list[str]:
    """Stable, duplicate-free load errors (the image and user scan can name one file)."""
    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        if message and message not in seen:
            seen.add(message)
            result.append(message)
    return result


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
    """Image, optional user, and adapter-loaded plugs, loaded once per request.

    Same-id selection is explicit (0.8.0 task-25), highest priority first:

    1. ``adapter`` - the manifest the adapter loader actually gated this process's
       in-process adapter against. It can live outside the scanned roots (a mounted
       ``PYTHONPATH`` directory); the code that runs is that plug, so its
       version/source must be what ``/api/v2/plugins`` reports.
    2. ``builtin`` - the image plug directory (``/opt/rui-plugins``, or ``plugs/`` in a
       checkout / ``RUI_PLUGINS_DIR`` in tests and operator installs).
    3. ``user`` - an optional plug under ``<data_dir>/plugs``.

    A candidate that loses a same-id collision is kept in :attr:`shadowed` with its
    source/version/origin, so the API can show "this older builtin plug was replaced by
    the adapter-loaded one" instead of hiding the decision. ``discover_plugins`` and the
    builtin scan already keep a broken manifest as data, never an exception.
    """

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        plugin_dirs: list[Path] | tuple[Path, ...] | None = None,
    ) -> None:
        candidates: list[PlugEntry] = []
        builtin_errors = builtin_plugin_errors(plugin_dirs)
        candidates.extend(
            PlugEntry(plugin, "builtin", str(plugin.source_dir or ""))
            for plugin in builtin_plugins(plugin_dirs)
        )
        scan = discover_plugins(data_dir)
        candidates.extend(
            PlugEntry(plugin, "user", str(plugin.source_dir or ""))
            for plugin in scan.plugins
        )
        # Co-located manifests the adapter loader already gated: the plane can load
        # the adapter, so its hardware facts must resolve from the first read and its
        # version must win over an older same-id image manifest.
        candidates.extend(adapter_entries())

        entries: dict[str, PlugEntry] = {}
        shadowed: dict[str, list[PlugEntry]] = {}
        for candidate in candidates:
            plugin_id = str(candidate.plugin.id)
            current = entries.get(plugin_id)
            if current is None:
                entries[plugin_id] = candidate
                continue
            if _same_manifest_file(current, candidate):
                # The adapter entry re-reports the same file the user scan already
                # loaded (or vice versa): one manifest, not a shadowed duplicate.
                continue
            if (PLUG_SOURCE_PRIORITY.get(candidate.source, 9)
                    < PLUG_SOURCE_PRIORITY.get(current.source, 9)):
                entries[plugin_id] = candidate
                losers = [
                    item for item in shadowed.get(plugin_id, [])
                    if not _same_manifest_file(item, candidate)
                ]
                losers.append(current)
                shadowed[plugin_id] = losers
            else:
                shadowed.setdefault(plugin_id, []).append(candidate)
        self.entries = entries
        self.shadowed = shadowed
        self.errors = _dedupe_errors([*builtin_errors, *scan.errors])

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
