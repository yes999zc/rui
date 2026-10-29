"""Fixture adapter whose ``frames()`` yields a dict instead of an AudioFrame.

The shape is close enough that "it yields something" tests would pass; the kit's
``capture.frames`` check requires the contract dataclass, which is the point.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from rui.device.contract import AudioFrame, Capability, DeviceInfo, Transport

FRAME_MS = 20
SAMPLE_RATE = 48000


class BadFramesAdapter:
    def __init__(self, device_id: str = "fail-bad-frames") -> None:
        self._device_id = device_id
        self._open = False
        self._seq = 0
        self.played: list[bytes] = []
        self.info = DeviceInfo(
            id=device_id,
            adapter=device_id,
            model="FAIL-2",
            host="127.0.0.1",
            capabilities=frozenset({Capability.PLAYBACK}),
            transport=Transport.INBOUND,
            channels=1,
            sample_rate=SAMPLE_RATE,
            mic_channels=1,
        )

    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        self._open = True

    async def close(self) -> None:
        self._open = False

    async def frames(self) -> AsyncIterator[AudioFrame]:
        if not self._open:
            raise RuntimeError("adapter is not open")
        while self._open:
            self._seq += 1
            # Wrong type on purpose: a plain dict, not rui.device.contract.AudioFrame.
            yield {
                "device_id": self._device_id,
                "seq": self._seq,
                "ts_ms": time.monotonic() * 1000.0,
                "pcm": b"\x00" * 160,
                "channels": 1,
                "sample_rate": SAMPLE_RATE,
            }
            await asyncio.sleep(FRAME_MS / 1000.0)

    async def play(self, pcm: bytes, *, sample_rate: int = SAMPLE_RATE,
                   channels: int = 1) -> None:
        self.played.append(bytes(pcm))

    async def stop_playback(self, fade_ms: int = 120) -> None:
        return None

    async def set_volume(self, percent: int) -> None:
        return None

    async def read_volume(self) -> int | None:
        return 60

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        return None

    def drain_signals(self) -> list:
        return []

    def drain_wakes(self) -> list:
        return []


def build(device: object = None, config: object = None) -> BadFramesAdapter:
    return BadFramesAdapter(str(getattr(device, "id", "fail-bad-frames")))
