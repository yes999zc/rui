"""Per-device device-test history: testing the study must not erase the living room.

The setup store used to keep one house-wide ``test/device/test`` slot, so two devices
interleaved their results: A's detail card went empty as soon as B was tested. The
store now keeps each device's record under ``test/device/by_id/<id>`` and still mirrors
the newest record into the legacy slot for the v1 device list, which has no device
dimension. These tests drive the real v2 routes, not the store helpers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui import setup_state
from rui.config import Config, DeviceConfig, save_config
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
LIVING = DeviceConfig(
    id="living", adapter="xiaoai", host="speaker.local", room="客厅",
    params={"plug_id": "xiaoai-lx06"},
)
STUDY = DeviceConfig(
    id="study", adapter="xiaoai", host="speaker.study", room="书房",
    params={"plug_id": "xiaoai-lx06"},
)
T1 = "2026-10-26T01:00:00+00:00"
T2 = "2026-10-26T02:00:00+00:00"


@pytest.fixture()
def console(tmp_path, monkeypatch):
    """A real app over a real config file with the image-shipped LX06 plug."""
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    created: list[TestClient] = []

    def build() -> tuple[TestClient, Config]:
        config = Config(
            devices=[item.model_copy(deep=True) for item in (LIVING, STUDY)],
            web={"auth_token": TOKEN},
        )
        config.system.data_dir = str(tmp_path / "data")
        config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, config)
        client = TestClient(create_app(config, config_path=path))
        created.append(client)
        return client, config

    yield build
    for client in created:
        client.close()


def _write_setup(config: Config, payload: object) -> Path:
    path = Path(config.system.data_dir) / "setup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _setup(client: TestClient, device_id: str) -> dict:
    response = client.get(f"/api/v2/devices/{device_id}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()["setup"]


def _test_device(client: TestClient, device_id: str) -> None:
    response = client.post(f"/api/v2/devices/{device_id}/test", headers=AUTH)
    assert response.status_code == 200, response.text


def test_interleaved_tests_keep_each_devices_own_result(console, monkeypatch):
    """A then B: A's card still shows A's test, B's shows B's, and they differ."""
    client, config = console()
    stamps = iter([T1, T2])
    # Later reads may take a timestamp too (the plan's own bookkeeping); only the two
    # probe writes need fixed values, so the iterator falls back to a third one.
    monkeypatch.setattr(setup_state, "iso_now",
                        lambda: next(stamps, "2026-10-26T03:00:00+00:00"))

    _test_device(client, "living")
    _test_device(client, "study")

    living = _setup(client, "living")
    study = _setup(client, "study")
    assert living["last_test_at"] == T1
    assert study["last_test_at"] == T2
    assert living["last_test_at"] != study["last_test_at"]

    # The legacy slot still says "the last test in this house" for the v1 list, and
    # both per-device records survive side by side.
    stored = json.loads(
        (Path(config.system.data_dir) / "setup.json").read_text(encoding="utf-8"))
    device_node = stored["test"]["device"]
    assert device_node["test"]["device_id"] == "study"
    assert device_node["test"]["at"] == T2
    assert set(device_node["by_id"]) == {"living", "study"}
    assert device_node["by_id"]["living"]["at"] == T1

    # The setup plan reads the same source, so it also stays per-device.
    plan = client.get(
        "/api/v2/setup/plan", params={"plugin_id": "xiaoai-lx06", "device_id": "living"},
        headers=AUTH)
    assert plan.status_code == 200, plan.text
    test_step = next(step for step in plan.json()["steps"] if step["id"] == "test")
    assert test_step["evidence"]["tested"] is True


def test_a_legacy_single_slot_still_feeds_only_its_own_device(console):
    """Files written before the per-device map: the old slot is a fallback, not a leak."""
    client, config = console()
    _write_setup(config, {"test": {"device": {"test": {
        "device_id": "living", "ok": True, "at": T1,
    }}}})

    living = _setup(client, "living")
    assert living["last_test_ok"] is True
    assert living["last_test_at"] == T1
    study = _setup(client, "study")
    assert study["last_test_ok"] is False
    assert study["last_test_at"] == ""


def test_the_per_device_record_wins_over_the_legacy_slot(console):
    client, config = console()
    _write_setup(config, {"test": {"device": {
        "by_id": {"study": {"device_id": "study", "ok": True, "at": T2}},
        "test": {"device_id": "living", "ok": False, "at": T1},
    }}})

    study = _setup(client, "study")
    assert (study["last_test_ok"], study["last_test_at"]) == (True, T2)
    living = _setup(client, "living")
    assert (living["last_test_ok"], living["last_test_at"]) == (False, T1)


def test_an_old_store_without_any_test_reads_as_empty(console):
    """No `last_test_*` anywhere is "never tested", not a 500 and not another device."""
    client, config = console()
    _write_setup(config, {"keys": {"last_saved_at": T1}})

    for device_id in ("living", "study"):
        setup = _setup(client, device_id)
        assert set(setup) == {"configured", "last_test_ok", "last_test_at"}
        assert setup["last_test_ok"] is False
        assert setup["last_test_at"] == ""


def test_a_corrupt_per_device_layer_does_not_500(console):
    """setup.json is user-editable: a non-dict `by_id` reads as "no per-device record"."""
    client, config = console()
    _write_setup(config, {"test": {"device": {
        "by_id": ["boom"],
        "test": {"device_id": "living", "ok": True, "at": T1},
    }}})

    living = _setup(client, "living")
    assert living["last_test_ok"] is True
    assert living["last_test_at"] == T1
    study = _setup(client, "study")
    assert (study["last_test_ok"], study["last_test_at"]) == (False, "")


def test_a_by_id_record_naming_another_device_is_no_record(console):
    """The map key is the identity: a hand-edited mismatch is corrupt, not evidence."""
    client, config = console()
    _write_setup(config, {"test": {"device": {
        "by_id": {"living": {"device_id": "study", "ok": True, "at": T1}},
        "test": {"device_id": "living", "ok": True, "at": T2},
    }}})

    # The mismatching by_id entry is not this device's record, and the legacy slot is
    # not a second chance for a corrupt per-device entry.
    setup = _setup(client, "living")
    assert (setup["last_test_ok"], setup["last_test_at"]) == (False, "")


def test_an_empty_device_id_reads_and_writes_nothing(console):
    """Draft callers (no device yet) must not raise or write a blank key."""
    assert setup_state.device_test_record({}, "") == {}
    mismatch = {"test": {"device": {"test": {
        "device_id": "living", "ok": True, "at": T1}}}}
    assert setup_state.device_test_record(mismatch, "   ") == {}

    data: dict = {}
    assert setup_state.record_device_test(data, "", {"ok": True}) is False
    assert setup_state.record_device_test(data, "   ", {"ok": True}) is False
    assert data == {}
