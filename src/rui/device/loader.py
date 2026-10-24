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
``plugin.yaml``, console input, the config file or the network, and it never starts
a subprocess. The environment is root/container-operator controlled; treating it as
trusted is the same decision as choosing the image. Third-party plugs stay
sidecar-only in the v1 contract (0.4 tightens this into a formal loader).

Everything is fail-soft: one bad entry logs one bounded warning and startup
continues. A missing module must never take the control plane down.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

from .. import env
from . import registry
from .registry import Builder

log = logging.getLogger(__name__)

ENV_VAR = "RUI_ADAPTER_PLUGINS"
ENTRY_POINT_GROUP = "rui.adapters"

__all__ = [
    "AdapterPluginReport",
    "ENTRY_POINT_GROUP",
    "ENV_VAR",
    "load_adapter_plugins",
    "load_plugins",
]

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


def load_adapter_plugins(value: str | None = None) -> AdapterPluginReport:
    """Load every configured downstream adapter, env first, then entry points.

    ``value`` defaults to ``RUI_ADAPTER_PLUGINS`` (``RUI_*`` before the legacy
    ``HOUSE_EAR_*`` spelling). Passing ``""`` disables env entries but still scans
    ``rui.adapters``. The function never raises for plugin problems.
    """
    report = AdapterPluginReport()
    raw = env.get(ENV_VAR) if value is None else value
    for text in _entries(raw):
        try:
            spec = _parse(text)
        except _SpecError as exc:
            _warn(report, f"{ENV_VAR}: {exc}")
            continue
        try:
            _load_env_spec(report, spec)
        except Exception as exc:  # noqa: BLE001 - plugin code must not stop startup
            _warn(report, f"{spec.source}: unexpected error: {_brief(exc)}")
    try:
        _load_entry_points(report)
    except Exception as exc:  # noqa: BLE001 - metadata/plugin code must not stop startup
        _warn(report, f"entry point group {ENTRY_POINT_GROUP!r}: unexpected error: {_brief(exc)}")
    if report.registered:
        log.info("adapter plugins: registered %s", ", ".join(sorted(report.registered)))
    return report


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


def _load_env_spec(report: AdapterPluginReport, spec: _Spec) -> None:
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
    _apply(
        report,
        explicit_name=spec.name,
        fallback_name=spec.module,
        obj=obj,
        attr=attr,
        source=spec.source,
        allow_override=True,
    )


def _pick_attribute(module: Any, spec: _Spec) -> tuple[str, Any]:
    if spec.attr:
        obj = getattr(module, spec.attr, None)
        return spec.attr, obj if callable(obj) else None
    for attr in ("build", "register"):
        obj = getattr(module, attr, None)
        if callable(obj):
            return attr, obj
    return "", None


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
    hook that returns a builder instead of registering directly.
    """
    before = dict(registry._BUILDERS)
    try:
        result = hook(registry) if _accepts_positional(hook) else hook()
    except Exception as exc:  # noqa: BLE001 - plugin code
        _warn(report, f"{source}: register hook raised {_brief(exc)}")
        return
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


def _call_kind(obj: Callable[..., Any], attr: str) -> str:
    """Classify by name first, then by required positional arguments.

    ``build(dev, cfg)`` (or a 1-arg builder) is called per device later;
    ``register(registry)`` and zero-argument factories run now.
    """
    if attr.lower() in _REGISTER_NAMES:
        return "register"
    try:
        signature = inspect.signature(obj)
    except (TypeError, ValueError):
        return "builder"
    params = list(signature.parameters.values())
    if any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in params):
        return "builder"
    required = [
        param
        for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                          inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and param.default is inspect.Parameter.empty
    ]
    if not required:
        return "factory"
    return "builder"


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
    if current is not None:
        if not allow_override:
            _warn(report, f"{source}: adapter name {name!r} is already registered; skipped")
            return
        _warn(
            report,
            f"{source}: adapter name {name!r} replaces {_describe(current)}",
        )
    registry.register(name, builder)
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


def _load_entry_points(report: AdapterPluginReport) -> None:
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
