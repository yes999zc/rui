"""The cue's own echo must never become a turn - without the optional VAD model.

`UtteranceGate.hold()` skips the cue at its *nominal* length and buffers the echo
window that follows, so a request starting right after the cue is not clipped. A real
device starts playing after the bytes arrive: with a deep playback buffer the cue is
still sounding when the skip window ends, and replaying that tail made ASR transcribe
our own 「在呢」 as a user turn (2026-10-26 12:33 `在呢。` -> `怎么了？`).

These tests run the real gate against a fake Silero (energy VAD). The point under test
is the gate's own state machine - which samples reach ASR - not Silero's accuracy.
"""
from __future__ import annotations

import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from rui.audio.vad import GateConfig, UtteranceGate

RATE = 16000
BLOCK = 640  # 20 ms of s16 at 16 kHz, the frame size the plane feeds


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

    def build() -> UtteranceGate:
        gate = UtteranceGate(GateConfig(), model)
        # The plane has already replayed the pre-wake ring before the hold; the wake
        # phrase tail is not this test's subject, so arm without the 300 ms skip.
        gate.arm(skip_after_wake_ms=0)
        return gate

    return build


def _speech(seconds: float) -> bytes:
    with wave.open(str(Path(__file__).parent / "fixtures" / "command-weather.wav")) as w:
        raw = w.readframes(w.getnframes())
    out = raw
    while len(out) < seconds * RATE * 2:
        out += raw
    return out[: int(seconds * RATE) * 2]


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def _feed(gate: UtteranceGate, pcm: bytes) -> tuple[bytes, float] | None:
    """Feed 20 ms blocks; return the utterance that closed, if any."""
    out = None
    for start in range(0, len(pcm), BLOCK):
        result = gate.feed(pcm[start : start + BLOCK])
        if result is not None:
            out = result
    return out


def _cue(seconds: float = 0.7) -> bytes:
    return _speech(seconds)


def _reference(gate: UtteranceGate, cue: bytes) -> None:
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)


def _close(gate: UtteranceGate) -> tuple[bytes, float] | None:
    """Silence long enough to close a started turn."""
    return _feed(gate, _silence(2.0))


def test_a_cue_tail_still_playing_after_the_skip_never_opens_a_turn(make_gate) -> None:
    """The device plays 550 ms late: the cue's last 300 ms falls in the echo window."""
    gate = make_gate()
    cue = _cue()
    _reference(gate, cue)
    gate.hold(1690, skip_ms=950)

    # Skip window (own voice): silence until playback starts, then the cue's first
    # 400 ms. Echo window (buffered): the rest of the cue, then real silence.
    _feed(gate, _silence(0.55) + cue[: int(0.4 * RATE) * 2])
    _feed(gate, cue[int(0.4 * RATE) * 2 :] + _silence(0.44))

    assert _close(gate) is None
    assert gate.turns == 0, "the replayed cue tail opened a turn"


def test_a_reply_after_a_late_cue_is_still_captured(make_gate) -> None:
    """Dropping the measured tail must not drop the person who answers it."""
    gate = make_gate()
    cue = _cue()
    _reference(gate, cue)
    gate.hold(1690, skip_ms=950)

    _feed(gate, _silence(0.55) + cue[: int(0.4 * RATE) * 2])
    reply = _speech(1.0)
    # Echo window: cue tail (300 ms, dropped), 100 ms pause, then the reply.
    _feed(gate, cue[int(0.4 * RATE) * 2 :] + _silence(0.1) + reply[: int(0.34 * RATE) * 2])

    captured = _feed(gate, reply[int(0.34 * RATE) * 2 : int(0.94 * RATE) * 2])
    if captured is None:
        captured = _close(gate)
    assert gate.turns == 1
    assert captured is not None
    assert len(captured[0]) / 2 / RATE > 0.5, "the reply was clipped"


def test_a_reply_right_after_a_normal_cue_is_not_dropped(make_gate) -> None:
    """No late tail: the measured remainder is zero, so the buffered reply is kept."""
    gate = make_gate()
    cue = _cue()
    _reference(gate, cue)
    gate.hold(1690, skip_ms=950)

    # Nominal playback: cue + 250 ms tail are the skip window; the reply follows.
    _feed(gate, cue + _silence(0.25))
    reply = _speech(1.2)
    _feed(gate, reply[: int(0.74 * RATE) * 2])

    captured = _feed(gate, reply[int(0.74 * RATE) * 2 : int(0.94 * RATE) * 2])
    if captured is None:
        captured = _close(gate)
    assert gate.turns == 1
    assert captured is not None
    assert len(captured[0]) / 2 / RATE > 0.8, "the reply that started after the cue was clipped"


def test_a_device_that_mutes_during_the_cue_is_unaffected(make_gate) -> None:
    """No echo in the skip window: no envelope match, so the reply is replayed."""
    gate = make_gate()
    cue = _cue()
    _reference(gate, cue)
    gate.hold(1690, skip_ms=950)

    _feed(gate, _silence(0.95))  # the device muted its microphone while cueing
    reply = _speech(1.2)
    _feed(gate, reply[: int(0.74 * RATE) * 2])

    captured = _feed(gate, reply[int(0.74 * RATE) * 2 : int(0.94 * RATE) * 2])
    if captured is None:
        captured = _close(gate)
    assert gate.turns == 1
    assert captured is not None
    assert len(captured[0]) / 2 / RATE > 0.8
