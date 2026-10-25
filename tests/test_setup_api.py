"""P2.2 backend: plug registry, eight-step evidence, and allowlisted actions.

The wizard's state must come from files, secrets and the live plane - never from a
client claim. These tests pin that, plus the write-only/0600/idempotent promises of
``data_dir/setup.json``.
"""
from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient

from rui import secrets as secrets_module
from rui.config import Config, DeviceConfig, load_config, save_config
from rui.config.schema import ProviderConfig, StageConfig
from rui.providers import keycheck
from rui.web.api import app as app_module
from rui.web.api.app import create_app

KEYS = {
    "RUI_LLM_KEY": "sk-llm-should-not-echo",
    "RUI_ASR_KEY": "sk-asr-should-not-echo",
    "RUI_TTS_KEY": "sk-tts-should-not-echo",
}

KWS_FILES = (
    "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
    "decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
    "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
)


class _Plane:
    """The minimum supervisor surface `_plane_snapshot` and a restart use."""

    def __init__(self, connected: bool = False, *, fail_start: bool = False) -> None:
        self.connected = connected
        self.fail_start = fail_start
        self.cfg = None
        self.error = ""

    def status(self) -> dict:
        return {
            "running": True,
            "device_id": "living",
            "error": self.error,
            "metrics": {"speaker_connected": self.connected},
        }

    async def stop(self) -> None:
        return None

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("plane did not come back")

    def watch(self) -> None:
        return None


@pytest.fixture()
def wizard(tmp_path, monkeypatch):
    """A config with a sherpa ASR dir, but no model files, keys or devices yet."""
    for name in (*KEYS, "RUI_DEVICE_TOKEN", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    asr = tmp_path / "asr"
    vad = tmp_path / "silero_vad.onnx"
    kws = tmp_path / "kws"
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.providers.catalog["asr"] = ProviderConfig(kind="sherpa", model=str(asr))
    # A configured endpoint is part of "this provider can answer"; the schema default
    # empty base_url is deliberately not enough.
    cfg.providers.catalog["tts"] = ProviderConfig(
        kind="openai_compat", base_url="https://tts.example/v1", model="tts-1",
        api_key_env="RUI_TTS_KEY")
    cfg.providers.catalog["llm"] = ProviderConfig(
        kind="openai_compat", base_url="https://llm.example/v1", model="chat",
        api_key_env="RUI_LLM_KEY")
    cfg.audio.voice.kws_model_dir = str(kws)
    cfg.audio.voice.vad_model_path = str(vad)
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), path, tmp_path


def _make_models(tmp_path) -> None:
    asr = tmp_path / "asr"
    asr.mkdir(exist_ok=True)
    (asr / "tokens.txt").write_text("x", encoding="utf-8")
    (asr / "model.int8.onnx").write_bytes(b"fake")
    (tmp_path / "silero_vad.onnx").write_bytes(b"fake")
    kws = tmp_path / "kws"
    kws.mkdir(exist_ok=True)
    (kws / "tokens.txt").write_text("x", encoding="utf-8")
    for name in KWS_FILES:
        (kws / name).write_bytes(b"fake")


def _make_local_asr(tmp_path) -> None:
    asr = tmp_path / "asr"
    asr.mkdir(exist_ok=True)
    (asr / "tokens.txt").write_text("x", encoding="utf-8")
    (asr / "model.int8.onnx").write_bytes(b"fake")


def _step(body: dict, step_id: str) -> dict:
    return next(item for item in body["steps"] if item["id"] == step_id)


