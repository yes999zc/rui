"""task-30: a short reply must reach TTS once, and the seam must be measurable.

The bug report was an audible gap at the comma of 「在，你说。」 on dot2-b with
`playback_gap_count=0`. The underrun counter cannot see it: when the control plane makes
two TTS calls, both renditions' own leading/trailing silence is already inside the PCM,
so the device never runs dry. These tests pin the two halves of the answer:

* the split policy (one vendor request for an ultra-short reply, even when the model
  pauses past the old 300 ms hold cap); and
* the segment ledger (per-reply segment count, per-segment duration, the audible seam
  between segments, and the longest silence *inside* one segment), on the wire and in
  the turn trace.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

import numpy as np

from rui.audio.process import AudioPlane
from rui.audio.protocol import HEADER, Metrics, encode_json, parse_control
from rui.config import Config
from rui.providers.base import ChatChunk, Transcript
from rui.session.turn import TurnRunner
from rui.trace import TurnTrace

RATE = 16000


class FakeAsr:
    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text="你好")


class SlowLlm:
    """One delta per entry, sleeping before each: models a pause mid-reply."""

    def __init__(self, chunks: list[str], delays: list[float]) -> None:
        self.chunks = chunks
        self.delays = delays

    async def chat(self, messages: list[dict], tools: list[dict]):
        for chunk, delay in zip(self.chunks, self.delays, strict=False):
            if delay:
                await asyncio.sleep(delay)
            yield ChatChunk(delta=chunk)


class PaddedTts:
    """A vendor-shaped fake: every request carries its own head/tail silence.

    The silence is the point. A real vendor pads each rendition; two requests therefore
    leave ~160 ms of zero PCM at the seam even though the audio plane never underruns.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield _silence(80)
        yield _tone(120)
        yield _silence(80)


def _silence(ms: int) -> bytes:
    return np.zeros(RATE * ms // 1000, dtype="<i2").tobytes()


def _tone(ms: int) -> bytes:
    return np.full(RATE * ms // 1000, 1200, dtype="<i2").tobytes()


def _run_reply(monkeypatch, chunks: list[str], delays: list[float]):
    import rui.session.turn as turn

    tts = PaddedTts()
    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("a", FakeAsr())])
    monkeypatch.setattr(
        turn, "llm_chain", lambda providers: [("l", SlowLlm(chunks, delays))]
    )
    monkeypatch.setattr(turn, "tts_chain", lambda providers, rate: [("t", tts)])

    async def speak(_pcm: bytes, *, segment_start: bool = False) -> None:
        return None

    trace = TurnTrace(device_id="test")
    result = asyncio.run(
        TurnRunner(Config().providers).run(b"\x00\x00" * 800, speak, trace)
    )
    return tts, result, trace


# ---------------------------------------------------------- split policy (red-first)
def test_a_slow_short_newline_reply_is_one_tts_call(monkeypatch) -> None:
    """300 ms hold + a slower model used to release by SPLITTING (d.10 residual risk).

    `在，\\n你说。` is 5 spoken characters. Two requests each carry 80 ms of head and
    tail silence, i.e. a 160 ms audible seam - the reported comma gap, with the underrun
    counter at zero. After task-30 the release merges instead of splitting.
    """
    tts, result, trace = _run_reply(monkeypatch, ["在，\n", "你说。"], [0.0, 0.35])

    assert len(tts.texts) == 1, tts.texts
    assert "你说" in tts.texts[0]
    assert result.reply == "在，\n你说。"
    assert trace.meta["tts_call_count"] == 1
    assert trace.meta["tts_inter_call_silence_ms"] == []


def test_a_five_char_comma_reply_is_one_tts_call(monkeypatch) -> None:
    """The literal report: `在，你说。` is one sentence and must stay one request."""
    tts, _result, trace = _run_reply(monkeypatch, ["在，你说。"], [0.0])

    assert len(tts.texts) == 1, tts.texts
    assert trace.meta["tts_call_count"] == 1


def test_a_multi_piece_reply_reports_its_inter_call_gap(monkeypatch) -> None:
    """A genuinely longer reply still streams, and the ledger names the seams.

    The trace must expose the count and one gap per junction, or the next report has to
    guess again (the whole reason task-30 exists).
    """
    tts, _result, trace = _run_reply(
        monkeypatch,
        ["今天北京晴，气温二十度。", "明天多云转晴。", "后天有小雨。"],
        [0.0, 0.05, 0.05],
    )

    assert len(tts.texts) >= 2, tts.texts
    count = trace.meta["tts_call_count"]
    assert count == len(tts.texts)
    assert len(trace.meta["tts_call_audio_ms"]) == count
    assert len(trace.meta["tts_inter_call_silence_ms"]) == count - 1
    assert all(gap >= 0.0 for gap in trace.meta["tts_inter_call_silence_ms"])


# ---------------------------------------------------------- plane segment ledger
class _RecordingAdapter:
    def __init__(self) -> None:
        self.pcm: list[bytes] = []

    async def play(self, pcm: bytes, sample_rate: int = 48000, channels: int = 1) -> None:
        self.pcm.append(pcm)

    async def stop_playback(self, fade_ms: int = 120) -> None:
        return None

    def health(self) -> dict:
        return {"connected": True}


def _plane(tmp_path: Path) -> AudioPlane:
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.audio.playback.prebuffer_ms = 0
    cfg.system.data_dir = str(tmp_path)
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    plane.adapter = _RecordingAdapter()  # type: ignore[assignment]
    return plane


def _enqueue(plane: AudioPlane, chunk: bytes, *, segment: bool) -> None:
    plane._speak_queued_s += len(chunk) / 2 / plane.cfg.audio.voice.rate
    plane._speak_finished = False
    plane._speak_queue.put_nowait((chunk, segment))


