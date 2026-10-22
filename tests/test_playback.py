"""Debug playback through the plane: the speaker must actually receive play frames."""
from __future__ import annotations

import asyncio
import json
import socket
import wave

import numpy as np
import pytest
from websockets.asyncio.client import connect

from house_ear.audio.client import AudioPlaneSupervisor
from house_ear.audio.protocol import PlayFile, StopAudio
from house_ear.config import load_config
from house_ear.device.xiaoai import protocol

TOKEN_ENV = "HOUSE_EAR_PLAY_TOKEN"
TOKEN = "play-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_tone(path, seconds: float = 0.4, rate: int = 16000) -> None:
    t = np.arange(int(seconds * rate)) / rate
    pcm = (np.sin(2 * np.pi * 440 * t) * 12000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def config_text(port: int) -> str:
    return f"""\
version: 2
devices:
  - id: living
    adapter: xiaoai
    host: <LAN-IP>
    listen_port: {port}
    token_env: {TOKEN_ENV}
audio:
  capture:
    rate: 16000
    channels: 8
    mic_channels: 6
    ref_channels: 2
    mic_shift: 12
    ref_shift: 16
  voice:
    rate: 16000
    frame_ms: 20
  playback:
    rate: 16000
    channels: 1
  aec:
    enabled: false        # playback test only; AEC has its own unit tests
"""


def test_play_file_reaches_the_speaker_and_stop_play_follows(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    monkeypatch.setenv("HOUSE_EAR_AUDIO_SOCK", str(tmp_path / "audio.sock"))
    tone = tmp_path / "tone.wav"
    write_tone(tone)
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
                start_recording = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                assert start_recording["Request"]["command"] == "start_recording"

                client = supervisor.client
                assert client is not None
                metrics = await client.request_metrics()
                assert metrics is not None and metrics.speaker_connected is True

                await client.send(PlayFile(path=str(tone), loop=False, gain=0.5))

                # Control requests (start_play, the volume probe) are answered like a real
                # device does; only stream frames are audio.
                async def recv_answer(raw: str) -> None:
                    body = json.loads(raw)
                    if "Request" in body:
                        await ws.send(json.dumps({
                            "Response": {"id": body["Request"]["id"], "code": 0},
                        }))

                def peek(raw: str) -> dict:
                    return json.loads(raw)

                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                while "Request" in peek(raw) and peek(raw)["Request"]["command"] != "start_play":
                    await recv_answer(raw)
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                open_player = peek(raw)
                assert open_player["Request"]["command"] == "start_play"
                await recv_answer(raw)
                assert open_player["Request"]["payload"]["channels"] == 1
                assert open_player["Request"]["payload"]["sample_rate"] == 16000

                chunks = 0
                while chunks < 2:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3)
                    if "Request" in peek(raw):
                        await recv_answer(raw)
                        continue
                    tag, pcm = protocol.decode_stream(raw)
                    assert tag == "play"
                    assert len(pcm) > 0
                    chunks += 1

                await client.send(StopAudio())
                # A stop is not instantaneous on the wire: blocks already handed to the
                # socket arrive first. What matters is that stop_play arrives and no new
                # block follows it.
                while True:
                    message = await asyncio.wait_for(ws.recv(), timeout=3)
                    if isinstance(message, str):
                        break
                    tag, _pcm = protocol.decode_stream(message)
                    assert tag == "play"
                stop = json.loads(message)
                assert stop["Request"]["command"] == "stop_play"
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(ws.recv(), timeout=0.5)
                metrics = await client.request_metrics()
                return {"metrics": metrics.model_dump() if metrics else {}}
        finally:
            await supervisor.stop()

    result = asyncio.run(run())
    assert result["metrics"].get("playback_s", 0) >= 0
