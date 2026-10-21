"""Audio plane: AEC, KWS, VAD and playback, in their own process.

Everything here is driven by the frames an adapter hands over. The plane never reads
`audio.capture` to decide a layout - a frame says its own `channels`, `sample_rate` and
`reference_channels`, so an LX06 (48 kHz / 8 ch / 6 mic + 2 ref) and a future ESP32
(16 kHz / 1 ch / no reference) run through the same code path with different adapters.
Config values are expectations the doctor checks, not assumptions the plane bakes in.

Pipeline today: decimate to the voice rate -> echo-cancel each mic against the reference
-> emit the channel that survived best -> hand one mono stream to the control plane.
Playback (the debug wav loop, CLI only) travels the other way through the adapter.

Run standalone:
    python -m house_ear.audio.process --config config.yaml --device living
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from ..config import Config, load_config
from ..device import build_adapters
from ..device.contract import AudioFrame, DeviceAdapter
from ..trace import now_ms
from .aec import AecChain, AecUnavailable
from .decimate import Decimator
from .protocol import (
    CONTROL,
    Error,
    FrameType,
    Hello,
    Metrics,
    PlayFile,
    QueryMetrics,
    Ready,
    StopAudio,
    encode_json,
    encode_pcm,
    read_frame,
)
from .resample import resample
from .ring import ArrivalStats, PcmRing

METRICS_EVERY_S = 2.0
PLAY_CHUNK_MS = 100
log = logging.getLogger(__name__)


class AudioPlane:
    def __init__(self, cfg: Config, device_id: str, socket_path: Path) -> None:
        self.cfg = cfg
        self.device_id = device_id
        self.socket_path = socket_path
        self.adapter: DeviceAdapter | None = None
        self.ring = PcmRing(capacity_ms=cfg.audio.playback.queue_ms, frame_ms=cfg.audio.voice.frame_ms)
        self.arrivals = ArrivalStats()
        self.aec: AecChain | None = None
        self.aec_error = ""
        self.frames_in = 0
        self.frames_out = 0
        self.frames_dropped = 0
        self.no_consumer = 0
        self.cpu_s = 0.0
        self.audio_s = 0.0
        self.playback_s = 0.0
        self.playback_path = ""
        self.last_playback_error = ""
        self._playback_task: asyncio.Task | None = None
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

    def _build_aec(self, mic_channels: int, ref_channels: int) -> None:
        if not self.cfg.audio.aec.enabled:
            return
        if ref_channels < 1:
            self.aec_error = "device has no reference channel: AEC and barge-in unavailable"
            log.warning("aec off: %s", self.aec_error)
            return
        try:
            self.aec = AecChain(
                channels=mic_channels,
                rate=self.cfg.audio.voice.rate,
                taps=self.cfg.audio.aec.filter_len,
            )
        except AecUnavailable as exc:
            self.aec_error = str(exc)
            log.warning("aec off: %s", exc)

    # ------------------------------------------------------------- pipeline
    def _frame_to_voice(self, frame: AudioFrame, decimator: Decimator) -> bytes:
        """Frame -> one mono voice-rate block, echo-cancelled when a reference exists."""
        if not frame.pcm or frame.mic_channels < 1:
            return b""
        samples = np.frombuffer(frame.pcm, dtype="<i2")
        usable = frame.frame_samples * frame.channels
        block = decimator.process(samples[:usable].reshape(-1, frame.channels))

        if self.aec is not None and frame.reference_channels > 0:
            ref = block[:, frame.mic_channels]
            mono = self.aec.process(block[:, : frame.mic_channels], ref)
        else:
            mono = block[:, 0]
        return np.clip(mono * 32768.0, -32768, 32767).astype("<i2").tobytes()

    async def _capture(self, connected: asyncio.Event) -> None:
        assert self.adapter is not None
        info = await self.adapter.probe()
        self._build_aec(info.mic_channels, info.ref_channels)
        decimator = Decimator(
            factor=max(1, info.sample_rate // self.cfg.audio.voice.rate),
            channels=info.channels,
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
            pcm = self._frame_to_voice(frame, decimator)
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

    # -------------------------------------------------------------- playback
    def _start_playback(self, msg: PlayFile) -> str:
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
        self.playback_path = msg.path
        self._playback_task = asyncio.create_task(
            self._play_file(Path(msg.path), msg.loop, msg.gain), name="playback"
        )
        return msg.path

    async def _play_file(self, path: Path, loop: bool, gain: float) -> None:
        try:
            pcm, rate = read_wav_mono(path)
        except (OSError, wave.Error) as exc:
            log.warning("playback: cannot read %s: %s", path, exc)
            return
        target = self.cfg.audio.playback.rate
        audio = np.clip(resample(pcm, rate, target) * float(gain), -1.0, 1.0)
        adapter = self.adapter
        if adapter is None:
            return
        log.info("playback: %s %.1f s at %s Hz", path.name, audio.size / target, target)
        try:
            await self._stream_file(audio, target, adapter, loop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed speaker must not kill the plane
            self.last_playback_error = f"{type(exc).__name__}: {exc}"
            log.warning("playback failed: %s", self.last_playback_error)
            return
        log.info("playback finished: %s", path.name)

    async def _stream_file(self, audio: np.ndarray, target: int, adapter: DeviceAdapter,
                           loop: bool) -> None:
        step = max(1, target * PLAY_CHUNK_MS // 1000)
        while not self._stop.is_set():
            for start in range(0, audio.size, step):
                if self._stop.is_set():
                    return
                chunk = audio[start:start + step]
                await adapter.play(
                    np.round(chunk * 32767.0).astype("<i2").tobytes(),
                    sample_rate=target,
                    channels=1,
                )
                self.playback_s += chunk.size / target
                await asyncio.sleep(PLAY_CHUNK_MS / 1000.0)
            if not loop:
                break

    async def _stop_playback(self) -> None:
        if self._playback_task is not None and not self._playback_task.done():
            self._playback_task.cancel()
            try:
                await self._playback_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._playback_task = None
        if self.adapter is not None:
            try:
                await self.adapter.stop_playback(self.cfg.audio.playback.fade_out_ms)
            except Exception as exc:  # noqa: BLE001 - the speaker may be gone
                log.info("playback: stop failed: %s", exc)

    def metrics(self) -> Metrics:
        return Metrics(
            uptime_s=round(time.monotonic() - self.started, 1),
            aec_enabled=self.aec is not None,
            aec_erle_db=self.aec.erle_db if self.aec is not None else None,
            aec_channel=self.aec.selected if self.aec is not None else 0,
            ref_active=bool(self.aec.ref_active) if self.aec is not None else False,
            speaker_connected=bool(getattr(self.adapter, "health", lambda: {})().get("connected"))
            if self.adapter is not None
            else False,
            playback_s=round(self.playback_s, 1),
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
                    continue  # PCM from the control plane is not used yet
                msg = CONTROL.validate_json(payload)
                if msg.type == "shutdown":
                    break
                if msg.type == "hello":
                    assert isinstance(msg, Hello)
                    self.cfg.audio.voice.rate = msg.voice_rate
                elif isinstance(msg, QueryMetrics):
                    writer.write(encode_json(self.metrics()))
                    await writer.drain()
                elif isinstance(msg, PlayFile):
                    path = self._start_playback(msg)
                    log.info("playback requested: %s loop=%s gain=%.2f", path, msg.loop, msg.gain)
                elif isinstance(msg, StopAudio):
                    await self._stop_playback()
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
            await self._stop_playback()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if self.aec is not None:
                self.aec.close()
                self.aec = None
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


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """int16/24/32-bit wav of any channel count -> mono float32 in [-1, 1]."""
    with wave.open(str(path)) as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        data = (v / float(1 << 23)).astype(np.float32)
    else:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / float(1 << 31)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


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
