"""The HA bridge against a fake MCP client: shapes and guards, no network."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rui.config.schema import HaConfig
from rui.session import phrases
from rui.tools.ha import HaBridge
from rui.tools.mcp_client import MCPUnavailable


class FakeClient:
    def __init__(self, tools: list[str], result: object = None, fail: bool = False,
                 error: str = "down"):
        self.tools = [{"name": n, "inputSchema": {}} for n in tools]
        self._result = result
        self._fail = fail
        self._error = error
        self.calls: list[tuple[str, dict]] = []
        self._connected = asyncio.Event()
        self._connected.set()

    async def call(self, tool: str, args: dict) -> object:
        self.calls.append((tool, args))
        if self._fail:
            raise MCPUnavailable(self._error)
        return self._result


class TextResult:
    def __init__(self, payload: object):
        class C:
            text = json.dumps(payload, ensure_ascii=False)
        self.content = [C()]


def make(tmp_path: Path, **kw) -> HaBridge:
    bridge = HaBridge(HaConfig(base_url="http://ha.local",
                               token_env="NO_TOKEN"), tmp_path)
    bridge.client = FakeClient(list(kw.get("tools", [])),
                               result=kw.get("result"), fail=kw.get("fail", False),
                               error=kw.get("error", "down"))
    return bridge


def test_call_service_accepts_combined_and_split_forms(tmp_path: Path) -> None:
    async def run() -> None:
        bridge = make(tmp_path, tools=["call_service", "get_state"])
        specs = dict((s.name, h) for s, h in bridge.tool_specs())
        out = await specs["local.ha_call_service"](
            {"service": "light.turn_on", "entity_id": "light.kids"})
        assert out.status == "ok"
        assert out.payload["action"] == "开了"
        sent = bridge.client.calls[-1]
        assert sent[1]["domain"] == "light" and sent[1]["service"] == "turn_on"
        # no cached names in this fixture: the domain is the fallback, never the id
        assert phrases.book.render("device.done", **out.payload) == \
            "好，light开了。"

    asyncio.run(run())


def test_excluded_domains_are_refused_where_calls_execute(tmp_path: Path) -> None:
    """The old build only hid lock/alarm in its console UI; the refusal lives
    here now, before any wire call."""
    async def run() -> None:
        bridge = make(tmp_path, tools=["call_service"])
        specs = {s.name: h for s, h in bridge.tool_specs()}
        out = await specs["local.ha_call_service"](
            {"service": "lock.open", "entity_id": "lock.front"})
        assert out.status == "denied" and not bridge.client.calls
        assert "不做" in phrases.book.render("device.excluded", **out.payload)

    asyncio.run(run())


def test_a_dead_ha_degrades_to_the_phrase(tmp_path: Path) -> None:
    async def run() -> None:
        bridge = make(tmp_path, tools=["call_service"], fail=True)
        specs = {s.name: h for s, h in bridge.tool_specs()}
        out = await specs["local.ha_call_service"](
            {"service": "light.turn_on", "entity_id": "light.kids"})
        assert out.status == "unreachable"
        assert "别当我成功了" in phrases.book.render("device.unreachable",
                                                     **out.payload)

    asyncio.run(run())


def test_entity_list_strips_states_and_filters_noise(tmp_path: Path) -> None:
    async def run() -> None:
        payload = [
            {"entity_id": "light.kids", "state": "on", "friendly_name": "儿童房主灯"},
            {"entity_id": "light.cam", "state": "off",
             "friendly_name": "摄像头指示灯"},   # SKIP_WORDS: 指示灯
        ]
        bridge = make(tmp_path, tools=["list_entities"], result=TextResult(payload))
        entities = await bridge._fetch_entities()
        assert entities == ["儿童房主灯=light.kids"]
        ctx = await bridge.entity_context()
        assert "儿童房主灯=light.kids" in ctx and '"state"' not in ctx

    asyncio.run(run())


def test_a_bare_service_and_a_json_list_of_ids_still_reach_ha(tmp_path: Path) -> None:
    """Measured 11:15:50: 「关闭儿童房的灯」 sent service="turn_off" (no domain) and
    entity_id as a JSON string; HA refused for a missing domain."""
    import json as _json

    async def run() -> None:
        bridge = make(tmp_path, tools=["call_service"])
        specs = {s.name: h for s, h in bridge.tool_specs()}
        out = await specs["local.ha_call_service"]({
            "service": "turn_off",
            "entity_id": _json.dumps(["switch.a", "switch.b"]),
        })
        assert out.status == "ok", out
        sent = bridge.client.calls[-1][1]
        assert sent["domain"] == "switch" and sent["service"] == "turn_off"
        assert sent["entity_id"] == "switch.a, switch.b"

    asyncio.run(run())


def test_batch_get_state_normalizes_every_entity_argument_shape(tmp_path: Path) -> None:
    """Issue #34: HA's batch read wants `entity_ids` (plural, a list) while the
    write tool takes `entity_id` (singular, comma string). All measured shapes must
    reach the wire as one list under `entity_ids`."""
    async def run() -> None:
        bridge = make(tmp_path, tools=["batch_get_state"], result=TextResult([]))
        handler = {s.name: h for s, h in bridge.tool_specs()}["local.ha_batch_get_state"]
        shapes = [
            {"entity_ids": ["light.a", "light.b"]},
            {"entity_id": ["light.a", "light.b"]},
            {"entity_id": "light.a, light.b"},
            {"entity_ids": "light.a, light.b"},
            {"entity_ids": json.dumps(["light.a", "light.b"])},
        ]
        for args in shapes:
            out = await handler(dict(args))
            assert out.status == "ok", (args, out)
            tool, sent = bridge.client.calls[-1]
            assert tool == "batch_get_state"
            assert sent["entity_ids"] == ["light.a", "light.b"], args
            assert "entity_id" not in sent
        # trims whitespace and drops empty entries rather than sending "" to HA
        await handler({"entity_ids": " light.a , ,light.b  "})
        assert bridge.client.calls[-1][1]["entity_ids"] == ["light.a", "light.b"]

    asyncio.run(run())


def test_batch_get_state_bad_arguments_never_say_unreachable(tmp_path: Path) -> None:
    """A malformed call is `bad_args` and says "这次没查到", not "设备连不上"."""
    async def run() -> None:
        # Local shapes that cannot be salvaged: no wire call at all.
        bridge = make(tmp_path, tools=["batch_get_state"])
        handler = {s.name: h for s, h in bridge.tool_specs()}["local.ha_batch_get_state"]
        for args in ({"entity_ids": " , "}, {"entity_ids": "[]"},
                     {"entity_id": {"light.a": True}}, {}):
            out = await handler(dict(args))
            assert out.status == "bad_args", args
            assert "没查到" in out.payload["say"]
            assert "连不上" not in out.payload["say"]
        assert not bridge.client.calls
        assert out.payload["say"] == phrases.book.render("device.bad_args")

        # HA's own validation error arrives as MCPUnavailable: still the caller's
        # mistake, not an offline house.
        failing = make(tmp_path, tools=["batch_get_state"], fail=True,
                       error="ha.batch_get_state: Invalid arguments: entity_ids is required")
        failing_handler = {s.name: h for s, h in failing.tool_specs()}[
            "local.ha_batch_get_state"]
        out = await failing_handler({"entity_id": "light.a"})
        assert out.status == "bad_args", out
        assert "没查到" in out.payload["say"]
        assert "连不上" not in out.payload["say"]

    asyncio.run(run())


def test_a_dead_ha_on_a_read_is_still_unreachable(tmp_path: Path) -> None:
    async def run() -> None:
        bridge = make(tmp_path, tools=["get_state"], fail=True,
                      error="ha.get_state: connection down")
        handler = {s.name: h for s, h in bridge.tool_specs()}["local.ha_get_state"]
        out = await handler({"entity_id": "light.a"})
        assert out.status == "unreachable"
        assert "连不上" in out.payload["say"]

    asyncio.run(run())


def test_other_read_tools_keep_their_arguments_untouched(tmp_path: Path) -> None:
    async def run() -> None:
        bridge = make(tmp_path, tools=["get_state"],
                      result=TextResult([{"entity_id": "light.a", "state": "on"}]))
        handler = {s.name: h for s, h in bridge.tool_specs()}["local.ha_get_state"]
        out = await handler({"entity_id": "light.a"})
        assert out.status == "ok"
        assert bridge.client.calls[-1] == ("get_state", {"entity_id": "light.a"})

    asyncio.run(run())
