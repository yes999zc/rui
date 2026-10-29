"""T5: capture overdrive must be visible, and one clipped utterance annotated.

Production 2026-10-29 (dot2-b, close-talk): the capture saturated, the segment's energy
average still looked quiet (`[seg] rms=0.0012 peak=0.0265`) and ASR returned 「。」, so
nothing in the log said "the microphone is clipping". These tests drive the real frame
path and the real gate: every frame's clipped share is counted, a burst is reported once,
the metrics snapshot carries it, and a clipped segment's `[seg]` line says so.
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from rui.audio.decimate import Decimator
from rui.audio.process import (
    OVERDRIVE_FRAME_RATIO,
    OVERDRIVE_FRAMES,
    AudioPlane,
)
from rui.audio.protocol import Metrics
from rui.audio.vad import GateConfig, UtteranceGate
from rui.config import Config
from rui.device.contract import AudioFrame

RATE = 16000
BLOCK_SAMPLES = 320  # 20 ms at the voice rate


def _plane() -> AudioPlane:
    cfg = Config()
    cfg.audio.aec.enabled = False
    return AudioPlane(cfg, "living", Path("/tmp/overdrive.sock"))


def _frame(*, constant: int | None = None, amplitude: float = 8000.0,
           seq: int = 1) -> AudioFrame:
    """One 20 ms frame on all mic columns; `constant` gives an exact clipped share."""
    if constant is None:
        t = np.arange(BLOCK_SAMPLES) / RATE
        tone = (amplitude * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
    else:
        tone = np.full(BLOCK_SAMPLES, constant, dtype=np.int16)
    block = np.zeros((BLOCK_SAMPLES, 8), dtype=np.int16)
    block[:, :6] = tone[:, None]
    return AudioFrame(
        device_id="living", seq=seq, ts_ms=0.0, pcm=block.tobytes(),
        channels=8, sample_rate=RATE, reference_channels=2,
    )


def _feed(plane: AudioPlane, decimator: Decimator, frame: AudioFrame) -> None:
    plane._frame_to_voice(frame, decimator)


def _clipped_burst(plane: AudioPlane, decimator: Decimator, seq: int = 1) -> None:
    for i in range(OVERDRIVE_FRAMES):
        _feed(plane, decimator, _frame(constant=32767, seq=seq + i))


def _decimator() -> Decimator:
    return Decimator(factor=1, channels=8)


def test_normal_audio_does_not_trigger_overdrive() -> None:
    plane = _plane()
    decimator = _decimator()
    for seq in range(10):
        _feed(plane, decimator, _frame(amplitude=8000.0, seq=seq))

    assert plane.overdrive_count == 0
    assert plane.overdrive_ratio_max < OVERDRIVE_FRAME_RATIO
    assert plane.last_overdrive_at is None


def test_a_clipped_burst_is_counted_once_with_its_ratio_and_peak(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="rui.audio.process")
    plane = _plane()
    decimator = _decimator()

    _clipped_burst(plane, decimator)
    assert plane.overdrive_count == 1, "five clipped frames must open exactly one burst"
    assert plane.overdrive_ratio_max == pytest.approx(1.0)
    assert plane.last_overdrive_at is not None

    # A sustained clip is one problem, not one event per 100 ms.
    _feed(plane, decimator, _frame(constant=32767, seq=99))
    assert plane.overdrive_count == 1

    warnings = [r.getMessage() for r in caplog.records if "overdrive:" in r.getMessage()]
    assert len(warnings) == 1
    assert "device=living" in warnings[0]
    assert "clipped=100.0%" in warnings[0]
    assert "peak=1.000" in warnings[0]


def test_a_new_burst_after_quiet_is_counted_and_the_log_is_rate_limited(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="rui.audio.process")
    plane = _plane()
    decimator = _decimator()

    _clipped_burst(plane, decimator, seq=1)
    _feed(plane, decimator, _frame(seq=10))  # below the threshold re-arms the burst
    _clipped_burst(plane, decimator, seq=20)

    assert plane.overdrive_count == 2
    warnings = [r.getMessage() for r in caplog.records if "overdrive:" in r.getMessage()]
    assert len(warnings) == 1, "the warning must be rate-limited within its window"


def test_metrics_snapshot_carries_the_overdrive_fields() -> None:
    plane = _plane()
    decimator = _decimator()
    _clipped_burst(plane, decimator)

    snapshot = plane.metrics()

    assert isinstance(snapshot, Metrics)
    assert snapshot.overdrive_count == 1
    assert snapshot.overdrive_ratio_max == pytest.approx(1.0)
    assert snapshot.last_overdrive_at == pytest.approx(plane.last_overdrive_at)


def test_the_metrics_fields_are_additive() -> None:
    """An older plane's payload (no overdrive keys) must still parse, with defaults."""
    required = {
        "uptime_s": 0.0, "frames_in": 0, "frames_out": 0, "frames_dropped": 0,
        "no_consumer": 0, "jitter_p50_ms": 0.0, "jitter_p99_ms": 0.0,
        "cpu_s": 0.0, "audio_s": 0.0, "rtf": 0.0,
    }
    old = Metrics.model_validate(required)
    assert old.overdrive_count == 0
    assert old.overdrive_ratio_max == 0.0
    assert old.last_overdrive_at is None


