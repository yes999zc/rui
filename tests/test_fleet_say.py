"""Tests for ``scripts/fleet_say.py`` (task-14 automated sampling).

The script is the producer of the marks rows ``fleet_matrix`` consumes, so three
properties are defended here, each with teeth:

  1. ``--dry-run`` never speaks and never writes - a test machine stays silent;
  2. every row carries the six scripted phrases, an empty ``heard_ok`` and an
     increasing timezone-aware millisecond ``ts``; the spoken text has no "①";
  3. a row is flushed *before* its phrase is spoken, so Ctrl-C keeps every row
     already written.

``say`` is never executed: every test injects a fake runner / sleeper / clock.
"""
from __future__ import annotations

import csv
import importlib.util
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fleet_say.py"
MATRIX = ROOT / "scripts" / "fleet_matrix.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fleet_say = _load("fleet_say_under_test", SCRIPT)
fleet_matrix = _load("fleet_matrix_for_say_test", MATRIX)

BASE = datetime(2026, 10, 26, 14, 40, 0, 123000, tzinfo=timezone(timedelta(hours=8)))
TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")


class FakeRunner:
    """Records the argv it would have spoken; can raise KeyboardInterrupt mid-run."""

    def __init__(self, interrupt_at: int | None = None) -> None:
        self.calls: list[list[str]] = []
        self.interrupt_at = interrupt_at

    def __call__(self, argv: list[str]) -> None:
        self.calls.append(list(argv))
        if self.interrupt_at is not None and len(self.calls) >= self.interrupt_at:
            raise KeyboardInterrupt


class FakeSleeper:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def clock(start: datetime = BASE, step_s: float = 12.0):
    """now() -> start, start+step, ...: one call for the plan, then one per row."""
    state = {"calls": -1}

    def now() -> datetime:
        state["calls"] += 1
        return start + timedelta(seconds=step_s * state["calls"])

    return now


