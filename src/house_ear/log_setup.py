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

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "info") -> None:
    """Configure the root logger once, at the level the config asks for."""
    root = logging.getLogger()
    if root.handlers:  # uvicorn or pytest already owns the output
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=FORMAT,
        stream=sys.stderr,
    )
