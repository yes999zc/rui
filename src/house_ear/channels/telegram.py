"""Telegram channel adapter — Bot API long polling, text and voice notes.

No third-party Telegram SDK: the five endpoints needed here are small enough that a
direct httpx client keeps the optional channel extra tiny and lets tests inject a fake
transport. The bot token and chat allowlist never leave this process.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .base import Inbound

log = logging.getLogger(__name__)
API = "https://api.telegram.org"
MAX_TEXT = 4096


async def _ffmpeg(*args: str, timeout: float = 30.0) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffmpeg timed out") from None
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode('utf-8', 'replace')[-300:]}")


async def ogg_to_pcm(path: Path) -> bytes:
    out = path.with_suffix(".pcm")
    await _ffmpeg("-i", str(path), "-f", "s16le", "-acodec", "pcm_s16le",
                  "-ac", "1", "-ar", "16000", str(out))
    try:
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


async def pcm_to_ogg(pcm: bytes, sample_rate: int = 16000) -> bytes:
    with tempfile.TemporaryDirectory(prefix="house-ear-tg-") as td:
        raw = Path(td) / "reply.pcm"
        out = Path(td) / "reply.ogg"
        raw.write_bytes(pcm)
        await _ffmpeg("-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
                      "-i", str(raw), "-c:a", "libopus", "-b:a", "32k", str(out))
        return out.read_bytes()


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("。", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        end = cut if cut == limit else cut + 1
        chunks.append(text[:end].strip())
        text = text[end:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class TelegramAdapter:
    name = "telegram"

    def __init__(
        self,
        token: str,
        *,
        allowed_chat_ids: set[str] | None = None,
        state_path: Path | None = None,
        api_base: str = API,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token = token
        self.allowed = {str(x) for x in (allowed_chat_ids or set())}
        self.state_path = state_path
        self.api_base = api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))
        self._owns_client = client is None
        self._offset = self._load_offset()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._on_inbound: Callable[[Inbound], Awaitable[None]] | None = None
        self.last_error = ""

    def _safe(self, value: object) -> str:
        """Never let the bot token into a log line from an httpx exception URL."""
        text = str(value)
        if self.token:
            text = text.replace(self.token, "<telegram-token>")
        return text

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self.token}/{method}"

    def _load_offset(self) -> int:
        if self.state_path is None:
            return 0
        try:
            return int(json.loads(self.state_path.read_text()).get("offset") or 0)
        except Exception:  # noqa: BLE001 - a missing/corrupt offset starts clean
            return 0

    def _save_offset(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"offset": self._offset}))
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)

    async def _api(self, method: str, data: dict[str, Any] | None = None,
                   *, files: dict[str, Any] | None = None, timeout: float = 40.0) -> dict[str, Any]:
        try:
            response = await self._client.post(self._url(method), data=data, files=files,
                                               timeout=httpx.Timeout(timeout, connect=10.0))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(self._safe(exc)) from None
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method}: {body.get('description') or body}")
        return body

    async def start(self, on_inbound: Callable[[Inbound], Awaitable[None]]) -> None:
        self._on_inbound = on_inbound
        me = await self._api("getMe")
        username = (me.get("result") or {}).get("username")
        if username:
            log.info("telegram: connected as @%s", username)
        self._task = asyncio.create_task(self._poll(), name="telegram-poll")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._owns_client:
            await self._client.aclose()

    async def _poll(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                body = await self._api("getUpdates", {
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": json.dumps(["message"]),
                }, timeout=40.0)
                for update in body.get("result") or []:
                    self._offset = max(self._offset, int(update.get("update_id") or 0) + 1)
                    self._save_offset()
                    await self._handle_update(update)
                # A fake/local API can return an empty poll instantly. Yield so cancellation
                # and the rest of the gateway are never starved by a tight retry loop.
                await asyncio.sleep(0.05)
                backoff = 2.0
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network errors are normal
                self.last_error = self._safe(exc)[:300]
                log.warning("telegram poll: %s; retrying in %.0fs", self._safe(exc), backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not chat_id or not self._on_inbound:
            return
        if self.allowed and chat_id not in self.allowed:
            log.info("telegram: ignored chat outside allowlist")
            return
        text = str(msg.get("text") or msg.get("caption") or "").strip()
        message_id = str(msg.get("message_id") or update.get("update_id") or "")
        if msg.get("voice") or msg.get("audio"):
            item = msg.get("voice") or msg.get("audio") or {}
            file_id = str(item.get("file_id") or "")
            if not file_id:
                return
            try:
                pcm = await self._download_voice(file_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram: voice download failed: %s", self._safe(exc))
                await self.send_text(chat_id, "这条语音没下载下来，再发一次？")
                return
            await self._on_inbound(Inbound(channel=self.name, peer_id=chat_id,
                                           message_id=message_id, text=text,
                                           audio_pcm=pcm, raw=msg))
            return
        if text:
            await self._on_inbound(Inbound(channel=self.name, peer_id=chat_id,
                                           message_id=message_id, text=text, raw=msg))

    async def _download_voice(self, file_id: str) -> bytes:
        info = await self._api("getFile", {"file_id": file_id})
        path = (info.get("result") or {}).get("file_path")
        if not path:
            raise RuntimeError("telegram getFile returned no file_path")
        # The file path is bot-relative; the token is a secret and must not be logged.
        response = await self._client.get(
            f"{self.api_base}/file/bot{self.token}/{path}",
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        response.raise_for_status()
        with tempfile.TemporaryDirectory(prefix="house-ear-tg-") as td:
            source = Path(td) / "voice.ogg"
            source.write_bytes(response.content)
            return await ogg_to_pcm(source)

    async def send_text(self, peer_id: str, text: str) -> None:
        for chunk in split_text(text):
            await self._api("sendMessage", {"chat_id": peer_id, "text": chunk})

    async def send_typing(self, peer_id: str) -> None:
        await self._api("sendChatAction", {"chat_id": peer_id, "action": "typing"})

    async def send_audio(self, peer_id: str, pcm: bytes, sample_rate: int) -> None:
        ogg = await pcm_to_ogg(pcm, sample_rate)
        await self._api(
            "sendVoice",
            {"chat_id": peer_id},
            files={"voice": ("reply.ogg", ogg, "audio/ogg")},
            timeout=60.0,
        )
