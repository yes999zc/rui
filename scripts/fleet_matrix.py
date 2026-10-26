#!/usr/bin/env python
"""fleet_matrix: a captured rui log (+ optional hand marks) -> Dot2 vs 小爱 matrix.

Everything printed here is either a number copied out of the log / the marks file,
or the explicit words 「证据不足」. Nothing is interpolated, averaged in, defaulted
to zero or filled from a neighbouring round: a cell the evidence cannot support is
printed as its own TODO, with the reason. Every printed number carries the line it
came from in the closing 「证据索引」.

Capture the log on the host that runs the container (this tool never ssh/es, never
talks to the NAS, never starts docker)::

    docker logs --since 48h rui > /tmp/rui-fleet.log 2>&1
    # compose: docker compose logs --no-color --since 2026-10-26T10:00:00 rui \\

Hand-marked rounds are optional (CSV, header row required)::

    phrase_id,device_id,round,ts,heard_ok,notes
    hello,lx06,1,2026-10-26T13:50:00,1,
    stop,dot2,1,2026-10-26T13:52:10,1,"停住了"

``phrase_id`` is one of the six scripted phrases::

    hello ①你好小瑞   time ②几点了   play ③放首歌
    next  ④下一首     stop ⑤停止播放  bye  ⑥退下吧

``heard_ok`` is the human verdict for that one round (1/0/true/false/yes/no/ok/对/
是/… ; empty = not judged, which is reported as such, never guessed). ``ts`` is the
wall-clock time the round started, used only to prove the round happened and to show
the cell's time span - it is *not* used to join log lines to a phrase (the log has no
phrase id, see the TODO section of the output).

Usage::

    python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks /tmp/fleet-marks.csv
    python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --json

Output goes to stdout only; the tool never writes into docs/ (redirect it yourself).
Exit code 0 = a report was printed, 2 = --log missing/unreadable.

Log formats are taken from the source, not guessed (file:line in this checkout):
  rui.audio.process "wake candidate <id> admitted after <n> ms"          audio/process.py:794
  rui.audio.process "wake candidate <id> rejected: <reason>"             audio/process.py:798
  rui.audio.process "wake candidate <id>: no verdict in <n> ms; dropped" audio/process.py:805
  rui.audio.process "wake candidate dropped: no control plane connected" audio/process.py:767
  rui.trace         "turn {json}"                      (device_id, first_response_ms,
                                                        stages_ms, live meta) trace.py:149
  rui.audio.process "barge: <n> wake during playback, dropped <s> s queued" process.py:1758
  rui.audio.process "barge: silence_after_ms=<n> (server-side: ...)"  process.py:1776
  rui.audio.process "reply: <n> gaps, max <n> ms"                     process.py:2337
  rui.audio.process "capture recovered after <n> failure(s)"          process.py:973
  rui.audio.process "capture: <detail>; retry <n>/<budget>"           process.py:990
  rui.audio.process "audio plane degraded: <detail>; stopping..."     process.py:1028
  rui.audio.vad     "[seg] <s> s rms=<f> peak=<f> speech=<s> s"       audio/vad.py:367
  rui.audio.vad     "utterance discarded: <n> ms of speech"           audio/vad.py:371
  rui.providers.chain "<asr|llm|tts>: <detail>"                       providers/chain.py:142
  rui.web.api.app   "turn: {json}" / "turn failed: <detail>"          web/api/app.py:2721
  rui.web.api.app   "barge-in: <s> s of queued speech dropped"        web/api/app.py:2833
  rui.web.api.app   "wake without speech (<s> s); idle goodbye ..."   web/api/app.py:2849

The prefix is what log_setup.FORMAT produces ("%(asctime)s %(levelname)s %(name)s:
%(message)s", log_setup.py:18). Lines without that prefix (a traceback continuation,
a docker warning, a truncated line) are kept: the message patterns are tried anyway
and whatever matches nothing is counted, never crashed on.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

PHRASES: tuple[tuple[str, str], ...] = (
    ("hello", "①你好小瑞"),
    ("time", "②几点了"),
    ("play", "③放首歌"),
    ("next", "④下一首"),
    ("stop", "⑤停止播放"),
    ("bye", "⑥退下吧"),
)
PHRASE_IDS = tuple(pid for pid, _label in PHRASES)
PHRASE_LABEL = {pid: label for pid, label in PHRASES}
PHRASE_INDEX = {pid: i for i, pid in enumerate(PHRASE_IDS)}

MARK_COLUMNS = ("phrase_id", "device_id", "round", "ts", "heard_ok", "notes")

# "%(asctime)s %(levelname)s %(name)s: %(message)s" - src/rui/log_setup.py:18.
LOG_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d{1,6})?)\s+"
    r"(?P<level>[A-Z]+)\s+(?P<logger>[A-Za-z0-9_.\-]+):\s?(?P<msg>.*)$"
)
# `docker logs -t` prepends an RFC3339 stamp; `docker compose logs` prepends "svc-1  | ".
DOCKER_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s+")
COMPOSE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*\s*\|\s?")
# wake_id = f"{self.device_id}-{time.monotonic_ns()}" - audio/process.py:769.
WAKE_ID = re.compile(r"^(?P<device>.+)-(?P<ns>\d+)$")
ADMITTED = re.compile(r"^wake candidate (?P<id>\S+) admitted after (?P<ms>\d+) ms$")
REJECTED = re.compile(r"^wake candidate (?P<id>\S+) rejected: (?P<reason>.+)$")
TIMEOUT = re.compile(r"^wake candidate (?P<id>\S+): no verdict in (?P<ms>\d+) ms; dropped$")
NO_PLANE = re.compile(r"^wake candidate dropped: no control plane connected$")
TRACE_TURN = re.compile(r"^turn (?P<json>\{.*\})$")
RESULT_TURN = re.compile(r"^turn: (?P<json>\{.*\})$")
TURN_FAILED = re.compile(r"^turn failed: (?P<detail>.+)$")
BARGE = re.compile(
    r"^barge: (?P<n>\d+) wake during playback, dropped (?P<dropped>[\d.]+) s queued$"
)
BARGE_STOP = re.compile(r"^barge: silence_after_ms=(?P<ms>\d+) \(")
BARGE_FAILED = re.compile(r"^barge: stop failed: (?P<detail>.+)$")
BARGE_CONTROL = re.compile(r"^barge-in: (?P<dropped>[\d.]+) s of queued speech dropped$")
SEG = re.compile(
    r"^\[seg\] (?P<dur>[\d.]+) s rms=(?P<rms>[\d.]+) peak=(?P<peak>[\d.]+) "
    r"speech=(?P<speech>[\d.]+) s$"
)
DISCARDED = re.compile(r"^utterance discarded: (?P<ms>\d+) ms of speech$")
REPLY_GAPS = re.compile(r"^reply: (?P<n>\d+) gaps, max (?P<ms>\d+) ms$")
REPLY_CHUNKS = re.compile(r"^reply: (?P<n>\d+) chunks, lead max (?P<ms>\d+) ms$")
STAGE_FAILURE = re.compile(r"^(?P<stage>asr|llm|tts): (?P<detail>.+)$")
PLANE_DEGRADED = re.compile(r"^audio plane degraded: (?P<detail>.+?); stopping.*$")
CAPTURE_RECOVERED = re.compile(r"^capture recovered after (?P<n>\d+) failure\(s\)$")
CAPTURE_RETRY = re.compile(r"^capture: (?P<detail>.*); retry (?P<streak>\d+)/(?P<budget>\d+)$")
WAKE_NO_SPEECH = re.compile(r"^wake without speech \((?P<s>[\d.]+) s\).*$")

TRUE_WORDS = {"1", "true", "yes", "y", "ok", "o", "t", "对", "是", "成功", "听到了"}
FALSE_WORDS = {"0", "false", "no", "n", "f", "x", "否", "没", "失败", "没听到"}

# Markdown only: a metric's line list is compressed into at most RANGE_LIMIT ranges.
# --json carries the full ``lines`` array of every evidence row; the one exception is
# the unparsed-line sample (UNPARSED_SAMPLE), which is capped in both because a log
# full of junk would otherwise dominate the report - the count above it is exact.
RANGE_LIMIT = 40
UNPARSED_SAMPLE = 20


# --------------------------------------------------------------------------- model
@dataclass
class WakeRec:
    lineno: int
    ts: str | None
    device: str | None
    verdict: str  # admitted | rejected | timeout | no_control_plane
    reason: str
    admit_ms: int | None


@dataclass
class TraceRec:
    lineno: int
    device: str
    first_response_ms: float | None
    silence_after_ms: float | None
    stages: dict
    meta: dict


@dataclass
class BargeRec:
    lineno: int
    counter: int
    dropped_s: float


@dataclass
class BargeStopRec:
    lineno: int
    ms: float
    paired_with: int | None = None


@dataclass
class LogScan:
    path: str = ""
    lines_total: int = 0
    blank: int = 0
    prefixed: int = 0
    unparsed: list[int] = field(default_factory=list)
    device_order: list[str] = field(default_factory=list)
    wake: list[WakeRec] = field(default_factory=list)
    traces: list[TraceRec] = field(default_factory=list)
    malformed_trace: list[int] = field(default_factory=list)
    malformed_result: list[int] = field(default_factory=list)
    barges: list[BargeRec] = field(default_factory=list)
    barge_stops: list[BargeStopRec] = field(default_factory=list)
    barge_failed: list[tuple[int, str]] = field(default_factory=list)
    barge_control: list[int] = field(default_factory=list)
    seg: list[tuple[int, float, float]] = field(default_factory=list)  # line, speech_s, dur_s
    discarded: list[tuple[int, int]] = field(default_factory=list)
    reply_gaps: list[tuple[int, int, int]] = field(default_factory=list)  # line, n, max_ms
    reply_chunks: list[int] = field(default_factory=list)
    stage_failures: list[tuple[int, str, str]] = field(default_factory=list)
    turn_failed: list[tuple[int, str]] = field(default_factory=list)
    plane_degraded: list[tuple[int, str]] = field(default_factory=list)
    capture_recovered: list[tuple[int, int]] = field(default_factory=list)
    capture_retry: list[tuple[int, int, int]] = field(default_factory=list)
    wake_no_speech: list[int] = field(default_factory=list)
    result_turns: list[tuple[int, str]] = field(default_factory=list)  # line, error
    result_transcripts: list[int] = field(default_factory=list)

    def note_device(self, device: str) -> None:
        if device not in self.device_order:
            self.device_order.append(device)

    @property
    def parsed(self) -> int:
        return (
            len(self.wake) + len(self.traces) + len(self.barges) + len(self.barge_stops)
            + len(self.seg) + len(self.discarded) + len(self.reply_gaps) + len(self.reply_chunks)
            + len(self.stage_failures) + len(self.turn_failed) + len(self.plane_degraded)
            + len(self.capture_recovered) + len(self.capture_retry) + len(self.result_turns)
            + len(self.wake_no_speech) + len(self.barge_control) + len(self.malformed_trace)
            + len(self.malformed_result)
        )


@dataclass
class MarkRow:
    lineno: int
    phrase_id: str
    device_id: str
    round: str
    ts_text: str
    ts: datetime | None
    heard_ok: bool | None
    notes: str


@dataclass
class MarksScan:
    path: str | None = None
    present: bool = False
    usable: bool = False
    why: str = ""
    header: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    rows: list[MarkRow] = field(default_factory=list)
    problems: list[tuple[int, str]] = field(default_factory=list)
    duplicates: list[tuple[int, int, str]] = field(default_factory=list)
    out_of_order: list[int] = field(default_factory=list)


# ------------------------------------------------------------------------ parsing
def _clean(text: str) -> str:
    return text.strip().strip("`'\" \t")


def parse_ts(text: str) -> datetime | None:
    """ISO-ish wall clock -> naive UTC datetime, or None. Never guesses a format."""
    raw = _clean(text).replace(",", ".")
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def heard_ok(text: str) -> bool | None:
    """Human verdict -> True/False; empty or unrecognised -> None (never a guess)."""
    token = _clean(text).lower()
    if not token:
        return None
    if token in TRUE_WORDS:
        return True
    if token in FALSE_WORDS:
        return False
    return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _json_obj(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is(logger: str | None, name: str) -> bool:
    """True when the line carries no logger (prefix-less) or belongs to `name`."""
    return logger is None or logger == name or logger.startswith(name + ".")


def parse_log(path: Path) -> LogScan:
    scan = LogScan(path=str(path))
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, start=1):
            scan.lines_total += 1
            line = raw.rstrip("\n")
            if not line.strip():
                scan.blank += 1
                continue
            body = _strip_transport_prefixes(line)
            match = LOG_PREFIX.match(body)
            if match:
                scan.prefixed += 1
                ts, logger, msg = match.group("ts"), match.group("logger"), match.group("msg")
            else:
                ts = logger = None
                msg = body.strip()
            if not _dispatch(scan, lineno, ts, logger, msg):
                scan.unparsed.append(lineno)
    _pair_barges(scan)
    return scan


def _strip_transport_prefixes(line: str) -> str:
    """Drop a `docker logs -t` stamp and/or a `docker compose logs` `name |` prefix.

    Neither is part of the app's own format (log_setup.FORMAT), but both are what a
    person actually pastes into /tmp: stripping them here means the capture command
    does not have to be exact. Two passes cover `rui-1  | <rfc3339> <app line>` and
    the reverse order; anything else is left untouched for the normal parser.
    """
    text = line
    for _ in range(2):
        stripped = DOCKER_STAMP.sub("", text, count=1)
        if stripped == text:
            stripped = COMPOSE_PREFIX.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text


def _dispatch(scan: LogScan, lineno: int, ts: str | None, logger: str | None, msg: str) -> bool:
    """Try every known line shape; True when the line was recognised."""
    if _is(logger, "rui.trace") and msg.startswith("turn {"):
        match = TRACE_TURN.match(msg)
        entry = _json_obj(match.group("json")) if match else None
        if not entry:
            scan.malformed_trace.append(lineno)
            return True
        device = entry.get("device_id")
        if not isinstance(device, str) or not device:
            scan.malformed_trace.append(lineno)
            return True
        stages = entry.get("stages_ms")
        meta = entry.get("meta")
        scan.note_device(device)
        scan.traces.append(
            TraceRec(
                lineno=lineno,
                device=device,
                first_response_ms=_float_or_none(entry.get("first_response_ms")),
                silence_after_ms=_float_or_none(entry.get("silence_after_ms")),
                stages=stages if isinstance(stages, dict) else {},
                meta=meta if isinstance(meta, dict) else {},
            )
        )
        return True

    if _is(logger, "rui.web.api.app") and msg.startswith("turn: {"):
        match = RESULT_TURN.match(msg)
        entry = _json_obj(match.group("json")) if match else None
        if entry is None:
            scan.malformed_result.append(lineno)
            return True
        error = entry.get("error")
        scan.result_turns.append((lineno, error if isinstance(error, str) else ""))
        if isinstance(entry.get("transcript"), str) and entry["transcript"]:
            scan.result_transcripts.append(lineno)
        return True

    if _is(logger, "rui.web.api.app"):
        match = TURN_FAILED.match(msg)
        if match:
            scan.turn_failed.append((lineno, match.group("detail").strip()))
            return True
        match = BARGE_CONTROL.match(msg)
        if match:
            scan.barge_control.append(lineno)
            return True
        match = WAKE_NO_SPEECH.match(msg)
        if match:
            scan.wake_no_speech.append(lineno)
            return True

    if _is(logger, "rui.audio.process"):
        match = ADMITTED.match(msg)
        if match:
            _add_wake(scan, lineno, ts, match.group("id"), "admitted", "",
                      int(match.group("ms")))
            return True
        match = REJECTED.match(msg)
        if match:
            _add_wake(scan, lineno, ts, match.group("id"), "rejected",
                      match.group("reason").strip(), None)
            return True
        match = TIMEOUT.match(msg)
        if match:
            _add_wake(scan, lineno, ts, match.group("id"), "timeout", "admit_timeout", None)
            return True
        if NO_PLANE.match(msg):
            scan.wake.append(
                WakeRec(lineno=lineno, ts=ts, device=None, verdict="no_control_plane",
                        reason="no_control_plane", admit_ms=None)
            )
            return True
        match = BARGE.match(msg)
        if match:
            scan.barges.append(
                BargeRec(lineno=lineno, counter=int(match.group("n")),
                         dropped_s=float(match.group("dropped")))
            )
            return True
        match = BARGE_STOP.match(msg)
        if match:
            scan.barge_stops.append(BargeStopRec(lineno=lineno, ms=float(match.group("ms"))))
            return True
        match = BARGE_FAILED.match(msg)
        if match:
            scan.barge_failed.append((lineno, match.group("detail").strip()))
            return True
        match = REPLY_GAPS.match(msg)
        if match:
            scan.reply_gaps.append((lineno, int(match.group("n")), int(match.group("ms"))))
            return True
        if REPLY_CHUNKS.match(msg):
            scan.reply_chunks.append(lineno)
            return True
        match = PLANE_DEGRADED.match(msg)
        if match:
            scan.plane_degraded.append((lineno, match.group("detail").strip()))
            return True
        match = CAPTURE_RECOVERED.match(msg)
        if match:
            scan.capture_recovered.append((lineno, int(match.group("n"))))
            return True
        match = CAPTURE_RETRY.match(msg)
        if match:
            scan.capture_retry.append(
                (lineno, int(match.group("streak")), int(match.group("budget")))
            )
            return True

    if _is(logger, "rui.audio.vad"):
        match = SEG.match(msg)
        if match:
            scan.seg.append(
                (lineno, float(match.group("speech")), float(match.group("dur")))
            )
            return True
        match = DISCARDED.match(msg)
        if match:
            scan.discarded.append((lineno, int(match.group("ms"))))
            return True

    if _is(logger, "rui.providers.chain"):
        match = STAGE_FAILURE.match(msg)
        if match:
            scan.stage_failures.append(
                (lineno, match.group("stage"), match.group("detail").strip())
            )
            return True

    return False


def _add_wake(scan: LogScan, lineno: int, ts: str | None, wake_id: str, verdict: str,
              reason: str, admit_ms: int | None) -> None:
    match = WAKE_ID.match(wake_id)
    device = match.group("device") if match else None
    if device:
        scan.note_device(device)
    scan.wake.append(
        WakeRec(lineno=lineno, ts=ts, device=device, verdict=verdict, reason=reason,
                admit_ms=admit_ms)
    )


def _pair_barges(scan: LogScan) -> None:
    """Pair a `barge: silence_after_ms=` line with the barge line on the line before it.

    Both are emitted microseconds apart by _stop_for_barge (audio/process.py:1758
    then :1776) and neither carries a device id. The only evidence that a pair
    belongs together is that nothing at all was logged between them, so the rule is
    literal adjacency (`stop.lineno == barge.lineno + 1`); everything else stays
    unpaired and is reported as unpaired rather than matched by guesswork.

    A stop is matched to the barge on the line immediately before it and nothing
    else: with several planes writing to the same stream the pair may straddle two
    planes, so this pairing never claims a device. The report prints both
    "barges with no stop" and "stops with no barge" separately - neither is a
    success rate, and a missing stop is not evidence that the speaker kept playing.
    """
    pending: BargeRec | None = None
    for rec in sorted(scan.barges + scan.barge_stops, key=lambda item: item.lineno):
        if isinstance(rec, BargeRec):
            pending = rec
            continue
        if pending is not None and rec.lineno == pending.lineno + 1:
            rec.paired_with = pending.lineno
        pending = None


def parse_marks(path: Path) -> MarksScan:
    marks = MarksScan(path=str(path), present=True)
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            marks.why = "marks 文件为空"
            return marks
        marks.header = [cell.strip() for cell in header]
        missing = [col for col in MARK_COLUMNS if col not in marks.header]
        marks.missing_columns = missing
        for required in ("phrase_id", "device_id"):
            if required in missing:
                marks.why = f"marks 表头缺 `{required}`，无法定位格子"
                return marks
        marks.usable = True
        index = {col: i for i, col in enumerate(marks.header)}
        seen: dict[tuple[str, str, str], int] = {}
        last_ts: dict[str, datetime] = {}
        for lineno, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            row = _mark_row(cells, index, lineno, marks)
            if row is None:
                continue
            key = (row.phrase_id, row.device_id, row.round)
            if row.round and key in seen:
                marks.duplicates.append((lineno, seen[key], f"{key[0]}/{key[1]}/{key[2]}"))
                continue
            if row.round:
                seen[key] = lineno
            if row.ts is not None:
                previous = last_ts.get(row.device_id)
                if previous is not None and row.ts < previous:
                    marks.out_of_order.append(lineno)
                else:
                    last_ts[row.device_id] = row.ts
            marks.rows.append(row)
    return marks


def _mark_row(cells: list[str], index: dict[str, int], lineno: int,
              marks: MarksScan) -> MarkRow | None:
    def cell(name: str) -> str:
        position = index.get(name)
        if position is None or position >= len(cells):
            return ""
        return cells[position].strip()

    phrase = cell("phrase_id")
    device = cell("device_id")
    if not phrase or not device:
        marks.problems.append((lineno, "缺 phrase_id 或 device_id，整行跳过"))
        return None
    ts_text = cell("ts")
    ts = parse_ts(ts_text) if ts_text else None
    if ts_text and ts is None:
        marks.problems.append((lineno, f"ts 无法解析: {ts_text!r}（保留该行，时间相关结论标证据不足）"))
    verdict_text = cell("heard_ok")
    verdict = heard_ok(verdict_text)
    if verdict_text and verdict is None:
        marks.problems.append(
            (lineno, f"heard_ok 无法判定: {verdict_text!r}（按未判定处理，不猜）")
        )
    return MarkRow(
        lineno=lineno,
        phrase_id=phrase,
        device_id=device,
        round=cell("round"),
        ts_text=ts_text,
        ts=ts,
        heard_ok=verdict,
        notes=cell("notes"),
    )


# ---------------------------------------------------------------------- statistics
def p90(values: list[float]) -> float:
    """Nearest-rank p90: no interpolation, no invented value between two samples."""
    ordered = sorted(values)
    index = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return ordered[index]


def latency(values: list[float], lines: list[int], why_empty: str,
            skipped: int = 0) -> dict:
    if not values:
        return {"median": None, "p90": None, "n": 0, "lines": list(lines),
                "skipped": skipped, "why": why_empty}
    return {
        "median": round(statistics.median(values), 1),
        "p90": round(p90(values), 1),
        "n": len(values),
        "lines": list(lines),
        "skipped": skipped,
        "why": None,
    }


def scalar(value: int | float | None, lines: list[int], why: str | None = None) -> dict:
    return {"value": value, "lines": list(lines), "why": why}


def ranges(numbers: list[int], limit: int = RANGE_LIMIT) -> str:
    """[1,2,3,7,8,20] -> "L1-L3, L7-L8, L20" (every number still traceable)."""
    unique = sorted(set(numbers))
    if not unique:
        return "—"
    spans: list[tuple[int, int]] = []
    start = prev = unique[0]
    for number in unique[1:]:
        if number == prev + 1:
            prev = number
            continue
        spans.append((start, prev))
        start = prev = number
    spans.append((start, prev))
    parts = [f"L{a}" if a == b else f"L{a}-L{b}" for a, b in spans]
    if len(parts) > limit:
        return (", ".join(parts[:limit])
                + f" …（共 {len(spans)} 段 / {len(unique)} 行，完整列表见 --json）")
    return ", ".join(parts)


def _fmt_latency(block: dict, unit: str = "ms") -> str:
    if block.get("median") is None:
        return "证据不足"
    text = f"{block['median']:.1f} / {block['p90']:.1f} {unit} (n={block['n']})"
    if block.get("skipped"):
        text += f"，另 {block['skipped']} 个无该标记"
    return text


def _fmt_count(block: dict) -> str:
    value = block.get("value")
    return "证据不足" if value is None else str(value)


# ------------------------------------------------------------------------- report
def build_report(scan: LogScan, marks: MarksScan) -> dict:
    device_order = list(scan.device_order)
    if marks.usable:
        for row in marks.rows:
            if row.device_id not in device_order:
                device_order.append(row.device_id)
    devices = {device: _device_block(scan, device) for device in device_order}
    cells = _cell_block(marks, device_order)
    unattributed = _unattributed_block(scan)
    reconcile = _reconcile(devices, cells, device_order)
    todo = _todo_block(scan, marks, cells, devices, device_order)
    evidence = _evidence_block(devices, cells, unattributed)
    return {
        "log": {
            "path": scan.path,
            "lines": scan.lines_total,
            "blank": scan.blank,
            "prefixed": scan.prefixed,
            "recognised": scan.parsed,
            "unparsed": {"value": len(scan.unparsed),
                         "lines": scan.unparsed[:UNPARSED_SAMPLE]},
        },
        "marks": {
            "path": marks.path,
            "present": marks.present,
            "usable": marks.usable,
            "why": marks.why or None,
            "header": marks.header,
            "missing_columns": marks.missing_columns,
            "rows": len(marks.rows),
            "problems": [{"line": line, "problem": problem} for line, problem in marks.problems],
            "duplicates": [{"line": line, "first_line": first, "key": key}
                           for line, first, key in marks.duplicates],
            "out_of_order": marks.out_of_order,
        },
        "phrase_order": PHRASE_IDS,
        "device_order": device_order,
        "devices": devices,
        "cells": cells,
        "unattributed": unattributed,
        "reconcile": reconcile,
        "todo": todo,
        "evidence": evidence,
    }


def _device_block(scan: LogScan, device: str) -> dict:
    wake = [rec for rec in scan.wake if rec.device == device]
    traces = [rec for rec in scan.traces if rec.device == device]
    admitted = [rec for rec in wake if rec.verdict == "admitted"]
    rejected = [rec for rec in wake if rec.verdict == "rejected"]
    timeouts = [rec for rec in wake if rec.verdict == "timeout"]
    reasons: Counter = Counter()
    reason_lines: dict[str, list[int]] = defaultdict(list)
    for rec in rejected:
        reasons[rec.reason] += 1
        reason_lines[rec.reason].append(rec.lineno)
    firsts = [(rec.first_response_ms, rec.lineno) for rec in traces
              if rec.first_response_ms is not None]
    barge_turns = [rec for rec in traces if "barge_in" in rec.stages]
    silences = [(rec.silence_after_ms, rec.lineno) for rec in barge_turns
                if rec.silence_after_ms is not None]
    # playback_gap_count is absent (not 0) on an older plane / when app.py:2714 had no
    # metrics to copy (trace.py:84-85 says so explicitly), so a missing field can never
    # become a provable 0. Only an int in meta counts as evidence.
    gap_known: list[int] = []
    gap_unknown: list[int] = []
    gap_positive: list[int] = []
    gap_total = 0
    gap_max: float | None = None
    for rec in traces:
        count = rec.meta.get("playback_gap_count")
        if isinstance(count, int) and not isinstance(count, bool):
            gap_known.append(rec.lineno)
            if count > 0:
                gap_positive.append(rec.lineno)
                gap_total += count
                maximum = _float_or_none(rec.meta.get("playback_max_gap_ms"))
                if maximum is not None and (gap_max is None or maximum > gap_max):
                    gap_max = maximum
        else:
            gap_unknown.append(rec.lineno)
    if not traces:
        gap_block = {
            "turns": None, "total": None, "max_ms": None, "known": 0, "unknown": 0,
            "positive_lines": [], "known_lines": [],
            "why": "没有 turn trace 行，无法逐台判断播放 gap",
        }
    elif not gap_known:
        gap_block = {
            "turns": None, "total": None, "max_ms": None, "known": 0,
            "unknown": len(gap_unknown), "positive_lines": [], "known_lines": [],
            "why": "trace 无 playback_gap_count 字段（旧 plane / metrics 未上报），无法判定",
        }
    else:
        gap_block = {
            "turns": len(gap_positive), "total": gap_total, "max_ms": gap_max,
            "known": len(gap_known), "unknown": len(gap_unknown),
            "positive_lines": gap_positive, "known_lines": gap_known, "why": None,
        }
        if gap_positive and gap_max is None:
            gap_block["why"] = "有 gap 的 turn 缺 playback_max_gap_ms，max 无法判定"
    asr_missing = [rec.lineno for rec in traces
                   if "wake" in rec.stages and "asr_done" not in rec.stages]
    no_wake_why = ("日志中没有该设备的 wake candidate 行（可能未启用仲裁/单 plane、"
                   "完全漏唤醒，或日志窗口未覆盖）")
    no_trace_why = "日志中没有该设备的 rui.trace turn 行"
    return {
        "wake_candidates": scalar(len(wake) if wake else None, [rec.lineno for rec in wake],
                                  None if wake else no_wake_why),
        "admitted": scalar(len(admitted) if wake else None,
                           [rec.lineno for rec in admitted],
                           None if wake else no_wake_why),
        "rejected": {
            "value": len(rejected) if wake else None,
            "by_reason": {name: {"count": count, "lines": reason_lines[name]}
                          for name, count in sorted(reasons.items())},
            "lines": [rec.lineno for rec in rejected],
            "why": None if wake else no_wake_why,
        },
        "timeouts": scalar(len(timeouts) if wake else None,
                           [rec.lineno for rec in timeouts],
                           None if wake else no_wake_why),
        "turns": scalar(len(traces) if traces else None, [rec.lineno for rec in traces],
                        None if traces else no_trace_why),
        "first_response_ms": latency(
            [value for value, _line in firsts], [line for _value, line in firsts],
            no_trace_why if not traces else "所有 turn 都没有 play_start 标记（first_response_ms 为空）",
            skipped=len(traces) - len(firsts),
        ),
        "barge_turns": scalar(
            len(barge_turns) if traces else None, [rec.lineno for rec in barge_turns],
            None if traces else "没有 turn trace 行，无法逐台判断 barge",
        ),
        "stop_after_ms": latency(
            [value for value, _line in silences], [line for _value, line in silences],
            "没有带 silence_after_ms 的 barge turn",
            skipped=len(barge_turns) - len(silences),
        ),
        "gaps": {**gap_block, "lines": gap_block["positive_lines"]},
        "asr_missing_turns": scalar(
            len(asr_missing) if traces else None, asr_missing,
            None if traces else "没有 turn trace 行",
        ),
        "evidence_lines": {
            "wake_candidates": [rec.lineno for rec in wake],
            "admitted": [rec.lineno for rec in admitted],
            "rejected": [rec.lineno for rec in rejected],
            "timeouts": [rec.lineno for rec in timeouts],
            "turns": [rec.lineno for rec in traces],
            "first_response_ms": [line for _v, line in firsts],
            "barge_turns": [rec.lineno for rec in barge_turns],
            "stop_after_ms": [line for _v, line in silences],
            "gap_known": gap_known,
            "gap_positive": gap_positive,
            "asr_missing_turns": asr_missing,
        },
    }


def _cell_block(marks: MarksScan, device_order: list[str]) -> dict:
    """phrase_id -> {device -> attempts / heard_ok / unknown / lines / span}."""
    cells: dict[str, dict[str, dict]] = {
        phrase: {} for phrase in PHRASE_IDS
    }
    unknown_phrases: dict[str, list[int]] = defaultdict(list)
    for row in marks.rows:
        if row.phrase_id not in cells:
            unknown_phrases[row.phrase_id].append(row.lineno)
            continue
        cell = cells[row.phrase_id].setdefault(
            row.device_id,
            {"attempts": 0, "heard_ok": 0, "unknown_ok": 0, "lines": [],
             "untimed": 0, "first_ts": None, "last_ts": None, "notes": []},
        )
        cell["attempts"] += 1
        cell["lines"].append(row.lineno)
        if row.heard_ok is True:
            cell["heard_ok"] += 1
        elif row.heard_ok is None:
            cell["unknown_ok"] += 1
        if row.ts is None:
            cell["untimed"] += 1
        else:
            if cell["first_ts"] is None or row.ts < cell["first_ts"]:
                cell["first_ts"] = row.ts
            if cell["last_ts"] is None or row.ts > cell["last_ts"]:
                cell["last_ts"] = row.ts
        if row.notes:
            cell["notes"].append({"line": row.lineno, "notes": row.notes})
    if not marks.usable:
        for phrase in cells:
            cells[phrase] = {}
    else:
        for per_device in cells.values():
            for device in device_order:
                per_device.setdefault(
                    device,
                    {"attempts": 0, "heard_ok": 0, "unknown_ok": 0, "lines": [],
                     "untimed": 0, "first_ts": None, "last_ts": None, "notes": []},
                )
    return {
        "usable": marks.usable,
        "heard_ok_column": "heard_ok" not in marks.missing_columns if marks.usable else None,
        "cells": cells,
        "unknown_phrases": {phrase: {"value": len(lines), "lines": lines}
                            for phrase, lines in sorted(unknown_phrases.items())},
    }


def _unattributed_block(scan: LogScan) -> dict:
    paired = [rec for rec in scan.barge_stops if rec.paired_with is not None]
    unpaired = [rec for rec in scan.barge_stops if rec.paired_with is None]
    paired_barges = {rec.paired_with for rec in paired}
    without_stop = [rec for rec in scan.barges if rec.lineno not in paired_barges]
    stage: dict[str, dict] = {}
    for name in ("asr", "llm", "tts"):
        lines = [line for line, stage_name, _detail in scan.stage_failures if stage_name == name]
        details: Counter = Counter()
        for _line, stage_name, detail in scan.stage_failures:
            if stage_name == name:
                details[detail] += 1
        stage[name] = {
            "value": len(lines),
            "lines": lines,
            "details": [{"count": count, "detail": detail}
                        for detail, count in sorted(details.items(),
                                                    key=lambda item: (-item[1], item[0]))],
        }
    errors: Counter = Counter()
    error_lines: dict[str, list[int]] = defaultdict(list)
    for line, error in scan.result_turns:
        if error:
            errors[error] += 1
            error_lines[error].append(line)
    return {
        "barge": scalar(len(scan.barges), [rec.lineno for rec in scan.barges]),
        "barge_stop_paired": latency(
            [rec.ms for rec in paired], [rec.lineno for rec in paired],
            "没有可配对的 barge 行（barge 与 silence_after_ms 行不相邻）",
        ),
        "barge_stop_unpaired": scalar(len(unpaired), [rec.lineno for rec in unpaired]),
        "barge_without_stop": scalar(len(without_stop),
                                     [rec.lineno for rec in without_stop]),
        "barge_failed": {
            "value": len(scan.barge_failed),
            "lines": [line for line, _detail in scan.barge_failed],
            "details": [{"line": line, "detail": detail}
                        for line, detail in scan.barge_failed],
        },
        "barge_control": scalar(len(scan.barge_control), list(scan.barge_control)),
        "seg": {
            "value": len(scan.seg),
            "lines": [line for line, _speech, _dur in scan.seg],
            "speech_s": round(sum(speech for _line, speech, _dur in scan.seg), 2),
        },
        "discarded": scalar(len(scan.discarded), [line for line, _ms in scan.discarded]),
        "reply_gaps": {
            "value": len(scan.reply_gaps),
            "total_gaps": sum(count for _line, count, _ms in scan.reply_gaps),
            "max_ms": max((ms for _line, _count, ms in scan.reply_gaps), default=None),
            "lines": [line for line, _count, _ms in scan.reply_gaps],
        },
        "reply_chunks": scalar(len(scan.reply_chunks), list(scan.reply_chunks)),
        "stage_failures": stage,
        "turn_failed": {
            "value": len(scan.turn_failed),
            "lines": [line for line, _detail in scan.turn_failed],
            "details": [{"line": line, "detail": detail} for line, detail in scan.turn_failed],
        },
        "plane_degraded": {
            "value": len(scan.plane_degraded),
            "lines": [line for line, _detail in scan.plane_degraded],
            "details": [{"line": line, "detail": detail}
                        for line, detail in scan.plane_degraded],
        },
        "capture_recovered": {
            "value": len(scan.capture_recovered),
            "lines": [line for line, _n in scan.capture_recovered],
        },
        "capture_retry": {
            "value": len(scan.capture_retry),
            "lines": [line for line, _streak, _budget in scan.capture_retry],
        },
        "wake_no_speech": scalar(len(scan.wake_no_speech), list(scan.wake_no_speech)),
        "result_turns": {
            "value": len(scan.result_turns),
            "with_transcript": len(scan.result_transcripts),
            "errors": [{"count": count, "error": error, "lines": error_lines[error]}
                       for error, count in sorted(errors.items(),
                                                  key=lambda item: (-item[1], item[0]))],
            "lines": [line for line, _error in scan.result_turns],
        },
        "no_control_plane": scalar(
            len([rec for rec in scan.wake if rec.verdict == "no_control_plane"]),
            [rec.lineno for rec in scan.wake if rec.verdict == "no_control_plane"],
        ),
        "unparsed": {"value": len(scan.unparsed), "lines": scan.unparsed[:UNPARSED_SAMPLE]},
        "malformed_trace": scalar(len(scan.malformed_trace), list(scan.malformed_trace)),
        "malformed_result": scalar(len(scan.malformed_result), list(scan.malformed_result)),
    }


def _reconcile(devices: dict, cells: dict, device_order: list[str]) -> list[dict]:
    rows = []
    marked = bool(cells["usable"])
    for device in device_order:
        attempts = 0
        ok = 0
        unknown = 0
        for phrase in PHRASE_IDS:
            cell = cells["cells"].get(phrase, {}).get(device)
            if not cell:
                continue
            attempts += cell["attempts"]
            ok += cell["heard_ok"]
            unknown += cell["unknown_ok"]
        block = devices[device]
        candidates = block["wake_candidates"]["value"]
        admitted = block["admitted"]["value"]
        rows.append({
            "device": device,
            "marked_attempts": attempts if marked else None,
            "marked_heard_ok": ok if marked else None,
            "marked_unknown": unknown if marked else None,
            "log_candidates": candidates,
            "log_admitted": admitted,
            "attempts_minus_candidates": (
                attempts - candidates if marked and candidates is not None else None
            ),
            "heard_ok_minus_admitted": (
                ok - admitted if marked and admitted is not None else None
            ),
        })
    return rows


def _todo_block(scan: LogScan, marks: MarksScan, cells: dict, devices: dict,
                device_order: list[str]) -> list[str]:
    todo: list[str] = []
    todo.append(
        "句级日志指标（唤醒成功率/首字延迟/打断/停播/gap/ASR 错误）无法从日志归属到某一句："
        "日志行只带 device_id（wake candidate / rui.trace turn），没有 phrase_id，"
        "marks 也只有人工 heard_ok —— 逐句日志数字需要 rui 输出句标签或有人逐回合抄 ms。"
    )
    todo.append(
        "误唤醒：日志区分不了「没人说话却触发」与真唤醒，本工具不生成该数字，只能人工标记。"
    )
    todo.append(
        "admitted/candidates 的分母只含「已产生候选」的尝试；一次候选都没有的漏唤醒不在日志里，"
        "真实唤醒率请用 §2 的人工尝试数。"
    )
    todo.append(
        "ASR/LLM/TTS 失败计数按 provider 链 60 s 窗口去重（providers/chain.py:139），是下限不是总数。"
    )
    todo.append(
        "barge: / [seg] / reply: / turn: 行没有 device_id：fleet 级数字不能拆到单台设备。"
    )
    if not marks.present:
        todo.append("未提供 --marks：人工列（heard_ok / 尝试数）全部是 TODO。")
    elif not marks.usable:
        todo.append(f"marks 不可用（{marks.why or '表头不完整'}）：人工列全部是 TODO。")
    else:
        if "heard_ok" in marks.missing_columns:
            todo.append("marks 缺 heard_ok 列：格子只给尝试数，成功率标证据不足。")
        if "round" in marks.missing_columns:
            todo.append("marks 缺 round 列：无法判重，重复标记不会被发现。")
        if "ts" in marks.missing_columns:
            todo.append("marks 缺 ts 列：无法给出格子的时间跨度。")
        logged = set(scan.device_order)
        for device in device_order:
            if device not in logged:
                todo.append(f"marks 里有设备 `{device}`，日志里没有它的任何行：该设备列证据不足。")
        if marks.problems:
            todo.append(f"marks 有 {len(marks.problems)} 行问题（见 §5），相关格子按未判定处理。")
        if marks.duplicates:
            todo.append(
                f"marks 有 {len(marks.duplicates)} 行重复 (phrase/device/round)，"
                "已按第一行计数，重复行没被静默吞掉。"
            )
        if cells["unknown_phrases"]:
            names = "、".join(cells["unknown_phrases"])
            todo.append(f"marks 里出现未知 phrase_id：{names}（未计入矩阵，行号见 §6）。")
    untimed = 0
    for phrase in PHRASE_IDS:
        for cell in cells["cells"].get(phrase, {}).values():
            untimed += cell.get("untimed", 0)
    if untimed:
        todo.append(
            f"marks 有 {untimed} 行 ts 为空或无法解析：这些回合只有人工判定、没有时间点，"
            "无法与日志窗口对齐（行号见 §6 的 `marks L…`）。"
        )
    for device, block in devices.items():
        gaps = block["gaps"]
        if gaps["turns"] is None and gaps["unknown"]:
            todo.append(
                f"设备 `{device}`：{gaps['unknown']} 个 turn 都没有 playback_gap_count 字段"
                "（旧 plane / metrics 未上报）→ 播放 gap 证据不足，不是 0。"
            )
        elif gaps["unknown"]:
            todo.append(
                f"设备 `{device}`：{gaps['unknown']} 个 turn 没有 playback_gap_count 字段，"
                f"gap 只统计其余 {gaps['known']} 个 turn。"
            )
    if scan.malformed_trace or scan.malformed_result:
        todo.append(
            f"日志有 {len(scan.malformed_trace)} 行 turn trace JSON 无法解析、"
            f"{len(scan.malformed_result)} 行 turn JSON 无法解析（行号见 §3）。"
        )
    return todo


def _evidence_block(devices: dict, cells: dict, unattributed: dict) -> list[dict]:
    """One row per printed number, with the *full* line list.

    JSON consumers get every line; the markdown renderer compresses the list into
    ranges (and caps the range count) only for display.
    """
    rows: list[dict] = []

    def add(scope: str, metric: str, lines: list[int], file: str = "log") -> None:
        rows.append({"scope": scope, "metric": metric, "file": file,
                     "lines": list(lines)})

    for device, block in devices.items():
        evidence = block["evidence_lines"]
        add(device, "wake candidate 总数", evidence["wake_candidates"])
        add(device, "admitted", evidence["admitted"])
        for reason, info in block["rejected"]["by_reason"].items():
            add(device, f"rejected: {reason}", info["lines"])
        add(device, "rejected 合计", block["rejected"]["lines"])
        add(device, "admit 超时", evidence["timeouts"])
        add(device, "turn trace（turns/ASR）", evidence["turns"])
        add(device, "first_response_ms", evidence["first_response_ms"])
        add(device, "barge turn", evidence["barge_turns"])
        add(device, "stop_after_ms", evidence["stop_after_ms"])
        add(device, "播放 gap（字段已知的 turn，0 与非 0 的判定依据）", evidence["gap_known"])
        add(device, "有 gap 的 turn", evidence["gap_positive"])
        add(device, "wake 无 asr_done", evidence["asr_missing_turns"])
    for phrase in PHRASE_IDS:
        for device, cell in cells["cells"].get(phrase, {}).items():
            if cell["attempts"]:
                span = ""
                if cell["first_ts"] is not None and cell["last_ts"] is not None:
                    span = (f"；{cell['first_ts']:%H:%M:%S}–{cell['last_ts']:%H:%M:%S}"
                            f"（其中 {cell['untimed']} 行无 ts）" if cell["untimed"]
                            else f"；{cell['first_ts']:%H:%M:%S}–{cell['last_ts']:%H:%M:%S}")
                add(f"{PHRASE_LABEL[phrase]}×{device}",
                    f"人工 heard_ok {cell['heard_ok']}/{cell['attempts']}"
                    f"（未判定 {cell['unknown_ok']}）{span}",
                    cell["lines"], file="marks")
    for device in devices:
        lines: list[int] = []
        attempts = 0
        for phrase in PHRASE_IDS:
            cell = cells["cells"].get(phrase, {}).get(device)
            if not cell:
                continue
            lines.extend(cell["lines"])
            attempts += cell["attempts"]
        if attempts:
            add(device, "人工 heard_ok 合计 / 尝试数", lines, file="marks")
    add("fleet", "barge 行", unattributed["barge"]["lines"])
    add("fleet", "barge→silence_after_ms 配对", unattributed["barge_stop_paired"]["lines"])
    add("fleet", "barge 未配对的 silence_after_ms",
        unattributed["barge_stop_unpaired"]["lines"])
    add("fleet", "无 stop 的 barge（barges − 配对）",
        unattributed["barge_without_stop"]["lines"])
    add("fleet", "barge: stop failed", unattributed["barge_failed"]["lines"])
    add("fleet", "控制面 barge-in 行", unattributed["barge_control"]["lines"])
    add("fleet", "[seg] 段", unattributed["seg"]["lines"])
    add("fleet", "utterance discarded", unattributed["discarded"]["lines"])
    add("fleet", "reply gap", unattributed["reply_gaps"]["lines"])
    add("fleet", "reply chunks", unattributed["reply_chunks"]["lines"])
    for name, info in unattributed["stage_failures"].items():
        add("fleet", f"{name} 失败告警", info["lines"])
    add("fleet", "turn failed", unattributed["turn_failed"]["lines"])
    add("fleet", "audio plane degraded", unattributed["plane_degraded"]["lines"])
    add("fleet", "capture recovered", unattributed["capture_recovered"]["lines"])
    add("fleet", "capture retry", unattributed["capture_retry"]["lines"])
    add("fleet", "wake without speech", unattributed["wake_no_speech"]["lines"])
    add("fleet", "turn 结果行（transcript/error）", unattributed["result_turns"]["lines"])
    add("fleet", "wake candidate 无控制面被丢弃", unattributed["no_control_plane"]["lines"])
    add("fleet", "turn trace JSON 无法解析", unattributed["malformed_trace"]["lines"])
    add("fleet", "turn 结果 JSON 无法解析", unattributed["malformed_result"]["lines"])
    add("fleet", f"未识别行（只列前 {UNPARSED_SAMPLE} 行）", unattributed["unparsed"]["lines"])
    return rows


# ------------------------------------------------------------------------- render
def render_markdown(report: dict) -> str:
    log = report["log"]
    marks = report["marks"]
    unattributed = report["unattributed"]
    out: list[str] = []
    add = out.append

    add("# rui fleet matrix — Dot2 vs 小爱")
    add("")
    prefixless = log["lines"] - log["prefixed"] - log["blank"]
    add(f"- 日志：`{log['path']}`（共 {log['lines']} 行 = 标准前缀 {log['prefixed']} "
        f"+ 无前缀 {prefixless} + 空行 {log['blank']}；识别 {log['recognised']} 行；"
        f"未识别 {log['unparsed']['value']} 行）")
    if marks["present"]:
        state = "可用" if marks["usable"] else f"不可用（{marks['why'] or '表头不完整'}）"
        add(f"- 人工标记：`{marks['path']}`（{marks['rows']} 行，{state}）")
    else:
        add("- 人工标记：未提供（`--marks`）")
    add("- 口径：只填日志/标记能证明的数字；证明不了 → 「证据不足」。不插值、不补默认值。")
    add("- 统计口径：median = 中位数（偶数个取中间两个的平均）；p90 = nearest-rank（不插值）；"
        "ms 保留 1 位小数。")
    add("- 行号：`L<n>` 指 `--log` 文件第 n 行；`marks L<n>` 指 marks 文件第 n 行。")
    add("")

    add("## 1. 设备 × 日志可证指标")
    add("")
    if not report["device_order"]:
        add("日志里没有识别出任何设备行（`wake candidate` / `rui.trace turn`）→ 没有设备列。"
            "这不是 0，是证据不足。")
        add("")
    add("| 设备 | wake candidates | admitted | 被拒（按 reason） | admit 超时 | turns | "
        "first_response_ms p50/p90 | barge turn | 停播延迟 p50/p90 | gap turn/总数/max | "
        "wake 无 asr_done |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for device in report["device_order"]:
        block = report["devices"][device]
        rejected = block["rejected"]["by_reason"]
        rejected_text = "证据不足"
        if rejected:
            items = sorted(rejected.items(), key=lambda item: (-item[1]["count"], item[0]))
            rejected_text = ", ".join(f"{name}×{info['count']}" for name, info in items)
        elif block["rejected"]["value"] == 0:
            rejected_text = "0"
        gaps = block["gaps"]
        if gaps["turns"] is None:
            gap_text = "证据不足"
        else:
            if gaps["max_ms"] is None:
                maximum = "max 证据不足" if gaps["turns"] else "—"
            else:
                maximum = f"{gaps['max_ms']:.1f} ms"
            gap_text = f"{gaps['turns']}/{gaps['total']}/{maximum}"
            if gaps["unknown"]:
                gap_text += f"，另 {gaps['unknown']} 个 turn 无该字段"
        stop_text = _fmt_latency(block["stop_after_ms"])
        if block["barge_turns"]["value"] == 0 and block["stop_after_ms"]["median"] is None:
            stop_text = "无 barge（0 个 barge turn）"
        add(
            f"| {device} | {_fmt_count(block['wake_candidates'])} | "
            f"{_fmt_count(block['admitted'])} | {rejected_text} | "
            f"{_fmt_count(block['timeouts'])} | {_fmt_count(block['turns'])} | "
            f"{_fmt_latency(block['first_response_ms'])} | "
            f"{_fmt_count(block['barge_turns'])} | "
            f"{stop_text} | {gap_text} | "
            f"{_fmt_count(block['asr_missing_turns'])} |"
        )
    add("")
    add("> `barge turn` / `gap` 只覆盖日志里存在 rui.trace turn 行的回合；"
        "`wake 无 asr_done` 也可能是 turn 在 ASR 之前被打断，不等于 ASR 失败。")
    add("> `gap` 只统计 meta 里**显式**带 `playback_gap_count` 的 turn"
        "（`trace.py:84-85`：旧 plane 缺字段是跳过、不记 0）；缺字段的 turn 不计入，"
        "数量写在格子里，全部缺字段时整格写「证据不足」。")
    add("")

    add("## 2. 逐句矩阵（人工 heard_ok / 尝试数）")
    add("")
    if not report["device_order"]:
        add("日志和 marks 里都没有设备 → 矩阵没有列可填（证据不足）。")
        add("")
    elif not marks["usable"]:
        add("人工标记不可用 → 整张表 TODO（`--marks` 缺文件或表头不全，见 §4）。")
        add("")
        add("| 句 | phrase_id | " + " | ".join(report["device_order"]) + " | 日志侧句级 |")
        add("|---|---|" + "---|" * len(report["device_order"]) + "---|")
        for phrase in PHRASE_IDS:
            add(f"| {PHRASE_LABEL[phrase]} | `{phrase}` | "
                + " | ".join(["TODO"] * len(report["device_order"]))
                + " | 证据不足 |")
    else:
        add("| 句 | phrase_id | " + " | ".join(report["device_order"]) + " | 日志侧句级 |")
        add("|---|---|" + "---|" * len(report["device_order"]) + "---|")
        for phrase in PHRASE_IDS:
            cells = cells_line(report, phrase)
            add(f"| {PHRASE_LABEL[phrase]} | `{phrase}` | {cells} | 证据不足 |")
        totals = cells_totals(report)
        add("| **合计** | — | " + " | ".join(totals) + " | — |")
        add("")
        add("> 格子 = `heard_ok成功数/标记尝试数`；`（?n）` = n 行 heard_ok 为空/无法判定，"
            "不计入成功数。人工格子的行号见 §6；日志无法归属到句，右列一律「证据不足」。")
    add("")

    add("## 3. 未归属到设备的 fleet 级指标")
    add("")
    add("| 指标 | 值 |")
    add("|---|---|")
    add(f"| `barge:` 行 | {_fmt_count(unattributed['barge'])} |")
    add(f"| barge→`silence_after_ms` 配对 | "
        f"{_fmt_latency(unattributed['barge_stop_paired'])} |")
    add(f"| barge `silence_after_ms` 未配对 | "
        f"{_fmt_count(unattributed['barge_stop_unpaired'])} |")
    add(f"| 无 stop 的 barge（barges − 配对，不是成功率） | "
        f"{_fmt_count(unattributed['barge_without_stop'])} |")
    add(f"| `barge: stop failed` | {_fmt_count(unattributed['barge_failed'])} |")
    add(f"| 控制面 `barge-in:` 行 | {_fmt_count(unattributed['barge_control'])} |")
    seg = unattributed["seg"]
    add(f"| `[seg]` 段 | {_fmt_count(seg)}"
        + (f"（speech 合计 {seg['speech_s']} s）" if seg["value"] else "") + " |")
    add(f"| `utterance discarded` | {_fmt_count(unattributed['discarded'])} |")
    gaps = unattributed["reply_gaps"]
    gap_text = _fmt_count(gaps)
    if gaps["value"]:
        gap_text += f"（gaps 合计 {gaps['total_gaps']}，max {gaps['max_ms']} ms）"
    add(f"| `reply: N gaps` 行 | {gap_text} |")
    add(f"| `reply: N chunks` 行 | {_fmt_count(unattributed['reply_chunks'])} |")
    for name in ("asr", "llm", "tts"):
        info = unattributed["stage_failures"][name]
        add(f"| `{name}:` 失败告警（下限） | {_fmt_count(info)} |")
    add(f"| `turn failed` | {_fmt_count(unattributed['turn_failed'])} |")
    add(f"| `audio plane degraded` | {_fmt_count(unattributed['plane_degraded'])} |")
    add(f"| `capture recovered`（恢复成功） | "
        f"{_fmt_count(unattributed['capture_recovered'])} |")
    add(f"| `capture: ... retry n/N` | {_fmt_count(unattributed['capture_retry'])} |")
    add(f"| `wake without speech` | {_fmt_count(unattributed['wake_no_speech'])} |")
    results = unattributed["result_turns"]
    result_text = _fmt_count(results)
    if results["value"]:
        result_text += f"（其中有 transcript {results['with_transcript']} 行，"
        result_text += f"error 非空 {sum(item['count'] for item in results['errors'])} 行）"
    add(f"| 控制面 `turn:` 结果行 | {result_text} |")
    add(f"| `wake candidate` 无控制面丢弃 | {_fmt_count(unattributed['no_control_plane'])} |")
    add(f"| turn trace JSON 无法解析 | {_fmt_count(unattributed['malformed_trace'])} |")
    add(f"| turn 结果 JSON 无法解析 | {_fmt_count(unattributed['malformed_result'])} |")
    add(f"| 未识别行 | {_fmt_count(unattributed['unparsed'])} |")
    add("")
    details = unattributed["result_turns"]["errors"]
    if details:
        add("控制面 `turn:` 行里的 error 分布（解释不了「为什么」，只报原文）：")
        add("")
        for item in details[:5]:
            add(f"- ×{item['count']} `{item['error']}`（{ranges(item['lines'])}）")
        if len(details) > 5:
            add(f"- …另 {len(details) - 5} 种，见 --json")
        add("")
    for key, label in (("turn_failed", "turn failed 原文"),
                       ("plane_degraded", "audio plane degraded 原文"),
                       ("barge_failed", "barge stop 失败原文")):
        items = unattributed[key]["details"]
        if items:
            add(f"{label}：")
            add("")
            for item in items[:5]:
                add(f"- `L{item['line']}` {item['detail']}")
            if len(items) > 5:
                add(f"- …另 {len(items) - 5} 条，见 --json")
            add("")

    add("## 4. 证据不足 / TODO（不许补数）")
    add("")
    for line in report["todo"]:
        add(f"- {line}")
    add("")

    add("## 5. 人工标记 vs 日志对账")
    add("")
    add("| 设备 | marks 尝试 | marks heard_ok（未判定） | 日志 candidates | 日志 admitted | "
        "尝试−candidates | heard_ok−admitted |")
    add("|---|---|---|---|---|---|---|")
    for row in report["reconcile"]:
        def show(value: object) -> str:
            return "—" if value is None else str(value)
        add(f"| {row['device']} | {show(row['marked_attempts'])} | "
            f"{show(row['marked_heard_ok'])}（{show(row['marked_unknown'])}） | "
            f"{show(row['log_candidates'])} | {show(row['log_admitted'])} | "
            f"{show(row['attempts_minus_candidates'])} | "
            f"{show(row['heard_ok_minus_admitted'])} |")
    add("")
    add("> 差额不是判决，只是把两边数字并排放好：`docker logs --since` 没覆盖、"
        "漏唤醒、掉线重建、人工标错都会造成差额。工具不解释、不补数，需人工核对。")
    add("")
    if marks["present"] and (marks["problems"] or marks["duplicates"]
                             or marks["out_of_order"] or report["cells"]["unknown_phrases"]):
        add("### marks 文件问题（逐行）")
        add("")
        for item in marks["problems"][:20]:
            add(f"- `marks L{item['line']}` {item['problem']}")
        if len(marks["problems"]) > 20:
            add(f"- …另 {len(marks['problems']) - 20} 行问题，见 --json")
        for item in marks["duplicates"][:20]:
            add(f"- `marks L{item['line']}` 重复 (phrase/device/round)={item['key']}，"
                f"首行 `marks L{item['first_line']}`")
        if len(marks["duplicates"]) > 20:
            add(f"- …另 {len(marks['duplicates']) - 20} 行重复，见 --json")
        if marks["out_of_order"]:
            add(f"- 这些行的 ts 早于同设备上一行（仍计入，只提示）："
                f"{ranges(marks['out_of_order']).replace('L', 'marks L')}")
        for phrase, info in report["cells"]["unknown_phrases"].items():
            ref = ranges(info["lines"]).replace("L", "marks L")
            add(f"- 未知 phrase_id `{phrase}`：{info['value']} 行（{ref}）")
        add("")
    add("## 6. 证据索引")
    add("")
    add("| 范围 | 指标 | 证据 |")
    add("|---|---|---|")
    for row in report["evidence"]:
        ref = ranges(row["lines"]) if row["lines"] else "—"
        if row["file"] == "marks" and row["lines"]:
            ref = "marks " + ref
        add(f"| {row['scope']} | {row['metric']} | {ref} |")
    add("")

    return "\n".join(out).rstrip() + "\n"


def cells_line(report: dict, phrase: str) -> str:
    parts: list[str] = []
    for device in report["device_order"]:
        cell = report["cells"]["cells"].get(phrase, {}).get(device)
        parts.append(_cell_text(cell, report["cells"]["heard_ok_column"]))
    return " | ".join(parts)


def cells_totals(report: dict) -> list[str]:
    parts: list[str] = []
    for device in report["device_order"]:
        attempts = ok = unknown = 0
        for phrase in PHRASE_IDS:
            cell = report["cells"]["cells"].get(phrase, {}).get(device)
            if not cell:
                continue
            attempts += cell["attempts"]
            ok += cell["heard_ok"]
            unknown += cell["unknown_ok"]
        if not attempts:
            parts.append("无标记")
        elif report["cells"]["heard_ok_column"] is False:
            parts.append(f"证据不足（{attempts} 行标记）")
        elif unknown == attempts:
            parts.append(f"证据不足（heard_ok 全空；{attempts} 行标记）")
        else:
            text = f"{ok}/{attempts}"
            if unknown:
                text += f"（?{unknown}）"
            parts.append(text)
    return parts


def _cell_text(cell: dict | None, heard_ok_column: bool | None) -> str:
    if not cell or not cell["attempts"]:
        return "无标记"
    attempts = cell["attempts"]
    if heard_ok_column is False:
        return f"证据不足（heard_ok 列缺失；{attempts} 行标记）"
    unknown = cell["unknown_ok"]
    if unknown == attempts:
        return f"证据不足（heard_ok 全空；{attempts} 行标记）"
    text = f"{cell['heard_ok']}/{attempts}"
    if unknown:
        text += f"（?{unknown}）"
    return text


def render_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False, default=str)


# ---------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fleet_matrix",
        description="rui container log (+ human marks CSV) -> Dot2 vs 小爱 matrix, "
                    "every number carrying its log line",
    )
    parser.add_argument("--log", required=True,
                        help="captured rui log (docker logs --since 48h rui > /tmp/rui.log)")
    parser.add_argument("--marks", default=None,
                        help="optional CSV: phrase_id,device_id,round,ts,heard_ok,notes")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.is_file():
        print(f"fleet_matrix: log not found: {log_path}", file=sys.stderr)
        return 2
    try:
        scan = parse_log(log_path)
    except OSError as exc:
        print(f"fleet_matrix: cannot read log: {exc}", file=sys.stderr)
        return 2

    marks = MarksScan()
    if args.marks:
        marks_path = Path(args.marks)
        if not marks_path.is_file():
            print(f"fleet_matrix: marks not found: {marks_path} "
                  "(continuing with log-only metrics)", file=sys.stderr)
        else:
            try:
                marks = parse_marks(marks_path)
            except OSError as exc:
                print(f"fleet_matrix: cannot read marks: {exc}", file=sys.stderr)
                marks = MarksScan(path=str(marks_path), present=True,
                                  why=f"读取失败: {exc}")

    report = build_report(scan, marks)
    print(render_json(report) if args.json else render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
