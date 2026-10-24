"""The console must not be a LAN-open door (security audit 2026-10-23).

These pin the three fixes: a non-loopback bind with no token refuses to start, a
token is compared in constant time with a failure budget, and debug playback can no
longer name any file on the host.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from rui.__main__ import cmd_setup, cmd_token, insecure_bind
from rui.config import Config, load_config
from rui.web.api.app import create_app


def test_an_exposed_console_without_a_token_refuses_to_start() -> None:
    assert insecure_bind("0.0.0.0", "", False)
    assert insecure_bind("192.0.2.10", "", False)
    assert insecure_bind("::", "", False)
    # loopback, a token, or an explicit development opt-in are all fine
    assert insecure_bind("127.0.0.1", "", False) == ""
    assert insecure_bind("localhost", "", False) == ""
    assert insecure_bind("0.0.0.0", "tok", False) == ""
    assert insecure_bind("0.0.0.0", "", True) == ""


def test_setup_generates_a_console_token(tmp_path, capsys) -> None:
    path = tmp_path / "config.yaml"
    rc = cmd_setup(SimpleNamespace(config=str(path), force=True, wake_word=None))
    assert rc == 0
    token = load_config(path).web.auth_token
    assert len(token) >= 24
    # printed once so a headless install can hand it to the phone
    assert token in capsys.readouterr().out


def test_the_token_command_prints_and_rotates(tmp_path, capsys) -> None:
    path = tmp_path / "config.yaml"
    cmd_setup(SimpleNamespace(config=str(path), force=True, wake_word=None))
    first = load_config(path).web.auth_token
    assert cmd_token(SimpleNamespace(config=str(path), rotate=False)) == 0
    assert capsys.readouterr().out.strip().endswith(first)
    assert cmd_token(SimpleNamespace(config=str(path), rotate=True)) == 0
    second = load_config(path).web.auth_token
    assert second and second != first


def _app(**web) -> tuple[TestClient, Config]:
    cfg = Config(**web)
    return TestClient(create_app(cfg)), cfg


def test_a_set_token_gates_every_api_route() -> None:
    c, _ = _app(web={"auth_token": "s3cret"})
    assert c.get("/health").status_code == 200           # the probe stays open
    assert c.get("/api/v1/config").status_code == 401
    assert c.get("/api/v1/config", headers={"X-Auth-Token": "nope"}).status_code == 401
    assert c.get("/api/v1/config", headers={"X-Auth-Token": "s3cret"}).status_code == 200


def test_bad_tokens_hit_a_rate_limit() -> None:
    c, _ = _app(web={"auth_token": "s3cret"})
    codes = [c.get("/api/v1/config", headers={"X-Auth-Token": "nope"}).status_code
             for _ in range(12)]
    assert 401 in codes
    assert 429 in codes


def test_debug_playback_is_confined_to_the_media_roots(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    inside = data / "test.wav"
    inside.write_bytes(b"RIFF")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFF")

    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def send(self, message: object) -> None:
            self.sent.append(message)

    cfg = Config()
    cfg.system.data_dir = str(data)
    app = create_app(cfg)
    client = FakeClient()
    app.state.audio = SimpleNamespace(client=client)
    c = TestClient(app)

    assert c.post("/api/v1/debug/play", json={"file": str(inside)}).status_code == 200
    assert client.sent
    body = c.post("/api/v1/debug/play", json={"file": str(outside)})
    # a generic 404: the probe must not say whether a path exists
    assert body.status_code == 404
    assert str(outside) not in body.text
    assert c.post("/api/v1/debug/play", json={"file": "/etc/passwd"}).status_code == 404
