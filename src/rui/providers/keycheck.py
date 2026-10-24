"""Provider metadata and cheap credential smoke tests for the install wizard.

Two jobs, both server-side only:

* tell the console which credentials each active chain entry needs, with a display
  name, a human label, a hint and an official docs URL - never a value;
* run the cheapest test that can actually fail when a credential is wrong. A test that
  only checks "the string is non-empty" is not a test, so every provider that has an
  online call gets one: the LLM models/chat endpoint, ASR with a fraction of a second
  of silence, TTS with one character. Providers with no safe online call say so and
  return ``ok: false`` instead of a false pass.

No response, message or log line here may contain a secret value. Vendor error text is
redacted against the values actually used before it is returned.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .. import secrets
from ..config.schema import STAGES, Config, ProviderConfig

__all__ = [
    "KeyCheckError",
    "provider_catalog",
    "provider_entries",
    "test_provider",
]

# ---------------------------------------------------------------- catalog metadata

VOLC_ASR_DOC = (
    "https://docs.volcengine.com/docs/DoubaoVoice/"
    "bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh"
)
VOLC_TTS_DOC = (
    "https://docs.volcengine.com/docs/DoubaoVoice/"
    "HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh"
)
VOLC_KEY_DOC = "https://docs.volcengine.com/docs/DoubaoVoice/APIKeyUsage?lang=zh"
XFYUN_TTS_DOC = "https://www.xfyun.cn/doc/tts/online_tts/API.html"
DEEPSEEK_DOC = "https://api-docs.deepseek.com/"
SILICONFLOW_DOC = "https://docs.siliconflow.cn/"
SHERPA_DOC = "https://github.com/k2-fsa/sherpa-onnx"

_FIELD_LABELS = {
    "api_key": "API Key",
    "app_id": "AppID",
    "api_secret": "API Secret",
}

_FIELD_HINTS = {
    "api_key": {
        "zh": "API Key 是密钥值本身，不是变量名；保存后只显示「已配置」，永远读不回。",
        "en": "The API key value itself, not a variable name. After saving only "
              "\"configured\" is shown; it can never be read back.",
    },
    "app_id": {
        "zh": "应用 ID（讯飞控制台「应用信息」里）。和 APIKey、APISecret 一起使用。",
        "en": "The application ID from the 讯飞 console (app info). Used together with "
              "the API key and API secret.",
    },
    "api_secret": {
        "zh": "应用密钥（讯飞控制台「应用信息」里）。只写不读。",
        "en": "The application secret from the 讯飞 console. Write-only.",
    },
}


@dataclass
class KeyField:
    name: str
    role: str
    label: str
    hint: dict[str, str]
    docs_url: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "label": self.label,
            "label_i18n": {"zh": self.label, "en": self.label},
            "hint": self.hint["en"],
            "hint_i18n": dict(self.hint),
            "docs_url": self.docs_url,
            "secret": True,
            "required": True,
            "configured": secrets.resolve(self.name) is not None,
        }


@dataclass
class ProviderEntry:
    stage: str
    name: str
    kind: str
    display_name: str
    display_name_en: str
    docs_url: str
    hint: dict[str, str]
    fields: list[KeyField] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return all(field.as_dict()["configured"] for field in self.fields)

    @property
    def missing(self) -> list[str]:
        return [f.name for f in self.fields if not f.as_dict()["configured"]]

    def as_dict(self) -> dict:
        fields = [item.as_dict() for item in self.fields]
        return {
            "id": f"{self.stage}:{self.name}",
            "stage": self.stage,
            "name": self.name,
            "kind": self.kind,
            "display_name": self.display_name,
            "display_name_en": self.display_name_en,
            "docs_url": self.docs_url,
            "hint": self.hint["en"],
            "hint_i18n": dict(self.hint),
            "configured": all(item["configured"] for item in fields),
            "missing": [item["name"] for item in fields if not item["configured"]],
            "fields": fields,
        }


class KeyCheckError(ValueError):
    """A bad stage/provider argument; the API turns this into a 400 object."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _host(base_url: str) -> str:
    try:
        return _norm(urlparse(base_url).hostname or "")
    except ValueError:
        return ""


