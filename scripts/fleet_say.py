#!/usr/bin/env python
"""fleet_say: speak the six scripted phrases and record *when* each window started.

Why this exists
---------------
``fleet_matrix`` has to know which log lines belong to which scripted phrase. The rui
log carries a ``device_id`` (``wake candidate <device>-<ns>``, ``rui.trace turn``) but
no phrase id, so the only join key available is time. This script produces that key:
one marks row per window, written *before* the wake word runs, so ``ts`` is the window
start and not an after-the-fact guess.

Wake pairing (measured on Dot2, Mac ``say -v Tingting``, volume 100)
-------------------------------------------------------------------
Speaking a command 2.5 s after the wake word produced a KWS hit in only 11/30 tries
(~37%). With a 1.5 s lead, 10/10 attempts produced ``kws=1 adm=1 turn=1`` (rate=190;
rate=150 mis-transcribed 2/5 as echo). So every non-``hello`` phrase is played as::

    wake word  ->  pause --wake-lead (default 1.5 s, allowed 1.0-5.0)  ->  phrase

``hello`` is the wake test itself: it plays the wake word exactly once and no
instruction. ``--wake-prefix ''`` disables the pairing (each phrase is played alone;
``hello`` still plays its own wake word once). The marks row is still one per phrase:
its ``ts`` is the wake word's start, which is the window start the matrix uses.

    phrase_id,device_id,round,ts,heard_ok,notes
    hello,speaker,1,2026-10-26T14:40:00.123+08:00,,wake-only voice=Tingting rate=190 cmd=...
    time,speaker,1,2026-10-26T14:40:12.456+08:00,,wake=你好小瑞/lead=1.5s/rate=190 ...

``heard_ok`` is deliberately left empty: ``fleet_matrix`` derives it from the log
(wake / admitted / ASR / turn result inside the time window). A value typed here would
be reported as ``human`` and take precedence, so an empty cell is the honest default
for an automated run.

``device_id`` is the *playback source* label, not a device under test: this script
never talks to Dot2 / 小爱 / the network / the NAS, it only runs the local ``say``
command. The default ``speaker`` is not a device in the rui log, which makes
``fleet_matrix`` judge every online device in each window ("single source, multiple
devices"). Pass ``--device lx06`` to lock the rows to one device instead.

Safety / interruption
---------------------
``--dry-run`` prints the complete plan (wake word, pause, instruction, expected time)
and the exact marks rows without speaking and without writing anything. In a real run
every row is flushed before the next phrase starts, so Ctrl-C keeps every row already
written (exit 130). If ``say`` is missing or fails, the already-written row's notes
are rewritten with ``say_failed`` - the failure is never silent.

This file must stay in the same directory as ``fleet_matrix.py``: it imports
``PHRASES`` / ``MARK_COLUMNS`` from there as the single source of truth.

Usage::

    python scripts/fleet_say.py --rounds 1 --out /tmp/fleet-marks.csv
    python scripts/fleet_say.py --dry-run --rounds 1
    python scripts/fleet_say.py --device lx06 --rounds 2 --gap 12 --rate 190
    python scripts/fleet_say.py --wake-lead 1.5 --wake-prefix 你好小瑞
    python scripts/fleet_say.py --wake-prefix ''

Exit: 0 = all phrases spoken, 2 = usage / file / ``say`` error, 130 = interrupted.
"""
from __future__ import annotations

import argparse
import csv
import io
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# The six phrases and the marks header are defined once, in fleet_matrix: this script
# is the producer of the very rows the matrix parses, so a second copy could drift.
# Both files must therefore stay in the same directory.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from fleet_matrix import (  # noqa: E402
    MARK_COLUMNS,
    PHRASE_IDS,
    PHRASES,
)

DEFAULT_VOICE = "Tingting"
DEFAULT_RATE = 180
# One phrase at 12 s gives the device time to answer (first_response_ms is ~2.6-3.1 s
# in the fixture) and keeps adjacent windows from overlapping. The floor of 10 s is
# enforced below: a shorter gap turns the previous phrase's reply into this window's
# evidence and the attribution stops being honest.
DEFAULT_GAP_S = 12.0
MIN_GAP_S = 10.0
DEFAULT_DEVICE = "speaker"

