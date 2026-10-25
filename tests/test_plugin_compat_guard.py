"""Regression guard: manifests and fixtures must accept the running RUI build.

The v0.4.0 baseline failed 26 tests because four fixtures declared
rui_compat ">=0.2.0 <0.4.0" while rui.__version__ was 0.4.0. The loader refuses
an out-of-range manifest, so every test that loaded one failed. This guard makes
a future version bump fail at the source with the offending range and file named,
instead of as a wall of unrelated test failures.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from _plugin_compat import FIXTURE_COMPAT_FLOOR, fixture_compat_range
from ruamel.yaml import YAML

import rui
from rui.plugins import (
    PluginError,
    builtin_plugin,
    load_plugin,
    parse_range,
    parse_version,
)

REPO = Path(__file__).resolve().parents[1]
XIAOAI = REPO / "plugins" / "rui-plugin-xiaoai"
EXAMPLE = REPO / "examples" / "plugin-example"
_YAML = YAML(typ="safe")


def _manifest_range(path: Path) -> str:
    data = _YAML.load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} is not a YAML mapping"
    compat = data.get("rui_compat")
    assert isinstance(compat, str) and compat, f"{path} has no rui_compat"
    return compat


def _assert_accepts(label: str, range_text: str) -> None:
    running = rui.__version__
    assert parse_range(range_text).matches(running), (
        f"{label} declares rui_compat {range_text!r}, which does not accept the "
        f"running build {running}. Shipped manifests and test fixtures must "
        "accept the version they run under; widen the ceiling (or derive it "
        "from rui.__version__) before bumping the version."
    )


def test_fixture_compat_range_accepts_the_running_build() -> None:
    compat = fixture_compat_range()
    _assert_accepts("tests/_plugin_compat.fixture_compat_range()", compat)
    # Keep the fixtures on the v0 line: the floor must not drift upwards.
    assert parse_range(compat).matches(parse_version(FIXTURE_COMPAT_FLOOR)), (
        f"fixture rui_compat {compat!r} no longer accepts the v0 floor "
        f"{FIXTURE_COMPAT_FLOOR}; the fixtures model v0 plugins"
    )


@pytest.mark.parametrize(
    ("label", "manifest"),
    [
        ("in-code builtin xiaoai-lx06 (src/rui/plugins/builtin.py)", None),
        ("plugins/rui-plugin-xiaoai/plugin.yaml", XIAOAI),
        ("examples/plugin-example/plugin.yaml", EXAMPLE),
    ],
)
def test_shipped_manifest_accepts_the_running_build(
    label: str, manifest: Path | None
) -> None:
    if manifest is None:
        plugin = builtin_plugin("xiaoai-lx06")
        assert plugin is not None, "the in-code builtin xiaoai-lx06 is missing"
        _assert_accepts(label, plugin.rui_compat)
        return
    try:
        plugin = load_plugin(manifest)
    except PluginError as exc:
        raise AssertionError(
            f"{label} cannot be loaded by rui {rui.__version__}: {exc}. "
            "A shipped manifest must accept the build it ships with; widen its "
            "rui_compat ceiling before bumping the version."
        ) from None
    _assert_accepts(label, plugin.rui_compat)


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
