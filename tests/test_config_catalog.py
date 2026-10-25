"""The console configuration directory (docs/config-catalog-contract.md).

The console renders whatever `GET /api/v1/config/catalog` says, and writes through
`PUT /api/v1/config/values`. Those two endpoints are a contract with the frontend and a
promise to 9哥: every editable parameter has an input, a hint that says what changing it
achieves, and an honest save-vs-restart label. A field that exists here but not in the
schema, or a `save` field that does not actually take effect, is the "假旋钮" failure
this project already hit once (`audio.voice.kws_threshold`).
"""
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, load_config, save_config
from rui.config.catalog import coerce, document, field_specs
from rui.config.schema import SystemConfig
from rui.session import prompts
from rui.web.api.app import create_app

ALLOWED_KINDS = {"text", "textarea", "number", "bool", "select", "list", "secret"}
ALLOWED_EFFECTS = {"save", "restart"}


# --------------------------------------------------------------------- fixtures

def _config(tmp_path: pathlib.Path, **overrides: object) -> tuple[Config, pathlib.Path]:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        block = getattr(cfg, section)
        setattr(block, field, value)
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return cfg, path


def _app(cfg: Config, path: pathlib.Path) -> TestClient:
    return TestClient(create_app(cfg, config_path=path))


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """A console with a real config file and no provider keys in the environment."""
    for name in ("RUI_LLM_KEY", "RUI_ASR_KEY", "RUI_TTS_KEY",
                 "DEEPSEEK_API_KEY", "XFYUN_API_KEY", "XFYUN_APPID", "XFYUN_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(prompts, "_TIMEZONE", "")
    cfg, path = _config(tmp_path)
    with _app(cfg, path) as http:
        yield http, path
    prompts.set_timezone("")


# ------------------------------------------------------------------- directory

def test_the_directory_has_the_four_tiers_and_coherent_groups() -> None:
    body = document(Config())
    assert [tier["id"] for tier in body["tiers"]] == [1, 2, 3, 4]
    for tier in body["tiers"]:
        assert tier["title"]["zh"] and tier["title"]["en"]
        assert tier["hint"]["zh"] and tier["hint"]["en"]
    groups = {group["id"]: group for group in body["groups"]}
    assert len(groups) == len(body["groups"]) and len(groups) >= 15
    for group in groups.values():
        assert group["tier"] in (1, 2, 3, 4), group["id"]
        assert isinstance(group["order"], int), group["id"]
        assert group["effect"] in ALLOWED_EFFECTS, group["id"]
        assert group["title"]["zh"] and group["title"]["en"], group["id"]
        assert group["hint"]["zh"] and group["hint"]["en"], group["id"]
    for field in body["fields"]:
        assert field["group"] in groups, field["path"]
        assert field["tier"] == groups[field["group"]]["tier"], field["path"]
        assert field["kind"] in ALLOWED_KINDS, field["path"]
        assert field["effect"] in ALLOWED_EFFECTS, field["path"]
        assert isinstance(field["writable"], bool), field["path"]
        if field["writable"]:
            assert field["label"]["zh"] and field["label"]["en"], field["path"]
            # The hint answers "what does changing this achieve", not the field name.
            assert len(field["hint"]["zh"]) > len(field["label"]["zh"]), field["path"]
            assert len(field["hint"]["en"]) > len(field["label"]["en"]), field["path"]


def test_the_four_tiers_all_have_fields() -> None:
    """9哥's four classes are not decoration: each must actually carry parameters."""
    fields = document(Config())["fields"]
    by_tier = {tier: [f for f in fields if f["tier"] == tier] for tier in (1, 2, 3, 4)}
    assert all(by_tier.values())
    groups = {tier: {f["group"] for f in items} for tier, items in by_tier.items()}
    assert {"system", "web", "assistant", "session"} <= groups[1]
    assert {"audio.capture", "audio.voice", "audio.playback", "audio.aec",
            "ack", "devices"} <= groups[2]
    assert {"providers.catalog", "providers.asr", "providers.tts",
            "providers.llm"} <= groups[3]
    assert {"tools.weather", "tools.news", "tools.search", "tools.ha",
            "tools.mcp"} <= groups[4]
    assert "music" in groups[4]
    # Every field of the schema's editable blocks is represented.
    assert len(fields) >= 100


def test_the_directory_offers_an_input_for_every_editable_parameter() -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock", host="127.0.0.1",
                                       room="客厅")])
    body = document(cfg)
    paths = {field["path"] for field in body["fields"]}
    values = body["values"]
    # A representative path from every schema block - not just "the sections exist".
    required = {
        "system.data_dir", "system.log_level", "system.timezone",
        "web.host", "web.port",
        "audio.capture.rate", "audio.capture.mic_shift", "audio.capture.ref_shift",
        "audio.voice.kws_enabled", "audio.voice.vad_end_silence_ms",
        "audio.voice.wake_skip_ms", "audio.voice.barge_in", "audio.voice.vad_model_path",
        "audio.playback.rate", "audio.playback.channels", "audio.playback.credit_window",
        "audio.aec.enabled", "audio.aec.filter_len", "audio.aec.cancel",
        "ack.mode", "ack.text", "ack.chime", "ack.chime_ms", "ack.max_ms",
        "assistant.name", "assistant.persona", "assistant.system_prompt",
        "assistant.reply_max_chars", "assistant.reply_max_chars_search",
        "assistant.reply_max_chars_long", "assistant.filler_after_s",
        "assistant.filler_after_tool_s", "assistant.filler_text",
        "session.timeout_s", "session.farewell", "session.farewell_text",
        "session.farewell_text_full", "session.end_words", "session.keep_context",
        "session.history_rounds", "session.vendor_quiet_s",
        "devices", "devices.0.host", "devices.0.room", "devices.0.wake_words",
        "devices.0.volume", "devices.0.listen_port", "devices.0.led.enabled",
        "devices.0.led.gain", "devices.0.led.night_factor", "devices.0.led.fade_ms",
        "devices.0.led.colors.music", "devices.0.led.colors.music_paused",
        "providers.asr.chain", "providers.tts.chain", "providers.llm.chain",
        "tools.weather.city", "tools.weather.latitude", "tools.weather.longitude",
        "tools.weather.days_max", "tools.weather.timeout_s",
        "tools.news.enabled", "tools.news.source", "tools.news.timeout_s",
        "tools.search.enabled", "tools.search.kind", "tools.search.base_url",
        "tools.search.api_key_env", "tools.search.count", "tools.search.timeout_s",
        "tools.ha.base_url", "tools.ha.token_env", "tools.mcp_servers",
        "tools.expose", "tools.whitelist",
        "music.enabled", "music.source", "music.secrets_path", "music.cache_dir",
        "music.cache_tracks", "music.queue_min_tracks", "music.queue_batch",
        "music.history_max",
    }
    assert required <= paths
    # Every catalog path resolves to a current value (the console shows the baseline).
    assert set(values) == paths
    assert values["assistant.reply_max_chars"] == 120
    assert values["devices"][0]["id"] == "living"


