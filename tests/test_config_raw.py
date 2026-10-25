"""`GET /api/v1/config/raw`: the System page's read-only, redacted YAML view.

The endpoint is a transparency feature, so the risk is entirely in what it might leak:
the effective config (defaults + config.yaml) including an auth token, legacy inline
music credentials, provider keys, MCP headers/env. These tests pin four things:

1. the response is a valid YAML document of the config the process is running with;
2. no credential value appears anywhere in the payload - it is replaced by
   `<redacted>` and named in `redacted_keys`;
3. the file path + mtime come from the file the app was started with, and a config
   running on schema defaults says so instead of inventing a path;
4. every method is GET: there is no write path, and auth is required like the rest of
   the API.

The `redact_secrets` unit tests cover credential-shaped fields the current schema has
no plaintext field for (provider inline keys, MCP headers/env, device params) so the
defence does not silently depend on today's model.
"""
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from rui.config import Config, save_config
from rui.config.schema import CONFIG_VERSION
from rui.web.api.app import create_app
from rui.web.api.config_view import (
    MAX_YAML_CHARS,
    REDACTED,
    config_raw_document,
    redact_secrets,
)

TOKEN = "console-auth-token-do-not-leak"
LEGACY_PASSWORD = "legacy-music-password-do-not-leak"
LEGACY_TOKEN = "legacy-music-token-do-not-leak"


def _yaml() -> YAML:
    return YAML()


def _load(text: str) -> dict:
    return _yaml().load(StringIO(text))


def _client(cfg: Config, path: Path | None = None) -> TestClient:
    return TestClient(create_app(cfg, config_path=path))


def test_the_raw_view_returns_the_effective_config_as_yaml(tmp_path) -> None:
    cfg = Config(web={"auth_token": TOKEN})
    cfg.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)

    response = _client(cfg, path).get(
        "/api/v1/config/raw", headers={"X-Auth-Token": TOKEN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"path", "mtime", "yaml", "redacted_keys", "truncated"}
    assert body["path"] == str(path)
    assert body["mtime"] == pytest.approx(path.stat().st_mtime)
    assert body["truncated"] is False
    # Effective config, not just the overrides: schema defaults are all there.
    document = _load(body["yaml"])
    assert document["version"] == CONFIG_VERSION
    assert document["web"]["port"] == 8080
    # Neutral core default (0.6 M1); a device's hardware layout comes from its plug
    # manifest and is not part of the config document.
    assert document["audio"]["capture"]["channels"] == 1
    assert document["assistant"]["name"] == "小瑞"
    # Chinese copy round-trips instead of becoming YAML escapes.
    assert "我在。" in body["yaml"]


def test_the_console_token_is_redacted_and_named(tmp_path) -> None:
    cfg = Config(web={"auth_token": TOKEN})
    cfg.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)

    body = _client(cfg, path).get(
        "/api/v1/config/raw", headers={"X-Auth-Token": TOKEN}).json()
    assert TOKEN not in json.dumps(body)
    assert TOKEN not in body["yaml"]
    assert body["redacted_keys"] == ["web.auth_token"]
    assert _load(body["yaml"])["web"]["auth_token"] == REDACTED


def test_the_raw_view_requires_auth(tmp_path) -> None:
    cfg = Config(web={"auth_token": TOKEN})
    cfg.system.data_dir = str(tmp_path / "data")
    client = _client(cfg)
    assert client.get("/api/v1/config/raw").status_code == 401
    assert client.get(
        "/api/v1/config/raw", headers={"X-Auth-Token": "wrong"}).status_code == 401
    assert client.get(
        "/api/v1/config/raw", headers={"X-Auth-Token": TOKEN}).status_code == 200
    # An empty token (loopback dev default) keeps the endpoint open, like every other
    # route: auth follows `web.auth_token`, it is not a second mechanism.
    assert _client(Config()).get("/api/v1/config/raw").status_code == 200


def test_the_endpoint_is_get_only() -> None:
    """Read-only by construction: there is no counterpart that writes the YAML back."""
    client = TestClient(create_app(Config()))
    methods: set[str] = set()
    for route in client.app.routes:  # type: ignore[attr-defined]
        if getattr(route, "path", "") == "/api/v1/config/raw":
            methods |= set(getattr(route, "methods", set()) or set())
    assert methods == {"GET"}
    assert client.put("/api/v1/config/raw", json={}).status_code in {404, 405}
    assert client.post("/api/v1/config/raw", json={}).status_code in {404, 405}


