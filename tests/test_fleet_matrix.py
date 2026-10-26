"""Tests for ``scripts/fleet_matrix.py`` (task-6 sampling tool).

Two things are being defended here:
  1. the parser really reads the line shapes rui logs (audio/process.py, trace.py,
     audio/vad.py, providers/chain.py) and survives the shapes it does not know;
  2. no number in the matrix exists without a line behind it - a metric the fixture
     cannot prove has to stay empty ("证据不足") instead of turning into 0.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fleet_matrix.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# `.log` is gitignored (.gitignore:12); keep the sampled log under `.log.txt`
# so the fixture travels with the test instead of only existing on this machine.
SAMPLE_LOG = FIXTURES / "fleet-matrix-sample.log.txt"
SAMPLE_MARKS = FIXTURES / "fleet-matrix-marks.csv"


def _load_module():
    spec = importlib.util.spec_from_file_location("fleet_matrix_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses looks the module up in sys.modules while executing it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fm = _load_module()


# --------------------------------------------------------------------- helpers
def proc(msg: str, ts: str = "2026-10-26 10:00:00,000") -> str:
    return f"{ts} INFO rui.audio.process: {msg}"


def vad(msg: str, ts: str = "2026-10-26 10:00:00,000") -> str:
    return f"{ts} INFO rui.audio.vad: {msg}"


def trace(entry: dict, ts: str = "2026-10-26 10:00:00,000") -> str:
    return f"{ts} INFO rui.trace: turn {json.dumps(entry)}"


def result(entry: dict, ts: str = "2026-10-26 10:00:00,000") -> str:
    return f"{ts} INFO rui.web.api.app: turn: {json.dumps(entry)}"


def write_log(tmp_path: Path, lines: list[str], name: str = "rui.log") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_marks(tmp_path: Path, text: str, name: str = "marks.csv") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def scan_and_build(log_path: Path, marks_path: Path | None = None) -> dict:
    scan = fm.parse_log(log_path)
    marks = fm.parse_marks(marks_path) if marks_path is not None else fm.MarksScan()
    return fm.build_report(scan, marks)


def ref_lines(ref: str) -> list[int]:
    """'L2, L4-L6' -> [2, 4, 5, 6] (stop at the first non-L token)."""
    numbers: list[int] = []
    for part in ref.split(","):
        part = part.strip()
        if not part.startswith("L"):
            continue
        body = part[1:]
        if "-L" in body:
            first, last = body.split("-L", 1)
            numbers.extend(range(int(first), int(last) + 1))
        else:
            numbers.append(int(body))
    return numbers


def evidence(report: dict, scope: str, metric_prefix: str) -> dict:
    for row in report["evidence"]:
        if row["scope"] == scope and row["metric"].startswith(metric_prefix):
            return row
    raise AssertionError(f"no evidence row for {scope}/{metric_prefix}: "
                         f"{[(r['scope'], r['metric']) for r in report['evidence']]}")


def evidence_ref(row: dict) -> str:
    """The markdown rendering of one evidence row (JSON keeps the full list)."""
    ref = fm.ranges(row["lines"]) if row["lines"] else "—"
    if row["file"] == "marks" and row["lines"]:
        ref = "marks " + ref
    return ref


def section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start:]
    return rest.split("\n## ", 1)[0]


# --------------------------------------------------------------- sample fixture
def test_sample_fixture_device_numbers_are_the_provable_ones() -> None:
    report = scan_and_build(SAMPLE_LOG)

    lx06 = report["devices"]["lx06"]
    assert lx06["wake_candidates"]["value"] == 5
    assert lx06["admitted"]["value"] == 4
    assert lx06["rejected"]["by_reason"]["turn_open"]["count"] == 1
    assert lx06["timeouts"]["value"] == 0
    assert lx06["turns"]["value"] == 4
    assert lx06["first_response_ms"]["median"] == 2610.4
    assert lx06["first_response_ms"]["p90"] == 2742.5
    assert lx06["first_response_ms"]["n"] == 3
    assert lx06["first_response_ms"]["skipped"] == 1
    assert lx06["barge_turns"]["value"] == 0
    assert lx06["gaps"]["turns"] == 1
    assert lx06["gaps"]["total"] == 2
    assert lx06["gaps"]["max_ms"] == 120.5
    assert lx06["gaps"]["known"] == 1
    assert lx06["gaps"]["unknown"] == 3
    assert "另 3 个 turn 无该字段" in fm.render_markdown(report)
    assert lx06["asr_missing_turns"]["value"] == 1

    dot2 = report["devices"]["dot2"]
    assert dot2["wake_candidates"]["value"] == 5
    assert dot2["admitted"]["value"] == 2
    assert dot2["rejected"]["by_reason"]["cross_echo"]["count"] == 1
    assert dot2["rejected"]["by_reason"]["turn_open"]["count"] == 1
    assert dot2["timeouts"]["value"] == 1
    assert dot2["first_response_ms"]["median"] == 3060.3
    assert dot2["first_response_ms"]["p90"] == 3100.0
    assert dot2["barge_turns"]["value"] == 1
    assert dot2["stop_after_ms"]["median"] == 96.0
    # dot2 carries an explicit "playback_gap_count": 0 on all three traces, so 0 is proven.
    assert dot2["gaps"]["turns"] == 0
    assert dot2["gaps"]["total"] == 0
    assert dot2["gaps"]["max_ms"] is None
    assert dot2["gaps"]["known"] == 3
    assert dot2["gaps"]["unknown"] == 0
    assert dot2["gaps"]["why"] is None


def test_sample_fixture_fleet_numbers_are_the_provable_ones() -> None:
    report = scan_and_build(SAMPLE_LOG)
    fleet = report["unattributed"]
    assert fleet["barge"]["value"] == 3
    assert fleet["barge_stop_paired"]["n"] == 2
    assert fleet["barge_stop_paired"]["median"] == 54.0
    assert fleet["barge_stop_paired"]["p90"] == 60.0
    assert fleet["barge_stop_unpaired"]["value"] == 1
    assert fleet["seg"]["value"] == 3
    assert fleet["seg"]["speech_s"] == 3.21
    assert fleet["discarded"]["value"] == 1
    assert fleet["reply_gaps"]["total_gaps"] == 2
    assert fleet["reply_gaps"]["max_ms"] == 120
    assert fleet["stage_failures"]["asr"]["value"] == 1
    assert fleet["stage_failures"]["llm"]["value"] == 0
    assert fleet["turn_failed"]["value"] == 1
    assert fleet["plane_degraded"]["value"] == 1
    assert fleet["capture_recovered"]["value"] == 1
    assert fleet["capture_retry"]["value"] == 1
    assert fleet["wake_no_speech"]["value"] == 1
    assert fleet["no_control_plane"]["value"] == 1
    assert fleet["result_turns"]["value"] == 3
    assert fleet["result_turns"]["errors"][0]["error"] == "asr: no ready provider"
    assert report["log"]["lines"] == 44
    assert report["log"]["unparsed"]["value"] == 2


def test_sample_fixture_markdown_keeps_the_honest_columns() -> None:
    report = scan_and_build(SAMPLE_LOG, SAMPLE_MARKS)
    text = fm.render_markdown(report)
    row_pattern = re.compile(r"^\| ([①②③④⑤⑥][^|]*) \| `([a-z]+)` \|")
    rows = {match.group(1): line for line in text.splitlines()
            if (match := row_pattern.match(line))}
    assert len(rows) == 6
    assert rows["①你好小瑞"].strip() == "| ①你好小瑞 | `hello` | 2/2 | 1/2（?1） | 证据不足 |"
    assert rows["③放首歌"].strip() == "| ③放首歌 | `play` | 1/2 | 0/2 | 证据不足 |"
    assert rows["⑤停止播放"].strip() == "| ⑤停止播放 | `stop` | 2/2 | 1/2 | 证据不足 |"
    assert "| **合计** | — | 11/12 | 6/12（?2） | — |" in text
    assert "6. 证据索引" in text
    # The phrase-level log column can never be filled from this log.
    assert all(row.rstrip().endswith("| 证据不足 |") for row in rows.values())


# ------------------------------------------------------------- interleaved devs
def test_interleaved_devices_are_not_cross_attributed(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        proc("wake candidate lx06-100 admitted after 4 ms"),
        proc("wake candidate dot2-101 admitted after 6 ms"),
        proc("wake candidate lx06-102 rejected: turn_open"),
        proc("wake candidate dot2-103 rejected: lost_same_word"),
        proc("wake candidate lx06-104 admitted after 9 ms"),
    ])
    report = scan_and_build(path)
    assert report["device_order"] == ["lx06", "dot2"]
    assert report["devices"]["lx06"]["wake_candidates"]["value"] == 3
    assert report["devices"]["lx06"]["admitted"]["value"] == 2
    assert list(report["devices"]["lx06"]["rejected"]["by_reason"]) == ["turn_open"]
    assert report["devices"]["dot2"]["wake_candidates"]["value"] == 2
    assert report["devices"]["dot2"]["admitted"]["value"] == 1
    assert list(report["devices"]["dot2"]["rejected"]["by_reason"]) == ["lost_same_word"]
    assert report["devices"]["lx06"]["rejected"]["by_reason"]["turn_open"]["lines"] == [3]
    assert report["devices"]["dot2"]["rejected"]["by_reason"]["lost_same_word"]["lines"] == [4]


# ------------------------------------------------------- missing prefix/multiline
def test_prefixless_line_and_multiline_noise_do_not_break_the_scan(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        "wake candidate dot2-200 rejected: turn_open",          # 1: no ts/level/logger
        "Traceback (most recent call last):",                   # 2: multi-line noise
        '  File "/opt/rui/src/rui/audio/process.py", line 1',   # 3: continuation
        proc("wake candidate lx06-201 admitted after 5 ms"),    # 4
        "",                                                     # 5: blank
        proc("wake candi"),                                     # 6: truncated / unknown
    ])
    report = scan_and_build(path)
    assert report["log"]["lines"] == 6
    assert report["log"]["blank"] == 1
    assert report["log"]["unparsed"]["value"] == 3
    assert report["log"]["unparsed"]["lines"] == [2, 3, 6]
    assert report["devices"]["dot2"]["wake_candidates"]["value"] == 1
    assert report["devices"]["dot2"]["rejected"]["by_reason"]["turn_open"]["lines"] == [1]
    assert report["devices"]["lx06"]["admitted"]["lines"] == [4]


def test_docker_timestamp_and_compose_prefix_are_tolerated(tmp_path: Path) -> None:
    """The capture command does not have to be exact: -t and compose both work."""
    path = write_log(tmp_path, [
        "rui-1  | 2026-10-26 10:00:00,000 INFO rui.audio.process: "
        "wake candidate lx06-1500 admitted after 5 ms",
        "2026-10-26T02:00:01.123456789Z 2026-10-26 10:00:01,000 INFO "
        "rui.audio.process: wake candidate lx06-1501 rejected: turn_open",
        "rui-1  | 2026-10-26T02:00:02.000000000Z 2026-10-26 10:00:02,000 INFO "
        "rui.audio.process: wake candidate lx06-1502 admitted after 6 ms",
    ])
    report = scan_and_build(path)
    assert report["log"]["unparsed"]["value"] == 0
    assert report["devices"]["lx06"]["admitted"]["value"] == 2
    assert report["devices"]["lx06"]["rejected"]["by_reason"]["turn_open"]["count"] == 1


def test_malformed_json_is_counted_and_does_not_crash(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        '2026-10-26 10:00:00,000 INFO rui.trace: turn {"device_id": "lx06", ',
        '2026-10-26 10:00:00,000 INFO rui.web.api.app: turn: {"transcript": "下',
        '2026-10-26 10:00:00,000 INFO rui.trace: turn {"device_id": 7}',
        trace({"device_id": "lx06", "first_response_ms": 100.0,
               "stages_ms": {"wake": 0.0, "play_start": 100.0}}),
    ])
    report = scan_and_build(path)
    assert report["unattributed"]["malformed_trace"]["value"] == 2
    assert report["unattributed"]["malformed_result"]["value"] == 1
    assert report["devices"]["lx06"]["turns"]["value"] == 1
    assert report["devices"]["lx06"]["first_response_ms"]["median"] == 100.0


# ------------------------------------------------------------------ unknown bits
def test_unknown_reject_reason_is_aggregated_not_dropped(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        proc("wake candidate lx06-300 rejected: weird_reason_xyz"),
        proc("wake candidate lx06-301 rejected: weird_reason_xyz"),
        proc("wake candidate lx06-302 rejected: turn_open"),
    ])
    report = scan_and_build(path)
    reasons = report["devices"]["lx06"]["rejected"]["by_reason"]
    assert reasons["weird_reason_xyz"]["count"] == 2
    assert reasons["weird_reason_xyz"]["lines"] == [1, 2]
    assert reasons["turn_open"]["count"] == 1
    text = fm.render_markdown(report)
    assert "weird_reason_xyz×2" in text


def test_unknown_phrase_id_in_marks_is_reported(tmp_path: Path) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-400 admitted after 5 ms")])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00,1,\n"
        "hello2,lx06,1,2026-10-26T10:01:00,0,\n",
    )
    report = scan_and_build(log, marks)
    assert report["cells"]["unknown_phrases"]["hello2"]["value"] == 1
    assert report["cells"]["unknown_phrases"]["hello2"]["lines"] == [3]
    assert any("hello2" in item for item in report["todo"])


# -------------------------------------------------------------------- evidence
def test_evidence_index_names_the_real_lines(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        "2026-10-26 10:00:00,000 INFO rui.log_setup: ready",                  # 1
        proc("wake candidate lx06-500 admitted after 5 ms"),                  # 2
        proc("wake candidate lx06-501 rejected: turn_open"),                  # 3
        proc("wake candidate lx06-502 admitted after 7 ms"),                  # 4
        vad("[seg] 1.00 s rms=0.0100 peak=0.1000 speech=0.90 s"),             # 5
    ])
    report = scan_and_build(path)
    assert evidence_ref(evidence(report, "lx06", "wake candidate")) == "L2-L4"
    assert evidence_ref(evidence(report, "lx06", "admitted")) == "L2, L4"
    assert evidence_ref(evidence(report, "lx06", "rejected: turn_open")) == "L3"
    assert evidence_ref(evidence(report, "fleet", "[seg]")) == "L5"

    raw = path.read_text(encoding="utf-8").splitlines()
    for ref, token in (("L2, L4", "admitted"), ("L3", "rejected")):
        for number in ref_lines(ref):
            raw_line = raw[number - 1]
            assert "wake candidate" in raw_line, raw_line
            assert token in raw_line, raw_line
    for number in ref_lines(evidence_ref(evidence(report, "fleet", "[seg]"))):
        assert "[seg]" in raw[number - 1]


def test_barge_pair_needs_literal_adjacency(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        proc("barge: 1 wake during playback, dropped 1.0 s queued"),           # 1
        proc("barge: silence_after_ms=48 (server-side: queue flushed)"),       # 2
        proc("barge: 2 wake during playback, dropped 2.0 s queued"),           # 3
        vad("[seg] 1.00 s rms=0.0100 peak=0.1000 speech=0.90 s"),              # 4
        proc("barge: silence_after_ms=1200 (server-side: queue flushed)"),     # 5
    ])
    report = scan_and_build(path)
    assert report["unattributed"]["barge"]["value"] == 2
    assert report["unattributed"]["barge_stop_paired"]["n"] == 1
    assert report["unattributed"]["barge_stop_paired"]["lines"] == [2]
    assert report["unattributed"]["barge_stop_unpaired"]["value"] == 1
    assert report["unattributed"]["barge_stop_unpaired"]["lines"] == [5]


def test_fleet_barge_lines_are_never_attributed_to_a_device(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        proc("wake candidate lx06-600 admitted after 5 ms"),                   # 1
        trace({"device_id": "lx06", "first_response_ms": 900.0,                # 2
               "stages_ms": {"wake": 0.0, "play_start": 900.0}}),
        proc("barge: 1 wake during playback, dropped 1.0 s queued"),           # 3
        proc("barge: silence_after_ms=48 (server-side: queue flushed)"),       # 4
    ])
    report = scan_and_build(path)
    assert report["devices"]["lx06"]["barge_turns"]["value"] == 0
    # 3/4 sit at fleet level; the device evidence row stays empty.
    assert evidence_ref(evidence(report, "lx06", "barge turn")) == "—"
    assert report["unattributed"]["barge"]["lines"] == [3]
    assert report["unattributed"]["barge_stop_paired"]["lines"] == [4]


# --------------------------------------------------- honesty: no invented numbers
def test_missing_markers_yield_evidence_insufficient_not_zero(tmp_path: Path) -> None:
    """A wake with no play_start cannot become a 0 ms first response."""
    path = write_log(tmp_path, [
        proc("wake candidate lx06-700 admitted after 5 ms"),
        trace({"device_id": "lx06", "first_response_ms": None,
               "stages_ms": {"wake": 0.0}}),
    ])
    report = scan_and_build(path)
    block = report["devices"]["lx06"]["first_response_ms"]
    assert block["median"] is None
    assert block["p90"] is None
    assert block["n"] == 0
    assert block["why"]
    text = fm.render_markdown(report)
    assert "first_response_ms p50/p90" in text
    assert "0.0 ms (n=0)" not in text


def test_device_only_in_marks_has_no_log_numbers(tmp_path: Path) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-800 admitted after 5 ms")])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00,1,\n"
        "hello,dot2,1,2026-10-26T10:00:05,1,\n",
    )
    report = scan_and_build(log, marks)
    assert report["device_order"] == ["lx06", "dot2"]
    dot2 = report["devices"]["dot2"]
    assert dot2["wake_candidates"]["value"] is None
    assert dot2["admitted"]["value"] is None
    assert "日志中没有该设备" in dot2["wake_candidates"]["why"]
    assert any("dot2" in item and "日志里没有" in item for item in report["todo"])


def test_p90_is_nearest_rank_not_interpolated() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert fm.p90(values) == 40.0          # nearest-rank: the 4th sample
    assert fm.p90(values) != 37.0          # the linear-interpolation answer
    assert fm.p90([7.0]) == 7.0
    assert fm.p90([5.0, 9.0]) == 9.0


# ------------------------------------------------------------------------ marks
def test_marks_missing_heard_ok_column_degrades_to_attempts_only(tmp_path: Path) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-900 admitted after 5 ms")])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00,\n"
        "play,lx06,1,2026-10-26T10:01:00,放歌没起来\n",
    )
    report = scan_and_build(log, marks)
    assert report["marks"]["usable"] is True
    assert report["marks"]["missing_columns"] == ["heard_ok"]
    assert report["cells"]["heard_ok_column"] is False
    cell = report["cells"]["cells"]["hello"]["lx06"]
    assert cell["attempts"] == 1
    text = fm.render_markdown(report)
    assert "证据不足（heard_ok 列缺失；1 行标记）" in text
    assert "heard_ok 列缺失" in text


def test_marks_without_device_column_disables_the_matrix(tmp_path: Path) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-1000 admitted after 5 ms")])
    marks = write_marks(
        tmp_path,
        "phrase_id,round,ts,heard_ok,notes\n"
        "hello,1,2026-10-26T10:00:00,1,\n",
    )
    report = scan_and_build(log, marks)
    assert report["marks"]["usable"] is False
    assert "device_id" in report["marks"]["why"]
    text = fm.render_markdown(report)
    assert "整张表 TODO" in text


def test_duplicate_mark_rows_are_reported_not_double_counted(tmp_path: Path) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-1100 admitted after 5 ms")])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00,1,\n"
        "hello,lx06,1,2026-10-26T10:00:00,1,抄了两遍\n",
    )
    report = scan_and_build(log, marks)
    assert report["cells"]["cells"]["hello"]["lx06"]["attempts"] == 1
    assert report["marks"]["duplicates"] == [
        {"line": 3, "first_line": 2, "key": "hello/lx06/1"}
    ]
    assert "重复" in fm.render_markdown(report)


def test_blank_heard_ok_is_unknown_not_failure(tmp_path: Path) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-1200 admitted after 5 ms")])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00,,没记\n"
        "hello,lx06,2,2026-10-26T10:01:00,0,\n",
    )
    report = scan_and_build(log, marks)
    cell = report["cells"]["cells"]["hello"]["lx06"]
    assert cell["attempts"] == 2
    assert cell["heard_ok"] == 0
    assert cell["unknown_ok"] == 1
    assert "0/2（?1）" in fm.render_markdown(report)


# -------------------------------------------------------------------------- cli
def test_cli_is_deterministic_and_json_parses(capsys) -> None:
    report_path = SAMPLE_LOG
    assert fm.main(["--log", str(report_path)]) == 0
    first = capsys.readouterr().out
    assert fm.main(["--log", str(report_path)]) == 0
    second = capsys.readouterr().out
    assert first == second
    assert first.startswith("# rui fleet matrix")

    assert fm.main(["--log", str(report_path), "--marks", str(SAMPLE_MARKS), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["devices"]["lx06"]["admitted"]["value"] == 4
    assert payload["cells"]["cells"]["play"]["lx06"]["attempts"] == 2


def test_cli_without_marks_prints_todo_section(tmp_path: Path, capsys) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-1300 admitted after 5 ms")])
    assert fm.main(["--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "TODO" in out
    assert "未提供 --marks" in out
    assert "| lx06 | 1 | 1 | 0 | 0 | 证据不足 | 证据不足 |" in out
    assert out.rstrip().splitlines()[-1].startswith("| fleet |")


def test_cli_missing_marks_file_falls_back_to_log_only(tmp_path: Path, capsys) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-1400 admitted after 5 ms")])
    assert fm.main(["--log", str(log), "--marks", str(tmp_path / "nope.csv")]) == 0
    captured = capsys.readouterr()
    assert "marks not found" in captured.err
    assert "| lx06 | 1 | 1 | 0 | 0 | 证据不足 | 证据不足 |" in captured.out
    assert "TODO" in captured.out


def test_cli_missing_log_exits_2(tmp_path: Path, capsys) -> None:
    assert fm.main(["--log", str(tmp_path / "nope.log")]) == 2
    assert "log not found" in capsys.readouterr().err


# ---------------------------------------------------------- reverse validation
def test_reverse_guard_harness_self_check() -> None:
    """Harness self-check: these deliberately wrong values are NOT what the tool says.

    This only proves the asserts below are evaluated against real report values; it
    does not mean "any drift fails here". The teeth are the exact positive
    assertions (test_sample_fixture_* and test_evidence_*): they pin the numbers to
    the fixture and to the line they came from.
    """
    report = scan_and_build(SAMPLE_LOG)
    with pytest.raises(AssertionError):
        assert report["devices"]["lx06"]["admitted"]["value"] == 9   # real value: 4
    with pytest.raises(AssertionError):
        assert report["devices"]["lx06"]["admitted"]["value"] == 5   # candidates, not admitted
    with pytest.raises(AssertionError):
        assert report["devices"]["dot2"]["first_response_ms"]["skipped"] == 0  # real: 1
    with pytest.raises(AssertionError):
        assert report["devices"]["lx06"]["first_response_ms"]["median"] == 0.0
    with pytest.raises(AssertionError):
        assert fm.p90([10.0, 20.0, 30.0, 40.0]) == 37.0  # interpolation is not the p90
    with pytest.raises(AssertionError):
        assert report["unattributed"]["barge_stop_unpaired"]["value"] == 0  # real: 1


def test_marks_with_excel_bom_and_crlf_still_match_the_header(tmp_path: Path) -> None:
    """9 哥 will save the CSV from Excel once; a BOM must not hide every column."""
    log = write_log(tmp_path, [proc("wake candidate lx06-1600 admitted after 5 ms")])
    path = tmp_path / "bom.csv"
    path.write_bytes(
        ("phrase_id,device_id,round,ts,heard_ok,notes\r\n"
         "hello,lx06,1,2026-10-26T10:00:00,1,唤醒成功\r\n").encode("utf-8-sig")
    )
    report = scan_and_build(log, path)
    assert report["marks"]["usable"] is True
    assert report["marks"]["missing_columns"] == []
    cell = report["cells"]["cells"]["hello"]["lx06"]
    assert cell["attempts"] == 1
    assert cell["heard_ok"] == 1


def test_empty_log_says_evidence_insufficient_not_zero(tmp_path: Path, capsys) -> None:
    path = write_log(tmp_path, [])
    assert fm.main(["--log", str(path)]) == 0
    out = capsys.readouterr().out
    assert "没有识别出任何设备行" in out
    assert "|  |" not in out
    assert "0.0 ms" not in out


# --------------------------------------------- playback gap: field absent vs zero
def gap_trace(device: str, meta: dict) -> str:
    """A turn with wake+play_start (so first_response is measurable) and a given meta."""
    return trace({"device_id": device, "first_response_ms": 900.0,
                  "silence_after_ms": None,
                  "stages_ms": {"wake": 0.0, "play_start": 900.0}, "meta": meta})


def test_gap_field_missing_is_evidence_insufficient_not_zero(tmp_path: Path) -> None:
    """trace.py:84-85: an absent playback_gap_count is skipped, never recorded as 0."""
    missing = write_log(tmp_path, [gap_trace("lx06", {"wake_source": "rui"})], "missing.log")
    explicit = write_log(
        tmp_path,
        [gap_trace("lx06", {"wake_source": "rui", "playback_gap_count": 0})],
        "explicit.log",
    )
    miss = scan_and_build(missing)["devices"]["lx06"]["gaps"]
    zero = scan_and_build(explicit)["devices"]["lx06"]["gaps"]

    assert miss["turns"] is None
    assert miss["total"] is None
    assert miss["known"] == 0
    assert miss["unknown"] == 1
    assert miss["why"] and "playback_gap_count" in miss["why"]

    assert zero["turns"] == 0          # provable: the trace says 0
    assert zero["total"] == 0
    assert zero["known"] == 1
    assert zero["unknown"] == 0
    assert zero["why"] is None

    miss_row = next(line for line in
                    section(fm.render_markdown(scan_and_build(missing)), "## 1.").splitlines()
                    if line.startswith("| lx06 |"))
    zero_row = next(line for line in
                    section(fm.render_markdown(scan_and_build(explicit)), "## 1.").splitlines()
                    if line.startswith("| lx06 |"))
    assert "证据不足" in miss_row
    assert "0/0/—" not in miss_row
    assert "0/0/—" in zero_row


def test_gap_counts_only_turns_that_have_the_field(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        gap_trace("lx06", {"playback_gap_count": 3, "playback_max_gap_ms": 90.0}),
        gap_trace("lx06", {"wake_source": "rui"}),
    ])
    report = scan_and_build(path)
    gaps = report["devices"]["lx06"]["gaps"]
    assert gaps["turns"] == 1
    assert gaps["total"] == 3
    assert gaps["max_ms"] == 90.0
    assert gaps["known"] == 1
    assert gaps["unknown"] == 1
    assert gaps["why"] is None
    assert "1/3/90.0 ms，另 1 个 turn 无该字段" in fm.render_markdown(report)
    assert any("playback_gap_count" in item for item in report["todo"])


# --------------------------------------------------- evidence: JSON full, md short
def test_evidence_json_keeps_every_line_while_markdown_truncates(tmp_path: Path) -> None:
    lines: list[str] = []
    for index in range(45):
        lines.append(proc(f"wake candidate lx06-{2000 + index} admitted after {index} ms"))
        lines.append(proc(f"wake candidate lx06-{3000 + index} rejected: turn_open"))
    path = write_log(tmp_path, lines)
    report = scan_and_build(path)
    row = evidence(report, "lx06", "admitted")
    assert len(row["lines"]) == 45
    assert row["lines"] == [2 * index + 1 for index in range(45)]
    assert "完整列表见 --json" in evidence_ref(row)
    assert "45 行" in evidence_ref(row)

    payload = json.loads(fm.render_json(report))
    json_row = next(item for item in payload["evidence"]
                    if item["scope"] == "lx06" and item["metric"] == "admitted")
    assert json_row["lines"] == [2 * index + 1 for index in range(45)]


def test_barge_without_stop_is_reported_separately(tmp_path: Path) -> None:
    path = write_log(tmp_path, [
        proc("barge: 1 wake during playback, dropped 1.0 s queued"),
        proc("barge: silence_after_ms=48 (server-side: queue flushed)"),
        proc("barge: 2 wake during playback, dropped 2.0 s queued"),
    ])
    report = scan_and_build(path)
    assert report["unattributed"]["barge"]["value"] == 2
    assert report["unattributed"]["barge_without_stop"]["value"] == 1
    assert report["unattributed"]["barge_without_stop"]["lines"] == [3]
    text = fm.render_markdown(report)
    assert "无 stop 的 barge" in text
    assert "不是成功率" in text
    assert evidence_ref(evidence(report, "fleet", "无 stop 的 barge")) == "L3"


# --------------------------------------------------- time windows (task-14)
# 9 哥's 2026-10-26 decision: marks are written by fleet_say with only `ts`, and the
# verdict is derived from the log. Two honesty rules are pinned below and must never
# regress: (a) a window with no wake line for that device is `unknown`, not `fail`;
# (b) success needs admitted *and* a turn result (asr_done or play_start).
AUTO_LOG = FIXTURES / "fleet-auto-sample.log.txt"
AUTO_MARKS = FIXTURES / "fleet-auto-marks.csv"


def auto_report(**kwargs) -> dict:
    return fm.build_report(fm.parse_log(AUTO_LOG), fm.parse_marks(AUTO_MARKS), **kwargs)


def window_row(report: dict, phrase: str, device: str) -> dict:
    for row in report["windows"]["rows"]:
        if row["phrase_id"] == phrase and row["device"] == device:
            return row
    raise AssertionError(f"no window for {phrase}/{device}: "
                         f"{[(r['phrase_id'], r['device']) for r in report['windows']['rows']]}")


def test_window_success_needs_admitted_and_a_turn_result() -> None:
    """success = admitted + (asr_done or play_start), all inside one window."""
    report = auto_report()
    assert report["windows"]["log_tz"] == "UTC"
    assert report["windows"]["multi_device"] is True
    assert report["windows"]["judged_devices"] == ["lx06", "dot2"]

    hello = window_row(report, "hello", "lx06")
    assert hello["verdict"] == "success"
    assert hello["heard_ok"] is True and hello["source"] == "log"
    assert hello["wake_lines"] == [1]
    assert hello["admitted_lines"] == [1]
    assert hello["turn_lines"] == [2] and hello["turn_result_lines"] == [2]

    # asr_done alone is enough (lx06/stop), play_start alone is enough (dot2/stop).
    stop_lx = window_row(report, "stop", "lx06")
    assert stop_lx["verdict"] == "success"
    assert stop_lx["turn_result_lines"] == [13]
    assert "asr_done" in stop_lx["reason"]
    stop_dot2 = window_row(report, "stop", "dot2")
    assert stop_dot2["verdict"] == "success"
    assert stop_dot2["turn_result_lines"] == [14]
    assert "play_start" in stop_dot2["reason"]

    assert report["windows"]["totals"] == {"success": 5, "fail": 5, "unknown": 2}


def test_window_without_any_wake_line_is_unknown_not_fail() -> None:
    report = auto_report()
    row = window_row(report, "hello", "dot2")
    assert row["verdict"] == "unknown"
    assert row["heard_ok"] is None          # never False: unknown must not read as fail
    assert row["wake_lines"] == []
    assert "没有任何 wake 行" in row["reason"]
    assert report["cells"]["verdict_mode"] == "log"
    assert report["cells"]["cells"]["hello"]["dot2"]["derived"] == {
        "success": 0, "fail": 0, "unknown": 1, "human": 0, "trace_only": 0,
        "windows": [1],
    }
    text = fm.render_markdown(report)
    # unknown stays out of the denominator and is spelled out in the cell.
    assert ("| ①你好小瑞 | `hello` | 1/1 | "
            "证据不足（1 个窗口内无 wake 行；unknown 不计失败） | 见 §2b |") in text
    assert "2/6" in text
    assert "3/4（unknown 2 不计失败）" in text
    assert "unknown = 窗口内没有该设备的 wake 行" in text


def test_window_fail_reasons_come_from_the_rejection_lines() -> None:
    report = auto_report()
    time_lx = window_row(report, "time", "lx06")
    assert time_lx["verdict"] == "fail"
    assert "turn_open" in time_lx["reason"]
    assert time_lx["rejected_lines"] == [3]

    play_dot2 = window_row(report, "play", "dot2")
    assert play_dot2["verdict"] == "fail"
    assert "cross_echo" in play_dot2["reason"]
    assert play_dot2["rejected_lines"] == [8]

    next_lx = window_row(report, "next", "lx06")
    assert next_lx["verdict"] == "fail"
    assert "admit_timeout" in next_lx["reason"]

    play_lx = window_row(report, "play", "lx06")
    assert play_lx["verdict"] == "fail"
    assert "没有 turn 结果" in play_lx["reason"]
    assert play_lx["admitted_lines"] == [6]
    assert play_lx["turn_lines"] == [7]
    assert play_lx["turn_result_lines"] == []


def test_adjacent_windows_are_half_open_and_do_not_share_lines() -> None:
    report = auto_report()
    stop = window_row(report, "stop", "lx06")
    bye = window_row(report, "bye", "lx06")
    assert stop["window_start"] == "06:17:48.000"
    assert stop["window_end"] == "06:18:00.000"      # [ts_i, ts_{i+1})
    assert bye["window_start"] == "06:18:00.000"
    assert 15 in stop["wake_lines"]                  # 06:17:59.999 belongs to stop
    assert 17 in bye["wake_lines"]                   # 06:18:00.100 belongs to bye
    assert 15 not in bye["wake_lines"]
    assert 17 not in stop["wake_lines"]
    # 06:18:00.000 is exactly the next ts: excluded from stop, included in bye.
    assert window_row(report, "stop", "dot2")["wake_lines"] == [12]
    assert window_row(report, "bye", "dot2")["wake_lines"] == [16]
    assert window_row(report, "bye", "dot2")["turn_result_lines"] == [18]


def test_window_flag_closes_only_the_last_window() -> None:
    default = auto_report()
    wider = auto_report(window_s=20.0)
    assert window_row(default, "bye", "lx06")["verdict"] == "fail"
    assert window_row(wider, "bye", "lx06")["verdict"] == "success"
    assert window_row(wider, "bye", "lx06")["turn_result_lines"] == [19]
    # Earlier windows still end at the next marks ts, so --window changes nothing there.
    assert window_row(default, "hello", "lx06")["window_end"] == "06:17:12.000"
    assert window_row(wider, "hello", "lx06")["window_end"] == "06:17:12.000"
    assert default["windows"]["totals"] == {"success": 5, "fail": 5, "unknown": 2}
    assert wider["windows"]["totals"] == {"success": 6, "fail": 4, "unknown": 2}


def test_log_tz_is_the_join_frame_and_has_teeth(tmp_path: Path) -> None:
    """Production logs are UTC while marks are +08:00; a literal compare is 8 h off."""
    aware = auto_report()
    assert window_row(aware, "hello", "lx06")["verdict"] == "success"

    shifted = auto_report(log_tz=fm.log_tzinfo("Asia/Shanghai"))
    assert shifted["windows"]["log_tz"] == "Asia/Shanghai"
    assert shifted["windows"]["totals"] == {"success": 0, "fail": 0, "unknown": 12}
    assert window_row(shifted, "hello", "lx06")["verdict"] == "unknown"

    offset = auto_report(log_tz=fm.log_tzinfo("+08:00"))
    assert fm.tz_label(fm.log_tzinfo("+08:00")) == "UTC+08:00"
    assert offset["windows"]["totals"] == {"success": 0, "fail": 0, "unknown": 12}

    # A tz-naive marks ts is read in --log-tz (default UTC): same verdict as +08:00.
    naive_marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,speaker,1,2026-10-26T06:17:00.000,,\n"
        "time,speaker,1,2026-10-26T06:17:12.000,,\n",
        name="naive.csv",
    )
    naive = fm.build_report(fm.parse_log(AUTO_LOG), fm.parse_marks(naive_marks))
    assert window_row(naive, "hello", "lx06")["verdict"] == "success"
    assert window_row(naive, "hello", "lx06")["mark_ts"] == "2026-10-26T06:17:00.000"


def test_invalid_log_tz_exits_2(tmp_path: Path, capsys) -> None:
    log = write_log(tmp_path, [proc("wake candidate lx06-1700 admitted after 5 ms")])
    assert fm.main(["--log", str(log), "--log-tz", "Not/AZone"]) == 2
    assert "unknown --log-tz" in capsys.readouterr().err


def test_human_heard_ok_is_kept_and_marked_human(tmp_path: Path) -> None:
    """A human verdict wins the heard_ok column, but the log verdict is still shown."""
    log = write_log(tmp_path, [
        proc("wake candidate lx06-1800 admitted after 5 ms",
             ts="2026-10-26 10:00:00,000"),                             # 1
        trace({"device_id": "lx06", "stages_ms": {"wake": 0.0}},
              ts="2026-10-26 10:00:01,000"),                            # 2: no result
        proc("wake candidate lx06-1801 admitted after 5 ms",
             ts="2026-10-26 10:00:20,000"),                             # 3: next window
    ])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "play,speaker,1,2026-10-26T10:00:00+00:00,1,\n"
        "next,speaker,1,2026-10-26T10:00:20+00:00,,\n",
    )
    report = fm.build_report(fm.parse_log(log), fm.parse_marks(marks))
    row = window_row(report, "play", "lx06")
    assert row["verdict"] == "fail"          # the log saw admitted but no turn result
    assert row["heard_ok"] is True           # the human said it was OK
    assert row["source"] == "human"
    assert report["cells"]["verdict_mode"] == "human"
    text = fm.render_markdown(report)
    assert "| 1 | ③放首歌 | 1 | 2026-10-26T10:00:00+00:00 |" in text
    assert "| human |" in text


def test_source_device_flag_judges_every_online_device(tmp_path: Path) -> None:
    # fleet_say wrote `lx06` as the source, but lx06 *is* a logged device: without the
    # flag the rows are device-locked; --source-device declares them as the source.
    marks_path = tmp_path / "locked.csv"
    marks_path.write_text(AUTO_MARKS.read_text(encoding="utf-8").replace(",speaker,", ",lx06,"),
                          encoding="utf-8")
    locked = fm.build_report(fm.parse_log(AUTO_LOG), fm.parse_marks(marks_path))
    assert locked["windows"]["multi_device"] is False
    assert len(locked["windows"]["rows"]) == 6
    assert {row["device"] for row in locked["windows"]["rows"]} == {"lx06"}
    assert {row["mode"] for row in locked["windows"]["rows"]} == {"device"}

    expanded = fm.build_report(fm.parse_log(AUTO_LOG), fm.parse_marks(marks_path),
                               source_device="lx06")
    assert expanded["windows"]["multi_device"] is True
    assert len(expanded["windows"]["rows"]) == 12
    assert window_row(expanded, "hello", "dot2")["verdict"] == "unknown"
    assert not any("未生效" in note for note in expanded["windows"]["notes"])


def test_untimed_wake_lines_are_reported_but_never_attributed() -> None:
    report = auto_report()
    play_dot2 = window_row(report, "play", "dot2")
    assert 9 not in play_dot2["wake_lines"]   # prefix-less line has no timestamp
    assert play_dot2["verdict"] == "fail"     # decided by the timed cross_echo line
    # The reader must be able to open the exact line, not hunt for a shape word.
    assert report["log"]["untimed"] == {"value": 1, "devices": {"dot2": [9]}}
    assert any("dot2×1（L9）" in item for item in report["todo"])
    assert any("没有时间戳" in item and "§6" in item for item in report["todo"])
    row = evidence(report, "fleet", "无时间戳的 wake/turn 行")
    assert row["file"] == "log"
    assert row["lines"] == [9]
    # The prefix-less source line really is the untimed wake line.
    raw = AUTO_LOG.read_text(encoding="utf-8").splitlines()
    assert "wake candidate" in raw[8] and "rejected" in raw[8]


def test_window_evidence_keeps_log_and_marks_line_numbers() -> None:
    report = auto_report()
    scope = "①你好小瑞（wake-only）×lx06（R1 #1）"
    log_row = evidence(report, scope, "窗口")
    assert log_row["file"] == "log"
    assert log_row["lines"] == [1, 2]
    assert "success" in log_row["metric"]
    marks_row = evidence(report, scope, "窗口起点")
    assert marks_row["file"] == "marks"
    assert marks_row["lines"] == [2]

    payload = json.loads(fm.render_json(report))
    assert payload["windows"]["totals"] == {"success": 5, "fail": 5, "unknown": 2}
    assert payload["windows"]["rows"][0]["mark_line"] == 2
    assert payload["windows"]["judged_devices"] == ["lx06", "dot2"]


def test_no_timed_marks_means_no_windows_not_fail() -> None:
    report = scan_and_build(SAMPLE_LOG)       # log only, no marks at all
    assert report["windows"]["rows"] == []
    assert report["cells"]["verdict_mode"] == "human"
    text = fm.render_markdown(report)
    assert "没有可比对的窗口" in text
    assert "不做时间窗对齐" in text


def test_cli_window_and_log_tz_flags_are_wired(capsys) -> None:
    assert fm.main(["--log", str(AUTO_LOG), "--marks", str(AUTO_MARKS)]) == 0
    out = capsys.readouterr().out
    assert "- 日志时区：UTC（--log-tz）" in out
    assert "## 2b. 时间窗对齐（heard_ok 由日志判定）" in out
    assert "| ①你好小瑞 | `hello` | 1/1 |" in out
    assert "1 | ①你好小瑞（wake-only） | 1 | 2026-10-26T14:17:00.000+08:00 |" in out

    assert fm.main(["--log", str(AUTO_LOG), "--marks", str(AUTO_MARKS),
                    "--window", "20", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["windows"]["window_s"] == 20.0
    assert payload["windows"]["totals"] == {"success": 6, "fail": 4, "unknown": 2}

    assert fm.main(["--log", str(AUTO_LOG), "--marks", str(AUTO_MARKS),
                    "--log-tz", "Asia/Shanghai", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["windows"]["totals"] == {"success": 0, "fail": 0, "unknown": 12}

    with pytest.raises(SystemExit) as exc:
        fm.main(["--log", str(AUTO_LOG), "--window", "0"])
    assert exc.value.code == 2


def test_line_number_prefixed_and_comma_ms_log_lines_are_tolerated(tmp_path: Path) -> None:
    """9 哥 pastes snippets that may be numbered; comma-ms is the real rui format."""
    path = write_log(tmp_path, [
        "L1 2026-10-26 06:17:24,053 INFO rui.audio.process: "
        "wake candidate lx06-1900 admitted after 5 ms",
        "L2 2026-10-26 06:17:26,500 INFO rui.trace: turn "
        '{"device_id": "lx06", "stages_ms": {"wake": 0.0, "play_start": 2400.0}}',
    ])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,speaker,1,2026-10-26T06:17:24.000,,\n",
    )
    report = fm.build_report(fm.parse_log(path), fm.parse_marks(marks))
    assert report["log"]["unparsed"]["value"] == 0
    row = window_row(report, "hello", "lx06")
    assert row["verdict"] == "success"
    assert row["wake_lines"] == [1]
    assert row["turn_result_lines"] == [2]


# ------------------------------------------- plane relay (production log shape)
# Production capture: the plane is a child process whose stderr is forwarded by the
# control plane, so every plane line arrives as a *double prefix*:
#   <outer> INFO rui.audio.client: plane: <inner> INFO <plane logger>: <message>
# The outer logger alone made every wake/VAD line unparseable (task-14 rework).
PLANE_RELAY_LOG = FIXTURES / "fleet-plane-relay.log.txt"
PLANE_RELAY_REG_LOG = FIXTURES / "fleet-plane-relay-regression.log.txt"
PLANE_RELAY_REG_MARKS = FIXTURES / "fleet-plane-relay-regression-marks.csv"


def test_plane_relay_lines_are_parsed_with_the_inner_logger() -> None:
    report = scan_and_build(PLANE_RELAY_LOG)
    assert report["log"]["unparsed"]["value"] == 0
    assert report["log"]["relay"]["value"] == 2
    assert report["log"]["relay"]["by_inner_logger"] == {"__main__": 1, "rui.audio.kws": 1}
    dot2 = report["devices"]["dot2"]
    assert dot2["wake_candidates"]["value"] == 1
    assert dot2["admitted"]["value"] == 1
    assert dot2["admitted"]["lines"] == [1]          # physical outer log line
    kws = report["unattributed"]["kws_wake"]
    assert kws["value"] == 1
    assert kws["keywords"][0]["keyword"] == "你好小瑞"
    assert kws["keywords"][0]["lines"] == [2]
    # A KWS hit is not a candidate and must not be added to it.
    assert dot2["wake_candidates"]["value"] == 1


def test_plane_relay_uses_the_inner_ts_not_the_1ms_outer_stamp(tmp_path: Path) -> None:
    """Outer/inner stamps can differ by 1 ms; the window boundary is exact."""
    path = write_log(tmp_path, [
        "2026-10-26 06:21:05,215 INFO rui.audio.client: plane: "
        "2026-10-26 06:21:05,216 INFO __main__: "
        "wake candidate dot2-9001 admitted after 9 ms",                       # 1
        "2026-10-26 06:21:07,000 INFO rui.audio.client: plane: "
        "2026-10-26 06:21:07,001 INFO __main__: "
        "wake candidate dot2-9002 rejected: lost_same_word",                  # 2
        trace({"turn_id": "relay-1", "device_id": "dot2",
               "stages_ms": {"wake": 0.0, "asr_done": 100.0, "play_start": 900.0}},
              ts="2026-10-26 06:21:06,000"),                                  # 3
    ])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,dot2,1,2026-10-26T06:21:05.216,,\n"
        "time,dot2,1,2026-10-26T06:21:10.000,,\n",
    )
    report = fm.build_report(fm.parse_log(path), fm.parse_marks(marks))
    row = window_row(report, "hello", "dot2")
    # The outer 05.215 is *before* the window start; the inner 05.216 is inside.
    assert row["wake_lines"] == [1, 2]
    assert row["admitted_lines"] == [1]
    assert row["verdict"] == "success"


def test_plane_relay_regression_structure_matches_production_counts() -> None:
    """Same structure/counts as the capture: 15 candidates, 14 admitted, 18 turns."""
    report = scan_and_build(PLANE_RELAY_REG_LOG, PLANE_RELAY_REG_MARKS)
    assert report["log"]["unparsed"]["value"] == 0
    assert report["log"]["relay"]["value"] == 33
    assert report["log"]["relay"]["by_inner_logger"] == {
        "__main__": 15, "rui.audio.kws": 15, "rui.audio.vad": 3,
    }
    dot2 = report["devices"]["dot2"]
    assert dot2["wake_candidates"]["value"] == 15
    assert dot2["admitted"]["value"] == 14
    rejected = dot2["rejected"]["by_reason"]
    assert rejected["lost_same_word"]["count"] == 1
    assert dot2["turns"]["value"] == 18
    # Arithmetic: candidates = admitted + rejected (nothing dropped silently).
    assert dot2["wake_candidates"]["value"] == (
        dot2["admitted"]["value"] + sum(info["count"] for info in rejected.values())
    )
    assert report["unattributed"]["kws_wake"]["value"] == 15
    assert report["windows"]["totals"] == {"success": 13, "fail": 2, "unknown": 15}
    assert sum(report["windows"]["totals"].values()) == 30
    # Six follow-up windows have a turn result but no wake line (see production).
    assert sum(item["trace_only"] for item in report["windows"]["summary"]) == 6
    text = fm.render_markdown(report)
    assert "`plane:` 转发行：33 行" in text
    assert "KWS `wake:` 行 | 15" in text
    assert "有 6 个窗口「有该设备的 turn 结果但没有 wake 行」" in text


def test_relayed_duplicate_turn_is_counted_once(tmp_path: Path) -> None:
    entry = {"turn_id": "dup-1", "device_id": "dot2",
             "stages_ms": {"wake": 0.0, "play_start": 1000.0}}
    path = write_log(tmp_path, [
        trace(entry),
        "2026-10-26 10:00:01,000 INFO rui.audio.client: plane: "
        f"2026-10-26 10:00:01,000 INFO rui.trace: turn {json.dumps(entry)}",
    ])
    report = scan_and_build(path)
    assert report["devices"]["dot2"]["turns"]["value"] == 1
    assert report["log"]["trace_duplicates"] == {
        "value": 1, "lines": [{"line": 2, "first_line": 1}],
    }
    assert any("同 (device, turn_id)" in item for item in report["todo"])


def test_trace_without_wake_is_unknown_and_says_why(tmp_path: Path) -> None:
    """A follow-up turn is real evidence but cannot prove *this* phrase -> unknown."""
    log = write_log(tmp_path, [
        trace({"turn_id": "follow-1", "device_id": "lx06",
               "stages_ms": {"wake": 0.0, "asr_done": 100.0, "play_start": 900.0}},
              ts="2026-10-26 10:00:02,000"),
    ])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00.000,,\n",
    )
    report = fm.build_report(fm.parse_log(log), fm.parse_marks(marks))
    row = window_row(report, "hello", "lx06")
    assert row["verdict"] == "unknown"
    assert row["heard_ok"] is None
    assert row["wake_lines"] == []
    assert "turn 结果" in row["reason"]
    assert "无法证明是本句" in row["reason"]
    summary = report["windows"]["summary"][0]
    assert summary["unknown"] == 1 and summary["trace_only"] == 1


# ---------------------------------------------- wake-only phrases (hello by default)
def wake_only_report(tmp_path: Path, log_lines: list[str], marks_text: str, **kwargs) -> dict:
    log = write_log(tmp_path, log_lines)
    marks = write_marks(tmp_path, marks_text)
    return fm.build_report(fm.parse_log(log), fm.parse_marks(marks), **kwargs)


def test_wake_only_hello_is_success_without_a_turn(tmp_path: Path) -> None:
    """A pure wake test is answered by the plane; demanding a turn made it 0/5."""
    report = wake_only_report(
        tmp_path,
        [proc("wake candidate lx06-2001 admitted after 5 ms",
              ts="2026-10-26 10:00:01,000")],
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00.000,,\n",
    )
    row = window_row(report, "hello", "lx06")
    assert row["wake_only"] is True
    assert row["verdict"] == "success"
    assert row["heard_ok"] is True
    assert "仅测唤醒" in row["reason"]
    assert "不要求 turn" in row["reason"]
    assert report["windows"]["wake_only_phrases"] == ["hello"]
    assert report["cells"]["cells"]["hello"]["lx06"]["derived"]["success"] == 1
    text = fm.render_markdown(report)
    assert "①你好小瑞（wake-only）" in text
    assert "仅测唤醒（wake-only）" in text


def test_wake_only_hello_all_rejected_is_fail(tmp_path: Path) -> None:
    report = wake_only_report(
        tmp_path,
        [proc("wake candidate lx06-2002 rejected: turn_open",
              ts="2026-10-26 10:00:01,000")],
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00.000,,\n",
    )
    row = window_row(report, "hello", "lx06")
    assert row["verdict"] == "fail"
    assert row["heard_ok"] is False
    assert "仅测唤醒" in row["reason"]
    assert "全被拒" in row["reason"]
    assert "turn_open×1" in row["reason"]


def test_wake_only_hello_without_any_candidate_is_unknown(tmp_path: Path) -> None:
    report = wake_only_report(
        tmp_path,
        [proc("capture recovered after 1 failure(s)",
              ts="2026-10-26 10:00:01,000"),
         # A device line exists (so lx06 is known), but there is no wake candidate.
         trace({"device_id": "lx06", "stages_ms": {"wake": 0.0}},
               ts="2026-10-26 10:00:02,000")],
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00.000,,\n",
    )
    row = window_row(report, "hello", "lx06")
    assert row["verdict"] == "unknown"
    assert row["heard_ok"] is None           # unknown must never read as fail
    assert "没有任何 wake 行" in row["reason"]
    assert "仅测唤醒" in row["reason"]


def test_wake_only_phrase_empty_restores_the_turn_rule(tmp_path: Path, capsys) -> None:
    log = write_log(tmp_path, [
        proc("wake candidate lx06-2003 admitted after 5 ms",
             ts="2026-10-26 10:00:01,000"),
    ])
    marks = write_marks(
        tmp_path,
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T10:00:00.000,,\n",
    )
    assert fm.main(["--log", str(log), "--marks", str(marks),
                    "--wake-only-phrase", "", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    row = payload["windows"]["rows"][0]
    assert row["wake_only"] is False
    assert row["verdict"] == "fail"          # old rule: admitted but no turn result
    assert "没有 turn 结果" in row["reason"]
    assert payload["windows"]["wake_only_phrases"] == []


def test_wake_only_phrase_accepts_a_comma_separated_list(capsys) -> None:
    assert fm.main(["--log", str(AUTO_LOG), "--marks", str(AUTO_MARKS),
                    "--wake-only-phrase", "hello,bye", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["windows"]["wake_only_phrases"] == ["bye", "hello"]   # sorted
    # bye/lx06 is admitted without a turn: wake-only turns it into a success.
    bye = next(row for row in payload["windows"]["rows"]
               if row["phrase_id"] == "bye" and row["device"] == "lx06")
    assert bye["verdict"] == "success"
    assert "仅测唤醒" in bye["reason"]
    assert payload["windows"]["totals"] == {"success": 6, "fail": 4, "unknown": 2}
