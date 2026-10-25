"""Provider presets for the console's "new provider" flow.

A preset is a *shape*, not a second configuration system: the create endpoint writes a
normal `providers.catalog.<name>` block plus a chain entry. Three rules are enforced here
so the console cannot turn into an arbitrary-env exfiltration tool:

* a preset owns its credential environment names; the client may only send values for
  those exact names (never a name of its choosing);
* a custom provider may only declare ``RUI_*`` environment names;
* only kinds the runtime can actually build for that stage are accepted.

Presets deliberately stay honest about protocol support: every LLM preset is
``openai_compat``. Native Anthropic Messages, non-OpenAI ASR/TTS shapes and other
protocols need an adapter first, so they are not offered as presets. Custom mode is the
first-class path for any OpenAI-compatible endpoint, including self-hosted gateways.

No secret value is accepted or returned by this module; it only names variables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "CUSTOM_ENV_PREFIX",
    "PRESETS",
    "Preset",
    "PresetCredential",
    "STAGE_KINDS",
    "custom_document",
    "document",
    "get_preset",
    "kind_allowed",
    "preset_for",
    "probe_for",
    "probe_supported",
    "valid_env_name",
    "valid_provider_name",
]

# Mirrors providers/chain.py's builders. A kind outside this table is a config error,
# not a preset we can offer.
STAGE_KINDS: dict[str, tuple[str, ...]] = {
    "llm": ("openai_compat",),
    "asr": ("openai_compat", "sherpa", "volc_asr"),
    "tts": ("openai_compat", "volc_tts", "xfyun"),
}

CUSTOM_ENV_PREFIX = "RUI_"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# Probe check per (stage, kind). "none" means there is no safe online test.
_PROBE: dict[tuple[str, str], str] = {
    ("llm", "openai_compat"): "models",
    ("asr", "openai_compat"): "transcription",
    ("asr", "volc_asr"): "stream",
    ("asr", "sherpa"): "local_model",
    ("tts", "openai_compat"): "synthesis",
    ("tts", "volc_tts"): "synthesis",
    ("tts", "xfyun"): "synthesis",
}


@dataclass(frozen=True)
class PresetCredential:
    """One write-only credential slot the preset controls."""

    role: str                 # api_key | app_id | api_secret
    env: str                  # exact environment name; never user-selectable
    label_zh: str
    label_en: str
    hint_zh: str = ""
    hint_en: str = ""

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "name": self.env,
            "label": {"zh": self.label_zh, "en": self.label_en},
            "hint": {"zh": self.hint_zh, "en": self.hint_en},
            "required": True,
        }


@dataclass(frozen=True)
class Preset:
    id: str
    stage: str
    kind: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    base_url: str = ""
    default_model: str = ""
    docs_url: str = ""
    credentials: tuple[PresetCredential, ...] = field(default_factory=tuple)
    default_voice: str = ""
    timeout_s: float = 15.0
    default_name: str = ""
    # For a local model the config field that must be filled in is a path, not a key.
    model_required: bool = False

    @property
    def keyless(self) -> bool:
        return not self.credentials

    @property
    def api_key_env(self) -> str:
        return next((item.env for item in self.credentials if item.role == "api_key"), "")

    @property
    def app_id_env(self) -> str:
        return next((item.env for item in self.credentials if item.role == "app_id"), "")

    @property
    def api_secret_env(self) -> str:
        return next((item.env for item in self.credentials if item.role == "api_secret"), "")

    @property
    def required_fields(self) -> list[str]:
        if self.model_required:
            return ["model"]
        return [item.role for item in self.credentials]

    @property
    def required_env(self) -> list[str]:
        return [item.env for item in self.credentials]

    def as_dict(self) -> dict:
        check = probe_for(self.stage, self.kind)
        return {
            "id": self.id,
            "stage": self.stage,
            "kind": self.kind,
            "display_name": {"zh": self.name_zh, "en": self.name_en},
            "display_name_zh": self.name_zh,
            "display_name_en": self.name_en,
            "description": {"zh": self.description_zh, "en": self.description_en},
            "description_zh": self.description_zh,
            "description_en": self.description_en,
            "base_url": self.base_url,
            "model": self.default_model,
            "api_key_env": self.api_key_env,
            "app_id_env": self.app_id_env,
            "api_secret_env": self.api_secret_env,
            "credentials": [item.as_dict() for item in self.credentials],
            "required_fields": self.required_fields,
            "required_env": self.required_env,
            "model_required": self.model_required,
            "keyless": self.keyless,
            "docs_url": self.docs_url,
            "probe": {
                "supported": check != "none",
                "check": check,
            },
            "default_name": self.default_name,
            "default_voice": self.default_voice,
            "timeout_s": self.timeout_s,
            "custom": False,
        }


def _key(env: str, hint_zh: str = "", hint_en: str = "") -> PresetCredential:
    return PresetCredential(
        "api_key", env, "API Key", "API Key",
        hint_zh or "密钥值本身，不是变量名；只写不读。",
        hint_en or "The key value itself, not the variable name; write-only.",
    )


def _app_id(env: str) -> PresetCredential:
    return PresetCredential(
        "app_id", env, "AppID", "AppID",
        "应用 ID（讯飞控制台「应用信息」里）。",
        "The application ID from the iFlytek console.",
    )


def _api_secret(env: str) -> PresetCredential:
    return PresetCredential(
        "api_secret", env, "API Secret", "API Secret",
        "应用密钥（讯飞控制台「应用信息」里）；只写不读。",
        "The application secret from the iFlytek console; write-only.",
    )


_PRESETS: tuple[Preset, ...] = (
    # ---------------------------------------------------------------- LLM, OpenAI-compatible
    Preset(
        id="openai",
        stage="llm",
        kind="openai_compat",
        name_zh="OpenAI",
        name_en="OpenAI",
        description_zh="OpenAI 官方接口；默认 gpt-4o-mini，适合日常语音对话。",
        description_en="OpenAI's own API; defaults to gpt-4o-mini for everyday voice chat.",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        docs_url="https://platform.openai.com/docs/api-reference",
        credentials=(_key("OPENAI_API_KEY"),),
        timeout_s=30.0,
        default_name="openai_llm",
    ),
    Preset(
        id="deepseek",
        stage="llm",
        kind="openai_compat",
        name_zh="DeepSeek",
        name_en="DeepSeek",
        description_zh="DeepSeek 官方 API；中文与工具调用表现好，价格低。",
        description_en="DeepSeek's own API; strong Chinese and tool calling at low cost.",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        docs_url="https://api-docs.deepseek.com/",
        credentials=(_key("DEEPSEEK_API_KEY"),),
        timeout_s=30.0,
        default_name="deepseek_llm",
    ),
    Preset(
        id="siliconflow",
        stage="llm",
        kind="openai_compat",
        name_zh="硅基流动 SiliconFlow",
        name_en="SiliconFlow",
        description_zh="国内 OpenAI 兼容聚合服务；模型名形如 Qwen/Qwen2.5-7B-Instruct。",
        description_en="China-based OpenAI-compatible aggregator; model ids look like "
                       "Qwen/Qwen2.5-7B-Instruct.",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-7B-Instruct",
        docs_url="https://docs.siliconflow.cn/",
        credentials=(_key("SILICONFLOW_API_KEY"),),
        default_name="siliconflow_llm",
    ),
    Preset(
        id="moonshot",
        stage="llm",
        kind="openai_compat",
        name_zh="月之暗面 Kimi",
        name_en="Moonshot Kimi",
        description_zh="Moonshot/Kimi 官方 OpenAI 兼容接口，长上下文见长。",
        description_en="Moonshot/Kimi's OpenAI-compatible API with long context.",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        docs_url="https://platform.moonshot.cn/docs/",
        credentials=(_key("MOONSHOT_API_KEY"),),
        timeout_s=30.0,
        default_name="moonshot_llm",
    ),
    Preset(
        id="zhipu",
        stage="llm",
        kind="openai_compat",
        name_zh="智谱 GLM",
        name_en="Zhipu GLM",
        description_zh="智谱开放平台 OpenAI 兼容接口；glm-4-flash 适合低成本对话。",
        description_en="Zhipu's OpenAI-compatible API; glm-4-flash is a low-cost chat model.",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        docs_url="https://docs.bigmodel.cn/",
        credentials=(_key("ZHIPU_API_KEY"),),
        timeout_s=30.0,
        default_name="zhipu_llm",
    ),
    Preset(
        id="dashscope",
        stage="llm",
        kind="openai_compat",
        name_zh="阿里云百炼 DashScope",
        name_en="Alibaba DashScope",
        description_zh="百炼的 OpenAI 兼容模式；qwen-plus 等通义模型走这一条。",
        description_en="DashScope's OpenAI-compatible mode for Qwen models such as qwen-plus.",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        docs_url="https://help.aliyun.com/zh/model-studio/developer-reference/"
                 "compatibility-of-openai-with-dashscope",
        credentials=(_key("DASHSCOPE_API_KEY"),),
        timeout_s=30.0,
        default_name="dashscope_llm",
    ),
    Preset(
        id="openrouter",
        stage="llm",
        kind="openai_compat",
        name_zh="OpenRouter",
        name_en="OpenRouter",
        description_zh="多模型聚合网关；模型名形如 openai/gpt-4o-mini。",
        description_en="Multi-vendor gateway; model ids look like openai/gpt-4o-mini.",
        base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
        docs_url="https://openrouter.ai/docs/quickstart",
        credentials=(_key("OPENROUTER_API_KEY"),),
        timeout_s=30.0,
        default_name="openrouter_llm",
    ),
    Preset(
        id="groq",
        stage="llm",
        kind="openai_compat",
        name_zh="Groq",
        name_en="Groq",
        description_zh="Groq 的 OpenAI 兼容接口，主打低延迟推理。",
        description_en="Groq's OpenAI-compatible API, focused on low-latency inference.",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.1-8b-instant",
        docs_url="https://console.groq.com/docs/openai",
        credentials=(_key("GROQ_API_KEY"),),
        timeout_s=30.0,
        default_name="groq_llm",
    ),
    Preset(
        id="ollama",
        stage="llm",
        kind="openai_compat",
        name_zh="Ollama（本地）",
        name_en="Ollama (local)",
        description_zh="本机 Ollama 的 OpenAI 兼容端点，无需密钥；容器内请把地址改成"
                       "host.docker.internal 或宿主机 IP。",
        description_en="Local Ollama's OpenAI-compatible endpoint, no key. Inside a "
                       "container change the host to host.docker.internal or the LAN IP.",
        base_url="http://127.0.0.1:11434/v1",
        default_model="qwen2.5:7b",
        docs_url="https://docs.ollama.com/api/openai-compatibility",
        default_name="ollama_llm",
    ),
    Preset(
        id="lmstudio",
        stage="llm",
        kind="openai_compat",
        name_zh="LM Studio（本地）",
        name_en="LM Studio (local)",
        description_zh="LM Studio 本地服务器的 OpenAI 兼容端点，无需密钥。",
        description_en="LM Studio's local server exposes an OpenAI-compatible endpoint, "
                       "no key required.",
        base_url="http://127.0.0.1:1234/v1",
        default_model="local-model",
        docs_url="https://lmstudio.ai/docs/app/api/endpoints/openai",
        default_name="lmstudio_llm",
    ),
    Preset(
        id="vllm",
        stage="llm",
        kind="openai_compat",
        name_zh="vLLM（自建）",
        name_en="vLLM (self-hosted)",
        description_zh="自建 vLLM 的 OpenAI 兼容服务，默认无需密钥；开启 --api-key 时请用"
                       "自定义模式填 RUI_ 变量。",
        description_en="Self-hosted vLLM's OpenAI-compatible server, keyless by default; "
                       "if started with --api-key use custom mode with a RUI_ variable.",
        base_url="http://127.0.0.1:8000/v1",
        default_model="Qwen/Qwen2.5-7B-Instruct",
        docs_url="https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
        default_name="vllm_llm",
    ),
    # ---------------------------------------------------------------- ASR
    Preset(
        id="volc-asr",
        stage="asr",
        kind="volc_asr",
        name_zh="火山引擎 · 豆包流式识别",
        name_en="Volcano Engine · Doubao ASR",
        description_zh="豆包流式语音识别 2.0；新版控制台一个 API Key，资源 ID 默认"
                       "volc.seedasr.sauc.duration（小时版）。",
        description_en="Doubao streaming ASR 2.0; one API key in the new console, "
                       "resource id defaults to volc.seedasr.sauc.duration (hourly).",
        default_model="volc.seedasr.sauc.duration",
        docs_url="https://docs.volcengine.com/docs/DoubaoVoice/"
                 "bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh",
        credentials=(_key("VOLC_API_KEY"),),
        default_name="volc_asr",
    ),
    Preset(
        id="local-sherpa",
        stage="asr",
        kind="sherpa",
        name_zh="本地 sherpa 模型（无需密钥）",
        name_en="Local sherpa model (keyless)",
        description_zh="本机 sherpa-onnx 模型，热响应约 50 ms；需要先下载模型文件。",
        description_en="Local sherpa-onnx model, ~50 ms warm; download the model files first.",
        default_model="/data/models/sensevoice",
        docs_url="https://github.com/k2-fsa/sherpa-onnx",
        model_required=True,
        default_name="local_asr",
    ),
    # ---------------------------------------------------------------- TTS
    Preset(
        id="volc-tts",
        stage="tts",
        kind="volc_tts",
        name_zh="火山引擎 · 豆包语音合成",
        name_en="Volcano Engine · Doubao TTS",
        description_zh="豆包语音合成 2.0，支持音色、语速与情感；一个 API Key。",
        description_en="Doubao TTS 2.0 with voices, speech rate and emotion; one API key.",
        default_model="seed-tts-2.0",
        default_voice="zh_female_xiaohe_uranus_bigtts",
        docs_url="https://docs.volcengine.com/docs/DoubaoVoice/"
                 "HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh",
        credentials=(_key("VOLC_API_KEY"),),
        default_name="volc_tts",
    ),
    Preset(
        id="xfyun-tts",
        stage="tts",
        kind="xfyun",
        name_zh="讯飞在线语音合成",
        name_en="iFlytek online TTS",
        description_zh="讯飞在线合成，需要 AppID + APIKey + APISecret 三项。",
        description_en="iFlytek online TTS; it needs all three: AppID, API key and API secret.",
        default_voice="x4_lingxiaowan_boy",
        docs_url="https://www.xfyun.cn/doc/tts/online_tts/API.html",
        credentials=(
            _app_id("XFYUN_APPID"),
            _key("XFYUN_API_KEY"),
            _api_secret("XFYUN_API_SECRET"),
        ),
        default_name="xfyun_tts",
    ),
)

PRESETS: tuple[Preset, ...] = _PRESETS

# Small, explicit alias table: the UI and docs use stable ids; accepting the obvious
# short spellings costs nothing and avoids a 422 for `kimi` vs `moonshot`.
_ALIASES: dict[str, str] = {
    "kimi": "moonshot",
    "moonshot-kimi": "moonshot",
    "glm": "zhipu",
    "zhipu-glm": "zhipu",
    "qwen": "dashscope",
    "dashscope-openai": "dashscope",
    "sherpa": "local-sherpa",
    "local_asr": "local-sherpa",
    "local-asr": "local-sherpa",
    "volc_asr": "volc-asr",
    "volc_tts": "volc-tts",
    "xfyun_tts": "xfyun-tts",
}


def _normalize_id(preset_id: str) -> str:
    return (preset_id or "").strip().lower().replace("_", "-")


def get_preset(preset_id: str) -> Preset | None:
    """Look up a stable preset id (aliases accepted; unknown returns None)."""
    key = _normalize_id(preset_id)
    key = _ALIASES.get(key, key)
    return next((preset for preset in PRESETS if preset.id == key), None)


def presets_for_stage(stage: str) -> list[Preset]:
    return [preset for preset in PRESETS if preset.stage == stage]


def kind_allowed(stage: str, kind: str) -> bool:
    return (kind or "").strip().lower() in STAGE_KINDS.get(stage, ())


def probe_for(stage: str, kind: str) -> str:
    """The cheapest real probe this (stage, kind) supports, or ``"none"``."""
    return _PROBE.get((stage, (kind or "").strip().lower()), "none")


def probe_supported(stage: str, kind: str) -> bool:
    return probe_for(stage, kind) != "none"


def valid_env_name(name: str, *, prefix: str = "") -> bool:
    if not name or not _ENV_RE.match(name):
        return False
    return not prefix or name.startswith(prefix)


def valid_provider_name(name: str) -> bool:
    """Catalog/chain names are lowercase and ASCII, so a typo cannot become a path."""
    return bool(name and _NAME_RE.match(name))


def _endpoint_key(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/").lower()


def preset_for(stage: str, kind: str, base_url: str) -> Preset | None:
    """Match a configured provider back to its preset, for a human display name."""
    key = _endpoint_key(base_url)
    for preset in PRESETS:
        if preset.stage != stage or preset.kind != (kind or "").strip().lower():
            continue
        if key and _endpoint_key(preset.base_url) == key:
            return preset
    return None


def custom_document(stage: str | None = None) -> dict:
    """What custom mode accepts; the UI renders this instead of hard-coding kinds."""
    stages = [stage] if stage else ["llm", "asr", "tts"]
    return {
        "enabled": True,
        "env_prefix": CUSTOM_ENV_PREFIX,
        "env_hint": {
            "zh": "自定义 provider 只能选择 RUI_ 开头的大写环境变量名。",
            "en": "A custom provider may only declare an uppercase RUI_* environment name.",
        },
        "stages": {
            name: {
                "kinds": list(STAGE_KINDS.get(name, ())),
                "default_kind": "openai_compat",
                "requires_base_url": name in ("llm", "asr", "tts"),
                "model_required": True,
            }
            for name in stages
        },
    }


def document(stage: str | None = None) -> dict:
    """The presets endpoint payload: presets plus custom-mode metadata.

    No secret value is read: credential entries only carry variable names and labels.
    """
    if stage is not None and stage not in STAGE_KINDS:
        raise ValueError(f"unknown stage: {stage!r}")
    shown = presets_for_stage(stage) if stage else list(PRESETS)
    return {
        "presets": [preset.as_dict() for preset in shown],
        "stages": [
            {
                "stage": name,
                "kinds": list(STAGE_KINDS.get(name, ())),
                "presets": [preset.id for preset in presets_for_stage(name)],
            }
            for name in (["llm", "asr", "tts"] if stage is None else [stage])
        ],
        "custom": custom_document(stage),
        "count": len(shown),
    }
