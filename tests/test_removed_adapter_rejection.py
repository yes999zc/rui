"""0.8.0: `adapter: xiaoai` is rejected with a fix sentence, never rewritten.

0.7.x rewrote the old spelling on load and warned. 0.8.0 makes it a load-time
config error so a stale deployment cannot run half-configured; the recovery path
still works, but its note carries the same fix sentence. These tests are the
reverse of the deleted migration tests: nothing is rewritten, and the operator is
told exactly which line to change.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui.config import load_config, save_config
from rui.config.recovery import load_with_recovery
from rui.config.schema import Config, ConfigError, DeviceConfig
from rui.device import registry
from rui.web.api.app import create_app

TOKEN = "test-token"
FIX_SENTENCE = (
    "devices[0] (Lab): adapter 'xiaoai' is no longer accepted in 0.8.0; "
    "change devices[].adapter to 'lx06'"
)

LEGACY_YAML = """\
version: 2
system:
  data_dir: {data_dir}
  timezone: Asia/Shanghai
web:
  auth_token: "{token}"
devices:
  - id: living
    adapter: xiaoai
    host: 192.0.2.10
    room: Lab
    listen_port: 8870
    token_env: RUI_DEVICE_TOKEN
"""

CURRENT_YAML = LEGACY_YAML.replace("adapter: xiaoai", "adapter: lx06")


def _write(tmp_path: Path, text: str = LEGACY_YAML) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text.format(data_dir=tmp_path / "data", token=TOKEN))
    return path


def test_old_spelling_is_rejected_with_the_fix_sentence(tmp_path: Path) -> None:
    path = _write(tmp_path)
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError) as error:
        load_config(path)

    assert str(error.value) == FIX_SENTENCE
    # Not rewritten on disk either: a downgrade still reads the file the operator
    # wrote, and the fix is the operator's edit, not a silent migration.
    assert path.read_text(encoding="utf-8") == before
    assert "adapter: xiaoai" in path.read_text(encoding="utf-8")


def test_recovery_note_reaches_the_marker_through_last_good(tmp_path: Path) -> None:
    """recovery.py keeps its policy: keep the bad file, expose the note, allow restore.

    The marker is written under the *loaded* config's data_dir, so this uses the
    last-good path (whose data_dir is the temp dir) rather than schema defaults
    (whose data_dir is the image default and not writable in this test).
    """
    path = _write(tmp_path)
    path.with_name("config.last-good.yaml").write_text(
        CURRENT_YAML.format(data_dir=tmp_path / "data", token=TOKEN), encoding="utf-8")

    cfg, info = load_with_recovery(path)

    assert [device.adapter for device in cfg.devices] == ["lx06"]
    assert info is not None
    assert info.mode == "last-good"
    assert info.note == FIX_SENTENCE
    assert Path(info.bad_path).is_file()
    marker = json.loads(
        (tmp_path / "data" / "config-recovery.json").read_text(encoding="utf-8")
    )
    assert marker["note"] == FIX_SENTENCE
    assert marker["mode"] == "last-good"


def test_recovery_from_defaults_keeps_the_fix_sentence(tmp_path: Path) -> None:
    cfg, info = load_with_recovery(_write(tmp_path))

    assert cfg.devices == []
    assert info is not None
    assert info.mode == "defaults"
    assert info.note == FIX_SENTENCE
    assert Path(info.bad_path).is_file()


def test_adapter_is_required(tmp_path: Path) -> None:
    path = _write(tmp_path, LEGACY_YAML.replace("    adapter: xiaoai\n", ""))
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert "adapter" in str(error.value)
    assert "Field required" in str(error.value)


def test_health_has_no_legacy_spelling_warning(tmp_path: Path) -> None:
    path = _write(tmp_path, CURRENT_YAML)
    cfg = load_config(path)
    app = create_app(cfg, config_path=path)
    body = TestClient(app).get("/health", headers={"X-Auth-Token": TOKEN}).json()
    assert body["adapter_plugins"] == {"registered": [], "warnings": 0}
    assert not any(row.get("code") == "legacy_adapter_spelling"
                   for row in body["warnings"])


def test_missing_external_plugin_points_at_the_plug_hint(monkeypatch) -> None:
    monkeypatch.delenv("RUI_PLUGINS_DIR", raising=False)
    importlib.reload(registry)
    cfg = Config(devices=[DeviceConfig(id="living", adapter="lx06",
                                       host="192.0.2.10")])
    built = registry.build_adapters(cfg)
    assert "living" not in built.adapters
    error = built.errors["living"]
    assert "unknown adapter" in error
    assert "/opt/rui-plugins" in error
    assert "manifest declares" in error


def _console(path: Path) -> TestClient:
    return TestClient(create_app(load_config(path), config_path=path))


def test_console_put_rejects_the_removed_spelling_before_any_write(tmp_path: Path) -> None:
    """The read-time rejection must also guard writes; otherwise a 200 saves a file
    the very next boot refuses (`Config.model_validate` accepts any adapter string).
    """
    path = _write(tmp_path, CURRENT_YAML)
    before = path.read_bytes()
    response = _console(path).put(
        "/api/v1/devices/living",
        headers={"X-Auth-Token": TOKEN},
        json={"adapter": "xiaoai"},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_values"
    assert FIX_SENTENCE in json.dumps(detail)
    assert detail["details"]["fields"][0]["path"] == "devices[0].adapter"
    # Nothing reached disk: no rewritten file, no backup of a rejected write.
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("config.yaml.bak-*"))


def test_setup_device_upsert_rejects_the_removed_spelling(tmp_path: Path) -> None:
    """The wizard shares `_write_devices`; its guard must reject the same payload."""
    path = _write(tmp_path, CURRENT_YAML)
    before = path.read_bytes()
    response = _console(path).post(
        "/api/v1/setup/step",
        headers={"X-Auth-Token": TOKEN},
        json={
            "step": "params",
            "action": "device.upsert",
            "payload": {
                "id": "living", "adapter": "xiaoai",
                "host": "192.0.2.10", "room": "Lab",
            },
        },
    )

    assert response.status_code == 422, response.text
    assert FIX_SENTENCE in response.text
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("config.yaml.bak-*"))


def test_save_config_refuses_a_removed_spelling_before_touching_the_file(
        tmp_path: Path) -> None:
    """The store is the last line: a writer that skipped the console check still
    cannot persist a file the next `load_config` refuses.
    """
    path = _write(tmp_path, CURRENT_YAML)
    config = load_config(path)
    bad = config.model_copy(update={"devices": [
        config.devices[0].model_copy(update={"adapter": "xiaoai"})]})
    before = path.read_bytes()

    with pytest.raises(ConfigError) as error:
        save_config(path, bad)

    assert str(error.value) == FIX_SENTENCE
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("config.yaml.bak-*"))


def test_wake_backstop_uses_configured_words_and_empty_disables_it() -> None:
    from rui.web.api.app import _wake_backstop

    assert _wake_backstop("hello there", []) == ""
    assert _wake_backstop("hello there", ["hello"]) == "hello"
    assert _wake_backstop("hello there", ["nope"]) == ""
    assert _wake_backstop("   ", ["hello"]) == ""
