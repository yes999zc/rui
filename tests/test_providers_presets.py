"""Provider presets, creation, inline keys and readiness (console Phase 3).

These tests pin the product object the console can now create: a preset (or a custom
OpenAI-compatible endpoint) becomes a normal `providers.catalog` block plus a chain
entry, with preset-controlled environment names and write-only values. No response may
ever contain a key, and readiness must be the same predicate the runtime uses.
"""
from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

from rui import secrets
from rui.config import Config, load_config, save_config
from rui.config.schema import ProviderConfig, StageConfig
from rui.providers import keycheck, presets
from rui.web.api.app import create_app

SECRET = "sk-provider-preset-should-never-echo"
CUSTOM_SECRET = "sk-custom-provider-should-never-echo"

ENV_NAMES = (
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "SILICONFLOW_API_KEY", "MOONSHOT_API_KEY",
    "ZHIPU_API_KEY", "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
    "VOLC_API_KEY", "XFYUN_APPID", "XFYUN_API_KEY", "XFYUN_API_SECRET",
    "RUI_LOCAL_GW_KEY", "RUI_PROBE_KEY", "RUI_LLM_KEY",
)


@pytest.fixture()
def provider_client(tmp_path, monkeypatch):
    """A baseline config with the schema defaults and no ambient provider keys."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    path = tmp_path / "config.yaml"
    save_config(path, cfg)
    return TestClient(create_app(cfg, config_path=path)), path, tmp_path


def _row(body: dict, stage: str, name: str) -> dict:
    return next(row for row in body["stages"][stage]["providers"] if row["name"] == name)


def _assert_no_secret(payload: object, *values: str) -> None:
    text = str(payload)
    for value in values:
        assert value not in text


# ---------------------------------------------------------------- registry shape

def test_presets_cover_the_required_vendors_and_are_honest_about_kinds() -> None:
    doc = presets.document()
    ids = {item["id"] for item in doc["presets"]}
    assert {
        "openai", "deepseek", "siliconflow", "moonshot", "zhipu", "dashscope",
        "openrouter", "groq", "ollama", "lmstudio", "vllm",
        "volc-asr", "volc-tts", "xfyun-tts", "local-sherpa",
    } <= ids
    # Stable ids: no accidental duplicate.
    assert len(ids) == len(doc["presets"])

    for item in doc["presets"]:
        assert item["stage"] in ("llm", "asr", "tts")
        assert item["kind"] in presets.STAGE_KINDS[item["stage"]]
        assert item["display_name_zh"] and item["display_name_en"]
        assert item["description_zh"] and item["description_en"]
        assert item["docs_url"].startswith("https://")
        assert isinstance(item["probe"], dict)
        assert set(item["probe"]) == {"supported", "check"}
        assert item["required_fields"] or item["keyless"]
        if item["kind"] == "openai_compat":
            assert item["base_url"].startswith(("http://", "https://"))
        # Preset credential names are fixed by the registry, never client input.
        assert all(cred["name"] == cred["name"].upper() for cred in item["credentials"])

    # Every LLM preset is OpenAI-compatible, because that is the only LLM builder.
    assert all(item["kind"] == "openai_compat"
               for item in doc["presets"] if item["stage"] == "llm")

    by_id = {item["id"]: item for item in doc["presets"]}
    assert by_id["local-sherpa"]["keyless"] is True
    assert by_id["local-sherpa"]["probe"]["check"] == "local_model"
    assert by_id["xfyun-tts"]["required_env"] == [
        "XFYUN_APPID", "XFYUN_API_KEY", "XFYUN_API_SECRET"]
    assert by_id["volc-asr"]["api_key_env"] == "VOLC_API_KEY"
    # Custom mode is first-class and names are RUI_* only.
    assert doc["custom"]["enabled"] is True
    assert doc["custom"]["env_prefix"] == "RUI_"
    assert doc["custom"]["stages"]["llm"]["kinds"] == ["openai_compat"]


def test_presets_endpoint_is_authenticated_and_never_reads_a_value(
        provider_client, monkeypatch) -> None:
    client, _path, tmp_path = provider_client
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    body = client.get("/api/v1/providers/presets").json()
    assert "presets" in body and "custom" in body
    _assert_no_secret(body, SECRET)

    cfg = Config()
    cfg.system.data_dir = str(tmp_path / "data")
    cfg.web.auth_token = "console-token"
    protected = TestClient(create_app(cfg, config_path=tmp_path / "auth.yaml"))
    assert protected.get("/api/v1/providers/presets").status_code == 401
    assert protected.get("/api/v1/providers/presets",
                         headers={"X-Auth-Token": "console-token"}).status_code == 200


# ---------------------------------------------------------------- create + keys

def test_create_preset_writes_catalog_chain_and_write_only_key(provider_client) -> None:
    client, path, tmp_path = provider_client
    response = client.post("/api/v1/providers", json={
        "preset_id": "deepseek",
        "keys": {"DEEPSEEK_API_KEY": SECRET},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    provider = body["provider"]
    assert provider["name"] == "deepseek_llm"
    assert provider["kind"] == "openai_compat"
    assert provider["ready"] is True
    assert provider["missing"] == []
    assert provider["reason"] == ""
    assert provider["probe"] == {"supported": True, "check": "models"}
    assert body["restart_required"] is True
    _assert_no_secret(body, SECRET)

    written = load_config(path)
    block = written.providers.catalog["deepseek_llm"]
    assert block.base_url == "https://api.deepseek.com"
    assert block.model == "deepseek-chat"
    assert block.api_key_env == "DEEPSEEK_API_KEY"
    # Appended after the existing chain entry, not silently dropped.
    assert written.providers.llm.chain == ["llm", "deepseek_llm"]
    # The env NAME is in config; the value is only in secrets.yaml (0600).
    assert SECRET not in path.read_text()
    secret_path = tmp_path / "data" / "secrets.yaml"
    assert secret_path.is_file()
    assert stat.S_IMODE(os.stat(secret_path).st_mode) == 0o600
    assert SECRET in secret_path.read_text()
    assert secrets.resolve("DEEPSEEK_API_KEY").value == SECRET

    # Every read path agrees and still never echoes the value.
    status = client.get("/api/v1/providers/status").json()
    assert _row(status, "llm", "deepseek_llm")["ready"] is True
    config = client.get("/api/v1/config").json()
    _assert_no_secret(config, SECRET)
    _assert_no_secret(status, SECRET)


def test_create_custom_provider_with_rui_env_and_set_primary(provider_client) -> None:
    client, path, _tmp = provider_client
    response = client.post("/api/v1/providers", json={
        "custom": {
            "name": "local_gateway",
            "stage": "llm",
            "kind": "openai_compat",
            "base_url": "https://gateway.example/v1",
            "model": "house-model",
            "api_key_env": "RUI_LOCAL_GW_KEY",
        },
        "keys": {"RUI_LOCAL_GW_KEY": CUSTOM_SECRET},
        "set_primary": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"]["primary"] is True
    assert body["stage"]["primary"] == "local_gateway"
    _assert_no_secret(body, CUSTOM_SECRET)

    written = load_config(path)
    assert written.providers.llm.chain == ["local_gateway", "llm"]
    block = written.providers.catalog["local_gateway"]
    assert (block.kind, block.base_url, block.model) == (
        "openai_compat", "https://gateway.example/v1", "house-model")
    assert secrets.resolve("RUI_LOCAL_GW_KEY").value == CUSTOM_SECRET


def test_creating_without_a_key_is_ready_false_with_a_reason(provider_client) -> None:
    client, _path, _tmp = provider_client
    body = client.post("/api/v1/providers", json={"preset_id": "openai"}).json()
    provider = body["provider"]
    assert provider["ready"] is False
    assert provider["missing"] == ["OPENAI_API_KEY"]
    assert provider["reason"] == "missing_key"
    assert provider["configured"] is False


def test_preset_chain_append_keeps_previous_order(provider_client) -> None:
    client, path, _tmp = provider_client
    first = client.post("/api/v1/providers", json={
        "preset_id": "groq", "keys": {"GROQ_API_KEY": "gsk-test-1"}})
    second = client.post("/api/v1/providers", json={
        "preset_id": "openrouter", "keys": {"OPENROUTER_API_KEY": "sk-or-test-1"}})
    assert first.status_code == 200 and second.status_code == 200
    written = load_config(path)
    assert written.providers.llm.chain == ["llm", "groq_llm", "openrouter_llm"]
    # Neither creation replaced the existing chain or the other provider.
    assert set(written.providers.catalog) >= {"llm", "groq_llm", "openrouter_llm"}


# ---------------------------------------------------------------- 422 validation

def test_create_rejects_unknown_duplicate_bad_env_and_bad_stage(provider_client) -> None:
    client, _path, _tmp = provider_client

    unknown = client.post("/api/v1/providers", json={"preset_id": "nope"})
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unknown_preset"

    assert client.post("/api/v1/providers", json={
        "preset_id": "deepseek", "keys": {"DEEPSEEK_API_KEY": SECRET},
    }).status_code == 200
    duplicate = client.post("/api/v1/providers", json={"preset_id": "deepseek"})
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "duplicate_provider"

    # A preset's env names are fixed: a different name is not a credential of it.
    wrong_preset_key = client.post("/api/v1/providers", json={
        "preset_id": "openai", "keys": {"DEEPSEEK_API_KEY": SECRET}})
    assert wrong_preset_key.status_code == 422
    assert wrong_preset_key.json()["detail"]["code"] == "invalid_key"

    blocked_env = client.post("/api/v1/providers", json={
        "custom": {"name": "bad_env", "stage": "llm", "kind": "openai_compat",
                   "base_url": "https://gw.example/v1", "model": "m",
                   "api_key_env": "DEEPSEEK_API_KEY"}})
    assert blocked_env.status_code == 422
    assert blocked_env.json()["detail"]["code"] == "invalid_env"

    bad_stage = client.post("/api/v1/providers", json={
        "custom": {"name": "bad_stage", "stage": "vision", "kind": "openai_compat",
                   "base_url": "https://gw.example/v1"}})
    assert bad_stage.status_code == 422
    assert bad_stage.json()["detail"]["code"] == "invalid_stage"

    bad_kind = client.post("/api/v1/providers", json={
        "custom": {"name": "bad_kind", "stage": "llm", "kind": "xfyun",
                   "base_url": "", "model": ""}})
    assert bad_kind.status_code == 422
    assert bad_kind.json()["detail"]["code"] == "invalid_kind"

    both = client.post("/api/v1/providers", json={
        "preset_id": "openai",
        "custom": {"name": "x", "stage": "llm", "kind": "openai_compat",
                   "base_url": "https://gw.example/v1"}})
    assert both.status_code == 422
    assert both.json()["detail"]["code"] == "invalid_request"

    no_config = TestClient(create_app(Config()))
    missing_file = no_config.post("/api/v1/providers", json={"preset_id": "openai"})
    assert missing_file.status_code == 409


# ---------------------------------------------------------------- readiness / status

def test_status_reports_chain_readiness_and_missing_keys(provider_client, monkeypatch) -> None:
    client, _path, _tmp = provider_client
    body = client.get("/api/v1/providers/status").json()
    llm = body["stages"]["llm"]
    assert llm["chain"] == ["llm"]
    assert llm["not_ready"] == ["llm"]
    assert llm["ready"] == []
    assert llm["satisfied"] is False
    row = _row(body, "llm", "llm")
    assert row["reason"] == "missing_key"
    assert row["probe"] == {"supported": True, "check": "models"}

    created = client.post("/api/v1/providers", json={
        "preset_id": "deepseek", "keys": {"DEEPSEEK_API_KEY": SECRET}}).json()
    assert created["provider"]["ready"] is True
    body = client.get("/api/v1/providers/status").json()
    assert body["stages"]["llm"]["ready"] == ["deepseek_llm"]
    assert body["stages"]["llm"]["not_ready"] == ["llm"]
    assert body["stages"]["llm"]["satisfied"] is True

    # Readiness is live with the secrets store, not frozen at startup.
    secrets.put("DEEPSEEK_API_KEY", "")
    body = client.get("/api/v1/providers/status").json()
    assert _row(body, "llm", "deepseek_llm")["reason"] == "missing_key"
    assert body["stages"]["llm"]["satisfied"] is False


def test_status_exposes_probe_support_per_provider(provider_client) -> None:
    client, _path, _tmp = provider_client
    client.post("/api/v1/providers", json={"preset_id": "volc-tts"})
    client.post("/api/v1/providers", json={"preset_id": "local-sherpa"})
    body = client.get("/api/v1/providers/status").json()
    tts = _row(body, "tts", "volc_tts")
    asr = _row(body, "asr", "local_asr")
    assert tts["probe"]["check"] == "synthesis"
    assert asr["probe"] == {"supported": True, "check": "local_model"}
    assert asr["reason"] == "local_model"  # model dir absent in this fixture


# ---------------------------------------------------------------- probe endpoint

def test_probe_draft_uses_the_real_probe_and_writes_nothing(
        provider_client, monkeypatch) -> None:
    client, path, _tmp = provider_client
    before = path.read_text()
    calls: list[dict] = []

    async def fake_test(cfg, stage, provider=None, *, keys=None):
        calls.append({"cfg": cfg, "stage": stage, "provider": provider, "keys": keys})
        assert cfg.providers.catalog[provider].base_url == "https://probe.example/v1"
        return {"ok": True, "stage": stage, "provider": provider,
                "message": "probe ok", "details": {"check": "models"}}

    monkeypatch.setattr(keycheck, "test_provider", fake_test)
    response = client.post("/api/v1/providers/probe", json={
        "custom": {
            "name": "probe_gateway",
            "stage": "llm",
            "kind": "openai_compat",
            "base_url": "https://probe.example/v1",
            "model": "probe-model",
            "api_key_env": "RUI_PROBE_KEY",
        },
        "keys": {"RUI_PROBE_KEY": "sk-probe"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["draft"] is True
    assert body["name"] == "probe_gateway"
    assert calls and calls[0]["keys"] == {"RUI_PROBE_KEY": "sk-probe"}
    # In-memory only: no catalog entry, chain edit or secret write.
    assert path.read_text() == before
    assert "probe_gateway" not in load_config(path).providers.catalog
    assert secrets.resolve("RUI_PROBE_KEY") is None


# ---------------------------------------------------------------- readiness predicate

def test_openai_compat_without_a_model_is_not_ready() -> None:
    cfg = Config()
    cfg.providers.catalog["gateway"] = ProviderConfig(
        kind="openai_compat", base_url="https://gw.example/v1", model="")
    cfg.providers.llm = StageConfig(chain=["gateway"])
    ready, missing, reason = keycheck.provider_readiness(cfg.providers, "llm", "gateway")
    assert ready is False
    assert missing == []
    assert reason == "model"
