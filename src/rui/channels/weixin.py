"""WeChat iLink Bot / ClawBot adapter.

This is the channel Hermes and OpenClaw use: a QR-scanned personal WeChat bot over
Tencent's iLink HTTP API. Receive is ``getupdates`` long polling; every reply must echo
the peer's latest ``context_token``; media moves through an AES-128-ECB encrypted CDN.

Voice notes are intentionally re-transcribed from the original ``.silk`` file. Tencent's
``voice_item.text`` is not trusted (Hermes hit wrong-language transcripts), so the adapter
downloads the audio, decodes it with ``pilk`` and sends the PCM through the normal ASR
chain. Voice replies are sent as *file attachments*: native voice bubbles are not reliable.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .base import Inbound

log = logging.getLogger(__name__)

ILINK = "https://ilinkai.weixin.qq.com"
CDN = "https://novac2c.cdn.weixin.qq.com/c2c"
CHANNEL_VERSION = "1.0.0"
MAX_TEXT = 2000
ITEM_TEXT, ITEM_IMAGE, ITEM_VOICE, ITEM_FILE, ITEM_VIDEO = 1, 2, 3, 4, 5


class WeixinError(RuntimeError):
    pass


class SessionExpired(WeixinError):
    pass


def _headers(token: str = "") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(
            str(random.getrandbits(32)).encode("ascii")
        ).decode("ascii"),
        "SKRouteTag": "1001",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION}


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    text = text.strip()
    out: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("。", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        end = cut if cut == limit else cut + 1
        out.append(text[:end].strip())
        text = text[end:].lstrip()
    if text:
        out.append(text)
    return out


def parse_aes_key(aes_key_b64: str) -> bytes:
    """iLink sends either base64(16 raw bytes) or base64(32 ASCII hex chars)."""
    raw = base64.b64decode(aes_key_b64)
    if len(raw) == 16:
        return raw
    if len(raw) == 32:
        try:
            return bytes.fromhex(raw.decode("ascii"))
        except ValueError as exc:
            raise WeixinError("bad WeChat AES key") from exc
    raise WeixinError(f"unexpected WeChat AES key length {len(raw)}")


def _encrypt(data: bytes, key: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - optional extra
        raise WeixinError("cryptography is required for WeChat channels") from exc
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _decrypt(data: bytes, key: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover
        raise WeixinError("cryptography is required for WeChat channels") from exc
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = decryptor.update(data) + decryptor.finalize()
    if not plain:
        return plain
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        plain = plain[:-pad]
    return plain


class AccountStore:
    """Credentials, getupdates cursor and per-peer context tokens, all 0600."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.data_dir, 0o700)
        self.account_path = self.data_dir / "account.json"
        self.sync_path = self.data_dir / "sync.json"
        self.context_path = self.data_dir / "context-tokens.json"

    def _read(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001 - unreadable state starts clean
            return default

    def _write(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def load_account(self) -> dict[str, Any] | None:
        data = self._read(self.account_path, None)
        return data if isinstance(data, dict) and data.get("token") else None

    def save_account(self, data: dict[str, Any]) -> None:
        self._write(self.account_path, data)

    def load_sync(self) -> str:
        data = self._read(self.sync_path, {})
        return str(data.get("get_updates_buf") or "")

    def save_sync(self, buf: str) -> None:
        self._write(self.sync_path, {"get_updates_buf": buf})

    def load_context(self) -> dict[str, str]:
        data = self._read(self.context_path, {})
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def save_context(self, tokens: dict[str, str]) -> None:
        self._write(self.context_path, tokens)


class WeixinAdapter:
    name = "weixin"

    def __init__(
        self,
        data_dir: Path,
        *,
        allowed_user_ids: set[str] | None = None,
        client: httpx.AsyncClient | None = None,
        base_url: str = ILINK,
        cdn_base: str = CDN,
        allow_all: bool = False,
        reply_mode: str = "text",
    ) -> None:
        self.store = AccountStore(data_dir)
        self.reply_mode = reply_mode
        self.allowed = {str(x) for x in (allowed_user_ids or set())}
        self.allow_all = allow_all
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))
        self._owns_client = client is None
        self.base_url = base_url.rstrip("/")
        self.cdn_base = cdn_base.rstrip("/")
        self.account: dict[str, Any] = self.store.load_account() or {}
        self.sync_buf = self.store.load_sync()
        self.context_tokens = self.store.load_context()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._on_inbound: Callable[[Inbound], Awaitable[None]] | None = None
        self.last_error = ""

    # ---------------------------------------------------------------- login
    async def qr_login(self, *, on_qr: Callable[[str, str], None] | None = None) -> dict[str, Any]:
        """Run the QR flow. Returns credentials; caller must persist via ``save_login``."""
        resp = await self._client.get(f"{self.base_url}/ilink/bot/get_bot_qrcode",
                                      params={"bot_type": 3})
        resp.raise_for_status()
        body = resp.json()
        qrcode = str(body.get("qrcode") or "")
        image = str(body.get("qrcode_img_content") or "")
        if not qrcode:
            raise WeixinError(f"no qrcode in login response: {body}")
        if on_qr is not None:
            on_qr(qrcode, image)
        for _ in range(180):
            status = await self._client.get(
                f"{self.base_url}/ilink/bot/get_qrcode_status",
                params={"qrcode": qrcode},
                headers={"iLink-App-ClientVersion": "1"},
            )
            status.raise_for_status()
            state = status.json()
            value = str(state.get("status") or "")
            if value == "confirmed":
                account = {
                    "token": str(state.get("bot_token") or ""),
                    "baseUrl": str(state.get("baseurl") or self.base_url),
                    "accountId": str(state.get("ilink_bot_id") or ""),
                    "userId": str(state.get("ilink_user_id") or ""),
                    "savedAt": int(__import__("time").time()),
                }
                if not account["token"]:
                    raise WeixinError("login confirmed without a token")
                return account
            if value == "expired":
                raise WeixinError("WeChat QR expired; run login again")
            await asyncio.sleep(2)
        raise WeixinError("WeChat QR login timed out")

    def save_login(self, account: dict[str, Any]) -> None:
        self.account = account
        self.store.save_account(account)

    # ---------------------------------------------------------------- HTTP
    def _api_headers(self) -> dict[str, str]:
        token = str(self.account.get("token") or "")
        if not token:
            raise WeixinError("WeChat account is not logged in")
        return _headers(token)

    async def _post(self, path: str, payload: dict[str, Any],
                    *, timeout: float = 45.0) -> dict[str, Any]:
        body = dict(payload)
        body.setdefault("base_info", _base_info())
        response = await self._client.post(
            f"{str(self.account.get('baseUrl') or self.base_url).rstrip('/')}{path}",
            json=body, headers=self._api_headers(),
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        response.raise_for_status()
        data = response.json()
        ret = data.get("ret")
        if ret not in (0, None):
            if ret == -14:
                raise SessionExpired("iLink session expired; rescan the QR code")
            raise WeixinError(f"iLink {path}: ret={ret} errmsg={data.get('errmsg') or ''}")
        return data

    # ---------------------------------------------------------------- receive
    async def start(self, on_inbound: Callable[[Inbound], Awaitable[None]]) -> None:
        if not self.account.get("token"):
            log.warning("weixin: no account credentials; channel disabled")
            return
        self._on_inbound = on_inbound
        self._task = asyncio.create_task(self._poll(), name="weixin-poll")
        log.info("weixin: connected as %s", self.account.get("accountId", "?"))

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
                data = await self._post("/ilink/bot/getupdates", {
                    "get_updates_buf": self.sync_buf,
                }, timeout=45.0)
                for msg in data.get("msgs") or []:
                    await self._handle_message(msg)
                buf = data.get("get_updates_buf")
                if buf:
                    self.sync_buf = str(buf)
                    self.store.save_sync(self.sync_buf)
                # Same as Telegram: an instantly-returning poll must not starve the loop.
                await asyncio.sleep(0.05)
                backoff = 2.0
                self.last_error = ""
            except SessionExpired as exc:
                self.last_error = str(exc)
                log.warning("weixin: %s", exc)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)[:300]
                log.warning("weixin poll: %s; retrying in %.0fs", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        if not self._on_inbound or msg.get("message_type") == 2:
            return
        peer = str(msg.get("from_user_id") or "")
        if not peer:
            return
        if not self.allowed and not self.allow_all:
            log.info("weixin: unpaired user %s (add it to the allowlist)", peer)
            return
        if self.allowed and peer not in self.allowed:
            log.info("weixin: ignored user outside allowlist")
            return
        context = str(msg.get("context_token") or "")
        if context:
            self.context_tokens[peer] = context
            self.store.save_context(self.context_tokens)
        text = ""
        audio_pcm: bytes | None = None
        for item in msg.get("item_list") or []:
            kind = item.get("type")
            if kind == ITEM_TEXT:
                text += str((item.get("text_item") or {}).get("text") or "")
            elif kind == ITEM_VOICE:
                audio_pcm = await self._download_voice(item.get("voice_item") or {})
        message_id = str(msg.get("message_id") or msg.get("seq") or "")
        await self._on_inbound(Inbound(channel=self.name, peer_id=peer,
                                       message_id=message_id, text=text.strip(),
                                       audio_pcm=audio_pcm, raw=msg))

    async def _download_voice(self, voice: dict[str, Any]) -> bytes | None:
        media = voice.get("media") or {}
        param = str(media.get("encrypt_query_param") or "")
        key_b64 = str(media.get("aes_key") or "")
        if not param or not key_b64:
            log.warning("weixin: voice item has no downloadable media")
            return None
        response = await self._client.get(
            f"{self.cdn_base}/download", params={"encrypted_query_param": param},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        response.raise_for_status()
        try:
            plain = _decrypt(response.content, parse_aes_key(key_b64))
        except Exception as exc:  # noqa: BLE001
            log.warning("weixin: voice decrypt failed: %s", exc)
            return None
        with tempfile.TemporaryDirectory(prefix="rui-wx-") as td:
            ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
            silk = Path(td) / "voice.silk"
            wav = Path(td) / "voice.wav"
            pcm = Path(td) / "voice.pcm"
            silk.write_bytes(plain)
            try:
                import pilk  # optional channel extra
            except ImportError:
                log.warning("weixin: pilk is not installed; cannot decode .silk voice")
                return None
            try:
                pilk.silk_to_wav(str(silk), str(wav))
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                     "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
                     str(pcm)],
                    check=True, timeout=30,
                )
                return pcm.read_bytes()
            except Exception as exc:  # noqa: BLE001
                log.warning("weixin: silk conversion failed: %s", exc)
                return None

    # ---------------------------------------------------------------- send
    async def send_text(self, peer_id: str, text: str) -> None:
        context = self.context_tokens.get(peer_id)
        if not context:
            raise WeixinError("no context_token for peer; wait for the user's next message")
        for chunk in split_text(text):
            await self._post("/ilink/bot/sendmessage", {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": peer_id,
                    "client_id": f"rui:{uuid.uuid4()}",
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context,
                    "item_list": [{"type": ITEM_TEXT, "text_item": {"text": chunk}}],
                },
            })

    async def send_typing(self, peer_id: str) -> None:
        context = self.context_tokens.get(peer_id)
        if not context:
            return
        try:
            cfg = await self._post("/ilink/bot/getconfig", {
                "ilink_user_id": peer_id, "context_token": context,
            })
            ticket = str(cfg.get("typing_ticket") or "")
            if ticket:
                await self._post("/ilink/bot/sendtyping", {
                    "ilink_user_id": peer_id, "typing_ticket": ticket, "status": 1,
                })
        except Exception:  # noqa: BLE001 - typing is cosmetic
            return

    async def send_audio(self, peer_id: str, pcm: bytes, sample_rate: int) -> None:
        """Send TTS as a playable audio file, not as a native WeChat voice bubble."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="rui-wx-") as td:
            raw = Path(td) / "reply.pcm"
            mp3 = Path(td) / "reply.mp3"
            raw.write_bytes(pcm)
            subprocess.run(
                [shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg",
                 "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le",
                 "-ar", str(sample_rate), "-ac", "1", "-i", str(raw), "-codec:a", "libmp3lame",
                 "-b:a", "48k", str(mp3)],
                check=True, timeout=30,
            )
            await self._send_file(peer_id, mp3.read_bytes(), "reply.mp3", "语音回复（文件）")

    async def _send_file(self, peer_id: str, data: bytes, name: str, caption: str) -> None:
        context = self.context_tokens.get(peer_id)
        if not context:
            raise WeixinError("no context_token for peer")
        aes_key = os.urandom(16)
        filekey = uuid.uuid4().hex
        encrypted = _encrypt(data, aes_key)
        upload = await self._post("/ilink/bot/getuploadurl", {
            "filekey": filekey,
            "media_type": 3,  # FILE
            "to_user_id": peer_id,
            "rawsize": len(data),
            "rawfilemd5": hashlib.md5(data).hexdigest(),  # noqa: S324 - protocol field
            "filesize": len(encrypted),
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
        })
        param = str(upload.get("upload_param") or "")
        if not param:
            raise WeixinError("iLink getuploadurl returned no upload_param")
        response = await self._client.post(
            f"{self.cdn_base}/upload",
            params={"encrypted_query_param": param, "filekey": filekey},
            content=encrypted,
            headers={"Content-Type": "application/octet-stream"},
            timeout=httpx.Timeout(90.0, connect=10.0),
        )
        response.raise_for_status()
        encrypted_param = response.headers.get("x-encrypted-param")
        if not encrypted_param:
            raise WeixinError("iLink CDN upload returned no x-encrypted-param")
        # Official clients use base64(hex string) for the outbound aes_key.
        aes_key_b64 = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
        await self._post("/ilink/bot/sendmessage", {
            "msg": {
                "from_user_id": "",
                "to_user_id": peer_id,
                "client_id": f"rui:{uuid.uuid4()}",
                "message_type": 2,
                "message_state": 2,
                "context_token": context,
                "item_list": [{
                    "type": ITEM_FILE,
                    "file_item": {
                        "media": {
                            "encrypt_query_param": encrypted_param,
                            "aes_key": aes_key_b64,
                            "encrypt_type": 1,
                        },
                        "file_name": name,
                        "len": str(len(data)),
                    },
                }],
            },
        })
        await self.send_text(peer_id, f"已作为文件发送：{caption}")
