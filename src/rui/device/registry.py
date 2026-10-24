"""Adapter lookup: a name, and the function that builds it.

Adding a speaker is one new module plus one line in `_BUILDERS` here. The table is the
*only* place a vendor name appears; nothing above this file validates, branches on, or
documents a vendor name, so a device that is not in this build can still be configured -
it fails with a readable error instead of a validation failure.

A missing adapter must not take the control plane down: the console has to come up, list
the device and say what is wrong with it. So building returns an AdapterSet (the adapters
that exist plus one error string per device that does not) instead of raising out of
application startup.
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config.schema import Config, DeviceConfig
from .contract import DeviceAdapter

log = logging.getLogger(__name__)

Builder = Callable[[DeviceConfig, Config], DeviceAdapter]

# A target is either a lazy "module:function" string (the built-ins) or a builder
# callable. Downstream plugins (device/loader.py) register the callable directly, so a
# plugin module is imported once at startup rather than once per device.
Target = str | Builder

# name -> target. A function, not a class, because adapters do not share a
# constructor: one needs the capture layout, another needs nothing but its id.
_BUILDERS: dict[str, Target] = {
    "xiaoai": "rui.device.xiaoai:build",
    "mock": "rui.device.mock:build",
}


class AdapterUnavailable(RuntimeError):
    """The configured adapter exists in config but not in this build."""


@dataclass
class AdapterSet:
    adapters: dict[str, DeviceAdapter] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.adapters)


def register(name: str, target: Target) -> None:
    """Let a downstream build add a device without editing this file's table.

    ``target`` is a ``module:function`` string or the builder itself. Passing the
    callable is what the env/entry-point loader does; strings keep the built-ins lazy.
    """
    _BUILDERS[name] = target


def available() -> list[str]:
    return sorted(_BUILDERS)


def _builder(name: str) -> Builder:
    target = _BUILDERS.get(name)
    if target is None:
        raise AdapterUnavailable(
            f"unknown adapter: {name!r} (this build has: {', '.join(available())})"
        )
    if callable(target):
        return target
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attribute)
    except (ImportError, AttributeError) as exc:
        raise AdapterUnavailable(f"adapter {name!r} is registered but not importable: {exc}") from exc


def build_adapters(cfg: Config) -> AdapterSet:
    out = AdapterSet()
    for dev in cfg.devices:
        if not dev.enabled:
            continue
        try:
            out.adapters[dev.id] = _builder(dev.adapter)(dev, cfg)
        except AdapterUnavailable as exc:
            out.errors[dev.id] = str(exc)
        except Exception as exc:  # noqa: BLE001 - one broken device must not stop the rest
            log.warning("adapter %r failed to build: %s", dev.adapter, exc)
            out.errors[dev.id] = f"{type(exc).__name__}: {exc}"
    return out
