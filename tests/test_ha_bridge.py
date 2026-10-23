"""The HA bridge against a fake MCP client: shapes and guards, no network."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from house_ear.config.schema import HaConfig
from house_ear.session import phrases
from house_ear.tools.ha import HaBridge
from house_ear.tools.mcp_client import MCPUnavailable


class FakeClient:
    def __init__(self, tools: list[str], result: object = None, fail: bool = False):
        self.tools = [{"name": n, "inputSchema": {}} for n in tools]
        self._result = result
        self._fail = fail
        self.calls: list[tuple[str, dict]] = []
        self._connected = asyncio.Event()
        self._connected.set()

    async def call(self, tool: str, args: dict) -> object:
        self.calls.append((tool, args))
        if self._fail:
            raise MCPUnavailable("down")
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
                               result=kw.get("result"), fail=kw.get("fail", False))
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
        assert phrases.book.render("device.done", **out.payload) == \
            "好，light.kids开了。"

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
