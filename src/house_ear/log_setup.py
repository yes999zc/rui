"""One logging setup for every process that speaks about a turn.

`system.log_level` existed in the config but nothing applied it: the plane and the control
plane both logged through `log.info(...)` and the root logger sat at WARNING, so a run in
which the turn gate silently stopped handing over utterances printed nothing at all
(measured 2026-10-22, five rounds, one turn). Diagnostics that nobody can see are not
diagnostics.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The console's log page reads this file (docs/console.md P3). A separate file,
# not the process's stdout: under Docker that belongs to the runtime, and the
# endpoint must work the same in both deployments.
LOG_FILE_NAME = "service.log"


def setup_logging(level: str = "info", log_file: Path | None = None) -> None:
    """Configure the root logger once, at the level the config asks for.

    `log_file` adds a rotating file the console can tail; stderr stays, because
    that is what a developer watches while serving from a terminal.
    """
    root = logging.getLogger()
    level_no = getattr(logging, level.upper(), logging.INFO)
    if root.handlers:  # uvicorn or pytest already owns the output
        root.setLevel(level_no)
        return
    logging.basicConfig(level=level_no, format=FORMAT, stream=sys.stderr)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)


def tail_lines(path: Path, lines: int = 200) -> list[str]:
    """The last `lines` lines of a log file; [] when it does not exist yet."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            # A log is small by design (2 MB rotate), so reading it whole is fine
            # and avoids the seek-back guesswork of a chunked tail.
            return f.readlines()[-max(1, lines):]
    except OSError:
        return []
