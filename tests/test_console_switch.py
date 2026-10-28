"""The console entry point after 0.8.0 removed the legacy shell.

`/` is a 302 to `/v2/` (query preserved) and nothing else: the old panel SPA, its
`/legacy` mount and the 15 redirects are gone, so every old console path is a plain
404. `web.console` stays in the schema for config compatibility, but its value is
ignored - keeping the enum is not the same as keeping the old shell. `/health.console`
still reports the configured value, so an operator can see what the config says.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from ruamel.yaml import YAML

from rui.config import Config, load_config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
REPO_ROOT = Path(__file__).resolve().parents[1]

# Every path the old console used to answer (eleven /panel/<id> + four hand-written
# pages) plus its own mount. All of them are 404 now.
OLD_CONSOLE_PATHS = (
    "/legacy",
    "/legacy/",
    "/legacy/panel/music",
    "/legacy/setup",
    "/legacy/api/v1/panels",
    "/panel/music",
    "/panel/overview",
    "/panel/credentials",
    "/setup",
    "/secrets",
    "/diagnostics",
    "/channels",
)


def _dist(tmp_path: Path) -> Path:
    """The subset of `web/dist` the server serves: v2 shell, assets and root static."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "fonts").mkdir()
    (dist / "assets" / "v2-test.js").write_text("// v2 bundle", encoding="utf-8")
    (dist / "v2.html").write_text("<html>v2-shell-marker</html>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (dist / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (dist / "logo-dark.svg").write_text("<svg/>", encoding="utf-8")
    (dist / "fonts" / "manrope-latin.woff2").write_bytes(b"fake-font")
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


@pytest.mark.parametrize("console", [None, "v2", "legacy"])
def test_root_always_hands_over_to_v2(tmp_path, monkeypatch, console) -> None:
    """`console: legacy` no longer renders a shell: the value is accepted and ignored."""
    client = TestClient(_app(tmp_path, monkeypatch, console=console))

    moved = client.get("/", follow_redirects=False)
    assert moved.status_code == 302
    assert moved.headers["location"] == "/v2/"

    kept = client.get("/?room=living&tab=config", follow_redirects=False)
    assert kept.status_code == 302
    assert kept.headers["location"] == "/v2/?room=living&tab=config"

    landed = client.get("/")
    assert landed.status_code == 200
    assert "v2-shell-marker" in landed.text

    v2 = client.get("/v2")
    assert v2.status_code == 200
    assert "v2-shell-marker" in v2.text

    assert client.get("/health").json()["console"] == (console or "v2")


def test_every_old_console_path_is_gone(tmp_path, monkeypatch) -> None:
    client = TestClient(_app(tmp_path, monkeypatch))
    for path in OLD_CONSOLE_PATHS:
        response = client.get(path)
        assert response.status_code == 404, path
        assert not response.headers["content-type"].startswith("text/html"), path


def test_the_root_static_assets_v2_html_needs_are_served(tmp_path, monkeypatch) -> None:
    """`v2.html` loads `/assets/*`, `/favicon.svg`, `/fonts/*` and `/logo*.svg`.

    These used to come from the old SPA catch-all; with it gone they are explicit
    mounts/routes, and an unknown path must stay a 404 instead of reviving the shell.
    """
    client = TestClient(_app(tmp_path, monkeypatch))

    for path in (
        "/favicon.svg",
        "/logo.svg",
        "/logo-dark.svg",
        "/fonts/manrope-latin.woff2",
        "/assets/v2-test.js",
        "/v2/assets/v2-test.js",
    ):
        assert client.get(path).status_code == 200, path

    assert client.get("/no-such-file.css").status_code == 404
    assert client.get("/panel/music").status_code == 404


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
        data = YAML(typ="safe").load((REPO_ROOT / name).read_text(encoding="utf-8"))
        assert data["web"]["console"] == "v2", name


def test_v2_sources_have_no_links_to_the_removed_console() -> None:
    """The old mount is a 404, so a v2 page linking to it would be a dead end."""
    v2 = REPO_ROOT / "web" / "src" / "v2"
    offenders = [
        str(path.relative_to(v2))
        for path in v2.rglob("*")
        if path.suffix in {".ts", ".vue"} and "/legacy" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], offenders

    for rel in ("views/RunView.vue", "views/DevicesView.vue"):
        text = (v2 / rel).read_text(encoding="utf-8")
        assert 'to="/setup"' in text, rel
