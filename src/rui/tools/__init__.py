"""Local tools: the registry contract, plus one place that assembles what ships.

`web/api/app.py` still imports `tools.info.build_tools` directly; reminders are reachable
through `build_local_tools()` so the next layer changes one import instead of two — both
builders return the same `[(ToolSpec, handler)]` shape.
"""
from __future__ import annotations

from typing import Any

from ..config.schema import Config
from .contract import Permission, ToolResult, ToolSpec
from .registry import ToolRegistry
from .reminders import build_scheduler
from .reminders import build_tools as build_reminder_tools

__all__ = [
    "Permission",
    "ToolResult",
    "ToolSpec",
    "ToolRegistry",
    "build_local_tools",
    "build_reminder_tools",
    "build_scheduler",
]


def build_local_tools(cfg: Config) -> list[tuple[ToolSpec, Any]]:
    """Every local tool this process exposes: information, jokes, then reminders."""
    from .info import build_tools as build_info_tools
    from .joke import build_tools as build_joke_tools

    return [*build_info_tools(cfg), *build_joke_tools(cfg), *build_reminder_tools(cfg)]
