"""Volume-key writeback policy (task-19), shared by both sides of the IPC seam.

A physical volume key changes the device's own mixer. Core keeps the *configured*
target (``devices[].volume``) as the level the reconciler enforces, so without a
writeback the next metrics tick pulls the key's change back (measured 2026-10-28:
``volume: reconciled to 204 (config 80%)`` every ~16 s while the key was pressed).

The two processes must agree on the step without a new control message:

* the audio plane applies it to its in-memory config, so ``_reconcile_volume``
  targets the new level immediately, and
* the control plane applies the same step and persists it - config.yaml has one
  writer, and it is not the plane.

Both import this module, so the step and the clamp cannot drift apart.

Step: 5 percent per press, clamped to 0..100. ``volume = 0`` keeps its existing
meaning, "the vendor owns the mixer": a key press from 0 does not seize control at
5 %, because that would turn a no-op into a 5 % reconciler target the user never
chose (the same rule the reconciler already follows by returning on ``target <= 0``).
"""

from __future__ import annotations

#: Percent per physical key press. The Dot 2 firmware's own ramp is not published;
#: 5 % is the console/volume-tool step (`volume_tool` moves 10 per voice command) and
#: is small enough that one press cannot jump a room from background to loud.
VOLUME_KEY_STEP = 5

#: After a key press, the device's own ramp is still settling and the bridge's
#: reported level can lag by one report (dot2 `read_volume` returns the device's
#: last report). The reconciler stays completely silent for this long; after it,
#: the target is the *new* config value, never the pre-key one.
VOLUME_KEY_GRACE_S = 10.0

VOLUME_KEY_KINDS = ("volume_up", "volume_down")


def volume_key_target(current: int, kind: str) -> int | None:
    """The config percent after one volume key, or None when it must not write back.

    ``current`` is the device's configured target (0-100). ``None`` means "leave the
    config alone": an unmanaged room (0) stays unmanaged, so the reconciler is not
    handed a target the user never asked for.
    """
    if kind not in VOLUME_KEY_KINDS:
        raise ValueError(f"not a volume key: {kind!r}")
    if current <= 0:
        return None
    step = VOLUME_KEY_STEP if kind == "volume_up" else -VOLUME_KEY_STEP
    return max(0, min(100, int(current) + step))
