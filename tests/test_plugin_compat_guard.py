"""Regression guard: manifests and fixtures must accept the running RUI build.

The v0.4.0 baseline failed 26 tests because four fixtures declared
rui_compat ">=0.2.0 <0.4.0" while rui.__version__ was 0.4.0. The loader refuses
an out-of-range manifest, so every test that loaded one failed. This guard makes
a future version bump fail at the source with the offending range and file named,
instead of as a wall of unrelated test failures.

0.7.0 repeated the trap one release later: the shipped manifests declared
"<0.7.0", so the version bump alone would have refused every plug. The scan below
now discovers every `plugin.yaml` in the repository instead of listing two paths,
so a new shipped manifest is covered the moment it lands.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from _plugin_compat import FIXTURE_COMPAT_FLOOR, fixture_compat_range
from ruamel.yaml import YAML

import rui
from rui.plugins import (
    PluginError,
    load_plugin,
    parse_range,
    parse_version,
)

REPO = Path(__file__).resolve().parents[1]
#: Directories whose `plugin.yaml` is generated, vendored, or not shipped by this repo.
SKIP_PARTS = {
    ".git", ".venv", "node_modules", "web", "build", "dist", "__pycache__",
}
_YAML = YAML(typ="safe")


def _shipped_manifests() -> list[Path]:
    """Every in-repo manifest the loader is expected to accept, not just two paths."""
    return sorted(
        path
        for path in REPO.rglob("plugin.yaml")
        if not any(part in SKIP_PARTS for part in path.relative_to(REPO).parts)
        and not any(part.startswith(".") for part in path.relative_to(REPO).parts)
    )


_MANIFESTS = _shipped_manifests()


def _manifest_range(path: Path) -> str:
    data = _YAML.load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} is not a YAML mapping"
    compat = data.get("rui_compat")
    assert isinstance(compat, str) and compat, f"{path} has no rui_compat"
    return compat


def _assert_accepts(label: str, range_text: str) -> None:
    running = rui.__version__
    # rui's own parser is the loader's authority (it also accepts the space-separated
    # manifest syntax, which `SpecifierSet` would reject without comma normalisation).
    assert parse_range(range_text).matches(running), (
        f"{label} declares rui_compat {range_text!r}, which does not accept the "
        f"running build {running}. Shipped manifests and test fixtures must "
        "accept the version they run under; widen the ceiling (or derive it "
        "from rui.__version__) before bumping the version."
    )


def test_the_repo_ships_the_known_manifests() -> None:
    """A parametrized scan that finds nothing would silently pass."""
    shipped = {path.relative_to(REPO).as_posix() for path in _MANIFESTS}
    assert shipped >= {
        "plugs/xiaoai/plugin.yaml",
        "examples/plugin-example/plugin.yaml",
    }, f"manifest discovery lost a shipped plug: {sorted(shipped)}"


def test_fixture_compat_range_accepts_the_running_build() -> None:
    compat = fixture_compat_range()
    _assert_accepts("tests/_plugin_compat.fixture_compat_range()", compat)
    # Keep the fixtures on the v0 line: the floor must not drift upwards.
    assert parse_range(compat).matches(parse_version(FIXTURE_COMPAT_FLOOR)), (
        f"fixture rui_compat {compat!r} no longer accepts the v0 floor "
        f"{FIXTURE_COMPAT_FLOOR}; the fixtures model v0 plugins"
    )


@pytest.mark.parametrize(
    "manifest",
    _MANIFESTS,
    ids=[path.relative_to(REPO).as_posix() for path in _MANIFESTS],
)
def test_every_shipped_manifest_accepts_the_running_build(manifest: Path) -> None:
    label = manifest.relative_to(REPO).as_posix()
    try:
        plugin = load_plugin(manifest.parent)
    except PluginError as exc:
        raise AssertionError(
            f"{label} cannot be loaded by rui {rui.__version__}: {exc}. "
            "A shipped manifest must accept the build it ships with; widen its "
            "rui_compat ceiling before bumping the version."
        ) from None
    _assert_accepts(label, plugin.rui_compat)
    # Read the raw file too: the loader could normalise the range in a future core.
    _assert_accepts(f"{label} (raw rui_compat)", _manifest_range(manifest))


def test_manifests_written_by_the_fixtures_accept_the_running_build(
    tmp_path: Path,
) -> None:
    from test_adapter_plugins import _write_manifest, _write_runtime_manifest
    from test_device_wake import _write_wake_plug

    adapter_root = tmp_path / "adapter"
    adapter_root.mkdir()
    _write_manifest(adapter_root)

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    _write_runtime_manifest(runtime_root, device_id="compat-guard")

    wake_root = tmp_path / "wake"
    wake_root.mkdir()
    _write_wake_plug(wake_root)

    cases = [
        (
            "tests/test_adapter_plugins.py::_write_manifest",
            adapter_root / "plugin.yaml",
        ),
        (
            "tests/test_adapter_plugins.py::_write_runtime_manifest",
            runtime_root / "plugs" / "runtime-plug" / "plugin.yaml",
        ),
        (
            "tests/test_device_wake.py::_write_wake_plug",
            wake_root / "plugs" / "dot2-plug" / "plugin.yaml",
        ),
    ]
    for label, manifest in cases:
        _assert_accepts(label, _manifest_range(manifest))
