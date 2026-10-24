"""The memory tools: what the model may write and read, and nothing else.

Three tools, the old build's shape:
- `remember` writes a *confirmed* entry - the user said 「记住…」, so there is
  nothing to confirm later;
- `forget` removes by text (「忘掉…」);
- `recall` answers 「你还记得什么」 from the store, never from the model's memory.

Candidates (turns that merely mentioned a preference) are never written here:
they are extracted by the app and wait for the console.
"""
from __future__ import annotations

import logging
from typing import Any

from ..session.memory import MemoryStore
from .contract import InteractionSpec, Permission, ToolResult, ToolSpec

log = logging.getLogger(__name__)


def build_memory_tools(store: MemoryStore) -> list[tuple[ToolSpec, Any]]:
    async def remember(args: dict[str, Any]) -> ToolResult:
        text = str(args.get("text") or "").strip()
        if not text:
            return ToolResult(status="empty")
        item = store.add(text, source="voice", confirmed=True)
        return ToolResult(status="ok").slot(text=item.text)

    async def forget(args: dict[str, Any]) -> ToolResult:
        text = str(args.get("text") or "").strip()
        removed = store.forget_text(text)
        return ToolResult(status="ok" if removed else "empty").slot(text=text)

    async def recall(_args: dict[str, Any]) -> ToolResult:
        texts = store.confirmed_texts(limit=8)
        if not texts:
            return ToolResult(status="empty")
        return ToolResult(status="ok").slot(items="、".join(texts))

    return [
        (ToolSpec(
            name="local.remember", permission=Permission.WRITE,
            description="用户让你记住一件事（「记住…」「以后都…」）时调用，text 写要记住的那件事。"
                        "只回一句确认。",
            input_schema={"type": "object", "properties": {
                "text": {"type": "string", "description": "要记住的事，一句话"}},
                "required": ["text"]},
            interaction=InteractionSpec(exec={"ok": "memory.saved",
                                              "empty": "memory.empty_text"}),
        ), remember),
        (ToolSpec(
            name="local.forget", permission=Permission.WRITE,
            description="用户让你忘掉某件事（「忘掉…」「别记着…」）时调用，text 写那件事的关键词。",
            input_schema={"type": "object", "properties": {
                "text": {"type": "string"}}, "required": ["text"]},
            interaction=InteractionSpec(exec={"ok": "memory.forgotten",
                                              "empty": "memory.not_found"}),
        ), forget),
        (ToolSpec(
            name="local.recall", permission=Permission.READ,
            description="用户问「你还记得什么/我让你记过什么」时调用，按存储回答，不要凭印象编。",
            interaction=InteractionSpec(exec={"ok": "memory.recall",
                                              "empty": "memory.recall_empty"}),
        ), recall),
    ]
