"""C-002 write surface and the frozen-contract blocker fixes.

The focus is the behaviour the contract review called out: an adapter-managed token is
never overwritten, rotation has one shared 10 s cooldown, a plan with an unusable plug
is a 200 with `blocked`, 422 bodies always carry `details.fields[]`, and a legacy
`mic_gain_db` param is preserved instead of rejected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rui import secrets
from rui.config import Config, DeviceConfig, load_config, save_config
from rui.web.api.app import create_app

TOKEN = "test-token-fake"
AUTH = {"X-Auth-Token": TOKEN}


def _device(**overrides) -> DeviceConfig:
    payload = {
        "id": "living",
        "adapter": "lx06",
        "host": "speaker.local",
        "room": "客厅",
        "params": {"plug_id": "xiaoai-lx06"},
    }
    payload.update(overrides)
    return DeviceConfig(**payload)


class _LinkedAdapter:
    """The adapter surface a live, adapter-managed dot2 exposes to the console."""

    def health(self) -> dict:
        return {"connected": True, "latency_ms": 12.0}


class _Plane:
    """The supervisor surface `_plane_snapshot` reads: one inbound link."""

    def __init__(self, connected: bool, device_id: str = "living") -> None:
        self.connected = connected
        self.device_id = device_id

    def status(self) -> dict:
        return {
            "running": True,
            "device_id": self.device_id,
            "error": "",
            "metrics": {"speaker_connected": self.connected},
        }


def _write_outbound_plug(tmp_path: Path) -> None:
    """Install a user plug whose only device is outbound (we dial it).

    The built-in LX06 is inbound-only, and the plan's outbound branch (no link hint,
    `pending` until a probe record exists) needs a manifest that says `outbound`.
    """
    root = tmp_path / "data" / "plugs" / "demo-outbound"
    (root / "devices").mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        "schema: rui-plugin/v0\n"
        "id: demo-outbound\n"
        "name: Outbound demo plug\n"
        "version: 1.0.0\n"
        'rui_compat: ">=0.0.1 <999.0.0"\n'
        "license: MIT\n"
        "maintainer: Test <test@example.invalid>\n"
        "trust: builtin\n"
        "devices:\n"
        "  - devices/outbound.yaml\n",
        encoding="utf-8",
    )
    (root / "devices" / "outbound.yaml").write_text(
        "schema: rui-device/v1\n"
        "id: outbound-demo\n"
        "model: OUT1\n"
        "name: Outbound Demo Speaker\n"
        "vendor: Demo Inc.\n"
        "class: A\n"
        "transport: outbound\n"
        "protocol: {kind: ws, version: '1'}\n"
        "capabilities: [playback]\n"
        "params:\n"
        "  - {key: host, label: Address, type: string, required: true}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def console(tmp_path, monkeypatch):
    monkeypatch.delenv("RUI_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("DOT2_EM_TOKEN", raising=False)

    def build(*, devices: list[DeviceConfig] | None = None,
              config: Config | None = None) -> tuple[TestClient, Config, Path]:
        if config is None:
            config = Config(web={"auth_token": TOKEN},
                            devices=[item.model_copy(deep=True)
                                     for item in (devices or [_device()])])
        config.system.data_dir = str(tmp_path / "data")
        config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        path = tmp_path / "config.yaml"
        save_config(path, config)
        return TestClient(create_app(config, config_path=path)), config, path

    return build


def _secrets_file(config: Config) -> Path:
    return Path(config.system.data_dir) / "secrets.yaml"


def test_rotate_has_one_shared_cooldown_state(console):
    client, config, _ = console()
    first = client.post("/api/v2/devices/living/token/rotate", headers=AUTH)
    assert first.status_code == 200
    assert first.json()["token"]
    assert client.app.state.token_rotations["living"]
    second = client.post("/api/v2/devices/living/token/rotate", headers=AUTH)
    assert second.status_code == 429
    body = second.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["details"]["retry_after_s"] > 0
    assert second.headers["Retry-After"]


def test_adapter_managed_token_is_never_overwritten(console):
    client, config, _ = console()
    secrets.configure(config.system.data_dir)
    client.app.state.adapters.adapters["living"] = _LinkedAdapter()

    connection = client.get("/api/v2/devices/living/connection", headers=AUTH).json()
    assert connection["token_managed_by_adapter"] is True
    assert connection["configured"] is True
    assert connection["token_configured"] is False
    assert "适配器" in connection["hint"]["zh"]
    assert "adapter" in connection["hint"]["en"]

    reveal = client.post("/api/v2/devices/living/connection/reveal", headers=AUTH)
    assert reveal.status_code == 409
    assert reveal.json()["error"]["code"] == "token_managed_by_adapter"

    rotate = client.post("/api/v2/devices/living/token/rotate", headers=AUTH)
    assert rotate.status_code == 409
    assert rotate.json()["error"]["code"] == "token_managed_by_adapter"
    # Nothing may be written to secrets.yaml for a token rui does not own.
    assert not _secrets_file(config).exists()


def test_reveal_returns_the_token_once_and_connection_masks_it(console):
    client, _, _ = console()
    rotated = client.post("/api/v2/devices/living/token/rotate", headers=AUTH).json()
    token = rotated["token"]
    assert rotated["token_env"] == "RUI_DEVICE_TOKEN"

    reveal = client.post("/api/v2/devices/living/connection/reveal", headers=AUTH)
    assert reveal.status_code == 200
    assert reveal.headers["cache-control"] == "no-store"
    assert reveal.json()["token"] == token

    connection = client.get("/api/v2/devices/living/connection", headers=AUTH)
    assert connection.json()["token_configured"] is True
    assert token not in connection.text


def test_plan_with_an_unusable_plug_is_a_blocked_200(console):
    client, _, _ = console()
    response = client.get("/api/v2/setup/plan?plugin_id=no-such-plug", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["blocked"]["code"] == "plug_unavailable"
    assert body["plugin"]["id"] == "no-such-plug"
    assert body["complete"] is False
    assert [step["id"] for step in body["steps"]] == [
        "keys", "models", "prepare", "params", "wake", "connect", "test", "verify",
    ]


def test_add_mode_plan_reports_house_setup_incomplete(console):
    client, _, _ = console()
    response = client.get(
        "/api/v2/setup/plan?plugin_id=xiaoai-lx06&device_id=study", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "add"
    assert body["device_id"] == "study"
    assert body["blocked"] == {
        "code": "house_setup_incomplete",
        "message": "finish the house setup steps before adding a device",
        "missing_steps": ["keys", "models"],
    }


def test_first_run_plan_has_the_eight_steps(console):
    empty = Config(web={"auth_token": TOKEN}, devices=[])
    client, _, _ = console(config=empty)
    body = client.get("/api/v2/setup/plan?plugin_id=xiaoai-lx06",
                      headers=AUTH).json()
    assert body["mode"] == "first-run"
    assert body["plugin"] == {"id": "xiaoai-lx06", "version": "0.3.0",
                              "name": "Xiaomi AI Speaker Pro (LX06)"}
    assert body["steps"][1]["evidence"] == {"asr": True, "vad": False, "kws": False}
    assert body["steps"][3]["actions"] == ["device.create"]


def test_first_run_step_creates_a_device(console):
    empty = Config(web={"auth_token": TOKEN}, devices=[])
    client, config, _ = console(config=empty)
    response = client.post(
        "/api/v2/setup/plan/steps/params", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "study",
              "action": "device.create",
              "values": {"id": "study", "room": "书房", "host": "192.168.1.9"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device"]["id"] == "study"
    assert body["device"]["model_id"] == "lx06"
    assert body["restart"]["scope"] == "fleet"
    assert body["plan"]["mode"] == "edit"
    fresh = load_config(Path(client.app.state.config_path))
    assert [item.id for item in fresh.devices] == ["study"]


def test_immutable_field_error_has_the_unified_fields_shape(console):
    client, _, _ = console()
    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"plugin_id": "xiaoai-lx06"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "immutable_field"
    # C-002 §9 pins the field-level code to "immutable" (the error code stays
    # `immutable_field`).
    assert body["error"]["details"]["fields"] == [{
        "path": "plugin_id", "code": "immutable",
        "message": "this field cannot be changed after creation",
    }]


def test_create_validation_uses_details_fields(console):
    client, _, _ = console()
    response = client.post("/api/v2/devices", headers=AUTH,
                           json={"id": "Bad Id", "host": "h", "plugin_id": "xiaoai-lx06"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["details"]["fields"][0]["path"] == "id"


def test_port_conflict_keeps_its_code_and_fields_shape(console):
    client, _, _ = console()
    response = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "study", "room": "书房", "plugin_id": "xiaoai-lx06", "host": "192.168.1.9",
    })
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "port_conflict"
    assert body["error"]["details"]["port"] == 4412
    assert body["error"]["details"]["device_ids"] == ["living", "study"]
    assert body["error"]["details"]["fields"][0]["path"] == "host"


def test_create_returns_the_contract_envelope(console):
    client, _, _ = console()
    response = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "study", "room": "书房", "plugin_id": "xiaoai-lx06",
        "host": "192.168.1.9", "listen_port": 8870,
    })
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"device", "restart"}
    assert body["device"]["id"] == "study"
    assert body["device"]["model_id"] == "lx06"
    assert body["restart"]["scope"] == "fleet"
    assert body["restart"]["auto"] is True

    duplicate = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "study", "room": "书房", "plugin_id": "xiaoai-lx06",
        "host": "192.168.1.9", "listen_port": 8871,
    })
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "device_exists"


def test_patch_device_returns_device_and_restart(console):
    client, _, _ = console()
    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"room": "书房", "volume_target": 40})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"device", "restart"}
    assert body["device"]["room"] == "书房"
    assert body["device"]["volume_target"] == 40
    assert body["restart"]["scope"] == "fleet"


def test_legacy_params_are_preserved_and_reported(console):
    device = _device(params={"plug_id": "xiaoai-lx06", "mic_gain_db": 18})
    client, _, _ = console(devices=[device])
    detail = client.get("/api/v2/devices/living", headers=AUTH).json()
    assert detail["params"]["mic_gain_db"] == 18
    assert detail["unknown_params"] == ["mic_gain_db"]

    # An edit that only changes the room must not fail on the legacy key.
    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"room": "书房"})
    assert response.status_code == 200, response.text
    assert response.json()["device"]["params"]["mic_gain_db"] == 18
    assert response.json()["device"]["unknown_params"] == ["mic_gain_db"]
    warning_codes = [item["code"] for item in response.json()["device"]["warnings"]]
    assert "unknown_params_ignored" in warning_codes


def test_delete_requires_confirm_and_returns_the_envelope(console):
    client, _, _ = console()
    missing = client.delete("/api/v2/devices/living", headers=AUTH)
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "confirm_required"
    assert missing.json()["error"]["details"]["impact"]

    removed = client.delete("/api/v2/devices/living?confirm=living", headers=AUTH)
    assert removed.status_code == 200
    assert removed.json() == {"removed": True, "device_id": "living"}
    assert client.get("/api/v2/devices/living", headers=AUTH).status_code == 404


def test_house_patch_and_audio_restart(console):
    client, config, _ = console()
    patched = client.patch("/api/v2/house", headers=AUTH,
                           json={"multi_device": {"enabled": True, "max_planes": 4}})
    assert patched.status_code == 200
    body = patched.json()
    assert body["house"]["multi_device"]["enabled"] is True
    assert body["restart"]["scope"] == "service"
    assert body["restart"]["auto"] is False
    assert "multi_device.enabled" in body["restart"]["restart_fields"]

    audio = client.patch("/api/v2/house/audio", headers=AUTH,
                         json={"ack": {"echo_ignore_ms": 1500}})
    assert audio.status_code == 200
    assert audio.json()["ack"]["echo_ignore_ms"] == 1500
    assert audio.json()["restart"]["scope"] == "audio_plane"
    assert audio.json()["restart"]["auto"] is False
    assert "ack.echo_ignore_ms" in audio.json()["restart"]["restart_fields"]
    assert "ack.echo_ignore_ms" in audio.json()["explicit"]

    restart = client.post("/api/v2/house/audio/restart", headers=AUTH)
    assert restart.status_code == 200
    assert set(restart.json()) == {"restart"}
    assert restart.json()["restart"]["error"] == "audio plane not started"


def test_device_audio_patch_and_clear(console):
    client, _, _ = console()
    patched = client.patch("/api/v2/devices/living/audio", headers=AUTH,
                           json={"audio": {"aec": {"who": "device"}}})
    assert patched.status_code == 200
    body = patched.json()
    assert set(body) == {"audio", "device", "restart"}
    assert body["audio"]["overrides"] == ["aec.who"]
    assert body["audio"]["sources"]["aec.who"] == "device-override"

    cleared = client.patch("/api/v2/devices/living/audio", headers=AUTH,
                           json={"audio": None})
    assert cleared.status_code == 200
    assert cleared.json()["audio"]["overrides"] == []
    assert cleared.json()["audio"]["sources"]["aec.who"] != "device-override"


def test_device_test_is_evidence_shaped(console):
    client, _, _ = console()
    body = client.post("/api/v2/devices/living/test", headers=AUTH).json()
    assert set(body) == {"ok", "ready", "waited_ms", "checked_at", "transport",
                         "checks", "errors"}
    assert [check["id"] for check in body["checks"]] == [
        "plugin", "link", "token", "capabilities",
    ]
    assert body["checks"][1]["code"] == "device_not_connected"
    assert body["ok"] is False


def test_actions_validate_the_action_and_the_link(console):
    client, _, _ = console()
    unknown = client.post("/api/v2/devices/living/actions", headers=AUTH,
                          json={"action": "lights.on", "args": {}})
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "unknown_action"
    assert "volume.set" in unknown.json()["error"]["details"]["allowed"]

    offline = client.post("/api/v2/devices/living/actions", headers=AUTH,
                          json={"action": "volume.read", "args": {}})
    assert offline.status_code == 409
    assert offline.json()["error"]["code"] == "device_not_connected"


def test_disabled_device_cannot_run_an_action(console):
    device = _device(enabled=False)
    client, _, _ = console(devices=[device])
    response = client.post("/api/v2/devices/living/actions", headers=AUTH,
                           json={"action": "volume.read", "args": {}})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_disabled"


def test_setup_step_rejects_an_unknown_action(console):
    client, _, _ = console()
    response = client.post("/api/v2/setup/plan/steps/params", headers=AUTH,
                           json={"plugin_id": "xiaoai-lx06", "action": "wake.set",
                                 "values": {}})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "action_not_allowed"
    assert body["error"]["details"]["allowed"] == ["device.create", "device.update"]


def test_setup_step_wake_set_persists_words(console):
    client, _, path = console()
    response = client.post(
        "/api/v2/setup/plan/steps/wake", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living", "action": "wake.set",
              "values": {"wake_words": ["小瑞小瑞"]}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    wake = next(step for step in body["plan"]["steps"] if step["id"] == "wake")
    assert wake["evidence"]["words"] == ["小瑞小瑞"]
    fresh = load_config(path)
    assert fresh.devices[0].wake_words == ["小瑞小瑞"]


def test_setup_step_device_update_edits_an_existing_device(console):
    client, _, path = console()
    response = client.post(
        "/api/v2/setup/plan/steps/params", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.update", "values": {"room": "书房"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device"]["room"] == "书房"
    assert body["plan"]["mode"] == "edit"
    assert load_config(path).devices[0].room == "书房"


def test_setup_step_manual_confirm_marks_prepare_done(console):
    client, _, _ = console()
    response = client.post(
        "/api/v2/setup/plan/steps/prepare", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "manual.confirm", "values": {"confirm": True}},
    )
    assert response.status_code == 200, response.text
    prepare = next(step for step in response.json()["plan"]["steps"]
                   if step["id"] == "prepare")
    assert prepare["status"] == "done"
    assert prepare["evidence"]["confirm"] is True


def test_setup_step_keys_save_is_write_only(console):
    client, config, _ = console()
    response = client.post(
        "/api/v2/setup/plan/steps/keys", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "keys.save", "values": {"values": {"RUI_LLM_KEY": "fake-key"}}},
    )
    assert response.status_code == 200, response.text
    assert "fake-key" not in response.text
    assert secrets.resolve("RUI_LLM_KEY") is not None


def test_setup_step_device_test_returns_checks(console):
    client, _, _ = console()
    response = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.test", "values": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [check["id"] for check in body["result"]["checks"]] == [
        "plugin", "link", "token", "capabilities",
    ]
    test_step = next(step for step in body["plan"]["steps"] if step["id"] == "test")
    assert test_step["evidence"]["checks"]


# ------------------------------------------- setup plan evidence must stay honest
def _plan(client, device_id: str = "living", *, plugin_id: str = "xiaoai-lx06") -> dict:
    return client.get(
        f"/api/v2/setup/plan?plugin_id={plugin_id}&device_id={device_id}",
        headers=AUTH,
    ).json()


def _set_test_step(plan: dict) -> dict:
    return next(step for step in plan["steps"] if step["id"] == "test")


def test_a_failed_device_test_is_not_hidden_by_a_connected_link(console):
    """A stored `ok:false` wins over the live link (regression).

    The old plan said `test_done = connected` / `evidence.ok = stored_ok or connected`,
    so a connected speaker whose last `device.test` failed (here: the device is
    disabled, a real failing check) looked "done" and the wizard skipped the failing
    checks and jumped to verify.
    """
    client, _, _ = console(devices=[_device(enabled=False)])
    client.app.state.audio = _Plane(connected=True)

    tested = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.test", "values": {}},
    )
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["result"]["ok"] is False
    assert any(check["code"] == "device_disabled"
               for check in body["result"]["checks"])
    step = _set_test_step(body["plan"])
    assert step["status"] != "done"
    assert step["evidence"]["ok"] is False
    assert step["evidence"]["tested"] is True

    # A fresh plan read still reports the stored failure even though the link is up.
    plan = _plan(client)
    step = _set_test_step(plan)
    assert step["status"] == "pending"
    assert step["evidence"]["ok"] is False
    assert step["evidence"]["tested"] is True
    assert any(check["code"] == "device_disabled" and check["status"] == "fail"
               for check in step["evidence"]["checks"])


def test_a_passing_device_test_completes_the_step(console):
    client, _, _ = console()
    # A live link is itself the proof the adapter's token works (token_managed), so the
    # probe passes without writing a secret.
    client.app.state.audio = _Plane(connected=True)

    tested = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.test", "values": {}},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["result"]["ok"] is True
    step = _set_test_step(tested.json()["plan"])
    assert step["status"] == "done"
    assert step["evidence"]["ok"] is True
    assert step["evidence"]["tested"] is True

    # The link is only a hint for the never-tested case. Drop it: the stored pass must
    # keep the step done, which is what separates "record says pass" from "link is up".
    client.app.state.audio = _Plane(connected=False)
    step = _set_test_step(_plan(client))
    assert step["status"] == "done"
    assert step["evidence"]["ok"] is True
    assert step["evidence"]["tested"] is True
    assert step["evidence"]["checks"]


def test_never_tested_but_connected_keeps_the_link_hint(console):
    """No record is not a failure: the live link is still the "not tested yet" hint."""
    client, _, _ = console()
    client.app.state.audio = _Plane(connected=True)
    step = _set_test_step(_plan(client))
    assert step["status"] == "done"
    assert step["evidence"]["ok"] is True
    assert step["evidence"]["tested"] is False
    assert step["evidence"]["checks"] == []

    # Still no record, link down: pending and not ok, with `tested` still false.
    client.app.state.audio = _Plane(connected=False)
    step = _set_test_step(_plan(client))
    assert step["status"] == "pending"
    assert step["evidence"]["ok"] is False
    assert step["evidence"]["tested"] is False


# ------------------------------------ the test record belongs to one device only
def _no_record(step: dict) -> None:
    assert step["status"] == "pending"
    assert step["evidence"] == {"ok": False, "tested": False, "checks": [], "at": ""}


def test_a_test_record_never_leaks_to_another_device(console):
    """M6 review BLOCKER: living's pass must not complete study's test step.

    The setup store has one house-wide test slot, so the plan has to compare
    `device_id` before treating a record as evidence (and a draft has no id to match).
    """
    client, _, _ = console(devices=[_device(), _device(id="study", room="书房")])
    client.app.state.audio = _Plane(connected=True)

    tested = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.test", "values": {}},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()["result"]["ok"] is True

    _no_record(_set_test_step(_plan(client, "study")))


def test_a_failed_test_for_one_device_does_not_fail_another(console):
    """The reverse direction: study's failure must not turn living's step pending."""
    client, _, _ = console(
        devices=[_device(), _device(id="study", room="书房", enabled=False)])
    client.app.state.audio = _Plane(connected=True)

    failed = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "study",
              "action": "device.test", "values": {}},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["result"]["ok"] is False

    step = _set_test_step(_plan(client, "living"))
    # No record for living -> the inbound link is the only hint, and it says "untested".
    assert step["status"] == "done"
    assert step["evidence"] == {"ok": True, "tested": False, "checks": [], "at": ""}


def test_a_draft_device_never_inherits_a_record_or_a_link_hint(console):
    """A draft id does not exist yet: someone else's record and link are not its own."""
    client, _, _ = console()
    client.app.state.audio = _Plane(connected=True)
    tested = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.test", "values": {}},
    )
    assert tested.json()["result"]["ok"] is True

    draft = _plan(client, "study")
    assert draft["mode"] == "add"
    assert draft["device_id"] == "study"
    _no_record(_set_test_step(draft))
    connect = next(step for step in draft["steps"] if step["id"] == "connect")
    assert connect["status"] == "pending"


