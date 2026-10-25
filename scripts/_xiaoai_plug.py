"""Load the LX06 plug for the measurement scripts (0.6 M2 moved it out of the core).

``src/rui/device/xiaoai/`` no longer exists: the adapter is an image plug declared by
``plugs/xiaoai/devices/lx06.yaml`` and imported through its ``runtime.entrypoint``
(``xiaoai_plug.adapter:build``). The measurement scripts still need the concrete
adapter class (for an ``isinstance`` sanity check) and the wire-framing helper, so
this module resolves the plug the same way the runtime does and imports its package
after putting the plug directory on ``sys.path`` - the source-checkout equivalent of
the image's ``PYTHONPATH=/opt/rui-plugins/xiaoai``.

A script must not import ``rui.device.xiaoai`` again: that path is a deleted core
module, and the resulting ``ModuleNotFoundError`` is what this helper fixes.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

ADAPTER_ID = "lx06"


class XiaoaiPlug(NamedTuple):
    """What the measurement scripts need from the plug package."""

    adapter: type
    protocol: ModuleType


def load_xiaoai_plug() -> XiaoaiPlug:
    """Resolve the LX06 manifest, import its entrypoint module and ``.protocol``.

    The in-repo ``plugs/`` directory is tried first (dev checkout); the normal
    catalog (``RUI_PLUGINS_DIR`` / ``/opt/rui-plugins`` / data dir) is the fallback,
    so the same script also works in an image that ships the plug.
    """
    from rui.plugins.resolve import PlugCatalog

    repo_plugs = Path(__file__).resolve().parents[1] / "plugs"
    resolved = PlugCatalog(plugin_dirs=[repo_plugs]).resolve(adapter=ADAPTER_ID)
    if resolved is None:
        resolved = PlugCatalog().resolve(adapter=ADAPTER_ID)
    if resolved is None:
        raise RuntimeError(
            f"no plug manifest declares adapter {ADAPTER_ID!r}; run from the rui checkout "
            "or set RUI_PLUGINS_DIR to the directory that holds plugs/xiaoai"
        )
    runtime = getattr(resolved.device, "runtime", None)
    entrypoint = str(getattr(runtime, "entrypoint", "") or "")
    module_name, separator, _attribute = entrypoint.partition(":")
    if not separator or not module_name:
        raise RuntimeError(f"plug {resolved.plugin.id!r} has no in-process entrypoint")
    source = Path(resolved.plugin.source_dir)
    for candidate in (source, source.parent):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    module = importlib.import_module(module_name)
    adapter = getattr(module, "XiaoaiAdapter", None)
    if not isinstance(adapter, type):
        raise RuntimeError(f"{entrypoint!r}: {module_name} does not export XiaoaiAdapter")
    package = module_name.rsplit(".", 1)[0] if "." in module_name else ""
    protocol_name = f"{package}.protocol" if package else "protocol"
    return XiaoaiPlug(adapter, importlib.import_module(protocol_name))
