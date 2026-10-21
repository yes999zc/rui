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

    async def set_led(self, effect: str, color: str = "") -> None:
        return None
