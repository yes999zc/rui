#!/usr/bin/env python
"""Summarise an S5a sweep: how often the request survives intact, per configuration.

    uv run python scripts/summarize_ack.py /tmp/rui-scratch/rui-s5a

Each jsonl is one configuration (preroll / gap / ack mode) written by
`measure_turn_pipeline.py --asr-only`. The reference transcript is the most common one in
the whole sweep - that is what "nothing lost" looks like - and every round is classified
against it: intact, missing the head of the request, or something else.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".")
    files = sorted(root.glob("*.jsonl"))
    if not files:
        print(f"no jsonl under {root}")
        return 1
    data = {path.stem: load(path) for path in files}
    counts = Counter(r["transcript"].strip() for rows in data.values() for r in rows)
    # The complete request is the longest transcript heard: "most common" would pick
    # whatever the buggy configuration produced, which is how a lossy variant becomes the
    # yardstick. Ties go to the more frequent one.
    longest = max(len(t) for t in counts if t)
    reference = max((t for t in counts if len(t) == longest), key=lambda t: counts[t])
    print(f"reference (longest) transcript: {reference!r} - heard {counts[reference]} times "
          f"of {sum(len(r) for r in data.values())} rounds\n")
    print(f"{'config':22s} {'n':>3s} {'intact':>7s} {'head lost':>10s} {'other':>6s} "
          f"{'lag p50':>8s} {'preroll':>8s} {'ack':>8s}")
    for name, rows in data.items():
        intact = head_lost = other = 0
        for r in rows:
            text = r["transcript"].strip()
            if text == reference:
                intact += 1
            elif text and reference.endswith(text):
                head_lost += 1  # the end of the request arrived, its start did not
            else:
                other += 1
        lags = [r["wake_lag_ms"] for r in rows if r.get("wake_lag_ms")]
        prerolls = [r["preroll_replayed_ms"] for r in rows if r.get("preroll_replayed_ms")]
        acks = {(r.get("ack_mode"), r.get("ack_plays")) for r in rows}
        print(f"{name:22s} {len(rows):3d} {intact:7d} {head_lost:10d} {other:6d} "
              f"{(statistics.median(lags) if lags else 0):7.0f}ms "
              f"{(statistics.median(prerolls) if prerolls else 0):7.0f}ms "
              f"{str(sorted(a for a in acks if a[0]))[:8]:>8s}")
    print("\ntranscripts per configuration:")
    for name, rows in data.items():
        dist = Counter(r["transcript"].strip() or "<empty>" for r in rows)
        print(f"  {name}: " + ", ".join(f"{t!r} x{n}" for t, n in dist.most_common()))
    errors = [
        (name, r["ack_error"]) for name, rows in data.items() for r in rows if r.get("ack_error")
    ]
    if errors:
        print("\nack downgrades:")
        for name, note in sorted(set(errors)):
            print(f"  {name}: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
