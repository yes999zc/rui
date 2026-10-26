"""0.7.8 D: every old console link answers 302 to its v2 page (v2 mode).

The list is the one measured in `docs/audit/console-functional.md` §8.2: the eleven
v1 `/panel/<id>` pages plus `/setup`, `/secrets`, `/diagnostics`, `/channels`.

Expectations are written out literally, not derived from `legacy_links`, so moving
a target is a visible test change. `providers`, `system` and `channels` have no v2
page yet (audit §8.3 rows 6-7/19/27): they land on `/v2/?from=<old>` instead of a
wrong page, and the landing really exists (the follow-up hop answers 200).

In `console: legacy` mode none of this fires — the same 15 paths keep serving the
old shell as 0.7.7 did, which is the whole rollback story.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, save_config
from rui.web.api import app as app_module
from rui.web.api.app import create_app
from rui.web.api.legacy_links import LEGACY_PATHS

TOKEN = "test-token-fake"

# old path -> exact `Location` header in v2 mode.
EXPECTED = {
    # The eleven v1 panel ids.
    "/panel/overview": "/v2/run",
    "/panel/devices": "/v2/devices",
    "/panel/turns": "/v2/conversation",
    "/panel/voice-assistant": "/v2/voice-assistant",
    "/panel/providers": "/v2/?from=/panel/providers",
    "/panel/integrations": "/v2/tools",
    "/panel/system": "/v2/?from=/panel/system",
    "/panel/credentials": "/v2/credentials",
    "/panel/logs": "/v2/logs",
    "/panel/memory": "/v2/memory",
    "/panel/music": "/v2/music",
    # The hand-written v1 routes (`/channels` has no v2 page).
    "/setup": "/v2/setup",
    "/secrets": "/v2/credentials",
    "/diagnostics": "/v2/health",
    "/channels": "/v2/?from=/channels",
}


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>old-shell-marker</html>", encoding="utf-8")
    (dist / "v2.html").write_text("<html>v2-shell-marker</html>", encoding="utf-8")
    return dist


def _app(tmp_path: Path, monkeypatch, *, console: str | None = None) -> TestClient:
    monkeypatch.setattr(app_module, "WEB_DIST", _dist(tmp_path))
    web = {"auth_token": TOKEN}
    if console is not None:
        web["console"] = console
    config = Config(web=web)
    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    return TestClient(create_app(config, config_path=path))


def test_the_audit_list_and_the_table_are_the_same_15() -> None:
    assert len(EXPECTED) == 15
    assert set(EXPECTED) == set(LEGACY_PATHS)


@pytest.mark.parametrize(("old", "target"), EXPECTED.items())
def test_old_link_redirects_to_v2(tmp_path, monkeypatch, old: str, target: str) -> None:
    client = _app(tmp_path, monkeypatch)

    response = client.get(old, follow_redirects=False)
    assert response.status_code == 302, old
    assert response.headers["location"] == target, old

    # "No 404, no old shell": the hop really lands on the v2 shell.
    landed = client.get(old)
    assert landed.status_code == 200, old
    assert "v2-shell-marker" in landed.text, old


@pytest.mark.parametrize(
    "old",
    [p for p in EXPECTED if p.startswith("/panel/") and "from=" not in EXPECTED[p]],
)
def test_a_panel_subpath_resolves_by_panel_id(tmp_path, monkeypatch, old: str) -> None:
    """`/panel/devices/manage` is a v1 tab deep link; v2 has no tabs, so drop the tab."""
    client = _app(tmp_path, monkeypatch)
    response = client.get(f"{old}/manage", follow_redirects=False)
    assert response.status_code == 302, old
    assert response.headers["location"] == EXPECTED[old], old


def test_an_unknown_panel_takes_the_from_landing(tmp_path, monkeypatch) -> None:
    """A bookmarked panel v2 never had still must not land on the old shell."""
    client = _app(tmp_path, monkeypatch)
    response = client.get("/panel/removed-in-phase-4", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/v2/?from=/panel/removed-in-phase-4"

    # A sub-path of a panel with no v2 page keeps the whole old path in `from`.
    gap = client.get("/panel/system/advanced", follow_redirects=False)
    assert gap.headers["location"] == "/v2/?from=/panel/system/advanced"


def test_the_query_string_survives_the_hop(tmp_path, monkeypatch) -> None:
    client = _app(tmp_path, monkeypatch)

    mapped = client.get("/panel/logs?level=warn", follow_redirects=False)
    assert mapped.headers["location"] == "/v2/logs?level=warn"

    landing = client.get("/panel/providers?tab=voice", follow_redirects=False)
    assert landing.headers["location"] == "/v2/?from=/panel/providers&tab=voice"


def test_legacy_mode_keeps_all_15_on_the_old_shell(tmp_path, monkeypatch) -> None:
    """The rollback must be boring: no redirects, the 0.7.7 answers stay 200."""
    client = _app(tmp_path, monkeypatch, console="legacy")
    for old in EXPECTED:
        response = client.get(old, follow_redirects=False)
        assert response.status_code == 200, old
        assert "old-shell-marker" in response.text, old


def test_the_legacy_mount_never_redirects(tmp_path, monkeypatch) -> None:
    """`/legacy/...` is the old shell's own home; it always answers 200 in v2 mode."""
    client = _app(tmp_path, monkeypatch)
    for path in ("/legacy", "/legacy/panel/logs", "/legacy/panel/system", "/legacy/setup"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path
        assert "old-shell-marker" in response.text, path
