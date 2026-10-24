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

    Prefer the object's own cached ``info`` (the LX06 builds one in ``__init__`` and its
    probe simply returns it); otherwise return an honest empty layout with the configured
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
    "fallback_info",
    "safe_probe",
]
