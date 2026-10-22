"""LX06 adapter: tier 2, the device's own client dials us.

Tier definitions live in docs/adapter-contract.md. This is tier 2 because the vendor
client hands us the microphone array *and* the playback reference, which is the only
way barge-in works while music plays.

    device  --ws://<server>:<listen_port>/?token=***-->  us
            <-- start_recording(rate, channels, shifts) --
            -- record frames (int16 interleaved, 20 ms) --->
            <-- play frames (int16 mono at playback.rate) --

Transport is inbound: `open()` starts a websocket server instead of connecting out, and
`probe()` reports the layout from config plus whatever the last handshake said, because
there is nothing to interrogate until the speaker connects. The audio plane keeps
running while the speaker is away and simply yields nothing - one dead speaker must not
take the process down.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import numpy as np
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from ...config.schema import CaptureConfig, DeviceConfig, VoiceConfig
from ...trace import now_ms
from ..contract import AudioFrame, Capability, DeviceAdapter, DeviceInfo, Transport
from . import protocol

log = logging.getLogger(__name__)

DEFAULT_LISTEN_PORT = 4400
PLAY_CHUNK_MS = 100


class XiaoaiAdapter(DeviceAdapter):
    def __init__(self, dev: DeviceConfig, capture: CaptureConfig, voice: VoiceConfig) -> None:
        self.dev = dev
        self.capture = capture
        self.voice = voice
        self.listen_port = dev.listen_port or DEFAULT_LISTEN_PORT
        self.token = os.environ.get(dev.token_env, "")
        self._server: Server | None = None
        self._conn: ServerConnection | None = None
        self._frames: asyncio.Queue[AudioFrame] = asyncio.Queue(maxsize=64)
        self._seq = 0
        self._signals: list[protocol.VendorSignal] = []
        # Request/Response correlation. The client answers every Request with a Response
        # carrying the same id, and a shell call is how this build reads the device's own
        # mixer (mysoftvol) instead of guessing at loudness.
        self._pending: dict[str, asyncio.Future[dict]] = {}
        from .services import XiaoaiServices

        self.services = XiaoaiServices(self)  # type: ignore[assignment]
        self.device_volume: int | None = None
        self.frames_received = 0
        self.frames_dropped = 0
        self.bad_frames = 0
        self.rejected = 0
        self.connected_at = 0.0
        self.last_error = ""
        self._playing = False
        # Play-head reports: the client sends {"event":"consumed","n":<chunk>} for every
        # block it hands to aplay. The plane paces by this instead of a wall clock.
        self.reports_consumption = True
        self.consumed = 0
        self.sink_generation = 0
        self._consumed_event = asyncio.Event()
        self.info = DeviceInfo(
            id=dev.id,
            adapter="xiaoai",
            model="LX06",
            host=dev.host,
            room=dev.room,
            capabilities=frozenset(
                {Capability.MIC_ARRAY, Capability.REFERENCE, Capability.PLAYBACK}
            ),
            transport=Transport.INBOUND,
            channels=capture.channels,
            sample_rate=capture.rate,
            bit_depth=16,  # the client converts S32 -> S16 before it leaves the speaker
            mic_channels=capture.mic_channels,
            ref_channels=capture.ref_channels,
        )

    # ------------------------------------------------------------------ contract
    async def probe(self) -> DeviceInfo:
        return self.info

    async def open(self) -> None:
        if not self.token:
            # Fail closed: an empty token used to mean "anyone on the LAN can open the
            # microphone and the speaker". A missing secret must stop us, not open us.
            if not self.dev.allow_no_token:
                raise RuntimeError(
                    f"{self.dev.token_env} is empty: refusing to accept an unauthenticated "
                    "speaker connection"
                )
            log.warning(
                "xiaoai: listening WITHOUT a token on :%s (devices.%s.allow_no_token) - "
                "anyone on this network can open the microphone",
                self.listen_port,
                self.dev.id,
            )
        self._server = await serve(
            self._handle,
            "0.0.0.0",
            self.listen_port,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
        log.info("xiaoai adapter listening on :%s for %s", self.listen_port, self.dev.id)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def frames(self) -> AsyncIterator[AudioFrame]:
        """Blocks while no speaker is connected; never raises on a disconnect."""
        while True:
            try:
                frame = await asyncio.wait_for(self._frames.get(), timeout=1.0)
            except TimeoutError:
                continue
            yield frame

    async def play(self, pcm: bytes, *, sample_rate: int, channels: int) -> None:
        # Argument checks first: a caller passing a stereo buffer made a mistake and
        # should hear about it whether or not the speaker happens to be online.
        if channels != 1:
            raise ValueError("the LX06 client plays mono frames; mix down before play()")
        conn = self._conn
        if conn is None:
            raise RuntimeError("speaker is not connected")
        if not self._playing:
            # Wait for the acknowledgement, not just for the send. The client's start_play
            # kills the previous aplay and spawns a new one, and until that returns its
            # frame sender is None - `play()` then drops the block on the floor and still
            # answers Ok. Measured 2026-10-22: the answer's first word was swallowed on
            # every fresh player, which is exactly the 100-300 ms the device is restarting.
            log.info("player: start_play at %d Hz, %d ms buffer", sample_rate,
                     protocol.playback_payload(sample_rate)["buffer_size"] * 1000 // sample_rate)
            await self.call(
                "start_play", protocol.playback_payload(sample_rate), timeout=2.0
            )
            # The receipt only says the client spawned aplay - the ALSA device is not
            # open yet, and first writes in that window block ~100 ms (device log:
            # "[aplay] slow write 112 ms (3200 bytes)"), which is the swallowed first
            # word. The old build slept the same 80 ms here: "let aplay open before
            # the first chunk" (xiaoai-voice-entry server/session.py Sink.open).
            await asyncio.sleep(0.08)
            # A fresh player restarts the device's chunk numbering at 1; the plane's
            # credit resets on this generation change, so the two counts agree.
            self.consumed = 0
            self._consumed_event.clear()
            self.sink_generation += 1
            self._playing = True
        bytes_per_chunk = max(2, sample_rate * PLAY_CHUNK_MS // 1000 * 2)
        for start in range(0, len(pcm), bytes_per_chunk):
            await conn.send(protocol.stream("play", pcm[start:start + bytes_per_chunk]))

    def forget_player(self) -> None:
        """The device's player is gone (idle): the next play() must say start_play again.

        `_playing` used to stay True forever after the first stream, so every later sound
        skipped start_play. If the client had closed its player in the meantime, those
        frames sat in its socket instead of being played - heard as the cue arriving with
        the answer instead of at the wake.
        """
        self._playing = False

    async def wait_consumed(self, since: int, timeout: float) -> int:
        """Wait for a consumed report newer than `since`; return it, or `since` on timeout.

        Cleared before the second check so a report arriving between the check and the
        wait cannot be missed (the client reports once per chunk, ~10 times a second).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if self.consumed > since:
                return self.consumed
            remaining = deadline - loop.time()
            if remaining <= 0:
                return since
            self._consumed_event.clear()
            if self.consumed > since:
                return self.consumed
            try:
                await asyncio.wait_for(self._consumed_event.wait(), remaining)
            except TimeoutError:
                return since

    async def stop_playback(self, fade_ms: int = 120) -> None:
        """Tell the device to drop what it has buffered.

        The fade itself is applied server-side before the last chunk goes out; `stop_play`
        is what empties the speaker's own ALSA buffer, which is the part that actually
        decides the 300 ms budget.
        """
        conn = self._conn
        self._playing = False
        if conn is None:
            return
        await conn.send(protocol.request("stop_play"))

    async def call(self, command: str, payload: object = None, timeout: float = 3.0) -> dict:
        """Send one Request and wait for the Response with the same id."""
        conn = self._conn
        if conn is None:
            raise RuntimeError("speaker is not connected")
        request_id = str(uuid.uuid4())
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await conn.send(json.dumps({
                "Request": {"id": request_id, "command": command, "payload": payload},
            }, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def shell(self, script: str, timeout: float = 3.0) -> str:
        """Run a shell command on the speaker and return stdout.

        The device is the only place where loudness is decided: `mysoftvol` (numid=177)
        sits after the digital gains, and the vendor rewrites it whenever it touches its
        own player. Measured on the old deployment: 160 -> 255 -> 160 within minutes. So
        this reads the real value rather than assuming the config stuck.
        """
        response = await self.call("run_shell", script, timeout)
        return str((response.get("data") or {}).get("stdout") or "").strip()

    async def read_volume(self) -> int | None:
        """Kept for callers that already hold the adapter; the detail lives in services."""
        value = await self.services.read_volume()
        self.device_volume = value
        return value

    async def set_volume(self, volume: int) -> None:
        """Write the mixer. `devices[].volume = 0` means "the vendor owns it"."""
        await self.services.set_volume(volume)
        self.device_volume = volume

    async def set_led(self, effect: str, color: str = "") -> None:
        raise NotImplementedError("LED control lands with the device shell channel (S5)")

    # -------------------------------------------------------------------- inbound
    async def _handle(self, conn: ServerConnection) -> None:
        peer = conn.remote_address[0] if conn.remote_address else "?"
        if not self._token_ok(conn):
            self.rejected += 1
            log.warning("xiaoai: rejected %s (bad or missing token)", peer)
            await conn.close(code=1008, reason="bad token")
            return
        if self._conn is not None:
            log.warning("xiaoai: replacing existing connection from %s", peer)
            with_previous, self._conn = self._conn, conn
            await with_previous.close(code=1012, reason="replaced")
        self._conn = conn
        self.connected_at = time.time()
        log.info("xiaoai: speaker connected from %s", peer)
        payload = protocol.start_recording_payload(
            rate=self.capture.rate,
            channels=self.capture.channels,
            frame_ms=self.voice.frame_ms,
            mic_shift=self.capture.mic_shift,
            ref_shift=self.capture.ref_shift,
        )
        await conn.send(protocol.request("start_recording", payload))
        try:
            async for message in conn:
                if isinstance(message, str):
                    self._on_text(message)
                else:
                    self._on_audio(message)
        except ConnectionClosed as exc:
            # A speaker that goes away is normal (wifi, reboot, the user moved it).
            log.info("xiaoai: speaker disconnected (%s)", exc)
        except Exception as exc:  # noqa: BLE001 - a dead socket is not a crash
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("xiaoai: connection ended: %s", self.last_error)
        finally:
            if self._conn is conn:
                self._conn = None
                self._playing = False
            log.info("xiaoai: speaker disconnected after %s frames", self.frames_received)

    def drain_signals(self) -> list[protocol.VendorSignal]:
        signals, self._signals = self._signals, []
        return signals

    def _token_ok(self, conn: ServerConnection) -> bool:
        path = getattr(getattr(conn, "request", None), "path", "") or ""
        if not self.token:
            # Only reachable when `allow_no_token` opened the listener above.
            return bool(self.dev.allow_no_token)
        got = (parse_qs(urlparse(path).query).get("token") or [""])[0]
        return hmac.compare_digest(got, self.token)

    def _on_text(self, message: str) -> None:
        reply = protocol.parse_reply(message)
        if reply is None:
            return
        if "Event" in reply:
            event = reply["Event"]
            if event.get("event") == "consumed":
                # The client has handed chunk n to aplay. This is the only rate signal
                # that follows the speaker's own clock; the old build paced on it too.
                data = event.get("data") or event
                try:
                    n = int(data.get("n") or 0)
                except (TypeError, ValueError):
                    return
                if n > self.consumed:
                    self.consumed = n
                    self._consumed_event.set()
                return
            signal = protocol.parse_vendor_signal(event, ts=now_ms())
            if signal is not None:
                # Vendor signals are not audio: the plane forwards them to the session,
                # which is the only layer that may decide to stay quiet.
                self._signals.append(signal)
                del self._signals[:-16]
                if signal.kind == "wake":
                    log.info("xiaoai: vendor wake (%r), we yield", signal.text)
            elif event.get("event") not in {"consumed"}:
                log.debug("xiaoai: event %s", event.get("event") or event.get("id"))
        elif "Response" in reply:
            response = reply["Response"]
            if int(response.get("code") or 0) != 0:
                self.last_error = str(response.get("message") or "device error")
                log.warning("xiaoai: command failed: %s", self.last_error)
            future = self._pending.get(str(response.get("id") or ""))
            if future is not None and not future.done():
                future.set_result(response)

    def _on_audio(self, message: bytes | str) -> None:
        try:
            tag, pcm = protocol.decode_stream(message)
        except protocol.StreamError as exc:
            self.bad_frames += 1
            log.debug("xiaoai: bad stream frame: %s", exc)
            return
        if tag != "record":
            return
        expected = self.capture.channels * 2
        if not pcm or len(pcm) % expected:
            self.bad_frames += 1
            return
        self._seq += 1
        self.frames_received += 1
        frame = AudioFrame(
            device_id=self.info.id,
            seq=self._seq,
            ts_ms=now_ms(),
            pcm=pcm,
            channels=self.capture.channels,
            sample_rate=self.capture.rate,
            reference_channels=self.capture.ref_channels,
        )
        try:
            self._frames.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop the oldest: a stale frame is worse than a missing one.
            try:
                self._frames.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._frames.put_nowait(frame)
            self.frames_dropped += 1

    # ------------------------------------------------------------------- reporting
    def health(self) -> dict:
        return {
            "connected": self._conn is not None,
            "connected_at": self.connected_at,
            "listen_port": self.listen_port,
            "frames_received": self.frames_received,
            "frames_dropped": self.frames_dropped,
            "bad_frames": self.bad_frames,
            "rejected": self.rejected,
            "last_error": self.last_error,
        }


def pcm_to_array(pcm: bytes, channels: int) -> np.ndarray:
    """(frames, channels) view of a record payload - handy for tests and diagnostics."""
    return np.frombuffer(pcm, dtype="<i2").reshape(-1, channels)


__all__ = ["DEFAULT_LISTEN_PORT", "XiaoaiAdapter", "pcm_to_array"]
