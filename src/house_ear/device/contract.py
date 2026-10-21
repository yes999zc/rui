"""The device contract. Everything device-specific lives behind this.

A new speaker is supported by writing one adapter, never by touching the core.
Adapters declare capabilities and the core degrades gracefully: a device with no
playback-reference channel simply cannot do barge-in while music plays, and the UI
says so instead of pretending.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Capability(StrEnum):
    MIC_ARRAY = "mic_array"          # more than one mic channel
    REFERENCE = "reference"          # playback reference loopback (needed for real AEC)
    PLAYBACK = "playback"            # can receive PCM from us
    LED = "led"
    KEYS = "keys"
    VOLUME = "volume"
    NATIVE_KWS = "native_kws"        # vendor wake engine we can lean on
    STREAMING_ASR = "streaming_asr"  # can push audio continuously


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    adapter: str
    model: str
    host: str
    room: str = ""
    capabilities: frozenset[Capability] = frozenset()
    channels: int = 1
    sample_rate: int = 16000
    bit_depth: int = 16

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def missing_for_barge_in(self) -> list[Capability]:
        need = [Capability.REFERENCE, Capability.PLAYBACK]
        return [c for c in need if c not in self.capabilities]


@dataclass
class AudioFrame:
    device_id: str
    seq: int
    ts_ms: float
    pcm: bytes                    # interleaved, already scaled to 16-bit
    channels: int = 1
    sample_rate: int = 16000
    is_reference: bool = False
    meta: dict = field(default_factory=dict)


@runtime_checkable
class DeviceAdapter(Protocol):
    """Async contract every speaker adapter implements."""

    async def probe(self) -> DeviceInfo: ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def frames(self) -> AsyncIterator[AudioFrame]: ...

    async def play(self, pcm: bytes, sample_rate: int) -> None: ...

    async def stop_playback(self, fade_ms: int = 120) -> None: ...

    async def set_volume(self, volume: int) -> None: ...

    async def set_led(self, effect: str, color: str = "") -> None: ...
