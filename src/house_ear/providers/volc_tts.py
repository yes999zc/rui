"""ByteDance Volcano Engine (豆包语音合成 2.0) TTS over its HTTP streaming endpoint.

Why this provider exists: 9哥 bought the 2.0 voices and picked three (小荷 2.0 / Vivi 2.0 /
邻家男孩 2.0). The endpoint streams JSON lines, each carrying base64 PCM, so the first
audio bytes arrive in ~0.5 s (measured 2026-10-23: 22 chars -> ttfb 0.58 s, 2.38 s audio)
and no decoder is needed: `format=pcm` at the pipeline's own sample rate.

Auth is the new-console API key (`X-Api-Key`), resolved through `secrets.get` so a key
entered in the console works. The resource id picks the model version and the billing
item: `seed-tts-2.0` for the 2.0 voices.
"""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator

from .. import secrets
from ..config.schema import ProviderConfig
from .openai_compat import MissingKey, ProviderError

log = logging.getLogger(__name__)

TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_RESOURCE = "seed-tts-2.0"
OK = 20000000


class VolcTts:
    def __init__(self, cfg: ProviderConfig, sample_rate: int = 16000) -> None:
        self.cfg = cfg
        self.sample_rate = sample_rate

    def _api_key(self) -> str:
        name = self.cfg.api_key_env
        key = secrets.get(name) if name else ""
        if not key:
            raise MissingKey(f"no value for {name or '(unset)'} (environment or secrets.yaml)")
        return key

    async def synthesize(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        import httpx

        speaker = voice or self.cfg.voice
        if not speaker:
            raise ProviderError("volc tts needs a speaker id in config.voice (or per request)")
        payload = {
            "user": {"uid": "house-ear"},
            "req_params": {
                "text": text,
                "speaker": speaker,
                "audio_params": {"format": "pcm", "sample_rate": self.sample_rate},
            },
        }
        headers = {
            "X-Api-Key": self._api_key(),
            "X-Api-Resource-Id": self.cfg.model or DEFAULT_RESOURCE,
            "Content-Type": "application/json",
        }
        url = self.cfg.base_url or TTS_URL
        async with httpx.AsyncClient(timeout=self.cfg.timeout_s) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:300]
                    raise ProviderError(f"volc tts HTTP {resp.status_code}: {body}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        continue
                    code = frame.get("code", 0)
                    if code not in (0, OK):
                        raise ProviderError(
                            f"volc tts code={code} message={frame.get('message')}")
                    data = frame.get("data")
                    if data:
                        chunk = base64.b64decode(data)
                        if chunk:
                            yield chunk

    async def warm(self) -> None:
        """One throwaway synthesis so the first real sentence does not pay the TLS handshake."""
        try:
            async for _chunk in self.synthesize("嗯", ""):
                break
        except Exception as exc:  # noqa: BLE001 - a warm-up must never break startup
            log.info("volc tts warm-up skipped: %s", exc)
