"""Adapter names kept as aliases for one minor (0.6 -> 0.7).

The in-core adapter table no longer names a device. A plug manifest declares the
adapter name as its device id (devices/<model>.yaml: id), and the image ships its
plugs under /opt/rui-plugins (docs/roadmap-0.6.md M2). Renaming the LX06 plug's
device id from the old in-core name to "lx06" would otherwise break every existing
config, so this module is the single, deliberately temporary place that maps the old
spelling. It disappears no earlier than 0.7.0.
"""
from __future__ import annotations

__all__ = [
    "ADAPTER_ALIASES",
    "LEGACY_DEFAULT_ADAPTER",
    "alias_names",
    "canonical_adapter",
]

#: Old adapter name -> the plug manifest's canonical device id.
ADAPTER_ALIASES: dict[str, str] = {
    "xiaoai": "lx06",
}

#: DeviceConfig.adapter default for a config written before the key existed.
#: 0.5 and earlier defaulted to the old in-core name, so an omitted key must keep
#: resolving to the same plug; the alias above turns it into "lx06".
LEGACY_DEFAULT_ADAPTER = "xiaoai"


def canonical_adapter(name: str) -> str:
    """The plug-facing adapter name for name (unchanged when it is not an alias)."""
    text = (name or "").strip()
    return ADAPTER_ALIASES.get(text, text)


def alias_names(canonical: str) -> list[str]:
    """Alias spellings that still resolve to canonical (for display/available())."""
    return sorted(alias for alias, target in ADAPTER_ALIASES.items() if target == canonical)
