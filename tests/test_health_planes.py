"""Two speakers: `/health` must say which devices actually have a plane."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, save_config
from rui.doctor import run_doctor
from rui.web.api.app import create_app

TOKEN = "test-token"
AUTH = {"X-Auth-Token": TOKEN}


def _devices() -> list[DeviceConfig]:
    return [
        DeviceConfig(id="dot2", adapter="dot2", host="192.0.2.10", room="One",
                     listen_port=8871, token_env="DOT2_EM_TOKEN"),
        DeviceConfig(id="lx06", adapter="lx06", host="192.0.2.11", room="Two",
                     listen_port=8872, token_env="RUI_DEVICE_TOKEN"),
    ]


class _Sup:
    def __init__(self, device_id: str, running: bool = True, error: str = "") -> None:
        self.device_id = device_id
        self.running = running
        self.error = error

    def status(self) -> dict:
        return {"device_id": self.device_id, "running": self.running,
                "error": self.error or ("" if self.running else "not connected"), "metrics": {},
                "restarts": 0, "pid": 1, "wake_count": 0, "utterances": 0}


class _Fleet:
    def __init__(self, sups: dict[str, _Sup]) -> None:
        self._sups = sups

    def device_ids(self) -> list[str]:
        return list(self._sups)

    def supervisor(self, device_id: str) -> _Sup:
        return self._sups[device_id]

    def running(self, device_id: str) -> bool:
        return self._sups[device_id].running

    def status(self) -> dict:
        return {device_id: sup.status() for device_id, sup in self._sups.items()}


def _client(tmp_path: Path, *, multi: bool) -> tuple[TestClient, Config]:
    devices = _devices()
    config = Config(devices=devices, web={"auth_token": TOKEN},
                    multi_device={"enabled": multi, "max_planes": 2})
    config.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    app = create_app(config, config_path=path)
    if multi:
        app.state.audio_fleet = _Fleet({d.id: _Sup(d.id) for d in devices})
    else:
        app.state.audio = _Sup("dot2")
    return TestClient(app), config


def test_planes_lists_every_device_and_marks_the_unstarted_one(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, multi=False)
    body = client.get("/health", headers=AUTH).json()
    assert body["audio_plane"] is True  # old aggregate field unchanged
    rows = {row["device"]: row for row in body["planes"]}
    assert set(rows) == {"dot2", "lx06"}
    assert rows["dot2"]["state"] == "running"
    assert rows["lx06"]["state"] == "not_started"
    assert rows["lx06"]["reason"] == "multi_device_disabled"


def test_planes_reports_both_when_multi_device_is_enabled(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, multi=True)
    body = client.get("/health", headers=AUTH).json()
    assert body["audio_plane"] is True
    rows = {row["device"]: row for row in body["planes"]}
    assert {row["state"] for row in rows.values()} == {"running"}
    assert all(row["multi_device"] is True for row in rows.values())


def test_doctor_warns_when_two_devices_run_single_plane(tmp_path: Path) -> None:
    config = Config(devices=_devices())
    config.system.data_dir = str(tmp_path / "data")
    report = run_doctor(config)
    checks = {check.name: check for check in report.checks}
    assert "multi_device" in checks
    check = checks["multi_device"]
    assert check.status == "warn"
    assert "multi_device.enabled" in check.hint


def test_doctor_does_not_warn_for_one_device(tmp_path: Path) -> None:
    config = Config(devices=_devices()[:1])
    config.system.data_dir = str(tmp_path / "data")
    report = run_doctor(config)
    assert "multi_device" not in {check.name for check in report.checks}


def test_first_device_start_failure_is_attributed_to_it(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, multi=False)
    client.app.state.audio = _Sup("", running=False, error="missing TLS material")
    body = client.get("/health", headers=AUTH).json()
    rows = {row["device"]: row for row in body["planes"]}
    assert rows["dot2"]["state"] == "error"
    assert rows["dot2"]["error"] == "missing TLS material"
    assert rows["lx06"]["state"] == "not_started"
    assert rows["lx06"]["reason"] == "multi_device_disabled"


def test_running_plane_that_drops_is_reported_as_error(tmp_path: Path) -> None:
    client, _ = client_error(tmp_path)
    body = client.get("/health", headers=AUTH).json()
    rows = {row["device"]: row for row in body["planes"]}
    assert rows["dot2"]["state"] == "error"
    assert "exited" in rows["dot2"]["error"]


def client_error(tmp_path: Path) -> tuple[TestClient, Config]:
    client, config = _client(tmp_path, multi=False)
    client.app.state.audio = _Sup("dot2", running=False, error="AudioPlaneError: audio plane exited with 1")
    return client, config


def test_fleet_device_that_never_started_keeps_its_launch_error(tmp_path: Path) -> None:
    class _PartialFleet(_Fleet):
        def status(self) -> dict:
            return {"dot2": self._sups["dot2"].status()}

        def start_errors(self) -> dict[str, str]:
            return {"lx06": "launch failed: missing TLS material"}

    client, _ = _client(tmp_path, multi=True)
    client.app.state.audio_fleet = _PartialFleet({"dot2": _Sup("dot2"),
                                                  "lx06": _Sup("lx06")})
    body = client.get("/health", headers=AUTH).json()
    rows = {row["device"]: row for row in body["planes"]}
    assert rows["dot2"]["state"] == "running"
    assert rows["lx06"]["state"] == "error"
    assert "missing TLS material" in rows["lx06"]["error"]
