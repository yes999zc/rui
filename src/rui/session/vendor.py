"""Yielding to the speaker's own assistant.

A speaker may still run its vendor assistant. Its wake word wakes *it*, and it answers -
which is fine until our own wake word fires on the same audio and we answer too: two
assistants, one sentence, from the old project's log at 20:47 on 2026-10-21.

The rule that fixed it there, kept here: a vendor wake means we yield.
  - we do not open a session, we do not play the cue;
  - a session that is already open goes quiet without a farewell;
  - for `quiet_s` seconds afterwards, any utterance we assemble is dropped;
  - our own wake word clears the window, so "你好小瑞" takes over immediately.
"""
from __future__ import annotations

import time
from collections.abc import Callable


class VendorWindow:
    """How long we stay quiet after the vendor's assistant answered."""

    def __init__(self, quiet_s: float = 12.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.quiet_s = max(0.0, quiet_s)
        self._clock = clock
        self._until = 0.0
        self.vendor_wakes = 0
        self.yielded_turns = 0

    def vendor_spoke(self, kind: str, *_args) -> bool:
        """A signal from the device. Only a final recognition opens the window."""
        if kind != "wake":
            return False
        self.vendor_wakes += 1
        self._until = self._clock() + self.quiet_s
        return True

    def ours_spoke(self) -> None:
        """Our own wake word: the user is talking to us, so the window is over."""
        self._until = 0.0

    def muted(self) -> bool:
        if self._clock() >= self._until:
            return False
        self.yielded_turns += 1
        return True

    def remaining(self) -> float:
        return max(0.0, self._until - self._clock())
