"""task-22: the real 5-plane layout (lx06 + 4x Dot2) — params write-through and limits.

The 2026-10-28 rollout (living/儿童房/次卧/主卧/卫生间, ports 4412/8870/8871/8873/8874)
needs three house-level facts proven before touching the NAS config:

1. ``POST /api/v2/devices`` with ``params.control_port`` / ``params.loudness_file``
   persists the *requested* values (never the global ``DOT2_*`` env fallback) and the
   adapter builder is handed the same values. The console list row intentionally
   carries only ``plugin_id``; the detail row carries ``params``.
2. ``control_port`` is an adapter parameter, not a listener: the core keeps ``0`` as
   ``0`` (a falsy value is data, not "unset"), never resolves it into a listen port and
   never counts it in ``port_conflict``. Two devices with distinct ``listen_port``
   create cleanly; two with the same one are still refused.
3. ``multi_device.max_planes=5`` starts five planes, accepts the sixth device write
   (over-limit is a warning by design, not a 4xx) and names it in
   ``/health.multi_device.refused`` rather than queueing it.

The manifest below mirrors rui-dot2-bridge 1.3.2's ``rs03qr.yaml`` declarations from
C-001 §1; the bridge itself is out of scope for this task.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rui.config import Config, DeviceConfig, load_config, save_config
from rui.device import registry
from rui.web.api import app as app_module
from rui.web.api.app import create_app
from rui.web.api.device_rules import effective_listen_port, port_conflict

TOKEN = "test-token-real-layout"
AUTH = {"X-Auth-Token": TOKEN}
PLUG_ID = "dot2-echomuse"
MODEL_ID = "dot2"

#: The real install, exactly as 9哥 laid it out (id, room, port, adapter).
REAL_LAYOUT: tuple[tuple[str, str, int, str], ...] = (
    ("living", "客厅", 4412, "mock"),
    ("dot2", "儿童房", 8870, MODEL_ID),
    ("dot2-b", "次卧", 8871, MODEL_ID),
    ("dot2-c", "主卧", 8873, MODEL_ID),
    ("dot2-d", "卫生间", 8874, MODEL_ID),
)


def _rooms(devices: list[DeviceConfig]) -> dict[str, str]:
    """Rooms read from the config — tests must not carry a second hardcoded map."""
    return {device.id: device.room for device in devices}


def _device(device_id: str, room: str, port: int, adapter: str = MODEL_ID,
            **overrides: object) -> DeviceConfig:
    payload: dict[str, object] = {
        "id": device_id,
        "adapter": adapter,
        "host": f"{device_id}.local",
        "room": room,
        "listen_port": port,
        "params": {"plug_id": PLUG_ID} if adapter == MODEL_ID else {},
    }
    payload.update(overrides)
    return DeviceConfig(**payload)


def _dot2_plug(data_dir: Path) -> None:
    """Install a manifest-only plug declaring the two C-001 params we write.

    Mirrors ``rui-dot2-bridge/src/rui_dot2_bridge/devices/rs03qr.yaml``: a per-device
    loudness state file and a loopback control port whose default is 0 (off).
    """
    from ruamel.yaml import YAML

    root = data_dir / "plugs" / "rui-plugin-dot2-echomuse"
    (root / "devices").mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    plugin = {
        "schema": "rui-plugin/v0",
        "id": PLUG_ID,
        "name": "Echo Dot 2 (EchoMuse)",
        "version": "1.3.2",
        "rui_compat": ">=0.7.0 <0.9.0",
        "license": "MIT",
        "maintainer": "rui maintainers",
        "homepage": "https://example.invalid/dot2",
        "trust": "trusted",
        "devices": ["devices/dot2.yaml"],
    }
    device = {
        "schema": "rui-device/v1",
        "id": MODEL_ID,
        "name": "Echo Dot 2",
        "vendor": "Amazon",
        "class": "A",
        "transport": "inbound",
        "protocol": {"kind": "ws", "version": "1"},
        "capabilities": ["playback"],
        "runtime": {
            "mode": "in-process",
            "entrypoint": "rui_dot2_bridge.adapter:build",
            "listen_port": 8870,
            "token_env": "DOT2_EM_TOKEN",
        },
        "params": [
            {"key": "control_port", "label": "响度控制端口", "type": "int",
             "default": 0},
            {"key": "loudness_file", "label": "响度状态文件", "type": "string",
             "default": ""},
        ],
        "compatibility": {"rui_min": "0.7.0", "tested_with": ["0.8.0"]},
    }
    with (root / "plugin.yaml").open("w", encoding="utf-8") as handle:
        yaml.dump(plugin, handle)
    with (root / "devices" / "dot2.yaml").open("w", encoding="utf-8") as handle:
        yaml.dump(device, handle)


@pytest.fixture()
def console(tmp_path, monkeypatch):
    """A real config file on disk, no audio process, device token env cleared.

    The ``DOT2_*`` variables are deliberately *not* touched: the core never reads
    them (they are the adapter's fallback, C-001 §2), so an env-based assertion here
    would only look like coverage.
    """
    for name in ("RUI_DEVICE_TOKEN", "DOT2_EM_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def build(devices: list[DeviceConfig] | None = None,
              config: Config | None = None, *,
              start_audio: bool = False) -> tuple[TestClient, Config, Path]:
        if config is None:
            config = Config(web={"auth_token": TOKEN},
                            devices=[item.model_copy(deep=True)
                                     for item in (devices or [])])
        config.system.data_dir = str(tmp_path / "data")
        config.audio.voice.kws_keywords_file = str(tmp_path / "keywords.txt")
        _dot2_plug(Path(config.system.data_dir))
        path = tmp_path / "config.yaml"
        save_config(path, config)
        return (TestClient(create_app(config, config_path=path,
                                     start_audio=start_audio)), config, path)

    return build


# ---------------------------------------------------------------- 1. params write-through
def test_create_persists_per_device_params_and_hands_them_to_the_adapter(
        console, monkeypatch):
    """RED-first claim: the two C-001 params survive the write and reach the builder.

    The core never reads ``DOT2_*``: those env names are the *adapter's* fallback
    (C-001 §2). What house owns is the exact-value round-trip — request → detail →
    config file → the ``DeviceConfig`` the builder receives — so that is what this
    test pins, without pretending an env override exists here.
    """
    client, _, path = console()

    seen: list[DeviceConfig] = []

    class _Adapter:
        pass

    def _builder(device: DeviceConfig, _config: Config) -> _Adapter:
        seen.append(device)
        return _Adapter()

    # The manifest-only plug has no adapter module in the core image; registering the
    # builder under its manifest device id is exactly what a downstream plug does.
    monkeypatch.setitem(registry._BUILDERS, MODEL_ID, _builder)

    response = client.post("/api/v2/devices", headers=AUTH, json={
        "id": "dot2-b",
        "room": "次卧",
        "plugin_id": PLUG_ID,
        "model_id": MODEL_ID,
        "host": "192.168.1.21",
        "listen_port": 8871,
        "wake_words": ["你好小瑞"],
        "volume_target": 60,
        "enabled": True,
        "params": {
            "control_port": 0,
            "loudness_file": "/var/lib/rui/loudness-dot2-b.json",
        },
    })
    assert response.status_code == 201, response.text
    body = response.json()["device"]
    assert body["params"]["control_port"] == 0
    assert body["params"]["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    assert body["unknown_params"] == []
    assert not [w for w in body["warnings"] if w["code"] == "unknown_params_ignored"]

    # On disk: the requested values, int and string, exactly as sent.
    fresh = load_config(path)
    stored = next(item for item in fresh.devices if item.id == "dot2-b")
    assert stored.params["plug_id"] == PLUG_ID
    assert stored.params["control_port"] == 0
    assert stored.params["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"

    # The detail view shows them; the list row carries the plug id only (by design).
    detail = client.get("/api/v2/devices/dot2-b", headers=AUTH).json()
    assert detail["params"]["control_port"] == 0
    assert detail["params"]["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    row = next(item for item in client.get("/api/v2/devices", headers=AUTH).json()["items"]
               if item["id"] == "dot2-b")
    assert row["plugin_id"] == PLUG_ID
    assert "params" not in row

    # The write path rebuilds the adapters (auto restart), so the builder already ran
    # once from that config; building again from the saved file must see the same data.
    assert seen, "the device write must rebuild the adapters"
    for device in seen:
        assert device.params["control_port"] == 0
        assert device.params["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    seen.clear()
    built = registry.build_adapters(load_config(path))
    assert [device.id for device in seen] == ["dot2-b"]
    assert seen[0].params["control_port"] == 0
    assert seen[0].params["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    assert "dot2-b" in built.adapters


def test_patch_from_null_params_writes_both_and_hands_them_to_the_adapter(
        console, monkeypatch):
    """The 2026-10-28 incident: dot2-b existed with no per-device params.

    C-001 deliberately does not backfill on read (HANDOFF: "明确不兜底"), so the
    operator PATCHes both keys once. This pins the whole repair path: integer ``0``
    survives (not "unset"), the file gets exactly the requested pair, and the fleet
    rebuild that follows the PATCH receives them.
    """
    config = Config(web={"auth_token": TOKEN}, devices=[
        _device("dot2-b", "次卧", 8871, params={"plug_id": PLUG_ID}),
    ], multi_device={"enabled": True, "default_output": "dot2-b"})
    client, _, path = console(config=config)

    before = load_config(path).devices[0].params
    assert before == {"plug_id": PLUG_ID}, "no implicit params may be synthesized on read"

    seen: list[DeviceConfig] = []

    class _Adapter:
        pass

    def _builder(device: DeviceConfig, _config: Config) -> _Adapter:
        seen.append(device)
        return _Adapter()

    monkeypatch.setitem(registry._BUILDERS, MODEL_ID, _builder)

    response = client.patch("/api/v2/devices/dot2-b", headers=AUTH, json={"params": {
        "control_port": 0,
        "loudness_file": "/var/lib/rui/loudness-dot2-b.json",
    }})
    assert response.status_code == 200, response.text
    body = response.json()["device"]
    assert body["params"]["control_port"] == 0
    assert body["params"]["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    assert body["unknown_params"] == []
    assert not [w for w in body["warnings"] if w["code"] == "unknown_params_ignored"]

    stored = next(item for item in load_config(path).devices if item.id == "dot2-b")
    assert stored.params == {
        "plug_id": PLUG_ID,
        "control_port": 0,
        "loudness_file": "/var/lib/rui/loudness-dot2-b.json",
    }

    # The PATCH rebuilds the fleet, so the builder already saw the repaired config.
    assert seen, "the device write must rebuild the adapters"
    for device in seen:
        assert device.params["control_port"] == 0
        assert device.params["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    seen.clear()
    built = registry.build_adapters(load_config(path))
    assert [device.id for device in seen] == ["dot2-b"]
    assert seen[0].params["control_port"] == 0
    assert seen[0].params["loudness_file"] == "/var/lib/rui/loudness-dot2-b.json"
    assert "dot2-b" in built.adapters


def test_params_survive_a_patch_without_rewriting_the_other_device(console):
    """Per-device write-through: patching one room cannot leak the neighbour's values."""
    config = Config(web={"auth_token": TOKEN}, devices=[
        _device("dot2", "儿童房", 8870,
                params={"plug_id": PLUG_ID, "control_port": 0,
                        "loudness_file": "/var/lib/rui/loudness-dot2.json"}),
        _device("dot2-b", "次卧", 8871,
                params={"plug_id": PLUG_ID, "control_port": 0,
                        "loudness_file": "/var/lib/rui/loudness-dot2-b.json"}),
    ], multi_device={"enabled": True, "default_output": "dot2"})
    client, _, path = console(config=config)

    response = client.patch("/api/v2/devices/dot2-b", headers=AUTH, json={"params": {
        "loudness_file": "/var/lib/rui/loudness-dot2-b-v2.json"}})
    assert response.status_code == 200, response.text
    assert response.json()["device"]["params"]["control_port"] == 0

    fresh = load_config(path)
    # Rooms round-trip from the config we wrote — no second literal map in the test.
    assert _rooms(fresh.devices) == {item.id: item.room for item in config.devices}
    stored = {item.id: item.params for item in fresh.devices}
    assert stored["dot2"]["loudness_file"] == "/var/lib/rui/loudness-dot2.json"
    assert stored["dot2-b"]["loudness_file"] == "/var/lib/rui/loudness-dot2-b-v2.json"
    assert stored["dot2"]["control_port"] == 0
    assert stored["dot2-b"]["control_port"] == 0


# ------------------------------------------------------------- 2. control_port / ports
def test_control_port_zero_stays_zero_and_is_never_a_listener(console):
    """``params.control_port: 0`` is data (control plane off), not "unset".

    The core must not resolve it into a listen port and must not compare it across
    devices: the adapter owns that loopback socket (C-001 §4: a bind failure degrades
    with a warning instead of aborting the plane).
    """
    config = Config(web={"auth_token": TOKEN}, devices=[
        _device("dot2", "儿童房", 8870,
                params={"plug_id": PLUG_ID, "control_port": 0,
                        "loudness_file": "/var/lib/rui/loudness-dot2.json"}),
        _device("dot2-b", "次卧", 8871,
                params={"plug_id": PLUG_ID, "control_port": 0,
                        "loudness_file": "/var/lib/rui/loudness-dot2-b.json"}),
    ], multi_device={"enabled": True, "default_output": "dot2"})
    client, _, path = console(config=config)

    fresh = load_config(path)
    assert _rooms(fresh.devices) == {item.id: item.room for item in config.devices}
    for device in fresh.devices:
        assert device.params["control_port"] == 0
        # The listener is listen_port; control_port never substitutes for it.
        assert effective_listen_port(device) == device.listen_port
        assert effective_listen_port(device) != 0

    # Same control_port on every plane is fine: the core never sees those sockets.
    assert port_conflict(fresh.devices, fresh.system.data_dir) is None

    # A device whose config leaves listen_port unset still resolves from the manifest
    # runtime (8870), never from control_port.
    from rui.plugins import PlugCatalog

    inherited = _device("dot2-c", "主卧", 0,
                        params={"plug_id": PLUG_ID, "control_port": 8872},
                        listen_port=None)
    resolved = PlugCatalog(fresh.system.data_dir).resolve(
        plug_id=PLUG_ID, adapter=MODEL_ID)
    assert resolved is not None
    assert effective_listen_port(inherited, resolved) == 8870


def test_distinct_listen_ports_create_cleanly_and_a_real_collision_is_still_refused(console):
    """8871/8873/8874 are unique; a duplicate must still be a port_conflict."""
    client, _, path = console()

    def create(device_id: str, room: str, port: int):
        return client.post("/api/v2/devices", headers=AUTH, json={
            "id": device_id, "room": room, "plugin_id": PLUG_ID,
            "model_id": MODEL_ID, "host": f"{device_id}.lan",
            "listen_port": port, "enabled": True,
            "params": {"control_port": 0,
                       "loudness_file": f"/var/lib/rui/loudness-{device_id}.json"},
        })

    for device_id, room, port in (("dot2-b", "次卧", 8871),
                                  ("dot2-c", "主卧", 8873),
                                  ("dot2-d", "卫生间", 8874)):
        response = create(device_id, room, port)
        assert response.status_code == 201, response.text
        assert response.json()["device"]["listen_port"] == port

    collision = create("dot2-e", "厨房", 8873)
    assert collision.status_code == 422, collision.text
    error = collision.json()["error"]
    assert error["code"] == "port_conflict"
    assert set(error["details"]["device_ids"]) == {"dot2-c", "dot2-e"}

    fresh = load_config(path)
    assert [item.id for item in fresh.devices] == ["dot2-b", "dot2-c", "dot2-d"]
    assert {item.params["control_port"] for item in fresh.devices} == {0}


# ------------------------------------------------------------------ 3. max_planes = 5
class _FakeHandle:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.sent: list[object] = []
        self.ready = None
        self.metrics = None
        self.last_wake = None

    async def send(self, message: object) -> None:
        self.sent.append(message)

    async def speak(self, pcm: bytes, **kwargs: object) -> None:
        return None

    async def reply_finished(self, *args: object, **kwargs: object) -> None:
        return None

    async def set_led(self, effect: str, repaint: bool = False) -> None:
        return None

    async def request_metrics(self, timeout_s: float = 0.5) -> dict:
        return {}


class _FakeSupervisor:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.client = _FakeHandle(device_id)
        self.client.metrics = SimpleNamespace(
            speaker_connected=True, output_owner="", gate_speaking=False)
        self.running = True

    def status(self) -> dict:
        return {
            "running": True,
            "device_id": self.device_id,
            "restarts": 0,
            "error": "",
            "metrics": {"speaker_connected": True, "speaker_rejected": 0,
                        "output_owner": ""},
        }


class _FakeFleet:
    """Records which ids the production `_start_fleet` hands to the spawner."""

    instances: list[_FakeFleet] = []

    def __init__(self, cfg: object, config_path: object = None, **kwargs: object) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self.start_errors: dict[str, str] = {}
        self.started_with: list[str] = []
        self._supervisors: dict[str, _FakeSupervisor] = {}
        self._handles: dict[str, _FakeHandle] = {}
        _FakeFleet.instances.append(self)

    async def start(self, device_ids: list[str], timeout_s: float = 15.0):
        self.started_with = list(device_ids)
        for device_id in device_ids:
            self._handles[device_id] = _FakeHandle(device_id)
            self._supervisors[device_id] = _FakeSupervisor(device_id)
        return self

    async def stop(self) -> None:
        return None

    def device_ids(self) -> list[str]:
        return list(self._supervisors)

    def handle(self, device_id: str) -> _FakeHandle:
        return self._handles[device_id]

    def supervisor(self, device_id: str) -> _FakeSupervisor:
        return self._supervisors[device_id]

    def running(self, device_id: str) -> bool:
        return device_id in self._supervisors

    def running_count(self) -> int:
        return len(self._supervisors)

    def status(self) -> dict:
        return {device_id: row.status()
                for device_id, row in self._supervisors.items()}


def test_max_planes_five_accepts_the_fifth_and_names_the_sixth_refused(
        console, monkeypatch):
    """Five planes is the real layout; the sixth is refused, not queued.

    Over-limit is deliberately a warning, not a 4xx (docs/multi-device-design.md D4:
    "超过上限明确告警、不排队"), so the write succeeds and `/health` names the room
    that has no plane.
    """
    _FakeFleet.instances.clear()
    monkeypatch.setattr(app_module, "PlaneFleet", _FakeFleet)

    config = Config(web={"auth_token": TOKEN}, devices=[
        _device(device_id, room, port, adapter)
        for device_id, room, port, adapter in REAL_LAYOUT
    ], multi_device={"enabled": True, "max_planes": 5, "default_output": "living"})
    client, _, path = console(config=config, start_audio=True)
    with client:  # TestClient context = the real lifespan, so `_start_fleet` runs
        fleet = _FakeFleet.instances[-1]
        assert fleet.started_with == [row[0] for row in REAL_LAYOUT]
        bodies = client.get("/health", headers=AUTH).json()
        assert bodies["multi_device"]["enabled"] is True
        assert bodies["multi_device"]["max_planes"] == 5
        assert bodies["multi_device"]["refused"] == []
        assert len(bodies["audio_devices"]) == 5

        sixth_payload = {
            "id": "dot2-e",
            "room": "厨房",
            "plugin_id": PLUG_ID,
            "model_id": MODEL_ID,
            "host": "192.168.1.25",
            "listen_port": 8875,
            "enabled": True,
            "params": {"control_port": 0,
                       "loudness_file": "/var/lib/rui/loudness-dot2-e.json"},
        }
        sixth = client.post("/api/v2/devices", headers=AUTH, json=sixth_payload)
        assert sixth.status_code == 201, sixth.text
        assert sixth.json()["restart"]["status"] == "ok"

        restarted = _FakeFleet.instances[-1]
        assert restarted.started_with == [row[0] for row in REAL_LAYOUT]
        assert "dot2-e" not in restarted.started_with
        health = client.get("/health", headers=AUTH).json()
        assert health["multi_device"]["refused"] == ["dot2-e"]
        over = [warning for warning in health["warnings"]
                if warning["code"] == "over_max_planes"]
        assert over and over[0]["details"]["refused_devices"] == ["dot2-e"]
        assert over[0]["details"]["max_planes"] == 5

    fresh = load_config(path)
    assert len(fresh.devices) == 6
    assert _rooms(fresh.devices) == {
        **{device_id: room for device_id, room, _, _ in REAL_LAYOUT},
        sixth_payload["id"]: sixth_payload["room"],
    }


# ------------------------------------------------------- 4. form-side guard (frontend)
def test_setup_form_blocks_empty_dot2_params() -> None:
    """The bridge manifest does not mark the two C-001 params `required`.

    A console operator adding a second/third Dot2 would otherwise submit an empty
    pair and silently inherit the global 8872 / shared loudness file. The params
    step is the only write path for a new device, so the guard must live there
    (and stay bilingual); `npm run typecheck` is the frontend gate.
    """
    root = Path(__file__).resolve().parents[1]
    vue = (root / "web/src/v2/components/setup/SetupParamsStep.vue").read_text(
        encoding="utf-8")
    assert "DOT2_PARAM_KEYS = ['control_port', 'loudness_file']" in vue
    assert vue.count("dot2ParamIssue(") >= 2, "the guard must run inside validation()"
    assert "paramRequired(spec)" in vue, "the label must show the form-side requirement"
    assert 'data-testid="setup-params-dot2-warning"' in vue
    assert "props.modelId === 'dot2' || props.pluginId === 'dot2-echomuse'" in vue

    i18n = (root / "web/src/v2/i18n.ts").read_text(encoding="utf-8")
    for key in ("v2.setup.params.dot2Warning",
                "v2.setup.params.dot2ControlPortRequired",
                "v2.setup.params.dot2LoudnessFileRequired"):
        assert i18n.count(f"'{key}'") == 2, f"{key} must exist in zh-Hans and en"
