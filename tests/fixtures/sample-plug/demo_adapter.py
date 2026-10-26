"""Minimal fake adapter for the CI ``rui plug-test`` smoke step.

This is a test fixture, not a supported plug: it synthesises 20 ms of silence and
keeps playback in memory, so the contract kit can run without a speaker, ALSA or
a network connection. Wake injection is omitted on purpose - ``devices/demo.yaml``
keeps the wake source on RUI KWS, so the kit reports those checks as SKIP.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from rui.device.contract import AudioFrame, Capability, DeviceInfo, Transport

FRAME_MS = 20
SAMPLE_RATE = 16000


class DemoAdapter:
    """A one-channel fake speaker implementing the DeviceAdapter surface."""

    def __init__(self, device_id: str = "demo-speaker") -> None:
        self._device_id = device_id
        self._open = False
        self._seq = 0
        self.played: list[bytes] = []
        self._volume = 50
        self._led = ""
        self.info = DeviceInfo(
            id=device_id,
            adapter=device_id,
            model="DEMO-01",
            host="127.0.0.1",
            room="test-room",
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
        self._open = True

    async def close(self) -> None:
        # Idempotent: the contract kit calls close() twice.
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
        # The contract kit only checks that the sink received the exact bytes.
        self.played.append(bytes(pcm))

    async def stop_playback(self) -> None:
        return None

    async def set_volume(self, volume: int) -> None:
        self._volume = volume

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        self._led = effect

    def drain_signals(self) -> list:
        return []

    def drain_wakes(self) -> list:
        return []


def build(device: object = None, config: object = None) -> DemoAdapter:
    """Entry point used by ``--adapter demo_adapter:build`` and runtime.entrypoint."""
    return DemoAdapter()
