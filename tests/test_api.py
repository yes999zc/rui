from __future__ import annotations

from fastapi.testclient import TestClient

from house_ear.config import Config, DeviceConfig
from house_ear.web.api.app import create_app


def client(cfg: Config | None = None) -> TestClient:
    return TestClient(create_app(cfg or Config()))


def test_health() -> None:
    body = client().get("/health").json()
    assert body["status"] == "ok"


def test_mock_device_is_probed() -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock", host="127.0.0.1", room="客厅")])
    devices = client(cfg).get("/api/v1/devices").json()
    assert devices[0]["room"] == "客厅"
    assert devices[0]["missing_for_barge_in"] == []


def test_secrets_are_not_in_payload() -> None:
    cfg = Config()
    cfg.providers.llm.api_key_env = "SOME_KEY"
    payload = client(cfg).get("/api/v1/config").json()
    assert "api_key" not in payload["providers"]["llm"]
    assert payload["providers"]["llm"]["has_key"] is False


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
