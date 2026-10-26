"""rui#18: request bodies are bounded before any handler sees them.

A large body without a token used to be read/parsed first, so an unauthenticated
caller could push megabytes through the v1 endpoints. The fix is an ASGI-level
limit (default 1 MiB, ``RUI_MAX_REQUEST_BYTES``) that rejects with 413 while
leaving the existing 401 (bad token) and 422 (bad JSON with a valid token)
semantics untouched.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, save_config
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
LIMIT = 1024


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RUI_MAX_REQUEST_BYTES", str(LIMIT))
    monkeypatch.delenv("HOUSE_EAR_MAX_REQUEST_BYTES", raising=False)
    config = Config(web={"auth_token": TOKEN})
    config.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    created = TestClient(create_app(config, config_path=path))
    yield created
    created.close()


def _large_body() -> bytes:
    return b'{"text":"' + b"x" * (LIMIT * 2) + b'"}'


def test_large_body_without_a_token_is_413(client: TestClient) -> None:
    response = client.post("/api/v1/chat", content=_large_body())
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_large_body_with_a_token_is_also_413(client: TestClient) -> None:
    response = client.post("/api/v1/chat", content=_large_body(), headers=AUTH)
    assert response.status_code == 413


def test_streamed_body_without_content_length_is_413(client: TestClient) -> None:
    def chunks():
        for _ in range(4):
            yield b"x" * LIMIT

    response = client.post("/api/v1/chat", content=chunks())
    assert response.status_code == 413


def test_small_body_without_a_token_is_still_401(client: TestClient) -> None:
    response = client.post("/api/v1/chat", json={"text": "hello"})
    assert response.status_code == 401


def test_bad_json_with_a_valid_token_is_still_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_small_body_with_a_valid_token_is_not_rejected_by_the_limit(
    client: TestClient,
) -> None:
    # The chat route answers 503 without a runner; the point is that the limit
    # middleware did not turn the request into 413/401.
    response = client.post("/api/v1/chat", json={"text": "hello"}, headers=AUTH)
    assert response.status_code in (200, 503)
