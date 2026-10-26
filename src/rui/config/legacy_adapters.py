"""0.7.x-only compatibility values; **the whole module is deleted in 0.8.0**.

It holds two things, both legacy and neither a normal configuration source:

* ``_LEGACY_ADAPTERS`` / ``DEFAULT_ADAPTER`` - the pre-plug adapter spelling. A config
  that still says ``adapter: xiaoai`` is rewritten to the plug manifest's device id on
  load, with a deprecation warning (see :func:`rui.config.store.migrate`).
* ``DEFAULT_LISTEN_PORT`` - the port that ``rui-device/v0`` manifests used to inherit
  implicitly. New manifests must state ``runtime.listen_port`` themselves.

0.8.0 removes this module, the rewrite in :func:`rui.config.store.migrate`, and the
implicit port default together.
"""
from __future__ import annotations

#: Old spelling -> the plug manifest's canonical device id.
_LEGACY_ADAPTERS: dict[str, str] = {"xiaoai": "lx06"}

#: Default for a config that never stated `devices[].adapter` (the external plug id).
DEFAULT_ADAPTER = "lx06"

#: Compatibility default for device manifests written before the port was required
#: (rui-device/v0 omitted `runtime.listen_port`). 0.8.0 removes it with the rest of
#: this module.
DEFAULT_LISTEN_PORT = 4412

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
