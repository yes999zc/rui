"""Home Assistant over MCP: the mature old-build plan, generalized.

What is ported verbatim (the old build ran it for weeks, gap report §3):
the six-tool whitelist, the entity list injected into the prompt as
``名字=entity_id`` **without state** ("stale truth is worse than none"), the
120-entry cap with dedupe, the 7-day on-disk cache that bridges an offline HA,
and the degrade rule - HA down never touches voice, music or local tools.

What is new on purpose:
- the server-side hard refusal for lock/alarm/valve: the old build only hid
  those domains in the console UI; the refusal now lives where calls execute;
- ToolResult + InteractionSpec: the HA tools declare their phrases, they do
  not carry them (docs/interaction.md).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..config.schema import HaConfig
from ..session import phrases
from .contract import InteractionSpec, Permission, ToolResult, ToolSpec
from .mcp_client import MCPClient, MCPUnavailable

log = logging.getLogger(__name__)

# The old build's set (assistant_v3.py:764): read-heavy, one control entry point.
DEFAULT_WHITELIST = (
    "get_state", "call_service", "list_areas", "search_entities",
    "batch_get_state", "get_system_status",
)
# Refused where calls execute, not hidden in a console list (old build's gap).
EXCLUDED_DOMAINS = ("lock.", "alarm_", "valve.")
SKIP_WORDS = ("指示灯", "确认", "已反转", "虚拟", "电量", "信号")
ENTITY_CAP = 120
CACHE_TTL_S = 7 * 86400
MAX_ENTITIES_TO_LIST = 6   # search_entities cap: the picker, not the house


class HaBridge:
    """One HA MCP server: connection, whitelist, entity context, tool handlers."""

    def __init__(self, cfg: HaConfig, data_dir: Path) -> None:
        url = cfg.base_url.rstrip("/")
        if not url.endswith("/api/mcp"):
            url = f"{url}/api/mcp"
        token = os.environ.get(cfg.token_env, "")
        self.cfg = cfg
        self.client = MCPClient(
            _server_cfg(url), headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        self._data_dir = data_dir
        self._entities_cache: list[str] | None = None

    async def start(self) -> bool:
        ok = await self.client.start()
        if ok:
            log.info("ha: connected via MCP (%s), %d tools listed",
                     self.cfg.base_url, len(self.client.tools))
        return ok

    async def stop(self) -> None:
        await self.client.stop()

    # ------------------------------------------------------------------ context
    async def entity_context(self) -> str:
        """The prompt block: names and ids, never states, cached for offline runs."""
        entities = await self._entities()
        if not entities:
            return ""
        listed = "\n".join(entities)
        note = "" if self.client._connected.is_set() else phrases.book.render("ha.list_stale")
        return phrases.book.render("ha.entity_list", count=len(entities),
                                   list=listed, note=note)

    async def _entities(self) -> list[str]:
        if self._entities_cache is not None:
            return self._entities_cache
        cache_path = self._data_dir / ".ha_entities.json"
        fresh: list[str] | None = None
        if self.client._connected.is_set():
            fresh = await self._fetch_entities()
        if fresh is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps({"t": int(time.time()), "entities": fresh},
                               ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            self._entities_cache = fresh
            return fresh
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("t") or 0) < CACHE_TTL_S:
                self._entities_cache = list(data.get("entities") or [])
                return self._entities_cache
        except (OSError, ValueError):
            pass
        return []

    async def _fetch_entities(self) -> list[str]:
        """`list_entities` per domain, states STRIPPED: the prompt carries names
        and ids only - a stale state is worse than none (old build ha.py:227)."""
        out: list[str] = []
        seen: set[str] = set()
        for domain in self.entity_domains():
            try:
                result = await self.client.call("list_entities",
                                                {"domain": domain, "limit": ENTITY_CAP})
            except MCPUnavailable:
                return []   # half a list is worse than the cache
            for item in _result_items(result):
                name = str(item.get("friendly_name") or item.get("name") or "").strip()
                eid = str(item.get("entity_id") or item.get("id") or "")
                if (not name or not eid or eid in seen
                        or any(w in name for w in SKIP_WORDS)):
                    continue
                seen.add(eid)
                out.append(f"{name}={eid}")
                if len(out) >= ENTITY_CAP:
                    return out
        return out

    def entity_domains(self) -> list[str]:
        # The old build's default five (assistant_v3.py:3726), configurable.
        return ["light", "switch", "cover", "scene", "script"]

    # ------------------------------------------------------------------ handlers
    def tool_specs(self) -> list[tuple[ToolSpec, Any]]:
        """The whitelisted six, with V-line declarations."""
        available = {t["name"]: t for t in self.client.tools}
        out: list[tuple[ToolSpec, Any]] = []
        for name in DEFAULT_WHITELIST:
            if name not in available:
                continue
            out.append(self._spec_for(name, available[name]))
        return out

    def _spec_for(self, name: str, raw: dict) -> tuple[ToolSpec, Any]:
        bridge = self

        if name == "call_service":
            async def call(args: dict[str, Any]) -> ToolResult:
                return await bridge._call_service(args)

            return ToolSpec(
                name="local.ha_call_service", permission=Permission.WRITE,
                description="控制家里设备（开/关/调节）。service 形如 switch.turn_on，"
                            "entity_id 用实体清单里的 id。没有清单里没有的设备先搜。",
                input_schema=raw.get("inputSchema") or {},
                interaction=InteractionSpec(
                    exec={"ok": "device.done", "empty": "device.missing_target",
                          "unreachable": "device.unreachable", "denied": "device.excluded"},
                    choose="ask", danger="excluded", filler="auto"),
            ), call

        async def reader(args: dict[str, Any]) -> ToolResult:
            return await bridge._read(name, args)

        return ToolSpec(
            name=f"local.ha_{name}", permission=Permission.READ,
            description=str(raw.get("description") or name),
            input_schema=raw.get("inputSchema") or {},
        ), reader

    async def _read(self, tool: str, args: dict[str, Any]) -> ToolResult:
        """The read tools are data for the model, not phrases: `say` passthrough."""
        try:
            result = await self.client.call(tool, args)
        except MCPUnavailable:
            return ToolResult(status="unreachable", payload={
                "say": phrases.book.render("device.unreachable", device="家庭设备")})
        return ToolResult.say(_compact(result))

    async def _call_service(self, args: dict[str, Any]) -> ToolResult:
        # HA's MCP wants `domain` and `service` separately; a model (or a test)
        # may hand over the combined "light.turn_on" spelling - accept both.
        service = str(args.get("service") or "")
        args = dict(args)
        if "." in service and not args.get("domain"):
            args["domain"], args["service"] = service.split(".", 1)
        target = args.get("entity_id") or ""
        if isinstance(target, list):
            target = ", ".join(str(t) for t in target)
        if not target:
            return ToolResult(status="empty")
        # The refusal the old build never had server-side (gap report §3.4-7).
        for prefix in EXCLUDED_DOMAINS:
            if prefix in str(args.get("entity_id", "")) or prefix.startswith(
                    str(args.get("domain") or "") + "."):
                return ToolResult(status="denied").slot(
                    device=str(args.get("domain") or _domain_of(target)), action="")
        try:
            await self.client.call("call_service", args)
        except MCPUnavailable as exc:
            log.info("ha call_service %s failed: %s", args.get("service"), exc)
            return ToolResult(status="unreachable").slot(device=_domain_of(target))
        return ToolResult(status="ok").slot(device=target,
                                            action=_action_word(str(args.get("service") or "")))

    @property
    def status(self) -> dict:
        return {
            "connected": self.client._connected.is_set(),
            "latency_ms": self.client.latency_ms,
            "tools": len(self.client.tools),
            "entities": len(self._entities_cache or []),
        }


def _server_cfg(url: str):
    from ..config.schema import McpServerConfig
    return McpServerConfig(name="ha", transport="http", url=url)


def _domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else entity_id


def _action_word(service: str) -> str:
    return {"turn_on": "开了", "turn_off": "关了", "toggle": "切换了"}.get(
        service.rsplit(".", 1)[-1], "操作了")


def _result_items(result: Any) -> list[dict]:
    """MCP CallToolResult -> its list of dicts, whatever wrapper it uses.

    HA answers as TextContent whose text is a JSON string; peel that first.
    """
    if result is None:
        return []
    for candidate in _payload_candidates(result):
        if isinstance(candidate, str):
            with contextlib_suppress():
                candidate = json.loads(candidate)
        if isinstance(candidate, list):
            return [x for x in candidate if isinstance(x, dict)]
        if isinstance(candidate, dict):
            for key in ("result", "items", "data"):
                if isinstance(candidate.get(key), list):
                    return [x for x in candidate[key] if isinstance(x, dict)]
    return []


def _payload_candidates(result: Any) -> list[Any]:
    out: list[Any] = []
    content = getattr(result, "content", None)
    if isinstance(content, list):
        out.extend(getattr(c, "text", c) for c in content)
    for attr in ("data", "result", "structuredContent"):
        value = getattr(result, attr, None)
        if value is not None:
            out.append(value)
    if isinstance(result, (list, str)):
        out.append(result)
    return out


def contextlib_suppress():
    import contextlib
    return contextlib.suppress(ValueError)


def _compact(result: Any) -> str:
    """One readable line for the model out of an MCP result."""
    items = _result_items(result)
    if items:
        return json.dumps(items[:10], ensure_ascii=False)
    text = getattr(result, "content", None) or result
    return str(text)[:1500]