def test_providers_catalog_and_the_three_chains_are_in_the_directory(tmp_path) -> None:
    cfg, _path = _config(tmp_path)
    names = list(cfg.providers.catalog)
    fields = {field["path"]: field for field in field_specs(cfg)}
    for name in names:
        for key in ("kind", "base_url", "model", "api_key_env"):
            path = f"providers.catalog.{name}.{key}"
            assert path in fields, path
            assert fields[path]["tier"] == 3
            assert fields[path]["effect"] == "restart"
    for stage in ("asr", "tts", "llm"):
        path = f"providers.{stage}.chain"
        assert path in fields, path
        assert fields[path]["kind"] == "list"
        assert fields[path]["options"] == names
        assert fields[path]["tier"] == 3


def test_effects_match_where_the_value_is_read() -> None:
    fields = {field["path"]: field for field in document(Config())["fields"]}
    # Conversational fields are rebuilt per turn / per call.
    for path in ("assistant.name", "session.timeout_s", "tools.weather.city",
                 "tools.search.count", "system.timezone"):
        assert fields[path]["effect"] == "save", path
    # The audio plane and the providers load their config once, at process start.
    for path in ("audio.playback.queue_ms", "audio.voice.vad_end_silence_ms",
                 "ack.mode", "system.data_dir", "web.port",
                 # Tools are registered at startup by these switches (audit round 2 P1-4).
                 "tools.news.enabled", "tools.search.enabled", "tools.search.kind"):
        assert fields[path]["effect"] == "restart", path
    # An env NAME decides which secret is sent where; the console must not edit it
    # (security audit round 2 P1-7).
    env_paths = [path for path in fields if path.endswith(".api_key_env")]
    for path in env_paths + ["tools.search.api_key_env", "tools.ha.token_env"]:
        assert fields[path]["writable"] is False, path
    # The ack text is a rendered file the plane re-reads, so it is a save.
    assert fields["ack.text"]["effect"] == "save"
    # The music source/paths rebuild the service at runtime (Phase 3).
    assert fields["music.source"]["effect"] == "save"
    assert fields["music.secrets_path"]["effect"] == "save"
    assert fields["music.cache_dir"]["effect"] == "save"
    # ...while enabling it assembles the layer at runtime.
    assert fields["music.enabled"]["effect"] == "save"
    # Read-only display entries exist for the pages that own their writes.
    assert fields["devices"]["writable"] is False
    assert fields["tools.mcp_servers"]["writable"] is False


