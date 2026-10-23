"""Wake acknowledgement (S5a): the cue, the pre-wake replay, and the echo hold.

The three failure modes this pins down, in the order they cost the most:
  1. the cue blocks the frame consumer and eats microphone audio (~0.7 s on the old build)
  2. the pre-wake ring is replayed but the hold then treats it as echo
  3. a spoken cue is longer than the cap and mutes the microphone past the request
"""
from __future__ import annotations

import asyncio
import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from house_ear.audio import ack, chime
from house_ear.audio.kws import WakeHit
from house_ear.audio.process import AudioPlane
from house_ear.config import Config

# Optional model for the gate tests. Point HOUSE_EAR_TEST_MODELS at a directory that
# holds silero_vad.onnx (and kws/, sensevoice/ for the other suites); without it these
# tests skip instead of depending on one developer's home directory.
_TEST_MODELS = os.environ.get("HOUSE_EAR_TEST_MODELS", "").strip()
MODEL = Path(_TEST_MODELS) / "silero_vad.onnx" if _TEST_MODELS else Path("/nonexistent/silero_vad.onnx")
needs_vad = pytest.mark.skipif(
    not MODEL.exists(),
    reason="optional model suite: set HOUSE_EAR_TEST_MODELS to a dir with silero_vad.onnx",
)


def tone_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    n = int(seconds * rate)
    pcm = (np.sin(2 * np.pi * 440 * np.arange(n) / rate) * 8000).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ----------------------------------------------------------------------- the cue
def test_the_cue_is_long_enough_for_the_device_to_start_playing() -> None:
    """160 ms did not fill the client's 300 ms buffer: it sat silent, then came out
    together with the answer (heard on the real speaker, 2026-10-22)."""
    audio = chime.render(rate=48000)
    assert audio.dtype == np.float32
    seconds = audio.size / 48000
    assert audio.size / 48000 == pytest.approx(Config().ack.chime_ms / 1000, abs=0.005)
    assert seconds >= 0.30, "shorter than the client's start threshold"
    # normalised to the level that sits below the knee of the device playback path
    assert float(np.max(np.abs(audio))) == pytest.approx(chime.PEAK, rel=0.01)
    # a second render is identical: no noise, no RNG, no drift between wakes
    assert np.array_equal(audio, chime.render(rate=48000))


def test_duration_follows_the_preset_not_the_rate() -> None:
    for rate in (16000, 24000, 48000):
        audio = chime.render("soft2", rate=rate, duration_ms=300)
        assert audio.size / rate == pytest.approx(0.300, abs=0.005)


def test_trim_only_lowers_the_level() -> None:
    full = float(np.max(np.abs(chime.render("blip", rate=16000))))
    quiet = float(np.max(np.abs(chime.render("blip", rate=16000, trim=0.5))))
    loud = float(np.max(np.abs(chime.render("blip", rate=16000, trim=2.0))))
    assert quiet == pytest.approx(full * 0.5, rel=0.01)
    assert loud == pytest.approx(full, rel=0.01)  # clamped: never above PEAK


def test_an_unknown_preset_is_an_error_not_silence() -> None:
    with pytest.raises(KeyError):
        chime.render("no-such-preset", rate=16000)


