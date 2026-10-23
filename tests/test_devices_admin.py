"""P5: device administration - edit one device without losing what the form hides.

The wizard adds the first speaker; this is the day-after page: rename the room,
fix the host, change the wake words, retarget the volume, or remove the device.
Every write restarts the audio plane (it reads the device list once), so the
tests also pin that keywords.txt follows a wake-word edit and that untouched
fields (LED colours, volume target) survive.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from house_ear.config import Config, DeviceConfig, load_config, save_config
from house_ear.web.api.app import create_app


@pytest.fixture()
def admin(tmp_path):
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    cfg.devices = [DeviceConfig(id="living", adapter="xiaoai", host="127.0.0.1",
                                room="客厅", wake_words=["你好小瑞"], volume=70)]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), path, tmp_path


def test_reads_one_device_with_its_live_status(admin) -> None:
    client, _path, _tmp = admin
    body = client.get("/api/v1/devices/living").json()
    assert body["device"]["id"] == "living"
    assert body["device"]["wake_words"] == ["你好小瑞"]
    assert isinstance(body["live"], dict)
    assert client.get("/api/v1/devices/ghost").status_code == 404


def test_edit_keeps_fields_the_form_does_not_show(admin) -> None:
    client, path, tmp = admin
    cfg = load_config(path)
    cfg = cfg.model_copy(update={"devices": [cfg.devices[0].model_copy(update={
        "listen_port": 4412, "led": cfg.devices[0].led.model_copy(update={"enabled": True}),
    })]})
    save_config(path, cfg)

    response = client.put("/api/v1/devices/living", json={"room": "书房"})
    assert response.status_code == 200, response.text
    device = load_config(path).devices[0]
    assert device.room == "书房"
    assert device.listen_port == 4412
    assert device.led.enabled is True


def test_wake_word_edit_regenerates_keywords(admin) -> None:
    client, path, tmp = admin
    assert client.put("/api/v1/devices/living",
                      json={"wake_words": ["嗨小瑞"]}).status_code == 200
    assert load_config(path).devices[0].wake_words == ["嗨小瑞"]
    assert "@嗨小瑞" in (tmp / "keywords.txt").read_text(encoding="utf-8")


def test_edit_validates_its_inputs(admin) -> None:
    client, _path, _tmp = admin
    for patch in (
        {"volume": 999},
        {"volume": "loud"},
        {"listen_port": 0},
        {"listen_port": "4412"},
        {"host": "http://x"},
        {"adapter": "  "},
        {"wake_words": ["你好\n小瑞"]},
        {"room": "x" * 40},
    ):
        response = client.put("/api/v1/devices/living", json=patch)
        assert response.status_code == 422, (patch, response.text)


def test_delete_removes_it_and_a_second_delete_is_404(admin) -> None:
    client, path, _tmp = admin
    body = client.delete("/api/v1/devices/living").json()
    assert body["removed"] == "living"
    assert body["devices"] == []
    # No plane runs in this test, so the restart outcome is "" (the key is always there;
    # a failed restart carries the error - see tests/test_setup.py).
    assert body["plane_error"] == ""
    assert load_config(path).devices == []
    assert client.delete("/api/v1/devices/living").status_code == 404


def test_writes_need_a_config_file_on_disk(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    assert client.put("/api/v1/devices/living", json={"room": "x"}).status_code == 409
    assert client.delete("/api/v1/devices/living").status_code == 409


def test_probe_reports_an_unreachable_host(admin) -> None:
    client, _path, _tmp = admin
    body = client.post("/api/v1/devices/probe",
                       json={"host": "no-such-host.invalid"}).json()
    assert body["usable"] is False
    assert body["ssh"]["port_open"] is False
