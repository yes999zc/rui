"""Bounded hand-off between capture and consumption, plus arrival statistics.

The ring is the only place frames can be lost, so it counts every drop instead of
growing without bound: an unbounded queue turns a slow consumer into permanent
latency, which is worse than a gap you can measure.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class ArrivalStats:
    """Inter-arrival intervals of capture frames, in milliseconds."""

    def __init__(self, window: int = 1000) -> None:
        self._samples: deque[float] = deque(maxlen=window)

    def add(self, interval_ms: float) -> None:
        if interval_ms >= 0:
            self._samples.append(float(interval_ms))

    def __len__(self) -> int:
        return len(self._samples)

    def p50(self) -> float:
        return self._percentile(50.0)

    def p99(self) -> float:
        return self._percentile(99.0)

    def mean(self) -> float:
        return float(np.mean(self._samples)) if self._samples else 0.0

    def _percentile(self, q: float) -> float:
        if not self._samples:
            return 0.0
        return float(np.percentile(np.fromiter(self._samples, dtype=np.float32), q))


class PcmRing:
    """Fixed-latency FIFO of (seq, ts_ms, pcm)."""

    def __init__(self, capacity_ms: int, frame_ms: int) -> None:
        self.capacity = max(1, capacity_ms // max(1, frame_ms))
        self._items: deque[tuple[int, float, bytes]] = deque()

    def push(self, seq: int, ts_ms: float, pcm: bytes) -> bool:
        """False means the item was dropped because the consumer is behind."""
        if len(self._items) >= self.capacity:
            return False
        self._items.append((seq, ts_ms, pcm))
        return True

    def pop(self) -> tuple[int, float, bytes] | None:
        return self._items.popleft() if self._items else None

    def __len__(self) -> int:
        return len(self._items)
