"""P5: HA over MCP from the console - write endpoint/token, test, reconnect live.

The bridge is the one place where a wrong URL used to mean "restart and hope": the
console now reconnects at runtime, drops the old tools by name and re-registers the
new ones. These tests pin the config/token writes and use a fake bridge for the
reconnect path (no HA server in CI).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from house_ear.config import Config, load_config, save_config
from house_ear.web.api import app as app_module
from house_ear.web.api.app import create_app


class FakeBridge:
    """Stands in for HaBridge: connect succeeds only for the good URL."""

    instances: list[FakeBridge] = []

    def __init__(self, cfg, data_dir) -> None:
        self.cfg = cfg
        self._data_dir = data_dir
        self.stopped = False
        self.client = type("C", (), {"last_error": ""})()
        FakeBridge.instances.append(self)

    async def start(self) -> bool:
        return self.cfg.base_url.startswith("http://ha")

    async def stop(self) -> None:
        self.stopped = True

    def tool_specs(self) -> list:
        return []

    @property
    def status(self) -> dict:
        ok = self.cfg.base_url.startswith("http://ha")
        return {"connected": ok, "latency_ms": 12 if ok else None,
                "tools": 6 if ok else 0, "entities": 120 if ok else 0}


@pytest.fixture()
def console(tmp_path, monkeypatch):
    for name in ("HOUSE_EAR_HA_TOKEN",):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    monkeypatch.setattr(app_module, "HaBridge", FakeBridge)
    FakeBridge.instances.clear()
    return TestClient(create_app(cfg, config_path=path)), path, tmp_path


def test_state_reports_an_unconfigured_ha(console) -> None:
    client, _path, _tmp = console
    body = client.get("/api/v1/mcp").json()
    assert body["ha"]["wired"] is False
    assert body["ha"]["connected"] is False
    assert body["ha"]["has_token"] is False
    # Generic MCP is wired now (task-16); an empty config simply has no servers.
    assert body["generic_wired"] is True
    assert body["stdio_enabled"] is False
    assert body["generic"] == {"enabled": 0, "connected": 0, "tools": 0,
                               "stdio_enabled": False}
    assert body["servers"] == []


def test_save_writes_url_and_token_then_reconnects(console) -> None:
    client, path, tmp = console
    response = client.put("/api/v1/mcp", json={
        "ha": {"base_url": "http://ha.local:8123/", "token": "long-lived-token"},
    })
    assert response.status_code == 200, response.text
    saved = load_config(path).tools.ha.base_url
    assert saved == "http://ha.local:8123"
    body = response.json()
    assert body["ha"]["connected"] is True
    assert body["ha"]["tools"] == 6
    assert body["ha"]["has_token"] is True
    assert "long-lived-token" not in response.text
    assert (tmp / "data" / "secrets.yaml").exists()
    # The old bridge (if any) was stopped, and a new one applied the config.
    assert FakeBridge.instances[-1].cfg.base_url == "http://ha.local:8123"


def test_rejects_urls_that_are_not_http(console) -> None:
    client, _path, _tmp = console
    for payload in (
        {"ha": {"base_url": "ha.local:8123"}},
        {"ha": "http://ha.local"},
        {"ha": {"base_url": "http://ha.local"}, "servers": "nope"},
    ):
        assert client.put("/api/v1/mcp", json=payload).status_code == 422


def test_clearing_the_url_disables_the_tools(console) -> None:
    client, path, _tmp = console
    client.put("/api/v1/mcp", json={"ha": {"base_url": "http://ha.local:8123"}})
    body = client.put("/api/v1/mcp", json={"ha": {"base_url": ""}}).json()
    assert body["ha"]["wired"] is False
    assert load_config(path).tools.ha.base_url == ""


def test_the_test_endpoint_connects_without_saving(console) -> None:
    client, path, _tmp = console
    body = client.post("/api/v1/mcp/test",
                       json={"base_url": "http://ha.local:8123"}).json()
    assert body["ok"] is True
    assert body["tools"] == 6
    assert body["entities"] == 120
    assert load_config(path).tools.ha.base_url == ""        # nothing saved
    assert FakeBridge.instances[-1].stopped is True         # throwaway bridge closed
    bad = client.post("/api/v1/mcp/test",
                      json={"base_url": "http://elsewhere:1"}).json()
    assert bad["ok"] is False


def test_save_needs_a_config_file_on_disk(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "HaBridge", FakeBridge)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    assert client.put("/api/v1/mcp", json={"ha": {"base_url": "http://ha"}}).status_code == 409
