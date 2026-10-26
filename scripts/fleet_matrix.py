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
是/… ; empty = not judged). When it is empty the verdict is derived from the log by
**time window** (see below); when it is present it is kept and reported as
``human``. Nothing is guessed.

Time windows
------------
The log has no phrase_id, so the join key is time. Marks rows are sorted by ``ts``
and the i-th window is ``[ts_i, ts_{i+1})``; the last window is closed with
``--window`` (default 15 s). Inside a window the only filters are time and the
device id - never "the n-th wake line":

  * ``success`` - the device has a ``wake candidate ... admitted`` line in the window
    *and* one of its turn traces in the window carries ``asr_done`` or ``play_start``
    (trace.py:27-39; the task card calls ``play_start`` "tts_play_start").
  * ``fail``   - there is a wake line but no admission (reason from the rejection:
    ``turn_open`` / ``cross_echo`` / ``admit_timeout`` / ...), or an admission with
    no turn result in the window.
  * ``unknown`` - the device has **no wake line at all** in the window (log gap, log
    not covering that period, or a complete miss). This is never counted as fail and
    never enters the success denominator.

``--wake-only-phrase`` (default ``hello``) marks pure wake tests: the user speaks only
the wake word and the plane answers "wake without speech", so success needs admitted
and **no turn**; candidate-but-all-rejected is fail; no candidate is unknown. Pass
``--wake-only-phrase ''`` to judge those phrases with the turn rule instead.

If a mark's ``device_id`` is not a device in the log (the usual case: ``fleet_say``
writes the *playback source*, e.g. ``speaker``), every online device is judged in
that window separately ("single source, multiple devices"); ``--source-device``
declares such an id explicitly.

Timezone: the log's ``asctime`` has no offset (production rui runs in a UTC
container), while marks ``ts`` are local with ``+08:00``. ``--log-tz`` (default
``UTC``) is the zone the log lines are interpreted in; tz-aware marks are converted,
tz-naive marks are interpreted in the same zone. Comparing the two literally would
put every window 8 h away from its log lines and turn all 30 cells into ``unknown``.

Usage::

    python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks /tmp/fleet-marks.csv
    python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks /tmp/fleet-marks.csv \\
        --log-tz UTC --window 15
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
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

# Stages that prove the turn produced a result, in trace.py:27-39 order. The task card
# calls `play_start` "tts_play_start": play_start is the first PCM written (trace.py:38),
# which is exactly the "TTS started playing" evidence. `wake` alone is not a result.
WINDOW_RESULT_STAGES = ("asr_done", "play_start")
DEFAULT_WINDOW_S = 15.0
DEFAULT_LOG_TZ = "UTC"
# hello is a pure wake test: the user speaks only the wake word and the plane answers
# "wake without speech (5.0 s)" by design, so no turn is expected. Judging it with the
# turn-based rule printed 0/5 for a run where all five wakes were admitted.
DEFAULT_WAKE_ONLY_PHRASES = ("hello",)

# "%(asctime)s %(levelname)s %(name)s: %(message)s" - src/rui/log_setup.py:18.
LOG_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d{1,6})?)\s+"
    r"(?P<level>[A-Z]+)\s+(?P<logger>[A-Za-z0-9_.\-]+):\s?(?P<msg>.*)$"
)
# `docker logs -t` prepends an RFC3339 stamp; `docker compose logs` prepends "svc-1  | ".
DOCKER_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s+")
COMPOSE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*\s*\|\s?")
# A manually numbered paste ("L42 2026-10-26 06:17:24,053 INFO ..."). The numbers are
# dropped; every `L<n>` in the report still means the physical `--log` line, never the
# number somebody typed into their snippet.
LINE_NO_PREFIX = re.compile(r"^L\d+\s+")
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
# Rui's own KWS hit line (audio/kws.py), relayed from the plane process. A keyword hit
# is not an arbitration candidate and never adds to wake candidate / admitted.
KWS_WAKE = re.compile(r"^wake: (?P<keyword>.+)$")
# The control plane forwards every plane stderr line through this prefix
# (audio/client.py:529 `log.info("plane: %s", ...)`); the payload is a complete app
# log line of its own ("2026-10-26 06:21:06,060 INFO __main__: ...").
PLANE_RELAY_PREFIX = "plane: "
# The plane is launched as `python -m ...` (audio/client.py:419-421), so every
# `getLogger(__name__)` inside it is "__main__". Inside a `plane:` relay that name is
# the audio plane entrypoint, audio/process.py; imported submodule loggers
# (rui.audio.vad / rui.audio.kws) keep their own names.
PLANE_ENTRY_LOGGER = "rui.audio.process"

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
    ts: str | None = None  # wall clock of the log line, used only by time windows
    turn_id: str | None = None  # dedupe key when the same turn is also relayed


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
    relayed: int = 0  # lines the control plane forwarded as `plane: <app line>`
    relay_lines: list[int] = field(default_factory=list)
    relay_loggers: Counter = field(default_factory=Counter)  # inner logger names
    unparsed: list[int] = field(default_factory=list)
    device_order: list[str] = field(default_factory=list)
    wake: list[WakeRec] = field(default_factory=list)
    traces: list[TraceRec] = field(default_factory=list)
    duplicate_traces: list[tuple[int, int]] = field(default_factory=list)  # line, first_line
    kws_wake: list[tuple[int, str]] = field(default_factory=list)  # line, keyword
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
            + len(self.malformed_result) + len(self.kws_wake) + len(self.duplicate_traces)
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
def _kws_keywords(scan: LogScan) -> list[tuple[str, int, list[int]]]:
    """keyword -> (count, lines), most frequent first. Never summed into candidates."""
    counts: Counter = Counter()
    lines: dict[str, list[int]] = defaultdict(list)
    for lineno, keyword in scan.kws_wake:
        counts[keyword] += 1
        lines[keyword].append(lineno)
    return [(keyword, counts[keyword], lines[keyword])
            for keyword in sorted(counts, key=lambda name: (-counts[name], name))]


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



