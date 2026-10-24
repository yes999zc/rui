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

import math
import threading
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable


class Capability(StrEnum):
    MIC_ARRAY = "mic_array"          # more than one mic channel
    REFERENCE = "reference"          # playback reference loopback (needed for real AEC)
    PLAYBACK = "playback"            # can receive PCM from us
    LED = "led"
    KEYS = "keys"
    VOLUME = "volume"
    NATIVE_KWS = "native_kws"        # deprecated in v1: vendor wake engine
    STREAMING_ASR = "streaming_asr"  # can push audio continuously
    # Socket & Plug v1 (docs/socket-plug-v1.md §2). `device_wake` is our wake word
    # detected on the device and reported through `drain_wakes()`; the vendor's own
    # wake still travels through `drain_signals()` and the two channels never merge.
    DEVICE_WAKE = "device_wake"
    BARGE_VIA_DEVICE = "barge_via_device"  # device can suggest a barge while playing


class Transport(StrEnum):
    OUTBOUND = "outbound"            # we dial the device
    INBOUND = "inbound"              # the device dials us


#: Capabilities where ``probe()`` can verify what the manifest declares. The
#: documented rule (Socket & Plug v1 §2) is manifest ∩ probe: a capability that
#: either side does not claim is not effective. The other three capability values
#: (``led``/``keys``/``volume``) are backed by ``DeviceServices`` calls instead, so
#: the manifest is authoritative for them and a probe that omits them must not
#: downgrade a working LX06.
PROBE_BOUND_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.MIC_ARRAY,
        Capability.REFERENCE,
        Capability.PLAYBACK,
        Capability.DEVICE_WAKE,
        Capability.BARGE_VIA_DEVICE,
    }
)


def effective_capabilities(
    manifest: Iterable[Capability] | None,
    probe: Iterable[Capability] | None,
) -> frozenset[Capability]:
    """The capabilities the core may act on: the manifest is the upper bound.

    ``manifest`` is what the plug's device manifest declares; ``probe`` is what the
    adapter reported. For :data:`PROBE_BOUND_CAPABILITIES` the result is the
    intersection (both sides must back the claim). For ``led``/``keys``/``volume``
    the manifest wins, because :class:`DeviceServices` is the actual runtime
    evidence and a v0 LX06 probe deliberately does not list them.

    ``manifest=None`` means "no manifest resolved" (the documented dev/mock case):
    the probe is then authoritative for every capability, exactly as before v1.
    Probe-only values outside the manifest are never promoted.
    """
    probed = frozenset(probe or ())
    if manifest is None:
        return probed
    effective: set[Capability] = set()
    for cap in frozenset(manifest):
        if cap not in PROBE_BOUND_CAPABILITIES or cap in probed:
            effective.add(cap)
    return frozenset(effective)


def probe_only_capabilities(
    manifest: Iterable[Capability] | None,
    probe: Iterable[Capability] | None,
) -> frozenset[Capability]:
    """Probe claims the manifest does not declare (reported, never effective)."""
    if manifest is None:
        return frozenset()
    return frozenset(probe or ()) - frozenset(manifest)


#: Config/UI volume is a percentage; the device mixer is 0-255. These are the two
#: unit boundaries of the canonical volume contract (Socket & Plug v1 §4.1).
VOLUME_PERCENT_MIN = 0
VOLUME_PERCENT_MAX = 100
DEVICE_VOLUME_MIN = 0
DEVICE_VOLUME_MAX = 255


def percent_to_device_volume(percent: int) -> int:
    """Map a 0-100 config/UI percentage to the adapter's 0-255 mixer value."""
    if isinstance(percent, bool) or not isinstance(percent, int):
        raise ValueError(f"volume percent must be an int in 0..100 (got {percent!r})")
    if not VOLUME_PERCENT_MIN <= percent <= VOLUME_PERCENT_MAX:
        raise ValueError(f"volume percent must be in 0..100 (got {percent!r})")
    return round(percent * DEVICE_VOLUME_MAX / VOLUME_PERCENT_MAX)


