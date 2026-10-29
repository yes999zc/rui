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
from pathlib import Path
from typing import Any

from ..config.schema import AssistantConfig, ProvidersConfig, SessionConfig
from ..providers.chain import (
    StageFailure,
    StageReport,
    asr_chain,
    call_stage,
    llm_chain,
    log_provider_failure,
    log_stage_failure,
    tts_chain,
)
from ..tools.contract import ToolResult
from ..tools.reminders import ToolContext, use_tool_context
from ..trace import TurnTrace
from . import phrases, prompts, rules
from .filler import filler_phrase, should_speak_filler
from .idle import IdleWatch, farewell_line
from .rules import stop_reply
from .speech_text import gap_after, prepare_for_speech
from .text import clean_reply, sanitize_spoken, strip_wake

# The speak callable is the audio plane's client.speak: one call per TTS chunk, with
# segment_start declaring the first chunk of a sentence - the only place a fade belongs.
SpeakFn = Callable[..., Awaitable[None]]

# After every spoken sentence the old build wrote 350 ms of true silence
# (its assistant_v3.speak_stream; docs/recon-old-build.md): the junction becomes silence to
# silence instead of waveform to waveform, and the device's reference channel gets a
# clean window - which is also what a wake word needs to pass AEC mid-answer. The gap
# rides after the last sentence too; the first one starts straight away.
SENTENCE_GAP_MS = 350


