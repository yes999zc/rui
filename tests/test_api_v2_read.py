"""C-002 console v2 read endpoints (milestone M1) on a real FastAPI app.

Every test drives ``create_app`` with a real config file and the image-shipped LX06
plug; nothing here mocks the service layer. The point is the contract shape a v2
frontend can rely on: device-centred data, capability provenance, a masked token,
and a ``/v2`` shell that never swallows ``/api/*``.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui import secrets
from rui.config import Config, DeviceConfig, save_config
from rui.log_setup import LOG_FILE_NAME
from rui.web.api import app as app_module
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}
DEVICE = DeviceConfig(
    id="living",
    adapter="xiaoai",
    host="speaker.local",
    room="客厅",
    params={"plug_id": "xiaoai-lx06"},
)


@pytest.fixture()
def console(tmp_path, monkeypatch):
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    created: list[TestClient] = []

    def build(*, devices: list[DeviceConfig] | None = None,
              config: Config | None = None,
              auth_token: str | None = TOKEN,
              dist: Path | None = None) -> tuple[TestClient, Config]:
        if config is None:
            options = {"web": {"auth_token": auth_token}} if auth_token else {}
            config = Config(
                devices=[item.model_copy(deep=True) for item in (devices or [DEVICE])],
                **options,
            )
        config.system.data_dir = str(tmp_path / "data")
        config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, config)
        if dist is not None:
            monkeypatch.setattr(app_module, "WEB_DIST", dist)
        app = create_app(config, config_path=path)
        client = TestClient(app)
        created.append(client)
        return client, config

    yield build
    for client in created:
        client.close()


def _paths(client: TestClient | None) -> list[str]:
    return [
        "/api/v2/meta",
        "/api/v2/house",
        "/api/v2/plugins",
        "/api/v2/plugins/xiaoai-lx06",
        "/api/v2/capabilities",
        "/api/v2/devices",
        "/api/v2/devices/living",
        "/api/v2/devices/living/audio",
        "/api/v2/devices/living/activity",
        "/api/v2/devices/living/connection",
    ]


def test_every_read_endpoint_requires_the_token(console):
    # The v1 auth dependency keeps a per-IP failure budget (10/min -> 429). Split the
    # endpoint matrix so every request below is a plain missing-token 401.
    paths = _paths(None)
    for chunk in (paths[:5], paths[5:]):
        client, _ = console()
        for path in chunk:
            response = client.get(path)
            assert response.status_code == 401, path
            body = response.json()
            assert body["error"]["code"] == "unauthorized"
            assert body["error"]["retryable"] is False
            assert body["request_id"] == response.headers["X-Request-ID"]
        client.close()


def test_meta_describes_the_contract(console):
    client, _ = console()
    response = client.get("/api/v2/meta", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "C-002/1"
    assert body["api_version"] == "2.0"
    assert body["compat"] == {"min": "0.6.0", "max_exclusive": "0.8.0"}
    assert body["features"]["house"] is True
    assert body["features"]["device_activity"] is True
    assert body["capabilities"] == [
        "mic_array", "reference", "playback", "led", "keys", "volume",
        "native_kws", "streaming_asr", "device_wake", "barge_via_device",
    ]
    assert response.headers["X-Request-ID"].startswith("req_")


def test_house_aggregates_devices_rooms_and_setup(console):
    client, _ = console()
    body = client.get("/api/v2/house", headers=AUTH).json()
    assert body["version"]["rui"] == "0.7.1"
    assert body["devices"] == {"total": 1, "enabled": 1, "online": 0, "ready": 0}
    assert body["multi_device"]["enabled"] is False
    assert body["multi_device"]["planes_running"] == 0
    assert body["rooms"] == [
        {"room": "客厅", "device_ids": ["living"], "online": 0, "default_output": False}
    ]
    assert body["setup"]["complete"] is False
    assert body["setup"]["next_step"] in ("params", "connect")
    assert isinstance(body["warnings"], list)


def test_house_warns_about_a_duplicate_room(console):
    second = DEVICE.model_copy(update={"id": "living2"})
    client, _ = console(devices=[DEVICE, second])
    body = client.get("/api/v2/house", headers=AUTH).json()
    codes = [warning["code"] for warning in body["warnings"]]
    assert "duplicate_room" in codes
    room = body["rooms"][0]
    assert room["device_ids"] == ["living", "living2"]


def test_plugins_expose_manifest_ids_not_hardware_names(console):
    client, _ = console()
    body = client.get("/api/v2/plugins", headers=AUTH).json()
    assert body["total"] == 1
    plugin = body["items"][0]
    assert plugin["id"] == "xiaoai-lx06"
    assert plugin["available"] is True
    assert plugin["vendor"] == "Xiaomi"
    device = plugin["devices"][0]
    # C-002's examples said `model_id: "rs03qr"`; the Lead ruled the manifest id is
    # the device id (here `lx06`) and `model` is the human-readable hardware string.
    assert device["model_id"] == "lx06"
    assert device["model"] == "LX06"
    assert device["wake"] == {"source": "rui", "barge_via_device": False}
    assert device["audio_defaults"]["ack_echo_ignore_ms"] == 1200
    assert device["audio_defaults"]["capture"]["rate"] == 48000


def test_plugin_detail_returns_public_manifest_without_entrypoint(console):
    client, _ = console()
    body = client.get("/api/v2/plugins/xiaoai-lx06", headers=AUTH).json()
    assert body["id"] == "xiaoai-lx06"
    assert body["schema"] == "rui-plugin/v0"
    # Lead ruling: rui-plugin/v0 has no requires_socket field; do not invent one.
    assert "requires_socket" not in body
    assert body["runtime"]["mode"] == "in-process"
    assert body["runtime"]["listen_port"] == 4412
    assert "entrypoint" not in body["runtime"]
    assert body["protocol"]["kind"] == "ws"
    assert body["device_side"]["artifact"] == "client"
    assert body["acceptance"]["verified"] is True
    assert body["devices"][0]["model_id"] == "lx06"
    assert "entrypoint" not in body["devices"][0]["runtime"]


def test_unknown_plugin_is_a_contract_404(console):
    client, _ = console()
    response = client.get("/api/v2/plugins/nope", headers=AUTH)
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["details"] == {"resource": "plugin", "id": "nope"}


def test_capability_catalogue_is_the_core_order(console):
    client, _ = console()
    body = client.get("/api/v2/capabilities", headers=AUTH).json()
    ids = [item["id"] for item in body["items"]]
    assert ids == [
        "mic_array", "reference", "playback", "led", "keys", "volume",
        "native_kws", "streaming_asr", "device_wake", "barge_via_device",
    ]
    volume = next(item for item in body["items"] if item["id"] == "volume")
    assert volume["ui_hint"] == "slider"
    assert [action["id"] for action in volume["actions"]] == ["volume.read", "volume.set"]
    assert volume["actions"][1]["args_schema"]["properties"]["value"]["maximum"] == 255
    assert volume["degraded_hint"]["en"]


def test_device_list_is_a_contract_summary(console):
    client, _ = console()
    body = client.get("/api/v2/devices", headers=AUTH).json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["id"] == "living"
    assert row["plugin_id"] == "xiaoai-lx06"
    assert row["model_id"] == "lx06"
    assert row["model"] == "LX06"
    assert row["vendor"] == "Xiaomi"
    assert row["transport"] == "inbound"
    assert row["listen_port"] == 4412  # the manifest's runtime default
    assert row["capabilities"] == [
        "mic_array", "reference", "playback", "led", "keys", "volume",
    ]
    assert row["unknown_capabilities"] == []
    assert row["status_reason"]["code"] in ("token_missing", "audio_plane_stopped",
                                           "not_connected")
    assert set(row["audio_summary"]) == {
        "capture_rate", "playback_rate", "aec_who", "ack_echo_ignore_ms",
    }
    assert row["audio_summary"]["ack_echo_ignore_ms"] == 1200
    assert row["connected"] in (False, None)


def test_device_detail_has_provenance_and_no_secret_values(console):
    client, config = console()
    secrets.put("RUI_DEVICE_TOKEN", "fake-device-token-xyz")
    body = client.get("/api/v2/devices/living", headers=AUTH).json()
    assert body["host"] == "speaker.local"
    assert body["wake_words"] == ["你好小瑞"]
    assert body["volume_target"] == 60
    assert body["params"] == {"plug_id": "xiaoai-lx06"}
    # The frozen contract removed secret_params; unknown legacy params are reported
    # separately instead of being rejected.
    assert "secret_params" not in body
    assert body["unknown_params"] == []
    assert body["effective_capabilities"] == [
        "mic_array", "reference", "playback", "led", "keys", "volume",
    ]
    caps = {item["id"]: item for item in body["capabilities"]}
    # The in-process adapter probes playback/reference, so they are effective; the
    # manifest alone decides led/keys/volume and the probe reports null for them.
    assert caps["playback"] == {
        "id": "playback", "declared": True, "probed": True,
        "effective": True, "known": True, "reason": "ok",
    }
    assert caps["led"]["reason"] == "manifest_only"
    assert caps["led"]["probed"] is None
    assert body["missing_for_barge_in"] == []
    assert body["audio"]["plug_resolved"] is True
    assert body["plane"]["running"] is False
    assert set(body["setup"]) == {"configured", "last_test_ok", "last_test_at"}
    dumped = json.dumps(body, ensure_ascii=False)
    assert "fake-device-token-xyz" not in dumped


def _write_setup_record(config: Config, payload: object) -> None:
    setup = Path(config.system.data_dir) / "setup.json"
    setup.parent.mkdir(parents=True, exist_ok=True)
    setup.write_text(json.dumps(payload), encoding="utf-8")


def test_device_setup_summary_never_reads_another_devices_test(console):
    """M6 review BLOCKER: the detail card's `setup` must be per-device evidence.

    The setup store has one house-wide `test/device/test` slot, so a pass recorded
    for the living room must not render as the study's last test result.
    """
    study = DeviceConfig(id="study", adapter="xiaoai", host="speaker.study",
                         room="书房", params={"plug_id": "xiaoai-lx06"})
    client, config = console(devices=[DEVICE, study])
    _write_setup_record(config, {"test": {"device": {"test": {
        "device_id": "living", "ok": True, "at": "2026-10-26T01:00:00+08:00",
    }}}})

    living = client.get("/api/v2/devices/living", headers=AUTH).json()["setup"]
    assert living["last_test_ok"] is True
    assert living["last_test_at"] == "2026-10-26T01:00:00+08:00"

    other = client.get("/api/v2/devices/study", headers=AUTH).json()["setup"]
    assert other["last_test_ok"] is False
    assert other["last_test_at"] == ""
    assert other["last_test_at"] != living["last_test_at"]


def test_a_string_ok_is_not_a_passing_device_test(console):
    """`"ok": "false"` is a truthy string; only the JSON boolean true may pass."""
    client, config = console()
    _write_setup_record(config, {"test": {"device": {"test": {
        "device_id": "living", "ok": "false", "at": "2026-10-26T02:00:00+08:00",
    }}}})

    setup = client.get("/api/v2/devices/living", headers=AUTH).json()["setup"]
    assert setup["last_test_ok"] is False
    # A failing record is still a record: keep its timestamp visible.
    assert setup["last_test_at"] == "2026-10-26T02:00:00+08:00"


def test_a_corrupt_setup_store_does_not_500_the_device_detail(console):
    """setup.json is user-editable: a non-dict `test` layer is "no record"."""
    client, config = console()
    _write_setup_record(config, {"test": ["boom"]})

    response = client.get("/api/v2/devices/living", headers=AUTH)
    assert response.status_code == 200
    setup = response.json()["setup"]
    assert set(setup) == {"configured", "last_test_ok", "last_test_at"}
    assert setup["last_test_ok"] is False
    assert setup["last_test_at"] == ""


def test_device_audio_endpoint_reports_all_four_layers(console):
    client, _ = console()
    body = client.get("/api/v2/devices/living/audio", headers=AUTH).json()
    assert set(body) == {"effective", "sources", "layers", "overrides",
                         "manifest_defaults", "plug_resolved"}
    # No explicit global value and no override: the plug's hardware layout wins.
    assert body["sources"]["capture.rate"] == "device-manifest"
    assert body["layers"]["capture.rate"]["manifest"] == 48000
    assert body["layers"]["capture.rate"]["effective"] == 48000
    assert body["sources"]["voice.rate"] == "core-default"
    assert body["sources"]["ack.echo_ignore_ms"] == "device-manifest"
    assert body["effective"]["ack_echo_ignore_ms"] == 1200
    assert body["overrides"] == []
    assert body["manifest_defaults"]["ack_echo_ignore_ms"] == 1200


def test_activity_is_empty_for_a_quiet_device(console):
    client, _ = console()
    body = client.get("/api/v2/devices/living/activity", headers=AUTH).json()
    assert body == {"items": [], "total": 0, "truncated": False}


def test_activity_returns_turns_matched_by_device_id(console):
    client, config = console()
    app = client.app
    app.state.recent_turns = collections.deque([
        {"transcript": "把客厅灯关了", "reply": "已关闭", "audio_bytes": 1234,
         "error": "", "tools": ["local.light_off"], "ts_epoch": 1761390000.12,
         "device_id": "living"},
        {"transcript": "卧室的", "reply": "好", "audio_bytes": 99,
         "error": "", "tools": [], "ts_epoch": 1761390100.0,
         "device_id": "dot2"},
    ], maxlen=50)
    body = client.get("/api/v2/devices/living/activity", headers=AUTH).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["type"] == "turn"
    assert item["matched_by"] == "device_id"
    assert item["title"] == "把客厅灯关了"
    assert item["detail"]["reply"] == "已关闭"
    assert item["ts"] == pytest.approx(1761390000.12)


def test_activity_reads_log_lines_by_literal_match(console):
    client, config = console()
    log = Path(config.system.data_dir) / LOG_FILE_NAME
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "2026-10-25 21:00:00,123 INFO device living link ready\n"
        "2026-10-25 21:00:01,000 WARNING unrelated subsystem\n",
        encoding="utf-8",
    )
    body = client.get("/api/v2/devices/living/activity", headers=AUTH).json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["type"] == "log"
    assert item["matched_by"] == "literal"
    assert item["level"] == "info"
    assert "device living link ready" in item["title"]


def test_activity_query_validation(console):
    client, _ = console()
    for query in ("limit=0", "limit=201", "limit=abc", "type=nope"):
        response = client.get(f"/api/v2/devices/living/activity?{query}", headers=AUTH)
        assert response.status_code == 422, query
        body = response.json()
        assert body["error"]["code"] == "invalid_values"
        assert body["error"]["details"]["fields"][0]["path"] in ("limit", "type")


def test_connection_masks_the_token_and_never_leaks_it(console, monkeypatch):
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "fake-device-token-xyz")
    client, _ = console()
    response = client.get("/api/v2/devices/living/connection", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "living"
    assert body["transport"] == "inbound"
    assert body["token_configured"] is True
    assert body["token_env"] == "RUI_DEVICE_TOKEN"
    assert body["listen_port"] == 4412
    assert body["ws_url_masked"].startswith("ws://")
    assert "token=\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" in body["ws_url_masked"]
    assert "fake-device-token-xyz" not in response.text
    assert body["hint"]["zh"] and body["hint"]["en"]


def test_connection_explains_a_missing_token(console):
    client, _ = console()
    body = client.get("/api/v2/devices/living/connection", headers=AUTH).json()
    assert body["configured"] is False
    assert body["token_configured"] is False
    assert "token=" not in (body["ws_url_masked"] or "")


def test_unknown_device_is_a_contract_404_everywhere(console):
    client, _ = console()
    for path in ("/api/v2/devices/nope", "/api/v2/devices/nope/audio",
                 "/api/v2/devices/nope/activity", "/api/v2/devices/nope/connection"):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 404, path
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["details"] == {"resource": "device", "id": "nope"}


def test_unmatched_api_v2_path_is_json_not_the_spa(console, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "v2.html").write_text("<html>v2-shell-marker</html>", encoding="utf-8")
    client, _ = console(dist=dist)
    response = client.get("/api/v2/does-not-exist", headers=AUTH)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "not_found"
    assert "v2-shell-marker" not in response.text


def test_v2_shell_hosts_history_routes_and_assets(console, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "v2.html").write_text("<html>v2-shell-marker</html>", encoding="utf-8")
    (dist / "index.html").write_text("<html>old-shell</html>", encoding="utf-8")
    (dist / "assets" / "app-abc.js").write_text("console.log('v2')", encoding="utf-8")
    client, _ = console(dist=dist)

    for path in ("/v2", "/v2/", "/v2/devices/living", "/v2/house"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "v2-shell-marker" in response.text, path
        assert "old-shell" not in response.text, path

    asset = client.get("/v2/assets/app-abc.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('v2')"
    assert "immutable" in asset.headers["cache-control"]

    missing = client.get("/v2/assets/does-not-exist.js")
    assert missing.status_code == 404
    assert "v2-shell-marker" not in missing.text


def test_v2_shell_falls_back_when_the_bundle_is_missing(console, tmp_path):
    dist = tmp_path / "empty-dist"
    dist.mkdir()
    client, _ = console(dist=dist)
    response = client.get("/v2")
    assert response.status_code == 200
    assert "not built yet" in response.text


def test_v1_routes_keep_their_own_auth_and_shape(console):
    client, _ = console()
    response = client.get("/api/v1/panels")
    assert response.status_code == 401
    response = client.get("/api/v1/panels", headers=AUTH)
    assert response.status_code == 200


def test_repeated_bad_tokens_hit_the_rate_limit_with_a_contract_body(console):
    client, _ = console()
    last = None
    for _ in range(10):
        last = client.get("/api/v2/meta", headers={"X-Auth-Token": "wrong-token"})
    assert last is not None
    assert last.status_code == 429
    body = last.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["retryable"] is True
    assert body["error"]["details"]["retry_after_s"] >= 1
    assert last.headers["Retry-After"]
    assert body["request_id"] == last.headers["X-Request-ID"]


def test_a_safe_incoming_request_id_is_echoed(console):
    client, _ = console()
    response = client.get("/api/v2/meta", headers={**AUTH, "X-Request-ID": "req_caller_123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req_caller_123"


def test_an_unsafe_incoming_request_id_is_replaced(console):
    client, _ = console()
    response = client.get("/api/v2/meta",
                          headers={**AUTH, "X-Request-ID": "bad id with spaces"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"].startswith("req_")
    assert " " not in response.headers["X-Request-ID"]


def test_device_detail_reports_legacy_params_without_dropping_them(console):
    legacy = DEVICE.model_copy(update={
        "params": {"plug_id": "xiaoai-lx06", "mic_gain_db": 18}})
    client, _ = console(devices=[legacy])
    body = client.get("/api/v2/devices/living", headers=AUTH).json()
    # The stored value stays in params (the v1 wizard wrote it); unknown_params only
    # marks it as not declared by the plug.
    assert body["params"]["mic_gain_db"] == 18
    assert body["unknown_params"] == ["mic_gain_db"]
    warning = next(item for item in body["warnings"]
                   if item["code"] == "unknown_params_ignored")
    assert warning["params"] == ["mic_gain_db"]
