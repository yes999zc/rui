"""0.7.6: `adapter: xiaoai` is a config migration, not a module alias."""
from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from rui.config import load_config
from rui.config.schema import Config, DeviceConfig
from rui.config.store import legacy_adapter_rewrites
from rui.device import registry
from rui.web.api.app import create_app

TOKEN = "test-token"
LEGACY_YAML = """\
version: 2
system:
  data_dir: {data_dir}
  timezone: Asia/Shanghai
web:
  auth_token: "{token}"
devices:
  - id: living
    adapter: xiaoai
    host: 192.0.2.10
    room: Lab
    listen_port: 8870
    token_env: RUI_DEVICE_TOKEN
"""


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(LEGACY_YAML.format(data_dir=tmp_path / "data", token=TOKEN))
    return path


def test_legacy_spelling_is_rewritten_and_recorded(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path))
    assert cfg.devices[0].adapter == "lx06"
    assert legacy_adapter_rewrites() == [
        {"device": "living", "old": "xiaoai", "new": "lx06"}
    ]


def test_health_counts_the_migration_and_names_the_device(tmp_path: Path) -> None:
    path = _write(tmp_path)
    cfg = load_config(path)
    app = create_app(cfg, config_path=path)
    body = TestClient(app).get("/health", headers={"X-Auth-Token": TOKEN}).json()
    assert body["adapter_plugins"]["warnings"] >= 1
    assert any(row["code"] == "legacy_adapter_spelling" for row in body["warnings"])


def test_missing_external_plugin_points_at_the_bridge(monkeypatch) -> None:
    monkeypatch.delenv("RUI_PLUGINS_DIR", raising=False)
    importlib.reload(registry)
    cfg = Config(devices=[DeviceConfig(id="living", adapter="lx06",
                                       host="192.0.2.10")])
    built = registry.build_adapters(cfg)
    assert "living" not in built.adapters
    assert "rui-xiaoai-bridge" in built.errors["living"]
