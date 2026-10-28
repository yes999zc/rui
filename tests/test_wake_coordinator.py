"""Phase 2 coordinator: candidates in, wake_admit/wake_reject out to the right plane.

The arbiter owns the decision; this glue owns the mapping. These tests pin the failure
modes that cost the most in a real house: a verdict sent to the wrong room, a batch that
never resolves, and a dead plane taking the whole merge window down.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rui.audio.arbiter import ArbiterDevice, WakeArbiter
from rui.audio.coordinator import WakeCoordinator
from rui.config.schema import ArbitrationConfig, MultiDeviceConfig

WINDOW_MS = 100


def candidate(wake_id: str, *, source: str = "device", keyword: str = "",
              score: float | None = None, threshold: float | None = None,
              barge: bool = False, playing: bool = False) -> SimpleNamespace:
    return SimpleNamespace(wake_id=wake_id, source=source, ts_ms=1.0, keyword=keyword,
                           score=score, threshold=threshold, barge=barge,
                           playing=playing)


def make_coordinator(*, senders: dict | None = None,
                     devices: tuple[tuple[str, str], ...] = (("living", "客厅"),
                                                             ("study", "书房")),
                     window_ms: int = WINDOW_MS) -> tuple[WakeCoordinator, dict]:
    cfg = MultiDeviceConfig(
        arbitration=ArbitrationConfig(candidate_merge_window_ms=window_ms))
    arbiter = WakeArbiter(cfg, [ArbiterDevice(device_id=room_id, room=room, index=index)
                                for index, (room_id, room) in enumerate(devices)])
    sent: dict[str, list] = {room_id: [] for room_id, _ in devices}

    def sender_for(room_id: str):
        async def send(message: object) -> None:
            sent[room_id].append(message)
        return send

    actual = senders if senders is not None else {
        room_id: sender_for(room_id) for room_id, _ in devices
    }
    coordinator = WakeCoordinator(
        arbiter, actual, rooms={room_id: room for room_id, room in devices})
    return coordinator, sent


def run(coro):
    return asyncio.run(coro)


def test_a_single_candidate_is_admitted_after_the_merge_window() -> None:
    coordinator, sent = make_coordinator()

    async def scenario():
        await coordinator.on_candidate("living", candidate("w1"))
        assert sent["living"] == []          # collected, not decided yet
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    assert len(sent["living"]) == 1
    admit = sent["living"][0]
    assert admit.__class__.__name__ == "WakeAdmit"
    assert admit.wake_id == "w1"
    assert coordinator.counters["wake_admitted"] == 1


def test_a_double_hit_produces_exactly_one_turn() -> None:
    """The whole point of Phase 2: two rooms heard the same word, one turn opens."""
    coordinator, sent = make_coordinator()

    async def scenario():
        await coordinator.on_candidate("living", candidate(
            "w-dev", source="device", score=0.9, threshold=0.5))
        await coordinator.on_candidate("study", candidate(
            "w-kws", source="rui", keyword="你好小瑞"))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    admits = sent["living"] + sent["study"]
    admits = [m for m in admits if m.__class__.__name__ == "WakeAdmit"]
    rejects = [m for m in sent["living"] + sent["study"]
               if m.__class__.__name__ == "WakeReject"]
    assert len(admits) == 1
    assert len(rejects) == 1
    # device_wake is the default source priority (§9.1 K3).
    assert admits[0].wake_id == "w-dev"
    assert rejects[0].reason == "lost_same_word"


def test_the_owner_barge_is_admitted_immediately() -> None:
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")

    async def scenario():
        await coordinator.on_candidate("living", candidate(
            "w1", barge=True, playing=True))
        await coordinator.close()

    run(scenario())
    assert len(sent["living"]) == 1
    assert sent["living"][0].__class__.__name__ == "WakeAdmit"


def test_another_room_can_wake_while_a_different_room_plays() -> None:
    """P1 (task-18): one room playing must not starve the other rooms' wakes."""
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")
    coordinator.set_playing({"living"})

    async def scenario():
        await coordinator.on_candidate("study", candidate(
            "w2", barge=True, playing=True))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].__class__.__name__ == "WakeAdmit"
    assert sent["living"] == []


def test_an_open_turn_rejects_new_candidates() -> None:
    coordinator, sent = make_coordinator()
    coordinator.set_turn_open(True)

    async def scenario():
        await coordinator.on_candidate("study", candidate("w3"))
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].reason == "turn_open"


def test_a_sender_that_raises_is_counted_not_crashing() -> None:
    async def broken_sender(message: object) -> None:
        raise RuntimeError("plane gone")

    coordinator, sent = make_coordinator(senders={"living": broken_sender, "study": broken_sender})
    coordinator.set_turn_open(True)

    async def scenario():
        await coordinator.on_candidate("living", candidate("w4"))
        await coordinator.close()

    run(scenario())
    assert coordinator.counters["wake_send_failed"] == 1


