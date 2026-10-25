"""The device-write invariant both consoles share (C-002 §4.5).

Repro before the fix: the v1 console wrote ``devices[]`` straight to ``save_config``,
which does not validate. Disabling the last speakers, deleting the device named by
``multi_device.default_output``/``room_priority``, or clearing a room while multi-device
was on all persisted a ``config.yaml`` that ``load_config`` rejects at the next boot.
These tests use a real ``TestClient`` and assert both the response and the file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app
from rui.web.api.device_write import DeviceWriteError, checked_device_config


async def _fake_probe(host: str) -> dict:
    return {"host": host, "usable": True,
            "ssh": {"port_open": True, "banner": True}, "hint": ""}


def _device(device_id: str, room: str, port: int) -> DeviceConfig:
    return DeviceConfig(id=device_id, adapter="xiaoai", host=f"{device_id}.local",
                        room=room, listen_port=port,
                        params={"plug_id": "xiaoai-lx06"})


@pytest.fixture()
def console(tmp_path, monkeypatch):
    """A v1 console on a real config file; the house options are per test."""
    monkeypatch.setattr(app_module, "_probe_host", _fake_probe)

    def build(*, default_output: str | None = None,
              room_priority: list[str] | None = None) -> tuple[TestClient, Path]:
        cfg = Config(
            devices=[_device("living", "客厅", 4412), _device("study", "书房", 4413)],
            multi_device={
                "enabled": True,
                "default_output": default_output,
                "arbitration": {"room_priority": room_priority or []},
            },
        )
        cfg.system.data_dir = str(tmp_path / "data")
        cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, cfg)
        return TestClient(create_app(cfg, config_path=path)), path

    return build


def test_v1_disable_every_speaker_leaves_a_loadable_config(console) -> None:
    client, path = console()
    first = client.post("/api/v1/devices/living/enabled", json={"enabled": False})
    assert first.status_code == 200, first.text
    last = client.post("/api/v1/devices/study/enabled", json={"enabled": False})
    assert last.status_code == 200, last.text
    assert last.json()["device"]["enabled"] is False
    assert last.json()["changed"] is True

    fresh = load_config(path)          # raised ConfigError before the fix
    assert [device.id for device in fresh.devices if device.enabled] == []
    assert fresh.multi_device.enabled is False


def test_v1_disable_the_default_output_clears_the_reference(console) -> None:
    client, path = console(default_output="living")
    response = client.post("/api/v1/devices/living/enabled", json={"enabled": False})
    assert response.status_code == 200, response.text

    fresh = load_config(path)
    assert fresh.multi_device.default_output is None
    assert fresh.multi_device.enabled is True          # study is still enabled


def test_v1_delete_drops_default_output_and_priority_references(console) -> None:
    client, path = console(default_output="living",
                           room_priority=["living", "study"])
    response = client.delete("/api/v1/devices/living")
    assert response.status_code == 200, response.text
    assert response.json()["removed"] == "living"

    fresh = load_config(path)          # raised ConfigError before the fix
    assert [device.id for device in fresh.devices] == ["study"]
    assert fresh.multi_device.default_output is None
    assert fresh.multi_device.arbitration.room_priority == ["study"]


def test_v1_put_with_an_invalid_house_field_is_422_and_writes_nothing(console) -> None:
    client, path = console()
    before = path.read_bytes()
    response = client.put("/api/v1/devices/study", json={"room": ""})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_values"
    assert detail["details"]["fields"][0]["path"].endswith(".room")
    assert any("room" in item["message"] and "study" in item["message"]
               for item in detail["details"]["fields"])
    assert path.read_bytes() == before


def test_v1_put_with_a_valid_change_still_writes(console) -> None:
    client, path = console()
    response = client.put("/api/v1/devices/study", json={"room": "书房", "volume": 42})
    assert response.status_code == 200, response.text
    saved = {device.id: device for device in load_config(path).devices}
    assert saved["study"].volume == 42


def test_the_shared_check_repairs_and_rejects_before_any_write(console) -> None:
    """No HTTP: the invariant itself is what v1, v2 and the wizard call."""
    _, path = console(default_output="living")
    config = load_config(path)
    repaired = checked_device_config(
        config, [device.model_copy(update={"enabled": False})
                 for device in config.devices])
    assert repaired.multi_device.enabled is False
    assert repaired.multi_device.default_output is None

    with pytest.raises(DeviceWriteError) as caught:
        checked_device_config(config, [config.devices[0], config.devices[0]])
    assert caught.value.status_code == 422
    assert caught.value.code == "invalid_values"
    # The rejected candidate never reached the file.
    assert [device.id for device in load_config(path).devices] == ["living", "study"]
