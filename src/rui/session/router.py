"""Wake arbitration: first frame wins.

Two speakers hear the same wake word; the one whose wake frame arrives first owns
the turn. The window exists only to let near-simultaneous events be compared, not
to delay the winner: audio from the winner is buffered during the window.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WakeEvent:
    device_id: str
    ts_ms: float
    score: float = 0.0


def decide(events: list[WakeEvent], window_ms: int = 200) -> str | None:
    """Pure decision function: earliest timestamp wins, ties broken by device id."""
    if not events:
        return None
    first = min(e.ts_ms for e in events)
    eligible = [e for e in events if e.ts_ms - first <= window_ms]
    return sorted(eligible, key=lambda e: (e.ts_ms, e.device_id))[0].device_id


class WakeArbiter:
    """Collects wake events and resolves them once the window closes."""

    def __init__(self, window_ms: int = 200) -> None:
        self.window_ms = window_ms
        self._events: list[WakeEvent] = []

    def offer(self, event: WakeEvent) -> bool:
        """Returns True when this event arrived inside the current window."""
        if not self._events:
            self._events.append(event)
            return True
        first = self._events[0].ts_ms
        if event.ts_ms - first > self.window_ms:
            return False
        self._events.append(event)
        return True

    def resolve(self) -> str | None:
        winner = decide(self._events, self.window_ms)
        self._events.clear()
        return winner

    def reset(self) -> None:
        self._events.clear()
