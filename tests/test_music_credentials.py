"""Music credentials: write-only file + panel contract + status boolean.

The music page gets its first interactive block (`credentials`): url/user/password are
typed in the console, saved to `data_dir/lan_media.json` (0600, atomic) and tested with a
real Subsonic round trip. No endpoint may ever return a stored value; the status and the
panel only know whether the file exists.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from house_ear.config import Config, save_config
from house_ear.web.api.app import create_app


def _client(tmp_path: Path, *, enabled: bool = False) -> tuple[TestClient, Config, Path]:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.source = "navidrome"
    cfg.music.cache_dir = str(tmp_path / "cache")
    if enabled:
        cfg.music.enabled = True
        cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), cfg, path


def _credentials_file(cfg: Config) -> Path:
    if cfg.music.secrets_path:
        return Path(cfg.music.secrets_path)
    return Path(cfg.system.data_dir) / "lan_media.json"


def test_music_panel_has_the_credentials_block(tmp_path) -> None:
    client, _cfg, _path = _client(tmp_path)
    panels = {panel["id"]: panel for panel in
              client.get("/api/v1/panels").json()["panels"]}
    music = panels["music"]
    blocks = music["blocks"]
    assert {"stats", "now", "actions", "list", "credentials"} <= {
        block["type"] for block in blocks}
    credentials = next(block for block in blocks if block["type"] == "credentials")
    assert credentials["status_key"] == "credentials_present"
    assert credentials["title"]["zh"] and credentials["title"]["en"]
    fields = {field["key"]: field for field in credentials["fields"]}
    assert set(fields) == {"url", "user", "password"}
    assert fields["url"]["kind"] == "text"
    assert fields["user"]["kind"] == "text"
    assert fields["password"]["kind"] == "password"
    for field in credentials["fields"]:
        for key in ("zh", "en"):
            assert field["label"][key] and field["hint"][key], field["key"]
    assert credentials["save"] == {"href": "/api/v1/music/credentials",
                                   "method": "PUT"}
    assert credentials["test"] == {"href": "/api/v1/music/test", "method": "POST"}
    # The block belongs to the music panel and does not replace the transport blocks.
    assert music["source"] == "/api/v1/music"


def test_music_status_exposes_only_a_boolean(tmp_path) -> None:
    client, cfg, _path = _client(tmp_path)
    body = client.get("/api/v1/music").json()
    assert body["enabled"] is False
    assert body["credentials_present"] is False
    assert isinstance(body["credentials_present"], bool)

    secret = "s3cret-music-pass"
    response = client.put("/api/v1/music/credentials", json={
        "url": "http://192.0.2.50:4533", "user": "bakey", "password": secret})
    assert response.status_code == 200, response.text
    stored = _credentials_file(cfg)
    assert stored.is_file()
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    assert json.loads(stored.read_text(encoding="utf-8"))["password"] == secret

    body = client.get("/api/v1/music").json()
    assert body["credentials_present"] is True
    assert isinstance(body["credentials_present"], bool)
    # No value, on any read path.
    for path in ("/api/v1/music", "/api/v1/panels", "/api/v1/config/catalog"):
        assert secret not in client.get(path).text, path
    assert "password" not in json.dumps(body)


def test_music_credentials_save_and_empty_delete(tmp_path) -> None:
    client, cfg, _path = _client(tmp_path)
    stored = _credentials_file(cfg)
    client.put("/api/v1/music/credentials", json={
        "url": "http://192.0.2.50:4533", "user": "bakey", "password": "pw"})
    assert stored.is_file()

    # An empty value deletes that field only.
    body = client.put("/api/v1/music/credentials", json={"password": ""}).json()
    assert body["has_password"] is False and body["has_credentials"] is False
    data = json.loads(stored.read_text(encoding="utf-8"))
    assert "password" not in data and data["url"].startswith("http://")
    # `credentials_present` is "the file exists", so a partial file still reads true;
    # the form's `test` action is what reports it is not usable yet.
    assert client.get("/api/v1/music").json()["credentials_present"] is True

    body = client.put("/api/v1/music/credentials", json={
        "url": "", "user": ""}).json()
    assert body["has_credentials"] is False
    assert not stored.is_file()
    assert client.get("/api/v1/music").json()["credentials_present"] is False

    # Invalid URL is still rejected; nothing is written.
    response = client.put("/api/v1/music/credentials", json={"url": "192.0.2.50:4533"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["url"]


def test_enabled_music_keeps_the_status_and_never_echoes_values(tmp_path) -> None:
    secret = "s3cret-enabled-pass"
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.music.enabled = True
    cfg.music.secrets_path = str(tmp_path / "data" / "lan_media.json")
    cfg.music.cache_dir = str(tmp_path / "cache")
    Path(cfg.music.secrets_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.music.secrets_path).write_text(json.dumps({
        "url": "http://192.0.2.50:4533", "user": "bakey", "password": secret}),
        encoding="utf-8")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    with TestClient(create_app(cfg, config_path=path)) as client:
        body = client.get("/api/v1/music").json()
        assert body["enabled"] is True
        assert body["credentials_present"] is True
        assert secret not in json.dumps(body)
        panels = {panel["id"]: panel for panel in
                  client.get("/api/v1/panels").json()["panels"]}
        assert panels["music"]["enabled"] is True
        assert any(block["type"] == "credentials"
                   for block in panels["music"]["blocks"])
