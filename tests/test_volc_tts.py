"""Volcano (豆包) TTS: the wire format, the voice directory and the console audition.

The protocol was measured on the real account (2026-10-23): JSON lines carrying base64
PCM at the pipeline sample rate, `X-Api-Key` auth, `seed-tts-2.0` resource id. These
tests pin the parser, the three voices 9哥 asked for, and that the audition endpoint
returns a playable wav without writing any config.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from house_ear import billing
from house_ear.config import Config
from house_ear.config.catalog import document
from house_ear.config.schema import ProviderConfig
from house_ear.providers.chain import build_stage
from house_ear.providers.volc_tts import VolcTts
from house_ear.providers.volc_voices import VOLC_TTS_VOICES, voices_for
from house_ear.web.api.app import create_app


def _frame(payload: bytes | None, code: int = 20000000) -> str:
    return json.dumps({"code": code, "message": "OK",
                       "data": base64.b64encode(payload).decode() if payload else None})


class FakeResponse:
    def __init__(self, lines: list[str], status: int = 200) -> None:
        self.lines = lines
        self.status_code = status

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeStream:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeClient:
    def __init__(self, lines: list[str], captured: dict | None = None) -> None:
        self.lines = lines
        self.captured = captured if captured is not None else {}

    def __init_subclass__(cls) -> None: ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def stream(self, method: str, url: str, headers: dict, json: dict):  # noqa: A002
        self.captured.update({"method": method, "url": url, "headers": headers,
                              "json": json})
        return FakeStream(FakeResponse(self.lines))


def _cfg(**kwargs) -> ProviderConfig:
    base = dict(kind="volc_tts", api_key_env="VOLC_API_KEY",
                voice="zh_female_xiaohe_uranus_bigtts", model="seed-tts-2.0")
    base.update(kwargs)
    return ProviderConfig(**base)


def test_the_stream_is_decoded_pcm_and_voice_can_override(monkeypatch) -> None:
    captured: dict = {}
    lines = [_frame(b"\x01\x00\x02\x00"), _frame(None)]
    monkeypatch.setattr("house_ear.providers.volc_tts.secrets.get",
                        lambda name: "test-key")
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: FakeClient(lines, captured))

    async def collect():
        tts = VolcTts(_cfg(), 16000)
        return b"".join([chunk async for chunk in tts.synthesize("你好", "zh_female_vv_uranus_bigtts")])

    assert __import__("asyncio").run(collect()) == b"\x01\x00\x02\x00"
    assert captured["headers"]["X-Api-Key"] == "test-key"
    assert captured["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert captured["json"]["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
    assert captured["json"]["req_params"]["audio_params"] == {"format": "pcm", "sample_rate": 16000}


def test_vendor_error_code_raises(monkeypatch) -> None:
    lines = [json.dumps({"code": 55000000, "message": "resource ID mismatched"})]
    monkeypatch.setattr("house_ear.providers.volc_tts.secrets.get", lambda name: "k")
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: FakeClient(lines))

    async def collect():
        async for _ in VolcTts(_cfg(), 16000).synthesize("你好"):
            pass

    from house_ear.providers.openai_compat import ProviderError

    with pytest.raises(ProviderError):
        __import__("asyncio").run(collect())


def test_a_missing_api_key_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.setattr("house_ear.providers.volc_tts.secrets.get", lambda name: "")
    from house_ear.providers.openai_compat import MissingKey

    async def collect():
        async for _ in VolcTts(_cfg(), 16000).synthesize("你好"):
            pass

    with pytest.raises(MissingKey):
        __import__("asyncio").run(collect())


def test_the_three_requested_voices_are_in_the_directory() -> None:
    voices = {item["value"] for item in voices_for("volc_tts")}
    assert {"zh_female_xiaohe_uranus_bigtts",
            "zh_female_vv_uranus_bigtts",
            "zh_male_linjiananhai_uranus_bigtts"} <= voices
    assert voices_for("xfyun") == []
    assert len(VOLC_TTS_VOICES) >= 3


def test_chain_builds_the_provider() -> None:
    cfg = Config()
    cfg.providers.catalog["volc-tts"] = _cfg()
    cfg.providers.tts.chain = ["volc-tts"]
    chain = build_stage(cfg.providers, "tts", sample_rate=16000)
    assert isinstance(chain[0][1], VolcTts)


def test_catalog_offers_a_voice_select_with_audition() -> None:
    cfg = Config()
    cfg.providers.catalog["volc-tts"] = _cfg()
    cfg.providers.tts.chain = ["volc-tts"]
    fields = {f["path"]: f for f in document(cfg)["fields"]}
    field = fields["providers.catalog.volc-tts.voice"]
    assert field["kind"] == "select"
    assert field["audition"] == {"stage": "tts", "provider": "volc-tts"}
    assert any(option["value"] == "zh_female_vv_uranus_bigtts"
               for option in field["options"])


def test_audition_returns_a_wav_and_does_not_save(monkeypatch) -> None:
    cfg = Config()
    cfg.providers.catalog["volc-tts"] = _cfg()
    cfg.providers.tts.chain = ["volc-tts"]

    class FakeProvider:
        async def synthesize(self, text: str, voice: str = ""):
            yield b"\x01\x00" * 1600

    monkeypatch.setattr("house_ear.web.api.app.build_stage",
                        lambda providers, stage, sample_rate: [("volc-tts", FakeProvider())])
    client = TestClient(create_app(cfg))
    response = client.post("/api/v1/providers/tts/volc-tts/audition",
                           json={"voice": "zh_female_xiaohe_uranus_bigtts"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content[:4] == b"RIFF"
    assert len(response.content) > 44
    # the saved config is untouched by an audition
    assert cfg.providers.catalog["volc-tts"].voice == "zh_female_xiaohe_uranus_bigtts"


def test_audition_rejects_an_unknown_provider(monkeypatch) -> None:
    monkeypatch.setattr("house_ear.web.api.app.build_stage",
                        lambda providers, stage, sample_rate: [])
    client = TestClient(create_app(Config()))
    response = client.post("/api/v1/providers/tts/ghost/audition", json={})
    assert response.status_code == 404


def test_voices_endpoint_lists_tts_providers(monkeypatch) -> None:
    cfg = Config()
    cfg.providers.catalog["volc-tts"] = _cfg()
    cfg.providers.tts.chain = ["volc-tts"]
    client = TestClient(create_app(cfg))
    body = client.get("/api/v1/providers/voices").json()
    assert "volc-tts" in body["tts"]
    assert body["tts"]["volc-tts"][0]["label"]["zh"]


def test_billing_signature_is_stable_and_covers_the_body() -> None:
    import datetime as dt

    headers = billing.sign_headers(
        ak="AK", sk="SK", method="POST", action="UsageMonitoring",
        version="2025-05-21", service="speech_saas_prod", region="cn-north-1",
        body={"ProjectName": "default"}, now=dt.datetime(2026, 10, 23, 7, 0, tzinfo=dt.UTC))
    assert headers["X-Content-Sha256"] == hashlib.sha256(
        b'{"ProjectName":"default"}').hexdigest()
    assert "/20261023/cn-north-1/speech_saas_prod/request" in headers["Authorization"]
    assert headers["X-Date"] == "20261023T070000Z"


def test_quota_summary_without_credentials_is_a_sentence(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(billing.secrets, "get", lambda name: "")
    data = asyncio.run(billing.quota_summary())
    assert data["available_balance"] is None
    assert data["errors"] and "VOLC_ACCESS_KEY_ID" in data["errors"][0]


def test_quota_summary_parses_balance_usage_and_packs(monkeypatch) -> None:
    import asyncio

    async def fake_post(client, **kwargs):  # noqa: ARG001
        action = kwargs["action"]
        if action == "QueryBalanceAcct":
            return {"Result": {"AvailableBalance": "12.5", "CashBalance": "10",
                               "CreditLimit": "0", "Currency": "CNY"}}
        if action == "UsageMonitoring":
            return {"Result": {"UsageMonitoring": [
                {"Day": "2026-10-22", "UsageType": "text_words", "Value": 120},
                {"Day": "2026-10-23", "UsageType": "text_words", "Value": 30}]}}
        return {"Result": {"Packs": [{"ResourceName": "TTS 2.0", "Remaining": 50000}]}}

    monkeypatch.setattr(billing, "_post", fake_post)
    monkeypatch.setattr(billing.secrets, "get", lambda name: "x")
    data = asyncio.run(billing.quota_summary())
    assert data["available_balance"] == "12.5"
    assert data["currency"] == "CNY"
    assert data["tts_chars_7d"] == 150
    assert data["packs"][0]["remaining"] == 50000
    assert data["errors"] == []


def test_prosody_is_forwarded_only_when_configured(monkeypatch) -> None:
    captured: dict = {}
    lines = [_frame(b"\x00\x00"), _frame(None)]
    monkeypatch.setattr("house_ear.providers.volc_tts.secrets.get", lambda name: "k")
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: FakeClient(lines, captured))

    async def collect(cfg):
        async for _ in VolcTts(cfg, 16000).synthesize("你好"):
            pass

    import asyncio

    asyncio.run(collect(_cfg()))
    assert "speech_rate" not in captured["json"]["req_params"]["audio_params"]
    assert "emotion" not in captured["json"]["req_params"]["audio_params"]

    asyncio.run(collect(_cfg(speech_rate=40, emotion="happy", emotion_scale=3)))
    params = captured["json"]["req_params"]["audio_params"]
    assert params["speech_rate"] == 40
    assert params["emotion"] == "happy"
    assert params["emotion_scale"] == 3


def test_catalog_exposes_speech_rate_emotion_and_strength() -> None:
    cfg = Config()
    cfg.providers.catalog["volc-tts"] = _cfg()
    cfg.providers.tts.chain = ["volc-tts"]
    fields = {f["path"]: f for f in document(cfg)["fields"]}
    rate = fields["providers.catalog.volc-tts.speech_rate"]
    assert (rate["kind"], rate["min"], rate["max"]) == ("number", -50, 100)
    emotion = fields["providers.catalog.volc-tts.emotion"]
    assert emotion["kind"] == "select"
    assert {"happy", "sad", "angry"} <= {option["value"] for option in emotion["options"]}
    strength = fields["providers.catalog.volc-tts.emotion_scale"]
    assert (strength["min"], strength["max"]) == (1, 5)


def test_xfyun_speed_maps_the_shared_rate() -> None:
    from house_ear.providers.xfyun_tts import request_payload

    # xfyun's own scale is 0..100 with 50 = normal; the shared speech_rate is -50..100.
    payload = request_payload("app", "你好", "voice", 16000, speed=75)
    assert payload["business"]["speed"] == 75
    assert request_payload("app", "你好", "voice", 16000, speed=0)["business"]["speed"] == 0
