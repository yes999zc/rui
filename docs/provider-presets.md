> Status: Phase 3 implementation (RUI 0.3.4). This document is the contract for the
> console's "new provider" flow. The audit's §2.2 remains the rationale; this file is
> the list of what the server actually accepts today.

# Provider presets, creation and readiness

The console used to be unable to create a provider: catalog fields only exist for
`providers.catalog` blocks already present in `config.yaml`, so the first provider had
to be hand-written YAML. Phase 3 adds a **preset registry**, a **create endpoint**, a
**draft probe**, and a **readiness endpoint**. A created provider is a normal catalog
block plus a normal chain entry — there is no second configuration system.

## Presets

Source of truth: [`src/rui/providers/presets.py`](../src/rui/providers/presets.py).
Stable ids are lowercase/kebab-case; the UI never invents a vendor.

### LLM — every preset is OpenAI-compatible

Native Anthropic Messages and other non-OpenAI LLM protocols are **not** offered:
`providers/chain.py` only builds `openai_compat` for the LLM stage. They need an
adapter first.

| id | Display name | Base URL | Default model | Key env | Probe |
|---|---|---|---|---|---|
| `openai` | OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | `OPENAI_API_KEY` | `/models` |
| `deepseek` | DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | `DEEPSEEK_API_KEY` | `/models` |
| `siliconflow` | 硅基流动 / SiliconFlow | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` | `SILICONFLOW_API_KEY` | `/models` |
| `moonshot` | 月之暗面 Kimi / Moonshot Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | `MOONSHOT_API_KEY` | `/models` |
| `zhipu` | 智谱 GLM / Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | `ZHIPU_API_KEY` | `/models` |
| `dashscope` | 阿里云百炼 / Alibaba DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `DASHSCOPE_API_KEY` | `/models` |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` | `OPENROUTER_API_KEY` | `/models` |
| `groq` | Groq | `https://api.groq.com/openai/v1` | `llama-3.1-8b-instant` | `GROQ_API_KEY` | `/models` |
| `ollama` | Ollama（本地） / Ollama (local) | `http://127.0.0.1:11434/v1` | `qwen2.5:7b` | — (keyless) | `/models` |
| `lmstudio` | LM Studio（本地） / LM Studio (local) | `http://127.0.0.1:1234/v1` | `local-model` | — (keyless) | `/models` |
| `vllm` | vLLM（自建） / vLLM (self-hosted) | `http://127.0.0.1:8000/v1` | `Qwen/Qwen2.5-7B-Instruct` | — (keyless) | `/models` |

Docker note: inside the container `127.0.0.1` is the container, not the NAS. Edit the
preset's Base URL in the create dialog to `host.docker.internal` or the host LAN IP.

### ASR / TTS

| id | Stage | Kind | Default model | Key envs | Probe |
|---|---|---|---|---|---|
| `volc-asr` | asr | `volc_asr` | `volc.seedasr.sauc.duration` | `VOLC_API_KEY` | streaming handshake |
| `local-sherpa` | asr | `sherpa` | `/data/models/sensevoice` | — (keyless; model path required) | local files |
| `volc-tts` | tts | `volc_tts` | `seed-tts-2.0` | `VOLC_API_KEY` | 1-char synthesis |
| `xfyun-tts` | tts | `xfyun` | — | `XFYUN_APPID`, `XFYUN_API_KEY`, `XFYUN_API_SECRET` | 1-char synthesis |

Custom mode covers OpenAI-compatible ASR/TTS endpoints (`/audio/transcriptions`,
`/audio/speech`). Other protocols require an adapter.

### Custom mode

Custom mode is a first-class path for any OpenAI-compatible `base_url`, including
gateways and staging proxies:

- required: `name`, `stage`, `kind`, `base_url`;
- `kind` must be buildable for the stage (`openai_compat` for LLM; `openai_compat`,
  `sherpa`, `volc_asr` for ASR; `openai_compat`, `volc_tts`, `xfyun` for TTS);
- `api_key_env` is optional (a keyless local endpoint is valid) and, when present,
  **must be uppercase `RUI_*`**. Preset credential names are fixed by the server and
  cannot be replaced by client input;
- `model` is optional in the request but an OpenAI-compatible provider without one is
  reported as `ready: false, reason: "model"` rather than pretending to work.

## API

All endpoints require the console token (`X-Auth-Token`) when `web.auth_token` is set.

### `GET /api/v1/providers/presets`

Optional query `?stage=llm|asr|tts`. No secret is read or returned.

```json
{
  "presets": [{
    "id": "deepseek",
    "stage": "llm",
    "kind": "openai_compat",
    "display_name": {"zh": "DeepSeek", "en": "DeepSeek"},
    "display_name_zh": "DeepSeek",
    "display_name_en": "DeepSeek",
    "description": {"zh": "...", "en": "..."},
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "credentials": [{"role": "api_key", "name": "DEEPSEEK_API_KEY", "required": true}],
    "required_fields": ["api_key"],
    "required_env": ["DEEPSEEK_API_KEY"],
    "model_required": false,
    "keyless": false,
    "docs_url": "https://api-docs.deepseek.com/",
    "probe": {"supported": true, "check": "models"},
    "default_name": "deepseek_llm"
  }],
  "stages": [{"stage": "llm", "kinds": ["openai_compat"], "presets": ["openai", "..."]}],
  "custom": {
    "enabled": true,
    "env_prefix": "RUI_",
    "stages": {"llm": {"kinds": ["openai_compat"], "default_kind": "openai_compat"}}
  },
  "count": 15
}
```

