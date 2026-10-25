"""Cold-start device_wake re-evaluation (dot2 follow-up).

The first probe runs before an inbound device has dialled in. A plug whose probe()
only reports device_wake once its link is up (dot2 waits for the device) would keep RUI
on its own KWS for the whole process lifetime. While the plug manifest says device_wake
is wanted, the plane re-probes a bounded number of times in the background; the first
probe that verifies the capability arms the v1 path, and a spent budget leaves KWS
untouched and does not inflate the fallback counter on every attempt.

These tests drive the scheduling helpers directly rather than the frame loop: the loop
hook is a single conditional call, while the state machine is what can regress.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from rui.audio import process as process_module
from rui.audio.process import AudioPlane, _WakePolicy
from rui.config import Config
from rui.device.contract import Capability, DeviceInfo
from rui.device.mock import MockAdapter

PLAYBACK = frozenset({Capability.PLAYBACK})
WAKE_CAPS = frozenset(
    {Capability.PLAYBACK, Capability.DEVICE_WAKE, Capability.BARGE_VIA_DEVICE}
)
DEVICE_POLICY = _WakePolicy(
    source="device",
    present=True,
    trusted=True,
    barge_enabled=True,
    capabilities=frozenset({Capability.PLAYBACK, Capability.DEVICE_WAKE, Capability.BARGE_VIA_DEVICE}),
)
RUI_POLICY = _WakePolicy(
    source="rui",
    present=True,
    trusted=True,
    capabilities=frozenset({Capability.DEVICE_WAKE}),
)


class ProbeAfter(MockAdapter):
    """probe() reports device_wake only after *probe_after* calls (0 = always)."""

    def __init__(self, probe_after: int = 0) -> None:
        super().__init__("living", capabilities=PLAYBACK)
        self.probe_after = probe_after
        self.probe_calls = 0

    async def probe(self) -> DeviceInfo:
        self.probe_calls += 1
        caps = WAKE_CAPS if self.probe_calls > self.probe_after else PLAYBACK
        return replace(self.info, capabilities=frozenset(caps))


def make_plane(
    monkeypatch: pytest.MonkeyPatch,
    adapter: MockAdapter,
    *,
    policy: _WakePolicy = DEVICE_POLICY,
    initial_caps: frozenset[Capability] = PLAYBACK,
) -> AudioPlane:
    cfg = Config()
    cfg.ack.mode = "none"
    plane = AudioPlane(cfg, "living", Path("/tmp/device-wake-reprobe.sock"))
    plane.adapter = adapter  # type: ignore[assignment]
    monkeypatch.setattr(AudioPlane, "_wake_policy", lambda self: policy)
    monkeypatch.setattr(process_module, "DEVICE_WAKE_REPROBE_S", 0.0)
    plane._configure_device_wake(
        replace(adapter.info, capabilities=frozenset(initial_caps))
    )  # the cold probe handed to the plane before the device connected
    return plane


async def one_reeval(plane: AudioPlane) -> None:
    plane._maybe_schedule_device_wake_reeval()  # arms the first deadline
    plane._maybe_schedule_device_wake_reeval()  # due now: starts the task
    task = plane._device_wake_reeval_task
    assert task is not None
    await task


def test_cold_start_probe_is_re_evaluated_after_the_device_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProbeAfter()  # the first re-probe already sees the connected device
    plane = make_plane(monkeypatch, adapter)
    assert plane.device_wake_active is False
    assert plane.device_wake_reeval_wanted is True
    assert plane.device_wake_fallbacks == 1

    asyncio.run(one_reeval(plane))

    assert plane.device_wake_active is True
    assert plane.device_wake_barge_active is True
    assert plane.device_wake_reeval_wanted is False
    assert adapter.probe_calls == 1
    assert plane.device_wake_fallbacks == 1, (
        "re-probing must not inflate the one-time fallback counter"
    )


def test_a_verified_initial_probe_never_schedules_a_re_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProbeAfter()
    plane = make_plane(monkeypatch, adapter, initial_caps=WAKE_CAPS)

    assert plane.device_wake_active is True
    assert plane.device_wake_reeval_wanted is False
    plane._maybe_schedule_device_wake_reeval()
    plane._maybe_schedule_device_wake_reeval()
    assert plane._device_wake_reeval_task is None
    assert adapter.probe_calls == 0, "the initial probe was passed in, not re-run"


def test_re_probe_budget_stops_and_does_not_spam_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_module, "DEVICE_WAKE_REPROBE_MAX", 2)
    adapter = ProbeAfter(probe_after=999)  # re-probes never report the capability
    plane = make_plane(monkeypatch, adapter)

    async def scenario() -> None:
        await one_reeval(plane)
        await one_reeval(plane)
        # Budget is spent: further ticks disable the re-probe instead of starting a task.
        plane._maybe_schedule_device_wake_reeval()
        assert plane.device_wake_reeval_wanted is False
        assert plane.device_wake_reprobes == 2

    asyncio.run(scenario())
    assert plane.device_wake_active is False
    assert plane.device_wake_fallbacks == 1


def test_no_re_probe_when_the_manifest_wants_rui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProbeAfter()  # probe would happily advertise device_wake
    plane = make_plane(monkeypatch, adapter, policy=RUI_POLICY)

    assert plane.device_wake_active is False
    assert plane.device_wake_reeval_wanted is False
    plane._maybe_schedule_device_wake_reeval()
    plane._maybe_schedule_device_wake_reeval()
    assert plane._device_wake_reeval_task is None


def test_a_plug_without_drain_wakes_is_not_re_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoDrain(MockAdapter):
        drain_wakes = None  # type: ignore[assignment] - a plug that cannot report wakes

        async def probe(self) -> DeviceInfo:  # pragma: no cover - must not be called
            raise AssertionError("must not re-probe a plug that cannot report wakes")

    adapter = NoDrain("living", capabilities=PLAYBACK)
    plane = make_plane(monkeypatch, adapter)

    assert plane.device_wake_reeval_wanted is False
    plane._maybe_schedule_device_wake_reeval()
    plane._maybe_schedule_device_wake_reeval()
    assert plane._device_wake_reeval_task is None


def test_a_stopped_plane_does_not_arm_after_a_slow_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ProbeAfter()
    plane = make_plane(monkeypatch, adapter)

    async def scenario() -> None:
        plane._maybe_schedule_device_wake_reeval()
        plane._maybe_schedule_device_wake_reeval()
        task = plane._device_wake_reeval_task
        assert task is not None
        plane._stop.set()
        await task

    asyncio.run(scenario())
    assert plane.device_wake_active is False, (
        "a probe that returns after the plane stopped must not arm the v1 path"
    )
