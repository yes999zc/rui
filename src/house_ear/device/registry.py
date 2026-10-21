"""Adapter lookup. v1 ships xiaoai + mock; new devices register here.

A missing adapter implementation must not take the control plane down: the console
has to come up, list the device and say what is wrong with it. So building returns
an AdapterSet - the adapters that exist, plus one error string per device that does
not - instead of raising out of application startup.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config.schema import Config, DeviceConfig
from .contract import DeviceAdapter
from .mock import MockAdapter


class AdapterUnavailable(RuntimeError):
    """The configured adapter exists in config but not in this build."""


@dataclass
class AdapterSet:
    adapters: dict[str, DeviceAdapter] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.adapters)


def _build(dev: DeviceConfig, cfg: Config) -> DeviceAdapter:
    if dev.adapter == "mock":
        return MockAdapter(dev.id, dev.host, dev.room)
    if dev.adapter == "xiaoai":
        # The adapter needs the capture layout to tell the device what to record.
        from .xiaoai.adapter import XiaoaiAdapter

        return XiaoaiAdapter(dev, cfg.audio.capture, cfg.audio.voice)
    raise AdapterUnavailable(f"unknown adapter: {dev.adapter}")


def build_adapters(cfg: Config) -> AdapterSet:
    out = AdapterSet()
    for dev in cfg.devices:
        if not dev.enabled:
            continue
        try:
            out.adapters[dev.id] = _build(dev, cfg)
        except AdapterUnavailable as exc:
            out.errors[dev.id] = str(exc)
    return out
