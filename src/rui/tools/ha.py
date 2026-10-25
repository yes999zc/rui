"""Home Assistant over MCP: the mature old-build plan, generalized.

What is ported verbatim (the old build ran it for weeks, gap report §3):
the six-tool whitelist, the entity list injected into the prompt as
``名字=entity_id`` **without state** ("stale truth is worse than none"), the
120-entry cap with dedupe, the 7-day on-disk cache that bridges an offline HA,
and the degrade rule - HA down never touches voice, music or local tools.

What is new on purpose:
- the server-side hard refusal for lock/alarm/valve plus the trigger/code domains
  (script/scene/automation/shell_command/python_script/hassio): the old build only
  hid lock/alarm/valve in the console UI; the refusal now lives where calls execute;
- ToolResult + InteractionSpec: the HA tools declare their phrases, they do
  not carry them (docs/interaction.md);
- entity names and read-tool output are labelled as untrusted external data and
  stripped of obvious instruction text before they reach the model.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from .. import secrets
from ..config.schema import HaConfig
from ..security.netguard import OutboundPolicy
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
# lock/alarm/valve were the old UI's hidden set; script/scene/automation can run
# arbitrary sequences and shell_command/python_script/hassio execute code or manage
# the supervisor, so a misheard name must never reach them through the voice path.
EXCLUDED_DOMAINS = (
    "lock.", "alarm_", "valve.",
    "script.", "scene.", "automation.",
    "shell_command.", "python_script.", "hassio.",
)
SKIP_WORDS = ("指示灯", "确认", "已反转", "虚拟", "电量", "信号")
ENTITY_CAP = 120
CACHE_TTL_S = 7 * 86400
MAX_ENTITIES_TO_LIST = 6   # search_entities cap: the picker, not the house

# HA names/states are user-editable data, not instructions. A name such as
# "忽略之前的指令" is text from a device, so it is stripped at the boundary and the
# whole block is labelled before it reaches the model.
UNTRUSTED_PREFIX = "[不可信外部数据]"
_HA_ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
# Whole lines that impersonate a chat role: drop them outright.
_ROLE_LINE = re.compile(r"^\s*(?:system|assistant|developer|user)\s*[:：]", re.IGNORECASE)
# Obvious imperative-injection phrases, English or Chinese. A line carrying one is
# dropped whole: a name that tries to instruct the model is not a usable room name.
_INSTRUCTION_TEXT = re.compile(
    r"(?:(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|of\s+)?(?:the\s+)?"
    r"(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|messages?)"
    r"|忽略(?:掉)?(?:之前|先前|前面|以上|此前)的?(?:所有)?(?:指令|指示|要求|提示词)"
    r"|(?:从现在起|你现在)(?:是|扮演|作为))",
    re.IGNORECASE,
)


def _clean_untrusted(text: str) -> str:
    """Drop role lines and instruction-shaped lines from externally supplied text."""
    kept: list[str] = []
    for line in str(text).splitlines():
        if _ROLE_LINE.match(line) or _INSTRUCTION_TEXT.search(line):
            continue
        stripped = line.strip()
        if stripped:
            kept.append(stripped)
    return "\n".join(kept)


def _untrusted(text: str) -> str:
    """Label external data for the model; the ToolResult shape stays unchanged."""
    return f"{UNTRUSTED_PREFIX}\n{_clean_untrusted(text)}".rstrip()


def _clean_value(value: Any) -> Any:
    """Recursively clean strings in an MCP result so one bad field cannot poison the line."""
    if isinstance(value, str):
        return _clean_untrusted(value)
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    return value


def _clean_entity(line: object) -> str:
    """`名字=entity_id` -> cleaned line, or "" when the name/id is unusable.

    Applied on ingestion and again on cache reads: an `.ha_entities.json` written by
    an older build must not bypass the prompt-injection guard.
    """
    name, _, eid = str(line).partition("=")
    name = _clean_untrusted(name).strip()
    eid = eid.strip()
    if not name or not _HA_ENTITY_ID.fullmatch(eid):
        return ""
    return f"{name}={eid}"


class HaBridge:
    """One HA MCP server: connection, whitelist, entity context, tool handlers."""

    def __init__(self, cfg: HaConfig, data_dir: Path,
                 policy: OutboundPolicy | None = None,
                 token: str | None = None) -> None:
        """`token` is a one-shot credential for a console probe.

        The console must be able to test a token the operator just typed without
        saving it first (and to test a *replacement* token while the old one is still
        stored). When the caller passes one it wins; otherwise the configured secret
        (`cfg.token_env`) is resolved as usual.
        """
        url = cfg.base_url.rstrip("/")
        if not url.endswith("/api/mcp"):
            url = f"{url}/api/mcp"
        token = token if token is not None else secrets.get(cfg.token_env)
        self.cfg = cfg
        self.client = MCPClient(
            _server_cfg(url), headers={"Authorization": f"Bearer {token}"} if token else {},
            policy=policy,
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
        rendered = phrases.book.render("ha.entity_list", count=len(entities),
                                       list=listed, note=note) + "\n" + phrases.book.render(
            "ha.multi_entity_rule")
        return _untrusted(rendered)

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
                os.chmod(cache_path, 0o600)
            except OSError:
                pass
            self._entities_cache = fresh
            return fresh
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("t") or 0) < CACHE_TTL_S:
                self._entities_cache = [
                    clean for line in data.get("entities") or []
                    if (clean := _clean_entity(line))
                ]
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
                eid = str(item.get("entity_id") or item.get("id") or "").strip()
                raw_name = item.get("friendly_name") or item.get("name") or ""
                # "=" is the list separator; a name carrying one would corrupt the
                # cache round-trip.
                name = _clean_untrusted(str(raw_name)).strip().replace("=", " ")
                # An entity id is a slug; anything else is not addressable and only
                # gives an attacker another way to write text into the prompt.
                if (not name or not _HA_ENTITY_ID.fullmatch(eid) or eid in seen
                        or any(word in name for word in SKIP_WORDS)):
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
                            "entity_id 用实体清单里的 id。清单里没有的设备先调 search_entities。"
                            "同一名字有多个 entity_id（比如儿童房的主灯和筒灯）时全部传入，"
                            "不要只挑一个，也不要反问用户。",
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
        # States/attributes are editable on the HA side: label them as data and strip
        # instruction-shaped text before the model ever sees them.
        return ToolResult.say(_untrusted(_compact(result)))

    async def _call_service(self, args: dict[str, Any]) -> ToolResult:
        # HA's MCP wants `domain` and `service` separately; a model (or a test)
        # may hand over the combined "light.turn_on" spelling, a bare "turn_off",
        # or the entity list as a JSON string. Measured 11:15:50: 「关闭儿童房的灯」
        # sent {"service": "turn_off", "entity_id": "[\"switch.x\", ...]"} and HA
        # refused for a missing domain.
        service = str(args.get("service") or "")
        args = dict(args)
        target = args.get("entity_id") or ""
        if isinstance(target, str) and target.strip().startswith("["):
            try:
                parsed = json.loads(target)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                target = parsed
        if isinstance(target, list):
            target = ", ".join(str(t) for t in target)
            args["entity_id"] = target
        if not isinstance(target, str):
            target = str(target)
        if "." in service:
            head, tail = service.split(".", 1)
            # "turn_off" alone: the domain lives in the entity id.
            args.setdefault("domain", head)
            args["service"] = tail
        elif not args.get("domain") and "." in str(target):
            args["domain"] = str(target).split(".", 1)[0]
            args["service"] = service
        if not target:
            return ToolResult(status="empty")
        # The refusal the old build never had server-side (gap report §3.4-7).
        # Domains are compared exactly (or by prefix for `alarm_*`): a substring check
        # on the whole entity list would both miss split forms and match names by
        # accident.
        domains = {str(args.get("domain") or "").strip()}
        domains.update(_domain_of(t.strip()) for t in target.split(",") if t.strip())
        if any(_excluded_domain(domain) for domain in domains):
            return ToolResult(status="denied").slot(
                device=str(args.get("domain") or _domain_of(target)), action="")
        try:
            await self.client.call("call_service", args)
        except MCPUnavailable as exc:
            log.info("ha call_service %s failed: %s", args.get("service"), exc)
            return ToolResult(status="unreachable").slot(device=self._spoken_name(target))
        return ToolResult(status="ok").slot(
            device=self._spoken_name(target),
            action=_action_word(str(args.get("service") or "")))

    def _spoken_name(self, target: str) -> str:
        """Entity ids -> the names a person uses, from the cached list.

        Measured 11:17:50: the reply read 「好，switch.unknown_switch_11, … 了」 -
        an id list spoken aloud. The list is already in memory (名字=id); use it,
        and keep the answer to two names plus 等 when several changed.
        """
        names = {}
        for line in self._entities_cache or []:
            name, _, eid = line.partition("=")
            if eid:
                names[eid] = name
        ids = [t.strip() for t in str(target).split(",") if t.strip()]
        spoken = [names.get(i, "") for i in ids]
        if not any(spoken):
            # No name known: say what kind of thing it is, never the id.
            return _domain_of(ids[0]) if ids else "设备"
        uniq = [n for n in dict.fromkeys(spoken) if n]
        return "、".join(uniq[:2]) + ("等" if len(uniq) > 2 else "")

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


def _excluded_domain(domain: str) -> bool:
    for prefix in EXCLUDED_DOMAINS:
        if prefix.endswith("."):
            if domain == prefix[:-1]:
                return True
        elif domain.startswith(prefix):
            return True
    return False


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
        # Clean per item before serialising: dropping the whole line because one
        # entity name is instruction-shaped would hide every other entity too.
        return json.dumps(_clean_value(items[:10]), ensure_ascii=False)
    text = getattr(result, "content", None) or result
    return str(text)[:1500]
