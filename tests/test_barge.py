"""Barge-in: a wake word while the speaker is talking must stop the talking.

The old deployment got this for free because its client killed `aplay` on the key press.
Here the plane has to drop what it queued and tell the device to stop, and it has to tell
the control plane too - otherwise the flushed queue refills from the answer that is still
being synthesised and the speaker talks over the new turn.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import numpy as np
import pytest

import rui.audio.process as process
from rui.audio.process import AudioPlane
from rui.config import Config


class StubAdapter:
    def __init__(self) -> None:
        self.stops = 0
        self.played = 0
        self.pcm_bytes = 0
        self.pcm = b""

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.played += 1
        self.pcm_bytes += len(pcm)
        self.pcm += pcm

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1

    def health(self) -> dict:
        return {"connected": True}


class StubWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


def make_plane() -> tuple[AudioPlane, StubAdapter]:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/barge.sock"))
    adapter = StubAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def queue_seconds(plane: AudioPlane, seconds: float) -> None:
    chunk = np.zeros(int(16000 * 0.1), dtype="<i2").tobytes()
    for _ in range(int(seconds / 0.1)):
        plane._speak_queued_s += 0.1
        plane._speak_queue.put_nowait((chunk, True))


def run_wake(plane: AudioPlane) -> list[dict]:
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
    asyncio.run(plane._on_wake(hit, writer, connected))  # type: ignore[arg-type]
    # Frames carry a 5-byte header (type + length); only control frames are JSON here.
    return [json.loads(f[5:].decode("utf-8")) for f in writer.frames if f[0] == 1]  # FrameType.CONTROL


def run_wake_and_settle(plane: AudioPlane, settle_s: float = 0.02) -> list[dict]:
    """`run_wake` that keeps the loop alive for the fire-and-forget cue task."""

    async def run() -> list[dict]:
        writer, connected = StubWriter(), asyncio.Event()
        connected.set()
        hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
        await plane._on_wake(hit, writer, connected)  # type: ignore[arg-type]
        await asyncio.sleep(settle_s)
        return [json.loads(f[5:].decode("utf-8")) for f in writer.frames if f[0] == 1]

    return asyncio.run(run())


class RecordingGate:
    """Records the wake bookkeeping a barge must keep: arm + preroll feed, no cue hold."""

    def __init__(self) -> None:
        self.armed = False
        self.arms = 0
        self.holds: list[float] = []
        self.fed = 0
        self.interrupted = 0
        self.speech_ms = 0.0
        self.echo_reference = None

    def arm(self, **_kwargs) -> None:
        self.armed = True
        self.arms += 1

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:  # noqa: ARG002
        self.holds.append(ms)

    def set_echo_reference(self, samples, rate: int) -> None:  # noqa: ARG002
        self.echo_reference = samples

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.fed += len(pcm)
        return None


def fake_ack(audio_ms: int = 33) -> tuple[list, object]:
    """A cue render that records its calls, so "rendered or not" is decisive."""
    calls: list = []

    def render(cfg):  # noqa: ANN001 - mirrors `render_ack(cfg)`
        calls.append(cfg)
        samples = int(48000 * audio_ms / 1000)
        return np.ones(samples, dtype="<i2"), "earcon", ""

    return calls, render


def test_a_barge_gets_a_short_cue_but_no_hold(monkeypatch) -> None:
    """2026-10-26: the full barge cue「在呢」 became the next turn (self-hearing loop).

    Logged on the Dot2: 12:07:03 barge -> cue -> 12:07:05 seg 「在呢。」 -> 12:07:07
    reply -> 12:07:12 barge again. A barge means the user is already talking, so the
    configured cue would talk over their first words and its echo feeds ASR. But a
    barge with no cue at all is the 2026-10-29 dot2-b failure: a clipped capture gave
    the user no feedback whatsoever. One short earcon (>= 80 ms, from the configured
    chime but capped) confirms the wake; no cue-length hold, echo reference or
    post-playback mute window is opened for it.
    """
    calls, render = fake_ack()
    monkeypatch.setattr(process, "render_ack", render)
    plane, adapter = make_plane()
    assert plane.cfg.ack.barge_ack is True, "the default must keep the barge audible"
    gate = RecordingGate()
    plane.gate = gate
    queue_seconds(plane, 2.0)

    frames = run_wake_and_settle(plane, settle_s=0.2)

    assert calls == [], "a barge rendered the configured ack cue"
    assert plane.ack_attempts == 1, "a barge started no cue at all"
    assert adapter.played > 0, "a barge handed no cue audio to the speaker"
    played_ms = adapter.pcm_bytes / 2 / plane.effective.playback.rate * 1000.0
    assert played_ms >= process.BARGE_CUE_MIN_MS, (
        f"the barge cue is {played_ms:.0f} ms, below the 80 ms floor")
    assert played_ms <= process.BARGE_CUE_MAX_MS, (
        f"the barge cue is {played_ms:.0f} ms, above the short-cue cap")
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True
    assert wake["ack_mode"] == "earcon", "the wake event must report the tone that played"
    assert plane._wake_hold_until == 0.0, "the cue opened an ack hold for a barge"
    assert plane._mic_mute_until == 0.0, "the barge opened a post-playback mute window"
    assert gate.echo_reference is None, "the barge cue handed the gate a reference"
    assert gate.holds == [], "the barge cue opened a gate hold"


def _assert_silent_barge(plane, adapter, gate, frames, calls) -> None:
    """The three silence switches share one contract: no cue, nothing else moves."""
    assert calls == [], "the configured ack was rendered for a barge"
    assert adapter.played == 0 and adapter.pcm_bytes == 0, (
        "a silent barge still handed audio to the speaker")
    assert plane.ack_attempts == 0
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True and wake["action"] == "barge"
    assert wake["ack_mode"] == "none", "the wake event must report the silent barge"
    # Silencing the cue must not touch the barge path itself.
    assert gate.arms == 1, "the barge did not arm the gate"
    assert plane.preroll_replays == 1, "the barge skipped the pre-wake replay"
    assert gate.holds == [], "a silent barge opened a gate hold"
    assert gate.echo_reference is None
    assert plane._wake_hold_until == 0.0
    assert plane._mic_mute_until == 0.0


def _barge_with(monkeypatch, configure) -> tuple:
    calls, render = fake_ack()
    monkeypatch.setattr(process, "render_ack", render)
    plane, adapter = make_plane()
    configure(plane)
    gate = RecordingGate()
    plane.gate = gate
    queue_seconds(plane, 2.0)
    frames = run_wake_and_settle(plane, settle_s=0.05)
    return plane, adapter, gate, frames, calls


def test_a_barge_ack_off_restores_the_silent_barge(monkeypatch) -> None:
    """`ack.barge_ack: false` = the pre-T5 behaviour: no cue, nothing else changes."""
    plane, adapter, gate, frames, calls = _barge_with(
        monkeypatch, lambda p: setattr(p.cfg.ack, "barge_ack", False))

    _assert_silent_barge(plane, adapter, gate, frames, calls)


def test_ack_mode_none_keeps_the_barge_silent(monkeypatch) -> None:
    """The user turned the ack off: a barge must not be the one sound left."""
    plane, adapter, gate, frames, calls = _barge_with(
        monkeypatch, lambda p: setattr(p.cfg.ack, "mode", "none"))

    _assert_silent_barge(plane, adapter, gate, frames, calls)


def test_a_muted_device_keeps_the_barge_silent(monkeypatch) -> None:
    """device_muted is the device's own silence: even barge_ack=true plays nothing."""
    plane, adapter, gate, frames, calls = _barge_with(
        monkeypatch, lambda p: setattr(p, "device_muted", True))

    _assert_silent_barge(plane, adapter, gate, frames, calls)


