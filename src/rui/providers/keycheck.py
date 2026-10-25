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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import secrets
from ..config.schema import STAGES, Config, ProviderConfig, ProvidersConfig
from .volc_voices import VOLC_TTS_VOICES

__all__ = [
    "KeyCheckError",
    "credential_names",
    "provider_catalog",
    "provider_docs_url",
    "provider_entries",
    "provider_readiness",
    "provider_ready",
    "sherpa_model_ready",
    "stage_provider_names",
    "stage_satisfied",
    "stage_state",
    "test_provider",
    "voice_options",
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


# Which provider kind can serve which stage, mirroring providers/chain.py. The wizard
# uses this to reject a provider in the wrong use (e.g. an LLM entry as ASR).
_STAGE_KINDS: dict[str, frozenset[str]] = {
    "asr": frozenset({"openai_compat", "sherpa", "volc_asr"}),
    "tts": frozenset({"openai_compat", "xfyun", "volc_tts"}),
    "llm": frozenset({"openai_compat"}),
}

# The only kind that can serve more than one stage. A name without a stage suffix is
# ambiguous for it, so it is offered only in the chain it already belongs to.
_MULTI_STAGE_KIND = "openai_compat"


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
    # The keys step is a provider table: one row per selectable provider, with the
    # configured chain's primary marked selected. Values are never carried here.
    selected: bool = False
    available: bool = True
    required: bool = True
    # Whether the runtime chain would keep this provider. `configured` only says the
    # declared env values exist; `ready` also covers a local model / missing base_url.
    ready: bool = False
    ready_reason: str = ""
    voices: list[dict] = field(default_factory=list)
    default_voice: str = ""

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
            "selected": self.selected,
            "available": self.available,
            "required": self.required,
            "ready": self.ready,
            "ready_reason": self.ready_reason,
            "voices": list(self.voices),
            "default_voice": self.default_voice,
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


def provider_docs_url(stage: str, provider: ProviderConfig) -> str:
    """Public docs link for a configured provider (never a credential value)."""
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


def _stage_hint(name: str) -> str:
    """The stage a provider name advertises (`volc_asr` -> asr, `llm` -> llm)."""
    low = _norm(name)
    for stage in STAGES:
        if low == stage or low.endswith(f"_{stage}") or low.endswith(f"-{stage}"):
            return stage
    return ""


def serves_stage(provider: ProviderConfig, stage: str) -> bool:
    """Whether this provider kind can actually build the stage (see providers/chain.py)."""
    return _norm(provider.kind) in _STAGE_KINDS.get(stage, frozenset())


def stage_provider_names(cfg: Config, stage: str) -> list[str]:
    """Providers the keys table offers for one use, chain entries first.

    A chain entry is always offered (it is what a legacy config already runs). Other
    catalog entries are offered only when their kind can serve the stage and their name
    says which stage they are for; an un-suffixed ``openai_compat`` entry is ambiguous
    and stays in the chain it was configured for.
    """
    chain = [name for name in getattr(cfg.providers, stage).chain
             if name in cfg.providers.catalog]
    offered = list(dict.fromkeys(chain))
    for name, provider in cfg.providers.catalog.items():
        if name in offered or not serves_stage(provider, stage):
            continue
        hint = _stage_hint(name)
        if hint == stage or (not hint and _norm(provider.kind) != _MULTI_STAGE_KIND):
            offered.append(name)
    return offered


def _providers_of(cfg: Config | ProvidersConfig) -> ProvidersConfig:
    """Accept either a full Config or its providers section (the runtime has the latter)."""
    value = getattr(cfg, "providers", None)
    return value if isinstance(value, ProvidersConfig) else cfg  # type: ignore[return-value]


def credential_names(
    cfg: Config | ProvidersConfig, stage: str, name: str,
) -> list[str]:
    """The env names this provider declares as fields (empty = keyless provider)."""
    provider = _providers_of(cfg).catalog.get(name)
    if provider is None:
        return []
    return [str(item) for item in (
        getattr(provider, "api_key_env", ""),
        getattr(provider, "app_id_env", ""),
        getattr(provider, "api_secret_env", ""),
    ) if item]


def voice_options(stage: str, provider: ProviderConfig) -> list[dict]:
    """Voice choices for a TTS provider: [{id,name,name_i18n,language,default}].

    Volcano 2.0 uses the curated `volc_voices` directory; every other vendor exposes
    the voice its config already names (讯飞 has no subset here). A configured voice
    that is not in the curated list is kept as an option so saving another field cannot
    silently reset it.
    """
    if stage != "tts":
        return []
    kind = _norm(provider.kind)
    options: list[dict] = []
    if kind == "volc_tts":
        for voice_id, label in VOLC_TTS_VOICES.items():
            options.append({
                "id": voice_id,
                "name": label["en"],
                "name_i18n": {"zh": label["zh"], "en": label["en"]},
                "language": "zh-CN",
                "default": False,
            })
    current = str(getattr(provider, "voice", "") or "").strip()
    if not current and kind == "xfyun":
        current = str(getattr(provider, "model", "") or "").strip()
    known = {item["id"] for item in options}
    if current and current not in known:
        options.append({
            "id": current,
            "name": current,
            "name_i18n": {"zh": current, "en": current},
            "language": "zh-CN" if kind in ("volc_tts", "xfyun") else "",
            "default": False,
        })
        known.add(current)
    default_voice = current if current in known else (options[0]["id"] if options else "")
    for item in options:
        item["default"] = item["id"] == default_voice
    return options


def _entry(stage: str, name: str, provider: ProviderConfig, cfg: Config) -> ProviderEntry:
    voices = voice_options(stage, provider)
    default_voice = next((item["id"] for item in voices if item["default"]), "")
    zh, en = display_names(name, provider)
    ready, _missing, ready_reason = provider_readiness(cfg, stage, name)
    return ProviderEntry(
        stage=stage,
        name=name,
        kind=str(provider.kind),
        display_name=zh,
        display_name_en=en,
        docs_url=_docs_url(stage, provider),
        hint=_provider_hint(stage, provider),
        fields=_fields_for(stage, provider),
        selected=_first_ready(cfg, stage) == name,
        available=serves_stage(provider, stage),
        required=True,
        ready=ready,
        ready_reason=ready_reason,
        voices=voices,
        default_voice=default_voice,
    )


def sherpa_model_ready(model: str) -> bool:
    """The files a local sherpa ASR needs before it can answer at all."""
    text = str(model or "").strip()
    if not text:
        return False
    directory = Path(text).expanduser()
    return bool(
        directory.is_dir()
        and (directory / "tokens.txt").is_file()
        and any((directory / name).is_file()
                for name in ("model.int8.onnx", "model.onnx"))
    )


def provider_readiness(
    cfg: Config | ProvidersConfig, stage: str, name: str,
) -> tuple[bool, list[str], str]:
    """(usable, missing env names, reason) for one chain entry.

    A provider is usable when every credential it declares resolves *and* it can
    actually run: a local model's files exist, a built-in vendor endpoint needs no
    ``base_url``, and an OpenAI-compatible entry needs its endpoint and model configured.

    ``reason`` is a stable machine word rather than prose, so the console can badge a
    provider without parsing a message: ``""`` (ready), ``missing_key``, ``local_model``,
    ``base_url``, ``model`` or ``unknown_provider``.
    """
    providers = _providers_of(cfg)
    provider = providers.catalog.get(name)
    if provider is None:
        return False, [], "unknown_provider"
    names = credential_names(providers, stage, name)
    missing = [item for item in names if secrets.resolve(item) is None]
    if missing:
        return False, missing, "missing_key"
    kind = _norm(provider.kind)
    if kind == "sherpa":
        ready = sherpa_model_ready(str(provider.model or ""))
        return ready, [], "" if ready else "local_model"
    if kind in ("volc_asr", "volc_tts", "xfyun"):
        return True, [], ""
    if not str(provider.base_url or "").strip():
        return False, [], "base_url"
    if kind == "openai_compat" and not str(provider.model or "").strip():
        # Creating a provider from a preset should leave no half-working entry: an
        # OpenAI-compatible endpoint without a model fails the first real call.
        return False, [], "model"
    return True, [], ""


def provider_ready(
    cfg: Config | ProvidersConfig, stage: str, name: str,
) -> tuple[bool, list[str]]:
    """(usable, missing env names) for one chain entry; see :func:`provider_readiness`."""
    ready, missing, _reason = provider_readiness(cfg, stage, name)
    return ready, missing


def _first_ready(cfg: Config, stage: str) -> str:
    """The first usable provider in the chain, else the configured primary."""
    chain = list(getattr(cfg.providers, stage).chain)
    for name in chain:
        ready, _missing = provider_ready(cfg, stage, name)
        if ready:
            return name
    return chain[0] if chain else ""


def stage_satisfied(cfg: Config, stage: str) -> tuple[bool, list[str]]:
    """(satisfied, missing env names) for *any* provider in a stage's chain.

    A stage is satisfied by one usable provider, not necessarily ``chain[0]``: a fresh
    install may keep 讯飞 first while only the 火山 fallback has a key. A keyless
    provider (sherpa) counts only when its model files are present. When nothing is
    usable, ``missing`` lists every credential that would make one of them usable.
    """
    chain = list(getattr(cfg.providers, stage).chain)
    missing_all: list[str] = []
    for name in chain:
        ready, missing = provider_ready(cfg, stage, name)
        if ready:
            return True, []
        missing_all.extend(missing)
    return False, list(dict.fromkeys(missing_all))


def stage_state(cfg: Config) -> dict[str, dict]:
    """Per-use selection: the usable provider, the full chain, and what is offered."""
    out: dict[str, dict] = {}
    for stage in STAGES:
        chain = list(getattr(cfg.providers, stage).chain)
        selected = _first_ready(cfg, stage)
        satisfied, missing = stage_satisfied(cfg, stage)
        readiness = {
            name: provider_readiness(cfg, stage, name)[0] for name in chain
        }
        out[stage] = {
            "selected": selected,
            "selected_id": f"{stage}:{selected}" if selected else "",
            "chain": chain,
            "required": True,
            "providers": stage_provider_names(cfg, stage),
            "satisfied": satisfied,
            "missing": missing,
            # Runtime starts only these; the rest stay in `chain` as configured intent
            # and are skipped (see providers/chain.py). Additive: badges read this.
            "ready": [name for name in chain if readiness.get(name)],
            "not_ready": [name for name in chain if not readiness.get(name)],
        }
    return out


def provider_entries(cfg: Config) -> list[ProviderEntry]:
    """One entry per (stage, provider) actually used by a configured chain."""
    entries: list[ProviderEntry] = []
    for stage in STAGES:
        for name in getattr(cfg.providers, stage).chain:
            provider = cfg.providers.catalog.get(name)
            if provider is None:
                continue
            entries.append(_entry(stage, name, provider, cfg))
    return entries


def provider_catalog(cfg: Config) -> dict:
    """The keys step's render model: one row per selectable provider, never a value."""
    entries: list[ProviderEntry] = []
    for stage in STAGES:
        for name in stage_provider_names(cfg, stage):
            provider = cfg.providers.catalog.get(name)
            if provider is None:
                continue
            entries.append(_entry(stage, name, provider, cfg))
    rows = [entry.as_dict() for entry in entries]
    return {
        "providers": rows,
        "stages": {
            stage: [row["id"] for row in rows if row["stage"] == stage]
            for stage in STAGES
        },
        "stage_state": stage_state(cfg),
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


def _values_for(
    provider: ProviderConfig,
    fields: list[KeyField],
    ephemeral: dict[str, str] | None = None,
) -> dict[str, str]:
    """Effective values for one probe: unsaved overrides first, then saved secrets.

    The returned dict (and an ephemeral payload) is only ever used for the probe; it is
    never written to disk or included in a result.
    """
    supplied = {str(name): str(value) for name, value in (ephemeral or {}).items()}
    allowed = {item.name for item in fields}
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise KeyCheckError(
            "invalid_values",
            "unsaved test values must be credentials of this provider: "
            + ", ".join(unknown),
            {"fields": sorted(allowed), "unknown": unknown},
        )
    values: dict[str, str] = {}
    for item in fields:
        if item.name in supplied:
            values[item.role] = supplied[item.name]
            continue
        resolution = secrets.resolve(item.name)
        values[item.role] = resolution.value if resolution else ""
    return values


def _note_ephemeral(result: dict, ephemeral: dict[str, str] | None) -> dict:
    """Mark an unsaved test so the response can say "not saved" without a value."""
    if not ephemeral:
        return result
    details = dict(result.get("details") or {})
    details["ephemeral"] = True
    details["saved"] = False
    message = str(result.get("message") or "")
    return {
        **result,
        "message": f"{message} (tested with unsaved values; not saved)",
        "details": details,
    }


def _target(cfg: Config, stage: str, provider: str | None) -> tuple[str, ProviderConfig]:
    if stage not in STAGES:
        raise KeyCheckError(
            "unknown_stage", f"no such stage: {stage!r}", {"stage": stage, "stages": list(STAGES)}
        )
    chain = list(getattr(cfg.providers, stage).chain)
    offered = stage_provider_names(cfg, stage)
    name = (provider or "").strip() or (chain[0] if chain else "")
    # A provider the table offers may be tested before it is selected in the chain;
    # whether the value comes from secrets or an unsaved payload is the caller's choice.
    if name not in offered:
        raise KeyCheckError(
            "unknown_provider",
            f"provider {name!r} is not available for {stage}",
            {"stage": stage, "provider": name, "available": offered},
        )
    entry = cfg.providers.catalog.get(name)
    if entry is None:  # schema validation already rejects this; keep the API honest.
        raise KeyCheckError(
            "unknown_provider", f"provider {name!r} is not in the catalog",
            {"stage": stage, "provider": name, "available": offered},
        )
    return name, entry


async def test_provider(
    cfg: Config,
    stage: str,
    provider: str | None = None,
    *,
    keys: dict[str, str] | None = None,
) -> dict:
    """Run the cheapest truthful credential test for one provider.

    ``keys`` carries unsaved values for the probe only (write-only semantics still
    hold: they are neither persisted nor echoed). Returns
    ``{ok, stage, provider, message, details}`` for every outcome, including "no key"
    and "no safe online test". Bad arguments raise :class:`KeyCheckError`; every
    network/parse failure is a result with ``ok: false``, never an exception.
    """
    name, entry = _target(cfg, stage, provider)
    fields = _fields_for(stage, entry)
    values = _values_for(entry, fields, keys)
    base_details = {
        "kind": str(entry.kind),
        "docs_url": _docs_url(stage, entry),
        "key_fields": [
            {"name": field.name, "role": field.role, "configured": bool(values[field.role])}
            for field in fields
        ],
    }

    if not fields:
        if _norm(entry.kind) == "sherpa":
            model = str(entry.model or "")
            ready = sherpa_model_ready(model)
            details = {**base_details, "model_dir": model, "models_ready": ready}
            return _note_ephemeral(_result(
                ready, stage, name,
                ("local model files are present; no online credential required"
                 if ready else
                 "local model files are missing; fetch the models first (models step)"),
                check="local_model", details=details,
            ), keys)
        return _note_ephemeral(_result(
            False, stage, name,
            "no safe online test; this keyless provider is not a local model",
            check="none", details=base_details,
        ), keys)

    missing = [field.name for field in fields if not values[field.role]]
    if missing:
        return _note_ephemeral(_result(
            False, stage, name,
            "missing credential: " + ", ".join(missing),
            check="none", details={**base_details, "missing": missing},
        ), keys)
    if not _looks_usable(values):
        return _note_ephemeral(_result(
            False, stage, name,
            "no safe online test; key format looks invalid "
            "(empty, too short, or contains whitespace)",
            check="format", details=base_details,
        ), keys)

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
        return _note_ephemeral(_result(
            False, stage, name, f"test failed: {message}",
            check="network",
            details={**base_details, "elapsed_ms": _elapsed(started)},
        ), keys)
    elapsed = _elapsed(started)
    message = _redact(probe.get("message", ""), secrets_used)
    ok = bool(probe.get("ok"))
    if not ok:
        message = f"test failed: {message}" if message else "test failed"
    return _note_ephemeral(_result(
        ok, stage, name, message,
        check=str(probe.get("check") or "online"),
        details={
            **base_details,
            "status": probe.get("status"),
            "elapsed_ms": elapsed,
            **(probe.get("details") or {}),
        },
    ), keys)


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
