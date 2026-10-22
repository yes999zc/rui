"""Reply pacing: the speaker's buffer must never be the thing that runs dry.

The old loop sent a block and then slept exactly its duration, so the buffer was empty
at every send - any scheduler hiccup came out of the speaker as a stutter. The
replacement prebuffers first, then writes a hair ahead of the device clock, with a cap.
"""
from __future__ import annotations

import pytest

from house_ear.audio.process import CATCHUP, MAX_LEAD_S, PREBUFFER_S, Pacer

CHUNK = 0.1  # PLAY_CHUNK_MS / 1000


def simulate(blocks: int) -> list[float]:
    """Buffer depth (s) right after each send, using the loop's own wait rule."""
    pacer = Pacer()
    now = 0.0
    depths = []
    for _ in range(blocks):
        wait = pacer.sleep_for(now, CHUNK)
        depths.append(pacer.lead)
        now += wait
    return depths


def test_one_block_alone_still_leaves_a_block_in_hand() -> None:
    assert simulate(1)[0] == pytest.approx(CHUNK)


def test_the_buffer_is_filled_to_the_prebuffer_within_a_few_blocks() -> None:
    depths = simulate(4)
    assert depths[-1] >= PREBUFFER_S


def test_the_buffer_never_drains_again() -> None:
    steady = simulate(400)[3:]
    assert min(steady) >= PREBUFFER_S


def test_the_lead_is_capped_so_a_barge_is_heard_promptly() -> None:
    # The cap is on the target; one block in flight can sit above it.
    assert max(simulate(2000)) <= MAX_LEAD_S + CHUNK + 1e-9


def test_pacing_does_not_run_ahead_of_the_audio_it_has_sent() -> None:
    # Wall clock can only be shorter than the audio duration by what is sitting in the
    # buffer. Anything faster would mean we are bursting audio at the speaker.
    pacer = Pacer()
    now = 0.0
    blocks = 400
    for _ in range(blocks):
        now += pacer.sleep_for(now, CHUNK)
    assert now >= blocks * CHUNK - MAX_LEAD_S - CHUNK


def test_a_stall_does_not_throw_the_schedule_into_the_future() -> None:
    # After a 5 s freeze (GC, VM pause) the deadline restarts from now: the stale one
    # would make us burst a second of audio to "catch up".
    pacer = Pacer()
    pacer.due = 10.0
    pacer.sleep_for(50.0, CHUNK)
    assert pacer.lead == pytest.approx(CHUNK)


def test_catchup_is_a_small_margin_not_a_speed_change() -> None:
    assert 1.0 < CATCHUP < 1.05
