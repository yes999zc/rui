"""The wake-time speaker gate: library, decision, and the silent reject on the wake path.

The judgement logic is exercised with injected embedders so the suite runs without
sherpa-onnx (CI installs `dev` only). The one test that needs the real CAM++ model is
skipped unless the extra and the model file are both present.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import numpy as np
import pytest

from rui.audio.kws import WakeHit
from rui.audio.process import AudioPlane
from rui.audio.speaker_gate import (
    SPEAKER_PREROLL_HISTORY_MS,
    SPEAKER_WAKE_WINDOW_MS,
    SpeakerEmbedder,
    SpeakerGate,
    SpeakerLibrary,
    SpeakerUnavailable,
    WakeDecision,
)
from rui.config.schema import Config, SpeakerGateConfig

RATE = 16000


def _pcm(ms: int = 1000, freq: float = 220.0, amp: float = 0.2) -> bytes:
    n = RATE * ms // 1000
    t = np.arange(n, dtype=np.float32) / RATE
    wave = np.clip(amp * np.sin(2 * np.pi * freq * t), -1.0, 1.0)
    return (wave * 32767.0).astype("<i2").tobytes()


def _unit(*values: float) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    return arr / np.linalg.norm(arr)


class FakeEmbedder:
    """Returns one fixed vector: the gate logic is what is under test, not CAM++."""

    def __init__(self, vector: np.ndarray | None = None, *, fail: bool = False) -> None:
        self.vector = None if vector is None else np.asarray(vector, dtype=np.float32)
        self.fail = fail
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    @property
    def dim(self) -> int:
        return 0 if self.vector is None else int(self.vector.size)

    def embed(self, pcm, rate: int) -> np.ndarray | None:  # noqa: ARG002
        self.calls += 1
        return None if self.fail else self.vector


def _gate_cfg(**overrides) -> SpeakerGateConfig:
    values = {"enabled": True, "model_path": "/models/campplus.onnx"}
    values.update(overrides)
    return SpeakerGateConfig(**values)


def _library(tmp_path: Path, *profiles: tuple[str, str, np.ndarray]) -> SpeakerLibrary:
    library = SpeakerLibrary(tmp_path / "data")
    library.load()
    for name, device, vector in profiles:
        library.add(name, device, vector)
    return library


# ------------------------------------------------------------------ library
def test_library_roundtrips_through_an_atomic_file(tmp_path: Path) -> None:
    _library(tmp_path, ("dad", "living", _unit(1.0, 0.0, 0.0)))
    target = tmp_path / "data" / "speakers.json"
    assert target.exists()
    assert not target.with_name(target.name + ".tmp").exists(), "tmp file must be replaced"
    assert (target.stat().st_mode & 0o777) == 0o600

    reloaded = SpeakerLibrary(tmp_path / "data", threshold=0.7)
    reloaded.load()
    assert reloaded.count() == 1
    profile = reloaded.profiles()[0]
    assert (profile.name, profile.device_id, profile.dim) == ("dad", "living", 3)  # type: ignore[union-attr]
    assert profile.samples == 1  # type: ignore[union-attr]


def test_library_missing_file_is_empty_not_an_error(tmp_path: Path, caplog) -> None:
    library = SpeakerLibrary(tmp_path / "data")
    library.load()
    assert library.count() == 0
    assert library.best_match(_unit(1.0, 0.0)) == (0.0, None)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_library_corrupt_file_degrades_to_empty(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "speakers.json").write_text("{not json", encoding="utf-8")
    library = SpeakerLibrary(data)
    library.load()
    assert library.count() == 0
    # The gate then fails open; writing over the corrupted file recovers the library.
    library.add("dad", "living", _unit(1.0, 0.0))
    reloaded = SpeakerLibrary(data)
    reloaded.load()
    assert reloaded.count() == 1


def test_library_rejects_an_empty_name_or_zero_vector(tmp_path: Path) -> None:
    library = SpeakerLibrary(tmp_path / "data")
    library.load()
    with pytest.raises(ValueError):
        library.add("", "living", _unit(1.0, 0.0))
    with pytest.raises(ValueError):
        library.add("dad", "living", np.zeros(4, dtype=np.float32))


def test_best_match_scores_and_filters_by_device(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        ("dad", "living", _unit(1.0, 0.0)),
        ("mom", "dot2", _unit(0.0, 1.0)),
    )
    score, best = library.best_match(_unit(1.0, 0.1), "living")
    assert best == "dad"
    assert score > 0.95
    # The dot2 profile must not admit a wake on the living-room box: the best living
    # candidate scores 0, so the threshold rejects it (best stays for the log line).
    assert library.best_match(_unit(0.0, 1.0), "living") == (0.0, "dad")
    # No device filter sees every profile.
    assert library.best_match(_unit(0.0, 1.0), None)[1] == "mom"
    # An empty device_id profile is a household-wide voice.
    library.add("grandma", "", _unit(-1.0, 0.0))
    assert library.best_match(_unit(-1.0, 0.05), "dot2")[1] == "grandma"


def test_best_match_ignores_a_profile_with_the_wrong_dimension(tmp_path: Path) -> None:
    library = _library(tmp_path, ("dad", "living", _unit(1.0, 0.0)))
    assert library.best_match(_unit(1.0, 0.0, 0.0), "living") == (0.0, None)


def test_rerecording_the_same_voice_overwrites_it(tmp_path: Path) -> None:
    library = _library(tmp_path, ("dad", "living", _unit(1.0, 0.0)))
    library.add("dad", "living", _unit(0.0, 1.0), samples=3)
    assert library.count() == 1
    score, best = library.best_match(_unit(0.0, 1.0), "living")
    assert (best, score) == ("dad", pytest.approx(1.0))
    stored = SpeakerLibrary(tmp_path / "data")
    stored.load()
    assert stored.profiles()[0].samples == 3


def test_remove_scopes_by_device(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        ("dad", "living", _unit(1.0, 0.0)),
        ("dad", "dot2", _unit(1.0, 0.0)),
    )
    assert library.remove("dad", "living") == 1
    assert [p.device_id for p in library.profiles()] == ["dot2"]
    assert library.remove("dad") == 1
    assert library.count() == 0
    assert library.remove("nobody") == 0


def test_hot_reload_picks_up_an_enrolment_without_a_restart(tmp_path: Path) -> None:
    data = tmp_path / "data"
    reader = SpeakerLibrary(data)
    reader.load()
    assert reader.count() == 0
    assert reader.reload_if_changed() is False

    writer = SpeakerLibrary(data)  # Card B's process, same directory
    writer.load()
    writer.add("dad", "living", _unit(1.0, 0.0))
    # A different library object must see the new profile by mtime, and only once.
    assert reader.reload_if_changed() is True
    assert reader.count() == 1
    assert reader.reload_if_changed() is False


# --------------------------------------------------------------------- gate
def _gate(tmp_path: Path, cfg: SpeakerGateConfig, embedder, *, device="living"):
    library = _library(tmp_path, ("dad", "living", _unit(1.0, 0.0)))
    return SpeakerGate(cfg, library, embedder, device_id=device)


def test_gate_admits_a_matching_voice(tmp_path: Path) -> None:
    gate = _gate(tmp_path, _gate_cfg(), FakeEmbedder(_unit(1.0, 0.05)))
    decision = gate.evaluate_wake(_pcm(900), RATE)
    assert decision.accepted is True
    assert decision.reason == "match"
    assert decision.best == "dad"
    assert decision.score > 0.9
    assert decision.ms >= 0.0


def test_gate_rejects_a_mismatch_with_score_and_best(tmp_path: Path) -> None:
    gate = _gate(tmp_path, _gate_cfg(), FakeEmbedder(_unit(0.0, 1.0)))
    decision = gate.evaluate_wake(_pcm(900), RATE)
    assert decision.accepted is False
    assert decision.reason == "mismatch"
    assert decision.best == "dad"
    assert decision.score == pytest.approx(0.0, abs=1e-6)


def test_gate_threshold_is_the_decision_line(tmp_path: Path) -> None:
    vector = _unit(0.6, 0.8)
    # cosine(unit(1,0), vector) == 0.6
    assert _gate(tmp_path, _gate_cfg(threshold=0.55), FakeEmbedder(vector)).evaluate_wake(
        _pcm(900), RATE
    ).accepted is True
    assert _gate(tmp_path, _gate_cfg(threshold=0.65), FakeEmbedder(vector)).evaluate_wake(
        _pcm(900), RATE
    ).accepted is False


def test_gate_rejects_short_audio_before_embedding(tmp_path: Path) -> None:
    embedder = FakeEmbedder(_unit(1.0, 0.0))
    gate = _gate(tmp_path, _gate_cfg(min_speech_ms=600), embedder)
    decision = gate.evaluate_wake(_pcm(380), RATE)
    assert (decision.accepted, decision.reason) == (False, "short")
    assert embedder.calls == 0, "half a phrase must not reach the model"


def test_gate_fails_open_when_the_model_is_unavailable(tmp_path: Path, caplog) -> None:
    gate = _gate(tmp_path, _gate_cfg(), None)
    with caplog.at_level(logging.WARNING, logger="rui.audio.speaker_gate"):
        first = gate.evaluate_wake(_pcm(900), RATE)
        second = gate.evaluate_wake(_pcm(900), RATE)
    assert (first.accepted, first.reason) == (True, "unavailable")
    assert (second.accepted, second.reason) == (True, "unavailable")
    warnings = [r for r in caplog.records if "admitting without gating" in r.getMessage()]
    assert len(warnings) == 1, "the fail-open warning must be one line, not one per wake"


def test_gate_fails_open_with_an_empty_library(tmp_path: Path, caplog) -> None:
    library = SpeakerLibrary(tmp_path / "data")
    library.load()
    gate = SpeakerGate(_gate_cfg(), library, FakeEmbedder(_unit(1.0, 0.0)), device_id="living")
    with caplog.at_level(logging.WARNING, logger="rui.audio.speaker_gate"):
        decision = gate.evaluate_wake(_pcm(900), RATE)
    assert (decision.accepted, decision.reason) == (True, "empty")
    assert any("no enrolled speakers" in r.getMessage() for r in caplog.records)


def test_gate_fails_open_when_no_embedding_is_produced(tmp_path: Path) -> None:
    gate = _gate(tmp_path, _gate_cfg(), FakeEmbedder(fail=True))
    decision = gate.evaluate_wake(_pcm(900), RATE)
    assert (decision.accepted, decision.reason) == (True, "no_embedding")


def test_gate_skips_a_barge_wake_but_only_by_config(tmp_path: Path) -> None:
    embedder = FakeEmbedder(_unit(0.0, 1.0))  # would be rejected if scored
    gate = _gate(tmp_path, _gate_cfg(), embedder)
    assert gate.evaluate_wake(_pcm(900), RATE, barge=True).reason == "barge"
    assert embedder.calls == 0

    strict = _gate(tmp_path, _gate_cfg(skip_on_barge=False), embedder)
    decision = strict.evaluate_wake(_pcm(900), RATE, barge=True)
    assert (decision.accepted, decision.reason) == (False, "mismatch")


def test_gate_is_a_no_op_when_disabled(tmp_path: Path) -> None:
    embedder = FakeEmbedder(_unit(0.0, 1.0))
    gate = _gate(tmp_path, _gate_cfg(enabled=False), embedder)
    decision = gate.evaluate_wake(_pcm(900), RATE)
    assert (decision.accepted, decision.reason) == (True, "disabled")
    assert embedder.calls == 0


def test_gate_falls_back_to_all_devices_when_this_device_has_no_enrollment(
    tmp_path: Path, caplog
) -> None:
    """A house that enrolled three people in the living room must not deafen the rest."""
    library = _library(tmp_path, ("mom", "dot2", _unit(1.0, 0.0)))
    gate = SpeakerGate(_gate_cfg(), library, FakeEmbedder(_unit(1.0, 0.0)), device_id="living")
    with caplog.at_level(logging.INFO, logger="rui.audio.speaker_gate"):
        first = gate.evaluate_wake(_pcm(900), RATE)
        second = gate.evaluate_wake(_pcm(900), RATE)
    assert (first.accepted, first.reason, first.best) == (True, "match", "mom")
    assert second.accepted is True
    fallbacks = [
        r for r in caplog.records
        if "no enrollment for device=living; matching all devices" in r.getMessage()
    ]
    assert len(fallbacks) == 1, "the fallback is one info line per device, not per wake"


def test_gate_still_filters_by_device_when_this_device_has_enrollment(tmp_path: Path) -> None:
    library = _library(
        tmp_path,
        ("dad", "living", _unit(1.0, 0.0)),
        ("mom", "dot2", _unit(0.0, 1.0)),
    )
    gate = SpeakerGate(_gate_cfg(), library, FakeEmbedder(_unit(0.0, 1.0)), device_id="living")
    decision = gate.evaluate_wake(_pcm(900), RATE)
    # mom is enrolled, but only on dot2: the living-room box must not admit her there.
    assert (decision.accepted, decision.reason, decision.best) == (False, "mismatch", "dad")


def test_gate_fails_open_when_no_profile_is_comparable(tmp_path: Path, caplog) -> None:
    """A model swap changes the embedding dimension; stale profiles must not deafen."""
    library = _library(tmp_path, ("dad", "living", np.array([1.0, 0.0, 0.0], np.float32)))
    gate = SpeakerGate(_gate_cfg(), library, FakeEmbedder(_unit(1.0, 0.0)), device_id="living")
    with caplog.at_level(logging.WARNING, logger="rui.audio.speaker_gate"):
        first = gate.evaluate_wake(_pcm(900), RATE)
        second = gate.evaluate_wake(_pcm(900), RATE)
    assert (first.accepted, first.reason) == (True, "no_candidate")
    assert second.accepted is True
    warnings = [r for r in caplog.records if "speaker gate unavailable" in r.getMessage()]
    assert len(warnings) == 1, "the fail-open warning must be one line, not one per wake"


# --------------------------------------------------------------- embedder
def test_embedder_without_a_model_path_raises_unavailable() -> None:
    with pytest.raises(SpeakerUnavailable):
        SpeakerEmbedder("")
    with pytest.raises(SpeakerUnavailable):
        SpeakerEmbedder("/nonexistent/campplus.onnx")


class _FakeStream:
    """The three calls `SpeakerEmbedder.embed` makes on a sherpa stream."""

    def __init__(self) -> None:
        self.waveform = None

    def accept_waveform(self, sample_rate, waveform) -> None:  # noqa: ARG002
        self.waveform = np.asarray(waveform)

    def input_finished(self) -> None:
        pass


class _FakeExtractor:
    """Duck-typed extractor: exercises `embed()` without sherpa-onnx or a model."""

    def __init__(self, embedding: list[float]) -> None:
        self._embedding = np.asarray(embedding, dtype=np.float32)
        self.streams = 0

    def create_stream(self) -> _FakeStream:
        self.streams += 1
        return _FakeStream()

    def is_ready(self, stream) -> bool:  # noqa: ARG002
        return True

    def compute(self, stream) -> list[float]:  # noqa: ARG002
        return list(self._embedding)


def _fake_embedder() -> SpeakerEmbedder:
    embedder = object.__new__(SpeakerEmbedder)
    embedder._extractor = _FakeExtractor([0.1, 0.2, 0.3])  # type: ignore[attr-defined]
    embedder._dim = 3  # type: ignore[attr-defined]
    embedder.model_path = "fake"  # type: ignore[attr-defined]
    return embedder


def test_embed_accepts_int16_and_float32_arrays_not_only_bytes() -> None:
    """0.8.16 regression: `not pcm` raised ValueError on an array (enroll commit 500).

    The enroll path hands `embed()` an array read from the recorded wav, so the
    guard must not ask a multi-element array for its truth value.
    """
    embedder = _fake_embedder()
    pcm_bytes = _pcm(600)
    int16 = np.frombuffer(pcm_bytes, dtype="<i2").copy()
    float32 = int16.astype(np.float32) / 32768.0

    for audio in (pcm_bytes, int16, float32):
        embedding = embedder.embed(audio, RATE)
        assert embedding is not None
        assert embedding.shape == (3,)

    assert embedder.embed(np.zeros(0, dtype="<i2"), RATE) is None
    assert embedder.embed(b"", RATE) is None


def test_real_campplus_model_embeds_when_the_extra_is_installed() -> None:
    pytest.importorskip("sherpa_onnx")
    model = Path(os.environ.get("RUI_SPEAKER_MODEL", "/data/models/speaker/campplus.onnx"))
    if not model.exists():
        pytest.skip(f"speaker model not present: {model}")
    embedder = SpeakerEmbedder(model)
    embedding = embedder.embed(_pcm(1200), RATE)
    assert embedding is not None
    assert embedding.size > 0
    assert embedder.dim == embedding.size


# ------------------------------------------------------- the wake path itself
class FakeGate:
    """Records that the VAD gate was armed; the wake path's side of the contract."""

    def __init__(self) -> None:
        self.armed = 0
        self.armed_state = False
        self.holds: list[float] = []
        self.fed = 0
        self.interrupted = 0
        self.speech_ms = 0.0
        self.events: list[str] = []

    def arm(self, **_kwargs) -> None:
        self.armed += 1
        self.armed_state = True
        self.events.append("arm")

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:  # noqa: ARG002
        self.holds.append(ms)
        self.events.append("hold")

    def set_echo_reference(self, samples, rate: int) -> None:  # noqa: ARG002
        self.events.append("echo")

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.fed += len(pcm)
        self.events.append("feed")
        return None


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        self.calls.append((len(pcm), sample_rate))

    async def stop_playback(self, fade_ms: int = 120) -> None:  # noqa: ARG002
        return None


