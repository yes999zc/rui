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
    assert set(body["plane"]) == {
        "speaker_connected", "last_seen", "audio_plane",
        "restarts", "error", "wake_candidates", "wake_admitted",
    }
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


# ------------------------------------------------- manifest runtime defaults (dot2)

def _yaml_text(*lines: str) -> str:
    return chr(10).join(lines) + chr(10)


def _dot2_plug(tmp_path) -> None:
    """An inbound plug carrying its own runtime port/token defaults (dot2 shape)."""
    root = tmp_path / "data" / "plugs" / "dot2-echomuse"
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        _yaml_text(
            "schema: rui-plugin/v0",
            "id: dot2-echomuse",
            "name: Dot2 test plug",
            "version: 1.0.0",
            "rui_compat: \">=0.0.1 <999.0.0\"",
            "license: MIT",
            "maintainer: Test <test@example.invalid>",
            "trust: trusted",
            "devices:",
            "  - devices/dot2.yaml",
        ),
        encoding="utf-8",
    )
    (root / "devices" / "dot2.yaml").write_text(
        _yaml_text(
            "schema: rui-device/v1",
            "id: dot2",
            "name: Echo Dot 2 (test)",
            "vendor: Test",
            "class: B",
            "transport: inbound",
            "protocol: {kind: wss, version: \"1\"}",
            "capabilities: [playback, volume, led]",
            "device_side:",
            "  artifact: firmware",
            "  name: Test device image",
            "  source: https://example.invalid/device",
            "  rollback: Restore the stock firmware.",
            "runtime: {mode: in-process, entrypoint: \"dot2_test.adapter:build\", "
            "listen_port: 8870, token_env: DOT2_EM_TOKEN}",
            "params:",
            "  - {key: host, label: Address, type: string, required: true}",
        ),
        encoding="utf-8",
    )


