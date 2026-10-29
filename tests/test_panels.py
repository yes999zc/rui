"""The console registry contract (docs/console.md, docs/console-ia-audit.md §3).

The backend declares the panels and `/api/v1/panels` still serves them (v1 is
frozen). 0.8.0 removed the legacy pages that rendered them - the routes are asserted
404 in `test_console_switch.py` - so this file pins the registry payload:

1. every declared source and action points at a real endpoint, so a renamed API
   cannot leave a dead button behind (the hrefs are payload, not live pages);
3. the merged pages own every field the deleted hand-written pages used to edit
   (assistant.* / session.* / speakers), otherwise a page deletion silently
   removes a setting from the console;
4. the PanelSpec `nav` object is the one navigation manifest (section/order/
   hidden/icon), so the rail has a single source of truth.

Phase 4 removed the pre-merge panel declarations (assistant-session, speaker,
devices-admin, tools, mcp, foundation, phrases): their content lives on the
combined destinations and their ids must not come back.

The API paths must stay JSON: a mistyped /api/v1/* must not fall into the SPA
shell, or a client gets HTML instead of a 404.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from rui.config import Config
from rui.web.api.app import create_app

BLOCK_TYPES = {
    "stats", "now", "actions", "list", "form", "table", "log", "memory", "say", "volume",
    "credentials",  # music credentials form (write-only url/user/password)
    "config",       # inline catalog group (music enable/source while the page is off)
    "component",    # hand-written view embedded in a tab (DeviceManager/McpManager/...)
}

# 0.8.0 removed the legacy console: the panel routes that used to render these
# declarations are 404 now, and only the `/api/v1/panels` registry remains.
OLD_CONSOLE_PATHS = [
    "/panel/overview", "/panel/turns", "/panel/memory", "/panel/logs",
    "/panel/voice-assistant", "/panel/devices", "/panel/music", "/panel/providers",
    "/panel/integrations", "/panel/system", "/panel/credentials",
    "/channels", "/diagnostics", "/secrets", "/setup", "/legacy",
]

# Compatibility-only panels deleted in Phase 4. Their task is represented by a
# merged destination, so re-adding an id would put a duplicate back in the IA.
RETIRED_PANEL_IDS = {
    "assistant-session", "speaker", "devices-admin", "tools", "mcp",
    "foundation", "phrases",
}

# The designed placement (docs/console-ia-audit.md §3). `nav` is authoritative;
# `group`/`order` remain declared for older control planes.
EXPECTED_NAV = {
    "overview": ("run", 10),
    "turns": ("run", 20),
    "memory": ("run", 30),
    "logs": ("run", 40),
    "voice-assistant": ("configure", 10),
    "devices": ("configure", 20),
    "music": ("configure", 30),
    "providers": ("configure", 40),
    "integrations": ("configure", 50),
    "system": ("configure", 60),
    "credentials": ("configure", 70),
}

NAV_SECTIONS = {"run", "configure"}

SETTINGS_FIELDS = {
    "name", "persona", "system_prompt",
    "reply_max_chars", "reply_max_chars_search", "reply_max_chars_long",
    "timeout_s", "farewell", "farewell_text", "keep_context", "history_rounds",
    "vendor_quiet_s",
}

APP_ICON_VUE = Path(__file__).resolve().parents[1] / "web/src/components/AppIcon.vue"


def client(cfg: Config | None = None) -> TestClient:
    return TestClient(create_app(cfg or Config()))


def panels(c: TestClient) -> dict[str, dict]:
    body = c.get("/api/v1/panels").json()
    return {panel["id"]: panel for panel in body["panels"]}


def app_icon_names() -> set[str]:
    """The shell's icon vocabulary (AppIcon.vue), so nav.icon cannot silently miss."""
    source = APP_ICON_VUE.read_text(encoding="utf-8")
    return set(re.findall(r"^  ([A-Za-z][A-Za-z0-9]*): \[", source, re.MULTILINE))


def test_the_old_panel_routes_are_gone() -> None:
    """The registry stays (`/api/v1/panels`, v1 is frozen); the pages do not."""
    c = client()
    assert c.get("/api/v1/panels").status_code == 200
    for route in OLD_CONSOLE_PATHS:
        response = c.get(route)
        assert response.status_code == 404, route


