"""0.8.16 ②: post-AEC voice gain with a hard limiter, and overdrive measured before it.

The far field needs ~6 dB before KWS/VAD/ASR, but the extra gain must not wrap and must
not hide a saturated capture: `overdrive` still measures the pre-gain AEC output, and a
new `limiter_count` counts the frames the gain pushes into the clamp.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from rui.audio.decimate import Decimator
from rui.audio.process import VOICE_LIMIT_LEVEL, AudioPlane
from rui.audio.protocol import Metrics
from rui.config.schema import Config
from rui.device.contract import AudioFrame

RATE = 16000
BLOCK_SAMPLES = 320  # 20 ms at 16 kHz


def _plane(tmp_path: Path, gain_db: float) -> AudioPlane:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.audio.voice.gain_db = gain_db
    cfg.audio.aec.enabled = False
    return AudioPlane(cfg, "living", tmp_path / "plane.sock")


def _frame(samples: np.ndarray) -> AudioFrame:
    pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2").tobytes()
    return AudioFrame(
        device_id="living",
        seq=0,
        ts_ms=0.0,
        pcm=pcm,
        channels=1,
        sample_rate=RATE,
        reference_channels=0,
    )


def _sine(ms: int, amp: float, freq: float = 220.0) -> np.ndarray:
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    return (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


def _run(plane: AudioPlane, samples: np.ndarray) -> np.ndarray:
    out = plane._frame_to_voice(_frame(samples), Decimator(1, 1))
    return np.frombuffer(out, dtype="<i2").astype(np.float32) / 32768.0


def test_gain_zero_is_byte_identical_to_the_old_output(tmp_path: Path) -> None:
    plane = _plane(tmp_path, gain_db=0.0)
    raw = np.array(
        [0, 1, -1, 1000, -1000, 12345, -12345, 32767, -32768, 25000, -25000],
        dtype="<i2",
    )
    pcm = np.resize(raw, BLOCK_SAMPLES).astype("<i2").tobytes()
    out = plane._frame_to_voice(
        AudioFrame(
            device_id="living", seq=0, ts_ms=0.0, pcm=pcm,
            channels=1, sample_rate=RATE, reference_channels=0,
        ),
        Decimator(1, 1),
    )
    assert out == pcm, "gain_db=0 must not even clamp: 0.8.15 bytes, exactly"
    assert plane.limiter_count == 0


def test_far_field_level_is_lifted_without_clipping(tmp_path: Path) -> None:
    plane = _plane(tmp_path, gain_db=6.0)
    signal = _sine(120, amp=0.0198)  # rms ~0.014, the 3.5 m measurement
    before_rms = float(np.sqrt(np.mean(np.square(signal))))
    out = _run(plane, signal)
    after_rms = float(np.sqrt(np.mean(np.square(out))))
    assert before_rms == pytest.approx(0.014, abs=0.001)
    assert after_rms / before_rms == pytest.approx(2.0, rel=0.02)
    assert float(np.abs(out).max()) < VOICE_LIMIT_LEVEL
    assert plane.limiter_count == 0


def test_limiter_counts_only_the_frames_it_clamps(tmp_path: Path) -> None:
    plane = _plane(tmp_path, gain_db=6.0)
    # 0.6 * 2 = 1.2: limited, but the capture itself was not saturated (0.6 < 0.97),
    # so this must not look like overdrive.
    out = _run(plane, _sine(20, amp=0.6))
    assert float(np.abs(out).max()) == pytest.approx(VOICE_LIMIT_LEVEL, abs=1 / 32768)
    assert plane.limiter_count == 1
    assert plane.overdrive_count == 0

    quiet = _run(plane, _sine(20, amp=0.1))
    assert plane.limiter_count == 1, "a frame below the limit must not count"
    assert float(np.abs(quiet).max()) == pytest.approx(0.2, abs=0.01)


def test_overdrive_still_sees_a_saturated_capture_through_the_gain(tmp_path: Path) -> None:
    plane = _plane(tmp_path, gain_db=6.0)
    signal = _sine(20, amp=1.0)
    for _ in range(5):  # OVERDRIVE_FRAMES consecutive frames
        _run(plane, signal)
    assert plane.overdrive_count == 1, "the gain must not hide capture saturation"
    assert plane.limiter_count == 5, "every frame was pushed into the clamp"


def test_limiter_is_exposed_in_metrics(tmp_path: Path) -> None:
    plane = _plane(tmp_path, gain_db=6.0)
    _run(plane, _sine(20, amp=0.6))
    assert "limiter_count" in Metrics.model_fields
    metrics = plane._build_metrics()
    assert metrics.limiter_count == 1


# ------------------------------------------------- residual bypass follows the turn
class RecordingAec:
    def __init__(self) -> None:
        self.holds = 0
        self.releases = 0
        # Read by the Wake event / diagnostics, not by the hold logic.
        self.erle_db = 0.0
        self.selected = 0
        self.ref_active = False

    def hold_for(self, seconds: float | None = None) -> None:  # noqa: ARG002
        self.holds += 1

    def release(self) -> None:
        self.releases += 1


class FakeVadGate:
    """The `UtteranceGate` surface `_begin_wake_turn` / `_feed_gate` touch."""

    def __init__(self, finished: tuple[bytes, float] | None = None) -> None:
        self.armed = True
        self.speaking = False
        self.armed_state = True
        self.idle_events = 0
        self.discarded = 0
        self.interrupted = 0
        self.speech_ms = 0.0
        self.silent_wakes = 0
        self.silent_wakes_session = 0
        self.finished = finished
        self.cfg = type("Cfg", (), {"max_wait_ms": 5000})()

    def arm(self, **_kwargs) -> None:
        self.armed = True

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:  # noqa: ARG002
        return None

    def set_echo_reference(self, samples, rate: int) -> None:  # noqa: ARG002
        return None

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.armed = False
        if self.finished is None:
            self.idle_events += 1
        return self.finished


class FakeAdapter:
    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:  # noqa: ARG002
        return None

    async def stop_playback(self, fade_ms: int = 120) -> None:  # noqa: ARG002
        return None


class FakeWriter:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None


def test_aec_release_ends_the_hold_immediately() -> None:
    from rui.audio.aec import AecChain, speex_available

    # CI installs no libspeexdsp: `AecChain` cannot be constructed there, and this
    # test is about the hold/release state machine, not about the canceller itself.
    if not speex_available():
        pytest.skip("libspeexdsp is not installed: AecChain is unavailable")

    chain = AecChain(channels=1, wake_hold_ms=15000)
    assert chain.holding is False
    chain.hold_for()
    assert chain.holding is True
    chain.release()
    assert chain.holding is False
    chain.close()

    disabled = AecChain(channels=1, wake_hold_ms=0)
    disabled.hold_for()
    assert disabled.holding is False, "0 is the rollback key: no bypass at all"
    disabled.close()


def test_the_plane_holds_at_the_wake_and_releases_at_every_turn_end(tmp_path: Path) -> None:
    from rui.audio.kws import WakeHit
    from rui.audio.speaker_gate import SegmentDecision

    class RejectingSegmentGate:
        def __init__(self) -> None:
            self.cfg = type("Cfg", (), {"threshold": 0.55})()

        def clear_anchor(self) -> None:
            return None

        def set_anchor(self, embedding, name=None) -> None:  # noqa: ARG002
            return None

        def evaluate_wake(self, pcm, rate, *, barge=False):  # noqa: ARG002
            from rui.audio.speaker_gate import WakeDecision

            return WakeDecision(True, 0.9, "dad", "match", 0.1)

        def evaluate_segment(self, pcm, rate, **kwargs):  # noqa: ARG002
            return SegmentDecision(False, 0.1, 0.1, None, "speaker", True, 1)

    plane = _plane(tmp_path, gain_db=6.0)
    aec = RecordingAec()
    plane.aec = aec  # type: ignore[assignment]
    plane.gate = FakeVadGate()  # type: ignore[assignment]
    plane.adapter = FakeAdapter()  # type: ignore[assignment]
    writer, connected = FakeWriter(), asyncio.Event()
    connected.set()

    async def run() -> None:
        # Wake: hold.
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), writer, connected)
        assert aec.holds == 1, "the wake must bypass residual suppression"
        assert aec.releases == 0

        # Segment reported: release.
        plane.gate = FakeVadGate((b"\x01\x00" * 320, 1000.0))  # type: ignore[assignment]
        await plane._feed_gate(b"\x00\x00" * 320, ts_ms=1000.0, writer=writer,
                               connected=connected)
        assert plane.utterances == 1
        assert aec.releases == 1

        # Segment rejected: release (and no turn).
        plane.speaker_gate = RejectingSegmentGate()  # type: ignore[assignment]
        plane.gate = FakeVadGate((b"\x01\x00" * 320, 2000.0))  # type: ignore[assignment]
        await plane._feed_gate(b"\x00\x00" * 320, ts_ms=2000.0, writer=writer,
                               connected=connected)
        assert plane.utterances == 1, "a rejected segment is not a turn"
        assert aec.releases == 2

        # Silent window: release.
        plane.speaker_gate = None
        plane.gate = FakeVadGate()  # type: ignore[assignment]
        plane._wake_armed = True
        await plane._feed_gate(b"\x00\x00" * 320, ts_ms=3000.0, writer=writer,
                               connected=connected)
        assert aec.releases == 3

        # Barge over the previous turn: release.
        await plane._stop_for_barge(0.0)
        assert aec.releases == 4

    asyncio.run(run())
