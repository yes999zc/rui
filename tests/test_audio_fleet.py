"""The multi-plane fleet: N children, N sockets, N independent watchdogs.

The fleet is only the mapping from device ids to ``AudioPlaneSupervisor`` instances. These
tests pin the parts the console and /health depend on:

- one child per device, each with its own ``audio-<id>.sock`` (never the legacy
  ``audio.sock``) and its own watchdog;
- killing one child is not an event for its siblings: its own watchdog rebuilds it;
- stop/stop_device leave no child and no socket behind;
- ``max_planes`` refuses before spawning anything and names the extras;
- a single device that fails to launch does not take the rest of the house down, while
  a fleet where nothing starts is a startup error.

Orchestration tests replace ``fleet.AudioPlaneSupervisor`` with a fake that records
lifecycle calls. The two ``real`` tests at the end spawn the actual plane child with the
mock adapter (same pattern as tests/test_plane_supervisor.py): no microphone, no device,
no network.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from rui.audio import fleet as fleet_module
from rui.audio.client import AudioPlaneError, PlaneHandle
from rui.audio.fleet import PlaneFleet, fleet_socket_path
from rui.config import load_config

CONFIG = """\
version: 2
web:
  host: 127.0.0.1
devices:
  - id: living
    adapter: mock
    host: 127.0.0.1
    room: 客厅
  - id: study
    adapter: mock
    host: 127.0.0.1
    room: 书房
"""

# Same two devices, but the fleet is capped at one plane.
CONFIG_MAX_ONE = """\
version: 2
web:
  host: 127.0.0.1
multi_device:
  max_planes: 1
devices:
  - id: living
    adapter: mock
    host: 127.0.0.1
    room: 客厅
  - id: study
    adapter: mock
    host: 127.0.0.1
    room: 书房
"""


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


@pytest.fixture()
def max_one_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config-max-one.yaml"
    path.write_text(CONFIG_MAX_ONE, encoding="utf-8")
    return path


@pytest.fixture()
def socket_dir() -> Iterator[Path]:
    """A short socket directory: the AF_UNIX limit is why fleet paths stay short.

    pytest's tmp_path is deep enough on macOS that ``resolve_socket_path`` would move the
    file into the temp root, hiding the naming/lifecycle behaviour under test.
    """
    path = Path(tempfile.mkdtemp(prefix="rui-fleet-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeSupervisor:
    """Stands in for AudioPlaneSupervisor: records calls, spawns nothing."""

    def __init__(
        self,
        cfg: object,
        config_path: Path | None = None,
        socket_path: Path | None = None,
        device_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self.socket_path = Path(socket_path) if socket_path is not None else None
        self.device_id = device_id
        self.proc = None
        self.handle = PlaneHandle()
        self.error = ""
        self.restarts = 0
        self.fail_start = False
        self.start_calls = 0
        self.stop_calls = 0
        self.watch_calls = 0
        self._running = False

    async def start(self, timeout_s: float = 15.0) -> FakeSupervisor:
        self.start_calls += 1
        if self.fail_start:
            self.error = "fake plane refused to start"
            raise AudioPlaneError(self.error)
        self._running = True
        self.error = ""
        if self.socket_path is not None:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            # Stand-in for the AF_UNIX file the real child binds; stop must clean it.
            self.socket_path.write_text("", encoding="utf-8")
        return self

    def watch(self) -> None:
        self.watch_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict:
        return {
            "running": self._running,
            "pid": None,
            "restarts": self.restarts,
            "error": self.error or ("" if self._running else "audio plane is not running"),
        }


def install_fake(
    monkeypatch: pytest.MonkeyPatch, *, fail: tuple[str, ...] = ()
) -> list[FakeSupervisor]:
    """Replace the fleet's supervisor class; return the instances it creates."""
    created: list[FakeSupervisor] = []

    def _factory(*args: object, **kwargs: object) -> FakeSupervisor:
        fake = FakeSupervisor(*args, **kwargs)  # type: ignore[arg-type]
        fake.fail_start = fake.device_id in fail
        created.append(fake)
        return fake

    monkeypatch.setattr(fleet_module, "AudioPlaneSupervisor", _factory)
    return created


