"""Regression tests for the four 0.6.x console-v2 backend-debt items.

A. DeviceDetail reports the stored `listen_port` and whether it was explicit.
B. A device write must not turn the derived global `kws_keywords_file` into an
   explicit audio field (or persist it).
C. `device_disabled` outranks the capability gate on /test, /actions and verify.say.
D. v2 DELETE ignores a pre-existing port conflict, like v1 does.

Every test drives `create_app` with a real config file and the image-shipped LX06 plug.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, load_config, save_config
from rui.config.catalog import keyword_file
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}


def _device(**overrides) -> DeviceConfig:
    payload = {
        "id": "living",
        "adapter": "xiaoai",
        "host": "speaker.local",
        "room": "客厅",
        "params": {"plug_id": "xiaoai-lx06"},
    }
    payload.update(overrides)
    return DeviceConfig(**payload)


@pytest.fixture()
def console(tmp_path, monkeypatch):
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    created: list[TestClient] = []

    def build(*, devices: list[DeviceConfig] | None = None,
              config: Config | None = None,
              set_keywords_path: bool = True) -> tuple[TestClient, Path, Config]:
        if config is None:
            config = Config(
                web={"auth_token": TOKEN},
                devices=[item.model_copy(deep=True)
                         for item in (devices or [_device()])],
            )
        config.system.data_dir = str(tmp_path / "data")
        if set_keywords_path:
            config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, config)
        client = TestClient(create_app(config, config_path=path))
        created.append(client)
        return client, path, config

    yield build
    for client in created:
        client.close()


# ------------------------------------------------------------------ A: listen_port

def test_device_detail_reports_stored_and_explicit_listen_port(console):
    client, _, _ = console(devices=[
        _device(id="explicit", listen_port=8870),
        _device(id="inherited", host="other.local"),
    ])

    explicit = client.get("/api/v2/devices/explicit", headers=AUTH).json()
    assert explicit["listen_port"] == 8870
    assert explicit["listen_port_stored"] == 8870
    assert explicit["listen_port_explicit"] is True

    inherited = client.get("/api/v2/devices/inherited", headers=AUTH).json()
    # 4412 is the LX06 manifest's runtime.listen_port, not a stored config value.
    assert inherited["listen_port"] == 4412
    assert inherited["listen_port_stored"] is None
    assert inherited["listen_port_explicit"] is False

    # The list shape stays a summary: the new fields are detail-only.
    rows = client.get("/api/v2/devices", headers=AUTH).json()["items"]
    by_id = {row["id"]: row for row in rows}
    assert "listen_port_stored" not in by_id["explicit"]
    assert "listen_port_explicit" not in by_id["inherited"]


# ------------------------------------------------- B: no global audio field on writes

def test_device_writes_do_not_make_kws_path_explicit(console, tmp_path):
    # No kws_keywords_file in the config: the path is derived, so a device write must
    # neither persist it nor report it as explicit.
    config = Config(web={"auth_token": TOKEN}, devices=[_device(wake_words=["你好小瑞"])])
    config.audio.voice.kws_model_dir = str(tmp_path / "kws")
    client, path, _ = console(config=config, set_keywords_path=False)

    before = client.get("/api/v2/house/audio", headers=AUTH).json()["explicit"]
    # `kws_model_dir` is a real explicit choice here; the derived file must not join it.
    assert "audio.voice.kws_keywords_file" not in before

    patched = client.patch("/api/v2/devices/living", headers=AUTH,
                           json={"wake_words": ["嗨小瑞"]})
    assert patched.status_code == 200, patched.text
    after_patch = client.get("/api/v2/house/audio", headers=AUTH).json()["explicit"]
    assert after_patch == before

    create = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "study", "plugin_id": "xiaoai-lx06",
        "host": "study.local", "room": "书房", "listen_port": 8871,
    })
    assert create.status_code == 201, create.text
    assert client.get("/api/v2/house/audio", headers=AUTH).json()["explicit"] == before

    delete = client.delete("/api/v2/devices/study?confirm=study", headers=AUTH)
    assert delete.status_code == 200, delete.text
    assert client.get("/api/v2/house/audio", headers=AUTH).json()["explicit"] == before

    # Nothing was written to the global audio layer.
    stored = load_config(path)
    assert stored.audio.voice.kws_keywords_file == ""
    text = path.read_text(encoding="utf-8")
    assert "kws_keywords_file" not in text

    # The derived paths still carry the new wake word: the plane fallback
    # (kws_model_dir/keywords.txt) and the setup evidence copy next to config.yaml.
    model_keywords = tmp_path / "kws" / "keywords.txt"
    assert "@嗨小瑞" in model_keywords.read_text(encoding="utf-8")
    config_keywords = path.parent / "keywords.txt"
    assert "@嗨小瑞" in config_keywords.read_text(encoding="utf-8")

    # That is the path the loaded config derives (no stored field needed), and the
    # console reads the same file back.
    assert keyword_file(stored) == model_keywords
    threshold = client.get("/api/v1/voice/threshold", headers=AUTH).json()
    assert threshold["exists"] is True
    assert threshold["keywords"] == 1


def test_an_explicit_kws_path_survives_a_device_write(console, tmp_path):
    # The mirror must not touch the other direction either: a path the operator did
    # choose stays explicit and stays on disk.
    config = Config(web={"auth_token": TOKEN}, devices=[_device()])
    config.audio.voice.kws_keywords_file = str(tmp_path / "custom-keywords.txt")
    client, path, _ = console(config=config, set_keywords_path=False)

    before = client.get("/api/v2/house/audio", headers=AUTH).json()["explicit"]
    assert "audio.voice.kws_keywords_file" in before

    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    after = client.get("/api/v2/house/audio", headers=AUTH).json()["explicit"]
    assert after == before
    stored = load_config(path)
    assert stored.audio.voice.kws_keywords_file == str(tmp_path / "custom-keywords.txt")
    assert "@嗨小瑞" in (tmp_path / "custom-keywords.txt").read_text(encoding="utf-8")


def test_wake_word_edit_keeps_a_hand_tuned_gate_with_no_configured_path(console, tmp_path):
    """MAJOR-2 regression: `:3.00 #0.20` must survive a wake-word edit.

    With neither `kws_keywords_file` nor `kws_model_dir`, `keyword_file()` is None and
    `keyword_settings()` can only answer the 0.15/2.00 defaults. The copy next to
    config.yaml is then the only home of the hand-tuned gate, so the mirror must read
    it before rewriting instead of resetting it.
    """
    config = Config(web={"auth_token": TOKEN},
                    devices=[_device(wake_words=["你好小瑞"])])
    client, path, _ = console(config=config, set_keywords_path=False)
    keywords = path.parent / "keywords.txt"
    keywords.write_text("n ǐ h ǎo x iǎo r uì :3.00 #0.20 @你好小瑞\n",
                        encoding="utf-8")

    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    text = keywords.read_text(encoding="utf-8")
    assert "@嗨小瑞" in text
    assert "@你好小瑞" not in text
    assert ":3.00 #0.20" in text

    # The threshold endpoint reads that same derived copy, not a cwd-relative file.
    threshold = client.get("/api/v1/voice/threshold", headers=AUTH).json()
    assert threshold["exists"] is True
    assert threshold["file"] == str(keywords)
    assert threshold["threshold"] == 0.20
    assert threshold["boost"] == 3.00
    assert threshold["keywords"] == 1


def test_threshold_endpoint_stays_readable_with_no_configured_path(console):
    """MINOR-2 regression: no path in config must not make the endpoint blind."""
    config = Config(web={"auth_token": TOKEN},
                    devices=[_device(wake_words=["你好小瑞"])])
    client, path, _ = console(config=config, set_keywords_path=False)

    missing = client.get("/api/v1/voice/threshold", headers=AUTH).json()
    assert missing["exists"] is False
    assert missing["file"] == str(path.parent / "keywords.txt")

    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    created = client.get("/api/v1/voice/threshold", headers=AUTH).json()
    assert created["exists"] is True
    assert created["threshold"] == 0.15
    assert created["boost"] == 2.00
    assert created["keywords"] == 1


def test_threshold_endpoint_degrades_instead_of_500_when_unreadable(console, monkeypatch,
                                                                    caplog):
    """A read-only endpoint must not 500 on a keywords path it cannot read.

    A directory where `keywords.txt` should be (or a permission/mount error) used to
    raise out of `keyword_meta`; the console then lost the whole page for a file it
    only shows. Degrade to the default gate with `exists=false` and log it.
    """
    config = Config(web={"auth_token": TOKEN},
                    devices=[_device(wake_words=["你好小瑞"])])
    client, path, _ = console(config=config, set_keywords_path=False)
    keywords = path.parent / "keywords.txt"
    keywords.write_text("n ǐ h ǎo x iǎo r uì :3.00 #0.20 @你好小瑞\n",
                        encoding="utf-8")

    def unreadable(target):
        raise OSError(21, "Is a directory", str(target))

    monkeypatch.setattr("rui.web.api.app.keyword_meta", unreadable)
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/v1/voice/threshold", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "file": str(keywords),
        "exists": False,
        "threshold": 0.15,
        "boost": 2.00,
        "keywords": 0,
    }
    assert any("cannot read" in record.getMessage() for record in caplog.records)


def test_unreadable_derived_copy_skips_the_mirror_instead_of_500(console, monkeypatch,
                                                                 caplog):
    """A copy that exists but cannot be read must not turn a saved device into a 500.

    `_write_devices` has already persisted the config and applied it when the mirror
    runs, so an uncaught `OSError` would fail the response after the write and fail
    every retry the same way. The mirror must be skipped, and the unreadable file must
    stay untouched: rewriting it from the 0.15/2.00 defaults would silently reset the
    hand-tuned gate that `test_wake_word_edit_keeps_a_hand_tuned_gate_*` protects.
    """
    config = Config(web={"auth_token": TOKEN},
                    devices=[_device(wake_words=["你好小瑞"])])
    client, path, _ = console(config=config, set_keywords_path=False)
    keywords = path.parent / "keywords.txt"
    original = "n ǐ h ǎo x iǎo r uì :3.00 #0.20 @你好小瑞\n"
    keywords.write_text(original, encoding="utf-8")

    def unreadable(target):
        raise OSError(13, "Permission denied", str(target))

    monkeypatch.setattr("rui.web.api.app.keyword_meta", unreadable)
    with caplog.at_level(logging.WARNING):
        response = client.patch("/api/v2/devices/living", headers=AUTH,
                                json={"wake_words": ["嗨小瑞"]})

    assert response.status_code == 200, response.text
    # save_config ran before the mirror, so the requested write is already on disk;
    # the response must not pretend otherwise.
    assert load_config(path).devices[0].wake_words == ["嗨小瑞"]
    # Skipped means skipped: the unreadable file keeps the hand-tuned gate.
    assert keywords.read_text(encoding="utf-8") == original
    assert any("skipping the mirror" in record.getMessage() for record in caplog.records)


# ------------------------------------------- C: device_disabled wins every gate

def test_disabled_device_reports_device_disabled_everywhere(console):
    client, _, _ = console(devices=[_device(enabled=False)])

    test = client.post("/api/v2/devices/living/test", headers=AUTH)
    assert test.status_code == 200
    body = test.json()
    assert body["ok"] is False
    codes = [check["code"] for check in body["checks"]]
    assert "device_disabled" in codes
    assert body["checks"][0]["code"] == "device_disabled"

    for action, args in (
        ("audio.say", {"text": "你好"}),
        ("volume.read", {}),
        ("led.set", {"effect": "on"}),
    ):
        response = client.post("/api/v2/devices/living/actions", headers=AUTH,
                               json={"action": action, "args": args})
        assert response.status_code == 409, action
        assert response.json()["error"]["code"] == "device_disabled", action

    verify = client.post("/api/v2/setup/plan/steps/verify", headers=AUTH, json={
        "action": "verify.say", "device_id": "living", "values": {"text": "你好"},
    })
    assert verify.status_code == 409
    assert verify.json()["error"]["code"] == "device_disabled"

    detail = client.get("/api/v2/devices/living", headers=AUTH).json()
    assert detail["status_reason"]["code"] == "device_disabled"


def test_disabled_device_outranks_an_unresolvable_capability(console):
    # No manifest resolves, so `declared` is empty. The enabled capability gate would
    # answer capability_unsupported; a disabled device must still say device_disabled.
    client, _, _ = console(devices=[
        _device(enabled=False, params={"plug_id": "no-such-plug"}),
    ])
    response = client.post("/api/v2/devices/living/actions", headers=AUTH,
                           json={"action": "audio.say", "args": {"text": "你好"}})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_disabled"


def test_verify_say_reports_plug_unavailable_before_device_disabled(console):
    """Known setup ordering (C-002 A-2): a step cannot start without a resolved plug.

    `/actions` can judge `enabled` without the plug manifest; `verify.say` first
    builds the plan context, so an unresolvable plug wins there. The contract records
    this exception instead of claiming one order for every endpoint.
    """
    client, _, _ = console(devices=[
        _device(enabled=False, params={"plug_id": "no-such-plug"}),
    ])
    response = client.post("/api/v2/setup/plan/steps/verify", headers=AUTH, json={
        "action": "verify.say", "device_id": "living", "values": {"text": "你好"},
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plug_unavailable"


# --------------------------------------------- D: DELETE ignores a port conflict

def test_delete_does_not_validate_a_preexisting_port_conflict(console):
    client, path, _ = console(devices=[
        _device(id="living", listen_port=8870),
        _device(id="living2", host="other.local", listen_port=8870),
        _device(id="third", host="third.local"),
    ])

    # An unrelated delete must succeed even though living/living2 share port 8870.
    response = client.delete("/api/v2/devices/third?confirm=third", headers=AUTH)
    assert response.status_code == 200, response.text
    assert [item.id for item in load_config(path).devices] == ["living", "living2"]

    # Deleting one of the colliding devices is the documented repair path.
    response = client.delete("/api/v2/devices/living?confirm=living", headers=AUTH)
    assert response.status_code == 200, response.text
    assert [item.id for item in load_config(path).devices] == ["living2"]

    # create/patch keep checking ports (only DELETE is exempt).
    create = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "third", "plugin_id": "xiaoai-lx06", "host": "third.local",
        "room": "客厅", "listen_port": 4412,
    })
    assert create.status_code == 201, create.text
    conflict = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "fourth", "plugin_id": "xiaoai-lx06", "host": "fourth.local",
        "room": "书房", "listen_port": 8870,
    })
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "port_conflict"
