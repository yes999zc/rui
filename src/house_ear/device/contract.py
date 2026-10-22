"""The device contract. Everything device-specific lives behind this.

A new speaker is supported by writing one adapter, never by touching the core.
Adapters declare capabilities and the core degrades gracefully: a device with no
playback-reference channel simply cannot do barge-in while music plays, and the UI
says so instead of pretending.

Two transport directions exist and both fit this contract:
  outbound - we connect to the device (USB audio, an exposed HTTP/ALSA bridge);
  inbound  - the device's client dials us (LX06: ws://<server>:<port>/?token=...).
An inbound adapter's `open()` starts accepting and `frames()` yields from the
connected client; `probe()` reports what the device declared at handshake and what
we verified over SSH, never an optimistic guess.
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


class Transport(StrEnum):
    OUTBOUND = "outbound"            # we dial the device
    INBOUND = "inbound"              # the device dials us


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    adapter: str
    model: str
    host: str
    room: str = ""
    capabilities: frozenset[Capability] = frozenset()
    transport: Transport = Transport.OUTBOUND
    channels: int = 1
    sample_rate: int = 16000
    bit_depth: int = 16
    mic_channels: int = 1
    ref_channels: int = 0

    def supports(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def missing_for_barge_in(self) -> list[Capability]:
        need = [Capability.REFERENCE, Capability.PLAYBACK]
        return [c for c in need if c not in self.capabilities]


@dataclass
class AudioFrame:
    """One interleaved capture block, already scaled to 16-bit.

    The reference channels travel inside the same frame as the microphones (LX06:
    ch0..ch5 mics, ch6..ch7 reference), so a frame describes one layout for all of
    its channels instead of pretending reference audio arrives separately.
    """

    device_id: str
    seq: int
    ts_ms: float
    pcm: bytes                    # interleaved, int16 little-endian, already scaled
    channels: int = 1
    sample_rate: int = 48000
    reference_channels: int = 0   # trailing channels in `pcm` that carry the reference
    meta: dict = field(default_factory=dict)

    @property
    def mic_channels(self) -> int:
        return self.channels - self.reference_channels

    @property
    def frame_samples(self) -> int:
        """Samples per channel in this frame."""
        if self.channels <= 0:
            return 0
        return len(self.pcm) // (2 * self.channels)

    @property
    def duration_ms(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return self.frame_samples * 1000.0 / self.sample_rate


@runtime_checkable
class DeviceServices(Protocol):
    """What a device can do beyond moving audio: a shell, the mixer, a light.

    Optional on purpose. A speaker that has none of this implements `NullServices` and
    reports no VOLUME/LED capability; nothing above has to ask "is this a 小爱".
    """

    async def shell(self, script: str, timeout: float = 3.0) -> str: ...

    async def read_volume(self) -> int | None:
        """0-255 mixer value, or None when the device cannot report one."""
        ...

    @property
    def services(self) -> DeviceServices:
        """Shell/mixer/light. Defaults to nothing rather than to a vendor guess."""
        return NullServices()

    async def set_volume(self, volume: int) -> None: ...


class NullServices:
    """The honest answer for a device with no shell, no mixer, no light."""

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        raise NotImplementedError("this device exposes no shell")

    async def read_volume(self) -> int | None:
        return None

    async def set_volume(self, volume: int) -> None:
        raise NotImplementedError("this device has no controllable mixer")


class DeviceAdapter(Protocol):
    """Async contract every speaker adapter implements."""

    async def probe(self) -> DeviceInfo: ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def frames(self) -> AsyncIterator[AudioFrame]: ...

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        """Queue PCM for playback. Channels matter: LX06 plays 2 ch at 48 kHz."""
        ...

    async def stop_playback(self, fade_ms: int = 120) -> None:
        """Fade out and flush, including whatever the device has already buffered."""
        ...

    async def set_volume(self, volume: int) -> None: ...

    def drain_signals(self) -> list:
        """Non-audio events the device reported since the last call (vendor wake, keys).

        Default is empty: an adapter that cannot see the vendor's own assistant simply
        never reports one, and the core then behaves as it always did.
        """
        return []

    async def set_led(self, effect: str, color: str = "") -> None: ...

    # --- optional play-head report ------------------------------------------------
    # A device that reports every chunk it has handed to its own sink ("consumed")
    # lets the plane pace by that clock instead of ours. The plane never runs more
    # than `window` chunks ahead of the newest report, which is what keeps the pipe
    # from filling and a block from blocking past its write timeout.
    #
    # Adapters that cannot report one leave `reports_consumption` False; the plane
    # then paces itself (wall clock) exactly as before. `sink_generation` increments
    # whenever a new player is opened, because the device restarts its chunk count
    # at 1 and the credit must reset with it.
    reports_consumption: bool = False
    consumed: int = 0
    sink_generation: int = 0

    async def wait_consumed(self, since: int, timeout: float) -> int:
        """Newest consumed chunk index (> since), or `since` if none arrived in time."""
        return since
