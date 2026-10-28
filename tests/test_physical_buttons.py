"""Physical button/mute events from the dot2 bridge (task-18).

The bridge drains ``drain_signals()`` items whose ``kind`` is ``mute`` /
``unmute`` / ``volume_up`` / ``volume_down`` / ``action``. The plane owns the
two behaviours that matter here: a muted device still opens a turn but must not
play the wake cue, and the ring stays red (``muted``) until it is unmuted.
Volume keys adopt the new target into the config (task-19, see
``test_volume_key_writeback.py``) but never rewrite the mixer from here - the
key already moved it.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import numpy as np

from rui.audio.kws import WakeHit
from rui.audio.process import AudioPlane
from rui.config import Config


class KeySignal:
    """The three fields the plane's drain validation reads."""

    def __init__(self, kind: str, text: str = "") -> None:
        self.kind = kind
        self.text = text
        self.ts = time.monotonic()


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[dict] = []

    def write(self, data: bytes) -> None:
        # The plane shares this writer with PCM; only 0x01 (CONTROL) frames are events.
        if data[:1] == b"\x01":
            self.written.append(json.loads(data[5:]))

    async def drain(self) -> None:
        return None


class FakeGate:
    def __init__(self) -> None:
        self.armed = 0
        self.armed_state = False
        self.holds: list[float] = []
        self.interrupted = 0
        self.speech_ms = 0.0
        self.speaking = False
        self.idle_events = 0
        self.fed = 0
        self.discarded = 0

    def arm(self, **_kwargs) -> None:
        self.armed += 1
        self.armed_state = True

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:  # noqa: ARG002
        self.holds.append(ms)

    def set_echo_reference(self, samples, rate: int) -> None:  # noqa: ARG002
        return None

    def feed(self, pcm: bytes, ts_ms: float = 0.0):  # noqa: ARG002
        self.fed += len(pcm)
        return None


class SignalAdapter:
    """An adapter that only exposes what this task needs: signals, LED, playback."""

    def __init__(self) -> None:
        self.signals: list[KeySignal] = []
        self.leds: list[tuple[str, bool]] = []
        self.played = 0
        self.health_muted = False
        self.volume_writes = 0

    def drain_signals(self):
        out, self.signals = self.signals, []
        return out

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        self.leds.append((effect, repaint))

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        self.played += 1

    async def stop_playback(self, fade_ms: int = 120) -> None:  # noqa: ARG002
        return None

    async def set_volume(self, percent: int) -> None:  # noqa: ARG002
        self.volume_writes += 1

    def health(self) -> dict:
        return {"connected": True, "muted": self.health_muted}


def make_plane(tmp_path: Path) -> tuple[AudioPlane, SignalAdapter]:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    cfg.ack.mode = "earcon"  # type: ignore[assignment]
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", tmp_path / "plane.sock")
    plane.gate = FakeGate()  # type: ignore[assignment]
    adapter = SignalAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    return plane, adapter


def apply_keys(plane: AudioPlane, *signals: KeySignal) -> None:
    """Drive the same drain -> apply path the capture loop uses."""
    plane.adapter.signals.extend(signals)  # type: ignore[union-attr]
    for event in plane._drain_signals():
        plane._handle_device_key(event)


def test_a_muted_key_skips_the_cue_but_still_opens_a_turn(tmp_path: Path) -> None:
    async def run() -> None:
        plane, adapter = make_plane(tmp_path)
        apply_keys(plane, KeySignal("mute", "muted"))
        assert plane.device_muted is True
        await asyncio.sleep(0)  # the LED paint is a task
        assert adapter.leds[-1][0] == "muted", "a muted device must show the red ring"

        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), writer, connected)

        assert plane.device_muted is True
        assert plane.ack_attempts == 0, "the mute key must suppress the ack cue"
        assert plane.ack_skipped == 1
        assert plane._ack_task is None
        assert plane.gate.armed == 1, "the turn must still open (transcription continues)"
        assert plane.last_wake is not None and plane.last_wake.ack_mode == "none"
        assert any(item.get("type") == "wake" for item in writer.written)

    asyncio.run(run())


def test_unmute_restores_the_ack(tmp_path: Path) -> None:
    async def run() -> None:
        plane, adapter = make_plane(tmp_path)
        apply_keys(plane, KeySignal("mute", "muted"), KeySignal("unmute", "unmuted"))
        assert plane.device_muted is False
        await asyncio.sleep(0)

        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=0.0), writer, connected)

        assert plane.ack_attempts == 1
        assert plane._ack_task is not None
        plane._ack_task.cancel()
        try:
            await plane._ack_task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_the_red_ring_wins_over_later_paints_while_muted(tmp_path: Path) -> None:
    async def run() -> None:
        plane, adapter = make_plane(tmp_path)
        apply_keys(plane, KeySignal("mute", "muted"))
        await asyncio.sleep(0)
        plane._request_led("listening")
        await asyncio.sleep(0)
        assert adapter.leds[-1][0] == "muted"

    asyncio.run(run())


def test_volume_keys_do_not_rewrite_the_mixer(tmp_path: Path) -> None:
    """The key already moved the mixer; a second write would fight its own ramp."""
    async def run() -> None:
        plane, adapter = make_plane(tmp_path)
        apply_keys(
            plane,
            KeySignal("volume_up", "muted"),
            KeySignal("volume_down", "unmuted"),
        )
        assert plane.key_events == {"volume_up": 1, "volume_down": 1}
        assert plane.last_key == ("volume_down", "unmuted")
        assert adapter.volume_writes == 0, "the key path must not call services.set_volume"

    asyncio.run(run())


def test_metrics_expose_the_device_mute_state(tmp_path: Path) -> None:
    async def run() -> None:
        plane, adapter = make_plane(tmp_path)
        apply_keys(plane, KeySignal("mute", "muted"))
        metrics = plane._build_metrics()
        assert metrics.muted is True
        assert adapter.health()["muted"] is False  # the event is authoritative
        adapter.health_muted = True
        assert plane._build_metrics().muted is True

    asyncio.run(run())


def test_muted_wake_still_carries_audio_to_the_gate(tmp_path: Path) -> None:
    """Keeping the cue out must not make the turn deaf: the gate still receives audio."""

    async def run() -> None:
        plane, _adapter = make_plane(tmp_path)
        apply_keys(plane, KeySignal("mute", "muted"))
        plane._keep_preroll(np.zeros(320, dtype="<i2").tobytes(), ts_ms=900.0)
        writer, connected = FakeWriter(), asyncio.Event()
        connected.set()
        await plane._on_wake(WakeHit(keyword="你好小瑞", ts_ms=1000.0), writer, connected)
        assert plane.gate.armed == 1
        assert plane.gate.fed > 0

    asyncio.run(run())