class FakeWriter:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None


class StubSpeakerGate:
    """Stands in for `SpeakerGate` so the wake path's ordering can be asserted."""

    def __init__(self, decision: WakeDecision, *, threshold: float = 0.55) -> None:
        self.decision = decision
        self.cfg = SpeakerGateConfig(enabled=True, threshold=threshold)
        self.calls: list[tuple[int, int, bool]] = []

    def evaluate_wake(self, pcm, rate: int, *, barge: bool = False) -> WakeDecision:
        self.calls.append((len(pcm), rate, barge))
        return self.decision


class FakeWake:
    """Records what the standby resume replays into the spotter."""

    def __init__(self) -> None:
        self.resets = 0
        self.fed: list[tuple[int, float]] = []

    def reset(self) -> None:
        self.resets += 1

    def accept(self, pcm: bytes, ts_ms: float = 0.0) -> list:
        self.fed.append((len(pcm), ts_ms))
        return []


def _plane(tmp_path: Path, *, gate_enabled: bool = False) -> AudioPlane:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.audio.voice.speaker_gate.enabled = gate_enabled
    return AudioPlane(cfg, "living", tmp_path / "plane.sock")


def _feed_preroll(plane: AudioPlane, blocks: int, *, start_ms: float = 0.0) -> None:
    block = b"\x01\x00" * 320  # 20 ms of s16 at 16 kHz
    for index in range(blocks):
        plane._keep_preroll(block, ts_ms=start_ms + index * 20)


