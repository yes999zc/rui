"""The ack cue must never become the ASR input - not even when the device lags.

Real failure (2026-10-26, production 0.7.1, Dot2, `/tmp/rui-fleet3.log` L403-L425):

    wake admitted 07:01:44.656 -> cue 540 ms handed over 07:01:44.719
    [seg] 1.61 s rms=0.0461 peak=1.0000 speech=1.51 s   07:01:46.959
    turn: {'transcript': '在呢。', 'reply': '我在，你说。'}   07:01:48.716

The person said an instruction right after the wake word. The device starts the cue
~700 ms after the bytes are handed over, so almost the whole cue sits in the gate's
echo buffer. The old gate searched for the cue only in the nominal skip window, saw
less than 60 ms of it there, gave up, and replayed the cue into ASR.

These tests drive the real gate (fake Silero = energy VAD, as in
`test_ack_echo_gate.py`) and assert on the samples handed back for transcription.
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
CUE_MS = 540
SKIP_MS = CUE_MS + 250                 # cue + ACK_TAIL_MS, as process.py passes it
HOLD_MS = CUE_MS + 1200                # cue + ack_echo_ignore_ms (global default)
CUE = None  # filled by the fixture-free helper below


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
        gate.arm(skip_after_wake_ms=0)  # the KWS phrase tail is not this test's subject
        return gate

    return build


def _raw(seconds: float) -> bytes:
    with wave.open(str(Path(__file__).parent / "fixtures" / "command-weather.wav")) as w:
        raw = w.readframes(w.getnframes())
    out = raw
    while len(out) < seconds * RATE * 2:
        out += raw
    return out[: int(seconds * RATE) * 2]


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * RATE)


def _loud(pcm: bytes, gain: float = 3.0, level: float = 0.995) -> bytes:
    """Drive a signal into the microphone's ceiling (the log's peak=1.0000)."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    return (np.clip(samples * gain, -level, level) * 32768.0).astype("<i2").tobytes()


