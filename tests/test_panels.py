"""The console registry contract (docs/console.md).

The backend declares the panels, the front end renders them. A page review keeps
breaking the same three things, so they are pinned here:

1. every console route opens by URL (a reload while sitting on /panel/music used
   to answer a JSON 404 - the SPA had no history fallback);
2. every declared source and action points at a real endpoint, so a renamed API
   cannot leave a dead button behind;
3. the settings panel owns every field the deleted hand-written pages used to
   edit (assistant.* / session.*), otherwise deleting those pages silently
   removes a setting from the console.

The API paths must stay JSON: a mistyped /api/v1/* must not fall into the SPA
shell, or a client gets HTML instead of a 404.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from house_ear.config import Config
from house_ear.web.api.app import create_app

BLOCK_TYPES = {
    "stats", "now", "actions", "list", "form", "table", "log", "memory", "say", "volume",
}

CONSOLE_ROUTES = [
    "/", "/panel/overview", "/panel/devices", "/panel/turns", "/panel/logs",
    "/panel/memory", "/panel/settings", "/panel/music",
    "/assistant", "/voice", "/models", "/tools", "/secrets", "/diagnostics", "/logs",
]

SETTINGS_FIELDS = {
    "name", "persona", "system_prompt",
    "reply_max_chars", "reply_max_chars_search", "reply_max_chars_long",
    "timeout_s", "farewell", "farewell_text", "keep_context", "history_rounds",
    "vendor_quiet_s",
}


def client(cfg: Config | None = None) -> TestClient:
    return TestClient(create_app(cfg or Config()))


def panels(c: TestClient) -> dict[str, dict]:
    body = c.get("/api/v1/panels").json()
    return {panel["id"]: panel for panel in body["panels"]}


def test_every_console_route_opens_by_url() -> None:
    c = client()
    for route in CONSOLE_ROUTES:
        response = c.get(route)
        assert response.status_code == 200, route
        assert response.headers["content-type"].startswith("text/html"), route


def test_the_health_api_keeps_its_own_path() -> None:
    """`/health` is the JSON endpoint; the 体检 page lives at /diagnostics.

    A page route named /health was shadowed by the API route, so a reload on the
    diagnostics page answered JSON instead of the page.
    """
    c = client()
    assert c.get("/health").headers["content-type"].startswith("application/json")
    assert c.get("/diagnostics").headers["content-type"].startswith("text/html")


def test_api_paths_never_fall_into_the_spa_shell() -> None:
    c = client()
    response = c.get("/api/v1/no-such-endpoint")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_the_console_still_opens_without_a_built_frontend(monkeypatch) -> None:
    """A fresh checkout (or a container before `npm run build`) has no dist."""
    from house_ear.web.api import app as app_module

    monkeypatch.setattr(app_module, "WEB_DIST", app_module.Path("/nonexistent-web-dist"))
    response = TestClient(create_app(Config())).get("/panel/music")
    assert response.status_code == 200
    assert "house-ear" in response.text


def test_panels_have_ids_groups_and_known_block_types() -> None:
    found = panels(client())
    assert len({p["id"] for p in found.values()}) == len(found)
    for panel in found.values():
        assert panel["group"], panel["id"]
        assert isinstance(panel["order"], int), panel["id"]
        for block in panel["blocks"]:
            assert block["type"] in BLOCK_TYPES, (panel["id"], block["type"])


def test_every_declared_source_answers() -> None:
    c = client()
    for panel in panels(c).values():
        for href in [panel["source"], *panel.get("extra_sources", {}).values()]:
            response = c.get(href)
            assert response.status_code == 200, (panel["id"], href)
            assert response.headers["content-type"].startswith("application/json")


def _routes(c: TestClient) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in c.app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            out.add((method, path))
    return out


def test_every_action_points_at_a_real_endpoint() -> None:
    c = client()
    routes = _routes(c)
    for panel in panels(c).values():
        for block in panel["blocks"]:
            targets: list[tuple[str, str]] = []
            if block["type"] == "actions":
                targets = [(b["method"], b["href"]) for b in block["buttons"]]
            elif block["type"] == "say":
                targets = [("POST", block["href"])]
            elif block["type"] == "volume":
                targets = [("GET", block["href"]), ("PUT", block["href"])]
            for method, href in targets:
                assert href.startswith("/api/"), (panel["id"], href)
                assert (method, href) in routes, (panel["id"], method, href)


def test_the_settings_panel_owns_the_deleted_pages_fields() -> None:
    """AssistantView used to edit these; deleting it must not delete the setting."""
    found = panels(client())["settings"]
    written = {
        field["key"]
        for block in found["blocks"] if block["type"] == "form"
        for field in block["fields"]
    }
    assert written == SETTINGS_FIELDS


def test_the_console_declares_the_tools_the_review_found_missing() -> None:
    """试一试 and the device volume read/write existed only in dead view files."""
    found = panels(client())
    overview = {b["type"] for b in found["overview"]["blocks"]}
    assert "say" in overview
    devices = found["devices"]
    assert any(b["type"] == "volume" for b in devices["blocks"])
    # The list subtitle mixes literals and lookups; the renderer must interpolate it.
    subtitle = next(b for b in devices["blocks"] if b["type"] == "list")["item_sub"]
    assert subtitle.count("$.") >= 2
    # The plane's 2 s level line is noise in a 300-line tail.
    log_block = next(b for b in found["logs"]["blocks"] if b["type"] == "log")
    assert "voice peak" in log_block["hide"]
    # P5: device administration and the HA/MCP page are hand-written components.
    assert found["devices-admin"]["component"] == "device-manager"
    assert found["mcp"]["component"] == "mcp-manager"
    for panel_id in ("devices-admin", "mcp", "phrases"):
        assert found[panel_id]["blocks"] == [], panel_id
