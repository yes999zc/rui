"""Control-plane side of the boundary: connect to (or supervise) the audio plane."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import Config
from ..device import build_adapters
from ..trace import now_ms
from .process import default_socket_path, resolve_socket_path
from .protocol import (
    CONTROL,
    ControlMessage,
    DeviceVolume,
    DeviceVolumeResult,
    Error,
    FrameType,
    Hello,
    LedState,
    Metrics,
    PauseMusic,
    PlayFinished,
    QueryMetrics,
    Ready,
    ReplyFinished,
    ResumeMusic,
    ShellCommand,
    ShellResult,
    Shutdown,
    SpeakFinished,
    SpeakStart,
    VendorSignalMsg,
    Wake,
    decode_pcm,
    encode_json,
    encode_pcm,
    read_frame,
)


class AudioPlaneError(RuntimeError):
    pass


log = logging.getLogger(__name__)

# The crash watchdog polls the child cheaply and backs off a crash loop: 1 s
# doubling to 60 s, reset as soon as a launch greets us. The cap matters on a
# NAS where a restart loop would otherwise burn CPU forever.
WATCH_INTERVAL_S = 1.0
RESTART_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0)


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
        self.wakes = 0
        self.last_wake: Wake | None = None
        self.vendor_signals = 0
        # The control plane decides what a vendor wake means; the client only delivers it.
        self.on_vendor: Callable[[str, str], object] | None = None
        self.on_wake: Callable[[Wake], object] | None = None
        # One non-looping file reached its natural end (the music queue advances
        # here); async because advancing resolves the next track.
        self.on_playback_finished: Callable[[str], Awaitable[None]] | None = None
        # Debug aid: keep the exact mono stream the plane produced (what AEC/KWS saw).
        self.record_voice = False
        self.voice_audio = bytearray()
        self.utterances = 0
        self.last_utterance: bytes = b""
        # Set by the control plane: called (in a task) for every finished utterance.
        self.on_utterance: Callable[[bytes], Awaitable[None]] | None = None
        self._shell_waiters: dict[str, asyncio.Future[str]] = {}
        self._volume_waiters: list[asyncio.Future[DeviceVolumeResult]] = []
        self.last_vendor: VendorSignalMsg | None = None
        self._turns: set[asyncio.Task] = set()
        self._metrics_event = asyncio.Event()
        self._read_task: asyncio.Task | None = None

    async def send(self, msg: ControlMessage) -> None:
        self.writer.write(encode_json(msg))
        await self.writer.drain()

    async def read_frame(self) -> tuple[FrameType, bytes]:
        return await read_frame(self.reader)

    async def pump(self) -> None:
        """Consume everything the plane sends; latest metrics wins.

        The loop is guarded because it is the only reader: if one message raises, the task
        dies and every symptom afterwards looks like the plane went quiet - no wake
        counter, no utterance, no reply - while the plane keeps running happily. That is
        exactly what "only the first turn lands" looked like (2026-10-22).
        """
        while True:
            try:
                kind, payload = await self.read_frame()
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:  # noqa: BLE001 - the socket is gone; say so
                log.warning("audio plane reader stopped: %s: %s", type(exc).__name__, exc)
                return
            try:
                self._dispatch(kind, payload)
            except Exception:  # noqa: BLE001 - one bad message must not end the session
                log.exception("audio plane message dropped (kind=%s)", kind)
                continue

    def _dispatch(self, kind: FrameType, payload: bytes) -> None:
        if kind is FrameType.UTTERANCE_PCM:
            _seq, _ts, pcm = decode_pcm(payload)
            self.utterances += 1
            self.last_utterance = pcm
            if self.on_utterance is not None:
                # Keep a reference: the loop only holds these weakly, and a turn that gets
                # collected mid-flight shows up as "utterance counted, no reply".
                task = asyncio.create_task(self.on_utterance(pcm), name="turn")
                self._turns.add(task)
                task.add_done_callback(self._turns.discard)
            return
        if kind is FrameType.VOICE_PCM:
            self.voice_frames += 1
            self.voice_bytes += max(0, len(payload) - 12)
            self.last_voice_ms = now_ms()
            if self.record_voice:
                self.voice_audio.extend(payload[12:])
            return
        if kind is not FrameType.CONTROL:
            return
        msg = CONTROL.validate_json(payload)
        if isinstance(msg, Metrics):
            self.metrics = msg
            self._metrics_event.set()
        elif isinstance(msg, Wake):
            self.wakes += 1
            self.last_wake = msg
            if self.on_wake is not None:
                self.on_wake(msg)
        elif isinstance(msg, VendorSignalMsg):
            self.vendor_signals += 1
            self.last_vendor = msg
            if self.on_vendor is not None:
                self.on_vendor(msg.kind, msg.text)
        elif isinstance(msg, PlayFinished):
            if self.on_playback_finished is not None:
                # A coroutine, unlike the sync wake/vendor hooks: advancing the queue
                # resolves the next track (network). Measured 22:19: calling it
                # directly left the coroutine unawaited and the queue stuck.
                task = asyncio.create_task(self.on_playback_finished(msg.path),
                                           name="music-advance")
                self._turns.add(task)
                task.add_done_callback(self._turns.discard)
        elif isinstance(msg, DeviceVolumeResult):
            for waiter in self._volume_waiters:
                if not waiter.done():
                    waiter.set_result(msg)
                    break
        elif isinstance(msg, ShellResult):
            waiter = self._shell_waiters.pop(msg.request_id, None)
            if waiter is not None and not waiter.done():
                if msg.error:
                    waiter.set_exception(RuntimeError(msg.error))
                else:
                    waiter.set_result(msg.stdout)
        elif isinstance(msg, Ready):
            self.ready = msg

    def start_pump(self) -> None:
        self._read_task = asyncio.create_task(self.pump(), name="audio-plane-read")

    async def speak(
        self, pcm: bytes, chunk_ms: int = 100, rate: int = 16000, *,
        segment_start: bool = False,
    ) -> None:
        """Push one piece of a reply to the speaker as PLAY_PCM frames.

        `segment_start` declares the first piece of a sentence (or the cached filler):
        the plane fades it in and counts the reply as open from here. The remaining
        pieces of the same sentence arrive unmarked on purpose - the vendor's chunks
        are framing of one continuous synthesis, so joining them raw is seamless, and
        a ramp on every piece was the pop heard between chunks.
        """
        if segment_start:
            await self.send(SpeakStart())
        step = max(2, rate * chunk_ms // 1000 * 2)
        for start in range(0, len(pcm), step):
            self.writer.write(encode_pcm(FrameType.PLAY_PCM, start // step, 0.0, pcm[start:start + step]))
        await self.writer.drain()
        await self.send(SpeakFinished())

    async def set_led(self, effect: str, repaint: bool = False) -> None:
        """Ask the plane to paint the ring. The plane applies it in a task, never inline.

        The method only sends; it does not wait for the animation, because the animation
        runs on the device for up to a couple of seconds and the caller is usually on the
        turn path.
        """
        await self.send(LedState(effect=effect, repaint=repaint))

    async def reply_finished(self, hold_music_s: float | None = None) -> None:
        """Tell the plane the turn is over (its cue for the follow-up gate)."""
        await self.send(ReplyFinished(hold_music_s=hold_music_s))

    async def pause_music(self) -> None:
        """「暂停」: park the track where it is; only resume_music lifts it."""
        await self.send(PauseMusic())

    async def resume_music(self) -> None:
        """「继续」: play on from where the track parked."""
        await self.send(ResumeMusic())

    async def device_volume(self, timeout_s: float = 3.0) -> int | None:
        """Read the speaker's mixer value through the plane (None if it cannot)."""
        future: asyncio.Future[DeviceVolumeResult] = asyncio.get_running_loop().create_future()
        self._volume_waiters.append(future)
        try:
            await self.send(DeviceVolume(action="read"))
            result = await asyncio.wait_for(future, timeout_s)
        finally:
            self._volume_waiters.remove(future)
        if result.error:
            raise RuntimeError(result.error)
        return result.volume

    async def set_device_volume(self, value: int, timeout_s: float = 3.0) -> int | None:
        future: asyncio.Future[DeviceVolumeResult] = asyncio.get_running_loop().create_future()
        self._volume_waiters.append(future)
        try:
            await self.send(DeviceVolume(action="set", value=value))
            result = await asyncio.wait_for(future, timeout_s)
        finally:
            self._volume_waiters.remove(future)
        if result.error:
            raise RuntimeError(result.error)
        return result.volume

    async def shell(self, script: str, timeout_s: float = 3.0) -> str:
        """Run a shell command on the speaker through the plane (device RPC)."""
        request_id = str(uuid.uuid4())
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._shell_waiters[request_id] = waiter
        try:
            await self.send(ShellCommand(request_id=request_id, script=script))
            return await asyncio.wait_for(waiter, timeout_s)
        finally:
            self._shell_waiters.pop(request_id, None)

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