def _plug_dir(tmp_path, plugin_id: str = "demo") -> None:
    root = tmp_path / "data" / "plugs" / plugin_id
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        "schema: rui-plugin/v0\n"
        f"id: {plugin_id}\n"
        "name: Demo Speaker Plug\n"
        "version: 1.0.0\n"
        'rui_compat: ">=0.0.1 <999.0.0"\n'
        "license: MIT\n"
        "maintainer: Test <test@example.invalid>\n"
        "trust: builtin\n"
        "devices:\n"
        "  - devices/demo.yaml\n",
        encoding="utf-8",
    )
    (root / "devices" / "demo.yaml").write_text(
        "schema: rui-device/v0\n"
        f"id: {plugin_id}\n"
        "name: Demo Speaker\n"
        "vendor: Demo Inc.\n"
        "class: A\n"
        "transport: outbound\n"
        "protocol: {kind: ws, version: '1'}\n"
        "capabilities: [mic_array, playback]\n"
        "params:\n"
        "  - {key: host, label: Address, type: string, required: true}\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------ plug registry

def test_plugs_list_contains_the_builtin_lx06(wizard) -> None:
    client, _path, _tmp = wizard
    body = client.get("/api/v1/plugs").json()
    lx06 = next(plug for plug in body["plugs"] if plug["id"] == "xiaoai-lx06")
    assert lx06["source"] == "builtin"
    assert lx06["trust"] == "builtin"
    assert lx06["class"] == "A"
    assert lx06["adapter"] == "lx06"
    assert lx06["transport"] == "inbound"
    assert lx06["available"] is True
    assert lx06["error"] is None


def test_user_plugs_load_and_broken_ones_are_data(tmp_path, monkeypatch) -> None:
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    _plug_dir(tmp_path, "demo")
    broken = tmp_path / "data" / "plugs" / "broken"
    broken.mkdir(parents=True)
    (broken / "plugin.yaml").write_text("schema: nope\n", encoding="utf-8")
    body = TestClient(create_app(cfg)).get("/api/v1/plugs").json()
    demo = next(plug for plug in body["plugs"] if plug["id"] == "demo")
    assert demo["source"] == "user" and demo["available"] is True
    failed = [plug for plug in body["plugs"] if plug["id"] is None]
    assert failed and "plugin.yaml" in failed[0]["error"]
    assert failed[0]["available"] is False


def test_plug_detail_includes_device_manifests_and_unknown_is_404(wizard) -> None:
    client, _path, _tmp = wizard
    body = client.get("/api/v1/plugs/xiaoai-lx06").json()
    assert body["available"] is True
    assert body["manifest"]["id"] == "xiaoai-lx06"
    assert body["manifest"]["devices"][0]["device_side"]["artifact"] == "client"
    assert body["manifest"]["devices"][0]["runtime"]["entrypoint"] == \
        "xiaoai_plug.adapter:build"
    assert {param["key"] for param in body["manifest"]["devices"][0]["params"]} == {
        "host", "room", "wake_words"}
    assert body["resolved"]["devices"][0]["listen_port"] == 4412
    assert body["resolved"]["devices"][0]["token_env"] == "RUI_DEVICE_TOKEN"

    missing = client.get("/api/v1/plugs/not-a-plug")
    assert missing.status_code == 404
    detail = missing.json()["detail"]
    assert detail["code"] == "unknown_plug" and detail["details"]["plug_id"] == "not-a-plug"


def test_plugs_and_step_endpoints_require_the_console_token(tmp_path) -> None:
    cfg = Config(web={"auth_token": "tok"})
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    assert client.get("/api/v1/plugs").status_code == 401
    assert client.get("/api/v1/setup/step").status_code == 401
    assert client.get("/api/v1/plugs", headers={"X-Auth-Token": "tok"}).status_code == 200
    assert client.get("/api/v1/setup/step", headers={"X-Auth-Token": "tok"}).status_code == 200


# ------------------------------------------------------------------ evidence transitions

def test_eight_steps_and_evidence_drives_every_transition(wizard, monkeypatch) -> None:
    client, path, tmp_path = wizard
    initial = client.get("/api/v1/setup/step").json()
    assert [step["id"] for step in initial["steps"]] == [
        "keys", "models", "prepare", "params", "wake", "connect", "test", "verify"]
    assert initial["next_step"] == "keys"
    assert initial["complete"] is False and initial["verified"] is False
    assert all(step["status"] == "pending" for step in initial["steps"])

    # keys: secrets are present, never echoed. The only ASR is a local sherpa, which
    # is keyless but stays unusable until its model files exist (models step).
    saved = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save", "payload": {"values": KEYS}})
    assert saved.status_code == 200, saved.text
    assert not any(value in saved.text for value in KEYS.values())
    assert _step(saved.json(), "keys")["status"] == "pending"

    # models: files on disk, not a claim; they also make the local ASR usable.
    _make_models(tmp_path)
    state = client.get("/api/v1/setup/step").json()
    assert _step(state, "models")["status"] == "done"
    assert _step(state, "keys")["status"] == "done"

    # prepare: persisted confirmation.
    assert client.post("/api/v1/setup/step", json={
        "step": "prepare", "action": "manual.confirm", "payload": {"confirmed": True},
    }).status_code == 200
    state = client.get("/api/v1/setup/step").json()
    assert _step(state, "prepare")["status"] == "done"

    # params + wake: one device write creates the config entry and keywords.txt.
    response = client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"id": "living", "host": "speaker.local", "room": "客厅",
                    "wake_words": ["你好小瑞"], "params": {"volume_max": 100}},
    })
    assert response.status_code == 200, response.text
    state = response.json()
    assert _step(state, "params")["status"] == "done"
    assert _step(state, "wake")["status"] == "done"
    device = load_config(path).devices[0]
    assert device.params == {"plug_id": "xiaoai-lx06", "volume_max": 100}
    assert "@你好小瑞" in (path.parent / "keywords.txt").read_text(encoding="utf-8")

    # connect/test: the live plane is the only connection evidence.
    client.app.state.audio = _Plane(connected=True)
    state = client.get("/api/v1/setup/step").json()
    assert _step(state, "connect")["status"] == "done"
    assert _step(state, "test")["status"] == "done"

    # verify: a turn after setup started.
    client.app.state.last_turn = {"transcript": "现在几点", "reply": "三点",
                                  "audio_bytes": 480, "device_id": "living"}
    state = client.get("/api/v1/setup/step").json()
    assert _step(state, "verify")["status"] == "done"
    assert state["verified"] is True
    assert state["complete"] is True and state["next_step"] is None


def test_a_turn_before_setup_start_is_not_verify_evidence(wizard) -> None:
    client, _path, _tmp = wizard
    client.get("/api/v1/setup/step")       # records _meta.started_at
    old = {"audio_bytes": 480, "ts_epoch": 1.0}
    client.app.state.recent_turns = __import__("collections").deque([old])
    client.app.state.last_turn = old
    state = client.get("/api/v1/setup/step").json()
    assert _step(state, "verify")["status"] == "pending"
    assert state["verified"] is False


def test_plane_error_marks_params_and_wake_as_error(wizard) -> None:
    client, _path, _tmp = wizard
    client.app.state.audio = _Plane(connected=True, fail_start=True)
    response = client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"host": "speaker.local", "wake_words": ["你好小瑞"]},
    })
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["plane_error"]
    assert _step(state, "params")["status"] == "error"
    assert _step(state, "wake")["status"] == "error"


# ------------------------------------------------------------------ actions and errors

def test_unknown_step_and_action_are_rejected_with_an_object(wizard) -> None:
    client, _path, _tmp = wizard
    unknown = client.post("/api/v1/setup/step",
                          json={"step": "nope", "action": "keys.save"})
    assert unknown.status_code == 400
    detail = unknown.json()["detail"]
    assert detail["code"] == "unknown_step" and detail["message"]

    rejected = client.post("/api/v1/setup/step",
                           json={"step": "keys", "action": "wake.set"})
    assert rejected.status_code == 400
    detail = rejected.json()["detail"]
    assert detail["code"] == "unknown_action"
    assert detail["details"]["step"] == "keys"
    assert detail["details"]["allowed"] == [
        "keys.save", "keys.test", "providers.save"]


