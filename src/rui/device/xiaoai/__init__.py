"""Xiaomi 小爱音箱 Pro (LX06) support: tier 2 over the device's own client."""
from __future__ import annotations

from ...config.schema import Config, DeviceConfig, effective_device_audio
from .adapter import DEFAULT_LISTEN_PORT, XiaoaiAdapter

__all__ = ["DEFAULT_LISTEN_PORT", "XiaoaiAdapter", "build"]


def build(dev: DeviceConfig, cfg: Config) -> XiaoaiAdapter:
    """Registry entry: the adapter needs the audio layout it is expected to deliver.

    0.6: the layout comes from the three-layer merge, not from the global block alone,
    so this LX06 asks the client for the plug manifest's 8 ch / 48 kHz capture unless
    the config overrides it (device default < global config < devices[].audio).
    """
    effective = effective_device_audio(cfg, dev)
    return XiaoaiAdapter(dev, effective.capture, effective.voice)
