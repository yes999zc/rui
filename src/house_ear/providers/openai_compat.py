"""Minimal OpenAI-compatible client. M1 wires it to a real vendor."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx

from ..config.schema import ProviderConfig
from .base import ChatChunk, Transcript


class MissingKey(RuntimeError):
    pass


def _key(cfg: ProviderConfig) -> str:
    if not cfg.api_key_env:
        raise MissingKey("no api_key_env configured")
    value = os.environ.get(cfg.api_key_env, "")
    if not value:
        raise MissingKey(f"environment variable {cfg.api_key_env} is empty")
    return value


class OpenAiCompatLlm:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg

    async def chat(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[ChatChunk]:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        payload: dict = {"model": self.cfg.model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {"Authorization": f"Bearer {_key(self.cfg)}"}
        async with httpx.AsyncClient(timeout=self.cfg.timeout_s) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        yield ChatChunk(delta="", done=True)
                        return
                    data = json.loads(chunk)
                    delta = data["choices"][0]["delta"].get("content") or ""
                    if delta:
                        yield ChatChunk(delta=delta)


class OpenAiCompatAsr:
    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg

    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        url = self.cfg.base_url.rstrip("/") + "/audio/transcriptions"
        files = {"file": ("audio.wav", pcm, "audio/wav")}
        data = {"model": self.cfg.model}
        headers = {"Authorization": f"Bearer {_key(self.cfg)}"}
        async with httpx.AsyncClient(timeout=self.cfg.timeout_s) as client:
            resp = await client.post(url, data=data, files=files, headers=headers)
            resp.raise_for_status()
            return Transcript(text=resp.json().get("text", ""))