### `POST /api/v1/providers`

Exactly one of `preset_id` or `custom` must be present.

```json
{
  "preset_id": "deepseek",
  "keys": {"DEEPSEEK_API_KEY": "sk-..."},
  "set_primary": true
}
```

```json
{
  "custom": {
    "name": "local_gateway",
    "stage": "llm",
    "kind": "openai_compat",
    "base_url": "https://gateway.example/v1",
    "model": "house-model",
    "api_key_env": "RUI_LOCAL_GW_KEY"
  },
  "keys": {"RUI_LOCAL_GW_KEY": "sk-..."},
  "set_primary": false
}
```

Behavior:

1. re-read `config.yaml` from disk; write `providers.catalog.<name>` and append the
   provider to the stage chain (`set_primary: true` puts it first). Existing entries and
   other sections are preserved;
2. validate the whole `Config` and write through the atomic `save_config` path
   (`0600`, timestamped backup, `os.replace`);
3. store only the declared credential values in `data_dir/secrets.yaml` (`0600`);
4. return the provider summary with `ready`, `missing`, `reason` and `probe`. Values are
   never echoed, logged or written into `config.yaml`.

Response (abridged):

```json
{
  "ok": true,
  "provider": {
    "name": "deepseek_llm", "stage": "llm", "kind": "openai_compat",
    "ready": true, "missing": [], "reason": "",
    "probe": {"supported": true, "check": "models"},
    "primary": true, "chain_index": 0, "keyless": false
  },
  "stage": {"chain": ["deepseek_llm", "llm"], "primary": "deepseek_llm",
            "ready": ["deepseek_llm"], "not_ready": ["llm"], "satisfied": true},
  "restart_required": true
}
```

Error shape (`422`/`409`): `{"detail": {"code": "...", "message": "...", "details": {}}}`.
Codes: `unknown_preset`, `duplicate_provider`, `invalid_provider_name`, `invalid_stage`,
`invalid_kind`, `invalid_base_url`, `invalid_model`, `invalid_env`, `missing_env`,
`invalid_key`, `invalid_keys`, `stage_mismatch`, `invalid_request`, `invalid_config`.

### `POST /api/v1/providers/probe` (draft test)

Additive helper for the dialog's **Test connection** button. It accepts the same body
as create, builds the draft in memory, and calls the same `keycheck.test_provider`
used by the wizard's `keys.test`. It writes nothing (no config, no secret). Unsaved
values are marked `details.ephemeral: true` and are never returned.

### `GET /api/v1/providers/status`

Reads the same readiness predicate the runtime uses; no network probe is started here.

```json
{
  "providers": [{
    "name": "deepseek_llm", "stage": "llm", "kind": "openai_compat",
    "display_name": "DeepSeek", "display_name_en": "DeepSeek",
    "configured": true, "ready": true, "missing": [], "reason": "",
    "primary": true, "chain_index": 0, "in_chain": true,
    "probe": {"supported": true, "check": "models"},
    "keyless": false, "docs_url": "https://api-docs.deepseek.com/"
  }],
  "stages": {
    "llm": {
      "chain": ["deepseek_llm"], "primary": "deepseek_llm",
      "ready": ["deepseek_llm"], "not_ready": [],
      "satisfied": true, "missing": [], "providers": ["..."]
    }
  },
  "catalog": [{"name": "deepseek_llm", "kind": "openai_compat",
               "stages": ["llm"], "in_chain": true}]
}
```

Reasons are stable machine words: `""` (ready), `missing_key` (with `missing` env
names), `base_url`, `model`, `local_model`, `unknown_provider`.

## Console flow

`web/src/views/ProvidersView.vue` + `web/src/components/providers/ProviderCreateDialog.vue`:

1. **New provider** → choose LLM / ASR / TTS;
2. pick a preset card (display name, what it is, docs, required fields, probe support)
   or **Custom compatible endpoint**;
3. fill only the required fields — for a preset the credential names are fixed; for
   custom the key env must be `RUI_*`; keys are password inputs held in memory;
4. **Test connection** calls `/api/v1/providers/probe` (same probe as the wizard);
5. **Create** optionally sets primary; the page reloads catalog + status and shows
   ready/missing badges. Provider cards expose **设为主用 / 移出链路**, which edit the
   draft chain; the normal stage save applies it. Audition and the rest of the chain
   editor are unchanged.

## Compatibility and safety

- Existing `config.yaml` files are not migrated: presets only add new blocks; current
  chains keep their order until an operator changes them.
- No `/api/v1/*` path used by other views was renamed. Additive endpoints are
  `/providers/presets`, `/providers/status`, `/providers/probe` and `/providers`.
- A create request can never choose an arbitrary environment name: presets are
  server-owned, custom is `RUI_*` only. This closes the "console token exfiltrates an
  ambient secret by pointing `api_key_env` elsewhere" hole.
- Provider construction happens at startup: the create response says
  `restart_required: true`; the UI never claims a live switch.

## Tests

`tests/test_providers_presets.py` pins: registry shape and required vendors, preset and
custom creation, atomic catalog/chain writes, duplicate/unknown/bad-env 422s,
write-only secrets, readiness and reason reporting, chain append/primary, and the
draft probe writing nothing. Existing provider tests (`test_setup_api.py`,
`test_provider_chain.py`, `test_key_compat.py`) continue to cover the runtime chain.
