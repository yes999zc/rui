"""P6: the console's phrasebook API.

Read all templates, save a validated override layer, reset it. The interesting
cases are the refusals: a template that drops a slot must not reach phrases.yaml,
because the speaker would then say a sentence with pieces missing (the old build
silently stripped the slot instead of reporting it).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rui.config import Config
from rui.session import phrases
from rui.web.api.app import create_app


@pytest.fixture()
def client(tmp_path):
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    yield TestClient(create_app(cfg)), tmp_path / "data"
    phrases.reset()          # the book is module-global; every test starts from defaults


def test_lists_every_template_with_the_slot_contract(client) -> None:
    api, _data = client
    body = api.get("/api/v1/phrases").json()
    assert body["effect"] == "next_phrase"
    assert len(body["keys"]) == len(phrases.DEFAULTS) == 62  # +1 turn.not_heard (T5), +1 music.star_unsupported (T3)
    assert any(key["advanced"] for key in body["keys"])
    assert body["file"].endswith("phrases.yaml")
    every = {key["key"] for key in body["keys"]}
    with_slots = next(key for key in body["keys"] if key["slots"])
    assert set(with_slots["slots"]) == set(phrases.DEFAULT_SLOTS[with_slots["key"]])
    assert set(phrases.DEFAULT_SLOTS) == every


def test_a_valid_override_applies_to_the_next_phrase_and_is_written(client) -> None:
    api, data = client
    key = "music.stopped"
    response = api.put("/api/v1/phrases", json={"overrides": {key: "好嘞，关了。"}})
    assert response.status_code == 200, response.text
    assert response.json()["overrides"][key] == "好嘞，关了。"
    assert phrases.book.render(key) == "好嘞，关了。"
    text = (data / "phrases.yaml").read_text(encoding="utf-8")
    assert "好嘞，关了。" in text


def test_dropping_a_slot_is_refused_and_writes_nothing(client) -> None:
    api, data = client
    key = next(name for name, slots in phrases.DEFAULT_SLOTS.items() if slots)
    response = api.put("/api/v1/phrases", json={"overrides": {key: "没有槽位的句子。"}})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert key in detail and detail[key]
    assert not (data / "phrases.yaml").exists()
    assert phrases.book.render(key, **{slot: "x" for slot in phrases.DEFAULT_SLOTS[key]})


def test_unknown_keys_and_bad_templates_are_refused(client) -> None:
    api, _data = client
    for overrides in (
        {"no.such.key": "x"},
        {"music.stopped": " unbalanced {"},
        {"music.stopped": "x" * 5000},
    ):
        response = api.put("/api/v1/phrases", json={"overrides": overrides})
        assert response.status_code == 422, (overrides, response.text)


def test_reset_restores_defaults(client) -> None:
    api, _data = client
    key = "music.stopped"
    api.put("/api/v1/phrases", json={"overrides": {key: "好嘞，关了。"}})
    body = api.post("/api/v1/phrases/reset", json={"keys": [key]}).json()
    assert key not in body["overrides"]
    assert phrases.book.render(key) == phrases.DEFAULTS[key]
    api.put("/api/v1/phrases", json={"overrides": {key: "再见。"}})
    body = api.post("/api/v1/phrases/reset", json={}).json()
    assert body["overrides"] == {}


def test_the_panel_uses_the_escape_hatch(client) -> None:
    api, _data = client
    panels = api.get("/api/v1/panels").json()["panels"]
    # Phase 4: the phrasebook is the "phrases" tab of the merged voice-assistant
    # page, not a panel of its own (docs/console-ia-audit.md §3/§5).
    panel = next(item for item in panels if item["id"] == "voice-assistant")
    phrases_blocks = [block for block in panel["blocks"]
                      if block.get("tab") == "phrases"]
    assert [block["component"] for block in phrases_blocks] == ["phrasebook-editor"]
    assert all(block["type"] == "component" for block in phrases_blocks)
    assert api.get(panel["source"]).status_code == 200
    assert api.get("/api/v1/phrases").status_code == 200
