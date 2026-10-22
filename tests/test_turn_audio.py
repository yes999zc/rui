"""Turn segmentation and PCM playback: from a wake word to one reply at the speaker."""
from __future__ import annotations

import asyncio
import json
import socket
import wave
from pathlib import Path

import numpy as np
import pytest

from house_ear.audio.vad import GateConfig, UtteranceGate

MODEL = Path.home() / "AI_Workspace/03_Data/Models/sherpa-onnx/silero_vad.onnx"
KWS_MODEL = Path.home() / "AI_Workspace/03_Data/Models/sherpa-onnx/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
FIXTURE = Path(__file__).parent / "fixtures" / "wake-nihao-xiaorui.wav"
COMMAND = Path(__file__).parent / "fixtures" / "command-weather.wav"

needs_vad = pytest.mark.skipif(not MODEL.exists(), reason="silero VAD model not installed")
needs_kws = pytest.mark.skipif(not KWS_MODEL.exists(), reason="KWS model not installed")


def speech_pcm(path: Path = FIXTURE) -> bytes:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def silence(ms: int) -> bytes:
    return b"\x00\x00" * (16000 * ms // 1000)


@needs_vad
def test_gate_closes_on_silence_and_keeps_the_speech() -> None:
    gate = UtteranceGate(GateConfig(), MODEL)
    gate.arm()
    stream = silence(100) + speech_pcm() + silence(2000)
    out = None
    for i in range(0, len(stream), 640):
        result = gate.feed(stream[i:i + 640])
        if result:
            out = result
    assert out is not None
    duration = len(out[0]) / 2 / 16000
    assert 1.0 < duration < 2.2
    assert gate.turns == 1


@needs_vad
def test_gate_gives_up_when_nothing_is_said() -> None:
    gate = UtteranceGate(GateConfig(max_wait_ms=1000), MODEL)
    gate.arm()
    for _ in range(80):  # 1.6 s of silence in 20 ms blocks
        gate.feed(silence(20))
    assert gate.armed is False
    assert gate.turns == 0
    assert gate.discarded == 1


@needs_vad
def test_a_cough_is_not_a_turn() -> None:
    gate = UtteranceGate(GateConfig(min_speech_ms=400, end_silence_ms=300), MODEL)
    gate.arm()
    stream = silence(100) + speech_pcm()[:3000] + silence(1500)  # ~90 ms of speech
    for i in range(0, len(stream), 640):
        gate.feed(stream[i:i + 640])
    assert gate.turns == 0


CONFIG = """\
version: 2
devices:
  - id: living
    adapter: xiaoai
    host: <LAN-IP>
    listen_port: {port}
    token_env: HOUSE_EAR_TURN_TOKEN
audio:
  capture: {{rate: 16000, channels: 8, mic_channels: 6, ref_channels: 2,
            mic_shift: 12, ref_shift: 16}}
  voice:
    rate: 16000
    frame_ms: 20
    kws_enabled: true
    kws_model_dir: {kws}
    vad_model_path: {vad}
    vad_min_speech_ms: 200
    vad_end_silence_ms: 600
  playback: {{rate: 16000, channels: 1}}
  aec: {{enabled: true, filter_len: 4096}}
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@needs_vad
@needs_kws
def test_wake_then_speech_becomes_an_utterance_and_a_reply_is_played(tmp_path, monkeypatch) -> None:
    from websockets.asyncio.client import connect

    from house_ear.audio.client import AudioPlaneSupervisor
    from house_ear.config import load_config
    from house_ear.device.xiaoai import protocol

    monkeypatch.setenv("HOUSE_EAR_TURN_TOKEN", "turn-token")
    monkeypatch.setenv("HOUSE_EAR_AUDIO_SOCK", str(tmp_path / "audio.sock"))
    port = free_port()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG.format(port=port, kws=KWS_MODEL, vad=MODEL), encoding="utf-8")

    async def send_audio(ws, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype="<i2")
        for start in range(0, samples.size, 2560):  # 160 ms device-style frames
            block = np.zeros((2560, 8), dtype="<i2")
            chunk = samples[start:start + 2560]
            block[: chunk.size, 0] = chunk
            await ws.send(protocol.stream("record", block.tobytes()))
            await asyncio.sleep(0.16)

    async def run() -> dict:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(cfg, config_path=config_path,
                                          socket_path=tmp_path / "audio.sock")
        await supervisor.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}/?token=turn-token", max_size=None) as ws:
                assert json.loads(await asyncio.wait_for(ws.recv(), timeout=3))["Request"]["command"] \
                    == "start_recording"
                # wake word, a short gap, the actual request, then silence to close the
                # turn. The gate deliberately drops the wake-phrase tail, so the request
                # has to be its own burst - exactly what a person does.
                await send_audio(
                    ws,
                    silence(200)
                    + speech_pcm()                      # wake word
                    + silence(300)
                    + speech_pcm(COMMAND)                # the actual request
                    + silence(2500),
                )
                await asyncio.sleep(1.0)
                client = supervisor.client
                assert client is not None
                assert client.wakes >= 1
                assert client.utterances >= 1

                reply = np.round(np.sin(2 * np.pi * 440 * np.arange(1600) / 16000) * 8000).astype("<i2")
                await client.speak(reply.tobytes(), rate=16000, segment_start=True)

                # The device answers control requests; start_play is only acknowledged
                # once its player is able to take frames, and the plane waits for that.
                async def next_audio():
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=3)
                        body = json.loads(raw)
                        if "Request" in body:
                            await ws.send(json.dumps({
                                "Response": {"id": body["Request"]["id"], "code": 0},
                            }))
                            if body["Request"]["command"] == "start_play":
                                opened.append(True)
                            continue
                        return protocol.decode_stream(raw)

                opened: list[bool] = []
                tag, pcm = await next_audio()
                assert opened, "start_play was never requested"
                assert tag == "play" and len(pcm) > 0
                return {"wakes": client.wakes, "utterances": client.utterances,
                        "utterance_bytes": len(client.last_utterance)}
        finally:
            await supervisor.stop()

    result = asyncio.run(run())
    assert result["utterances"] >= 1
    assert result["utterance_bytes"] > 16000  # at least 0.5 s of 16 kHz audio
