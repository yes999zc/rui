"""Per-turn latency marks: the ruler every M1 step is measured with.

One turn is a sequence of timestamps taken at the boundaries that actually cost time
(wake fire, end of speech, first ASR byte, first LLM token, first TTS frame, first PCM
written, silence after a barge-in). Nothing here touches audio: it only records.

Two numbers matter and they are computed, never estimated:
  first_response_ms  wake -> play_start          (how long until the user hears us)
  silence_after_ms   barge_in -> silence         (acceptance target: <= 300 ms)

Turn traces are JSON lines in the `house_ear.trace` log so a stage can be compared
across runs without a profiler attached.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger("house_ear.trace")

# Canonical order inside one turn. Marks may be skipped; going backwards in time is
# allowed (interrupts do) but is recorded so a bad measurement cannot hide.
STAGES = (
    "wake",
    "end_of_speech",
    "asr_first",
    "asr_done",
    "llm_first",
    "llm_done",
    "tts_first",
    "play_start",
    "barge_in",
    "silence",
    "play_stop",
)


def now_ms() -> float:
    """Monotonic milliseconds. Wall clock is useless for a latency budget."""
    return time.monotonic() * 1000.0


@dataclass
class TurnTrace:
    device_id: str
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    t0_ms: float = field(default_factory=now_ms)
    marks: dict[str, float] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def mark(self, stage: str, at_ms: float | None = None) -> float:
        """Record one stage boundary. Returns ms since turn start.

        First write wins: a turn can touch a boundary several times (one TTS call per
        sentence), and the latency we care about is the first one. Re-marking is a no-op
        rather than an overwrite, so `tts_first` cannot drift to the last sentence.
        """
        ts = now_ms() if at_ms is None else at_ms
        if stage in self.marks:
            log.debug("stage %s already marked on turn %s", stage, self.turn_id)
            return round(self.marks[stage] - self.t0_ms, 1)
        self.marks[stage] = ts
        return round(ts - self.t0_ms, 1)

    def at(self, stage: str) -> float | None:
        ts = self.marks.get(stage)
        return None if ts is None else round(ts - self.t0_ms, 1)

    def delta(self, start: str, end: str) -> float | None:
        a, b = self.marks.get(start), self.marks.get(end)
        if a is None or b is None:
            return None
        return round(b - a, 1)

    @property
    def first_response_ms(self) -> float | None:
        return self.delta("wake", "play_start")

    @property
    def silence_after_ms(self) -> float | None:
        return self.delta("barge_in", "silence")

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "device_id": self.device_id,
            "first_response_ms": self.first_response_ms,
            "silence_after_ms": self.silence_after_ms,
            "stages_ms": {s: self.at(s) for s in STAGES if s in self.marks},
            "meta": self.meta,
        }


class TraceLog:
    """Ring buffer of recent turns plus one JSON log line per finished turn."""

    def __init__(self, capacity: int = 200) -> None:
        self.capacity = capacity
        self._turns: deque[dict] = deque(maxlen=capacity)

    def add(self, trace: TurnTrace) -> dict:
        entry = trace.to_dict()
        self._turns.append(entry)
        log.info("turn %s", json.dumps(entry, ensure_ascii=False))
        return entry

    def recent(self, n: int = 20) -> list[dict]:
        if n <= 0:
            return []
        return list(self._turns)[-n:]

    def clear(self) -> None:
        self._turns.clear()
