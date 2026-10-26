"""The 18-LED ring: script builders plus the state machine that ships them.

Hardware: an AW20054 at ``/sys/devices/i2c-0/0-003a``. Writing ``"<index> 0xBBGGRR"``
(actually ``R | G<<8 | B<<16``) lights one LED, indices 0..17. A full-ring write costs
20-50 ms on the device, so an animation is shipped as ONE shell script with ``sleep``
statements in it - never one round-trip per frame.

Two rules shape everything here:

1. **A paint never blocks the caller.** ``set_state`` is synchronous, records the new
   state and hands the script to a background task. The old deployment awaited the
   shell request, and because that request shares the websocket with the audio stream,
   it delayed resuming playback by the full 3 s timeout (20:25 incident).
2. **Every change is a fade, never a cut**, and every fade ends exactly on its target
   colour, so a dropped frame cannot strand the ring between two states.

The state machine is per-instance (a second speaker may have no ring at all). The
night window is evaluated in ``Asia/Shanghai``: the container runs UTC, and
``datetime.now().hour`` silently dimmed the ring in the middle of the day.

This module only *builds* scripts and tracks state. It does not import the adapter;
the caller supplies ``shell(script, timeout)``, which is where execution and
fire-and-forget semantics live.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

LED_PATH = "/sys/devices/i2c-0/0-003a/led_rgb"
LED_COUNT = 18

Color = tuple[int, int, int]
Shell = Callable[[str, float], Awaitable[str]]

# Peak channels, in RGB order. They sit at 0x48 (28% of full scale) by design; gain
# and the night factor lift the whole ring together.
COLOR_RGB: dict[str, Color] = {
    "off": (0, 0, 0),
    "listening": (0, 72, 0),  # green: I am here, say something
    "thinking": (0, 0, 96),  # blue: working
    "speaking": (0, 48, 72),  # cyan: my voice is coming out of me
    "wake": (72, 72, 72),  # white: I heard you (transient)
    "error": (72, 0, 0),  # red: I could not do it
    "music": (72, 24, 96),  # violet: playing, deliberately unlike blue
    "music_paused": (96, 40, 0),  # amber: paused, not stopped
    "muted": (60, 0, 0),  # dim red: hardware microphone mute
}

GAIN = 1.9
NIGHT_FACTOR = 0.6
NIGHT_START = 0
NIGHT_END = 7

# Transition shape. Code constants, not config: this is the design, and one knob
# (fade_ms) is enough for the console.
FADE_IN_MS = 180
FADE_OUT_MS = 320  # shutting down should feel heavier than waking up
FADE_STEP_MS = 40
WAKE_STEP_MS = 90
THINK_FRAME_MS = 120
# One breathing rotation is 18 x 120 ms = 2.16 s. A state that replaces thinking
# must re-assert itself after that script has finished: device-side scripts run
# concurrently, and the last sysfs write wins, so the breathe tail used to paint
# blue over listening green on a fast turn (9哥 saw it end blue, 2026-10-22).
THINKING_SETTLE_S = 2.6
ERROR_PULSE_MS = 140
COMET_FRAME_MS = 33
COMET_FRAMES = 18
COMET_TAIL = 5
# Packed exactly as it is written to sysfs: B=0x60, G=0xB0, R=0xFF (amber).
# The tail divides this integer, not the channels - see comet_script.
COMET = 0x60B0FF

_SLEEP = re.compile(r"\bsleep\s+([0-9]+(?:\.[0-9]+)?)")
_FADE_LIMIT_MS = 1200.0


def _shanghai() -> tzinfo:
    """The one timezone this deployment speaks. Never the process's local time."""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:  # slim image without /usr/share/zoneinfo
        # China has had no DST since 1991, so this fallback is exact.
        return timezone(timedelta(hours=8), "Asia/Shanghai")


ZONE = _shanghai()


def _shanghai_hour() -> int:
    return datetime.now(ZONE).hour


def is_night(hour: int, night_start: int = NIGHT_START, night_end: int = NIGHT_END) -> bool:
    """``start <= hour < end``; start > end crosses midnight. start == end is never."""
    if night_start == night_end:
        return False
    if night_start < night_end:
        return night_start <= hour < night_end
    return hour >= night_start or hour < night_end


def brightness(
    hour: int,
    *,
    gain: float = GAIN,
    night_factor: float = NIGHT_FACTOR,
    night_start: int = NIGHT_START,
    night_end: int = NIGHT_END,
) -> float:
    """The multiplier applied to every channel: gain by day, dimmer at night."""
    if is_night(hour, night_start, night_end):
        return gain * night_factor
    return gain