def test_wake_rejected_by_the_speaker_gate_stays_completely_silent(tmp_path: Path, caplog) -> None:
    async def run() -> AudioPlane:
        plane = _plane(tmp_path)
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter()  # type: ignore[assignment]
        plane.speaker_gate = StubSpeakerGate(  # type: ignore[assignment]
            WakeDecision(False, 0.41, "son", "mismatch", 1.2)
        )
        _feed_preroll(plane, 5)
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        with caplog.at_level(logging.INFO, logger="rui.audio.process"):
            await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=100.0), writer, connected)
        await asyncio.sleep(0.02)
        return plane, writer

    plane, writer = asyncio.run(run())
    assert plane.speaker_gate is not None
    assert plane.speaker_gate.calls == [(5 * 640, RATE, False)]  # type: ignore[union-attr]
    assert plane.wake_rejections == {"speaker": 1}
    # Silence is the product requirement: no cue, no gate, no wake event, no turn.
    assert plane.gate is not None and plane.gate.armed == 0  # type: ignore[union-attr]
    assert plane.ack_attempts == 0
    assert plane.adapter is not None and plane.adapter.calls == []  # type: ignore[union-attr]
    assert writer.written == b""
    assert plane.preroll_replayed_ms == 0.0
    assert len(plane._preroll) == 5, "the gate must snapshot the ring, not drain it"
    assert any(
        "wake rejected: speaker score=0.41 best=son (threshold 0.55)" in r.getMessage()
        for r in caplog.records
    )


