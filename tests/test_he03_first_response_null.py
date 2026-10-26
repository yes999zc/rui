"""HE-03: ``first_response_ms`` must stay "evidence insufficient", never 0.

The 0.7.1 sample behind ``docs/analysis/dot2-vs-xiaoai.md`` had mostly-null
``first_response_ms`` fields (old plane / no ``play_start`` stage), and the tool
correctly printed 「证据不足」 instead of inventing 0 ms. These tests replay a
fixed copy of that shape (``tests/fixtures/he03-first-response-null.log.txt``)
and pin two things:

1. the analysis tool (``scripts/fleet_matrix.py``) counts only rows that carry
   both ``wake`` and ``play_start``; a null row is *skipped*, not folded in as
   zero, and a device with no measured turn stays evidence-insufficient;
2. production reporting is already correct: ``TurnRunner`` marks ``play_start``
   on the first answer chunk (the app marks ``wake`` before the run), while the
   early filler is deliberately not a response mark.

No production change was needed for HE-03; the tests are the deliverable.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from test_fleet_matrix import fm
from test_turn import FakeAsr, FakeLlm, FakeTts, patch_chains

from rui.config.schema import Config
from rui.session.turn import TurnRunner
from rui.trace import TurnTrace

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "he03-first-response-null.log.txt"


def _report() -> dict:
    return fm.build_report(fm.parse_log(FIXTURE), fm.MarksScan())


def test_null_first_response_is_skipped_not_counted_as_zero() -> None:
    """Three null turns + one measured turn: n stays 1, median stays the real value."""
    block = _report()["devices"]["lx06"]["first_response_ms"]

    assert block["n"] == 1
    assert block["median"] == 2610.4
    assert block["p90"] == 2610.4
    assert block["skipped"] == 3
    # The trap this test guards: four rows exist, so a zero-filler would give
    # n=4 and median=652.6. Neither is allowed.
    assert block["n"] != 4
    assert block["median"] != 652.6


def test_a_device_with_only_null_turns_stays_evidence_insufficient() -> None:
    """No measured turn at all: no median, no p90, and an explicit reason."""
    block = _report()["devices"]["dot2"]["first_response_ms"]

    assert block["median"] is None
    assert block["p90"] is None
    assert block["n"] == 0
    assert block["skipped"] == 2
    assert "play_start" in block["why"]


def test_rendered_matrix_says_evidence_insufficient_and_never_0_ms() -> None:
    text = fm.render_markdown(_report())

    assert "证据不足" in text
    assert "0.0 ms (n=0)" not in text
    # The one real sample must still be visible next to the empty device.
    assert "2610.4 / 2610.4 ms (n=1)" in text


def test_production_marks_play_start_from_the_first_answer_chunk(monkeypatch) -> None:
    """The app marks ``wake``; the runner must add ``play_start`` for the answer."""
    asr = FakeAsr("今天天气怎么样")
    llm = FakeLlm(["今天多云转晴，最高二十度。"])
    tts = FakeTts()
    patch_chains(monkeypatch, asr=asr, llms=[llm], ttss=[tts])

    spoken: list[bytes] = []

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        spoken.append(pcm)

    trace = TurnTrace(device_id="living")
    # src/rui/web/api/app.py:2618 marks the wake timestamp before run().
    trace.mark("wake", at_ms=trace.t0_ms)
    asyncio.run(TurnRunner(Config().providers).run(b"\x00\x00" * 1600, speak, trace))

    assert spoken
    assert trace.at("play_start") is not None
    assert trace.first_response_ms is not None
    assert trace.first_response_ms >= 0.0
    assert trace.to_dict()["first_response_ms"] == trace.first_response_ms


def test_filler_audio_is_not_a_first_response() -> None:
    """A filler is not the answer (turn.py:805); it must not make a turn look fast."""
    trace = TurnTrace(device_id="living")
    trace.mark("wake", at_ms=trace.t0_ms)
    trace.mark("filler", at_ms=trace.t0_ms + 1500)

    assert trace.first_response_ms is None
    assert trace.to_dict()["first_response_ms"] is None
