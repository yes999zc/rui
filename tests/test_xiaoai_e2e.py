"""End to end: a fake speaker dials the adapter, the audio plane turns it into voice PCM."""
from __future__ import annotations

import asyncio
import json
import socket
import time

import numpy as np
import pytest
from websockets.asyncio.client import connect
from xiaoai_plug import protocol

from rui.audio.client import AudioPlaneSupervisor
from rui.config import load_config

TOKEN_ENV = "RUI_E2E_TOKEN"
TOKEN = "e2e-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def config_text(port: int) -> str:
    return f"""\
version: 2
web:
  host: 127.0.0.1
devices:
  - id: living
    adapter: lx06
    host: 192.0.2.10
    room: 客厅
    listen_port: {port}
    token_env: {TOKEN_ENV}
audio:
  capture:
    rate: 16000        # ask the device to resample: the plane then does no decimation
    channels: 8
    mic_channels: 6
    ref_channels: 2
    bits: 32
    mic_shift: 12
    ref_shift: 16
  voice:
    rate: 16000
    frame_ms: 20
"""


def frames_of_tone(count: int, rate: int = 16000, channels: int = 8) -> list[bytes]:
    samples = rate // 50
    out = []
    for seq in range(count):
        t = (np.arange(samples) + seq * samples) / rate
        block = np.zeros((samples, channels), dtype=np.int16)
        block[:, 0] = (6000 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
        out.append(block.tobytes())
    return out


def test_fake_speaker_feeds_the_audio_plane(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    monkeypatch.setenv("RUI_AUDIO_SOCK", str(tmp_path / "audio.sock"))
    port = free_port()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text(port), encoding="utf-8")

    async def run() -> dict:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(cfg, config_path=config_path,
                                          socket_path=tmp_path / "audio.sock")
        await supervisor.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}/?token={TOKEN}", max_size=None) as ws:
                request = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                assert request["Request"]["command"] == "start_recording"
                assert request["Request"]["payload"]["mic_shift"] == 12

                for pcm in frames_of_tone(50):
                    await ws.send(protocol.stream("record", pcm))
                    await asyncio.sleep(0.02)   # real time: the plane consumes as it goes
                await asyncio.sleep(0.4)

            assert supervisor.client is not None
            metrics = await supervisor.client.request_metrics()
            assert metrics is not None
            return {"metrics": metrics.model_dump(),
                    "voice_frames": supervisor.client.voice_frames,
                    "voice_bytes": supervisor.client.voice_bytes}
        finally:
            await supervisor.stop()

    started = time.monotonic()
    result = asyncio.run(run())
    elapsed = time.monotonic() - started

    metrics = result["metrics"]
    assert metrics["frames_in"] >= 45          # 50 sent, a few may still be in flight
    assert metrics["frames_dropped"] == 0
    assert metrics["jitter_p50_ms"] == pytest.approx(20.0, abs=6.0)
    assert result["voice_frames"] >= 40        # one 16 kHz mono block per 20 ms
    assert result["voice_bytes"] == result["voice_frames"] * 640
    assert metrics["rtf"] < 0.5
    assert elapsed < 10.0
