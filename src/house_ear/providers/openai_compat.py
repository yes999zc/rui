"""OpenAI-compatible clients for the three stages.

SiliconFlow is the first real vendor: `/audio/transcriptions` for ASR,
`/audio/speech` for TTS (with `response_format: pcm`, so no decoder is needed on this
side) and `/chat/completions` for the LLM. Nothing here is vendor-specific beyond the
JSON shapes OpenAI standardised, so another compatible endpoint is a config change.
"""
from __future__ import annotations

import io
import json
import os
import wave
from collections.abc import AsyncIterator

import httpx

from ..config.schema import ProviderConfig
from .base import ChatChunk, Transcript


class MissingKey(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


def _key(cfg: ProviderConfig) -> str:
    if not cfg.api_key_env:
        raise MissingKey("no api_key_env configured")
    value = os.environ.get(cfg.api_key_env, "")
    if not value:
        raise MissingKey(f"environment variable {cfg.api_key_env} is empty")
    return value


def _url(cfg: ProviderConfig, path: str) -> str:
    if not cfg.base_url:
        raise ProviderError(f"base_url is empty for model {cfg.model or '(unset)'}")
    return cfg.base_url.rstrip("/") + path


def _headers(cfg: ProviderConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {_key(cfg)}"}


def pcm_to_wav(pcm: bytes, rate: int, channels: int = 1) -> bytes:
    """Wrap raw int16 PCM in a WAV container - every ASR endpoint expects a file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _HttpHolder:
    """One keep-alive client per provider instance.

    Three stages per turn through a cold connection each time costs 200-400 ms of TLS per
    stage before the vendor does any work; that is latency the user hears.
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.cfg.timeout_s,
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def chat_payload(cfg: ProviderConfig, messages: list[dict], tools: list[dict]) -> dict:
    """The request body, split out so the knobs that decide latency are testable."""
    payload: dict = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
    }
    if cfg.thinking == "disabled":
        payload["thinking"] = {"type": "disabled"}
    if tools:
        payload["tools"] = [{"type": "function", "function": t} for t in tools]
    return payload


class OpenAiCompatLlm(_HttpHolder):

    async def chat(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[ChatChunk]:
        payload = chat_payload(self.cfg, messages, tools)
        async with self.client.stream(
            "POST", _url(self.cfg, "/chat/completions"), json=payload,
            headers=_headers(self.cfg),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk == "[DONE]":
                    yield ChatChunk(delta="", done=True)
                    return
                data = json.loads(chunk)
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content") or ""
                if text:
                    yield ChatChunk(delta=text)


class OpenAiCompatAsr(_HttpHolder):

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> Transcript:
        files = {"file": ("utterance.wav", pcm_to_wav(pcm, sample_rate), "audio/wav")}
        data = {"model": self.cfg.model}
        resp = await self.client.post(
            _url(self.cfg, "/audio/transcriptions"),
            data=data,
            files=files,
            headers=_headers(self.cfg),
        )
        resp.raise_for_status()
        body = resp.json()
        return Transcript(
            text=(body.get("text") or "").strip(),
            language=body.get("language", ""),
        )


class OpenAiCompatTts(_HttpHolder):
    """Streams PCM chunks: the first chunk is what decides perceived latency."""

    def __init__(self, cfg: ProviderConfig, sample_rate: int = 16000) -> None:
        super().__init__(cfg)
        self.sample_rate = sample_rate

    async def synthesize(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        payload = {
            "model": self.cfg.model,
            "input": text,
            "voice": voice or self.cfg.voice,
            "response_format": "pcm",
            "sample_rate": self.sample_rate,
            "stream": True,
        }
        async with self.client.stream(
            "POST", _url(self.cfg, "/audio/speech"), json=payload, headers=_headers(self.cfg)
        ) as resp:
            resp.raise_for_status()
            # aiter_raw: no utf-8 decoding or chunk coalescing on a binary PCM stream
            async for block in resp.aiter_raw():
                if block:
                    yield block
