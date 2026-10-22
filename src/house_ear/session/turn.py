"""One conversational turn: transcript -> reply -> speech, with the latency marked.

Order matters for how it feels. Speech synthesis starts on the first finished sentence,
not on the finished answer: a short first sentence is the difference between "it is
thinking" and "it answered". The LLM keeps generating while the speaker is already
talking, and every stage boundary is stamped into `TurnTrace` so a slow turn can be
attributed instead of guessed at.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import AssistantConfig, ProvidersConfig, SessionConfig
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
from .filler import filler_phrase, should_speak_filler
from .idle import IdleWatch, farewell_line
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
    # Which tools actually ran this turn. "Did the model look, or did it answer from
    # memory" is the one question a fabrication report cannot answer afterwards.
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "transcript": self.transcript,
            "reply": self.reply,
            "audio_bytes": self.audio_bytes,
            "error": self.error,
            "tools": self.tools,
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
        session: SessionConfig | None = None,
        wake_words: Sequence[str] = (),
        tools: Sequence[tuple[Any, Any]] = (),
        spoken: Any = None,
        filler_pcm: dict[str, bytes] | bytes = b"",
        tool_timeout_s: float = 20.0,
        turn_timeout_s: float = 30.0,
        idle: IdleWatch | None = None,
    ) -> None:
        self.providers = providers
        # Built once, not per call. `asr_chain()` constructs the provider, and the local
        # SenseVoice provider loads its 1.1 GB model on first use - so rebuilding the
        # chain every turn reloaded the model every turn. Measured on the speaker:
        # 4.7-5.1 s of dead air between the wake word and the transcript, logged as
        # "local asr ready" before every single turn. The providers section is not
        # writable from the console, so these cannot go stale at runtime.
        self._asr = asr_chain(providers)
        self._llm = llm_chain(providers)
        self._tts = tts_chain(providers, sample_rate)
        # None means "assemble per turn": the prompt carries the length bands, the tool
        # rules and the clock, and a long session must not keep a stale copy.
        self.system_prompt = system_prompt
        self.assistant = assistant or AssistantConfig()
        # Session-level decisions: how long silence may last and what the goodbye says.
        # Held as the model (not copied into fields) so a console edit is one
        # `apply_session` away instead of a list of attributes to keep in sync.
        self.session = session or SessionConfig()
        # Injected, so the 15 s boundary can be tested without sleeping 15 s. Production
        # gets the monotonic clock and the configured window.
        self.idle = idle if idle is not None else IdleWatch(self.session.timeout_s)
        # A turn is not an idle session, however long it takes: a 20 s tool lookup must not
        # get a goodbye at 15 s. `check_idle` is a poller and may run while `run()` is open.
        self._in_turn = False
        self.sample_rate = sample_rate
        self.history_turns = history_turns
        self.wake_words = tuple(wake_words)
        self.tools = tuple(tools)
        # Shared with the control plane: what we said, so the mic cannot re-ask it.
        self.spoken = spoken
        # Pre-rendered filler, filled in by the control plane once TTS has answered.
        # Empty means "not ready yet" and the filler falls back to synthesising on the
        # spot - the old behaviour, 0.5-0.9 s slower.
        self.filler_pcm = filler_pcm
        # Set when the speaker reports a barge-in: the user said the wake word while we
        # were still talking, so the rest of this answer must not be spoken. Checked
        # between sentences and between TTS chunks - the plane has already dropped what
        # it had queued, and pushing more would talk over the new turn.
        self._abort = asyncio.Event()
        self.tool_timeout_s = tool_timeout_s
        # A turn that hangs is worse than a turn that fails: the speaker is left waiting and
        # the session says nothing. The old build wrapped it in wait_for(30) for that reason.
        self.turn_timeout_s = turn_timeout_s
        self.history: list[dict] = []
        self._handlers = {spec.llm_name: handler for spec, handler in self.tools}
        # The old build's INFO_TOOLS: a looked-up answer gets the longer budget; a weather
        # answer does not (it is short and the user is standing there).
        self.info_tool_names = {
            spec.llm_name for spec, _ in self.tools if spec.llm_name in ("news", "web_search")
        }

    @property
    def llm_tools(self) -> list[dict]:
        return [spec.as_llm_tool() for spec, _ in self.tools]

    def build_prompt(self) -> str:
        if self.system_prompt is not None:
            return self.system_prompt
        return prompts.build(
            self.assistant,
            tools_ok=bool(self.tools),
            tool_names=[getattr(spec, "name", "") for spec, _handler in self.tools],
        )

    def abort(self) -> None:
        """Stop speaking the current answer (barge-in)."""
        self._abort.set()

    async def warm(self) -> None:
        """Load what the first turn would otherwise load on its own.

        The local ASR provider pulls a 1.1 GB model on first use. Warming *these*
        instances (not a throwaway chain) is what keeps the first request as fast as the
        tenth: measured on the speaker, a cold first turn spent 5 s before the transcript.
        """
        seen: set[int] = set()
        for chain in (self._asr, self._tts, self._llm):
            for _name, provider in chain:
                if id(provider) in seen:
                    continue
                seen.add(id(provider))
                warm = getattr(provider, "warm", None)
                if warm is not None:
                    try:
                        await warm()
                    except Exception as exc:  # noqa: BLE001 - warming is never fatal
                        log.warning("warm: %s: %s", type(exc).__name__, exc)

    def apply_session(self, session: SessionConfig) -> None:
        """Take a console-edited `session` section without a restart.

        An open window keeps its original start time: if the new timeout is already
        exceeded, the next `check_idle` says goodbye at once. Resetting the countdown
        would hide the edit until the next turn, and a session that was already over
        would sit there looking alive.
        """
        self.session = session
        self.idle.timeout_s = session.timeout_s

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
                "asr", self._asr, "transcribe", pcm, self.sample_rate
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
                        if self._abort.is_set():
                            return name, spoken, None, ""
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
        *,
        transcript: str | None = None,
    ) -> TurnResult:
        """One turn, with a hard ceiling.

        A turn that hangs is worse than a turn that fails: the speaker waits, the session
        says nothing, and the logs do not say why either. The old build wrapped its turn in
        wait_for(30) for the same reason; a search that stalls at 12 s can otherwise run
        twice and eat the whole budget.
        """
        try:
            self._abort.clear()
            self._in_turn = True
            return await asyncio.wait_for(
                self._run(pcm, speak, trace, transcript=transcript), self.turn_timeout_s
            )
        except TimeoutError:
            log.warning("turn: timed out after %.0f s", self.turn_timeout_s)
            return TurnResult(error=f"turn timed out after {self.turn_timeout_s:.0f} s")
        finally:
            self._in_turn = False
            # The end of a turn is activity: the idle countdown must start after the
            # answer, not while it is still playing. A failed or timed-out turn counts
            # too - the person did speak.
            self.idle.touch()

    async def _run(
        self,
        pcm: bytes,
        speak: Callable[[bytes], Awaitable[None]],
        trace: TurnTrace,
        *,
        transcript: str | None = None,
    ) -> TurnResult:
        result = TurnResult()
        if transcript is None:
            spoken_text, asr_report = await self.transcribe(pcm, trace)
            if asr_report is not None:
                result.stages["asr"] = asr_report
            # The wake pre-roll is replayed into the gate on purpose, so the transcript can
            # start with the wake phrase. The model must see the request only.
            transcript = strip_wake(spoken_text, self.wake_words)
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

        tts_provider: str | None = None
        spoken_parts: list[str] = []
        # Every tool name the model asks for, recorded as the call streams in. The filler
        # decides on this list (session/filler.py): waiting until the round returns would
        # be too late to keep quiet about a music request.
        turn_tools: list[str] = []

        async def say(text: str) -> None:
            nonlocal tts_provider, result
            # Everything that leaves the speaker is remembered, filler included: the old
            # build's self-echo incident was exactly its own 「我想想」 coming back.
            if self.spoken is not None:
                self.spoken.remember(text)
            chain = list(self._tts)
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

        async def filler() -> None:
            """Say something small if the turn is still silent.

            Waiting 9 s in silence reads as "it did not hear me" - measured on the real
            speaker, where a tool-grounded answer takes 9-11 s to first audio. The old
            build did the same at 1.8 s. Cancelled the moment real audio starts, so a fast
            turn never hears it.

            The one turn that must stay silent is a music turn, because the song starts
            right behind the tool and the filler would land on the first bars. That rule
            lives in `session/filler.py` and is dormant until a music tool exists.
            """
            delay = self.assistant.filler_after_s
            text = filler_phrase(transcript, self.assistant.filler_text)
            if delay <= 0 or not text.strip():
                return
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if not should_speak_filler(
                delay_s=delay,
                text=text,
                tool_names=turn_tools,
                audio_started=trace.at("tts_first") is not None,
            ):
                return
            cached = (
                self.filler_pcm.get(text, b"")
                if isinstance(self.filler_pcm, dict)
                else self.filler_pcm
            )
            if cached:
                # The whole point of the filler is to arrive early; synthesising it now
                # adds the vendor's first byte to the wait it is hiding.
                log.info("filler after %.1f s (cached): %s", delay, text)
                if self.spoken is not None:
                    self.spoken.remember(text)
                if trace.at("tts_first") is None:
                    trace.mark("tts_first")
                if trace.at("play_start") is None:
                    trace.mark("play_start")
                await speak(cached)
                result.audio_bytes += len(cached)
                return
            log.info("filler after %.1f s: %s", delay, text)
            await say(text)

        filler_task = asyncio.create_task(filler(), name="filler")

        async def answer(tools: list[dict]) -> tuple[str, list[dict]]:
            """One LLM round: speak text as it streams, return (spoken text, tool calls).

            A round that decides to call a tool produces no text, so speaking on the way
            through costs nothing and keeps the first-sentence latency of S4 intact.
            """
            round_started = time.perf_counter()
            (_name, first, stream), report = await open_stream_stage(
                "llm", self._llm, "chat", messages, tools
            )
            log.info("llm round: tools=%d first_token=%.0f ms", len(tools),
                     (time.perf_counter() - round_started) * 1000)
            result.stages["llm"] = report
            trace.mark("llm_first")
            buffer = ""
            parts: list[str] = []
            calls: list[dict] = []
            first_sentence_ms = [0.0]

            def ingest(chunk: Any) -> list[str]:
                """Turn one streamed chunk into the sentences that are now complete.

                Everything here is synchronous and cheap. It must stay that way: this used
                to be a loop that drained the whole stream into a list first, which meant a
                short answer the vendor delivers in 0.9 s (measured) did not reach TTS until
                the last token arrived - a 4-5 s first-sentence latency, and the log blamed
                the model for it.
                """
                nonlocal buffer
                calls.extend(chunk.tool_calls or [])
                turn_tools.extend(call.get("name", "") for call in chunk.tool_calls or [])
                if chunk.finish_reason == "length":
                    # The vendor stopped at max_tokens: the last sentence is cut and TTS
                    # spoke half of it. Without this line the cut is completely silent.
                    log.warning("llm: answer hit max_tokens and was cut mid-sentence")
                    result.error = result.error or "answer truncated at max_tokens"
                if not chunk.delta:
                    return []
                buffer += chunk.delta
                sentences, buffer = split_sentences(buffer)
                out: list[str] = []
                for sentence in sentences:
                    # A model without a tool layer writes the call as text (DSML), and the
                    # split for streaming can cut a tag in half - so sanitise each piece
                    # instead of testing for a whole marker that may no longer be there.
                    speakable = sanitize_spoken(sentence)
                    if not speakable:
                        continue
                    parts.append(speakable)
                    if self.spoken is not None:
                        self.spoken.remember(speakable)
                    out.append(speakable)
                if out and not first_sentence_ms[0]:
                    first_sentence_ms[0] = (time.perf_counter() - round_started) * 1000
                return out

            # Synthesis and playback run on their own task: a sentence with six characters
            # takes 0.2-0.8 s to synthesise, and doing that inline here would stall the
            # reader that is still pulling tokens out of the vendor's stream.
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def speak_worker() -> None:
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    if self._abort.is_set():
                        # Barge-in: keep draining so the producer is never blocked, but
                        # say nothing more.
                        continue
                    await say(item)

            worker = asyncio.create_task(speak_worker(), name="tts")
            try:
                for sentence in ingest(first):
                    queue.put_nowait(sentence)
                async for chunk in stream:
                    if self._abort.is_set():
                        break
                    for sentence in ingest(chunk):
                        queue.put_nowait(sentence)
                tail = sanitize_spoken(buffer) if buffer.strip() else ""
                if tail:
                    parts.append(tail)
                    if self.spoken is not None:
                        self.spoken.remember(tail)
                    queue.put_nowait(tail)
            finally:
                queue.put_nowait(None)
                with contextlib.suppress(Exception):
                    await worker
            log.info("llm round done: %.0f ms (stream), %d chars, %d tool calls, "
                     "first sentence queued at %.0f ms",
                     (time.perf_counter() - round_started) * 1000, sum(len(p) for p in parts),
                     len(calls), first_sentence_ms[0])
            return "".join(parts), calls

        try:
            for attempt in range(3):
                # Two rounds with tools, then a forced answer without them: a model that
                # keeps asking for tools must still say something (old build: same rule).
                allow = (
                    self.llm_tools
                    if attempt < 2 and self.llm_tools and len(result.tools) < 5
                    else []
                )
                text, calls = await answer(allow)
                spoken_parts.append(text)
                # A round without tools is the answer by definition: if the model still
                # writes a tool call there, there is nothing left to run it with.
                if not calls or not allow:
                    break
                messages.append({
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": call.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": call.get("name", ""),
                                "arguments": call.get("arguments", "{}"),
                            },
                        }
                        for call in calls
                    ],
                })
                info_used = False
                for call in calls:
                    output = await self._call_tool(call)
                    result.tools.append(call.get("name", ""))
                    log.info("tool %s -> %s", call.get("name", ""), output[:160])
                    if call.get("name") in self.info_tool_names:
                        info_used = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": output,
                    })
                if info_used and messages and messages[0].get("role") == "system":
                    # A looked-up answer gets its own budget. Rewriting the system message
                    # keeps it one system prompt; a second one mid-conversation is not.
                    messages[0] = {
                        "role": "system",
                        "content": messages[0]["content"]
                        + "\n"
                        + prompts.search_hint(self.assistant.reply_max_chars_search),
                    }
        except StageFailure as exc:
            result.error = str(exc)
            return result
        finally:
            filler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await filler_task

        trace.mark("llm_done")
        # The reply is what was actually spoken, not the raw deltas: markup that never
        # reached the speaker must not reach the API response or the next turn's history.
        result.reply = clean_reply("".join(spoken_parts))
        self.history.append({"role": "user", "content": transcript})
        if result.reply:
            self.history.append({"role": "assistant", "content": result.reply})
        return result

    async def say(self, text: str, speak: Callable[[bytes], Awaitable[None]],
                  trace: TurnTrace) -> int:
        """Speak one canned line. No model, no round trip: session actions are decided."""
        chain = list(self._tts)
        if self.spoken is not None:
            self.spoken.remember(text)
        _used, spoken, _report, error = await self._speak_sentence(text, chain, speak, trace)
        if error:
            log.warning("canned line failed: %s", error)
        return spoken

    async def farewell(self, speak: Callable[[bytes], Awaitable[None]],
                       trace: TurnTrace) -> str:
        """End the session with the configured goodbye. Returns the line, "" if silent.

        The session closes even when the goodbye is turned off or TTS fails: the point of
        the call is the state change, the words are a courtesy. Without this, a stop word
        or an idle timeout would leave the window open and the next tick would try again.
        """
        line = farewell_line(self.session)
        try:
            if line:
                await self.say(line, speak, trace)
        finally:
            self.idle.stop()
        return line

    async def check_idle(self, speak: Callable[[bytes], Awaitable[None]],
                         trace: TurnTrace, *, now: float | None = None) -> str:
        """Poll the idle window; say goodbye if it has just closed.

        The caller needs no timer of its own: call this from whatever loop already runs
        (an audio frame, a heartbeat) and it is silent until the window closes, then
        silent again forever. Returns the spoken line, "" when there was nothing to do.

        A turn in flight is not idle, however long it takes: this is a poller and may run
        while `run()` is still awaiting a tool, so the goodbye waits for the answer.
        """
        if self._in_turn:
            return ""
        if not self.idle.tick(now=now):
            return ""
        log.info("session idle for %.1f s: farewell", self.idle.timeout_s)
        return await self.farewell(speak, trace)

    async def _call_tool(self, call: dict) -> str:
        """Run one tool call. Failures are sentences, because the model has to say them."""
        name = call.get("name", "")
        handler = self._handlers.get(name)
        if handler is None:
            return f"没有这个工具（{name}），直接说这件事你做不了。"
        try:
            args = json.loads(call.get("arguments") or "{}")
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        try:
            output = await asyncio.wait_for(handler(args), timeout=self.tool_timeout_s)
        except TimeoutError:
            log.warning("tool %s timed out after %.0f s", name, self.tool_timeout_s)
            return f"这个工具超时了（{name}），直接说没查到，不要自己编。"
        except Exception as exc:  # noqa: BLE001 - a broken tool must not kill the turn
            log.warning("tool %s failed: %s", name, exc)
            return f"这个工具出错了（{name}），直接说没查到，不要自己编。"
        return str(output)
