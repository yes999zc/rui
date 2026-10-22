"""One tool shape for local code, MCP servers and Home Assistant entities."""
from __future__ import annotations

import re
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
        """The OpenAI `tools[].function` entry.

        Two things here were wrong until tools were actually wired in:
        - `parameters`, not `input_schema`: an unknown key is ignored by the vendor, and the
          model then sees a function that takes no arguments at all.
        - the name cannot be the registry's `source.tool` form. Function names are
          `[A-Za-z0-9_-]{1,64}`; a dot is rejected outright (DeepSeek answers 400).
        """
        return {
            "name": self.llm_name,
            "description": self.description,
            "parameters": self.input_schema or {"type": "object", "properties": {}},
        }

    @property
    def llm_name(self) -> str:
        """`local.weather` -> `weather`: what the model calls, and what comes back."""
        tail = self.name.split(".", 1)[-1]
        return re.sub(r"[^A-Za-z0-9_-]", "_", tail)[:64]


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult]]