def test_legacy_validators_become_object_errors_on_the_new_endpoint(wizard) -> None:
    client, _path, _tmp = wizard
    for payload in (
        {"id": "a/b", "host": "speaker.local"},
        {"host": "speaker.local", "wake_words": ["你好\n小瑞"]},
        {"host": "has space"},
    ):
        response = client.post("/api/v1/setup/step", json={
            "step": "params", "action": "device.upsert", "payload": payload})
        assert response.status_code == 422, (payload, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_values"
        assert detail["message"]


def test_step_in_progress_is_409(wizard) -> None:
    client, _path, _tmp = wizard

    class Locked:
        def locked(self) -> bool:
            return True

    client.app.state.setup_locks["keys"] = Locked()
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save", "payload": {"values": KEYS}})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "step_in_progress"


def test_try_run_without_a_plane_is_a_structured_409(wizard) -> None:
    client, _path, _tmp = wizard
    response = client.post("/api/v1/setup/step", json={
        "step": "verify", "action": "try.run", "payload": {"text": "现在几点？"}})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "audio_plane_unavailable"
    assert detail["message"]


def test_device_test_writes_evidence_without_touching_config(wizard, monkeypatch) -> None:
    client, path, _tmp = wizard
    client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"host": "speaker.local", "wake_words": ["你好小瑞"]}})
    before = path.read_bytes()

    async def fake_probe(host: str) -> dict:
        return {"host": host, "usable": True,
                "ssh": {"port_open": True, "banner": True}, "hint": ""}

    monkeypatch.setattr(app_module, "_probe_host", fake_probe)
    client.app.state.audio = _Plane(connected=True)
    response = client.post("/api/v1/setup/step", json={
        "step": "test", "action": "device.test", "payload": {}})
    assert response.status_code == 200, response.text
    assert _step(response.json(), "test")["status"] == "done"
    assert path.read_bytes() == before
    setup_file = _tmp / "data" / "setup.json"
    assert '"test"' in setup_file.read_text(encoding="utf-8")


# ------------------------------------------------------------------ setup.json and params

def test_setup_json_is_0600_and_reposting_the_same_confirm_is_a_noop(wizard) -> None:
    client, _path, tmp_path = wizard
    payload = {"step": "prepare", "action": "manual.confirm",
               "payload": {"confirmed": True}}
    assert client.post("/api/v1/setup/step", json=payload).status_code == 200
    setup_file = tmp_path / "data" / "setup.json"
    assert setup_file.is_file()
    assert stat.S_IMODE(setup_file.stat().st_mode) == 0o600
    first = setup_file.read_bytes()
    stamp = setup_file.stat().st_mtime_ns

    assert client.post("/api/v1/setup/step", json=payload).status_code == 200
    assert setup_file.read_bytes() == first
    assert setup_file.stat().st_mtime_ns == stamp


def test_param_round_trip_and_device_upsert_idempotency(wizard) -> None:
    client, path, _tmp = wizard
    payload = {
        "step": "params", "action": "device.upsert",
        "payload": {"id": "living", "host": "speaker.local", "room": "客厅",
                    "wake_words": ["你好小瑞"],
                    "params": {"volume_max": 100, "mic_gain_db": 18, "plug_id": "ignored"}},
    }
    assert client.post("/api/v1/setup/step", json=payload).status_code == 200
    device = load_config(path).devices[0]
    assert device.id == "living"
    assert device.params == {
        "plug_id": "xiaoai-lx06", "volume_max": 100, "mic_gain_db": 18}
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns

    response = client.post("/api/v1/setup/step", json=payload)
    assert response.status_code == 200
    assert len(load_config(path).devices) == 1
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp


def test_device_params_survive_a_config_save_round_trip(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    save_config(path, Config(devices=[DeviceConfig(
        id="living", host="speaker.local", params={"plug_id": "xiaoai-lx06", "volume_max": 80})]))
    loaded = load_config(path)
    assert loaded.devices[0].params == {"plug_id": "xiaoai-lx06", "volume_max": 80}


def test_keys_save_is_write_only_and_idempotent(wizard) -> None:
    client, _path, tmp_path = wizard
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save", "payload": {"values": KEYS}})
    assert response.status_code == 200
    assert not any(value in response.text for value in KEYS.values())
    secrets_file = tmp_path / "data" / "secrets.yaml"
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert secrets_module.resolve("RUI_LLM_KEY").value == KEYS["RUI_LLM_KEY"]
    before = secrets_file.read_bytes()
    stamp = secrets_file.stat().st_mtime_ns
    assert client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": KEYS}}).status_code == 200
    assert secrets_file.read_bytes() == before
    assert secrets_file.stat().st_mtime_ns == stamp


# ---------------------------------------------------------------- provider keys (issue #5)

PROVIDER_SECRETS = {
    "RUI_LLM_KEY": "sk-llm-catalog-should-not-echo",
    "VOLC_API_KEY": "volc-key-catalog-should-not-echo",
    "XFYUN_APPID": "appid-catalog-should-not-echo",
    "XFYUN_API_KEY": "xfyun-key-catalog-should-not-echo",
    "XFYUN_API_SECRET": "xfyun-secret-catalog-should-not-echo",
}


def _provider_client(tmp_path, monkeypatch):
    """A config with the real vendor shapes: DeepSeek, 火山 ASR/TTS, 讯飞 TTS, local ASR."""
    for name in (*PROVIDER_SECRETS, "RUI_DEVICE_TOKEN", "DEEPSEEK_API_KEY",
                 "VOLC_ACCESS_KEY_ID", "VOLC_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.providers.catalog = {
        "local_asr": ProviderConfig(kind="sherpa", model=str(tmp_path / "asr")),
        "volc_asr": ProviderConfig(
            kind="volc_asr", model="volc.seedasr.sauc.duration",
            api_key_env="VOLC_API_KEY"),
        "volc_tts": ProviderConfig(
            kind="volc_tts", model="seed-tts-2.0",
            voice="zh_female_xiaohe_uranus_bigtts", api_key_env="VOLC_API_KEY"),
        "xfyun_tts": ProviderConfig(
            kind="xfyun", voice="x4_lingxiaowan_boy",
            app_id_env="XFYUN_APPID", api_key_env="XFYUN_API_KEY",
            api_secret_env="XFYUN_API_SECRET"),
        "deepseek_llm": ProviderConfig(
            kind="openai_compat", base_url="https://api.deepseek.com",
            model="deepseek-chat", api_key_env="RUI_LLM_KEY"),
    }
    cfg.providers.asr = StageConfig(chain=["volc_asr", "local_asr"])
    cfg.providers.tts = StageConfig(chain=["xfyun_tts", "volc_tts"])
    cfg.providers.llm = StageConfig(chain=["deepseek_llm"])
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), path, tmp_path