def test_an_outbound_device_without_a_record_stays_pending(console, tmp_path):
    """Outbound has no inbound link to hint with (and a neighbour's record is not one)."""
    _write_outbound_plug(tmp_path)
    outbound = _device(id="study", adapter="outbound-demo", host="10.0.0.9",
                       room="书房", params={"plug_id": "demo-outbound"})
    client, _, _ = console(devices=[_device(), outbound])
    client.app.state.audio = _Plane(connected=True)
    tested = client.post(
        "/api/v2/setup/plan/steps/test", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.test", "values": {}},
    )
    assert tested.json()["result"]["ok"] is True

    plan = _plan(client, "study", plugin_id="demo-outbound")
    assert plan["blocked"] is None
    assert plan["plugin"]["id"] == "demo-outbound"
    connect = next(step for step in plan["steps"] if step["id"] == "connect")
    assert connect["status"] == "skipped"  # outbound: there is no inbound link
    _no_record(_set_test_step(plan))


def test_a_corrupt_test_record_never_passes_and_never_500s(console):
    """setup.json is user-editable: bad layers are "no record", wrong `ok` is a fail."""
    client, config, _ = console()
    client.app.state.audio = _Plane(connected=False)
    setup = Path(config.system.data_dir) / "setup.json"
    setup.parent.mkdir(parents=True, exist_ok=True)
    cases = {
        # `"false"` is a truthy string; only the JSON boolean true may pass.
        "string ok": ({"test": {"device": {"test": {
            "ok": "false", "device_id": "living"}}}}, True),
        # A non-dict anywhere in the chain must read as "no record", not AttributeError.
        "test layer": ({"test": ["boom"]}, False),
        "device layer": ({"test": {"device": "boom"}}, False),
        # A record without `ok` is an incomplete probe: conservative failure.
        "missing ok": ({"test": {"device": {"test": {"device_id": "living"}}}}, True),
    }
    for name, (payload, has_record) in cases.items():
        setup.write_text(json.dumps(payload), encoding="utf-8")
        step = _set_test_step(_plan(client))
        assert step["status"] == "pending", name
        assert step["evidence"]["ok"] is False, name
        assert step["evidence"]["tested"] is has_record, name


