from __future__ import annotations

from fastapi.testclient import TestClient

from house_ear.config import Config, DeviceConfig
from house_ear.web.api.app import create_app


def client(cfg: Config | None = None) -> TestClient:
    return TestClient(create_app(cfg or Config()))


def test_health() -> None:
    body = client().get("/health").json()
    assert body["status"] == "ok"
    assert body["config_version"] == 2
    assert body["device_errors"] == 0


def test_mock_device_is_probed() -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock", host="127.0.0.1", room="客厅")])
    devices = client(cfg).get("/api/v1/devices").json()
    assert devices[0]["room"] == "客厅"
    assert devices[0]["status"] == "ready"
    assert devices[0]["missing_for_barge_in"] == []


def test_xiaoai_device_is_reported_as_an_inbound_speaker() -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="xiaoai", host="<LAN-IP>")])
    c = client(cfg)
    assert c.get("/health").json()["device_errors"] == 0
    device = c.get("/api/v1/devices").json()[0]
    assert device["status"] == "ready"
    assert device["transport"] == "inbound"
    assert "reference" in device["capabilities"]
    assert device["missing_for_barge_in"] == []


def test_an_adapter_that_cannot_be_built_is_reported_not_raised(monkeypatch) -> None:
    """A broken adapter must surface per device, never as a startup failure."""
    from house_ear.device import registry

    def boom(dev, cfg):
        raise registry.AdapterUnavailable("adapter exploded")

    monkeypatch.setattr(registry, "_build", boom)
    cfg = Config(devices=[DeviceConfig(id="living", adapter="xiaoai", host="<LAN-IP>")])
    c = client(cfg)
    assert c.get("/health").json()["device_errors"] == 1
    device = c.get("/api/v1/devices").json()[0]
    assert device["status"] == "unavailable"
    assert "exploded" in device["error"]


def test_secrets_are_not_in_payload() -> None:
    cfg = Config()
    cfg.providers.catalog["llm"].api_key_env = "SOME_KEY"
    payload = client(cfg).get("/api/v1/config").json()
    block = payload["providers"]["catalog"]["llm"]
    assert "api_key" not in block
    assert block["has_key"] is False


def test_present_key_is_reported_as_a_flag_only(monkeypatch) -> None:
    cfg = Config()
    cfg.providers.catalog["llm"].api_key_env = "HOUSE_EAR_TEST_KEY"
    monkeypatch.setenv("HOUSE_EAR_TEST_KEY", "sk-do-not-leak")
    payload = client(cfg).get("/api/v1/config").json()
    block = payload["providers"]["catalog"]["llm"]
    assert block["has_key"] is True
    assert "sk-do-not-leak" not in str(payload)


def test_device_token_is_reported_as_a_flag_only(monkeypatch) -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock", host="127.0.0.1")])
    monkeypatch.setenv("HOUSE_EAR_DEVICE_TOKEN", "dev-secret")
    payload = client(cfg).get("/api/v1/config").json()
    assert payload["devices"][0]["has_device_token"] is True
    assert "dev-secret" not in str(payload)


def test_auth_token_never_leaves_the_server() -> None:
    payload = client(Config(web={"auth_token": "s3cret"})).get(
        "/api/v1/config", headers={"X-Auth-Token": "s3cret"}
    ).json()
    assert "s3cret" not in str(payload)
    assert payload["web"]["auth_required"] is True


def test_auth_is_enforced_when_configured() -> None:
    cfg = Config(web={"auth_token": "s3cret"})
    c = client(cfg)
    assert c.get("/api/v1/config").status_code == 401
    assert c.get("/api/v1/config", headers={"X-Auth-Token": "s3cret"}).status_code == 200


def test_traces_are_empty_before_the_audio_plane_exists() -> None:
    body = client().get("/api/v1/traces").json()
    assert body["turns"] == []