def test_wake_rejected_for_short_audio_logs_the_duration(tmp_path: Path, caplog) -> None:
    async def run() -> AudioPlane:
        plane = _plane(tmp_path)
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter()  # type: ignore[assignment]
        plane.speaker_gate = StubSpeakerGate(  # type: ignore[assignment]
            WakeDecision(False, 0.0, None, "short", 0.4)
        )
        _feed_preroll(plane, 5)
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        with caplog.at_level(logging.INFO, logger="rui.audio.process"):
            await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=100.0), writer, connected)
        return plane

    plane = asyncio.run(run())
    assert plane.wake_rejections == {"speaker": 1}
    assert plane.gate is not None and plane.gate.armed == 0  # type: ignore[union-attr]
    assert any("wake rejected: speaker short=100ms" in r.getMessage() for r in caplog.records)


def test_wake_admitted_by_the_gate_keeps_the_old_wake_path(tmp_path: Path) -> None:
    async def run() -> tuple[AudioPlane, FakeWriter, StubSpeakerGate]:
        plane = _plane(tmp_path, gate_enabled=True)
        gate, adapter = FakeGate(), FakeAdapter()
        plane.gate = gate  # type: ignore[assignment]
        plane.adapter = adapter  # type: ignore[assignment]
        stub = StubSpeakerGate(WakeDecision(True, 0.72, "dad", "match", 0.9))
        plane.speaker_gate = stub  # type: ignore[assignment]
        _feed_preroll(plane, 125)  # 2500 ms: longer than the replay window
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=2500.0), writer, connected)
        await asyncio.sleep(0.02)
        return plane, writer, stub

    plane, writer, stub = asyncio.run(run())
    assert plane.wake_rejections == {}
    assert plane.gate is not None and plane.gate.armed == 1  # type: ignore[union-attr]
    assert writer.written, "an admitted wake must still reach the control plane"
    assert plane.ack_attempts == 1
    assert plane.adapter is not None and plane.adapter.calls  # type: ignore[union-attr]
    # The gate scored the configured window, not the whole retained history.
    assert len(stub.calls) == 1
    scored_ms = stub.calls[0][0] // 2 * 1000.0 / RATE
    assert scored_ms == pytest.approx(SPEAKER_WAKE_WINDOW_MS, abs=20)
    # ... and the VAD replay window is still `wake_preroll_ms`.
    assert plane.preroll_replayed_ms == pytest.approx(500.0, abs=20)
    assert plane.cfg.audio.voice.wake_preroll_ms == 500


