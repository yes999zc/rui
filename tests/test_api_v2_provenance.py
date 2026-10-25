"""C-002 §7.15 provenance matrix and §5.2 capability resolution.

These are contract-level tests: each of the five `source` values must be reachable, a
device value equal to the global one must still report `device-override`, and the ack
window's three special paths (`voice.ack_echo_ignore_ms` / manifest top level /
`ack.echo_ignore_ms`) must resolve to one `sources` key.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, save_config
from rui.web.api.app import create_app
from rui.web.api.v2_api import _capability_rows

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


def _build(tmp_path, raw: dict) -> tuple[TestClient, Config]:
    raw.setdefault("web", {"auth_token": TOKEN})
    config = Config.model_validate(raw)
    config.system.data_dir = str(tmp_path / "data")
    config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, config)
    return TestClient(create_app(config, config_path=path)), config


def _provenance(tmp_path, raw: dict) -> dict:
    client, _ = _build(tmp_path, raw)
    response = client.get("/api/v2/devices/living/audio", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def test_manifest_default_wins_over_unstated_core(tmp_path):
    body = _provenance(tmp_path, {"devices": [_device().model_dump(mode="json")]})
    assert body["sources"]["capture.rate"] == "device-manifest"
    assert body["layers"]["capture.rate"] == {
        "effective": 48000,
        "source": "device-manifest",
        "core": 16000,
        "manifest": 48000,
        "global": 16000,
        "global_explicit": False,
        "device": None,
    }
    assert body["sources"]["capture.mic_channels"] == "device-manifest"
    assert body["sources"]["voice.rate"] == "core-default"
    assert body["layers"]["voice.rate"]["manifest"] is None
    assert body["sources"]["ack.echo_ignore_ms"] == "device-manifest"
    assert body["effective"]["ack_echo_ignore_ms"] == 1200


def test_global_explicit_wins_over_the_manifest(tmp_path):
    body = _provenance(tmp_path, {
        "devices": [_device().model_dump(mode="json")],
        "audio": {"capture": {"rate": 16000}},
    })
    assert body["sources"]["capture.rate"] == "global-config"
    assert body["layers"]["capture.rate"]["global_explicit"] is True
    assert body["layers"]["capture.rate"]["effective"] == 16000
    assert body["layers"]["capture.rate"]["manifest"] == 48000


def test_global_value_equal_to_core_is_still_explicit(tmp_path):
    body = _provenance(tmp_path, {
        "devices": [_device().model_dump(mode="json")],
        "audio": {"voice": {"rate": 16000}},
    })
    assert body["layers"]["voice.rate"]["global_explicit"] is True
    assert body["sources"]["voice.rate"] == "global-config"


def test_device_override_beats_every_layer(tmp_path):
    device = _device(audio={"aec": {"who": "device", "enabled": False}})
    body = _provenance(tmp_path, {"devices": [device.model_dump(mode="json")]})
    assert body["sources"]["aec.who"] == "device-override"
    assert body["layers"]["aec.who"]["device"] == "device"
    assert body["layers"]["aec.who"]["global"] == "controller"
    assert body["layers"]["aec.who"]["manifest"] is None
    assert body["effective"]["aec"]["who"] == "device"
    assert body["overrides"] == ["aec.enabled", "aec.who"]
    # The ack override lives under voice but reports under the ack path.
    assert "voice.ack_echo_ignore_ms" not in body["sources"]


def test_explicit_device_value_equal_to_global_still_reports_device(tmp_path):
    device = _device(audio={"capture": {"rate": 16000}})
    body = _provenance(tmp_path, {
        "devices": [device.model_dump(mode="json")],
        "audio": {"capture": {"rate": 16000}},
    })
    # Values are equal, but the console must not infer inheritance from a value.
    assert body["layers"]["capture.rate"]["effective"] == 16000
    assert body["layers"]["capture.rate"]["global"] == 16000
    assert body["layers"]["capture.rate"]["device"] == 16000
    assert body["sources"]["capture.rate"] == "device-override"


def test_ack_window_has_three_layers_and_one_source_key(tmp_path):
    device = _device(audio={"voice": {"ack_echo_ignore_ms": 2500}})
    body = _provenance(tmp_path, {
        "devices": [device.model_dump(mode="json")],
        "ack": {"echo_ignore_ms": 1500},
    })
    assert body["effective"]["ack_echo_ignore_ms"] == 2500
    assert body["sources"]["ack.echo_ignore_ms"] == "device-override"
    assert body["layers"]["ack.echo_ignore_ms"]["manifest"] == 1200
    assert body["layers"]["ack.echo_ignore_ms"]["global"] == 1500
    assert body["layers"]["ack.echo_ignore_ms"]["device"] == 2500
    assert body["overrides"] == ["voice.ack_echo_ignore_ms"]
    assert "voice.ack_echo_ignore_ms" not in body["sources"]


def test_unresolved_plug_reports_unresolved_not_a_fake_default(tmp_path):
    ghost = DeviceConfig(id="living", adapter="no-such-adapter", host="speaker.local")
    body = _provenance(tmp_path, {"devices": [ghost.model_dump(mode="json")]})
    assert body["plug_resolved"] is False
    assert body["manifest_defaults"] == {}
    assert body["sources"]["capture.rate"] == "unresolved"
    assert body["layers"]["capture.rate"]["manifest"] is None


def test_capability_manifest_intersection_and_probe_missing():
    rows, effective, unknown = _capability_rows(
        ["playback", "reference"], ["reference"], True)
    by_id = {row["id"]: row for row in rows}
    assert by_id["reference"] == {
        "id": "reference", "declared": True, "probed": True,
        "effective": True, "known": True, "reason": "ok",
    }
    assert by_id["playback"]["probed"] is False
    assert by_id["playback"]["effective"] is False
    assert by_id["playback"]["reason"] == "probe_missing"
    assert effective == ["reference"]
    assert unknown == []


def test_capability_manifest_only_never_reports_a_probe_value():
    rows, effective, _ = _capability_rows(["led", "keys", "volume"],
                                          ["playback", "led"], True)
    by_id = {row["id"]: row for row in rows}
    for cap in ("led", "keys", "volume"):
        assert by_id[cap]["probed"] is None
        assert by_id[cap]["reason"] == "manifest_only"
    assert by_id["led"]["effective"] is True
    assert effective == ["led", "keys", "volume"]
    # A probe-only capability is reported but never promoted.
    assert by_id["playback"]["declared"] is False
    assert by_id["playback"]["effective"] is False
    assert by_id["playback"]["reason"] == "not_declared"


def test_capability_unresolved_manifest_makes_the_probe_authoritative():
    rows, effective, _ = _capability_rows([], ["playback", "led"], False)
    assert [row["id"] for row in rows] == ["playback", "led"]
    assert effective == ["playback", "led"]
    assert all(row["declared"] is False for row in rows)


def test_unknown_and_deprecated_capabilities_do_not_crash():
    rows, effective, unknown = _capability_rows(
        ["led", "future_sensor", "native_kws"], None, True)
    by_id = {row["id"]: row for row in rows}
    assert unknown == ["future_sensor"]
    assert by_id["future_sensor"]["known"] is False
    assert by_id["future_sensor"]["effective"] is False
    assert by_id["future_sensor"]["reason"] == "unknown_capability"
    assert by_id["native_kws"]["reason"] == "deprecated"
    assert by_id["native_kws"]["effective"] is False
    assert effective == ["led"]