def test_a_tts_ack_only_beeps_on_a_barge(monkeypatch) -> None:
    """`ack.mode: tts` speaks on a normal wake; a barge must only beep (no TTS)."""
    calls, render = fake_ack()
    monkeypatch.setattr(process, "render_ack", render)
    plane, adapter = make_plane()
    plane.cfg.ack.mode = "tts"
    plane.cfg.ack.text = "我在。"
    gate = RecordingGate()
    plane.gate = gate
    queue_seconds(plane, 2.0)

    frames = run_wake_and_settle(plane, settle_s=0.2)

    assert calls == [], "a barge rendered the tts ack instead of the short tone"
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True
    assert wake["ack_mode"] == "earcon", "the barge must report the tone that played"
    assert plane.ack_mode_effective == "earcon"
    played_ms = adapter.pcm_bytes / 2 / plane.effective.playback.rate * 1000.0
    assert process.BARGE_CUE_MIN_MS <= played_ms <= process.BARGE_CUE_MAX_MS, played_ms


@pytest.mark.parametrize(("configured_ms", "expected_ms"), [(140, 140), (1000, 160), (0, 160)])
def test_the_barge_cue_respects_the_configured_chime_but_stays_short(
        monkeypatch, configured_ms, expected_ms) -> None:
    """The short tone is the configured `ack.chime`, capped - not a hardcoded preset."""
    calls, render = fake_ack()
    monkeypatch.setattr(process, "render_ack", render)
    plane, adapter = make_plane()
    plane.cfg.ack.chime = "soft2"
    plane.cfg.ack.chime_ms = configured_ms  # 0 = natural length (500 ms for soft2)
    queue_seconds(plane, 2.0)

    run_wake_and_settle(plane, settle_s=0.2)

    played_ms = adapter.pcm_bytes / 2 / plane.effective.playback.rate * 1000.0
    assert played_ms == pytest.approx(expected_ms, abs=2.0), (
        f"configured chime_ms={configured_ms} produced {played_ms:.0f} ms")