def display_names(name: str, provider: ProviderConfig) -> tuple[str, str]:
    """(zh, en) display name for one catalog entry, inferred from kind/endpoint.

    The config has no display-name field on purpose: a custom provider is still
    labelled from its endpoint, and an unknown one falls back to its catalog name.
    The two shipped vendors users recognize (火山引擎, 讯飞) are fixed here; the rest
    come from the host so a proxy/staging URL does not masquerade as a vendor.
    """
    kind = _norm(provider.kind)
    host = _host(provider.base_url)
    if kind in ("volc_asr", "volc_tts"):
        return "火山引擎", "Volcano Engine"
    if kind == "xfyun":
        return "讯飞", "iFlytek"
    if kind == "sherpa":
        return "本地模型", "Local model"
    if "deepseek" in host or "deepseek" in _norm(name):
        return "DeepSeek", "DeepSeek"
    if "siliconflow" in host:
        return "硅基流动", "SiliconFlow"
    if host:
        # A readable fallback: api.example.com -> Example. Never invent a vendor.
        parts = [part for part in host.split(".") if part not in ("api", "www", "com", "cn")]
        label = parts[0].replace("-", " ").title() if parts else host
        return label, label
    return name.replace("_", " ").title(), name.replace("_", " ").title()


def _docs_url(stage: str, provider: ProviderConfig) -> str:
    kind = _norm(provider.kind)
    if kind == "volc_asr":
        return VOLC_ASR_DOC
    if kind == "volc_tts":
        return VOLC_TTS_DOC
    if kind == "xfyun":
        return XFYUN_TTS_DOC
    if kind == "sherpa":
        return SHERPA_DOC
    host = _host(provider.base_url)
    if "deepseek" in host:
        return DEEPSEEK_DOC
    if "siliconflow" in host:
        return SILICONFLOW_DOC
    if provider.base_url:
        return provider.base_url.rstrip("/")
    return ""


def _field_docs(stage: str, provider: ProviderConfig, role: str) -> str:
    if _norm(provider.kind) in ("volc_asr", "volc_tts") and role == "api_key":
        return VOLC_KEY_DOC
    return _docs_url(stage, provider)


def _provider_hint(stage: str, provider: ProviderConfig) -> dict[str, str]:
    kind = _norm(provider.kind)
    if kind in ("volc_asr", "volc_tts"):
        if kind == "volc_asr":
            return {
                "zh": "火山引擎豆包流式语音识别 2.0。新版控制台只需一个 API Key，"
                      "不需要 AppID；资源 ID 选 volc.seedasr.sauc.duration（小时版）"
                      "或 ...concurrent（并发版）。",
                "en": "Volcano Doubao streaming ASR 2.0. The new console needs one API "
                      "key (no AppID); the resource id is volc.seedasr.sauc.duration "
                      "(hourly) or ...concurrent.",
            }
        return {
            "zh": "火山引擎豆包语音合成 2.0。新版控制台只需一个 API Key，不需要 AppID；"
                  "资源 ID seed-tts-2.0 决定 2.0 音色和计费项。",
            "en": "Volcano Doubao TTS 2.0. The new console needs one API key (no AppID); "
                  "resource id seed-tts-2.0 selects 2.0 voices and billing.",
        }
    if kind == "xfyun":
        return {
            "zh": "讯飞在线语音合成。需要 AppID + APIKey + APISecret 三项，缺一不可。",
            "en": "iFlytek online TTS. It needs all three: AppID, API key and API secret.",
        }
    if kind == "sherpa":
        return {
            "zh": "本地 ASR 模型，不需要联网密钥；模型文件是否就位由 models 步骤检查。",
            "en": "Local ASR model; no online credential. The models step checks the files.",
        }
    if stage == "llm":
        return {
            "zh": "OpenAI 兼容大模型接口。填 API Key 后可用 models 或一次最小 chat 调用验证。",
            "en": "OpenAI-compatible LLM endpoint. After saving a key, a models call or a "
                  "minimal chat completion verifies it.",
        }
    return {
        "zh": "OpenAI 兼容接口。保存密钥后可做一次最小在线调用验证。",
        "en": "OpenAI-compatible endpoint. After saving, one minimal online call verifies "
              "the credential.",
    }


