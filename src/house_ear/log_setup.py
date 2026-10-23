"""One logging setup for every process that speaks about a turn.

`system.log_level` existed in the config but nothing applied it: the plane and the control
plane both logged through `log.info(...)` and the root logger sat at WARNING, so a run in
which the turn gate silently stopped handing over utterances printed nothing at all
(measured 2026-10-22, five rounds, one turn). Diagnostics that nobody can see are not
diagnostics.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The console's log page reads this file (docs/console.md P3). A separate file,
# not the process's stdout: under Docker that belongs to the runtime, and the
# endpoint must work the same in both deployments.
LOG_FILE_NAME = "service.log"


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler whose file is 0600 now and after every rollover.

    The log carries transcripts and device details; the default `open()` would
    create it 0644 under the usual umask. `_open` is the one place every writer
    (first open, rotation) passes through.
    """

    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


def _private(path: Path) -> None:
    """Create/repair the log file at 0600 before the handler can write to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        # A file we may append to but not chown (a bind mount owned by another uid)
        # must still let the service start; the 0600 create mode already applies to
        # files this process owns.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    finally:
        os.close(fd)


def setup_logging(level: str = "info", log_file: Path | None = None) -> None:
    """Configure the root logger once, at the level the config asks for.

    `log_file` adds a rotating file the console can tail; stderr stays, because
    that is what a developer watches while serving from a terminal.
    """
    root = logging.getLogger()
    level_no = getattr(logging, level.upper(), logging.INFO)
    # httpx logs every request URL at INFO. For Navidrome that URL carries the
    # reusable token+salt (security audit round 2, P1-3) and it drowned the turn/audio
    # lines in the console's log panel.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level_no, logging.WARNING))
    if root.handlers:  # uvicorn or pytest already owns the output
        root.setLevel(level_no)
        return
    logging.basicConfig(level=level_no, format=FORMAT, stream=sys.stderr)
    if log_file is not None:
        _private(log_file)
        handler = _PrivateRotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
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
