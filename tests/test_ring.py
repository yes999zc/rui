from __future__ import annotations

from rui.audio.ring import ArrivalStats, PcmRing


def test_ring_drops_instead_of_growing() -> None:
    ring = PcmRing(capacity_ms=100, frame_ms=20)  # 5 slots
    accepted = [ring.push(i, 0.0, b"x") for i in range(7)]
    assert accepted == [True] * 5 + [False, False]
    assert len(ring) == 5


def test_ring_is_fifo() -> None:
    ring = PcmRing(capacity_ms=100, frame_ms=20)
    for i in range(3):
        ring.push(i, float(i), str(i).encode())
    popped = [ring.pop() for _ in range(3)]
    assert [p[0] for p in popped if p is not None] == [0, 1, 2]
    assert ring.pop() is None


def test_arrival_stats_report_both_percentiles() -> None:
    stats = ArrivalStats(window=100)
    for _ in range(95):
        stats.add(20.0)
    for _ in range(4):
        stats.add(40.0)
    stats.add(90.0)
    assert stats.p50() == 20.0
    assert 39.0 <= stats.p99() <= 90.0
    assert stats.mean() > stats.p50()


def test_arrival_stats_ignore_negative_intervals_and_empty() -> None:
    stats = ArrivalStats()
    assert stats.p50() == 0.0 and stats.p99() == 0.0
    stats.add(-5.0)
    assert len(stats) == 0
