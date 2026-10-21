"""Control-plane side of the boundary: connect to (or supervise) the audio plane."""
from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

from ..config import Config
from ..device import build_adapters
from ..trace import now_ms
from .process import default_socket_path, resolve_socket_path
from .protocol import (
    CONTROL,
    ControlMessage,
    FrameType,
    Hello,
    Metrics,
    QueryMetrics,
    Ready,
    Shutdown,
    encode_json,
    read_frame,
)


class AudioPlaneError(RuntimeError):
    pass


class AudioPlaneClient:
    """A connected audio plane: send control, receive PCM and metrics."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.ready: Ready | None = None
        self.metrics: Metrics | None = None
        self.voice_bytes = 0
        self.voice_frames = 0
        self.last_voice_ms: float | None = None
        self._metrics_event = asyncio.Event()
        self._read_task: asyncio.Task | None = None

    async def send(self, msg: ControlMessage) -> None:
        self.writer.write(encode_json(msg))
        await self.writer.drain()

    async def read_frame(self) -> tuple[FrameType, bytes]:
        return await read_frame(self.reader)

    async def pump(self) -> None:
        """Consume everything the plane sends; latest metrics wins."""
        while True:
            kind, payload = await self.read_frame()
            if kind is FrameType.VOICE_PCM:
                self.voice_frames += 1
                self.voice_bytes += max(0, len(payload) - 12)
                self.last_voice_ms = now_ms()
                continue
            if kind is not FrameType.CONTROL:
                continue
            msg = CONTROL.validate_json(payload)
            if isinstance(msg, Metrics):
                self.metrics = msg
                self._metrics_event.set()
            elif isinstance(msg, Ready):
                self.ready = msg

    def start_pump(self) -> None:
        self._read_task = asyncio.create_task(self.pump(), name="audio-plane-read")

    async def request_metrics(self, timeout_s: float = 3.0) -> Metrics | None:
        """Ask for a metrics snapshot instead of using the 2 s periodic one."""
        self._metrics_event.clear()
        await self.send(QueryMetrics())
        try:
            await asyncio.wait_for(self._metrics_event.wait(), timeout=timeout_s)
        except TimeoutError:
            return self.metrics
        return self.metrics

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.send(Shutdown())
        if self._read_task is not None:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._read_task
        self.writer.close()
        with contextlib.suppress(ConnectionError):
            await self.writer.wait_closed()


class AudioPlaneSupervisor:
    """Spawns the audio plane as a child process and keeps a connection to it."""

    def __init__(
        self,
        cfg: Config,
        config_path: Path | None = None,
        socket_path: Path | None = None,
        device_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self.socket_path = resolve_socket_path(socket_path or default_socket_path())
        self.device_id = device_id
        self.proc: asyncio.subprocess.Process | None = None
        self.client: AudioPlaneClient | None = None
        self.error = ""

    def _pick_device(self) -> str | None:
        if self.device_id:
            return self.device_id
        adapters = build_adapters(self.cfg)
        for dev_id in adapters.adapters:
            return dev_id
        return None

    async def start(self, timeout_s: float = 15.0) -> AudioPlaneSupervisor:
        if self.config_path is None:
            raise AudioPlaneError("the audio plane needs a config file on disk")
        device = self._pick_device()
        if device is None:
            raise AudioPlaneError("no device this build can drive")

        self.proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "house_ear.audio.process",
            "--config",
            str(self.config_path),
            "--device",
            device,
            "--socket",
            str(self.socket_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self.proc.returncode is not None:
                err = (await self.proc.stderr.read()).decode(errors="replace") if self.proc.stderr else ""
                raise AudioPlaneError(f"audio plane exited with {self.proc.returncode}: {err[-400:]}")
            if self.socket_path.exists():
                try:
                    reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
                else:
                    self.client = AudioPlaneClient(reader, writer)
                    await self.client.send(
                        Hello(
                            device_id=device,
                            voice_rate=self.cfg.audio.voice.rate,
                            frame_ms=self.cfg.audio.voice.frame_ms,
                        )
                    )
                    kind, payload = await self.client.read_frame()
                    if kind is not FrameType.CONTROL:
                        raise AudioPlaneError("audio plane did not greet us")
                    msg = CONTROL.validate_json(payload)
                    if not isinstance(msg, Ready):
                        raise AudioPlaneError(f"audio plane refused: {msg.model_dump()}")
                    self.client.ready = msg
                    self.client.start_pump()
                    return self
            await asyncio.sleep(0.05)
        await self.stop()
        raise AudioPlaneError(f"audio plane did not accept a connection in {timeout_s:.0f}s")

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            if self.proc.returncode is None:
                self.proc.kill()
        self.proc = None

    @property
    def running(self) -> bool:
        return self.client is not None and self.proc is not None and self.proc.returncode is None

    def status(self) -> dict:
        if self.client is None:
            return {"running": False, "error": self.error}
        metrics = self.client.metrics
        return {
            "running": self.running,
            "pid": self.proc.pid if self.proc else None,
            "device_id": self.client.ready.device_id if self.client.ready else "",
            "capture": {
                "rate": self.client.ready.capture_rate if self.client.ready else 0,
                "channels": self.client.ready.capture_channels if self.client.ready else 0,
                "mic_channels": self.client.ready.mic_channels if self.client.ready else 0,
                "reference_channels": self.client.ready.reference_channels if self.client.ready else 0,
            },
            "metrics": metrics.model_dump() if metrics else None,
            "voice_frames_received": self.client.voice_frames,
            "voice_bytes_received": self.client.voice_bytes,
        }

