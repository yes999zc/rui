"""One conversational turn: transcript -> reply -> speech, with the latency marked.

Order matters for how it feels. Speech synthesis starts on the first finished sentence,
not on the finished answer: a short first sentence is the difference between "it is
thinking" and "it answered". The LLM keeps generating while the speaker is already
talking, and every stage boundary is stamped into `TurnTrace` so a slow turn can be
attributed instead of guessed at.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import ProvidersConfig
from ..providers.base import ChatChunk
from ..providers.chain import (
    StageFailure,
    StageReport,
    asr_chain,
    call_stage,
    llm_chain,
    tts_chain,
)
from ..trace import TurnTrace

log = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "你是一个家庭语音助手，你的回答会被直接念出来。"
    "只用中文口语回答，一到两句，总共不超过六十个字。"
    "绝对不要使用列表、编号、markdown、表情符号或括号补充。"
    "不知道就直说不知道，不要介绍去哪里查。"
)

SENTENCE_ENDINGS = "。！？!?；;\n"
# A comma is a chance to start speaking: by the time the first clause is synthesised the
# rest of the answer has usually arrived. Only worth it once a clause is long enough to
# not sound like a hiccup.
SOFT_ENDINGS = "，,、：:"
SOFT_MIN_CHARS = 10
# Any complete sentence is worth speaking at once: the first one decides perceived
# latency, and a longer wait for a "nicer" split is a worse trade. The cap only exists so
# a reply with no punctuation at all cannot hold the speaker silent.
MIN_SENTENCE_CHARS = 2
MAX_SENTENCE_CHARS = 45


@dataclass
class TurnResult:
    transcript: str = ""
    reply: str = ""
    audio_bytes: int = 0
    stages: dict[str, StageReport] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "reply": self.reply,
            "audio_bytes": self.audio_bytes,
            "error": self.error,
            "stages": {
                name: {"used": r.used, "elapsed_ms": r.elapsed_ms, "attempts": r.attempts}
                for name, r in self.stages.items()
            },
        }


def split_sentences(
    buffer: str, force: bool = False, clauses: bool = True
) -> tuple[list[str], str]:
    """Pull speakable pieces out of a streaming buffer, keep the remainder.

    `clauses=True` also breaks after a comma once the clause is long enough: the first
    piece can go to TTS while the model is still writing, which is the only way the
    speaker starts before the answer is finished.
    """
    out: list[str] = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch in SENTENCE_ENDINGS and i - start + 1 >= MIN_SENTENCE_CHARS:
            out.append(buffer[start:i + 1].strip())
            start = i + 1
        elif clauses and ch in SOFT_ENDINGS and i - start + 1 >= SOFT_MIN_CHARS:
            out.append(buffer[start:i + 1].strip())
            start = i + 1
        elif i - start + 1 >= MAX_SENTENCE_CHARS:
            out.append(buffer[start:i + 1].strip())
            start = i + 1
    rest = buffer[start:]
    if force and rest.strip():
        out.append(rest.strip())
        rest = ""
    return [s for s in out if s], rest


async def open_stream_stage(
    stage: str,
    chain: list[tuple[str, Any]],
    method: str,
    *args: Any,
) -> tuple[tuple[str, Any, AsyncIterator[Any]], StageReport]:
    """Start a streaming stage and prove it works before committing to it.

    A provider that connects and then fails on its first chunk is not a provider; pulling
    one chunk inside the try is what makes the fallback chain meaningful for streaming.
    """
    attempts: list[dict] = []
    for name, provider in chain:
        try:
            stream = getattr(provider, method)(*args)
            first = await anext(stream)
        except Exception as exc:  # noqa: BLE001 - that is what the chain is for
            attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}"})
            log.warning("%s: %s failed to start: %s", stage, name, exc)
            continue
        attempts.append({"provider": name, "error": ""})
        return (name, first, stream), StageReport(
            stage=stage, used=name, attempts=attempts, elapsed_ms=0.0
        )
    raise StageFailure(stage, attempts)


class TurnRunner:
    def __init__(
        self,
        providers: ProvidersConfig,
        *,
        system_prompt: str = DEFAULT_PROMPT,
        sample_rate: int = 16000,
        history_turns: int = 4,
    ) -> None:
        self.providers = providers
        self.system_prompt = system_prompt
        self.sample_rate = sample_rate
        self.history_turns = history_turns
        self.history: list[dict] = []

    async def transcribe(self, pcm: bytes, trace: TurnTrace) -> tuple[str, StageReport | None]:
        trace.mark("end_of_speech")
        try:
            transcript, report = await call_stage(
                "asr", asr_chain(self.providers), "transcribe", pcm, self.sample_rate
            )
        except StageFailure as exc:
            log.warning("turn: %s", exc)
            return "", None
        trace.mark("asr_done")
        return transcript.text, report

    async def _speak_sentence(
        self,
        text: str,
        chain: list[tuple[str, Any]],
        speak: Callable[[bytes], Awaitable[None]],
        trace: TurnTrace,
    ) -> tuple[str, int, StageReport | None, str]:
        """Synthesise one sentence, taking the first provider that actually streams.

        Returns (provider name, bytes spoken, report, error). The caller keeps the name so
        the next sentence goes straight to the same provider instead of re-probing.
        """
        attempts: list[dict] = []
        for name, provider in chain:
            spoken = 0
            try:
                stream = provider.synthesize(text, "")
                async for chunk in stream:
                    if not chunk:
                        continue
                    if spoken == 0:
                        trace.mark("tts_first")
                        if not trace.marks.get("play_start"):
                            trace.mark("play_start")
                    await speak(chunk)
                    spoken += len(chunk)
            except Exception as exc:  # noqa: BLE001 - fall through to the next vendor
                attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}"})
                log.warning("tts: %s failed: %s", name, exc)
                if spoken:
                    return name, spoken, None, ""
                continue
            attempts.append({"provider": name, "error": ""})
            return name, spoken, StageReport("tts", name, attempts, 0.0), ""
        return "", 0, None, f"tts failed for every provider: {attempts}"

    async def run(
        self,
        pcm: bytes,
        speak: Callable[[bytes], Awaitable[None]],
        trace: TurnTrace,
    ) -> TurnResult:
        result = TurnResult()
        transcript, asr_report = await self.transcribe(pcm, trace)
        result.transcript = transcript
        if asr_report is not None:
            result.stages["asr"] = asr_report
        if not transcript.strip():
            result.error = "empty transcript"
            return result

        messages: list[dict] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages += self.history[-2 * self.history_turns:]
        messages.append({"role": "user", "content": transcript})

        try:
            (llm_name, first_chunk, stream), llm_report = await open_stream_stage(
                "llm", llm_chain(self.providers), "chat", messages, []
            )
        except StageFailure as exc:
            result.error = str(exc)
            return result
        result.stages["llm"] = llm_report
        trace.mark("llm_first")

        tts_provider: str | None = None
        buffer = ""
        reply_parts: list[str] = []

        async def say(text: str) -> None:
            nonlocal tts_provider, result
            chain = tts_chain(self.providers, self.sample_rate)
            if tts_provider is not None:
                chain = [pair for pair in chain if pair[0] == tts_provider] + [
                    pair for pair in chain if pair[0] != tts_provider
                ]
            used, spoken, report, error = await self._speak_sentence(text, chain, speak, trace)
            if report is not None:
                result.stages["tts"] = report
                tts_provider = used
            if error:
                result.error = error
            result.audio_bytes += spoken

        chunks: list[ChatChunk] = [first_chunk]
        async for chunk in stream:
            chunks.append(chunk)
        for chunk in chunks:
            if not chunk.delta:
                continue
            buffer += chunk.delta
            reply_parts.append(chunk.delta)
            sentences, buffer = split_sentences(buffer)
            for sentence in sentences:
                await say(sentence)
        if buffer.strip():
            await say(buffer.strip())

        trace.mark("llm_done")
        result.reply = "".join(reply_parts)
        self.history.append({"role": "user", "content": transcript})
        self.history.append({"role": "assistant", "content": result.reply})
        return result
