"""Audio plane: AEC, KWS, VAD and playback, in their own process.

Everything here is driven by the frames an adapter hands over. The plane never reads
`audio.capture` to decide a layout - a frame says its own `channels`, `sample_rate` and
`reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
(16 kHz / 1 ch / no reference) can run through the same code path with different
adapters. Config values are expectations the doctor checks, not assumptions the plane
bakes in.

Run standalone:
    python -m house_ear.audio.process --config config.yaml --device living
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from ..config import Config, load_config
from ..device import build_adapters
from ..device.contract import AudioFrame, DeviceAdapter
from ..trace import now_ms
from .decimate import Decimator
from .protocol import (
    CONTROL,
    Error,
    FrameType,
    Hello,
    Metrics,
    QueryMetrics,
    Ready,
    encode_json,
    encode_pcm,
    read_frame,
)
from .ring import ArrivalStats, PcmRing

METRICS_EVERY_S = 2.0


class AudioPlane:
    def __init__(self, cfg: Config, device_id: str, socket_path: Path) -> None:
        self.cfg = cfg
        self.device_id = device_id
        self.socket_path = socket_path
        self.adapter: DeviceAdapter | None = None
        self.ring = PcmRing(capacity_ms=cfg.audio.playback.queue_ms, frame_ms=cfg.audio.voice.frame_ms)
        self.arrivals = ArrivalStats()
        self.frames_in = 0
        self.frames_out = 0
        self.frames_dropped = 0
        self.no_consumer = 0
        self.cpu_s = 0.0
        self.audio_s = 0.0
        self.started = time.monotonic()
        self._stop = asyncio.Event()

    # ---------------------------------------------------------------- adapter
    async def _open_adapter(self) -> DeviceAdapter:
        adapters = build_adapters(self.cfg)
        if self.device_id in adapters.errors:
            raise RuntimeError(adapters.errors[self.device_id])
        adapter = adapters.adapters.get(self.device_id)
        if adapter is None:
            raise RuntimeError(f"no such device: {self.device_id}")
        await adapter.open()
        return adapter

    # ------------------------------------------------------------- pipeline
    def _mic_to_voice(self, frame: AudioFrame, decimator: Decimator) -> bytes:
        """Pick a speech channel out of the frame and decimate it to the voice rate.

        Mono out on purpose: the seam hands the control plane *one* voice-rate stream.
        Which microphone - or which beamformed sum - that is, stays a decision of the
        audio plane, so S3 can put AEC and beamforming behind the same contract without
        the control plane ever knowing how many mics a device has.
        """
        if not frame.pcm or frame.mic_channels < 1:
            return b""
        samples = np.frombuffer(frame.pcm, dtype="<i2")
        usable = frame.frame_samples * frame.channels
        samples = samples[:usable].reshape(-1, frame.channels)
        mic = samples[:, :1]  # channel 0 until AEC/beamforming lands in S3
        out = decimator.process(mic)
        return out.astype("<i2", copy=False).tobytes()

    async def _capture(self, connected: asyncio.Event) -> None:
        assert self.adapter is not None
        info = await self.adapter.probe()
        decimator = Decimator(
            factor=max(1, info.sample_rate // self.cfg.audio.voice.rate),
            channels=1,
        )
        last_ts: float | None = None
        async for frame in self.adapter.frames():
            if self._stop.is_set():
                break
            t0 = time.perf_counter()
            self.frames_in += 1
            self.audio_s += frame.duration_ms / 1000.0
            if last_ts is not None:
                self.arrivals.add(frame.ts_ms - last_ts)
            last_ts = frame.ts_ms
            pcm = self._mic_to_voice(frame, decimator)
            if not connected.is_set():
                self.no_consumer += 1
            elif not self.ring.push(frame.seq, now_ms(), pcm):
                self.frames_dropped += 1
            self.cpu_s += time.perf_counter() - t0

    async def _send_audio(self, writer: asyncio.StreamWriter, connected: asyncio.Event) -> None:
        """Consume the ring at real time and hand each block to the control plane."""
        period = self.cfg.audio.voice.frame_ms / 1000.0
        while not self._stop.is_set():
            await asyncio.sleep(period)
            if not connected.is_set():
                continue
            item = self.ring.pop()
            if item is None:
                continue
            t0 = time.perf_counter()
            seq, ts_ms, pcm = item
            writer.write(encode_pcm(FrameType.VOICE_PCM, seq, ts_ms, pcm))
            self.frames_out += 1
            self.cpu_s += time.perf_counter() - t0
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                connected.clear()
                return

    def metrics(self) -> Metrics:
        return Metrics(
            uptime_s=round(time.monotonic() - self.started, 1),
            frames_in=self.frames_in,
            frames_out=self.frames_out,
            frames_dropped=self.frames_dropped,
            no_consumer=self.no_consumer,
            jitter_p50_ms=round(self.arrivals.p50(), 2),
            jitter_p99_ms=round(self.arrivals.p99(), 2),
            cpu_s=round(self.cpu_s, 3),
            audio_s=round(self.audio_s, 2),
            rtf=round(self.cpu_s / self.audio_s, 4) if self.audio_s else 0.0,
        )

    async def _metrics_loop(self, writer: asyncio.StreamWriter, connected: asyncio.Event) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(METRICS_EVERY_S)
            if not connected.is_set():
                continue
            try:
                writer.write(encode_json(self.metrics()))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                connected.clear()
                return

    # ---------------------------------------------------------------- server
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected = asyncio.Event()
        tasks: list[asyncio.Task] = []
        try:
            self.adapter = await self._open_adapter()
            info = await self.adapter.probe()
            writer.write(
                encode_json(
                    Ready(
                        pid=os.getpid(),
                        device_id=info.id,
                        capture_rate=info.sample_rate,
                        capture_channels=info.channels,
                        mic_channels=info.mic_channels,
                        reference_channels=info.ref_channels,
                        voice_rate=self.cfg.audio.voice.rate,
                    )
                )
            )
            await writer.drain()
            connected.set()
            tasks = [
                asyncio.create_task(self._capture(connected), name="capture"),
                asyncio.create_task(self._send_audio(writer, connected), name="send-audio"),
                asyncio.create_task(self._metrics_loop(writer, connected), name="metrics"),
            ]
            while not self._stop.is_set():
                kind, payload = await read_frame(reader)
                if kind is not FrameType.CONTROL:
                    continue  # playback PCM arrives in S5
                msg = CONTROL.validate_json(payload)
                if msg.type == "shutdown":
                    break
                if msg.type == "hello":
                    assert isinstance(msg, Hello)
                    self.cfg.audio.voice.rate = msg.voice_rate
                elif isinstance(msg, QueryMetrics):
                    writer.write(encode_json(self.metrics()))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - reported to the client, then the process exits
            try:
                writer.write(encode_json(Error(message=str(exc))))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            raise
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if self.adapter is not None:
                await self.adapter.close()
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def serve(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.handle, path=str(self.socket_path))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:  # pragma: no cover - non-POSIX
                pass
        async with server:
            await self._stop.wait()
        self.socket_path.unlink(missing_ok=True)


def default_socket_path() -> Path:
    env = os.environ.get("HOUSE_EAR_AUDIO_SOCK")
    if env:
        return resolve_socket_path(Path(env))
    return resolve_socket_path(Path.home() / ".local" / "state" / "house-ear" / "audio.sock")


def resolve_socket_path(path: Path) -> Path:
    """Keep the path inside the platform's AF_UNIX limit (104 bytes on macOS).

    A deep `system.data_dir` would otherwise fail at bind time with a bare
    "AF_UNIX path too long". The short path is derived from the requested one, so
    both sides of the boundary resolve the same name without extra configuration.
    """
    text = str(path)
    if len(text.encode()) <= 96:
        return path
    digest = hashlib.sha1(text.encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"house-ear-{digest}.sock"


def build_plane(cfg: Config, device_id: str, socket_path: Path | None = None) -> AudioPlane:
    return AudioPlane(cfg, device_id, resolve_socket_path(socket_path or default_socket_path()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="house-ear-audio", description="house-ear audio plane")
    parser.add_argument("--config", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--socket", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    plane = build_plane(cfg, args.device, Path(args.socket) if args.socket else None)
    try:
        asyncio.run(plane.serve())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
