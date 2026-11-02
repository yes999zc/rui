"""Segment speaker gate (0.8.16 P1): four layers between a VAD segment and a turn.

The decisions are exercised with injected embedders (no sherpa-onnx in CI); the wake
path integration is asserted against a stub `SpeakerGate`, so the contract under test is
"a rejected segment stays silent", not the model's accuracy.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import numpy as np
import pytest

from rui.audio.process import AudioPlane
from rui.audio.protocol import FrameType, parse_header
from rui.audio.speaker_gate import (
    SegmentDecision,
    SpeakerGate,
    SpeakerLibrary,
    WakeDecision,
)
from rui.config.schema import Config, SpeakerGateConfig

RATE = 16000


def _pcm(ms: int) -> bytes:
    n = int(RATE * ms / 1000)
    return (np.zeros(n, dtype=np.int16) + 1000).astype("<i2").tobytes()


def _unit(*values: float) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    return arr / np.linalg.norm(arr)


class ScriptedEmbedder:
    """Returns the next vector on each call (last one repeats), or None when failing."""

    def __init__(self, *vectors: np.ndarray, fail: bool = False) -> None:
        self.vectors = [np.asarray(v, dtype=np.float32) for v in vectors] or [
            np.zeros(2, dtype=np.float32)
        ]
        self.fail = fail
        self.calls: list[int] = []

    @property
    def available(self) -> bool:
        return True

    @property
    def dim(self) -> int:
        return int(self.vectors[0].size)

    def embed(self, pcm, rate: int) -> np.ndarray | None:  # noqa: ARG002
        self.calls.append(len(pcm))
        if self.fail:
            return None
        if len(self.vectors) > 1:
            return self.vectors.pop(0)
        return self.vectors[0]


def _library(tmp_path: Path, *profiles: tuple[str, str, np.ndarray]) -> SpeakerLibrary:
    library = SpeakerLibrary(tmp_path / "data")
    library.load()
    for name, device, vector in profiles:
        library.add(name, device, vector)
    return library


def _cfg(**overrides) -> SpeakerGateConfig:
    values = {"enabled": True, "model_path": "/models/campplus.onnx"}
    values.update(overrides)
    return SpeakerGateConfig(**values)


def _gate(tmp_path: Path, embedder, *, cfg: SpeakerGateConfig | None = None,
          library: SpeakerLibrary | None = None) -> SpeakerGate:
    lib = library if library is not None else _library(
        tmp_path, ("dad", "living", _unit(1.0, 0.0))
    )
    return SpeakerGate(cfg or _cfg(), lib, embedder, device_id="living")


# ------------------------------------------------------------------- config
def test_new_defaults_are_the_documented_ones() -> None:
    cfg = Config()
    assert cfg.audio.voice.vad_max_speech_ms == 8000
    assert cfg.audio.voice.speaker_gate.segment_gate is True
    assert cfg.audio.voice.speaker_gate.wake_window_ms == 2500
    assert cfg.ack.keep_overlapping_speech is True


def test_out_of_range_values_are_rejected() -> None:
    from pydantic import ValidationError

    from rui.config.schema import AckConfig, SpeakerGateConfig, VoiceConfig

    with pytest.raises(ValidationError):
        VoiceConfig(vad_max_speech_ms=1000)
    with pytest.raises(ValidationError):
        VoiceConfig(vad_max_speech_ms=40000)
    with pytest.raises(ValidationError):
        SpeakerGateConfig(wake_window_ms=20000)
    with pytest.raises(ValidationError):
        AckConfig(keep_overlapping_speech="maybe")  # type: ignore[arg-type]


# ------------------------------------------------------------- segment layers
def test_segment_gate_off_is_a_no_op(tmp_path: Path) -> None:
    embedder = ScriptedEmbedder(_unit(0.0, 1.0))
    gate = _gate(tmp_path, embedder, cfg=_cfg(segment_gate=False))
    decision = gate.evaluate_segment(_pcm(1000), RATE)
    assert (decision.accepted, decision.reason) == (True, "disabled")
    assert embedder.calls == []


def test_matching_segment_is_admitted(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.05)))
    decision = gate.evaluate_segment(_pcm(1200), RATE)
    assert decision.accepted is True
    assert decision.reason == "match"
    assert decision.best == "dad"
    assert decision.chunks == 1
    assert decision.window is False


def test_unknown_media_voice_is_rejected_as_speaker(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(0.0, 1.0)))
    decision = gate.evaluate_segment(_pcm(1200), RATE, since_wake_ms=4000)
    assert decision.accepted is False
    assert decision.reason == "speaker"
    assert decision.score == pytest.approx(0.0, abs=1e-6)
    assert decision.best == "dad"


def test_a_long_media_segment_is_scored_per_chunk(tmp_path: Path) -> None:
    """9.38 s with one matching and one foreign chunk: the whole segment is rejected."""
    embedder = ScriptedEmbedder(_unit(1.0, 0.0), _unit(0.0, 1.0))
    gate = _gate(tmp_path, embedder)
    decision = gate.evaluate_segment(_pcm(9380), RATE, max_speech_ms=8000)
    assert decision.accepted is False
    assert decision.reason == "chunk"
    assert decision.chunks == 2
    assert len(embedder.calls) == 2, "both chunks must be scored, not just the first"
    assert embedder.calls[0] == 8000 * RATE // 1000  # samples, float input
    assert embedder.calls[1] == 1380 * RATE // 1000


def test_all_chunks_must_match(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0), _unit(1.0, 0.1)))
    decision = gate.evaluate_segment(_pcm(9380), RATE, max_speech_ms=8000)
    assert (decision.accepted, decision.reason, decision.chunks) == (True, "match", 2)


def test_wake_window_trusts_only_the_anchor(tmp_path: Path) -> None:
    """A voice enrolled in the library but not the wake anchor is refused in-window."""
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0)))
    gate.set_anchor(_unit(0.0, 1.0), "mom")
    decision = gate.evaluate_segment(_pcm(1200), RATE, since_wake_ms=1000)
    assert decision.accepted is False
    assert decision.reason == "wake_window"
    assert decision.window is True
    assert decision.anchor_score == pytest.approx(0.0, abs=1e-6)
    assert decision.best == "dad"


def test_outside_the_wake_window_the_library_counts_again(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0)))
    gate.set_anchor(_unit(0.0, 1.0), "mom")
    decision = gate.evaluate_segment(_pcm(1200), RATE, since_wake_ms=4000)
    assert decision.accepted is True
    assert decision.window is False


def test_wake_window_zero_disables_the_layer(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0)), cfg=_cfg(wake_window_ms=0))
    gate.set_anchor(_unit(0.0, 1.0), "mom")
    decision = gate.evaluate_segment(_pcm(1200), RATE, since_wake_ms=100)
    assert decision.accepted is True
    assert decision.window is False


def test_wake_owner_mode_uses_the_anchor_only(tmp_path: Path) -> None:
    # Library says dad, anchor says somebody else. With the fallback off, wake_owner
    # follows the anchor and refuses the library voice.
    gate = _gate(
        tmp_path,
        ScriptedEmbedder(_unit(1.0, 0.0)),
        cfg=_cfg(
            mode="wake_owner", wake_window_ms=0, accept_anchor_or_library=False
        ),
    )
    gate.set_anchor(_unit(0.0, 1.0), "mom")
    decision = gate.evaluate_segment(_pcm(1200), RATE, since_wake_ms=4000)
    assert decision.accepted is False
    assert decision.reason == "speaker"

    # `accept_anchor_or_library=true` (the default) keeps the library as the fallback.
    gate2 = _gate(
        tmp_path,
        ScriptedEmbedder(_unit(1.0, 0.0)),
        cfg=_cfg(mode="wake_owner", wake_window_ms=0, accept_anchor_or_library=True),
    )
    gate2.set_anchor(_unit(0.0, 1.0), "mom")
    assert gate2.evaluate_segment(_pcm(1200), RATE, since_wake_ms=4000).accepted is True


def test_the_anchor_layer_fails_open_without_an_anchor(tmp_path: Path) -> None:
    """A barge wake skips the wake gate, so it has no anchor: never deafen the turn."""
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0)))
    decision = gate.evaluate_segment(_pcm(1200), RATE, since_wake_ms=1000)
    assert decision.accepted is True
    assert decision.reason == "no_anchor"


def test_no_wake_clock_falls_back_to_the_library_only(tmp_path: Path) -> None:
    """Device wake / follow-up: no anchor and no wake window, so the library decides."""
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(0.0, 1.0)))
    decision = gate.evaluate_segment(_pcm(1200), RATE)  # since_wake_ms=None
    assert decision.accepted is False
    assert decision.reason == "speaker"
    assert decision.window is False

    matching = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0)))
    accepted = matching.evaluate_segment(_pcm(1200), RATE)
    assert (accepted.accepted, accepted.reason, accepted.best) == (True, "match", "dad")


def test_segment_gate_fails_open_without_a_model(tmp_path: Path) -> None:
    gate = _gate(tmp_path, None)
    decision = gate.evaluate_segment(_pcm(1200), RATE)
    assert (decision.accepted, decision.reason) == (True, "unavailable")


def test_segment_gate_fails_open_without_an_embedding(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0), fail=True))
    decision = gate.evaluate_segment(_pcm(1200), RATE)
    assert (decision.accepted, decision.reason) == (True, "no_embedding")


def test_anchor_helpers(tmp_path: Path) -> None:
    gate = _gate(tmp_path, ScriptedEmbedder(_unit(1.0, 0.0)))
    gate.set_anchor(_unit(0.0, 1.0), "mom")
    assert gate.anchor_score(_unit(0.0, 1.0)) == pytest.approx(1.0)
    assert gate.anchor_score(_unit(1.0, 0.0)) == pytest.approx(0.0)
    gate.clear_anchor()
    assert gate.anchor_score(_unit(0.0, 1.0)) == 0.0


# --------------------------------------------------- the wake path integration
class FakeVadGate:
    """The minimal `UtteranceGate` surface `_feed_gate` touches."""

    def __init__(self, finished: tuple[bytes, float] | None) -> None:
        self.finished = finished
        self.armed = True
        self.idle_events = 0
        self.cfg = type("Cfg", (), {"max_wait_ms": 5000})()

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.armed = False
        return self.finished


class FakeWriter:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None


class StubSegmentGate:
    def __init__(self, decision: SegmentDecision) -> None:
        self.decision = decision
        self.calls: list[float | None] = []

    def evaluate_segment(self, pcm, rate, *, since_wake_ms=None, max_speech_ms=8000):  # noqa: ARG002
        self.calls.append(since_wake_ms)
        return self.decision


def _frames(data: bytes) -> list[tuple[FrameType, bytes]]:
    out: list[tuple[FrameType, bytes]] = []
    offset = 0
    while offset + 5 <= len(data):
        kind, length = parse_header(data[offset : offset + 5])
        out.append((kind, data[offset + 5 : offset + 5 + length]))
        offset += 5 + length
    return out


def _plane(tmp_path: Path, *, segment_gate: bool = True) -> AudioPlane:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.audio.voice.speaker_gate.enabled = True
    cfg.audio.voice.speaker_gate.segment_gate = segment_gate
    return AudioPlane(cfg, "living", tmp_path / "plane.sock")


def test_rejected_segment_stays_silent(tmp_path: Path, caplog) -> None:
    async def run() -> tuple[AudioPlane, FakeWriter, StubSegmentGate]:
        plane = _plane(tmp_path)
        stub = StubSegmentGate(
            SegmentDecision(False, 0.31, 0.22, None, "speaker", True, 3)
        )
        plane.speaker_gate = stub  # type: ignore[assignment]
        plane.gate = FakeVadGate((_pcm(20), 1000.0))  # type: ignore[assignment]
        plane._wake_audio_ts = 0.0
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        with caplog.at_level(logging.INFO, logger="rui.audio.process"):
            await plane._feed_gate(_pcm(20), ts_ms=1000.0, writer=writer,
                                   connected=connected)
        return plane, writer, stub

    plane, writer, stub = asyncio.run(run())
    assert plane.utterances == 0
    assert plane._turn_pending is False
    assert plane.turn_rejections == {"speaker": 1}
    # The window runs on the audio clock: 1000 ms segment end - 20 ms duration - wake.
    assert stub.calls == [pytest.approx(980.0)]
    kinds = [kind for kind, _ in _frames(writer.written)]
    assert kinds == [FrameType.CONTROL], "a rejected segment must not send UTTERANCE_PCM"
    payload = json.loads(_frames(writer.written)[0][1])
    assert payload["type"] == "wake_idle"
    assert payload["barge"] is False, "a silent close must not become「没听清」"
    assert any(
        "segment rejected: speaker score=0.31 anchor=0.22 best=none window=wake chunks=3 "
        "(threshold 0.55)" in r.getMessage()
        for r in caplog.records
    )


def test_admitted_segment_becomes_a_turn(tmp_path: Path) -> None:
    async def run() -> tuple[AudioPlane, FakeWriter]:
        plane = _plane(tmp_path)
        plane.speaker_gate = StubSegmentGate(  # type: ignore[assignment]
            SegmentDecision(True, 0.72, 0.80, "dad", "match", True, 1)
        )
        plane.gate = FakeVadGate((_pcm(20), 1000.0))  # type: ignore[assignment]
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._feed_gate(_pcm(20), ts_ms=1000.0, writer=writer, connected=connected)
        return plane, writer

    plane, writer = asyncio.run(run())
    assert plane.utterances == 1
    assert plane._turn_pending is True
    assert plane.turn_rejections == {}
    kinds = [kind for kind, _ in _frames(writer.written)]
    assert kinds == [FrameType.UTTERANCE_PCM]


def test_segment_gate_disabled_at_the_plane_level(tmp_path: Path) -> None:
    async def run() -> tuple[AudioPlane, StubSegmentGate]:
        plane = _plane(tmp_path, segment_gate=False)
        stub = StubSegmentGate(SegmentDecision(False, 0.0, 0.0, None, "speaker", False, 1))
        plane.speaker_gate = stub  # type: ignore[assignment]
        plane.gate = FakeVadGate((_pcm(20), 1000.0))  # type: ignore[assignment]
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._feed_gate(_pcm(20), ts_ms=1000.0, writer=writer, connected=connected)
        return plane, stub

    plane, stub = asyncio.run(run())
    assert stub.calls == [], "the rollback key must not even score the segment"
    assert plane.utterances == 1


def test_the_wake_anchor_is_set_from_the_admitted_wake(tmp_path: Path) -> None:
    class AnchorGate:
        def __init__(self) -> None:
            self.cfg = _cfg()
            self.last_wake_embedding = _unit(1.0, 0.0)
            self.anchors: list[tuple[np.ndarray, str | None]] = []
            self.cleared = 0

        def clear_anchor(self) -> None:
            self.cleared += 1

        def set_anchor(self, embedding, name=None) -> None:
            self.anchors.append((np.asarray(embedding), name))

        def evaluate_wake(self, pcm, rate, *, barge=False):  # noqa: ARG002
            return WakeDecision(True, 0.72, "dad", "match", 0.5)

    plane = _plane(tmp_path)
    gate = AnchorGate()
    plane.speaker_gate = gate  # type: ignore[assignment]
    assert plane._speaker_gate_admit(False) is True
    assert gate.cleared == 1
    assert len(gate.anchors) == 1
    assert gate.anchors[0][1] == "dad"
