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
    assert set(body["steps"][0]["detail"]["missing"]) == {
        "HOUSE_EAR_LLM_KEY", "HOUSE_EAR_ASR_KEY", "HOUSE_EAR_TTS_KEY",}
    assert body["complete"] is False
    assert [step["id"] for step in body["steps"]] == [
        "keys", "models", "device", "wake", "verify"]
    assert body["config_exists"] is True
    assert body["device"] is None


def test_keys_are_stored_but_never_echoed(wizard) -> None:
    client, path = wizard
    response = client.post("/api/v1/setup/keys", json={"values": {
        "HOUSE_EAR_LLM_KEY": "sk-test-should-not-echo",
        "HOUSE_EAR_ASR_KEY": "sk-test-2",
        "HOUSE_EAR_TTS_KEY": "sk-test-3",
    }})
    assert response.status_code == 200
    assert "sk-test-should-not-echo" not in response.text
    keys_step = response.json()["steps"][0]
    assert keys_step["detail"]["missing"] == []
    # Keys are stored; the stages still need an endpoint (schema defaults have none),
    # which is exactly the "keys are not the whole story" the wizard must show.
    assert keys_step["done"] is False
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


def test_saving_the_device_keeps_fields_the_wizard_does_not_show(wizard) -> None:
    """An edit from the wizard must not drop listen_port / LED / volume."""
    client, path = wizard
    client.post("/api/v1/setup/device", json={"host": "speaker.local", "wake_words": ["你好小瑞"]})
    cfg = load_config(path)
    device = cfg.devices[0].model_copy(update={"listen_port": 4412, "volume": 70})
    save_config(path, cfg.model_copy(update={"devices": [device]}))
    client.post("/api/v1/setup/device", json={"host": "speaker.local", "wake_words": ["嗨小瑞"]})
    saved = load_config(path).devices[0]
    assert saved.host == "speaker.local"
    assert saved.listen_port == 4412
    assert saved.volume == 70


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
    assert body["ssh"]["port_open"] is False
    assert body["hint"]


def test_invalid_device_fields_answer_422_not_500(wizard) -> None:
    client, _path = wizard
    for payload in (
        {"host": "127.0.0.1", "id": "a/b"},                    # id must be alnum
        {"host": "127.0.0.1", "id": "x" * 40},                 # id max length
        {"host": "127.0.0.1", "wake_words": "你好小瑞"},        # must be a list
        {"host": "127.0.0.1", "wake_words": ["你好\n小瑞"]},   # newline breaks the keywords file
        {"host": "127.0.0.1", "wake_words": ["你好" * 8]},      # too long for the spotter
        {"host": "127.0.0.1", "wake_words": ["a", "b", "c", "d"]},  # at most 3
        {"host": "127.0.0.1", "room": "x" * 40},
        {"host": "has space"},
    ):
        response = client.post("/api/v1/setup/device", json=payload)
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["detail"]


def test_wake_endpoint_rejects_a_broken_word(wizard) -> None:
    client, _path = wizard
    client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    assert client.post("/api/v1/setup/wake", json={"wake_words": ["你好\n小瑞"]}).status_code == 422
    assert client.post("/api/v1/setup/wake", json={"wake_words": "你好"}).status_code == 422


def test_probe_requires_an_ssh_banner(monkeypatch, wizard) -> None:
    """A TCP connect is not a speaker: a VPN answers it for any address."""
    client, _path = wizard

    class FakeWriter:
        def close(self) -> None: ...
        async def wait_closed(self) -> None: ...

    class FakeReader:
        def __init__(self, line: bytes) -> None:
            self.line = line
        async def readline(self) -> bytes:
            return self.line

    async def fake_open(host: str, port: int):
        return FakeReader(b"SSH-2.0-dropbear_2020.81\r\n"), FakeWriter()

    monkeypatch.setattr("asyncio.open_connection", fake_open)
    body = client.post("/api/v1/setup/probe", json={"host": "speaker.local"}).json()
    assert body["usable"] is True
    assert body["ssh"]["banner"] is True

    async def fake_open_no_banner(host: str, port: int):
        return FakeReader(b""), FakeWriter()

    monkeypatch.setattr("asyncio.open_connection", fake_open_no_banner)
    body = client.post("/api/v1/setup/probe", json={"host": "speaker.local"}).json()
    assert body["usable"] is False
    assert body["ssh"]["port_open"] is True
    assert "banner" in body["hint"]


def test_the_wake_model_check_needs_all_four_files(tmp_path, monkeypatch) -> None:
    """tokens.txt alone is not a wake model: the spotter loads encoder/decoder/joiner too."""
    from house_ear.config import Config
    from house_ear.web.api.app import create_app

    for name in ("HOUSE_EAR_LLM_KEY", "HOUSE_EAR_ASR_KEY", "HOUSE_EAR_TTS_KEY"):
        monkeypatch.delenv(name, raising=False)
    model = tmp_path / "kws"
    model.mkdir()
    (model / "tokens.txt").write_text("x", encoding="utf-8")
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_model_dir = str(model)
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    client = TestClient(create_app(cfg))
    state = client.get("/api/v1/setup").json()
    assert state["steps"][1]["detail"]["kws"] is False
    for name in ("encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
                 "decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
                 "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx"):
        (model / name).write_bytes(b"fake")
    assert TestClient(create_app(cfg)).get("/api/v1/setup").json()["steps"][1]["detail"]["kws"] is True


def test_device_needs_a_config_file_on_disk(tmp_path) -> None:
    client = TestClient(create_app(Config()))
    response = client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    assert response.status_code == 409
