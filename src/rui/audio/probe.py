"""Fail-soft ``probe()`` boundary for adapters.

`probe()` is supplied by third-party code and used by both the audio plane and the
console. A single raising (or hanging, or ``None``-returning) probe must never take a
process down or turn a device page into a 500: the callers get a degraded
:class:`DeviceInfo` plus a stable error code they can render.

The helper lives beside the request/response seam rather than in the device registry so
both processes can share it without importing the plane (and its numpy/KWS imports)
into the web app.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..device.contract import DeviceInfo, Transport

log = logging.getLogger(__name__)

# A probe is a local/introspection call in every shipped adapter. Five seconds is enough
# for an SSH/RPC-backed one on a slow LAN and short enough that a dead adapter cannot
# hang the device page (the console awaits it per row).
PROBE_TIMEOUT_S = 5.0
# One bounded sentence for the UI/log; exception text may contain a host or a path.
MAX_PROBE_DETAIL = 200

# Why a probe did not produce usable information. Stable codes, not sentences: the
# console translates them and the `/devices` payload stays machine-readable.
PROBE_FAILED = "probe_failed"
PROBE_TIMEOUT = "probe_timeout"
PROBE_INVALID = "probe_invalid"


@dataclass(frozen=True)
class ProbeResult:
    """One adapter probe, degraded instead of raising."""

    info: DeviceInfo
    error: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_error(self) -> str:
        """``code: detail`` for endpoints that only expose a sentence."""
        return f"{self.error}: {self.detail}" if self.detail else self.error


# ---------------------------------------------------------------- wire format
#
# The adapter contract reports the *capture* wire (rate/channels/mic/ref) but has
# no playback field: a bridge like the Dot 2 accepts one PCM format only and
# raises from `play()` for anything else. The model manifest is therefore the
# expected playback wire. This comparison is deliberately pure so the plane can
# refuse to start and the console can show the same answer independently.

#: Stable prefix that marks a wire-mismatch refusal. It lives here (not in the
#: plane) because the plane, the supervisor and the console must match one string.
WIRE_MISMATCH_PREFIX = "audio_config_mismatch: "


def is_wire_mismatch(text: object) -> bool:
    """True when a plane/supervisor error carries the wire-mismatch code.

    Never anchor on the start of the message: the supervisor wraps the plane's
    own words ("audio plane refused: ..."), the plane prefixes degraded errors
    ("audio plane degraded: ...") and the watchdog adds the exception class
    ("AudioPlaneError: ..."). All of them must still read as a config mismatch.
    """
    return WIRE_MISMATCH_PREFIX.rstrip() in str(text or "")


@dataclass(frozen=True)
class WireFormat:
    """One side of the comparison (configured, probe-reported or manifest)."""

    rate: int | None = None
    channels: int | None = None
    mic_channels: int | None = None
    ref_channels: int | None = None
    #: Human-readable provenance, shown verbatim: "probe (dot2 ...)" or
    #: "model manifest dot2 (dot2-echomuse)".
    source: str = ""


@dataclass(frozen=True)
class WireMismatch:
    """One field where the effective config and the device disagree."""

    path: str
    configured: object
    actual: object
    source: str
    fix: str

    def line(self) -> str:
        return (
            f"{self.path}: configured {self.configured}, device {self.actual} "
            f"(source: {self.source}); fix: {self.fix}"
        )


def _fix_for(path: str, actual: object) -> str:
    if path == "capture.rate":
        return (
            f"set devices[].audio.capture.rate to {actual} "
            "(the rate the device sends), or fix audio_defaults.capture.rate"
        )
    if path.startswith("capture."):
        field = path.split(".", 1)[1]
        return (
            f"set devices[].audio.capture.{field} to {actual} "
            "(channels must equal mic_channels + ref_channels)"
        )
    if path == "playback.rate":
        return (
            f"set devices[].audio.playback.rate to {actual} "
            "(the rate the adapter accepts), or fix audio_defaults.playback.rate"
        )
    if path == "playback.channels":
        return f"set devices[].audio.playback.channels to {actual}"
    return f"set {path} to {actual} for this device"


@dataclass(frozen=True)
class WireCheck:
    """The result of comparing effective config with the device's wire format."""

    problems: tuple[WireMismatch, ...] = ()
    checked: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_error(self) -> str:
        """One sentence per disagreeing field; empty when the wire matches."""
        return "; ".join(problem.line() for problem in self.problems)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checked": list(self.checked),
            "message": self.as_error(),
            "mismatches": [
                {
                    "path": problem.path,
                    "configured": problem.configured,
                    "device": problem.actual,
                    "source": problem.source,
                    "fix": problem.fix,
                }
                for problem in self.problems
            ],
        }


def _compare(
    configured: WireFormat,
    actual: WireFormat,
    fields: tuple[str, ...],
    *,
    prefix: str,
    problems: list[WireMismatch],
    checked: list[str],
) -> None:
    for field in fields:
        want = getattr(configured, field)
        have = getattr(actual, field)
        if want is None or have is None:
            # One side did not state the field: an unknown is not a mismatch.
            continue
        path = f"{prefix}.{field}"
        checked.append(path)
        if want == have:
            continue
        problems.append(WireMismatch(
            path=path,
            configured=want,
            actual=have,
            source=actual.source or "device",
            fix=_fix_for(path, have),
        ))


