"""First-party integrations, described once for the console.

Home Assistant is not a generic `mcp_servers` row and not an MCP instance: it runs
over its own bridge (tools/ha.py) and keeps a six-tool whitelist, a hard refusal for
dangerous domains and the `local.ha_*` tool names. The console renders it as its own
first-party section (not inside the MCP server list), so the shape it renders comes
from here instead of being re-invented in Vue. Adding another first-party integration means adding a
descriptor here; a third-party server needs no code at all (`PUT /api/v1/mcp`).

This module is data only. The HA bridge remains `tools/ha.py`; nothing here weakens
its policy or renames its tools.
"""
from __future__ import annotations

from typing import Any

from .ha import DEFAULT_WHITELIST, EXCLUDED_DOMAINS

MCP_MANIFEST_VERSION = 1


def first_party_instances(*, base_url: str, token_env: str, has_token: bool,
                          status: dict[str, Any], wired: bool) -> list[dict]:
    """The first-party instances the console renders (today: Home Assistant, which runs
    over its own bridge; generic MCP servers are not in this list).

    `status` is `HaBridge.status` (`connected/latency_ms/tools/entities`); only
    presence is reported for the token - the value never enters this structure.
    """
    return [
        {
            "id": "ha",
            "name": "Home Assistant",
            "kind": "first_party",
            "transport": "http",
            "config_path": "tools.ha",
            "endpoint": base_url,
            "token_env": token_env,
            # Presence, not the token. The badge is the only thing the browser learns.
            "has_token": bool(has_token),
            "wired": bool(wired),
            "connected": bool(status.get("connected")),
            "latency_ms": status.get("latency_ms"),
            "tools": int(status.get("tools") or 0),
            "entities": int(status.get("entities") or 0),
            "tool_prefix": "local.ha_",
            # The model-facing names the bridge actually registers; shown so the MCP
            # card cannot silently drift to `mcp.ha_*`.
            "tool_names": [f"local.ha_{name}" for name in DEFAULT_WHITELIST],
            # Shown as policy, never as a mutable field: the bridge enforces both where
            # calls execute, and a UI change must not quietly widen them.
            "whitelist": list(DEFAULT_WHITELIST),
            "excluded_domains": list(EXCLUDED_DOMAINS),
            "danger_policy": "dangerous domains are refused before any call",
            "href": "/panel/integrations?tab=mcp",
            "docs": "docs/integrations-mcp.md",
        }
    ]


__all__ = ["MCP_MANIFEST_VERSION", "first_party_instances"]
