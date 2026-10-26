"""Regression guard: a speaker brand must not creep back into the core source.

What this blocks: the model tokens ``xiaoai`` / ``lx06`` / ``小爱`` anywhere in the
files under ``src/rui`` (Python, resources such as ``config/template.yaml``, and
comments/docstrings). The functional decoupling itself happened in 0.6 M2 (the
adapter moved to ``tests/fixtures/legacy_lx06/`` and is loaded through ``runtime.entrypoint``; the
core has no import, branch or hardware-parameter dependency on it). This test does
not prove that decoupling - it only stops a later edit from reintroducing the brand
in code or copy, which is how the old coupling used to justify device branches.

Why ``legacy_adapters.py`` is allowed: it is the 0.7.6 migration table that rewrites
an old adapter spelling on load (removed in 0.8.0). Keeping the exception to one file
is the point.

Why ``src/rui/web`` is excluded: the remaining control-plane hits (hardcoded vendor
wake words, the 4412 fallback port, comments and the wizard i18n copy) are owned by a
separate task that may not touch this one's files. They are listed per file:line in
the hand-off table of ``docs/audit/xiaoai-decoupling.md``. Delete the exclusion when
that task closes so the guard covers the whole core.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "rui"

#: The one module allowed to spell the legacy adapter name (the 0.7.6 migration
#: table; deleted in 0.8.0 together with the rewrite).
ALLOWED_FILES = {"legacy_adapters.py"}
#: Temporary (task-13): the web control-plane strings are ported with the rest of
#: the plug tests. Remove this exclusion when that task closes.
EXCLUDED_DIRS = {"web"}
#: Model names and the vendor wake phrase. Case-insensitive: "LX06" and "lx06" are equal.
MODEL_TOKEN = re.compile(r"xiaoai|lx06|小爱", re.IGNORECASE)


def _leaks() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        relative = path.relative_to(SRC)
        if relative.name in ALLOWED_FILES:
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # a binary resource is not source text
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if MODEL_TOKEN.search(line):
                found.append(f"src/rui/{relative}:{number}: {line.strip()}")
    return found


def test_core_mentions_no_speaker_model() -> None:
    leaks = _leaks()
    assert not leaks, (
        "a speaker model name leaked back into the core; a plug manifest/adapter owns "
        "it now (docs/audit/xiaoai-decoupling.md):\n" + "\n".join(leaks)
    )
