"""P2.4 backend: device management for the console.

The device API must expose the editable ``params`` plus the plug they resolve to,
validate writes against the manifest without ever echoing a secret, and test a device
without writing config or playing audio. Deleting the last speaker must leave the
control plane alive and the wizard able to say "no device".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rui import secrets
from rui.config import Config, DeviceConfig, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN_ENV = "RUI_DEVICE_TOKEN"


class _Plane:
    """The minimum supervisor surface ``_plane_snapshot`` and a restart use."""

    def __init__(self, connected: bool = False) -> None:
        self.connected = connected
        self.cfg = None
        self.error = ""
        self.stopped = 0
        self.started = 0

    def status(self) -> dict:
        return {
            "running": True,
            "device_id": "living",
            "error": self.error,
            "metrics": {"speaker_connected": self.connected},
        }

    async def stop(self) -> None:
        self.stopped += 1

    async def start(self) -> None:
        self.started += 1

    def watch(self) -> None:
        return None


def _demo_plug(tmp_path) -> None:
    """A user plug with every param type the PUT validator must understand."""
    root = tmp_path / "data" / "plugs" / "demo"
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        "schema: rui-plugin/v0\n"
        "id: demo\n"
        "name: Demo Speaker Plug\n"
        "version: 1.0.0\n"
        'rui_compat: ">=0.0.1 <999.0.0"\n'
        "license: MIT\n"
        "maintainer: Test <test@example.invalid>\n"
        "trust: builtin\n"
        "devices:\n"
        "  - devices/demo.yaml\n",
        encoding="utf-8",
    )
    (root / "devices" / "demo.yaml").write_text(
        "schema: rui-device/v0\n"
        "id: demo-speaker\n"
        "name: Demo Speaker\n"
        "vendor: Demo Inc.\n"
        "class: A\n"
        "transport: outbound\n"
        "protocol: {kind: ws, version: '1'}\n"
        "capabilities: [playback, volume]\n"
        "params:\n"
        "  - {key: host, label: Address, type: string, required: true}\n"
        "  - {key: volume_max, label: Max volume, type: int, required: true}\n"
        "  - {key: eq, label: EQ, type: choice, options: [flat, bass], default: flat}\n"
        "  - {key: api_secret, label: Device secret, type: secret}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def console(tmp_path, monkeypatch):
    """Build a console on a real config file; ``params`` land on the single device."""
    for name in (TOKEN_ENV, "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    created: list[TestClient] = []

    def build(*, adapter: str = "xiaoai", params: dict | None = None,
              auth_token: str | None = None) -> tuple[TestClient, object]:
        options = {"web": {"auth_token": auth_token}} if auth_token else {}
        cfg = Config(
            devices=[DeviceConfig(
                id="living", adapter=adapter, host="speaker.local", room="客厅",
                params=dict(params or {}))],
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


def test_detail_and_list_include_params_plug_and_capabilities(console) -> None:
    client, _path = console(params={"volume_max": 80, "mic_gain_db": 12})
    detail = client.get("/api/v1/devices/living").json()
    assert detail["plug_id"] == "xiaoai-lx06"
    assert detail["params"] == {"volume_max": 80, "mic_gain_db": 12}
    assert detail["device"]["params"] == detail["params"]
    # A configured device still reports the manifest's promised capabilities.
    assert "reference" in detail["live"]["capabilities"]

    summary = next(item for item in client.get("/api/v1/devices").json()
                   if item["id"] == "living")
    assert summary["plug_id"] == "xiaoai-lx06"
    assert summary["params"] == {"volume_max": 80, "mic_gain_db": 12}
    assert {"reference", "playback"} <= set(summary["capabilities"])
    assert summary["status"] in {"unknown", "ready", "disconnected"}


def test_put_params_round_trip_and_legacy_payload(console) -> None:
    client, path = console(params={"volume_max": 80, "plug_id": "xiaoai-lx06"})
    response = client.put("/api/v1/devices/living", json={"params": {"volume_max": 95}})
    assert response.status_code == 200, response.text
    assert response.json()["params"] == {"plug_id": "xiaoai-lx06", "volume_max": 95}
    assert load_config(path).devices[0].params == {
        "plug_id": "xiaoai-lx06", "volume_max": 95}

    # A pre-params payload keeps every old field and preserves unmentioned params.
    legacy = client.put("/api/v1/devices/living", json={
        "host": "new.local", "room": "卧室", "wake_words": ["小瑞"],
        "volume": 30, "enabled": True, "listen_port": 4413,
    })
    assert legacy.status_code == 200, legacy.text
    saved = load_config(path).devices[0]
    assert (saved.host, saved.room, saved.volume) == ("new.local", "卧室", 30)
    assert saved.listen_port == 4413
    assert saved.params == {"plug_id": "xiaoai-lx06", "volume_max": 95}


def test_put_validates_manifest_types_choices_and_required(console, tmp_path) -> None:
    _demo_plug(tmp_path)
    client, path = console(params={"plug_id": "demo", "volume_max": 50})

    wrong_type = client.put("/api/v1/devices/living", json={"params": {"volume_max": "loud"}})
    assert wrong_type.status_code == 422
    assert wrong_type.json()["detail"]["code"] == "invalid_values"
    assert wrong_type.json()["detail"]["details"]["param"] == "volume_max"

    wrong_choice = client.put("/api/v1/devices/living", json={"params": {"eq": "metal"}})
    assert wrong_choice.status_code == 422
    assert wrong_choice.json()["detail"]["details"]["options"] == ["flat", "bass"]

    # Removing a required value is a 422, and it must not reach the disk.
    before = path.read_bytes()
    missing = client.put("/api/v1/devices/living", json={"params": {"volume_max": None}})
    assert missing.status_code == 422
    assert missing.json()["detail"]["details"]["missing"] == ["volume_max"]
    assert path.read_bytes() == before

    valid = client.put("/api/v1/devices/living", json={
        "params": {"volume_max": 70, "eq": "bass"}})
    assert valid.status_code == 200, valid.text
    assert valid.json()["params"] == {
        "plug_id": "demo", "volume_max": 70, "eq": "bass"}


def test_secret_param_is_write_only(console, tmp_path) -> None:
    _demo_plug(tmp_path)
    client, path = console(params={"plug_id": "demo", "volume_max": 50})
    value = "s3cr3t-value-never-echoed"
    response = client.put("/api/v1/devices/living", json={
        "params": {"api_secret": value, "volume_max": 60}})
    assert response.status_code == 200, response.text
    assert value not in response.text
    assert "api_secret" not in response.json()["params"]
    assert "api_secret" not in load_config(path).devices[0].params
    assert secrets.resolve("API_SECRET").value == value

    # The read endpoints must not start echoing it either.
    assert value not in client.get("/api/v1/devices/living").text
    assert value not in client.get("/api/v1/devices").text


def test_unknown_plug_id_is_422_with_details(console) -> None:
    client, path = console()
    before = path.read_bytes()
    response = client.put("/api/v1/devices/living", json={"params": {"plug_id": "nope"}})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "unknown_plug"
    assert detail["details"]["plug_id"] == "nope"
    assert "xiaoai-lx06" in detail["details"]["available"]
    assert path.read_bytes() == before


def test_connection_test_reports_checks_and_writes_nothing(console, monkeypatch) -> None:
    client, path = console()
    monkeypatch.setenv(TOKEN_ENV, "device-token-value")

    async def fake_probe(host: str) -> dict:
        return {"host": host, "usable": True,
                "ssh": {"port_open": True, "banner": True}, "hint": ""}

    monkeypatch.setattr(app_module, "_probe_host", fake_probe)
    client.app.state.audio = _Plane(connected=True)
    before = path.read_bytes()

    body = client.post("/api/v1/devices/living/test").json()
    assert body["ok"] is True
    assert [check["name"] for check in body["checks"]] == [
        "host", "plane", "token", "capabilities"]
    assert all(isinstance(check["ok"], bool) and isinstance(check["detail"], dict)
               for check in body["checks"])
    assert body["error"] == ""
    # The token is reported as configured, never by value.
    assert "device-token-value" not in str(body)
    assert body["checks"][2]["detail"]["configured"] is True
    assert path.read_bytes() == before


def test_connection_test_reports_failed_checks(console, monkeypatch) -> None:
    client, _path = console()

    async def fake_probe(host: str) -> dict:
        return {"host": host, "usable": False,
                "ssh": {"port_open": False}, "hint": "no route"}

    monkeypatch.setattr(app_module, "_probe_host", fake_probe)
    client.app.state.audio = _Plane(connected=False)
    body = client.post("/api/v1/devices/living/test").json()
    assert body["ok"] is False
    failed = {check["name"] for check in body["checks"] if not check["ok"]}
    # The built-in speaker is inbound: it dials us, so an SSH probe on :22 is not the
    # gate (a Dot 2 runs no SSH at all). The live link and the token are.
    assert {"plane", "token"} <= failed
    by_name = {check["name"]: check for check in body["checks"]}
    assert by_name["host"]["status"] == "not_applicable"
    assert by_name["host"]["ok"] is True
    assert by_name["host"]["detail"]["transport"] == "inbound"
    assert "failed checks" in body["error"]
    assert client.post("/api/v1/devices/missing/test").status_code == 404


def test_put_and_delete_require_the_console_token(console) -> None:
    client, _path = console(auth_token="tok")
    assert client.get("/api/v1/devices/living").status_code == 401
    assert client.put("/api/v1/devices/living", json={"room": "x"}).status_code == 401
    assert client.post("/api/v1/devices/living/test").status_code == 401
    assert client.delete("/api/v1/devices/living").status_code == 401
    headers = {"X-Auth-Token": "tok"}
    assert client.get("/api/v1/devices/living", headers=headers).status_code == 200
    assert client.put("/api/v1/devices/living", json={"room": "x"},
                      headers=headers).status_code == 200


def test_delete_last_device_stops_the_plane_and_setup_reflects_it(console) -> None:
    client, path = console()
    plane = _Plane(connected=True)
    client.app.state.audio = plane

    response = client.delete("/api/v1/devices/living")
    assert response.status_code == 200, response.text
    assert response.json() == {"removed": "living", "devices": [], "plane_error": ""}
    assert plane.stopped == 1
    assert plane.started == 0
    assert load_config(path).devices == []
    assert client.get("/api/v1/devices").json() == []

    # The control plane survived and the wizard says there is no device to configure.
    setup = client.get("/api/v1/setup").json()
    assert setup["device"] is None
    assert setup["enabled_devices"] == 0
    assert setup["plane_error"] == ""
    assert setup["next_step"] != "done"
    state = client.get("/api/v1/setup/step").json()
    params_step = next(step for step in state["steps"] if step["id"] == "params")
    assert params_step["status"] == "pending"


def test_delete_last_enabled_device_with_a_disabled_leftover_stops_cleanly(console) -> None:
    client, path = console()
    current = load_config(path)
    disabled = DeviceConfig(id="spare", adapter="mock", host="127.0.0.1", enabled=False)
    save_config(path, current.model_copy(update={"devices": [*current.devices, disabled]}))
    plane = _Plane(connected=True)
    client.app.state.audio = plane

    response = client.delete("/api/v1/devices/living")
    assert response.status_code == 200, response.text
    assert response.json()["devices"] == ["spare"]
    assert response.json()["plane_error"] == ""
    assert plane.stopped == 1
    assert plane.started == 0
    assert [device.id for device in load_config(path).devices] == ["spare"]
