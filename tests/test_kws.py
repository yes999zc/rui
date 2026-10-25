"""Wake word: the spotter itself, and the plane path that produces a Wake event."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import wave
from pathlib import Path

import numpy as np
import pytest

from rui.audio.kws import KwsUnavailable, WakeWord, model_files

# Optional wake model. RUI_TEST_MODELS must point at the parent of kws/, e.g. the
# directory `fetch-models --kws-dest` fills; otherwise these tests skip.
_TEST_MODELS = os.environ.get("RUI_TEST_MODELS", "").strip()
_KWS_REL = Path("kws") / "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
MODEL = Path(_TEST_MODELS) / _KWS_REL if _TEST_MODELS else Path("/nonexistent") / _KWS_REL
FIXTURE = Path(__file__).parent / "fixtures" / "wake-nihao-xiaorui.wav"

pytestmark = [
    pytest.mark.skipif(
        not MODEL.exists(),
        reason="optional wake model: set RUI_TEST_MODELS to the dir holding kws/",
    ),
    pytest.mark.skipif(not FIXTURE.exists(), reason="wake fixture missing"),
]


def read_pcm() -> bytes:
    with wave.open(str(FIXTURE)) as w:
        assert w.getframerate() == 16000
        return w.readframes(w.getnframes())


def silent(ms: int) -> bytes:
    return b"\x00\x00" * (16000 * ms // 1000)


def feed(spotter: WakeWord, pcm: bytes) -> list[str]:
    hits: list[str] = []
    for i in range(0, len(pcm), 640):  # 20 ms blocks, exactly like the plane
        hits += [h.keyword for h in spotter.accept(pcm[i:i + 640])]
    return hits


def test_spotter_hears_the_wake_word() -> None:
    spotter = WakeWord(MODEL)
    assert "你好小瑞" in feed(spotter, silent(100) + read_pcm() + silent(200))


def test_spotter_ignores_noise() -> None:
    spotter = WakeWord(MODEL)
    noise = (np.random.default_rng(0).standard_normal(16000 * 2) * 800).astype("<i2").tobytes()
    assert feed(spotter, noise) == []


def test_speech_without_the_keyword_does_not_fire() -> None:
    """A different phrase from the same voice, at the same level."""
    spotter = WakeWord(MODEL)
    t = np.arange(16000) / 16000
    tone_speech = (np.sin(2 * np.pi * 300 * t) * 6000).astype("<i2").tobytes()
    assert feed(spotter, silent(100) + tone_speech + silent(200)) == []


def test_missing_model_files_say_what_is_missing() -> None:
    with pytest.raises(KwsUnavailable, match="missing KWS model files"):
        model_files(Path("/nonexistent/kws"), chunk=8)


CONFIG = """\
version: 2
devices:
  - id: living
    adapter: xiaoai
    host: 192.0.2.10
    listen_port: {port}
    token_env: RUI_KWS_TOKEN
audio:
  capture: {{rate: 16000, channels: 8, mic_channels: 6, ref_channels: 2,
            mic_shift: 12, ref_shift: 16}}
  voice:
    rate: 16000
    frame_ms: 20
    kws_enabled: true
    kws_model_dir: {model}
  playback: {{rate: 16000, channels: 1}}
  aec: {{enabled: true, filter_len: 4096}}
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_wake_event_reaches_the_control_plane(tmp_path, monkeypatch) -> None:
    from websockets.asyncio.client import connect
    from xiaoai_plug import protocol

    from rui.audio.client import AudioPlaneSupervisor
    from rui.config import load_config

    monkeypatch.setenv("RUI_KWS_TOKEN", "kws-token")
    monkeypatch.setenv("RUI_AUDIO_SOCK", str(tmp_path / "audio.sock"))
    port = free_port()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG.format(port=port, model=MODEL), encoding="utf-8")

    async def run() -> dict:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(cfg, config_path=config_path,
                                          socket_path=tmp_path / "audio.sock")
        await supervisor.start()
        try:
            async with connect(f"ws://127.0.0.1:{port}/?token=kws-token", max_size=None) as ws:
                assert json.loads(await asyncio.wait_for(ws.recv(), timeout=3))["Request"]["command"] \
                    == "start_recording"
                # ch0 carries the speech; the other microphones and the reference stay
                # silent - the layout the real client sends, one frame per 160 ms.
                speech = np.frombuffer(silent(200) + read_pcm() + silent(400), dtype="<i2")
                for start in range(0, speech.size, 2560):
                    block = np.zeros((2560, 8), dtype="<i2")
                    chunk = speech[start:start + 2560]
                    block[: chunk.size, 0] = chunk
                    await ws.send(protocol.stream("record", block.tobytes()))
                    await asyncio.sleep(0.16)
                await asyncio.sleep(0.6)
            assert supervisor.client is not None
            return {"wakes": supervisor.client.wakes,
                    "last": supervisor.client.last_wake.model_dump()
                    if supervisor.client.last_wake else None,
                    "metrics": (await supervisor.client.request_metrics()).model_dump()}
        finally:
            await supervisor.stop()

    result = asyncio.run(run())
    assert result["wakes"] >= 1
    assert result["last"]["keyword"] == "你好小瑞"
    assert result["metrics"]["wake_count"] >= 1
    assert result["metrics"]["frames_dropped"] == 0
