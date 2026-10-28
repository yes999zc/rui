"""Adapter lookup: a name, and the function that builds it.

Adding a speaker is one plug directory, not one line here. Since 0.6 the adapter
table contains only the vendor-neutral development adapter (mock); every device
adapter comes from a manifest: the image plugs under /opt/rui-plugins are registered
from their own runtime.entrypoint (docs/roadmap-0.6.md M2), and a downstream image
registers through RUI_ADAPTER_PLUGINS / the rui.adapters entry-point group
(device/loader.py). Nothing above this file validates, branches on, or documents a
vendor name, so a device that is not in this build can still be configured - it
fails with a readable error instead of a validation failure.

A missing adapter must not take the control plane down: the console has to come up,
list the device and say what is wrong with it. So building returns an AdapterSet (the
adapters that exist plus one error string per device that does not) instead of raising
out of application startup.
"""
from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config.schema import Config, DeviceConfig
from .contract import DeviceAdapter

log = logging.getLogger(__name__)

Builder = Callable[[DeviceConfig, Config], DeviceAdapter]

# A target is either a lazy "module:function" string or a builder callable.
T = str | Builder

# name -> target. A function, not a class, because adapters do not share a
# constructor: one needs the capture layout, another needs nothing but its id.
_BUILDERS: dict[str, T] = {
    "mock": "rui.device.mock:build",
}

#: Names shipped in the image (mock plus every image plug registered from its
#: manifest). Neither the env hook nor an entry point may replace one (Socket & Plug
#: v1 §4.4): an operator who needs different behaviour registers a new name and
#: selects it with devices[].adapter.
BUILTIN_TARGETS: dict[str, T] = dict(_BUILDERS)
BUILTIN_NAMES: set[str] = set(BUILTIN_TARGETS)

#: Manifest runtime defaults per adapter name, recorded when a manifest-backed
#: adapter is registered (the loader/registry is the only component that knows which
#: manifest backed the module). build_adapters applies them when the config does not
#: already set a value.
RUNTIME_DEFAULTS: dict[str, dict[str, object]] = {}

_IMAGE_LOADING = False


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


def _ensure_image_adapters() -> None:
    """Register the adapters declared by the plugs the image ships.

    Generic on purpose: the plug manifest names its module, so the registry never
    mentions a device. Only image-wise plugs (builtin_plugins(), i.e. /opt/rui-plugins
    or the development plugs/ tree) are executed here; a user plug under
    <data_dir>/plugs still has to be named by the operator through
    RUI_ADAPTER_PLUGINS, because a directory a user can write is not an execution
    boundary.
    """
    global _IMAGE_LOADING
    if _IMAGE_LOADING:
        return
    _IMAGE_LOADING = True
    try:
        from ..plugins.builtin import builtin_plugins

        plugins = builtin_plugins()
    except Exception as exc:  # noqa: BLE001 - a broken catalog must not stop startup
        log.warning("image plugs: cannot read the catalog: %s", exc)
        _IMAGE_LOADING = False
        return
    try:
        for plugin in plugins:
            if str(getattr(plugin, "trust", "")) not in {"builtin", "trusted"}:
                continue
            for device in plugin.devices:
                runtime = getattr(device, "runtime", None)
                if runtime is None:
                    continue
                if str(getattr(runtime, "mode", "")) != "in-process":
                    continue
                entrypoint = str(getattr(runtime, "entrypoint", "") or "")
                name = str(getattr(device, "id", "") or "")
                if not entrypoint or not name or name in _BUILDERS:
                    continue
                try:
                    builder = _import_entrypoint(plugin.source_dir, entrypoint)
                except Exception as exc:  # noqa: BLE001 - one broken plug is data
                    log.warning(
                        "image plug %r: cannot import %s: %s", plugin.id, entrypoint, exc
                    )
                    continue
                _BUILDERS[name] = builder
                BUILTIN_TARGETS[name] = builder
                BUILTIN_NAMES.add(name)
                set_runtime_defaults(
                    name,
                    listen_port=getattr(runtime, "listen_port", None),
                    token_env=getattr(runtime, "token_env", "") or None,
                )
                log.info("image plug %r registered adapter %r", plugin.id, name)
    finally:
        _IMAGE_LOADING = False