def test_a_missing_plane_is_counted() -> None:
    coordinator, sent = make_coordinator(senders={"living": None})
    coordinator.drop_device("study")
    coordinator.set_turn_open(True)

    async def scenario():
        await coordinator.on_candidate("study", candidate("w5"))
        await coordinator.close()

    run(scenario())
    assert coordinator.counters["wake_send_failed"] == 1
    assert sent["study"] == []


def test_metrics_expose_the_last_decision() -> None:
    coordinator, _sent = make_coordinator()

    async def scenario():
        await coordinator.on_candidate("living", candidate("w6"))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    metrics = coordinator.metrics()
    assert metrics["arbitration_winner"] == "living"
    assert metrics["arbitration_candidates"] == 1
    assert metrics["wake_candidates"] == 1
    assert metrics["wake_admit_latency_ms"] >= WINDOW_MS


def test_close_cancels_a_pending_window() -> None:
    coordinator, sent = make_coordinator()

    async def scenario():
        await coordinator.on_candidate("living", candidate("w7"))
        await coordinator.close()
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)

    run(scenario())
    assert sent["living"] == []
    assert coordinator.counters["wake_admitted"] == 0


# ------------------------------------------------- turn latch (task-10 watchdog)
def test_dropping_the_turn_room_releases_the_latch() -> None:
    """2026-10-26: a plane that dies mid-turn can never send the closing event.

    `drop_device` used to forget only the sender/room, so `turn_open` (and the output
    claim) survived the device and every later wake was rejected until a restart.
    """
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")
    coordinator.set_turn_open(True, "living")

    coordinator.drop_device("living")

    assert coordinator.arbiter.state.turn_open is False
    assert coordinator.arbiter.state.owner_device is None
    assert coordinator.arbiter.state.playing == set()
    assert coordinator.turn_device is None

    async def scenario():
        await coordinator.on_candidate("study", candidate("w-after-drop"))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].__class__.__name__ == "WakeAdmit"


def test_dropping_another_room_keeps_the_open_turn() -> None:
    """Only the device that owns the turn may release it; another room's turn lives."""
    coordinator, _sent = make_coordinator()
    coordinator.set_turn_open(True, "living")

    coordinator.drop_device("study")

    assert coordinator.arbiter.state.turn_open is True
    assert coordinator.turn_device == "living"


def test_turn_age_starts_at_the_false_to_true_edge() -> None:
    """on_wake and on_utterance both open the same turn; neither may refresh the age."""
    now = {"t": 100.0}
    cfg = MultiDeviceConfig(
        arbitration=ArbitrationConfig(candidate_merge_window_ms=WINDOW_MS))
    arbiter = WakeArbiter(cfg, [ArbiterDevice(device_id="living", room="客厅", index=0)])
    coordinator = WakeCoordinator(arbiter, {}, clock=lambda: now["t"])

    coordinator.set_turn_open(True, "living")
    now["t"] += 5.0
    assert coordinator.turn_open_for_s() == 5.0
    coordinator.set_turn_open(True, "living")      # same turn
    now["t"] += 5.0
    assert coordinator.turn_open_for_s() == 10.0
    coordinator.set_turn_open(False)
    assert coordinator.turn_open_for_s() is None


# ------------------------------------------------- per-room isolation (task-18)
def test_a_wake_in_another_room_is_admitted_while_one_room_plays() -> None:
    """P1: living playing must not reject dot2's wake (4-device DoD)."""
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")
    coordinator.set_playing({"living"})

    async def scenario():
        await coordinator.on_candidate("study", candidate("w-other-room"))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].__class__.__name__ == "WakeAdmit"


def test_its_own_room_still_rejects_a_wake_while_playing() -> None:
    """Regression: the playing room's own second wake stays lost_playing."""
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")
    coordinator.set_playing({"living"})

    async def scenario():
        await coordinator.on_candidate("living", candidate("w-same-room"))
        await coordinator.close()

    run(scenario())
    assert sent["living"][0].__class__.__name__ == "WakeReject"
    assert sent["living"][0].reason == "lost_playing"


def test_an_open_turn_does_not_reject_a_wake_in_another_room() -> None:
    """turn_open is one-turn-per-room, not a house-wide latch (K2 stays per room)."""
    coordinator, sent = make_coordinator()
    coordinator.set_turn_open(True, "living")

    async def scenario():
        await coordinator.on_candidate("study", candidate("w-other-turn"))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].__class__.__name__ == "WakeAdmit"


def test_the_open_turn_still_rejects_its_own_room() -> None:
    coordinator, sent = make_coordinator()
    coordinator.set_turn_open(True, "living")

    async def scenario():
        await coordinator.on_candidate("living", candidate("w-same-turn"))
        await coordinator.close()

    run(scenario())
    assert sent["living"][0].__class__.__name__ == "WakeReject"
    assert sent["living"][0].reason == "turn_open"


