"""Shared test setup.

0.7.6 moved the in-tree LX06 plug out of the image. The core test-suite still pins
that adapter's contract and wire behaviour, so the frozen copy under
tests/fixtures/legacy_lx06 is exposed through the same operator hook the image
uses (RUI_PLUGINS_DIR). It is a test fixture only; the built image does not copy
tests/. 0.7.6-b (task-13) ports these cases to rui-xiaoai-bridge and deletes it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _legacy_lx06_plugin_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import shutil
    root = tmp_path / "legacy-plugins"
    root.mkdir()
    shutil.copytree(FIXTURE_ROOT / "legacy_lx06", root / "legacy_lx06")
    monkeypatch.setenv("RUI_PLUGINS_DIR", str(root))
