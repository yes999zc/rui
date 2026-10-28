"""task-25 (P0 R4): ``/api/v2/plugins`` describes the plug this process loaded.

On production the image's builtin catalog reported ``dot2-echomuse 1.3.1`` while the
mounted ``PYTHONPATH`` adapter running in the same process was ``1.3.2``. Two rules
are pinned here:

* the adapter loader searches a manifest shipped next to the imported module **first**
  (the code that runs owns the version), then the image catalog, then
  ``<data_dir>/plugs``; the catalog registers which manifest gated the adapter, and
  a same-id collision is decided explicitly as ``adapter > builtin > user`` with the
  losing candidate listed as ``shadowed``;
* ``GET /api/v2/plugins`` reports the winning version/source/source_path, and a
  manifest that exists but cannot be parsed is visible in ``errors`` (never silently
  dropped).

The device page's three-value provenance is covered in
``tests/test_audio_provenance_three_values.py``.
"""
from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest
from _plugin_compat import fixture_compat_range

from rui.config import Config, DeviceConfig
from rui.config.schema import device_audio_defaults_info
from rui.device import loader, registry
from rui.plugins import builtin
from rui.plugins.resolve import (
    PlugCatalog,
    clear_adapter_plugins,
    register_adapter_plugin,
)

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
MODULE = "mounted_dot2_adapter"


