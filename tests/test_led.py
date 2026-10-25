"""The ring's script builders and state machine, with no speaker in the room.

Every check here is offline: the fake shell only records the script it was asked to
ship. That is the point - the LED layer must be verifiable without a device, and the
timing is all device-side sleeps inside one string.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from xiaoai_plug.led import (
    FADE_IN_MS,
    FADE_OUT_MS,
    LED_COUNT,
    LED_PATH,
    LedRing,
    breathe_script,
    brightness,
    comet_script,
    duration_of,
    fade_steps,
    frame,
)

# 72 * 1.9 = 136.8 -> int 136 = 0x88 in the green channel -> 0x008800 (BBGGRR).
DAY_GREEN = "0x008800"
# 72 * 1.9 * 0.6 = 82.08 -> int 82 = 0x52.
NIGHT_GREEN = "0x005200"
# 72 * 1.9 * 0.45 = 61.56 -> int 61 = 0x3D on every channel.
WAKE_WHITE = "0x3D3D3D"


class RecordingShell:
    """``shell(script, timeout)``: keeps what would have gone to the device."""

    def __init__(self, fail: bool = False) -> None:
        self.scripts: list[str] = []
        self.timeouts: list[float] = []
        self.fail = fail

    async def __call__(self, script: str, timeout: float) -> str:
        self.scripts.append(script)
        self.timeouts.append(timeout)
        if self.fail:
            raise RuntimeError("device went away")
        return ""


def make_ring(shell: RecordingShell, **kwargs) -> LedRing:
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("clock", lambda: 12)
    return LedRing(shell, **kwargs)


async def flush(ring: LedRing) -> None:
    """Run every spawned paint task to completion (the fake shell never blocks)."""
    while True:
        pending = {task for task in ring._tasks if not task.done()}
        if not pending:
            return
        await asyncio.gather(*pending)


def parts_of(script: str) -> list[str]:
    return script.split("; ")


def indices_of(script: str) -> list[int]:
    out = []
    for part in parts_of(script):
        if part.startswith("echo "):
            out.append(int(part.split('"')[1].split()[0]))
    return out


# --------------------------------------------------------------------- pure builders
def test_fade_180ms_is_four_frames_and_ends_exactly_on_target() -> None:
    steps = fade_steps((0, 0, 0), (0, 72, 0), FADE_IN_MS)
    assert len(steps) == 4, "180 / 40 is 4.5 and Python's round() gives 4, not 5"
    assert [color for color, _ in steps] == [(0, 18, 0), (0, 36, 0), (0, 54, 0), (0, 72, 0)]
    assert steps[-1][0] == (0, 72, 0), "the last frame must be the target, bit for bit"
    assert steps[-1][1] == 0.0, "no sleep after the last frame"
    assert sum(ms for _, ms in steps) == pytest.approx(135.0), "3 sleeps of 45 ms"


def test_fade_zero_is_a_single_cut_frame() -> None:
    steps = fade_steps((0, 0, 0), (0, 72, 0), 0)
    assert steps == [((0, 72, 0), 0.0)]


def test_fade_never_exceeds_the_design_ceiling() -> None:
    steps = fade_steps((0, 0, 0), (0, 72, 0), 5000)
    assert len(steps) == round(1200 / 40)  # clamped to 1200 ms


def test_brightness_day_night_and_a_window_that_crosses_midnight() -> None:
    assert brightness(12) == pytest.approx(1.9)
    assert brightness(3) == pytest.approx(1.9 * 0.6)
    assert brightness(6) == pytest.approx(1.9 * 0.6), "the default 00-07 window is [start, end)"
    assert brightness(7) == pytest.approx(1.9), "end is exclusive"

    # 22 -> 06: four hours before midnight and six after it are all night.
    for hour in (22, 23, 0, 1, 5):
        assert brightness(hour, night_start=22, night_end=6) == pytest.approx(1.9 * 0.6), hour
    for hour in (6, 7, 12, 21):
        assert brightness(hour, night_start=22, night_end=6) == pytest.approx(1.9), hour


def test_a_channel_is_clamped_at_255() -> None:
    script = frame((255, 255, 255), level=2.0)
    assert script.count("0xFFFFFF") == LED_COUNT
    assert frame((300, -5, 0)).count("0x0000FF") == LED_COUNT  # upper and lower clamps


def test_one_frame_is_eighteen_echo_lines_in_bbggrr() -> None:
    script = frame((0x12, 0x34, 0x56))
    parts = parts_of(script)
    assert len(parts) == LED_COUNT
    pattern = re.compile(r'echo "\d+ 0x[0-9A-F]{6}" > ')
    assert all(pattern.match(part) for part in parts)
    assert parts[0] == f'echo "0 0x563412" > {LED_PATH}'
    assert parts[-1] == f'echo "17 0x563412" > {LED_PATH}'


def test_comet_and_breathe_are_one_script_each() -> None:
    comet = comet_script()
    assert comet
    assert comet.count("sleep ") == 18, "18 frames of 33 ms, all device-side"
    assert "0x60B0FF" in comet
    # The tail divides the packed integer, exactly like the old build.
    assert "0x30587F" in comet
    assert "0x000000" in parts_of(comet)[-1], "the ring ends dark"

    breathe = breathe_script()
    assert breathe
    assert breathe.count("sleep ") == 18
    assert duration_of(breathe) == pytest.approx(18 * 0.120)
    assert "0x603D2B" in breathe, "hsv(220, 0.55, 96) head"
    assert breathe.endswith(frame((0, 0, 96), 0.9)), "final settle frame is base * 0.9"


# ------------------------------------------------------------------- state machine
def test_off_uses_the_slower_fade_out() -> None:
    shell = RecordingShell()
    ring = make_ring(shell)

    async def go() -> None:
        ring.set_state("listening")
        await flush(ring)
        ring.set_state("off")
        await flush(ring)

    asyncio.run(go())
    assert len(shell.scripts) == 2
    fade_in, fade_out = shell.scripts
    assert duration_of(fade_in) == pytest.approx(0.135), "180 ms fade = 3 x 45 ms sleeps"
    assert duration_of(fade_out) == pytest.approx(0.280), "320 ms fade = 7 x 40 ms sleeps"
    assert fade_out.count("echo ") == 8 * LED_COUNT, "320 / 40 = 8 frames"
    assert FADE_OUT_MS > FADE_IN_MS


def test_wake_ring_sweeps_three_masks_then_settles_on_green() -> None:
    shell = RecordingShell()
    ring = make_ring(shell)

    async def go() -> None:
        ring.wake_ring()
        await flush(ring)

    asyncio.run(go())
    assert len(shell.scripts) == 1
    script = shell.scripts[0]
    parts = parts_of(script)
    written = indices_of(script)
    assert written[:2] == [8, 9]
    assert written[2:8] == list(range(6, 12))
    assert written[8:20] == list(range(3, 15))
    assert all(WAKE_WHITE in part for part in parts[:2])
    assert script.count("sleep 0.090") == 3
    # The fade to listening green is the tail of the same script; its last frame
    # writes all 18 LEDs, so a following set_state("listening") is a no-op.
    assert written[-LED_COUNT:] == list(range(LED_COUNT))
    assert all(DAY_GREEN in part for part in parts[-LED_COUNT:])
    assert ring.state == "listening"


def test_same_state_is_idempotent_and_force_repaints() -> None:
    shell = RecordingShell()
    ring = make_ring(shell)

    async def go() -> None:
        ring.set_state("listening")
        await flush(ring)
        sent = len(shell.scripts)
        ring.set_state("listening")
        await flush(ring)
        assert len(shell.scripts) == sent, "the same state must not cost a round-trip"
        ring.set_state("listening", force=True)
        await flush(ring)
        assert len(shell.scripts) == sent + 1, "force is how a wiped ring is re-asserted"
        ring.set_state("not-a-state")
        await flush(ring)
        assert len(shell.scripts) == sent + 1
        assert ring.state == "listening"

    asyncio.run(go())


def test_disabled_tracks_state_but_ships_nothing() -> None:
    shell = RecordingShell()
    ring = make_ring(shell, enabled=False)

    async def go() -> None:
        ring.set_state("thinking")
        ring.wake_ring()
        ring.error_pulse()
        ring.comet()
        await flush(ring)

    asyncio.run(go())
    assert shell.scripts == []
    assert ring.state == "off", "animations only record their state when the ring is off"


def test_a_superseded_state_never_reaches_the_device() -> None:
    shell = RecordingShell()
    ring = make_ring(shell)

    async def go() -> None:
        ring.set_state("listening")
        ring.set_state("speaking")  # bumps the generation before the first task runs
        await flush(ring)

    asyncio.run(go())
    assert len(shell.scripts) == 1, "the queued listening fade must be discarded"
    assert ring.state == "speaking"
    # cyan (0, 48, 72) * 1.9 -> channels 0, 91 (0x5B), 136 (0x88) -> 0x885B00
    assert all("0x885B00" in part for part in parts_of(shell.scripts[0])[-LED_COUNT:])


def test_thinking_fades_in_and_then_breathes_in_one_script() -> None:
    shell = RecordingShell()
    ring = make_ring(shell)

    async def go() -> None:
        ring.set_state("thinking")
        await flush(ring)

    asyncio.run(go())
    assert len(shell.scripts) == 1
    script = shell.scripts[0]
    # 25 ms per frame in the runtime path: the answer may start 1.2 s later and the
    # animation must be finished, or its blue frames overwrite the speaking cyan.
    assert duration_of(script) == pytest.approx(0.135 + 18 * 0.025)
    assert script.count("sleep 0.025") == 18
    assert script.endswith(frame((0, 0, 96), 0.9, level=1.9))


def test_the_timeout_is_the_estimated_duration_plus_margin() -> None:
    shell = RecordingShell()
    ring = make_ring(shell)

    async def go() -> None:
        ring.set_state("speaking")
        await flush(ring)

    asyncio.run(go())
    assert shell.timeouts == [pytest.approx(duration_of(shell.scripts[0]) + 1.5)]
    assert shell.timeouts[0] > 1.5


def test_the_injected_clock_decides_night_brightness() -> None:
    shell = RecordingShell()
    ring = make_ring(shell, clock=lambda: 3)

    async def go() -> None:
        ring.set_state("listening")
        await flush(ring)

    asyncio.run(go())
    last_frame = parts_of(shell.scripts[0])[-LED_COUNT:]
    assert all(NIGHT_GREEN in part for part in last_frame)


def test_the_chain_thinking_speaking_listening_still_ends_green(monkeypatch) -> None:
    """The breathe runs 2.16 s on the device; the last sysfs write wins.

    A fast turn paints thinking, speaking and then listening while the breathe is still
    writing, and the breathe ends on dark blue - 9哥 saw two turns end blue. The settle
    is keyed on "a long script is still running", not on "the previous state was
    thinking", because by the time listening is painted the long script is two states
    back.
    """
    import asyncio
    import time

    import xiaoai_plug.led as led_mod

    scripts: list[str] = []

    async def fake_shell(script: str, timeout: float) -> str:
        scripts.append(script)
        return ""

    ring = led_mod.LedRing(fake_shell, enabled=True, clock=lambda: 12)  # noon: gain only

    async def run() -> None:
        ring.set_state("thinking")
        await asyncio.sleep(0)
        # Shorten the in-flight long animation so the test does not sleep 2.3 s.
        ring._long_until = time.monotonic() + 0.001
        before = len(scripts)
        ring.set_state("speaking")
        ring.set_state("listening")
        await asyncio.sleep(0.01)
        # Back-to-back states: the speaking fade is dropped by the generation token,
        # only the newest script leaves. (In a real turn they are seconds apart.)
        assert len(scripts) == before + 1
        await asyncio.sleep(0.25)                  # settle delay (remaining + 0.15)
        assert len(scripts) == before + 2
        settle = scripts[-1]
        assert settle.count("led_rgb") == 18
        assert "0x008800" in settle                # listening green at gain 1.9, noon

    asyncio.run(run())


def test_a_repaint_only_touches_the_state_it_names() -> None:
    """The vendor repaints its idle colour after our audio; we re-assert, but never over a new state."""
    import asyncio

    scripts: list[str] = []

    async def fake_shell(script: str, timeout: float) -> str:
        scripts.append(script)
        return ""

    from xiaoai_plug.led import LedRing

    ring = LedRing(fake_shell, enabled=True, clock=lambda: 12)

    async def run() -> None:
        ring.set_state("listening")
        await asyncio.sleep(0.01)
        before = len(scripts)
        ring.repaint("listening")
        await asyncio.sleep(0.01)
        assert len(scripts) == before + 1
        # A newer state must not be overwritten by a late retry.
        ring.set_state("thinking")
        await asyncio.sleep(0.01)
        before = len(scripts)
        ring.repaint("listening")
        await asyncio.sleep(0.01)
        assert len(scripts) == before

    asyncio.run(run())


def test_the_thinking_animation_finishes_before_an_answer_can_start() -> None:
    """The answer starts ~1.2 s after thinking; a longer animation paints blue over cyan."""
    import asyncio

    import xiaoai_plug.led as led_mod

    scripts: list[str] = []

    async def fake_shell(script: str, timeout: float) -> str:
        scripts.append(script)
        return ""

    ring = led_mod.LedRing(fake_shell, enabled=True, clock=lambda: 12)

    async def run() -> None:
        ring.set_state("thinking")
        await asyncio.sleep(0.01)
        assert led_mod.duration_of(scripts[0]) < 1.0

    asyncio.run(run())
