"""Xiaomi 小爱音箱 Pro (LX06) support: tier 2 over the device's own client."""
from __future__ import annotations

from ...config.schema import Config, DeviceConfig
from .adapter import DEFAULT_LISTEN_PORT, XiaoaiAdapter

__all__ = ["DEFAULT_LISTEN_PORT", "XiaoaiAdapter", "build"]


def build(dev: DeviceConfig, cfg: Config) -> XiaoaiAdapter:
    """Registry entry: the adapter needs the audio layout it is expected to deliver."""
    return XiaoaiAdapter(dev, cfg.audio.capture, cfg.audio.voice)
