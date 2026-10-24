from __future__ import annotations

import pytest

from rui.tools.contract import Permission, ToolSpec
from rui.tools.registry import ToolError, ToolRegistry


def spec(name: str, perm: Permission = Permission.READ) -> ToolSpec:
    return ToolSpec(name=name, description="x", permission=perm)


def test_namespaced_names_are_required() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolError):
        reg.register(spec("light_turn_on"))


def test_duplicates_are_rejected() -> None:
    reg = ToolRegistry()
    reg.register(spec("ha.light_turn_on"))
    with pytest.raises(ToolError):
        reg.register(spec("ha.light_turn_on"))


def test_read_only_tools_rank_first_and_dangerous_is_hidden() -> None:
    reg = ToolRegistry()
    reg.register(spec("ha.write", Permission.WRITE))
    reg.register(spec("ha.read"))
    reg.register(ToolSpec("ha.nuke", "x", permission=Permission.DANGEROUS, needs_confirm=True))
    exposed = [s.name for s in reg.expose(budget=10)]
    assert exposed == ["ha.read", "ha.write"]


def test_mcp_import_namespaces_and_scrubs() -> None:
    specs = ToolRegistry.from_mcp("Home Assistant", [{"name": "light-turn_on", "description": "d"}])
    assert specs[0].name == "home_assistant.light_turn_on"
    assert specs[0].source == "mcp:Home Assistant"