def test_preroll_history_grows_only_when_the_gate_is_on(tmp_path: Path) -> None:
    off = _plane(tmp_path)
    assert off._preroll_history_ms() == off.cfg.audio.voice.wake_preroll_ms
    on = _plane(tmp_path, gate_enabled=True)
    assert on._preroll_history_ms() >= SPEAKER_PREROLL_HISTORY_MS
    assert on._speaker_window_ms() >= SPEAKER_WAKE_WINDOW_MS


def test_standby_replay_does_not_feed_the_longer_speaker_ring(tmp_path: Path) -> None:
    """The 2 s ring exists for the speaker snapshot; the spotter replay stays 500 ms.

    Replaying the whole ring would let the KWS hear a wake it already reported and
    emit it a second time (duplicated cue / barge).
    """

    async def run() -> tuple[AudioPlane, FakeWake]:
        plane = _plane(tmp_path, gate_enabled=True)
        wake = FakeWake()
        plane.wake = wake  # type: ignore[assignment]
        _feed_preroll(plane, 125)  # 2500 ms of history, of which 2000 ms is retained
        await plane._reset_and_replay_spotter(FakeWriter(), asyncio.Event())
        return plane, wake

    plane, wake = asyncio.run(run())
    fed_ms = sum(size for size, _ in wake.fed) // 2 * 1000.0 / RATE
    assert wake.resets == 1
    assert 0 < fed_ms <= plane.cfg.audio.voice.wake_preroll_ms
    assert fed_ms == pytest.approx(500.0, abs=20)
    # The ring itself is untouched: the VAD replay still selects from it by timestamp.
    assert len(plane._preroll) == 100


def test_no_gate_means_the_old_wake_path_is_untouched(tmp_path: Path) -> None:
    async def run() -> AudioPlane:
        plane = _plane(tmp_path)
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter()  # type: ignore[assignment]
        _feed_preroll(plane, 5)
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=100.0), writer, connected)
        return plane

    plane = asyncio.run(run())
    assert plane.speaker_gate is None
    assert plane.wake_rejections == {}
    assert plane.gate is not None and plane.gate.armed == 1  # type: ignore[union-attr]