def test_the_health_api_keeps_its_own_path() -> None:
    """`/health` is the JSON endpoint; the 体检 page lives at /diagnostics.

    A page route named /health was shadowed by the API route, so a reload on the
    diagnostics page answered JSON instead of the page.
    """
    c = client()
    assert c.get("/health").headers["content-type"].startswith("application/json")
    # The old diagnostics page is gone; the API path must not turn into an HTML shell.
    assert c.get("/diagnostics").status_code == 404


def test_api_paths_never_fall_into_the_spa_shell() -> None:
    c = client()
    response = c.get("/api/v1/no-such-endpoint")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_the_v2_shell_still_answers_without_a_built_frontend(monkeypatch) -> None:
    """A fresh checkout (or a container before `npm run build`) has no dist."""
    from rui.web.api import app as app_module

    monkeypatch.setattr(app_module, "WEB_DIST", app_module.Path("/nonexistent-web-dist"))
    c = TestClient(create_app(Config()))
    root = c.get("/", follow_redirects=False)
    assert root.status_code == 302 and root.headers["location"] == "/v2/"
    response = c.get("/v2")
    assert response.status_code == 200
    assert "v2 console" in response.text


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


def test_nav_is_the_single_navigation_manifest() -> None:
    """Phase 4: section/order/hidden/icon live in the PanelSpec nav object.

    Every kept panel is either placed in a section or explicitly hidden, and the
    icon is a real AppIcon name - the rail and the page kicker both read this.
    """
    found = panels(client())
    icons = app_icon_names()
    assert "gauge" in icons, f"could not parse {APP_ICON_VUE}"

    assert set(found) == set(EXPECTED_NAV)
    for panel_id, (section, order) in EXPECTED_NAV.items():
        panel = found[panel_id]
        nav = panel["nav"]
        assert nav["section"] in NAV_SECTIONS, panel_id
        assert nav["section"] == section, panel_id
        assert nav["order"] == order, panel_id
        assert isinstance(nav["hidden"], bool), panel_id
        assert nav["icon"] in icons, (panel_id, nav["icon"])
        # Compatibility fields stay declared but no longer decide placement.
        assert panel["group"], panel_id
        assert isinstance(panel["order"], int), panel_id

    hidden = {panel_id for panel_id, panel in found.items() if panel["nav"]["hidden"]}
    assert hidden == {"credentials"}
    # Every visible panel has exactly one place in its section.
    placements = [(p["nav"]["section"], p["nav"]["order"])
                  for p in found.values() if not p["nav"]["hidden"]]
    assert len(placements) == len(set(placements))


def test_retired_panels_are_not_declared() -> None:
    """Phase 4 removed the compatibility-only declarations after 0.4.0/0.4.1.

    Their content is on the merged destinations (voice-assistant, devices,
    integrations, system); a stale declaration would be a second, dead home.
    """
    found = panels(client())
    assert RETIRED_PANEL_IDS.isdisjoint(found)


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


def test_the_merged_pages_render_from_the_catalog() -> None:
    """Deleting the pre-merge panels must not delete a catalog home.

    AssistantView/ConfigCenter used to edit these fields, so the directory has to
    keep every one of them - a page deletion that silently removes a setting is
    the failure this pins. Phase 4 removed the duplicate pages, not the fields.
    """
    c = client()
    found = panels(c)
    assert "settings" not in found
    catalog = c.get("/api/v1/config/catalog").json()
    declared_groups = {group["id"] for group in catalog["groups"]}
    paths = {field["path"] for field in catalog["fields"]}

    provider_groups = {"providers.catalog", "providers.asr", "providers.tts",
                       "providers.llm"}
    assert provider_groups <= declared_groups
    # Providers has its own page (three stage chains, cards, audition); its fields
    # still live in the same catalog, so no setting loses a home.
    providers = found["providers"]
    assert providers["component"] == "providers-v2"
    assert providers["blocks"] == []
    assert providers["source"] == "/api/v1/config/catalog"

    # Assistant/session behaviour lives on the voice-assistant reply tab.
    assert _config_groups(_tab_blocks(found["voice-assistant"], "reply")) == {
        "assistant", "session"}
    # Speaker hardware parameters live on the devices audio tab.
    assert _config_groups(_tab_blocks(found["devices"], "audio")) == {
        "multi_device", "devices", "audio.capture", "audio.voice", "audio.playback",
        "audio.aec", "ack"}
    # Process/console/SSRF fields live in the system component; log_level keeps its
    # catalog home but is rendered only by the logs page.
    system = found["system"]
    assert set(system["component_args"]["groups"]) == {"system", "web", "security"}
    assert system["component_args"]["excludePaths"] == ["system.log_level"]
    log_config = [block for block in found["logs"]["blocks"] if block["type"] == "config"]
    assert [block["paths"] for block in log_config] == [["system.log_level"]]

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

    # A rendered group that is not in the catalog is a dead editor.
    for panel in found.values():
        for block in panel["blocks"]:
            assert set(block.get("groups", [])) <= declared_groups, panel["id"]
        assert (set(panel.get("component_args", {}).get("groups", []))
                <= declared_groups), panel["id"]


