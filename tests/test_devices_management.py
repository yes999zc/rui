"""P3.4 device management: status, enable/disable, token rotation, port conflicts.

These tests pin the day-after semantics the console now exposes:

* the status endpoint is evidence-only and never carries a secret value;
* disabling the last enabled speaker stops the plane, enabling it starts it again;
* a rotated token is written once, returned once, and masked everywhere else;
* two enabled inbound devices cannot share a listen port (null means the default), and
  the console is told explicitly that 0.3 drives only one of them.
"""
from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient

from rui import secrets
from rui.config import Config, DeviceConfig, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN_ENV = "RUI_DEVICE_TOKEN"


class _Plane:
    """The minimum supervisor surface ``_plane_snapshot`` and a restart use."""

    def __init__(self, connected: bool = False, device_id: str = "living") -> None:
        self.connected = connected
        self.device_id = device_id
        self.cfg = None
        self.error = ""
        self.stopped = 0
        self.started = 0

    def status(self) -> dict:
        return {
            "running": True,
            "device_id": self.device_id,
            "error": self.error,
            "metrics": {"speaker_connected": self.connected,
                        "speaker_rejected": 0},
        }

    async def stop(self) -> None:
        self.stopped += 1

    async def start(self) -> None:
        self.started += 1

    def watch(self) -> None:
        return None


@pytest.fixture()
def console(tmp_path, monkeypatch):
    """Build a console on a real config file; devices are supplied per test."""
    for name in (TOKEN_ENV, "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app_module, "_probe_host", _fake_probe)

    created: list[TestClient] = []

    def build(devices: list[DeviceConfig] | None = None, *,
              auth_token: str | None = None) -> tuple[TestClient, object]:
        options = {"web": {"auth_token": auth_token}} if auth_token else {}
        cfg = Config(
            devices=devices or [DeviceConfig(
                id="living", adapter="xiaoai", host="speaker.local", room="客厅")],
            **options,
        )
        cfg.system.data_dir = str(tmp_path / "data")
        cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, cfg)
        client = TestClient(create_app(cfg, config_path=path))
        created.append(client)
        return client, path

    return build


async def _fake_probe(host: str) -> dict:
    return {"host": host, "usable": True,
            "ssh": {"port_open": True, "banner": True}, "hint": ""}


def test_status_shape_is_evidence_only(console, monkeypatch) -> None:
    token = "status-token-never-echoed"
    monkeypatch.setenv(TOKEN_ENV, token)
    client, _path = console()
    client.app.state.audio = _Plane(connected=True)

    response = client.get("/api/v1/devices/living/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {
        "id", "enabled", "ready", "token_configured", "token_env", "listen_port",
        "plane", "capabilities", "setup_state", "errors",
    } <= set(body)
    assert body["id"] == "living"
    assert body["enabled"] is True
    assert body["ready"] is True
    assert body["token_configured"] is True
    assert body["token_env"] == TOKEN_ENV
    assert body["listen_port"] == 4412
    assert set(body["plane"]) == {"speaker_connected", "last_seen", "audio_plane"}
    assert body["plane"]["speaker_connected"] is True
    assert body["plane"]["audio_plane"] is True
    assert {"reference", "playback"} <= set(body["capabilities"])
    assert {"configured", "wake_words", "last_test_ok", "last_test_at"} <= set(
        body["setup_state"])
    assert body["errors"] == []
    # The token is reported as configured, never by value.
    assert token not in response.text
    assert client.get("/api/v1/devices/ghost/status").status_code == 404


def test_status_without_token_explains_why_it_is_not_ready(console) -> None:
    client, _path = console()
    client.app.state.audio = _Plane(connected=False)
    body = client.get("/api/v1/devices/living/status").json()
    assert body["ready"] is False
    assert body["token_configured"] is False
    assert "token_missing" in body["errors"]
    assert "not_connected" not in body["errors"]       # no token is the primary reason