def test_legacy_inline_music_credentials_are_redacted_in_the_payload(
        tmp_path, monkeypatch) -> None:
    """A failed migration leaves plaintext in the live model; the view must not leak it.

    `create_app` normally moves `music.url/user/password/token` into the 0600 JSON and
    clears them. Patching the migration off reproduces the failure mode (unwritable data
    dir) and proves the raw view is safe even then, plus that it *names* the fields it
    hid instead of silently dropping them.
    """
    monkeypatch.setattr(
        "rui.web.api.app.migrate_legacy_music_credentials", lambda _cfg: False)
    cfg = Config(web={"auth_token": TOKEN})
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.enabled = True
    cfg.music.url = "http://192.0.2.50:4533"
    cfg.music.user = "someone"
    cfg.music.password = LEGACY_PASSWORD
    cfg.music.token = LEGACY_TOKEN
    client = TestClient(create_app(cfg))
    response = client.get("/api/v1/config/raw", headers={"X-Auth-Token": TOKEN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert LEGACY_PASSWORD not in response.text
    assert LEGACY_TOKEN not in response.text
    assert "music.password" in body["redacted_keys"]
    assert "music.token" in body["redacted_keys"]
    music = _load(body["yaml"])["music"]
    assert music["password"] == REDACTED
    assert music["token"] == REDACTED
    # The non-secret slots stay readable: hiding the server URL would not protect
    # anything and is the one fact needed to understand the credential.
    assert music["url"] == "http://192.0.2.50:4533"
    assert music["user"] == "someone"


def test_a_config_with_no_file_names_no_path(tmp_path) -> None:
    """A process on schema defaults has no origin file; do not invent one."""
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    body = _client(cfg).get("/api/v1/config/raw").json()
    assert body["path"] == ""
    assert body["mtime"] == 0.0
    assert _load(body["yaml"])["version"] == CONFIG_VERSION


def test_an_oversized_config_answers_a_bounded_valid_document() -> None:
    cfg = Config()
    cfg.assistant.persona = "x" * (MAX_YAML_CHARS + 10)
    body = config_raw_document(cfg, None)
    assert body["truncated"] is True
    assert len(body["yaml"]) < 1_000
    # Still YAML: the view refuses to show an unparseable half-document.
    assert _load(body["yaml"]) == {"truncated": True}
    assert "x" * 100 not in body["yaml"]


def test_redact_secrets_covers_credentials_the_schema_has_no_field_for() -> None:
    """The walk is name-based, so a future inline credential is covered on day one."""
    dump = {
        "web": {"auth_token": "web-auth-value", "auth_required": True},
        "devices": [{"id": "living", "token_env": "RUI_DEVICE_TOKEN",
                     "params": {"password": "device-pass-value"}}],
        "providers": {"catalog": {"llm": {
            "api_key_env": "RUI_LLM_KEY", "api_key": "sk-inline-value",
            "api_secret_env": "", "base_url": "https://api.example.test"}}},
        "tools": {"search": {"api_key_env": "DEEPSEEK_API_KEY",
                             "api_key": "sk-search-value"},
                  "mcp_servers": [{
                      "name": "gh",
                      "token_env": "GH_TOKEN",
                      "headers": {"Authorization": "Bearer header-secret-value",
                                  "Accept": "application/json"},
                      "env": {"GITHUB_TOKEN": "ghp-env-secret-value", "PATH": "/usr/bin"},
                  }]},
        "music": {"password": "music-pass-value", "secrets_path": "/tmp/creds.json",
                  "cache_dir": "/tmp/music"},
    }
    clean, keys = redact_secrets(dump)
    serialised = json.dumps(clean)
    for secret in ("web-auth-value", "device-pass-value", "sk-inline-value",
                   "sk-search-value", "header-secret-value", "ghp-env-secret-value",
                   "music-pass-value"):
        assert secret not in serialised, secret
    assert keys == [
        "devices[0].params.password",
        "music.password",
        "providers.catalog.llm.api_key",
        "tools.mcp_servers[0].env.GITHUB_TOKEN",
        "tools.mcp_servers[0].headers.Authorization",
        "tools.search.api_key",
        "web.auth_token",
    ]
    # Names are references, not values; presence flags are facts. Neither is hidden.
    assert clean["devices"][0]["token_env"] == "RUI_DEVICE_TOKEN"
    assert clean["providers"]["catalog"]["llm"]["api_key_env"] == "RUI_LLM_KEY"
    assert clean["tools"]["mcp_servers"][0]["headers"]["Accept"] == "application/json"
    assert clean["tools"]["mcp_servers"][0]["env"]["PATH"] == "/usr/bin"
    assert clean["web"]["auth_required"] is True
    assert clean["music"]["secrets_path"] == "/tmp/creds.json"


def test_empty_values_are_not_reported_as_redacted() -> None:
    """`auth_token: ""` means "no token"; marking it redacted would claim otherwise."""
    clean, keys = redact_secrets({"web": {"auth_token": ""}, "music": {"password": ""}})
    assert clean == {"web": {"auth_token": ""}, "music": {"password": ""}}
    assert keys == []


def test_the_system_page_renders_the_view_bilingually() -> None:
    """UI contract: the System panel consumes the endpoint read-only, with both languages.

    No component test harness exists in this repo; the console contract tests read the
    Vue source (tests/test_panels.py, tests/test_audit_fixes.py), and that is enough to
    keep the section wired and the copy translated.
    """
    root = Path(__file__).resolve().parents[1]
    view = (root / "web/src/views/SystemView.vue").read_text(encoding="utf-8")
    assert "api.configRaw()" in view
    assert "system.rawTitle" in view
    assert "system.rawRedacted" in view
    assert "<pre" in view and 'class="mono raw-yaml"' in view
    # Read-only: no write call and no editable control for this data.
    assert "saveConfig" not in view and "configRaw" in view
    assert "contenteditable" not in view and "<textarea" not in view

    i18n = (root / "web/src/i18n.ts").read_text(encoding="utf-8")
    for key in ("system.rawTitle", "system.rawHint", "system.rawPath",
                "system.rawMtime", "system.rawCopy", "system.rawRedacted",
                "system.rawNoneRedacted", "system.rawTruncated", "system.rawDefaults"):
        entries = [line for line in i18n.splitlines() if f"'{key}':" in line]
        assert len(entries) >= 2, (key, "missing a translation")
        for line in entries:
            assert line.split("':", 1)[1].strip().strip("',"), (key, line)