# ---------------------------------------------------------------- dotted writes

def test_save_effect_fields_apply_without_a_restart(live) -> None:
    http, path = live
    response = http.put("/api/v1/config/values", json={"values": {
        "assistant.reply_max_chars": 160,
        "session.timeout_s": 30,
        "assistant.name": "小雷",
    }})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restart_required"] is False
    assert body["restart_fields"] == []
    assert set(body["applied"]) == {
        "assistant.reply_max_chars", "assistant.name", "session.timeout_s"}
    assert body["config"]["assistant"]["reply_max_chars"] == 160
    assert body["config"]["session"]["timeout_s"] == 30
    # On disk (atomic write), in the live config the API reads, and in the runner.
    assert load_config(path).assistant.reply_max_chars == 160
    assert http.get("/api/v1/config").json()["assistant"]["name"] == "小雷"
    runner = http.app.state.runner
    assert runner.assistant.reply_max_chars == 160
    assert runner.session.timeout_s == 30
    assert runner.idle.timeout_s == 30


def test_restart_effect_fields_are_reported_not_claimed(live) -> None:
    http, path = live
    response = http.put("/api/v1/config/values", json={"values": {
        "audio.playback.queue_ms": 250,
        "web.port": 8099,
    }})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restart_required"] is True
    assert body["applied"] == []
    assert set(body["restart_fields"]) == {"audio.playback.queue_ms", "web.port"}
    # Written to disk even though the running process has not reloaded it.
    saved = load_config(path)
    assert saved.audio.playback.queue_ms == 250 and saved.web.port == 8099


def test_dotted_put_rejects_bad_input_per_field(live) -> None:
    http, _path = live
    cases = [
        ({"assistant.nope": 1}, "unknown path"),
        ({"devices": []}, "read-only"),
        ({"tools.mcp_servers": []}, "read-only"),
        ({"assistant.reply_max_chars": "hundred"}, "number"),
        ({"assistant.reply_max_chars": 999}, "at most"),
        ({"audio.playback.queue_ms": 200.5}, None),          # pydantic rejects int field
        ({"ack.mode": "loud"}, "one of"),
        ({"session.end_words": ["好的", 3]}, "string"),
        ({"providers.asr.chain": ["does-not-exist"]}, "providers.asr.chain"),
        ({"system.timezone": "Mars/Olympus"}, "timezone"),
    ]
    for values, message in cases:
        response = http.put("/api/v1/config/values", json={"values": values})
        assert response.status_code == 422, (values, response.text)
        detail = response.json()["detail"]
        loc = detail[0]["loc"]
        assert loc and loc[0] != "config", (values, detail)
        if message is not None:
            assert message in str(detail), (values, detail)


def test_dotted_put_requires_a_non_empty_values_object(live) -> None:
    http, _path = live
    assert http.put("/api/v1/config/values", json={}).status_code == 422
    assert http.put("/api/v1/config/values", json={"values": {}}).status_code == 422
    assert http.put("/api/v1/config/values", json={"values": []}).status_code == 422


def test_a_failed_write_leaves_the_file_alone(tmp_path) -> None:
    cfg, path = _config(tmp_path)
    with _app(cfg, path) as http:
        before = path.read_text(encoding="utf-8")
        response = http.put("/api/v1/config/values", json={"values": {
            "assistant.reply_max_chars": 160,
            "assistant.system_prompt": "x" * 10,
            "providers.asr.chain": ["nope"],
        }})
        assert response.status_code == 422
        assert path.read_text(encoding="utf-8") == before


