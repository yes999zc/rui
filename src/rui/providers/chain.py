"""Per-stage provider chains: primary first, then fallbacks, in config order.

The rule that matters for a voice assistant: a failure must be cheap and it must be
visible. Every attempt is timed and reported in the stage report, and the stage only
fails when the whole ready chain fails - otherwise one flaky vendor would take the
assistant down with it.

Runtime chains are built from *ready* providers only (`keycheck.provider_readiness`).
A provider missing its keys is a configuration fact, not a per-sentence error: it is
skipped once, at startup, with one INFO; a stage with nothing ready gets one ERROR and
is never attempted per sentence. Real vendor failures are WARNINGs rate-limited per
provider and time window. The console reads the same readiness predicate, so its badges
and the runtime cannot disagree.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config.schema import ProvidersConfig
from .base import AsrProvider, LlmProvider, TtsProvider
from .keycheck import provider_readiness
from .openai_compat import MissingKey, OpenAiCompatAsr, OpenAiCompatLlm, OpenAiCompatTts
from .sherpa_asr import SherpaAsr
from .volc_asr import VolcAsr
from .volc_tts import VolcTts
from .xfyun_tts import XfyunTts

log = logging.getLogger(__name__)

# kind -> stage -> builder. `sherpa` is the local ASR model (first in the chain, 50 ms warm);
# `openai_compat` covers the cloud stages and the ASR fallback. TTS and LLM are API-only:
# 9哥's call, and the numbers back it -- a local TTS would mean shipping a voice model and
# giving up the `alex` voice he picked.
_BUILDERS: dict[str, dict[str, Callable[..., Any]]] = {
    "openai_compat": {"asr": OpenAiCompatAsr, "tts": OpenAiCompatTts, "llm": OpenAiCompatLlm},
    "sherpa": {"asr": SherpaAsr},
    "xfyun": {"tts": XfyunTts},
    "volc_tts": {"tts": VolcTts},
    "volc_asr": {"asr": VolcAsr},
}


@dataclass
class StageReport:
    stage: str
    used: str          # provider name that answered
    attempts: list[dict]  # {"provider", "error", "elapsed_ms"} per attempt, in order
    # Wall-clock milliseconds measured by this process:
    # - asr: the full transcription call;
    # - llm: time-to-first-token (TTFT) of the streaming round - not the whole stream;
    # - tts: cumulative synthesis time for every sentence in the turn (per-sentence
    #   reports are summed by TurnRunner.say), so it is 0.0 only when nothing was said.
    elapsed_ms: float


class StageFailure(RuntimeError):
    def __init__(self, stage: str, attempts: list[dict]) -> None:
        self.stage = stage
        self.attempts = attempts
        detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts)
        super().__init__(f"{stage} failed after {len(attempts)} attempt(s): {detail}")


# ---------------------------------------------------------------- logging policy
# Once-per-process state. A readiness skip is keyed by (stage, provider) so the four
# build sites at startup (turn, ack, filler, ack_render) still produce one line; a real
# failure is keyed the same way and separated by a time window.
_SKIP_LOGGED: set[tuple[str, str]] = set()
_FAILURE_LOGGED_AT: dict[tuple[str, str], float] = {}
_STAGE_EMPTY_LOGGED: set[str] = set()

# One warning per provider per minute is enough to notice a revoked key or a quota
# wall without turning a long answer into a wall of identical lines.
FAILURE_LOG_INTERVAL_S = 60.0

_REASON_TEXT = {
    "missing_key": "missing credential",
    "local_model": "local model files are not present",
    "base_url": "base_url is empty",
    "model": "model is empty",
    "unknown_provider": "not in providers.catalog",
}


def reset_log_state() -> None:
    """Forget once-per-process logs (tests only; production never calls this)."""
    _SKIP_LOGGED.clear()
    _FAILURE_LOGGED_AT.clear()
    _STAGE_EMPTY_LOGGED.clear()


def _first_time(stage: str, name: str) -> bool:
    key = (stage, name)
    if key in _SKIP_LOGGED:
        return False
    _SKIP_LOGGED.add(key)
    return True


def _reason_text(missing: list[str], reason: str) -> str:
    text = _REASON_TEXT.get(reason, reason or "not ready")
    if missing:
        text += f": {', '.join(missing)}"
    return text


def log_provider_failure(stage: str, name: str, exc: BaseException) -> None:
    """Log one provider failure once, never once per sentence.

    ``MissingKey`` means "not configured": expected on a fresh install (the console
    already shows the missing field), so INFO once per provider. Everything else
    (``ProviderError``, auth, quota, transport, vendor error) is a real failure:
    WARNING, rate-limited per provider and time window.
    """
    if isinstance(exc, MissingKey):
        if _first_time(stage, name):
            log.info("%s: %s is not configured (%s); skipped - add the credential or "
                     "remove it from the chain", stage, name, exc)
        return
    key = (stage, name)
    now = time.monotonic()
    last = _FAILURE_LOGGED_AT.get(key)
    if last is not None and now - last < FAILURE_LOG_INTERVAL_S:
        return
    _FAILURE_LOGGED_AT[key] = now
    # Always include the type: an empty vendor message told us nothing in the field.
    log.warning("%s: %s failed (%s): %s", stage, name, type(exc).__name__, exc)


def log_stage_failure(stage: str, detail: str) -> None:
    """One WARNING per stage per window when no provider in the ready chain worked."""
    key = (stage, "<stage>")
    now = time.monotonic()
    last = _FAILURE_LOGGED_AT.get(key)
    if last is not None and now - last < FAILURE_LOG_INTERVAL_S:
        return
    _FAILURE_LOGGED_AT[key] = now
    log.warning("%s: %s", stage, detail)


def build_stage(providers: ProvidersConfig, stage: str, **kwargs: Any) -> list[tuple[str, Any]]:
    """Construct every configured provider, ready or not (used by explicit audition)."""
    out: list[tuple[str, Any]] = []
    for name, cfg in providers.resolve(stage):
        kinds = _BUILDERS.get(cfg.kind)
        if kinds is None or stage not in kinds:
            raise ValueError(f"providers.{name}.kind={cfg.kind} cannot serve stage {stage}")
        builder = kinds[stage]
        out.append((name, builder(cfg, **kwargs) if kwargs else builder(cfg)))
    return out


def build_runtime_stage(
    providers: ProvidersConfig,
    stage: str,
    *,
    log_readiness: bool = True,
    **kwargs: Any,
) -> list[tuple[str, Any]]:
    """Build the chain the runtime actually uses: ready providers only, config order.

    `build_stage` stays unfiltered because the console's audition explicitly names one
    provider and wants its real error. Readiness is the wizard's
    `provider_readiness`, so a provider the Models page badges as missing is exactly one
    the runtime skips.
    """
    entries = list(providers.resolve(stage))
    for name, cfg in entries:
        kinds = _BUILDERS.get(cfg.kind)
        if kinds is None or stage not in kinds:
            raise ValueError(f"providers.{name}.kind={cfg.kind} cannot serve stage {stage}")

    readiness = {
        name: provider_readiness(providers, stage, name) for name, _cfg in entries
    }
    ready_names = [name for name, _cfg in entries if readiness[name][0]]
    skipped = [
        (name, readiness[name][1], readiness[name][2])
        for name, _cfg in entries if not readiness[name][0]
    ]
    if log_readiness:
        for name, missing, reason in skipped:
            if _first_time(stage, name):
                log.info("%s: %s skipped (%s); runtime chain=%s",
                         stage, name, _reason_text(missing, reason), ready_names)
        if not ready_names and stage not in _STAGE_EMPTY_LOGGED:
            _STAGE_EMPTY_LOGGED.add(stage)
            chain = [name for name, _cfg in entries]
            detail = "; ".join(
                f"{name} ({_reason_text(missing, reason)})"
                for name, missing, reason in skipped
            ) or "chain is empty"
            log.error(
                "%s: no ready provider - this stage stays idle until one works. "
                "chain=%s; skipped: %s. Add the credentials (or a ready fallback) and restart.",
                stage, chain, detail,
            )

    out: list[tuple[str, Any]] = []
    for name, cfg in entries:
        if not readiness[name][0]:
            continue
        builder = _BUILDERS[cfg.kind][stage]
        out.append((name, builder(cfg, **kwargs) if kwargs else builder(cfg)))
    return out


async def call_stage(
    stage: str,
    chain: list[tuple[str, Any]],
    method: str,
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, StageReport]:
    """Call the first provider that works; collect why the others did not."""
    attempts: list[dict] = []
    for name, provider in chain:
        retries = getattr(getattr(provider, "cfg", None), "retries", 0)
        for _attempt in range(retries + 1):
            started = time.perf_counter()
            try:
                result = await getattr(provider, method)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - a fallback chain exists for exactly this
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}",
                                 "elapsed_ms": elapsed})
                log_provider_failure(stage, name, exc)
                continue
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            attempts.append({"provider": name, "error": "", "elapsed_ms": elapsed})
            return result, StageReport(stage=stage, used=name, attempts=attempts,
                                       elapsed_ms=elapsed)
    raise StageFailure(stage, attempts)


def asr_chain(providers: ProvidersConfig) -> list[tuple[str, AsrProvider]]:
    return build_runtime_stage(providers, "asr")


def tts_chain(providers: ProvidersConfig, sample_rate: int) -> list[tuple[str, TtsProvider]]:
    return build_runtime_stage(providers, "tts", sample_rate=sample_rate)


def llm_chain(providers: ProvidersConfig) -> list[tuple[str, LlmProvider]]:
    return build_runtime_stage(providers, "llm")
