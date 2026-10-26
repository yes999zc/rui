from __future__ import annotations

from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig
from rui.web.api.app import create_app


def client(cfg: Config | None = None) -> TestClient:
    return TestClient(create_app(cfg or Config()))


def test_health() -> None:
    body = client().get("/health").json()
    assert body["status"] == "ok"
    assert body["config_version"] == 3
    assert body["device_errors"] == 0


def test_mock_device_is_probed() -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock", host="127.0.0.1", room="客厅")])
    devices = client(cfg).get("/api/v1/devices").json()
    assert devices[0]["room"] == "客厅"
    # No audio plane runs in this test, so the control plane cannot see the outbound
    # link either: "unknown", not the old hard-coded "ready" (audit round 1 #12).
    assert devices[0]["status"] == "unknown"
    assert devices[0]["connected"] is None
    assert devices[0]["missing_for_barge_in"] == []


def test_xiaoai_device_is_reported_as_an_inbound_speaker() -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="lx06", host="192.0.2.10")])
    c = client(cfg)
    assert c.get("/health").json()["device_errors"] == 0
    device = c.get("/api/v1/devices").json()[0]
    # The inbound socket lives in the audio-plane process; with no plane in this test the
    # honest answer is "unknown" (the link cannot be observed from here).
    assert device["status"] == "unknown"
    assert device["connected"] is None
    assert device["transport"] == "inbound"
    assert "reference" in device["capabilities"]
    assert device["missing_for_barge_in"] == []


def test_an_adapter_that_cannot_be_built_is_reported_not_raised(monkeypatch) -> None:
    """A broken adapter must surface per device, never as a startup failure."""
    from rui.device import registry

    def boom(dev, cfg):
        raise registry.AdapterUnavailable("adapter exploded")

    # The registry is a table of builders now: patching `_builder` fails every name at
    # build time without needing a fake module on disk.
    monkeypatch.setattr(registry, "_builder", lambda name: boom)
    cfg = Config(devices=[DeviceConfig(id="living", adapter="lx06", host="192.0.2.10")])
    c = client(cfg)
    assert c.get("/health").json()["device_errors"] == 1
    device = c.get("/api/v1/devices").json()[0]
    assert device["status"] == "unavailable"
    assert "exploded" in device["error"]


def test_secrets_are_not_in_payload() -> None:
    cfg = Config()
    cfg.providers.catalog["llm"].api_key_env = "SOME_KEY"
    payload = client(cfg).get("/api/v1/config").json()
    block = payload["providers"]["catalog"]["llm"]
    assert "api_key" not in block
    assert block["has_key"] is False


def test_present_key_is_reported_as_a_flag_only(monkeypatch) -> None:
    cfg = Config()
    cfg.providers.catalog["llm"].api_key_env = "RUI_TEST_KEY"
    monkeypatch.setenv("RUI_TEST_KEY", "sk-do-not-leak")
    payload = client(cfg).get("/api/v1/config").json()
    block = payload["providers"]["catalog"]["llm"]
    assert block["has_key"] is True
    assert "sk-do-not-leak" not in str(payload)


def test_device_token_is_reported_as_a_flag_only(monkeypatch) -> None:
    cfg = Config(devices=[DeviceConfig(id="living", adapter="mock", host="127.0.0.1")])
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "dev-secret")
    payload = client(cfg).get("/api/v1/config").json()
    assert payload["devices"][0]["has_device_token"] is True
    assert "dev-secret" not in str(payload)


def test_auth_token_never_leaves_the_server() -> None:
    payload = client(Config(web={"auth_token": "s3cret"})).get(
        "/api/v1/config", headers={"X-Auth-Token": "s3cret"}
    ).json()
    assert "s3cret" not in str(payload)
    assert payload["web"]["auth_required"] is True


def test_auth_is_enforced_when_configured() -> None:
    cfg = Config(web={"auth_token": "s3cret"})
    c = client(cfg)
    assert c.get("/api/v1/config").status_code == 401
    assert c.get("/api/v1/config", headers={"X-Auth-Token": "s3cret"}).status_code == 200


