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


def test_another_room_cannot_barge_while_someone_plays() -> None:
    coordinator, sent = make_coordinator()
    coordinator.set_owner("living")

    async def scenario():
        await coordinator.on_candidate("study", candidate(
            "w2", barge=True, playing=True))
        await coordinator.close()

    run(scenario())
    assert sent["study"][0].__class__.__name__ == "WakeReject"
    assert sent["study"][0].reason == "lost_playing"
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
