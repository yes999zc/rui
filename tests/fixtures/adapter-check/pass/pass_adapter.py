"""Fixture adapter that must pass every rui-adapter-check line.

It is deliberately plain Python (no import of the Protocol, no inheritance): a bridge
author's adapter usually looks exactly like this, and the kit's job is to accept it while
still checking the dataclasses it returns. The ``played`` list is the recording sink the
kit uses to prove ``play()`` really received the bytes.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from rui.device.contract import AudioFrame, Capability, DeviceInfo, Transport

FRAME_MS = 20
SAMPLE_RATE = 48000


class ExampleAdapter:
    """One-channel fake bridge implementing the full DeviceAdapter surface."""

    def __init__(self, device_id: str = "example-speaker") -> None:
        self._device_id = device_id
        self._open = False
        self._seq = 0
        self._volume = 60
        self.played: list[bytes] = []
        self.info = DeviceInfo(
            id=device_id,
            adapter=device_id,
            model="EXAMPLE-1",
            host="127.0.0.1",
            room="adapter-check",
            capabilities=frozenset(
                {Capability.PLAYBACK, Capability.VOLUME, Capability.LED}
            ),
            transport=Transport.INBOUND,
            channels=1,
            sample_rate=SAMPLE_RATE,
            bit_depth=16,
            mic_channels=1,
            ref_channels=0,
        )

    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        # Idempotent on purpose: the kit calls open() twice.
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

    async def play(self, pcm: bytes, *, sample_rate: int = SAMPLE_RATE,
                   channels: int = 1) -> None:
        self.played.append(bytes(pcm))

    async def stop_playback(self, fade_ms: int = 120) -> None:
        return None

    async def set_volume(self, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("volume percent is 0-100")
        self._volume = percent

    async def read_volume(self) -> int | None:
        return 60

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        return None

    def drain_signals(self) -> list:
        return []

    def drain_wakes(self) -> list:
        return []


def build(device: object = None, config: object = None) -> ExampleAdapter:
    """Entry point used by devices/example.yaml and `--adapter ...:build`."""
    device_id = getattr(device, "id", "example-speaker")
    return ExampleAdapter(str(device_id))
