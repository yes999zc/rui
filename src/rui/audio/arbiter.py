"""Pure decision logic for cross-device wake arbitration (Phase 2).

One household, one turn, one output owner at a time (docs/multi-device-design.md
§9.1 K2). Every audio plane reports wake *candidates*; this module is the
deterministic half of the control plane's WakeArbiter: given the candidates and a
snapshot of the turn/playback state it decides who wins and why, and which
candidates are dropped. It owns no clock, no IO, no asyncio, no audio and no
config store - the caller passes a monotonic ``now_ms`` and the mutable state it
keeps, then sends ``wake_admit``/``wake_reject`` from the returned decisions
(§12.2, §12.3).

Frozen inputs this implements:

- §9.1 K2 / §4.2: a second wake while a turn is open is only a candidate; the
  arbiter never starts a second turn.
- §9.1 K3 / §4.2: selection order is explicit ``room_priority`` -> source group
  (``source_priority``, default device_wake > kws) -> within-group score/threshold
  margin -> last_active (within ``last_active_window_s``) -> devices order.
  Scores are never compared across sources.
- §4.2.2 + §11 Q5: only the current output owner device may barge; the barge
  fast path does not wait for the merge window (§12.3.2).
- §4.5 + §12.3.3: a candidate from a *different* device in the same room as a
  playing device is ``cross_echo``; other devices while something plays are
  ``lost_playing`` (§12.3.2).
- §4.2.6 / §12.3.5: the same keyword admitted inside ``duplicate_window_ms`` is
  ``duplicate``; an empty keyword (a device wake with no transcript) is not
  deduped by word.
- §9.2 D1: the winner's no-speech path ends the session. The arbiter never
  promotes the runner-up, never retries and never ends a session itself - it only
  reports decisions.

Deliberately *not* here:

- ``admit_timeout``: that is the plane's own timer (it drops the candidate if the
  control plane stays silent); the control plane never sends it (§12.3.6). It is
  kept in :data:`REJECT_REASONS` because the wire vocabulary is frozen.
- Counters/trace storage (§12.5): a :class:`Decision` carries the facts
  (candidate count, winner, per-candidate reason, ``wake_source``) and the control
  plane counts them.
- Retrying, promoting, or ending turns (§9.2 D1): those are control-plane actions.

Timing rule: all windows are compared against the ``now_ms`` the caller provides;
the merge window opens when the *first* candidate is handed in, never when that
candidate was observed (plane clocks are not comparable).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, cast

from ..config.schema import MultiDeviceConfig

#: The two sources a plane can report, and the §12.5 ``wake_source`` trace names.
#: ``device`` means a device-side OWW hit ("device_wake"), ``rui`` means the RUI
#: KWS that runs in the plane ("kws"). Only the names matter for grouping.
Source = Literal["rui", "device"]
WAKE_SOURCE: Final[dict[str, str]] = {"rui": "kws", "device": "device_wake"}

#: §12.2: the complete ``wake_reject`` vocabulary. ``admit_timeout`` belongs to the
#: plane (§12.3.6) and is therefore never emitted by this module.
REJECT_REASONS: Final[tuple[str, ...]] = (
    "turn_open",
    "lost_same_word",
    "cross_echo",
    "lost_playing",
    "duplicate",
    "over_capacity",
    "admit_timeout",
)

#: §12.5 ``arbitration_reason`` vocabulary for an admitted winner. The doc also
#: reserves ``playing`` and ``same_room``; this arbiter reports the playback fast
#: path as ``barge`` and same-room suppression as the ``cross_echo`` rejection, so
#: neither is emitted. ``room_priority``/source-group/margin all report ``score``
#: because they are the evidence-quality step of §12.5's table, whereas
#: ``Decision.decided_by`` names the exact step.
ADMIT_REASONS: Final[tuple[str, ...]] = ("barge", "score", "last_active", "tie")

#: Characters of a rank that never wins: an unranked device/source sorts last.
_BIG: Final = 1 << 30

#: ``set_state`` sentinel: ``None`` is a real value for ``owner_device``.
_UNSET: Final = object()

#: ``Decision.decided_by`` -> §12.5 ``arbitration_reason``.
_ADMIT_REASON: Final[dict[str, str]] = {
    "single": "score",
    "room_priority": "score",
    "source": "score",
    "margin": "score",
    "last_active": "last_active",
    "stable": "tie",
    "barge": "barge",
}


@dataclass(frozen=True)
class Candidate:
    """One wake candidate, enriched by the control plane (§4.1, §12.2).

    A plane only reports what it knows (``wake_id``, source facts, ``barge``,
    ``playing``); ``device_id``/``room`` are filled from config by the control
    plane ("RUI enriches"). ``ts_ms`` is the plane's observation time and is
    informational here: windows use the caller's ``now_ms``.

    ``room`` may be empty when the caller relies on the configured room of the
    device; same-room rules fall back to :class:`ArbiterDevice.room`.
    """

    wake_id: str
    device_id: str
    room: str
    source: Source
    ts_ms: float
    #: Device wakes may carry no transcript, and then are not deduped by word.
    keyword: str = ""
    score: float | None = None
    threshold: float | None = None
    #: Device-local barge suggestion (§4.1; the plane's local gates already passed).
    barge: bool = False
    #: Whether this device is producing sound right now (plane's own view).
    playing: bool = False

    def __post_init__(self) -> None:
        if self.source not in WAKE_SOURCE:
            raise ValueError(f"unknown wake source {self.source!r}; expected 'rui' or 'device'")

    @property
    def wake_source(self) -> str:
        """§12.5 ``wake_source`` value for this candidate."""
        return WAKE_SOURCE[self.source]

    @property
    def margin(self) -> float | None:
        """Normalised evidence margin, comparable only inside one source group.

        ``None`` when the plane did not report both numbers (a bare KWS hit is
        "命中即 1.0", §4.1) - such a candidate has no quantitative evidence and
        sorts below a measured margin inside its own group.
        """
        if self.score is None or self.threshold is None:
            return None
        return self.score - self.threshold


@dataclass(frozen=True)
class ArbiterDevice:
    """The config view of one device, as arbitration needs it (§4.1, §5.3).

    ``index`` and ``room_priority_rank`` are derived from the caller's sequence
    and ``multi_device.arbitration.room_priority`` when left at their defaults
    (``-1`` / ``None``), so a caller can pass ``ArbiterDevice(id, room)``.
    ``room_priority`` holds *device ids* (despite its name, §5.3).
    """

    device_id: str
    room: str = ""
    enabled: bool = True
    #: Position in ``multi_device.devices`` order; ``-1`` means "use list position".
    index: int = -1
    #: Explicit priority rank; ``None`` means "derive from room_priority, else none".
    room_priority_rank: int | None = None


@dataclass
class ArbiterState:
    """The control plane's view of the house, mutated by the control plane.

    The arbiter reads it on every call and writes only two things on admission
    (§12.3): the duplicate history of the admitted keyword and that device's
    ``last_active_ms``. Turn/owner/playback changes belong to the control plane:
    a rendered decision never ends a session or flips playback by itself (§9.2 D1).
    """

    turn_open: bool = False
    #: Current output owner (§12.4). The only device allowed to barge.
    owner_device: str | None = None
    #: Devices currently producing sound; any non-empty value gates candidates.
    playing: set[str] = field(default_factory=set)
    #: device_id -> monotonic ms of its room's last activity (§4.1).
    last_active_ms: dict[str, float] = field(default_factory=dict)
    #: Admitted keyword -> monotonic ms of the admit (§4.2.6).
    admitted_words: dict[str, float] = field(default_factory=dict)

    def reset(self) -> None:
        """Forget everything (new session / supervisor restart)."""
        self.turn_open = False
        self.owner_device = None
        self.playing.clear()
        self.last_active_ms.clear()
        self.admitted_words.clear()


@dataclass(frozen=True)
class Decision:
    """One arbitration outcome: admit a winner, or drop a candidate/batch.

    ``reason`` is an ``wake_reject`` reason when ``winner`` is ``None``, otherwise
    one of the §12.5 ``arbitration_reason`` values (see :data:`ADMIT_REASONS`).
    ``decided_by`` names the exact §4.2 step for the trace
    (``single``/``room_priority``/``source``/``margin``/``last_active``/``stable``/
    ``barge``); it is empty for a rejection.
    """

    winner: Candidate | None
    reason: str
    rejected: list[tuple[Candidate, str]] = field(default_factory=list)
    #: §12.5 ``arbitration_candidates``: candidates considered by this decision.
    candidates: int = 0
    #: §12.5 ``wake_source`` per candidate, keyed by ``wake_id`` (insertion order).
    wake_sources: dict[str, str] = field(default_factory=dict)
    #: Exact tie-break step that decided the winner (§4.2), empty for a rejection.
    decided_by: str = ""
    #: How long the batch sat in the merge window (0 for immediate decisions).
    waited_ms: float = 0.0

    @property
    def admitted(self) -> bool:
        """True when this decision admits ``winner`` instead of dropping a batch."""
        return self.winner is not None


@dataclass(frozen=True)
class _DeviceView:
    """A normalized :class:`ArbiterDevice` (derived index/rank, active set)."""

    device_id: str
    room: str
    index: int
    room_priority_rank: int | None


class WakeArbiter:
    """Deterministic cross-device wake arbitration (§4, §9.1 K3, §12.3).

    Usage, from the control plane's event loop::

        arbiter = WakeArbiter(cfg.multi_device, devices)
        arbiter.set_state(turn_open=True, owner_device="lx06", playing={"lx06"})
        decision = arbiter.candidate(candidate, now_ms)   # fast path / rejects
        decision = arbiter.resolve(now_ms)                # after the merge window

    ``candidate()`` and ``resolve()`` are the only ways in; every decision leaves
    the object exactly once (a decision produced while flushing an expired batch is
    handed out by the next call, never dropped). The arbiter never sleeps: the
    caller owns the merge-window timer and calls ``resolve()`` when it fires.
    """

    def __init__(self, cfg: MultiDeviceConfig, devices: Sequence[ArbiterDevice]) -> None:
        self.cfg = cfg
        self.arbitration = cfg.arbitration
        self.state = ArbiterState()

        self.merge_window_ms = float(cfg.arbitration.candidate_merge_window_ms)
        self.duplicate_window_ms = float(cfg.arbitration.duplicate_window_ms)
        self.last_active_window_ms = float(cfg.arbitration.last_active_window_s) * 1000.0
        # Lower rank wins; sources not listed in source_priority sort last.
        self._source_rank: dict[str, int] = {
            name: rank for rank, name in enumerate(cfg.arbitration.source_priority)
        }

        self._all: dict[str, _DeviceView] = {}
        for position, device in enumerate(devices):
            if device.device_id in self._all:
                raise ValueError(f"duplicate device id {device.device_id!r} in arbiter devices")
            view = _DeviceView(
                device_id=device.device_id,
                room=device.room,
                index=device.index if device.index >= 0 else position,
                room_priority_rank=(
                    device.room_priority_rank
                    if device.room_priority_rank is not None
                    else self._configured_rank(device.device_id)
                ),
            )
            self._all[device.device_id] = view

        # §5.3/D4: disabled devices do not arbitrate, and enabled devices beyond
        # max_planes never got a plane, so neither can win.
        enabled = sorted(
            (d for d in devices if d.enabled),
            key=lambda d: (self._all[d.device_id].index, d.device_id),
        )
        active_ids = {d.device_id for d in enabled[: cfg.max_planes]}
        self._active: dict[str, _DeviceView] = {
            device_id: view for device_id, view in self._all.items() if device_id in active_ids
        }

        # Candidates in the current merge window, and decisions waiting to be read.
        self._pending: list[tuple[Candidate, float]] = []
        self._pending_since: float | None = None
        self._ready: list[Decision] = []

    # ------------------------------------------------------------------ public
    @property
    def pending_count(self) -> int:
        """Number of candidates collected for the current merge window."""
        return len(self._pending)

    def candidate(self, candidate: Candidate, now_ms: float) -> Decision | None:
        """Hand in one candidate; return an immediate decision, or ``None``.

        Returns a :class:`Decision` for the barge fast path (§12.3.2) and for the
        immediate rejects (``turn_open``/``cross_echo``/``lost_playing``/
        ``duplicate``/``over_capacity``); ``None`` means "collected, call
        ``resolve()`` later". A candidate from a device that has no active plane is
        rejected ``over_capacity`` (D4: over the limit is warned, never queued).

        If the previous merge window already elapsed (the caller was late), that
        batch is resolved first and its decision is returned; this candidate starts
        a new batch. No decision is ever dropped, only deferred to the next call.
        """
        if self._expired(now_ms):
            self._resolve_pending(now_ms)
        self._handle(candidate, now_ms)
        return self._ready.pop(0) if self._ready else None

    def resolve(self, now_ms: float) -> Decision | None:
        """Return the pending decision once the merge window has elapsed.

        ``None`` while still collecting (or when nothing is pending). The window is
        measured from the first collected candidate, so a batch always waits the
        full ``candidate_merge_window_ms`` - uniform behaviour and a deterministic
        order-independent winner.
        """
        if self._ready:
            return self._ready.pop(0)
        if not self._pending or self._pending_since is None:
            return None
        if now_ms - self._pending_since < self.merge_window_ms:
            return None
        self._resolve_pending(now_ms)
        return self._ready.pop(0) if self._ready else None

    def set_state(
        self,
        *,
        turn_open: bool | None = None,
        owner_device: str | None | object = _UNSET,
        playing: Iterable[str] | None = None,
        last_active_ms: Mapping[str, float] | None = None,
    ) -> None:
        """Update the control-plane state snapshot (only supplied fields change).

        ``owner_device=None`` (or a device id) is a real value: use it to clear or
        claim output ownership (§12.4). ``playing`` replaces the whole set;
        ``last_active_ms`` merges into the existing map.
        """
        if turn_open is not None:
            self.state.turn_open = turn_open
        if owner_device is not _UNSET:
            self.state.owner_device = cast("str | None", owner_device)
        if playing is not None:
            self.state.playing = set(playing)
        if last_active_ms is not None:
            self.state.last_active_ms.update(last_active_ms)

    def touch(self, device_id: str, now_ms: float) -> None:
        """Record activity for a device (turn end, volume key, ... see §4.1)."""
        self.state.last_active_ms[device_id] = now_ms

    def reset(self) -> None:
        """Drop pending work and reset the state snapshot (new session)."""
        self._pending.clear()
        self._pending_since = None
        self._ready.clear()
        self.state.reset()

    # ------------------------------------------------------------- collection
    def _expired(self, now_ms: float) -> bool:
        return (
            bool(self._pending)
            and self._pending_since is not None
            and now_ms - self._pending_since >= self.merge_window_ms
        )

    def _handle(self, candidate: Candidate, now_ms: float) -> None:
        """Route one candidate: fast path, immediate reject, or collect."""
        view = self._active.get(candidate.device_id)
        if view is None:
            # No plane runs this device (unknown, disabled, or beyond max_planes).
            self._ready.append(self._reject(candidate, "over_capacity"))
            return

        # §4.2.1 exception + §12.3.2: the output owner's own barge is the only way
        # through a playing speaker and does not wait for the merge window. It is
        # checked before turn_open because a barge normally happens inside a turn,
        # and before duplicate because the greeting word repeats within the turn.
        if candidate.barge and candidate.playing and self.state.owner_device == candidate.device_id:
            rejected = self._flush_pending(fallback="lost_playing")
            self._note_admitted(candidate, now_ms)
            self._ready.append(
                self._decision(
                    winner=candidate,
                    reason="barge",
                    decided_by="barge",
                    rejected=rejected,
                    candidates=1 + len(rejected),
                    waited_ms=0.0,
                )
            )
            return

        # §4.2.1 / §12.3.4: an open turn takes no other candidate (K2).
        if self.state.turn_open:
            self._ready.append(self._reject(candidate, "turn_open"))
            return

        # §4.2.2 / §12.3.2: while a device plays, only its owner barge may pass.
        if self.state.playing:
            reason = "cross_echo" if self._same_room_echo(candidate, view) else "lost_playing"
            self._ready.append(self._reject(candidate, reason))
            return

        # §4.2.6 / §12.3.5: one admit per word per duplicate window.
        if self._is_duplicate(candidate, now_ms):
            self._ready.append(self._reject(candidate, "duplicate"))
            return

        # §12.3.1: one pending candidate per device per merge window; a second hit
        # from the same device is a local repeat, not a second candidate.
        if any(pending.device_id == candidate.device_id for pending, _ in self._pending):
            self._ready.append(self._reject(candidate, "over_capacity"))
            return

        self._pending.append((candidate, now_ms))
        if self._pending_since is None:
            self._pending_since = now_ms

    def _resolve_pending(self, now_ms: float) -> None:
        """Select a winner for the collected batch (or drop it on a state change)."""
        since = self._pending_since if self._pending_since is not None else now_ms
        batch = [candidate for candidate, _ in self._pending]
        waited_ms = now_ms - since
        self._pending.clear()
        self._pending_since = None

        # The control plane may have opened a turn or started playback while the
        # window was open. The batch was collected under idle assumptions, so it
        # must not produce an admit any more: re-apply the gates (K2 stays true).
        if self.state.turn_open:
            self._ready.append(
                self._decision(
                    winner=None,
                    reason="turn_open",
                    rejected=[(c, "turn_open") for c in batch],
                    candidates=len(batch),
                    waited_ms=waited_ms,
                )
            )
            return
        if self.state.playing:
            rejected = [
                (
                    c,
                    "cross_echo"
                    if self._same_room_echo(c, self._active[c.device_id])
                    else "lost_playing",
                )
                for c in batch
            ]
            self._ready.append(
                self._decision(
                    winner=None,
                    reason=rejected[0][1],
                    rejected=rejected,
                    candidates=len(batch),
                    waited_ms=waited_ms,
                )
            )
            return

        winner, decided_by = self._select(batch, now_ms)
        self._note_admitted(winner, now_ms)
        self._ready.append(
            self._decision(
                winner=winner,
                reason=_ADMIT_REASON[decided_by],
                decided_by=decided_by,
                rejected=[(c, "lost_same_word") for c in batch if c is not winner],
                candidates=len(batch),
                waited_ms=waited_ms,
            )
        )

    # ------------------------------------------------------------- selection
    def _select(self, batch: Sequence[Candidate], now_ms: float) -> tuple[Candidate, str]:
        """Pick the winner and the step that decided it (§4.2, §9.1 K3)."""
        if len(batch) == 1:
            return batch[0], "single"

        remaining = list(batch)
        decided_by = ""
        steps = (
            ("room_priority", lambda c: self._key_room_priority(c)),
            ("source", lambda c: self._key_source(c)),
            ("margin", lambda c: _key_margin(c.margin)),
            ("last_active", lambda c: self._key_last_active(c, now_ms)),
        )
        for name, step_key in steps:
            scored = [(step_key(candidate), candidate) for candidate in remaining]
            best = min(key for key, _ in scored)
            tied = [candidate for key, candidate in scored if key == best]
            if not decided_by and len(tied) < len(remaining):
                decided_by = name
            remaining = tied
            if len(remaining) == 1:
                break

        if len(remaining) > 1:
            # Stable order: config devices index, then wake_id (a total order).
            remaining.sort(key=lambda c: (self._active[c.device_id].index, c.wake_id))
            if not decided_by:
                decided_by = "stable"
        return remaining[0], decided_by

    def _key_room_priority(self, candidate: Candidate) -> int:
        rank = self._active[candidate.device_id].room_priority_rank
        return rank if rank is not None else _BIG

    def _key_source(self, candidate: Candidate) -> int:
        return self._source_rank.get(candidate.wake_source, _BIG)

    def _key_last_active(self, candidate: Candidate, now_ms: float) -> tuple[int, float]:
        last = self.state.last_active_ms.get(candidate.device_id)
        if last is None or now_ms - last > self.last_active_window_ms:
            # Outside the window (or never active): no recency signal, so the
            # candidates tie here and fall through to the stable order.
            return (1, 0.0)
        return (0, -last)  # more recent = larger `last` = smaller key

    # -------------------------------------------------------------- evidence
    def _same_room_echo(self, candidate: Candidate, view: _DeviceView) -> bool:
        """§4.5: a *different* device in the same room as a playing device."""
        if candidate.device_id in self.state.playing:
            return False
        room = candidate.room or view.room
        if not room:
            return False
        for playing_id in self.state.playing:
            playing_view = self._active.get(playing_id)
            if playing_view is not None and playing_view.room == room:
                return True
        return False

    def _is_duplicate(self, candidate: Candidate, now_ms: float) -> bool:
        """§4.2.6: same non-empty keyword admitted inside duplicate_window_ms."""
        if not candidate.keyword:
            return False
        last = self.state.admitted_words.get(candidate.keyword)
        return last is not None and now_ms - last < self.duplicate_window_ms

    def _note_admitted(self, candidate: Candidate, now_ms: float) -> None:
        """On admission record duplicate history and last-active (§12.3)."""
        if candidate.keyword:
            self._prune_admitted(now_ms)
            self.state.admitted_words[candidate.keyword] = now_ms
        self.state.last_active_ms[candidate.device_id] = now_ms

    def _prune_admitted(self, now_ms: float) -> None:
        """Keep the duplicate history bounded; entries at/after the window are dead."""
        cutoff = now_ms - self.duplicate_window_ms
        for keyword in [word for word, ts in self.state.admitted_words.items() if ts <= cutoff]:
            del self.state.admitted_words[keyword]

    # -------------------------------------------------------------- plumbing
    def _flush_pending(self, *, fallback: str) -> list[tuple[Candidate, str]]:
        """Drop the current batch because an immediate admit overtook it."""
        rejected: list[tuple[Candidate, str]] = []
        for candidate, _ in self._pending:
            view = self._active[candidate.device_id]
            if self.state.turn_open:
                reason = "turn_open"
            elif self._same_room_echo(candidate, view):
                reason = "cross_echo"
            elif self.state.playing:
                reason = "lost_playing"
            else:
                reason = fallback
            rejected.append((candidate, reason))
        self._pending.clear()
        self._pending_since = None
        return rejected

    def _configured_rank(self, device_id: str) -> int | None:
        """Rank from ``arbitration.room_priority`` (a list of device ids, §5.3)."""
        try:
            return list(self.arbitration.room_priority).index(device_id)
        except ValueError:
            return None

    def _reject(self, candidate: Candidate, reason: str) -> Decision:
        return self._decision(
            winner=None,
            reason=reason,
            rejected=[(candidate, reason)],
            candidates=1,
            waited_ms=0.0,
        )

    def _decision(
        self,
        *,
        winner: Candidate | None,
        reason: str,
        rejected: list[tuple[Candidate, str]],
        candidates: int,
        waited_ms: float,
        decided_by: str = "",
    ) -> Decision:
        wake_sources: dict[str, str] = {}
        if winner is not None:
            wake_sources[winner.wake_id] = winner.wake_source
        for candidate, _ in rejected:
            wake_sources[candidate.wake_id] = candidate.wake_source
        return Decision(
            winner=winner,
            reason=reason,
            rejected=rejected,
            candidates=candidates,
            wake_sources=wake_sources,
            decided_by=decided_by,
            waited_ms=waited_ms,
        )


def _key_margin(margin: float | None) -> tuple[int, float]:
    """Sort key for within-group evidence: measured margin, higher is better.

    ``None`` (no score/threshold comparison possible, §4.1) sorts below any
    measured margin inside the same source group; sources are never compared
    because the source step precedes this one (K3).
    """
    if margin is None:
        return (1, 0.0)
    return (0, -margin)
