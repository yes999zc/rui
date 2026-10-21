"""Adapter lookup. v1 ships xiaoai + mock; new devices register here."""
from __future__ import annotations

from ..config.schema import Config, DeviceConfig
from .contract import DeviceAdapter
from .mock import MockAdapter


# The xiaoai adapter lands in M1; importing it lazily keeps the skeleton runnable
# on machines without the audio stack installed.
def _build(dev: DeviceConfig) -> DeviceAdapter:
    if dev.adapter == "mock":
        return MockAdapter(dev.id, dev.host, dev.room)
    if dev.adapter == "xiaoai":
        from .xiaoai.adapter import XiaoaiAdapter

        return XiaoaiAdapter(dev)
    raise ValueError(f"unknown adapter: {dev.adapter}")


def build_adapters(cfg: Config) -> dict[str, DeviceAdapter]:
    return {d.id: _build(d) for d in cfg.devices if d.enabled}
