"""Music source registry + generic Subsonic source, against a real fake HTTP server.

Phase 3 (docs/console-ia-audit.md §2.1/§5) asked for a preset-driven source instead of
a hardcoded Navidrome. These tests pin the contract:

* the registry offers Navidrome/Airsonic/Gonic/Ampache/custom, all Subsonic;
* one `SubsonicSource` serves every preset, and a real login/search/random/favorites
  round trip works for at least Navidrome and Airsonic (fake server, token auth
  verified server-side);
* unknown `music.source` is a 422 at save and never claims to be enabled;
* credentials stay write-only (values never appear in any response);
* a legacy config with inline `music.url/user/password` migrates itself;
* the test endpoint performs a real `ping` and redacts failures.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, load_config, save_config
from rui.music import build_source
from rui.music.sources.presets import get_preset, missing_credentials, preset_ids
from rui.security.netguard import OutboundPolicy
from rui.web.api.app import create_app

USER = "bakey"
SECRET = "s3cret-subsonic-pass"


class _Handler(BaseHTTPRequestHandler):
    """A tiny real Subsonic endpoint. Authentication is verified, not assumed."""

    protocol_version = "HTTP/1.0"

    def log_message(self, *_args) -> None:  # keep pytest output clean
        pass

    def _send(self, body: dict, status: int = 200) -> None:
        raw = json.dumps({"subsonic-response": body}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        name = parsed.path.rsplit("/", 1)[-1]
        if not name.endswith(".view"):
            self._send({"status": "failed", "error": {"code": 0, "message": "no such endpoint"}})
            return
        method = name[:-5]
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.server.calls.append(method)  # type: ignore[attr-defined]
        self.server.queries.append(query)  # type: ignore[attr-defined]
        salt = query.get("s", "")
        expected = hashlib.md5((SECRET + salt).encode()).hexdigest()
        if not (query.get("u") == USER and query.get("t") == expected):
            self._send({"status": "failed",
                        "error": {"code": 40, "message": "Wrong username or password"}})
            return
        if method == "ping":
            self._send({"status": "ok", "version": "1.16.1"})
        elif method == "search3":
            self._send({"status": "ok", "searchResult3": {"song": [
                {"id": "1", "title": "晴天", "artist": "周杰伦", "duration": 269,
                 "genre": "Mandopop", "bpm": 0},
                {"id": "2", "title": "前奏", "artist": "某人", "duration": 12},
            ]}})
        elif method == "getRandomSongs":
            self._send({"status": "ok", "randomSongs": {"song": [
                {"id": "3", "title": "夜曲", "artist": "周杰伦", "duration": 200},
            ]}})
        elif method == "getStarred2":
            self._send({"status": "ok", "starred2": {"song": [
                {"id": "4", "title": "收藏曲", "artist": "某人", "duration": 180},
            ]}})
        elif method in {"star", "unstar"}:
            self._send({"status": "ok"})
        else:
            self._send({"status": "failed",
                        "error": {"code": 0, "message": f"unsupported {method}"}})


class FakeSubsonic(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.calls: list[str] = []
        self.queries: list[dict] = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


@pytest.fixture()
def subsonic():
    server = FakeSubsonic()
    server.thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        server.thread.join(timeout=2)


def _api(tmp_path: Path) -> tuple[TestClient, Config, Path]:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.source = "navidrome"
    cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    cfg.music.cache_dir = str(tmp_path / "cache")
    # The fake server is loopback; the real SSRF guard stays enabled.
    cfg.security.outbound.allow_loopback = True
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), cfg, path


# --------------------------------------------------------------------- registry

def test_registry_offers_the_subsonic_family() -> None:
    assert preset_ids() == ("navidrome", "airsonic", "gonic", "ampache", "custom")
    for preset_id in ("navidrome", "airsonic", "gonic", "ampache"):
        preset = get_preset(preset_id)
        assert preset is not None
        assert preset.protocol_family == "subsonic"
        assert preset.supported is True
        assert preset.default_port
        assert preset.docs_url.startswith("https://")
        assert {item.key for item in preset.credential_fields} == {
            "url", "user", "password", "token"}
        # Password and token are alternatives; URL and user are mandatory.
        assert missing_credentials(preset, {}) == ("url", "user", "password")
        assert missing_credentials(preset, {"url": "http://x", "user": "u",
                                            "token": "t"}) == ()
    custom = get_preset("custom")
    assert custom is not None and custom.custom is True
    assert custom.default_port is None
    assert get_preset("jellyfin") is None


def test_config_source_is_a_registry_driven_select(tmp_path: Path) -> None:
    client, _cfg, _path = _api(tmp_path)
    catalog = client.get("/api/v1/config/catalog").json()
    field = next(item for item in catalog["fields"] if item["path"] == "music.source")
    assert field["kind"] == "select"
    assert set(field["options"]) == set(preset_ids())
    assert field["effect"] == "save"


# ------------------------------------------------------- real Subsonic round trip

@pytest.mark.parametrize("preset_id", ["navidrome", "airsonic"])
def test_fake_subsonic_login_search_random_favorites(
        tmp_path: Path, subsonic: FakeSubsonic, preset_id: str) -> None:
    secrets = tmp_path / "lan_media.json"
    secrets.write_text(json.dumps({
        "url": subsonic.url, "user": USER, "password": SECRET}), encoding="utf-8")
    source = build_source(
        preset_id, secrets, tmp_path / "cache", cache_tracks=2,
        policy=OutboundPolicy(enabled=False),  # loopback is intentional here
    )

    async def run() -> None:
        try:
            await source.ping()
            hits = await source.search("晴天", 10)
            assert [track.title for track in hits] == ["晴天"]  # the 12 s clip is filtered
            assert hits[0].id == "subsonic:1"
            radio = await source.random(5)
            assert [track.id for track in radio] == ["subsonic:3"]
            favorites = await source.favorites()
            assert [track.id for track in favorites] == ["subsonic:4"]
            assert await source.star("subsonic:3", True) is True
            assert await source.star("subsonic:3", False) is True
        finally:
            await source.close()

    asyncio.run(run())
    assert source.source_id == preset_id
    for method in ("ping", "search3", "getRandomSongs", "getStarred2", "star", "unstar"):
        assert method in subsonic.calls, subsonic.calls
    # Token+salt auth: the password never travels in a query string.
    assert all("p" not in query for query in subsonic.queries)
    assert all(query.get("u") == USER and query.get("t") and query.get("s")
               for query in subsonic.queries)


def test_wrong_password_raises_source_error(tmp_path: Path,
                                            subsonic: FakeSubsonic) -> None:
    secrets = tmp_path / "lan_media.json"
    secrets.write_text(json.dumps({
        "url": subsonic.url, "user": USER, "password": "wrong"}), encoding="utf-8")
    source = build_source("navidrome", secrets, tmp_path / "cache", cache_tracks=1,
                          policy=OutboundPolicy(enabled=False))

    async def run() -> None:
        try:
            with pytest.raises(Exception) as caught:
                await source.ping()
            assert "Wrong username or password" in str(caught.value)
        finally:
            await source.close()

    asyncio.run(run())


# ---------------------------------------------------------------- write-only API

def test_credentials_are_write_only_and_token_is_an_alternative(
        tmp_path: Path, subsonic: FakeSubsonic) -> None:
    client, cfg, _path = _api(tmp_path)
    response = client.put("/api/v1/music/credentials", json={
        "source": "gonic", "url": subsonic.url, "user": USER, "token": SECRET})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_credentials"] is True
    assert body["has_password"] is False
    assert SECRET not in response.text

    stored = json.loads((Path(cfg.system.data_dir) / "lan_media.json").read_text())
    assert stored == {"url": subsonic.url, "user": USER, "token": SECRET}
    assert oct((Path(cfg.system.data_dir) / "lan_media.json").stat().st_mode & 0o777) == "0o600"

    sources = client.get("/api/v1/music/sources").json()
    assert sources["current"] == "gonic"
    assert sources["source_known"] is True
    assert sources["credentials"]["ready"] is True
    assert sources["credentials"]["fields"]["token"] is True
    assert sources["credentials"]["fields"]["password"] is False
    for path in ("/api/v1/music", "/api/v1/music/sources", "/api/v1/panels",
                 "/api/v1/config/catalog"):
        assert SECRET not in client.get(path).text, path


def test_unknown_source_is_rejected_at_save(tmp_path: Path) -> None:
    client, cfg, _path = _api(tmp_path)
    response = client.put("/api/v1/music/credentials", json={"source": "jellyfin"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["source"]
    assert "navidrome" in detail[0]["msg"] and "jellyfin" in detail[0]["msg"]
    assert not (Path(cfg.system.data_dir) / "lan_media.json").exists()

    # The config-center path rejects it too (existing behaviour, kept here as the
    # single save contract).
    response = client.put("/api/v1/config/values", json={
        "values": {"music.source": "jellyfin"}})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["music.source"]


def test_panel_does_not_fake_enabled_for_an_unknown_source(tmp_path: Path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.enabled = True
    cfg.music.source = "jellyfin"  # hand-edited YAML analogue
    cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    client = TestClient(create_app(cfg))
    panels = {panel["id"]: panel for panel in
              client.get("/api/v1/panels").json()["panels"]}
    assert panels["music"]["enabled"] is False
    assert "jellyfin" in panels["music"]["enabled_reason"]
    assert "不受支持" in panels["music"]["enabled_reason"]
    status = client.get("/api/v1/music").json()
    assert status["enabled"] is False
    assert status["source_known"] is False
    # The page still opens, and the registry explains the invalid value.
    sources = client.get("/api/v1/music/sources").json()
    assert sources["source_known"] is False
    assert sources["current_preset"] is None
    assert sources["current"] == "jellyfin"
    assert {preset["id"] for preset in sources["sources"]} == set(preset_ids())


# ------------------------------------------------------------------ test endpoint

def test_test_endpoint_does_a_real_ping_for_each_named_preset(
        tmp_path: Path, subsonic: FakeSubsonic) -> None:
    client, _cfg, _path = _api(tmp_path)
    for preset_id in ("navidrome", "airsonic"):
        body = client.post("/api/v1/music/test", json={
            "source": preset_id, "url": subsonic.url,
            "user": USER, "password": SECRET}).json()
        assert body["ok"] is True, body
        assert body["source"] == preset_id
        assert isinstance(body["latency_ms"], int)
        assert SECRET not in json.dumps(body)
    assert subsonic.calls.count("ping") >= 2


def test_test_endpoint_redacts_failures_and_rejects_unknown_sources(
        tmp_path: Path, subsonic: FakeSubsonic) -> None:
    client, _cfg, _path = _api(tmp_path)
    body = client.post("/api/v1/music/test", json={
        "url": subsonic.url, "user": USER, "password": "wrong-secret"}).json()
    assert body["ok"] is False
    assert "wrong-secret" not in json.dumps(body)

    missing = client.post("/api/v1/music/test", json={}).json()
    assert missing["ok"] is False and "凭据" in missing["error"]

    response = client.post("/api/v1/music/test", json={"source": "jellyfin"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["source"]


def test_source_switch_via_credentials_applies_without_restart(
        tmp_path: Path, subsonic: FakeSubsonic) -> None:
    """A saved source is validated, persisted, and the music service is rebuilt."""
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.enabled = True
    cfg.music.source = "navidrome"
    cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    cfg.music.cache_dir = str(tmp_path / "cache")
    cfg.security.outbound.allow_loopback = True
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    with TestClient(create_app(cfg, config_path=path)) as client:
        assert client.app.state.music is not None
        assert client.app.state.music.source.source_id == "navidrome"
        response = client.put("/api/v1/music/credentials", json={
            "source": "airsonic", "url": subsonic.url, "user": USER, "password": SECRET})
        assert response.status_code == 200, response.text
        assert load_config(path).music.source == "airsonic"
        assert client.app.state.music is not None
        assert client.app.state.music.source.source_id == "airsonic"
        assert client.get("/api/v1/music/sources").json()["running_source"] == "airsonic"


# -------------------------------------------------------------------- migration

def test_legacy_inline_credentials_migrate_to_the_0600_file(
        tmp_path: Path, subsonic: FakeSubsonic) -> None:
    data_dir = tmp_path / "data"
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({
        "version": 2,
        "system": {"data_dir": str(data_dir)},
        "music": {
            "enabled": True,
            "source": "navidrome",
            "url": subsonic.url,
            "user": USER,
            "password": SECRET,
        },
    }), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.music.password == SECRET  # read once, for migration only

    client = TestClient(create_app(cfg, config_path=path))
    credentials = data_dir / "lan_media.json"
    assert credentials.is_file()
    assert oct(credentials.stat().st_mode & 0o777) == "0o600"
    assert json.loads(credentials.read_text()) == {
        "url": subsonic.url, "user": USER, "password": SECRET}
    assert cfg.music.password == "" and cfg.music.url == ""
    assert cfg.music.secrets_path == str(credentials)
    # The startup migration rewrote the config without the plaintext.
    assert SECRET not in path.read_text(encoding="utf-8")

    status = client.get("/api/v1/music").json()
    assert status["enabled"] is True
    assert status["credentials_ready"] is True
    assert status["source"] == "navidrome"
    assert SECRET not in json.dumps(status)