# ------------------------------------------------------- speaker (device) fields

def _device_config(tmp_path: pathlib.Path) -> tuple[Config, pathlib.Path]:
    cfg, path = _config(tmp_path)
    cfg.devices = [DeviceConfig(id="living", adapter="mock", host="192.0.2.10",
                                room="客厅", wake_words=["你好小瑞"])]
    save_config(path, cfg)
    return cfg, path


def test_device_fields_are_writable_and_regenerate_the_keywords(tmp_path) -> None:
    """The speaker tier is not a read-only corner: LED has no other console input."""
    cfg, path = _device_config(tmp_path)
    with _app(cfg, path) as http:
        response = http.put("/api/v1/config/values", json={"values": {
            "devices.0.room": "主卧",
            "devices.0.led.enabled": True,
            "devices.0.led.colors.music": "#123456",
            "devices.0.wake_words": ["你好小瑞", "小瑞小瑞"],
        }})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["restart_required"] is True
        assert {"devices.0.room", "devices.0.led.enabled",
                "devices.0.led.colors.music", "devices.0.wake_words"} <= set(
                    body["restart_fields"])
        saved = load_config(path)
        assert saved.devices[0].room == "主卧"
        assert saved.devices[0].led.enabled is True
        assert saved.devices[0].led.colors["music"] == "#123456"
        assert saved.devices[0].wake_words == ["你好小瑞", "小瑞小瑞"]
        # A wake-word edit must leave a new keywords file behind before any restart.
        keywords = pathlib.Path(saved.audio.voice.kws_keywords_file)
        assert keywords.is_file()
        assert "@小瑞小瑞" in keywords.read_text(encoding="utf-8")
        # Adapters are rebuilt immediately so the Devices page reads the new values.
        devices = http.get("/api/v1/devices").json()
        assert devices[0]["room"] == "主卧"
        assert http.app.state.runner.wake_words == ("你好小瑞", "小瑞小瑞")


def test_device_fields_validate_like_the_schema(tmp_path) -> None:
    cfg, path = _device_config(tmp_path)
    with _app(cfg, path) as http:
        before = path.read_text(encoding="utf-8")
        response = http.put("/api/v1/config/values", json={"values": {
            "devices.0.led.colors.off": "red"}})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail[0]["loc"] == ["devices.0.led.colors.off"]
        assert path.read_text(encoding="utf-8") == before
        # listen_port is nullable: empty means "adapter default".
        response = http.put("/api/v1/config/values", json={"values": {
            "devices.0.listen_port": None}})
        assert response.status_code == 200, response.text
        assert load_config(path).devices[0].listen_port is None


# --------------------------------------------------------------- music sources

def test_changing_the_music_source_autofills_its_paths(tmp_path) -> None:
    """9哥: 音源定了以后路径要自动更新 - a stale path is 'enabled but silent'."""
    cfg, path = _config(tmp_path)
    cfg.music.source = "other"
    cfg.music.secrets_path = "/manual/creds.json"
    cfg.music.cache_dir = "/manual/cache"
    save_config(path, cfg)
    with _app(cfg, path) as http:
        response = http.put("/api/v1/config/values", json={
            "values": {"music.source": "navidrome"}})
        assert response.status_code == 200, response.text
        body = response.json()
        # Source + path saves rebuild the music service at runtime now.
        assert body["restart_required"] is False
        assert {"music.source", "music.secrets_path", "music.cache_dir"} <= set(
            body["applied"])
        assert any("自动更新" in warning for warning in body["warnings"])
        values = http.get("/api/v1/config/catalog").json()["values"]
        data_dir = pathlib.Path(cfg.system.data_dir)
        assert values["music.secrets_path"] == str(data_dir / "lan_media.json")
        assert values["music.cache_dir"] == str(data_dir / "music_cache")
        assert load_config(path).music.secrets_path.endswith("lan_media.json")


