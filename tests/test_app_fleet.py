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

from rui.audio.protocol import (
    PauseMusic,
    PlayFile,
    StopAudio,
    Wake,
    WakeCandidate,
    WakeIdle,
)
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

    async def request_metrics(self, timeout_s: float = 0.5) -> dict:
        """`/api/v1/audio` asks each live client for a fresh snapshot."""
        return {}


class FakeSupervisor:
    def __init__(self, device_id: str, running: bool = True,
                 client: FakeHandle | None = None) -> None:
        self.device_id = device_id
        # The routing helpers resolve a room through `supervisor(id).client`, so the
        # fake exposes the same handle the callbacks were wired to (a real supervisor
        # does too: client and handle are two views of one plane).
        self.client = client if client is not None else FakeHandle(device_id)
        self.client.metrics = SimpleNamespace(
            speaker_connected=running, output_owner="", gate_speaking=False)
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
            handle = FakeHandle(device_id)
            self._handles[device_id] = handle
            self._supervisors[device_id] = FakeSupervisor(device_id, client=handle)
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


def _utter(portal, app, handle, text: str, monkeypatch) -> None:
    """Drive the real on_utterance closure with a fixed transcript."""
    runner = app.state.runner

    async def transcribe(pcm: bytes, trace: object) -> tuple[str, None]:  # noqa: ARG001
        return text, None

    monkeypatch.setattr(runner, "transcribe", transcribe)
    portal.call(handle.on_utterance, b"\x00\x00")


def _next_wake_is_admitted(client, handle, wake_id: str) -> str:
    client.portal.call(handle.on_wake_candidate, _candidate(wake_id))
    time.sleep(0.25)  # the coordinator's merge window is 100 ms in CONFIG
    return str(getattr(handle.sent[-1], "type", ""))


def test_a_canned_command_that_skips_reply_finished_releases_the_turn(
        tmp_path, monkeypatch) -> None:
    """「关闭上下文」 answers without a reply drain - and must not wedge turn_open.

    P0 2026-10-26: the dismissal path had the same shape (close the session, skip
    reply_finished) and every later wake was rejected turn_open until a restart. The
    utterance wrapper now releases the latch however the handler ends, without asking
    the plane for a follow-up window.
    """
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        coordinator = app.state.wake_coordinator
        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))
        assert coordinator.arbiter.state.turn_open is True

        _utter(client.portal, app, living, "关闭上下文", monkeypatch)

        assert app.state.last_turn["tools"] == ["context.off"]
        assert coordinator.arbiter.state.turn_open is False, (
            "the canned command left every later wake rejected turn_open")
        assert living.replies == 0, "a canned command must not open the follow-up window"
        assert _next_wake_is_admitted(client, living, "w-after-context") == "wake_admit"


def test_a_clarify_turn_that_skips_reply_finished_releases_the_turn(
        tmp_path, monkeypatch) -> None:
    """A bare device name answers 「打开还是关闭」 without reply_finished either."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        coordinator = app.state.wake_coordinator
        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))

        _utter(client.portal, app, living, "餐厅灯。", monkeypatch)

        assert app.state.last_turn["tools"] == ["clarify.verb"]
        assert coordinator.arbiter.state.turn_open is False, (
            "the clarify turn left every later wake rejected turn_open")
        assert living.replies == 0, "clarify must not open the follow-up window"
        assert _next_wake_is_admitted(client, living, "w-after-clarify") == "wake_admit"


def test_a_normal_reply_still_releases_the_turn_through_reply_finished(
        tmp_path, monkeypatch) -> None:
    """Regression: an ordinary answer keeps its reply_finished (follow-up window)."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = FakeFleet.instances[-1]
        living = fleet.handle("living")
        coordinator = app.state.wake_coordinator
        client.portal.call(living.on_wake, Wake(ts_ms=1.0, keyword="你好小瑞"))

        async def run(pcm, speak, trace, transcript=None, hint=None):  # noqa: ANN001, ARG001
            return SimpleNamespace(reply="好。", audio_bytes=0, tools=[], stages={},
                                   to_dict=lambda: {"transcript": "现在几点", "reply": "好。"})

        monkeypatch.setattr(app.state.runner, "run", run)
        _utter(client.portal, app, living, "现在几点", monkeypatch)

        assert coordinator.arbiter.state.turn_open is False
        assert living.replies == 1, "the ordinary turn lost its follow-up window"



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


