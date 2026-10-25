"""Image-shipped Socket & Plug manifests, loaded from a plug directory.

0.6 M2 (docs/roadmap-0.6.md): the LX06 manifest is no longer in code. The image
copies the repository's plugs/ tree to /opt/rui-plugins, and this module is the
catalog over that directory; a source checkout falls back to its own plugs/
directory, so "uv run rui" and the test suite see the same manifests the image
ships. Nothing here names a device.

Ordering:
* RUI_PLUGINS_DIR - extra operator roots, os.pathsep-separated;
* /opt/rui-plugins - the image path, always kept so an operator override cannot
  hide the adapter an existing config selects;
* <repo>/plugs - development fallback (only when neither of the above exists).

Each root contains one directory per plug (<root>/<plug>/plugin.yaml), the same
shape a downstream image copies its package into. Directories without a
plugin.yaml are ignored, symlinks are skipped, and one broken manifest never hides
the others: the failure is logged and exposed via builtin_plugin_errors(), as data,
not an exception at import.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..env import get as env_get
from .errors import PluginError
from .loader import load_plugin
from .models import Plugin

__all__ = [
    "ENV_VAR",
    "IMAGE_DIR",
    "builtin_plugin",
    "builtin_plugin_errors",
    "builtin_plugins",
    "plugin_dirs",
]

log = logging.getLogger(__name__)

#: Colon-separated roots (like PATH) that hold one directory per plug.
ENV_VAR = "RUI_PLUGINS_DIR"
#: Where the image copies plugs/ (docs/plug-test-kit.md downstream recipe).
IMAGE_DIR = Path("/opt/rui-plugins")

#: repo root from src/rui/plugins/builtin.py; only used when it actually has plugs/
_DEV_ROOT = Path(__file__).resolve().parents[3]
_DEV_DIR = _DEV_ROOT / "plugs"

# key -> (plugins, errors). The key is the resolved directory list, so a test that
# points RUI_PLUGINS_DIR at a fixture gets its own scan without reloading the module.
_CACHE: dict[tuple[str, ...], tuple[tuple[Plugin, ...], tuple[str, ...]]] = {}


def plugin_dirs() -> list[Path]:
    """The roots to scan, highest priority first.

    RUI_PLUGINS_DIR adds roots; it never replaces the image directory, because an
    existing config that selects an image plug must not break just because an
    operator pointed the variable at an extra directory (include both explicitly
    only if a root should be scanned before the image: the image is appended last).
    """
    raw = env_get(ENV_VAR).strip()
    roots = [
        Path(part).expanduser()
        for part in raw.split(os.pathsep)
        if part.strip()
    ] if raw else []
    if IMAGE_DIR.is_dir():
        roots.append(IMAGE_DIR)
    elif not roots and _DEV_DIR.is_dir():
        roots.append(_DEV_DIR)
    return _dedupe(roots)


def builtin_plugins(dirs: list[Path] | tuple[Path, ...] | None = None) -> list[Plugin]:
    """Every plug shipped with this build, in stable directory order.

    Passing dirs overrides the environment/development discovery (tests and
    embedders); it is not an operator-facing knob. A fresh list is returned.
    """
    roots = plugin_dirs() if dirs is None else _dedupe(Path(d) for d in dirs)
    plugins, _errors = _scan(roots)
    return list(plugins)


def builtin_plugin(plugin_id: str) -> Plugin | None:
    """The image plug with plugin_id, or None."""
    wanted = (plugin_id or "").strip()
    for plugin in builtin_plugins():
        if plugin.id == wanted:
            return plugin
    return None


def builtin_plugin_errors(dirs: list[Path] | tuple[Path, ...] | None = None) -> list[str]:
    """One bounded line per broken image plug, for the console/doctor to display."""
    roots = plugin_dirs() if dirs is None else _dedupe(Path(d) for d in dirs)
    _plugins, errors = _scan(roots)
    return list(errors)


def _dedupe(paths) -> list[Path]:
    seen: list[Path] = []
    for path in paths:
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):  # a bad env entry is not fatal
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def _scan(dirs: list[Path]) -> tuple[tuple[Plugin, ...], tuple[str, ...]]:
    key = tuple(str(path) for path in dirs)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    plugins: list[Plugin] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for root in dirs:
        try:
            if not root.is_dir():
                continue
            children = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            errors.append(f"cannot read plug directory {root}: {exc}")
            continue
        for child in children:
            try:
                if child.is_symlink() or not child.is_dir():
                    continue
                if not (child / "plugin.yaml").is_file():
                    continue
            except OSError:
                continue
            try:
                plugin = load_plugin(child)
            except PluginError as exc:
                errors.append(str(exc))
                log.warning("image plug %s cannot be loaded: %s", child, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - one plug must not break the image
                errors.append(f"{child}: unexpected error while loading plug: {exc}")
                log.warning("image plug %s cannot be loaded: %s", child, exc)
                continue
            if plugin.id in seen_ids:
                errors.append(f"{child}: duplicate plugin id {plugin.id!r}; skipped")
                continue
            seen_ids.add(plugin.id)
            plugins.append(plugin)
    result = (tuple(plugins), tuple(errors))
    _CACHE[key] = result
    return result
