"""rui#17: `_greet` must yield while waiting for the plane's socket.

The old loop polled ``socket_path.exists()`` and ``proc.returncode`` with no
``await``, so while the child was starting (or dead) it froze the event loop for
the whole greet timeout. The fix yields between polls and bounds the greet
``read_frame`` with the remaining deadline; cold start may add at most the poll
interval.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

from rui.audio.client import AudioPlaneError, AudioPlaneSupervisor
from rui.audio.protocol import HEADER, FrameType, Ready, encode_json, parse_header, read_frame
from rui.config import Config


class _Proc:
    """Minimal stand-in for the child process while it is still starting."""

    returncode = None


async def _serve_ready(path: Path) -> asyncio.AbstractServer:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readexactly(HEADER.size)
        _kind, length = parse_header(header)
        if length:
            await reader.readexactly(length)
        writer.write(
            encode_json(
                Ready(
                    pid=1234,
                    device_id="living",
                    capture_rate=16000,
                    capture_channels=1,
                    mic_channels=1,
                    reference_channels=0,
                    voice_rate=16000,
                )
            )
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(handler, path=str(path))


def test_greet_yields_to_the_event_loop_while_the_socket_is_missing(
    tmp_path: Path,
) -> None:
    async def scenario() -> int:
        supervisor = AudioPlaneSupervisor(
            Config(), socket_path=tmp_path / "missing.sock", device_id="living"
        )
        supervisor.proc = _Proc()  # type: ignore[assignment]
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.001)

        tick = asyncio.create_task(ticker())
        try:
            with pytest.raises(AudioPlaneError):
                await supervisor._greet("living", 1.0)
        finally:
            tick.cancel()
            with pytest.raises(asyncio.CancelledError):
                await tick
        return ticks

    ticks = asyncio.run(scenario())
    print(f"greet ticker turns in 1 s: {ticks}")
    # A 1 ms ticker must still get scheduled during the 1 s wait; the old busy loop
    # gave it zero turns. 95 leaves room for scheduling jitter on CI.
    assert ticks >= 95, f"event loop starved: only {ticks} ticker turns in 1 s"


def test_greet_connects_promptly_once_the_socket_appears(tmp_path: Path) -> None:
    async def scenario() -> float:
        socket_path = Path(tempfile.mkdtemp(prefix="rui-greet-")) / "late.sock"
        supervisor = AudioPlaneSupervisor(
            Config(), socket_path=socket_path, device_id="living"
        )
        supervisor.proc = _Proc()  # type: ignore[assignment]
        greet = asyncio.create_task(supervisor._greet("living", 2.0))
        await asyncio.sleep(0.03)  # let the poll loop start
        started = time.perf_counter()
        server = await _serve_ready(socket_path)
        try:
            await greet
        finally:
            server.close()
            await server.wait_closed()
        return time.perf_counter() - started

    elapsed = asyncio.run(scenario())
    print(f"cold-start greet latency: {elapsed * 1000:.1f} ms")
    assert elapsed <= 0.05, f"cold-start greet added {elapsed * 1000:.1f} ms"


def test_read_frame_reads_with_a_timeout() -> None:
    async def scenario() -> float:
        from rui.audio.protocol import read_frame

        reader = asyncio.StreamReader()
        started = time.perf_counter()
        try:
            await asyncio.wait_for(read_frame(reader, timeout=0.02), timeout=0.5)
        except TimeoutError:
            pass
        return time.perf_counter() - started

    elapsed = asyncio.run(scenario())
    # If read_frame ignores its own timeout the outer 0.5 s guard fires instead;
    # the elapsed check distinguishes the two.
    assert elapsed < 0.5, f"read_frame ignored its timeout ({elapsed:.2f} s)"


def test_greet_retries_a_socket_that_accepts_then_closes(tmp_path: Path) -> None:
    """A child that is exiting can accept the connection and drop it immediately."""

    async def scenario() -> tuple[float, BaseException | None]:
        socket_path = Path(tempfile.mkdtemp(prefix="rui-greet-")) / "stale.sock"

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handler, path=str(socket_path))
        supervisor = AudioPlaneSupervisor(
            Config(), socket_path=socket_path, device_id="living"
        )
        supervisor.proc = _Proc()  # type: ignore[assignment]
        started = time.perf_counter()
        error: BaseException | None = None
        try:
            await supervisor._greet("living", 0.4)
        except BaseException as exc:  # noqa: BLE001 - the type is the assertion
            error = exc
        finally:
            server.close()
            await server.wait_closed()
        return time.perf_counter() - started, error

    elapsed, error = asyncio.run(scenario())
    assert isinstance(error, AudioPlaneError)
    # It retried until the greet budget ran out instead of failing on the first EOF.
    assert elapsed >= 0.3, f"greet gave up after {elapsed:.2f} s"


def test_read_frame_timeout_covers_header_and_payload_as_one_deadline() -> None:
    async def scenario() -> float:
        reader = asyncio.StreamReader()

        async def feed_header_late() -> None:
            await asyncio.sleep(0.08)
            reader.feed_data(HEADER.pack(int(FrameType.CONTROL), 10))
            # payload deliberately never arrives

        feeder = asyncio.create_task(feed_header_late())
        started = time.perf_counter()
        with pytest.raises(TimeoutError):
            await read_frame(reader, timeout=0.1)
        elapsed = time.perf_counter() - started
        await feeder
        return elapsed

    elapsed = asyncio.run(scenario())
    # Same timeout for both reads: 0.1 s total, not 0.08 + 0.1 = 0.18 s.
    assert elapsed < 0.14, f"timeout applied per read, not per frame ({elapsed:.2f} s)"