def test_health_has_one_row_per_plane_and_keeps_the_old_aggregate(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert isinstance(body["devices"], int)  # old count field untouched
        assert body["audio_devices"]
        rows = {row["id"]: row for row in body["audio_devices"]}
        assert set(rows) == {"living", "study"}
        assert rows["living"]["room"] == "客厅"
        assert rows["living"]["running"] is True
        assert rows["living"]["speaker_connected"] is True
        assert rows["study"]["room"] == "书房"
        assert "wake_candidates" in rows["living"]
        assert body["multi_device"] == {
            "enabled": True, "max_planes": 4, "default_output": "living",
            "refused": [], "error": "",
        }


def test_multi_device_warnings_replace_the_single_plane_notice(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        cfg_now = app.state.cfg
        codes = {warning["code"] for warning in app_module._device_warnings(app, cfg_now)}
        assert "multiple_enabled_inbound" not in codes
        assert codes == set()
        # A crashed room is named, not silently painted green with the others.
        fleet = FakeFleet.instances[-1]
        fleet.supervisor("study").running = False
        warnings = app_module._device_warnings(app, cfg_now)
        assert [warning["code"] for warning in warnings] == ["plane_not_running"]
        assert warnings[0]["details"]["device"] == "study"
        assert warnings[0]["details"]["room"] == "书房"
        # ... and the device row says so too, without touching the healthy room.
        study = client.get("/api/v1/devices/study/status").json()
        assert "audio_plane_stopped" in study["errors"]
        living = client.get("/api/v1/devices/living/status").json()
        assert "audio_plane_stopped" not in living["errors"]


def test_max_planes_is_an_explicit_warning(tmp_path, monkeypatch) -> None:
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch,
               CONFIG.replace("max_planes: 4", "max_planes: 1"))
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["multi_device"]["refused"] == ["study"]
        warnings = app_module._device_warnings(app, app.state.cfg)
        over = [warning for warning in warnings if warning["code"] == "over_max_planes"]
        assert over and over[0]["details"]["refused_devices"] == ["study"]


def test_session_client_follows_the_turn_device(monkeypatch) -> None:
    """The goodbye must come from the room that spoke, not the primary one (D1)."""
    from types import SimpleNamespace

    from rui.web.api import app as app_module

    class _App:
        state = SimpleNamespace(turn_device="dot2")

    monkeypatch.setattr(
        app_module, "_client_for",
        lambda app, device_id="": f"client:{device_id}" if device_id else None,
    )
    assert app_module._session_client(_App()) == "client:dot2"

    _App.state.turn_device = ""
    monkeypatch.setattr(app_module, "_primary_client", lambda app: "client:primary")
    assert app_module._session_client(_App()) == "client:primary"




# ---------------------------------------------------------------- room routing (M3)
# A two-room mock fleet is the integration harness for 0.6 M3: every output must be
# routed by turn_device / default_output / output owner, never by "the first plane".


def test_goodbye_uses_the_room_that_spoke(tmp_path, monkeypatch) -> None:
    """The session goodbye is spoken by the room the person spoke to (D1)."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = app.state.audio_fleet
        client.portal.call(
            fleet.handle("study").on_wake, Wake(ts_ms=1.0, keyword="\u4f60\u597d\u5c0f\u745e"))
        assert app.state.turn_device == "study"
        assert app.state.last_active_room == "study"
        assert app_module._session_client(app) is fleet.supervisor("study").client
        # A later wake in the other room moves the goodbye there too - there is no
        # fixed primary speaker that keeps answering for everyone.
        client.portal.call(
            fleet.handle("living").on_wake, Wake(ts_ms=2.0, keyword="\u4f60\u597d\u5c0f\u745e"))
        assert app_module._session_client(app) is fleet.supervisor("living").client


def test_reminder_room_is_default_output_then_last_active(tmp_path, monkeypatch) -> None:
    """A reminder has no source room: default_output, else the last active room."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)          # default_output: living
    with TestClient(app) as client:
        fleet = app.state.audio_fleet
        client.portal.call(
            fleet.handle("study").on_wake, Wake(ts_ms=1.0, keyword="\u4f60\u597d\u5c0f\u745e"))
        assert app_module._reminder_client(app) is fleet.supervisor("living").client
        # K1/K4: an empty default_output falls back to the most recently active room.
        app.state.cfg.multi_device.default_output = None
        assert app_module._reminder_client(app) is fleet.supervisor("study").client
        # A room that is no longer configured must not win the fallback.
        app.state.last_active_room = "gone"
        assert app_module._reminder_client(app) is not None


def test_music_starts_in_the_turn_room_and_transport_follows_the_owner(
    tmp_path, monkeypatch
) -> None:
    """Music: new queue -> turn_device; pause/stop -> the room that has the song."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = app.state.audio_fleet
        study = fleet.handle("study")
        living = fleet.handle("living")
        sender = app_module._music_sender(app)

        app.state.turn_device = "study"
        app.state.last_active_room = "study"
        client.portal.call(sender, PlayFile(path="/tmp/song.mp3"))
        assert any(isinstance(msg, PlayFile) for msg in study.sent)
        assert not any(isinstance(msg, PlayFile) for msg in living.sent)
        app.state.output_owner = "study"

        # A transport command asked from another room still has to reach the song's
        # room (section 4.4): stopping the wrong plane leaves the song sounding.
        app.state.turn_device = "living"
        client.portal.call(sender, PauseMusic())
        assert isinstance(study.sent[-1], PauseMusic)
        assert not any(isinstance(msg, PauseMusic) for msg in living.sent)

        # Starting a new queue in living moves playback: the old owner is stopped and
        # the house's single owner is released before the new room sounds.
        client.portal.call(sender, PlayFile(path="/tmp/other.mp3"))
        assert any(isinstance(msg, StopAudio) for msg in study.sent)
        assert any(isinstance(msg, PlayFile) for msg in living.sent)
        assert app.state.output_owner == ""


def test_barge_only_the_output_owner_room_can_interrupt(tmp_path, monkeypatch) -> None:
    """Playing room ownership decides which barge is admitted (section 12.3.2)."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        fleet = app.state.audio_fleet
        living = fleet.handle("living")
        study = fleet.handle("study")
        # The living room is sounding; the arbiter gets the same owner the plane said.
        fleet.supervisor("living").client.metrics.output_owner = "dialogue"
        app.state.output_owner = "living"
        app.state.wake_coordinator.set_owner("living")

        client.portal.call(living.on_wake_candidate, WakeCandidate(
            wake_id="w-living", ts_ms=1.0, source="rui", keyword="\u4f60\u597d\u5c0f\u745e",
            barge=True, playing=True))
        time.sleep(0.05)
        assert living.sent and living.sent[-1].type == "wake_admit"

        client.portal.call(study.on_wake_candidate, WakeCandidate(
            wake_id="w-study", ts_ms=2.0, source="rui", keyword="\u4f60\u597d\u5c0f\u745e",
            barge=True, playing=True))
        time.sleep(0.05)
        assert study.sent[-1].type == "wake_reject"
        assert study.sent[-1].reason == "lost_playing"
        assert not any(msg.type == "wake_admit" for msg in study.sent)


def test_device_rows_carry_each_rooms_effective_audio(tmp_path, monkeypatch) -> None:
    """The console's per-device view: merged capture/playback/AEC/ack, not globals."""
    FakeFleet.instances.clear()
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        rows = {row["id"]: row for row in client.get("/api/v1/devices").json()}
        assert set(rows) == {"living", "study"}
        for row in rows.values():
            audio = row["audio"]
            assert audio["capture"]["rate"] == 16000
            assert audio["capture"]["mic_channels"] + audio["capture"]["ref_channels"] == \
                audio["capture"]["channels"]
            assert audio["playback"]["rate"] == 16000
            assert isinstance(audio["ack_echo_ignore_ms"], int)
            assert audio["overrides"] == []          # both rooms inherit everything
        detail = client.get("/api/v1/devices/study").json()
        assert detail["audio"]["capture"]["channels"] == 1
        assert detail["audio"]["aec"]["who"] in {"controller", "device", "none", "both"}

        # A devices[].audio override is merged into the effective value and named.
        from rui.config.schema import DeviceAudioOverride

        app.state.cfg.devices[1].audio = DeviceAudioOverride.model_validate(
            {"playback": {"rate": 24000}})
        row = {r["id"]: r for r in client.get("/api/v1/devices").json()}["study"]
        assert row["audio"]["playback"]["rate"] == 24000
        assert row["audio"]["overrides"] == ["playback.rate"]
        untouched = {r["id"]: r for r in client.get("/api/v1/devices").json()}["living"]
        assert untouched["audio"]["playback"]["rate"] == 16000


def test_wake_in_flight_detects_pending_candidates() -> None:
    """The goodbye must wait for a wake that is still being arbitrated."""
    from types import SimpleNamespace

    from rui.web.api import app as app_module

    class _Task:
        def __init__(self, done: bool) -> None:
            self._done = done

        def done(self) -> bool:
            return self._done

    app = SimpleNamespace(state=SimpleNamespace(wake_tasks={_Task(False)}))
    assert app_module._wake_in_flight(app) is True
    app.state.wake_tasks = {_Task(True)}
    assert app_module._wake_in_flight(app) is False
    app.state.wake_tasks = None
    assert app_module._wake_in_flight(app) is False
