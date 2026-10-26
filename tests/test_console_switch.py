"""M10 console switch: `web.console` picks what `/` opens, nothing moves by default.

`legacy` (the default) keeps the 0.6 behaviour byte for byte: `/` serves the old SPA.
`v2` turns `/` into a 302 to `/v2/` (query preserved) and keeps the old shell at
`/legacy`, history deep links included. `/health.console` reports the live choice so
an operator never has to infer it from a bookmark. A config written before this field
existed carries no `console` key and must still load as legacy.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rui.config import Config, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN = "test-token-fake"


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>old-shell-marker</html>", encoding="utf-8")
    (dist / "v2.html").write_text("<html>v2-shell-marker</html>", encoding="utf-8")
    return dist


def _app(tmp_path: Path, monkeypatch, *, console: str | None = None):
    monkeypatch.setattr(app_module, "WEB_DIST", _dist(tmp_path))
    web = {"auth_token": TOKEN}
    if console is not None:
        web["console"] = console
    config = Config(web=web)
    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    return create_app(config, config_path=path)


def test_legacy_is_the_default_entry_point(tmp_path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))

    root = client.get("/")
    assert root.status_code == 200
    assert "old-shell-marker" in root.text

    # The old shell is reachable under its own mount in both modes; a history link
    # must answer index.html, not a 404 or the v2 shell.
    for path in ("/legacy", "/legacy/", "/legacy/panel/music", "/legacy/setup"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "old-shell-marker" in response.text, path

    v2 = client.get("/v2")
    assert v2.status_code == 200
    assert "v2-shell-marker" in v2.text

    assert client.get("/health").json()["console"] == "legacy"
    assert client.get("/legacy/api/v1/panels").status_code == 404


def test_v2_mode_redirects_the_root_and_keeps_the_query(tmp_path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch, console="v2"))

    moved = client.get("/", follow_redirects=False)
    assert moved.status_code == 302
    assert moved.headers["location"] == "/v2/"

    kept = client.get("/?room=living&tab=config", follow_redirects=False)
    assert kept.status_code == 302
    assert kept.headers["location"] == "/v2/?room=living&tab=config"

    # Following the hop lands on the real v2 shell, so the redirect target exists.
    landed = client.get("/")
    assert landed.status_code == 200
    assert "v2-shell-marker" in landed.text

    for path in ("/v2", "/v2/house"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "v2-shell-marker" in response.text, path

    legacy = client.get("/legacy/panel/music")
    assert legacy.status_code == 200
    assert "old-shell-marker" in legacy.text

    assert client.get("/health").json()["console"] == "v2"


def test_a_pre_m10_config_without_the_key_loads_as_legacy(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "version: 3\nweb:\n  auth_token: test-token-fake\ndevices: []\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.web.console == "legacy"

    # Additive on read, strict on write: only the two documented values are accepted.
    assert Config(web={"console": "v2"}).web.console == "v2"
    with pytest.raises(ValidationError):
        Config(web={"console": "next"})

    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    monkeypatch.setattr(app_module, "WEB_DIST", _dist(tmp_path))
    client = TestClient(create_app(config, config_path=path))

    root = client.get("/")
    assert root.status_code == 200
    assert "old-shell-marker" in root.text
    assert client.get("/health").json()["console"] == "legacy"


def test_v2_legacy_links_point_at_the_legacy_mount() -> None:
    """`/` in v2 mode is a redirect, so a v2 page linking to it would loop."""
    v2 = Path(__file__).resolve().parents[1] / "web" / "src" / "v2"
    shell = (v2 / "App.vue").read_text(encoding="utf-8")
    assert 'class="v2-legacy" href="/legacy"' in shell
    for rel in ("views/RunView.vue", "views/DevicesView.vue"):
        text = (v2 / rel).read_text(encoding="utf-8")
        assert 'href="/legacy"' in text, rel