def _code(rgb: Color, factor: float = 1.0) -> int:
    """Pack one RGB triple into the device's ``0xBBGGRR`` integer, clamped per channel."""
    r, g, b = (max(0, min(255, int(v * factor))) for v in rgb)
    return r | (g << 8) | (b << 16)


def _echo(index: int, code: int, path: str) -> str:
    return f'echo "{index} 0x{code:06X}" > {path}'


def _sleep(ms: float) -> str:
    return f"sleep {max(0.0, float(ms)) / 1000:.3f}"


def frame(
    rgb: Color,
    factor: float = 1.0,
    *,
    level: float = 1.0,
    path: str = LED_PATH,
) -> str:
    """One full-ring frame: 18 echo lines, one per LED, all the same colour."""
    code = _code(rgb, level * factor)
    return "; ".join(_echo(i, code, path) for i in range(LED_COUNT))


def frame_mask(
    indices: Iterable[int],
    rgb: Color,
    factor: float = 1.0,
    *,
    level: float = 1.0,
    path: str = LED_PATH,
) -> str:
    """One frame that writes only ``indices`` - the wake sweep needs a partial ring."""
    code = _code(rgb, level * factor)
    return "; ".join(_echo(i, code, path) for i in indices)


def duration_of(script: str) -> float:
    """Wall-clock seconds the device spends inside one script (sum of its sleeps)."""
    return sum(float(match.group(1)) for match in _SLEEP.finditer(script))


def fade_steps(
    prev: Color,
    rgb: Color,
    duration_ms: float,
) -> list[tuple[Color, float]]:
    """One cross-fade as ``(colour, sleep_ms_after)`` pairs.

    Linear interpolation is what the eye reads as a fade on a ring this small. The
    step count is ``round(duration / 40)``: 180 ms is exactly 4 frames of 45 ms, and
    a total sleep of 135 ms. Do not replace ``round`` with ``int(x + 0.5)`` - 180/40
    is 4.5, and the extra frame changes the perceived timing.

    The last frame is always exactly ``rgb`` and carries no sleep.
    """
    duration_ms = max(0.0, min(float(duration_ms), _FADE_LIMIT_MS))
    steps = max(1, int(round(duration_ms / FADE_STEP_MS))) if duration_ms else 1
    step_ms = duration_ms / steps
    out: list[tuple[Color, float]] = []
    for k in range(1, steps + 1):
        if k == steps:
            color = rgb
        else:
            t = k / steps
            color = tuple(round(a + (b - a) * t) for a, b in zip(prev, rgb, strict=True))  # type: ignore[assignment]
        out.append((color, step_ms if k < steps else 0.0))
    return out


def _hsv(hue: float, sat: float = 0.85, val: float = 96.0) -> Color:
    """Hue in degrees, saturation 0-1, value 0-255; same brightness band as the palette."""
    h = hue % 360
    c = val * sat
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = val - c
    if h < 60:
        r, g, b = c, x, 0.0
    elif h < 120:
        r, g, b = x, c, 0.0
    elif h < 180:
        r, g, b = 0.0, c, x
    elif h < 240:
        r, g, b = 0.0, x, c
    elif h < 300:
        r, g, b = x, 0.0, c
    else:
        r, g, b = c, 0.0, x
    return (round(r + m), round(g + m), round(b + m))


def breathe_script(
    base: Color = COLOR_RGB["thinking"],
    *,
    cycles: int = 1,
    tail: int = 6,
    frame_ms: int = THINK_FRAME_MS,
    level: float = 1.0,
    path: str = LED_PATH,
) -> str:
    """Thinking: one slow blue rotation, not a rainbow.

    A short bright head and a fading tail move around the ring; everything else
    stays ``base``. Only LEDs whose code changed are written, so a frame is usually
    two echo lines instead of eighteen. The final full frame settles at
    ``base * 0.9`` and carries no sleep, so nothing animates forever.
    """
    bright = _hsv(220, 0.55, 96.0)
    lines = [frame(base, level=level, path=path)]
    lit: dict[int, int] = {}
    for _ in range(max(1, cycles)):
        for i in range(LED_COUNT):
            new: dict[int, Color] = {i: bright}
            for k in range(1, tail + 1):
                new[(i - k) % LED_COUNT] = tuple(v * (1 - k / (tail + 1)) for v in base)
            for pos, color in new.items():
                code = _code(color, level)
                if lit.get(pos) != code:
                    lines.append(_echo(pos, code, path))
            for pos in lit:
                if pos not in new:
                    lines.append(_echo(pos, 0, path))
            lit = {pos: _code(color, level) for pos, color in new.items()}
            lines.append(_sleep(frame_ms))
    lines.append(frame(base, 0.9, level=level, path=path))
    return "; ".join(lines)


