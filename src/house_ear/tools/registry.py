"""Tool registry: namespacing, dedupe, and a hard cap on what reaches the model.

423 Home Assistant entities as 423 tools destroys the prompt. Exposure defaults to
domain-level templates with entity arguments, plus an explicit whitelist.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .contract import Permission, ToolHandler, ToolSpec

MAX_EXPOSED_TOOLS = 50
_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")


class ToolError(ValueError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler | None = None) -> None:
        if not _NAME_RE.match(spec.name):
            raise ToolError(f"tool name must be namespaced as <source>.<tool>: {spec.name}")
        if spec.name in self._specs:
            raise ToolError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler

    def unregister_source(self, prefix: str) -> None:
        for name in [n for n in self._specs if n.startswith(prefix + ".")]:
            self._specs.pop(name, None)
            self._handlers.pop(name, None)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ToolError(f"unknown tool: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._specs)

    def expose(self, budget: int = MAX_EXPOSED_TOOLS) -> list[ToolSpec]:
        """Read-only first, then write; dangerous tools never auto-exposed."""
        ranked = sorted(
            self._specs.values(),
            key=lambda s: (s.permission is not Permission.READ, s.name),
        )
        return [s for s in ranked if not s.needs_confirm][:budget]

    def llm_tools(self, budget: int = MAX_EXPOSED_TOOLS) -> list[dict]:
        return [s.as_llm_tool() for s in self.expose(budget)]

    async def call(self, name: str, args: dict) -> object:
        spec = self.get(name)
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolError(f"tool has no handler yet: {name} ({spec.source})")
        return await handler(args)

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9_]", "_", value.lower()).strip("_")

    @classmethod
    def namespace(cls, server: str, tool: str) -> str:
        return f"{cls._slug(server)}.{cls._slug(tool)}"

    @staticmethod
    def from_mcp(server: str, tools: Iterable[dict]) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for t in tools:
            out.append(
                ToolSpec(
                    name=ToolRegistry.namespace(server, t["name"]),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}) or {},
                    permission=Permission.WRITE,
                    source=f"mcp:{server}",
                )
            )
        return out