def check_wire_format(
    *,
    capture_configured: WireFormat | None = None,
    capture_reported: WireFormat | None = None,
    playback_configured: WireFormat | None = None,
    playback_expected: WireFormat | None = None,
) -> WireCheck:
    """Compare the effective audio config with the device's actual wire format.

    Capture comes from ``probe()``: that is what the device sends and what the
    plane's decimator interprets. Playback comes from the model manifest, because
    ``DeviceInfo`` has no playback field and a plug like the Dot 2 rejects every
    block whose rate/channel count differs from its hardware wire.

    A side that is absent (probe failed, manifest not installed) leaves those
    fields unchecked rather than inventing an expectation: a false refusal is worse
    than a late `play()` error, which the plane still counts and surfaces.
    """
    problems: list[WireMismatch] = []
    checked: list[str] = []
    capture_fields = ("rate", "channels", "mic_channels", "ref_channels")
    playback_fields = ("rate", "channels")
    if capture_configured is not None and capture_reported is not None:
        _compare(capture_configured, capture_reported, capture_fields,
                 prefix="capture", problems=problems, checked=checked)
    if playback_configured is not None and playback_expected is not None:
        _compare(playback_configured, playback_expected, playback_fields,
                 prefix="playback", problems=problems, checked=checked)
    return WireCheck(tuple(problems), tuple(checked))


def _brief(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = type(exc).__name__
    elif len(text) > MAX_PROBE_DETAIL:
        text = text[: MAX_PROBE_DETAIL - 1] + "…"
    return text


def _usable(value: object) -> bool:
    """True when ``value`` has the fields every consumer of ``probe()`` reads.

    Duck-typed on purpose: downstream adapters may return their own DeviceInfo-shaped
    object. Only the fields used downstream are required; a subclass still passes.
    """
    return all(
        hasattr(value, attr)
        for attr in (
            "id",
            "adapter",
            "model",
            "host",
            "capabilities",
            "transport",
            "channels",
            "sample_rate",
            "mic_channels",
            "ref_channels",
        )
    )


def fallback_info(
    adapter: object,
    *,
    device_id: str = "",
    adapter_name: str = "",
) -> DeviceInfo:
    """The best degraded description of an adapter whose probe just failed.

    Prefer the object's own cached ``info`` (an adapter that builds one in ``__init__``
    and whose probe simply returns it); otherwise return an honest empty layout with the configured
    identity so the console can still render the row it must let the user remove.
    """
    cached = getattr(adapter, "info", None)
    if _usable(cached):
        if device_id and not str(getattr(cached, "id", "") or "").strip():
            return DeviceInfo(
                id=device_id,
                adapter=str(getattr(cached, "adapter", adapter_name) or adapter_name),
                model=str(getattr(cached, "model", "") or ""),
                host=str(getattr(cached, "host", "") or ""),
                room=str(getattr(cached, "room", "") or ""),
                capabilities=frozenset(getattr(cached, "capabilities", frozenset()) or ()),
                transport=getattr(cached, "transport", Transport.OUTBOUND),
                channels=int(getattr(cached, "channels", 1) or 1),
                sample_rate=int(getattr(cached, "sample_rate", 16000) or 16000),
                bit_depth=int(getattr(cached, "bit_depth", 16) or 16),
                mic_channels=int(getattr(cached, "mic_channels", 1) or 1),
                ref_channels=int(getattr(cached, "ref_channels", 0) or 0),
            )
        return cached
    return DeviceInfo(
        id=device_id or "unknown",
        adapter=adapter_name or type(adapter).__name__,
        model="",
        host="",
        capabilities=frozenset(),
        transport=Transport.OUTBOUND,
        channels=1,
        sample_rate=16000,
        bit_depth=16,
        mic_channels=1,
        ref_channels=0,
    )


async def safe_probe(
    adapter: object,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    device_id: str = "",
    adapter_name: str = "",
) -> ProbeResult:
    """Call ``adapter.probe()`` once; never raise for adapter-supplied failure.

    A timeout is reported as ``probe_timeout``, any exception as ``probe_failed`` and a
    ``None``/malformed return as ``probe_invalid``. The returned info always has the
    layout fields a caller may touch.
    """
    probe = getattr(adapter, "probe", None)
    if not callable(probe):
        return ProbeResult(
            fallback_info(adapter, device_id=device_id, adapter_name=adapter_name),
            PROBE_INVALID,
            "adapter has no probe()",
        )
    try:
        info = await asyncio.wait_for(probe(), timeout_s)
    except TimeoutError:
        detail = f"probe() did not return within {timeout_s:.1f} s"
        log.warning("%s", detail)
        return ProbeResult(
            fallback_info(adapter, device_id=device_id, adapter_name=adapter_name),
            PROBE_TIMEOUT,
            detail,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - adapter input is untrusted
        detail = _brief(exc)
        log.warning("probe() failed (%s); reporting a degraded device", detail)
        return ProbeResult(
            fallback_info(adapter, device_id=device_id, adapter_name=adapter_name),
            PROBE_FAILED,
            detail,
        )
    if info is None:
        return ProbeResult(
            fallback_info(adapter, device_id=device_id, adapter_name=adapter_name),
            PROBE_INVALID,
            "probe() returned None",
        )
    if not _usable(info):
        return ProbeResult(
            fallback_info(adapter, device_id=device_id, adapter_name=adapter_name),
            PROBE_INVALID,
            f"probe() returned {type(info).__name__}, not a DeviceInfo",
        )
    return ProbeResult(info)


__all__ = [
    "PROBE_FAILED",
    "PROBE_INVALID",
    "PROBE_TIMEOUT",
    "PROBE_TIMEOUT_S",
    "ProbeResult",
    "WIRE_MISMATCH_PREFIX",
    "WireCheck",
    "WireFormat",
    "WireMismatch",
    "check_wire_format",
    "fallback_info",
    "is_wire_mismatch",
    "safe_probe",
]
