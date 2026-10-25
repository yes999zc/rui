"""The control plane drives several audio planes when multi_device is enabled.

This is the app-level wiring test for Phase 2: a fake PlaneFleet replaces the process
spawner, so the assertions are about selection, callbacks and arbitration - not about
child processes (those are covered by test_audio_fleet.py).
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from rui.audio.protocol import Wake, WakeCandidate, WakeIdle
from rui.config import load_config
from rui.web.api import app as app_module
from rui.web.api.app import _PlaneLink, create_app

CONFIG = '''\
version: 3
web:
  host: 127.0.0.1
multi_device:
  enabled: true
  max_planes: 4
  default_output: living
  arbitration:
    candidate_merge_window_ms: 100
devices:
  - id: living
    adapter: mock
    host: 127.0.0.1
    room: 客厅
    wake_words: [你好小瑞]
  - id: study
    adapter: mock
    host: 127.0.0.1
    room: 书房
    wake_words: [你好小瑞]
'''

SINGLE_CONFIG = '''\
version: 2
web:
  host: 127.0.0.1
devices:
  - id: mockdev
    adapter: mock
    host: 127.0.0.1
    room: 书房
'''


class FakeHandle:
    """A plane handle that records everything the control plane sends it."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.sent: list[object] = []
        self.ready = None
        self.metrics = None
        self.last_wake = None
        self.led: list[str] = []
        self.spoken: list[bytes] = []
        self.replies = 0

    async def send(self, msg: object) -> None:
        self.sent.append(msg)

    async def speak(self, pcm: bytes, **kwargs: object) -> None:
        self.spoken.append(pcm)

    async def reply_finished(self, *args: object, **kwargs: object) -> None:
        self.replies += 1

    async def set_led(self, effect: str, repaint: bool = False) -> None:
        self.led.append(effect)


class FakeSupervisor:
    def __init__(self, device_id: str, running: bool = True) -> None:
        self.device_id = device_id
        self.client = None
        self.running = running

    def status(self) -> dict:
        return {
            "running": self.running,
            "device_id": self.device_id,
            "restarts": 0,
            "error": "" if self.running else "crashed",
            "metrics": {
                "speaker_connected": self.running,
                "speaker_rejected": 0,
                "output_owner": "",
            },
        }


class FakeFleet:
    """One supervised child per device, faked down to the messages they carry."""

    instances: list[FakeFleet] = []

    def __init__(self, cfg: object, config_path: object = None, **kwargs: object) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self._ids: list[str] = []
        self._handles: dict[str, FakeHandle] = {}
        self._supervisors: dict[str, FakeSupervisor] = {}
        self.start_errors: dict[str, str] = {}
        self.started_with: list[str] | None = None
        self.stopped = False
        FakeFleet.instances.append(self)

    async def start(self, device_ids: list[str], timeout_s: float = 15.0) -> FakeFleet:
        self.started_with = list(device_ids)
        for device_id in device_ids:
            self._ids.append(device_id)
            self._handles[device_id] = FakeHandle(device_id)
            self._supervisors[device_id] = FakeSupervisor(device_id)
        return self

    async def stop(self) -> None:
        self.stopped = True

    def device_ids(self) -> list[str]:
        return list(self._ids)

    def handle(self, device_id: str) -> FakeHandle:
        return self._handles[device_id]

    def supervisor(self, device_id: str) -> FakeSupervisor:
        return self._supervisors[device_id]

    def running(self, device_id: str) -> bool:
        return bool(self._supervisors[device_id].running)

    def running_count(self) -> int:
        return sum(1 for row in self._supervisors.values() if row.running)

    def status(self) -> dict:
        return {device_id: row.status() for device_id, row in self._supervisors.items()}


def _app(tmp_path, monkeypatch, config: str = CONFIG, factory: object = FakeFleet):
    path = tmp_path / "config.yaml"
    path.write_text(config, encoding="utf-8")
    monkeypatch.setattr(app_module, "PlaneFleet", factory)
    return create_app(load_config(path), config_path=path, start_audio=True)


def _candidate(wake_id: str, ts_ms: float = 0.0, keyword: str = "你好小瑞") -> WakeCandidate:
    return WakeCandidate(wake_id=wake_id, ts_ms=ts_ms, source="rui", keyword=keyword)


