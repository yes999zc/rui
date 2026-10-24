"""When the conversation is over: silence for a while, then a goodbye.

The old deployment's rule (`design-and-measurements.md` §1.5 and §4.2) is
`conversation.timeout_seconds = 15` of *no activity*, then a two-line farewell. Activity
is a new request or a finished turn - not playback: a paused song is not a reason to keep
the session open, and a song that is playing was never an idle session at all.

This build has no music player yet, so only the first kind of activity exists. `touch()`
is where both kinds will land: a request resets the window when it arrives, and the turn
runner resets it again when the turn ends, so the countdown starts *after* the answer -
counting from the question instead would fire the goodbye while the assistant is still
talking about the weather.

Two details are deliberate:

- The clock is injected. With `time.monotonic` a 15 s window can only be tested by
  sleeping 15 s; with a fake clock the boundary (14.999 vs 15.000) is exact and the suite
  stays fast. Same pattern as `session/vendor.py`.
- `tick()` fires once and closes the session as it returns True. A caller that polls in a
  loop therefore cannot say goodbye twice, and the failure mode that matters - 「我先下啦」
  repeated every 15 s for the rest of the night - is not expressible.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from ..config.schema import SessionConfig

_FOREVER = float("inf")


class IdleWatch:
    """A clock over one conversation, and the single goodbye at the end of it.

    `timeout_s <= 0` means "never time out": `expired()` stays False and `tick()` never
    fires, which is how the console disables the behaviour without a separate flag.
    """

    def __init__(
        self,
        timeout_s: float = 15.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_s = float(timeout_s)
        self._clock = clock
        # None = no session is open, so nothing can expire.
        self._last: float | None = None

    @property
    def active(self) -> bool:
        return self._last is not None

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def start(self, now: float | None = None) -> None:
        """Open a session (a wake, a new conversation)."""
        self._last = self._now(now)

    def stop(self) -> None:
        """Close the session without waiting for the timeout (a stop word, a farewell)."""
        self._last = None

    def touch(self, now: float | None = None) -> None:
        """Record activity. Also opens the session if none is open.

        Opening here rather than only in `start()` is what keeps the watchdog from being
        silently dead: any path that produced a turn has clearly had activity, even if the
        caller forgot to call `start()` on the wake.
        """
        self._last = self._now(now)

    def remaining(self, now: float | None = None) -> float:
        """Seconds until the goodbye; `inf` when no session is open or the timeout is off."""
        if self._last is None or self.timeout_s <= 0:
            return _FOREVER
        return max(0.0, self.timeout_s - (self._now(now) - self._last))

    def expired(self, now: float | None = None) -> bool:
        """Is the window over? A pure question: it does not close the session."""
        if self._last is None or self.timeout_s <= 0:
            return False
        return self._now(now) - self._last >= self.timeout_s

    def tick(self, now: float | None = None) -> bool:
        """True exactly once: the moment the idle window closes.

        The session is closed on the same call, so the next tick cannot fire again. A
        farewell that gets spoken twice is worse than a late one.
        """
        if not self.expired(now):
            return False
        self.stop()
        return True


def farewell_line(session: SessionConfig) -> str:
    """The line to say when a session ends; ``""`` means exit silently.

    The texts live in `session.*` (the console writes that section), not here: they are
    wording, and wording is configuration. Read through the model rather than duplicated
    as constants so an edited line takes effect without a restart.
    """
    if session.farewell == "full":
        return session.farewell_text_full.strip()
    if session.farewell == "short":
        return session.farewell_text.strip()
    return ""   # "off": the session still ends, it just does not say so
