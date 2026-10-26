"""Table-driven tests for the pure WakeArbiter (docs/multi-device-design.md §4/§9/§12.3).

Everything here is deterministic on purpose: the arbiter receives an explicit
`now_ms`, so no test reads the wall clock, and every candidate is constructed with
a fixed `wake_id`. The tie-break chain is asserted by `Decision.decided_by`, which
names the §4.2 step that decided the winner, while `Decision.reason` stays in the
frozen trace vocabulary of §12.5.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from rui.audio.arbiter import (
    ADMIT_REASONS,
    REJECT_REASONS,
    ArbiterDevice,
    ArbiterState,
    Candidate,
    Decision,
    WakeArbiter,
)
from rui.config.schema import ArbitrationConfig, MultiDeviceConfig

T0 = 10_000.0

LX06 = "lx06"
DOT2 = "dot2"
LIVING = "living"
STUDY = "study"


# --------------------------------------------------------------------- helpers
def make_arbiter(
    *,
    rooms: Mapping[str, str] | None = None,
    devices: Sequence[ArbiterDevice] | None = None,
    merge: int = 400,
    duplicate: int = 2000,
    last_active_s: int = 120,
    room_priority: Sequence[str] = (),
    source_priority: Sequence[str] = ("device_wake", "kws"),
    max_planes: int = 4,
) -> WakeArbiter:
    """One arbiter with the schema defaults unless a test overrides a window."""
    cfg = MultiDeviceConfig(
        enabled=True,
        max_planes=max_planes,
        arbitration=ArbitrationConfig(
            candidate_merge_window_ms=merge,
            duplicate_window_ms=duplicate,
            last_active_window_s=last_active_s,
            room_priority=list(room_priority),
            source_priority=list(source_priority),
        ),
    )
    if devices is None:
        room_map = dict(rooms) if rooms is not None else {LX06: LIVING, DOT2: STUDY}
        devices = [ArbiterDevice(device_id=dev, room=room) for dev, room in room_map.items()]
    return WakeArbiter(cfg, devices)


def cand(
    device: str = LX06,
    *,
    wake_id: str | None = None,
    room: str = "",
    source: str = "device",
    ts_ms: float = T0,
    keyword: str = "",
    score: float | None = None,
    threshold: float | None = None,
    barge: bool = False,
    playing: bool = False,
) -> Candidate:
    """A candidate as the control plane would enrich it (room defaults to config)."""
    return Candidate(
        wake_id=wake_id or f"wake-{device}",
        device_id=device,
        room=room,
        source=source,  # type: ignore[arg-type]
        ts_ms=ts_ms,
        keyword=keyword,
        score=score,
        threshold=threshold,
        barge=barge,
        playing=playing,
    )


def resolve_at_window(arb: WakeArbiter, start: float = T0) -> Decision | None:
    """Resolve exactly at the end of the 400 ms merge window."""
    return arb.resolve(start + 400.0)


# ------------------------------------------------------- merge window / basics
def test_single_candidate_admitted_after_full_merge_window() -> None:
    """§12.3.1: no arm/admit before the window elapsed; one candidate still waits."""
    arb = make_arbiter()
    candidate = cand(LX06, wake_id="w1", keyword="你好小瑞")

    assert arb.candidate(candidate, T0) is None
    assert arb.pending_count == 1
    assert arb.resolve(T0 + 399.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.admitted
    assert decision.winner is candidate
    assert decision.reason == "score"
    assert decision.decided_by == "single"
    assert decision.candidates == 1
    assert decision.rejected == []
    assert decision.wake_sources == {"w1": "device_wake"}
    assert decision.waited_ms == 400.0
    # Admission records duplicate history and last-active, nothing else (§12.3).
    assert arb.state.admitted_words["你好小瑞"] == T0 + 400.0
    assert arb.state.last_active_ms[LX06] == T0 + 400.0
    assert arb.state.turn_open is False  # ending/opening turns is the control plane


def test_empty_batch_returns_none() -> None:
    """No candidates: resolve is a no-op and stays a no-op."""
    arb = make_arbiter()
    assert arb.resolve(T0) is None
    assert arb.resolve(T0 + 10_000.0) is None
    assert arb.pending_count == 0


def test_merge_window_winner_and_losers_are_stable_and_order_independent() -> None:
    """§4.2.5: identical candidates fall to devices order (tie), arrival order free."""
    arb = make_arbiter()
    first = cand(DOT2, wake_id="w-dot2")
    second = cand(LX06, wake_id="w-lx06")
    assert arb.candidate(first, T0) is None
    assert arb.candidate(second, T0 + 100.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is second
    assert decision.decided_by == "stable"
    assert decision.reason == "tie"
    assert decision.rejected == [(first, "lost_same_word")]
    assert decision.candidates == 2
    # §9.2 D1: the runner-up is never promoted; a later resolve has nothing to say.
    assert arb.resolve(T0 + 10_000.0) is None


@pytest.mark.parametrize("arrival", [(LX06, DOT2), (DOT2, LX06)])
def test_winner_is_arrival_order_independent(arrival: tuple[str, str]) -> None:
    """Stable order must not depend on which plane happened to report first."""
    arb = make_arbiter()
    candidates = {
        LX06: cand(LX06, wake_id="w-lx06"),
        DOT2: cand(DOT2, wake_id="w-dot2"),
    }
    for offset, device in enumerate(arrival):
        assert arb.candidate(candidates[device], T0 + offset * 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is candidates[LX06]
    assert decision.decided_by == "stable"


# ------------------------------------------------------------------ turn/playback
def test_turn_open_blocks_every_new_candidate() -> None:
    """§4.2.1 / §12.3.4 (K2): an open turn admits no second wake."""
    arb = make_arbiter()
    arb.set_state(turn_open=True)

    decision = arb.candidate(cand(LX06), T0)
    assert decision is not None
    assert not decision.admitted
    assert decision.reason == "turn_open"
    assert decision.rejected[0][1] == "turn_open"
    assert arb.pending_count == 0
    assert arb.resolve(T0 + 5_000.0) is None


def test_barge_fast_path_admits_owner_immediately() -> None:
    """§12.3.2: owner device + barge + playing admits without the merge window."""
    arb = make_arbiter()
    arb.set_state(owner_device=LX06, playing={LX06})
    barge = cand(LX06, wake_id="barge", keyword="你好小瑞", barge=True, playing=True)

    decision = arb.candidate(barge, T0)
    assert decision is not None
    assert decision.admitted
    assert decision.winner is barge
    assert decision.reason == "barge"
    assert decision.decided_by == "barge"
    assert decision.candidates == 1
    assert arb.pending_count == 0
    assert arb.state.admitted_words["你好小瑞"] == T0
    assert arb.state.last_active_ms[LX06] == T0
    assert arb.resolve(T0 + 1_000.0) is None


def test_barge_is_allowed_inside_an_open_turn() -> None:
    """§4.2.1 exception: the owner's barge passes even while the turn is open."""
    arb = make_arbiter()
    arb.set_state(turn_open=True, owner_device=LX06, playing={LX06})

    decision = arb.candidate(cand(LX06, barge=True, playing=True), T0)
    assert decision is not None
    assert decision.admitted
    assert decision.reason == "barge"