def test_setup_providers_catalog_shape_state_and_no_values(tmp_path, monkeypatch) -> None:
    client, _path, _tmp = _provider_client(tmp_path, monkeypatch)
    response = client.get("/api/v1/setup/providers")
    assert response.status_code == 200, response.text
    body = response.json()
    rows = {(row["stage"], row["name"]): row for row in body["providers"]}
    assert set(rows) == {
        ("asr", "volc_asr"), ("asr", "local_asr"),
        ("tts", "xfyun_tts"), ("tts", "volc_tts"), ("llm", "deepseek_llm"),
    }
    assert body["stages"]["llm"] == ["llm:deepseek_llm"]

    volc_asr = rows[("asr", "volc_asr")]
    assert volc_asr["display_name"] == "火山引擎"
    assert volc_asr["display_name_en"] == "Volcano Engine"
    assert volc_asr["configured"] is False
    assert volc_asr["missing"] == ["VOLC_API_KEY"]
    assert volc_asr["docs_url"].startswith("https://docs.volcengine.com/")
    field = volc_asr["fields"][0]
    assert (field["name"], field["role"], field["label"]) == (
        "VOLC_API_KEY", "api_key", "API Key")
    assert field["configured"] is False
    assert field["hint"] and field["hint_i18n"]["zh"]
    assert field["docs_url"].startswith("https://docs.volcengine.com/")

    # The same key serves ASR and TTS, but the UI must show both uses separately.
    volc_tts = rows[("tts", "volc_tts")]
    assert volc_tts["fields"][0]["name"] == "VOLC_API_KEY"
    assert [f["label"] for f in rows[("tts", "xfyun_tts")]["fields"]] == [
        "API Key", "AppID", "API Secret"]
    assert rows[("asr", "local_asr")]["fields"] == []
    assert rows[("asr", "local_asr")]["configured"] is True
    assert rows[("llm", "deepseek_llm")]["display_name"] == "DeepSeek"

    saved = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": PROVIDER_SECRETS}})
    assert saved.status_code == 200, saved.text
    assert not any(value in saved.text for value in PROVIDER_SECRETS.values())
    catalog = client.get("/api/v1/setup/providers")
    assert not any(value in catalog.text for value in PROVIDER_SECRETS.values())
    rows = {(row["stage"], row["name"]): row
            for row in catalog.json()["providers"]}
    assert all(row["configured"] for row in rows.values())

    state = client.get("/api/v1/setup/step").json()
    keys_step = _step(state, "keys")
    assert keys_step["status"] == "done"
    assert keys_step["evidence"]["stages"] == {"asr": True, "tts": True, "llm": True}
    assert {row["name"] for row in keys_step["evidence"]["providers"]} == {
        "volc_asr", "local_asr", "xfyun_tts", "volc_tts", "deepseek_llm"}


def test_providers_table_has_selection_voices_and_required_flags(tmp_path, monkeypatch) -> None:
    client, _path, _tmp = _provider_client(tmp_path, monkeypatch)
    body = client.get("/api/v1/setup/providers").json()
    stage_state = body["stage_state"]
    assert stage_state["asr"]["selected"] == "volc_asr"
    assert stage_state["asr"]["selected_id"] == "asr:volc_asr"
    assert stage_state["asr"]["chain"] == ["volc_asr", "local_asr"]
    assert stage_state["tts"]["selected"] == "xfyun_tts"
    assert stage_state["llm"]["selected"] == "deepseek_llm"
    assert all(item["required"] is True for item in stage_state.values())
    assert "volc_tts" in stage_state["tts"]["providers"]

    rows = {(row["stage"], row["name"]): row for row in body["providers"]}
    assert rows[("asr", "volc_asr")]["selected"] is True
    assert rows[("asr", "local_asr")]["selected"] is False
    assert rows[("asr", "volc_asr")]["required"] is True
    assert rows[("asr", "volc_asr")]["voices"] == []
    assert rows[("asr", "volc_asr")]["default_voice"] == ""

    volc_tts = rows[("tts", "volc_tts")]
    assert volc_tts["default_voice"] == "zh_female_xiaohe_uranus_bigtts"
    assert {voice["id"] for voice in volc_tts["voices"]} >= {
        "zh_female_xiaohe_uranus_bigtts", "zh_female_vv_uranus_bigtts"}
    for voice in volc_tts["voices"]:
        assert set(voice) >= {"id", "name", "language", "default"}
        assert voice["language"] == "zh-CN"
    assert [voice["id"] for voice in volc_tts["voices"] if voice["default"]] == [
        volc_tts["default_voice"]]

    # 讯飞 has no curated subset here, so its configured vcn is the one option.
    xfyun = rows[("tts", "xfyun_tts")]
    assert [voice["id"] for voice in xfyun["voices"]] == ["x4_lingxiaowan_boy"]
    assert xfyun["default_voice"] == "x4_lingxiaowan_boy"


