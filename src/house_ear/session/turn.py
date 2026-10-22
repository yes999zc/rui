"""One conversational turn: transcript -> reply -> speech, with the latency marked.

Order matters for how it feels. Speech synthesis starts on the first finished sentence,
not on the finished answer: a short first sentence is the difference between "it is
thinking" and "it answered". The LLM keeps generating while the speaker is already
talking, and every stage boundary is stamped into `TurnTrace` so a slow turn can be
attributed instead of guessed at.
"""
from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import AssistantConfig, ProvidersConfig
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
from . import prompts
from .text import clean_reply, sanitize_spoken, strip_wake

log = logging.getLogger(__name__)

# The prompt now lives in `session/prompts.py`, assembled per turn (it carries the length
# bands and the "no tools this round" note). Kept here as an alias so old imports do not
# silently get a different prompt.
DEFAULT_PROMPT = prompts.DEFAULT_SYSTEM_PROMPT

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

# A follow-up whose whole meaning lives in the previous answer. Dropping that answer from
# the window (see `history_messages`) would leave the model with 「再说一遍」 and nothing to
# repeat, so these keep the earlier round.
FOLLOW_UP = re.compile(
    r"再说|再讲|再说一遍|继续|接着说|往下说|说下去|详细|展开|具体点|多说点|举个例子|举例|"
    r"然后呢|后来呢|为什么|凭什么|重复|再念|说全|说完整|长一点|短一点|换个说法"
)


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
        system_prompt: str | None = None,
        sample_rate: int = 16000,
        history_turns: int = 6,
        assistant: AssistantConfig | None = None,
        wake_words: Sequence[str] = (),
        tools_ok: bool = False,
    ) -> None:
        self.providers = providers
        # None means "assemble per turn": the prompt carries the length bands and the
        # no-tools note, and a long session must not keep a stale copy. A string is an
        # explicit override, used by tests and by scripts that measure one prompt.
        self.system_prompt = system_prompt
        self.assistant = assistant or AssistantConfig()
        self.sample_rate = sample_rate
        self.history_turns = history_turns
        self.wake_words = tuple(wake_words)
        self.tools_ok = tools_ok
        self.history: list[dict] = []

    def build_prompt(self) -> str:
        if self.system_prompt is not None:
            return self.system_prompt
        return prompts.build(self.assistant, tools_ok=self.tools_ok)

    def history_messages(self, transcript: str) -> list[dict]:
        """The last `history_turns` rounds, minus earlier copies of the question asked now.

        Two things are deliberate here.

        The window is empty when `history_turns` is 0 - `self.history[-2 * 0:]` is the whole
        list, not nothing (found while measuring: the "no history" run reproduced 15
        identical answers in a row and looked like the opposite of what it was).

        An earlier copy of *this* question is dropped. A model that can see its own answer
        to the same question imitates it: measured 20 rounds of one question, the answers
        after round 9 came back nearly word for word, and the longer the window the earlier
        the convergence. The question is still not fed twice: history is context, not a
        transcript to copy.
        """
        if self.history_turns <= 0:
            return []
        window = self.history[-2 * self.history_turns:]
        groups: list[list[dict]] = []
        for message in window:
            if message.get("role") == "user" or not groups:
                groups.append([message])
            else:
                groups[-1].append(message)
        out: list[dict] = []
        follow_up = bool(FOLLOW_UP.search(transcript))
        for group in groups:
            # A follow-up ("再说一遍", "详细点") means nothing without the answer it refers
            # to, so nothing is dropped for those. Only a self-contained question replaces
            # its earlier copy.
            if group[0].get("content") == transcript and not follow_up:
                continue
            out.extend(group)
        return out

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
                # aclosing: an early exit mid-sentence must close the HTTP stream, otherwise
                # the vendor keeps a half-read request open and the next turn queues behind it
                async with contextlib.aclosing(provider.synthesize(text, "")) as stream:
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
        if asr_report is not None:
            result.stages["asr"] = asr_report
        # The wake pre-roll is replayed into the gate on purpose, so the transcript can
        # start with the wake phrase. The model must see the request only.
        transcript = strip_wake(transcript, self.wake_words)
        result.transcript = transcript
        if not transcript.strip():
            result.error = "empty transcript"
            return result

        messages: list[dict] = []
        system_prompt = self.build_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages += self.history_messages(transcript)
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
        spoken_parts: list[str] = []

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
            if chunk.delta:
                buffer += chunk.delta
            if chunk.finish_reason == "length":
                # The vendor stopped at max_tokens: the last sentence is cut and TTS spoke
                # half of it. Without this line the cut is completely silent.
                log.warning("llm: answer hit max_tokens and was cut mid-sentence")
                result.error = result.error or "answer truncated at max_tokens"
            if not chunk.delta:
                continue
            sentences, buffer = split_sentences(buffer)
            for sentence in sentences:
                # A model with no tools sometimes writes the tool call as text (DSML), and
                # the split for streaming can cut a tag in half - so sanitise each piece
                # instead of testing for a whole marker that may no longer be there.
                speakable = sanitize_spoken(sentence)
                if not speakable:
                    continue
                spoken_parts.append(speakable)
                await say(speakable)
        tail = sanitize_spoken(buffer) if buffer.strip() else ""
        if tail:
            spoken_parts.append(tail)
            await say(tail)

        trace.mark("llm_done")
        # The reply is what was actually spoken, not the raw deltas: markup that never
        # reached the speaker must not reach the API response or the next turn's history.
        result.reply = clean_reply("".join(spoken_parts))
        self.history.append({"role": "user", "content": transcript})
        if result.reply:
            self.history.append({"role": "assistant", "content": result.reply})
        return result
