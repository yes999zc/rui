"""Xunfei TTS adapter: the signing and the frame handling, without the network."""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from rui.config.schema import ProviderConfig
from rui.providers.xfyun_tts import (
    TTS_HOST,
    audio_from_frame,
    build_auth_url,
    request_payload,
)


def test_auth_url_carries_a_signed_authorization() -> None:
    url = build_auth_url("api-key-123", "api-secret-456")
    parsed = urlparse(url)
    assert parsed.scheme == "wss"
    assert parsed.netloc == TTS_HOST
    assert parsed.path == "/v2/tts"
    params = parse_qs(parsed.query)
    assert params["host"] == [TTS_HOST]
    assert params["date"][0].endswith("GMT")
    authorization = base64.b64decode(params["authorization"][0]).decode()
    assert 'api_key="api-key-123"' in authorization
    assert 'algorithm="hmac-sha256"' in authorization
    assert 'headers="host date request-line"' in authorization
    # the signature is derived from the secret, so a different one must not verify
    other = parse_qs(urlparse(build_auth_url("api-key-123", "other-secret")).query)
    assert other["authorization"] != params["authorization"] or other["date"] != params["date"]


def test_request_payload_asks_for_raw_pcm_at_the_voice_rate() -> None:
    payload = request_payload("app-id", "你好", "x4_lingxiaowan_boy", 16000)
    business = payload["business"]
    assert business["aue"] == "raw"
    assert business["auf"] == "audio/L16;rate=16000"
    assert business["vcn"] == "x4_lingxiaowan_boy"
    assert payload["common"]["app_id"] == "app-id"
    assert base64.b64decode(payload["data"]["text"]).decode() == "你好"
    assert payload["data"]["status"] == 2


def test_frames_yield_pcm_and_finish_on_status_two() -> None:
    pcm = b"\x01\x00" * 320
    first = {"code": 0, "data": {"audio": base64.b64encode(pcm).decode(), "status": 1}}
    last = {"code": 0, "data": {"audio": base64.b64encode(pcm).decode(), "status": 2}}
    empty = {"code": 0, "data": {"audio": "", "status": 1}}
    assert audio_from_frame(first) == (pcm, False)
    assert audio_from_frame(last) == (pcm, True)
    assert audio_from_frame(empty) == (b"", False)


def test_a_vendor_error_is_raised_not_swallowed() -> None:
    from rui.providers.openai_compat import ProviderError

    with pytest.raises(ProviderError, match="code=11200"):
        audio_from_frame({"code": 11200, "message": "licc failed"})


def test_missing_credentials_name_the_variable(monkeypatch) -> None:
    from rui.providers.openai_compat import MissingKey
    from rui.providers.xfyun_tts import XfyunTts

    monkeypatch.delenv("RUI_TEST_XFYUN_SECRET", raising=False)
    tts = XfyunTts(ProviderConfig(kind="xfyun", voice="x4_lingxiaowan_boy",
                                 app_id_env="RUI_TEST_XFYUN_APP",
                                 api_key_env="RUI_TEST_XFYUN_KEY",
                                 api_secret_env="RUI_TEST_XFYUN_SECRET"))
    monkeypatch.setenv("RUI_TEST_XFYUN_APP", "app")
    monkeypatch.setenv("RUI_TEST_XFYUN_KEY", "key")
    with pytest.raises(MissingKey, match="RUI_TEST_XFYUN_SECRET"):
        tts._credentials()


def test_chain_builds_xfyun_for_the_tts_stage() -> None:
    from rui.config.schema import Config
    from rui.providers.chain import build_stage

    cfg = Config()
    cfg.providers.catalog["xf"] = ProviderConfig(
        kind="xfyun", voice="x4_lingxiaowan_boy",
        app_id_env="A", api_key_env="B", api_secret_env="C",
    )
    cfg.providers.tts.chain = ["xf"]
    chain = build_stage(cfg.providers, "tts", sample_rate=16000)
    assert [name for name, _ in chain] == ["xf"]
    assert chain[0][1].sample_rate == 16000


def test_json_frame_round_trip_matches_the_wire_shape() -> None:
    """The frame the vendor sends is JSON text; make sure we parse text, not bytes."""
    frame = json.loads(json.dumps({"code": 0, "data": {"audio": "", "status": 2}}))
    assert audio_from_frame(frame) == (b"", True)
