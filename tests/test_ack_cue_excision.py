"""Cue excision 0.8.16 P1: drop the pure-cue prefix, keep speech that overlaps it.

The old behaviour dropped the whole cue window. A person who starts a command while
「在呢」 is still sounding lost those first words (or, when the cue could not be located,
the conservative cue+1000 ms window). These tests drive the real `UtteranceGate` with a
fake energy VAD and synthetic audio, so the block geometry is exactly known:

    mic:  [silence 100 ms][cue 540 ms][person ...]
    cue:  tone 200 ms / gap 120 ms / tone 220 ms   (a syllable rhythm with a gap)

The gap is what the overlap detector keys on: voice energy where the cue has none, and
then more energy than the cue explains for two consecutive 20 ms blocks.
"""
from __future__ import annotations

import logging
import sys
import types

import numpy as np
import pytest

from rui.audio.vad import CUE_OVERLAP_RATIO, GateConfig, UtteranceGate

RATE = 16000
BLOCK = 640  # 20 ms of s16 at 16 kHz, the frame size the plane feeds
CUE_START_MS = 100
CUE_MS = 540
SKIP_MS = 300
HOLD_MS = 1500


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

    def build(*, keep_overlapping_speech: bool = True) -> UtteranceGate:
        cfg = GateConfig(keep_overlapping_speech=keep_overlapping_speech)
        gate = UtteranceGate(cfg, model)
        gate.arm(skip_after_wake_ms=0)
        return gate

    return build


# ------------------------------------------------------------------ synthesis
def _cue() -> np.ndarray:
    """540 ms of noise with a 120 ms silent gap.

    Noise, not a tone: its loudness envelope is unique, so the envelope matcher finds
    one clear alignment instead of accidentally correlating with the person's tone.
    The gap is where the overlap detector can see somebody talking where the cue cannot.
    """
    rng = np.random.default_rng(7)
    n = int(RATE * 0.540)
    noise = rng.standard_normal(n).astype(np.float32)
    noise = np.convolve(noise, np.ones(8, dtype=np.float32) / 8.0, mode="same")
    envelope = np.concatenate([
        np.ones(int(RATE * 0.200), dtype=np.float32),
        np.zeros(int(RATE * 0.120), dtype=np.float32),
        np.ones(int(RATE * 0.220), dtype=np.float32),
    ])
    return (0.55 * noise * envelope).astype(np.float32)


def _person(ms: int) -> np.ndarray:
    """A voice clearly different from the cue: two tones, smooth loudness (no gaps)."""
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    tremolo = 0.6 + 0.4 * np.sin(2.0 * np.pi * t / 0.9)
    sig = (0.20 * np.sin(2.0 * np.pi * 180.0 * t)
           + 0.12 * np.sin(2.0 * np.pi * 700.0 * t)) * tremolo
    return sig.astype(np.float32)


def _mic(total_ms: int, *parts: tuple[int, np.ndarray]) -> bytes:
    buf = np.zeros(int(RATE * total_ms / 1000), dtype=np.float32)
    for start_ms, sig in parts:
        start = int(RATE * start_ms / 1000)
        end = min(buf.size, start + sig.size)
        buf[start:end] += sig[: end - start]
    return (np.clip(buf, -0.999, 0.999) * 32767.0).astype("<i2").tobytes()


def _feed(gate: UtteranceGate, pcm: bytes) -> tuple[bytes, float] | None:
    out = None
    for start in range(0, len(pcm), BLOCK):
        result = gate.feed(pcm[start : start + BLOCK])
        if result is not None:
            out = result
    return out


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def _as_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _retained_energy(segment: np.ndarray, speech: np.ndarray) -> float:
    """Share of `speech`'s energy present in `segment` at its best alignment."""
    if speech.size == 0 or segment.size < speech.size:
        return 0.0
    best_lag, best_score = 0, -1.0
    for lag in range(0, segment.size - speech.size + 1, 40):
        window = segment[lag : lag + speech.size]
        denom = float(np.linalg.norm(window)) * float(np.linalg.norm(speech))
        if denom <= 0.0:
            continue
        score = float(np.dot(window, speech) / denom)
        if score > best_score:
            best_lag, best_score = lag, score
    ref_energy = float(np.sum(np.square(speech)))
    got = float(np.sum(np.square(segment[best_lag : best_lag + speech.size])))
    return got / ref_energy if ref_energy > 0 else 0.0


def _cue_gate_line(caplog) -> str:
    lines = [r.getMessage() for r in caplog.records if "cue_gate:" in r.getMessage()]
    assert lines, "the cue_gate line is how the bridge calibrates overlap_from"
    return lines[-1]


