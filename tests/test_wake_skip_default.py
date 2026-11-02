"""`wake_skip_ms`: default 300; 0 is available per deployment (0.8.17 decision).

0.8.16 defaulted this to 0 to keep the first word after the wake ("几点了" had arrived
as "点了"). On the 2026-11-02 Dot-only architecture that default let the wake phrase's
own tail into the transcript (a turn built from 「宝。」), so the default is back to 300
and 0 stays a documented per-deployment choice. Range stays 0–1000.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from rui.audio.process import AudioPlane
from rui.audio.vad import GateConfig, UtteranceGate
from rui.config import load_config
from rui.config.schema import Config, VoiceConfig

RATE = 16000
BLOCK = 640


class _FakeSileroConfig:
    def __init__(self) -> None:
        self.silero_vad = types.SimpleNamespace()
        self.sample_rate = RATE


class _FakeSileroVad:
    """Per-block energy VAD: speech whenever the block is not near-silence."""

    def __init__(self, config, buffer_size_in_seconds: int = 30) -> None:  # noqa: ARG002
        self.config = config
        self._speech = False

    def reset(self) -> None:
        self._speech = False

    def accept_waveform(self, samples: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        self._speech = rms > 0.01

    def is_speech_detected(self) -> bool:
        return self._speech


@pytest.fixture()
def make_gate(tmp_path, monkeypatch):
    module = types.ModuleType("sherpa_onnx")
    module.VadModelConfig = _FakeSileroConfig
    module.VoiceActivityDetector = _FakeSileroVad
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"fake")
    return lambda: UtteranceGate(GateConfig(), model)


def _speech(ms: int, freq: float = 220.0, amp: float = 0.3) -> bytes:
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    sig = amp * np.sin(2.0 * np.pi * freq * t)
    return (sig * 32767.0).astype("<i2").tobytes()


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def _feed(gate: UtteranceGate, pcm: bytes) -> tuple[bytes, float] | None:
    out = None
    for start in range(0, len(pcm), BLOCK):
        result = gate.feed(pcm[start : start + BLOCK])
        if result is not None:
            out = result
    return out


def test_schema_default_is_300_and_zero_stays_available() -> None:
    assert VoiceConfig().wake_skip_ms == 300
    assert Config().audio.voice.wake_skip_ms == 300
    assert VoiceConfig(wake_skip_ms=0).wake_skip_ms == 0
    assert VoiceConfig(wake_skip_ms=1000).wake_skip_ms == 1000
    with pytest.raises(ValueError):
        VoiceConfig(wake_skip_ms=1001)


def test_the_plane_passes_the_configured_skip_into_the_vad_gate(tmp_path, monkeypatch) -> None:
    captured: list[GateConfig] = []

    class StubGate:
        def __init__(self, cfg: GateConfig, model: object) -> None:  # noqa: ARG002
            captured.append(cfg)
            self.armed = False
            self.silent_wakes = 0

    monkeypatch.setattr("rui.audio.process.UtteranceGate", StubGate)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.audio.voice.vad_model_path = "silero_vad.onnx"
    plane = AudioPlane(cfg, "living", tmp_path / "plane.sock")
    plane._build_vad()
    assert captured and captured[0].skip_after_wake_ms == 300

    cfg.audio.voice.wake_skip_ms = 0
    plane._build_vad()
    assert captured[-1].skip_after_wake_ms == 0, "a deployment may still opt into 0"


def test_a_request_right_after_the_wake_keeps_its_first_word(make_gate) -> None:
    """300 ms of speech starting 100 ms after the wake: skip 0 keeps it, skip 300 loses it."""
    fresh = make_gate()
    fresh.arm(skip_after_wake_ms=0)
    kept = _feed(fresh, _silence(100) + _speech(300) + _silence(1200))
    assert kept is not None, "skip=0 must not eat the request's first word"
    assert len(kept[0]) >= int(RATE * 0.25) * 2

    old = make_gate()
    old.arm(skip_after_wake_ms=300)
    lost = _feed(old, _silence(100) + _speech(300) + _silence(1200))
    assert lost is None, "the old 300 ms skip is exactly the failure this change fixes"


def test_the_gate_config_fallback_is_also_zero() -> None:
    """A caller that forgets to pass `voice.wake_skip_ms` must not eat the first word."""
    assert GateConfig().skip_after_wake_ms == 0


def test_a_cold_install_template_carries_300(tmp_path) -> None:
    """`rui setup` writes template.yaml verbatim; an explicit 300 would beat the schema."""
    from rui.__main__ import _production_template

    path = tmp_path / "config.yaml"
    path.write_text(_production_template(path), encoding="utf-8")
    assert load_config(path).audio.voice.wake_skip_ms == 300

    repo = Path(__file__).resolve().parents[1]
    template = (repo / "src" / "rui" / "config" / "template.yaml").read_text(encoding="utf-8")
    assert "wake_skip_ms: 300" in template
    example = (repo / "config.example.yaml").read_text(encoding="utf-8")
    assert "wake_skip_ms: 300" in example