# Measured 2026-10-26 on Dot2: 2.5 s after the wake word only 11/30 runs had a KWS
# hit; 1.5 s gave 10/10. Below 1.0 s the wake word and the command merge in the VAD;
# above 5.0 s the plane's follow-up gate (5 s) closes and the wake is discarded.
DEFAULT_WAKE_PREFIX = "你好小瑞"
DEFAULT_WAKE_LEAD_S = 1.5
MIN_WAKE_LEAD_S = 1.0
MAX_WAKE_LEAD_S = 5.0
WAKE_ONLY_PHRASE = "hello"

# What is actually spoken. The matrix labels carry circled numbers for display
# ("①你好小瑞"); a TTS voice must not be asked to read "①".
SPOKEN: dict[str, str] = {
    "hello": "你好小瑞",
    "time": "几点了",
    "play": "放首歌",
    "next": "下一首",
    "stop": "停止播放",
    "bye": "退下吧",
}


def now_local() -> datetime:
    """Wall clock with the machine's offset, so ``ts`` survives a timezone change."""
    return datetime.now().astimezone()


def format_ts(when: datetime) -> str:
    """ISO 8601 with offset and milliseconds, e.g. 2026-10-26T14:40:00.123+08:00."""
    return when.isoformat(timespec="milliseconds")


def say_text_argv(text: str, voice: str, rate: int) -> list[str]:
    """The exact local command for one spoken text (macOS ``say``; never a device)."""
    return ["say", "-v", voice, "-r", str(rate), text]


def say_argv(phrase_id: str, voice: str, rate: int) -> list[str]:
    return say_text_argv(SPOKEN[phrase_id], voice, rate)


def default_runner(argv: list[str]) -> None:
    """Speak one command synchronously. Tests inject a fake instead."""
    subprocess.run(argv, check=True)


def default_sleeper(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True)
class Step:
    """One marks row: the wake word (optional), the pause, then the phrase (optional)."""
    phrase_id: str
    round: int
    planned_ts: datetime
    gap_after: float
    wake_argv: tuple[str, ...] | None
    instruction_argv: tuple[str, ...] | None
    wake_lead: float
    wake_word: str  # "" when pairing is disabled
    wake_only: bool  # hello: wake word only, no instruction

    def commands(self) -> list[tuple[str, ...]]:
        return [cmd for cmd in (self.wake_argv, self.instruction_argv) if cmd is not None]


def build_plan(*, rounds: int, gap_s: float, voice: str, rate: int,
               start: datetime, wake_prefix: str,
               wake_lead: float) -> list[Step]:
    """The full playback plan, in order, with the earliest possible start per window.

    ``planned_ts`` is only what ``--dry-run`` prints: a real run stamps ``ts`` from
    the clock immediately before writing the row, because speaking takes time.
    """
    wake_word = wake_prefix.strip()
    steps: list[Step] = []
    total = rounds * len(PHRASE_IDS)
    index = 0
    for round_no in range(1, rounds + 1):
        for phrase_id in PHRASE_IDS:
            planned_ts = start + timedelta(seconds=index * gap_s)
            gap_after = gap_s if index + 1 < total else 0.0
            if phrase_id == WAKE_ONLY_PHRASE:
                # hello is the wake test: exactly one wake word, never a second copy.
                text = wake_word or SPOKEN[phrase_id]
                step = Step(
                    phrase_id=phrase_id,
                    round=round_no,
                    planned_ts=planned_ts,
                    gap_after=gap_after,
                    wake_argv=tuple(say_text_argv(text, voice, rate)),
                    instruction_argv=None,
                    wake_lead=0.0,
                    wake_word=wake_word,
                    wake_only=True,
                )
            elif wake_word:
                step = Step(
                    phrase_id=phrase_id,
                    round=round_no,
                    planned_ts=planned_ts,
                    gap_after=gap_after,
                    wake_argv=tuple(say_text_argv(wake_word, voice, rate)),
                    instruction_argv=tuple(say_argv(phrase_id, voice, rate)),
                    wake_lead=wake_lead,
                    wake_word=wake_word,
                    wake_only=False,
                )
            else:
                step = Step(
                    phrase_id=phrase_id,
                    round=round_no,
                    planned_ts=planned_ts,
                    gap_after=gap_after,
                    wake_argv=None,
                    instruction_argv=tuple(say_argv(phrase_id, voice, rate)),
                    wake_lead=0.0,
                    wake_word="",
                    wake_only=False,
                )
            steps.append(step)
            index += 1
    return steps