def test_unsupported_music_source_is_refused_by_the_catalog(tmp_path) -> None:
    """The source is a select now, not free text.

    A typo used to be saved and then leave the panel green while the runtime kept
    music off. The catalog refuses an id without an adapter; a hand-edited YAML can
    still carry one, and that case is reported by the panel as disabled (test_api).
    """
    cfg, path = _config(tmp_path)
    cfg.music.secrets_path = "/manual/creds.json"
    cfg.music.cache_dir = "/manual/cache"
    save_config(path, cfg)
    with _app(cfg, path) as http:
        response = http.put("/api/v1/config/values", json={
            "values": {"music.source": "some-new-service"}})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail[0]["loc"] == ["music.source"]
        assert "navidrome" in detail[0]["msg"]
        # Nothing was written: the previous source and its manual paths are intact.
        values = http.get("/api/v1/config/catalog").json()["values"]
        assert values["music.source"] == "navidrome"
        assert values["music.secrets_path"] == "/manual/creds.json"
        assert values["music.cache_dir"] == "/manual/cache"


def test_enabling_music_assembles_the_tools_and_disabling_removes_them(tmp_path) -> None:
    cfg, path = _config(tmp_path)
    with _app(cfg, path) as http:
        response = http.put("/api/v1/config/values", json={
            "values": {"music.enabled": True}})
        assert response.status_code == 200, response.text
        assert "music.enabled" in response.json()["applied"]
        assert any("自动补上" in warning for warning in response.json()["warnings"])
        assert http.app.state.music is not None
        names = http.get("/api/v1/tools").json()["names"]
        assert any(name.startswith("local.music_") for name in names)
        # The panel registry follows the flag without a restart.
        panels = {panel["id"]: panel for panel in
                  http.get("/api/v1/panels").json()["panels"]}
        assert panels["music"]["enabled"] is True

        response = http.put("/api/v1/config/values", json={
            "values": {"music.enabled": False}})
        assert response.status_code == 200, response.text
        assert http.app.state.music is None
        names = http.get("/api/v1/tools").json()["names"]
        assert not any(name.startswith("local.music_") for name in names)
        panels = {panel["id"]: panel for panel in
                  http.get("/api/v1/panels").json()["panels"]}
        assert panels["music"]["enabled"] is False


# ---------------------------------------------------------------- audio restart

class FakePlane:
    """Stands in for AudioPlaneSupervisor: records the calls, no subprocess."""

    def __init__(self, fail: bool = False) -> None:
        self.cfg = None
        self.fail = fail
        self.starts = 0
        self.stops = 0
        self.watches = 0
        self.error = ""

    async def stop(self) -> None:
        self.stops += 1

    async def start(self) -> None:
        self.starts += 1
        if self.fail:
            raise RuntimeError("no device this build can drive")

    def watch(self) -> None:
        self.watches += 1


def test_the_audio_restart_endpoint_restarts_only_the_plane(live) -> None:
    http, path = live
    plane = FakePlane()
    http.app.state.audio = plane
    response = http.post("/api/v1/system/audio/restart")
    assert response.status_code == 200
    assert response.json() == {"restarted": True, "error": ""}
    assert (plane.stops, plane.starts, plane.watches) == (1, 1, 1)
    # The endpoint restarts the plane with the live config, not a startup copy.
    assert plane.cfg is http.app.state.cfg
    assert path.exists()


def test_the_audio_restart_endpoint_reports_failure(live) -> None:
    http, _path = live
    http.app.state.audio = FakePlane(fail=True)
    body = http.post("/api/v1/system/audio/restart").json()
    assert body["restarted"] is False
    assert "RuntimeError" in body["error"]
    # The watchdog stays armed even when the launch failed.
    assert http.app.state.audio.watches == 1


def test_the_audio_restart_endpoint_without_a_plane_is_honest(live) -> None:
    http, _path = live
    body = http.post("/api/v1/system/audio/restart").json()
    assert body["restarted"] is False
    assert body["error"]


# --------------------------------------------------------------------- secrets

def test_secret_values_never_travel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUI_LLM_KEY", "sk-do-not-leak-123")
    cfg, path = _config(tmp_path)
    cfg.web.auth_token = "console-token-do-not-leak"
    save_config(path, cfg)
    with _app(cfg, path) as http:
        response = http.get("/api/v1/config/catalog",
                            headers={"X-Auth-Token": "console-token-do-not-leak"})
        assert response.status_code == 200
        text = response.text
        assert "sk-do-not-leak-123" not in text
        assert "console-token-do-not-leak" not in text
        body = response.json()
        assert body["secrets"]["RUI_LLM_KEY"]["has_value"] is True
        assert body["secrets"]["web.auth_token"]["has_value"] is True
        # The provider's api_key_env is a *name*, and no secret path exists in values.
        assert body["values"]["providers.catalog.llm.api_key_env"] == "RUI_LLM_KEY"
        assert "web.auth_token" not in body["values"]
        assert all("sk-do-not-leak" not in str(value) for value in body["values"].values())
        # GET /api/v1/config stays redacted too.
        config = http.get("/api/v1/config",
                          headers={"X-Auth-Token": "console-token-do-not-leak"}).json()
        assert "auth_token" not in config["web"]
        assert config["web"]["auth_required"] is True


