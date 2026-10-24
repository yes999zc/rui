from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from rui.config import Config
from rui.session.turn import TurnResult
from rui.trace import TurnTrace
from rui.web.api.app import create_app


class FakeRunner:
    def __init__(self) -> None:
        self.busy = False
        self.history: list[dict] = []
        self.runs: list[str] = []
        self.touch_idle: list[bool] = []

    async def run(self, _pcm, speak, _trace: TurnTrace, *, transcript: str = "",
                  **_kwargs):  # noqa: ANN001, ANN003
        self.runs.append(transcript)
        self.touch_idle.append(bool(_kwargs.get("touch_idle", True)))
        await speak(b"\x00\x00" * 8, segment_start=True)
        self.history.append({"role": "user", "content": transcript})
        self.history.append({"role": "assistant", "content": "你好"})
        return TurnResult(transcript=transcript, reply="你好", audio_bytes=16)

    async def transcribe(self, _pcm: bytes, _trace: TurnTrace):
        return "这是语音", None


def make_client() -> TestClient:
    return TestClient(create_app(Config(web={"auth_token": "tok"})))


def test_chat_requires_auth() -> None:
    c = make_client()
    assert c.post("/api/v1/chat", json={"text": "hi"}).status_code == 401


def test_chat_runs_a_text_turn_and_returns_audio() -> None:
    c = make_client()
    c.app.state.runner = FakeRunner()
    body = c.post("/api/v1/chat", headers={"X-Auth-Token": "tok"},
                  json={"channel": "telegram", "peer_id": "42", "text": "你好",
                        "want_audio": True}).json()
    assert body["reply"] == "你好"
    assert body["session_id"] == "telegram:42"
    assert c.app.state.runner.touch_idle == [False]  # channel turns never arm the speaker goodbye
    assert body["audio_bytes"] == 16
    assert base64.b64decode(body["audio_b64"]) == b"\x00\x00" * 8


def test_chat_sessions_do_not_share_history() -> None:
    c = make_client()
    runner = FakeRunner()
    c.app.state.runner = runner
    headers = {"X-Auth-Token": "tok"}
    c.post("/api/v1/chat", headers=headers,
           json={"channel": "telegram", "peer_id": "a", "text": "one"})
    c.post("/api/v1/chat", headers=headers,
           json={"channel": "telegram", "peer_id": "b", "text": "two"})
    assert runner.history == []  # the runner's own (speaker) history was restored
    assert [m["content"] for m in c.app.state.chat_histories["telegram:a"]] == ["one", "你好"]
    assert [m["content"] for m in c.app.state.chat_histories["telegram:b"]] == ["two", "你好"]


def test_chat_reset_drops_session_history() -> None:
    c = make_client()
    c.app.state.runner = FakeRunner()
    headers = {"X-Auth-Token": "tok"}
    c.post("/api/v1/chat", headers=headers,
           json={"channel": "weixin", "peer_id": "u", "text": "one"})
    c.post("/api/v1/chat", headers=headers,
           json={"channel": "weixin", "peer_id": "u", "text": "two", "reset": True})
    assert [m["content"] for m in c.app.state.chat_histories["weixin:u"]] == ["two", "你好"]


def test_asr_endpoint_decodes_pcm() -> None:
    c = make_client()
    c.app.state.runner = FakeRunner()
    body = c.post("/api/v1/asr", headers={"X-Auth-Token": "tok"},
                  json={"audio_b64": base64.b64encode(b"\x00\x00" * 160).decode()}).json()
    assert body["text"] == "这是语音"


def test_asr_rejects_bad_base64() -> None:
    c = make_client()
    c.app.state.runner = FakeRunner()
    response = c.post("/api/v1/asr", headers={"X-Auth-Token": "tok"},
                      json={"audio_b64": "!!!!"})
    assert response.status_code == 422


def test_channels_config_read_write(tmp_path) -> None:  # noqa: ANN001
    c = TestClient(create_app(Config(web={"auth_token": "tok"},
                                     system={"data_dir": str(tmp_path)})))
    headers = {"X-Auth-Token": "tok"}
    body = c.get("/api/v1/channels", headers=headers).json()
    assert body["telegram"]["enabled"] is False
    assert body["telegram"]["token_configured"] is False
    updated = c.put("/api/v1/channels", headers=headers, json={
        "telegram": {"enabled": True, "allowed_chat_ids": ["42"], "reply_mode": "text"},
        "weixin": {"enabled": False, "allowed_user_ids": ["u"], "reply_mode": "both"},
    }).json()
    assert updated["telegram"]["enabled"] is True
    assert updated["telegram"]["allowed_chat_ids"] == ["42"]
    assert updated["weixin"]["reply_mode"] == "both"
    path = tmp_path / "channels.json"
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_gateway_start_refuses_without_enabled_channels(tmp_path) -> None:  # noqa: ANN001
    c = TestClient(create_app(Config(web={"auth_token": "tok"},
                                     system={"data_dir": str(tmp_path)})))
    headers = {"X-Auth-Token": "tok"}
    body = c.post("/api/v1/channels/gateway/start", headers=headers).json()
    assert body["running"] is False
    assert "no channel" in body["last_error"]
    assert c.get("/api/v1/channels", headers=headers).json()["gateway"]["status"]["managed"] is True
