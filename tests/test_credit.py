"""Device-paced playback: the plane never runs further ahead than the speaker reports.

The wall clock and the speaker's clock are not the same clock. These tests pin the
four behaviours the old deployment's Credit had measured (window, clamp, timeout,
reset) plus the adapter's parsing of the report itself.
"""
from __future__ import annotations

import asyncio

from house_ear.audio.process import Credit
from house_ear.config.schema import CaptureConfig, DeviceConfig, VoiceConfig
from house_ear.device.xiaoai.adapter import XiaoaiAdapter


class FakeDevice:
    """Reports a play head on demand; `report()` is the device consuming a block."""

    reports_consumption = True
    sink_generation = 1

    def __init__(self) -> None:
        self.consumed = 0
        self._event = asyncio.Event()

    async def wait_consumed(self, since: int, timeout: float) -> int:
        if self.consumed > since:
            return self.consumed
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            return since
        return self.consumed

    def report(self, n: int) -> None:
        self.consumed = n
        self._event.set()


def test_window_blocks_the_third_block_until_the_device_reports() -> None:
    async def run() -> None:
        credit = Credit(window=2, timeout=0.5)
        device = FakeDevice()
        assert await credit.wait(device, 0.1) is True   # nothing sent yet
        credit.note_sent(0.1)
        assert await credit.wait(device, 0.1) is True   # 1 in flight
        credit.note_sent(0.1)                            # 2 in flight = the window
        blocked = asyncio.create_task(credit.wait(device, 0.1))
        await asyncio.sleep(0.05)
        assert not blocked.done(), "the third block must wait for a report"
        device.report(1)
        await asyncio.sleep(0.01)
        assert blocked.done() and blocked.result() is True
        assert credit.acked == 1
        assert credit.lead == 0.1

    asyncio.run(run())


def test_a_stale_report_cannot_open_a_window() -> None:
    """A report from the previous player arriving after reset is clamped to what was sent."""
    credit = Credit(window=2)
    credit.note_sent(0.1)
    credit.ack(99)
    assert credit.acked == 1


def test_a_device_that_stops_reporting_falls_back_to_the_clock() -> None:
    async def run() -> None:
        credit = Credit(window=2, timeout=0.05)
        device = FakeDevice()
        credit.note_sent(0.1)
        credit.note_sent(0.1)
        assert await credit.wait(device, 0.1) is False
        assert credit.dead is True
        # Dead is sticky: every later block returns at once instead of waiting 1.5 s.
        assert await credit.wait(device, 0.1) is False
        assert await credit.wait(device, 0.1) is False

    asyncio.run(run())


def test_reset_starts_a_new_player_from_zero() -> None:
    credit = Credit(window=2, timeout=0.05)
    credit.note_sent(0.1)
    credit.ack(1)
    credit.dead = True
    credit.reset()
    assert (credit.sent, credit.acked, credit.dead, credit.lead) == (0, 0, False, 0.0)


def test_adapter_records_the_consumed_report() -> None:
    adapter = XiaoaiAdapter(
        DeviceConfig(id="living", adapter="xiaoai", host="127.0.0.1"),
        CaptureConfig(),
        VoiceConfig(),
    )
    adapter._on_text('{"Event": {"event": "consumed", "n": 3}}')
    assert adapter.consumed == 3
    # The client has also used a data envelope; both shapes mean the same thing.
    adapter._on_text('{"Event": {"event": "consumed", "data": {"n": 5}}}')
    assert adapter.consumed == 5
    # An older report must not move the play head backwards.
    adapter._on_text('{"Event": {"event": "consumed", "data": {"n": 4}}}')
    assert adapter.consumed == 5