def test_dot2_style_plug_reports_effective_port_and_token_env(
    console, tmp_path, monkeypatch
) -> None:
    _dot2_plug(tmp_path)
    token = "dot2-token-never-echoed"
    monkeypatch.setenv("DOT2_EM_TOKEN", token)
    client, _path = console([
        DeviceConfig(id="dot2", adapter="dot2", host="dot2.local", room="客厅"),
        DeviceConfig(id="lx06", adapter="xiaoai", host="lx06.local", room="卧室"),
    ])
    client.app.state.audio = _Plane(connected=True)

    status = client.get("/api/v1/devices/dot2/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["listen_port"] == 8870
    assert body["token_env"] == "DOT2_EM_TOKEN"
    assert body["token_configured"] is True
    assert "token_missing" not in body["errors"]
    assert token not in status.text

    # The LX06 keeps the old defaults: nothing changes for the built-in plug.
    lx = client.get("/api/v1/devices/lx06/status").json()
    assert lx["listen_port"] == 4412
    assert lx["token_env"] == "RUI_DEVICE_TOKEN"

    rows = {row["id"]: row for row in client.get("/api/v1/devices").json()}
    assert rows["dot2"]["listen_port"] == 8870
    assert rows["dot2"]["token_env"] == "DOT2_EM_TOKEN"
    assert rows["lx06"]["listen_port"] == 4412

    detail = client.get("/api/v1/devices/dot2")
    assert detail.status_code == 200, detail.text
    assert detail.json()["listen_port"] == 8870
    assert detail.json()["token_env"] == "DOT2_EM_TOKEN"
    assert token not in detail.text

    # /connect is the one endpoint that returns the token; it must use the same env.
    connect = client.get("/api/v1/devices/dot2/connect")
    assert connect.status_code == 200, connect.text
    info = connect.json()
    assert info["token_env"] == "DOT2_EM_TOKEN"
    assert info["configured"] is True
    assert info["token"] == token
    assert ":8870/" in info["ws_url"]
    assert "token=" + token in info["ws_url"]


def test_dot2_and_lx06_do_not_false_conflict_on_the_default_port(
    console, tmp_path
) -> None:
    from rui.plugins import PlugCatalog
    from rui.web.api.device_rules import (
        effective_listen_port,
        effective_token_env,
        port_conflict,
    )

    _dot2_plug(tmp_path)
    data_dir = tmp_path / "data"
    dot2 = DeviceConfig(id="dot2", adapter="dot2", host="dot2.local")
    lx06 = DeviceConfig(id="lx06", adapter="xiaoai", host="lx06.local")
    catalog = PlugCatalog(data_dir)
    resolved_dot2 = catalog.resolve(adapter="dot2")

    assert effective_listen_port(dot2, resolved_dot2) == 8870
    assert effective_token_env(dot2, resolved_dot2) == "DOT2_EM_TOKEN"
    assert effective_listen_port(lx06, None) == 4412
    assert effective_token_env(lx06, None) == "RUI_DEVICE_TOKEN"
    # The LX06's 4412 is not a collision with the dot2 plug's 8870.
    assert port_conflict([dot2, lx06], data_dir) is None
    # Two devices on the same dot2 manifest do collide -- on the manifest port.
    dot2_b = DeviceConfig(id="dot2-b", adapter="dot2", host="dot2-b.local")
    conflict = port_conflict([dot2, dot2_b], data_dir)
    assert conflict is not None
    assert conflict["port"] == 8870
    assert set(conflict["device_ids"]) == {"dot2", "dot2-b"}

    # And the HTTP enable path accepts dot2 + LX06 together (warning, not 422).
    client, _path = console([
        DeviceConfig(id="dot2", adapter="dot2", host="dot2.local"),
        DeviceConfig(id="lx06", adapter="xiaoai", host="lx06.local", enabled=False),
    ])
    client.app.state.audio = _Plane(connected=True)
    enabled = client.post("/api/v1/devices/lx06/enabled", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    rows = client.get("/api/v1/devices").json()
    warning = next(
        warning
        for row in rows
        for warning in row["warnings"]
        if warning["code"] == "multiple_enabled_inbound"
    )
    assert warning["details"]["active_device"] in {"dot2", "lx06"}


def test_loader_cached_runtime_defaults_fill_in_when_the_catalog_cannot_resolve(
    monkeypatch,
) -> None:
    from rui.device.registry import RUNTIME_DEFAULTS
    from rui.web.api.device_rules import effective_listen_port, effective_token_env

    monkeypatch.setitem(
        RUNTIME_DEFAULTS, "dot2",
        {"listen_port": 8870, "token_env": "DOT2_EM_TOKEN"},
    )
    dot2 = DeviceConfig(id="dot2", adapter="dot2", host="dot2.local")
    # No resolved manifest in scope: the loader's cached defaults must still win over
    # the LX06 literals, exactly like build_adapters applies them.
    assert effective_listen_port(dot2, None) == 8870
    assert effective_token_env(dot2, None) == "DOT2_EM_TOKEN"


def test_dot2_style_plug_rotates_the_effective_token_env(
    console, tmp_path, monkeypatch
) -> None:
    _dot2_plug(tmp_path)
    monkeypatch.delenv("DOT2_EM_TOKEN", raising=False)
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    client, _path = console([
        DeviceConfig(id="dot2", adapter="dot2", host="dot2.local"),
    ])
    client.app.state.audio = _Plane(connected=True)

    response = client.post("/api/v1/devices/dot2/token/rotate")
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    # Rotation must store under the manifest runtime name, not the config default.
    assert secrets.get("DOT2_EM_TOKEN") == token
    assert secrets.resolve("RUI_DEVICE_TOKEN") is None
    ws_url = response.json()["ws_url"]
    assert ":8870/" in ws_url
    assert "token=" + token in ws_url

    status = client.get("/api/v1/devices/dot2/status").json()
    assert status["token_env"] == "DOT2_EM_TOKEN"
    assert status["token_configured"] is True
    assert token not in client.get("/api/v1/devices/dot2/status").text

    # The same env is what the next rotation cooldown report and the detail name.
    detail = client.get("/api/v1/devices/dot2").json()
    assert detail["token_env"] == "DOT2_EM_TOKEN"


# ------------------------------ satellite connection test (inbound, dot2-style plug)


def test_satellite_test_uses_the_live_link_instead_of_ssh(
    console, tmp_path, monkeypatch
) -> None:
    """A Dot 2 runs no SSH: a connected satellite must still test green (dot2 #7)."""
    _dot2_plug(tmp_path)
    monkeypatch.setattr(app_module, "DEVICE_TEST_WAIT_S", 0.0)
    monkeypatch.delenv("DOT2_EM_TOKEN", raising=False)
    client, _path = console([
        DeviceConfig(id="dot2", adapter="dot2", host="dot2.local", room="客厅"),
    ])
    client.app.state.audio = _Plane(connected=True, device_id="dot2")

    body = client.post("/api/v1/devices/dot2/test").json()
    assert body["ok"] is True, body
    assert body["transport"] == "inbound"
    assert body["listen_port"] == 8870
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["host"]["status"] == "not_applicable"
    assert checks["host"]["detail"]["listen_port"] == 8870
    # The token is not in rui's env/secrets store; the live link is the proof.
    assert checks["token"]["ok"] is True
    assert checks["token"]["detail"]["configured"] is False
    assert checks["token"]["detail"]["managed_by_adapter"] is True


def test_satellite_test_waits_for_a_device_that_is_still_dialling_in(
    console, tmp_path, monkeypatch
) -> None:
    """Saving a satellite restarts the plane; the test must not race the reconnect."""
    _dot2_plug(tmp_path)
    monkeypatch.setattr(app_module, "DEVICE_TEST_WAIT_S", 0.5)
    monkeypatch.delenv("DOT2_EM_TOKEN", raising=False)
    client, _path = console([
        DeviceConfig(id="dot2", adapter="dot2", host="dot2.local", room="客厅"),
    ])
    plane = _Plane(connected=False, device_id="dot2")
    base_status = plane.status
    calls = [0]

    def status_after_one_poll() -> dict:
        calls[0] += 1
        if calls[0] >= 2:
            plane.connected = True
        return base_status()

    plane.status = status_after_one_poll  # type: ignore[method-assign]
    client.app.state.audio = plane

    body = client.post("/api/v1/devices/dot2/test").json()
    assert body["waited_ms"] > 0
    assert body["ok"] is True, body
    assert body["connection_message"] == "connected"
