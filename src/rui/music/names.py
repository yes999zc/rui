"""Chinese nicknames -> the name the library actually holds, and the alias table.

Ported from the old build (`songname.py` + `taste.aliases`), which solved a
measured case: 「播放战车乐队的歌」 found nothing, because the library files the band
as Rammstein. The old build's rule is the important part:

* the **model only translates** - it returns up to three candidate proper names,
  never a search query, and never a decision;
* the **library only searches** - every candidate is verified with the same
  `accepts` guard a literal hit goes through;
* a candidate that worked is **remembered** as an alias, so the model is asked
  once per nickname, and an alias hit skips the model entirely.

`confirms` (the YES/NO judge for online candidates) is not ported: this build has
no online source yet. When one lands, it belongs next to it, with the old
build's fail-open rule.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..security.netguard import OutboundPolicy, guarded_async_client

log = logging.getLogger(__name__)

# The old build's prompt, verbatim (songname.py:18-22): translate, at most three
# candidates, JSON array only, empty array when unsure.
PROMPT = (
    "用户在智能音箱上点歌，说的是中文，可能是俗称、意译、音译或者半截名字。"
    "请给出他最可能想听的歌手/乐队/歌曲的正式名称（原文，通常是拉丁字母），最多 3 个候选。"
    '只输出 JSON 数组，例如 ["Rammstein"]；拿不准就输出 []。不要解释，不要输出别的。'
)


class AliasStore:
    """Nickname -> real name, persisted in data_dir (not a temp cache).

    The old build kept aliases in `.cache/music_taste.json`, where they survived
    until someone cleaned the cache - its own 「高强=高旗」 entry is gone. This one
    lives with the rest of the data and is written atomically.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._aliases: dict[str, str] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._aliases = {str(k): str(v) for k, v in (data or {}).items()}
        except (OSError, ValueError):
            self._aliases = {}

    def apply(self, text: str) -> str:
        """Rewrite known nicknames inside a query, longest first (nested keys)."""
        out = text or ""
        for wrong in sorted(self._aliases, key=len, reverse=True):
            right = self._aliases[wrong]
            if wrong and wrong in out:
                out = out.replace(wrong, right)
        return out

    def remember(self, wrong: str, right: str) -> None:
        wrong, right = (wrong or "").strip(), (right or "").strip()
        if not wrong or not right or wrong == right:
            return
        if self._aliases.get(wrong) == right:
            return
        self._aliases[wrong] = right
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._aliases, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            os.chmod(self.path, 0o600)
            log.info("music alias: %s -> %s", wrong, right)
        except OSError as exc:
            log.info("music alias write failed: %s", exc)

    def get(self, name: str) -> str:
        return self._aliases.get(name, "")


class NameResolver:
    """Ask the model for proper-name candidates; never block a play request."""

    def __init__(self, aliases: AliasStore, timeout_s: float = 6.0,
                 policy: OutboundPolicy | None = None) -> None:
        self.aliases = aliases
        self.timeout_s = timeout_s
        # `DEEPSEEK_BASE_URL` is operator-controlled, but the request still goes
        # through the same outbound policy as every other tool URL.
        self.policy = policy or OutboundPolicy()
        self._cache: dict[str, list[str]] = {}

    async def candidates(self, name: str) -> list[str]:
        key = (name or "").strip()
        if not key:
            return []
        if key in self._cache:
            return self._cache[key]
        known = self.aliases.get(key)
        if known:
            self._cache[key] = [known]
            return [known]
        out = await self._ask(key)
        # Empty is cached too: one bad nickname must not cost a model call per turn.
        self._cache[key] = out
        return out

    async def _ask(self, name: str) -> list[str]:
        from .. import secrets  # a console-written key must work here too

        api_key = secrets.get("DEEPSEEK_API_KEY")
        if not api_key:
            return []           # no key: the literal search already failed, move on
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        payload = {
            "model": "deepseek-flash",
            "messages": [{"role": "system", "content": PROMPT},
                         {"role": "user", "content": name}],
            "temperature": 0, "max_tokens": 60,
            "thinking": {"type": "disabled"},
        }
        try:
            async with guarded_async_client(self.policy,
                                            timeout=self.timeout_s) as client:
                resp = await client.post(f"{base}/chat/completions",
                                         headers={"Authorization": f"Bearer {api_key}"},
                                         json=payload)
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001 - never block a play request
            log.info("music: name lookup failed (%s: %s)", type(exc).__name__, exc)
            return []
        out = _clean(text)
        log.info("music: %s -> %s", name, out)
        return out


def _clean(raw: str) -> list[str]:
    """Pull the JSON array out of whatever wrapper the model used."""
    import re

    m = re.search(r"\[[^\]]*\]", raw or "")
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip().strip("《》\"'")
            if text and text not in out:
                out.append(text[:60])
    return out[:3]