@pytest.mark.parametrize(
    ("device", "barge", "playing", "owner", "expected"),
    [
        (LX06, False, True, LX06, "lost_playing"),  # no barge suggestion
        (LX06, True, False, LX06, "lost_playing"),  # plane says it is not speaking
        (LX06, True, True, None, "lost_playing"),  # nobody owns the output (§12.4)
    ],
)
def test_barge_gate_rejects_non_owner_candidates(
    device: str, barge: bool, playing: bool, owner: str | None, expected: str
) -> None:
    """Only the output owner's real barge gets through; everything else loses."""
    arb = make_arbiter()
    arb.set_state(owner_device=owner, playing={LX06})

    decision = arb.candidate(cand(device, barge=barge, playing=playing), T0)
    assert decision is not None
    assert decision.reason == expected
    assert decision.rejected[0][1] == expected


def test_a_barge_from_another_room_is_not_rejected() -> None:
    """P1 (task-18): another room's barge is a wake; only its own room is blocked."""
    arb = make_arbiter()
    arb.set_state(owner_device=LX06, playing={LX06})

    decision = arb.candidate(cand(DOT2, barge=True, playing=True), T0)

    assert decision is None, "the other room's barge was rejected outright"
    resolved = arb.resolve(T0 + 1_000.0)
    assert resolved is not None and resolved.admitted