def _fields_for(stage: str, provider: ProviderConfig) -> list[KeyField]:
    out: list[KeyField] = []
    for attr, role in (
        ("api_key_env", "api_key"),
        ("app_id_env", "app_id"),
        ("api_secret_env", "api_secret"),
    ):
        name = str(getattr(provider, attr, "") or "").strip()
        if not name:
            continue
        out.append(KeyField(
            name=name,
            role=role,
            label=_FIELD_LABELS[role],
            hint=_FIELD_HINTS[role],
            docs_url=_field_docs(stage, provider, role),
        ))
    return out


def provider_entries(cfg: Config) -> list[ProviderEntry]:
    """One entry per (stage, provider) actually used by a configured chain."""
    entries: list[ProviderEntry] = []
    for stage in STAGES:
        for name in getattr(cfg.providers, stage).chain:
            provider = cfg.providers.catalog.get(name)
            if provider is None:
                continue
            zh, en = display_names(name, provider)
            entries.append(ProviderEntry(
                stage=stage,
                name=name,
                kind=str(provider.kind),
                display_name=zh,
                display_name_en=en,
                docs_url=_docs_url(stage, provider),
                hint=_provider_hint(stage, provider),
                fields=_fields_for(stage, provider),
            ))
    return entries


def provider_catalog(cfg: Config) -> dict:
    """The keys step's render model: providers grouped by use, values never included."""
    entries = [entry.as_dict() for entry in provider_entries(cfg)]
    return {
        "providers": entries,
        "stages": {
            stage: [entry["id"] for entry in entries if entry["stage"] == stage]
            for stage in STAGES
        },
    }


# ---------------------------------------------------------------- probes

_MIN_KEY_LEN = 4


def _looks_usable(values: dict[str, str]) -> bool:
    """A shape check only. It never turns into a pass; it gates the online probe."""
    for value in values.values():
        text = str(value or "")
        if len(text) < _MIN_KEY_LEN or any(ch.isspace() for ch in text):
            return False
    return True


def _redact(text: object, values: list[str]) -> str:
    out = str(text or "")
    for value in values:
        if value and len(value) >= 4:
            out = out.replace(value, "***")
    return out[:400]


def _result(
    ok: bool,
    stage: str,
    provider: str,
    message: str,
    *,
    check: str,
    details: dict | None = None,
) -> dict:
    return {
        "ok": bool(ok),
        "stage": stage,
        "provider": provider,
        "message": message,
        "details": {"check": check, **(details or {})},
    }