def _run_overlap_case(make_gate, *, keep: bool, caplog) -> tuple[UtteranceGate, np.ndarray]:
    gate = make_gate(keep_overlapping_speech=keep)
    cue = _cue()
    person = _person(1160)  # starts with the cue and keeps talking past it
    gate.set_echo_reference(np.round(cue * 32767.0).astype("<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)
    mic = _mic(
        HOLD_MS + 1400,
        (CUE_START_MS, cue),
        (CUE_START_MS + 240, person),  # 240 ms into the cue: person at 340 ms
    )
    with caplog.at_level(logging.INFO, logger="rui.audio.vad"):
        captured = _feed(gate, mic)
    assert captured is not None, "the person's command must still form a turn"
    return gate, _as_float(captured[0])


def test_overlapping_speech_is_kept_from_its_first_block(make_gate, caplog) -> None:
    gate, segment = _run_overlap_case(make_gate, keep=True, caplog=caplog)
    line = _cue_gate_line(caplog)
    overlap = int(line.split("overlap_from=")[1].split()[0])
    # Person starts at 100+240 = 340 ms; the two-block confirmation returns that point.
    assert 320 <= overlap <= 360, line
    retained = _retained_energy(segment, _person(1160))
    assert retained >= 0.9, f"the command spoken over the cue was cut (kept {retained:.0%})"
    # Only the pure-cue prefix (before the person) is gone: the cue's first tone (its
    # first 200 ms) must not be in the retained audio.
    first_tone = _cue()[: int(RATE * 0.200)]
    first_tone_corr = _best_corr(segment, first_tone)
    assert first_tone_corr < 0.5, f"the dropped cue prefix came back ({first_tone_corr:.2f})"


def test_the_rollback_key_restores_the_whole_cue_drop(make_gate, caplog) -> None:
    _gate, segment_new = _run_overlap_case(make_gate, keep=True, caplog=caplog)
    caplog.clear()
    _gate_old, segment_old = _run_overlap_case(make_gate, keep=False, caplog=caplog)
    new_kept = _retained_energy(segment_new, _person(1160))
    old_kept = _retained_energy(segment_old, _person(1160))
    assert new_kept >= 0.9
    assert old_kept <= 0.8, "with the rollback key the early words are cut as before"
    assert "overlap_from=none" in _cue_gate_line(caplog)


def test_pure_cue_is_excised_exactly_as_before(make_gate, caplog) -> None:
    """No person over the cue: the whole cue (plus reverb guard) is still dropped."""
    gate = make_gate(keep_overlapping_speech=True)
    cue = _cue()
    reply = _person(600)
    gate.set_echo_reference(np.round(cue * 32767.0).astype("<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)
    mic = _mic(
        HOLD_MS + 1400,
        (CUE_START_MS, cue),
        (CUE_START_MS + CUE_MS + 200, reply),  # person speaks only after the cue
    )
    with caplog.at_level(logging.INFO, logger="rui.audio.vad"):
        captured = _feed(gate, mic)
    assert captured is not None
    segment = _as_float(captured[0])
    retained = _retained_energy(segment, reply)
    assert retained >= 0.9, f"a reply after the cue was cut (kept {retained:.0%})"
    line = _cue_gate_line(caplog)
    assert "overlap_from=none" in line
    # The cue's first tone never reaches ASR: the drop window still covers the cue end.
    cue_corr = _best_corr(segment, _as_float(
        np.round(cue * 32767.0).astype("<i2").tobytes()))
    assert cue_corr < 0.4, f"the cue reached ASR (corr={cue_corr:.2f})"


def test_an_unlocatable_cue_keeps_the_conservative_window(make_gate, caplog) -> None:
    """Cue audible late and clipped: matched=False, contaminated=True -> old fallback."""
    gate = make_gate(keep_overlapping_speech=True)
    cue = _cue()
    reply = _person(600)
    gate.set_echo_reference(np.round(cue * 32767.0).astype("<i2"), RATE)
    gate.hold(1740, skip_ms=CUE_MS + 250)
    mic = _mic(
        1740 + 1400,
        (1050, np.clip(cue * 12.0, -0.995, 0.995)),  # past both search bounds, saturated
        (1700, reply),
    )
    with caplog.at_level(logging.INFO, logger="rui.audio.vad"):
        captured = _feed(gate, mic)
        if captured is None:
            captured = _feed(gate, _silence(2000))
    line = _cue_gate_line(caplog)
    assert "fallback=true" in line
    assert "overlap_from=none" in line
    if captured is not None:
        # The conservative window ends at cue_ms + 1000 ms from the handover, so a reply
        # that starts before it is lost with it (documented); the cue itself must never
        # become the transcript.
        cue_corr = _best_corr(_as_float(captured[0]), _as_float(
            np.round(cue * 32767.0).astype("<i2").tobytes()))
        assert cue_corr < 0.4, f"the cue reached ASR (corr={cue_corr:.2f})"


def _best_corr(observed: np.ndarray, reference: np.ndarray) -> float:
    if reference.size == 0 or observed.size < reference.size:
        return 0.0
    best = 0.0
    ref_norm = float(np.linalg.norm(reference))
    for lag in range(0, observed.size - reference.size + 1, 40):
        window = observed[lag : lag + reference.size]
        denom = float(np.linalg.norm(window)) * ref_norm
        if denom <= 0.0:
            continue
        best = max(best, float(np.dot(window, reference) / denom))
    return best


def test_overlap_ratio_is_the_documented_constant() -> None:
    # The bridge calibration greps `overlap_from`; the ratio is the knob behind it.
    assert CUE_OVERLAP_RATIO == pytest.approx(1.6)