def test_providers_table_offers_a_catalog_provider_not_in_the_chain(tmp_path, monkeypatch) -> None:
    for name in (*PROVIDER_SECRETS, "RUI_DEVICE_TOKEN", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.providers.catalog = {
        "local_asr": ProviderConfig(kind="sherpa", model=str(tmp_path / "asr")),
        "volc_asr": ProviderConfig(kind="volc_asr", api_key_env="VOLC_API_KEY"),
        "deepseek_llm": ProviderConfig(
            kind="openai_compat", base_url="https://api.deepseek.com",
            model="deepseek-chat", api_key_env="RUI_LLM_KEY"),
    }
    cfg.providers.asr = StageConfig(chain=["local_asr"])
    cfg.providers.llm = StageConfig(chain=["deepseek_llm"])
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    client = TestClient(create_app(cfg, config_path=path))
    body = client.get("/api/v1/setup/providers").json()
    rows = {(row["stage"], row["name"]): row for row in body["providers"]}
    # Not in the asr chain, but the kind and name identify it as an ASR provider.
    assert ("asr", "volc_asr") in rows
    assert rows[("asr", "volc_asr")]["selected"] is False
    assert rows[("asr", "local_asr")]["selected"] is True
    assert body["stage_state"]["asr"]["providers"] == ["local_asr", "volc_asr"]


def test_providers_save_writes_chain_voice_and_keeps_other_config(tmp_path, monkeypatch) -> None:
    client, path, _tmp = _provider_client(tmp_path, monkeypatch)
    # A value the keys step does not own must survive the provider write.
    on_disk = load_config(path)
    on_disk.assistant.name = "保留我"
    on_disk.audio.voice.vad_end_silence_ms = 777
    save_config(path, on_disk)

    payload = {
        "llm": {"provider": "deepseek_llm",
                "keys": {"RUI_LLM_KEY": "sk-save-llm-should-not-echo"}},
        "asr": {"provider": "asr:volc_asr",
                "keys": {"VOLC_API_KEY": "volc-save-asr-should-not-echo"}},
        "tts": {"provider": "volc_tts", "voice": "zh_female_vv_uranus_bigtts",
                "keys": {"VOLC_API_KEY": "volc-save-tts-should-not-echo"}},
    }
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "providers.save", "payload": payload})
    assert response.status_code == 200, response.text
    assert not any(value in response.text
                   for stage in payload.values() for value in stage["keys"].values())

    configured = load_config(path)
    # Selected first, existing fallbacks kept after it (asr already had volc_asr first).
    assert configured.providers.asr.chain == ["volc_asr", "local_asr"]
    assert configured.providers.tts.chain == ["volc_tts", "xfyun_tts"]
    assert configured.providers.llm.chain == ["deepseek_llm"]
    assert configured.providers.catalog["volc_tts"].voice == "zh_female_vv_uranus_bigtts"
    assert configured.assistant.name == "保留我"
    assert configured.audio.voice.vad_end_silence_ms == 777
    assert secrets_module.resolve("RUI_LLM_KEY").value == \
        "sk-save-llm-should-not-echo"
    assert secrets_module.resolve("VOLC_API_KEY").value == \
        "volc-save-tts-should-not-echo"
    config_text = path.read_text(encoding="utf-8")
    assert "sk-save-llm-should-not-echo" not in config_text
    assert "volc-save-tts-should-not-echo" not in config_text

    state = response.json()
    keys_step = _step(state, "keys")
    assert keys_step["status"] == "done"
    assert keys_step["evidence"]["selected"] == {
        "asr": "volc_asr", "tts": "volc_tts", "llm": "deepseek_llm"}
    assert keys_step["evidence"]["missing"] == []
    catalog = client.get("/api/v1/setup/providers").json()
    rows = {(row["stage"], row["name"]): row for row in catalog["providers"]}
    assert rows[("tts", "volc_tts")]["selected"] is True
    assert rows[("tts", "volc_tts")]["default_voice"] == "zh_female_vv_uranus_bigtts"
    assert rows[("tts", "xfyun_tts")]["configured"] is False


def test_providers_save_rejects_unknown_wrong_stage_and_foreign_keys(tmp_path, monkeypatch) -> None:
    client, path, _tmp = _provider_client(tmp_path, monkeypatch)
    before = path.read_bytes()
    for payload in (
        {"asr": {"provider": "deepseek_llm"}},
        {"tts": {"provider": "local_asr"}},
        {"llm": {"provider": "not-a-provider"}},
    ):
        response = client.post("/api/v1/setup/step", json={
            "step": "keys", "action": "providers.save", "payload": payload})
        assert response.status_code == 422, (payload, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == "unknown_provider"
        assert detail["details"]["stage"] in {"asr", "tts", "llm"}

    foreign_key = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "providers.save",
        "payload": {"llm": {"provider": "deepseek_llm",
                            "keys": {"VOLC_API_KEY": "not-this-provider"}}}})
    assert foreign_key.status_code == 422
    assert foreign_key.json()["detail"]["code"] == "invalid_values"
    assert path.read_bytes() == before


def test_keys_step_is_satisfied_by_any_provider_in_a_fallback_chain(
        tmp_path, monkeypatch) -> None:
    client, path, _tmp = _provider_client(tmp_path, monkeypatch)
    # Only the fallback TTS provider (火山) has a key; the primary (讯飞) is missing.
    client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": {
            "RUI_LLM_KEY": "sk-selected-llm",
            "VOLC_API_KEY": "volc-selected-key",
        }}})
    state = client.get("/api/v1/setup/step").json()
    keys_step = _step(state, "keys")
    assert keys_step["status"] == "done"
    assert keys_step["evidence"]["stages"] == {"asr": True, "tts": True, "llm": True}
    assert keys_step["evidence"]["missing"] == []
    # The usable fallback is what the table shows, not the unusable chain[0].
    assert keys_step["evidence"]["selected"] == {
        "asr": "volc_asr", "tts": "volc_tts", "llm": "deepseek_llm"}
    catalog = client.get("/api/v1/setup/providers").json()
    assert catalog["stage_state"]["tts"]["selected"] == "volc_tts"
    assert catalog["stage_state"]["tts"]["chain"] == ["xfyun_tts", "volc_tts"]
    rows = {(row["stage"], row["name"]): row for row in catalog["providers"]}
    assert rows[("tts", "volc_tts")]["selected"] is True
    assert rows[("tts", "xfyun_tts")]["selected"] is False
    assert rows[("tts", "xfyun_tts")]["configured"] is False

    # Selecting the provider whose key is present makes the intent explicit; the
    # unconfigured 讯飞 fallback no longer blocks the step.
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "providers.save",
        "payload": {"tts": {"provider": "volc_tts",
                            "keys": {"VOLC_API_KEY": "volc-selected-key"}}}})
    assert response.status_code == 200, response.text
    keys_step = _step(response.json(), "keys")
    assert keys_step["status"] == "done"
    # The selected provider moved first; the unconfigured 讯飞 entry is preserved as a
    # fallback instead of being silently dropped by the save.
    assert load_config(path).providers.tts.chain == ["volc_tts", "xfyun_tts"]
    assert keys_step["evidence"]["chains"]["tts"] == ["volc_tts", "xfyun_tts"]


