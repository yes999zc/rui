"""The console reports this version; keep it in sync with the package metadata."""
from __future__ import annotations

import pathlib
import tomllib

from house_ear import __version__


def test_version_matches_pyproject() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == data["project"]["version"]
