"""P2 (audit 2026-10-23): the [level] line was log noise at one line every 2 s.

The measurement stays (it is how a threshold change is judged) and the console's log
filter still keys on "voice peak"; only the cadence changes to one line per 30 s.
"""
from __future__ import annotations

from rui.audio.process import LEVEL_LOG_EVERY, METRICS_EVERY_S


def test_the_level_line_is_thirty_seconds_not_every_metrics_tick() -> None:
    assert METRICS_EVERY_S == 2.0
    assert LEVEL_LOG_EVERY * METRICS_EVERY_S == 30.0


def test_httpx_request_urls_are_not_logged_at_info() -> None:
    """Security audit round 2 P1-3: Navidrome's token+salt lives in the request URL."""
    import logging

    from rui.log_setup import setup_logging

    setup_logging("info")
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