# ------------------------------------------------------------------ [seg] annotation
class _FakeVad:
    """Energy-free stand-in: the test decides whether the block is speech."""

    speech = False

    def __init__(self, config, buffer_size_in_seconds: int = 30) -> None:  # noqa: ARG002
        self.config = config

    def reset(self) -> None:
        return None

    def accept_waveform(self, samples: object) -> None:  # noqa: ARG002
        return None

    def is_speech_detected(self) -> bool:
        return _FakeVad.speech


@pytest.fixture()
def make_gate(tmp_path, monkeypatch) -> UtteranceGate:
    module = types.ModuleType("sherpa_onnx")

    class _Cfg:
        def __init__(self) -> None:
            self.silero_vad = types.SimpleNamespace()
            self.sample_rate = RATE

    module.VadModelConfig = _Cfg
    module.VoiceActivityDetector = _FakeVad
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"fake")
    _FakeVad.speech = False
    gate = UtteranceGate(GateConfig(end_silence_ms=100), model)
    gate.arm(skip_after_wake_ms=0)
    return gate


def _close_segment(gate: UtteranceGate, speech_block: bytes) -> None:
    _FakeVad.speech = True
    for _ in range(30):  # 300 ms of speech, well past min_speech_ms
        gate.feed(speech_block, ts_ms=0.0)
    _FakeVad.speech = False
    for _ in range(15):  # the 100 ms end point closes the segment
        if gate.feed(b"\x00\x00" * 160, ts_ms=0.0) is not None:
            return
    raise AssertionError("the utterance never closed")


def test_a_clipped_utterance_is_annotated_in_the_seg_line(make_gate, caplog) -> None:
    caplog.set_level(logging.INFO, logger="rui.audio.vad")
    clipped = (np.full(160, 32767, dtype=np.int16)).tobytes()

    _close_segment(make_gate, clipped)

    seg = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("[seg]"))
    # 300 ms clipped speech + the 100 ms trailing silence kept by the trim = 75%.
    assert "clipped=75%" in seg, seg
    # The transcript itself is still produced: the annotation never blocks recognition.
    assert make_gate.turns == 1


def test_a_normal_utterance_has_no_clipped_annotation(make_gate, caplog) -> None:
    caplog.set_level(logging.INFO, logger="rui.audio.vad")
    normal = (np.full(160, 8000, dtype=np.int16)).tobytes()

    _close_segment(make_gate, normal)

    seg = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("[seg]"))
    assert "clipped=" not in seg, seg
    assert make_gate.turns == 1
