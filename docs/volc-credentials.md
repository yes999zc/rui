# 火山引擎（豆包语音）凭证：RUI 该问什么、怎么验

> 调研日期 2026-10-24。结论先行：**新版控制台的语音 ASR/TTS 都只需要一个 `VOLC_API_KEY`（请求头 `X-Api-Key`），不需要 AppID、Access Token 或 AK/SK**。AK/SK 只用于火山 OpenAPI 的 V4 签名（控制台额度/用量卡片），不参与语音识别/合成。

## 1. 官方文档怎么说

| 产品 | 鉴权方式 | 资源 ID | 官方文档 |
|---|---|---|---|
| 豆包流式语音识别 2.0（ASR，WebSocket） | `X-Api-Key` **必选**，从「控制台 → API Key 管理」获取；文档同时说明兼容旧版控制台鉴权 | `X-Api-Resource-Id`：`volc.seedasr.sauc.duration`（小时版）/ `volc.seedasr.sauc.concurrent`（并发版） | [实时语音识别](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh) |
| 豆包语音合成 2.0（TTS，HTTP Chunked/SSE 单向流） | 新版控制台 `X-Api-Key` **必选**；旧版控制台用 `X-Api-App-Id` + `X-Api-Access-Key` | `X-Api-Resource-Id`：`seed-tts-2.0`（2.0 音色+字符版计费）/ `seed-tts-1.0` / `seed-tts-1.0-concurr` | [HTTP Chunked/SSE 单向流式-V3](https://docs.volcengine.com/docs/DoubaoVoice/HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh) |
| API Key 管理 | 控制台可见/禁用/删除；**“在任意接口中，填入 header 即可，不用填写 appid”** | — | [API Key 使用](https://docs.volcengine.com/docs/DoubaoVoice/APIKeyUsage?lang=zh) |
| OpenAPI 签名（AK/SK） | `Access Key ID` + `Secret Access Key` 做 HMAC-SHA256/V4 签名，签名的是管理/计费类 OpenAPI | — | [为什么要进行签名](https://docs.volcengine.com/docs/6369/67269?lang=zh) |

关键原文（TTS 文档“鉴权 Request Headers”）：

- 新版控制台：`X-Api-Key` = “使用火山引擎控制台获取的API Key”，**必须**；`X-Api-Resource-Id` **必须**。
- 旧版控制台：`X-Api-App-Id`（APP ID）+ `X-Api-Access-Key`（Access Token）**必须**；文档明确写“新版控制台只需要 X-Api-Key 即可”。

ASR 文档同样把 `X-Api-Key` 列为必选，并注明“同时支持旧版控制台的鉴权方式”；旧版示例使用 App Key / Access Key，但 RUI 目前的 `volc_asr` 实现只发送 `X-Api-Key`。

## 2. 三种凭证到底各管什么

| 凭证 | 管什么 | RUI 需要吗 |
|---|---|---|
| **`VOLC_API_KEY`**（新版控制台 API Key） | 豆包语音 ASR/TTS 的请求鉴权；一个 Key 可同时用于已开通的 ASR 和 TTS | **是**，ASR 和 TTS 各一行，值可以相同 |
| **AppID + Access Token**（旧版控制台） | 旧版语音接口的鉴权 | **不需要**；现有 provider 不发这两个头。旧账号应新建 API Key 迁移 |
| **AK/SK**（`VOLC_ACCESS_KEY_ID` / `VOLC_SECRET_ACCESS_KEY`） | 火山 OpenAPI V4 签名：查余额、查用量、资源包、API Key 管理等 | **不用于 ASR/TTS**；仅控制台额度卡片可选 |

注意：API Key **不是** AK/SK 的简写，两者不能互换；把 AK/SK 填进 `VOLC_API_KEY` 会在语音接口直接 401。

## 3. RUI 推荐字段 schema

### 3.1 语音链路（向导 keys 步骤：每种用途选一个 provider）

keys 步骤现在是 **provider 表**：LLM / ASR / TTS 各一个下拉，选中火山后只显示下面这一行，不再同时铺开多个 provider 的字段。ASR 与 TTS 可以各选一次火山，也可以只选其中一个。

| 字段（secrets/env 名） | 适用 stage | 人类标签 | 必填 | 说明 |
|---|---|---|---|---|
| `VOLC_API_KEY` | `asr` | API Key | 是（ASR 选火山时） | 请求头 `X-Api-Key`；控制台 API Key 管理创建 |
| `VOLC_API_KEY` | `tts` | API Key | 是（TTS 选火山时） | 同一个 Key 可复用；两个 use 都选火山时在各自下拉里保存 |
| （非 secret，config）`providers.catalog.<name>.model` | `asr` / `tts` | 资源 ID | 是 | ASR：`volc.seedasr.sauc.duration`；TTS：`seed-tts-2.0` |
| （非 secret，config）`providers.catalog.volc_tts.voice` | `tts` | 音色 | 是 | `providers.save` 的 `tts.voice` 写入；默认 `zh_female_xiaohe_uranus_bigtts` |

不向用户索要：`AppID`、`Access Token`、`Secret Key`（语音链路）。选火山后 TTS 下拉的音色来自 `providers/volc_voices.py` 的 2.0 音色表；讯飞则只展示其配置里的 `voice`（vcn）。

### 3.2 控制台额度卡片（可选，与语音链路分开）

| 字段 | 用途 | 必填 |
|---|---|---|
| `VOLC_ACCESS_KEY_ID` | 火山 OpenAPI V4 签名 | 否；只影响额度/用量卡片 |
| `VOLC_SECRET_ACCESS_KEY` | 同上 | 否；只影响额度/用量卡片 |

### 3.3 旧版兼容（只读回退，不主动索取）

如果旧部署的 `secrets.yaml` 里已有 `VOLC_APP_ID` / `VOLC_ACCESS_TOKEN`，可以保留，但当前 provider 不会发送它们；向导不应把它们列为必填。迁移动作是“去新版控制台创建 API Key → 填 `VOLC_API_KEY` → 跑一次 `keys.test`”。

## 4. 怎么烟测（`POST /api/v1/setup/step`，`step=keys`, `action=keys.test`）

| stage | 最便宜的诚实检查 | 成本 |
|---|---|---|
| `llm` | `GET {base_url}/models`（Bearer）；不支持 `/models` 的厂商回退到 1-token chat completion | 0 token / 1 token |
| `asr`（火山） | 用同一组 `X-Api-Key`/`X-Api-Resource-Id` 建立 WebSocket，发 config + 100 ms 静音末包，等第一个服务端帧 | ~0.1 s 音频 |
| `asr`（OpenAI 兼容） | `POST /audio/transcriptions`，100 ms 静音 WAV | 1 次短请求 |
| `tts`（火山） | `POST /v3/tts/unidirectional`，文本「嗯」，读第一帧 | 1 字符 |
| `tts`（讯飞） | 签名 WebSocket + 单字请求，读第一帧 | 1 字符 |
| `tts`（OpenAI 兼容） | `POST /audio/speech`，文本「嗯」，读第一块 | 1 字符 |

返回固定 `{ok, stage, provider, message, details}`；401/403 归为凭据失败，超时/网络错误归为网络失败。本地 sherpa 不做在线调用：模型目录含 `tokens.txt` + `model.int8.onnx`/`model.onnx` 时返回 `ok: true` + “local model …”，缺文件时返回 `ok: false` + “local model files are missing…”，不会假装在线通过。

`keys.test` 还接受 `keys: {ENV: value}` 临时值：只用于本次探测（覆盖已保存值），不写 `secrets.yaml`、不回显、不写入 `last_test`；响应带 `details.ephemeral=true` / `details.saved=false`，`message` 末尾注明 “not saved”。不传 `keys` 时仍只测已保存值。

## 5. 兼容性与迁移

- **现有单 Key 配置继续有效**：`VOLC_API_KEY` + `providers.catalog.volc_asr/volc_tts` 不需要改。RUI 0.2.0 的 provider 只读配置里的 `api_key_env`（模板里写的是 `VOLC_API_KEY`），发送 `X-Api-Key`。
- **AK/SK 继续只服务额度卡片**：`VOLC_ACCESS_KEY_ID` / `VOLC_SECRET_ACCESS_KEY` 仍由 `billing.py` 使用；不把它们提升为语音链路的必填项。
- **旧版 AppID/Access Token 不被当前代码使用**：如果用户只有旧版凭证，`keys.test` 会真实返回 401，而不是误报成功；迁移路径是新建新版 API Key。
- **轮换**：控制台禁用/删除旧 Key → 在「密钥」页保存新 `VOLC_API_KEY` → `keys.test` 通过后再删除旧值。密钥只写不读，响应/日志不出现值。
- **同一 Key 两个 stage**：ASR 和 TTS 若都选火山，两张下拉里都填 `VOLC_API_KEY`（同一个值）即可；`providers.save` 会把各自 `providers.<stage>.chain` 写成 `[volc_*]`。保存一次后 `configured` 对两个 use 都变绿；测试时按 stage 分别调用各自端点，避免“TTS 通过就以为 ASR 也通过”。
- **链内任一 provider 完成度**：每个 use 只要链中**任一** provider 可用即可（不再要求 `chain[0]`）。全新的“讯飞在前、火山在后”配置里，只填 `VOLC_API_KEY` 也能让 TTS 变绿；`stage_state.selected` 显示第一个可用 provider，`chain` 原样保留。没选中的 provider（如 fallback 的讯飞/火山另一条）缺 key 不会让 keys 步骤卡住；这取代了“多 provider 链要求全部配置”的旧行为。

## 6. 参考链接

- [实时语音识别（ASR 2.0，WebSocket）](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket?lang=zh)
- [HTTP Chunked/SSE 单向流式-V3（TTS 2.0）](https://docs.volcengine.com/docs/DoubaoVoice/HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh)
- [API Key 使用（新版控制台，无需 AppID）](https://docs.volcengine.com/docs/DoubaoVoice/APIKeyUsage?lang=zh)
- [API Key 管理控制台](https://console.volcengine.com/speech/new/setting/apikeys?projectName=default)
- [火山 OpenAPI 签名（AK/SK 的用途）](https://docs.volcengine.com/docs/6369/67269?lang=zh)
