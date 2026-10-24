"""The process boundary, exercised for real: spawn the plane, talk to it, read numbers."""
from __future__ import annotations

import asyncio

import pytest

from rui.audio.client import AudioPlaneError, AudioPlaneSupervisor
from rui.config import load_config

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
    # ±10 ms (audit P2-17). A single ±5 assertion measured 25.48 in a full run on this
    # loaded Mac and passed in isolation: it is a scheduler/load measurement, not a
    # product invariant. The frame count and drop count above stay strict; the cadence
    # bound only rejects double-speed/stalled capture.
    assert metrics["jitter_p50_ms"] == pytest.approx(20.0, abs=10.0)
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


def test_a_mid_song_stream_stop_is_not_a_finished_song() -> None:
    """15:53:28: the speaker dropped after a resume; the 207 s track "finished" in 0.7 s
    and the queue advanced. A short stream must not look like a natural end."""
    from rui.audio.process import AudioPlane

    step = 640
    assert AudioPlane.playback_reached_end(position=1000 + 204_800, offset=1000,
                                      size=204_800, step=step)
    assert not AudioPlane.playback_reached_end(position=1000 + 11_200, offset=1000,
                                          size=204_800, step=step)
    assert not AudioPlane.playback_reached_end(position=1000, offset=1000, size=204_800, step=step)
