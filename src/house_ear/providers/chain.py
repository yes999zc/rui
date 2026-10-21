"""Per-stage provider chains: primary first, then fallbacks, in config order.

The rule that matters for a voice assistant: a failure must be cheap and it must be
visible. Every attempt is timed and logged, and the stage only fails when the whole chain
fails - otherwise one flaky vendor would take the assistant down with it.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config.schema import ProvidersConfig
from .base import AsrProvider, LlmProvider, TtsProvider
from .openai_compat import OpenAiCompatAsr, OpenAiCompatLlm, OpenAiCompatTts

log = logging.getLogger(__name__)

# Everything is an API call: no local models, so a NAS deployment is just this container and
# a key. kind exists so a provider with a different wire shape can be added beside this one.
_BUILDERS: dict[str, dict[str, Callable[..., Any]]] = {
    "openai_compat": {"asr": OpenAiCompatAsr, "tts": OpenAiCompatTts, "llm": OpenAiCompatLlm},
}


@dataclass
class StageReport:
    stage: str
    used: str          # provider name that answered
    attempts: list[dict]
    elapsed_ms: float


class StageFailure(RuntimeError):
    def __init__(self, stage: str, attempts: list[dict]) -> None:
        self.stage = stage
        self.attempts = attempts
        detail = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts)
        super().__init__(f"{stage} failed after {len(attempts)} attempt(s): {detail}")


def build_stage(providers: ProvidersConfig, stage: str, **kwargs: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for name, cfg in providers.resolve(stage):
        kinds = _BUILDERS.get(cfg.kind)
        if kinds is None or stage not in kinds:
            raise ValueError(f"providers.{name}.kind={cfg.kind} cannot serve stage {stage}")
        builder = kinds[stage]
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
        for attempt in range(retries + 1):
            started = time.perf_counter()
            try:
                result = await getattr(provider, method)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - a fallback chain exists for exactly this
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                attempts.append({"provider": name, "error": f"{type(exc).__name__}: {exc}",
                                 "elapsed_ms": elapsed})
                log.warning("%s: %s attempt %d failed after %.0f ms: %s",
                            stage, name, attempt + 1, elapsed, exc)
                continue
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            attempts.append({"provider": name, "error": "", "elapsed_ms": elapsed})
            return result, StageReport(stage=stage, used=name, attempts=attempts,
                                       elapsed_ms=elapsed)
    raise StageFailure(stage, attempts)


def asr_chain(providers: ProvidersConfig) -> list[tuple[str, AsrProvider]]:
    return build_stage(providers, "asr")


def tts_chain(providers: ProvidersConfig, sample_rate: int) -> list[tuple[str, TtsProvider]]:
    return build_stage(providers, "tts", sample_rate=sample_rate)


def llm_chain(providers: ProvidersConfig) -> list[tuple[str, LlmProvider]]:
    return build_stage(providers, "llm")