def test_a_blocked_plan_keeps_the_test_evidence_shape(console):
    """§7.23: a blocked plan still carries the test step's `tested`/`ok` fields."""
    client, _, _ = console()
    body = client.get("/api/v2/setup/plan?plugin_id=no-such-plug", headers=AUTH).json()
    assert body["blocked"]["code"] == "plug_unavailable"
    _no_record(_set_test_step(body))


def test_a_failed_keys_test_never_completes_the_keys_step(console):
    client, _, _ = console()
    response = client.post(
        "/api/v2/setup/plan/steps/keys", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "keys.test",
              # An explicitly malformed key fails the format check without a network
              # call, so this test stays deterministic and offline.
              "values": {"stage": "llm", "keys": {"RUI_LLM_KEY": "x"}}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["ok"] is False
    keys = next(step for step in body["plan"]["steps"] if step["id"] == "keys")
    assert keys["status"] == "pending"
    assert keys["evidence"]["stages"]["llm"] is False
    assert body["plan"]["next_step"] == "keys"


def test_a_failed_verify_say_never_marks_the_plan_verified(console):
    client, _, _ = console()
    # The link is up (so the action gate passes) but there is no audio client to drive.
    client.app.state.audio = _Plane(connected=True)
    failed = client.post(
        "/api/v2/setup/plan/steps/verify", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "verify.say", "values": {"text": "你好"}},
    )
    assert failed.status_code == 409, failed.text
    plan = _plan(client)
    assert plan["verified"] is False
    verify = next(step for step in plan["steps"] if step["id"] == "verify")
    assert verify["status"] == "pending"
    assert verify["evidence"]["text_tested"] is False

    # Real evidence is what flips the flag: a turn that actually produced audio.
    last = getattr(client.app.state, "last_turn", None)
    assert last is None or int(last.get("audio_bytes") or 0) == 0
    client.app.state.last_turn = {"audio_bytes": 640, "reply": "你好"}
    plan = _plan(client)
    assert plan["verified"] is True
    verify = next(step for step in plan["steps"] if step["id"] == "verify")
    assert verify["status"] == "done"
    assert verify["evidence"]["text_tested"] is True


