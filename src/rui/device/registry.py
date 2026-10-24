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

#: Names shipped in the image. Neither the env hook nor an entry point may replace
#: one (Socket & Plug v1 §4.4): an operator who needs different behaviour registers a
#: new name and selects it with ``devices[].adapter``.
BUILTIN_TARGETS: dict[str, Target] = dict(_BUILDERS)
BUILTIN_NAMES: frozenset[str] = frozenset(BUILTIN_TARGETS)

#: Manifest ``runtime`` defaults per downstream adapter name. The adapter loader
#: records these when it gates a co-located/user manifest (it is the only component
#: that knows which manifest backed the module); ``build_adapters`` applies them when
#: the config does not already set a value. Kept in this module so the loader can set
#: it without importing the plugin catalog during startup.
RUNTIME_DEFAULTS: dict[str, dict[str, object]] = {}


def set_runtime_defaults(
    name: str, *, listen_port: int | None = None, token_env: str | None = None
) -> None:
    if listen_port is None and token_env is None:
        return
    values = RUNTIME_DEFAULTS.setdefault(name, {})
    if listen_port is not None:
        values["listen_port"] = int(listen_port)
    if token_env:
        values["token_env"] = token_env


def clear_runtime_defaults() -> None:
    """Test/embedding helper: forget cached manifest defaults."""
    RUNTIME_DEFAULTS.clear()


class AdapterUnavailable(RuntimeError):
    """The configured adapter exists in config but not in this build."""


class BuiltinAdapterName(ValueError):
    """A downstream channel tried to replace an adapter the image ships."""


@dataclass
class AdapterSet:
    adapters: dict[str, DeviceAdapter] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.adapters)


def register(name: str, target: Target, *, allow_builtin: bool = False) -> None:
    """Let a downstream build add a device without editing this file's table.

    ``target`` is a ``module:function`` string or the builder itself. Passing the
    callable is what the env/entry-point loader does; strings keep the built-ins lazy.
    Replacing a name this image ships raises :class:`BuiltinAdapterName` unless the
    caller is core code (``allow_builtin=True``); downstream channels register a new
    name instead so ``/health`` and the config cannot silently change meaning.
    """
    if (
        not allow_builtin
        and name in BUILTIN_NAMES
        and _BUILDERS.get(name) is BUILTIN_TARGETS[name]
    ):
        raise BuiltinAdapterName(
            f"adapter name {name!r} is built into this image and cannot be replaced; "
            "register a new name and set devices[].adapter to it"
        )
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


def _resolve_plug(dev: DeviceConfig, cfg: Config):
    """Best-effort manifest lookup for one configured device (never raises).

    ``plugins.resolve`` is imported lazily: it reads the plug catalog, which imports
    the plugin models, which import this package's ``contract`` module. Importing at
    module load time would create a cycle; per build call is cheap (once per device).
    """
    params = dev.params if isinstance(dev.params, dict) else {}
    plug_id = str(params.get("plug_id") or "").strip()
    if not plug_id and dev.adapter in BUILTIN_NAMES:
        # The image's own adapters need no catalog scan on every build; their manifest
        # runtime defaults are the config defaults (xiaoai: 4412 / RUI_DEVICE_TOKEN).
        return None
    try:
        from ..plugins.resolve import resolve_device_plug

        return resolve_device_plug(dev, cfg.system.data_dir)
    except Exception as exc:  # noqa: BLE001 - a broken plug must not stop a device
        log.debug("adapter %r: plug manifest not resolvable: %s", dev.adapter, exc)
        return None


def _apply_manifest_runtime(dev: DeviceConfig, resolved) -> DeviceConfig:
    """Fill manifest ``runtime.listen_port``/``token_env`` when config leaves them unset.

    ``DeviceConfig.listen_port = None`` explicitly means "adapter default" and
    ``token_env`` defaults to ``RUI_DEVICE_TOKEN``; the manifest can therefore supply
    both without overwriting an explicit operator value (Socket & Plug v1 §4.1/P1-7).
    The adapter sees the resolved copy, never a mutated config.
    """
    runtime = getattr(getattr(resolved, "device", None), "runtime", None)
    cached = RUNTIME_DEFAULTS.get(dev.adapter, {})
    listen_port = (
        int(runtime.listen_port) if runtime is not None and runtime.listen_port
        else cached.get("listen_port")
    )
    token_env = (
        str(runtime.token_env) if runtime is not None and runtime.token_env
        else str(cached.get("token_env") or "")
    )
    if listen_port is None and not token_env:
        return dev
    updates: dict[str, object] = {}
    if dev.listen_port is None and listen_port is not None:
        updates["listen_port"] = int(listen_port)
    if (
        dev.token_env == "RUI_DEVICE_TOKEN"
        and token_env
        and token_env != "RUI_DEVICE_TOKEN"
    ):
        updates["token_env"] = token_env
    return dev.model_copy(update=updates) if updates else dev


def build_adapters(cfg: Config) -> AdapterSet:
    out = AdapterSet()
    for dev in cfg.devices:
        if not dev.enabled:
            continue
        resolved = _resolve_plug(dev, cfg)
        runtime = getattr(getattr(resolved, "device", None), "runtime", None)
        if runtime is not None and str(getattr(runtime, "mode", "")) == "sidecar":
            # 0.3.x has no sidecar runner: say so once, instead of reporting the
            # adapter as "unknown" or pretending the manifest is runnable.
            out.errors[dev.id] = (
                "runtime.mode 'sidecar' is not implemented in 0.3.x; run the plug "
                "in-process with trust: trusted (or wait for the 0.4 sidecar runner)"
            )
            continue
        effective = _apply_manifest_runtime(dev, resolved)
        try:
            out.adapters[dev.id] = _builder(effective.adapter)(effective, cfg)
        except AdapterUnavailable as exc:
            out.errors[dev.id] = str(exc)
        except Exception as exc:  # noqa: BLE001 - one broken device must not stop the rest
            log.warning("adapter %r failed to build: %s", effective.adapter, exc)
            out.errors[dev.id] = f"{type(exc).__name__}: {exc}"
    return out
