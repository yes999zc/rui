#!/usr/bin/env python
"""Turn coverage.py JSON + the pytest JUnit XML into the 0.7.x coverage baseline.

    uv run pytest --cov=rui --cov-branch --cov-report=term-missing \\
        --cov-report=json:coverage.json --junit-xml=pytest-report.xml
    uv run python scripts/coverage_report.py \\
        --coverage-json coverage.json --junit-xml pytest-report.xml

The script prints a Markdown table (the one pasted into
``docs/audit/coverage-baseline.md``), writes the machine-readable baseline to
``docs/audit/coverage-baseline.json`` and is the tool a later release uses to
answer "did coverage regress?":

    uv run python scripts/coverage_report.py \\
        --coverage-json coverage.json --junit-xml pytest-report.xml \\
        --compare docs/audit/coverage-baseline.json

``--compare`` exits 1 when overall or any stored package line/branch percentage
drops (persisted values are rounded to two decimals); in that mode no file is
written, so a baseline can never be overwritten by the run that checks it.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = "src/rui/"
DEFAULT_OUTPUT = ROOT / "docs/audit/coverage-baseline.json"
DEFAULT_COMMAND = (
    "uv run pytest --cov=rui --cov-branch --cov-report=term-missing "
    "--cov-report=json:coverage.json --junit-xml=pytest-report.xml"
)


def _percent(covered: int, total: int) -> float:
    return round(100.0 * covered / total, 2) if total else 100.0


def _relative(path: str) -> str:
    """Normalise a coverage path to ``src/rui/...`` regardless of cwd/OS."""
    normalised = path.replace("\\", "/")
    marker = normalised.rfind("/" + SOURCE_ROOT)
    if marker >= 0:
        return normalised[marker + 1 :]
    if normalised.startswith(SOURCE_ROOT):
        return normalised
    return normalised


def _package_of(relative: str) -> str:
    rest = relative[len(SOURCE_ROOT) :]
    head, separator, _tail = rest.partition("/")
    return head if separator else "(root)"


def _summary(entry: dict[str, Any]) -> dict[str, Any]:
    summary = entry.get("summary", {})
    statements = int(summary.get("num_statements", 0))
    covered = int(summary.get("covered_lines", 0))
    branches = int(summary.get("num_branches", 0))
    covered_branches = int(summary.get("covered_branches", 0))
    return {
        "line_percent": _percent(covered, statements),
        "branch_percent": _percent(covered_branches, branches),
        "covered_lines": covered,
        "num_statements": statements,
        "covered_branches": covered_branches,
        "num_branches": branches,
    }


def _packages(files: dict[str, Any]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, int]] = {}
    for path, entry in files.items():
        relative = _relative(path)
        if not relative.startswith(SOURCE_ROOT):
            continue
        name = _package_of(relative)
        bucket = totals.setdefault(name, {"c": 0, "s": 0, "cb": 0, "b": 0})
        summary = entry.get("summary", {})
        bucket["c"] += int(summary.get("covered_lines", 0))
        bucket["s"] += int(summary.get("num_statements", 0))
        bucket["cb"] += int(summary.get("covered_branches", 0))
        bucket["b"] += int(summary.get("num_branches", 0))
    return {
        name: {
            "line_percent": _percent(bucket["c"], bucket["s"]),
            "branch_percent": _percent(bucket["cb"], bucket["b"]),
            "covered_lines": bucket["c"],
            "num_statements": bucket["s"],
        }
        for name, bucket in sorted(totals.items())
    }


def _test_counts(report: Path) -> dict[str, int]:
    root = ElementTree.parse(report).getroot()
    cases = list(root.iter("testcase"))
    skipped = xfail = failed = 0
    for case in cases:
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
            continue
        skipped_node = case.find("skipped")
        if skipped_node is None:
            continue
        if skipped_node.get("type") == "pytest.xfail":
            xfail += 1
        else:
            skipped += 1
    return {
        "collected": len(cases),
        "passed": len(cases) - failed - skipped - xfail,
        "skipped": skipped,
        "xfail": xfail,
        "failed": failed,
    }


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _git_commit() -> str:
    return _git("rev-parse", "--short", "HEAD")


def _worktree_dirty() -> bool | None:
    status = _git("status", "--porcelain")
    return None if status == "unknown" else bool(status)


def _build(coverage: Path, junit: Path | None, command: str) -> dict[str, Any]:
    data = json.loads(coverage.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    overall = _summary({"summary": totals})
    return {
        "schema": "rui-coverage-baseline/v1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "worktree_dirty": _worktree_dirty(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "command": command,
        "tests": _test_counts(junit) if junit else None,
        "overall": overall,
        "packages": _packages(data.get("files", {})),
    }


def _markdown(baseline: dict[str, Any]) -> str:
    lines = [
        "| scope | line % | covered / statements | branch % |",
        "| --- | ---: | ---: | ---: |",
    ]
    overall = baseline["overall"]
    for name, row in baseline["packages"].items():
        lines.append(
            f"| `{name}` | {row['line_percent']:.2f} | "
            f"{row['covered_lines']} / {row['num_statements']} | "
            f"{row['branch_percent']:.2f} |"
        )
    lines.append(
        f"| **total** | {overall['line_percent']:.2f} | "
        f"{overall['covered_lines']} / {overall['num_statements']} | "
        f"{overall['branch_percent']:.2f} |"
    )
    tests = baseline.get("tests")
    if tests:
        lines.append("")
        lines.append(
            "tests: collected={collected} passed={passed} skipped={skipped} "
            "xfail={xfail} failed={failed}".format(**tests)
        )
    return "\n".join(lines)


def _compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    regressions: list[str] = []
    for field in ("line_percent", "branch_percent"):
        now = current["overall"][field]
        was = baseline.get("overall", {}).get(field)
        if isinstance(was, (int, float)) and round(now, 2) < round(was, 2):
            regressions.append(f"overall {field}: {now:.2f}% < baseline {was:.2f}%")
    now_packages = current["packages"]
    for name, was_row in baseline.get("packages", {}).items():
        now_row = now_packages.get(name)
        if now_row is None:
            regressions.append(f"{name}: package missing (baseline {was_row['line_percent']:.2f}%)")
            continue
        if now_row["line_percent"] < was_row["line_percent"]:
            regressions.append(
                f"{name} line_percent: {now_row['line_percent']:.2f}% "
                f"< baseline {was_row['line_percent']:.2f}%"
            )
        if now_row["branch_percent"] < was_row.get("branch_percent", 0):
            regressions.append(
                f"{name} branch_percent: {now_row['branch_percent']:.2f}% "
                f"< baseline {was_row['branch_percent']:.2f}%"
            )
    return regressions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coverage-json", type=Path, default=Path("coverage.json"))
    parser.add_argument("--junit-xml", type=Path, default=Path("pytest-report.xml"))
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--command", default=DEFAULT_COMMAND)
    parser.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="compare against a stored baseline and exit 1 on a regression",
    )
    args = parser.parse_args(argv)

    if not args.coverage_json.is_file():
        print(f"coverage json not found: {args.coverage_json}", file=sys.stderr)
        return 2
    junit = args.junit_xml if args.junit_xml.is_file() else None
    if junit is None:
        print(
            f"warning: {args.junit_xml} not found; test counts are omitted",
            file=sys.stderr,
        )

    baseline = _build(args.coverage_json, junit, args.command)
    print(_markdown(baseline))

    if args.compare is not None:
        if not args.compare.is_file():
            print(f"baseline not found: {args.compare}", file=sys.stderr)
            return 2
        stored = json.loads(args.compare.read_text(encoding="utf-8"))
        regressions = _compare(baseline, stored)
        if regressions:
            print("\ncoverage regression against " + str(args.compare) + ":", file=sys.stderr)
            for line in regressions:
                print("  " + line, file=sys.stderr)
            return 1
        print(f"\nno regression against {args.compare}")
        return 0

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