def test_house_audio_read_has_explicit_and_effects(console):
    client, _, _ = console()
    body = client.get("/api/v2/house/audio", headers=AUTH).json()
    assert set(body["audio"]) == {"capture", "voice", "playback", "aec"}
    assert body["ack"]["echo_ignore_ms"] == 1200
    assert "ack.echo_ignore_ms" not in body["explicit"]
    assert body["effects"]["restart"] == "audio_plane"
    assert "audio.capture.rate" in body["effects"]["fields"]


def test_rotate_refuses_a_token_from_the_environment(console, monkeypatch):
    monkeypatch.setenv("RUI_DEVICE_TOKEN", "environment-token")
    client, _, _ = console()
    response = client.post("/api/v2/devices/living/token/rotate", headers=AUTH)
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "token_from_environment"
    assert body["error"]["details"]["token_env"] == "RUI_DEVICE_TOKEN"
    assert "environment-token" not in response.text


# --------------------------------------------------------------------- 0.6 blockers
#
# The independent review's write-path findings. Each test reproduces the measured
# failure: a cleared audio field that stayed explicit, a last-enabled-device write that
# left an unloadable file, setup mode/immutable rules that were not enforced, and the
# smaller §7.22/§7.13/§7.24/§4.4 gaps fixed in the same batch.