def _import_entrypoint(source_dir, entrypoint: str) -> Builder:
    """Import a manifest runtime.entrypoint and return its callable.

    The plug directory is added to sys.path (like the documented
    PYTHONPATH=/opt/rui-plugins/... recipe) so a package next to plugin.yaml imports
    without an install step. It stays on sys.path: doctor also imports the module
    read-only to read its declared wire rate.
    """
    module_name, separator, attribute = entrypoint.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"invalid runtime.entrypoint {entrypoint!r}")
    path = str(getattr(source_dir, "resolve", lambda: source_dir)())
    # Two layouts ship in the wild: <plug>/<package>/adapter.py (an image plug
    # package) and <plug>/adapter.py where <plug> is itself the top-level package (the
    # Dot 2 downstream recipe, PYTHONPATH=/opt/rui-plugins). Make both importable.
    for candidate in (path, str(Path(path).parent)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    module = importlib.import_module(module_name)
    obj = getattr(module, attribute, None)
    if not callable(obj):
        raise TypeError(f"{entrypoint!r} is not callable")
    return obj


def register(name: str, target: T, *, allow_builtin: bool = False) -> None:
    """Let a downstream build add a device without editing this file's table.

    target is a module:function string or the builder itself. Passing the callable
    is what the env/entry-point loader does; strings keep the built-ins lazy.
    Replacing a name this image ships raises BuiltinAdapterName unless the caller is
    core code (allow_builtin=True); downstream channels register a new name instead
    so /health and the config cannot silently change meaning.
    """
    if (
        not allow_builtin
        and name in BUILTIN_NAMES
        and (name not in _BUILDERS or _BUILDERS.get(name) is BUILTIN_TARGETS.get(name))
    ):
        raise BuiltinAdapterName(
            f"adapter name {name!r} is built into this image and cannot be replaced; "
            "register a new name and set devices[].adapter to it"
        )
    _BUILDERS[name] = target


def available() -> list[str]:
    """Adapter names that can be selected in this build."""
    _ensure_image_adapters()
    return sorted(_BUILDERS)


def _unknown_adapter_hint() -> str:
    """Actionable, brand-free hint for an adapter that is not registered.

    The core does not know which model a name belongs to (and since 0.8.0 does not
    translate any old spelling), so the fix is always the plug: install/mount the
    device's manifest and select the id it declares.
    """
    return (
        "install the plug that owns this device under /opt/rui-plugins (or register "
        "it through RUI_ADAPTER_PLUGINS) and set devices[].adapter to the id its "
        "manifest declares"
    )


def _builder(name: str) -> Builder:
    _ensure_image_adapters()
    target = _BUILDERS.get(name)
    if target is None:
        raise AdapterUnavailable(
            f"unknown adapter: {name!r} (this build has: {', '.join(available())}); "
            f"{_unknown_adapter_hint()}"
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

    plugins.resolve is imported lazily: it reads the plug catalog, which imports the
    plugin models, which import this package's contract module. Importing at module
    load time would create a cycle; per build call is cheap (once per device).
    """
    params = dev.params if isinstance(dev.params, dict) else {}
    plug_id = str(params.get("plug_id") or "").strip()
    if not plug_id and dev.adapter in BUILTIN_NAMES:
        # Manifest-backed image adapters already recorded their runtime defaults when
        # they were registered, so no catalog scan is needed here.
        return None
    try:
        from ..plugins.resolve import resolve_device_plug

        return resolve_device_plug(dev, cfg.system.data_dir)
    except Exception as exc:  # noqa: BLE001 - a broken plug must not stop a device
        log.debug("adapter %r: plug manifest not resolvable: %s", dev.adapter, exc)
        return None


def _apply_manifest_runtime(dev: DeviceConfig, resolved) -> DeviceConfig:
    """Fill manifest runtime.listen_port/token_env when config leaves them unset.

    DeviceConfig.listen_port = None explicitly means "adapter default" and token_env
    defaults to RUI_DEVICE_TOKEN; the manifest can therefore supply both without
    overwriting an explicit operator value (Socket & Plug v1 §4.1/P1-7). The adapter
    sees the resolved copy, never a mutated config.
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
    _ensure_image_adapters()
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
