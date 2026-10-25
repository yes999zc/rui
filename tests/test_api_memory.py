"""The v1 memory API validates instead of raising or silently cutting.

M7/M8 found two real-process bugs: an empty/blank `add` reached `MemoryStore.add`
and came back as a 500 + traceback, and a 300-char fact was accepted with the text
silently sliced to 200. Both are the console's problem to show, so both are 422.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rui.config import Config
from rui.session.memory import MAX_TEXT
from rui.web.api.app import create_app


def client(tmp_path: Path) -> TestClient:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    return TestClient(create_app(cfg))


def test_empty_and_blank_text_are_422_not_500(tmp_path: Path) -> None:
    c = client(tmp_path)
    for text in ("", "   "):
        response = c.post("/api/v1/memory", json={"action": "add", "text": text})
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "empty" in detail  # readable in the console, not a traceback
    assert c.get("/api/v1/memory").json()["items"] == []


def test_over_long_text_is_422_and_not_stored(tmp_path: Path) -> None:
    c = client(tmp_path)
    response = c.post("/api/v1/memory", json={"action": "add", "text": "长" * (MAX_TEXT + 1)})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert str(MAX_TEXT) in detail  # the client learns the limit
    assert c.get("/api/v1/memory").json()["items"] == []


def test_invalid_action_and_payload_shapes_are_422(tmp_path: Path) -> None:
    c = client(tmp_path)
    cases = [
        {"action": "nope"},
        {},
        {"action": "add", "text": 42},
        {"action": "remove", "id": 7},
    ]
    for body in cases:
        response = c.post("/api/v1/memory", json=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["detail"]


def test_add_and_confirm_still_work(tmp_path: Path) -> None:
    c = client(tmp_path)
    response = c.post("/api/v1/memory", json={"action": "add", "text": "我喜欢周杰伦"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item"]["text"] == "我喜欢周杰伦"
    assert [i["text"] for i in body["items"]] == ["我喜欢周杰伦"]

    # Exactly the limit is allowed - the check is > MAX_TEXT, not >=.
    response = c.post("/api/v1/memory", json={"action": "add", "text": "好" * MAX_TEXT})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["text"] == "好" * MAX_TEXT

    item_id = body["item"]["id"]
    response = c.post("/api/v1/memory", json={"action": "confirm", "id": item_id})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["confirmed"] is True


def test_confirm_does_not_truncate_an_over_long_edit(tmp_path: Path) -> None:
    c = client(tmp_path)
    item = c.post("/api/v1/memory", json={"action": "add", "text": "咖啡不要糖"}).json()["item"]
    response = c.post(
        "/api/v1/memory",
        json={"action": "confirm", "id": item["id"], "text": "长" * (MAX_TEXT + 1)},
    )
    assert response.status_code == 422, response.text
    assert str(MAX_TEXT) in response.json()["detail"]
    # The stored entry is untouched.
    assert c.get("/api/v1/memory").json()["items"][0]["text"] == "咖啡不要糖"