def mark_notes(step: Step, *, voice: str, rate: int) -> str:
    """Notes carry the pairing parameters and the exact command(s), never a comma."""
    if step.wake_only:
        prefix = f"wake-only voice={voice} rate={rate}"
    elif step.wake_word:
        prefix = f"wake={step.wake_word}/lead={step.wake_lead:g}s/rate={rate} voice={voice}"
    else:
        prefix = f"wake=none voice={voice} rate={rate}"
    commands = " ; ".join(shlex.join(cmd) for cmd in step.commands())
    return f"{prefix} cmd={commands}"


def mark_row(step: Step, ts: datetime, *, device_id: str, voice: str,
             rate: int) -> list[str]:
    """One marks row: heard_ok empty by design; ts is the window (wake) start."""
    return [
        step.phrase_id,
        device_id,
        str(step.round),
        format_ts(ts),
        "",
        mark_notes(step, voice=voice, rate=rate),
    ]


def csv_line(cells: list[str]) -> str:
    """Render one row exactly as it will be written (quoting included)."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(cells)
    return buffer.getvalue()


def existing_mark_keys(path: Path) -> set[tuple[str, str, str]]:
    """(phrase_id, device_id, round) of an existing marks file (for --append warnings)."""
    keys: set[tuple[str, str, str]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            phrase = (row.get("phrase_id") or "").strip()
            device = (row.get("device_id") or "").strip()
            round_ = (row.get("round") or "").strip()
            if phrase and device:
                keys.add((phrase, device, round_))
    return keys


class MarksWriter:
    """Append-only marks writer: header once, one flush per row, nothing rewritten.

    ``say_failed`` is the one exception: the row for the phrase that failed is
    rewritten in place (seek/truncate to its offset), so a failed run cannot look
    like a clean one.
    """

    def __init__(self, path: Path, *, append: bool) -> None:
        self.path = path
        self.append = append
        self.rows_written = 0
        self.existed = False
        self._handle = None
        self._writer = None
        self._last_offset: int | None = None
        self._last_cells: list[str] | None = None

    def open(self) -> None:
        self.existed = self.path.exists() and self.path.stat().st_size > 0
        if self.existed and not self.append:
            raise FileExistsError(
                f"{self.path} 已存在且非空；换一个 --out，或加 --append 追加（会与已有行一起被矩阵读取）"
            )
        if self.existed:
            with self.path.open("r", encoding="utf-8-sig", newline="") as fh:
                header = next(csv.reader(fh), [])
            header = [cell.strip() for cell in header]
            if header and header != list(MARK_COLUMNS):
                raise ValueError(
                    f"{self.path} 的表头 {header} 与 {list(MARK_COLUMNS)} 不一致，拒绝混写"
                )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a" if self.existed else "w",
                                      encoding="utf-8", newline="")
        self._writer = csv.writer(self._handle, lineterminator="\n")
        if not self.existed:
            self._writer.writerow(MARK_COLUMNS)
            self._handle.flush()

    def write(self, cells: list[str]) -> None:
        if self._writer is None or self._handle is None:
            raise RuntimeError("MarksWriter.open() was not called")
        # Remember where this row starts: a later `say` failure rewrites just this line.
        self._last_offset = self._handle.tell()
        self._last_cells = list(cells)
        self._writer.writerow(cells)
        # Flush *before* the next phrase is spoken: Ctrl-C or a crash must never
        # cost a row whose playback already started.
        self._handle.flush()
        self.rows_written += 1

    def mark_last_failed(self, tag: str = "say_failed") -> None:
        """Rewrite the last written row with `tag` appended to its notes."""
        if (self._handle is None or self._writer is None
                or self._last_cells is None or self._last_offset is None):
            return
        cells = list(self._last_cells)
        cells[5] = f"{cells[5]} {tag}".strip()
        try:
            self._handle.seek(self._last_offset)
            self._handle.truncate()
            self._writer.writerow(cells)
            self._handle.flush()
        except OSError:
            return  # keep the original row rather than lose it
        self._last_cells = cells

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
            self._writer = None


def default_out(start: datetime) -> Path:
    """A fresh file per run, outside the repo: marks are run artifacts, not source."""
    return Path(tempfile.gettempdir()) / f"rui-fleet-marks-{start.strftime('%Y%m%d-%H%M%S')}.csv"


def plan_line(index: int, step: Step) -> str:
    """One complete plan line: wake word -> pause -> instruction, with its start time."""
    if step.wake_only:
        tag = "[wake-only]"
    elif step.wake_word:
        tag = f"[wake {step.wake_word} → 停顿 {step.wake_lead:g} s]"
    else:
        tag = "[无唤醒配对]"
    sequence = " → ".join(shlex.join(cmd) for cmd in step.commands())
    return (f"{index:>2}. r{step.round} {format_ts(step.planned_ts)} "
            f"{step.phrase_id} {tag} {sequence}")


def print_dry_run(plan: list[Step], out_path: Path, args: argparse.Namespace,
                  start: datetime) -> None:
    pairing = (f"开（wake={args.wake_prefix.strip()}，lead={args.wake_lead:g} s）"
               if args.wake_prefix.strip() else "关闭（--wake-prefix ''）")
    print(f"fleet_say（dry-run）：{len(plan)} 句 = {len(PHRASES)} 句 × {args.rounds} 轮；"
          f"语音 {args.voice}，rate={args.rate}，句间隔 {args.gap:g} s；唤醒配对：{pairing}")
    print("计划（ts = 各句窗口起点；预计时刻按 gap 累加，"
          "真实运行以写盘前一刻为准）：")
    for index, step in enumerate(plan, start=1):
        print(plan_line(index, step))
    print()
    print("（不发声、不写盘）")
    print(f"将写入 marks：{out_path}"
          + ("（--append 追加）" if args.append else ""))
    print(csv_line(list(MARK_COLUMNS)), end="")
    for step in plan:
        print(csv_line(mark_row(step, step.planned_ts, device_id=args.device,
                                voice=args.voice, rate=args.rate)), end="")
    print(f"（共 {len(plan)} 行；heard_ok 全部留空，由 fleet_matrix 按日志时间窗判定）")


def main(argv: list[str] | None = None, *, runner=None, sleeper=None, now=None) -> int:
    runner = default_runner if runner is None else runner
    sleeper = default_sleeper if sleeper is None else sleeper
    now = now_local if now is None else now

    parser = argparse.ArgumentParser(
        prog="fleet_say",
        description="播放 6 句固定话术（本机 say），并把每句的窗口起点写成 marks 行",
    )
    parser.add_argument("--rounds", type=int, default=1,
                        help="轮数（每轮 6 句；默认 1）")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP_S,
                        help=f"句间隔秒数（默认 {DEFAULT_GAP_S:g}，不得小于 {MIN_GAP_S:g}）")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"say 语音（默认 {DEFAULT_VOICE}）")
    parser.add_argument("--rate", type=int, default=DEFAULT_RATE,
                        help=f"say 语速（默认 {DEFAULT_RATE}；实测 190 比 150 少回声）")
    parser.add_argument("--device", default=DEFAULT_DEVICE,
                        help=f"写进 marks 的 device_id（音源标签，默认 {DEFAULT_DEVICE}；"
                             "不控制真机）")
    parser.add_argument("--wake-prefix", default=DEFAULT_WAKE_PREFIX,
                        help=f"非 hello 句前先播的唤醒词（默认 {DEFAULT_WAKE_PREFIX}；"
                             "传空串 '' 关闭配对）")
    parser.add_argument("--wake-lead", type=float, default=DEFAULT_WAKE_LEAD_S,
                        help=f"唤醒词与指令之间的停顿秒数（默认 {DEFAULT_WAKE_LEAD_S:g}，"
                             f"允许 {MIN_WAKE_LEAD_S:g}-{MAX_WAKE_LEAD_S:g}）")
    parser.add_argument("--out", default=None,
                        help="marks 输出路径（默认 /tmp/rui-fleet-marks-<时间戳>.csv）")
    parser.add_argument("--append", action="store_true",
                        help="允许追加到已存在的 marks 文件（表头必须一致）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印完整计划与将写的 marks 行：不发声、不写盘")
    args = parser.parse_args(argv)

    if args.rounds < 1:
        parser.error("--rounds 必须 >= 1")
    if args.rate <= 0:
        parser.error("--rate 必须 > 0")
    if args.gap < MIN_GAP_S:
        parser.error(
            f"--gap 必须 >= {MIN_GAP_S:g} s（更短的间隔会让上一句的回复落进下一句的窗口，"
            "时间窗归属就不可信了）"
        )
    if not MIN_WAKE_LEAD_S <= args.wake_lead <= MAX_WAKE_LEAD_S:
        parser.error(
            f"--wake-lead 必须在 {MIN_WAKE_LEAD_S:.1f}-{MAX_WAKE_LEAD_S:.1f} s："
            f"小于 {MIN_WAKE_LEAD_S:.1f} s 唤醒词与指令会被 VAD 并成一段，"
            f"大于 {MAX_WAKE_LEAD_S:.1f} s 会越过 plane 的 5 s follow-up gate、"
            "wake 被丢弃（实测 2.5 s 时 KWS 命中只有 11/30）"
        )

    start = now()
    out_path = Path(args.out) if args.out else default_out(start)
    plan = build_plan(rounds=args.rounds, gap_s=args.gap, voice=args.voice,
                      rate=args.rate, start=start, wake_prefix=args.wake_prefix,
                      wake_lead=args.wake_lead)

    if args.dry_run:
        print_dry_run(plan, out_path, args, start)
        return 0

    writer = MarksWriter(out_path, append=args.append)
    try:
        writer.open()
    except (OSError, ValueError) as exc:
        print(f"fleet_say: {exc}", file=sys.stderr)
        return 2

    if writer.existed:
        existing = existing_mark_keys(out_path)
        planned = {(step.phrase_id, args.device, str(step.round)) for step in plan}
        overlap = existing & planned
        if overlap:
            rounds = "、".join(sorted({item[2] or "?" for item in overlap}))
            print(f"fleet_say: 警告：--append 与已有 marks 有 {len(overlap)} 行重复 "
                  f"(phrase/device/round)，涉及 round {rounds}；"
                  "fleet_matrix 会按重复丢弃这些行 → 这轮采样可能白跑。", file=sys.stderr)

    print(f"fleet_say: {len(plan)} 句 → {out_path}"
          "（Ctrl-C 可中断，已写行保留）", file=sys.stderr)
    interrupted = False
    failure = ""
    try:
        for index, step in enumerate(plan):
            # Row first, sound second: the matrix needs the window start even if the
            # process dies before the first syllable comes out.
            writer.write(mark_row(step, now(), device_id=args.device,
                                  voice=args.voice, rate=args.rate))
            try:
                if step.wake_argv is not None:
                    runner(list(step.wake_argv))
                    if step.wake_lead:
                        sleeper(step.wake_lead)
                if step.instruction_argv is not None:
                    runner(list(step.instruction_argv))
            except FileNotFoundError:
                writer.mark_last_failed()
                raise
            except subprocess.CalledProcessError:
                writer.mark_last_failed()
                raise
            if index + 1 < len(plan):
                sleeper(step.gap_after)
    except KeyboardInterrupt:
        interrupted = True
    except FileNotFoundError as exc:
        failure = f"找不到本机 say 命令（{exc.filename or exc}）；本脚本只支持 macOS say"
    except subprocess.CalledProcessError as exc:
        failure = f"say 退出码 {exc.returncode}：{exc.cmd}"
    finally:
        writer.close()

    if interrupted:
        print(f"\nfleet_say: 已中断；{writer.rows_written} 行已写盘（{out_path}），没有丢行",
              file=sys.stderr)
        return 130
    if failure:
        print(f"fleet_say: {failure}；已写 {writer.rows_written} 行（{out_path}，"
              "失败行 notes 已标 say_failed）", file=sys.stderr)
        return 2
    print(f"fleet_say: 完成，{writer.rows_written} 行 marks 已写盘：{out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
