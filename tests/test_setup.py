"""C-UI P4: the install wizard's backend.

A stranger's install has to become a talking speaker without editing YAML: the
wizard needs to know what is still missing, store keys write-only, write the first
device, regenerate the wake words, and probe the speaker's host. Each test below is
one of those promises; "it renders" is not one of them.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rui import secrets as secrets_module
from rui.config import Config, DeviceConfig, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import DEVICE_DEFAULT_LISTEN_PORT, create_app


@pytest.fixture()
def wizard(tmp_path, monkeypatch):
    """A console with a config file on disk and no keys in the environment."""
    for name in ("RUI_LLM_KEY", "RUI_ASR_KEY", "RUI_TTS_KEY",
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
        "RUI_LLM_KEY", "RUI_ASR_KEY", "RUI_TTS_KEY",}
    assert body["complete"] is False
    assert [step["id"] for step in body["steps"]] == [
        "keys", "models", "device", "wake", "verify"]
    assert body["config_exists"] is True
    assert body["device"] is None


def test_keys_are_stored_but_never_echoed(wizard) -> None:
    client, path = wizard
    response = client.post("/api/v1/setup/keys", json={"values": {
        "RUI_LLM_KEY": "sk-test-should-not-echo",
        "RUI_ASR_KEY": "sk-test-2",
        "RUI_TTS_KEY": "sk-test-3",
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


def test_key_values_must_be_strings(wizard) -> None:
    client, _path = wizard
    for values in ({"RUI_LLM_KEY": None}, {"RUI_TTS_KEY": 123}):
        response = client.post("/api/v1/setup/keys", json={"values": values})
        assert response.status_code == 422, response.text


def test_host_rejects_urls_and_normalises_bracketed_ipv6(wizard) -> None:
    client, path = wizard
    for bad in ("http://127.0.0.1", "-bad-", "has space"):
        assert client.post("/api/v1/setup/device", json={"host": bad}).status_code == 422, bad
    assert client.post("/api/v1/setup/device",
                       json={"host": "[::1]", "id": "Living"}).status_code == 200
    device = load_config(path).devices[0]
    assert device.host == "::1"
    assert device.id == "living"


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
    from rui.config import Config
    from rui.web.api.app import create_app

    for name in ("RUI_LLM_KEY", "RUI_ASR_KEY", "RUI_TTS_KEY"):
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


def test_builtin_endpoint_providers_do_not_need_a_base_url(tmp_path, monkeypatch) -> None:
    """A volc_tts/volc_asr/xfyun provider has the vendor endpoint built in.

    Requiring a non-empty base_url marked them permanently unusable: the wizard asked
    for keys forever and a fresh console stayed on /setup (found 2026-10-23 17:46).
    """
    from rui.config.schema import ProviderConfig

    monkeypatch.setenv("VOLC_API_KEY", "test-key")
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.providers.catalog["volc-tts"] = ProviderConfig(
        kind="volc_tts", api_key_env="VOLC_API_KEY", model="seed-tts-2.0")
    cfg.providers.tts.chain = ["volc-tts"]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    body = TestClient(create_app(cfg, config_path=path)).get("/api/v1/setup").json()
    keys = body["steps"][0]
    assert keys["detail"]["stages"]["tts"] is True
    assert keys["detail"]["missing_by_stage"].get("tts", []) == []


# ------------------------------------------------ plane restart honesty (P1-6)

class _RestartFailingPlane:
    """Stands in for AudioPlaneSupervisor: the restart never comes back."""

    def __init__(self) -> None:
        self.cfg = None
        self.error = ""
        self.stops = 0
        self.starts = 0
        self.watches = 0

    async def stop(self) -> None:
        self.stops += 1

    async def start(self) -> None:
        self.starts += 1
        raise RuntimeError("no device this build can drive")

    def watch(self) -> None:
        self.watches += 1


class _RestartOkPlane(_RestartFailingPlane):
    async def start(self) -> None:
        self.starts += 1


def test_device_step_is_not_done_when_the_plane_restart_fails(wizard) -> None:
    client, path = wizard
    client.app.state.audio = _RestartFailingPlane()
    response = client.post("/api/v1/setup/device", json={
        "host": "speaker.local", "room": "客厅", "wake_words": ["你好小瑞"]})
    assert response.status_code == 200, response.text
    body = response.json()
    step = next(item for item in body["steps"] if item["id"] == "device")
    # The config was saved, but the room is deaf: not "done", and never silent about it.
    assert step["done"] is False
    assert "RuntimeError" in step["detail"]["plane_error"]
    assert "RuntimeError" in body["plane_error"]
    assert body["complete"] is False
    assert [device.id for device in load_config(path).devices] == ["living"]
    plane = client.app.state.audio
    assert plane.starts == 1 and plane.stops == 1 and plane.watches == 1
    # GET /api/v1/setup tells the same story on a reload.
    state = client.get("/api/v1/setup").json()
    assert state["plane_error"] == body["plane_error"]
    assert next(item for item in state["steps"]
                if item["id"] == "device")["detail"]["plane_error"]


def test_a_successful_restart_clears_the_plane_error(wizard) -> None:
    client, _path = wizard
    client.app.state.audio = _RestartFailingPlane()
    client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    assert client.get("/api/v1/setup").json()["plane_error"]

    client.app.state.audio = _RestartOkPlane()
    response = client.post("/api/v1/setup/wake", json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plane_error"] == ""
    step = next(item for item in body["steps"] if item["id"] == "device")
    assert step["done"] is True
    assert step["detail"]["plane_error"] == ""


def test_wake_word_save_surfaces_a_failed_plane_restart(wizard) -> None:
    client, _path = wizard
    client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    client.app.state.audio = _RestartFailingPlane()
    response = client.post("/api/v1/setup/wake", json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "RuntimeError" in body["plane_error"]
    step = next(item for item in body["steps"] if item["id"] == "device")
    assert step["done"] is False


def test_device_admin_endpoints_surface_plane_errors(wizard) -> None:
    client, _path = wizard
    client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    client.app.state.audio = _RestartFailingPlane()

    response = client.put("/api/v1/devices/living", json={"room": "书房"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "RuntimeError" in body["plane_error"]
    assert body["device"]["room"] == "书房"
    detail = client.get("/api/v1/devices/living").json()
    assert detail["plane_error"] == body["plane_error"]

    removed = client.delete("/api/v1/devices/living").json()
    # The last speaker is a clean stop, not a failed restart: an empty device list has
    # nothing to drive, so the console must not report a restart error for it.
    assert removed["plane_error"] == ""
    assert removed["devices"] == []

    # A working plane clears the sentence again on the next write.
    client.app.state.audio = _RestartOkPlane()
    client.post("/api/v1/setup/device", json={"host": "speaker.local"})
    response = client.put("/api/v1/devices/living", json={"room": "主卧"})
    assert response.status_code == 200, response.text
    assert response.json()["plane_error"] == ""


# ------------------------------------------------ cold-install connect info

@pytest.fixture()
def connect(tmp_path, monkeypatch):
    """One configured inbound speaker, no token in the environment."""
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [DeviceConfig(id="living", adapter="xiaoai", host="192.0.2.10",
                                room="客厅", listen_port=4412)]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), cfg, path


def test_connect_info_returns_the_token_and_redacts_it_elsewhere(connect, monkeypatch,
                                                                 caplog) -> None:
    client, _cfg, _path = connect
    token = "tok-abc-123"
    monkeypatch.setenv("RUI_DEVICE_TOKEN", token)
    with caplog.at_level("INFO"):
        response = client.get("/api/v1/devices/living/connect")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"device_id", "room", "host", "ws_url", "token", "token_env",
                         "configured", "allow_no_token", "host_source", "hint"}
    assert body["host_source"] == "request"
    assert body["device_id"] == "living"
    assert body["room"] == "客厅"
    assert body["host"] == "192.0.2.10"          # the speaker's address, not the console's
    assert body["token"] == token
    assert body["token_env"] == "RUI_DEVICE_TOKEN"
    assert body["configured"] is True
    assert body["allow_no_token"] is False
    assert body["ws_url"] == f"ws://testserver:4412/?token={token}"
    assert body["hint"]

    # Masking stays everywhere else: /api/v1/config reports presence, never the value.
    config_response = client.get("/api/v1/config")
    assert token not in config_response.text
    assert config_response.json()["devices"][0]["has_device_token"] is True
    # ...and the endpoint itself writes only the device id/booleans to the log.
    assert token not in caplog.text


def test_connect_info_missing_token_is_not_configured(connect) -> None:
    client, _cfg, _path = connect
    body = client.get("/api/v1/devices/living/connect").json()
    assert body["token"] == ""
    assert body["configured"] is False
    assert body["allow_no_token"] is False
    assert body["ws_url"] == "ws://testserver:4412/?token="
    assert "RUI_DEVICE_TOKEN" in body["hint"]
    assert client.get("/api/v1/config").json()["devices"][0]["has_device_token"] is False


def test_connect_info_allow_no_token_is_configured_with_a_warning(connect) -> None:
    _client, cfg, path = connect
    updated = cfg.model_copy(update={"devices": [
        cfg.devices[0].model_copy(update={"allow_no_token": True})]})
    save_config(path, updated)
    client = TestClient(create_app(updated, config_path=path))
    body = client.get("/api/v1/devices/living/connect").json()
    assert body["token"] == ""
    assert body["allow_no_token"] is True
    assert body["configured"] is True      # a tokenless listener is deliberate
    assert body["ws_url"].endswith("?token=")
    assert "allow_no_token" in body["hint"] and "麦克风" in body["hint"]


def test_connect_info_swaps_the_console_port_and_keeps_proxy_hosts(connect,
                                                                  monkeypatch) -> None:
    _client, cfg, path = connect
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "tok-xyz")
    for listen_port, expected in ((5000, "5000"), (None, str(DEVICE_DEFAULT_LISTEN_PORT))):
        updated = cfg.model_copy(update={"devices": [
            cfg.devices[0].model_copy(update={"listen_port": listen_port})]})
        save_config(path, updated)
        client = TestClient(create_app(updated, config_path=path))
        proxied = client.get("/api/v1/devices/living/connect", headers={
            "host": "nas.local:8091",
            "x-forwarded-host": "he.example.ts.net",
            "x-forwarded-proto": "https",
        }).json()
        assert proxied["ws_url"] == f"wss://he.example.ts.net:{expected}/?token=tok-xyz"
        plain = client.get("/api/v1/devices/living/connect",
                           headers={"host": "nas.local:8091"}).json()
        assert plain["ws_url"] == f"ws://nas.local:{expected}/?token=tok-xyz"


def test_connect_info_url_quotes_the_token(connect, monkeypatch) -> None:
    client, _cfg, _path = connect
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "a b&c")
    body = client.get("/api/v1/devices/living/connect").json()
    assert body["token"] == "a b&c"                  # raw value in the JSON field
    assert body["ws_url"].endswith("?token=a%20b%26c")


def test_connect_info_reads_secrets_yaml_and_env_wins(connect, monkeypatch) -> None:
    client, _cfg, _path = connect
    secrets_module.put("RUI_DEVICE_TOKEN", "file-token")
    assert client.get("/api/v1/devices/living/connect").json()["token"] == "file-token"
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "env-token")
    assert client.get("/api/v1/devices/living/connect").json()["token"] == "env-token"


def test_connect_info_needs_auth_when_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.web.auth_token = "console-token"
    cfg.devices = [DeviceConfig(id="living", adapter="xiaoai", host="192.0.2.10")]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    client = TestClient(create_app(cfg, config_path=path))
    assert client.get("/api/v1/devices/living/connect").status_code == 401
    response = client.get("/api/v1/devices/living/connect",
                          headers={"X-Auth-Token": "console-token"})
    assert response.status_code == 200
    assert response.json()["token"] == ""


def test_connect_info_unknown_device_is_404(connect) -> None:
    client, _cfg, _path = connect
    assert client.get("/api/v1/devices/ghost/connect").status_code == 404


def test_the_connect_info_default_port_matches_the_adapter() -> None:
    from rui.device.xiaoai import DEFAULT_LISTEN_PORT

    assert app_module.DEVICE_DEFAULT_LISTEN_PORT == DEFAULT_LISTEN_PORT == 4412