class PlaneHandle:
    """A stable facade over whichever plane connection is live right now.

    The supervisor restarts a crashed child and connects a brand-new
    AudioPlaneClient. The control plane wires the turn callbacks (on_utterance,
    on_wake, ...) once at startup, so they are attached here and re-applied on
    every bind; reads delegate to the current connection. Without this, a
    restarted plane would be healthy but deaf: no callbacks on the new client,
    every utterance silently dropped.
    """

    _CALLBACKS = ("on_utterance", "on_wake", "on_vendor", "on_playback_finished")

    def __init__(self) -> None:
        self._callbacks: dict[str, object] = {}
        self._client: AudioPlaneClient | None = None

    @property
    def client(self) -> AudioPlaneClient | None:
        return self._client

    @property
    def ready(self) -> Ready | None:
        return self._client.ready if self._client is not None else None

    @property
    def metrics(self) -> Metrics | None:
        return self._client.metrics if self._client is not None else None

    def bind(self, client: AudioPlaneClient | None) -> None:
        self._client = client
        if client is not None:
            for name, fn in self._callbacks.items():
                setattr(client, name, fn)

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._CALLBACKS:
            self._callbacks[name] = value
            if self._client is not None:
                setattr(self._client, name, value)
            return
        object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> object:
        client = self._client
        if client is None:
            raise AttributeError(f"no audio plane connected ({name})")
        return getattr(client, name)


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
        self.handle = PlaneHandle()
        self._stderr_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._stopping = False
        self.restarts = 0
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
        # A restart after a crash may still hold the dead child's stderr task and
        # half-open socket; clear them before spawning the replacement.
        self._stopping = False
        await self._kill_child()

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
        self._stderr_task = asyncio.create_task(self._pump_stderr(), name="plane-stderr")
        try:
            await self._greet(device, timeout_s)
        except Exception as exc:
            # Whatever went wrong, the console must be able to say why: keep the reason
            # and take the half-started child down instead of leaving it running blind.
            self.error = str(exc)
            await self._kill_child()
            raise
        self.handle.bind(self.client)
        return self

    def watch(self) -> None:
        """Arm the crash watchdog (idempotent).

        Docker does not restart a container because a healthcheck turned
        unhealthy, so nothing outside this process notices that the only thing
        touching the microphone died: /health keeps answering, the console keeps
        rendering, and the room goes deaf. This loop is what makes "the plane
        crashed" a few seconds of silence instead of a manual restart on the NAS.

        With no device to drive there is nothing to supervise: restarting would
        only spin out "no device this build can drive" on every backoff step (seen
        on a fresh install before the wizard adds the speaker). The device-write
        path arms the watchdog again.
        """
        if self._watch_task is not None and not self._watch_task.done():
            return
        if self._pick_device() is None:
            log.info("plane watchdog idle: no drivable device in the config")
            return
        self._stopping = False
        self._watch_task = asyncio.create_task(self._watch(), name="plane-watchdog")

    async def _watch(self) -> None:
        failures = 0
        while not self._stopping:
            await asyncio.sleep(WATCH_INTERVAL_S)
            if self._stopping:
                return
            if self.running:
                failures = 0
                continue
            delay = RESTART_BACKOFF_S[min(failures, len(RESTART_BACKOFF_S) - 1)]
            log.warning("audio plane is down (%s); restarting in %.0f s",
                        self.error or "child exited", delay)
            await asyncio.sleep(delay)
            if self._stopping:
                return
            failures += 1
            try:
                await self.start()
            except Exception as exc:  # noqa: BLE001 - keep watching, never die here
                self.error = f"{type(exc).__name__}: {exc}"
                log.warning("audio plane restart failed: %s", self.error)
                continue
            self.restarts += 1
            log.info("audio plane restarted (#%d, pid=%s)", self.restarts,
                     self.proc.pid if self.proc is not None else "?")

    async def _kill_child(self) -> None:
        """Take the child and its connection down, leaving the watchdog alone."""
        if self.client is not None:
            await self.client.close()
            self.client = None
            self.handle.bind(None)
        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            if self.proc.returncode is None:
                self.proc.kill()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task
            self._stderr_task = None
        self.proc = None


    async def _pump_stderr(self) -> None:
        """Read the plane's stderr for as long as it runs.

        Not drained, a 64 KB pipe fills and the child blocks inside a log call - a frozen
        capture loop with no crash and no message. And the plane's own lines (gate disarmed,
        utterance discarded, playback finished) are the only way to explain a turn that
        never landed: this is how "rounds 2-5 produced no turn" gets an answer instead of a
        theory.
        """
        stderr = getattr(self.proc, "stderr", None)
        if stderr is None:
            return
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    return
                log.info("plane: %s", line.decode(errors="replace").rstrip())
        except (asyncio.CancelledError, OSError, ValueError):
            return

    async def _greet(self, device: str, timeout_s: float) -> None:
        assert self.proc is not None
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self.proc.returncode is not None:
                # The stderr pump already printed whatever the child said; reading the pipe
                # here would race with it.
                raise AudioPlaneError(
                    f"audio plane exited with {self.proc.returncode}"
                )
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
                        detail = msg.message if isinstance(msg, Error) else msg.model_dump()
                        raise AudioPlaneError(f"audio plane refused: {detail}")
                    self.client.ready = msg
                    self.client.start_pump()
                    self.error = ""
                    return
        raise AudioPlaneError(f"audio plane did not accept a connection in {timeout_s:.0f}s")

    async def stop(self) -> None:
        self._stopping = True
        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watch_task
            self._watch_task = None
        await self._kill_child()

    @property
    def running(self) -> bool:
        return (
            self.client is not None
            and self.client.ready is not None
            and self.proc is not None
            and self.proc.returncode is None
        )

    def status(self) -> dict:
        """Everything the console needs, and nothing it has to interpret."""
        ready = self.client.ready if self.client is not None else None
        running = self.running
        out: dict = {
            "running": running,
            "pid": self.proc.pid if self.proc is not None else None,
            "restarts": self.restarts,
            "error": self.error or ("" if running else "audio plane is not running"),
        }
        if ready is not None:
            out["device_id"] = ready.device_id
            out["capture"] = {
                "rate": ready.capture_rate,
                "channels": ready.capture_channels,
                "mic_channels": ready.mic_channels,
                "reference_channels": ready.reference_channels,
            }
        if self.client is not None and self.client.metrics is not None:
            out["metrics"] = self.client.metrics.model_dump()
        if self.client is not None:
            out["voice_frames_received"] = self.client.voice_frames
            out["voice_bytes_received"] = self.client.voice_bytes
            out["wake_count"] = self.client.wakes
            out["utterances"] = self.client.utterances
            if self.client.last_wake is not None:
                out["last_wake"] = self.client.last_wake.model_dump()
        return out