def test_providers_save_keeps_previous_fallbacks_across_saves(
        tmp_path, monkeypatch) -> None:
    """Selecting a new primary must not throw away the one it replaces.

    The wizard is re-runnable; a save used to write `chain=[selected]`, so the working
    fallback silently disappeared after the second run. The order is now "selected first,
    the rest in their previous order", deduped.
    """
    client, path, _tmp = _provider_client(tmp_path, monkeypatch)
    first = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "providers.save",
        "payload": {"tts": {"provider": "volc_tts",
                            "keys": {"VOLC_API_KEY": "volc-key-for-tts"}}}})
    assert first.status_code == 200, first.text
    assert load_config(path).providers.tts.chain == ["volc_tts", "xfyun_tts"]

    second = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "providers.save",
        "payload": {"tts": {
            "provider": "xfyun_tts",
            "keys": {
                "XFYUN_APPID": "xfyun-appid",
                "XFYUN_API_KEY": "xfyun-key",
                "XFYUN_API_SECRET": "xfyun-secret",
            },
        }}})
    assert second.status_code == 200, second.text
    configured = load_config(path)
    assert configured.providers.tts.chain == ["xfyun_tts", "volc_tts"]

    body = second.json()
    keys_step = _step(body, "keys")
    assert keys_step["evidence"]["chains"]["tts"] == ["xfyun_tts", "volc_tts"]
    stage_state = keys_step["evidence"]["stage_state"]["tts"]
    assert stage_state["ready"] == ["xfyun_tts", "volc_tts"]
    assert stage_state["not_ready"] == []

    rows = {(row["stage"], row["name"]): row
            for row in client.get("/api/v1/setup/providers").json()["providers"]}
    assert rows[("tts", "xfyun_tts")]["ready"] is True
    assert rows[("tts", "volc_tts")]["ready"] is True
    assert rows[("tts", "xfyun_tts")]["ready_reason"] == ""


def test_providers_endpoint_badges_a_missing_provider_as_not_ready(
        tmp_path, monkeypatch) -> None:
    client, _path, _tmp = _provider_client(tmp_path, monkeypatch)
    body = client.get("/api/v1/setup/providers").json()
    rows = {(row["stage"], row["name"]): row for row in body["providers"]}
    assert rows[("tts", "xfyun_tts")]["ready"] is False
    assert rows[("tts", "xfyun_tts")]["ready_reason"] == "missing_key"
    assert body["stage_state"]["tts"]["ready"] == []
    assert body["stage_state"]["tts"]["not_ready"] == ["xfyun_tts", "volc_tts"]


def test_keys_step_local_sherpa_fallback_is_usable_without_a_cloud_key(
        tmp_path, monkeypatch) -> None:
    client, _path, _tmp = _provider_client(tmp_path, monkeypatch)
    _make_local_asr(tmp_path)
    client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": {"RUI_LLM_KEY": "sk-local-fallback-llm"}}})
    state = client.get("/api/v1/setup/step").json()
    keys_step = _step(state, "keys")
    assert keys_step["evidence"]["stages"]["asr"] is True
    assert keys_step["evidence"]["selected"]["asr"] == "local_asr"
    stages = client.get("/api/v1/setup/providers").json()["stage_state"]
    assert stages["asr"]["selected"] == "local_asr"
    assert stages["asr"]["chain"] == ["volc_asr", "local_asr"]
    # TTS still needs one provider: no fallback key is configured, so it stays pending
    # and the primary (讯飞) remains the shown selection.
    assert keys_step["evidence"]["stages"]["tts"] is False
    assert keys_step["evidence"]["selected"]["tts"] == "xfyun_tts"
    assert {"XFYUN_API_KEY", "VOLC_API_KEY"} <= set(keys_step["evidence"]["missing"])


def test_setup_providers_endpoint_requires_the_console_token(tmp_path, monkeypatch) -> None:
    for name in PROVIDER_SECRETS:
        monkeypatch.delenv(name, raising=False)
    cfg = Config(web={"auth_token": "tok"})
    cfg.system.data_dir = str(tmp_path / "data")
    client = TestClient(create_app(cfg))
    assert client.get("/api/v1/setup/providers").status_code == 401
    assert client.get("/api/v1/setup/providers",
                      headers={"X-Auth-Token": "tok"}).status_code == 200


def test_keys_test_llm_success_failure_and_no_secret_leak(tmp_path, monkeypatch) -> None:
    client, _path, _tmp = _provider_client(tmp_path, monkeypatch)
    client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": {"RUI_LLM_KEY": PROVIDER_SECRETS["RUI_LLM_KEY"]}}})

    async def ok_probe(entry, values):
        assert values["api_key"] == PROVIDER_SECRETS["RUI_LLM_KEY"]
        return {"ok": True, "check": "models", "status": 200,
                "message": "models endpoint accepted the key"}

    monkeypatch.setattr(keycheck, "_probe_llm", ok_probe)
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "llm", "provider": "deepseek_llm"}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["stage"] == "llm"
    assert body["provider"] == "deepseek_llm"
    assert body["message"] and body["details"]["check"] == "models"
    assert not any(value in response.text for value in PROVIDER_SECRETS.values())

    async def fail_probe(entry, values):
        return {"ok": False, "check": "models", "status": 401,
                "message": "provider rejected the key (HTTP 401)"}

    monkeypatch.setattr(keycheck, "_probe_llm", fail_probe)
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test", "payload": {"stage": "llm"}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False and body["provider"] == "deepseek_llm"
    assert "401" in body["message"]
    assert not any(value in response.text for value in PROVIDER_SECRETS.values())


def test_keys_test_asr_tts_missing_no_safe_test_and_dispatch(tmp_path, monkeypatch) -> None:
    client, _path, _tmp = _provider_client(tmp_path, monkeypatch)

    # No ASR key yet: a clear missing-credential result, not a network call.
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test", "payload": {"stage": "asr"}})
    body = response.json()
    assert response.status_code == 200 and body["ok"] is False
    assert "VOLC_API_KEY" in body["message"]
    assert body["details"]["check"] == "none"

    # A local/keyless provider is green only when its model files exist; the check
    # never pretends to make an online call.
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "asr", "provider": "local_asr"}})
    body = response.json()
    assert response.status_code == 200 and body["ok"] is False
    assert "local model files are missing" in body["message"]
    _make_local_asr(tmp_path)
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "asr", "provider": "local_asr"}})
    body = response.json()
    assert response.status_code == 200, body
    assert body["ok"] is True and body["provider"] == "local_asr"
    assert "local model" in body["message"]
    assert body["details"]["check"] == "local_model"
    assert body["details"]["models_ready"] is True

    # With a key, ASR/TTS dispatch to their stage probe (not to the LLM probe).
    client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": {
            "VOLC_API_KEY": PROVIDER_SECRETS["VOLC_API_KEY"],
            "XFYUN_APPID": PROVIDER_SECRETS["XFYUN_APPID"],
            "XFYUN_API_KEY": PROVIDER_SECRETS["XFYUN_API_KEY"],
            "XFYUN_API_SECRET": PROVIDER_SECRETS["XFYUN_API_SECRET"],
        }}})

    calls: list[tuple[str, str]] = []

    async def asr_probe(entry, values):
        calls.append(("asr", entry.kind))
        return {"ok": True, "check": "asr", "status": 200,
                "message": "asr endpoint accepted the key"}

    async def tts_probe(entry, values):
        calls.append(("tts", entry.kind))
        return {"ok": True, "check": "synthesis", "status": 200,
                "message": "tts endpoint returned audio"}

    monkeypatch.setattr(keycheck, "_probe_asr", asr_probe)
    monkeypatch.setattr(keycheck, "_probe_tts", tts_probe)

    asr = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test", "payload": {"stage": "asr"}}).json()
    tts = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test", "payload": {"stage": "tts"}}).json()
    assert asr["ok"] is True and asr["provider"] == "volc_asr"
    assert tts["ok"] is True and tts["provider"] == "xfyun_tts"
    assert calls == [("asr", "volc_asr"), ("tts", "xfyun")]

    unknown = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "asr", "provider": "deepseek_llm"}})
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["code"] == "unknown_provider"
    bad_stage = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test", "payload": {"stage": "vision"}})
    assert bad_stage.status_code == 422
    assert bad_stage.json()["detail"]["code"] == "invalid_values"