def _sentence_gap(rate: int, ms: int = SENTENCE_GAP_MS) -> bytes:
    """The inter-sentence gap as mono s16 PCM at ``rate``."""
    return b"\x00\x00" * (rate * ms // 1000)

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
# task-32 (2026-10-28): 10 -> 16. 「一分钟后提醒你喝水，小志。」 is one natural short
# sentence, but the 10-char comma rule cut it into tts_call#1 chars=10 plus a second
# 624 ms request, and the vendor's head/tail silence made the comma audible. 16 keeps a
# 13-char sentence whole while a clause of 16+ chars still streams early, so the
# first-audio path for real long answers is unchanged. A sentence end is always released
# immediately: the measured first-audio lead is 343 ms, and the only extra wait here is
# the 3 characters between the comma and the full stop.
SOFT_MIN_CHARS = 16
# task-3 (0.8.5, 2026-10-28): the 16-char clause window alone still split 17-20 char
# replies whose comma sat at the boundary. 「今天沈阳多云转晴最高气温二十度，好。」 is
# 16 chars up to the comma, so it reached TTS as a 16-char request plus a second one
# for the 3-char tail - the same vendor head/tail seam task-32 fixed, one length band
# higher (measured: 17/18/19/20-char comma replies each produced 2 TTS calls). A comma
# is now a split point only once MORE than SOFT_MERGE_MAX_CHARS characters of the
# current sentence are buffered, so any reply that reads as one short sentence (<= 20
# chars) is one request, while a real long answer still streams its first clause. The
# first-audio cost is bounded: a > 20-char answer releases its first clause on the
# 21st character (at most 5 characters later than the old 16-char threshold, well
# inside the ~343 ms first-audio lead). Sentence ends are still released at once.
SOFT_MERGE_MAX_CHARS = 20
# Any complete sentence is worth speaking at once: the first one decides perceived
# latency, and a longer wait for a "nicer" split is a worse trade. The cap only exists so
# a reply with no punctuation at all cannot hold the speaker silent.
MIN_SENTENCE_CHARS = 2
MAX_SENTENCE_CHARS = 45
# A short reply must reach TTS as ONE request. Splitting 「在，\n你说。」 on the newline
# (or forcing its tail) gave the vendor two calls and an audible gap between the two
# renditions (measured 2026-10-28 11:01:35: `reply: 1 gaps, max 217 ms`). The streaming
# path therefore holds pieces back while the whole buffer is still short AND would
# otherwise split into more than one piece (an internal newline, or several sentence
# ends). A single short sentence already yields one piece, so holding it would only add
# latency - that is why the condition is not "short" alone. The hold is bounded: a delta
# that ends the short window releases it, so does an explicit force/tail, and
# `SHORT_REPLY_HOLD_MS` caps it when the model pauses mid-reply.
# task-30 (2026-10-28): ≤10 characters, not 8. 「在，\n你说。」 is 5 spoken chars plus
# the newline; the 8-char window expired on a slow model and the release path split it
# again. The window now covers every ultra-short reply the model can emit.
SHORT_REPLY_CHARS = 10
SHORT_REPLY_HOLD_MS = 300.0


def _short_reply_pending(buffer: str) -> bool:
    """True while `buffer` could still be one short reply that must not be split."""
    text = buffer.strip()
    if not text or len(text) > SHORT_REPLY_CHARS:
        return False
    # Check the raw buffer: stripping removes a trailing newline, which is exactly the
    # structural break that must not split a short reply.
    return "\n" in buffer or sum(text.count(ch) for ch in SENTENCE_ENDINGS) > 1


# Where a reply may be cut when it runs past the console's budget: a sentence end reads
# as punctuation, a clause end as a pause, and anything else is an unavoidable character
# cut. Deliberately not `SENTENCE_ENDINGS`: that one also splits on "\n", which is a
# structural line break, not a spoken pause.
REPLY_ENDINGS = "。！？!?；;"
REPLY_SOFT_ENDINGS = "，,、：:"

# The one case where the everyday budget must not win: the user asked for the whole
# thing. It also wins over the search budget, because 9哥 asking for a full read-out is
# a stronger signal than the tool that fetched the material.
EXPAND_REQUEST = re.compile(
    r"背|全文|念全文|完整|详细|展开|讲一下|念一遍|读一遍|多说点|系统地|"
    # 16:41 measured: 「帮我朗读岳阳楼记」 did not match, so the 466-char text was cut
    # at the 120-char default and the reading stopped mid-sentence (朝晖夕阴，).
    r"朗读|朗诵|诵读|背诵|整篇|通篇|从头念|从头读"
)

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


@dataclass
class DirectSpeech:
    """A tool result that is read out verbatim instead of being re-answered.

    The old build's ``@@BRIEF`` contract (assistant_v3.py:906-913, 1017-1024): a
    daily briefing is the answer, so it is spoken as written (barge-in still works)
    and its text goes into history verbatim - but the model never sees the script
    in the turn that produced it. `notice` is the only thing that enters the tool
    message; `history` is what a later 「第三条再说说」 can read.

    Opt-in: a runner without `direct_hook` behaves exactly as before.
    """

    text: str
    notice: str = ""
    history: list[dict[str, str]] | None = None
    # A pre-rendered local file that is the answer itself (the joke service can ship
    # audio beside its text). Played instead of TTS; the text still goes to history.
    # Only a local path ever reaches this field - the tool rejects URLs first.
    audio_path: str | None = None


# `(tool name, user transcript, raw handler output) -> DirectSpeech | None`.
DirectHook = Callable[[str, str, Any], DirectSpeech | None]

DIRECT_HISTORY_LIMIT = 800


def split_sentences(
    buffer: str, force: bool = False, clauses: bool = True, *,
    hold_short: bool = False, short_wait_ms: float = 0.0,
) -> tuple[list[str], str]:
    """Pull speakable pieces out of a streaming buffer, keep the remainder.

    `clauses=True` also breaks after a comma once the clause is long enough **and** more
    than ``SOFT_MERGE_MAX_CHARS`` (20) characters of the current sentence are buffered:
    the first piece can go to TTS while the model is still writing, which is the only
    way the speaker starts before the answer is finished. The buffer window is what
    keeps a 17-20 char comma reply in one vendor request (task-3); a longer answer
    still streams its first clause, at the cost of waiting for the 21st character.

    `hold_short` is the streaming caller saying "this may still be a short reply": while
    the buffer is short and would split into several pieces (`_short_reply_pending`),
    nothing is emitted, so 「在，\n你说。」 reaches TTS as one request instead of two
    (2026-10-28: the second synth left a 217 ms gap; task-30: the release itself was
    still splitting after a slow model pause). It is a bounded hold - a delta that ends
    the short window, a `force`/tail call, or `short_wait_ms >= SHORT_REPLY_HOLD_MS`
    releases it **as one merged piece**, never as a split. Callers that are not
    streaming (a verbatim briefing) leave it off and keep the old split.
    """
    if hold_short and _short_reply_pending(buffer):
        # task-30: an ultra-short reply that would split is spoken as ONE piece. The old
        # release path split it once `short_wait_ms` passed the cap, which is exactly how
        # 「在，\n你说。」 still reached TTS twice when the model paused >300 ms after the
        # newline (d.10 residual risk). Releasing by merging instead of splitting keeps
        # the first-audio timing (the release happens on the same delta) while removing
        # the vendor's double head/tail silence at the seam.
        stripped = buffer.strip()
        complete = bool(stripped) and stripped[-1] in SENTENCE_ENDINGS
        if force or (complete and short_wait_ms >= SHORT_REPLY_HOLD_MS):
            return ([stripped] if stripped else []), ""
        return [], buffer
    out: list[str] = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch in SENTENCE_ENDINGS and i - start + 1 >= MIN_SENTENCE_CHARS:
            out.append(buffer[start:i + 1].strip())
            start = i + 1
        elif (clauses and ch in SOFT_ENDINGS
              and i - start + 1 >= SOFT_MIN_CHARS
              and len(buffer) - start > SOFT_MERGE_MAX_CHARS):
            # Never split inside a URL: the scheme colon alone is 10 characters into
            # "看这里 https://...", so the clause rule used to cut it in half and the
            # vendor spoke "a.cn，x" instead of a sentence (measured 2026-10-23).
            if ch in "：:" and re.search(r"(?:https?|ftp)$", buffer[max(0, i - 5):i].lower()):
                continue
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


def speech_pieces(text: str) -> list[str]:
    """Sentence-sized chunks for a verbatim read-out (a daily briefing).

    Deliberately no clause splitting: every ``_speak_sentence`` call is followed by
    the 350 ms sentence gap, and a briefing split at commas would stutter. Long
    text still gets the 45-character cap, so one vendor request cannot hang.
    """
    pieces, _rest = split_sentences(text, force=True, clauses=False)
    return pieces


def enforce_reply_cap(text: str, cap: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``cap`` characters, at the least abrupt boundary.

    Returns ``(text, truncated)``. A reply that already fits is returned unchanged, and
    so is empty text (or a non-positive cap): this is a ceiling, not a target.

    Preference order: the last sentence end within the cap, then the last clause end
    (comma / enumeration comma / colon), then a hard character cut. The boundary
    character is kept - a spoken answer that ends on 。 sounds finished, one that just
    stops does not - and trailing whitespace is dropped.

    Why this exists at all: the three budgets in ``assistant`` were only written into
    the prompt, and the model ignored them on the real speaker (2026-10-23: 478 chars
    over 90 s against a 300-char search budget). A prompt is a request; this is the
    server-side answer.
    """
    if not text or cap <= 0 or len(text) <= cap:
        return text, False
    head = text[:cap]
    for endings in (REPLY_ENDINGS, REPLY_SOFT_ENDINGS):
        cut = max(head.rfind(char) for char in endings)
        if cut >= 0:
            return text[: cut + 1].rstrip(), True
    hard = head.rstrip()
    return (hard or head), True


def reply_cap_for(
    assistant: AssistantConfig, transcript: str, info_used: bool = False
) -> tuple[int, str]:
    """``(cap, source)`` for one turn, from the three settings the console edits.

    - the user asked to expand / read it all -> ``reply_max_chars_long``;
    - an information tool ran this turn -> ``reply_max_chars_search``;
    - otherwise -> ``reply_max_chars``.

    Expansion wins over search: both are configured ceilings, and the explicit request
    is the more specific intent. ``source`` is only for the log line.
    """
    if EXPAND_REQUEST.search(transcript or ""):
        return assistant.reply_max_chars_long, "long"
    if info_used:
        return assistant.reply_max_chars_search, "search"
    return assistant.reply_max_chars, "default"



async def open_stream_stage(
    stage: str,
    chain: list[tuple[str, Any]],
    method: str,
    *args: Any,
) -> tuple[tuple[str, Any, AsyncIterator[Any]], StageReport]:
    """Start a streaming stage and prove it works before committing to it.

    A provider that connects and then fails on its first chunk is not a provider; pulling
    one chunk inside the try is what makes the fallback chain meaningful for streaming.

    ``elapsed_ms`` is time-to-first-token (TTFT): from calling the provider until its
    first chunk arrives. The rest of the stream is consumed by the caller and is not
    part of this number - the first chunk is what the speaker is actually waiting for.
    """
    attempts: list[dict] = []
    for name, provider in chain:
        started = time.perf_counter()
        try:
            stream = getattr(provider, method)(*args)
            first = await anext(stream)
        except Exception as exc:  # noqa: BLE001 - that is what the chain is for
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}",
                             "elapsed_ms": elapsed})
            log_provider_failure(stage, name, exc)
            continue
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        attempts.append({"provider": name, "error": "", "elapsed_ms": elapsed})
        return (name, first, stream), StageReport(
            stage=stage, used=name, attempts=attempts, elapsed_ms=elapsed
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
        direct_hook: DirectHook | None = None,
        play_file: Callable[[str], Awaitable[None]] | None = None,
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
        # Per-turn prompt addition (HA entities today, memory in D2).
        self.extra_context: Callable[[], str] | None = None
        self.assistant = assistant or AssistantConfig()
        # Session-level decisions: how long silence may last and what the goodbye says.
        # Held as the model (not copied into fields) so a console edit is one
        # `apply_session` away instead of a list of attributes to keep in sync.
        self.session = session or SessionConfig()
        self.keep_context = self.session.keep_context
        # Injected, so the 15 s boundary can be tested without sleeping 15 s. Production
        # gets the monotonic clock and the configured window.
        self.idle = idle if idle is not None else IdleWatch(self.session.timeout_s)
        # A turn is not an idle session, however long it takes: a 20 s tool lookup must not
        # get a goodbye at 15 s. `check_idle` is a poller and may run while `run()` is open.
        self._in_turn = False
        self._hint = ""
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
        # The V-line rendering table: llm_name -> InteractionSpec (None = the
        # handler's own text; docs/interaction.md §3).
        self._interactions = {spec.llm_name: spec.interaction for spec, _ in self.tools}
        # The old build's INFO_TOOLS: a looked-up answer gets the longer budget; a weather
        # answer does not (it is short and the user is standing there).
        self.info_tool_names = {
            spec.llm_name for spec, _ in self.tools if spec.llm_name in ("news", "web_search")
        }
        # Opt-in direct-speech channel (daily briefing today). None = the historical
        # behaviour, byte for byte: a tool result is rendered and the model answers it.
        self.direct_hook = direct_hook
        # Where a DirectSpeech carrying `audio_path` sends the file (the app wires the
        # same PlayFile room routing music uses). None = always TTS, the pre-task
        # behaviour; it is never required for text-only direct speech.
        self.play_file = play_file
        self._pending_direct: DirectSpeech | None = None
        # task-30: one entry per TTS request this turn (a "segment" on the wire). The
        # ledger is what the reply log and the turn trace read: before this, a short
        # reply split into two requests left a seam that `playback_gap_count` could not
        # see (no underrun - the audio was buffered), so the only evidence was the ear.
        self._tts_calls: list[dict] = []
        self._tts_seq = 0
        self._tts_turn_id = ""
        # The trace of the turn in flight. `abort()` is called by the control plane when a
        # barge-in lands; marking barge_in / silence here is what turns the acceptance
        # number (<=300 ms) from a test-only mark into a production measurement. The app
        # adds the same trace object to TraceLog after `run()`, so both marks land.
        self._trace: TurnTrace | None = None

    @property
    def llm_tools(self) -> list[dict]:
        return [spec.as_llm_tool() for spec, _ in self.tools]

    def set_tools(self, tools: Sequence[tuple[Any, Any]]) -> None:
        """Adopt tools registered after construction (music, HA, volume).

        `self.tools` is a tuple built in __init__; the app registers some tools
        inside the lifespan, once the plane client and config are known. Without
        this, those tools existed in the registry but the model never saw them -
        measured 08:57:57: 「音量大一点」 got 「我这边没法调音量」 with zero
        tool calls, HA's six tools likewise unreachable by the model.
        """
        self.tools = tuple(tools)
        self._handlers = {spec.llm_name: handler for spec, handler in self.tools}
        self._interactions = {spec.llm_name: spec.interaction for spec, _ in self.tools}
        self.info_tool_names = {
            spec.llm_name for spec, _ in self.tools
            if spec.llm_name in ("news", "web_search")
        }

    def build_prompt(self) -> str:
        text = self.system_prompt
        if text is None:
            text = prompts.build(
                self.assistant,
                tools_ok=bool(self.tools),
                tool_names=[getattr(spec, "name", "") for spec, _handler in self.tools],
            )
        # One hook, many tenants: HA's entity list today, memory's recall in D2.
        # A callable so the content is as fresh as the turn that uses it.
        if self.extra_context is not None:
            with contextlib.suppress(Exception):
                extra = self.extra_context()
                if extra:
                    text = f"{text}\n{extra}"
        return text

    def reset_history(self) -> None:
        """A new wake starts a new conversation: no context carried over.

        The old build did exactly this in `begin_session()` (`history = []` on
        every wake), keeping history only inside one session. Without it the
        model answered a weather question and then asked which 周杰伦 song to
        play, because the previous session's music turns were still in the
        window (measured 10:41:26). Memory, when D2 lands, is a separate,
        curated store injected through `extra_context` - not this list.
        """
        self.history.clear()

    def abort(self) -> None:
        """Stop speaking the current answer (barge-in).

        Marks `barge_in` on the turn in flight; the first place that notices the abort
        marks `silence`. Together they are `TurnTrace.silence_after_ms`, which used to be
        null in production because only tests ever set those stages (audit 2026-10-23).
        """
        self._abort.set()
        if self._trace is not None:
            self._trace.mark("barge_in")

    def _mark_silence(self) -> None:
        """The first instant after a barge that this turn stops pushing audio."""
        if self._trace is not None:
            self._trace.mark("silence")

    async def warm(self) -> None:
        """Load what the first turn would otherwise load on its own.

        The local ASR provider pulls a 1.1 GB model on first use. Warming *these*
        instances (not a throwaway chain) is what keeps the first request as fast as the
        tenth: measured on the speaker, a cold first turn spent 5 s before the transcript.
        Stage-aware providers (the LLM) also open their TLS/HTTP connection here.
        """
        import inspect

        seen: set[int] = set()
        for stage, chain in (("asr", self._asr), ("tts", self._tts), ("llm", self._llm)):
            for _name, provider in chain:
                if id(provider) in seen:
                    continue
                seen.add(id(provider))
                warm = getattr(provider, "warm", None)
                if warm is None:
                    continue
                try:
                    if "stage" in inspect.signature(warm).parameters:
                        await warm(stage=stage)
                    else:
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
        self.keep_context = session.keep_context
        if not self.keep_context:
            self.history.clear()

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
        if self.history_turns <= 0 or not self.keep_context:
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
        if not self._asr:
            # The readiness pass already logged the one actionable ERROR at startup;
            # a per-turn copy of it would be the noise this change removes.
            return "", None
        try:
            transcript, report = await call_stage(
                "asr", self._asr, "transcribe", pcm, self.sample_rate
            )
        except StageFailure as exc:
            log_stage_failure("asr", f"turn: {exc}")
            return "", None
        trace.mark("asr_done")
        return transcript.text, report

    async def _speak_sentence(
        self,
        text: str,
        chain: list[tuple[str, Any]],
        speak: SpeakFn,
        trace: TurnTrace,
    ) -> tuple[str, int, StageReport | None, str]:
        """Synthesise one sentence, taking the first provider that actually streams.

        The vendor gets `prepare_for_speech(text)`, never the raw reply: measured
        2026-10-23, a URL was spelled out ("h t t p s 冒号 ...", 4.17 s) and a Markdown
        "## " was read as a symbol name. Punctuation that only acts as a pause is left
        to the gap logic below.

        Returns (provider name, bytes spoken, report, error). The caller keeps the name so
        the next sentence goes straight to the same provider instead of re-probing.
        """
        speakable = prepare_for_speech(text)
        if not speakable:
            return "", 0, None, ""
        if not chain:
            return "", 0, None, "tts: no ready provider"
        text = speakable
        attempts: list[dict] = []
        # One sentence's synthesis time. The caller sums these into the turn's `tts`
        # stage (see `say`); a failed provider attempt is part of the wait, so the
        # clock starts before the chain is probed.
        sentence_started = time.perf_counter()

        def finish(name: str, spoken: int, report: StageReport | None,
                   error: str = "") -> tuple[str, int, StageReport | None, str]:
            # task-30: every actual vendor request is one segment. Log it and keep the
            # per-turn ledger even when only part of a sentence made it out (abort or a
            # mid-stream failure), because that is the case where a seam is most likely.
            self._record_tts_call(trace, text, name, spoken, sentence_started, error=error)
            return name, spoken, report, error
        for name, provider in chain:
            spoken = 0
            retries = max(0, int(getattr(provider, "retries", 0) or 0))
            for attempt in range(retries + 1):
                spoken = 0
                attempt_started = time.perf_counter()
                try:
                    # aclosing: an early exit mid-sentence must close the HTTP stream,
                    # otherwise the vendor keeps a half-read request open and the next
                    # turn queues behind it
                    async with contextlib.aclosing(provider.synthesize(text, "")) as stream:
                        first = True
                        async for chunk in stream:
                            if self._abort.is_set():
                                self._mark_silence()
                                return finish(name, spoken, None)
                            if not chunk:
                                continue
                            if first:
                                trace.mark("tts_first")
                                if not trace.marks.get("play_start"):
                                    trace.mark("play_start")
                            await speak(chunk, segment_start=first)
                            first = False
                            spoken += len(chunk)
                        # The vendor's chunks are slices of one synthesis, so inside the
                        # sentence they join seamlessly - no marker, no fade. The sentence
                        # end is the junction: true silence after it, the old build's shape,
                        # so the next sentence starts from silence and AEC gets a clean
                        # window. A clause split is different: the vendor already rendered
                        # the comma pause, and another 350 ms of silence made mid-sentence
                        # speech sound stitched together (measured 2026-10-23).
                        gap_ms = gap_after(text)
                        if spoken and gap_ms:
                            await speak(_sentence_gap(self.sample_rate, gap_ms))
                except Exception as exc:  # noqa: BLE001 - fall through to the next vendor
                    elapsed = round((time.perf_counter() - attempt_started) * 1000, 1)
                    attempts.append({"provider": name,
                                     "error": f"{type(exc).__name__}: {exc}",
                                     "elapsed_ms": elapsed})
                    # MissingKey: one INFO per provider per process. A real vendor
                    # failure: one WARNING per provider per window, never per sentence.
                    log_provider_failure("tts", name, exc)
                    if spoken:
                        return finish(name, spoken, None)
                    if attempt < retries:
                        continue
                    break
                elapsed = round((time.perf_counter() - attempt_started) * 1000, 1)
                attempts.append({"provider": name, "error": "", "elapsed_ms": elapsed})
                elapsed = round((time.perf_counter() - sentence_started) * 1000, 1)
                return finish(name, spoken, StageReport("tts", name, attempts, elapsed))
        return "", 0, None, f"tts failed for every provider: {attempts}"

    @property
    def busy(self) -> bool:
        """True while a turn is running - the reminder layer waits for it."""
        return self._in_turn

    async def run(
        self,
        pcm: bytes,
        speak: SpeakFn,
        trace: TurnTrace,
        *,
        transcript: str | None = None,
        hint: str = "",
        audio: bool = True,
        touch_idle: bool = True,
        tool_context: ToolContext | None = None,
    ) -> TurnResult:
        """One turn, with a hard ceiling.

        A turn that hangs is worse than a turn that fails: the speaker waits, the session
        says nothing, and the logs do not say why either. The old build wrapped its turn in
        wait_for(30) for the same reason; a search that stalls at 12 s can otherwise run
        twice and eat the whole budget.

        ``tool_context`` carries the speaking device's resolved room to the tool
        handlers (task-32). It is scoped to this turn only: the runner is shared by
        every room, and a ContextVar copy per task keeps a 客厅 turn from seeing a
        次卧 tool call. A channel/console turn passes nothing and tools see an empty
        context, exactly as before.
        """
        with use_tool_context(tool_context or ToolContext()):
            return await self._run_turn(
                pcm, speak, trace,
                transcript=transcript, hint=hint, audio=audio, touch_idle=touch_idle,
            )

    async def _run_turn(
        self,
        pcm: bytes,
        speak: SpeakFn,
        trace: TurnTrace,
        *,
        transcript: str | None = None,
        hint: str = "",
        audio: bool = True,
        touch_idle: bool = True,
    ) -> TurnResult:
        self._begin_tts_ledger(trace)
        try:
            self._abort.clear()
            self._in_turn = True
            result = await asyncio.wait_for(
                self._run(pcm, speak, trace, transcript=transcript, hint=hint, audio=audio),
                self.turn_timeout_s,
            )
        except TimeoutError:
            log.warning("turn: timed out after %.0f s", self.turn_timeout_s)
            result = TurnResult(error=f"turn timed out after {self.turn_timeout_s:.0f} s")
        finally:
            self._in_turn = False
            self._trace = None
            # The end of a turn is activity: the idle countdown must start after the
            # answer, not while it is still playing. A failed or timed-out turn counts
            # too - the person did speak. Channel turns pass touch_idle=False: a
            # Telegram/WeChat reply must never arm the living-room goodbye.
            if touch_idle:
                self.idle.touch()
        self._note_tts_calls(trace)
        return result

    def _begin_tts_ledger(self, trace: TurnTrace) -> None:
        """Attribute the TTS-call ledger to one trace.

        A fresh trace starts a fresh ledger. `app.py` may run the runner twice on the
        same trace after a false claim; keeping the turn id makes that retry accumulate
        instead of resetting. Canned lines (`say`/`farewell`) use their own trace and
        therefore get their own ledger rather than inheriting the previous answer's.
        """
        if self._tts_turn_id != trace.turn_id:
            self._tts_calls = []
            self._tts_seq = 0
            self._tts_turn_id = trace.turn_id

    def _note_tts_calls(self, trace: TurnTrace) -> None:
        """Publish the turn's TTS-call ledger into ``trace.meta`` (task-30, additive)."""
        calls = self._tts_calls
        trace.meta["tts_call_count"] = len(calls)
        trace.meta["tts_call_chars"] = [int(call["chars"]) for call in calls]
        trace.meta["tts_call_audio_ms"] = [round(call["audio_ms"], 1) for call in calls]
        trace.meta["tts_inter_call_silence_ms"] = [
            round(call["gap_ms"], 1) for call in calls[1:]
        ]

    def _record_tts_call(
        self, trace: TurnTrace, text: str, provider: str, spoken: int,
        started: float, error: str = "",
    ) -> None:
        """One TTS request: log it and add it to the per-turn ledger.

        ``gap_ms`` is the producer's dead time since the previous request ended (synth
        wait + queueing). It is not the audible seam on its own - the plane publishes
        that as ``reply_segment_silences_ms``, which also counts the vendor's own
        leading/trailing silence - but it is the part this side can act on.
        """
        ended = time.perf_counter()
        self._tts_seq += 1
        audio_ms = spoken / 2 / self.sample_rate * 1000.0
        prev = self._tts_calls[-1] if self._tts_calls else None
        gap_ms = max(0.0, (started - prev["ended"]) * 1000.0) if prev is not None else 0.0
        call = {
            "k": self._tts_seq,
            "chars": len(text),
            "provider": provider,
            "started": started,
            "ended": ended,
            "synth_ms": (ended - started) * 1000.0,
            "audio_ms": audio_ms,
            "gap_ms": gap_ms,
            "error": error,
        }
        self._tts_calls.append(call)
        shown = text if len(text) <= 120 else text[:117] + "..."
        log.info(
            "tts_call#%d chars=%d provider=%s synth=%.0fms audio=%.0fms gap=%.0fms text=%r",
            self._tts_seq, len(text), provider, call["synth_ms"], audio_ms, gap_ms, shown,
        )
        # Keep the trace readable while the turn is still running (a canned line has no
        # `run()` wrapper); `_note_tts_calls` overwrites it with the final ledger.
        trace.meta["tts_call_count"] = len(self._tts_calls)
        trace.meta["tts_call_chars"] = [int(c["chars"]) for c in self._tts_calls]
        trace.meta["tts_call_audio_ms"] = [round(c["audio_ms"], 1) for c in self._tts_calls]
        trace.meta["tts_inter_call_silence_ms"] = [
            round(c["gap_ms"], 1) for c in self._tts_calls[1:]
        ]

    async def _run(
        self,
        pcm: bytes,
        speak: SpeakFn,
        trace: TurnTrace,
        *,
        transcript: str | None = None,
        hint: str = "",
        audio: bool = True,
    ) -> TurnResult:
        result = TurnResult()
        self._trace = trace
        self._hint = hint
        if transcript is None:
            if not self._asr:
                # One ERROR was logged when the chain was built; do not repeat it per turn.
                result.error = "asr: no ready provider"
                return result
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
        if not self._llm:
            result.error = "llm: no ready provider"
            return result

        # The reply ceiling for this turn. Applied while the answer streams (below), not
        # after it: TTS starts on the first finished sentence, so a cut applied once the
        # model finished would only change the text we report - the long answer would
        # already have been synthesised. Measured 2026-10-23: 478 chars / 90 s with the
        # prompt asking for 300, which is what a prompt-only ceiling buys.
        cap_chars, cap_source = reply_cap_for(self.assistant, transcript)
        generated_chars = 0   # everything the model wrote, capped or not (for the log)
        spoken_chars = 0      # what actually went to TTS
        capped = False

        messages: list[dict] = []
        system_prompt = self.build_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages += self.history_messages(transcript)
        messages.append({"role": "user", "content": transcript})
        if self._hint:
            # A retry instruction (false-claim guard) is a system note: putting it in the
            # transcript made it the tool query and the logged request (16:51 music query
            # contained the whole guard text).
            note = f"重要：{self._hint}"
            if messages and messages[0].get("role") == "system":
                messages[0] = {
                    "role": "system",
                    "content": messages[0]["content"] + "\n\n" + note,
                }
            else:
                messages.insert(0, {"role": "system", "content": note})

        tts_provider: str | None = None
        spoken_parts: list[str] = []
        # Every tool name the model asks for, recorded as the call streams in. The filler
        # decides on this list (session/filler.py): waiting until the round returns would
        # be too late to keep quiet about a music request.
        turn_tools: list[str] = []

        async def say(text: str) -> None:
            nonlocal tts_provider, result
            # Text channels want the answer, not a TTS bill and a second of latency.
            # The model's reply and history are assembled elsewhere; skipping `say`
            # leaves audio_bytes at zero and keeps the same tool/session behavior.
            if not audio:
                return
            if not self._tts:
                # Readiness already logged the one ERROR; per-sentence attempts (and
                # per-sentence errors) are exactly the noise this replaces.
                result.error = result.error or "tts: no ready provider"
                return
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
                # One StageReport per sentence, but the turn has one `tts` entry: sum
                # the synthesis time (and keep every attempt) instead of letting the
                # last sentence overwrite the report at 0 ms (M7/M8 review).
                previous = result.stages.get("tts")
                if previous is not None:
                    report.elapsed_ms = round(previous.elapsed_ms + report.elapsed_ms, 1)
                    report.attempts = previous.attempts + report.attempts
                result.stages["tts"] = report
                tts_provider = used
            if error:
                result.error = error
            result.audio_bytes += spoken

        # Set the moment the model asks for a tool. The filler must not run on a timer
        # from the start of the turn: measured 2026-10-22, the news tool answered at 1.77 s
        # and the filler fired at 1.80 s - 30 ms before the answer, so it covered nothing
        # and pushed the answer back by its own length. It now waits for the decision, then
        # a short beat.
        tools_pending = asyncio.Event()

        async def filler() -> None:
            """Say something small while a tool is actually running.

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
                # Two waits, whichever comes first. The useful one is "a tool is running":
                # that is a wait the turn cannot shorten. The other is the old turn-level
                # cap for a model that is simply slow with no tool at all.
                await asyncio.wait_for(tools_pending.wait(), timeout=delay)
                delay = self.assistant.filler_after_tool_s
                await asyncio.sleep(delay)
            except TimeoutError:
                pass  # no tool decision: the cap above has already elapsed
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
                # Not tts_first: the filler is not the answer, and marking it there made
                # every slow turn look fast in the metrics.
                trace.mark("filler")
                await speak(cached, segment_start=True)
                await speak(_sentence_gap(self.sample_rate))
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
            # `elapsed_ms` is this round's TTFT (time until the vendor's first chunk).
            # A tool-calling turn streams several rounds; the last one replaces the
            # entry, and that is the round that produced the spoken answer.
            result.stages["llm"] = report
            trace.mark("llm_first")
            buffer = ""
            parts: list[str] = []
            calls: list[dict] = []
            first_sentence_ms = [0.0]
            # When the buffer first became short enough to be a single short reply.
            # `None` once it grows past SHORT_REPLY_CHARS; reset after an emission.
            short_since: float | None = None

            def take(speakable: str) -> str:
                """One sanitised piece against this turn's cap; "" means do not speak it.

                The cap has to be spent here, while the answer is still streaming,
                because `say` runs on its own task as each sentence completes: a cut
                applied after the model finished would report a short reply while the
                speaker had already read the whole long one out.

                A piece that is a complete sentence or clause is dropped whole when it
                does not fit - ending on the previous piece beats speaking half of the
                next one. A piece cut by the 45-char split (or the tail) has no such
                boundary, so it is cut inside with `enforce_reply_cap`.
                """
                nonlocal capped, generated_chars, spoken_chars
                if not speakable:
                    return ""
                generated_chars += len(speakable)
                if capped:
                    return ""
                room = cap_chars - spoken_chars
                if len(speakable) <= room:
                    parts.append(speakable)
                    spoken_chars += len(speakable)
                    if self.spoken is not None:
                        self.spoken.remember(speakable)
                    return speakable
                if room <= 0:
                    capped = True
                    return ""
                if spoken_chars > 0 and (
                    speakable[-1] in REPLY_ENDINGS or speakable[-1] in REPLY_SOFT_ENDINGS
                ):
                    capped = True
                    return ""
                cut, _truncated = enforce_reply_cap(speakable, room)
                capped = True
                if not cut:
                    return ""
                parts.append(cut)
                spoken_chars += len(cut)
                if self.spoken is not None:
                    self.spoken.remember(cut)
                return cut

            def ingest(chunk: Any) -> list[str]:
                """Turn one streamed chunk into the sentences that are now complete.

                Everything here is synchronous and cheap. It must stay that way: this used
                to be a loop that drained the whole stream into a list first, which meant a
                short answer the vendor delivers in 0.9 s (measured) did not reach TTS until
                the last token arrived - a 4-5 s first-sentence latency, and the log blamed
                the model for it.
                """
                nonlocal buffer, short_since
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
                now = time.monotonic()
                if _short_reply_pending(buffer):
                    if short_since is None:
                        short_since = now
                else:
                    short_since = None
                waited_ms = 0.0 if short_since is None else (now - short_since) * 1000.0
                sentences, buffer = split_sentences(
                    buffer, hold_short=True, short_wait_ms=waited_ms)
                if sentences:
                    short_since = now if _short_reply_pending(buffer) else None
                out: list[str] = []
                for sentence in sentences:
                    # A model without a tool layer writes the call as text (DSML), and the
                    # split for streaming can cut a tag in half - so sanitise each piece
                    # instead of testing for a whole marker that may no longer be there.
                    piece = take(sanitize_spoken(sentence))
                    if piece:
                        out.append(piece)
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
                        # say nothing more. This is where pushing audio actually stops.
                        self._mark_silence()
                        continue
                    await say(item)

            worker = asyncio.create_task(speak_worker(), name="tts")
            try:
                for sentence in ingest(first):
                    queue.put_nowait(sentence)
                async for chunk in stream:
                    if self._abort.is_set():
                        self._mark_silence()
                        break
                    for sentence in ingest(chunk):
                        queue.put_nowait(sentence)
                tail = sanitize_spoken(buffer) if buffer.strip() else ""
                piece = take(tail) if tail else ""
                if piece:
                    queue.put_nowait(piece)
            finally:
                queue.put_nowait(None)
                with contextlib.suppress(Exception):
                    await worker
            log.info("llm round done: %.0f ms (stream), %d chars, %d tool calls, "
                     "first sentence queued at %.0f ms",
                     (time.perf_counter() - round_started) * 1000, sum(len(p) for p in parts),
                     len(calls), first_sentence_ms[0])
            if calls:
                # The next thing that happens is a tool call, so from here the turn is
                # waiting on something outside itself: that is when a filler helps.
                tools_pending.set()
            return "".join(parts), calls

        self._transcript = transcript
        self._silent_answer = False
        self._pending_direct = None
        direct: DirectSpeech | None = None
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
                if capped:
                    # The budget is spent, so the turn is over: running a tool this
                    # round asked for would be work behind a sentence already cut. The
                    # remaining stream was only drained to count what the model wrote.
                    break
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
                silent_calls = 0
                for call in calls:
                    guard = self._interactions.get(call.get("name", ""))
                    if (guard is not None and getattr(guard, "guard", "") == "music_request"
                            and not rules.looks_like_music_request(self._transcript)):
                        # Not a music ask: the model repeated an earlier call.
                        log.info("tool %s refused: the user did not ask for music",
                                 call.get("name", ""))
                        messages.append({"role": "tool",
                                         "tool_call_id": call.get("id", ""),
                                         "content": "用户这句话里没有点歌动词，不要调用音乐工具，"
                                                    "也不要说已经放了。问一句「要听哪首？说放一首歌名」。"})
                        continue
                    # The music query is what the USER said, never what the model
                    # typed: measured twice in one hour - it sent {} (10:13:04, a
                    # random batch instead of the band) and the previous turn's
                    # query (10:15:57: asked for 周杰伦, played Rammstein). The
                    # args keep `action`; only `query` is replaced.
                    if (guard is not None and getattr(guard, "guard", "") == "music_request"):
                        try:
                            parsed = json.loads(call.get("arguments") or "{}")
                        except ValueError:
                            parsed = {}
                        if not isinstance(parsed, dict):
                            parsed = {}
                        parsed["query"] = self._transcript
                        call = dict(call)
                        call["arguments"] = json.dumps(parsed, ensure_ascii=False)
                    output = await self._call_tool(call)
                    result.tools.append(call.get("name", ""))
                    spec = self._interactions.get(call.get("name", ""))
                    if spec is not None and getattr(spec, "answer", "text") == "silent":
                        silent_calls += 1
                    log.info("tool %s(%s) -> %s", call.get("name", ""),
                             str(call.get("arguments") or "")[:120], output[:160])
                    if call.get("name") in self.info_tool_names:
                        info_used = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": output,
                    })
                # A direct-speech tool result ends the model's turn: the script is the
                # answer (old @@BRIEF, assistant_v3.py:3492-3512). The tool message in
                # `messages` holds only the notice, so a later round could never see it.
                if self._pending_direct is not None:
                    direct = self._pending_direct
                    self._pending_direct = None
                    log.info("direct speech: %d chars (model round skipped)", len(direct.text))
                    played = False
                    # A pre-rendered file is the answer when there is one (joke audio);
                    # any problem with it (missing file, dead plane) is a spoken line,
                    # never a silent turn - the text is still in `direct.text`.
                    if direct.audio_path and self.play_file is not None:
                        try:
                            if not Path(direct.audio_path).is_file():
                                raise FileNotFoundError(direct.audio_path)
                            await self.play_file(direct.audio_path)
                            played = True
                            log.info("direct speech: played %s instead of TTS",
                                     direct.audio_path)
                        except Exception as exc:  # noqa: BLE001 - TTS is the fallback
                            log.warning("direct speech: cannot play %s (%s: %s), using TTS",
                                        direct.audio_path, type(exc).__name__, exc)
                    elif direct.audio_path:
                        log.warning("direct speech: %s was given but no play_file channel "
                                    "exists, using TTS", direct.audio_path)
                    if not played:
                        for piece in speech_pieces(direct.text):
                            await say(piece)
                    break
                # Only when EVERY tool this round is silent is the action the whole
                # answer: a hallucinated music_play beside a weather call must not
                # swallow the weather answer (measured 10:00:50).
                if silent_calls and silent_calls == len(calls):
                    self._silent_answer = True
                if self._silent_answer:
                    # Checked after the tools ran: the action was the answer.
                    log.info("song is the answer: no spoken confirmation")
                    break
                if info_used:
                    # A looked-up answer gets its own budget. Both settings are the
                    # console's; the prompt hint alone was measured not to hold, so the
                    # ceiling moves with it for the next round.
                    cap_chars, cap_source = reply_cap_for(
                        self.assistant, transcript, info_used=True
                    )
                if info_used and messages and messages[0].get("role") == "system":
                    # Rewriting the system message keeps it one system prompt; a second
                    # one mid-conversation is not.
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
        if direct is not None:
            # The script is the answer, verbatim: no clean_reply (it is already
            # speech-shaped) and no second model round. History keeps what was read
            # out, which is what 「第三条再说说」 needs next turn.
            result.reply = direct.text
            self._record_direct_history(direct, transcript)
            return result
        # The reply is what was actually spoken, not the raw deltas: markup that never
        # reached the speaker must not reach the API response or the next turn's history.
        raw_reply = clean_reply("".join(spoken_parts))
        # Safety net (and the one place the cap shows up in the API/history): the
        # streaming path above already stopped the speech itself. `enforce_reply_cap`
        # keeps this exact even across rounds, and the clean step above can only shorten.
        result.reply, post_cut = enforce_reply_cap(raw_reply, cap_chars)
        if capped or post_cut:
            log.info(
                "reply capped: %d -> %d chars (cap %d from %s)",
                max(generated_chars, len(raw_reply)), len(result.reply),
                cap_chars, cap_source,
            )
        if self.keep_context:
            self.history.append({"role": "user", "content": transcript})
            if result.reply:
                self.history.append({"role": "assistant", "content": result.reply})
        else:
            self.history.clear()   # stateless: nothing carries to the next turn
        return result

    def _record_direct_history(self, direct: DirectSpeech, transcript: str) -> None:
        """History for a verbatim read-out. Same keep_context rule as a normal turn."""
        if not self.keep_context:
            self.history.clear()
            return
        if direct.history is not None:
            self.history.extend(direct.history)
            return
        self.history.append({"role": "user", "content": transcript})
        if direct.text:
            self.history.append({"role": "assistant",
                                 "content": direct.text[:DIRECT_HISTORY_LIMIT]})

    async def say(self, text: str, speak: SpeakFn,
                  trace: TurnTrace, *, keep_abort: bool = False) -> int:
        """Speak one canned line. No model, no round trip: session actions are decided.

        The abort flag is cleared first: a wake that barged over a song sets it, and
        this line is a *new* intent, not the interrupted answer. Without this the
        canned reply rendered as pure silence - measured 08:24:52, 「暂停」 was heard,
        the session ended, and nothing was said.
        """
        if keep_abort:
            # A goodbye must respect a wake that already aborted it: clearing the flag
            # here let a farewell that started just after the wake play in full
            # (2026-10-26 07:12).
            if self._abort.is_set():
                return 0
        else:
            self._abort.clear()
        if not self._tts:
            return 0
        self._begin_tts_ledger(trace)
        chain = list(self._tts)
        if self.spoken is not None:
            self.spoken.remember(text)
        _used, spoken, _report, error = await self._speak_sentence(text, chain, speak, trace)
        if error:
            log.warning("canned line failed: %s", error)
        return spoken

    async def farewell(self, speak: SpeakFn,
                       trace: TurnTrace) -> str:
        """End the session with the configured goodbye. Returns the line, "" if silent.

        The session closes even when the goodbye is turned off or TTS fails: the point of
        the call is the state change, the words are a courtesy. Without this, a stop word
        or an idle timeout would leave the window open and the next tick would try again.
        """
        line = farewell_line(self.session)
        # Close the session before speaking. The goodbye can be aborted by a wake that
        # arrives mid-synthesis (2026-10-26 07:12); stopping the idle clock afterwards
        # would then clobber the new wake's session and the next goodbye would never
        # fire. The session is over either way.
        self.idle.stop()
        if line:
            await self.say(line, speak, trace, keep_abort=True)
        return line

    async def dismiss(self, speak: SpeakFn,
                      trace: TurnTrace) -> int:
        """Answer a stop word and close the session in the same breath.

        The stop reply *is* a goodbye. Speaking it without closing the idle watch left
        the clock running behind a session the user had already ended, and the farewell
        said goodbye a second time 15 s later (measured 20:42:16 -> 20:42:34: 「退下吧」
        -> 「好。」 -> 「没事的话，我先下啦。」).
        """
        self.idle.stop()
        return await self.say(stop_reply(), speak, trace)

    async def check_idle(self, speak: SpeakFn,
                         trace: TurnTrace, *, now: float | None = None,
                         busy: Callable[[], bool] | None = None) -> str:
        """Poll the idle window; say goodbye if it has just closed.

        The caller needs no timer of its own: call this from whatever loop already runs
        (an audio frame, a heartbeat) and it is silent until the window closes, then
        silent again forever. Returns the spoken line, "" when there was nothing to do.

        A turn in flight is not idle, however long it takes: this is a poller and may run
        while `run()` is still awaiting a tool, so the goodbye waits for the answer.

        Neither is a turn whose audio is still coming out of the speaker, and least of all
        a song. The old deployment hit exactly this: a five-minute news read or a record
        playing, nobody speaking, and 「我先下啦」 cutting in over it. `busy` is the
        playback state, polled rather than inferred, and while it is true the window keeps
        being pushed forward - so the countdown starts when the sound actually stops.
        """
        if self._in_turn:
            return ""
        if busy is not None and busy():
            # Extend an open session, never open one: the farewell itself plays audio, so
            # touching unconditionally reopened the session and said goodbye again every
            # 18 s, forever (measured 15:13:30, 15:13:49, 15:14:06).
            if self.idle.active:
                self.idle.touch()
            return ""
        if not self.idle.tick(now=now):
            return ""
        log.info("session idle for %.1f s: farewell", self.idle.timeout_s)
        return await self.farewell(speak, trace)

    async def _call_tool(self, call: dict) -> str:
        """Run one tool call. Failures are sentences, because the model has to say them.

        A handler may return a structured ToolResult; its words then come from the
        PhraseBook via the tool's InteractionSpec (docs/interaction.md §3). A plain
        string still passes through unchanged - the migration is per-tool.
        """
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
        # Opt-in verbatim channel (daily briefing): the hook sees the handler's raw
        # output before the phrase layer, and returning a DirectSpeech means "this is
        # the answer". Without a hook, nothing here runs and the path is unchanged.
        if self.direct_hook is not None:
            direct: DirectSpeech | None = None
            try:
                direct = self.direct_hook(name, getattr(self, "_transcript", ""), output)
            except Exception as exc:  # noqa: BLE001 - a hook bug must not kill the turn
                log.warning("direct hook for %s failed: %s", name, exc)
            if direct is not None:
                self._pending_direct = direct
                log.info("tool %s -> direct speech (%d chars)", name, len(direct.text))
                return direct.notice
        if isinstance(output, ToolResult):
            return self._render_tool_result(name, output)
        return str(output)

    def _render_tool_result(self, name: str, result: ToolResult) -> str:
        """System-layer wording: slots in, one phrase out - never the tool's own."""
        if "say" in result.payload:
            return str(result.payload["say"])
        spec = (self._interactions or {}).get(name)
        key = (spec.exec.get(result.status) if spec else None) or ""
        if not key:
            log.info("tool %s returned %s with no exec mapping; model gets slots only",
                     name, result.status)
            return json.dumps({"status": result.status, **result.payload},
                              ensure_ascii=False)
        rendered = phrases.book.render(key, **result.payload)
        log.info("tool %s -> phrase %s (%s)", name, key, result.status)
        return rendered
