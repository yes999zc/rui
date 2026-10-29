"""task-6: the standby KWS gate must save model work without losing a wake.

The plane feeds every 20 ms frame to the streaming spotter, so an idle but open
microphone costs 50 inferences/s. The gate suspends that while the frame level says
"room noise only" and nothing else is happening; the first louder frame resumes it and
the preroll ring is replayed into the spotter. These cases pin down the state machine,
the skipped-frame accounting, and that a wake word arriving while cold is still fed
and still fires.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from ruamel.yaml import YAML

from rui.audio.process import AudioPlane, StandbyKwsGate
from rui.config import Config
from rui.config.catalog import document

REPO_ROOT = Path(__file__).resolve().parents[1]
QUIET_DB = -80.0
LOUD_DB = -30.0


# ------------------------------------------------------------------ the gate alone

def quiet_gate(**overrides: object) -> StandbyKwsGate:
    kwargs: dict[str, object] = {"enabled": True, "threshold_db": -60.0, "hold_s": 1.0}
    kwargs.update(overrides)
    return StandbyKwsGate(**kwargs)  # type: ignore[arg-type]


def test_the_gate_cools_only_after_the_hold_and_counts_every_skipped_frame() -> None:
    gate = quiet_gate(hold_s=0.1)
    actions = [
        gate.observe(rms_db=QUIET_DB, engaged=False, now=i * 0.02) for i in range(6)
    ]
    # 5 x 20 ms processed, the 6th reaches 0.10 s of quiet and is the first skipped one.
    assert actions == [StandbyKwsGate.PROCESS] * 5 + [StandbyKwsGate.SKIP]
    assert gate.hot is False
    assert gate.suspends == 1
    assert gate.skipped == 1

    for i in range(6, 16):
        assert gate.observe(rms_db=QUIET_DB, engaged=False, now=i * 0.02) == StandbyKwsGate.SKIP
    assert gate.skipped == 11
    assert gate.suspends == 1


def test_the_first_frame_at_or_above_the_threshold_resumes_on_speech() -> None:
    gate = quiet_gate(hold_s=0.0)
    assert gate.observe(rms_db=QUIET_DB, engaged=False, now=0.0) == StandbyKwsGate.SKIP
    assert gate.hot is False

    # The comparison is >=; with the learned floor at -80 the absolute -60 bound is
    # the effective threshold, so a frame exactly at -60 is speech.
    assert gate.observe(rms_db=-60.0, engaged=False, now=0.02) == StandbyKwsGate.RESUME
    assert gate.hot is True
    assert gate.resumes == 1

    # Back at full rate: nothing is skipped while the level stays above the threshold.
    for i in range(3):
        assert gate.observe(rms_db=LOUD_DB, engaged=False, now=0.04 + i * 0.02) == (
            StandbyKwsGate.PROCESS
        )
    assert gate.skipped == 1


def test_activity_resumes_without_replaying_the_quiet_ring() -> None:
    gate = quiet_gate(hold_s=0.0)
    gate.observe(rms_db=QUIET_DB, engaged=False, now=0.0)
    assert gate.observe(rms_db=QUIET_DB, engaged=True, now=0.02) == StandbyKwsGate.RESTART
    assert gate.hot is True
    assert gate.resumes == 1


def test_ambient_above_the_absolute_threshold_is_learned_and_suspends() -> None:
    """task-9: -40 dBFS is above the -60 absolute bound, but it is still the room.

    The floor starts at the absolute lower bound and rises ~0.2 dB/frame at a
    -40 dBFS ambient, so it needs ~2.4 s before -40 is below floor+6. With hold 0 the
    gate suspends on the first frame that qualifies.
    """
    gate = quiet_gate(hold_s=0.0)
    for i in range(100):  # 2 s: not learned yet
        gate.observe(rms_db=-40.0, engaged=False, now=i * 0.02)
    assert gate.hot is True
    for i in range(100, 150):  # up to 3 s
        gate.observe(rms_db=-40.0, engaged=False, now=i * 0.02)
    assert gate.hot is False
    assert gate.suspends == 1
    assert gate.skipped > 0
    assert gate.floor_db > -60.0
    assert gate.effective_threshold_db > -45.0


def test_ambient_with_the_default_hold_suspends_within_a_few_seconds() -> None:
    gate = quiet_gate(hold_s=1.0)
    for i in range(200):  # 4 s: ~2.4 s to learn the floor + 1 s hold
        gate.observe(rms_db=-40.0, engaged=False, now=i * 0.02)
    assert gate.hot is False
    assert gate.suspends == 1


def test_speech_above_the_learned_floor_resumes() -> None:
    gate = quiet_gate(hold_s=0.0)
    for i in range(150):
        gate.observe(rms_db=-40.0, engaged=False, now=i * 0.02)
    assert gate.hot is False
    # -15 dBFS is 25 dB above the learned -40 room: a speech onset, not ambient.
    assert gate.observe(rms_db=-15.0, engaged=False, now=3.0) == StandbyKwsGate.RESUME
    assert gate.hot is True
    assert gate.resumes == 1


def test_the_absolute_threshold_is_still_the_lower_bound() -> None:
    gate = quiet_gate(hold_s=0.0)
    gate.observe(rms_db=-80.0, engaged=False, now=0.0)
    assert gate.floor_db == -80.0
    assert gate.effective_threshold_db == -60.0  # floor+6 = -74, capped at -60
    assert gate.hot is False
    assert gate.observe(rms_db=-55.0, engaged=False, now=0.02) == StandbyKwsGate.RESUME


def test_the_floor_drops_immediately_and_rises_slowly() -> None:
    gate = quiet_gate(hold_s=10.0)  # stay hot so every frame goes through observe
    for i in range(100):
        gate.observe(rms_db=-40.0, engaged=False, now=i * 0.02)
    raised = gate.floor_db
    assert -60.0 < raised < -40.0

    gate.observe(rms_db=-80.0, engaged=False, now=2.0)
    assert gate.floor_db == -80.0  # down immediately

    gate.observe(rms_db=-40.0, engaged=False, now=2.02)
    assert abs(gate.floor_db - (-80.0 + 0.01 * 40.0)) < 1e-9  # one slow EMA step


def test_a_disabled_gate_is_transparent() -> None:
    gate = quiet_gate(enabled=False, hold_s=0.0)
    for i in range(20):
        assert gate.observe(rms_db=QUIET_DB, engaged=False, now=i * 0.02) == (
            StandbyKwsGate.PROCESS
        )
    assert gate.skipped == 0
    assert gate.suspends == 0


# ------------------------------------------------------- the plane feeding the gate

class FakeWake:
    """Records what the spotter was fed and hits on one marked block."""

    def __init__(self, hit_block: bytes | None = None) -> None:
        self.fed: list[tuple[float, bytes]] = []
        self.resets = 0
        self.hit_block = hit_block

    def accept(self, pcm: bytes, ts_ms: float = 0.0) -> list[SimpleNamespace]:
        self.fed.append((ts_ms, pcm))
        if self.hit_block is not None and pcm == self.hit_block:
            return [SimpleNamespace(keyword="你好小瑞", ts_ms=ts_ms)]
        return []

    def reset(self) -> None:
        self.resets += 1


class FakeProbe:
    def __init__(self) -> None:
        self.fed = 0

    def accept(self, pcm: bytes, ts_ms: float = 0.0, rms_db: float | None = None) -> list[str]:
        self.fed += 1
        return []


class _Writer:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


def make_plane(hold_s: float = 0.0) -> AudioPlane:
    cfg = Config()
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/standby-gate.sock"))
    plane.standby_kws = StandbyKwsGate(enabled=True, threshold_db=-60.0, hold_s=hold_s)
    plane.wake = FakeWake()  # type: ignore[assignment]
    return plane


def drive(
    plane: AudioPlane,
    frames: list[tuple[bytes, float, float]],
    wakes: list[SimpleNamespace] | None = None,
) -> list[SimpleNamespace]:
    """One `_run_spotter` per frame, exactly like `_capture_frames` around it.

    The caller keeps the frame in the preroll ring *after* the spotter call (the real
    order), so a resume replays the frames that were skipped.
    """
    wakes = [] if wakes is None else wakes

    async def on_wake(hit: SimpleNamespace, _writer: object, _connected: object) -> None:
        wakes.append(hit)

    plane._on_wake = on_wake  # type: ignore[method-assign]

    async def run() -> None:
        writer = _Writer()
        connected = asyncio.Event()
        connected.set()
        for pcm, rms_db, ts_ms in frames:
            plane.voice_rms_db = rms_db
            await plane._run_spotter(pcm, ts_ms, writer, connected)  # type: ignore[arg-type]
            plane._keep_preroll(pcm, ts_ms)

    asyncio.run(run())
    return wakes


def test_a_wake_word_arriving_while_cold_is_still_fed_and_fires() -> None:
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    quiet = b"\x01\x00" * 320
    quiet2 = b"\x02\x00" * 320
    wake_block = b"wake-word-block"
    tail = b"\x03\x00" * 320
    wake.hit_block = wake_block

    wakes = drive(
        plane,
        [(quiet, QUIET_DB, 0.0), (quiet2, QUIET_DB, 20.0), (wake_block, LOUD_DB, 40.0),
         (tail, LOUD_DB, 60.0)],
    )

    # The two quiet frames were skipped cold; the loud one reset the spotter, replayed
    # the whole preroll ring (both skipped frames) and then processed itself - so the
    # wake word is not lost.
    assert wake.resets == 1
    assert wake.fed == [(0.0, quiet), (20.0, quiet2), (40.0, wake_block), (60.0, tail)]
    assert plane.wake_count == 1
    assert [hit.keyword for hit in wakes] == ["你好小瑞"]
    # The wake timestamp is the live frame's own ts_ms (40.0 ms), not a replay-time
    # stamp: the hit position is the same as the always-on path.
    assert wakes[0].ts_ms == 40.0
    assert plane.standby_kws.skipped == 2


def test_ambient_then_speech_resumes_and_replays_the_ring() -> None:
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    blocks = [bytes([i % 256, 0]) * 320 for i in range(150)]
    wake_block = b"wake-word-block"
    wake.hit_block = wake_block

    # 3 s of -40 dBFS room noise: loud in absolute terms, but the relative gate learns
    # it as the floor and goes cold; the first speech frame must wake it, not stay cold.
    drive(plane, [(blocks[i], -40.0, i * 20.0) for i in range(150)])
    assert plane.standby_kws.hot is False
    assert plane.standby_kws.skipped > 0
    fed_before = len(wake.fed)

    wakes = drive(plane, [(wake_block, -15.0, 3000.0)])

    assert plane.standby_kws.resumes == 1
    assert wake.resets == 1
    # 500 ms preroll (25 frames) replayed in timestamp order, then the live frame.
    replayed = wake.fed[fed_before:]
    assert len(replayed) == 26
    assert replayed[-2] == (2980.0, blocks[149])
    assert replayed[-1] == (3000.0, wake_block)
    assert [hit.keyword for hit in wakes] == ["你好小瑞"]
    assert wakes[0].ts_ms == 3000.0


def test_the_wake_hit_used_the_gate_not_a_stale_always_on_path() -> None:
    """Control case: without the gate every frame is fed, including the quiet ones."""
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    plane.standby_kws = StandbyKwsGate(enabled=False, threshold_db=-60.0, hold_s=0.0)
    quiet = b"\x01\x00" * 320
    wake_block = b"wake-word-block"
    wake.hit_block = wake_block

    drive(plane, [(quiet, QUIET_DB, 0.0), (wake_block, LOUD_DB, 20.0)])

    assert wake.fed == [(0.0, quiet), (20.0, wake_block)]
    assert plane.standby_kws.skipped == 0


def test_a_turn_resumes_the_spotter_without_replaying_the_quiet_ring() -> None:
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    quiet = b"\x01\x00" * 320
    live = b"\x04\x00" * 320

    # First frame while idle: the gate goes cold and the spotter never sees it.
    drive(plane, [(quiet, QUIET_DB, 0.0)])
    assert wake.fed == []

    # A turn starts (pending utterance): full rate again, no onset replay.
    plane._turn_pending = True
    drive(plane, [(live, LOUD_DB, 20.0)])
    assert wake.resets == 1
    assert wake.fed == [(20.0, live)]


def test_a_playback_owner_freezes_the_cold_state_without_replaying() -> None:
    """An owned speaker (music/cue/dialogue) must resume the spotter even on quiet audio."""
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    quiet = b"\x01\x00" * 320
    quiet2 = b"\x02\x00" * 320

    drive(plane, [(quiet, QUIET_DB, 0.0)])
    assert plane.standby_kws.skipped == 1

    plane._owner = "music"  # playback is active, but the frame is still quiet
    skipped_before = plane.standby_kws.skipped
    drive(plane, [(quiet2, QUIET_DB, 20.0)])

    assert plane.standby_kws.skipped == skipped_before  # no more cold frames
    assert wake.fed == [(20.0, quiet2)]                 # fed, no RESUME replay
    assert wake.resets == 1                             # activity RESTART, not RESUME


def test_an_active_device_wake_keeps_the_spotter_hot() -> None:
    """Documented limitation: device wake active = gate never suspends (zero saving)."""
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    quiet = b"\x01\x00" * 320
    quiet2 = b"\x02\x00" * 320

    drive(plane, [(quiet, QUIET_DB, 0.0)])
    assert plane.standby_kws.skipped == 1

    plane.device_wake_active = True
    skipped_before = plane.standby_kws.skipped
    drive(plane, [(quiet2, QUIET_DB, 20.0)])

    assert plane.standby_kws.skipped == skipped_before
    assert wake.fed == [(20.0, quiet2)]
    assert wake.resets == 1


def test_a_configured_probe_keeps_the_old_always_on_behaviour() -> None:
    plane = make_plane(hold_s=0.0)
    wake = plane.wake
    assert isinstance(wake, FakeWake)
    probe = FakeProbe()
    plane.probe = probe  # type: ignore[assignment]
    quiet = b"\x01\x00" * 320

    drive(plane, [(quiet, QUIET_DB, 0.0), (quiet, QUIET_DB, 20.0)])

    # The probe is a debugging second spotter; gating is a production-CPU feature, so
    # configuring it leaves the pre-0.8.7 cadence untouched.
    assert len(wake.fed) == 2
    assert probe.fed == 2
    assert plane.standby_kws.skipped == 0


# ---------------------------------------------------------- config / catalog wiring

def test_the_three_config_places_agree_on_the_gate() -> None:
    fields = {field["path"]: field for field in document(Config())["fields"]}
    for key in ("standby_kws_gate", "standby_kws_db", "standby_kws_hold_ms"):
        field = fields[f"audio.voice.{key}"]
        assert field["effect"] == "restart"

    data = YAML(typ="safe").load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    voice = data["audio"]["voice"]
    assert voice["standby_kws_gate"] is True
    assert voice["standby_kws_db"] == -60.0
    assert voice["standby_kws_hold_ms"] == 1000


def test_the_schema_defaults_are_conservative() -> None:
    voice = Config().audio.voice
    assert voice.standby_kws_gate is True
    # Well below speech, and the hold is long enough not to flap on word gaps.
    assert voice.standby_kws_db == -60.0
    assert voice.standby_kws_hold_ms == 1000


def test_a_zero_preroll_disables_the_gate_even_if_it_is_enabled() -> None:
    cfg = Config()
    cfg.audio.voice.wake_preroll_ms = 0
    cfg.audio.aec.enabled = False
    plane = AudioPlane(cfg, "living", Path("/tmp/standby-gate-nopreroll.sock"))
    assert plane.standby_kws.enabled is False
