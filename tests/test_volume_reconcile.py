"""d.7: volume reconciliation tolerates one mixer step.

Measured 2026-10-28 on the real device: the vendor's read-back is quantised, so a level
written as 219 could report back as 218/220. The old exact comparison then wrote the same
level again every metrics cadence (~16 s) and logged `volume: reconciled to 219` each
time - a write nobody asked for and log noise on top of it.

The unit here is the low-level mixer step (0-255), not the config percent: config
`devices[].volume` is 0-100 and `_reconcile_volume` maps it with `round(pct * 255 / 100)`.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from rui.audio import process as process_module
from rui.audio.client import AudioPlaneClient
from rui.audio.probe import ProbeResult
from rui.audio.process import AudioPlane
from rui.config import Config, DeviceConfig
from rui.device.contract import DeviceInfo


def _plane(
    tmp_path: Path, volume: int,
) -> tuple[AudioPlane, list[int], dict[str, int]]:
    """A plane wired to a fake mixer that reports whatever the test sets."""
    cfg = Config()
    cfg.audio.aec.enabled = False
    cfg.devices = [
        DeviceConfig(id="living", adapter="lx06", host="192.0.2.10", volume=volume),
    ]
    plane = AudioPlane(cfg, "living", tmp_path / "out.sock")
    writes: list[int] = []
    reported = {"value": 0}

    class Svc:
        async def set_volume(self, value: int) -> None:
            writes.append(value)

    async def read() -> int:
        return reported["value"]

    plane.adapter = SimpleNamespace(services=Svc(), read_volume=read)
    return plane, writes, reported


def test_reconcile_ignores_a_one_step_quantisation_difference(tmp_path: Path) -> None:
    """|read-back - target| <= 1 mixer step is the same level, not drift."""
    async def run() -> None:
        plane, writes, reported = _plane(tmp_path, 86)   # 86 % -> 219 on 0-255
        reported["value"] = 219
        await plane._read_device_volume()
        assert writes == [], "a target the device already reports was rewritten"

        reported["value"] = 220                          # +1: quantisation
        await plane._read_device_volume()
        assert writes == [], "a one-step read-back difference was treated as drift"

        reported["value"] = 218                          # -1: quantisation
        await plane._read_device_volume()
        assert writes == [], "a one-step read-back difference was treated as drift"

        reported["value"] = 221                          # +2: real drift
        await plane._read_device_volume()
        assert writes == [219], writes
        assert plane.device_volume == 219

        reported["value"] = 217                          # -2: real drift
        await plane._read_device_volume()
        assert writes == [219, 219], writes

    asyncio.run(run())


def test_a_config_change_still_writes_at_once(tmp_path: Path) -> None:
    """The tolerance only describes the read-back; a new target is written immediately."""
    async def run() -> None:
        plane, writes, reported = _plane(tmp_path, 86)
        reported["value"] = 219
        await plane._read_device_volume()
        assert writes == []

        plane.cfg.devices[0].volume = 40                 # 40 % -> 102 on 0-255
        await plane._read_device_volume()
        assert writes == [102], writes

    asyncio.run(run())


class _Mixer:
    def __init__(self, value: int) -> None:
        self.value = value
        self.writes: list[int] = []

    async def read_volume(self) -> int:
        return self.value

    async def set_volume(self, value: int) -> None:
        self.writes.append(value)
        self.value = value


class _SocketAdapter:
    def __init__(self, mixer: _Mixer) -> None:
        self.services = mixer
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def frames(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover - cancelled when the connection ends


async def _probe(_adapter) -> ProbeResult:  # noqa: ANN001 - stands in for safe_probe
    return ProbeResult(
        info=DeviceInfo(id="living", adapter="lx06", model="lx06", host="192.0.2.10"),
    )


def _user_set_roundtrip(
    tmp_path: Path, monkeypatch, *, mixer_value: int, request: int,
) -> tuple[int | None, int, list[int]]:
    """Drive `DeviceVolume(action="set")` over the real control socket.

    Returns (the level the plane answered, the config percent it learned, the writes).
    Using the socket keeps the assertion on the plane's set branch (process.py), not on
    `_reconcile_volume` - the point is that the tolerance never gates a user choice.
    """
    async def run() -> tuple[int | None, int, list[int]]:
        plane, _writes, _reported = _plane(tmp_path, 0)   # 0 = hands off by default
        mixer = _Mixer(mixer_value)
        adapter = _SocketAdapter(mixer)

        async def open_adapter() -> _SocketAdapter:
            return adapter

        plane._open_adapter = open_adapter             # type: ignore[method-assign]
        monkeypatch.setattr(process_module, "safe_probe", _probe)

        # AF_UNIX caps the whole path at ~104 bytes; pytest's tmp_path is longer.
        with tempfile.TemporaryDirectory(prefix="ruivol-") as td:
            sock = Path(td) / "c.sock"
            server = await asyncio.start_unix_server(plane.handle, path=str(sock))
            try:
                reader, writer = await asyncio.open_unix_connection(str(sock))
                client = AudioPlaneClient(reader, writer)
                client.start_pump()
                try:
                    for _ in range(300):
                        if client.ready is not None:
                            break
                        await asyncio.sleep(0.01)
                    assert client.ready is not None, "the plane never sent Ready"
                    level = await client.set_device_volume(request)
                finally:
                    await client.close()

                for _ in range(300):
                    if adapter.closed:
                        break
                    await asyncio.sleep(0.01)
                assert adapter.closed, "the plane did not close its adapter"
            finally:
                server.close()
                await server.wait_closed()
                current = asyncio.current_task()
                rest = [task for task in asyncio.all_tasks() if task is not current]
                for task in rest:
                    task.cancel()
                await asyncio.gather(*rest, return_exceptions=True)
        return level, plane.cfg.devices[0].volume, mixer.writes

    return asyncio.run(run())


def test_a_user_volume_set_still_writes_immediately(tmp_path: Path, monkeypatch) -> None:
    """A large user move is written and learned as the new target."""
    level, learned, writes = _user_set_roundtrip(
        tmp_path, monkeypatch, mixer_value=255, request=200,
    )

    assert level == 200, level          # the plane answers the level it read back
    assert writes == [200], writes      # set is not the reconciler: it wrote at once
    assert learned == 78                # round(200 * 100 / 255)


def test_a_one_step_user_set_still_writes(tmp_path: Path, monkeypatch) -> None:
    """Mixer 219, user asks 220 (one step): still written - tolerance is read-back only.

    This is the case a tolerance accidentally applied to the set path would swallow, so
    it is the one that actually pins "the tolerance does not gate a user choice".
    """
    level, learned, writes = _user_set_roundtrip(
        tmp_path, monkeypatch, mixer_value=219, request=220,
    )

    assert level == 220, level
    assert writes == [220], writes
    assert learned == 86                # round(220 * 100 / 255)