def test_keys_test_accepts_unsaved_values_without_storing_or_echoing(
        tmp_path, monkeypatch) -> None:
    client, _path, tmp_path = _provider_client(tmp_path, monkeypatch)
    ephemeral = "sk-ephemeral-should-not-be-stored"
    seen: list[str] = []

    async def ok_probe(entry, values):
        seen.append(values["api_key"])
        return {"ok": True, "check": "models", "status": 200,
                "message": "models endpoint accepted the key"}

    monkeypatch.setattr(keycheck, "_probe_llm", ok_probe)
    response = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "llm", "provider": "deepseek_llm",
                    "keys": {"RUI_LLM_KEY": ephemeral}}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["provider"] == "deepseek_llm"
    assert "not saved" in body["message"]
    assert body["details"]["ephemeral"] is True and body["details"]["saved"] is False
    assert seen == [ephemeral]
    assert ephemeral not in response.text
    assert secrets_module.resolve("RUI_LLM_KEY") is None
    assert not (tmp_path / "data" / "secrets.yaml").exists()

    # A value for another provider's credential is a clear 400, never stored.
    foreign = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "llm", "provider": "deepseek_llm",
                    "keys": {"VOLC_API_KEY": ephemeral}}})
    assert foreign.status_code == 400
    assert foreign.json()["detail"]["code"] == "invalid_values"
    assert ephemeral not in foreign.text

    # The saved-only path still works and is not marked ephemeral.
    client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": {"RUI_LLM_KEY": "sk-saved-llm"}}})
    saved = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.test",
        "payload": {"stage": "llm", "provider": "deepseek_llm"}}).json()
    assert saved["ok"] is True and "ephemeral" not in saved["details"]


def test_device_upsert_accepts_top_level_controls_and_validates_ranges(wizard) -> None:
    client, path, _tmp = wizard
    response = client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"id": "living", "host": "speaker.local", "room": "客厅",
                    "wake_words": ["你好小瑞"], "volume": 35, "led_enabled": False,
                    "listen_port": 5555, "mic_gain_db": 12.5}})
    assert response.status_code == 200, response.text
    device = load_config(path).devices[0]
    assert device.volume == 35
    assert device.led.enabled is False
    assert device.listen_port == 5555
    assert device.params["mic_gain_db"] == 12.5
    assert device.params["plug_id"] == "xiaoai-lx06"
    # The response lists param keys but never their values.
    params_detail = _step(response.json(), "params")["detail"]
    assert "params" in params_detail and "mic_gain_db" in params_detail["params"]

    # A later save that only changes the room keeps the controls (existing merged).
    assert client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"room": "卧室"}}).status_code == 200
    kept = load_config(path).devices[0]
    assert (kept.room, kept.volume, kept.led.enabled, kept.listen_port) == (
        "卧室", 35, False, 5555)
    assert kept.params["mic_gain_db"] == 12.5

    before = path.read_bytes()
    for controls in ({"volume": 101}, {"volume": True}, {"led_enabled": "yes"},
                     {"listen_port": 0}, {"mic_gain_db": 99}):
        rejected = client.post("/api/v1/setup/step", json={
            "step": "params", "action": "device.upsert", "payload": controls})
        assert rejected.status_code == 422, (controls, rejected.text)
        assert rejected.json()["detail"]["code"] == "invalid_values"
    assert path.read_bytes() == before


def test_device_test_ssh_only_is_not_green_for_an_inbound_speaker(
        wizard, monkeypatch) -> None:
    client, _path, tmp_path = wizard
    client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"host": "speaker.local", "wake_words": ["你好小瑞"]}})

    async def fake_probe(host: str) -> dict:
        return {"host": host, "usable": True,
                "ssh": {"port_open": True, "banner": True}, "hint": ""}

    monkeypatch.setattr(app_module, "_probe_host", fake_probe)
    client.app.state.audio = _Plane(connected=False)
    state = client.post("/api/v1/setup/step", json={
        "step": "test", "action": "device.test", "payload": {}}).json()
    assert _step(state, "test")["status"] == "pending"

    stored = __import__("json").loads(
        (tmp_path / "data" / "setup.json").read_text(encoding="utf-8"))
    entry = stored["test"]["device"]["test"]
    assert entry["ok"] is False
    assert entry["connection_message"] != "connected"
    checks = {check["id"]: check for check in entry["checks"]}
    assert checks["host"]["ok"] is True and checks["host"]["status"] == "ok"
    assert checks["plane"]["ok"] is False and checks["plane"]["status"] == "fail"

    # The live inbound link (not SSH) is what turns the test green.
    client.app.state.audio = _Plane(connected=True)
    state = client.post("/api/v1/setup/step", json={
        "step": "test", "action": "device.test", "payload": {}}).json()
    assert _step(state, "test")["status"] == "done"


