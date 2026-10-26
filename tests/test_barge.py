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

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.played += 1

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


def test_a_barge_wake_does_not_play_the_cue(monkeypatch) -> None:
    """2026-10-26: the barge cue「在呢」 became the next turn (self-hearing loop).

    Logged on the Dot2: 12:07:03 barge -> cue -> 12:07:05 seg 「在呢。」 -> 12:07:07
    reply -> 12:07:12 barge again. A barge means the user is already talking, so the
    cue talks over their first words and its echo feeds ASR. No cue may be rendered,
    and no cue-length hold or post-playback mute window may be opened for it.
    """
    calls, render = fake_ack()
    monkeypatch.setattr(process, "render_ack", render)
    plane, adapter = make_plane()
    gate = RecordingGate()
    plane.gate = gate
    queue_seconds(plane, 2.0)

    frames = run_wake_and_settle(plane)

    assert calls == [], "a barge rendered an ack cue"
    assert adapter.played == 0, "a barge handed cue audio to the speaker"
    assert plane.ack_attempts == 0
    assert plane._ack_task is None
    wake = next(f for f in frames if f.get("type") == "wake")
    assert wake["barge"] is True
    assert wake["ack_mode"] == "none", "the wake event must report that no cue played"
    assert plane._wake_hold_until == 0.0, "the ack hold was opened for a cue-less barge"
    assert plane._mic_mute_until == 0.0, "the barge opened a post-playback mute window"
    assert gate.echo_reference is None, "a cue-less barge handed the gate a reference"
    assert gate.holds == [], "a cue-less barge opened a gate hold"


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

    assert plane._wake_hold_until == before, "a cue-less barge moved the ack hold"


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