def test_two_planes_in_the_same_room_still_exclude_each_other() -> None:
    """Future 4-device case: two planes in one room share the room's playing state."""
    coordinator, sent = make_coordinator(
        devices=(("lx1", "客厅"), ("lx2", "客厅")))
    coordinator.set_playing({"lx1"})

    async def scenario():
        await coordinator.on_candidate("lx2", candidate("w-same-room-2"))
        await coordinator.close()

    run(scenario())
    assert sent["lx2"][0].__class__.__name__ == "WakeReject"
    assert sent["lx2"][0].reason == "cross_echo"


# ------------------------------------- P0-4: cross-echo count + worst-case coverage
def test_cross_echo_is_rejected_counted_and_named(caplog) -> None:
    """a. Same room, two planes: A playing -> B's wake is cross_echo and counted.

    The count is what the 4-device acceptance reads; the log must name who was
    playing and who was suppressed, or a real 双声 report is impossible to diagnose.
    """
    import logging

    coordinator, sent = make_coordinator(devices=(("lx1", "客厅"), ("lx2", "客厅")))
    coordinator.set_playing({"lx1"})

    async def scenario():
        with caplog.at_level(logging.INFO, logger="rui.audio.coordinator"):
            await coordinator.on_candidate("lx2", candidate("w-echo", keyword="你好小瑞"))
        await coordinator.close()

    run(scenario())
    reject = sent["lx2"][0]
    assert reject.__class__.__name__ == "WakeReject"
    assert reject.reason == "cross_echo"
    metrics = coordinator.metrics()
    assert metrics["arbitration_cross_echo_suppressed"] == 1
    assert metrics["wake_rejected"] == 1
    text = caplog.text
    assert "playing_device=lx1" in text
    assert "suppressed_device=lx2" in text
    assert "room=客厅" in text


def test_same_batch_same_word_is_one_admit_and_one_lost_same_word() -> None:
    """b. Same room, same word in one batch: one turn, the loser is lost_same_word."""
    coordinator, sent = make_coordinator(devices=(("lx1", "客厅"), ("lx2", "客厅")))

    async def scenario():
        await coordinator.on_candidate("lx1", candidate(
            "w-dev", source="device", score=0.9, threshold=0.5, keyword="你好小瑞"))
        await coordinator.on_candidate("lx2", candidate(
            "w-kws", source="rui", keyword="你好小瑞"))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    messages = sent["lx1"] + sent["lx2"]
    admits = [m for m in messages if m.__class__.__name__ == "WakeAdmit"]
    rejects = [m for m in messages if m.__class__.__name__ == "WakeReject"]
    assert len(admits) == 1
    assert len(rejects) == 1
    assert rejects[0].reason == "lost_same_word"
    assert coordinator.counters["wake_admitted"] == 1
    assert coordinator.metrics()["arbitration_cross_echo_suppressed"] == 0


def test_cross_room_same_word_is_admitted_never_barges_and_keeps_playing() -> None:
    """c. Different rooms: A playing, B wakes -> B is admitted without stealing A.

    Per-room isolation (task-18): the same word in another room must not be
    suppressed as cross_echo and must not barge the playing room.
    """
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")

    async def scenario():
        await coordinator.on_candidate("study", candidate(
            "w-study", keyword="你好小瑞", barge=True, playing=True))
        await asyncio.sleep(WINDOW_MS / 1000 + 0.08)
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].__class__.__name__ == "WakeAdmit"
    assert coordinator.last_decision is not None
    assert coordinator.last_decision.decided_by != "barge"
    assert coordinator.arbiter.state.playing == {"living"}
    assert coordinator.arbiter.state.owner_device == "living"
    assert coordinator.metrics()["arbitration_cross_echo_suppressed"] == 0


def test_another_plane_in_the_turn_room_is_rejected_turn_open() -> None:
    """e. Same room, *another* plane: the open turn rejects it with turn_open.

    The owner (lx1) and the candidate (lx2) differ, so this covers the room-equality
    branch of `_in_open_turn` (arbiter.py:546-548), not only the same-device shortcut.
    """
    coordinator, sent = make_coordinator(devices=(("lx1", "客厅"), ("lx2", "客厅")))

    async def scenario():
        coordinator.set_turn_open(True, "lx1")
        await coordinator.on_candidate("lx2", candidate("w-same-room-turn"))
        await coordinator.close()

    run(scenario())
    reject = sent["lx2"][0]
    assert reject.__class__.__name__ == "WakeReject"
    assert reject.reason == "turn_open"
    assert coordinator.metrics()["arbitration_cross_echo_suppressed"] == 0