@pytest.fixture(autouse=True)
def _isolated_catalog(monkeypatch: pytest.MonkeyPatch):
    """No installed entry point, no leaked env hook, fresh scan caches."""
    before = dict(registry._BUILDERS)
    for name in ("RUI_ADAPTER_PLUGINS", "HOUSE_EAR_ADAPTER_PLUGINS",
                 loader.DATA_DIR_ENV, "RUI_PLUGINS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(loader.metadata, "entry_points", lambda **_: [])
    monkeypatch.setattr(builtin, "IMAGE_DIR", Path("/nonexistent/rui-plugins"))
    monkeypatch.setattr(builtin, "_DEV_DIR", Path("/nonexistent/rui-plugs"))
    clear_adapter_plugins()
    builtin._CACHE.clear()
    yield
    clear_adapter_plugins()
    builtin._CACHE.clear()
    registry._BUILDERS.clear()
    registry._BUILDERS.update(before)
    registry.RUNTIME_DEFAULTS.clear()
    sys.modules.pop(MODULE, None)


def _write_plug(
    root: Path,
    *,
    plugin_id: str,
    version: str,
    device_id: str,
    ack: int = 1200,
    capture_rate: int = 16000,
) -> None:
    """One minimal builtin/trusted plug with one device and an in-process runtime."""
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-plugin/v0
            id: {plugin_id}
            name: "Test plug {plugin_id}"
            version: {version}
            rui_compat: "{fixture_compat_range()}"
            license: MIT
            maintainer: "rui tests"
            trust: trusted
            devices:
              - devices/{device_id}.yaml
            """
        ),
        encoding="utf-8",
    )
    (root / "devices" / f"{device_id}.yaml").write_text(
        textwrap.dedent(
            f"""
            schema: rui-device/v1
            id: {device_id}
            model: "TEST-{device_id}"
            name: "Test speaker {device_id}"
            vendor: "rui tests"
            class: A
            transport: inbound
            protocol: {{kind: ws, version: "1"}}
            capabilities: [playback]
            audio_defaults:
              capture: {{rate: {capture_rate}, channels: 1, mic_channels: 1,
                         ref_channels: 0, bits: 16}}
              playback: {{rate: 48000, channels: 1}}
              ack_echo_ignore_ms: {ack}
            runtime:
              mode: in-process
              entrypoint: "{MODULE}:build"
              listen_port: 4412
            params: []
            """
        ),
        encoding="utf-8",
    )


def _write_module(directory: Path) -> None:
    (directory / f"{MODULE}.py").write_text(
        textwrap.dedent(
            """
            def build(device=None, config=None):
                return {"device": getattr(device, "id", "")}
            """
        ),
        encoding="utf-8",
    )


def _point_builtin_at(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("RUI_PLUGINS_DIR", str(root))
    builtin._CACHE.clear()


def _device(device_id: str, plugin_id: str, adapter: str = "dot2") -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        adapter=adapter,
        host="192.0.2.10",
        room=device_id,
        params={"plug_id": plugin_id},
    )


# — same-id rule: the loaded adapter's manifest wins over the image's older plug ——


def test_mounted_manifest_wins_over_same_id_builtin_and_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_root = tmp_path / "image-plugs"
    _write_plug(image_root / "dot2", plugin_id="dot2-echomuse", version="1.3.1",
                device_id="dot2", ack=1200)
    mounted = tmp_path / "mounted" / "dot2"
    _write_plug(mounted, plugin_id="dot2-echomuse", version="1.3.2",
                device_id="dot2", ack=2500)
    _write_module(mounted)
    _point_builtin_at(monkeypatch, image_root)
    monkeypatch.syspath_prepend(str(mounted))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={MODULE}:build")

    report = loader.load_adapter_plugins()

    assert report.warnings == [], report.warnings
    assert "dot2" in report.registered
    catalog = PlugCatalog(plugin_dirs=[image_root])
    entry = catalog.get("dot2-echomuse")
    assert entry is not None
    assert str(entry.plugin.version) == "1.3.2"
    assert entry.source == "adapter"
    assert entry.origin == str(mounted)
    # The older builtin candidate is not hidden: it is reported as shadowed.
    assert [(item.source, str(item.plugin.version))
            for item in catalog.shadowed["dot2-echomuse"]] == [("builtin", "1.3.1")]

    from fastapi.testclient import TestClient

    from rui.web.api.app import create_app

    cfg = Config(devices=[], web={"auth_token": TOKEN})
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    try:
        body = client.get("/api/v2/plugins", headers=AUTH).json()
    finally:
        client.close()

    assert body["errors"] == []
    item = next(row for row in body["items"] if row["id"] == "dot2-echomuse")
    assert item["version"] == "1.3.2"
    assert item["source"] == "adapter"
    assert item["source_path"] == str(mounted)
    assert item["shadowed"] == [{
        "id": "dot2-echomuse",
        "source": "builtin",
        "version": "1.3.1",
        "source_path": str(image_root / "dot2"),
    }]


def test_mounted_module_prefers_the_data_dir_manifest_over_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production layout with no co-located manifest: code under PYTHONPATH 1.3.2,
    manifest under ``<data_dir>/plugs`` 1.3.2, image baked 1.3.1."""
    image_root = tmp_path / "image-plugs"
    _write_plug(image_root / "dot2", plugin_id="dot2-echomuse", version="1.3.1",
                device_id="dot2", ack=1200)
    data_dir = tmp_path / "data"
    mounted_plug = data_dir / "plugs" / "dot2-echomuse"
    _write_plug(mounted_plug, plugin_id="dot2-echomuse", version="1.3.2",
                device_id="dot2", ack=2500)
    module_dir = tmp_path / "mounted-code"
    module_dir.mkdir()
    _write_module(module_dir)
    _point_builtin_at(monkeypatch, image_root)
    monkeypatch.syspath_prepend(str(module_dir))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dot2={MODULE}:build")

    report = loader.load_adapter_plugins(data_dir=data_dir)

    assert report.warnings == [], report.warnings
    catalog = PlugCatalog(data_dir=data_dir, plugin_dirs=[image_root])
    entry = catalog.get("dot2-echomuse")
    assert entry is not None
    assert (str(entry.plugin.version), entry.source) == ("1.3.2", "adapter")
    assert entry.origin == str(mounted_plug)
    assert [(item.source, str(item.plugin.version))
            for item in catalog.shadowed["dot2-echomuse"]] == [("builtin", "1.3.1")]


def test_no_adapter_registration_keeps_the_image_plug_as_builtin(
    tmp_path: Path,
) -> None:
    image_root = tmp_path / "image-plugs"
    _write_plug(image_root / "dot2", plugin_id="dot2-echomuse", version="1.3.1",
                device_id="dot2")

    catalog = PlugCatalog(plugin_dirs=[image_root])

    entry = catalog.get("dot2-echomuse")
    assert entry is not None
    assert entry.source == "builtin"
    assert str(entry.plugin.version) == "1.3.1"
    assert catalog.shadowed == {}


def test_adapter_registration_wins_over_a_same_id_user_plug(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_plug(data_dir / "plugs" / "dot2-user", plugin_id="dot2-echomuse",
                version="1.0.0", device_id="dot2")
    mounted = tmp_path / "mounted"
    _write_plug(mounted, plugin_id="dot2-echomuse", version="1.3.2", device_id="dot2")

    from rui.plugins import load_plugin

    register_adapter_plugin(load_plugin(mounted), source="adapter", origin=str(mounted))
    catalog = PlugCatalog(data_dir=data_dir, plugin_dirs=[tmp_path / "empty"])

    entry = catalog.get("dot2-echomuse")
    assert entry is not None and entry.source == "adapter"
    assert str(entry.plugin.version) == "1.3.2"
    assert [(item.source, str(item.plugin.version))
            for item in catalog.shadowed["dot2-echomuse"]] == [("user", "1.0.0")]


# — mixed fleet: every device resolves its own manifest, no cross-contamination ——


def test_three_devices_use_their_own_manifest_including_one_shadowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_root = tmp_path / "image-plugs"
    _write_plug(image_root / "dot2-a", plugin_id="plug-a", version="1.0.0",
                device_id="dev-a", ack=1010)
    _write_plug(image_root / "dot2-b", plugin_id="plug-b", version="2.0.0",
                device_id="dev-b", ack=2020)
    _write_plug(image_root / "dot2-c", plugin_id="plug-c", version="3.0.0",
                device_id="dev-c", ack=3030)
    mounted = tmp_path / "mounted" / "dot2-c"
    _write_plug(mounted, plugin_id="plug-c", version="3.1.0", device_id="dev-c",
                ack=3131)
    _write_module(mounted)
    _point_builtin_at(monkeypatch, image_root)
    monkeypatch.syspath_prepend(str(mounted))
    monkeypatch.setenv("RUI_ADAPTER_PLUGINS", f"dev-c={MODULE}:build")

    loader.load_adapter_plugins()

    infos = {
        device_id: device_audio_defaults_info(
            _device(device_id, f"plug-{suffix}"), str(tmp_path / "data"))
        for device_id, suffix in (("dev-a", "a"), ("dev-b", "b"), ("dev-c", "c"))
    }
    assert [(info.plug_id, str(info.defaults.ack_echo_ignore_ms), info.error)
            for info in infos.values()] == [
        ("plug-a", "1010", ""),
        ("plug-b", "2020", ""),
        ("plug-c", "3131", ""),
    ]

    catalog = PlugCatalog(plugin_dirs=[image_root])
    versions = {plugin_id: str(entry.plugin.version)
                for plugin_id, entry in catalog.entries.items()}
    assert versions == {"plug-a": "1.0.0", "plug-b": "2.0.0", "plug-c": "3.1.0"}
    assert catalog.get("plug-c").source == "adapter"


# — a manifest that exists but cannot be parsed is visible, not silently dropped ——


def test_parse_failure_is_visible_in_the_plugin_list_and_device_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    image_root = tmp_path / "image-plugs"
    _write_plug(image_root / "good", plugin_id="good-plug", version="1.0.0",
                device_id="good-device")
    broken = image_root / "broken"
    broken.mkdir()
    (broken / "plugin.yaml").write_text("schema: [unclosed\n", encoding="utf-8")
    _point_builtin_at(monkeypatch, image_root)

    catalog = PlugCatalog()
    assert catalog.get("good-plug") is not None
    assert len(catalog.errors) == 1
    assert "cannot parse" in catalog.errors[0]
    assert str(broken / "plugin.yaml") in catalog.errors[0]

    from fastapi.testclient import TestClient

    from rui.web.api.app import create_app

    cfg = Config(devices=[], web={"auth_token": TOKEN})
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    try:
        body = client.get("/api/v2/plugins", headers=AUTH).json()
    finally:
        client.close()
    assert body["errors"] == catalog.errors, (body["errors"], catalog.errors)
    assert any("cannot parse" in error for error in body["errors"])

    with caplog.at_level(logging.WARNING, logger="rui.config.schema"):
        info = device_audio_defaults_info(
            _device("parse-failure-device", "parse-failure-plug"),
            str(tmp_path / "data"))
    assert "has no usable model manifest" in caplog.text
    assert "cannot parse" in caplog.text
    assert info.error
    assert "not installed or its manifest did not load" in info.error
    assert "cannot parse" in info.error
    assert str(broken / "plugin.yaml") in info.error