def read_marks(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def run(argv: list[str], *, runner: FakeRunner | None = None,
        sleeper: FakeSleeper | None = None, now=None) -> tuple[int, FakeRunner, FakeSleeper]:
    runner = runner or FakeRunner()
    sleeper = sleeper or FakeSleeper()
    return fleet_say.main(argv, runner=runner, sleeper=sleeper, now=now or clock()), runner, sleeper


# ---------------------------------------------------------------------- dry run
def test_dry_run_prints_the_rows_without_sound_or_file(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"
    rc, runner, sleeper = run(["--dry-run", "--rounds", "1", "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "phrase_id,device_id,round,ts,heard_ok,notes" in text
    rows = [line for line in text.splitlines()
            if line.split(",", 1)[0] in fleet_say.PHRASE_IDS]
    assert len(rows) == 6
    assert runner.calls == [] and sleeper.sleeps == []
    assert not out.exists()
    assert "dry-run" in text
    assert "唤醒配对：开（wake=你好小瑞，lead=1.5 s）" in text


def test_dry_run_shows_two_rounds_as_twelve_rows(tmp_path: Path, capsys) -> None:
    rc, runner, _sleeper = run(["--dry-run", "--rounds", "2",
                                "--out", str(tmp_path / "marks.csv")])
    assert rc == 0
    rows = [line for line in capsys.readouterr().out.splitlines()
            if line.split(",", 1)[0] in fleet_say.PHRASE_IDS]
    assert len(rows) == 12
    assert rows[0].split(",")[0] == "hello"
    assert rows[6].split(",")[0] == "hello"
    assert runner.calls == []


# -------------------------------------------------------------------- real run
def test_marks_rows_are_the_six_phrases_with_increasing_aware_ts(tmp_path: Path) -> None:
    out = tmp_path / "marks.csv"
    rc, runner, sleeper = run(["--rounds", "1", "--out", str(out), "--device", "speaker"])
    assert rc == 0
    rows = read_marks(out)
    assert [row["phrase_id"] for row in rows] == list(fleet_say.PHRASE_IDS)
    assert [row["device_id"] for row in rows] == ["speaker"] * 6
    assert [row["round"] for row in rows] == ["1"] * 6
    assert all(row["heard_ok"] == "" for row in rows)

    stamps = [row["ts"] for row in rows]
    assert all(TS_PATTERN.match(stamp) for stamp in stamps)
    parsed = [datetime.fromisoformat(stamp) for stamp in stamps]
    assert all(moment.tzinfo is not None for moment in parsed)
    assert parsed == sorted(parsed)
    assert len(set(parsed)) == 6
    assert parsed[1] - parsed[0] == timedelta(seconds=12)

    assert rows[0]["notes"].startswith("wake-only voice=Tingting rate=180")
    assert "wake=你好小瑞/lead=1.5s/rate=180" in rows[1]["notes"]
    assert "cmd=say -v Tingting -r 180 '你好小瑞' ; say -v Tingting -r 180" in rows[1]["notes"]
    assert runner.calls[0] == ["say", "-v", "Tingting", "-r", "180", "你好小瑞"]
    # hello once, then wake + instruction for each of the other five phrases.
    assert len(runner.calls) == 11
    assert [call[-1] for call in runner.calls] == [
        "你好小瑞",
        "你好小瑞", "几点了",
        "你好小瑞", "放首歌",
        "你好小瑞", "下一首",
        "你好小瑞", "停止播放",
        "你好小瑞", "退下吧",
    ]
    assert all("①" not in call[-1] for call in runner.calls)
    # hello gap, then (lead, gap) for the paired phrases; the last has no trailing gap.
    assert sleeper.sleeps == [12.0, 1.5, 12.0, 1.5, 12.0, 1.5, 12.0, 1.5, 12.0, 1.5]

    # The consumer can read the file unchanged.
    marks = fleet_matrix.parse_marks(out)
    assert marks.usable is True
    assert len(marks.rows) == 6
    assert all(row.heard_ok is None for row in marks.rows)


def test_rounds_repeat_the_six_phrases_in_order(tmp_path: Path) -> None:
    out = tmp_path / "marks.csv"
    rc, _runner, _sleeper = run(["--rounds", "2", "--out", str(out)])
    assert rc == 0
    rows = read_marks(out)
    assert [row["phrase_id"] for row in rows] == list(fleet_say.PHRASE_IDS) * 2
    assert [row["round"] for row in rows] == ["1"] * 6 + ["2"] * 6
    stamps = [datetime.fromisoformat(row["ts"]) for row in rows]
    assert stamps == sorted(stamps)


def test_device_flag_changes_marks_only(tmp_path: Path) -> None:
    out = tmp_path / "lx06.csv"
    rc, runner, _sleeper = run(["--out", str(out), "--device", "lx06",
                                "--voice", "Meijia", "--rate", "200"])
    assert rc == 0
    rows = read_marks(out)
    assert {row["device_id"] for row in rows} == {"lx06"}
    assert runner.calls[0] == ["say", "-v", "Meijia", "-r", "200", "你好小瑞"]
    assert "voice=Meijia" in rows[0]["notes"]
    assert "rate=200" in rows[0]["notes"]
    # The device id only lives in the marks; the say command never mentions it.
    assert all("lx06" not in " ".join(call) for call in runner.calls)


def test_ctrl_c_keeps_every_row_already_written(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"
    rc, runner, sleeper = run(["--out", str(out)], runner=FakeRunner(interrupt_at=3))
    assert rc == 130
    rows = read_marks(out)
    # call 1 = hello wake, call 2 = time wake, call 3 = time instruction (interrupted).
    assert [row["phrase_id"] for row in rows] == ["hello", "time"]
    assert len(runner.calls) == 3
    assert sleeper.sleeps == [12.0, 1.5]     # hello gap, then time wake lead
    err = capsys.readouterr().err
    assert "已中断" in err
    assert "没有丢行" in err


def test_missing_say_keeps_the_row_for_the_phrase_that_failed(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"

    def missing(argv: list[str]) -> None:
        raise FileNotFoundError(2, "No such file or directory", "say")

    rc, _runner, _sleeper = run(["--out", str(out)], runner=missing)
    assert rc == 2
    rows = read_marks(out)
    assert len(rows) == 1                    # the row is written before say runs
    assert rows[0]["notes"].endswith("say_failed")
    err = capsys.readouterr().err
    assert "say_failed" in err
    assert "找不到本机 say" in err


# ------------------------------------------------------------------- refusals
@pytest.mark.parametrize("gap", ["9.9", "0", "-5"])
def test_gap_below_ten_is_rejected(gap: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        fleet_say.main(["--dry-run", "--gap", gap],
                       runner=FakeRunner(), sleeper=FakeSleeper(), now=clock())
    assert exc.value.code == 2
    assert "--gap" in capsys.readouterr().err


def test_rounds_must_be_at_least_one(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        fleet_say.main(["--dry-run", "--rounds", "0"],
                       runner=FakeRunner(), sleeper=FakeSleeper(), now=clock())
    assert exc.value.code == 2
    assert "--rounds" in capsys.readouterr().err


def test_existing_marks_file_is_not_overwritten_by_default(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"
    out.write_text(
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T14:40:00.000+08:00,,\n",
        encoding="utf-8",
    )
    before = out.read_text(encoding="utf-8")
    rc, runner, _sleeper = run(["--out", str(out)])
    assert rc == 2
    assert runner.calls == []                # nothing spoken on a refused write
    assert out.read_text(encoding="utf-8") == before
    assert "已存在" in capsys.readouterr().err


def test_append_keeps_earlier_rows_and_writes_the_header_once(tmp_path: Path) -> None:
    out = tmp_path / "marks.csv"
    out.write_text(
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,2,2026-10-26T13:00:00.000+08:00,,\n",
        encoding="utf-8",
    )
    rc, _runner, _sleeper = run(["--out", str(out), "--append"])
    assert rc == 0
    rows = read_marks(out)
    assert len(rows) == 7
    assert rows[0]["round"] == "2" and rows[0]["device_id"] == "lx06"
    assert [row["phrase_id"] for row in rows[1:]] == list(fleet_say.PHRASE_IDS)


def test_append_refuses_a_foreign_header(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"
    out.write_text("phrase,device,ts\nhello,lx06,2026-10-26T14:40:00+08:00\n",
                   encoding="utf-8")
    rc, _runner, _sleeper = run(["--out", str(out), "--append"])
    assert rc == 2
    assert "表头" in capsys.readouterr().err


# --------------------------------------------------------------- contract pin
def test_phrases_and_columns_match_the_matrix_contract() -> None:
    assert [phrase for phrase, _label in fleet_say.PHRASES] == \
        [phrase for phrase, _label in fleet_matrix.PHRASES]
    assert fleet_say.MARK_COLUMNS == fleet_matrix.MARK_COLUMNS
    assert set(fleet_say.SPOKEN) == set(fleet_matrix.PHRASE_IDS)
    for phrase, label in fleet_matrix.PHRASES:
        # The matrix label carries the circled number; the spoken text must not.
        assert fleet_say.SPOKEN[phrase] == label.lstrip("①②③④⑤⑥")
        assert "①" not in fleet_say.SPOKEN[phrase]


def plan_lines(text: str) -> list[str]:
    """The dry-run plan lines (" 1. r1 <ts> <phrase> ...")."""
    return [line for line in text.splitlines() if re.match(r"^\s*\d+\. r\d+ ", line)]


def test_dry_run_plan_shows_wake_pause_and_instruction(tmp_path: Path, capsys) -> None:
    rc, runner, sleeper = run(["--dry-run", "--rounds", "1",
                               "--out", str(tmp_path / "marks.csv")])
    assert rc == 0
    text = capsys.readouterr().out
    lines = plan_lines(text)
    assert len(lines) == 6
    hello = lines[0]
    assert " hello [wake-only] " in hello
    assert hello.count("你好小瑞") == 1      # the wake test plays the word exactly once
    assert hello.count("say ") == 1
    for line in lines[1:]:
        assert "[wake 你好小瑞 → 停顿 1.5 s]" in line
        assert line.count("say ") == 2      # wake word then the instruction
    assert "几点了" in lines[1]
    assert runner.calls == [] and sleeper.sleeps == []


@pytest.mark.parametrize("lead", ["0.5", "0.99", "5.01", "6"])
def test_wake_lead_outside_the_allowed_range_is_rejected(lead: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        fleet_say.main(["--dry-run", "--wake-lead", lead],
                       runner=FakeRunner(), sleeper=FakeSleeper(), now=clock())
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--wake-lead" in err
    assert "1.0-5.0 s" in err
    assert "follow-up gate" in err          # the reason, not just the range


def test_empty_wake_prefix_disables_pairing(tmp_path: Path, capsys) -> None:
    rc, runner, _sleeper = run(["--dry-run", "--wake-prefix", "",
                                "--out", str(tmp_path / "marks.csv")])
    assert rc == 0
    text = capsys.readouterr().out
    assert "唤醒配对：关闭（--wake-prefix ''）" in text
    assert "停顿" not in text               # no pause is scheduled anywhere
    lines = plan_lines(text)
    assert len(lines) == 6
    assert "[wake-only]" in lines[0]        # hello still plays its own wake word once
    for line in lines[1:]:
        assert "[无唤醒配对]" in line
        assert line.count("say ") == 1

    out = tmp_path / "real.csv"
    rc, runner, _sleeper = run(["--wake-prefix", "", "--out", str(out)])
    assert rc == 0
    rows = read_marks(out)
    assert len(rows) == 6                   # one marks row per phrase, always
    assert rows[1]["notes"].startswith("wake=none")
    assert len(runner.calls) == 6


class FailingRunner(FakeRunner):
    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at

    def __call__(self, argv: list[str]) -> None:
        super().__call__(argv)
        if len(self.calls) == self.fail_at:
            raise subprocess.CalledProcessError(1, argv)


def test_say_failure_tags_the_written_row(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"
    runner = FailingRunner(fail_at=3)       # hello wake, time wake, time instruction
    rc, runner, _sleeper = run(["--out", str(out)], runner=runner)
    assert rc == 2
    rows = read_marks(out)
    assert [row["phrase_id"] for row in rows] == ["hello", "time"]
    assert "say_failed" not in rows[0]["notes"]
    assert rows[1]["notes"].endswith("say_failed")
    err = capsys.readouterr().err
    assert "say 退出码 1" in err
    assert "say_failed" in err


def test_append_with_duplicate_round_warns_loudly(tmp_path: Path, capsys) -> None:
    out = tmp_path / "marks.csv"
    out.write_text(
        "phrase_id,device_id,round,ts,heard_ok,notes\n"
        "hello,lx06,1,2026-10-26T14:40:00.000+08:00,,\n",
        encoding="utf-8",
    )
    rc, _runner, _sleeper = run(["--out", str(out), "--append",
                                 "--device", "lx06", "--rounds", "1"])
    assert rc == 0
    assert len(read_marks(out)) == 7        # appended despite the warning
    err = capsys.readouterr().err
    assert "警告" in err
    assert "重复" in err
    assert "白跑" in err
    assert "round 1" in err


def test_non_default_wake_lead_reaches_plan_notes_and_playback_order(
        tmp_path: Path, capsys) -> None:
    """2.5 s is legal and must reach every layer; a hardcoded 1.5 must fail here."""
    # 1) dry-run: the plan line and the header both carry the requested lead.
    out = tmp_path / "marks.csv"
    rc, runner, sleeper = run(["--dry-run", "--wake-lead", "2.5", "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "lead=2.5 s" in text
    assert "[wake 你好小瑞 → 停顿 2.5 s]" in plan_lines(text)[1]
    assert "停顿 1.5 s" not in text
    assert runner.calls == [] and sleeper.sleeps == []

    # 2) real run: the pause is executed between that phrase's wake word and its
    #    instruction, in order - not merely "a sleep of some length happened".
    events: list[tuple[str, object]] = []

    def event_runner(argv: list[str]) -> None:
        events.append(("say", argv[-1]))

    def event_sleeper(seconds: float) -> None:
        events.append(("sleep", seconds))

    real_out = tmp_path / "real.csv"
    rc = fleet_say.main(["--wake-lead", "2.5", "--out", str(real_out)],
                        runner=event_runner, sleeper=event_sleeper, now=clock())
    assert rc == 0
    rows = read_marks(real_out)
    assert len(rows) == 6
    assert "lead=" not in rows[0]["notes"]           # hello is wake-only
    for row in rows[1:]:
        assert "lead=2.5s" in row["notes"]

    expected: list[tuple[str, object]] = [("say", "你好小瑞"), ("sleep", 12.0)]
    for phrase in fleet_say.PHRASE_IDS[1:]:
        expected += [("say", "你好小瑞"), ("sleep", 2.5),
                     ("say", fleet_say.SPOKEN[phrase])]
        if phrase != fleet_say.PHRASE_IDS[-1]:
            expected.append(("sleep", 12.0))
    assert events == expected
