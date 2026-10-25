"""The plane watchdog: a crashed child comes back, and the turn path survives it.

Two failures this pins, both measured as "the room went deaf until someone
restarted the service on the NAS":

- the supervisor spawned the plane exactly once, so a crash left /health green
  while no microphone was being read (Docker does not restart on unhealthy);
- the callbacks that make an utterance a turn were attached to one
  AudioPlaneClient, so even a manual reconnect would have produced a healthy,
  mute plane. PlaneHandle re-applies them on every bind.
"""
from __future__ import annotations

import asyncio
import os
import signal

import pytest

from rui.audio.client import AudioPlaneSupervisor, PlaneHandle
from rui.config import load_config

CONFIG = """\
version: 2
web:
  host: 127.0.0.1
devices:
  - id: mockdev
    adapter: mock
    host: 127.0.0.1
    room: 书房
"""


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


class FakeClient:
    def __init__(self) -> None:
        self.on_utterance = None
        self.on_wake = None
        self.on_vendor = None
        self.on_playback_finished = None
        self.on_wake_idle = None
        self.on_wake_candidate = None
        self.value = 1

    def ping(self) -> str:
        return "pong"


def test_handle_reapplies_callbacks_to_every_connection() -> None:
    handle = PlaneHandle()
    heard: list[bytes] = []

    def on_utterance(pcm: bytes) -> None:
        heard.append(pcm)

    handle.on_utterance = on_utterance          # set before any connection exists
    assert handle.ready is None and handle.metrics is None
    with pytest.raises(AttributeError):
        handle.ping()

    first = FakeClient()
    handle.bind(first)
    assert first.on_utterance is on_utterance
    assert handle.value == 1
    assert handle.ping() == "pong"

    # A restart swaps the connection; the callback must follow it, not the old object.
    handle.bind(None)
    second = FakeClient()
    handle.bind(second)
    assert second.on_utterance is on_utterance
    second.on_utterance(b"x")
    assert heard == [b"x"]

    # Setting one after a bind applies to the live connection immediately.
    assert second.on_wake is None
    handle.on_wake = lambda msg: None
    assert second.on_wake is not None

    # The callbacks that were missing from the rebind list: a wake that heard nothing
    # and a Phase 2 candidate must both still reach a restarted plane's handler.
    idle_calls: list[object] = []
    handle.on_wake_idle = lambda msg: idle_calls.append(msg)
    assert second.on_wake_idle is not None
    second.on_wake_idle("idle")
    assert idle_calls == ["idle"]
    handle.on_wake_candidate = lambda msg: None
    assert second.on_wake_candidate is not None


def test_watchdog_stays_idle_with_no_device(tmp_path) -> None:
    """A fresh install has no speaker yet; the watchdog must not spin restarting nothing."""
    path = tmp_path / "empty.yaml"
    path.write_text("version: 2\nweb:\n  host: 127.0.0.1\ndevices: []\n", encoding="utf-8")

    async def run() -> AudioPlaneSupervisor:
        supervisor = AudioPlaneSupervisor(
            load_config(path), config_path=path, socket_path=tmp_path / "audio.sock")
        supervisor.watch()
        await asyncio.sleep(0.2)
        try:
            assert supervisor._watch_task is None
            assert supervisor.restarts == 0
            assert supervisor.proc is None
        finally:
            await supervisor.stop()
        return supervisor

    asyncio.run(run())


def test_watchdog_restarts_a_killed_plane(config_path, tmp_path) -> None:
    async def run() -> tuple[AudioPlaneSupervisor, int, int, bool]:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(
            cfg, config_path=config_path, socket_path=tmp_path / "audio.sock"
        )
        supervisor.handle.on_utterance = lambda pcm: None  # wired once, before launch
        await supervisor.start()
        first_pid = supervisor.proc.pid if supervisor.proc is not None else -1
        supervisor.watch()
        os.kill(first_pid, signal.SIGKILL)

        deadline = asyncio.get_running_loop().time() + 20.0
        second_pid = first_pid
        callback_followed = False
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
            if (supervisor.running and supervisor.proc is not None
                    and supervisor.proc.pid != first_pid):
                second_pid = supervisor.proc.pid
                live = supervisor.handle.client
                callback_followed = live is not None and live.on_utterance is not None
                break
        try:
            return supervisor, first_pid, second_pid, callback_followed
        finally:
            await supervisor.stop()

    supervisor, first_pid, second_pid, callback_followed = asyncio.run(run())

    assert second_pid != first_pid, "the watchdog never replaced the killed plane"
    assert supervisor.restarts == 1
    assert supervisor.status()["running"] is False      # stopped again for the test
    # The new connection carried the callback the control plane wired at startup.
    assert callback_followed


def test_a_half_closed_socket_is_not_a_healthy_plane(config_path, tmp_path) -> None:
    """P1 (audit 2026-10-23): only the socket's read end dies, the child keeps running.

    ready + a live child used to be enough for `running`, so /health said green and the
    watchdog never rebuilt a plane nobody was reading - the room was deaf while the
    console claimed all was well. Feeding EOF into the reader is that exact failure:
    the child never learns anything, the pump returns, and `running` must go false while
    the process is still alive - then the watchdog replaces it.
    """

    async def run() -> tuple[AudioPlaneSupervisor, int, int, bool, str]:
        cfg = load_config(config_path)
        supervisor = AudioPlaneSupervisor(
            cfg, config_path=config_path, socket_path=tmp_path / "audio.sock"
        )
        await supervisor.start()
        supervisor.watch()          # the app arms this at startup
        assert supervisor.running
        first_pid = supervisor.proc.pid if supervisor.proc is not None else -1
        first_proc = supervisor.proc
        client = supervisor.client
        assert client is not None

        # Kill only the control side's read end: pause the transport first so no frame can
        # arrive after the artificial EOF (that is an asyncio assertion, not a socket
        # event), then feed EOF. The plane child itself is not signalled and keeps running.
        transport = client.writer.transport
        assert transport is not None
        transport.pause_reading()
        client.reader.feed_eof()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while loop.time() < deadline and supervisor.running:
            await asyncio.sleep(0.02)
        detected = not supervisor.running
        child_was_alive = first_proc is not None and first_proc.returncode is None
        error = supervisor.error

        second_pid = first_pid
        deadline = loop.time() + 20.0
        while loop.time() < deadline:
            await asyncio.sleep(0.1)
            if (supervisor.running and supervisor.proc is not None
                    and supervisor.proc.pid != first_pid):
                second_pid = supervisor.proc.pid
                break
        try:
            return supervisor, first_pid, second_pid, detected and child_was_alive, error
        finally:
            await supervisor.stop()

    supervisor, first_pid, second_pid, dead_while_child_alive, error = asyncio.run(run())

    assert first_pid > 0
    assert dead_while_child_alive, "the dead reader was still reported as running"
    assert "reader stopped" in error, error
    assert second_pid != first_pid, "the watchdog never rebuilt the half-closed plane"
    assert supervisor.restarts == 1
    assert supervisor.status()["running"] is False      # stopped again for the test
