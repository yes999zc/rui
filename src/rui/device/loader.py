"""Load downstream adapters named by the operator, without forking the core.

GitHub issue #7 Q1 promised an official way for an out-of-tree plug (the Echo Dot 2
bridge) to register an adapter on top of the official image, without the
``PYTHONPATH + sitecustomize`` hack. Two explicit sources exist:

* ``RUI_ADAPTER_PLUGINS`` - comma-separated ``module:build`` entries, optionally
  prefixed with ``name=``. ``module:register`` is a registration hook, a bare
  ``module`` tries ``build`` then ``register``.
* the ``rui.adapters`` Python entry-point group - the entry-point name becomes the
  adapter name and its value may be a builder or a zero-argument builder factory.

Security posture: only code named by the operator's environment or by an installed
distribution is imported, in-process. Nothing here reads ``devices[].params``,
console input, the config file or the network, and it never starts a subprocess. The
environment is root/container-operator controlled; treating it as trusted is the same
decision as choosing the image.

Both channels are **manifest-gated** (v1 §4.4): an entry is registered only when a
resolvable plug manifest with ``trust: builtin|trusted`` backs its adapter name. The
manifest may live next to the adapter module/package (recommended for downstream
images), under ``<data_dir>/plugs/*`` (pass ``data_dir=`` or set ``RUI_DATA_DIR``), or
be the in-image builtin manifest. ``third-party`` manifests are refused in-process
because 0.3.x has no sidecar runner. A builtin name can never be replaced.

Everything is fail-soft: one bad entry logs one bounded warning and startup
continues. A missing module must never take the control plane down.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from .. import env
from . import registry
from .registry import Builder

log = logging.getLogger(__name__)

ENV_VAR = "RUI_ADAPTER_PLUGINS"
ENTRY_POINT_GROUP = "rui.adapters"
#: Optional operator-owned data directory for ``<data_dir>/plugs/*`` manifests.
#: The web app does not pass its config value here (the hook runs before config is
#: available), so a downstream image either ships the manifest next to its adapter
#: package or sets this variable.
DATA_DIR_ENV = "RUI_DATA_DIR"

__all__ = [
    "AdapterPluginReport",
    "DATA_DIR_ENV",
    "ENTRY_POINT_GROUP",
    "ENV_VAR",
    "load_adapter_plugins",
    "load_plugins",
]

#: In-process code may only be backed by these manifest trust levels (§4.4).
IN_PROCESS_TRUST = frozenset({"builtin", "trusted"})
_FACTORY_ROLE_NAMES = frozenset({"factory", "make", "create_factory"})

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_REGISTER_NAMES = frozenset({"register", "register_adapter", "register_adapters"})
_BUILDER_ATTRS = ("build", "builder", "adapter", "factory")
_MAX_WARNING_CHARS = 400


@dataclass
class AdapterPluginReport:
    """What a load attempt did. Never an exception: startup keeps going.

    ``registered`` maps the adapter name (the value for ``devices[].adapter``) to a
    short source description. ``warnings`` holds one bounded line per failed entry;
    the service only exposes names/counts, never tracebacks, from ``/health``.
    """

    registered: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        """Alias kept for callers that think in errors rather than warnings."""
        return self.warnings

    @property
    def ok(self) -> bool:
        return not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {"registered": dict(self.registered), "warnings": list(self.warnings)}


@dataclass(frozen=True)
class _Spec:
    module: str
    attr: str | None
    name: str
    source: str


class _SpecError(ValueError):
    pass


def load_adapter_plugins(
    value: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> AdapterPluginReport:
    """Load every configured downstream adapter, env first, then entry points.

    ``value`` defaults to ``RUI_ADAPTER_PLUGINS`` (``RUI_*`` before the legacy
    ``HOUSE_EAR_*`` spelling). Passing ``""`` disables env entries but still scans
    ``rui.adapters``. ``data_dir`` (default: ``RUI_DATA_DIR``) is where a plug
    manifest may be installed under ``plugs/*``; a manifest shipped next to the
    adapter module is always considered. The function never raises for plugin
    problems.
    """
    report = AdapterPluginReport()
    registry.clear_runtime_defaults()
    root = Path(data_dir).expanduser() if data_dir is not None else _env_data_dir()
    raw = env.get(ENV_VAR) if value is None else value
    for text in _entries(raw):
        try:
            spec = _parse(text)
        except _SpecError as exc:
            _warn(report, f"{ENV_VAR}: {exc}")
            continue
        try:
            _load_env_spec(report, spec, data_dir=root)
        except Exception as exc:  # noqa: BLE001 - plugin code must not stop startup
            _warn(report, f"{spec.source}: unexpected error: {_brief(exc)}")
    try:
        _load_entry_points(report, data_dir=root)
    except Exception as exc:  # noqa: BLE001 - metadata/plugin code must not stop startup
        _warn(report, f"entry point group {ENTRY_POINT_GROUP!r}: unexpected error: {_brief(exc)}")
    if report.registered:
        log.info("adapter plugins: registered %s", ", ".join(sorted(report.registered)))
    return report


def _env_data_dir() -> Path | None:
    text = env.get(DATA_DIR_ENV).strip()
    return Path(text).expanduser() if text else None


def _entries(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _parse(text: str) -> _Spec:
    """``module[:attr]``, optionally prefixed with ``name=``.

    ``module`` alone defaults to ``build`` (then ``register``); the adapter name is
    the explicit prefix, else plugin-returned metadata, else the module string.
    """
    name = ""
    target = text
    if "=" in text:
        name, _, target = text.partition("=")
        name, target = name.strip(), target.strip()
        if not name:
            raise _SpecError(f"empty name in {text!r}; use module:build or name=module:build")
        if not _NAME_RE.fullmatch(name):
            raise _SpecError(
                f"invalid adapter name {name!r}; use letters, digits, '_', '.', '-'"
            )
    if not target:
        raise _SpecError(f"missing module in {text!r}")
    module, separator, attr = target.partition(":")
    module, attr = module.strip(), attr.strip()
    if not separator:
        attr = ""
    if not module or not _MODULE_RE.fullmatch(module):
        raise _SpecError(f"invalid module {module!r} in {text!r}")
    if separator and not attr:
        raise _SpecError(f"missing attribute after ':' in {text!r}")
    return _Spec(
        module=module,
        attr=attr or None,
        name=name,
        source=f"{ENV_VAR} {module}:{attr or '<auto>'}",
    )


def _load_env_spec(
    report: AdapterPluginReport, spec: _Spec, *, data_dir: Path | None = None
) -> None:
    try:
        module = importlib.import_module(spec.module)
    except Exception as exc:  # noqa: BLE001 - a broken plugin is an operator problem
        _warn(report, f"{spec.source}: cannot import {spec.module!r}: {_brief(exc)}")
        return

    attr, obj = _pick_attribute(module, spec)
    if obj is None:
        _warn(
            report,
            f"{spec.source}: module {spec.module!r} has no callable "
            f"{attr or 'build()/register()'}",
        )
        return
    if spec.name and spec.name in registry.BUILTIN_NAMES:
        _warn(
            report,
            f"{spec.source}: adapter name {spec.name!r} is built into this image and "
            "cannot be replaced; use a new name in devices[].adapter",
        )
        return
    before = set(report.registered)
    allowed, manifest_device = _manifest_allows(
        report,
        module=module,
        name=spec.name or spec.module,
        source=spec.source,
        data_dir=data_dir,
    )
    if not allowed:
        return
    _apply(
        report,
        explicit_name=spec.name,
        fallback_name=spec.module,
        obj=obj,
        attr=attr,
        source=spec.source,
        allow_override=True,
    )
    _remember_runtime_defaults(
        report, before=before, device=manifest_device, extra=spec.name
    )


def _manifest_allows(
    report: AdapterPluginReport,
    *,
    module: Any,
    name: str,
    source: str,
    data_dir: Path | None,
) -> tuple[bool, Any | None]:
    """Require a builtin/trusted manifest before executing an in-process adapter.

    ``trust`` itself is self-asserted metadata, not an authorization boundary; this
    gate is the mechanical half the audit asked for: no manifest (or a third-party
    one) means the env/entry-point channel refuses to register the name. The real
    authorization remains "the operator put the code and the manifest in the image".
    """
    found, problem = _find_manifest(module=module, name=name, data_dir=data_dir)
    if problem:
        _warn(report, f"{source}: {problem}")
        return False, None
    if found is None:
        _warn(
            report,
            f"{source}: no plug manifest backs adapter {name!r}; the env/entry-point "
            "hook requires a rui-plugin manifest with trust: builtin|trusted (ship "
            "plugin.yaml next to the adapter, or install it under <data_dir>/plugs/ "
            f"and set {DATA_DIR_ENV})",
        )
        return False, None
    plugin, device = found
    trust = str(getattr(plugin, "trust", ""))
    if trust not in IN_PROCESS_TRUST:
        _warn(
            report,
            f"{source}: plug {getattr(plugin, 'id', '?')!r} is trust={trust!r}; "
            "in-process adapters require trust: builtin or trusted (third-party is "
            "sidecar-only and 0.3.x has no sidecar runner)",
        )
        return False, None
    runtime = getattr(device, "runtime", None)
    if runtime is not None and str(getattr(runtime, "mode", "")) == "sidecar":
        _warn(
            report,
            f"{source}: plug {getattr(plugin, 'id', '?')!r} asks for runtime.mode "
            "'sidecar', which is not implemented in 0.3.x; set mode: in-process with "
            "trust: trusted, or wait for the 0.4 sidecar runner",
        )
        return False, None
    return True, device


def _find_manifest(
    *, module: Any, name: str, data_dir: Path | None
) -> tuple[tuple[Any, Any] | None, str]:
    """Find the (plugin, device) manifest that backs ``name``; ``(None, "")`` if absent.

    Search order: the in-image builtin catalog, user plugs under ``data_dir``, then a
    ``plugin.yaml`` shipped next to the adapter module/package (the downstream-image
    recipe). A manifest file that exists but cannot be loaded is a hard failure, not a
    fall-through: a broken upper bound must never degrade into "no manifest".
    """
    try:
        from ..plugins.builtin import builtin_plugins
    except Exception as exc:  # pragma: no cover - import cycle guard
        return None, f"cannot read the builtin plug catalog: {_brief(exc)}"

    for plugin in builtin_plugins():
        # Strict match: the single builtin device must not become a universal
        # trust grant for an arbitrary env entry name.
        device = _match_device(plugin, name, allow_single=False)
        if device is not None:
            return (plugin, device), ""

    scan_problem = ""
    if data_dir is not None:
        try:
            from ..plugins.loader import discover_plugins
        except Exception as exc:  # pragma: no cover - import cycle guard
            return None, f"cannot read the plug loader: {_brief(exc)}"
        scan = discover_plugins(data_dir)
        for plugin in scan.plugins:
            device = _match_device(plugin, name, allow_single=False)
            if device is not None:
                return (plugin, device), ""
        # Broken user plugs never authorize anything. Keep the first failures as the
        # explanation only if no co-located manifest resolves the name either.
        if scan.errors:
            detail = "; ".join(error.splitlines()[0] for error in scan.errors[:2])
            scan_problem = (
                f"no loadable plug declares adapter {name!r} under {data_dir}: {detail}"
            )

    root_problem = ""
    for root in _module_plugin_roots(module):
        try:
            from ..plugins.loader import load_plugin
        except Exception as exc:  # pragma: no cover - import cycle guard
            return None, f"cannot read the plug loader: {_brief(exc)}"
        try:
            plugin = load_plugin(root)
        except Exception as exc:  # noqa: BLE001 - include our own path in the warning
            return None, f"manifest {root / 'plugin.yaml'} cannot be loaded: {_brief(exc)}"
        # A co-located manifest belongs to the adapter module we just imported, so a
        # single device may back an env name that differs from the device id (the
        # ``lxref=...`` case). The env prefix still selects the config-facing name.
        device = _match_device(plugin, name, allow_single=True)
        if device is not None:
            return (plugin, device), ""
        root_problem = (
            f"manifest {root / 'plugin.yaml'} has no device {name!r} and declares "
            f"{len(plugin.devices)} device(s); name the env entry after a device id "
            "or add a matching devices/<model>.yaml"
        )
    return None, root_problem or scan_problem


def _match_device(plugin: Any, name: str, *, allow_single: bool) -> Any | None:
    devices = list(getattr(plugin, "devices", []) or [])
    for device in devices:
        if str(getattr(device, "id", "")) == name:
            return device
    if str(getattr(plugin, "id", "")) == name and devices:
        return devices[0]
    if allow_single and len(devices) == 1:
        return devices[0]
    return None


def _module_plugin_roots(module: Any) -> list[Path]:
    """Directories around an adapter module that may hold its ``plugin.yaml``."""
    path = getattr(module, "__file__", None)
    if not path:
        return []
    try:
        base = Path(path).resolve()
    except (OSError, ValueError):
        return []
    roots: list[Path] = []
    for candidate in (base.parent, base.parent.parent):
        try:
            if (candidate / "plugin.yaml").is_file() and candidate not in roots:
                roots.append(candidate)
        except OSError:
            continue
    return roots


def _pick_attribute(module: Any, spec: _Spec) -> tuple[str, Any]:
    if spec.attr:
        obj = getattr(module, spec.attr, None)
        return spec.attr, obj if callable(obj) else None
    for attr in ("build", "register"):
        obj = getattr(module, attr, None)
        if callable(obj):
            return attr, obj
    return "", None


def _remember_runtime_defaults(
    report: AdapterPluginReport,
    *,
    before: set[str],
    device: Any,
    extra: str = "",
) -> None:
    """Cache a gated manifest's runtime defaults for ``build_adapters``.

    The adapter loader is the only component that sees a co-located manifest; the
    registry later needs its ``listen_port``/``token_env`` when the config leaves
    those fields unset (P1-7). The cache is keyed by registration name, so a factory
    that returns a metadata-named builder is covered too.
    """
    runtime = getattr(device, "runtime", None)
    if runtime is None:
        return
    names = set(report.registered) - before
    if extra and extra in report.registered:
        names.add(extra)
    for name in names:
        registry.set_runtime_defaults(
            name,
            listen_port=getattr(runtime, "listen_port", None),
            token_env=getattr(runtime, "token_env", "") or None,
        )


def _apply(
    report: AdapterPluginReport,
    *,
    explicit_name: str,
    fallback_name: str,
    obj: Any,
    attr: str,
    source: str,
    allow_override: bool,
) -> None:
    """Turn one imported object into a registered builder, or into a warning."""
    kind = _call_kind(obj, attr)
    if kind == "register":
        _run_register_hook(
            report,
            hook=obj,
            explicit_name=explicit_name,
            fallback_name=fallback_name,
            source=source,
            allow_override=allow_override,
        )
        return
    if kind == "factory":
        before = dict(registry._BUILDERS)
        try:
            result = obj()
        except Exception as exc:  # noqa: BLE001 - plugin code
            _warn(report, f"{source}: builder factory raised {_brief(exc)}")
            return
        _apply_result(
            report,
            result=result,
            explicit_name=explicit_name,
            fallback_name=fallback_name,
            factory=obj,
            source=source,
            allow_override=allow_override,
            before=before,
        )
        return
    name = _resolve_name(explicit_name, (obj,), fallback_name)
    _register(report, name, obj, source=source, allow_override=allow_override)


def _apply_result(
    report: AdapterPluginReport,
    *,
    result: Any,
    explicit_name: str,
    fallback_name: str,
    factory: Any,
    source: str,
    allow_override: bool,
    before: dict[str, Any] | None = None,
) -> None:
    """Handle what a zero-argument factory returned.

    Supported: a builder callable, a mapping/object with ``build`` (or
    ``builder``/``adapter``) plus optional ``name``/``adapter_name``, or a
    ``"module:attr"`` target string. ``None`` means the factory registered itself.
    """
    if result is None:
        changed = _changed_names(before) if before is not None else []
        if not changed:
            _warn(report, f"{source}: factory returned None and registered no adapter")
        else:
            for name in changed:
                report.registered.setdefault(name, f"{source} (self-registered)")
        return
    if callable(result):
        name = _resolve_name(explicit_name, (result, factory), fallback_name)
        _register(report, name, result, source=source, allow_override=allow_override)
        return
    if isinstance(result, str):
        target = result.strip()
        if not target:
            _warn(report, f"{source}: factory returned an empty target")
            return
        name = _resolve_name(explicit_name, (factory,), fallback_name)
        _register_target(report, name, target, source=source, allow_override=allow_override)
        return

    builder = _builder_attribute(result)
    if builder is not None:
        name = _resolve_name(explicit_name, (result, factory), fallback_name)
        _register(report, name, builder, source=source, allow_override=allow_override)
        return
    target = _target_attribute(result)
    if target:
        name = _resolve_name(explicit_name, (result, factory), fallback_name)
        _register_target(report, name, target, source=source, allow_override=allow_override)
        return
    _warn(
        report,
        f"{source}: factory returned {type(result).__name__}, expected a builder, "
        "mapping with build/name, or module:attr string",
    )


class _RegistryFacade:
    """The registry view a register hook may mutate.

    Passing the real module let a hook call ``registry.register("mock", ...)`` and
    bypass ``allow_override=False`` (impl audit P1-9). This facade routes every
    registration through the loader's checks, including the builtin-name protection;
    it deliberately exposes only the public read helpers a well-behaved hook needs.
    """

    BUILTIN_NAMES = registry.BUILTIN_NAMES

    def __init__(
        self,
        report: AdapterPluginReport,
        *,
        source: str,
        allow_override: bool,
    ) -> None:
        self._report = report
        self._source = source
        self._allow_override = allow_override

    def register(self, name: str, target: Any) -> None:
        source = f"{self._source} (register hook)"
        if isinstance(target, str):
            # The public registry accepts the lazy ``module:attr`` form; keep that
            # working for hooks but route it through the same checked path.
            _register_target(
                self._report,
                name,
                target,
                source=source,
                allow_override=self._allow_override,
            )
            return
        _register(
            self._report,
            name,
            target,
            source=source,
            allow_override=self._allow_override,
        )

    def available(self) -> list[str]:
        return registry.available()


def _run_register_hook(
    report: AdapterPluginReport,
    *,
    hook: Callable[..., Any],
    explicit_name: str,
    fallback_name: str,
    source: str,
    allow_override: bool,
) -> None:
    """Call ``register(registry)`` and record the names it added/replaced.

    The hook owns its names, so the env ``name=`` prefix is only a fallback for a
    hook that returns a builder instead of registering directly. A hook receives
    :class:`_RegistryFacade`, not the real module, so ``allow_override=False`` and the
    builtin-name rule hold even for a hook that calls ``registry.register`` directly.
    """
    before = dict(registry._BUILDERS)
    facade = _RegistryFacade(report, source=source, allow_override=allow_override)
    try:
        result = hook(facade) if _accepts_positional(hook) else hook()
    except Exception as exc:  # noqa: BLE001 - plugin code
        _warn(report, f"{source}: register hook raised {_brief(exc)}")
        return
    _restore_builtins(report, source=source)
    if result is not None:
        _apply_result(
            report,
            result=result,
            explicit_name=explicit_name,
            fallback_name=fallback_name,
            factory=hook,
            source=source,
            allow_override=allow_override,
            before=before,
        )
    changed = _changed_names(before)
    if not changed:
        _warn(report, f"{source}: register hook changed no adapter")
        return
    for name in changed:
        report.registered.setdefault(name, f"{source} (register hook)")


def _restore_builtins(report: AdapterPluginReport, *, source: str) -> None:
    """Undo a hook that imported the real registry and replaced an image adapter."""
    restored: list[str] = []
    for name, target in registry.BUILTIN_TARGETS.items():
        # Only undo a *replacement*: a name that is absent was removed by a test or
        # an embedding and must not be resurrected from a stale protection table.
        if name in registry._BUILDERS and registry._BUILDERS[name] is not target:
            registry._BUILDERS[name] = target
            restored.append(name)
    if restored:
        _warn(
            report,
            f"{source}: register hook replaced built-in adapter(s) "
            f"{', '.join(sorted(restored))}; restored the image definitions",
        )


def _call_kind(obj: Callable[..., Any], attr: str) -> str:
    """Classify by declared role, then by whether it can take a device/config.

    The bug this fixes (impl audit P1-1): ``build(dev=None, cfg=None)`` has no
    *required* argument, so the old rule called it at startup as a zero-argument
    factory. Both bundled reference adapters use exactly that shape. The rule now is:

    * ``register*`` -> registration hook;
    * ``factory``/``make``/``create_factory`` -> zero-argument factory;
    * any callable that declares a positional parameter (required *or* defaulted)
      is a per-device builder, and is never called at startup;
    * a truly parameterless callable is a factory (``build()`` returning a builder).
    """
    role = attr.lower()
    if role in _REGISTER_NAMES:
        return "register"
    try:
        signature = inspect.signature(obj)
    except (TypeError, ValueError):
        return "builder"
    params = list(signature.parameters.values())
    if any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in params):
        return "builder"
    positional = [
        param
        for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if role in _FACTORY_ROLE_NAMES:
        if any(param.default is inspect.Parameter.empty for param in positional):
            # A "factory" that requires arguments cannot be run now; treating it as a
            # builder at least defers the error to the per-device call.
            return "builder"
        return "factory"
    if positional:
        return "builder"
    return "factory"


def _accepts_positional(fn: Callable[..., Any]) -> bool:
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return True
    if any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in params):
        return True
    return any(
        param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                       inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for param in params
    )


def _builder_attribute(result: Any) -> Builder | None:
    if isinstance(result, Mapping):
        for key in _BUILDER_ATTRS:
            value = result.get(key)
            if callable(value):
                return value  # type: ignore[return-value]
        return None
    for key in _BUILDER_ATTRS:
        value = getattr(result, key, None)
        if callable(value):
            return value  # type: ignore[return-value]
    return None


def _target_attribute(result: Any) -> str:
    value = result.get("target") if isinstance(result, Mapping) else getattr(result, "target", None)
    return value.strip() if isinstance(value, str) else ""


def _metadata_name(obj: Any) -> str:
    if isinstance(obj, Mapping):
        for key in ("adapter_name", "name"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    for attr in ("adapter_name", "name"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_name(explicit: str, objects: tuple[Any, ...], fallback: str) -> str:
    if explicit:
        return explicit
    for obj in objects:
        name = _metadata_name(obj)
        if name:
            return name
    return fallback


def _register(
    report: AdapterPluginReport,
    name: str,
    builder: Any,
    *,
    source: str,
    allow_override: bool,
) -> None:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name.strip()):
        _warn(report, f"{source}: invalid adapter name {name!r}")
        return
    name = name.strip()
    if not callable(builder):
        _warn(report, f"{source}: {name!r} is not callable")
        return
    current = registry._BUILDERS.get(name)
    if current is builder:
        report.registered[name] = source
        return
    if name in registry.BUILTIN_NAMES and current is registry.BUILTIN_TARGETS.get(name):
        _warn(
            report,
            f"{source}: adapter name {name!r} is built into this image and cannot be "
            "replaced; register a new name and set devices[].adapter to it",
        )
        return
    if current is not None:
        if not allow_override:
            _warn(report, f"{source}: adapter name {name!r} is already registered; skipped")
            return
        _warn(
            report,
            f"{source}: adapter name {name!r} replaces {_describe(current)}",
        )
    try:
        registry.register(name, builder)
    except registry.BuiltinAdapterName as exc:
        _warn(report, f"{source}: {exc}")
        return
    report.registered[name] = source


def _register_target(
    report: AdapterPluginReport,
    name: str,
    target: str,
    *,
    source: str,
    allow_override: bool,
) -> None:
    """Register a lazy ``module:attr`` string after checking it resolves."""
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name.strip() or not attribute.strip():
        _warn(report, f"{source}: invalid target {target!r}; expected module:attr")
        return
    resolved = _load_target(module_name.strip(), attribute.strip())
    if resolved is None:
        _warn(report, f"{source}: target {target!r} is not importable")
        return
    _register(report, name, resolved, source=source, allow_override=allow_override)


def _load_target(module_name: str, attribute: str) -> Builder | None:
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - reported by the caller with the source
        return None
    value = getattr(module, attribute, None)
    return value if callable(value) else None


def _changed_names(before: dict[str, Any]) -> list[str]:
    return sorted(
        name
        for name, target in registry._BUILDERS.items()
        if before.get(name) != target
    )


def _load_entry_points(
    report: AdapterPluginReport, *, data_dir: Path | None = None
) -> None:
    try:
        points = list(metadata.entry_points(group=ENTRY_POINT_GROUP))
    except TypeError:  # pragma: no cover - Python < 3.10 compatibility
        points = list(metadata.entry_points().get(ENTRY_POINT_GROUP, []))
    except Exception as exc:  # noqa: BLE001 - metadata problems are not startup failures
        _warn(report, f"entry point group {ENTRY_POINT_GROUP!r}: cannot enumerate: {_brief(exc)}")
        return

    seen: set[tuple[str, str]] = set()
    for point in points:
        name = str(getattr(point, "name", "") or "").strip()
        value = str(getattr(point, "value", "") or "")
        if not name:
            _warn(report, f"entry point group {ENTRY_POINT_GROUP!r}: unnamed entry point")
            continue
        if (name, value) in seen:
            continue
        seen.add((name, value))
        if name in registry._BUILDERS:
            # Entry points load automatically from installed distributions, so they
            # must not shadow a built-in or an explicit operator env entry.
            _warn(report, f"entry point {name!r}: adapter name is already registered; skipped")
            continue
        try:
            obj = point.load()
        except Exception as exc:  # noqa: BLE001 - plugin code
            _warn(report, f"entry point {name!r}: cannot load {value!r}: {_brief(exc)}")
            continue
        module = _entry_point_module(obj, value)
        if module is None:
            _warn(
                report,
                f"entry point {name!r}: cannot locate the module of {value!r} for its "
                "manifest trust check",
            )
            continue
        before = set(report.registered)
        allowed, manifest_device = _manifest_allows(
            report,
            module=module,
            name=name,
            source=f"entry point {name!r}",
            data_dir=data_dir,
        )
        if not allowed:
            continue
        try:
            _apply(
                report,
                explicit_name=name,
                fallback_name=name,
                obj=obj,
                attr=_entry_point_attr(value),
                source=f"entry point {name!r}",
                allow_override=False,
            )
        except Exception as exc:  # noqa: BLE001 - plugin code must not stop startup
            _warn(report, f"entry point {name!r}: unexpected error: {_brief(exc)}")
            continue
        _remember_runtime_defaults(report, before=before, device=manifest_device, extra=name)


def _entry_point_module(obj: Any, value: str) -> Any | None:
    """The module object an entry point's manifest can live next to."""
    module_name = str(getattr(obj, "__module__", "") or "")
    module = sys.modules.get(module_name) if module_name else None
    if module is not None:
        return module
    target = value.split("[", 1)[0].strip().split(":", 1)[0].strip()
    if not target:
        return None
    try:
        return importlib.import_module(target)
    except Exception:  # noqa: BLE001 - reported by the caller with the entry name
        return None


def _entry_point_attr(value: str) -> str:
    """``pkg.mod:build [extra]`` -> ``build``; used only to spot a register hook."""
    text = value.split("[", 1)[0].strip()
    return text.rsplit(":", 1)[1].strip() if ":" in text else ""


def _describe(target: Any) -> str:
    return target if isinstance(target, str) else "a builder callable"


def _brief(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _warn(report: AdapterPluginReport, message: str) -> None:
    line = " ".join(str(message).split())
    if len(line) > _MAX_WARNING_CHARS:
        line = line[: _MAX_WARNING_CHARS - 3] + "..."
    report.warnings.append(line)
    log.warning("adapter plugin: %s", line)


# Short alias for callers that already know this module only loads adapters.
load_plugins = load_adapter_plugins