def _user(seconds: float) -> bytes:
    """A synthetic voice distinct from the cue fixture, so correlation is unambiguous."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    syllable = (np.sin(2.0 * np.pi * t / 0.5) > -0.2).astype(np.float32)
    sig = (0.32 * np.sin(2.0 * np.pi * 180.0 * t)
           + 0.18 * np.sin(2.0 * np.pi * 320.0 * t)
           + 0.08 * np.sin(2.0 * np.pi * 900.0 * t)) * syllable
    return (np.clip(sig, -0.99, 0.99) * 32768.0).astype("<i2").tobytes()


def _mix(total_s: float, *parts: tuple[float, bytes]) -> bytes:
    buf = np.zeros(int(total_s * RATE), dtype=np.float32)
    for offset_s, pcm in parts:
        x = _as_float(pcm)
        start = int(offset_s * RATE)
        end = min(buf.size, start + x.size)
        buf[start:end] += x[: end - start]
    return (np.clip(buf, -0.995, 0.995) * 32768.0).astype("<i2").tobytes()


def _as_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _feed(gate: UtteranceGate, pcm: bytes) -> tuple[bytes, float] | None:
    out = None
    for start in range(0, len(pcm), BLOCK):
        result = gate.feed(pcm[start : start + BLOCK])
        if result is not None:
            out = result
    return out


def _close(gate: UtteranceGate) -> tuple[bytes, float] | None:
    return _feed(gate, _silence(2.0))


def _best_corr(observed: np.ndarray, reference: np.ndarray) -> float:
    """Best normalized cross-correlation of `reference` inside `observed`."""
    if reference.size == 0 or observed.size < reference.size:
        return 0.0
    best = 0.0
    ref_norm = float(np.linalg.norm(reference))
    for lag in range(0, observed.size - reference.size + 1, 80):
        window = observed[lag : lag + reference.size]
        denom = float(np.linalg.norm(window)) * ref_norm
        if denom <= 0.0:
            continue
        best = max(best, float(np.dot(window, reference) / denom))
    return best


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
    got_energy = float(np.sum(np.square(segment[best_lag : best_lag + speech.size])))
    return got_energy / ref_energy if ref_energy > 0 else 0.0


def _max_windowed_corr(observed: np.ndarray, reference: np.ndarray,
                       window: int = RATE // 10, step: int = 80) -> float:
    """Largest correlation of any 100 ms slice of `reference` at any alignment.

    A partial cue tail (its last 490 ms, say) leaves the whole-signal correlation low
    while one 100 ms slice still matches almost perfectly; this catches that case.
    """
    if reference.size < window or observed.size < window:
        return 0.0
    best = 0.0
    for lag in range(0, observed.size - window + 1, step):
        for off in range(0, reference.size - window + 1, window // 2):
            w = observed[lag : lag + window]
            c = reference[off : off + window]
            denom = float(np.linalg.norm(w)) * float(np.linalg.norm(c))
            if denom <= 0.0:
                continue
            best = max(best, float(np.dot(w, c) / denom))
    return best


def _run_sequence(gate: UtteranceGate, mic: bytes) -> tuple[bytes, float] | None:
    return _feed(gate, mic)


def test_the_logged_failure_never_reaches_asr(make_gate) -> None:
    """Cue 540 ms audible only from 850 ms, person asks at 1.5 s, capture clipped.

    The whole cue lands in the gate's echo buffer: the skip window sees only silence.
    The OLD gate found nothing there and replayed the cue into ASR (whole-segment cue
    correlation 1.00). The new gate cannot locate it either (the search is capped at
    800 ms so the person's reply is never matched), but the clipped capture marks the
    window contaminated: it removes the cue's worst-case window (cue+1000 ms from the
    handover) and keeps the person's first word.
    """
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    reply = _loud(_user(1.0))
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    mic = (
        _silence(0.85)                    # device playback lag: cue starts in the buffer
        + _loud(cue)                      # cue audible 0.85..1.39 s (log: peak=1.0000)
        + _silence(0.11)                  # cue ends at 1.39 s
        + reply                           # the person starts at 1.50 s
        + _silence(1.4)                   # close the turn
    )
    captured = _run_sequence(gate, mic)
    if captured is None:
        captured = _close(gate)

    assert gate.turns == 1, "the turn the person actually asked for was lost"
    assert captured is not None
    segment = _as_float(captured[0])
    cue_corr = _best_corr(segment, _as_float(cue))
    assert cue_corr < 0.4, f"the cue reached ASR (corr={cue_corr:.2f})"
    tail_corr = _max_windowed_corr(segment, _as_float(cue))
    assert tail_corr < 0.6, (
        f"a cue fragment reached ASR (100 ms window corr={tail_corr:.2f})")
    retained = _retained_energy(segment, _as_float(reply))
    assert retained >= 0.8, f"the person's first word was cut (kept {retained:.0%})"


def test_a_clipped_overlap_never_feeds_the_cue_to_asr(make_gate) -> None:
    """Person talks over the cue and the microphone clips: deterministic, no cue.

    The cue cannot be located in that audio. The gate removes the cue's worst-case
    window and keeps what follows; whether the remaining words form a turn or the
    wake times out, the cue itself must never be the transcript.
    """
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    # Cue audible 0.55..1.09 s; the person starts at 0.70 s, on top of it.
    mic = (
        _mix(2.8, (0.55, _loud(cue)), (0.70, _loud(_user(1.4))))
        + _silence(1.4)
    )
    captured = _run_sequence(gate, mic)
    if captured is None:
        captured = _close(gate)

    if captured is not None:
        segment = _as_float(captured[0])
        cue_corr = _best_corr(segment, _as_float(cue))
        assert cue_corr < 0.4, f"the overlapping cue reached ASR (corr={cue_corr:.2f})"
        tail_corr = _max_windowed_corr(segment, _as_float(cue))
        assert tail_corr < 0.6, (
            f"a cue fragment reached ASR (100 ms window corr={tail_corr:.2f})")


def test_a_muted_microphone_keeps_the_reply_untouched(make_gate) -> None:
    """No cue in the capture (device mutes its mic while cueing): drop nothing."""
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    reply = _user(1.0)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    mic = _silence(0.79) + reply + _silence(1.4)
    captured = _run_sequence(gate, mic)
    if captured is None:
        captured = _close(gate)

    assert gate.turns == 1
    assert captured is not None
    segment = _as_float(captured[0])
    assert _retained_energy(segment, _as_float(reply)) >= 0.95
    assert _max_windowed_corr(segment, _as_float(cue)) < 0.6


def test_a_silent_wake_with_a_muted_microphone_times_out(make_gate) -> None:
    """True silent wake (no cue in the capture): one WakeIdle, no turn - unchanged."""
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    assert _feed(gate, _silence(0.79 + 6.0)) is None
    assert gate.turns == 0
    assert gate.idle_events == 1, "a silent wake must still report WakeIdle"
    assert gate.armed is False


def test_a_cue_then_silence_produces_no_turn_or_segment(make_gate) -> None:
    """Cue echo removed and the person stays silent: no turn, no empty segment.

    The old code's silent disarm in this scenario came from the cue tail being
    misdetected as a short utterance (the P2-1 bug), not from the wake-without-speech
    path. This test pins only "no turn / no segment" so it adds no new event semantics.
    """
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    assert _feed(gate, _silence(0.85) + _loud(cue) + _silence(6.0)) is None
    assert gate.turns == 0, "silence after the cue produced a turn"
    assert gate.idle_events == 0, "a cue-heavy silent wake must disarm silently (old semantics)"
    assert gate.armed is False


def test_a_person_who_waits_for_the_cue_is_unchanged(make_gate) -> None:
    """Normal turn: nobody talks during the cue, so nothing beyond the cue is cut."""
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    reply = _user(1.0)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    mic = _silence(0.55) + cue + _silence(1.05) + reply + _silence(1.4)
    captured = _run_sequence(gate, mic)
    if captured is None:
        captured = _close(gate)

    assert gate.turns == 1
    assert captured is not None
    segment = _as_float(captured[0])
    assert _best_corr(segment, _as_float(cue)) < 0.4
    assert _max_windowed_corr(segment, _as_float(cue)) < 0.6
    assert _retained_energy(segment, _as_float(reply)) >= 0.95


def test_a_cue_at_the_950ms_boundary_is_conservatively_removed(make_gate, caplog) -> None:
    """Boundary case: the cue's onset is 950 ms after the handover, past the match bound.

    No match is possible there (search stops at 800 ms so the person's reply is never
    matched), but the clipped capture marks the echo window contaminated: the worst-case
    guard (cue+1000 ms) removes the cue and keeps the reply that starts after it.
    """
    import logging

    caplog.set_level(logging.INFO, logger="rui.audio.vad")
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    reply = _user(1.0)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)

    mic = (
        _silence(0.95)                    # cue audible 0.95..1.49 s: whole cue in buffer
        + _loud(cue)
        + _silence(0.11)
        + reply                           # the person starts at 1.60 s, after the cue
        + _silence(1.4)
    )
    captured = _feed(gate, mic)
    if captured is None:
        captured = _close(gate)

    line = next(r.getMessage() for r in caplog.records if "cue_gate:" in r.getMessage())
    assert "fallback=true" in line, line
    # The guard ends at skip 790 + (540 + 1000 - 790) = 1540 ms, i.e. past the cue end
    # (1490 ms) and before the reply (1600 ms).
    assert "dropped=[0,1540]ms" in line, line
    assert gate.turns == 1
    assert captured is not None
    segment = _as_float(captured[0])
    cue_corr = _best_corr(segment, _as_float(cue))
    assert cue_corr < 0.4, f"the boundary cue reached ASR (corr={cue_corr:.2f})"
    assert _max_windowed_corr(segment, _as_float(cue)) < 0.6
    retained = _retained_energy(segment, _as_float(reply))
    assert retained >= 0.8, f"the reply after the boundary cue was cut (kept {retained:.0%})"


def _one_wake(gate: UtteranceGate, cue: bytes, *, lag_ms: int,
              user_at_ms: int | None = None, clip_cue: bool = False,
              ) -> tuple[bytes, float] | None:
    """Run one wake on an existing gate: cue at `lag_ms`, optional reply at `user_at_ms`."""
    gate.arm(skip_after_wake_ms=0)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(HOLD_MS, skip_ms=SKIP_MS)
    cue_audio = _loud(cue) if clip_cue else cue
    mic = _silence(lag_ms / 1000) + cue_audio
    if user_at_ms is None:
        mic += _silence(6.0)
    else:
        gap = (user_at_ms - lag_ms - CUE_MS) / 1000
        mic += _silence(gap) + _user(1.0) + _silence(1.4)
    captured = _feed(gate, mic)
    if captured is None:
        captured = _close(gate)
    return captured


def _learn_lag(gate: UtteranceGate, cue: bytes, lag_ms: int) -> None:
    """One matched wake (no reply) so the gate learns this device's cue latency."""
    _one_wake(gate, cue, lag_ms=lag_ms)


