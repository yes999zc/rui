"""One tool shape for local code, MCP servers and Home Assistant entities."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Permission(StrEnum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


@dataclass(frozen=True)
class ToolSpec:
    name: str                    # namespaced: "ha.light_turn_on"
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    permission: Permission = Permission.READ
    source: str = "local"        # local | mcp:<server> | ha
    needs_confirm: bool = False

    def as_llm_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult]]