def parse_ts_aware(text: str) -> datetime | None:
    """Like parse_ts but keeps the offset (needed to align log and marks windows)."""
    raw = _clean(text).replace(",", ".")
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def log_tzinfo(name: str) -> tzinfo:
    """--log-tz -> tzinfo. Accepts ``UTC``, an IANA name, or ``+08:00`` / ``-0530``.

    ``UTC`` is the default on purpose: production rui runs in a UTC container, so a
    log line ``2026-10-26 06:17:24,053`` is 06:17 UTC while the marks written on the
    Mac say ``2026-10-26T14:17:24.053+08:00``. Comparing the two literally would put
    every window 8 hours away from its own log lines.
    """
    token = (name or "").strip()
    if not token:
        return UTC
    offset = re.fullmatch(r"(?P<sign>[+-])(?P<h>\d{2}):?(?P<m>\d{2})", token)
    if offset:
        delta = timedelta(hours=int(offset.group("h")), minutes=int(offset.group("m")))
        if offset.group("sign") == "-":
            delta = -delta
        return timezone(delta)
    if token.upper() == "UTC":
        return UTC
    return ZoneInfo(token)


def to_utc_naive(value: datetime, zone: tzinfo) -> datetime:
    """One comparable frame (naive UTC). A naive input is read as wall clock in `zone`."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC).replace(tzinfo=None)


def format_wall(moment: datetime, zone: tzinfo) -> str:
    """Naive-UTC moment -> ``HH:MM:SS.mmm`` in `zone`, i.e. the log's own wall clock."""
    return moment.replace(tzinfo=UTC).astimezone(zone).strftime("%H:%M:%S.%f")[:-3]


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
                inner_ts, inner_logger, inner_msg = _unwrap_plane_relay(msg)
                if inner_logger is not None:
                    scan.relayed += 1
                    scan.relay_lines.append(lineno)
                    scan.relay_loggers[inner_logger] += 1
                    logger = (PLANE_ENTRY_LOGGER if inner_logger == "__main__"
                              else inner_logger)
                    msg = inner_msg
                    # The inner stamp is the real event time; the outer one is only a
                    # fallback (they can differ by 1 ms and a window boundary is exact).
                    ts = inner_ts or ts
            else:
                ts = logger = None
                msg = body.strip()
            if not _dispatch(scan, lineno, ts, logger, msg):
                scan.unparsed.append(lineno)
    _pair_barges(scan)
    _dedupe_traces(scan)
    return scan


def _unwrap_plane_relay(msg: str) -> tuple[str | None, str | None, str]:
    """Peel ``plane: <app line>`` layers -> (inner ts, inner logger, inner message).

    The control plane forwards every plane stderr line as
    ``<outer stamp> INFO rui.audio.client: plane: <inner app line>``
    (audio/client.py:529). Without this step the whole payload looks like one
    unknown ``rui.audio.client`` message and every plane-side wake/VAD line is lost.
    Returns ``(None, None, msg)`` when no well-formed payload is present, so the
    caller falls back to the outer line. Up to 3 nested layers are peeled; the
    innermost logger/message win.
    """
    ts: str | None = None
    logger: str | None = None
    text = msg
    for _ in range(3):
        stripped = text.lstrip()
        if not stripped.startswith(PLANE_RELAY_PREFIX):
            break
        inner = LOG_PREFIX.match(stripped[len(PLANE_RELAY_PREFIX):])
        if inner is None:
            break
        ts, logger = inner.group("ts"), inner.group("logger")
        text = inner.group("msg")
    return ts, logger, text


def _dedupe_traces(scan: LogScan) -> None:
    """Count one turn once when the same turn_id arrives twice (direct + relayed).

    ``turn_id`` is generated per turn (trace.py), so a duplicate means the same
    event was logged twice, not two turns. The first line is kept and every dropped
    line is reported - never silently swallowed.
    """
    seen: dict[tuple[str, str], int] = {}
    kept: list[TraceRec] = []
    for rec in scan.traces:
        if rec.turn_id:
            key = (rec.device, rec.turn_id)
            first = seen.get(key)
            if first is not None:
                scan.duplicate_traces.append((rec.lineno, first))
                continue
            seen[key] = rec.lineno
        kept.append(rec)
    scan.traces = kept


