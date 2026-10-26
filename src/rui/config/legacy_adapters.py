"""One-minor migration table for adapter spellings that predate the plug split.

0.7.6 removed the in-tree LX06 plug. A config written before that may still say
``adapter: xiaoai``; it is rewritten to the plug manifest's device id on load and a
deprecation warning is logged. **This module is deleted in 0.8.0** together with the
rewrite in :func:`rui.config.store.migrate`.
"""
from __future__ import annotations

#: Old spelling -> the plug manifest's canonical device id.
_LEGACY_ADAPTERS: dict[str, str] = {"xiaoai": "lx06"}

#: Default for a config that never stated `devices[].adapter` (the external plug id).
DEFAULT_ADAPTER = "lx06"

#: Message shown when a legacy spelling is rewritten (kept here so the grep gate for
#: core code only has to exclude this one file).
LEGACY_ADAPTER_HINT = (
    "adapter {old!r} is deprecated; migrated to {new!r}. "
    "The LX06 adapter now ships as rui-xiaoai-bridge (mount it under /opt/rui-plugins)."
)


def canonical(name: str) -> str:
    """Return the canonical spelling for ``name`` (unchanged when not legacy)."""
    return _LEGACY_ADAPTERS.get(name.strip(), name.strip())


def is_legacy(name: str) -> bool:
    return name.strip() in _LEGACY_ADAPTERS


def unknown_adapter_hint(name: str) -> str:
    """Actionable hint for an adapter that is not registered in this build."""
    if is_legacy(name):
        return LEGACY_ADAPTER_HINT.format(old=name, new=canonical(name))
    return (
        "the LX06 adapter is not built in since 0.7.6; install/mount "
        "rui-xiaoai-bridge under /opt/rui-plugins"
    )
