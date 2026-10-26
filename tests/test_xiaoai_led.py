"""The xiaoai adapter's ring: capability, state delegation, and no shell in the caller."""
from __future__ import annotations

import asyncio

from xiaoai_plug.adapter import XiaoaiAdapter
from xiaoai_plug.services import XiaoaiServices

from rui.config.schema import CaptureConfig, DeviceConfig, LedConfig, VoiceConfig
from rui.device.contract import Capability


def _adapter(**led: object) -> XiaoaiAdapter:
    dev = DeviceConfig(
        id="living", adapter="lx06", host="127.0.0.1",
        led=LedConfig(enabled=True, **led),
    )
    return XiaoaiAdapter(dev, CaptureConfig(), VoiceConfig())


def test_the_ring_is_declared_and_delegates(monkeypatch) -> None:
    scripts: list[str] = []

    async def fake_shell(self, script: str, timeout: float = 3.0) -> str:
        scripts.append(script)
        return ""

    monkeypatch.setattr(XiaoaiServices, "shell", fake_shell)
    adapter = _adapter()
    assert Capability.LED in adapter.info.capabilities

    async def run() -> None:
        await adapter.set_led("thinking")
        assert adapter.led.state == "thinking"
        # The call returned before the animation finished: the script is a task.
        assert scripts == []
        await asyncio.sleep(0)
        assert scripts and "led_rgb" in scripts[0]
        # "wake" is the outward sweep and ends on listening green.
        await adapter.set_led("wake")
        assert adapter.led.state == "listening"

    asyncio.run(run())


def test_a_disabled_ring_keeps_state_but_sends_nothing(monkeypatch) -> None:
    scripts: list[str] = []

    async def fake_shell(self, script: str, timeout: float = 3.0) -> str:
        scripts.append(script)
        return ""

    monkeypatch.setattr(XiaoaiServices, "shell", fake_shell)
    dev = DeviceConfig(
        id="living", adapter="lx06", host="127.0.0.1", led=LedConfig(enabled=False),
    )
    adapter = XiaoaiAdapter(dev, CaptureConfig(), VoiceConfig())

    async def run() -> None:
        await adapter.set_led("thinking")
        await asyncio.sleep(0)
        assert adapter.led.state == "thinking"
        assert scripts == []

    asyncio.run(run())