# --------------------------------------------------------------- orchestration (fake)
def test_start_spawns_one_plane_per_device_with_distinct_short_sockets(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        assert await fleet.start(["living", "study"]) is fleet
        try:
            assert fleet.device_ids() == ["living", "study"]
            assert [fake.device_id for fake in created] == ["living", "study"]
            paths = [fake.socket_path for fake in created]
            assert None not in paths
            assert paths[0] != paths[1]
            assert paths[0].name == "audio-living.sock"
            assert paths[1].name == "audio-study.sock"
            # A short socket dir is used verbatim, so the names stay inspectable.
            assert paths[0].parent == socket_dir
            assert all(path.exists() for path in paths)
            # One handle per device, distinct objects; a handle survives restarts.
            assert fleet.handle("living") is created[0].handle
            assert fleet.handle("study") is created[1].handle
            assert fleet.handle("living") is not fleet.handle("study")
            assert fleet.supervisor("living") is created[0]
            assert fleet.running("living") and fleet.running("study")
            assert fleet.running_count() == 2
            assert fleet.start_errors == {}
            # Each supervisor's crash watchdog is armed exactly once on start.
            assert [fake.watch_calls for fake in created] == [1, 1]
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_one_device_failing_to_start_does_not_stop_the_others(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch, fail=("study",))
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        # Chosen policy: start() does not raise for a partial failure; the failure is
        # reported per device and the healthy room keeps working.
        await fleet.start(["living", "study"])
        try:
            assert fleet.running("living") is True
            assert fleet.running("study") is False
            assert fleet.running_count() == 1
            assert set(fleet.start_errors) == {"study"}
            assert "fake plane refused" in fleet.start_errors["study"]
            status = fleet.status()
            assert set(status) == {"living", "study"}
            assert status["living"]["running"] is True
            assert "fake plane refused" in status["study"]["error"]
            # The failed device stays registered (so /health can show why) and gets its
            # own watchdog: it retries independently instead of being dropped silently.
            failed = fleet.supervisor("study")
            assert failed.watch_calls == 1
            assert created[0].stop_calls == 0  # the sibling was never touched
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_fleet_that_starts_nothing_raises_after_cleaning_up(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch, fail=("living", "study"))
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        with pytest.raises(AudioPlaneError, match="no audio plane started"):
            await fleet.start(["living", "study"])
        # No half-registered fleet is left behind for status() to advertise.
        assert fleet.device_ids() == []
        assert fleet.status() == {}
        assert fleet.start_errors == {}
        assert all(fake.stop_calls == 1 for fake in created)
        assert not any(path.exists() for fake in created if (path := fake.socket_path))

    asyncio.run(run())


def test_max_planes_refuses_extra_devices_before_spawning_anything(
    max_one_config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch)
    cfg = load_config(max_one_config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, max_one_config_path, socket_dir=socket_dir)
        with pytest.raises(AudioPlaneError) as caught:
            await fleet.start(["living", "study"])
        message = str(caught.value)
        # Explicit refusal: the limit and the extras are named, no silent truncation.
        assert "max_planes=1" in message
        assert "study" in message
        assert created == []
        assert fleet.device_ids() == []

    asyncio.run(run())


def test_restart_rebuilds_only_the_named_plane(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living", "study"])
        try:
            living, study = created
            await fleet.restart("living")
            assert living.start_calls == 2 and living.stop_calls == 1
            assert study.start_calls == 1 and study.stop_calls == 0
            assert fleet.running_count() == 2
            assert fleet.device_ids() == ["living", "study"]
            # Restart re-arms the rebuilt plane's watchdog, not its sibling's.
            assert living.watch_calls == 2
            assert study.watch_calls == 1
            assert fleet.start_errors == {}
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_update_config_reaches_every_supervisor(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living", "study"])
        try:
            # A reloaded config object (the console saves a new file, it does not mutate
            # the old object) must reach every existing supervisor and the next creation.
            reloaded = load_config(config_path)
            fleet.update_config(reloaded)
            assert fleet.cfg is reloaded
            assert all(fake.cfg is reloaded for fake in created)
            await fleet.restart("living")
            assert fleet.supervisor("living").cfg is reloaded
            assert fleet.supervisor("study").cfg is reloaded
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_failed_restart_is_reported_and_the_watchdog_stays_armed(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living", "study"])
        try:
            living = fleet.supervisor("living")
            living.fail_start = True
            with pytest.raises(AudioPlaneError, match="did not restart"):
                await fleet.restart("living")
            assert "fake plane refused" in fleet.start_errors["living"]
            assert fleet.status()["living"]["running"] is False
            # The supervisor is kept and re-armed so its own watchdog can recover it.
            assert living.watch_calls == 2
            assert fleet.running("study") is True

            # A later successful restart clears the recorded launch error.
            living.fail_start = False
            await fleet.restart("living")
            assert fleet.start_errors == {}
            assert fleet.running("living") is True
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_stop_device_stops_only_that_plane_and_removes_its_socket(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living", "study"])
        living, study = created
        try:
            await fleet.stop_device("living")
            assert living.stop_calls == 1
            assert study.stop_calls == 0
            assert fleet.device_ids() == ["study"]
            assert fleet.running("study") is True
            assert not living.socket_path.exists()
            assert study.socket_path.exists()
            with pytest.raises(KeyError):
                fleet.handle("living")
        finally:
            await fleet.stop()
        assert study.stop_calls == 1
        assert not study.socket_path.exists()
        assert fleet.status() == {}

    asyncio.run(run())


def test_unknown_devices_raise_keyerror_and_running_is_false(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living"])
        try:
            with pytest.raises(KeyError):
                fleet.handle("attic")
            with pytest.raises(KeyError):
                fleet.supervisor("attic")
            with pytest.raises(KeyError):
                await fleet.restart("attic")
            with pytest.raises(KeyError):
                await fleet.stop_device("attic")
            # Health aggregation must not fail on a device that is simply absent.
            assert fleet.running("attic") is False
            assert set(fleet.status()) == {"living"}
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_start_twice_is_refused(
    config_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(monkeypatch)
    cfg = load_config(config_path)

    async def run() -> None:
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living"])
        try:
            with pytest.raises(AudioPlaneError, match="already started"):
                await fleet.start(["study"])
        finally:
            await fleet.stop()

    asyncio.run(run())


def test_fleet_socket_path_is_named_after_the_device(socket_dir: Path) -> None:
    living = fleet_socket_path("living-room", socket_dir)
    assert living == socket_dir / "audio-living-room.sock"
    assert fleet_socket_path("living-room", socket_dir) == living  # deterministic
    # Characters a device id should never contain cannot escape the directory or make
    # two ids share one socket file.
    odd = fleet_socket_path("attic/../x", socket_dir)
    assert odd.parent == socket_dir
    assert odd != fleet_socket_path("attic___x", socket_dir)


# ------------------------------------------------------------ real child processes
def test_real_fleet_starts_two_planes_stops_them_and_removes_sockets(
    config_path: Path, socket_dir: Path
) -> None:
    async def run() -> None:
        cfg = load_config(config_path)
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living", "study"], timeout_s=20.0)
        supervisors = [fleet.supervisor(device_id) for device_id in fleet.device_ids()]
        pids = [supervisor.proc.pid for supervisor in supervisors if supervisor.proc is not None]
        paths = [supervisor.socket_path for supervisor in supervisors]
        try:
            assert fleet.device_ids() == ["living", "study"]
            assert fleet.running_count() == 2
            assert len(paths) == 2 and len(set(paths)) == 2
            assert all(path.exists() for path in paths)
            assert fleet.handle("living").ready is not None
            assert fleet.handle("living").ready.device_id == "living"
            assert fleet.handle("study").ready.device_id == "study"
            status = fleet.status()
            assert status["living"]["running"] is True
            assert status["study"]["running"] is True
        finally:
            await fleet.stop()

        assert fleet.device_ids() == []
        assert fleet.status() == {}
        assert all(not path.exists() for path in paths)
        assert all(supervisor.proc is None for supervisor in supervisors)
        assert len(pids) == 2
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)

    asyncio.run(run())


def test_real_fleet_crash_isolation_and_independent_watchdogs(
    config_path: Path, socket_dir: Path
) -> None:
    """SIGKILL one child: its own watchdog rebuilds it, the other room never notices."""

    async def run() -> None:
        cfg = load_config(config_path)
        fleet = PlaneFleet(cfg, config_path, socket_dir=socket_dir)
        await fleet.start(["living", "study"], timeout_s=20.0)
        living = fleet.supervisor("living")
        study = fleet.supervisor("study")
        try:
            assert fleet.running_count() == 2
            assert living.proc is not None and study.proc is not None
            living_pid = living.proc.pid
            study_pid = study.proc.pid
            assert living_pid is not None and study_pid is not None
            assert living_pid != study_pid

            os.kill(living_pid, signal.SIGKILL)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 25.0
            while loop.time() < deadline:
                await asyncio.sleep(0.1)
                if (
                    fleet.running("living")
                    and living.proc is not None
                    and living.proc.pid != living_pid
                ):
                    break

            assert fleet.running("living"), "the killed plane's watchdog never rebuilt it"
            assert living.proc is not None and living.proc.pid != living_pid
            assert living.restarts == 1
            # The sibling was not restarted, stopped or otherwise involved.
            assert fleet.running("study")
            assert study.proc is not None and study.proc.pid == study_pid
            assert study.restarts == 0
            assert fleet.running_count() == 2
            status = fleet.status()
            assert status["living"]["restarts"] == 1
            assert status["study"]["restarts"] == 0
        finally:
            await fleet.stop()

    asyncio.run(run())