def test_connect_url_uses_the_server_lan_ip_when_opened_via_loopback(
        wizard, monkeypatch) -> None:
    client, _path, _tmp = wizard
    client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"host": "speaker.local", "wake_words": ["你好小瑞"]}})
    monkeypatch.setattr(app_module, "server_lan_ipv4", lambda: "192.168.5.20")
    for request_host in ("127.0.0.1:8080", "localhost:8080", "[::1]:8080",
                         "0.0.0.0:8080"):
        body = client.get("/api/v1/devices/living/connect",
                          headers={"host": request_host}).json()
        assert body["host_source"] == "server", request_host
        assert body["ws_url"].startswith("ws://192.168.5.20:4412/")
        assert body["host_hint"]["zh"] and body["host_hint"]["en"]

    # A routed/proxied host is kept, and no loopback hint is added.
    body = client.get("/api/v1/devices/living/connect",
                      headers={"host": "nas.local:8080"}).json()
    assert body["host_source"] == "request"
    assert body["ws_url"].startswith("ws://nas.local:4412/")
    assert "host_hint" not in body

    # When no LAN address can be discovered the request host is kept, with the hint.
    monkeypatch.setattr(app_module, "server_lan_ipv4", lambda: "")
    body = client.get("/api/v1/devices/living/connect",
                      headers={"host": "127.0.0.1:8080"}).json()
    assert body["host_source"] == "request"
    assert body["ws_url"].startswith("ws://127.0.0.1:4412/")
    assert "host_hint" in body


def test_connect_evidence_has_clean_fields_and_message(wizard, monkeypatch) -> None:
    client, _path, _tmp = wizard
    evidence = _step(client.get("/api/v1/setup/step").json(), "connect")["evidence"]
    assert evidence["connection_message"] == "waiting_for_client"
    assert evidence["speaker_connected"] is False
    assert evidence["listen_port"] == 4412
    assert evidence["last_seen"] is None
    assert evidence["hint"]["zh"] and evidence["hint"]["en"]
    assert {"speaker_connected", "listen_port", "last_seen", "hint"} <= set(evidence)

    # A configured device with a token but no listener is a port problem.
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "device-token")
    client.post("/api/v1/setup/step", json={
        "step": "params", "action": "device.upsert",
        "payload": {"host": "speaker.local", "wake_words": ["你好小瑞"]}})
    evidence = _step(client.get("/api/v1/setup/step").json(), "connect")["evidence"]
    assert evidence["connection_message"] == "port_blocked"

    # A live inbound link is "connected" and carries a last_seen timestamp.
    client.app.state.audio = _Plane(connected=True)
    evidence = _step(client.get("/api/v1/setup/step").json(), "connect")["evidence"]
    assert evidence["connection_message"] == "connected"
    assert evidence["speaker_connected"] is True
    assert isinstance(evidence["last_seen"], (int, float))

    # A refused client is the one signal that means token mismatch.
    plane = _Plane(connected=False)
    plane.status = lambda: {
        "running": True, "device_id": "living", "error": "",
        "metrics": {"speaker_connected": False, "speaker_rejected": 2},
    }
    client.app.state.audio = plane
    state = client.get("/api/v1/setup/step").json()
    evidence = _step(state, "connect")["evidence"]
    assert evidence["connection_message"] == "token_mismatch"
    assert _step(state, "connect")["detail"]["speaker_rejected"] == 2


def test_prepare_detail_carries_the_manifest_install_steps(wizard) -> None:
    client, _path, _tmp = wizard
    detail = _step(client.get("/api/v1/setup/step").json(), "prepare")["detail"]
    steps = detail["install_steps"]
    assert len(steps) >= 6
    joined = "\n".join(steps)
    for needle in ("DHCP", "SSH", "open-xiaoai", "ws://", "4412", "token"):
        assert needle in joined, needle
    assert "connect" in joined and ("重启" in joined or "restart" in joined)
    assert "日志" in joined or "log" in joined
    assert detail["rollback"]

    manifest = client.get("/api/v1/plugs/xiaoai-lx06").json()["manifest"]
    device_side = manifest["devices"][0]["device_side"]
    assert device_side["install_steps"] == steps
    assert device_side["rollback"] == detail["rollback"]


# ------------------------------------------------- manifest runtime defaults (dot2)

def _yaml_text(*lines: str) -> str:
    return chr(10).join(lines) + chr(10)


def _dot2_plug(tmp_path) -> None:
    """An inbound plug carrying its own runtime port/token defaults (dot2 shape)."""
    root = tmp_path / "data" / "plugs" / "dot2-echomuse"
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(_yaml_text(
        "schema: rui-plugin/v0",
        "id: dot2-echomuse",
        "name: Dot2 test plug",
        "version: 1.0.0",
        'rui_compat: ">=0.0.1 <999.0.0"',
        "license: MIT",
        "maintainer: Test <test@example.invalid>",
        "trust: trusted",
        "devices:",
        "  - devices/dot2.yaml",
    ), encoding="utf-8")
    (root / "devices" / "dot2.yaml").write_text(_yaml_text(
        "schema: rui-device/v1",
        "id: dot2",
        "name: Echo Dot 2 (test)",
        "vendor: Test",
        "class: B",
        "transport: inbound",
        'protocol: {kind: wss, version: "1"}',
        "capabilities: [playback, volume, led]",
        "device_side:",
        "  artifact: firmware",
        "  name: Test device image",
        "  source: https://example.invalid/device",
        "  rollback: Restore the stock firmware.",
        'runtime: {mode: in-process, entrypoint: "dot2_test.adapter:build", '
        "listen_port: 8870, token_env: DOT2_EM_TOKEN}",
        "params:",
        "  - {key: host, label: Address, type: string, required: true}",
    ), encoding="utf-8")


def test_connect_evidence_reports_the_manifest_token_env(tmp_path, monkeypatch) -> None:
    for name in (*KEYS, "RUI_DEVICE_TOKEN", "DOT2_EM_TOKEN", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOT2_EM_TOKEN", "dot2-token")
    _dot2_plug(tmp_path)
    cfg = Config(devices=[
        DeviceConfig(id="dot2", adapter="dot2", host="dot2.local"),
    ])
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    client = TestClient(create_app(cfg, config_path=path))

    state = client.get("/api/v1/setup/step?plug=dot2-echomuse").json()
    evidence = _step(state, "connect")["evidence"]
    assert evidence["listen_port"] == 8870
    assert evidence["token_env"] == "DOT2_EM_TOKEN"
    assert evidence["token_configured"] is True
    assert "dot2-token" not in client.get("/api/v1/setup/step?plug=dot2-echomuse").text
