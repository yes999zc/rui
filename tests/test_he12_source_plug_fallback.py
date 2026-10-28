"""HE-12: the source-run fallback loads a plug from the repo's ``plugs/`` tree.

A checkout has no ``/opt/rui-plugins`` image directory, so
``rui.plugins.builtin.plugin_dirs()`` falls back to ``<repo>/plugs``. That branch
is what makes ``uv run rui`` see the same manifests the image ships, but it was
only covered indirectly (the LX06 plug was present on every dev machine). These
tests build a synthetic ``plugs/`` source tree with a fake adapter and prove:

1. the manifest from the fallback directory authorises the in-process adapter;
2. a user plug under ``<data_dir>/plugs`` declaring the same device id cannot
   shadow the fallback (source-run) manifest -- the catalog keeps the source one.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from _plugin_compat import fixture_compat_range

from rui.device import loader, registry
from rui.plugins import builtin
from rui.plugins.resolve import PlugCatalog

SOURCE_DEVICE = "source-speaker"
ADAPTER_MODULE = "demo_source_adapter"


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch):
    """Start from the shipped builders and an empty plug environment."""
    before = dict(registry._BUILDERS)
    for name in ("RUI_ADAPTER_PLUGINS", "HOUSE_EAR_ADAPTER_PLUGINS",
                 loader.DATA_DIR_ENV, "RUI_PLUGINS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: [])
    yield
    registry._BUILDERS.clear()
    registry._BUILDERS.update(before)
    registry.RUNTIME_DEFAULTS.clear()
    builtin._CACHE.clear()
    sys.modules.pop(ADAPTER_MODULE, None)


def _write_plug(root: Path, *, plugin_id: str, device_id: str) -> None:
    """One minimal source-tree plug: manifest + device manifest + fake adapter."""
    (root / "devices").mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-plugin/v0
            id: {plugin_id}
            name: "Source fallback plug"
            version: 1.0.0
            rui_compat: "{fixture_compat_range()}"
            license: MIT
            maintainer: "rui fixtures"
            trust: trusted
            devices:
              - devices/demo.yaml
            """
        ),
        encoding="utf-8",
    )
    (root / "devices" / "demo.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-device/v1
            id: {device_id}
            name: "Source fallback speaker"
            vendor: "rui fixtures"
            class: A
            transport: inbound
            protocol: {{kind: ws, version: "1"}}
            capabilities: [playback]
            runtime:
              mode: in-process
              entrypoint: "{ADAPTER_MODULE}:build"
              listen_port: 4412
            params: []
            """
        ),
        encoding="utf-8",
    )
    (root / f"{ADAPTER_MODULE}.py").write_text(
        textwrap.dedent(
            """
            def build(device=None, config=None):
                return {"device": getattr(device, "id", "source-speaker")}
            """
        ),
        encoding="utf-8",
    )


def _point_fallback_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plugs: Path) -> None:
    monkeypatch.delenv("RUI_PLUGINS_DIR", raising=False)
    monkeypatch.setattr(builtin, "IMAGE_DIR", tmp_path / "no-image-plugins")
    monkeypatch.setattr(builtin, "_DEV_DIR", plugs)
    builtin._CACHE.clear()


def test_source_plugs_fallback_backs_the_in_process_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugs = tmp_path / "plugs"
    _write_plug(plugs / "demo", plugin_id="source-plug", device_id=SOURCE_DEVICE)
    _point_fallback_at(monkeypatch, tmp_path, plugs)
    monkeypatch.syspath_prepend(str(plugs / "demo"))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"{SOURCE_DEVICE}={ADAPTER_MODULE}:build")

    # The catalog itself comes from plugs/, not from an image directory.
    assert [plugin.id for plugin in builtin.builtin_plugins()] == ["source-plug"]
    assert builtin.builtin_plugin_errors() == []

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert SOURCE_DEVICE in report.registered
    assert callable(registry._BUILDERS[SOURCE_DEVICE])


def test_source_fallback_manifest_wins_over_a_same_device_user_plug(tmp_path: Path) -> None:
    builtin_root = tmp_path / "plugs"
    _write_plug(builtin_root / "demo", plugin_id="source-plug", device_id=SOURCE_DEVICE)
    data_dir = tmp_path / "data"
    _write_plug(data_dir / "plugs" / "shadow", plugin_id="shadow-plug",
                device_id=SOURCE_DEVICE)

    catalog = PlugCatalog(data_dir=data_dir, plugin_dirs=[builtin_root])
    resolved = catalog.resolve(adapter=SOURCE_DEVICE)

    assert resolved is not None
    assert resolved.source == "builtin"
    assert resolved.plugin.id == "source-plug"
    assert resolved.plug_id == "source-plug"