def _values_for(provider: ProviderConfig, fields: list[KeyField]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in fields:
        resolution = secrets.resolve(item.name)
        values[item.role] = resolution.value if resolution else ""
    return values


def _target(cfg: Config, stage: str, provider: str | None) -> tuple[str, ProviderConfig]:
    if stage not in STAGES:
        raise KeyCheckError(
            "unknown_stage", f"no such stage: {stage!r}", {"stage": stage, "stages": list(STAGES)}
        )
    chain = list(getattr(cfg.providers, stage).chain)
    name = (provider or "").strip() or (chain[0] if chain else "")
    if name not in chain:
        raise KeyCheckError(
            "unknown_provider",
            f"provider {name!r} is not in the {stage} chain",
            {"stage": stage, "provider": name, "available": chain},
        )
    entry = cfg.providers.catalog.get(name)
    if entry is None:  # schema validation already rejects this; keep the API honest.
        raise KeyCheckError(
            "unknown_provider", f"provider {name!r} is not in the catalog",
            {"stage": stage, "provider": name, "available": chain},
        )
    return name, entry


async def test_provider(cfg: Config, stage: str, provider: str | None = None) -> dict:
    """Run the cheapest truthful credential test for one chain entry.

    Returns ``{ok, stage, provider, message, details}`` for every outcome, including
    "no key" and "no safe online test". Bad arguments raise :class:`KeyCheckError`;
    every network/parse failure is a result with ``ok: false``, never an exception.
    """
    name, entry = _target(cfg, stage, provider)
    fields = _fields_for(stage, entry)
    values = _values_for(entry, fields)
    base_details = {
        "kind": str(entry.kind),
        "docs_url": _docs_url(stage, entry),
        "key_fields": [
            {"name": field.name, "role": field.role, "configured": bool(values[field.role])}
            for field in fields
        ],
    }

    if not fields:
        return _result(
            False, stage, name,
            "no safe online test; this provider needs no key (local or keyless provider)",
            check="none", details=base_details,
        )

    missing = [field.name for field in fields if not values[field.role]]
    if missing:
        return _result(
            False, stage, name,
            "missing credential: " + ", ".join(missing),
            check="none", details={**base_details, "missing": missing},
        )
    if not _looks_usable(values):
        return _result(
            False, stage, name,
            "no safe online test; key format looks invalid "
            "(empty, too short, or contains whitespace)",
            check="format", details=base_details,
        )

    secrets_used = [value for value in values.values() if value]
    started = time.perf_counter()
    try:
        if stage == "llm":
            probe = await _probe_llm(entry, values)
        elif stage == "tts":
            probe = await _probe_tts(entry, values)
        else:
            probe = await _probe_asr(entry, values)
    except Exception as exc:  # noqa: BLE001 - a failed probe is data, not a 500
        message = _redact(f"{type(exc).__name__}: {exc}", secrets_used)
        return _result(
            False, stage, name, f"test failed: {message}",
            check="network",
            details={**base_details, "elapsed_ms": _elapsed(started)},
        )
    elapsed = _elapsed(started)
    message = _redact(probe.get("message", ""), secrets_used)
    ok = bool(probe.get("ok"))
    if not ok:
        message = f"test failed: {message}" if message else "test failed"
    return _result(
        ok, stage, name, message,
        check=str(probe.get("check") or "online"),
        details={
            **base_details,
            "status": probe.get("status"),
            "elapsed_ms": elapsed,
            **(probe.get("details") or {}),
        },
    )


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _timeout(entry: ProviderConfig) -> float:
    return min(max(float(entry.timeout_s or 8.0), 1.0), 8.0)


async def _probe_llm(entry: ProviderConfig, values: dict[str, str]) -> dict:
    """LLM: the models list first (no tokens), then a 1-token chat as a fallback."""
    import httpx

    if not entry.base_url:
        return {"ok": False, "check": "config", "message": "base_url is empty"}
    if not entry.model:
        return {"ok": False, "check": "config", "message": "model is empty"}
    headers = {"Authorization": f"Bearer {values['api_key']}"}
    timeout = _timeout(entry)
    async with httpx.AsyncClient(timeout=timeout) as client:
        url = entry.base_url.rstrip("/") + "/models"
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            return {"ok": True, "status": 200, "check": "models",
                    "message": "models endpoint accepted the key"}
        if response.status_code in (401, 403):
            return {"ok": False, "status": response.status_code, "check": "models",
                    "message": f"provider rejected the key (HTTP {response.status_code})"}
        if response.status_code not in (404, 405):
            return {"ok": False, "status": response.status_code, "check": "models",
                    "message": f"models endpoint returned HTTP {response.status_code}"}
        # A vendor without /models: one token is the smallest real completion.
        chat_url = entry.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": entry.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
            "stream": False,
        }
        response = await client.post(chat_url, headers=headers, json=body)
        if response.status_code == 200:
            return {"ok": True, "status": 200, "check": "chat",
                    "message": "minimal chat completion accepted the key"}
        if response.status_code in (401, 403):
            return {"ok": False, "status": response.status_code, "check": "chat",
                    "message": f"provider rejected the key (HTTP {response.status_code})"}
        return {"ok": False, "status": response.status_code, "check": "chat",
                "message": f"chat endpoint returned HTTP {response.status_code}"}


async def _probe_asr(entry: ProviderConfig, values: dict[str, str]) -> dict:
    kind = _norm(entry.kind)
    if kind == "volc_asr":
        return await _probe_volc_asr(entry, values["api_key"])
    if kind == "openai_compat":
        return await _probe_openai_asr(entry, values["api_key"])
    return {"ok": False, "check": "none",
            "message": "no safe online test; key format looks valid"}