# --------------------------------------------------------------- choosing a mode
def test_mode_none_renders_nothing(tmp_path: Path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "none"  # type: ignore[assignment]
    audio, mode, note = ack.render(cfg)
    assert mode == "none" and audio.size == 0 and note == ""


def test_missing_spoken_cue_falls_back_to_the_tone(tmp_path: Path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    audio, mode, note = ack.render(cfg)
    assert mode == "earcon"
    assert "unreadable" in note
    assert audio.size / cfg.audio.playback.rate == pytest.approx(cfg.ack.chime_ms / 1000, abs=0.01)


def test_oversized_spoken_cue_is_refused(tmp_path: Path) -> None:
    """The cap exists because a long cue mutes the microphone past the request."""
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    cfg.ack.max_ms = 600
    tone_wav(ack.ack_wav_path(cfg), 0.9)
    audio, mode, note = ack.render(cfg)
    assert mode == "earcon"
    assert "over the 600 ms cap" in note
    assert audio.size / cfg.audio.playback.rate == pytest.approx(cfg.ack.chime_ms / 1000, abs=0.01)


def test_spoken_cue_within_the_cap_is_resampled_to_the_playback_rate(tmp_path: Path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    tone_wav(ack.ack_wav_path(cfg), 0.4, rate=16000)
    audio, mode, note = ack.render(cfg)
    assert mode == "tts" and note == ""
    assert audio.size / cfg.audio.playback.rate == pytest.approx(0.4, abs=0.01)


# ------------------------------------------------------------------ the gate hold
def _blocks(pcm: bytes, size: int = 640):
    for i in range(0, len(pcm), size):
        yield pcm[i : i + size]


def _speech(seconds: float = 1.0) -> bytes:
    with wave.open(str(Path(__file__).parent / "fixtures" / "command-weather.wav")) as w:
        raw = w.readframes(w.getnframes())
    rate = 16000
    out = raw
    while len(out) < seconds * rate * 2:
        out += raw
    return out[: int(seconds * rate) * 2]


@needs_vad
def test_a_request_spoken_during_the_cue_is_buffered_not_dropped() -> None:
    """The hold must not be a deaf window.

    The old build tried a mute window first and had to replace it with an echo-tail buffer:
    a person answers the moment the cue ends, which is inside the window. What the hold is
    allowed to do is delay the *turn*, not throw the words away.
    """
    from house_ear.audio.vad import GateConfig, UtteranceGate

    gate = UtteranceGate(GateConfig(), MODEL)
    gate.arm()
    gate.hold(1200)  # the cue is still sounding
    for block in _blocks(b"\x00\x00" * 8000):  # 0.5 s: the cue and its tail
        assert gate.feed(block) is None  # nothing may open a turn while it sounds
    for block in _blocks(_speech(0.9)):  # the request starts inside the window
        gate.feed(block)
    out = None
    for block in _blocks(b"\x00\x00" * 48000):
        result = gate.feed(block)
        if result:
            out = result
    assert out is not None, "the request spoken during the cue was thrown away"
    assert len(out[0]) / 2 / 16000 > 0.3


@needs_vad
def test_preroll_replayed_into_the_gate_survives_the_hold() -> None:
    """Replay must come before the hold: those blocks are the person, not the speaker."""
    from house_ear.audio.vad import GateConfig, UtteranceGate

    gate = UtteranceGate(GateConfig(), MODEL)
    gate.arm()
    replayed = _speech(0.6)
    for block in _blocks(_speech(0.6)):
        gate.feed(block)  # replayed pre-wake audio (short: no utterance yet)
    gate.hold(600)
    out = None
    for block in _blocks(b"\x00\x00" * 16000 + b"\x00\x00" * 32000):
        result = gate.feed(block)
        if result:
            out = result
    # Live audio alone is silence, so any turn at all came from the replayed ring. It is
    # shorter than what was replayed: the 300 ms skip after the wake word is still spent.
    assert out is not None
    assert len(out[0]) / 2 / 16000 > 0.25
    assert gate.turns == 1 and len(replayed) > 0


# ----------------------------------------------------------------- the plane side
class FakeGate:
    """Records the *order* of arm / feed / hold: replay-before-hold is the invariant."""

    def __init__(self) -> None:
        self.armed = 0
        self.armed_state = False  # what gate.armed would be between arm() and a turn
        self.holds: list[float] = []
        self.fed = 0
        self.interrupted = 0
        self.speech_ms = 0.0
        self.events: list[str] = []

    def arm(self, **_kwargs) -> None:
        self.armed += 1
        self.armed_state = True
        self.events.append("arm")

    def hold(self, ms: float) -> None:
        self.holds.append(ms)
        self.events.append("hold")

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.fed += len(pcm)
        self.events.append("feed")
        return None


class FakeAdapter:
    def __init__(self, slow_s: float = 0.0) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self.slow_s = slow_s
        self.stops = 0

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        self.calls.append((len(pcm), sample_rate, channels))
        if self.slow_s:
            await asyncio.sleep(self.slow_s)

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1


class FakeWriter:
    def __init__(self) -> None:
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None


def make_plane(tmp_path: Path, mode: str = "earcon") -> AudioPlane:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = mode  # type: ignore[assignment]
    cfg.audio.aec.enabled = False
    return AudioPlane(cfg, "living", tmp_path / "plane.sock")


def test_wake_replays_the_preroll_then_holds() -> None:
    async def run() -> None:
        plane = make_plane(Path("/tmp"))
        gate = FakeGate()
        plane.gate = gate  # type: ignore[assignment]
        adapter = FakeAdapter()
        plane.adapter = adapter  # type: ignore[assignment]
        for i in range(5):  # 5 x 20 ms of pre-wake audio
            plane._keep_preroll(b"\x01\x00" * 320, ts_ms=900.0 + i * 20)
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()

        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=1000.0), writer, connected)

        assert gate.armed == 1
        assert gate.fed == 5 * 640  # every pre-wake block reached the gate
        assert gate.events == ["arm"] + ["feed"] * 5 + ["hold"], (
            "replay must happen before the hold: those blocks are the person, not the cue"
        )
        assert plane._preroll == type(plane._preroll)()  # ring consumed, not kept
        assert plane.preroll_replayed_ms == pytest.approx(100.0, abs=1.0)
        # The tone itself plus the tail floor: the device's own buffer keeps sounding for
        # ~300 ms after the last frame, so holding for exactly cue_ms opens the gate early.
        cue_ms = plane.cfg.ack.chime_ms
        assert gate.holds and gate.holds[0] == pytest.approx(cue_ms + 250, abs=10)
        assert writer.written  # the wake event reached the control plane
        await asyncio.sleep(0.05)  # let the cue task run
        assert adapter.calls and adapter.calls[0][1] == plane.cfg.audio.playback.rate

    asyncio.run(run())


def test_the_cue_does_not_block_the_frame_consumer() -> None:
    """A 0.5 s cue must cost ~0 ms of the capture loop, not 0.5 s of microphone audio."""

    async def run() -> None:
        plane = make_plane(Path("/tmp"))
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter(slow_s=0.5)  # type: ignore[assignment]  # a cue that takes real time to play
        started = time.perf_counter()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), FakeWriter(), asyncio.Event())
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1, f"the wake handler waited {elapsed:.3f}s for the speaker"
        assert plane._ack_task is not None and not plane._ack_task.done()
        plane._ack_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await plane._ack_task

    asyncio.run(run())