@pytest.mark.parametrize("lag_ms,user_at_ms", [(850, 1500), (950, 1600)])
def test_an_unclipped_late_cue_never_reaches_asr(make_gate, lag_ms, user_at_ms) -> None:
    """Real risk range: quiet person (no clipping) and a 740-950 ms device lag.

    A muted-microphone replay must still pass (`test_ack_echo_gate.py`), so a fixed
    search bound cannot cover this. After two matched wakes (760/820 ms) the learned
    latency puts the cue in the narrow re-search window; the cue is removed and the
    reply that follows is kept.
    """
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    _learn_lag(gate, cue, 760)
    _learn_lag(gate, cue, 820)

    captured = _one_wake(gate, cue, lag_ms=lag_ms, user_at_ms=user_at_ms)

    assert captured is not None
    segment = _as_float(captured[0])
    cue_corr = _best_corr(segment, _as_float(cue))
    assert cue_corr < 0.4, f"the unclipped late cue reached ASR (corr={cue_corr:.2f})"
    assert _max_windowed_corr(segment, _as_float(cue)) < 0.6
    retained = _retained_energy(segment, _as_float(_user(1.0)))
    assert retained >= 0.8, f"the reply after the cue was cut (kept {retained:.0%})"
    assert gate.turns == 1


def test_the_learned_lag_is_a_bounded_ema_and_recovers_a_third_cue(make_gate) -> None:
    """760 then 820 ms -> EMA in range; an unmatched 850 ms cue is then recovered."""
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    _learn_lag(gate, cue, 760)
    _learn_lag(gate, cue, 820)

    assert gate._cue_lag_seen is True
    assert 700.0 <= gate._cue_lag_ms <= 850.0, gate._cue_lag_ms

    captured = _one_wake(gate, cue, lag_ms=850, user_at_ms=1500)
    assert captured is not None
    assert _best_corr(_as_float(captured[0]), _as_float(cue)) < 0.4


