"""The mixer lives in the device module, not in the API layer.

`mysoftvol` is the vendor's knob: it gets rewritten whenever the vendor touches its own
player, so the only useful behaviour is "read it when asked". These tests pin the parsing
and the refusal to invent a number.
"""
from __future__ import annotations

import asyncio

import pytest

from house_ear.device.mock import MockAdapter
from house_ear.device.xiaoai.services import XiaoaiServices


class Shell:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.scripts: list[str] = []

    async def call(self, command: str, payload: object = None, timeout: float = 3.0) -> dict:
        self.scripts.append(str(payload))
        return {"data": {"stdout": self.answer}}


def test_the_mixer_value_is_parsed_from_amixer() -> None:
    shell = Shell("220\n")
    services = XiaoaiServices(shell)
    assert asyncio.run(services.read_volume()) == 220
    assert "numid=177" in shell.scripts[0]


@pytest.mark.parametrize("answer", ["", "not a number", "999", "-3"])
def test_an_unreadable_mixer_is_none_not_a_guess(answer: str) -> None:
    assert asyncio.run(XiaoaiServices(Shell(answer)).read_volume()) is None


def test_writing_refuses_the_zero_that_means_leave_it_to_the_vendor() -> None:
    services = XiaoaiServices(Shell("ok"))
    with pytest.raises(ValueError):
        asyncio.run(services.set_volume(0))


def test_the_mock_device_round_trips_a_volume() -> None:
    adapter = MockAdapter("living")

    async def run() -> None:
        await adapter.services.set_volume(180)
        assert await adapter.services.read_volume() == 180

    asyncio.run(run())
