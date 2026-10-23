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

from house_ear.audio.client import AudioPlaneSupervisor, PlaneHandle
from house_ear.config import load_config

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