def test_a_barge_cue_uses_the_configured_trim(monkeypatch) -> None:
    """`ack.chime_trim` still applies to the barge tone (quieter rooms)."""
    plane, adapter = make_plane()
    plane.cfg.ack.chime_trim = 0.5
    queue_seconds(plane, 2.0)

    run_wake_and_settle(plane, settle_s=0.2)

    pcm = np.frombuffer(adapter.pcm, dtype="<i2").astype(np.float32)
    peak = float(np.abs(pcm).max()) / 32768.0
    # PEAK (0.12) * trim (0.5) = 0.06; the preset's own attack keeps it at the top.
    assert 0.03 <= peak <= 0.08, f"the trim did not apply to the barge cue (peak={peak})"


def test_a_quiet_wake_still_plays_the_cue(monkeypatch) -> None:
    """Regression: the ordinary wake keeps its cue and the hold that covers it."""
    calls, render = fake_ack()
    monkeypatch.setattr(process, "render_ack", render)
    plane, adapter = make_plane()
    gate = RecordingGate()
    plane.gate = gate

    frames = run_wake_and_settle(plane)

    assert len(calls) == 1, "the ordinary wake skipped its cue"
    assert plane.ack_attempts == 1
    assert plane.ack_plays == 1, "the cue was started but never played"
    assert adapter.played > 0, "no cue audio reached the speaker"
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is False
    assert wake["ack_mode"] == "earcon"
    assert plane._wake_hold_until > time.monotonic(), "the cue hold is missing"
    # The gate must know which cue it is hearing, or it cannot tell its echo from a
    # person when the device plays it late.
    assert gate.echo_reference is not None
    assert len(gate.echo_reference) > 0
    assert gate.holds and gate.holds[0] > 0


def test_a_barge_does_not_extend_the_cue_hold() -> None:
    plane, _ = make_plane()
    before = time.monotonic() - 1.0  # an expired hold, so any extension would show
    plane._wake_hold_until = before
    queue_seconds(plane, 1.0)

    run_wake(plane)

    assert plane._wake_hold_until == before, "the barge cue moved the ack hold"


def test_a_barge_still_arms_the_gate_and_replays_the_preroll() -> None:
    """Only the cue path changes: gate, preroll and the wake event stay as they were."""
    plane, _ = make_plane()
    gate = RecordingGate()
    plane.gate = gate  # type: ignore[assignment]
    queue_seconds(plane, 1.0)

    frames = run_wake(plane)

    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["action"] == "barge"
    assert gate.arms == 1, "the barge did not arm the gate"
    assert plane.preroll_replays == 1, "the barge skipped the pre-wake replay"
    assert gate.holds == [], "the gate was held over the user's first words"


def test_a_wake_during_playback_stops_the_speaker(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="rui.audio.process")
    plane, adapter = make_plane()
    queue_seconds(plane, 3.0)

    frames = run_wake(plane)

    assert adapter.stops >= 1, "the device was never told to stop"
    assert plane.barges == 1
    assert plane._speak_queued_s == 0.0, "queued audio survived the barge"
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True
    assert wake["barged_s"] == pytest.approx(3.0, abs=0.05)
    # P1 (audit 2026-10-23): the acceptance number used to exist only in tests. Every
    # production barge now reports the server-side stop in the log.
    assert plane.last_barge_stop_ms is not None
    assert 0.0 <= plane.last_barge_stop_ms < 300.0, plane.last_barge_stop_ms
    assert any("silence_after_ms=" in r.getMessage() for r in caplog.records)


