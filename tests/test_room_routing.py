"""0.6 M3 wizard step 1: no device is chosen for the user.

A fresh install with several installed plugs must ask which one to set up. The old
behaviour compiled one plug into the core and silently defaulted to it, which made
"xiaoai" the answer for every house. These tests pin both sides: several -> a
`plug_required` answer listing the installed plugs, exactly one -> that plug is used.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from rui.config import Config, save_config
from rui.plugins import resolve as plugin_resolve
from rui.web.api import app as app_module


def _plug(root, plugin_id: str, name: str) -> None:
    """One minimal user plug under <data_dir>/plugs/<id>/."""
    folder = root / plugin_id
    (folder / "devices").mkdir(parents=True, exist_ok=True)
    (folder / "plugin.yaml").write_text(
        "schema: rui-plugin/v0\n"
        f"id: {plugin_id}\n"
        f"name: {name}\n"
        "version: 1.0.0\n"
        'rui_compat: ">=0.0.1 <999.0.0"\n'
        "license: MIT\n"
        "maintainer: Test <test@example.invalid>\n"
        "trust: builtin\n"
        "devices:\n"
        "  - devices/device.yaml\n",
        encoding="utf-8",
    )
    (folder / "devices" / "device.yaml").write_text(
        "schema: rui-device/v0\n"
        f"id: {plugin_id}\n"
        f"name: {name}\n"
        "vendor: Demo Inc.\n"
        "class: A\n"
        "transport: outbound\n"
        "protocol: {kind: ws, version: '1'}\n"
        "capabilities: [playback]\n"
        "params:\n"
        "  - {key: host, label: Address, type: string, required: true}\n",
        encoding="utf-8",
    )


def _client(tmp_path, monkeypatch, plug_ids: list[str]) -> TestClient:
    """An install with no devices and exactly the shipped plugs given."""
    # Keep only the two/one user plugs under data_dir: the image-plug catalog is
    # what a real build ships, and its content must not decide this test.
    monkeypatch.setattr(plugin_resolve, "builtin_plugins", lambda *args, **kwargs: [])
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    root = tmp_path / "data" / "plugs"
    for index, plugin_id in enumerate(plug_ids):
        _plug(root, plugin_id, f"Demo Speaker {index}")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(app_module.create_app(cfg, config_path=path))


def test_several_installed_plugs_make_the_wizard_ask_first(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, ["demo-a", "demo-b"])
    response = client.get("/api/v1/setup/step")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "plug_required"
    assert detail["details"]["available"] == ["demo-a", "demo-b"]
    # Choosing one explicitly is what starts the eight steps.
    chosen = client.get("/api/v1/setup/step?plug=demo-a")
    assert chosen.status_code == 200
    assert chosen.json()["plug"]["id"] == "demo-a"


def test_a_single_installed_plug_needs_no_question(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, ["only-plug"])
    response = client.get("/api/v1/setup/step")
    assert response.status_code == 200
    assert response.json()["plug"]["id"] == "only-plug"
    assert response.json()["next_step"] == "keys"
