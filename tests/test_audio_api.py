"""The control plane exposes the audio plane's numbers through the API."""
from __future__ import annotations

import asyncio
import time

import psutil
from fastapi.testclient import TestClient

from rui.audio.client import AudioPlaneClient
from rui.config import load_config
from rui.web.api.app import create_app

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
    monkeypatch.setenv("RUI_AUDIO_SOCK", str(tmp_path / "audio.sock"))

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


def test_plane_failure_is_reported_with_its_reason(tmp_path, monkeypatch) -> None:
    """An LX06 with no device token must show up as a stated error, not as silence."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "version: 2\n"
        "devices:\n"
        "  - id: living\n"
        "    adapter: xiaoai\n"
        "    host: 192.0.2.10\n"
        "    listen_port: 4599\n"
        "    token_env: RUI_MISSING_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("RUI_MISSING_TOKEN", raising=False)
    monkeypatch.setenv("RUI_AUDIO_SOCK", str(tmp_path / "audio.sock"))

    app = create_app(load_config(config_path), config_path=config_path, start_audio=True)
    with TestClient(app) as client:
        assert client.get("/health").json()["audio_plane"] is False
        body = client.get("/api/v1/audio").json()
        assert body["running"] is False
        assert "TOKEN" in body["error"]
        assert "metrics" not in body


def test_close_survives_a_socket_whose_peer_is_already_gone() -> None:
    """Teardown must not raise when the plane's end of the socket is already gone.

    The red traceback (macOS) recorded the dead unix socket as a bare OSError
    (ENOTCONN, "Socket is not connected"); the transport keeps it as the
    connection-lost exception and ``StreamWriter.wait_closed`` re-raises it, so the
    ``suppress(ConnectionError)`` in ``close()`` did not cover it. That escaped the
    failed start (replacing "audio plane refused: ... TOKEN ..." with the socket
    error) and then failed the TestClient's shutdown. ``wait_closed`` is where the
    transport hands that error back, which is the one thing this stub reproduces.
    """

    class _GoneWriter:
        """The slice of StreamWriter that ``close()`` touches."""

        def __init__(self) -> None:
            self.frames: list[bytes] = []
            self.closed = False

        def write(self, data: bytes) -> None:
            self.frames.append(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            raise OSError(57, "Socket is not connected")

    async def scenario() -> None:
        writer = _GoneWriter()
        client = AudioPlaneClient(asyncio.StreamReader(), writer)  # type: ignore[arg-type]
        await client.close()
        assert writer.closed, "close() must still release the transport"
        # `_kill_child` clears `self.client` before the shutdown path, so a second
        # close here is defensive: it must be just as quiet as the first (the dead
        # peer is not a new error).
        await client.close()

    asyncio.run(scenario())
