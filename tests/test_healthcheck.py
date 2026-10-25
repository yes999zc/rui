"""The container probe's verdicts, including the root causes dot2 #12 asked for."""
from __future__ import annotations

from fastapi.testclient import TestClient

from rui import healthcheck
from rui.config import Config, DeviceConfig
from rui.doctor import Check, Report
from rui.web.api import app as app_module
from rui.web.api.app import create_app


def test_a_device_that_cannot_be_built_is_a_root_cause_not_a_restart() -> None:
    ok, reason = healthcheck.evaluate({
        "status": "ok", "enabled_devices": 1, "audio_plane": False,
        "speaker_connected": False, "device_errors": 1,
    })
    assert ok is False
    assert "cannot be driven" in reason
    assert "restarting will not fix" in reason
    assert "device_errors=1" in reason


def test_a_fleet_that_failed_to_start_repeats_the_real_error() -> None:
    ok, reason = healthcheck.evaluate({
        "status": "ok", "enabled_devices": 2, "audio_plane": False,
        "speaker_connected": False,
        "multi_device": {"enabled": True, "error": "two planes, one socket"},
    })
    assert ok is False
    assert "fleet failed to start" in reason
    assert "two planes, one socket" in reason


def test_a_dead_room_is_named_with_its_own_error() -> None:
    body = {
        "status": "ok", "enabled_devices": 2, "audio_plane": False,
        "speaker_connected": False,
        "audio_devices": [
            {"id": "living", "room": "客厅", "running": False,
             "speaker_connected": False, "error": "child exited 2"},
            {"id": "study", "room": "书房", "running": True,
             "speaker_connected": False, "error": ""},
        ],
    }
    ok, reason = healthcheck.evaluate(body)
    assert ok is False
    assert "客厅" in reason and "child exited 2" in reason
    assert "书房" in reason and "no speaker" in reason


def test_one_connected_room_satisfies_the_aggregate_probe() -> None:
    ok, reason = healthcheck.evaluate({
        "status": "ok", "enabled_devices": 2, "audio_plane": True,
        "speaker_connected": True,
        "audio_devices": [
            {"id": "living", "room": "客厅", "running": True,
             "speaker_connected": True, "error": ""},
            {"id": "study", "room": "书房", "running": True,
             "speaker_connected": False, "error": ""},
        ],
    })
    assert ok is True, reason


def test_doctor_row_matches_the_container_healthcheck(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "run_doctor",
        lambda cfg, **kwargs: Report(verdict="ok", checks=[Check("disk_free_gb", "ok", "10")]))
    with TestClient(create_app(Config())) as client:
        body = client.get("/api/v1/doctor").json()
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["container_healthcheck"]["status"] == "ok"
    assert checks["container_healthcheck"]["value"] == "healthy"


def test_doctor_row_names_the_reason_when_the_probe_would_fail(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "run_doctor",
        lambda cfg, **kwargs: Report(verdict="ok", checks=[]))
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock",
                                       host="127.0.0.1", room="客厅")])
    with TestClient(create_app(cfg)) as client:
        body = client.get("/api/v1/doctor").json()
    check = next(row for row in body["checks"] if row["name"] == "container_healthcheck")
    assert check["status"] == "warn"
    assert check["value"] == "unhealthy"
    assert "audio plane not running" in check["hint"]
