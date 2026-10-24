"""Reference RUI plug adapter for the Xiaomi AI Speaker Pro (LX06).

This is a **thin reference wrapper**, not a second LX06 implementation. It turns a
device spec (``DeviceConfig``-shaped) and an optional runtime config into the two
objects the in-core builder expects, then delegates to
``rui.device.xiaoai.build``. All capture, playback, LED, volume and vendor-signal
behaviour stays in ``src/rui/device/xiaoai/``.

The ``kit_live_device_required`` attribute is a contract-kit-only hint, not part
of ``DeviceAdapter``. The LX06 is an inbound transport: ``open()`` listens for the
open-xiaoai client and frames/playback only exist once that client dials in, so a
host-free kit run must record those checks as SKIP instead of pretending the
device is present. The audio plane ignores the hint.
"""
from __future__ import annotations

from collections.abc import Mapping

from rui.config.schema import Config, DeviceConfig
from rui.device.contract import DeviceAdapter
from rui.device.xiaoai import build as build_xiaoai

__all__ = ["KIT_LIVE_DEVICE_REASON", "build"]

_DEFAULT_ID = "xiaoai"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_WAKE_WORDS = ["你好小瑞"]

#: Kit-only hint: probe()/method surface are testable without hardware, but capture
#: and playback need the real inbound client. Documented in docs/plug-test-kit.md.
KIT_LIVE_DEVICE_REASON = (
    "the LX06 is an inbound transport: open() listens on ws://:4412 and capture/"
    "playback need the open-xiaoai client to dial in; probe() and the method surface "
    "were still exercised"
)


def build(spec: object | None = None, config: object | None = None) -> DeviceAdapter:
    """Build the LX06 adapter from a device spec and optional runtime config.

    ``spec`` may be a real :class:`DeviceConfig` (production) or any object with
    ``id``/``host``/``room``/``params`` (the plug-test kit passes a fake device).
    ``config`` may be a real :class:`Config`; the kit's fake config has no ``audio``
    block, so the LX06 capture/voice defaults are used for the host-free probe.
    """
    device = _device_config(spec)
    runtime = config if hasattr(config, "audio") else Config()
    adapter = build_xiaoai(device, runtime)
    # Kit-only annotations; ``DeviceAdapter`` itself stays unchanged.
    adapter.kit_live_device_required = True  # type: ignore[attr-defined]
    adapter.kit_live_device_reason = KIT_LIVE_DEVICE_REASON  # type: ignore[attr-defined]
    return adapter


def _device_config(spec: object | None) -> DeviceConfig:
    """Return a real ``DeviceConfig``, passing a production one through untouched."""
    if isinstance(spec, DeviceConfig):
        return spec
    params = getattr(spec, "params", None)
    params = dict(params) if isinstance(params, Mapping) else {}
    return DeviceConfig(
        id=_text(getattr(spec, "id", ""), _DEFAULT_ID),
        adapter=_text(getattr(spec, "adapter", ""), _DEFAULT_ID),
        host=_text(getattr(spec, "host", "") or params.get("host"), _DEFAULT_HOST),
        room=str(getattr(spec, "room", "") or params.get("room") or ""),
        wake_words=_wake_words(spec, params),
        params=params,
        token_env=_text(
            getattr(spec, "token_env", "") or params.get("token_env"), "RUI_DEVICE_TOKEN"
        ),
        allow_no_token=bool(
            getattr(spec, "allow_no_token", False) or params.get("allow_no_token", False)
        ),
    )


def _wake_words(spec: object | None, params: Mapping[str, object]) -> list[str]:
    raw = getattr(spec, "wake_words", None) or params.get("wake_words")
    if isinstance(raw, str):
        return [raw] if raw.strip() else list(_DEFAULT_WAKE_WORDS)
    if isinstance(raw, (list, tuple)):
        words = [str(word).strip() for word in raw if str(word).strip()]
        if words:
            return words
    return list(_DEFAULT_WAKE_WORDS)


def _text(value: object, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default
