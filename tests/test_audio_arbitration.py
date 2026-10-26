"""Phase 2 arbitration on the audio plane: candidates, verdicts, and the mute window.

The control plane owns the decision (docs/multi-device-design.md §12). These tests pin
the plane half of that contract:

- `multi_device.enabled=false` keeps the old local path (no candidate, no wait);
- with it on, a wake (KWS or device-detected) becomes a `wake_candidate` and the plane
  arms nothing until `wake_admit`; a reject or a timeout means no turn, never a local
  fallback (two planes "taking it themselves" is the double turn the arbiter prevents);
- the per-device `post_playback_mute_ms` (config v3) starts when the output drains, so
  barge during playback is untouched while the residual echo afterwards cannot become
  a request;
- `aec.who=device` skips RUI's canceller instead of cascading two of them.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rui.audio.kws import WakeHit
from rui.audio.process import AudioPlane
from rui.audio.protocol import (
    WakeAdmit,
    WakeCandidate,
    WakeIdle,
    WakeReject,
    encode_json,
    parse_control,
)
from rui.config import Config, DeviceConfig
from rui.config.schema import (
    DeviceAecOverride,
    DeviceAudioOverride,
    DeviceVoiceOverride,
)
from rui.device.contract import Capability
from rui.device.mock import MockAdapter


class StubWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


class FakeGate:
    def __init__(self) -> None:
        self.armed = False
        self.speaking = False
        self.arm_calls: list[dict] = []

    def arm(self, **kwargs) -> None:
        self.armed = True
        self.arm_calls.append(kwargs)

    def hold(self, ms: float, skip_ms: float = 0.0) -> None:
        return None

    def set_echo_reference(self, samples, rate: int) -> None:  # noqa: ARG002
        return None

    def disarm(self) -> None:
        self.armed = False


class ArbAdapter(MockAdapter):
    def __init__(self, capabilities: frozenset[Capability] = frozenset()) -> None:
        super().__init__("living", capabilities=capabilities)

    async def stop_playback(self, fade_ms: int = 120) -> None:
        return None


def make_plane(*, arbitrate: bool = True, device_id: str = "living",
               config: Config | None = None) -> tuple[AudioPlane, ArbAdapter]:
    cfg = config or Config()
    cfg.audio.aec.enabled = False
    cfg.ack.mode = "none"
    cfg.multi_device.enabled = arbitrate
    if not any(d.id == device_id for d in cfg.devices):
        cfg.devices = [DeviceConfig(id=device_id, adapter="mock", host="127.0.0.1",
                                    room="living")]
    plane = AudioPlane(cfg, device_id, Path("/tmp/audio-arbitration.sock"))
    adapter = ArbAdapter()
    plane.adapter = adapter  # type: ignore[assignment]
    plane.gate = FakeGate()  # type: ignore[assignment]
    return plane, adapter


def messages(writer: StubWriter) -> list[dict]:
    return [json.loads(frame[5:].decode("utf-8"))
            for frame in writer.frames if frame and frame[0] == 1]


async def drive_candidate(plane: AudioPlane, *, verdict: tuple[bool, str] | None,
                          source: str = "device") -> tuple[list[dict], StubWriter]:
    """Run one wake and answer its candidate with `verdict` (None = let it time out)."""
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()
    if source == "device":
        plane.adapter.inject_wake()  # type: ignore[attr-defined]
        task = asyncio.create_task(plane._drain_device_wakes(
            vendor_wake=False, writer=writer, connected=connected))
    else:
        task = asyncio.create_task(plane._on_wake(
            WakeHit(keyword="你好小瑞", ts_ms=1.0), writer, connected))
    candidate = None
    for _ in range(400):
        found = [m for m in messages(writer) if m["type"] == "wake_candidate"]
        if found:
            candidate = found[0]
            break
        await asyncio.sleep(0.005)
    assert candidate is not None, "plane did not report a candidate"
    if verdict is not None:
        pending = plane._pending_wakes[candidate["wake_id"]]
        pending.set_result(verdict)
    await asyncio.wait_for(task, timeout=3.0)
    return messages(writer), writer


# ---------------------------------------------------------------- single-plane mode

def test_single_plane_mode_never_reports_a_candidate() -> None:
    plane, _ = make_plane(arbitrate=False)
    assert plane.arbitrate is False
    writer, connected = StubWriter(), asyncio.Event()
    connected.set()

    admitted = asyncio.run(plane._arbitrate_candidate(
        source="rui", keyword="你好小瑞", barge=False, playing=False,
        writer=writer, connected=connected))

    assert admitted is True  # the local path may proceed; nothing was sent
    assert messages(writer) == []
    assert plane.wake_candidates == 0


# ------------------------------------------------------------- candidate/admit path

def test_device_wake_becomes_a_candidate_and_the_turn_waits_for_admit() -> None:
    plane, _ = make_plane()
    frames, _writer = asyncio.run(drive_candidate(plane, verdict=(True, "admitted")))

    kinds = [frame["type"] for frame in frames]
    # The observation message (device_wake) still precedes the policy action; the turn
    # (`wake`) must only follow the admit.
    assert "wake_candidate" in kinds
    assert kinds.index("wake_candidate") < kinds.index("wake")
    candidate = frames[kinds.index("wake_candidate")]
    assert candidate["source"] == "device"
    assert candidate["playing"] is False
    assert "wake" in kinds                     # only after the admit
    assert any(frame["type"] == "wake" for frame in frames)
    assert plane.gate.armed is True            # type: ignore[attr-defined]
    assert plane.wake_candidates == 1
    assert plane.wake_admitted == 1
    assert plane.wake_admit_latency_ms is not None


def test_rejected_candidate_does_not_open_a_turn() -> None:
    plane, _ = make_plane()
    frames, _writer = asyncio.run(
        drive_candidate(plane, verdict=(False, "lost_same_word")))

    assert [frame["type"] for frame in frames] == ["device_wake", "wake_candidate"]
    assert plane.gate.armed is False            # type: ignore[attr-defined]
    assert plane.wake_rejections == {"lost_same_word": 1}
    assert plane.wake_admitted == 0


def test_no_verdict_times_out_instead_of_taking_the_turn_locally() -> None:
    plane, _ = make_plane()
    plane._admit_timeout_s = 0.02
    frames, _writer = asyncio.run(drive_candidate(plane, verdict=None))

    assert [frame["type"] for frame in frames] == ["device_wake", "wake_candidate"]
    assert plane.wake_admit_timeouts == 1
    assert plane.wake_rejections["admit_timeout"] == 1
    assert plane.gate.armed is False            # type: ignore[attr-defined]


def test_a_missing_control_plane_drops_the_candidate() -> None:
    plane, _ = make_plane()
    plane.adapter.inject_wake()                 # type: ignore[attr-defined]
    writer = StubWriter()
    connected = asyncio.Event()                 # not set: no control plane

    asyncio.run(plane._drain_device_wakes(
        vendor_wake=False, writer=writer, connected=connected))

    assert plane.wake_candidates == 0
    assert plane.wake_admitted == 0
    assert plane.gate.armed is False            # type: ignore[attr-defined]


def test_kws_wake_also_goes_through_the_arbiter() -> None:
    plane, _ = make_plane()
    frames, _writer = asyncio.run(drive_candidate(
        plane, verdict=(True, "admitted"), source="rui"))

    candidate = frames[0]
    assert candidate["type"] == "wake_candidate"
    assert candidate["source"] == "rui"
    assert candidate["keyword"] == "你好小瑞"
    assert plane.wake_candidates == 1


# ------------------------------------------------------------------ mute window

def test_mute_window_starts_after_the_output_drains() -> None:
    cfg = Config()
    cfg.audio.voice.post_playback_mute_ms = 2500
    plane, _ = make_plane(arbitrate=False, config=cfg)

    import time
    delay = plane._followup_delay_from(pacer_due=time.monotonic())

    assert 3.4 <= delay <= 3.7          # 2.5 s mute + 1.0 s follow-up
    assert plane._mic_is_muted() is True
    plane._mic_mute_until = 0.0
    assert plane._mic_is_muted() is False


def test_device_override_wins_over_the_global_mute() -> None:
    cfg = Config()
    cfg.audio.voice.post_playback_mute_ms = 0
    cfg.devices = [DeviceConfig(
        id="dot2", adapter="mock", host="127.0.0.1", room="study",
        audio=DeviceAudioOverride(voice=DeviceVoiceOverride(post_playback_mute_ms=2500)),
    )]
    plane, _ = make_plane(arbitrate=False, device_id="dot2", config=cfg)

    import time
    delay = plane._followup_delay_from(pacer_due=time.monotonic())

    assert delay >= 2.5
    assert plane._mic_is_muted() is True


def test_without_a_mute_the_followup_delay_is_unchanged() -> None:
    cfg = Config()                      # default post_playback_mute_ms = 0
    plane, _ = make_plane(arbitrate=False, config=cfg)

    import time
    due = time.monotonic()
    delay = plane._followup_delay_from(pacer_due=due)

    assert 0.95 <= delay <= 1.05        # just the old 1.0 s follow-up window
    assert plane._mic_is_muted() is False


def test_aec_who_device_skips_the_rui_canceller() -> None:
    cfg = Config()
    cfg.audio.aec.enabled = True
    cfg.devices = [DeviceConfig(
        id="dot2", adapter="mock", host="127.0.0.1", room="study",
        audio=DeviceAudioOverride(aec=DeviceAecOverride(enabled=False, who="device")),
    )]
    plane, _ = make_plane(arbitrate=False, device_id="dot2", config=cfg)

    plane._build_aec(mic_channels=1, ref_channels=0)

    assert plane.aec is None
    assert "aec.who=device" in plane.aec_error


# ------------------------------------------------------------------ protocol shape

def test_phase2_messages_roundtrip() -> None:
    candidate = WakeCandidate(wake_id="w1", ts_ms=12.5, source="device",
                              barge=True, playing=True, score=0.8, threshold=0.5)
    admit = WakeAdmit(wake_id="w1", turn_id="t1")
    reject = WakeReject(wake_id="w1", reason="cross_echo")
    idle = WakeIdle(waited_ms=5000.0, wake_id="w1")

    for message in (candidate, admit, reject, idle):
        parsed = parse_control(message.model_dump_json().encode("utf-8"))
        assert parsed.type == message.type
        assert parsed == message

    # The frame encoder/decoder path used on the socket accepts them too.
    raw = encode_json(admit)
    assert parse_control(raw[5:]) == admit
