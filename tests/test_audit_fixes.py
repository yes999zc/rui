"""Audit round 1 fixes (docs/audit-round-1.md) - the backend batch.

Each test pins a promise the audit found broken:

* `/health` and the device list told the truth about the speaker only by accident
  (`status: "ready"` was a literal, `audio_plane` only said the process was alive);
* the container healthcheck counted disabled devices and fell back to port 8080 when
  the config could not be read;
* the secrets page and catalog hard-coded dev variable names, so a production install
  filled `RUI_*` and nothing used it;
* saving a speaker reset a hand-tuned wake threshold back to 0.15;
* panels put audio/ack in "foundation" while the catalog called them tier 2;
* the console could not write the music credentials at all;
* memory kept duplicate rows and injected an unbounded block.
"""
from __future__ import annotations

import json
import pathlib
import random
import string

import pytest
from fastapi.testclient import TestClient

from rui.audio.kws import write_keywords_file
from rui.config import Config, DeviceConfig, load_config, save_config
from rui.config.catalog import document
from rui.config.schema import ProviderConfig
from rui.session import memory as memory_module
from rui.session.memory import MemoryStore
from rui.web.api.app import _memory_context, _redact_error, create_app

# --------------------------------------------------------------------- fixtures

class FakePlane:
    """Stands in for AudioPlaneSupervisor.status(): no subprocess, just the numbers."""

    def __init__(self, *, running: bool = True, connected: bool = False,
                 device_id: str = "", error: str = "") -> None:
        self.running = running
        self.speaker_connected = connected
        self.device_id = device_id
        self.error = error

    def status(self) -> dict:
        out: dict = {"running": self.running, "error": self.error}
        if self.device_id:
            out["device_id"] = self.device_id
        out["metrics"] = {"speaker_connected": self.speaker_connected}
        return out


def _client(tmp_path: pathlib.Path, cfg: Config | None = None) -> tuple[TestClient, pathlib.Path]:
    cfg = cfg or Config()
    cfg.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), path


def _device(*, adapter: str = "mock", device_id: str = "living", enabled: bool = True,
            host: str = "192.0.2.10") -> DeviceConfig:
    return DeviceConfig(id=device_id, adapter=adapter, host=host, room="客厅",
                        wake_words=["你好小瑞"], enabled=enabled)


# ----------------------------------------------------------- 1. honest /health

