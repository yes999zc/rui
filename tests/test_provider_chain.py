"""Readiness filtering and log policy for provider chains (audit §6).

The xfyun case: the template keeps 讯飞 first while only 火山 has a key. The old runtime
tried the unconfigured provider on every sentence and wrote one WARNING each time. These
tests pin the replacement behaviour:

* the runtime chain contains ready providers only;
* a not-ready provider is reported once per process (INFO), not per sentence/ack/filler;
* a stage with nothing ready gets one actionable ERROR and is not retried per sentence;
* a real vendor failure is one WARNING per provider/window;
* a ready fallback still produces the audio, and the trace says which provider answered.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from rui.config.schema import Config, ProviderConfig
from rui.providers import chain as chain_module
from rui.providers.base import ChatChunk, Transcript
from rui.providers.chain import StageFailure, call_stage, tts_chain
from rui.providers.openai_compat import MissingKey, ProviderError
from rui.session.turn import TurnRunner
from rui.trace import TurnTrace


def _cfg_with_xfyun_first(monkeypatch) -> Config:
    """讯飞 (no keys) first, 火山 (key present) as the fallback."""
    for name in (
        "RUI_TEST_XFYUN_APP", "RUI_TEST_XFYUN_KEY", "RUI_TEST_XFYUN_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUI_TEST_VOLC_KEY", "volc-ready-key")
    cfg = Config()
    cfg.providers.catalog = {
        "xfyun_tts": ProviderConfig(
            kind="xfyun", voice="x4_lingxiaowan_boy",
            app_id_env="RUI_TEST_XFYUN_APP",
            api_key_env="RUI_TEST_XFYUN_KEY",
            api_secret_env="RUI_TEST_XFYUN_SECRET",
        ),
        "volc_tts": ProviderConfig(
            kind="volc_tts", model="seed-tts-2.0",
            voice="zh_female_xiaohe_uranus_bigtts",
            api_key_env="RUI_TEST_VOLC_KEY",
        ),
    }
    cfg.providers.tts.chain = ["xfyun_tts", "volc_tts"]
    return cfg


def _cfg_all_tts_missing(monkeypatch) -> Config:
    """Two configured TTS entries, neither with a key: the stage has nothing ready."""
    for name in (
        "RUI_TEST_XFYUN_APP", "RUI_TEST_XFYUN_KEY", "RUI_TEST_XFYUN_SECRET",
        "RUI_TEST_VOLC_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.providers.catalog = {
        "xfyun_tts": ProviderConfig(
            kind="xfyun", voice="v", app_id_env="RUI_TEST_XFYUN_APP",
            api_key_env="RUI_TEST_XFYUN_KEY", api_secret_env="RUI_TEST_XFYUN_SECRET",
        ),
        "volc_tts": ProviderConfig(
            kind="volc_tts", voice="v", api_key_env="RUI_TEST_VOLC_KEY",
        ),
    }
    cfg.providers.tts.chain = ["xfyun_tts", "volc_tts"]
    return cfg


def test_runtime_chain_skips_not_ready_and_logs_it_once(monkeypatch, caplog) -> None:
    chain_module.reset_log_state()
    cfg = _cfg_with_xfyun_first(monkeypatch)
    caplog.set_level(logging.INFO, logger="rui.providers.chain")

    # Startup builds the chain from three places (turn, ack, filler): one line total.
    for _ in range(3):
        chain = tts_chain(cfg.providers, 16000)
        assert [name for name, _provider in chain] == ["volc_tts"]

    infos = [record for record in caplog.records
             if "xfyun_tts" in record.getMessage()]
    warnings = [record for record in caplog.records
                if record.levelno >= logging.WARNING]
    assert len(infos) == 1
    assert infos[0].levelno == logging.INFO
    assert "RUI_TEST_XFYUN" in infos[0].getMessage()
    assert warnings == []


def test_all_providers_missing_is_one_error_not_per_sentence(monkeypatch, caplog) -> None:
    chain_module.reset_log_state()
    cfg = _cfg_all_tts_missing(monkeypatch)
    caplog.set_level(logging.INFO, logger="rui.providers.chain")

    for _ in range(3):
        assert tts_chain(cfg.providers, 16000) == []

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    warnings = [record for record in caplog.records
                if record.levelno == logging.WARNING]
    assert len(errors) == 1
    assert "no ready provider" in errors[0].getMessage()
    assert "xfyun_tts" in errors[0].getMessage()
    assert "volc_tts" in errors[0].getMessage()
    assert warnings == []


def _patch_non_tts_stages(monkeypatch) -> None:
    """Only ASR/LLM are faked: TTS goes through the real readiness-filtered chain."""
    import rui.session.turn as turn

    monkeypatch.setattr(turn, "asr_chain", lambda providers: [("asr", _FakeAsr())])
    monkeypatch.setattr(
        turn, "llm_chain",
        lambda providers: [("llm", FakeLlm(["今天多云。", "明天有雨。", "后天转晴。"]))],
    )


def _install_fake_tts(monkeypatch) -> FakeTts:
    """A ready fake TTS kind, so the chain can mix it with a real unready xfyun entry."""
    fallback = FakeTts()
    monkeypatch.setitem(
        chain_module._BUILDERS, "fake_tts",
        {"tts": lambda cfg, sample_rate=16000: fallback},
    )
    return fallback


def _cfg_with_fake_fallback(monkeypatch) -> Config:
    cfg = _cfg_with_xfyun_first(monkeypatch)
    cfg.providers.catalog["fake_tts"] = ProviderConfig(
        kind="fake_tts", base_url="http://fake-tts.invalid",
    )
    cfg.providers.tts.chain = ["xfyun_tts", "fake_tts"]
    return cfg


def _run_three_sentences(monkeypatch, caplog, providers=None):
    chain_module.reset_log_state()
    caplog.set_level(logging.INFO)
    _patch_non_tts_stages(monkeypatch)
    spoken: list[bytes] = []

    async def speak(pcm: bytes, *, segment_start: bool = False) -> None:
        spoken.append(pcm)

    trace = TurnTrace(device_id="living")
    return asyncio.run(
        TurnRunner(providers or Config().providers).run(b"\x00\x00" * 800, speak, trace)
    )


def test_three_sentences_one_missing_provider_one_info_and_fallback_audio(
    monkeypatch, caplog,
) -> None:
    fallback = _install_fake_tts(monkeypatch)
    cfg = _cfg_with_fake_fallback(monkeypatch)
    result = _run_three_sentences(monkeypatch, caplog, cfg.providers)

    messages = [record.getMessage() for record in caplog.records]
    info = [message for message in messages if "xfyun_tts" in message]
    assert len(info) == 1
    assert "skipped" in info[0]
    assert not [message for message in messages if "failed" in message.lower()]
    assert result.stages["tts"].used == "fake_tts"
    assert result.audio_bytes > 0
    assert len(fallback.texts) == 3
    assert [name for name, _ in tts_chain(cfg.providers, 16000)] == ["fake_tts"]


def test_zero_ready_tts_does_not_attempt_every_sentence(monkeypatch, caplog) -> None:
    cfg = _cfg_all_tts_missing(monkeypatch)
    result = _run_three_sentences(monkeypatch, caplog, cfg.providers)
    assert result.audio_bytes == 0
    assert result.error == "tts: no ready provider"
    messages = [record.getMessage() for record in caplog.records]
    assert not [message for message in messages if "tts failed" in message]
    errors = [record for record in caplog.records
              if record.levelno >= logging.ERROR and "no ready provider" in record.getMessage()]
    assert len(errors) == 1


def test_a_provider_error_is_rate_limited_not_per_sentence(monkeypatch, caplog) -> None:
    """A real ProviderError is one WARNING per provider/window, not one per sentence."""
    chain_module.reset_log_state()
    caplog.set_level(logging.INFO, logger="rui.providers.chain")

    class Broken:
        async def transcribe(self, pcm: bytes, sample_rate: int):
            raise ProviderError("quota exhausted (HTTP 429)")

    async def three_calls() -> None:
        for _ in range(3):
            with pytest.raises(StageFailure):
                await call_stage("asr", [("broken", Broken())], "transcribe", b"", 16000)

    asyncio.run(three_calls())
    warnings = [record for record in caplog.records
                if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "quota exhausted" in warnings[0].getMessage()
    assert "ProviderError" in warnings[0].getMessage()


def test_ack_and_filler_paths_use_the_same_policy(monkeypatch, caplog) -> None:
    from rui.audio import ack_render
    from rui.session import ack as ack_module

    chain_module.reset_log_state()
    missing = _MissingTts()
    fallback = FakeTts()
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(
        ack_module, "tts_chain",
        lambda providers, rate: [("xfyun_tts", missing), ("volc_tts", fallback)],
    )
    monkeypatch.setattr(
        ack_render, "tts_chain",
        lambda providers, rate: [("xfyun_tts", missing), ("volc_tts", fallback)],
    )
    cfg = Config()

    for _ in range(3):  # the ack is re-rendered on config saves, the filler has 3 phrases
        pcm, _rate, provider = asyncio.run(ack_module.synthesize(cfg, "在。"))
        assert pcm and provider == "volc_tts"
        filler = asyncio.run(ack_render.render_filler_pcm(cfg, "我查一下。"))
        assert filler

    messages = [record.getMessage() for record in caplog.records]
    assert sum("xfyun_tts" in message for message in messages) == 1
    assert not [message for message in messages if "failed" in message.lower()]


class _MissingTts:
    """Configured but no key: the exact shape the runtime used to log per sentence."""

    async def synthesize(self, text: str, voice: str = ""):
        raise MissingKey("no value for RUI_TEST_XFYUN_KEY")
        yield b""  # pragma: no cover - makes this an async generator


class _FakeAsr:
    async def transcribe(self, pcm: bytes, sample_rate: int) -> Transcript:
        return Transcript(text="今天天气怎么样")


class FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def synthesize(self, text: str, voice: str = ""):
        self.texts.append(text)
        yield b"\x01\x00" * 320


class FakeLlm:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def chat(self, messages: list[dict], tools: list[dict]):
        for chunk in self.chunks:
            yield ChatChunk(delta=chunk)
