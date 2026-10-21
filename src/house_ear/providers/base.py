"""Provider interfaces. Every stage is swappable; no stage knows about another."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Transcript:
    text: str
    language: str = ""
    duration_ms: int = 0


@dataclass
class ChatChunk:
    delta: str
    done: bool = False


class AsrProvider(Protocol):
    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript: ...


class TtsProvider(Protocol):
    async def synthesize(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        """Yields PCM or encoded chunks; first chunk must arrive fast."""
        ...


class LlmProvider(Protocol):
    async def chat(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[ChatChunk]: ...
