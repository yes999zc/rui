"""The channel boundary: adapters own transport, the gateway owns policy.

A channel is deliberately thin. Telegram/WeChat adapters know how to receive a text or
voice message and how to send one back; every conversation decision belongs to the
control plane's existing turn runner. That keeps a new platform from forking the LLM,
tools, session or TTS code.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass
class Inbound:
    """One user message normalised by an adapter."""

    channel: str
    peer_id: str
    message_id: str = ""
    text: str = ""
    audio_pcm: bytes | None = None
    sample_rate: int = 16000
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Reply:
    text: str = ""
    audio_pcm: bytes = b""
    sample_rate: int = 16000
    error: str = ""


class ChannelAdapter(Protocol):
    """Transport-specific receive/send surface."""

    name: str

    async def start(self, on_inbound: Callable[[Inbound], Awaitable[None]]) -> None: ...
    async def stop(self) -> None: ...
    async def send_text(self, peer_id: str, text: str) -> None: ...
    async def send_audio(self, peer_id: str, pcm: bytes, sample_rate: int) -> None: ...
    async def send_typing(self, peer_id: str) -> None: ...


class ControlClient:
    """HTTP client for the local house-ear control plane.

    The gateway never imports the turn runner: it talks to the same authenticated API
    the console uses, so a channel cannot bypass auth or create a second tool loop.
    """

    def __init__(self, base_url: str, token: str, *, timeout_s: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            headers={"X-Auth-Token": token} if token else {},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, text: str, *, channel: str, peer_id: str,
                   session_id: str = "", want_audio: bool = False,
                   reset: bool = False) -> Reply:
        body = {
            "channel": channel,
            "peer_id": peer_id,
            "text": text,
            "session_id": session_id,
            "want_audio": want_audio,
            "reset": reset,
        }
        response = await self._client.post(f"{self.base_url}/api/v1/chat", json=body)
        response.raise_for_status()
        data = response.json()
        audio = b""
        if data.get("audio_b64"):
            audio = base64.b64decode(data["audio_b64"])
        return Reply(text=str(data.get("reply") or ""), audio_pcm=audio,
                     sample_rate=int(data.get("sample_rate") or 16000),
                     error=str(data.get("error") or ""))

    async def asr(self, pcm: bytes, sample_rate: int = 16000) -> str:
        response = await self._client.post(
            f"{self.base_url}/api/v1/asr",
            json={
                "audio_b64": base64.b64encode(pcm).decode("ascii"),
                "sample_rate": sample_rate,
            },
        )
        response.raise_for_status()
        return str(response.json().get("text") or "")


class Dedupe:
    """Small TTL set; adapters retry but a user must not get two answers."""

    def __init__(self, ttl_s: float = 300.0, max_items: int = 2000) -> None:
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._seen: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str) -> bool:
        if not key:
            return False
        now = time.monotonic()
        while self._seen:
            old_key, old_ts = next(iter(self._seen.items()))
            if now - old_ts <= self.ttl_s and len(self._seen) <= self.max_items:
                break
            self._seen.pop(old_key, None)
        if key in self._seen and now - self._seen[key] <= self.ttl_s:
            return True
        self._seen[key] = now
        self._seen.move_to_end(key)
        return False


class ChannelGateway:
    """Fan-in for adapters; one serial queue per peer keeps replies in order."""

    def __init__(
        self,
        control: ControlClient,
        *,
        allow: Callable[[str, str], bool] | None = None,
        reply_mode: str = "text",
        dedupe: Dedupe | None = None,
        on_error: Callable[[str, str, Exception], None] | None = None,
    ) -> None:
        self.control = control
        self.allow = allow or (lambda _channel, _peer: True)
        self.reply_mode = reply_mode
        self.dedupe = dedupe or Dedupe()
        self.on_error = on_error
        self._adapters: list[ChannelAdapter] = []
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._running = False

    def add(self, adapter: ChannelAdapter) -> None:
        self._adapters.append(adapter)

    async def start(self) -> None:
        self._running = True
        for adapter in self._adapters:
            await adapter.start(self.handle)

    async def stop(self) -> None:
        self._running = False
        for adapter in self._adapters:
            try:
                await adapter.stop()
            except Exception as exc:  # noqa: BLE001 - shutdown is best effort
                log.warning("channel %s stop: %s", adapter.name, exc)

    def _lock(self, channel: str, peer: str) -> asyncio.Lock:
        return self._locks.setdefault((channel, peer), asyncio.Lock())

    async def handle(self, message: Inbound) -> None:
        if not self.allow(message.channel, message.peer_id):
            log.info("channel %s: ignored unauthorized peer", message.channel)
            return
        key = f"{message.channel}:{message.peer_id}:{message.message_id}"
        if message.message_id and self.dedupe.seen(key):
            return
        async with self._lock(message.channel, message.peer_id):
            await self._handle_locked(message)

    async def _handle_locked(self, message: Inbound) -> None:
        adapter = next((a for a in self._adapters if a.name == message.channel), None)
        if adapter is None:
            return
        text = message.text.strip()
        if text in {"/new", "/reset"}:
            await self.control.chat("新会话", channel=message.channel,
                                    peer_id=message.peer_id, reset=True)
            await adapter.send_text(message.peer_id, "好，新会话。")
            return
        if text == "/whoami":
            await adapter.send_text(message.peer_id, f"{message.channel}:{message.peer_id}")
            return
        if text == "/status":
            await adapter.send_text(message.peer_id, "小瑞在线。")
            return
        if text == "/start":
            await adapter.send_text(message.peer_id, "在。直接说事情就行。")
            return
        if not text and message.audio_pcm:
            try:
                text = await self.control.asr(message.audio_pcm, message.sample_rate)
            except Exception as exc:  # noqa: BLE001
                log.warning("channel %s: ASR failed: %s", message.channel, exc)
                await adapter.send_text(message.peer_id, "这段语音没转出来，再发一次？")
                return
        if not text:
            return
        try:
            await adapter.send_typing(message.peer_id)
        except Exception:  # noqa: BLE001 - typing is cosmetic
            pass
        mode = getattr(adapter, "reply_mode", self.reply_mode)
        want_audio = mode in {"voice", "both"}
        try:
            reply = await self.control.chat(
                text, channel=message.channel, peer_id=message.peer_id,
                session_id=f"{message.channel}:{message.peer_id}",
                want_audio=want_audio,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("channel %s: chat failed: %s", message.channel, exc)
            if self.on_error is not None:
                self.on_error(message.channel, message.peer_id, exc)
            await adapter.send_text(message.peer_id, "我这边刚才没答上来，再试一次？")
            return
        if reply.audio_pcm and want_audio:
            try:
                await adapter.send_audio(message.peer_id, reply.audio_pcm, reply.sample_rate)
                if mode == "both" and reply.text:
                    await adapter.send_text(message.peer_id, reply.text)
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("channel %s: voice reply failed, falling back to text: %s",
                            message.channel, exc)
        await adapter.send_text(message.peer_id, reply.text or "（没有回复）")