def _dot2_manifest_plug(data_dir: Path, *, capabilities: tuple[str, ...] = ("playback",),
                        ack_ms: int | None = None,
                        params: tuple[tuple[str, str, str], ...] = ()) -> None:
    """A manifest-only v1 plug resolved from `data_dir/plugs` (no adapter code)."""
    from ruamel.yaml import YAML

    root = data_dir / "plugs" / "rui-plugin-dot2"
    (root / "devices").mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    plugin = {
        "schema": "rui-plugin/v0",
        "id": "dot2",
        "name": "Echo Dot 2",
        "version": "1.2.0",
        "rui_compat": ">=0.5.0 <0.9.0",
        "license": "MIT",
        "maintainer": "rui maintainers",
        "homepage": "https://example.invalid/dot2",
        "trust": "trusted",
        "devices": ["devices/dot2.yaml"],
    }
    device: dict = {
        "schema": "rui-device/v1",
        "id": "dot2",
        "name": "Echo Dot 2",
        "vendor": "Amazon",
        "class": "A",
        "transport": "inbound",
        "protocol": {"kind": "ws", "version": "1"},
        "capabilities": list(capabilities),
        "params": [{"key": key, "label": label, "type": kind}
                   for key, label, kind in params],
        "compatibility": {"rui_min": "0.5.0", "tested_with": ["0.5.3"]},
    }
    if ack_ms is not None:
        device["audio_defaults"] = {"ack_echo_ignore_ms": ack_ms}
    with (root / "plugin.yaml").open("w", encoding="utf-8") as handle:
        yaml.dump(plugin, handle)
    with (root / "devices" / "dot2.yaml").open("w", encoding="utf-8") as handle:
        yaml.dump(device, handle)


def _manifest_device(**overrides) -> DeviceConfig:
    payload = {
        "id": "dot2",
        "adapter": "dot2",
        "host": "dot2.local",
        "room": "书房",
        # The manifest-only plug below declares no runtime block, so the config must
        # state the port: 0.8.0 has no 4412 fallback for the connect URL.
        "listen_port": 4412,
        "params": {"plug_id": "dot2"},
    }
    payload.update(overrides)
    return DeviceConfig(**payload)


def _yaml_document(path: Path) -> dict:
    from ruamel.yaml import YAML

    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def _multi_config() -> Config:
    return Config(
        web={"auth_token": TOKEN},
        devices=[
            _device(id="living", room="客厅", listen_port=8870),
            _device(id="study", room="书房", listen_port=8871),
        ],
        multi_device={
            "enabled": True,
            "default_output": "living",
            "arbitration": {"room_priority": ["living", "study"]},
        },
    )


