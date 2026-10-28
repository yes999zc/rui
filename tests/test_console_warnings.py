"""0.6 startup warnings: the multi-plane notice and inbound port conflicts.

The console review measured two gaps in the warning surface:

* the single-plane notice still said "0.3 supports one active inbound audio plane";
* two enabled inbound speakers on one `listen_port` started without any warning, so
  `/health` looked green while one room could never be driven.

Both are additive to v1: the existing `/health` keys and the
`multiple_enabled_inbound` code are unchanged.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, save_config
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}


def _device(device_id: str, *, room: str, listen_port: int | None = None,
            adapter: str = "lx06") -> DeviceConfig:
    return DeviceConfig(id=device_id, adapter=adapter, host=f"{device_id}.local",
                        room=room, listen_port=listen_port,
                        params={"plug_id": "xiaoai-lx06"})


def _client(tmp_path, config: Config) -> tuple[TestClient, Config]:
    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    return TestClient(create_app(config, config_path=path)), config


def _warning_codes(warnings: list[dict]) -> list[str]:
    return [str(item.get("code")) for item in warnings]


def test_two_rooms_on_one_port_are_reported_at_health(tmp_path):
    """P11: startup still accepts the config, `/health` names the conflict."""
    config = Config(web={"auth_token": TOKEN}, devices=[
        _device("living", room="客厅", listen_port=8870),
        _device("study", room="书房", listen_port=8870),
    ])
    client, _ = _client(tmp_path, config)
    body = client.get("/health").json()

    # Additive: every pre-existing top-level field keeps its meaning.
    assert body["status"] == "ok"
    assert body["enabled_devices"] == 2
    assert isinstance(body["audio_devices"], list)

    warning = next(item for item in body["warnings"]
                   if item["code"] == "port_conflict")
    assert warning["details"]["port"] == 8870
    assert warning["details"]["device_ids"] == ["living", "study"]
    assert "8870" in warning["message"]

    # The authenticated device rows still render their own warnings; the health
    # payload is additive (the old aggregate fields are covered above).
    rows = client.get("/api/v1/devices", headers=AUTH).json()
    assert "multiple_enabled_inbound" in _warning_codes(rows[0]["warnings"])


def test_single_plane_notice_uses_06_multi_plane_wording(tmp_path):
    """P10: the notice keeps the code and room, and 0.6's two fixes."""
    config = Config(web={"auth_token": TOKEN}, devices=[
        _device("living", room="客厅", listen_port=8870),
        _device("study", room="书房", listen_port=8871),
    ])
    client, _ = _client(tmp_path, config)
    body = client.get("/health").json()
    assert "port_conflict" not in _warning_codes(body["warnings"])

    warning = next(item for item in body["warnings"]
                   if item["code"] == "multiple_enabled_inbound")
    assert "0.6" in warning["message"]
    assert "multi-device" in warning["message"]
    assert "Disable the other device" in warning["message"]
    # The active room is named, and the old detail keys are still there (additive).
    assert warning["details"]["active_room"] in {"客厅", "书房"}
    for key in ("active_device", "enabled_inbound", "ignored_devices",
                "max_active_inbound"):
        assert key in warning["details"]


def test_multi_device_mode_also_reports_a_port_conflict(tmp_path):
    """P11: arbitration does not make one port able to serve two planes."""
    config = Config(web={"auth_token": TOKEN}, devices=[
        _device("living", room="客厅", listen_port=8870),
        _device("study", room="书房", listen_port=8870),
    ], multi_device={"enabled": True})
    client, _ = _client(tmp_path, config)
    codes = _warning_codes(client.get("/health").json()["warnings"])
    assert "port_conflict" in codes
    assert "multiple_enabled_inbound" not in codes
