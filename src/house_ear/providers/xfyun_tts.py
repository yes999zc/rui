"""Xunfei (讯飞) online TTS over its v2 websocket, streaming PCM as frames arrive.

Why this provider exists: SiliconFlow's `CosyVoice2` measured 3.7-21.6 s to first byte across
20 consecutive turns (see docs/latency.md), and a voice assistant cannot answer on that
budget. 讯飞 answers in a few hundred milliseconds and the voice 9哥 actually uses on the
speaker is `x4_lingxiaowan_boy`.

Protocol: HMAC-SHA256 signed URL, one JSON request frame, then JSON frames carrying base64
PCM until `status == 2`. `aue=raw` + `auf=audio/L16;rate=<rate>` gives headerless PCM, so no
decoder is needed here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import quote_plus, urlencode

from ..config.schema import ProviderConfig
from .openai_compat import MissingKey, ProviderError

log = logging.getLogger(__name__)

TTS_HOST = "tts-api.xfyun.cn"
TTS_PATH = "/v2/tts"
TTS_URL = f"wss://{TTS_HOST}{TTS_PATH}"


def build_auth_url(api_key: str, api_secret: str) -> str:
    date = format_datetime(datetime.now(UTC), usegmt=True)
    signature_origin = f"host: {TTS_HOST}\ndate: {date}\nGET {TTS_PATH} HTTP/1.1"
    digest = hmac.new(api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{base64.b64encode(digest).decode()}"'
    )
    params = {
        "host": TTS_HOST,
        "date": date,
        "authorization": base64.b64encode(authorization_origin.encode()).decode(),
    }
    return TTS_URL + "?" + urlencode(params, quote_via=quote_plus)


def request_payload(app_id: str, text: str, vcn: str, rate: int) -> dict:
    return {
        "common": {"app_id": app_id},
        "business": {
            "aue": "raw",
            "auf": f"audio/L16;rate={rate}",
            "vcn": vcn,
            "tte": "UTF8",
            "speed": 50,
            "volume": 50,
            "pitch": 50,
            "bgs": 0,
        },
        "data": {"status": 2, "text": base64.b64encode(text.encode()).decode()},
    }


def audio_from_frame(frame: dict) -> tuple[bytes, bool]:
    """(pcm, done) for one websocket frame; raises on a vendor error code."""
    if frame.get("code", 0) != 0:
        raise ProviderError(f"xfyun code={frame.get('code')} message={frame.get('message')}")
    data = frame.get("data") or {}
    audio = base64.b64decode(data.get("audio") or "")
    return audio, int(data.get("status", 1)) == 2


class XfyunTts:
    def __init__(self, cfg: ProviderConfig, sample_rate: int = 16000) -> None:
        self.cfg = cfg
        self.sample_rate = sample_rate

    def _credentials(self) -> tuple[str, str, str]:
        def get(name: str) -> str:
            value = os.environ.get(name, "") if name else ""
            if not value:
                raise MissingKey(f"environment variable {name or '(unset)'} is empty")
            return value

        return (
            get(self.cfg.app_id_env),
            get(self.cfg.api_key_env),
            get(self.cfg.api_secret_env),
        )

    async def synthesize(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        import websockets

        app_id, api_key, api_secret = self._credentials()
        vcn = voice or self.cfg.voice or self.cfg.model
        if not vcn:
            raise ProviderError("xfyun needs a voice name (vcn) in config.voice or config.model")
        url = build_auth_url(api_key, api_secret)
        payload = json.dumps(request_payload(app_id, text, vcn, self.sample_rate))

        # close_timeout: the server answers the close handshake ~1 s late, and waiting for it
        # would add that second to every sentence even though the audio is already complete.
        async with websockets.connect(url, close_timeout=0.1, max_size=None) as ws:
            await ws.send(payload)
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.cfg.timeout_s)
                audio, done = audio_from_frame(json.loads(raw))
                if audio:
                    yield audio
                if done:
                    break

    async def warm(self) -> None:
        """One throwaway synthesis so the first real sentence does not pay the handshake."""
        try:
            async for _chunk in self.synthesize("嗯", ""):
                break
        except Exception as exc:  # noqa: BLE001 - a warm-up must never break startup
            log.info("xfyun warm-up skipped: %s", exc)
