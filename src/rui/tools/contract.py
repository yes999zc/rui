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
    # The V-line declaration (docs/interaction.md §3): which system-layer
    # phrases render this tool's results. Empty = the handler's own text.
    interaction: InteractionSpec | None = None

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
    """What a handler returns: state + slots, never its own wording.

    The phrase is rendered by the system layer (`session/phrases.py`) from the
    tool's InteractionSpec - the V-line rule that ended four speech dialects in
    one stroke (docs/interaction.md §3). `say` is the escape hatch for genuinely
    free-form content (a weather report is data, not a template) and is spoken
    verbatim. A plain `str` return value still works and means the same as
    `say`: tools migrate at their own pace.
    """

    status: str = "ok"          # ok | empty | unreachable | denied | options
    payload: dict[str, Any] = field(default_factory=dict)
    options: list[dict[str, Any]] = field(default_factory=list)   # choose consumes (V2)

    def slot(self, **slots: object) -> ToolResult:
        """`ToolResult().slot(title="晴天", count=1)` - the common one-liner."""
        self.payload.update(slots)
        return self

    @staticmethod
    def say(text: str) -> ToolResult:
        return ToolResult(status="ok", payload={"say": text})


@dataclass(frozen=True)
class InteractionSpec:
    """How the system layer interacts for this tool. Declared, never implemented.

    `exec` maps a ToolResult status to a PhraseBook key; the book renders the
    words. `choose`/`danger`/`filler` are the V2 hooks the HA set will exercise;
    declaring them now costs nothing and makes that migration one diff.
    """

    exec: dict[str, str] = field(default_factory=dict)
    choose: str = "first"       # first | top3 | ask (V2)
    danger: str = "none"        # none | confirm | excluded (V2)
    filler: str = "auto"        # auto | none
    # "silent": the action IS the answer, so the turn says nothing (old build:
    # "@@PLAY -> the song is the answer; talking over it would be noise").
    answer: str = "text"        # text | silent
    # "music_request": refuse the call unless the user's own words were a music
    # ask. Guards against the model repeating a previous turn's call (measured
    # 10:00:50: a weather question also called music_play).
    guard: str = ""             # "" | music_request


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult | str]]
