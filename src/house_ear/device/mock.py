"""Mock adapter: the second implementation that keeps the abstraction honest.

Tests and CI run the whole pipeline against this, with no hardware attached.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from .contract import AudioFrame, Capability, DeviceAdapter, DeviceInfo


class MockAdapter(DeviceAdapter):
    def __init__(self, device_id: str, host: str = "127.0.0.1", room: str = "") -> None:
        self.info = DeviceInfo(
            id=device_id,
            adapter="mock",
            model="mock",
            host=host,
            room=room,
            capabilities=frozenset(
                {Capability.MIC_ARRAY, Capability.REFERENCE, Capability.PLAYBACK, Capability.LED, Capability.VOLUME}
            ),
            channels=6,
            sample_rate=16000,
            bit_depth=16,
        )
        self._open = False
        self._seq = 0
        self.played: list[bytes] = []
        self.volume = 60

    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False

    async def frames(self, frame_ms: int = 20) -> AsyncIterator[AudioFrame]:
        """Silence at a fixed cadence; enough to exercise timing and state."""
        if not self._open:
            raise RuntimeError("adapter not open")
        n = int(16000 * frame_ms / 1000) * 2
        while self._open:
            self._seq += 1
            yield AudioFrame(
                device_id=self.info.id,
                seq=self._seq,
                ts_ms=time.monotonic() * 1000,
                pcm=b"\x00" * n,
                channels=1,
                sample_rate=16000,
            )
            await asyncio.sleep(frame_ms / 1000)

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        self.played.append(pcm)

    async def stop_playback(self, fade_ms: int = 120) -> None:
        await asyncio.sleep(min(fade_ms, 20) / 1000)

    async def set_volume(self, volume: int) -> None:
        self.volume = volume

    async def set_led(self, effect: str, color: str = "") -> None:
        return None