def test_traces_are_empty_before_the_audio_plane_exists() -> None:
    body = client().get("/api/v1/traces").json()
    assert body["turns"] == []


def test_the_console_can_write_the_two_conversational_sections(tmp_path) -> None:
    """PUT /api/v1/config: merge, validate, back up, and apply the prompt live."""
    from rui.config.store import load_config, save_config

    path = tmp_path / "config.yaml"
    save_config(path, Config())
    c = TestClient(create_app(Config(), config_path=path))

    body = c.put("/api/v1/config", json={
        "assistant": {"name": "小雷", "reply_max_chars": 80},
        "session": {"history_rounds": 8},
    }).json()
    assert body["assistant"]["name"] == "小雷"
    assert body["assistant"]["reply_max_chars"] == 80
    # the sections that were not in the patch are untouched
    assert body["assistant"]["reply_max_chars_long"] == 900
    assert body["session"]["history_rounds"] == 8
    # the read endpoint must agree with the write, or a page reload shows the old value
    assert c.get("/api/v1/config").json()["assistant"]["name"] == "小雷"
    written = load_config(path)
    assert written.assistant.name == "小雷"
    assert written.session.history_rounds == 8
    # a backup of the previous file exists, so a bad edit is one cp away from undone
    assert list(tmp_path.glob("config.yaml.bak-*"))


def test_the_write_endpoint_refuses_what_it_does_not_own(tmp_path) -> None:
    from rui.config.store import save_config

    path = tmp_path / "config.yaml"
    save_config(path, Config())
    c = TestClient(create_app(Config(), config_path=path))
    # providers carry keys and the audio section belongs to a running plane: not from here
    assert c.put("/api/v1/config", json={"providers": {}}).status_code == 400
    # a value the schema rejects must not reach the disk
    assert c.put("/api/v1/config", json={"assistant": {"name": ""}}).status_code == 422
    assert c.put("/api/v1/config", json={"assistant": "nope"}).status_code == 400


