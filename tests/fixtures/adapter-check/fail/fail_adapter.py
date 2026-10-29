"""Fixture adapter with one deliberate gap: there is no ``play()`` at all.

The kit must name the missing member and exit non-zero; a smoke test that only calls what
exists would call this adapter healthy.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from rui.device.contract import AudioFrame, Capability, DeviceInfo, Transport

FRAME_MS = 20
SAMPLE_RATE = 48000


class MissingPlayAdapter:
    def __init__(self, device_id: str = "fail-missing-play") -> None:
        self._device_id = device_id
        self._open = False
        self._seq = 0
        self.played: list[bytes] = []
        self.info = DeviceInfo(
            id=device_id,
            adapter=device_id,
            model="FAIL-1",
            host="127.0.0.1",
            capabilities=frozenset({Capability.PLAYBACK, Capability.VOLUME}),
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
        samples = SAMPLE_RATE * FRAME_MS // 1000
        pcm = b"\x00" * (samples * 2)
        while self._open:
            self._seq += 1
            yield AudioFrame(
                device_id=self._device_id,
                seq=self._seq,
                ts_ms=time.monotonic() * 1000.0,
                pcm=pcm,
                channels=1,
                sample_rate=SAMPLE_RATE,
            )
            await asyncio.sleep(FRAME_MS / 1000.0)

    # play() is the one missing member on purpose.

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


def build(device: object = None, config: object = None) -> MissingPlayAdapter:
    return MissingPlayAdapter(str(getattr(device, "id", "fail-missing-play")))
