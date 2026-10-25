"""Phase 2 control-plane coordinator: wake candidates in, verdicts out.

The pure decision is `arbiter.WakeArbiter` (docs/multi-device-design.md §12.3); this
class is the thin, testable glue between it and the N audio planes: it enriches a
plane's `WakeCandidate` with the device id/room, feeds the arbiter, waits out the merge
window, and sends `WakeAdmit` / `WakeReject` back to the right plane. It owns no audio
and no turn: the control plane still decides what a turn does (`on_utterance`).

Why a separate module: the FastAPI app is a poor place to test arbitration timing, and
the mapping "winner -> that device's plane" is where a bug produces a wake spoken in
the wrong room. Everything mutable here (clock, senders) is injectable.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from .arbiter import Candidate, Decision, WakeArbiter
from .protocol import WakeAdmit, WakeReject

log = logging.getLogger(__name__)

Send = Callable[[object], Awaitable[None]]

# How often the merge-window task re-asks the arbiter. The arbiter's own clock is
# millisecond-based; polling keeps the coordinator out of its timing decisions.
POLL_S = 0.01


class WakeCoordinator:
    """Glue between the arbitration rules and the planes that must answer them."""

    def __init__(
        self,
        arbiter: WakeArbiter,
        senders: dict[str, Send],
        *,
        rooms: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.arbiter = arbiter
        self._senders = senders
        self._rooms = rooms or {}
        self._clock = clock
        self._resolve_task: asyncio.Task[None] | None = None
        self.counters: dict[str, int] = {
            "wake_candidates": 0,
            "wake_admitted": 0,
            "wake_rejected": 0,
            "wake_send_failed": 0,
            "wake_admit_latency_ms": 0,
        }
        self.last_decision: Decision | None = None
        self._window_started_at: float | None = None

    # ------------------------------------------------------------------ state
    def set_turn_open(self, open_: bool) -> None:
        self.arbiter.set_state(turn_open=open_)

    def set_owner(self, device_id: str | None) -> None:
        """The current output owner: only this device may barge while it plays."""
        self.arbiter.set_state(owner_device=device_id)
        self.arbiter.set_state(playing={device_id} if device_id else set())

    def touch(self, device_id: str) -> None:
        self.arbiter.touch(device_id, self._now_ms())

    def replace_sender(self, device_id: str, send: Send) -> None:
        self._senders[device_id] = send

    def drop_device(self, device_id: str) -> None:
        self._senders.pop(device_id, None)
        self._rooms.pop(device_id, None)

    # ----------------------------------------------------------------- events
    async def on_candidate(self, device_id: str, msg: object) -> None:
        """One plane's candidate: decide now (barge/reject) or collect it."""
        wake_id = str(getattr(msg, "wake_id", ""))
        if not wake_id:
            log.info("wake candidate without an id dropped (device %s)", device_id)
            return
        self.counters["wake_candidates"] += 1
        candidate = Candidate(
            wake_id=wake_id,
            device_id=device_id,
            room=self._rooms.get(device_id, ""),
            source=str(getattr(msg, "source", "rui")),
            ts_ms=float(getattr(msg, "ts_ms", 0.0)),
            keyword=str(getattr(msg, "keyword", "") or ""),
            score=getattr(msg, "score", None),
            threshold=getattr(msg, "threshold", None),
            barge=bool(getattr(msg, "barge", False)),
            playing=bool(getattr(msg, "playing", False)),
        )
        decision = self.arbiter.candidate(candidate, self._now_ms())
        if decision is not None:
            await self._apply(decision)
            return
        if self._window_started_at is None:
            self._window_started_at = self._now_ms()
        self._schedule_resolve()

    def _schedule_resolve(self) -> None:
        if self._resolve_task is not None and not self._resolve_task.done():
            return
        self._resolve_task = asyncio.create_task(
            self._resolve_loop(), name="wake-arbiter-resolve")

    async def _resolve_loop(self) -> None:
        """Ask the arbiter until the merge window is over; then send the verdicts."""
        try:
            while True:
                decision = self.arbiter.resolve(self._now_ms())
                if decision is not None:
                    await self._apply(decision)
                    return
                await asyncio.sleep(POLL_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad batch must not kill the loop
            log.warning("wake arbiter resolve failed: %s: %s", type(exc).__name__, exc)

    # --------------------------------------------------------------- verdicts
    async def _apply(self, decision: Decision) -> None:
        self.last_decision = decision
        if self._window_started_at is not None:
            self.counters["wake_admit_latency_ms"] = int(
                self._now_ms() - self._window_started_at)
            self._window_started_at = None
        winner = decision.winner
        if winner is not None:
            self.counters["wake_admitted"] += 1
            await self._send(winner.device_id, WakeAdmit(
                wake_id=winner.wake_id,
                turn_id=f"turn-{winner.wake_id}",
            ))
        for candidate, reason in decision.rejected:
            self.counters["wake_rejected"] += 1
            await self._send(candidate.device_id, WakeReject(
                wake_id=candidate.wake_id, reason=reason,
            ))

    async def _send(self, device_id: str, message: object) -> None:
        send = self._senders.get(device_id)
        if send is None:
            log.warning("no audio plane for device %r; verdict dropped (%s)",
                        device_id, getattr(message, "type", "?"))
            self.counters["wake_send_failed"] += 1
            return
        try:
            await send(message)
        except Exception as exc:  # noqa: BLE001 - a dead plane must not break the batch
            self.counters["wake_send_failed"] += 1
            log.warning("verdict to %r failed: %s: %s", device_id,
                        type(exc).__name__, exc)

    # --------------------------------------------------------------- reporting
    def _now_ms(self) -> float:
        return self._clock() * 1000.0

    def metrics(self) -> dict:
        """The §12.5 facts the console/health/trace need, without any inference."""
        return {
            **self.counters,
            "arbiter_pending": self.arbiter.pending_count,
            "arbitration_candidates": (
                self.last_decision.candidates if self.last_decision else 0
            ),
            "arbitration_winner": (
                self.last_decision.winner.device_id
                if self.last_decision and self.last_decision.winner else ""
            ),
            "arbitration_reason": self.last_decision.decided_by if self.last_decision else "",
            "arbitration_ties": (
                1 if self.last_decision and self.last_decision.decided_by == "tie" else 0
            ),
        }

    async def close(self) -> None:
        task = self._resolve_task
        self._resolve_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
