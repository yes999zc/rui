"""The control plane exposes the audio plane's numbers through the API."""
from __future__ import annotations

import time

import psutil
from fastapi.testclient import TestClient

from house_ear.config import load_config
from house_ear.web.api.app import create_app

CONFIG = """\
version: 2
web:
  host: 127.0.0.1
devices:
  - id: mockdev
    adapter: mock
    host: 127.0.0.1
    room: 书房
"""


def test_api_reports_plane_metrics_and_shuts_it_down(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("HOUSE_EAR_AUDIO_SOCK", str(tmp_path / "audio.sock"))

    app = create_app(load_config(config_path), config_path=config_path, start_audio=True)
    with TestClient(app) as client:
        assert client.get("/health").json()["audio_plane"] is True
        time.sleep(2.6)  # one periodic metrics message
        body = client.get("/api/v1/audio").json()
        assert body["running"] is True
        assert body["capture"]["rate"] == 48000
        assert body["capture"]["reference_channels"] == 2
        metrics = body["metrics"]
        assert metrics is not None
        assert metrics["frames_in"] > 50
        assert metrics["frames_dropped"] == 0
        plane_pid = body["pid"]

    # the plane is a child process: leaving the lifespan must not leave it behind
    assert not psutil.pid_exists(plane_pid), "audio plane survived its supervisor"

    with TestClient(app) as client:
        time.sleep(1.0)
        assert client.get("/api/v1/audio").json()["running"] is True


def test_api_without_plane_says_so(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")
    app = create_app(load_config(config_path))
    with TestClient(app) as client:
        body = client.get("/api/v1/audio").json()
        assert body == {"running": False, "error": "audio plane not started"}