def device_volume_to_percent(value: int) -> int:
    """Map an adapter/mixer 0-255 value back to a 0-100 config/UI percentage."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"device volume must be an int in 0..255 (got {value!r})")
    if not DEVICE_VOLUME_MIN <= value <= DEVICE_VOLUME_MAX:
        raise ValueError(f"device volume must be in 0..255 (got {value!r})")
    return round(value * VOLUME_PERCENT_MAX / DEVICE_VOLUME_MAX)


#: Adapters should bound their own queue (v1 §4.2); this is the shared default.
MAX_PENDING_WAKES = 32


class BoundedWakeQueue:
    """Thread-safe, drop-oldest queue for :meth:`DeviceAdapter.drain_wakes`.

    The contract asks the adapter to keep a bounded queue (v1 §4.2). Producing from a
    device thread and draining from the audio plane's frame loop is the normal shape,
    so every operation is guarded by one lock. ``dropped`` lets an adapter expose the
    overflow in its own diagnostics; RUI never relies on it for policy.
    """

    def __init__(self, maxlen: int = MAX_PENDING_WAKES) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._items: deque[DeviceWake] = deque(maxlen=int(maxlen))
        self._lock = threading.Lock()
        self.dropped = 0

    def push(self, wake: DeviceWake) -> None:
        with self._lock:
            if len(self._items) == self._items.maxlen:
                self.dropped += 1
            self._items.append(wake)

    def drain(self) -> list[DeviceWake]:
        with self._lock:
            pending = list(self._items)
            self._items.clear()
            return pending

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


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
        """What still blocks barge-in, honouring both documented paths.

        A device can support barge either through RUI's own AEC (``reference`` +
        ``playback``) or by detecting the interruption itself (``device_wake`` +
        ``barge_via_device`` + ``playback``). Reporting the reference path as
        "missing" for a device that has the second path was audit finding P1-3.
        The manifest's ``wake.barge_via_device`` enable flag is still checked
        separately by the audio policy; this method answers the capability question.
        """
        device_path = {
            Capability.DEVICE_WAKE,
            Capability.BARGE_VIA_DEVICE,
            Capability.PLAYBACK,
        }
        if device_path <= self.capabilities:
            return []
        need = [Capability.REFERENCE, Capability.PLAYBACK]
        return [c for c in need if c not in self.capabilities]


@dataclass(frozen=True)
class DeviceWake:
    """One wake the *device* detected with *our* wake word.

    A suggestion, not a decision: RUI validates it (finite/range/staleness), applies its
    own debounce/rate limit and only then opens the VAD gate. It never calls the LLM,
    never bypasses `voice.barge_in`, and never grants a capability.

    `ts_ms` uses the same monotonic origin as `AudioFrame.ts_ms` (not wall-clock epoch),
    so the plane can reject a stale or future event. `score` is normalised 0-1 by the
    adapter; `threshold` is the device's own gate at decision time (advisory, recorded
    for the trace). `level` (dBFS) is diagnostic only.

    The event deliberately carries **only** these six fields. ``device_id`` is implied
    by the one-plane-per-device model; ``room``/``playing``/``last_active`` are not part
    of v1 (0.4 will add a separate arbitration event or optional fields). RUI enriches
    the trace from the plane it already owns, so an adapter must not try to smuggle
    them into the dataclass (it would be a ``TypeError``).

    ``barge`` is validated here because the truthiness of a non-bool value (``"false"``,
    ``1``) would otherwise stop playback against the adapter's intent. The remaining
    numeric/range/staleness rules live in :func:`wake_validation_error` because they
    depend on policy constants and the current clock.
    """

    ts_ms: float
    score: float
    threshold: float
    barge: bool = False
    level: float | None = None
    source: Literal["device"] = "device"

    def __post_init__(self) -> None:
        if not isinstance(self.barge, bool):
            raise TypeError(f"DeviceWake.barge must be a bool (got {type(self.barge).__name__})")
        if self.source != "device":
            raise ValueError(f"DeviceWake.source must be 'device' (got {self.source!r})")


def wake_validation_error(
    wake: object,
    *,
    now_ms: float | None = None,
    stale_ms: float = 1500.0,
    future_ms: float = 500.0,
) -> str | None:
    """Return the v1 §5 reason an event is invalid, or ``None`` when it is usable.

    This is the contract-level counterpart to the audio plane's policy check: adapters
    and the contract kit can validate an event without importing the audio process, and
    the plane can share the exact same wording. ``now_ms`` is optional so a pure shape
    check is possible; when given, stale/future events are rejected with the documented
    defaults (1500 ms late / 500 ms early).
    """
    if not isinstance(wake, DeviceWake):
        return f"not a DeviceWake ({type(wake).__name__})"
    if not isinstance(wake.barge, bool):
        return f"barge must be a bool (got {wake.barge!r})"
    if wake.source != "device":
        return f"source must be 'device' (got {wake.source!r})"
    for name, value in (("ts_ms", wake.ts_ms), ("score", wake.score),
                        ("threshold", wake.threshold)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{name} must be a finite number (got {value!r})"
        if not math.isfinite(value):
            return f"{name} must be finite (got {value!r})"
    if not 0.0 <= wake.score <= 1.0:
        return f"score must be in 0..1 (got {wake.score!r})"
    if wake.threshold <= 0.0:
        return f"threshold must be > 0 (got {wake.threshold!r})"
    if wake.score < wake.threshold:
        return f"score {wake.score!r} is below threshold {wake.threshold!r}"
    if wake.level is not None:
        if isinstance(wake.level, bool) or not isinstance(wake.level, (int, float)):
            return f"level must be a finite number or None (got {wake.level!r})"
        if not math.isfinite(wake.level):
            return f"level must be finite or None (got {wake.level!r})"
    if now_ms is not None:
        age = now_ms - wake.ts_ms
        if age > stale_ms:
            return f"event is stale by {age:.0f} ms (limit {stale_ms:.0f} ms)"
        if age < -future_ms:
            return f"event is {-age:.0f} ms in the future (limit {future_ms:.0f} ms)"
    return None


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
        """Device mixer value, **0-255**, or None when the device cannot report one."""
        ...

    async def set_volume(self, volume: int) -> None:
        """Write the device mixer in its low-level **0-255** unit.

        The config/UI percentage (0-100) is mapped before it reaches this method with
        :func:`percent_to_device_volume`. Implementations must reject values outside
        0-255 rather than clamping silently. ``0`` is in range but may be reserved for
        "leave it to the vendor" (the LX06's services rejects a 0 write); core never
        sends 0 because config ``volume = 0`` means "do not manage".
        """
        ...

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        """Paint the light ring, if the device has one. Fire-and-forget."""
        ...


class NullServices:
    """The honest answer for a device with no shell, no mixer, no light."""

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        raise NotImplementedError("this device exposes no shell")

    async def read_volume(self) -> int | None:
        return None

    async def set_volume(self, volume: int) -> None:
        raise NotImplementedError("this device has no controllable mixer")

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None:
        raise NotImplementedError("this device has no light ring")


class DeviceAdapter(Protocol):
    """Async contract every speaker adapter implements."""

    #: Shell/mixer/light. Optional: adapters without one leave it unset (or assign
    #: :class:`NullServices`) and must not advertise VOLUME/LED. It is an attribute,
    #: not a property, because an adapter may assign a vendor-specific object in
    #: ``__init__`` (the LX06 does).
    services: DeviceServices

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

    async def set_volume(self, percent: int) -> None:
        """High-level volume, **0-100**, same unit as config/UI.

        Optional: an adapter with VOLUME may implement it (mapping to its own mixer),
        and core may call it instead of the low-level ``services.set_volume``. The
        actual 0.3 plane uses ``services.set_volume`` (0-255) when present; both must
        agree on the mapping. See :func:`percent_to_device_volume`.
        """
        ...

    async def read_volume(self) -> int | None:
        """Device mixer value in **0-255**, delegated to ``services`` by default.

        A contract-only adapter (no vendor object) still reports through
        ``services.read_volume``; returning ``None`` means "cannot report".
        """
        services = getattr(self, "services", None)
        if services is None:
            return None
        try:
            return await services.read_volume()
        except NotImplementedError:
            return None

    def drain_signals(self) -> list:
        """Non-audio events the device reported since the last call (vendor wake, keys).

        Default is empty: an adapter that cannot see the vendor's own assistant simply
        never reports one, and the core then behaves as it always did.
        """
        return []

    def drain_wakes(self) -> list[DeviceWake]:
        """Device-detected *RUI* wakes since the last call (Socket & Plug v1 §4.2).

        Synchronous, non-blocking, no I/O, one caller, and no event returned twice.
        Default is empty: an adapter without a device-side wake model is indistinguishable
        from an LX06, and RUI's own KWS path stays exactly as it was. Vendor wakes belong
        to ``drain_signals()``; the two channels must not be merged.
        """
        return []

    async def set_led(self, effect: str, color: str = "", *, repaint: bool = False) -> None: ...

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
