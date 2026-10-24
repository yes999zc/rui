"""P2.2 backend: plug registry, eight-step evidence, and allowlisted actions.

The wizard's state must come from files, secrets and the live plane - never from a
client claim. These tests pin that, plus the write-only/0600/idempotent promises of
``data_dir/setup.json``.
"""
from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient

from house_ear import secrets as secrets_module
from house_ear.config import Config, DeviceConfig, load_config, save_config
from house_ear.config.schema import ProviderConfig
from house_ear.web.api import app as app_module
from house_ear.web.api.app import create_app

KEYS = {
    "HOUSE_EAR_LLM_KEY": "sk-llm-should-not-echo",
    "HOUSE_EAR_ASR_KEY": "sk-asr-should-not-echo",
    "HOUSE_EAR_TTS_KEY": "sk-tts-should-not-echo",
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
    for name in (*KEYS, "HOUSE_EAR_DEVICE_TOKEN", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    asr = tmp_path / "asr"
    vad = tmp_path / "silero_vad.onnx"
    kws = tmp_path / "kws"
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.providers.catalog["asr"] = ProviderConfig(kind="sherpa", model=str(asr))
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
    assert lx06["adapter"] == "xiaoai"
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
        "house_ear.device.xiaoai:build"
    assert {param["key"] for param in body["manifest"]["devices"][0]["params"]} == {
        "host", "room", "wake_words"}
    assert body["resolved"]["devices"][0]["listen_port"] == 4412
    assert body["resolved"]["devices"][0]["token_env"] == "HOUSE_EAR_DEVICE_TOKEN"

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

    # keys: secrets are present, never echoed.
    saved = client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save", "payload": {"values": KEYS}})
    assert saved.status_code == 200, saved.text
    assert not any(value in saved.text for value in KEYS.values())
    assert _step(saved.json(), "keys")["status"] == "done"

    # models: files on disk, not a claim.
    _make_models(tmp_path)
    assert _step(client.get("/api/v1/setup/step").json(), "models")["status"] == "done"

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
    assert detail["details"]["allowed"] == ["keys.save"]


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
    assert secrets_module.resolve("HOUSE_EAR_LLM_KEY").value == KEYS["HOUSE_EAR_LLM_KEY"]
    before = secrets_file.read_bytes()
    stamp = secrets_file.stat().st_mtime_ns
    assert client.post("/api/v1/setup/step", json={
        "step": "keys", "action": "keys.save",
        "payload": {"values": KEYS}}).status_code == 200
    assert secrets_file.read_bytes() == before
    assert secrets_file.stat().st_mtime_ns == stamp