def test_the_console_auth_token_is_write_only(tmp_path) -> None:
    cfg, path = _config(tmp_path)
    with _app(cfg, path) as http:
        response = http.post("/api/v1/secrets", json={
            "name": "web.auth_token", "value": "new-token"})
        assert response.status_code == 200
        assert response.json() == {"name": "web.auth_token", "has_key": True}
        # The token is in config.yaml (it is read before authentication) but never echoed.
        assert load_config(path).web.auth_token == "new-token"
        assert "new-token" not in response.text
        assert http.get("/api/v1/config").status_code == 401
        assert http.get("/api/v1/config",
                        headers={"X-Auth-Token": "new-token"}).status_code == 200
        listing = http.get("/api/v1/secrets",
                           headers={"X-Auth-Token": "new-token"}).json()["names"]
        assert "web.auth_token" in listing
        response = http.post("/api/v1/secrets",
                             headers={"X-Auth-Token": "new-token"},
                             json={"name": "web.auth_token", "value": ""})
        assert response.json()["has_key"] is False
        assert load_config(path).web.auth_token == ""
        assert "web.auth_token" not in http.get(
            "/api/v1/secrets").json()["names"]


# -------------------------------------------------------------------- timezone

def test_the_timezone_field_validates_and_is_wired_into_the_clock(tmp_path) -> None:
    assert SystemConfig(timezone="Asia/Shanghai").timezone == "Asia/Shanghai"
    assert SystemConfig().timezone == ""
    with pytest.raises(ValueError):
        SystemConfig(timezone="Mars/Olympus")
    cfg, path = _config(tmp_path)
    with _app(cfg, path) as http:
        response = http.put("/api/v1/config/values", json={
            "values": {"system.timezone": "Asia/Shanghai"}})
        assert response.status_code == 200, response.text
        assert response.json()["applied"] == ["system.timezone"]
        assert prompts._TIMEZONE == "Asia/Shanghai"
        prompts.set_timezone("")
        # The clock line is rendered in the configured zone: compare with the zone's
        # own clock. Minute rollover between the two calls is tolerated by the 2 min
        # window (the real assertion is that the zones differ or match as expected).
        prompts.set_timezone("UTC")
        utc_hour = int(re.search(r"(\d+)点", prompts.now_str()).group(1))  # type: ignore[union-attr]
        prompts.set_timezone("Asia/Shanghai")
        sh_hour = int(re.search(r"(\d+)点", prompts.now_str()).group(1))  # type: ignore[union-attr]
        assert (sh_hour - utc_hour) % 24 == 8
        prompts.set_timezone("")
    assert prompts._TIMEZONE == ""


# --------------------------------------------------------------------- coerce

def test_coerce_reports_the_field_not_the_schema_location() -> None:
    spec = next(field for field in field_specs(Config())
                if field["path"] == "ack.mode")
    assert coerce(spec, "tts") == "tts"
    with pytest.raises(ValueError, match="one of"):
        coerce(spec, "baritone")
    spec = next(field for field in field_specs(Config())
                if field["path"] == "tools.news.enabled")
    assert coerce(spec, True) is True
    with pytest.raises(ValueError, match="true or false"):
        coerce(spec, "yes")


def test_followup_window_controls_match_the_schema() -> None:
    body = document(Config())
    fields = {field["path"]: field for field in body["fields"]}
    delay = fields["audio.voice.followup_delay_ms"]
    assert delay["kind"] == "number"
    assert delay["min"] == 0
    assert delay["max"] == 10000
    assert delay["unit"] == {"zh": "ms", "en": "ms"}
    assert delay["effect"] == "restart"
    replay = fields["audio.voice.followup_replay"]
    assert replay["kind"] == "bool"
    assert replay["effect"] == "restart"
    # The baseline keeps the LX06's measured 1 s tail and the replay on.
    assert body["values"]["audio.voice.followup_delay_ms"] == 1000
    assert body["values"]["audio.voice.followup_replay"] is True