def test_health_separates_process_up_from_the_room_hearing(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [_device(), _device(device_id="kitchen", enabled=False)]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    http = TestClient(create_app(cfg, config_path=path))
    body = http.get("/health").json()
    assert body["audio_plane"] is False
    assert body["speaker_connected"] is False
    # Only the enabled device needs a plane; the disabled one is deliberate.
    assert body["enabled_devices"] == 1
    http.app.state.audio = FakePlane(running=True, connected=True, device_id="living")
    body = http.get("/health").json()
    assert body["audio_plane"] is True
    assert body["speaker_connected"] is True
    assert body["speaker_device"] == "living"
    assert body["speaker_last_seen"]  # sampled from the plane, never a placeholder


def test_device_status_is_connected_not_hardcoded_ready(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [_device(adapter="lx06")]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    http = TestClient(create_app(cfg, config_path=path))

    # No plane: the control plane cannot see the inbound socket -> unknown, not ready.
    device = http.get("/api/v1/devices").json()[0]
    assert device["status"] == "unknown"
    assert device["connected"] is None

    http.app.state.audio = FakePlane(running=True, connected=True, device_id="living")
    device = http.get("/api/v1/devices").json()[0]
    assert device["status"] == "ready"
    assert device["connected"] is True
    detail = http.get("/api/v1/devices/living").json()
    assert detail["live"]["status"] == "ready"
    assert detail["live"]["connected"] is True
    assert detail["live"]["last_seen"]

    # Speaker unplugged: disconnected, and the last-seen timestamp survives.
    http.app.state.audio.speaker_connected = False
    device = http.get("/api/v1/devices").json()[0]
    assert device["status"] == "disconnected"
    assert device["connected"] is False
    assert device["last_seen"] is not None
    detail = http.get("/api/v1/devices/living").json()
    assert detail["live"]["status"] == "disconnected"


def test_a_config_only_device_is_reported_unavailable(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [DeviceConfig(id="living", adapter="not-in-this-build",
                                host="192.0.2.10")]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    http = TestClient(create_app(cfg, config_path=path))
    device = http.get("/api/v1/devices").json()[0]
    assert device["status"] == "unavailable"
    assert device["connected"] is False
    assert "unknown adapter" in device["error"]


def test_setup_state_reports_the_speaker_link(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [_device()]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    http = TestClient(create_app(cfg, config_path=path))
    body = http.get("/api/v1/setup").json()
    assert body["speaker_connected"] is False
    assert body["enabled_devices"] == 1
    assert body["steps"][-1]["detail"]["text_tested"] is False
    http.app.state.audio = FakePlane(running=True, connected=True, device_id="living")
    body = http.get("/api/v1/setup").json()
    assert body["speaker_connected"] is True
    assert body["device"]["connected"] is True


# --------------------------------------------------------- 2. healthcheck rules

def _healthcheck_module():
    # The rules live in the installable module; the image copies the thin launcher in
    # deploy/. Assert the launcher exists so packaging cannot silently lose it.
    path = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "rui-healthcheck.py"
    assert path.is_file()
    from rui import healthcheck as module

    return module


def test_healthcheck_counts_only_enabled_devices(tmp_path, monkeypatch) -> None:
    hc = _healthcheck_module()
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [_device(), _device(device_id="kitchen", enabled=False)]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    monkeypatch.setenv("RUI_CONFIG", str(path))
    port, enabled, error = hc._config()
    assert error == ""
    assert port == 8080
    assert enabled == 1


def test_healthcheck_fails_closed_when_the_config_is_unreadable(tmp_path, monkeypatch,
                                                               capsys) -> None:
    hc = _healthcheck_module()
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("RUI_CONFIG", str(missing))
    port, enabled, error = hc._config()
    assert port is None and enabled == 0
    assert "cannot read config" in error
    # main() must report unhealthy with the reason and never probe a fallback port.
    monkeypatch.setattr(hc, "_config", lambda: (None, 0, error))
    assert hc.main() == 1
    assert "cannot read config" in capsys.readouterr().err


@pytest.mark.parametrize(("body", "enabled", "ok"), [
    ({"status": "ok", "audio_plane": False, "speaker_connected": False,
      "enabled_devices": 0}, 0, True),                     # wizard-first boot
    ({"status": "ok", "audio_plane": True, "speaker_connected": True,
      "enabled_devices": 1}, 1, True),
    ({"status": "ok", "audio_plane": False, "speaker_connected": False,
      "enabled_devices": 1}, 1, False),                    # plane dead
    ({"status": "ok", "audio_plane": True, "speaker_connected": False,
      "enabled_devices": 1}, 1, False),                    # up but room deaf
    ({"status": "degraded", "audio_plane": True, "speaker_connected": True,
      "enabled_devices": 1}, 1, False),
])
def test_healthcheck_verdict_matrix(body, enabled, ok) -> None:
    hc = _healthcheck_module()
    got, reason = hc.evaluate(body, enabled)
    assert got is ok, reason
    if not ok:
        assert reason


def test_healthcheck_falls_back_to_the_configured_count_for_old_servers() -> None:
    hc = _healthcheck_module()
    ok, reason = hc.evaluate({"status": "ok", "audio_plane": False}, configured_enabled=2)
    assert ok is False and "2 enabled" in reason


# --------------------------------------------------- 3. secrets are derived

def _production_like_config() -> Config:
    cfg = Config()
    cfg.providers.catalog["asr"].api_key_env = "RUI_ASR_KEY"
    cfg.providers.catalog["tts"].api_key_env = "RUI_TTS_KEY"
    cfg.providers.catalog["llm"].api_key_env = "RUI_LLM_KEY"
    cfg.tools.search.api_key_env = "DEEPSEEK_API_KEY"
    cfg.tools.ha.token_env = "RUI_HA_TOKEN"
    cfg.devices = [_device()]
    return cfg


def test_secret_names_come_from_the_chains_not_a_hardcoded_list(monkeypatch) -> None:
    cfg = _production_like_config()
    # A provider that no chain references must not appear: filling its key would do
    # nothing, and showing it would promise an effect it cannot have.
    cfg.providers.catalog["orphan"] = ProviderConfig(api_key_env="ORPHAN_KEY")
    monkeypatch.setenv("RUI_LLM_KEY", "sk-production-secret")
    secrets = document(cfg)["secrets"]
    assert {"RUI_ASR_KEY", "RUI_TTS_KEY", "RUI_LLM_KEY",
            "DEEPSEEK_API_KEY", "RUI_HA_TOKEN", "RUI_DEVICE_TOKEN",
            "web.auth_token"} <= set(secrets)
    assert "ORPHAN_KEY" not in secrets
    assert secrets["RUI_LLM_KEY"]["has_value"] is True
    assert secrets["RUI_ASR_KEY"]["has_value"] is False
    assert secrets["RUI_LLM_KEY"]["used_by"] == ["llm:llm"]
    assert secrets["RUI_DEVICE_TOKEN"]["used_by"] == ["device:living"]
    for entry in secrets.values():
        assert entry["hint"]["zh"] and entry["hint"]["en"]
        assert isinstance(entry["used_by"], list) and entry["used_by"]
    assert "sk-production-secret" not in json.dumps(document(cfg))


def test_secret_entries_carry_typed_categories_and_consumers() -> None:
    """The console must not guess a consumer from an env name (audit §2.3)."""
    cfg = _production_like_config()
    table = document(cfg)["secrets"]
    categories = {"provider", "search", "music", "ha", "device", "console", "other"}
    for name, entry in table.items():
        assert entry["category"] in categories, name
        assert isinstance(entry["consumers"], list) and entry["consumers"], name
        assert entry["used_by"] == sorted(entry["used_by"]), name
        for consumer in entry["consumers"]:
            assert consumer["id"] and consumer["kind"] in categories, name
            assert consumer["label"]["zh"] and consumer["label"]["en"], name
            assert consumer.get("href", "").startswith("/panel/"), name

    llm = next(item for item in table["RUI_LLM_KEY"]["consumers"]
               if item["id"] == "llm:llm")
    assert llm["kind"] == "provider" and llm["stage"] == "llm"
    assert llm["provider"] == "llm"
    assert table["RUI_DEVICE_TOKEN"]["consumers"][0]["device"] == "living"
    assert table["RUI_HA_TOKEN"]["category"] == "ha"
    assert table["web.auth_token"]["category"] == "console"
    # Music is a consumer, but its credentials have their own write-only file.
    music = table["music.credentials"]
    assert music["category"] == "music"
    assert music["writable"] is False
    assert music["href"] == "/panel/music"
    assert "music:source" in music["used_by"]


def test_the_secrets_endpoint_returns_the_typed_table(tmp_path) -> None:
    cfg = _production_like_config()
    cfg.system.data_dir = str(tmp_path / "data")
    body = TestClient(create_app(cfg)).get("/api/v1/secrets").json()
    assert "names" in body and "secrets" in body
    entry = body["secrets"]["RUI_LLM_KEY"]
    assert entry["category"] == "provider"
    assert entry["consumers"][0]["stage"] == "llm"
    assert entry["has_value"] is False
    # Presence only; there is still no endpoint that returns a value.
    assert "value" not in entry and "token" not in entry


def test_credentials_pages_do_not_guess_consumers_from_names() -> None:
    """The Phase 3 contract: typed `category`/`consumers`, no name regexes in Vue."""
    root = pathlib.Path(__file__).resolve().parents[1]
    for relative in ("web/src/views/SecretsView.vue",
                     "web/src/components/ConfigCenter.vue"):
        text = (root / relative).read_text(encoding="utf-8")
        assert "SECRET_MATCHERS" not in text, relative
        assert "kindOf(" not in text, relative
        for guessed in ("XFYUN", "SILICONFLOW", "HASS_TOKEN", "DEEPSEEK"):
            assert guessed not in text, (relative, guessed)
    secrets_view = (root / "web/src/views/SecretsView.vue").read_text(encoding="utf-8")
    config_center = (root / "web/src/components/ConfigCenter.vue").read_text(encoding="utf-8")
    assert "consumers" in secrets_view and "category" in secrets_view
    assert "SECRET_CATEGORIES" in config_center


def test_secret_names_follow_a_dev_config_too(monkeypatch) -> None:
    cfg = Config()
    cfg.providers.catalog["asr"].api_key_env = "SILICONFLOW_API_KEY"
    cfg.providers.catalog["tts"].api_key_env = "XFYUN_API_KEY"
    cfg.providers.catalog["tts"].app_id_env = "XFYUN_APPID"
    cfg.providers.catalog["tts"].api_secret_env = "XFYUN_API_SECRET"
    cfg.providers.catalog["llm"].api_key_env = "DEEPSEEK_API_KEY"
    cfg.tools.search.api_key_env = "DEEPSEEK_API_KEY"
    cfg.tools.ha.token_env = "HASS_TOKEN"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-dev-secret")
    secrets = document(cfg)["secrets"]
    assert {"SILICONFLOW_API_KEY", "XFYUN_API_KEY", "XFYUN_APPID", "XFYUN_API_SECRET",
            "DEEPSEEK_API_KEY", "HASS_TOKEN"} <= set(secrets)
    assert secrets["DEEPSEEK_API_KEY"]["used_by"] == ["llm:llm", "tool:search"]
    assert secrets["DEEPSEEK_API_KEY"]["has_value"] is True
    assert "sk-dev-secret" not in json.dumps(document(cfg))


def test_the_production_template_renders_its_own_key_names(tmp_path) -> None:
    from rui.__main__ import main

    path = tmp_path / "config.yaml"
    assert main(["setup", "--config", str(path)]) == 0
    cfg = load_config(path)
    names = set(document(cfg)["secrets"])
    # The template's actual env names, which the old hard-coded secrets page missed.
    # SiliconFlow TTS is off the template chain (2026-10-23); Volcano is the fallback,
    # so its API key replaces the old RUI_TTS_KEY entry.
    assert {"RUI_ASR_KEY", "RUI_LLM_KEY",
            "XFYUN_APPID", "XFYUN_API_KEY", "XFYUN_API_SECRET",
            "DEEPSEEK_API_KEY", "RUI_HA_TOKEN", "VOLC_API_KEY",
            "web.auth_token"} <= names


# ------------------------------------------------- 4. the wake gate is writable

def _keyword_config(tmp_path: pathlib.Path) -> tuple[Config, pathlib.Path]:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
    cfg.devices = [_device()]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    write_keywords_file(pathlib.Path(cfg.audio.voice.kws_keywords_file),
                        ["你好小瑞"], threshold=0.20, boost=3.00)
    return cfg, path


def test_the_wake_gate_is_a_catalog_field_written_into_keywords(tmp_path) -> None:
    cfg, path = _keyword_config(tmp_path)
    fields = {field["path"]: field for field in document(cfg)["fields"]}
    assert fields["audio.voice.keywords.threshold"]["kind"] == "number"
    assert fields["audio.voice.keywords.threshold"]["effect"] == "restart"
    assert fields["audio.voice.keywords.boost"]["effect"] == "restart"
    assert document(cfg)["values"]["audio.voice.keywords.threshold"] == pytest.approx(0.20)
    assert document(cfg)["values"]["audio.voice.keywords.boost"] == pytest.approx(3.00)

    http = TestClient(create_app(cfg, config_path=path))
    response = http.put("/api/v1/config/values", json={
        "values": {"audio.voice.keywords.threshold": 0.25}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == []
    assert body["restart_fields"] == ["audio.voice.keywords.threshold"]
    text = pathlib.Path(cfg.audio.voice.kws_keywords_file).read_text(encoding="utf-8")
    assert "#0.25" in text
    # The boost was not touched: only the field the request named changed.
    assert ":3.00" in text
    assert "@你好小瑞" in text


def test_a_device_save_preserves_the_hand_tuned_gate(tmp_path) -> None:
    cfg, path = _keyword_config(tmp_path)
    http = TestClient(create_app(cfg, config_path=path))
    response = http.put("/api/v1/devices/living", json={"wake_words": ["嗨小瑞"]})
    assert response.status_code == 200, response.text
    text = pathlib.Path(cfg.audio.voice.kws_keywords_file).read_text(encoding="utf-8")
    # audit #8: saving a speaker used to reset #0.20/:3.00 back to #0.15/:2.00.
    assert "#0.20" in text and ":3.00" in text
    assert "@嗨小瑞" in text


def test_a_device_save_through_the_catalog_preserves_the_gate(tmp_path) -> None:
    cfg, path = _keyword_config(tmp_path)
    http = TestClient(create_app(cfg, config_path=path))
    response = http.put("/api/v1/config/values", json={
        "values": {"devices.0.room": "书房"}})
    assert response.status_code == 200, response.text
    text = pathlib.Path(cfg.audio.voice.kws_keywords_file).read_text(encoding="utf-8")
    assert "#0.20" in text and ":3.00" in text


def test_the_wake_gate_needs_words_and_a_sane_value(tmp_path) -> None:
    http, _path = _client(tmp_path)  # no devices -> no wake words
    response = http.put("/api/v1/config/values", json={
        "values": {"audio.voice.keywords.threshold": 0.2}})
    assert response.status_code == 422
    assert "no wake words" in str(response.json()["detail"])
    cfg, path = _keyword_config(tmp_path / "gate")
    http = TestClient(create_app(cfg, config_path=path))
    response = http.put("/api/v1/config/values", json={
        "values": {"audio.voice.keywords.threshold": 5}})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["audio.voice.keywords.threshold"]


# --------------------------------------------------------- 5. panels by tier

def test_each_config_panel_owns_exactly_its_tier_groups(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.devices = [_device()]
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    http = TestClient(create_app(cfg, config_path=path))
    panels = {panel["id"]: panel for panel in http.get("/api/v1/panels").json()["panels"]}
    catalog = http.get("/api/v1/config/catalog").json()
    by_tier: dict[int, set[str]] = {}
    for group in catalog["groups"]:
        by_tier.setdefault(group["tier"], set()).add(group["id"])

    def config_groups(panel: dict, tab: str | None = None) -> set[str]:
        return {group for block in panel["blocks"]
                if block["type"] == "config"
                and (tab is None or block.get("tab") == tab)
                for group in block.get("groups", [])}

    # Phase 2: the speaker parameters moved to the 音频与唤醒 tab; Phase 4 removed
    # the duplicate `speaker` panel, so that tab is the single tier-2 home.
    assert config_groups(panels["devices"], "audio") == by_tier[2]
    # Providers moved to a dedicated page (providers-v2) but its tier-3 groups stay
    # catalogued, so every provider field keeps its single home.
    assert panels["providers"]["component"] == "providers-v2"
    assert {"providers.catalog", "providers.asr", "providers.tts",
            "providers.llm"} <= by_tier[3]
    # Tier 4 is split by task: built-in tools/exposure in the integrations tabs,
    # music on its own page, and tools.ha/tools.mcp owned by McpManager (the one
    # hot-write path) instead of a second ConfigCenter form.
    assert config_groups(panels["integrations"]) | {"music", "tools.ha", "tools.mcp"} == by_tier[4]
    assert config_groups(panels["music"]) == {"music"}
    # Tier 1 is split by meaning: conversational fields vs process foundations.
    # Phase 4 removed the `assistant-session`/`foundation` duplicates; the reply
    # tab and the system component are now the only tier-1 homes.
    assert config_groups(panels["voice-assistant"], "reply") == {"assistant", "session"}
    assert set(panels["system"]["component_args"]["groups"]) == {
        "system", "web", "security"}
    assert (config_groups(panels["voice-assistant"], "reply")
            | set(panels["system"]["component_args"]["groups"])) == by_tier[1]
    # Speaker hardware stays out of the process-foundations page.
    assert "audio.capture" not in json.dumps(panels["system"]["component_args"])


# --------------------------------------------------------- 6. music credentials

def test_music_credentials_are_write_only_and_0600(tmp_path) -> None:
    http, path = _client(tmp_path)
    credential_file = pathlib.Path(load_config(path).system.data_dir) / "lan_media.json"
    response = http.put("/api/v1/music/credentials", json={
        "url": "http://192.0.2.50:4533", "user": "bakey", "password": "s3cret-pass"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"has_credentials": True, "has_url": True, "has_user": True,
                    "has_password": True}
    assert "s3cret-pass" not in response.text
    # Never in config.yaml; the credentials file is 0600 and JSON.
    assert "s3cret-pass" not in path.read_text(encoding="utf-8")
    assert oct(credential_file.stat().st_mode & 0o777) == "0o600"
    stored = json.loads(credential_file.read_text(encoding="utf-8"))
    assert stored == {"url": "http://192.0.2.50:4533", "user": "bakey",
                      "password": "s3cret-pass"}
    catalog = http.get("/api/v1/config/catalog").json()
    assert catalog["values"]["music.has_credentials"] is True
    assert "s3cret-pass" not in json.dumps(catalog)

    # An empty field deletes just that field.
    body = http.put("/api/v1/music/credentials", json={"password": ""}).json()
    assert body["has_password"] is False and body["has_credentials"] is False
    assert "password" not in json.loads(credential_file.read_text(encoding="utf-8"))
    # Clearing everything removes the file.
    body = http.put("/api/v1/music/credentials", json={"url": "", "user": ""}).json()
    assert body["has_credentials"] is False
    assert not credential_file.exists()


def test_music_credentials_validate_the_url(tmp_path) -> None:
    http, _path = _client(tmp_path)
    response = http.put("/api/v1/music/credentials", json={"url": "192.0.2.50:4533"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["url"]
    response = http.put("/api/v1/music/credentials", json={})
    assert response.status_code == 422


def test_music_test_reports_missing_credentials_and_redacts_errors(tmp_path,
                                                                  monkeypatch) -> None:
    http, _path = _client(tmp_path)
    body = http.post("/api/v1/music/test", json={}).json()
    assert body["ok"] is False and body["latency_ms"] is None
    assert "凭据" in body["error"]

    response = http.post("/api/v1/music/test", json={
        "url": "http://127.0.0.1:9", "user": "u", "password": "topsecret"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "topsecret" not in response.text
    # The stored file was not touched by a test of unsaved values.
    assert not (pathlib.Path(load_config(_path).system.data_dir) / "lan_media.json").exists()


def test_credential_query_strings_are_redacted() -> None:
    error = RuntimeError("GET http://nas/rest/ping.view?v=1&u=bakey&p=topsecret&c=x failed")
    text = _redact_error(error)
    assert "topsecret" not in text
    assert "p=***" in text


# ---------------------------------------------------------- 7. memory dedupe

def test_add_deduplicates_and_promotes_candidates(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.json")
    first = store.add("我喜欢周杰伦", confirmed=True)
    again = store.add("我喜欢周杰伦", confirmed=True)
    assert again.id == first.id
    assert len(store.items()) == 1
    # Same fact with different punctuation / spacing is the same memory.
    similar = store.add(" 我喜欢周杰伦。 ", confirmed=True)
    assert similar.id == first.id
    assert len(store.items()) == 1

    candidate = store.extract("记住我明天要开会")
    assert candidate is not None and candidate.confirmed is False
    # The `remember` tool writes the same sentence as confirmed in the same turn.
    promoted = store.add(candidate.text, confirmed=True)
    assert promoted.id == candidate.id
    assert promoted.confirmed is True
    assert len(store.items()) == 2


def test_a_remember_turn_does_not_leave_a_candidate_and_a_confirmed_copy(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.json")
    store.extract("记住：咖啡不要糖")
    # different phrasing of the same fact, as the model's tool text often is
    store.add("咖啡不要糖", confirmed=True)
    assert len(store.items()) == 1
    assert store.items()[0].confirmed is True


def _fake_app(store: MemoryStore):
    class App:
        pass

    app = App()
    app.state = type("State", (), {"memory": store})()
    return app


def test_the_injected_memory_block_respects_the_800_char_budget(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.json")
    items = []
    for index in range(10):
        rng = random.Random(index)
        text = "".join(rng.choices(string.ascii_letters + string.digits, k=190))
        items.append(store.add(text, confirmed=True).text)
    block = _memory_context(_fake_app(store))
    body = block.removeprefix("（你记得这些事，用户说过：\n").removesuffix("）")
    lines = body.splitlines()
    assert lines, "some memory must still be injected"
    assert sum(len(line) for line in lines) <= 800
    assert len(lines) < len(items), "the budget must actually drop old entries"
    # The budget is spent on the newest facts; the oldest are the ones that fall off.
    assert any(f"- {items[-1]}" in line for line in lines)
    assert not any(f"- {items[0]}" in line for line in lines)


def test_memory_similarity_threshold_is_documented_and_used() -> None:
    assert memory_module.DUP_RATIO == 0.8
    assert memory_module._similar("我喜欢周杰伦的歌", "我喜欢周杰伦的歌曲")
    assert not memory_module._similar("今天天气很好", "把客厅灯关掉")
