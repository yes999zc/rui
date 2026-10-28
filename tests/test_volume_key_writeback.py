"""task-19: physical volume keys write back to the config target.

Real-device evidence (2026-10-28 12:29-12:34): a hardware volume key moved the
Dot 2's own mixer, but `devices[].volume` stayed at 80 %, so `_reconcile_volume()`
put 204 back every metrics tick (`volume: reconciled to 204 (config 80%)`).

The plane now adopts the key into its in-memory config - the target the reconciler
reads - and the control plane persists it (config.yaml has one writer). For 10 s
after a key the reconciler must not write at all: the device's own ramp and the
bridge's reported level are still settling.

Units: config `volume` is percent 0-100; the mixer is 0-255 and a target maps with
`round(pct * 255 / 100)`, the same conversion `_reconcile_volume` uses.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from rui.audio.process import AudioPlane
from rui.audio.volume_keys import VOLUME_KEY_GRACE_S, VOLUME_KEY_STEP
from rui.config import Config, DeviceConfig


def _plane(tmp_path: Path, volume: int) -> tuple[AudioPlane, list[int], dict[str, int]]:
    """A plane wired to a fake mixer that reports whatever the test sets."""
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.devices = [
        DeviceConfig(id="living", adapter="lx06", host="192.0.2.10", volume=volume),
    ]
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    writes: list[int] = []
    reported = {"value": 0}

    class Svc:
        async def set_volume(self, value: int) -> None:
            writes.append(value)

    async def read() -> int:
        return reported["value"]

    plane.adapter = SimpleNamespace(services=Svc(), read_volume=read)
    return plane, writes, reported


def _key(plane: AudioPlane, kind: str, text: str = "") -> None:
    """Drive one drained device signal through the same handler the capture loop uses."""
    plane._handle_device_key(SimpleNamespace(kind=kind, text=text, ts=time.monotonic()))


def _target(plane: AudioPlane) -> int:
    return next(d for d in plane.cfg.devices if d.id == "living").volume


def test_a_volume_down_key_lowers_the_plane_target(tmp_path: Path) -> None:
    """The key already moved the mixer; the config target must follow it."""
    plane, _writes, _reported = _plane(tmp_path, 80)
    _key(plane, "volume_down")

    assert _target(plane) == 80 - VOLUME_KEY_STEP
    assert plane.last_user_volume_at is not None
    # The reconciler's read-back estimate and /health's existing field both move now,
    # not 16 s later: round(75 * 255 / 100).
    assert plane.device_volume == round((80 - VOLUME_KEY_STEP) * 255 / 100)
    assert plane._build_metrics().device_volume == plane.device_volume
    assert plane.key_events == {"volume_down": 1}


def test_reconcile_does_not_pull_back_within_the_grace_window(tmp_path: Path) -> None:
    """A stale 80 % read-back must not undo the key for the first 10 s."""
    async def run() -> None:
        plane, writes, reported = _plane(tmp_path, 80)
        _key(plane, "volume_down")                 # target 75 % -> 191
        reported["value"] = round(80 * 255 / 100)  # the mixer still reports the old 80 %
        await plane._read_device_volume()

        assert writes == [], "the reconciler pulled the key's change back inside the grace"
        assert _target(plane) == 75

    asyncio.run(run())


def test_reconcile_targets_the_new_value_after_the_grace_window(tmp_path: Path) -> None:
    """After 10 s the write target is the new config value, never the old one."""
    async def run() -> None:
        plane, writes, reported = _plane(tmp_path, 80)
        _key(plane, "volume_down")                 # 75 % -> 191
        plane.last_user_volume_at = time.monotonic() - (VOLUME_KEY_GRACE_S + 0.5)
        reported["value"] = round(80 * 255 / 100)  # still the old value
        await plane._read_device_volume()

        assert writes == [round(75 * 255 / 100)], writes
        assert plane.device_volume == round(75 * 255 / 100)

    asyncio.run(run())


def test_the_d7_one_step_tolerance_still_holds_after_a_key(tmp_path: Path) -> None:
    """The key path must not resurrect the exact-comparison write (d.7)."""
    async def run() -> None:
        plane, writes, reported = _plane(tmp_path, 80)
        _key(plane, "volume_up")                   # 85 % -> 217
        plane.last_user_volume_at = time.monotonic() - (VOLUME_KEY_GRACE_S + 0.5)

        reported["value"] = 218                    # +1: the vendor's quantisation
        await plane._read_device_volume()
        reported["value"] = 216                    # -1
        await plane._read_device_volume()
        assert writes == [], "a one-step read-back difference was treated as drift"

        reported["value"] = 215                    # -2: real drift
        await plane._read_device_volume()
        assert writes == [217], writes

    asyncio.run(run())


def test_volume_up_clamps_at_100(tmp_path: Path) -> None:
    plane, _writes, _reported = _plane(tmp_path, 100 - VOLUME_KEY_STEP + 1)
    _key(plane, "volume_up")
    assert _target(plane) == 100
    _key(plane, "volume_up")
    assert _target(plane) == 100, "a key above the top must not write 105"
    assert plane.key_events == {"volume_up": 2}


def test_volume_down_clamps_at_0(tmp_path: Path) -> None:
    plane, _writes, _reported = _plane(tmp_path, VOLUME_KEY_STEP - 2)
    _key(plane, "volume_down")
    assert _target(plane) == 0
    _key(plane, "volume_down")
    assert _target(plane) == 0, "a key below the bottom must not write -5"


def test_volume_zero_stays_vendor_owned(tmp_path: Path) -> None:
    """0 means "do not manage": pressing up must not seize the mixer at 5 %.

    Taking over there would make the reconciler force a level the user never chose
    (the first key from an unmanaged room would become a 5 % target).
    """
    plane, writes, _reported = _plane(tmp_path, 0)
    _key(plane, "volume_up")

    assert _target(plane) == 0
    assert plane.device_volume is None
    assert writes == []
    assert plane.last_user_volume_at is not None, "the user action is still recorded"
