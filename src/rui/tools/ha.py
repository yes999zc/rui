"""Home Assistant over its own bridge (tools/ha.py), not generic MCP: the mature
old-build plan, generalized.

What is ported verbatim (the old build ran it for weeks, gap report §3):
the six-tool whitelist, the entity list injected into the prompt as
``名字=entity_id`` **without state** ("stale truth is worse than none"), the
configurable entry cap with dedupe, the 7-day on-disk cache that bridges an offline
HA, and the degrade rule - HA down never touches voice, music or local tools.

What is new on purpose:
- the server-side hard refusal for lock/alarm/valve plus the trigger/code domains
  (script/automation/shell_command/python_script/hassio): the old build only hid
  lock/alarm/valve in the console UI; the refusal now lives where calls execute;
- scenes are sanctioned one entity id at a time (`allowed_scenes`), not by domain:
  the whitelisted ones are injected as their own prompt segment, every other
  `scene.*` call stays refused;
- multi-entity control goes out in serial batches of at most `SERVICE_BATCH` with a
  configurable per-call budget, and a failed batch is read back before the reply -
  partial success is reported as partial, never as ok and never as "nothing worked";
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
# lock/alarm/valve were the old UI's hidden set; script/automation can run
# arbitrary sequences and shell_command/python_script/hassio execute code or manage
# the supervisor, so a misheard name must never reach them through the voice path.
# `scene.` is deliberately NOT here since 0.8.11: a whitelist (`allowed_scenes`)
# decides per entity id instead of refusing the domain outright (0.8.10 production:
# 「关闭所有灯」 was denied, the model degraded to 20+ switch calls, timed out, and
# finally claimed nothing was turned off). Scene is still closed by default.
EXCLUDED_DOMAINS = (
    "lock.", "alarm_", "valve.",
    "script.", "automation.",
    "shell_command.", "python_script.", "hassio.",
)
# The only scene service that activates a whitelisted scene. Everything else in the
# domain (`scene.delete`, `scene.reload`, `scene.create`, ...) is a management call
# and stays denied even for a whitelisted id.
SCENE_SERVICES = ("turn_on",)
# Entities per wire call. 17 entities = 8/8/1, serially: one 20-entity call was what
# timed out in 0.8.10 (a single unresponsive device held the whole batch).
SERVICE_BATCH = 8
# Wall-clock guard for one `_call_batches` run: no new batch after this much time.
# 1.5 x the per-call budget leaves room for the read-back inside the turn's tool
# timeout, while still letting a normal 17-entity run (milliseconds per batch) through.
SERVICE_BUDGET_FACTOR = 1.5
# Below this much remaining budget a batch is not worth starting: a sub-second call
# would time out on its own handshake and only delay the read-back.
SERVICE_MIN_TIMEOUT_S = 1.0
SKIP_WORDS = ("指示灯", "确认", "已反转", "虚拟", "电量", "信号")
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
        # Raw `名字=id` lines, as fetched or read from `.ha_entities.json`.
        self._entities_cache: list[str] | None = None
        # The domain set those raw lines were obtained for. A config change must not
        # wait for the 7-day cache TTL, so a mismatch refetches (or re-reads the cache
        # and re-filters it) on the next injection.
        self._entities_key: tuple[str, ...] | None = None
        # What the last injection actually contained (None = entity_context never ran).
        self._last_injected: int | None = None
        # `entity_id -> friendly name` for the whitelisted scenes, fetched once from HA
        # and kept in memory: the prompter asks every turn, HA does not need to be.
        # None = never fetched successfully; {} = fetched and HA knows no names.
        self._scene_names_cache: dict[str, str] | None = None

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
        """The prompt block: names and ids, never states, cached for offline runs.

        Filtering order is fixed and visible: domain -> `context_exclude` regex ->
        `entity_cap`. Applying the cap first would let an excluded entry eat a slot,
        and filtering domains after the cap would let an unwanted domain push wanted
        entities out.

        Whitelisted scenes are a second, independent segment. They are the only
        sanctioned composite actions, so the model has to see them or it will keep
        assembling 20 switch calls (0.8.10); they are therefore not subject to
        `context` / `context_domains`, which govern the entity list. One
        `[不可信外部数据]` label covers the whole block.
        """
        entities: list[str] = []
        fetched = 0
        blocks: list[str] = []
        if self.cfg.context:
            raw = await self._entities()
            entities = self._visible(raw)
            fetched = len(raw)
            if entities:
                listed = "\n".join(entities)
                note = ("" if self.client._connected.is_set()
                        else phrases.book.render("ha.list_stale"))
                blocks.append(phrases.book.render("ha.entity_list", count=len(entities),
                                                  list=listed, note=note))
                blocks.append(phrases.book.render("ha.multi_entity_rule"))
        self._last_injected = len(entities)
        scene_block = await self._scene_block()
        if scene_block:
            blocks.append(scene_block)
        if not blocks:
            log.info("ha: entity context 0 entities, 0 chars (fetched %d)", fetched)
            return ""
        text = _untrusted("\n".join(blocks))
        # The one line that makes §7 measurable: production was fetched 75 / injected 75
        # (4121 chars, 82 % of the prompt); the point of the four knobs is the gap.
        log.info("ha: entity context %d entities, %d chars (fetched %d)",
                 len(entities), len(text), fetched)
        return text

    async def _scene_block(self) -> str:
        """The whitelisted-scene paragraph, or "" when nothing is whitelisted."""
        names = await self._scene_names()
        if not names:
            return ""
        listed = "、".join(f"{name}={eid}" for eid, name in names.items())
        return phrases.book.render("ha.scene_list", list=listed)

    async def _scene_names(self) -> dict[str, str]:
        """`scene.x -> spoken name` for the whitelist, in configured order.

        Matching is by entity id; the name is decoration. HA supplies the friendly
        name when it can be read, otherwise the id's tail keeps the scene visible
        (an offline HA must not hide an action the operator sanctioned).
        """
        ids = list(self.cfg.allowed_scenes)
        if not ids:
            return {}
        fetched = await self._scene_friendly_names()
        return {eid: fetched.get(eid) or _scene_tail(eid) for eid in ids}

    async def _scene_friendly_names(self) -> dict[str, str]:
        """One `list_entities` for the scene domain, cached for the bridge's life.

        All clean names are cached, not only the currently whitelisted ones: the
        console can add a scene id without a restart, and the next injection should
        use the name HA already told us instead of the id tail.
        """
        if not self.cfg.allowed_scenes:
            return {}
        if self._scene_names_cache is not None:
            return self._scene_names_cache
        if not self.client._connected.is_set():
            return {}       # tails are still injected; nothing to cache from silence
        try:
            result = await self.client.call("list_entities",
                                            {"domain": "scene", "limit": 200})
        except MCPUnavailable as exc:
            log.info("ha: scene names unavailable (%s), using entity id tails", exc)
            return {}
        names: dict[str, str] = {}
        for item in _result_items(result):
            eid = str(item.get("entity_id") or item.get("id") or "").strip()
            # Defensive: a server ignoring the `domain` argument must not leak another
            # domain's names into the scene segment.
            if not _HA_ENTITY_ID.fullmatch(eid) or _domain_of(eid) != "scene":
                continue
            # "=" is the name=id separator and names are untrusted external text.
            name = _clean_untrusted(
                str(item.get("friendly_name") or item.get("name") or "")).strip()
            name = name.replace("=", " ")
            if name:
                names[eid] = name
        self._scene_names_cache = names
        return names

    def _exclude_patterns(self) -> list[re.Pattern[str]]:
        patterns: list[re.Pattern[str]] = []
        for raw in self.cfg.context_exclude:
            text = str(raw or "")
            if not text.strip():
                continue
            try:
                patterns.append(re.compile(text, re.IGNORECASE))
            except re.error:
                # The schema refuses these at load time; a bridge built from an older
                # in-memory config must still answer a turn, so skip the bad pattern.
                log.warning("ha: ignoring invalid context_exclude pattern %r", text)
        return patterns

    def _visible(self, raw: list[str]) -> list[str]:
        """domain filter -> exclude regexes (name OR entity id) -> cap."""
        domains = set(self.entity_domains())
        patterns = self._exclude_patterns()
        out: list[str] = []
        for line in raw:
            name, _, eid = line.partition("=")
            if _domain_of(eid) not in domains:
                continue
            if any(p.search(name) or p.search(eid) for p in patterns):
                continue
            out.append(line)
            if len(out) >= self.cfg.entity_cap:
                break
        return out

    async def _entities(self) -> list[str]:
        """Raw `名字=id` lines for the configured domains, cached for offline runs.

        The cache stores the raw list, not the injected block: every injection re-runs
        the domain/exclude/cap filter, so changing any of them takes effect on the next
        turn instead of after the 7-day TTL. `.ha_entities.json` written by an older
        build (no `domains` key) is still read - its lines are re-filtered too.
        """
        key = tuple(self.entity_domains())
        offline = not self.client._connected.is_set()
        # Offline the raw list is the best data there is, and it is re-filtered below,
        # so a domains change needs no disk round-trip. Online, a different domain set
        # means the cache was never asked for it: refetch instead of waiting out the TTL.
        if self._entities_cache is not None and (self._entities_key == key or offline):
            return self._entities_cache
        cache_path = self._data_dir / ".ha_entities.json"
        fresh: list[str] | None = None
        if self.client._connected.is_set():
            fresh = await self._fetch_entities()
        if fresh is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps({"t": int(time.time()), "domains": list(key),
                                "entities": fresh}, ensure_ascii=False),
                    encoding="utf-8")
                os.chmod(cache_path, 0o600)
            except OSError:
                pass
            self._entities_cache = fresh
            self._entities_key = key
            return fresh
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("t") or 0) < CACHE_TTL_S:
                self._entities_cache = [
                    clean for line in data.get("entities") or []
                    if (clean := _clean_entity(line))
                ]
                # Record what the FILE says it covers, never what we asked for now.
                # A cache written by 0.8.10 has no `domains` key at all: `None` can
                # never equal a requested key, so the first injection after HA comes
                # back refetches instead of trusting a list that may predate the
                # configured domains.
                stored = data.get("domains")
                self._entities_key = (tuple(str(domain) for domain in stored)
                                      if isinstance(stored, list) else None)
                return self._entities_cache
        except (OSError, ValueError):
            pass
        return []

    async def _fetch_entities(self) -> list[str]:
        """`list_entities` per configured domain, states STRIPPED: the prompt carries
        names and ids only - a stale state is worse than none (old build ha.py:227).

        The fetch budget is wider than `entity_cap` on purpose: entries the exclude
        regexes drop must not leave the cap half empty.
        """
        domains = self.entity_domains()
        if not domains:
            return []
        budget = min(max(self.cfg.entity_cap * 3, 200), 1000)
        allowed = set(domains)
        out: list[str] = []
        seen: set[str] = set()
        for domain in domains:
            try:
                result = await self.client.call("list_entities",
                                                {"domain": domain, "limit": budget})
            except MCPUnavailable:
                return []   # half a list is worse than the cache
            for item in _result_items(result):
                eid = str(item.get("entity_id") or item.get("id") or "").strip()
                raw_name = item.get("friendly_name") or item.get("name") or ""
                # "=" is the list separator; a name carrying one would corrupt the
                # cache round-trip.
                name = _clean_untrusted(str(raw_name)).strip().replace("=", " ")
                # An entity id is a slug; anything else is not addressable and only
                # gives an attacker another way to write text into the prompt. The
                # domain check is defensive: a server that ignores the `domain`
                # argument must not leak scene/script into the list.
                if (not name or not _HA_ENTITY_ID.fullmatch(eid) or eid in seen
                        or _domain_of(eid) not in allowed
                        or any(word in name for word in SKIP_WORDS)):
                    continue
                seen.add(eid)
                out.append(f"{name}={eid}")
                if len(out) >= budget:
                    return out
        return out

    def entity_domains(self) -> list[str]:
        """The prompt's domains, from config (0.8.11). Not hard-coded any more.

        Empty `context_domains` means "list nothing" (the same practical result as
        `context: false` for the list); an explicitly configured domain, even one whose
        calls are refused, is honoured as written - the defaults are the safe set.
        """
        out: list[str] = []
        for raw in self.cfg.context_domains:
            domain = str(raw or "").strip().lower().rstrip(".")
            if domain and domain not in out:
                out.append(domain)
        return out

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
                            "不要只挑一个，也不要反问用户。"
                            "关灯/回家这类组合动作优先用 service=scene.turn_on + 场景清单里的 "
                            "scene id；逐个实体是退路。",
                input_schema=raw.get("inputSchema") or {},
                interaction=InteractionSpec(
                    exec={"ok": "device.done", "empty": "device.missing_target",
                          "unreachable": "device.unreachable",
                          "partial": "device.partial",
                          "denied": "device.excluded"},
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
        if tool == "batch_get_state":
            # HA's batch read wants `entity_ids` (plural, a list) while the write tool
            # takes `entity_id` (singular, comma string), and a model that just used
            # call_service reuses the write spelling here. HA then rejected the call
            # and the user was told the house was offline (issue #34), so the shapes
            # are unified before the wire; other read tools are passed untouched.
            ids, ok = _normalize_entity_ids(args)
            if not ok:
                return _bad_args()
            args = {key: value for key, value in args.items() if key != "entity_id"}
            args["entity_ids"] = ids
        try:
            result = await self.client.call(tool, args)
        except MCPUnavailable as exc:
            # A validation error is a caller mistake, not an offline house. The
            # transport wraps both in one exception, so the message is the only
            # signal there is; when it is not conclusive, unreachable stays.
            if _looks_like_bad_args(exc):
                log.info("ha %s rejected its arguments: %s", tool, exc)
                return _bad_args()
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
        ids = [part.strip() for part in target.split(",") if part.strip()]
        if not ids:
            return ToolResult(status="empty")
        # The refusal the old build never had server-side (gap report §3.4-7).
        # Domains are compared exactly (or by prefix for `alarm_*`): a substring check
        # on the whole entity list would both miss split forms and match names by
        # accident.
        domains = {str(args.get("domain") or "").strip()}
        domains.update(_domain_of(entity_id) for entity_id in ids)
        denied = ToolResult(status="denied").slot(
            device=str(args.get("domain") or _domain_of(target)), action="")
        if any(_excluded_domain(domain) for domain in domains):
            return denied
        # `scene` is not a domain exemption but a per-entity whitelist: the call must
        # be a scene activation, every target id must be listed, and no foreign domain
        # may ride along (a `domain: light` payload naming a scene id). A single
        # unlisted id refuses the whole call, before any wire traffic - HA applies a
        # service call for all its entities or none.
        if "scene" in domains:
            requested = str(args.get("domain") or "").strip()
            service_tail = str(args.get("service") or "").rsplit(".", 1)[-1]
            allowed = set(self.cfg.allowed_scenes)
            if (requested != "scene"
                    or any(_domain_of(entity_id) != "scene" for entity_id in ids)
                    or any(entity_id not in allowed for entity_id in ids)
                    or service_tail not in SCENE_SERVICES):
                return denied
        # One entity keeps the 0.8.10 path; several go out in serial batches so one
        # unresponsive device cannot hold the whole house hostage.
        if len(ids) == 1:
            return await self._call_one(args, ids)
        return await self._call_batches(args, ids)

    async def _call_one(self, args: dict[str, Any], ids: list[str]) -> ToolResult:
        target = ", ".join(ids)
        try:
            await self.client.call("call_service", {**args, "entity_id": target},
                                   timeout_s=self.cfg.call_timeout_s)
        except MCPUnavailable as exc:
            log.info("ha call_service %s failed: %s", args.get("service"), exc)
            return ToolResult(status="unreachable").slot(device=self._spoken_name(target))
        return ToolResult(status="ok").slot(
            device=self._spoken_name(target),
            action=_action_word(str(args.get("service") or "")))

    async def _call_batches(self, args: dict[str, Any], ids: list[str]) -> ToolResult:
        """Serial batches of at most `SERVICE_BATCH`, then a read-back before speaking.

        0.8.10 sent 20+ ids in one call with a fixed 8 s budget: one unresponsive
        device timed out the batch, HA still applied part of it, and the reply claimed
        「全都没关」. The state of the house now decides the wording, not the timeout.

        Two guards keep a broken HA from eating the turn:
        * the first failed/timed-out batch stops the run - a transport death means the
          next batches would fail too, and continuing only spends the turn's budget;
        * no new batch is started once the run has spent `call_timeout_s * 1.5`.

        Whatever was sent is read back, successful batches included: a transport-level
        ok is not a device-level ok. Then:
        * every sent entity confirmed, nothing unsent -> ok (unchanged wording);
        * some confirmed, some not/unsent            -> `partial` with counts + names;
        * nothing confirmed applied                  -> unreachable (existing wording);
        * the read-back itself failed                -> partial when a batch answered,
          otherwise unreachable. Never ok, never "none of them" (a claim we cannot
          support).
        """
        service = str(args.get("service") or "")
        done: list[str] = []            # batches that the transport accepted
        sent: list[str] = []            # every id that reached the wire, in order
        stopped = ""                    # "failed" | "budget" | "" (all batches sent)
        budget_s = self.cfg.call_timeout_s * SERVICE_BUDGET_FACTOR
        started = time.monotonic()
        for start in range(0, len(ids), SERVICE_BATCH):
            chunk = ids[start:start + SERVICE_BATCH]
            remaining = budget_s - (time.monotonic() - started)
            if remaining < SERVICE_MIN_TIMEOUT_S:
                log.info("ha call_service: %.1fs of %.1fs budget left, %d of %d entities "
                         "left unsent", remaining, budget_s,
                         len(ids) - len(sent), len(ids))
                stopped = "budget"
                break
            # The configured value stays the per-batch cap; a late batch only gets what
            # is left of the run budget, so the whole run stays inside the turn's tool
            # timeout instead of letting the last batch eat a full call_timeout_s.
            batch_timeout = min(self.cfg.call_timeout_s, round(remaining, 1))
            sent.extend(chunk)
            try:
                await self.client.call("call_service",
                                       {**args, "entity_id": ", ".join(chunk)},
                                       timeout_s=batch_timeout)
            except MCPUnavailable as exc:
                log.info("ha call_service batch %d-%d failed, %d entities left unsent: %s",
                         start + 1, start + len(chunk), len(ids) - len(sent), exc)
                stopped = "failed"
                break
            done.extend(chunk)
        if not stopped:
            return ToolResult(status="ok").slot(
                device=self._spoken_name(", ".join(ids)), action=_action_word(service))
        # Read back everything that was sent, not only the failed batch: a batch that
        # answered can still have been applied to only part of its entities.
        verified = await self._verify_states(sent, service)
        unsent = ids[len(sent):]
        if verified is None:
            # No usable read-back. What the transport accepted is still real; nothing
            # else is claimed.
            unknown = [entity_id for entity_id in ids if entity_id not in set(done)]
            log.info("ha call_service: %d of %d confirmed, state check unavailable",
                     len(done), len(ids))
            if done:
                return self._partial(len(done), len(ids), unknown)
            return ToolResult(status="unreachable").slot(
                device=self._spoken_name(", ".join(ids)))
        applied = [entity_id for entity_id in sent if verified.get(entity_id)]
        not_applied = [entity_id for entity_id in sent if not verified.get(entity_id)]
        if not applied:
            return ToolResult(status="unreachable").slot(
                device=self._spoken_name(", ".join(ids)))
        if not_applied or unsent:
            return self._partial(len(applied), len(ids), not_applied + unsent)
        # A timed-out batch that the house says did apply is a success: report the state,
        # not the transport.
        return ToolResult(status="ok").slot(
            device=self._spoken_name(", ".join(ids)), action=_action_word(service))

    def _partial(self, ok_count: int, total: int, failed: list[str]) -> ToolResult:
        return ToolResult(status="partial").slot(
            ok_count=ok_count, total=total,
            failed=self._spoken_name(", ".join(failed)))

    async def _verify_states(self, ids: list[str], service: str) -> dict[str, bool] | None:
        """Read the failed entities back: True = reached the state the service asked for.

        `None` means "cannot tell" (unverifiable service, or the read itself failed)
        and the caller stays conservative. Uses the read-only `batch_get_state` tool
        the bridge already exposes - no second connection, no new transport.
        """
        desired = _desired_state(service)
        if desired is None or not ids:
            return None
        wanted = set(ids)
        try:
            result = await self.client.call("batch_get_state", {"entity_ids": ids})
        except MCPUnavailable as exc:
            log.info("ha: state check after a failed batch failed: %s", exc)
            return None
        states: dict[str, str] = {}
        for item in _result_items(result):
            eid = str(item.get("entity_id") or item.get("id") or "").strip()
            state = str(item.get("state") or "").strip().lower()
            if eid in wanted and state:
                states[eid] = state
        if not states:
            return None
        # An id the answer left out is not confirmed as applied: conservative False.
        return {entity_id: states.get(entity_id) == desired for entity_id in ids}

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
        # `entities` is what the prompt actually carries (after domain/exclude/cap),
        # not the raw cache: the console number should match the injected block.
        injected = (self._last_injected if self._last_injected is not None
                    else len(self._entities_cache or []))
        return {
            "connected": self.client._connected.is_set(),
            "latency_ms": self.client.latency_ms,
            "tools": len(self.client.tools),
            "entities": injected,
        }


def _server_cfg(url: str):
    from ..config.schema import McpServerConfig
    return McpServerConfig(name="ha", transport="http", url=url)


def _domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else entity_id


def _scene_tail(entity_id: str) -> str:
    """Name fallback for a whitelisted scene HA could not name: `scene.a_b` -> `a_b`."""
    return entity_id.split(".", 1)[-1]


def _desired_state(service: str) -> str | None:
    """The state a `turn_*` service should leave an entity in; None = unverifiable.

    Only these two are mapped: covers/climate/media use other services or other
    state vocabularies, and guessing there would turn "cannot tell" into a wrong
    failure. None keeps the caller conservative.
    """
    return {"turn_on": "on", "turn_off": "off"}.get(service.rsplit(".", 1)[-1])


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


# HA's two spellings and the argument errors that used to be reported as an
# offline house (issue #34). Conservative on purpose: the transport wraps tool
# validation failures and connection deaths in one MCPUnavailable, so only a
# message naming the argument contract counts as the caller's mistake.
_BAD_ARGS_HINTS = (
    "validation", "invalid", "required", "missing", "unexpected",
    "entity_id", "schema", "not a list", "bad request",
)


def _normalize_entity_ids(args: dict[str, Any]) -> tuple[list[str], bool]:
    """`entity_ids`/`entity_id` in any known shape -> (ids, usable).

    Accepted: a list, a comma-separated string, or a JSON array string (the forms
    measured in issue #34). `usable=False` means the caller passed nothing usable:
    that is the caller's error (`bad_args`), never "the device is unreachable".
    """
    value = args.get("entity_ids")
    if value is None:
        value = args.get("entity_id")
    items: list[Any] | tuple[Any, ...]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                return [], False
            if not isinstance(parsed, list):
                return [], False
            items = parsed
        else:
            items = text.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return [], False
    ids: list[str] = []
    for item in items:
        if not isinstance(item, (str, int, float)):
            return [], False
        # A list entry may itself carry "a, b" (a model flattening the write form).
        ids.extend(part.strip() for part in str(item).split(",") if part.strip())
    return ids, bool(ids)


def _bad_args() -> ToolResult:
    return ToolResult(status="bad_args", payload={
        "say": phrases.book.render("device.bad_args")})


def _looks_like_bad_args(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _BAD_ARGS_HINTS)


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
