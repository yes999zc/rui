"""Socket & Plug v0: manifest models and a loader that never executes code.

Import surface for the upcoming wizard/API::

    from house_ear.plugins import load_plugin

    plugin = load_plugin(Path("/path/to/rui-plugin-example"))
    for device in plugin.devices:
        device.capabilities          # tuple of house_ear.device.Capability
        device.runtime.mode          # resolved in-process | sidecar (or None)
"""
from __future__ import annotations

from .errors import PluginError
from .loader import load_plugin, running_version
from .models import (
    DEVICE_SCHEMA,
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
)
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
    "Plugin",
    "PluginError",
    "PluginManifest",
    "ProtocolSpec",
    "RuntimeMode",
    "RuntimeSpec",
    "TrustLevel",
    "Version",
    "VersionError",
    "VersionRange",
    "load_plugin",
    "parse_range",
    "parse_version",
    "running_version",
    "satisfies",
]