async def _probe_tts(entry: ProviderConfig, values: dict[str, str]) -> dict:
    kind = _norm(entry.kind)
    if kind == "volc_tts":
        return await _probe_volc_tts(entry, values["api_key"])
    if kind == "xfyun":
        return await _probe_xfyun_tts(
            entry, values["api_key"], values.get("app_id", ""), values.get("api_secret", ""))
    if kind == "openai_compat":
        return await _probe_openai_tts(entry, values["api_key"])
    return {"ok": False, "check": "none",
            "message": "no safe online test; key format looks valid"}


async def _probe_openai_asr(entry: ProviderConfig, api_key: str) -> dict:
    """ASR: 100 ms of silence as a WAV, so the auth path is exercised end to end."""
    import httpx

    from .openai_compat import pcm_to_wav

    if not entry.base_url or not entry.model:
        return {"ok": False, "check": "config", "message": "base_url/model is empty"}
    wav = pcm_to_wav(b"\x00\x00" * 1600, 16000)  # 100 ms, mono s16
    async with httpx.AsyncClient(timeout=_timeout(entry)) as client:
        response = await client.post(
            entry.base_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("probe.wav", wav, "audio/wav")},
            data={"model": entry.model},
        )
    if response.status_code == 200:
        return {"ok": True, "status": 200, "check": "transcription",
                "message": "transcription endpoint accepted the key (100 ms silence)"}
    if response.status_code in (401, 403):
        return {"ok": False, "status": response.status_code, "check": "transcription",
                "message": f"provider rejected the key (HTTP {response.status_code})"}
    return {"ok": False, "status": response.status_code, "check": "transcription",
            "message": f"transcription endpoint returned HTTP {response.status_code}"}


async def _probe_openai_tts(entry: ProviderConfig, api_key: str) -> dict:
    """TTS: one character, first audio chunk only."""
    import httpx

    if not entry.base_url or not entry.model:
        return {"ok": False, "check": "config", "message": "base_url/model is empty"}
    body: dict[str, Any] = {
        "model": entry.model,
        "input": "嗯",
        "response_format": "pcm",
    }
    if entry.voice:
        body["voice"] = entry.voice
    async with httpx.AsyncClient(timeout=_timeout(entry)) as client:
        async with client.stream(
            "POST", entry.base_url.rstrip("/") + "/audio/speech",
            headers={"Authorization": f"Bearer {api_key}"}, json=body,
        ) as response:
            if response.status_code == 200:
                async for chunk in response.aiter_bytes():
                    if chunk:
                        return {"ok": True, "status": 200, "check": "synthesis",
                                "message": "speech endpoint returned audio (1 character)"}
                return {"ok": False, "status": 200, "check": "synthesis",
                        "message": "speech endpoint returned 200 but no audio"}
            return {
                "ok": False, "status": response.status_code, "check": "synthesis",
                "message": (
                    f"provider rejected the key (HTTP {response.status_code})"
                    if response.status_code in (401, 403)
                    else f"speech endpoint returned HTTP {response.status_code}"
                ),
            }


async def _probe_volc_tts(entry: ProviderConfig, api_key: str) -> dict:
    """TTS: the vendor's own unidirectional endpoint with a one-character text."""
    import httpx

    from .volc_tts import DEFAULT_RESOURCE, OK, TTS_URL

    if not entry.voice:
        return {"ok": False, "check": "config",
                "message": "voice is empty; configure a 2.0 speaker id first"}
    payload = {
        "user": {"uid": "rui"},
        "req_params": {
            "text": "嗯",
            "speaker": entry.voice,
            "audio_params": {"format": "pcm", "sample_rate": 16000},
        },
    }
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": entry.model or DEFAULT_RESOURCE,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=_timeout(entry)) as client:
        async with client.stream(
            "POST", entry.base_url or TTS_URL, headers=headers, json=payload,
        ) as response:
            if response.status_code != 200:
                return {
                    "ok": False, "status": response.status_code, "check": "synthesis",
                    "message": (
                        f"provider rejected the key (HTTP {response.status_code})"
                        if response.status_code in (401, 403)
                        else f"tts endpoint returned HTTP {response.status_code}"
                    ),
                }
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                code = frame.get("code", 0)
                if code in (0, OK):
                    return {"ok": True, "status": 200, "check": "synthesis",
                            "message": "tts endpoint returned audio (1 character)"}
                return {"ok": False, "status": 200, "check": "synthesis",
                        "message": f"tts code={code} message={frame.get('message') or ''}"}
    return {"ok": False, "status": 200, "check": "synthesis",
            "message": "tts endpoint returned 200 but no frames"}


