"""The process boundary, exercised for real: spawn the plane, talk to it, read numbers."""
from __future__ import annotations

import asyncio

import pytest

from house_ear.audio.client import AudioPlaneError, AudioPlaneSupervisor
from house_ear.config import load_config

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


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_plane_decimates_a_mock_device_over_a_socket(config_path, tmp_path) -> None:
    async def run() -> tuple[dict, dict]:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(
            cfg, config_path=config_path, socket_path=tmp_path / "audio.sock"
        )
        await supervisor.start()
        try:
            assert supervisor.running is True
            await asyncio.sleep(3.0)
            if supervisor.client is not None:
                await supervisor.client.request_metrics()
            live = supervisor.status()
        finally:
            await supervisor.stop()
        return live, supervisor.status()

    live, after = asyncio.run(run())

    assert live["running"] is True
    assert after["running"] is False
    assert live["capture"] == {
        "rate": 48000,
        "channels": 8,
        "mic_channels": 6,
        "reference_channels": 2,
    }

    metrics = live["metrics"]
    assert metrics is not None
    # ~3 s at 50 frames/s, minus process startup
    assert 100 <= metrics["frames_in"] <= 200
    assert metrics["frames_dropped"] == 0
    assert metrics["jitter_p50_ms"] == pytest.approx(20.0, abs=3.0)
    assert metrics["jitter_p99_ms"] < 120.0
    assert metrics["rtf"] < 0.5

    # 16 kHz mono, 20 ms per block -> 640 bytes of PCM in every voice frame
    assert live["voice_frames_received"] >= 100
    ratio = live["voice_bytes_received"] / live["voice_frames_received"]
    assert 600 <= ratio <= 700


def test_supervisor_reports_a_missing_device_instead_of_hanging(config_path, tmp_path) -> None:
    async def run() -> None:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(
            cfg, config_path=config_path, socket_path=tmp_path / "nope.sock", device_id="ghost"
        )
        await supervisor.start()

    with pytest.raises(AudioPlaneError):
        asyncio.run(run())