def _strip_transport_prefixes(line: str) -> str:
    """Drop a `docker logs -t` stamp and/or a `docker compose logs` `name |` prefix.

    Neither is part of the app's own format (log_setup.FORMAT), but both are what a
    person actually pastes into /tmp: stripping them here means the capture command
    does not have to be exact. Two passes cover `rui-1  | <rfc3339> <app line>` and
    the reverse order; anything else is left untouched for the normal parser.
    """
    text = line
    for _ in range(3):
        stripped = DOCKER_STAMP.sub("", text, count=1)
        if stripped == text:
            stripped = COMPOSE_PREFIX.sub("", text, count=1)
        if stripped == text:
            stripped = LINE_NO_PREFIX.sub("", text, count=1)
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
        turn_id = entry.get("turn_id")
        scan.note_device(device)
        scan.traces.append(
            TraceRec(
                lineno=lineno,
                device=device,
                first_response_ms=_float_or_none(entry.get("first_response_ms")),
                silence_after_ms=_float_or_none(entry.get("silence_after_ms")),
                stages=stages if isinstance(stages, dict) else {},
                meta=meta if isinstance(meta, dict) else {},
                ts=ts,
                turn_id=turn_id if isinstance(turn_id, str) and turn_id else None,
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

    if _is(logger, "rui.audio.kws"):
        # A KWS hit proves the keyword matched; it is not a candidate and not a
        # success. Reported separately so it can never inflate admitted/success.
        match = KWS_WAKE.match(msg)
        if match:
            scan.kws_wake.append((lineno, match.group("keyword").strip()))
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


# ------------------------------------------------------------------- time windows
def tz_label(zone: tzinfo) -> str:
    """A stable, human-checkable label: IANA key when present, else UTC+HH:MM."""
    key = getattr(zone, "key", None)
    if isinstance(key, str):
        return key
    offset = zone.utcoffset(datetime.now(UTC))
    if not offset:
        return "UTC"
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    hours, minutes = divmod(abs(seconds) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _index_log_times(scan: LogScan, zone: tzinfo) -> tuple[dict, dict, dict[str, list[int]]]:
    """Device -> [(utc-naive moment, record)] for wake and trace lines.

    Only lines that carry a parseable timestamp can be placed in a window. The rest
    are kept as device -> physical line numbers (not just a count): the report prints
    "dot2×1（L9）" and §6 carries the full evidence list, so a reader can always open
    the exact line - they are never assigned to the nearest window.
    """
    wake: dict[str, list[tuple[datetime, WakeRec]]] = defaultdict(list)
    trace: dict[str, list[tuple[datetime, TraceRec]]] = defaultdict(list)
    untimed: dict[str, list[int]] = defaultdict(list)
    for rec in scan.wake:
        if rec.device is None:
            continue
        parsed = parse_ts_aware(rec.ts) if rec.ts else None
        if parsed is None:
            untimed[rec.device].append(rec.lineno)
            continue
        wake[rec.device].append((to_utc_naive(parsed, zone), rec))
    for rec in scan.traces:
        parsed = parse_ts_aware(rec.ts) if rec.ts else None
        if parsed is None:
            untimed[rec.device].append(rec.lineno)
            continue
        trace[rec.device].append((to_utc_naive(parsed, zone), rec))
    return wake, trace, untimed


def _untimed_lines(scan: LogScan) -> dict[str, list[int]]:
    """Device -> physical lines whose wake/turn record has no parseable timestamp."""
    untimed: dict[str, list[int]] = defaultdict(list)
    for rec in scan.wake:
        if rec.device is None:
            continue
        if not rec.ts or parse_ts_aware(rec.ts) is None:
            untimed[rec.device].append(rec.lineno)
    for rec in scan.traces:
        if not rec.ts or parse_ts_aware(rec.ts) is None:
            untimed[rec.device].append(rec.lineno)
    return untimed


def _window_targets(row: MarkRow, devices: list[str],
                    source_device: str | None) -> tuple[list[str], bool]:
    """Which devices a marks row is judged for, and whether that is multi-device mode.

    A marks row usually names the playback source (``fleet_say`` writes ``speaker``),
    which is not a device in the log; then every online device is judged separately
    in that window. A row naming a logged device judges that device only - unless
    ``--source-device`` declares that id to be the source rather than the device.
    """
    if source_device and row.device_id == source_device:
        return list(devices), True
    if row.device_id in devices:
        return [row.device_id], False
    return list(devices), True


def _judge_window(device: str, start: datetime, end: datetime, wake_index: dict,
                  trace_index: dict, untimed: dict[str, list[int]],
                  wake_only: bool = False) -> tuple[str, str, dict]:
    """Verdict for one device inside ``[start, end)``. Only time and device filter."""
    wakes = [(moment, rec) for moment, rec in wake_index.get(device, [])
             if start <= moment < end]
    turns = [(moment, rec) for moment, rec in trace_index.get(device, [])
             if start <= moment < end]
    admitted = [rec for _moment, rec in wakes if rec.verdict == "admitted"]
    rejected = [rec for _moment, rec in wakes
                if rec.verdict in ("rejected", "timeout")]
    result_turns = [rec for _moment, rec in turns
                    if any(stage in rec.stages for stage in WINDOW_RESULT_STAGES)]
    stages_present = [stage for stage in WINDOW_RESULT_STAGES
                      if any(stage in rec.stages for _moment, rec in turns)]
    reasons = Counter(rec.reason for rec in rejected if rec.reason)
    detail = {
        "wake": [rec.lineno for _moment, rec in wakes],
        "admitted": [rec.lineno for rec in admitted],
        "rejected": [rec.lineno for rec in rejected],
        "turn": [rec.lineno for _moment, rec in turns],
        "turn_result": [rec.lineno for rec in result_turns],
        "rejected_reasons": dict(reasons),
    }
    if not wakes:
        extra = ""
        if untimed.get(device):
            lines = untimed[device]
            extra = (f"；该设备另有 {len(lines)} 条没有时间戳的 wake/turn 行"
                     f"（{ranges(lines)}），无法归属（见 §6）")
        prefix = "仅测唤醒（wake-only）：" if wake_only else ""
        if result_turns:
            # A follow-up turn can be handled without a new wake candidate (the plane's
            # follow-up gate stays open). The turn is real evidence, but the log still
            # has no phrase id: it cannot prove *this* phrase caused it, so it stays
            # unknown - never a silent success and never a fail.
            lines = "、".join(f"L{rec.lineno}" for rec in result_turns)
            return ("unknown",
                    f"{prefix}窗口内没有该设备的 wake 行，但有 turn 结果（{lines}）：可能是唤醒门控/"
                    f"跟随时段，日志无 phrase_id，无法证明是本句 → unknown，不判 fail{extra}",
                    detail)
        return ("unknown",
                f"{prefix}窗口内该设备没有任何 wake 行（日志断档/未覆盖/未命中）{extra}",
                detail)
    if wake_only:
        # A wake-only phrase is answered by the plane itself (wake without speech), so
        # admitted is the whole success criterion; asking for a turn here is wrong.
        if admitted:
            return ("success", "仅测唤醒（wake-only）：窗口内有 admitted，不要求 turn", detail)
        reason_text = "、".join(f"{name}×{count}" for name, count in sorted(reasons.items()))
        return ("fail",
                f"仅测唤醒（wake-only）：candidate 全被拒（{reason_text or 'reason 未记录'}）",
                detail)
    if admitted:
        if result_turns:
            return ("success",
                    f"admitted + turn 结果（{'/'.join(stages_present)}）", detail)
        return ("fail", "admitted 后窗口内没有 turn 结果（无 asr_done/play_start）", detail)
    reason_text = "、".join(f"{name}×{count}" for name, count in sorted(reasons.items()))
    return ("fail", f"有 wake 行但无 admitted（{reason_text or 'reason 未记录'}）", detail)


def _window_block(scan: LogScan, marks: MarksScan, *, window_s: float,
                  source_device: str | None, log_tz: tzinfo,
                  wake_only_phrases: frozenset[str] = frozenset()) -> dict:
    """Build every time-window judgement. Empty when marks carry no usable ``ts``."""
    block: dict = {
        "window_s": window_s,
        "source_device": source_device,
        "log_tz": tz_label(log_tz),
        "wake_only_phrases": sorted(wake_only_phrases),
        "multi_device": False,
        "rows": [],
        "summary": [],
        "judged_devices": [],
        "totals": {},
        "notes": [],
        "untimed": {},
        "max_window_s": None,
    }
    untimed_lines = _untimed_lines(scan)
    block["untimed"] = {device: lines for device, lines in untimed_lines.items()}
    timed = [row for row in marks.rows if row.ts is not None]
    if not marks.usable or not timed:
        block["notes"].append(
            "未提供带 ts 的 marks → 不做时间窗对齐（证据不足，不是 fail）。"
        )
        return block

    wake_index, trace_index, untimed = _index_log_times(scan, log_tz)
    ordered = sorted(timed, key=lambda row: (row.ts, row.lineno))
    groups: list[tuple[datetime, list[MarkRow]]] = []
    for row in ordered:
        parsed = parse_ts_aware(row.ts_text)
        if parsed is None:  # defensive: _mark_row already parsed the same text
            block["notes"].append(f"marks L{row.lineno} 的 ts 无法解析，未参与窗口对齐。")
            continue
        moment = to_utc_naive(parsed, log_tz)
        if groups and groups[-1][0] == moment:
            groups[-1][1].append(row)
        else:
            groups.append((moment, [row]))
    if not groups:
        block["notes"].append("marks 里有 ts 的行都解析失败，无法建立窗口。")
        return block

    devices = list(scan.device_order)
    human_rows = 0
    index = 0
    for position, (start, rows) in enumerate(groups):
        if position + 1 < len(groups):
            end, end_kind = groups[position + 1][0], "next"
        else:
            end, end_kind = start + timedelta(seconds=window_s), "window"
        gap_s = (end - start).total_seconds()
        if block["max_window_s"] is None or gap_s > block["max_window_s"]:
            block["max_window_s"] = gap_s
        for row in rows:
            index += 1
            if row.heard_ok is not None:
                human_rows += 1
            targets, multi = _window_targets(row, devices, source_device)
            block["multi_device"] = block["multi_device"] or multi
            if not targets:
                block["rows"].append({
                    "index": index,
                    "mark_line": row.lineno,
                    "phrase_id": row.phrase_id,
                    "round": row.round,
                    "mark_device": row.device_id,
                    "mark_ts": row.ts_text,
                    "window_start": format_wall(start, log_tz),
                    "window_end": format_wall(end, log_tz),
                    "window_end_kind": end_kind,
                    "device": "",
                    "wake_only": row.phrase_id in wake_only_phrases,
                    "mode": "none",
                    "verdict": "unknown",
                    "reason": "日志里没有任何设备行，无法判定（证据不足，不是 fail）",
                    "source": "none",
                    "heard_ok": row.heard_ok,
                    "wake_lines": [], "admitted_lines": [], "rejected_lines": [],
                    "turn_lines": [], "turn_result_lines": [],
                    "rejected_reasons": {},
                })
                continue
            wake_only = row.phrase_id in wake_only_phrases
            for device in targets:
                verdict, reason, detail = _judge_window(
                    device, start, end, wake_index, trace_index, untimed,
                    wake_only=wake_only,
                )
                official = {"success": True, "fail": False}.get(verdict)
                block["rows"].append({
                    "index": index,
                    "mark_line": row.lineno,
                    "phrase_id": row.phrase_id,
                    "round": row.round,
                    "mark_device": row.device_id,
                    "mark_ts": row.ts_text,
                    "window_start": format_wall(start, log_tz),
                    "window_end": format_wall(end, log_tz),
                    "window_end_kind": end_kind,
                    "device": device,
                    "wake_only": wake_only,
                    "mode": "multi" if multi else "device",
                    "verdict": verdict,
                    "reason": reason,
                    "source": "human" if row.heard_ok is not None else "log",
                    "heard_ok": row.heard_ok if row.heard_ok is not None else official,
                    "wake_lines": detail["wake"],
                    "admitted_lines": detail["admitted"],
                    "rejected_lines": detail["rejected"],
                    "turn_lines": detail["turn"],
                    "turn_result_lines": detail["turn_result"],
                    "rejected_reasons": detail["rejected_reasons"],
                })

    counts = Counter(row["verdict"] for row in block["rows"])
    # Keep all three keys even at 0: "unknown 0" is information, a missing key
    # makes JSON consumers guess.
    block["totals"] = {key: counts.get(key, 0)
                       for key in ("success", "fail", "unknown")}
    summary_devices = list(devices)
    for row in block["rows"]:
        device = row["device"]
        if device and device not in summary_devices:
            summary_devices.append(device)
    for device in summary_devices:
        rows = [row for row in block["rows"] if row["device"] == device]
        block["summary"].append({
            "device": device,
            "windows": len(rows),
            "success": sum(1 for row in rows if row["verdict"] == "success"),
            "fail": sum(1 for row in rows if row["verdict"] == "fail"),
            "unknown": sum(1 for row in rows if row["verdict"] == "unknown"),
            "human": sum(1 for row in rows if row["source"] == "human"),
            "trace_only": sum(1 for row in rows if row["verdict"] == "unknown"
                              and row["turn_result_lines"]),
            "lines": [row["mark_line"] for row in rows],
        })
    # The phrase matrix has one column per device that was actually judged; a marks
    # id that is only the playback source is not a device and does not get a column.
    block["judged_devices"] = [item["device"] for item in block["summary"]]

    block["notes"].append(
        f"日志时间按 --log-tz={block['log_tz']} 解释：日志 asctime 不带时区，"
        "容器与本机时区不同时必须显式指定，否则窗口会整体偏移。"
    )
    if block["wake_only_phrases"]:
        block["notes"].append(
            "仅测唤醒（wake-only）句：`"
            + "`, `".join(block["wake_only_phrases"])
            + "` —— success 只要求窗口内有 admitted（不要求 turn）；"
            "candidate 全被拒 = fail；没有 candidate = unknown。可用 --wake-only-phrase '' 关闭。"
        )
    unknown_wake_only = sorted(set(block["wake_only_phrases"]) - set(PHRASE_IDS))
    if unknown_wake_only:
        block["notes"].append(
            "--wake-only-phrase 含未知 phrase_id："
            + "、".join(unknown_wake_only) + "（不生效，见 phrase_order）。"
        )
    if block["multi_device"]:
        block["notes"].append(
            "单音源多设备模式：marks 的 device_id 不是日志里的设备（或 --source-device 声明了它），"
            "窗口内每台在线设备分别判定；窗口内没有 wake 行的设备记 unknown，不是 fail。"
        )
    if source_device and not any(row.device_id == source_device for row in marks.rows):
        block["notes"].append(
            f"--source-device {source_device} 在 marks 里没有匹配行，该参数未生效。"
        )
    if untimed:
        detail = "、".join(f"{device}×{len(lines)}（{ranges(lines)}）"
                           for device, lines in sorted(untimed.items()))
        block["notes"].append(
            f"日志里有 {sum(len(lines) for lines in untimed.values())} 条 wake/turn 行"
            f"没有时间戳（无前缀或被截断），无法归属到任何窗口：{detail}（行号见 §6）。"
        )
    if block["max_window_s"] is not None and block["max_window_s"] > max(window_s, 1e-9):
        block["notes"].append(
            f"最长的相邻窗口 {block['max_window_s']:.1f} s 大于 --window={window_s:g} s"
            "（通常是跨 round 的间隙）：按 [ts_i, ts_{i+1}) 的规则仍然整段归给前一句。"
        )
    if human_rows:
        block["notes"].append(
            f"marks 里有 {human_rows} 行带人工 heard_ok：heard_ok 列原样保留并标 human，"
            "日志判定仍并列在表里。"
        )
    return block


def _derived_cell_stats(windows: dict) -> dict[tuple[str, str], dict]:
    """(phrase_id, device) -> window tally, consumed by the phrase matrix."""
    stats: dict[tuple[str, str], dict] = {}
    for row in windows["rows"]:
        key = (row["phrase_id"], row["device"])
        cell = stats.setdefault(
            key,
            {"success": 0, "fail": 0, "unknown": 0, "human": 0, "trace_only": 0,
             "windows": []},
        )
        cell[row["verdict"]] += 1
        if row["source"] == "human":
            cell["human"] += 1
        if row["verdict"] == "unknown" and row["turn_result_lines"]:
            cell["trace_only"] += 1
        cell["windows"].append(row["index"])
    return stats


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
def build_report(scan: LogScan, marks: MarksScan, *, window_s: float = DEFAULT_WINDOW_S,
                 source_device: str | None = None, log_tz: tzinfo = UTC,
                 wake_only_phrases: tuple[str, ...] | frozenset[str] | None
                 = DEFAULT_WAKE_ONLY_PHRASES) -> dict:
    device_order = list(scan.device_order)
    if marks.usable:
        for row in marks.rows:
            if row.device_id not in device_order:
                device_order.append(row.device_id)
    devices = {device: _device_block(scan, device) for device in device_order}
    windows = _window_block(scan, marks, window_s=window_s,
                            source_device=source_device, log_tz=log_tz,
                            wake_only_phrases=frozenset(wake_only_phrases or ()))
    derived = _derived_cell_stats(windows)
    cells = _cell_block(marks, device_order, derived)
    unattributed = _unattributed_block(scan)
    reconcile = _reconcile(devices, cells, device_order)
    todo = _todo_block(scan, marks, cells, devices, device_order, windows)
    evidence = _evidence_block(devices, cells, unattributed, windows, scan)
    return {
        "log": {
            "path": scan.path,
            "lines": scan.lines_total,
            "blank": scan.blank,
            "prefixed": scan.prefixed,
            "recognised": scan.parsed,
            "relay": {
                "value": scan.relayed,
                "by_inner_logger": dict(sorted(scan.relay_loggers.items())),
                "lines": scan.relay_lines,
            },
            "trace_duplicates": {
                "value": len(scan.duplicate_traces),
                "lines": [{"line": line, "first_line": first}
                          for line, first in scan.duplicate_traces],
            },
            "untimed": {
                "value": sum(len(lines) for lines in windows.get("untimed", {}).values()),
                "devices": {device: lines
                            for device, lines in windows.get("untimed", {}).items()},
            },
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
        "windows": windows,
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


def _empty_cell() -> dict:
    return {"attempts": 0, "heard_ok": 0, "unknown_ok": 0, "lines": [],
            "untimed": 0, "first_ts": None, "last_ts": None, "notes": []}


def _cell_block(marks: MarksScan, device_order: list[str],
                derived: dict[tuple[str, str], dict] | None = None) -> dict:
    """phrase_id -> {device -> attempts / heard_ok / unknown / lines / span}.

    ``heard_ok`` / ``unknown_ok`` still count the *human* column, exactly as before.
    ``derived`` (from time windows) is attached per cell so the matrix can print the
    log verdict when the whole run left heard_ok empty. The two are never mixed: the
    report states which mode it is in (``verdict_mode``).
    """
    cells: dict[str, dict[str, dict]] = {
        phrase: {} for phrase in PHRASE_IDS
    }
    unknown_phrases: dict[str, list[int]] = defaultdict(list)
    for row in marks.rows:
        if row.phrase_id not in cells:
            unknown_phrases[row.phrase_id].append(row.lineno)
            continue
        cell = cells[row.phrase_id].setdefault(row.device_id, _empty_cell())
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
                per_device.setdefault(device, _empty_cell())
    empty_derived = {"success": 0, "fail": 0, "unknown": 0, "human": 0,
                     "trace_only": 0, "windows": []}
    derived = derived or {}
    for phrase in cells:
        for device, cell in cells[phrase].items():
            cell["derived"] = dict(derived.get((phrase, device), empty_derived))
            cell["derived"]["windows"] = list(cell["derived"]["windows"])
    no_human = not any(row.heard_ok is not None for row in marks.rows)
    verdict_mode = "log" if no_human and derived else "human"
    return {
        "usable": marks.usable,
        "heard_ok_column": "heard_ok" not in marks.missing_columns if marks.usable else None,
        "verdict_mode": verdict_mode,
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
        "kws_wake": {
            "value": len(scan.kws_wake),
            "keywords": [{"count": count, "keyword": keyword, "lines": lines}
                         for keyword, count, lines in _kws_keywords(scan)],
            "lines": [line for line, _keyword in scan.kws_wake],
        },
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
                device_order: list[str], windows: dict | None = None) -> list[str]:
    todo: list[str] = []
    windows = windows or {"rows": [], "notes": []}
    if windows["rows"]:
        todo.append(
            "句级归属只靠 marks.ts 时间窗 + device（窗口定义 `[ts_i, ts_{i+1})`）：日志没有 "
            "phrase_id，同一窗口内若该设备还响应了别的句子，无法区分；"
            "每个窗口的判定与行号见 §2b / §6。"
        )
    else:
        todo.append(
            "句级日志指标（唤醒成功率/首字延迟/打断/停播/gap/ASR 错误）无法从日志归属到某一句："
            "日志行只带 device_id（wake candidate / rui.trace turn），没有 phrase_id；"
            "提供带 ts 的 marks（fleet_say 自动生成）后才能做时间窗对齐。"
        )
    todo.append(
        "误唤醒：日志区分不了「没人说话却触发」与真唤醒，本工具不生成该数字，只能人工标记。"
    )
    todo.append(
        "admitted/candidates 的分母只含「已产生候选」的尝试；一次候选都没有的漏唤醒不在日志里，"
        "真实唤醒率请用 §2 的尝试数（人工标记或时间窗判定，报告会注明是哪一种）。"
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
            f"marks 有 {untimed} 行 ts 为空或无法解析：这些回合没有时间点，"
            "无法与日志窗口对齐（行号见 §6 的 `marks L…`）。"
        )
    for note in windows.get("notes") or []:
        todo.append(note)
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
    if scan.relayed:
        inner = "、".join(f"{name}×{count}"
                          for name, count in sorted(scan.relay_loggers.items()))
        todo.append(
            f"`plane:` 转发行 {scan.relayed} 行已按内层 app 行解析（内层 ts/logger 优先，"
            f"外层 ts 仅兜底；内层 logger：{inner}）。内层 `__main__` 是 plane 入口"
            f"（audio/process.py 以 `-m` 启动），已映射为 rui.audio.process。"
        )
    if scan.kws_wake:
        todo.append(
            f"KWS `wake:` 行 {len(scan.kws_wake)} 条只证明关键词命中，不是 wake candidate，"
            "没有 device_id，未计入任何格子，也不与 admitted/成功数相加。"
        )
    if scan.duplicate_traces:
        todo.append(
            f"有 {len(scan.duplicate_traces)} 行 turn trace 与前面同 (device, turn_id)，"
            "已按首行去重（行号见 §6），同一 turn 不会被计两次。"
        )
    return todo


def _evidence_block(devices: dict, cells: dict, unattributed: dict,
                    windows: dict | None = None,
                    scan: LogScan | None = None) -> list[dict]:
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
    for row in (windows or {}).get("rows", []):
        label = PHRASE_LABEL.get(row["phrase_id"], row["phrase_id"])
        if row["wake_only"]:
            label += "（wake-only）"
        scope = f"{label}×{row['device'] or '—'}（R{row['round'] or '?'} #{row['index']}）"
        log_lines = sorted(set(row["wake_lines"]) | set(row["rejected_lines"])
                           | set(row["turn_lines"]))
        add(scope, f"窗口 {row['window_start']}–{row['window_end']}"
                   f"（{row['window_end_kind']}）判定 {row['verdict']}：{row['reason']}",
            log_lines)
        add(scope, f"窗口起点 marks L{row['mark_line']}（ts={row['mark_ts']}）",
            [row["mark_line"]], file="marks")
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
    add("fleet", "plane: 转发内层行", (scan.relay_lines if scan else []))
    add("fleet", "KWS wake 行（关键词命中，不是候选）", unattributed["kws_wake"]["lines"])
    if scan is not None:
        untimed = _untimed_lines(scan)
        add("fleet", "无时间戳的 wake/turn 行（无法归属窗口）",
            sorted(line for lines in untimed.values() for line in lines))
    add("fleet", "重复 turn trace（同 device+turn_id，已去重）",
        [line for line, _first in (scan.duplicate_traces if scan else [])])
    add("fleet", "turn trace JSON 无法解析", unattributed["malformed_trace"]["lines"])
    add("fleet", "turn 结果 JSON 无法解析", unattributed["malformed_result"]["lines"])
    add("fleet", f"未识别行（只列前 {UNPARSED_SAMPLE} 行）", unattributed["unparsed"]["lines"])
    return rows


# ------------------------------------------------------------------------- render
def _render_windows(add, report: dict) -> None:
    """§2b: every window, its rule, its verdict and the lines behind it."""
    windows = report["windows"]
    add("## 2b. 时间窗对齐（heard_ok 由日志判定）")
    add("")
    add(f"日志时区：{windows['log_tz']}（--log-tz）；窗口边界按该时区显示。")
    add("")
    add("窗口定义：同一 marks 文件按 ts 排序，第 i 个窗口 = `[ts_i, ts_{i+1})`；"
        f"最后一个窗口用 `--window={windows['window_s']:g} s` 封口。"
        "窗口内只按时间与 device 过滤，不按「第几条 wake 行」猜；相邻窗口不重叠。")
    add("")
    add("判定规则（表头口径）：")
    add("")
    add(f"- `success`：窗口内有该设备 `wake candidate … admitted`，且窗口内该设备有 turn 行带 "
        f"`{WINDOW_RESULT_STAGES[0]}` 或 `{WINDOW_RESULT_STAGES[1]}`"
        "（trace.py:27-39 的阶段名；任务卡里的 `tts_play_start` 对应 trace 的 `play_start`）。")
    add("- `fail`：有 wake 行但没有 admitted（按 rejected reason 归因，含 `admit_timeout`），"
        "或 admitted 后窗口内没有 turn 结果。")
    add("- `unknown`：窗口内该设备**没有任何 wake 行**（日志断档/未覆盖/未命中）"
        "——不判 fail，也不进入成功分母。")
    add("- `heard_ok`：marks 里人工值非空时原样保留并标 `human`；否则取日志判定"
        "（success→1 / fail→0 / unknown→—）。")
    if windows["wake_only_phrases"]:
        add(f"- 仅测唤醒（wake-only）句：`{'`, `'.join(windows['wake_only_phrases'])}` "
            "—— success = 窗口内有 `wake candidate … admitted`（**不要求 turn**）；"
            "fail = 有 candidate 但全被拒（按 reason 归因）；unknown = 没有 candidate。")
    add("")
    if not windows["rows"]:
        add("没有可比对的窗口 → 不做时间窗判定（证据不足，不是 fail）。")
        add("")
        return
    if windows["multi_device"]:
        add("模式：单音源多设备（同一窗口对每台在线设备分别判定）。")
        add("")
    for note in windows.get("notes") or []:
        add(f"> {note}")
    add("")
    add("| # | 句 | 轮 | marks ts | 窗口（" + windows["log_tz"] + "） | 设备 | 日志判定 | "
        "归因 | heard_ok | 来源 | 证据 |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in windows["rows"]:
        log_lines = sorted(set(row["wake_lines"]) | set(row["rejected_lines"])
                           | set(row["turn_lines"]))
        ref = ranges(log_lines) if log_lines else "—"
        ref += f" / marks L{row['mark_line']}"
        heard = "—" if row["heard_ok"] is None else ("1" if row["heard_ok"] else "0")
        label = PHRASE_LABEL.get(row["phrase_id"], row["phrase_id"])
        if row["wake_only"]:
            label += "（wake-only）"
        add(f"| {row['index']} | {label} | {row['round'] or '—'} | {row['mark_ts']} | "
            f"{row['window_start']}–{row['window_end']} | {row['device'] or '—'} | "
            f"{row['verdict']} | {row['reason']} | {heard} | {row['source']} | {ref} |")
    add("")
    add("按设备汇总：")
    add("")
    add("| 设备 | 窗口 | success | fail | unknown | 有 turn 无 wake | 人工 heard_ok | 证据 |")
    add("|---|---|---|---|---|---|---|---|")
    for item in windows["summary"]:
        ref = ranges(item["lines"]) if item["lines"] else "—"
        add(f"| {item['device'] or '—'} | {item['windows']} | {item['success']} | "
            f"{item['fail']} | {item['unknown']} | {item['trace_only']} | "
            f"{item['human']} | marks {ref} |")
    trace_only = sum(item["trace_only"] for item in windows["summary"])
    if trace_only:
        add("")
        add(f"> 有 {trace_only} 个窗口「有该设备的 turn 结果但没有 wake 行」"
            "（跟随时段/唤醒门控）：按规则记 unknown，不判 fail，也不计入成功分母。")
    totals = windows["totals"]
    add("")
    add(f"合计：success {totals.get('success', 0)} / fail {totals.get('fail', 0)} / "
        f"unknown {totals.get('unknown', 0)}。unknown = 窗口内没有该设备的 wake 行"
        "（日志断档，或跟随时段只有 turn 结果）；它不是「设备没响应」，也不判 fail。")
    add("")


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
    relay = log["relay"]
    if relay["value"]:
        inner = "、".join(f"{name}×{count}"
                          for name, count in relay["by_inner_logger"].items())
        add(f"- `plane:` 转发行：{relay['value']} 行（内层 logger：{inner}；"
            "按内层 ts/logger 解析，外层 ts 仅兜底）")
    duplicates = log["trace_duplicates"]
    if duplicates["value"]:
        add(f"- 重复 turn trace：{duplicates['value']} 行（同 device+turn_id，已去重）")
    windows = report["windows"]
    add(f"- 日志时区：{windows['log_tz']}（--log-tz）")
    add(f"- 时间窗：{windows['window_s']:g} s（--window；最后一个窗口的封口长度，"
        "判定规则见 §2b）")
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

    mode = report["cells"].get("verdict_mode", "human")
    if mode == "log":
        add("## 2. 逐句矩阵（heard_ok 由日志时间窗判定）")
    else:
        add("## 2. 逐句矩阵（人工 heard_ok / 尝试数）")
    add("")
    columns = report["device_order"]
    if mode == "log" and windows.get("judged_devices"):
        # A playback-source id (e.g. `speaker` from fleet_say) is not a device under
        # test: it appears in §1/reconcile as a log-less row, never as a matrix column.
        columns = list(windows["judged_devices"])
    if not columns:
        add("日志和 marks 里都没有可判定的设备 → 矩阵没有列可填（证据不足）。")
        add("")
    elif not marks["usable"]:
        add("人工标记不可用 → 整张表 TODO（`--marks` 缺文件或表头不全，见 §4）。")
        add("")
        add("| 句 | phrase_id | " + " | ".join(columns) + " | 日志侧句级 |")
        add("|---|---|" + "---|" * len(columns) + "---|")
        for phrase in PHRASE_IDS:
            add(f"| {PHRASE_LABEL[phrase]} | `{phrase}` | "
                + " | ".join(["TODO"] * len(columns))
                + " | 证据不足 |")
    else:
        tail = "见 §2b" if mode == "log" else "证据不足"
        add("| 句 | phrase_id | " + " | ".join(columns) + " | 日志侧句级 |")
        add("|---|---|" + "---|" * len(columns) + "---|")
        for phrase in PHRASE_IDS:
            cells = cells_line(report, phrase, columns)
            add(f"| {PHRASE_LABEL[phrase]} | `{phrase}` | {cells} | {tail} |")
        totals = cells_totals(report, columns)
        add("| **合计** | — | " + " | ".join(totals) + " | — |")
        add("")
        if mode == "log":
            add("> 本表 `heard_ok` 由日志时间窗判定（`verdict_mode=log`）：格子 = "
                "`success/（success+fail）`；`unknown` 是窗口内没有该设备 wake 行的句数"
                "（日志断档/未覆盖），不计入分母、不算失败。wake-only 句（默认 hello）"
                "只要求 admitted、不要求 turn，规则与逐行证据见 §2b。")
        else:
            add("> 格子 = `heard_ok成功数/标记尝试数`；`（?n）` = n 行 heard_ok 为空/无法判定，"
                "不计入成功数。人工格子的行号见 §6；日志侧句级归属见 §2b。")
    add("")

    _render_windows(add, report)

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
    add(f"| `plane:` 转发行（按内层 app 行解析） | {relay['value']} |")
    kws = unattributed["kws_wake"]
    kws_text = _fmt_count(kws)
    if kws["value"]:
        top = "、".join(f"{item['keyword']}×{item['count']}" for item in kws["keywords"])
        kws_text += f"（{top}；关键词命中，不是候选，也不与 admitted 相加）"
    add(f"| KWS `wake:` 行 | {kws_text} |")
    add(f"| 重复 turn trace（已去重） | {_fmt_count(duplicates)} |")
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


def cells_line(report: dict, phrase: str, devices: list[str] | None = None) -> str:
    parts: list[str] = []
    mode = report["cells"].get("verdict_mode", "human")
    for device in (report["device_order"] if devices is None else devices):
        cell = report["cells"]["cells"].get(phrase, {}).get(device)
        parts.append(_cell_text(cell, report["cells"]["heard_ok_column"], mode))
    return " | ".join(parts)


def cells_totals(report: dict, devices: list[str] | None = None) -> list[str]:
    parts: list[str] = []
    mode = report["cells"].get("verdict_mode", "human")
    for device in (report["device_order"] if devices is None else devices):
        attempts = ok = unknown = 0
        derived = {"success": 0, "fail": 0, "unknown": 0, "human": 0,
                   "trace_only": 0, "windows": 0}
        for phrase in PHRASE_IDS:
            cell = report["cells"]["cells"].get(phrase, {}).get(device)
            if not cell:
                continue
            attempts += cell["attempts"]
            ok += cell["heard_ok"]
            unknown += cell["unknown_ok"]
            for key in derived:
                if key == "windows":
                    derived[key] += len(cell.get("derived", {}).get("windows", []))
                else:
                    derived[key] += cell.get("derived", {}).get(key, 0)
        if report["cells"]["heard_ok_column"] is False:
            parts.append(f"证据不足（{attempts} 行标记）")
        elif mode == "log":
            # In multi-device mode the mark rows are attributed to the *source* id, so
            # `attempts` can be 0 while the window judgements for this device exist.
            judged = derived["success"] + derived["fail"]
            if not derived["windows"]:
                parts.append("无标记" if not attempts
                             else f"证据不足（{attempts} 行标记没有可对齐的窗口）")
            elif not judged:
                extra = (f"；其中 {derived['trace_only']} 个有 turn 结果"
                         if derived.get("trace_only") else "")
                parts.append(f"证据不足（{derived['windows']} 个窗口内无 wake 行{extra}）")
            else:
                text = f"{derived['success']}/{judged}"
                if derived["unknown"]:
                    text += f"（unknown {derived['unknown']} 不计失败）"
                parts.append(text)
        elif not attempts:
            parts.append("无标记")
        elif unknown == attempts:
            parts.append(f"证据不足（heard_ok 全空；{attempts} 行标记）")
        else:
            text = f"{ok}/{attempts}"
            if unknown:
                text += f"（?{unknown}）"
            parts.append(text)
    return parts


def _cell_text(cell: dict | None, heard_ok_column: bool | None,
               verdict_mode: str = "human") -> str:
    if cell is None:
        return "无标记"
    attempts = cell.get("attempts", 0)
    if verdict_mode == "log":
        if heard_ok_column is False:
            return f"证据不足（heard_ok 列缺失；{attempts} 行标记）"
        derived = cell.get("derived") or {}
        windows = len(derived.get("windows", []))
        judged = derived.get("success", 0) + derived.get("fail", 0)
        if windows and judged:
            text = f"{derived['success']}/{judged}"
            if derived.get("unknown"):
                text += f"（unknown {derived['unknown']} 不计失败）"
            if derived.get("human"):
                text += f"（人工覆盖 {derived['human']}）"
            return text
        if windows:
            extra = (f"；其中 {derived['trace_only']} 个有 turn 结果"
                     if derived.get("trace_only") else "")
            return f"证据不足（{windows} 个窗口内无 wake 行{extra}；unknown 不计失败）"
        if attempts:
            return f"证据不足（{attempts} 行标记没有可对齐的窗口）"
        return "无标记"
    if not attempts:
        return "无标记"
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
    parser.add_argument("--wake-only-phrase",
                        default=",".join(DEFAULT_WAKE_ONLY_PHRASES),
                        help="逗号分隔的仅测唤醒句（默认 hello；success 只要求 "
                             "admitted、不要求 turn；传空串 '' 关闭该语义）")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_S,
                        help="seconds used to close the last time window "
                             f"(default {DEFAULT_WINDOW_S:g}); intermediate windows end "
                             "at the next marks ts")
    parser.add_argument("--source-device", default=None,
                        help="marks device_id that is the playback source, not a device "
                             "under test: every online device is judged in its windows")
    parser.add_argument("--log-tz", default=DEFAULT_LOG_TZ,
                        help="timezone the log's asctime is written in: UTC (default), "
                             "an IANA name such as Asia/Shanghai, or +08:00")
    parser.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    args = parser.parse_args(argv)

    if args.window <= 0:
        parser.error("--window must be > 0 seconds")
    try:
        log_tz = log_tzinfo(args.log_tz)
    except (ZoneInfoNotFoundError, ValueError):
        print(f"fleet_matrix: unknown --log-tz {args.log_tz!r} "
              "(use UTC, an IANA name like Asia/Shanghai, or +08:00)", file=sys.stderr)
        return 2

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

    wake_only = tuple(part.strip() for part in args.wake_only_phrase.split(",")
                      if part.strip())
    report = build_report(scan, marks, window_s=args.window,
                          source_device=args.source_device, log_tz=log_tz,
                          wake_only_phrases=wake_only)
    print(render_json(report) if args.json else render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