def test_fleet_starts_every_enabled_inbound_device(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        assert fleet.started_with == ["living", "study"]
        assert app.state.audio_fleet is fleet
        assert app.state.wake_coordinator is not None

        body = client.get("/health").json()
        # Old top-level fields survive; the aggregate is "any room", not "all rooms".
        assert isinstance(body["devices"], int)
        assert body["devices"] == 2
        assert body["audio_plane"] is True
        assert body["speaker_connected"] is True
        assert body["speaker_device"] == "living"  # default_output

        audio = client.get("/api/v1/audio").json()
        assert audio["running"] is True
        assert set(audio["planes"]) == {"living", "study"}
    assert fleet.stopped


def test_fleet_starts_at_most_max_planes_and_names_the_refused(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch,
               CONFIG.replace("max_planes: 4", "max_planes: 1"))
    with TestClient(app):
        fleet = FakeFleet.instances[-1]
        assert fleet.started_with == ["living"]
        assert app.state.fleet_refused == ["study"]
        assert app.state.fleet_error == ""


def test_fleet_start_failure_leaves_the_console_up(tmp_path, monkeypatch) -> None:
    class BrokenFleet(FakeFleet):
        async def start(self, device_ids, timeout_s=15.0):
            raise RuntimeError("two planes, one socket")

    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch, factory=BrokenFleet)
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["audio_plane"] is False  # no plane is running, but no crash loop
        assert "two planes, one socket" in app.state.fleet_error


def test_candidates_are_arbitrated_and_verdicts_reach_the_right_plane(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        study = fleet.handle("study")
        # Two rooms hear the same word; the arbiter collects them for the merge window.
        client.portal.call(living.on_wake_candidate, _candidate("w-living", 0.0))
        client.portal.call(study.on_wake_candidate, _candidate("w-study", 0.0))
        time.sleep(0.4)
        assert [msg.type for msg in living.sent] == ["wake_admit"]
        assert study.sent and study.sent[0].type == "wake_reject"
        assert study.sent[0].reason == "lost_same_word"  # type: ignore[attr-defined]


def test_wake_opens_the_turn_and_idle_closes_it(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        study = fleet.handle("study")
        arbiter = app.state.wake_coordinator.arbiter
        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))
        assert arbiter.state.turn_open is True
        # A different word from another room must not become a second turn (K2).
        client.portal.call(study.on_wake_candidate, _candidate("w-study", 1.0, keyword="开灯"))
        time.sleep(0.05)
        assert study.sent[-1].type == "wake_reject"
        assert study.sent[-1].reason == "turn_open"  # type: ignore[attr-defined]
        # A silent gate ends the turn, or every later wake would be rejected forever.
        client.portal.call(living.on_wake_idle, WakeIdle())
        assert arbiter.state.turn_open is False


def test_plane_link_claims_the_owner_when_it_speaks_and_closes_the_turn() -> None:
    events: list[tuple[str, object]] = []

    class Coordinator:
        def set_owner(self, device_id):
            events.append(("owner", device_id))

        def set_turn_open(self, open_):
            events.append(("turn", open_))

        def touch(self, device_id):
            events.append(("touch", device_id))

    app = SimpleNamespace(state=SimpleNamespace())
    handle = FakeHandle("living")
    link = _PlaneLink(app, "living", handle, Coordinator())
    asyncio.run(link.speak(b"pcm"))
    asyncio.run(link.speak(b"pcm"))
    assert events.count(("owner", "living")) == 1  # one claim per turn, not per block
    assert app.state.output_owner == "living"
    asyncio.run(link.reply_finished())
    assert ("turn", False) in events
    assert ("touch", "living") in events
    assert handle.replies == 1


def test_multi_device_disabled_never_builds_a_fleet(tmp_path, monkeypatch) -> None:
    class ExplodingFleet(FakeFleet):
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("PlaneFleet must not be built when multi_device is off")

    path = tmp_path / "config.yaml"
    path.write_text(SINGLE_CONFIG, encoding="utf-8")
    monkeypatch.setattr(app_module, "PlaneFleet", ExplodingFleet)
    app = create_app(load_config(path), config_path=path, start_audio=False)
    with TestClient(app):
        assert getattr(app.state, "audio_fleet", None) is None
        assert getattr(app.state, "wake_coordinator", None) is None