def test_disable_last_device_stops_the_plane_and_enable_starts_it(console) -> None:
    client, path = console()
    plane = _Plane(connected=True)
    client.app.state.audio = plane

    disabled = client.post("/api/v1/devices/living/enabled", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["changed"] is True
    assert disabled.json()["device"]["enabled"] is False
    assert disabled.json()["live"]["status"] == "disabled"
    assert plane.stopped == 1
    assert plane.started == 0
    assert load_config(path).devices[0].enabled is False

    summary = next(item for item in client.get("/api/v1/devices").json()
                   if item["id"] == "living")
    assert summary["enabled"] is False
    assert summary["status"] == "disabled"

    status = client.get("/api/v1/devices/living/status").json()
    assert status["enabled"] is False and status["ready"] is False
    assert "device_disabled" in status["errors"]

    enabled = client.post("/api/v1/devices/living/enabled", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["device"]["enabled"] is True
    assert plane.started == 1
    assert load_config(path).devices[0].enabled is True


def test_enabled_body_must_be_a_boolean(console) -> None:
    client, _path = console()
    for payload in ({}, {"enabled": "yes"}, {"enabled": 1}, {"enabled": None}):
        response = client.post("/api/v1/devices/living/enabled", json=payload)
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["detail"]["code"] == "invalid_values"


def test_token_rotation_returns_once_and_is_masked_afterwards(console, caplog) -> None:
    client, _path = console()
    old = "old-device-token"
    secrets.put(TOKEN_ENV, old)

    with caplog.at_level("INFO"):
        response = client.post("/api/v1/devices/living/token/rotate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"token", "ws_url"}
    token = body["token"]
    assert token and token != old
    assert body["ws_url"] == f"ws://testserver:4412/?token={token}"
    assert secrets.get(TOKEN_ENV) == token
    secrets_file = _path.parent / "data" / "secrets.yaml"
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    # The audit line names the env var, never the value.
    assert token not in caplog.text

    # Subsequent reads are masked: presence only, and the status/list bodies stay clean.
    for href in ("/api/v1/devices", "/api/v1/devices/living",
                 "/api/v1/devices/living/status"):
        text = client.get(href).text
        assert token not in text
    status = client.get("/api/v1/devices/living/status").json()
    assert status["token_configured"] is True

    # A second rotation inside the cooldown is refused; the first token stays valid.
    again = client.post("/api/v1/devices/living/token/rotate")
    assert again.status_code == 429, again.text
    assert again.json()["detail"]["code"] == "rate_limited"
    assert again.json()["detail"]["details"]["retry_after_s"] > 0
    assert "Retry-After" in again.headers
    assert secrets.get(TOKEN_ENV) == token


def test_token_rotation_refuses_an_environment_override(console, monkeypatch) -> None:
    client, _path = console()
    monkeypatch.setenv(TOKEN_ENV, "from-the-environment")
    response = client.post("/api/v1/devices/living/token/rotate")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "token_from_environment"
    # Nothing was written: the env value remains the effective one.
    assert secrets.get(TOKEN_ENV) == "from-the-environment"


def test_port_conflict_on_enable_and_put_and_setup_upsert(console) -> None:
    devices = [
        DeviceConfig(id="living", adapter="xiaoai", host="speaker-a.local"),
        DeviceConfig(id="kitchen", adapter="xiaoai", host="speaker-b.local",
                     listen_port=4412, enabled=False),
    ]
    client, path = console(devices)
    before = path.read_bytes()

    # Enabling the second device would share the default 4412 with `living`.
    enable = client.post("/api/v1/devices/kitchen/enabled", json={"enabled": True})
    assert enable.status_code == 422, enable.text
    detail = enable.json()["detail"]
    assert detail["code"] == "port_conflict"
    assert detail["details"]["port"] == 4412
    assert set(detail["details"]["device_ids"]) == {"living", "kitchen"}
    assert path.read_bytes() == before

    # A PUT that introduces the collision is rejected too.
    put = client.put("/api/v1/devices/kitchen",
                     json={"enabled": True, "listen_port": None})
    assert put.status_code == 422, put.text
    assert put.json()["detail"]["code"] == "port_conflict"
    assert path.read_bytes() == before

    # The wizard's device.upsert is the create path; it must reject as well.
    upsert = client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"id": "study", "host": "speaker-c.local",
                    "wake_words": ["你好小瑞"]},
    })
    assert upsert.status_code == 422, upsert.text
    assert upsert.json()["detail"]["code"] == "port_conflict"
    assert path.read_bytes() == before

    # A different explicit port is accepted, and null/default semantics are preserved.
    ok = client.post("/api/v1/devices/kitchen/enabled", json={"enabled": True})
    assert ok.status_code == 422                 # still 4412, still a conflict
    moved = client.put("/api/v1/devices/kitchen", json={"listen_port": 4413})
    assert moved.status_code == 200, moved.text
    saved = {device.id: device for device in load_config(path).devices}
    assert saved["kitchen"].listen_port == 4413
    enabled = client.post("/api/v1/devices/kitchen/enabled", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text


def test_two_enabled_inbound_devices_produce_an_explicit_warning(console) -> None:
    devices = [
        DeviceConfig(id="living", adapter="xiaoai", host="speaker-a.local",
                     listen_port=4412),
        DeviceConfig(id="kitchen", adapter="xiaoai", host="speaker-b.local",
                     listen_port=4413),
    ]
    client, _path = console(devices)
    rows = client.get("/api/v1/devices").json()
    assert {row["id"] for row in rows} == {"living", "kitchen"}
    assert all(row["enabled"] for row in rows)
    assert [row["id"] for row in rows if row["active"]] == ["living"]
    warnings = rows[0]["warnings"]
    assert warnings and warnings[0]["code"] == "multiple_enabled_inbound"
    details = warnings[0]["details"]
    assert details["active_device"] == "living"
    assert details["enabled_inbound"] == ["living", "kitchen"]
    assert details["ignored_devices"] == ["kitchen"]
    assert details["max_active_inbound"] == 1
    assert rows[1]["warnings"] == warnings
    status = client.get("/api/v1/devices/kitchen/status").json()
    assert status["warnings"][0]["code"] == "multiple_enabled_inbound"

    # Disabling one of them clears the warning rather than pretending forever.
    assert client.post("/api/v1/devices/kitchen/enabled",
                       json={"enabled": False}).status_code == 200
    assert client.get("/api/v1/devices").json()[0]["warnings"] == []


def test_new_device_endpoints_require_the_console_token(console) -> None:
    client, _path = console(auth_token="tok")
    for method, href in (
        ("get", "/api/v1/devices/living/status"),
        ("post", "/api/v1/devices/living/enabled"),
        ("post", "/api/v1/devices/living/token/rotate"),
    ):
        assert getattr(client, method)(href).status_code == 401
    headers = {"X-Auth-Token": "tok"}
    assert client.get("/api/v1/devices/living/status",
                      headers=headers).status_code == 200
    assert client.post("/api/v1/devices/living/enabled", json={"enabled": False},
                       headers=headers).status_code == 200