@pytest.mark.parametrize(
    ("rooms", "candidate_room", "expected"),
    [
        ({LX06: LIVING, DOT2: LIVING}, "", "cross_echo"),  # same room, §4.5
        ({LX06: LIVING, DOT2: STUDY}, LIVING, "cross_echo"),  # candidate's room wins
    ],
)
def test_playing_rejections(
    rooms: Mapping[str, str], candidate_room: str, expected: str
) -> None:
    """A playing house gates non-owner candidates: cross_echo if same room (§4.5)."""
    arb = make_arbiter(rooms=rooms)
    arb.set_state(owner_device=LX06, playing={LX06})

    decision = arb.candidate(cand(DOT2, room=candidate_room), T0)
    assert decision is not None
    assert decision.reason == expected
    assert decision.rejected[0][1] == expected


def test_playing_device_itself_is_not_cross_echo() -> None:
    """§4.5 suppresses a *different* device; the playing device itself is lost_playing."""
    arb = make_arbiter(rooms={LX06: LIVING, DOT2: LIVING})
    arb.set_state(owner_device=LX06, playing={LX06})

    decision = arb.candidate(cand(LX06, barge=False, playing=True), T0)
    assert decision is not None
    assert decision.reason == "lost_playing"


# ------------------------------------------------------------------- duplicate
@pytest.mark.parametrize(("offset", "expected"), [(1_999.0, "duplicate"), (2_000.0, "collected")])
def test_duplicate_window_boundary(offset: float, expected: str) -> None:
    """§4.2.6: same word inside duplicate_window_ms is duplicate; at the edge it is new."""
    arb = make_arbiter(duplicate=2000)
    first = cand(LX06, wake_id="w1", keyword="你好小瑞")
    assert arb.candidate(first, T0) is None
    assert resolve_at_window(arb) is not None

    second = cand(LX06, wake_id="w2", keyword="你好小瑞")
    decision = arb.candidate(second, T0 + 400.0 + offset)
    if expected == "duplicate":
        assert decision is not None
        assert decision.reason == "duplicate"
        assert arb.pending_count == 0
    else:
        assert decision is None
        assert arb.pending_count == 1
        assert arb.resolve(T0 + 400.0 + offset + 400.0) is not None


def test_duplicate_is_global_across_devices() -> None:
    """§4.2.6: one admit per word per window across the whole house."""
    arb = make_arbiter()
    assert arb.candidate(cand(LX06, wake_id="w1", keyword="你好小瑞"), T0) is None
    assert resolve_at_window(arb) is not None

    decision = arb.candidate(cand(DOT2, wake_id="w2", keyword="你好小瑞"), T0 + 500.0)
    assert decision is not None
    assert decision.reason == "duplicate"


def test_empty_and_different_keywords_are_not_deduplicated() -> None:
    """§4.1: a device wake with no transcript has no word to dedupe on."""
    arb = make_arbiter()
    assert arb.candidate(cand(LX06, wake_id="w1", keyword=""), T0) is None
    assert resolve_at_window(arb) is not None

    second = cand(LX06, wake_id="w2", keyword="")
    assert arb.candidate(second, T0 + 500.0) is None  # collected, not duplicate
    assert arb.resolve(T0 + 900.0) is not None