def comet_script(
    *,
    frames: int = COMET_FRAMES,
    step_ms: int = COMET_FRAME_MS,
    tail: int = COMET_TAIL,
    path: str = LED_PATH,
) -> str:
    """One pass of a comet, then the ring goes dark (a shutdown cue).

    The tail is ``COMET // (k + 1)`` on the packed integer, exactly as the old build
    wrote it. Dividing the integer (rather than each channel) lets a borrow cross
    channel boundaries at low intensities; that is intentional, not a bug to fix
    while porting. Brightness is not applied here for the same reason: the values
    are already device codes.
    """
    frames = max(1, frames)
    lines: list[str] = []
    lit: dict[int, int] = {}
    for i in range(frames):
        new = {i: COMET}
        for k in range(1, tail + 1):
            new[(i - k) % frames] = COMET // (k + 1)
        for pos, code in new.items():
            if lit.get(pos) != code:
                lines.append(_echo(pos, code, path))
        for pos in lit:
            if pos not in new:
                lines.append(_echo(pos, 0, path))
        lit = new
        lines.append(_sleep(step_ms))
    for pos in lit:
        lines.append(_echo(pos, 0, path))
    return "; ".join(lines)


class LedRing:
    """The ring's state machine: one script per state change, painted in the background.

    ``shell`` is ``async (script, timeout) -> stdout``. It is called from a spawned
    task and never awaited on the caller's stack.
    """

    def __init__(
        self,
        shell: Shell,
        colors: dict[str, Color] | None = None,
        gain: float = GAIN,
        night_factor: float = NIGHT_FACTOR,
        night_start: int = NIGHT_START,
        night_end: int = NIGHT_END,
        fade_ms: int = FADE_IN_MS,
        enabled: bool = False,
        clock: Callable[[], int] | None = None,
        device_path: str = LED_PATH,
    ) -> None:
        self._shell = shell
        self.colors: dict[str, Color] = dict(COLOR_RGB)
        if colors:
            self.colors.update(colors)  # a partial map keeps the states it omits
        self.gain = gain
        self.night_factor = night_factor
        self.night_start = night_start
        self.night_end = night_end
        self.fade_ms = fade_ms
        # Public so a console save can turn the ring off without rebuilding the object.
        self.enabled = enabled
        self.device_path = device_path
        self._clock = clock or _shanghai_hour
        self._name: str | None = None
        self._rgb: Color = (0, 0, 0)
        # Every accepted state change bumps this. A task that starts after a newer
        # change wins is discarded before it reaches the device.
        self._generation = 0
        self._tasks: set[asyncio.Task] = set()
        # Monotonic end of the in-flight long animation (thinking's 2.16 s breathe).
        # Any state painted before this moment needs a settle frame afterwards.
        self._long_until = 0.0

    @property
    def state(self) -> str:
        return self._name or "unknown"

    # ------------------------------------------------------------------ state machine
    def set_state(self, name: str, force: bool = False) -> None:
        """Record a state and paint it in the background. Unknown states are ignored."""
        rgb = self.colors.get(name)
        if rgb is None:
            return
        if not force and self._name == name:
            return  # idempotent: the same state must not cost a shell round-trip
        prev = self._rgb
        self._name, self._rgb = name, rgb
        token = self._bump()
        if not self.enabled:
            return  # state is still tracked; the ring stays dark
        script = self._transition_script(name, prev, rgb)
        self._spawn(script, token)
        if name == "thinking":
            self._long_until = time.monotonic() + duration_of(script)
        else:
            # Any state painted while the breathe is still running needs one settle
            # frame after it: the device executes scripts concurrently and the last
            # sysfs write wins. Checking "still running" rather than "previous state
            # was thinking" matters for thinking -> speaking -> listening, where the
            # long script is two states back (9哥 saw the turn end blue twice).
            remaining = self._long_until - time.monotonic()
            if remaining > 0:
                self._settle(name, token, remaining + 0.15)

    def _settle(self, name: str, token: int, delay: float) -> None:
        """Re-paint a state once the previous long animation has finished.

        Only needed after thinking: its breathe runs for 2.16 s on the device, so a
        short turn can paint speaking and then listening while the breathe is still
        writing - and the breathe ends on dark blue. One extra frame at the end makes
        the final colour deterministic regardless of what was in flight.
        """
        rgb = self.colors.get(name)
        if rgb is None:
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._settle_later(name, rgb, token, delay),
                                name=f"led-settle-{name}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _settle_later(self, name: str, rgb: Color, token: int, delay: float) -> None:
        await asyncio.sleep(delay)
        if token != self._generation:
            return  # a newer state owns the ring; do not paint over it
        script = frame(rgb, level=self._level(), path=self.device_path)
        await self._run(script, token)

    def repaint(self, name: str) -> None:
        """Re-assert the ring's current state, but only if it is still `name`.

        The vendor's LED service repaints its own idle colour after our audio stops, so
        the app re-asserts listening a few times after playback ends. A repaint must not
        overwrite a newer state (a wake during the retry window), hence the name check.
        """
        if self._name != name:
            return
        token = self._bump()
        if not self.enabled:
            return
        self._spawn(frame(self._rgb, level=self._level(), path=self.device_path), token)

    def wake_ring(self) -> None:
        """Outward sweep from the centre, then settle on listening green.

        The script itself ends on the listening colour, so the state is recorded as
        ``listening`` immediately - a following ``set_state("listening")`` is then a
        no-op instead of a second, competing fade.
        """
        white = self.colors.get("wake", COLOR_RGB["wake"])
        green = self.colors["listening"]
        self._name, self._rgb = "listening", green
        token = self._bump()
        if not self.enabled:
            return
        level = self._level()
        parts: list[str] = []
        for indices in ((8, 9), range(6, 12), range(3, 15)):
            parts.append(frame_mask(indices, white, 0.45, level=level, path=self.device_path))
            parts.append(_sleep(WAKE_STEP_MS))
        parts.extend(self._fade_parts(white, green, self.fade_ms, level))
        self._spawn("; ".join(parts), token)
        remaining = self._long_until - time.monotonic()
        if remaining > 0:
            self._settle("listening", token, remaining + 0.15)

    def error_pulse(self) -> None:
        """Two short red pulses plus a dim red settle, over in ~700 ms."""
        red = self.colors.get("error", COLOR_RGB["error"])
        self._name, self._rgb = "error", red
        token = self._bump()
        if not self.enabled:
            return
        level = self._level()
        parts: list[str] = []
        for _ in range(2):
            parts.append(frame(red, level=level, path=self.device_path))
            parts.append(_sleep(ERROR_PULSE_MS))
            parts.append(frame((0, 0, 0), level=level, path=self.device_path))
            parts.append(_sleep(ERROR_PULSE_MS))
        parts.append(frame(red, 0.6, level=level, path=self.device_path))
        self._spawn("; ".join(parts), token)

    def comet(self) -> None:
        """One comet pass, ending dark. The state is recorded as ``off``."""
        self._name, self._rgb = "off", (0, 0, 0)
        token = self._bump()
        if not self.enabled:
            return
        self._spawn(comet_script(path=self.device_path), token)

    # ----------------------------------------------------------------------- internals
    def _bump(self) -> int:
        self._generation += 1
        return self._generation

    def _level(self) -> float:
        return brightness(
            self._clock(),
            gain=self.gain,
            night_factor=self.night_factor,
            night_start=self.night_start,
            night_end=self.night_end,
        )

    def _transition_script(self, name: str, prev: Color, rgb: Color) -> str:
        level = self._level()
        duration = FADE_OUT_MS if name == "off" else self.fade_ms
        parts = self._fade_parts(prev, rgb, duration, level)
        if name == "thinking":
            # The old build ran set_state("thinking") and start_breathing() as two
            # scripts. One script keeps the fade and the rotation in order.
            # 18 frames at 25 ms, not the old build's 120 ms: the answer can start
            # 1.2 s after thinking, and a 2.16 s breathe keeps writing blue over the
            # speaking cyan (9哥 saw the whole answer blue). Scripts run concurrently on
            # the device and the last write wins, so the thinking animation must finish
            # before the first spoken block.
            parts.append(
                breathe_script(rgb, level=level, path=self.device_path, frame_ms=25)
            )
        return "; ".join(parts)

    def _fade_parts(self, prev: Color, rgb: Color, duration_ms: float, level: float) -> list[str]:
        parts: list[str] = []
        for color, sleep_ms in fade_steps(prev, rgb, duration_ms):
            parts.append(frame(color, level=level, path=self.device_path))
            if sleep_ms > 0:
                parts.append(_sleep(sleep_ms))
        return parts

    def _spawn(self, script: str, token: int) -> None:
        if not script:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # called from a thread or at import time
            log.warning("led: no running event loop; %s animation dropped", self.state)
            return
        task = loop.create_task(self._run(script, token), name=f"led-{self.state}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, script: str, token: int) -> None:
        if token != self._generation:
            return  # a newer state won before this script left the process
        timeout = duration_of(script) + 1.5
        try:
            await self._shell(script, timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dark ring must not take the turn down
            log.warning("led: %s animation failed: %s", self.state, exc)
            if token == self._generation:
                # Speaker went away: forget the state so the next set_state retries.
                self._name = None
                self._rgb = (0, 0, 0)


__all__ = [
    "COLOR_RGB",
    "LedRing",
    "breathe_script",
    "brightness",
    "comet_script",
    "duration_of",
    "fade_steps",
    "frame",
    "frame_mask",
    "is_night",
]