def test_audio_patch_null_clears_the_global_field_back_to_the_manifest(console):
    """P1: `null` deletes the explicit global field instead of rewriting the default.

    Repro before the fix: the file kept `capture.rate: 16000`, `explicit` still listed
    `audio.capture.rate`, and the LX06's 48 kHz manifest default could never apply.
    """
    config = Config(web={"auth_token": TOKEN}, devices=[_device()])
    # 24000, not 48000: an explicit rate equal to the manifest reports the manifest as
    # the source (0.8.0 tie rule), so use a real disagreement to see "global" first.
    config.audio.capture.rate = 24000
    client, _, path = console(config=config)
    before = client.get("/api/v2/devices/living/audio", headers=AUTH).json()
    assert before["sources"]["capture.rate"] == "global-config"
    assert before["layers"]["capture.rate"]["effective"] == 24000
    assert [item["path"] for item in before["conflicts"]] == ["capture.rate"]

    response = client.patch("/api/v2/house/audio", headers=AUTH,
                            json={"audio": {"capture": {"rate": None}}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "audio.capture.rate" not in body["explicit"]
    assert body["audio"]["capture"]["rate"] == 16000   # the global block is neutral now
    document = _yaml_document(path)
    assert "rate" not in (document.get("audio") or {}).get("capture", {})

    after = client.get("/api/v2/devices/living/audio", headers=AUTH).json()
    assert after["sources"]["capture.rate"] == "device-manifest"
    assert after["layers"]["capture.rate"]["effective"] == 48000
    assert after["layers"]["capture.rate"]["global_explicit"] is False


def test_audio_patch_keeps_an_explicit_value_equal_to_the_core_default(console):
    """P1a: an explicit choice at the core default must survive the save."""
    client, _, path = console()
    response = client.patch("/api/v2/house/audio", headers=AUTH,
                            json={"audio": {"voice": {"rate": 16000}}})
    assert response.status_code == 200, response.text
    assert "audio.voice.rate" in response.json()["explicit"]

    document = _yaml_document(path)
    assert document["audio"]["voice"]["rate"] == 16000
    after = client.get("/api/v2/devices/living/audio", headers=AUTH).json()
    assert after["sources"]["voice.rate"] == "global-config"
    assert after["layers"]["voice.rate"]["effective"] == 16000
    assert "audio.voice.rate" in client.get("/api/v2/house/audio",
                                            headers=AUTH).json()["explicit"]


def test_audio_patch_null_clears_the_ack_window_to_the_manifest(console):
    """P1c: clearing the cue window leaves the plug's measured 2500 ms in effect.

    0.8.0: the global 1500 ms never won while the manifest declared 2500 (the model's
    acoustic tail is authoritative); clearing still deletes the useless explicit
    value and removes the conflict warning.
    """
    config = Config(web={"auth_token": TOKEN}, devices=[_manifest_device()])
    config.ack.echo_ignore_ms = 1500
    client, _, path = console(config=config)
    _dot2_manifest_plug(Path(config.system.data_dir), ack_ms=2500)

    before = client.get("/api/v2/devices/dot2/audio", headers=AUTH).json()
    assert before["sources"]["ack.echo_ignore_ms"] == "device-manifest"
    assert before["effective"]["ack_echo_ignore_ms"] == 2500
    assert any(item["path"] == "ack.echo_ignore_ms" for item in before["conflicts"])

    response = client.patch("/api/v2/house/audio", headers=AUTH,
                            json={"ack": {"echo_ignore_ms": None}})
    assert response.status_code == 200, response.text
    assert "ack.echo_ignore_ms" not in response.json()["explicit"]
    assert "echo_ignore_ms" not in (_yaml_document(path).get("ack") or {})

    after = client.get("/api/v2/devices/dot2/audio", headers=AUTH).json()
    assert after["effective"]["ack_echo_ignore_ms"] == 2500
    assert after["sources"]["ack.echo_ignore_ms"] == "device-manifest"
    assert after["conflicts"] == []


def test_deleting_the_last_enabled_device_leaves_a_loadable_config(console):
    """P2: the file written after the last delete must boot (`load_config`)."""
    client, _, path = console(config=_multi_config())
    first = client.delete("/api/v2/devices/study?confirm=study", headers=AUTH)
    assert first.status_code == 200, first.text
    # A reference to the removed device (room_priority here) cannot survive the write.
    after_first = load_config(path)
    assert after_first.multi_device.arbitration.room_priority == ["living"]
    assert after_first.multi_device.default_output == "living"

    last = client.delete("/api/v2/devices/living?confirm=living", headers=AUTH)
    assert last.status_code == 200, last.text
    assert last.json() == {"removed": True, "device_id": "living"}

    fresh = load_config(path)          # raises ConfigError before the fix
    assert fresh.devices == []
    assert fresh.multi_device.enabled is False
    assert fresh.multi_device.default_output is None


def test_disabling_the_last_enabled_device_stops_the_plane(console):
    """P2/§4.5: 200 + `restart.status="stopped"`, and a config that still loads."""
    client, _, path = console(config=_multi_config())
    first = client.patch("/api/v2/devices/study", headers=AUTH, json={"enabled": False})
    assert first.status_code == 200, first.text

    last = client.patch("/api/v2/devices/living", headers=AUTH, json={"enabled": False})
    assert last.status_code == 200, last.text
    body = last.json()
    assert body["device"]["enabled"] is False
    assert body["restart"]["status"] == "stopped"
    assert body["restart"]["error"] == ""
    assert body["restart"]["scope"] == "fleet"

    fresh = load_config(path)
    assert [device.id for device in fresh.devices if device.enabled] == []
    assert fresh.multi_device.enabled is False


def test_setup_device_create_is_not_allowed_in_edit_mode(console):
    """P3: `device.create` in edit mode used to add a device and answer 200."""
    client, _, path = console()
    response = client.post(
        "/api/v2/setup/plan/steps/params", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "device.create",
              "values": {"id": "ghost", "room": "书房", "host": "192.168.1.50"}},
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "action_not_allowed"
    assert body["error"]["details"]["step"] == "params"
    assert body["error"]["details"]["mode"] == "edit"
    assert [device.id for device in load_config(path).devices] == ["living"]


def test_setup_device_update_refuses_immutable_fields_and_never_creates(console):
    """P3: every §7.13 immutable field in `values` is a 422, and edit never creates."""
    client, _, path = console()
    for name, value in (("id", "ghost"), ("plugin_id", "xiaoai-lx06"),
                        ("model_id", "lx06"), ("adapter", "xiaoai")):
        response = client.post(
            "/api/v2/setup/plan/steps/params", headers=AUTH,
            json={"plugin_id": "xiaoai-lx06", "device_id": "living",
                  "action": "device.update", "values": {name: value, "room": "厨房"}},
        )
        assert response.status_code == 422, (name, response.text)
        body = response.json()
        assert body["error"]["code"] == "immutable_field"
        assert body["error"]["details"]["fields"][0]["path"] == name
        assert body["error"]["details"]["fields"][0]["code"] == "immutable"
        assert [device.id for device in load_config(path).devices] == ["living"]


def test_setup_device_update_is_allowed_only_for_an_existing_device(console):
    """P3: device.update with no existing device is a mode error, not `device_exists`."""
    empty = Config(web={"auth_token": TOKEN}, devices=[])
    client, _, _ = console(config=empty)
    response = client.post(
        "/api/v2/setup/plan/steps/params", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "study",
              "action": "device.update", "values": {"room": "书房"}},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "action_not_allowed"
    assert response.json()["error"]["details"]["allowed"] == ["device.create"]


def test_add_mode_keys_save_is_not_blocked_by_the_house_steps(console):
    """P4: only device-scoped steps need the house keys/models to be complete."""
    client, _, _ = console()
    response = client.post(
        "/api/v2/setup/plan/steps/keys", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "study",
              "action": "keys.save", "values": {"values": {"RUI_LLM_KEY": "fake-key"}}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["blocked"]["code"] == "house_setup_incomplete"
    assert "fake-key" not in response.text


def test_verify_say_offline_is_device_not_connected(console):
    """P5: the wizard walks §7.21, so an offline device is 409 device_not_connected."""
    client, _, _ = console()
    response = client.post(
        "/api/v2/setup/plan/steps/verify", headers=AUTH,
        json={"plugin_id": "xiaoai-lx06", "device_id": "living",
              "action": "verify.say", "values": {"text": "你好"}},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "device_not_connected"
    assert body["error"]["hint"]
    assert body["error"]["details"] == {"id": "living"}


def test_verify_say_requires_the_playback_capability(console):
    """P5: a device without `playback` is refused before anything speaks."""
    config = Config(web={"auth_token": TOKEN}, devices=[_manifest_device()])
    client, _, _ = console(config=config)
    _dot2_manifest_plug(Path(config.system.data_dir), capabilities=("led",))
    response = client.post(
        "/api/v2/setup/plan/steps/verify", headers=AUTH,
        json={"plugin_id": "dot2", "device_id": "dot2",
              "action": "verify.say", "values": {"text": "你好"}},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "capability_unsupported"
    assert body["error"]["details"]["capability"] == "playback"


def test_house_patch_reports_the_offending_device_component(console):
    """P6: a Config-level 422 names `devices[0].room`, not an empty path."""
    config = Config(web={"auth_token": TOKEN},
                    devices=[_device(id="living", room=""),
                             _device(id="study", room="书房")])
    client, _, _ = console(config=config)
    response = client.patch("/api/v2/house", headers=AUTH,
                            json={"multi_device": {"enabled": True}})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "invalid_values"
    assert body["error"]["details"]["fields"][0]["path"] == "devices[0].room"


def test_house_patch_reports_the_default_output_path(console):
    """P6: `multi_device.default_output` is the dotted path for a bad reference."""
    client, _, _ = console()
    response = client.patch("/api/v2/house", headers=AUTH,
                            json={"multi_device": {"default_output": "ghost"}})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["fields"][0]["path"] == (
        "multi_device.default_output")


def test_house_audio_patch_reports_the_capture_component(console):
    """P6: an invalid capture layout names `audio.capture.channels`."""
    client, _, _ = console()
    response = client.patch("/api/v2/house/audio", headers=AUTH,
                            json={"audio": {"capture": {"channels": 8}}})
    assert response.status_code == 422, response.text
    fields = response.json()["error"]["details"]["fields"]
    assert fields[0]["path"] == "audio.capture.channels"
    assert "must equal channels(8)" in fields[0]["message"]


def test_activity_type_filter_applies_before_the_limit(console):
    """P8: `type=` filters the collection that `total`/`truncated` describe."""
    client, config, _ = console()
    client.app.state.recent_turns = [{
        "turn_id": "t1", "device_id": "living", "ts_epoch": 100.0,
        "transcript": "把灯关了", "reply": "已关闭", "error": "", "tools": [],
    }]
    log_path = Path(config.system.data_dir) / "service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("2026-10-25 21:00:00 INFO living link: connected\n",
                        encoding="utf-8")

    turns = client.get("/api/v2/devices/living/activity?type=turn&limit=1",
                       headers=AUTH).json()
    assert [item["type"] for item in turns["items"]] == ["turn"]
    assert turns["total"] == 1
    assert turns["truncated"] is False

    logs = client.get("/api/v2/devices/living/activity?type=log&limit=1",
                      headers=AUTH).json()
    assert [item["type"] for item in logs["items"]] == ["log"]
    assert logs["total"] == 1
    assert logs["truncated"] is False

    both = client.get("/api/v2/devices/living/activity?limit=1", headers=AUTH).json()
    assert both["total"] == 2
    assert both["truncated"] is True


def test_unknown_params_are_ignored_not_stored_or_deleted(console):
    """P9/§7.13: an undeclared param is ignored, including a `null` on it."""
    device = _device(params={"plug_id": "xiaoai-lx06", "mic_gain_db": 18})
    client, _, path = console(devices=[device])
    response = client.patch(
        "/api/v2/devices/living", headers=AUTH,
        json={"params": {"not_declared": "x", "mic_gain_db": None,
                         "weird": {"nested": True}}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device"]["params"]["mic_gain_db"] == 18
    assert "not_declared" not in body["device"]["params"]
    ignored = {name
               for warning in body["device"]["warnings"]
               if warning["code"] == "unknown_params_ignored"
               for name in warning.get("params") or []}
    assert {"not_declared", "weird"} <= ignored

    fresh = load_config(path)
    assert fresh.devices[0].params == {"plug_id": "xiaoai-lx06", "mic_gain_db": 18}


def test_create_ignores_undeclared_params(console):
    """P9/§7.11: create stores only declared params and reports the ignored ones."""
    client, _, path = console()
    response = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "study", "room": "书房", "plugin_id": "xiaoai-lx06",
        "host": "192.168.1.9", "listen_port": 8870,
        "params": {"not_declared": "x"},
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert "not_declared" not in body["device"]["params"]
    ignored = {name
               for warning in body["device"]["warnings"]
               if warning["code"] == "unknown_params_ignored"
               for name in warning.get("params") or []}
    assert "not_declared" in ignored
    created = next(item for item in load_config(path).devices if item.id == "study")
    assert "not_declared" not in created.params


def test_null_removes_a_declared_param_only(console):
    """P9/§7.13: `null` deletes a declared key; an undeclared `null` does nothing."""
    config = Config(web={"auth_token": TOKEN}, devices=[
        _manifest_device(params={"plug_id": "dot2", "oww_model": "x"})])
    client, _, path = console(config=config)
    _dot2_manifest_plug(Path(config.system.data_dir),
                        params=(("oww_model", "OWW model", "string"),))

    ignored = client.patch("/api/v2/devices/dot2", headers=AUTH,
                           json={"params": {"legacy_key": None}})
    assert ignored.status_code == 200, ignored.text
    assert ignored.json()["device"]["params"]["oww_model"] == "x"

    removed = client.patch("/api/v2/devices/dot2", headers=AUTH,
                           json={"params": {"oww_model": None}})
    assert removed.status_code == 200, removed.text
    assert "oww_model" not in removed.json()["device"]["params"]
    assert "oww_model" not in load_config(path).devices[0].params


def test_disabling_the_default_output_clears_it_and_says_so(console):
    """MINOR-2: the repair is visible, but only on the device that caused it."""
    client, _, path = console(config=_multi_config())
    other = client.patch("/api/v2/devices/study", headers=AUTH, json={"enabled": False})
    assert other.status_code == 200, other.text
    assert not [item for item in other.json()["device"]["warnings"]
                if item["code"] == "default_output_cleared"]

    response = client.patch("/api/v2/devices/living", headers=AUTH,
                            json={"enabled": False})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device"]["enabled"] is False
    warning = next(item for item in body["device"]["warnings"]
                   if item["code"] == "default_output_cleared")
    assert warning["message"] and warning["hint"]
    assert "most recently active room" in warning["hint"]
    assert set(warning) == {"code", "message", "hint"}

    fresh = load_config(path)
    assert fresh.multi_device.default_output is None
    assert fresh.multi_device.enabled is False


@pytest.mark.parametrize("declared", [("playback",), ("led",)])
def test_a_disabled_device_reports_device_disabled_before_the_capability_gate(
        console, declared):
    """`device_disabled` wins the capability gate for any declared set.

    The enabled check runs before the capability check, so an operator looking at a
    disabled speaker always gets the one fix that applies instead of
    `capability_unsupported` (which can be an artifact of the missing adapter probe).
    """
    config = Config(web={"auth_token": TOKEN},
                    devices=[_manifest_device(enabled=False)])
    client, _, _ = console(config=config)
    _dot2_manifest_plug(Path(config.system.data_dir), capabilities=declared)
    response = client.post("/api/v2/devices/dot2/actions", headers=AUTH,
                           json={"action": "audio.say", "args": {"text": "你好"}})
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "device_disabled"
    assert body["error"]["hint"]
    assert body["error"]["details"] == {"id": "dot2"}


def test_an_enabled_device_without_the_capability_says_capability_unsupported(console):
    """Once enabled, the capability gate still runs (C-002 §5.4 step 3)."""
    config = Config(web={"auth_token": TOKEN},
                    devices=[_manifest_device(enabled=True)])
    client, _, _ = console(config=config)
    _dot2_manifest_plug(Path(config.system.data_dir), capabilities=("led",))
    refusal = client.post("/api/v2/devices/dot2/actions", headers=AUTH,
                          json={"action": "audio.say", "args": {"text": "你好"}})
    assert refusal.status_code == 409, refusal.text
    body = refusal.json()
    assert body["error"]["code"] == "capability_unsupported"
    assert body["error"]["details"] == {"capability": "playback", "declared": ["led"]}


def test_led_set_is_gated_on_the_live_link_before_args(console):
    """C-002 §5.4 step 4: `led.set` is a live-link action.

    Without an audio plane the refusal must be 409 `device_not_connected` with an
    actionable hint - for malformed args too, which otherwise answered 422 first.
    """
    client, _, _ = console()
    for args in ({"effect": "nope"}, {"effect": "off"}):
        response = client.post("/api/v2/devices/living/actions", headers=AUTH,
                               json={"action": "led.set", "args": args})
        assert response.status_code == 409, (args, response.text)
        body = response.json()
        assert body["error"]["code"] == "device_not_connected"
        assert body["error"]["hint"]


def test_every_contract_live_action_is_gated_without_a_plane(console):
    """The §5.4 live-link list: audio.say/play/stop, volume.read/set, led.set."""
    client, _, _ = console()
    cases = (
        ("volume.read", {}),
        ("volume.set", {"value": 120}),
        ("led.set", {"effect": "off"}),
        ("audio.say", {"text": "你好"}),
        ("audio.play", {"file": "missing.wav"}),
        ("audio.stop", {}),
    )
    for action, args in cases:
        response = client.post("/api/v2/devices/living/actions", headers=AUTH,
                               json={"action": action, "args": args})
        assert response.status_code == 409, (action, response.text)
        error = response.json()["error"]
        assert error["code"] == "device_not_connected", action
        assert error["hint"], action