async def _probe_volc_asr(entry: ProviderConfig, api_key: str) -> dict:
    """ASR: authenticate, then send 100 ms of silence and wait for the first frame.

    The handshake alone is not proof on every gateway version, so the probe sends a
    valid config frame and a final silent audio packet. A server response means the
    key, resource id and endpoint all worked; an error frame carries the vendor code.
    """
    import websockets

    from .volc_asr import ASR_URL, DEFAULT_RESOURCE, header, parse_response

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": entry.model or DEFAULT_RESOURCE,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Connect-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
    }
    config = {
        "user": {"uid": "rui"},
        "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw"},
        "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True,
                    "result_type": "full"},
    }
    timeout = _timeout(entry)
    try:
        async with websockets.connect(
            entry.base_url or ASR_URL, additional_headers=headers, max_size=None,
            open_timeout=timeout, close_timeout=2,
        ) as ws:
            await ws.send(header(0x1, 0x0, json.dumps(config).encode()))
            await ws.send(header(0x2, 0x2, b"\x00\x00" * 1600, serialization=0))
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - websockets' exception hierarchy varies
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return {"ok": False, "status": status, "check": "asr",
                    "message": f"provider rejected the key (HTTP {status})"}
        return {"ok": False, "status": status, "check": "asr",
                "message": f"{type(exc).__name__}: {exc}"}
    if not isinstance(raw, bytes):
        return {"ok": False, "check": "asr", "message": "unexpected non-binary server frame"}
    try:
        msg_type, _flags, payload = parse_response(raw)
    except Exception as exc:  # noqa: BLE001 - a malformed frame is a failed probe
        return {"ok": False, "check": "asr",
                "message": f"malformed server frame: {type(exc).__name__}"}
    if msg_type == 0xF:
        try:
            error = json.loads(payload)
        except ValueError:
            error = {}
        status = error.get("status_code") or error.get("status")
        message = error.get("message") or error.get("error") or "vendor error"
        if status in (401, 403):
            return {"ok": False, "status": status, "check": "asr",
                    "message": f"provider rejected the key ({message})"}
        return {"ok": False, "status": status, "check": "asr",
                "message": f"asr error {status}: {message}"}
    return {"ok": True, "status": 200, "check": "asr",
            "message": "asr endpoint accepted the key and returned a response (100 ms silence)"}


async def _probe_xfyun_tts(
    entry: ProviderConfig, api_key: str, app_id: str, api_secret: str,
) -> dict:
    """TTS: signed websocket URL plus one-character request."""
    import websockets

    from .xfyun_tts import build_auth_url, request_payload

    if not app_id:
        return {"ok": False, "check": "config", "message": "app_id is empty"}
    if not entry.voice:
        return {"ok": False, "check": "config", "message": "voice is empty"}
    url = build_auth_url(api_key, api_secret)
    payload = request_payload(app_id, "嗯", entry.voice, 16000, 50)
    timeout = _timeout(entry)
    try:
        async with websockets.connect(url, open_timeout=timeout, close_timeout=2) as ws:
            await ws.send(json.dumps(payload))
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - handshake/auth failures are data
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return {"ok": False, "status": status, "check": "synthesis",
                    "message": f"provider rejected the key (HTTP {status})"}
        return {"ok": False, "status": status, "check": "synthesis",
                "message": f"{type(exc).__name__}: {exc}"}
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return {"ok": False, "check": "synthesis", "message": "unexpected non-JSON frame"}
    code = frame.get("code", 0)
    if code != 0:
        return {"ok": False, "status": 200, "check": "synthesis",
                "message": f"xfyun code={code} message={frame.get('message') or ''}"}
    data = frame.get("data") or {}
    if data.get("audio") or int(data.get("status", 1)) == 2:
        return {"ok": True, "status": 200, "check": "synthesis",
                "message": "xfyun returned audio for one character"}
    return {"ok": False, "status": 200, "check": "synthesis",
            "message": "xfyun returned code 0 but no audio"}