def test_spoken_cue_holds_the_gate_for_its_length_plus_the_echo_window(tmp_path: Path) -> None:
    async def run() -> None:
        plane = make_plane(tmp_path, mode="tts")
        plane.cfg.ack.echo_ignore_ms = 1200
        tone_wav(ack.ack_wav_path(plane.cfg), 0.5)
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter()  # type: ignore[assignment]
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), FakeWriter(), asyncio.Event())
        assert plane.ack_mode_effective == "tts"
        assert plane.gate.holds[0] == pytest.approx(500 + 1200, abs=15)

    asyncio.run(run())


def test_oversized_cue_reports_its_downgrade(tmp_path: Path) -> None:
    async def run() -> None:
        plane = make_plane(tmp_path, mode="tts")
        plane.cfg.ack.max_ms = 600
        tone_wav(ack.ack_wav_path(plane.cfg), 0.9)
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter()  # type: ignore[assignment]
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), FakeWriter(), asyncio.Event())
        assert plane.ack_mode_effective == "earcon"
        assert "cap" in plane.ack_error  # the console can show why it sounds like a tone

    asyncio.run(run())


# --------------------------------------------------------------- reply playback rate
def test_reply_pcm_is_resampled_from_the_voice_rate() -> None:
    """16 kHz TTS pushed as PLAY_PCM used to reach a 48 kHz device unchanged: 3x speed."""

    async def run() -> None:
        plane = make_plane(Path("/tmp"))
        adapter = FakeAdapter()
        plane.adapter = adapter  # type: ignore[assignment]
        voice = plane.cfg.audio.voice.rate
        pcm = np.zeros(voice // 5, dtype="<i2").tobytes()  # 200 ms at the voice rate
        task = asyncio.create_task(plane._speak_loop())
        await plane._speak_queue.put((pcm, True))
        await asyncio.sleep(0.1)
        plane._stop.set()
        await plane._speak_queue.put(None)
        await asyncio.wait_for(task, timeout=1.0)

        rate = plane.cfg.audio.playback.rate
        assert adapter.calls and adapter.calls[0][1] == rate
        played = sum(c[0] for c in adapter.calls)
        assert played / 2 / rate == pytest.approx(0.2, abs=0.01)

    asyncio.run(run())


# ------------------------------------------------------- pre-rendering (control plane)
class FakeTts:
    """Streams `seconds` of PCM at the voice rate, like a well-behaved vendor."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    async def synthesize(self, text: str, voice: str = ""):  # noqa: ARG002
        self.calls += 1
        n = int(self.seconds * 16000)
        t = np.arange(n) / 16000.0
        pcm = (np.sin(2 * np.pi * 440 * t) * 12000).astype("<i2")
        for start in range(0, n, 1600):
            yield pcm[start : start + 1600].tobytes()


def _wire_fake_tts(monkeypatch, seconds: float) -> FakeTts:
    from house_ear.session import ack as ack_module

    fake = FakeTts(seconds)
    monkeypatch.setattr(ack_module, "tts_chain", lambda _providers, _rate: [("fake", fake)])
    return fake


def test_ensure_ack_wav_writes_a_file_within_the_cap(tmp_path: Path, monkeypatch) -> None:
    from house_ear.session.ack import ensure_ack_wav

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    cfg.ack.max_ms = 600
    fake = _wire_fake_tts(monkeypatch, 0.4)

    note = asyncio.run(ensure_ack_wav(cfg))

    path = ack.ack_wav_path(cfg)
    assert path.exists(), note
    assert "400 ms" in note and "fake" in note
    assert fake.calls == 1
    audio, rate = __import__("house_ear.audio.wav", fromlist=["read_wav_mono"]).read_wav_mono(path)
    assert audio.size / rate == pytest.approx(0.4, abs=0.05)


def test_ensure_ack_wav_refuses_text_over_the_cap(tmp_path: Path, monkeypatch) -> None:
    from house_ear.session.ack import ensure_ack_wav

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    cfg.ack.max_ms = 600
    _wire_fake_tts(monkeypatch, 1.4)

    note = asyncio.run(ensure_ack_wav(cfg))

    assert not ack.ack_wav_path(cfg).exists()  # a stale long file must not survive
    assert "over the 600 ms cap" in note


def test_ensure_ack_wav_does_nothing_for_the_tone_modes(tmp_path: Path, monkeypatch) -> None:
    from house_ear.session.ack import ensure_ack_wav

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "earcon"  # type: ignore[assignment]
    fake = _wire_fake_tts(monkeypatch, 0.3)

    note = asyncio.run(ensure_ack_wav(cfg))

    assert "tone" in note and fake.calls == 0


def test_the_written_cue_contains_the_audio_the_provider_sent(tmp_path: Path, monkeypatch) -> None:
    """A byte-swapped or wrong-rate file would pass a duration-only assertion."""
    from house_ear.audio.wav import read_wav_mono
    from house_ear.session.ack import ensure_ack_wav

    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "nested" / "data")  # must be created
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    cfg.ack.max_ms = 600
    _wire_fake_tts(monkeypatch, 0.4)

    note = asyncio.run(ensure_ack_wav(cfg))

    path = ack.ack_wav_path(cfg)
    audio, rate = read_wav_mono(path)
    assert rate == cfg.audio.voice.rate, note
    assert float(np.max(np.abs(audio))) > 0.1  # the sine survived, not zeros
    leftovers = list(path.parent.glob("*.part"))
    assert not leftovers, f"atomic write left {leftovers} behind"
    assert not path.with_suffix(".wav.part").exists()


def test_an_unwritable_data_dir_is_a_downgrade_not_an_outage(tmp_path: Path, monkeypatch) -> None:
    """A read-only volume on the NAS must not take the control plane down at startup."""
    from house_ear.session.ack import ensure_ack_wav

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    cfg = Config()
    cfg.system.data_dir = str(blocker)  # data_dir is a file: writing ack.wav cannot work
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    _wire_fake_tts(monkeypatch, 0.3)

    note = asyncio.run(ensure_ack_wav(cfg))  # must not raise

    assert "tone" in note or "cannot write" in note


def test_a_stale_overlong_cue_is_removed(tmp_path: Path, monkeypatch) -> None:
    from house_ear.session.ack import ensure_ack_wav

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "tts"  # type: ignore[assignment]
    cfg.ack.max_ms = 600
    tone_wav(ack.ack_wav_path(cfg), 1.4)  # a file from a previous, longer setting
    _wire_fake_tts(monkeypatch, 1.4)

    note = asyncio.run(ensure_ack_wav(cfg))

    assert not ack.ack_wav_path(cfg).exists(), "an over-cap file must not survive"
    assert "cap" in note


def test_a_second_wake_supersedes_the_cue_in_flight() -> None:
    """Two wakes 100 ms apart must leave one cue playing, and say so in the counters."""

    async def run() -> None:
        plane = make_plane(Path("/tmp"))
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter(slow_s=0.4)  # type: ignore[assignment]
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), writer, connected)
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=100.0), writer, connected)
        assert plane.ack_attempts == 2
        assert plane.ack_skipped == 1
        # arm() on top of an open turn throws that buffer away; it must be counted, not lost
        assert plane.gate.interrupted == 1
        assert plane._ack_task is not None
        plane._ack_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await plane._ack_task

    asyncio.run(run())


def test_stop_audio_also_silences_the_cue() -> None:
    """A cue is playback: the barge-in path must be able to stop it."""

    async def run() -> None:
        plane = make_plane(Path("/tmp"))
        plane.gate = FakeGate()  # type: ignore[assignment]
        plane.adapter = FakeAdapter(slow_s=1.0)  # type: ignore[assignment]
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), FakeWriter(), asyncio.Event())
        assert plane._ack_task is not None and not plane._ack_task.done()
        await plane._stop_playback()
        assert plane._ack_task is None
        assert plane.ack_plays == 0  # it never finished, and the counters say so

    asyncio.run(run())


def test_the_wake_event_survives_the_wire_with_its_levels() -> None:
    """These fields were silently dropped by pydantic until they existed on the model."""
    from house_ear.audio.protocol import CONTROL, Wake, encode_json

    payload = encode_json(
        Wake(ts_ms=1234.0, keyword="你好小瑞", voice_rms_db=-33.5, ref_rms_db=-12.0,
             ack_mode="earcon")
    )
    back = CONTROL.validate_json(payload[5:])
    assert isinstance(back, Wake)
    assert (back.voice_rms_db, back.ref_rms_db, back.ack_mode) == (-33.5, -12.0, "earcon")


def test_a_preset_at_natural_length_keeps_its_own_duration() -> None:
    """`chime_ms: 0` is the escape hatch back to the lengths the old room was tuned on."""
    for name, preset in chime.PRESETS.items():
        audio = chime.render(name, rate=16000, duration_ms=0)
        assert audio.size / 16000 == pytest.approx(preset.natural_ms / 1000, abs=0.01), name


def test_an_unknown_preset_is_rejected_by_the_config_not_at_runtime() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Config(**{"ack": {"chime": "no-such-preset"}})
    assert Config(**{"ack": {"chime": "windchime"}}).ack.chime == "windchime"