def test_plane_metrics_expose_segment_count_duration_and_seam(tmp_path: Path) -> None:
    """Two padded renditions: the seam is 80 ms tail + 80 ms head, gap_count stays 0.

    This is the instrument the trace needs. `playback_gap_count` is asserted to stay at
    zero on purpose: the audio is contiguous PCM, so no existing counter fires.
    """
    plane = _plane(tmp_path)

    async def scenario() -> Metrics:
        task = asyncio.create_task(plane._speak_loop())
        try:
            for index in range(2):
                for part, block in enumerate((_silence(80), _tone(120), _silence(80))):
                    _enqueue(plane, block, segment=(part == 0))
                if index == 0:
                    await asyncio.sleep(0.05)
            plane._speak_finished = True
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if len(plane.adapter.pcm) >= 6:  # type: ignore[union-attr]
                    break
                await asyncio.sleep(0.01)
            return plane.metrics()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    metrics = asyncio.run(scenario())

    assert metrics.playback_gap_count == 0, "the seam is PCM silence, not an underrun"
    assert metrics.reply_segment_count == 2
    assert metrics.reply_segment_audio_ms == [280.0, 280.0]
    assert len(metrics.reply_segment_silences_ms) == 1
    assert 140.0 <= metrics.reply_segment_silences_ms[0] <= 200.0, (
        metrics.reply_segment_silences_ms
    )
    # No silence inside either rendition: the padded edges are not counted twice.
    assert metrics.reply_segment_inner_silence_ms == [0.0, 0.0]


def test_reply_log_carries_segment_count_and_silences(
    tmp_path: Path, caplog,
) -> None:
    """The drain-time `reply:` line is where a live incident is read first."""
    plane = _plane(tmp_path)

    async def scenario() -> None:
        task = asyncio.create_task(plane._speak_loop())
        try:
            for _index in range(2):
                for part, block in enumerate((_silence(80), _tone(120), _silence(80))):
                    _enqueue(plane, block, segment=(part == 0))
            plane._speak_finished = True
            # The loop logs the reply only after its 0.5 s queue timeout proves the
            # producer is done; wait past it rather than inspecting internals.
            await asyncio.sleep(1.5)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.INFO, logger="rui.audio.process"):
        asyncio.run(scenario())

    lines = [record.getMessage() for record in caplog.records
             if record.getMessage().startswith("reply: ")
             and "chunks," in record.getMessage()]
    assert lines, "the reply line was not logged"
    line = lines[0]
    assert "2 segments" in line, line
    assert "silences_ms=[160.0]" in line, line
    assert "inner_ms=[0, 0]" in line, line


def test_a_comma_pause_inside_one_segment_is_visible_as_inner_silence(tmp_path: Path) -> None:
    """The one-request case still shows the vendor's comma pause, with no seam list.

    If a 5-char reply genuinely reaches TTS once, this is what proves the remaining
    audible pause is inside the rendition, not between two of them.
    """
    plane = _plane(tmp_path)

    async def scenario() -> Metrics:
        task = asyncio.create_task(plane._speak_loop())
        try:
            for part, block in enumerate((_tone(100), _silence(120), _tone(100))):
                _enqueue(plane, block, segment=(part == 0))
            plane._speak_finished = True
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if len(plane.adapter.pcm) >= 3:  # type: ignore[union-attr]
                    break
                await asyncio.sleep(0.01)
            return plane.metrics()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    metrics = asyncio.run(scenario())

    assert metrics.reply_segment_count == 1
    assert metrics.reply_segment_silences_ms == []
    assert metrics.reply_segment_inner_silence_ms[0] >= 100.0


# ---------------------------------------------------------- wire + trace plumbing
def _metrics(**kwargs: object) -> Metrics:
    base = dict(
        uptime_s=1.0, frames_in=0, frames_out=0, frames_dropped=0, no_consumer=0,
        jitter_p50_ms=0.0, jitter_p99_ms=0.0, cpu_s=0.0, audio_s=0.0, rtf=0.0,
    )
    return Metrics(**base, **kwargs)  # type: ignore[arg-type]


def test_segment_ledger_survives_the_plane_wire() -> None:
    msg = _metrics(
        reply_segment_count=2,
        reply_segment_audio_ms=[280.0, 280.0],
        reply_segment_silences_ms=[160.0],
        reply_segment_inner_silence_ms=[0.0, 0.0],
    )
    parsed = parse_control(encode_json(msg)[HEADER.size:])

    assert isinstance(parsed, Metrics)
    assert parsed.reply_segment_count == 2
    assert parsed.reply_segment_silences_ms == [160.0]
    assert parsed.reply_segment_inner_silence_ms == [0.0, 0.0]


def test_trace_copies_segment_count_and_silence() -> None:
    trace = TurnTrace(device_id="living")
    trace.note_playback_metrics(
        _metrics(
            reply_segment_count=2,
            reply_segment_audio_ms=[280.0, 280.0],
            reply_segment_silences_ms=[163.5],
            reply_segment_inner_silence_ms=[0.0, 110.0],
        )
    )

    assert trace.meta["reply_segment_count"] == 2
    assert trace.meta["reply_segment_audio_ms"] == [280.0, 280.0]
    assert trace.meta["reply_segment_silences_ms"] == [163.5]
    assert trace.meta["reply_segment_inner_silence_ms"] == [0.0, 110.0]


def test_an_older_plane_without_the_fields_leaves_the_trace_unchanged() -> None:
    trace = TurnTrace(device_id="living")
    trace.note_playback_metrics(_metrics(playback_gap_count=0, playback_max_gap_ms=0.0))

    assert "reply_segment_count" not in trace.meta
    assert "reply_segment_silences_ms" not in trace.meta