def _learn_on_geometry(gate: UtteranceGate, cue: bytes, lag_ms: int,
                       *, hold_ms: int, skip_ms: int) -> None:
    gate.arm(skip_after_wake_ms=0)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(hold_ms, skip_ms=skip_ms)
    _feed(gate, _silence(lag_ms / 1000) + cue + _silence(6.0))


def test_a_muted_reply_after_learning_the_lag_is_not_dropped(make_gate, caplog) -> None:
    """Blocking regression: learned EMA must not turn a muted-device reply into a cue.

    Geometry mirrors `test_ack_echo_gate.py` (cue 700 ms, hold 1690, skip 950): after
    learning 760/820 the narrow window covers 950, where the reply begins. The reply
    keeps sounding past the cue's length, so the quiet-after guard rejects the match.
    """
    import logging

    caplog.set_level(logging.INFO, logger="rui.audio.vad")
    gate = make_gate()
    cue = _raw(0.7)
    _learn_on_geometry(gate, cue, 760, hold_ms=1690, skip_ms=950)
    _learn_on_geometry(gate, cue, 820, hold_ms=1690, skip_ms=950)
    assert gate._cue_lag_seen is True

    gate.arm(skip_after_wake_ms=0)
    gate.set_echo_reference(np.frombuffer(cue, dtype="<i2"), RATE)
    gate.hold(1690, skip_ms=950)
    reply = _user(1.2)
    captured = _feed(gate, _silence(0.95) + reply + _silence(1.4))
    if captured is None:
        captured = _close(gate)

    assert captured is not None
    assert len(captured[0]) / 2 / RATE >= 1.2, "the muted-device reply was cut"
    lines = [r.getMessage() for r in caplog.records if "cue_gate:" in r.getMessage()]
    line = lines[-1]
    assert "dropped=[0,950]ms" in line, line
    assert "fallback=false" in line, line


def test_a_narrow_hit_does_not_update_the_learned_lag(make_gate) -> None:
    """A narrow/contaminated hit must not reinforce its own offset in the EMA."""
    gate = make_gate()
    cue = _raw(CUE_MS / 1000)
    _learn_lag(gate, cue, 760)
    _learn_lag(gate, cue, 820)
    learned = gate._cue_lag_ms

    _one_wake(gate, cue, lag_ms=870)  # only the narrow window can find this one

    assert gate._cue_lag_ms == learned, "a narrow hit moved the learned latency"
    assert gate._last_match_offset_ms == 860.0, gate._last_match_offset_ms
