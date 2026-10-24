from __future__ import annotations

import datetime as dt
from pathlib import Path

from rui.session.memory import MemoryStore
from rui.tools.reminders import ReminderStore


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_memory_file_is_private(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.json")
    store.add("我喜欢喝茶", source="console", confirmed=True)
    assert _mode(store.path) == 0o600


def test_reminders_file_is_private(tmp_path: Path) -> None:
    store = ReminderStore(tmp_path)
    now = dt.datetime.now(dt.UTC)
    store.add("明天带伞", now + dt.timedelta(minutes=5), now=now)
    assert _mode(store.path) == 0o600