def test_a_key_can_be_typed_in_the_console_and_is_never_read_back(tmp_path) -> None:
    """The key goes to secrets.yaml (0600) and only its presence is ever returned."""
    import os
    import stat

    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    c = TestClient(create_app(cfg))

    body = c.post("/api/v1/secrets", json={"name": "MY_SEARCH_KEY", "value": "sk-test-123"}).json()
    assert body == {"name": "MY_SEARCH_KEY", "has_key": True}
    path = tmp_path / "secrets.yaml"
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "sk-test-123" in path.read_text()      # stored in the file, not in the config

    # the listing has names, never values
    listing = c.get("/api/v1/secrets").json()
    assert listing["names"] == ["MY_SEARCH_KEY"]
    assert "sk-test-123" not in str(listing)

    # an unconfigured search reports has_key from the file, not only from the environment
    search = c.get("/api/v1/config").json()["tools"]["search"]
    assert search["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "has_key" in search

    # an empty value deletes it again
    assert c.post("/api/v1/secrets", json={"name": "MY_SEARCH_KEY", "value": ""}).json()["has_key"] is False
    assert c.get("/api/v1/secrets").json()["names"] == []


def test_a_bad_secret_name_is_refused(tmp_path) -> None:
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    c = TestClient(create_app(cfg))
    assert c.post("/api/v1/secrets", json={"name": "lower case", "value": "x"}).status_code == 400


def test_reminder_tools_are_registered_for_the_turn_loop() -> None:
    """P10 was written but never wired.

    `tools.reminders` had 44 tests and a scheduler, but the app imported only
    `tools.info.build_tools` and nothing ever constructed `build_scheduler`. The model
    could not see the reminder tools at all. This is the regression guard: the registry
    the turn loop and the console share must contain them.
    """
    app = create_app(Config())
    names = set(app.state.tools.names())
    assert {"local.set_reminder", "local.list_reminders", "local.cancel_reminder"} <= names


def test_the_scheduler_starts_with_the_app(tmp_path) -> None:
    """A due reminder needs a tick and a speaker; both live in the lifespan now."""
    cfg = Config()
    cfg.system.data_dir = str(tmp_path)
    app = create_app(cfg)
    with TestClient(app):
        assert app.state.reminders is not None
        assert app.state.reminder_tick is not None
        assert app.state.reminder_task is not None


def test_voice_threshold_reads_the_keywords_file(tmp_path) -> None:
    """The console must show the gate the model uses, not a config field nobody reads."""
    keywords = tmp_path / "keywords.txt"
    keywords.write_text("n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞\n", encoding="utf-8")
    cfg = Config()
    cfg.audio.voice.kws_keywords_file = str(keywords)
    body = TestClient(create_app(cfg)).get("/api/v1/voice/threshold").json()
    assert body["exists"] is True
    assert body["threshold"] == 0.15
    assert body["boost"] == 2.0
    assert body["keywords"] == 1


def test_debug_say_requires_text_and_a_speaker() -> None:
    c = client()
    assert c.post("/api/v1/debug/say", json={"text": "   "}).status_code == 422
    # No audio plane in this app: a real text turn must say so, not hang.
    assert c.post("/api/v1/debug/say", json={"text": "现在几点"}).status_code == 409


def test_security_headers_are_present() -> None:
    response = client().get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "microphone=()" in response.headers["permissions-policy"]


def test_auth_failure_is_logged_without_the_token(caplog) -> None:  # noqa: ANN001
    cfg = Config(web={"auth_token": "s3cret"})
    response = client(cfg).get("/api/v1/devices", headers={"X-Auth-Token": "wrong-token"})
    assert response.status_code == 401
    text = caplog.text
    assert "console auth failed" in text
    assert "s3cret" not in text and "wrong-token" not in text


# ---------------------------------------------------------------- music panel IA
def _music_panel(c: TestClient) -> dict:
    return next(p for p in c.get("/api/v1/panels").json()["panels"] if p["id"] == "music")


def test_music_panel_is_configurable_while_disabled() -> None:
    """Music off must not be a dead end (audit §2.1/§5 Phase 1).

    The page keeps the enable toggle + source (the `music` catalog group) and the
    write-only credentials block, so turning music on no longer requires finding
    the Tools panel first.
    """
    c = client()
    music = _music_panel(c)
    assert music["enabled"] is False
    assert "music.enabled" in music["enabled_reason"]
    blocks = {block["type"]: block for block in music["blocks"]}
    assert "config" in blocks and "credentials" in blocks
    assert blocks["config"]["groups"] == ["music"]
    assert blocks["config"]["tier"] == 4
    catalog = c.get("/api/v1/config/catalog").json()
    paths = {field["path"] for field in catalog["fields"]}
    assert {"music.enabled", "music.source"} <= paths


def test_music_source_field_is_a_select_with_navidrome() -> None:
    """Typing a source by hand was how an unsupported adapter got saved silently."""
    catalog = client().get("/api/v1/config/catalog").json()
    field = next(f for f in catalog["fields"] if f["path"] == "music.source")
    assert field["kind"] == "select"
    assert "navidrome" in field["options"]


def test_an_unknown_music_source_is_not_reported_as_enabled() -> None:
    """`enabled` means "the runtime can assemble it", not just the config flag."""
    cfg = Config()
    cfg.music.enabled = True
    cfg.music.source = "plex"  # no adapter in this release
    cfg.music.secrets_path = "/tmp/rui-test-music.json"
    c = client(cfg)
    music = _music_panel(c)
    assert music["enabled"] is False
    assert "plex" in music["enabled_reason"]
    assert "不受支持" in music["enabled_reason"]
    # The music API agrees: no service was built, so there is nothing "enabled".
    assert c.get("/api/v1/music").json()["enabled"] is False


def test_a_supported_music_source_stays_enabled() -> None:
    cfg = Config()
    cfg.music.enabled = True
    cfg.music.source = "navidrome"
    cfg.music.secrets_path = "/tmp/rui-test-music.json"
    music = _music_panel(client(cfg))
    assert music["enabled"] is True
    assert music["enabled_reason"] == ""