def test_second_candidate_from_same_device_is_over_capacity() -> None:
    """§12.3.1: one pending candidate per device per window; a repeat is dropped."""
    arb = make_arbiter()
    first = cand(LX06, wake_id="w1")
    assert arb.candidate(first, T0) is None

    decision = arb.candidate(cand(LX06, wake_id="w2"), T0 + 50.0)
    assert decision is not None
    assert decision.reason == "over_capacity"
    assert decision.rejected[0][1] == "over_capacity"
    assert arb.pending_count == 1
    resolved = resolve_at_window(arb)
    assert resolved is not None and resolved.winner is first


def test_candidates_without_an_active_plane_are_over_capacity() -> None:
    """§5.3 / §9.2 D4: disabled, unknown, or beyond max_planes devices never win."""
    rooms = {LX06: LIVING, DOT2: STUDY, "dot3": "kitchen", "dot4": "bath", "dot5": "hall"}
    devices = [ArbiterDevice(device_id=dev, room=room) for dev, room in rooms.items()]
    devices.append(ArbiterDevice(device_id="off", room="garage", enabled=False))
    arb = make_arbiter(devices=devices, max_planes=2)  # only lx06 + dot2 get a plane

    for device_id in ("dot3", "dot5", "off", "ghost"):
        decision = arb.candidate(cand(device_id), T0)
        assert decision is not None
        assert decision.reason == "over_capacity"
        assert decision.rejected[0][1] == "over_capacity"
    assert arb.pending_count == 0