# Phase 2 (docs/console-ia-audit.md §3/§5): merged destinations and their tabs.
COMBINED_TABS = {
    "voice-assistant": ["reply", "phrases"],
    "devices": ["status", "manage", "audio"],
    "integrations": ["builtin", "mcp", "registry", "exposure"],
}


def test_combined_pages_compose_existing_components() -> None:
    """Phase 2 merges pages without copying their logic or adding a second save.

    The `/api/v1/panels` payload keeps the merged tab lists and the component ids the
    old console used (DeviceManager, ConfigCenter, McpManager, PhrasebookEditor). The
    Vue files behind those ids were deleted with the legacy console in 0.8.0; this
    test only pins the v1 payload: one `config` block per tab, real catalog groups.
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
    assert audio_groups == {"multi_device", "devices", "audio.capture", "audio.voice",
                            "audio.playback", "audio.aec", "ack"}

    # 工具与集成: the v1 payload still declares tools.ha/tools.mcp here. The old
    # writers (ConfigCenter/McpManager) are deleted in 0.8.0, v2 has no writer for
    # tools.mcp_servers (PUT /api/v1/mcp remains the API). The read-only registry is
    # embedded, and exposure gets its own tab (not a duplicate policy editor).
    integrations = found["integrations"]
    assert _config_groups(_tab_blocks(integrations, "builtin")) == {
        "tools.weather", "tools.news", "tools.search", "tools.joke"}
    assert _component_ids(_tab_blocks(integrations, "mcp")) == {"mcp-manager"}
    registry = _tab_blocks(integrations, "registry")
    assert _component_ids(registry) == {"tools-view"}
    assert registry[0]["component_args"]["embedded"] is True
    assert _config_groups(_tab_blocks(integrations, "exposure")) == {"tools.exposure"}
    all_integration_groups = {
        group for block in integrations["blocks"] for group in block.get("groups", [])}
    assert "music" not in all_integration_groups
    # The v1 payload still declares the groups; the panel UI that rendered them
    # (McpManager) was deleted with the legacy console in 0.8.0.
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
        assert panel.get("title"), panel["id"]


def test_system_owns_the_process_data_and_logs_own_log_level() -> None:
    """Phase 3 IA: `/panel/system` is the rail name; the data never left the catalog.

    Phase 4 removed the `foundation` duplicate panel: its groups are exactly the
    system component's groups, so every field keeps a single home.
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

    logs = found["logs"]
    log_config = [block for block in logs["blocks"] if block["type"] == "config"]
    assert len(log_config) == 1
    assert log_config[0]["groups"] == ["system"]
    assert log_config[0]["paths"] == ["system.log_level"]
    assert any(block["type"] == "log" for block in logs["blocks"])
    assert c.get("/panel/system").status_code == 404


def test_credentials_is_a_hidden_typed_ledger() -> None:
    c = client()
    found = panels(c)
    credentials = found["credentials"]
    assert credentials["component"] == "secrets-view"
    assert credentials["title"] == "凭据（只写）"
    assert credentials["title_en"] == "Credentials (write-only)"
    assert credentials["source"] == "/api/v1/secrets"
    assert credentials["blocks"] == []
    assert credentials["nav"]["hidden"] is True
    # The registry entry survives; the old pages that rendered it are 404.
    for route in ("/panel/credentials", "/secrets"):
        assert c.get(route).status_code == 404, route


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
    # P5: device administration, the HA/MCP editor and the phrasebook are
    # hand-written components inside the merged destinations, not panels of their own.
    assert _component_ids(_tab_blocks(devices, "manage")) == {"device-manager"}
    assert _component_ids(_tab_blocks(found["integrations"], "mcp")) == {"mcp-manager"}
    assert _component_ids(_tab_blocks(found["voice-assistant"], "phrases")) == {
        "phrasebook-editor"}
