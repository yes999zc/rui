"""Memory: what the assistant is allowed to remember, and who decides.

Ported from the old build's `memory.py` (which took the idea from 虾哥's 小智):
memory is **curated**, not automatic. A candidate becomes memory only after it
is confirmed - in the old build through the console's memory page. That is the
whole difference between "it remembers that I like 周杰伦" and "it remembers
everything I said".

Two kinds of entry live in the same file:

* **confirmed** - injected into the prompt every turn (a handful of lines);
* **candidate** - extracted from a turn, listed in the console, waiting for the
  user to accept or edit it. Never injected.

The store sits in `data_dir`, not a temp cache: the old build's aliases lived in
`.cache/` and were lost to a cleanup.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MAX_ITEMS = 200
# Utterances that ask the assistant to remember something. Deliberately explicit:
# the old build extracted candidates in the background with a model call; here the
# phrase itself is the signal, and the console confirms.
# (pattern, keep_whole_match). 「记住…」 keeps only the fact; 「以后…」 and
# 「我喜欢…」 are self-contained and read better with their trigger word.
REMEMBER_PATTERNS = (
    (re.compile(r"(?:记住|记一下|记着)[，,:：\s]*(.{2,60})"), False),
    (re.compile(r"(?:以后|今后).{2,60}"), True),
    (re.compile(r"我(?:喜欢|爱听|爱喝|爱吃|不吃|不喜欢).{2,40}"), True),
)


@dataclass
class MemoryItem:
    id: str
    text: str
    created: int
    source: str = "voice"      # voice | console | turn
    confirmed: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "created": self.created,
                "source": self.source, "confirmed": self.confirmed}


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: list[MemoryItem] = []
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._items = []
            return
        out: list[MemoryItem] = []
        for raw in (data.get("items") if isinstance(data, dict) else data) or []:
            try:
                out.append(MemoryItem(
                    id=str(raw["id"]), text=str(raw["text"]),
                    created=int(raw.get("created") or 0),
                    source=str(raw.get("source") or "voice"),
                    confirmed=bool(raw.get("confirmed")),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        self._items = out[:MAX_ITEMS]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"items": [i.as_dict() for i in self._items]},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.info("memory: write failed: %s", exc)

    # ------------------------------------------------------------------ api
    def items(self, *, confirmed: bool | None = None) -> list[MemoryItem]:
        if confirmed is None:
            return list(self._items)
        return [i for i in self._items if i.confirmed is confirmed]

    def add(self, text: str, *, source: str = "voice", confirmed: bool = True) -> MemoryItem:
        clean = (text or "").strip()[:200]
        if not clean:
            raise ValueError("memory text is empty")
        item = MemoryItem(id=uuid.uuid4().hex[:8], text=clean, created=int(time.time()),
                          source=source, confirmed=confirmed)
        self._items.append(item)
        del self._items[:-MAX_ITEMS]
        self._save()
        return item

    def confirm(self, item_id: str, text: str | None = None) -> MemoryItem | None:
        for item in self._items:
            if item.id == item_id:
                item.confirmed = True
                if text and text.strip():
                    item.text = text.strip()[:200]
                self._save()
                return item
        return None

    def remove(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [i for i in self._items if i.id != (item_id or "").strip()]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def forget_text(self, needle: str) -> int:
        """Remove confirmed items whose text contains `needle` (voice 「忘掉…」)."""
        key = (needle or "").strip()
        if not key:
            return 0
        before = len(self._items)
        self._items = [i for i in self._items if key not in i.text]
        if len(self._items) != before:
            self._save()
        return before - len(self._items)

    def confirmed_texts(self, limit: int = 20) -> list[str]:
        return [i.text for i in self.items(confirmed=True)][-limit:]

    def extract(self, transcript: str) -> MemoryItem | None:
        """A candidate from an utterance that asked to be remembered."""
        text = ""
        for pattern, whole in REMEMBER_PATTERNS:
            m = pattern.search(transcript or "")
            if m:
                text = (m.group(0) if whole else m.group(1)).strip(" 。，,.!！？?")
                break
        if not text or len(text) < 2:
            return None
        if any(text in item.text or item.text in text for item in self._items):
            return None
        return self.add(text, source="turn", confirmed=False)
