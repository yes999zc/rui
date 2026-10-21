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
import logging
import os
import time
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
        self.frames_received = 0
        self.frames_dropped = 0
        self.bad_frames = 0
        self.rejected = 0
        self.connected_at = 0.0
        self.last_error = ""
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
            raise RuntimeError(
                f"{self.dev.token_env} is empty: refusing to accept an unauthenticated "
                "speaker connection"
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
        bytes_per_chunk = max(2, sample_rate * PLAY_CHUNK_MS // 1000 * 2)
        for start in range(0, len(pcm), bytes_per_chunk):
            await conn.send(protocol.stream("play", pcm[start:start + bytes_per_chunk]))

    async def stop_playback(self, fade_ms: int = 120) -> None:
        """Tell the device to drop what it has buffered.

        The fade itself is applied server-side before the last chunk goes out; `stop_play`
        is what empties the speaker's own ALSA buffer, which is the part that actually
        decides the 300 ms budget.
        """
        conn = self._conn
        if conn is None:
            return
        await conn.send(protocol.request("stop_play"))

    async def set_volume(self, volume: int) -> None:
        raise NotImplementedError("volume goes through the device shell channel (S5)")

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
            log.info("xiaoai: speaker disconnected after %s frames", self.frames_received)

    def _token_ok(self, conn: ServerConnection) -> bool:
        path = getattr(getattr(conn, "request", None), "path", "") or ""
        got = (parse_qs(urlparse(path).query).get("token") or [""])[0]
        return bool(self.token) and hmac.compare_digest(got, self.token)

    def _on_text(self, message: str) -> None:
        reply = protocol.parse_reply(message)
        if reply is None:
            return
        if "Event" in reply:
            event = reply["Event"]
            log.info("xiaoai: event %s", event.get("event") or event.get("id"))
        elif "Response" in reply:
            response = reply["Response"]
            if int(response.get("code") or 0) != 0:
                self.last_error = str(response.get("message") or "device error")
                log.warning("xiaoai: command failed: %s", self.last_error)

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
