"""Socket & Plug v0: manifest models and a loader that never executes code.

Import surface for the upcoming wizard/API::

    from rui.plugins import load_plugin

    plugin = load_plugin(Path("/path/to/rui-plugin-example"))
    for device in plugin.devices:
        device.capabilities          # tuple of rui.device.Capability
        device.runtime.mode          # resolved in-process | sidecar (or None)
"""
from __future__ import annotations

from .builtin import builtin_plugin, builtin_plugin_errors, builtin_plugins
from .errors import PluginError
from .loader import PluginScan, discover_plugins, load_plugin, running_version
from .models import (
    DEVICE_SCHEMA,
    DEVICE_SCHEMA_V1,
    PLUGIN_SCHEMA,
    Acceptance,
    Compatibility,
    DeviceArtifact,
    DeviceClass,
    DeviceManifest,
    DeviceSide,
    ParamSpec,
    ParamType,
    Plugin,
    PluginManifest,
    ProtocolSpec,
    RuntimeMode,
    RuntimeSpec,
    TrustLevel,
    WakeSource,
    WakeSpec,
)
from .resolve import PlugCatalog, PlugEntry, ResolvedPlug, resolve_device_plug
from .semver import (
    Comparator,
    Version,
    VersionError,
    VersionRange,
    parse_range,
    parse_version,
    satisfies,
)

__all__ = [
    "DEVICE_SCHEMA",
    "DEVICE_SCHEMA_V1",
    "PLUGIN_SCHEMA",
    "Acceptance",
    "Comparator",
    "Compatibility",
    "DeviceArtifact",
    "DeviceClass",
    "DeviceManifest",
    "DeviceSide",
    "ParamSpec",
    "ParamType",
    "PlugCatalog",
    "PlugEntry",
    "Plugin",
    "PluginError",
    "PluginManifest",
    "PluginScan",
    "ProtocolSpec",
    "ResolvedPlug",
    "RuntimeMode",
    "RuntimeSpec",
    "TrustLevel",
    "Version",
    "VersionError",
    "VersionRange",
    "WakeSource",
    "WakeSpec",
    "builtin_plugin",
    "builtin_plugin_errors",
    "builtin_plugins",
    "discover_plugins",
    "load_plugin",
    "parse_range",
    "parse_version",
    "resolve_device_plug",
    "running_version",
    "satisfies",
]
