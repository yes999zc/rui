"""Reference adapter used by ``examples/plugin-example`` and the contract kit.

It is deliberately small but complete: a fake 16 kHz mono speaker that captures
frames, accepts PCM into an in-memory sink, and lets the test kit inject
``DeviceWake`` events. It never touches real hardware, ALSA or the network.

The ``inject_wake`` hook is **kit-only** (not part of ``DeviceAdapter``); a real
adapter would push events from its own wake engine into ``drain_wakes()``.

Volume follows the canonical v1 contract: ``set_volume`` takes the config/UI
percentage (0-100) and maps it to the device mixer, while ``services`` exposes the
low-level 0-255 unit that the 0.3 audio plane calls (v1 spec §4.1).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from rui.device.contract import (
    AudioFrame,
    BoundedWakeQueue,
    Capability,
    DeviceInfo,
    Transport,
    percent_to_device_volume,
)

try:  # RUI 0.3+ has the v1 event; 0.2.x (this tree) does not yet.
    from rui.device.contract import DeviceWake as _DeviceWake
except ImportError:  # pragma: no cover - exercised on the 0.2.x tree
    @dataclass(frozen=True)
    class _DeviceWake:  # type: ignore[no-redef]
        ts_ms: float
        score: float
        threshold: float
        barge: bool = False
        level: float | None = None
        source: str = "device"


DeviceWake = _DeviceWake
FRAME_MS = 20


def _capability(name: str) -> object:
    """Use the core enum when it exists; otherwise keep the v1 string."""
    return getattr(Capability, name.upper(), name)


class FakeServices:
    """The low-level mixer surface: 0-255, like a real adapter's ``services``."""

    def __init__(self, adapter: FakeSpeakerAdapter) -> None:
        self._adapter = adapter

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        raise NotImplementedError("the fake speaker has no shell")

    async def read_volume(self) -> int | None:
        return self._adapter._device_volume

    async def set_volume(self, volume: int) -> None:
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 255:
            raise ValueError("device volume is 0-255")
        self._adapter._device_volume = volume

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        self._adapter.last_led = effect


class FakeSpeakerAdapter:
    """A deterministic fake device with DeviceWake injection."""

    def __init__(self, device_id: str = "example-open-speaker", *, rate: int = 16000) -> None:
        self._device_id = device_id
        self._rate = rate
        self._open = False
        self._seq = 0
        self.played: list[bytes] = []
        self.play_calls: list[tuple[int, int]] = []
        self.stops = 0
        # The mixer is device units (0-255); the config-facing set_volume maps to it.
        self._device_volume = 102
        self.last_led = ""
        self.services = FakeServices(self)
        self._wakes = BoundedWakeQueue(32)
        self.info = DeviceInfo(
            id=device_id,
            adapter="example-open-speaker",
            model="EX-01",
            host="127.0.0.1",
            room="contract-test",
            capabilities=frozenset(
                {
                    _capability("playback"),
                    _capability("volume"),
                    _capability("led"),
                    _capability("device_wake"),
                    _capability("barge_via_device"),
                }
            ),
            transport=Transport.INBOUND,
            channels=1,
            sample_rate=rate,
            bit_depth=16,
            mic_channels=1,
            ref_channels=0,
        )

    @property
    def volume(self) -> int:
        """Current mixer value (0-255), for tests that want the raw unit."""
        return self._device_volume

    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False
        self._wakes.clear()

    async def frames(self, frame_ms: int = FRAME_MS) -> AsyncIterator[AudioFrame]:
        if not self._open:
            raise RuntimeError("adapter not open")
        samples = int(self._rate * frame_ms / 1000)
        pcm = b"\x00" * (samples * 2)
        while self._open:
            self._seq += 1
            yield AudioFrame(
                device_id=self._device_id,
                seq=self._seq,
                ts_ms=time.monotonic() * 1000.0,
                pcm=pcm,
                channels=1,
                sample_rate=self._rate,
                reference_channels=0,
            )
            await asyncio.sleep(frame_ms / 1000)

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        if not pcm:
            raise ValueError("play() received empty PCM")
        self.played.append(bytes(pcm))
        self.play_calls.append((sample_rate, channels))

    async def stop_playback(self, fade_ms: int = 120) -> None:
        self.stops += 1

    async def set_volume(self, percent: int) -> None:
        """Config/UI unit (0-100); the mixer unit is ``services.set_volume``."""
        await self.services.set_volume(percent_to_device_volume(percent))

    async def read_volume(self) -> int | None:
        """Mixer unit (0-255); the caller converts to percent at the UI boundary."""
        return await self.services.read_volume()

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        await self.services.set_led(effect, color, repaint=repaint)

    def drain_signals(self) -> list:
        """Vendor signals are a separate channel; this fake has none."""
        return []

    def drain_wakes(self) -> list[DeviceWake]:
        """Return every pending wake once, then clear (bounded to 32)."""
        return self._wakes.drain()

    # --- kit-only injection hook ------------------------------------------
    def inject_wake(
        self,
        *,
        ts_ms: float | None = None,
        score: float = 0.9,
        threshold: float = 0.5,
        barge: bool = False,
        level: float | None = None,
    ) -> None:
        self._wakes.push(
            DeviceWake(
                ts_ms=time.monotonic() * 1000.0 if ts_ms is None else float(ts_ms),
                score=float(score),
                threshold=float(threshold),
                barge=bool(barge),
                level=level,
            )
        )


def build(device: object | None = None, config: object | None = None) -> FakeSpeakerAdapter:
    """Entry point: ``rui plug-test examples/plugin-example --adapter ...:build``."""
    device_id = str(getattr(device, "id", "") or "example-open-speaker")
    return FakeSpeakerAdapter(device_id)