def test_a_wake_while_the_vendor_talks_pauses_its_player() -> None:
    """Taking the line from 小爱 mid-answer: our wake also pauses the vendor player.

    The old build's mic was gated shut while the vendor played (assistant_v3.py:3115), so
    its wake never faced this; ours hears 你好小瑞 mid-answer and must cut the vendor off,
    or both assistants talk at once.
    """

    async def run(*, playing: bool, fresh: bool = True) -> list[str]:
        plane, adapter = make_plane()
        adapter.vendor_playing = playing
        adapter.vendor_playing_ts = time.monotonic() if fresh else time.monotonic() - 100.0
        pauses: list[str] = []

        async def pause(reason: str) -> None:
            pauses.append(reason)

        adapter.pause_vendor_player = pause  # type: ignore[method-assign]
        writer, connected = StubWriter(), asyncio.Event()
        connected.set()
        hit = type("Hit", (), {"keyword": "你好小瑞", "ts_ms": 1.0})()
        await plane._on_wake(hit, writer, connected)  # type: ignore[arg-type]
        await asyncio.sleep(0)  # the pause is fire-and-forget; give the task its turn
        return pauses

    assert asyncio.run(run(playing=True)) == ["wake takeover"]
    assert asyncio.run(run(playing=True, fresh=False)) == [], "a stale playing state is ignored"
    assert asyncio.run(run(playing=False)) == [], "nothing to take over"


def test_a_wake_in_silence_is_not_a_barge() -> None:
    plane, adapter = make_plane()

    frames = run_wake(plane)

    assert adapter.stops == 0
    assert plane.barges == 0
    assert plane.last_barge_stop_ms is None
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is False


def test_the_control_side_exposes_the_barge_instant_for_the_trace() -> None:
    """P1 (audit 2026-10-23): `silence_after_ms` was null in production.

    The plane measures its own stop (`last_barge_stop_ms`, logged as
    `silence_after_ms=`); the control side keeps the wake instant so app.py can mark
    `barge_in` on the turn trace once it is wired. This test pins the data plumbing.
    """
    from rui.audio.client import AudioPlaneClient
    from rui.audio.protocol import FrameType, Wake

    class Dummy:
        pass

    client = AudioPlaneClient(Dummy(), Dummy())  # type: ignore[arg-type]
    wake = Wake(ts_ms=1.0, keyword="你好小瑞", barge=True, barged_s=1.2)
    client._dispatch(FrameType.CONTROL, wake.model_dump_json().encode("utf-8"))
    assert client.last_barge_ms is not None
    assert client.last_wake is not None and client.last_wake.barge

    # A later wake in silence must not leave a stale barge instant behind.
    quiet = Wake(ts_ms=2.0, keyword="你好小瑞")
    client._dispatch(FrameType.CONTROL, quiet.model_dump_json().encode("utf-8"))
    assert client.last_barge_ms is None


def test_the_lead_is_bounded_and_the_barge_flushes_the_device() -> None:
    # The lead used to be kept under 0.35 s on the theory that it was barge latency, but
    # it is not: stop_play makes the client drop what it holds (measured 420 ms end to
    # end). The lead's job is the opposite - the client only opens its player at ~300 ms,
    # and a 200 ms lead stuttered on the first word. So: fill past the start threshold,
    # and keep the cap tight enough that a long answer never runs a second ahead.
    from rui.audio.process import MAX_LEAD_S, PREBUFFER_S

    assert 0.2 <= PREBUFFER_S <= 0.35
    assert MAX_LEAD_S <= 0.4


def test_barge_in_off_ignores_a_wake_during_playback() -> None:
    """§7: 0.2's `_on_wake` never read `voice.barge_in`; 0.3 must honour it.

    With barge-in off (or half-duplex), a wake during an answer must not stop the
    speaker or arm a gate over our own audio. The wake word works again once the
    answer drains.
    """
    plane, adapter = make_plane()
    plane.cfg.audio.voice.barge_in = False
    queue_seconds(plane, 3.0)

    frames = run_wake(plane)

    assert adapter.stops == 0, "barge-in is off but the speaker was stopped"
    assert plane.barges == 0
    assert plane._speak_queued_s == pytest.approx(3.0, abs=0.05)
    assert not any(frame.get("type") == "wake" for frame in frames)
