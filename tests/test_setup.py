"""C-UI P4: the install wizard's backend.

A stranger's install has to become a talking speaker without editing YAML: the
wizard needs to know what is still missing, store keys write-only, write the first
device, regenerate the wake words, and probe the speaker's host. Each test below is
one of those promises; "it renders" is not one of them.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from house_ear.config import Config, load_config, save_config
from house_ear.web.api.app import create_app


@pytest.fixture()
def wizard(tmp_path, monkeypatch):
    """A console with a config file on disk and no keys in the environment."""
    for name in ("HOUSE_EAR_LLM_KEY", "HOUSE_EAR_ASR_KEY", "HOUSE_EAR_TTS_KEY",
                 "DEEPSEEK_API_KEY", "XFYUN_API_KEY", "XFYUN_APPID", "XFYUN_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), path


def test_state_asks_for_the_keys_first(wizard) -> None:
    client, _path = wizard
    body = client.get("/api/v1/setup").json()
    assert body["next_step"] == "keys"
    assert [step["id"] for step in body["steps"]] == [
        "keys", "models", "device", "wake", "verify"]
    assert body["config_exists"] is True
    assert body["device"] is None


def test_keys_are_stored_but_never_echoed(wizard) -> None:
    client, path = wizard
    response = client.post("/api/v1/setup/keys",
                           json={"values": {"HOUSE_EAR_LLM_KEY": "sk-test-should-not-echo"}})
    assert response.status_code == 200
    assert "sk-test-should-not-echo" not in response.text
    assert response.json()["steps"][0]["detail"]["HOUSE_EAR_LLM_KEY"] is True
    # The value is on disk for the providers, not in config.yaml.
    assert "sk-test-should-not-echo" not in path.read_text(encoding="utf-8")
    assert (path.parent / "data" / "secrets.yaml").exists()


def test_device_is_written_and_the_keywords_file_follows(wizard) -> None:
    client, path = wizard
    response = client.post("/api/v1/setup/device", json={
        "host": "speaker.local", "room": "客厅", "wake_words": ["你好小瑞"],
    })
    assert response.status_code == 200, response.text
    cfg = load_config(path)
    assert [device.id for device in cfg.devices] == ["living"]
    assert cfg.devices[0].host == "speaker.local"
    keywords = path.parent / "keywords.txt"
    assert keywords.is_file()
    assert keywords.read_text(encoding="utf-8").strip() == \
        "n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞"
    # The device list endpoint sees it without a control-plane restart.
    devices = client.get("/api/v1/devices").json()
    assert any(device["id"] == "living" for device in devices)


def test_wake_words_can_be_changed_after_the_device_exists(wizard) -> None:
    client, path = wizard
    assert client.post("/api/v1/setup/wake", json={"wake_words": ["嗨小瑞"]}).status_code == 409
    client.post("/api/v1/setup/device", json={"host": "speaker.local", "wake_words": ["你好小瑞"]})
    response = client.post("/api/v1/setup/wake", json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    assert "@嗨小瑞" in (path.parent / "keywords.txt").read_text(encoding="utf-8")
    assert load_config(path).devices[0].wake_words == ["嗨小瑞"]


def test_probe_reports_an_unreachable_host_instead_of_raising(wizard) -> None:
    client, _path = wizard
    response = client.post("/api/v1/setup/probe", json={"host": "no-such-host.invalid"})
    assert response.status_code == 200
    body = response.json()
    assert body["usable"] is False
    assert body["ssh"]["open"] is False
    assert body["hint"]


def test_device_needs_a_config_file_on_disk(tmp_path) -> None:
    client = TestClient(create_app(Config()))
    response = client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    assert response.status_code == 409
