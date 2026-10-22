"""Mock adapter: the second implementation that keeps the abstraction honest.

Mirrors the LX06's real wire format - 48 kHz, 8 interleaved channels, 6 mics and 2
duplicated reference channels - so code written against the mock cannot quietly assume
a mono 16 kHz device. Tests and CI run the whole pipeline against this, no hardware.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from .contract import AudioFrame, Capability, DeviceAdapter, DeviceInfo, Transport


class MockServices:
    """A device that can hold a number and nothing else - enough to test the path."""

    def __init__(self, adapter: MockAdapter) -> None:
        self._adapter = adapter

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        raise NotImplementedError("the mock device has no shell")

    async def read_volume(self) -> int | None:
        return self._adapter.volume

    async def set_volume(self, volume: int) -> None:
        if not 0 < volume <= 255:
            raise ValueError("device volume is 0-255")
        self._adapter.volume = volume


class MockAdapter(DeviceAdapter):
    def __init__(
        self,
        device_id: str,
        host: str = "127.0.0.1",
        room: str = "",
        rate: int = 48000,
        mic_channels: int = 6,
        ref_channels: int = 2,
    ) -> None:
        self.info = DeviceInfo(
            id=device_id,
            adapter="mock",
            model="mock",
            host=host,
            room=room,
            capabilities=frozenset(
                {Capability.MIC_ARRAY, Capability.REFERENCE, Capability.PLAYBACK, Capability.LED, Capability.VOLUME}
            ),
            transport=Transport.OUTBOUND,
            channels=mic_channels + ref_channels,
            sample_rate=rate,
            bit_depth=32,
            mic_channels=mic_channels,
            ref_channels=ref_channels,
        )
        self._open = False
        self._seq = 0
        self.played: list[bytes] = []
        self.play_calls: list[tuple[int, int]] = []
        self.volume = 60
        self.last_led = ""
        self.led_history: list[str] = []
        self.services = MockServices(self)
        self._rate = rate
        self._channels = mic_channels + ref_channels

    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False

    async def frames(self, frame_ms: int = 20) -> AsyncIterator[AudioFrame]:
        """Silence at a fixed cadence, in the real channel layout and rate."""
        if not self._open:
            raise RuntimeError("adapter not open")
        block = int(self._rate * frame_ms / 1000) * self._channels * 2
        while self._open:
            self._seq += 1
            yield AudioFrame(
                device_id=self.info.id,
                seq=self._seq,
                ts_ms=time.monotonic() * 1000,
                pcm=b"\x00" * block,
                channels=self._channels,
                sample_rate=self._rate,
                reference_channels=self.info.ref_channels,
            )
            await asyncio.sleep(frame_ms / 1000)

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        self.played.append(pcm)
        self.play_calls.append((sample_rate, channels))

    async def stop_playback(self, fade_ms: int = 120) -> None:
        await asyncio.sleep(min(fade_ms, 20) / 1000)

    async def set_volume(self, volume: int) -> None:
        self.volume = volume

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        self.last_led = effect
        self.led_history.append(effect)


def build(dev: object, cfg: object) -> MockAdapter:
    """Registry entry. The mock device ignores audio layout: it invents one."""
    return MockAdapter(
        getattr(dev, "id", "mock"),
        getattr(dev, "host", "127.0.0.1"),
        getattr(dev, "room", ""),
    )
