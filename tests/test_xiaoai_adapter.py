"""LX06 adapter against a fake speaker that speaks the real client's protocol."""
from __future__ import annotations

import asyncio
import json
import socket

import numpy as np
import pytest
from websockets.asyncio.client import connect

from house_ear.config.schema import CaptureConfig, DeviceConfig, VoiceConfig
from house_ear.device.xiaoai import protocol
from house_ear.device.xiaoai.adapter import XiaoaiAdapter

TOKEN_ENV = "HOUSE_EAR_TEST_DEVICE_TOKEN"
TOKEN = "test-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_adapter(port: int, *, rate: int = 48000, allow_no_token: bool = False) -> XiaoaiAdapter:
    dev = DeviceConfig(
        id="living", adapter="xiaoai", host="<LAN-IP>", listen_port=port,
        token_env=TOKEN_ENV, allow_no_token=allow_no_token,
    )
    capture = CaptureConfig(rate=rate)
    return XiaoaiAdapter(dev, capture, VoiceConfig())


def tone_frame(rate: int, channels: int, mic_hz: float, ref_amplitude: int, seq: int) -> bytes:
    """20 ms of the real layout: 6 mics (ch0 carries a tone) + 2 reference channels."""
    samples = rate // 50
    t = (np.arange(samples) + seq * samples) / rate
    block = np.zeros((samples, channels), dtype=np.int16)
    block[:, 0] = (8000 * np.sin(2 * np.pi * mic_hz * t)).astype(np.int16)
    block[:, 6:] = ref_amplitude
    return block.tobytes()


async def run_scenario(rate: int = 48000) -> dict:
    port = free_port()
    adapter = make_adapter(port, rate=rate)
    await adapter.open()
    received: list[dict] = []
    frames = []
    try:
        async with connect(f"ws://127.0.0.1:{port}/?token={TOKEN}", max_size=None) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            received.append(first)

            async def collector() -> None:
                async for frame in adapter.frames():
                    frames.append(frame)
                    if len(frames) >= 10:
                        return

            task = asyncio.create_task(collector())
            for seq in range(10):
                await ws.send(protocol.stream("record", tone_frame(rate, 8, 1000.0, 20000, seq)))
                await asyncio.sleep(0.005)
            await asyncio.wait_for(task, timeout=3)


            # playback: the adapter must open the client's player first, then stream.
            # start_play is acknowledged now: the client's player is only able to accept
            # frames once it returns, so play() waits for it (a real client answers).
            play_task = asyncio.create_task(
                adapter.play(b"\x00\x01" * 800, sample_rate=16000, channels=1)
            )
            open_player = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            await ws.send(json.dumps({
                "Response": {"id": open_player["Request"]["id"], "code": 0},
            }))
            await asyncio.wait_for(play_task, timeout=2)
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            await adapter.stop_playback()
            stop = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            received.extend([open_player, reply, stop])
    finally:
        await adapter.close()
    return {"first": received[0], "start_play": received[1], "play": received[2],
            "stop": received[3], "frames": frames, "health": adapter.health()}


@pytest.fixture()
def device_token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return TOKEN


@pytest.mark.parametrize("rate", [48000, 16000])
def test_adapter_asks_for_the_configured_layout_and_publishes_frames(device_token, rate) -> None:
    result = asyncio.run(run_scenario(rate))

    payload = result["first"]["Request"]["payload"]
    assert result["first"]["Request"]["command"] == "start_recording"
    assert payload["channels"] == 8
    assert payload["sample_rate"] == rate
    assert payload["bits_per_sample"] == 16
    assert payload["mic_shift"] == 12 and payload["ref_shift"] == 16

    frames = result["frames"]
    assert len(frames) == 10
    assert all(f.channels == 8 for f in frames)
    assert all(f.reference_channels == 2 for f in frames)
    assert all(f.sample_rate == rate for f in frames)
    assert all(f.frame_samples == rate // 50 for f in frames)
    assert [f.seq for f in frames] == list(range(1, 11))

    mic = np.frombuffer(frames[0].pcm, dtype="<i2").reshape(-1, 8)
    assert np.abs(mic[:, 0]).max() > 1000      # the tone arrived on a microphone channel
    assert np.all(mic[:, 6] == 20000)          # reference channels keep their own level
    assert result["health"]["frames_received"] == 10
    assert result["health"]["bad_frames"] == 0


def test_playback_opens_the_player_streams_and_stops(device_token) -> None:
    result = asyncio.run(run_scenario(48000))
    open_player = result["start_play"]
    assert open_player["Request"]["command"] == "start_play"
    assert open_player["Request"]["payload"]["channels"] == 1
    assert open_player["Request"]["payload"]["sample_rate"] == 16000
    assert open_player["Request"]["payload"]["buffer_size"] == 16000 * 300 // 1000
    play = result["play"]
    assert play["tag"] == "play"
    assert len(play["bytes"]) == 1600
    assert result["stop"]["Request"]["command"] == "stop_play"


def test_play_refuses_stereo(device_token) -> None:
    async def run() -> None:
        adapter = make_adapter(free_port())
        await adapter.open()
        try:
            await adapter.play(b"\x00\x00", sample_rate=48000, channels=2)
        finally:
            await adapter.close()

    with pytest.raises(ValueError, match="mono"):
        asyncio.run(run())


def test_wrong_token_is_rejected(device_token) -> None:
    async def run() -> int:
        port = free_port()
        adapter = make_adapter(port)
        await adapter.open()
        try:
            try:
                async with connect(f"ws://127.0.0.1:{port}/?token=wrong", max_size=None) as ws:
                    await asyncio.wait_for(ws.recv(), timeout=2)
            except Exception:  # noqa: BLE001 - the server closes; that is the assertion
                pass
            return adapter.health()["rejected"]
        finally:
            await adapter.close()

    assert asyncio.run(run()) == 1


def test_open_fails_closed_without_a_token(monkeypatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    async def run() -> None:
        adapter = make_adapter(free_port())
        await adapter.open()

    with pytest.raises(RuntimeError, match="refusing"):
        asyncio.run(run())


def test_a_tokenless_listener_is_an_explicit_dev_switch(monkeypatch) -> None:
    """The LX06 boot script cannot send a token, so a dev run has to open the door.

    The switch must be opt-in per device and the connection must still be served: the
    client is handed its start_recording request, exactly as with a token.
    """
    monkeypatch.delenv(TOKEN_ENV, raising=False)

    async def run() -> bool:
        port = free_port()
        adapter = make_adapter(port, allow_no_token=True)
        await adapter.open()
        try:
            async with connect(f"ws://127.0.0.1:{port}/", max_size=None) as ws:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                return message["Request"]["command"] == "start_recording"
        finally:
            await adapter.close()

    assert asyncio.run(run()) is True
