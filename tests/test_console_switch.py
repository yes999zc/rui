"""M10 console switch: `web.console` picks what `/` opens — v2 by default since 0.7.8.

`v2` (the default) turns `/` into a 302 to `/v2/` (query preserved) and keeps the
old shell, marked deprecated, at `/legacy`, history deep links included. `legacy`
is the one-release rollback: `/` serves the old SPA byte for byte, and the 15 old
console links keep answering it (the 302 matrix lives in `test_legacy_links.py`).
`/health.console` reports the live choice so an operator never has to infer it from
a bookmark. A config written before this field existed carries no `console` key and
must load as v2.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rui.config import Config, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_v2_is_the_default_entry_point(tmp_path, monkeypatch) -> None:
    """A fresh install lands on the v2 console; the old shell is the /legacy mount."""
    client = TestClient(_app(tmp_path, monkeypatch))

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

    # The old shell keeps its own mount in both modes; a history link must answer
    # index.html, not a 404 or the v2 shell.
    for path in ("/legacy", "/legacy/", "/legacy/panel/music", "/legacy/setup"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "old-shell-marker" in response.text, path

    v2 = client.get("/v2")
    assert v2.status_code == 200
    assert "v2-shell-marker" in v2.text

    assert client.get("/health").json()["console"] == "v2"
    assert client.get("/legacy/api/v1/panels").status_code == 404


def test_legacy_mode_is_the_one_release_rollback(tmp_path, monkeypatch) -> None:
    """`console: legacy` still serves the old shell at `/` and reports it as legacy."""
    client = TestClient(_app(tmp_path, monkeypatch, console="legacy"))

    root = client.get("/")
    assert root.status_code == 200
    assert "old-shell-marker" in root.text

    for path in ("/legacy/panel/music", "/panel/music", "/setup"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "old-shell-marker" in response.text, path

    v2 = client.get("/v2")
    assert v2.status_code == 200
    assert "v2-shell-marker" in v2.text

    assert client.get("/health").json()["console"] == "legacy"


def test_a_pre_078_config_without_the_key_loads_as_v2(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "version: 3\nweb:\n  auth_token: test-token-fake\ndevices: []\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.web.console == "v2"

    # Additive on read, strict on write: only the two documented values are accepted.
    assert Config(web={"console": "legacy"}).web.console == "legacy"
    with pytest.raises(ValidationError):
        Config(web={"console": "next"})

    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    monkeypatch.setattr(app_module, "WEB_DIST", _dist(tmp_path))
    client = TestClient(create_app(config, config_path=path))

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["location"] == "/v2/"
    assert client.get("/health").json()["console"] == "v2"


def test_the_shipped_config_examples_name_v2() -> None:
    """`config.example.yaml` and the setup template must not lag behind the schema."""
    for name in ("config.example.yaml", "src/rui/config/template.yaml"):
        data = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
        assert data["web"]["console"] == "v2", name


def test_v2_legacy_links_point_at_the_legacy_mount() -> None:
    """`/` in v2 mode is a redirect, so a v2 page linking to it would loop."""
    v2 = REPO_ROOT / "web" / "src" / "v2"
    shell = (v2 / "App.vue").read_text(encoding="utf-8")
    assert 'class="v2-legacy" href="/legacy"' in shell
    for rel in ("views/RunView.vue", "views/DevicesView.vue"):
        text = (v2 / rel).read_text(encoding="utf-8")
        assert 'href="/legacy"' in text, rel
