"""The console registry contract (docs/console.md).

The backend declares the panels, the front end renders them. A page review keeps
breaking the same three things, so they are pinned here:

1. every console route opens by URL (a reload while sitting on /panel/music used
   to answer a JSON 404 - the SPA had no history fallback);
2. every declared source and action points at a real endpoint, so a renamed API
   cannot leave a dead button behind;
3. the config-center panels own every field the deleted hand-written pages
   used to edit (assistant.* / session.*), otherwise deleting those pages
   silently removes a setting from the console.

The API paths must stay JSON: a mistyped /api/v1/* must not fall into the SPA
shell, or a client gets HTML instead of a 404.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from rui.config import Config
from rui.web.api.app import create_app

BLOCK_TYPES = {
    "stats", "now", "actions", "list", "form", "table", "log", "memory", "say", "volume",
    "credentials",  # music credentials form (write-only url/user/password)
    "config",       # inline catalog group (music enable/source while the page is off)
    "component",    # hand-written view embedded in a tab (DeviceManager/McpManager/...)
}

CONSOLE_ROUTES = [
    "/", "/panel/overview", "/panel/devices", "/panel/turns", "/panel/logs",
    "/panel/memory", "/panel/settings", "/panel/music", "/panel/assistant-session",
    "/panel/speaker", "/panel/devices-admin", "/panel/providers", "/panel/tools",
    "/panel/phrases", "/panel/mcp", "/panel/foundation",
    # Phase 2 combined destinations.
    "/panel/voice-assistant", "/panel/integrations",
    # Phase 3: system replaces foundation in the rail; credentials is hidden but a
    # real destination, and `/secrets` remains the standalone bookmark route.
    "/panel/system", "/panel/credentials",
    "/assistant", "/settings", "/voice", "/models", "/tools", "/secrets",
    "/diagnostics", "/logs", "/setup", "/channels",
]

# The IA change hides some destinations but must not delete their routes/panel ids
# for one release (audit §3 "保留与兼容", §5 Phase 1).
LEGACY_PANEL_IDS = {
    "overview", "devices", "turns", "logs", "memory", "music",
    "assistant-session", "speaker", "devices-admin", "providers", "tools",
    "phrases", "mcp", "foundation",
}

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
    from rui.web.api import app as app_module

    monkeypatch.setattr(app_module, "WEB_DIST", app_module.Path("/nonexistent-web-dist"))
    response = TestClient(create_app(Config())).get("/panel/music")
    assert response.status_code == 200
    assert "rui" in response.text


def test_panels_have_ids_groups_and_known_block_types() -> None:
    found = panels(client())
    assert len({p["id"] for p in found.values()}) == len(found)
    for panel in found.values():
        assert panel["group"], panel["id"]
        assert isinstance(panel["order"], int), panel["id"]
        tabs = [tab["id"] for tab in panel.get("tabs", [])]
        assert len(tabs) == len(set(tabs)), panel["id"]
        for tab in panel.get("tabs", []):
            assert tab.get("title"), (panel["id"], tab)
        for block in panel["blocks"]:
            assert block["type"] in BLOCK_TYPES, (panel["id"], block["type"])
            if block.get("tab"):
                assert block["tab"] in tabs, (panel["id"], block["tab"])
            if block["type"] == "component":
                assert block.get("component"), panel["id"]


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


CONFIG_CENTER_PANELS = {
    "assistant-session": {"assistant", "session"},
    # tier 2: everything that follows the speaker hardware (devices + DSP + ack).
    "speaker": {"devices", "audio.capture", "audio.voice", "audio.playback",
                "audio.aec", "ack"},
    # Legacy tools page: music left for the music page in Phase 2; the combined
    # integrations page splits these groups across tabs instead.
    "tools": {"tools.weather", "tools.news", "tools.search", "tools.ha",
              "tools.mcp", "tools.exposure"},
    # tier 1 process foundations only; assistant/session is its own panel.
    "foundation": {"system", "web", "security"},
}


def test_the_config_center_panels_render_from_the_catalog() -> None:
    """The old hand-written `settings` panel is gone; five catalog-driven panels replace it.

    AssistantView used to edit these fields, so the directory has to keep every one of
    them - a page deletion that silently deletes a setting is the failure this pins.
    """
    c = client()
    found = panels(c)
    assert "settings" not in found
    catalog = c.get("/api/v1/config/catalog").json()
    declared_groups = {group["id"] for group in catalog["groups"]}
    paths = {field["path"] for field in catalog["fields"]}
    covered: set[str] = set()
    provider_groups = {"providers.catalog", "providers.asr", "providers.tts",
                       "providers.llm"}
    assert provider_groups <= declared_groups
    # Providers has its own page since 2026-10-23 (three stage chains, cards, audition);
    # its fields still live in the same catalog, so no setting loses a home.
    providers = found["providers"]
    assert providers["component"] == "providers-v2"
    assert providers["blocks"] == []
    assert providers["source"] == "/api/v1/config/catalog"
    for panel_id, groups in CONFIG_CENTER_PANELS.items():
        panel = found[panel_id]
        assert panel["component"] == "config-center", panel_id
        assert panel["blocks"] == [], panel_id
        assert panel["source"] == "/api/v1/config/catalog", panel_id
        assert set(panel["component_args"]["groups"]) == groups, panel_id
        assert groups <= declared_groups, (panel_id, groups - declared_groups)
        covered |= groups
    settings_paths = {
        f"assistant.{key}" for key in SETTINGS_FIELDS if key not in
        {"timeout_s", "farewell", "farewell_text", "keep_context", "history_rounds",
         "vendor_quiet_s"}
    } | {
        f"session.{key}" for key in SETTINGS_FIELDS if key in
        {"timeout_s", "farewell", "farewell_text", "keep_context", "history_rounds",
         "vendor_quiet_s"}
    }
    assert settings_paths <= paths
    assert {"assistant", "session"} <= covered


# Phase 2 (docs/console-ia-audit.md §3/§5): merged destinations and their tabs.
COMBINED_TABS = {
    "voice-assistant": ["reply", "phrases"],
    "devices": ["status", "manage", "audio"],
    "integrations": ["builtin", "mcp", "registry", "exposure"],
}


def _tab_blocks(panel: dict, tab_id: str) -> list[dict]:
    return [block for block in panel["blocks"] if block.get("tab") == tab_id]


def _config_groups(blocks: list[dict]) -> set[str]:
    return {group for block in blocks if block["type"] == "config"
            for group in block.get("groups", [])}


def _component_ids(blocks: list[dict]) -> set[str]:
    return {block["component"] for block in blocks if block["type"] == "component"}


def _section_titles(blocks: list[dict]) -> list[str]:
    out: list[str] = []
    for block in blocks:
        title = block.get("section_title")
        text = title.get("zh") if isinstance(title, dict) else title
        if text and (not out or out[-1] != text):
            out.append(text)
    return out


def test_combined_pages_compose_existing_components() -> None:
    """Phase 2 merges pages without copying their logic or adding a second save.

    Each merged page is a tab list over the *existing* block vocabulary and
    escape-hatch components: list/volume for the live speaker state, DeviceManager
    for the lifecycle, ConfigCenter for catalog groups, McpManager/PhrasebookEditor
    for the heavy editors. One `config` block per tab is what guarantees a single
    sticky save bar is visible at a time.
    """
    c = client()
    found = panels(c)
    catalog = c.get("/api/v1/config/catalog").json()
    declared_groups = {group["id"] for group in catalog["groups"]}

    for panel_id, tab_ids in COMBINED_TABS.items():
        panel = found[panel_id]
        assert [tab["id"] for tab in panel["tabs"]] == tab_ids, panel_id
        assert panel["source"], panel_id
        for tab_id in tab_ids:
            blocks = _tab_blocks(panel, tab_id)
            assert blocks, (panel_id, tab_id)
            configs = [block for block in blocks if block["type"] == "config"]
            assert len(configs) <= 1, (panel_id, tab_id, "one sticky save per tab")
            assert _config_groups(blocks) <= declared_groups, (panel_id, tab_id)

    # 设备与音频: live state first, lifecycle second, hardware parameters third.
    devices = found["devices"]
    assert {"list", "volume"} <= {b["type"] for b in _tab_blocks(devices, "status")}
    assert _component_ids(_tab_blocks(devices, "manage")) == {"device-manager"}
    audio_groups = _config_groups(_tab_blocks(devices, "audio"))
    assert audio_groups == {"devices", "audio.capture", "audio.voice",
                            "audio.playback", "audio.aec", "ack"}

    # 工具与集成: ConfigCenter no longer renders the HA/MCP writers; McpManager is
    # the only hot-write path. The read-only registry is embedded, and exposure
    # gets its own tab (not a duplicate policy editor inside the registry).
    integrations = found["integrations"]
    assert _config_groups(_tab_blocks(integrations, "builtin")) == {
        "tools.weather", "tools.news", "tools.search"}
    assert _component_ids(_tab_blocks(integrations, "mcp")) == {"mcp-manager"}
    registry = _tab_blocks(integrations, "registry")
    assert _component_ids(registry) == {"tools-view"}
    assert registry[0]["component_args"]["embedded"] is True
    assert _config_groups(_tab_blocks(integrations, "exposure")) == {"tools.exposure"}
    all_integration_groups = {
        group for block in integrations["blocks"] for group in block.get("groups", [])}
    assert "music" not in all_integration_groups
    # The HA/MCP fields still have a home: McpManager owns them.
    assert {"tools.ha", "tools.mcp"} <= declared_groups

    # 语音助手: behavior/replies and the phrasebook are one destination.
    assistant = found["voice-assistant"]
    assert _config_groups(_tab_blocks(assistant, "reply")) == {"assistant", "session"}
    assert _component_ids(_tab_blocks(assistant, "phrases")) == {"phrasebook-editor"}

    # 音乐: sections, not tabs; the config block lives under 高级 and keeps every
    # music.* field editable, while credentials stay write-only.
    music = found["music"]
    assert "tabs" not in music
    assert _section_titles(music["blocks"]) == ["正在播放", "音源与凭据", "高级"]
    playback = [b for b in music["blocks"] if b["type"] in {"stats", "now", "actions", "list"}]
    assert playback and all(b["section_title"]["zh"] == "正在播放" for b in playback)
    credential = next(b for b in music["blocks"] if b["type"] == "credentials")
    assert credential["section_title"]["zh"] == "音源与凭据"
    music_config = next(b for b in music["blocks"] if b["type"] == "config")
    assert music_config["section_title"]["zh"] == "高级"
    assert music_config["groups"] == ["music"]


def test_every_panel_has_an_english_title() -> None:
    """The console is bilingual; the backend supplies both titles (task-6 renders them)."""
    for panel in panels(client()).values():
        assert panel.get("title_en"), panel["id"]


def test_legacy_panel_ids_stay_declared_for_one_release() -> None:
    """Hiding a rail entry is a frontend decision; the panel id/route is the contract.

    The two-group rail merges assistant-session+phrases, devices+speaker+devices-admin
    and tools+mcp. Old bookmarks and external links must keep resolving for a release.
    """
    found = panels(client())
    c = client()
    assert LEGACY_PANEL_IDS <= set(found)
    for panel_id in LEGACY_PANEL_IDS:
        response = c.get(f"/panel/{panel_id}")
        assert response.status_code == 200, panel_id
        assert response.headers["content-type"].startswith("text/html"), panel_id


def test_system_owns_foundation_data_and_logs_owns_log_level() -> None:
    """Phase 3 IA: `/panel/system` is the rail name; the data did not move out of the catalog.

    `foundation` stays declared for one release and `/panel/foundation` still answers.
    `system.log_level` keeps its catalog home but is rendered only by the logs page.
    """
    c = client()
    found = panels(c)
    system = found["system"]
    assert system["component"] == "system-console"
    assert system["source"] == "/api/v1/config/catalog"
    args = system["component_args"]
    assert set(args["groups"]) == {"system", "web", "security"}
    assert args["excludePaths"] == ["system.log_level"]

    catalog = c.get("/api/v1/config/catalog").json()
    paths = {field["path"] for field in catalog["fields"]}
    assert {"system.data_dir", "system.log_level", "system.timezone",
            "web.host", "web.port", "web.allow_anonymous"} <= paths
    outbound = {path for path in paths if path.startswith("security.outbound.")}
    assert len(outbound) >= 4, outbound
    # foundation keeps all 13 fields (its own config-center has no exclusion).
    assert found["foundation"]["component"] == "config-center"
    assert set(found["foundation"]["component_args"]["groups"]) == {
        "system", "web", "security"}

    logs = found["logs"]
    log_config = [block for block in logs["blocks"] if block["type"] == "config"]
    assert len(log_config) == 1
    assert log_config[0]["groups"] == ["system"]
    assert log_config[0]["paths"] == ["system.log_level"]
    assert any(block["type"] == "log" for block in logs["blocks"])

    for route in ("/panel/system", "/panel/foundation"):
        response = c.get(route)
        assert response.status_code == 200, route
        assert response.headers["content-type"].startswith("text/html"), route


def test_credentials_is_a_hidden_typed_ledger_with_the_old_route_alive() -> None:
    c = client()
    found = panels(c)
    credentials = found["credentials"]
    assert credentials["component"] == "secrets-view"
    assert credentials["title"] == "凭据（只写）"
    assert credentials["title_en"] == "Credentials (write-only)"
    assert credentials["source"] == "/api/v1/secrets"
    assert credentials["blocks"] == []
    for route in ("/panel/credentials", "/secrets"):
        response = c.get(route)
        assert response.status_code == 200, route
        assert response.headers["content-type"].startswith("text/html"), route


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