# ---------------------------------------------------------------- tie-break chain
def test_room_priority_beats_source_group() -> None:
    """§9.1 K3 step 1: an explicitly ranked device wins even from the weaker source."""
    arb = make_arbiter(room_priority=[DOT2])
    kws = cand(DOT2, wake_id="kws", source="rui", score=1.0)
    oww = cand(LX06, wake_id="oww", score=0.9, threshold=0.5)
    assert arb.candidate(kws, T0) is None
    assert arb.candidate(oww, T0 + 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is kws
    assert decision.decided_by == "room_priority"
    assert decision.reason == "score"
    assert (oww, "lost_same_word") in decision.rejected


def test_source_group_beats_cross_source_margin() -> None:
    """§9.1 K3: scores are never compared across sources (KWS 0.9 still loses)."""
    arb = make_arbiter()
    weak_oww = cand(LX06, wake_id="oww", score=0.4, threshold=0.39)  # margin 0.01
    strong_kws = cand(DOT2, wake_id="kws", source="rui", score=1.0, threshold=0.1)  # 0.9
    assert arb.candidate(weak_oww, T0) is None
    assert arb.candidate(strong_kws, T0 + 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is weak_oww
    assert decision.decided_by == "source"


def test_source_priority_is_configurable() -> None:
    """§11 Q2: source_priority only decides the group order, and is config-driven."""
    arb = make_arbiter(source_priority=("kws", "device_wake"))
    oww = cand(LX06, wake_id="oww", score=0.9, threshold=0.5)
    kws = cand(DOT2, wake_id="kws", source="rui", score=1.0, threshold=0.9)
    assert arb.candidate(oww, T0) is None
    assert arb.candidate(kws, T0 + 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is kws
    assert decision.decided_by == "source"


@pytest.mark.parametrize("arrival", [(LX06, DOT2), (DOT2, LX06)])
def test_higher_margin_wins_within_source(arrival: tuple[str, str]) -> None:
    """§4.2.3 (same group only): larger score-threshold margin wins."""
    arb = make_arbiter()
    low = cand(LX06, wake_id="low", score=0.6, threshold=0.5)  # margin 0.10
    high = cand(DOT2, wake_id="high", score=0.9, threshold=0.2)  # margin 0.70
    candidates = {LX06: low, DOT2: high}
    for offset, device in enumerate(arrival):
        assert arb.candidate(candidates[device], T0 + offset * 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is high
    assert decision.decided_by == "margin"
    assert decision.rejected == [(low, "lost_same_word")]


def test_known_margin_beats_missing_margin_in_same_source() -> None:
    """§4.1: a bare KWS-style hit (no score/threshold) has no comparable margin."""
    arb = make_arbiter()  # lx06 would win the later stable-order step
    unmeasured = cand(LX06, wake_id="unmeasured")
    measured = cand(DOT2, wake_id="measured", score=0.7, threshold=0.5)
    assert arb.candidate(unmeasured, T0) is None
    assert arb.candidate(measured, T0 + 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is measured
    assert decision.decided_by == "margin"


def test_last_active_wins_tie_break() -> None:
    """§4.2.4: within the window, the more recently active device wins."""
    arb = make_arbiter()
    arb.touch(LX06, T0 - 100_000.0)
    arb.touch(DOT2, T0 - 5_000.0)
    assert arb.candidate(cand(LX06, wake_id="w-lx06"), T0) is None
    assert arb.candidate(cand(DOT2, wake_id="w-dot2"), T0 + 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.winner is not None
    assert decision.winner.device_id == DOT2
    assert decision.decided_by == "last_active"
    assert decision.reason == "last_active"


@pytest.mark.parametrize(("age_ms", "expected"), [(120_000.0, "last_active"), (120_001.0, "stable")])
def test_last_active_window_boundary(age_ms: float, expected: str) -> None:
    """§4.2.4: activity inside last_active_window_s counts; one ms past is stale."""
    arb = make_arbiter()
    decision_time = T0 + 400.0
    arb.touch(LX06, decision_time - age_ms)
    arb.touch(DOT2, decision_time - 150_000.0)  # always outside the 120 s window
    assert arb.candidate(cand(LX06, wake_id="w-lx06"), T0) is None
    assert arb.candidate(cand(DOT2, wake_id="w-dot2"), T0 + 10.0) is None

    decision = arb.resolve(decision_time)
    assert decision is not None
    assert decision.decided_by == expected


# --------------------------------------------------- state changes / late callers
def test_turn_opened_during_window_drops_the_batch() -> None:
    """A batch collected while idle must not admit once a turn opened (K2)."""
    arb = make_arbiter()
    candidate = cand(LX06)
    assert arb.candidate(candidate, T0) is None
    arb.set_state(turn_open=True)

    decision = resolve_at_window(arb)
    assert decision is not None
    assert not decision.admitted
    assert decision.reason == "turn_open"
    assert decision.rejected == [(candidate, "turn_open")]


def test_playback_started_during_window_drops_the_batch() -> None:
    """Same for playback: re-apply §12.3.2/§4.5 gates to the collected batch."""
    arb = make_arbiter(rooms={LX06: LIVING, DOT2: LIVING})
    dot_candidate = cand(DOT2, wake_id="w-dot2")
    lx_candidate = cand(LX06, wake_id="w-lx06")
    assert arb.candidate(dot_candidate, T0) is None
    assert arb.candidate(lx_candidate, T0 + 10.0) is None
    arb.set_state(owner_device=LX06, playing={LX06})

    decision = resolve_at_window(arb)
    assert decision is not None
    assert not decision.admitted
    assert (dot_candidate, "cross_echo") in decision.rejected  # same room as playing lx06
    assert (lx_candidate, "lost_playing") in decision.rejected  # the playing device itself
    assert decision.reason == "cross_echo"


def test_barge_overtakes_a_pending_idle_batch() -> None:
    """The fast path wins the turn; the idle batch can then only lose (§12.3.2)."""
    arb = make_arbiter()
    idle = cand(DOT2, wake_id="idle")
    assert arb.candidate(idle, T0) is None
    arb.set_state(owner_device=LX06, playing={LX06})

    barge = cand(LX06, wake_id="barge", barge=True, playing=True)
    decision = arb.candidate(barge, T0 + 100.0)
    assert decision is not None
    assert decision.admitted
    assert decision.winner is barge
    assert decision.candidates == 2
    assert (idle, "lost_playing") in decision.rejected
    assert arb.pending_count == 0
    assert arb.resolve(T0 + 1_000.0) is None


def test_queued_decisions_are_handed_out_in_order() -> None:
    """Two decisions in one call: the expired batch first, the new reject second."""
    arb = make_arbiter()
    first = cand(LX06, wake_id="w1")
    assert arb.candidate(first, T0) is None
    arb.set_state(turn_open=True)

    late = arb.candidate(cand(DOT2, wake_id="w2"), T0 + 500.0)
    assert late is not None
    assert late.reason == "turn_open"
    assert late.rejected[0][0] is first  # the expired batch is never dropped

    queued = arb.resolve(T0 + 500.0)
    assert queued is not None
    assert queued.reason == "turn_open"
    assert queued.rejected[0][0].wake_id == "w2"
    assert arb.resolve(T0 + 500.0) is None


def test_late_candidate_flushes_expired_batch_and_starts_a_new_one() -> None:
    """A caller that misses the window still gets its decision, never a lost candidate."""
    arb = make_arbiter()
    first = cand(LX06, wake_id="w1")
    assert arb.candidate(first, T0) is None

    second = cand(DOT2, wake_id="w2")
    late = arb.candidate(second, T0 + 500.0)  # first batch's window already elapsed
    assert late is not None
    assert late.admitted
    assert late.winner is first
    assert late.waited_ms == 500.0
    assert arb.pending_count == 1  # second now waits for its own window
    assert arb.resolve(T0 + 899.0) is None
    resolved = arb.resolve(T0 + 900.0)
    assert resolved is not None and resolved.winner is second


# ------------------------------------------------------ trace fields / API edges
def test_wake_sources_and_reason_vocabulary() -> None:
    """§12.5 trace fields and the frozen §12.2 reason vocabulary."""
    arb = make_arbiter()
    oww = cand(LX06, wake_id="oww", source="device")
    kws = cand(DOT2, wake_id="kws", source="rui", score=1.0, threshold=0.9)
    assert arb.candidate(oww, T0) is None
    assert arb.candidate(kws, T0 + 10.0) is None

    decision = arb.resolve(T0 + 400.0)
    assert decision is not None
    assert decision.wake_sources == {"oww": "device_wake", "kws": "kws"}
    assert decision.reason in ADMIT_REASONS
    for _, reason in decision.rejected:
        assert reason in REJECT_REASONS

    assert REJECT_REASONS == (
        "turn_open",
        "lost_same_word",
        "cross_echo",
        "lost_playing",
        "duplicate",
        "over_capacity",
        "admit_timeout",
    )
    assert set(ADMIT_REASONS) == {"barge", "score", "last_active", "tie"}


def test_candidate_rejects_unknown_source() -> None:
    """The source literal is a contract (`rui`|`device`), not free text."""
    with pytest.raises(ValueError, match="unknown wake source"):
        cand(LX06, source="microphone")


def test_set_state_touch_and_reset() -> None:
    """The control plane's state snapshot is mutable, explicit, and resettable."""
    arb = make_arbiter()
    arb.set_state(
        turn_open=True,
        owner_device=LX06,
        playing={LX06},
        last_active_ms={LX06: T0 - 1.0},
    )
    assert arb.state.turn_open is True
    assert arb.state.owner_device == LX06
    assert arb.state.playing == {LX06}
    assert arb.state.last_active_ms[LX06] == T0 - 1.0

    arb.touch(DOT2, T0)
    assert arb.state.last_active_ms[DOT2] == T0

    arb.set_state(owner_device=None)  # None is a real value: release the claim
    assert arb.state.owner_device is None

    assert arb.candidate(cand(DOT2), T0) is not None  # turn_open still gates
    assert arb.pending_count == 0

    arb.reset()
    assert arb.state == ArbiterState()
    assert arb.pending_count == 0
