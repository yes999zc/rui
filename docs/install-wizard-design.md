# 安装向导设计（P2.2）

> Implementation note (0.2.2): 实际端点是 `GET/POST /api/v1/setup/step`、`GET /api/v1/setup/providers`；
> 文档里的 `/setup/try`、`/setup/test-connection` 未按独立端点实现，prepare/verify 确认持久化在 `data/setup.json`，
> verify 跳过目前仅前端语义。


> 状态：**设计稿，待评审（2026-10-24）**。在现有 `SetupView` 上增量实现；不建 DB、不做设备端自动化、不加载第三方可执行代码。
> 输入：`docs/device-management-research.md` §3、`docs/socket-plug.md` v0、`docs/ROADMAP.md` §3 P2.2。
> 契约：manifest 以 socket-plug 的 **`rui-plugin/v0` / `rui-device/v0`** 为准；调研稿的 `rui-plug/v1`、`manifest`、`requires_socket`、JSON Schema 形式的 `params`、`setup.slots` **已被 v0 取代**。

## 1. 目标与非目标

### 1.1 目标

1. 陌生人从 `install.sh` 之后，在浏览器完成：key → 模型 → 选机型 → 设备侧准备 → 参数 → 唤醒词 → 接线 → 测试 → 第一句。
2. **通用步 + 插头步**：RUI 固定骨架；设备侧说明、参数表单、连接测试方式来自 manifest。
3. 完成度只由服务端证据计算，不信任前端 cookie、按钮或 manifest 自述。
4. 每一步显示“用户做什么 / RUI 做什么”；失败有稳定错误码、人话 hint、可复制诊断。
5. 刷新/重启/重复提交不产生第二台设备或第二份配置；已完成步骤可重跑。
6. 1–2 天可落地：复用 `_wizard_state()`、`_write_devices()`、`secrets.put()`、`device_connect()`，只加薄层。

### 1.2 非目标

| 不做 | 原因 / 去向 |
|---|---|
| USB/ADB/TWRP/刷机/重启设备；设备端命令执行 | RUI 不接触设备；B 类只给链接、版本、回滚说明；manifest 只读 |
| 第三方 adapter 代码 / sidecar / 插件市场 | 0.2 只加载 `builtin`/`trusted` manifest；P3 再评估 |
| 多设备仲裁、approve 队列、OTA、每设备证书 | P3；本切片单用户、单音频面、单设备 |
| SQLite / 新数据库；自动生成/轮换 device token | 沿用 `config.yaml` + `secrets.yaml` + `keywords.txt`；token 仍由 `install.sh` 写 `.env` |

## 2. 八步模型

8 个编号步骤；**插头选择在页头，不编号**，因为它会改变 `prepare` 之后的步骤。

| # | step id | 类型 | 一句话 | 位置理由 |
|---|---|---|---|---|
| 0 | `keys` | 通用 | 每种用途（LLM/ASR/TTS）选一个 provider 并填它的凭据 | 没有 key，真实 turn 无法验证 |
| 1 | `models` | 通用 | ASR/VAD/KWS 文件齐全 | 只检查，不代下；下载走 `fetch-models` |
| 2 | `prepare` | 插头 | 用户在音箱上装 client/固件 | 先让设备具备拨入/被拨能力 |
| 3 | `params` | 插头 | 地址、房间、manifest 参数 | 先写 `devices[]`，后面才有设备 id |
| 4 | `wake` | 通用 | 确认 1–3 个唤醒词 | **放在连接测试前**：写 `keywords.txt` 会重启音频面，避免冲掉 `speaker_connected` |
| 5 | `connect` | 通用 | 把 ws 地址/token 填进设备 | inbound 才需要；outbound 自动跳过 |
| 6 | `test` | 插头 | 连接测试 | 用连接证据判定准备/接线是否成立 |
| 7 | `verify` | 通用 | 说第一句，听到回答 | 可选（`required=false`），不阻塞“装好了” |

> 与 socket-plug §6 的 0–8 骨架对应（行号为 socket-plug 表）：0 预检并入 `models`/页头；3 选插头变页头；5 参数拆成 `params` + `wake`；6 接线 = `connect`；7 验证拆成 `test` + `verify`；8 保存是写入副作用。顺序与调研稿的 `test → wake` 不同，理由见上表。

插头选择：`GET /setup` 不带 `plug` 时，有设备取 `devices[0].adapter` 反查 manifest，无设备默认 `xiaoai`；只列 `available=true`；`devices.length > 0` 时下拉禁用，换机型走设备管理。

## 3. 服务端状态模型

### 3.1 状态

每次 `GET /api/v1/setup` 重算，**不持久化游标**。`status` 五种：

| status | 含义 | 何时出现 |
|---|---|---|
| `pending` | 未满足证据，也未被跳过 | 初始、前置未完成、证据消失 |
| `running` | 该步动作正在执行 | `POST /setup/step` 持有内存锁期间 |
| `done` | 证据成立 | 见 §3.2 |
| `error` | 最近动作失败，或证据自相矛盾（如 `plane_error`） | 内存中的 `last_error` |
| `skipped` | 可选步被跳过，或机型不需要（outbound `connect`、`artifact:none` 的 `prepare`） | 显式/规则自动 |

全局：`next_step`（第一个非 done 的 required 步）、`complete`（required 全 done/skipped）、`blocked`（插头不可用原因）、`plane_error`。优先级：`done`（证据）> `running` > `error` > `skipped` > `pending`；证据已成立的步骤不会因上次失败显示失败。

### 3.2 每步完成证据

| step | 服务端证据 | 失败 `code` |
|---|---|---|
| `keys` | 每种用途的 **任一** provider（按链顺序，不要求 `chain[0]`）可用：声明的必填 secret 都能解析，且 provider 真能运行（本地 sherpa 需要模型文件；OpenAI 兼容 provider 需要 `base_url`；火山/讯飞内置端点）。`stage_state.selected` 取链中第一个可用 provider，全部不可用时回退 `chain[0]`。LLM 必填；ASR/TTS 各由一个 provider 满足 | `missing_keys` |
| `models` | ASR 有 `tokens.txt` + `model.int8.onnx`/`model.onnx`；VAD 文件存在；KWS 四件套齐（`app.py` L3084-3160、`audio/kws.py` L93-104） | `missing_models` |
| `prepare` | A：内存 `manual.confirm`，或同插头 `test` 已过；B 额外要 `rollback_ack=true`；`artifact=none` 自动 skipped | `prepare_not_confirmed` / `rollback_not_acknowledged` |
| `params` | `devices[]` 有目标设备；`adapter` 与 manifest 一致；`host` 非空；required 参数齐；`enabled=true`；且 `plane_error==""` | `invalid_params` / `plane_restart_failed` |
| `wake` | `wake_words` 非空；`keywords.txt` 存在且含每个词的 `@<word>` 行（`app.py` L888-909）；`plane_error==""` | `keywords_missing` |
| `connect` | inbound：`configured=true`（`secrets.has(token_env)` 或 `allow_no_token`）且 `listen_port` 有效；outbound：skipped | `device_token_missing` |
| `test` | inbound：`plane.speaker_connected==true` 且 `plane_error==""`；outbound：`adapter.probe()` 成功且 registry 有该 adapter | `device_not_connected` / `adapter_unavailable` |
| `verify` | `app.state.first_answer` 存在且 `audio_bytes>0`；只由 `POST /setup/try` 写入 | `no_audio` |

key 存在 ≠ 链路可用：schema 默认 provider 无 endpoint 时仍算未完成（`test_keys_are_stored_but_never_echoed` 已固定）。本地 sherpa 的“可用”指 `tokens.txt` + `model.int8.onnx`/`model.onnx` 存在；`keys.test` 据此给中性/绿色结果。`connect` 只查 token 是否配置，不假装知道设备端状态；真正证据是 `test`，且 inbound 只认 `speaker_connected`。`verify` 可选，`first_answer` 在内存，重启回 pending（§9）。

keys 步骤是**一张 provider 表**：每个 use 一个下拉（`llm` / `asr` / `tts`），只渲染选中 provider 的 1–2 个凭据字段；TTS 额外给默认音色下拉。`providers.save` 把选中项放到该 use `chain` 的**第一位，并保留原有其他 provider 作为 fallback**（去重、其余顺序不变），因此重跑向导不会丢掉上一轮可用的 fallback；未配置的 fallback 仍不阻塞（`stage_satisfied` 只要求任一 provider 可用）。响应里 `keys.evidence.chains` 给出保存后的完整顺序，`stage_state.ready` / `not_ready` 与 `providers[].ready` / `ready_reason` 给出运行时实际会装配的 provider。legacy 多 provider 链照常加载：**任一** provider 满足即可，`stage_state.selected` 展示第一个可用项（回退 `chain[0]`），`chain` 原样保留。运行时只装配 ready provider（未就绪者每进程一条 INFO，整条 stage 无 ready 时一条 ERROR）。

### 3.3 幂等与续跑

| 场景 | 规则 |
|---|---|
| 刷新/重启服务 | 从 config、secrets、keywords、live plane 重算，没有游标可丢 |
| 重复提交同一参数 | 先比较设备记录；相同则不写盘、不重启，直接返回状态 |
| 重复添加设备 | 按 `devices[].id` merge，其余设备保留；`params` 浅合并，`listen_port`/LED/volume 不丢（`app.py` L3270-3296、L3376-3387） |
| 写 config 崩溃 | `save_config` 0600 临时文件 + `os.replace` + 5 份备份（`config/store.py` L133-158） |
| 音频面起不来 | config 已保存，`params`/`wake` 不算 done；`plane_error` 返回；UI 可调 `/api/v1/system/audio/restart` |
| 两个标签页 | 每 plug 一把内存 `asyncio.Lock`；第二个请求 409 `step_in_progress` |
| 插头删除/不可用 | 设备仍在 config；`GET /setup` 返回 `blocked` + `plug_unavailable`，通用步可看，插头步禁用 |
| 想改已完成步骤 | 允许。点任意步骤重跑；只有证据真的消失才 `done → pending` |

### 3.4 动作 allowlist

action **由服务端按 `(step, plug)` 决定**，客户端和 manifest 不能内联命令或 action 字符串。

| step | action | 前置 | 写什么 |
|---|---|---|---|
| `keys` | `keys.save` | 无 | `secrets.yaml`（`secrets.put`，L64-93） |
| `keys` | `keys.test` | 无 | 不写；可用 `keys:{ENV:value}` 测未保存值，仅在本次探测内使用，响应标 `ephemeral:true`/`saved:false`，不落盘、不回显 |
| `keys` | `providers.save` | 无 | `secrets.yaml`（只写选中 provider 的字段）+ `config.yaml`（`providers.<stage>.chain=[selected, ...previous]`，去重；TTS 写 `providers.catalog.<name>.voice`） |
| `prepare` | `manual.confirm` | 无 | 仅内存 `confirmed`/`rollback_ack` |
| `params` | `device.upsert` | `prepare` done/skipped | `config.yaml` + `keywords.txt` + 重启平面；接受顶层 `volume`（0–100）、`led_enabled`、`listen_port`（1–65535）、`mic_gain_db`（0–42），与已有设备 merge；`mic_gain_db` 存 `params` |
| `wake` | `wake.set` | 设备存在 | `config.yaml` + `keywords.txt` + 重启平面 |
| `connect` | `connect.inspect` | 设备存在 | 无；token 仍只由 `GET /devices/{id}/connect` 返回 |
| `test` | `device.test` | 设备存在 | 无（只探测） |
| `verify` | `try.run`（走 `/setup/try`） | 设备存在 + 平面在线 | 仅内存 `first_answer` |

## 4. API

### 4.1 现有端点（代码事实，不改语义）

| 方法/路径 | 现状 | 位置 |
|---|---|---|
| `GET /api/v1/setup` | 5 步：keys/models/device/wake/verify | `app.py` L3298-3301 |
| `POST /api/v1/setup/keys` | 写 `secrets.yaml`，只写不读 | L3303-3317 |
| `POST /api/v1/setup/device` | 加/改设备、写 keywords、重启平面 | L3362-3406 |
| `POST /api/v1/setup/wake` | 改第一台设备唤醒词 | L3408-3423 |
| `POST /api/v1/setup/probe` | TCP 22 + SSH banner | L3425-3428 |
| `GET /api/v1/devices/{id}/connect` | **唯一**返回 device token 的端点；`ws_url` 的 host 若来自回环（`127.0.0.1`/`localhost`/`::1`/`0.0.0.0`）会自动换成服务端局域网 IPv4，响应带 `host_source: request|server`（回环时另有 `host_hint`） | L3459-3515 |
| `POST /api/v1/debug/say` | 试一句，真实 turn | L2220-2264 |

前端：`web/src/views/SetupView.vue`、`web/src/api.ts` L293-312/L731-745、路由 `/setup`（`router.ts` L18）、未完成自动跳转（`App.vue` L135-136）。

### 4.2 新增端点（最小集）

| 方法/路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `GET /api/v1/plugs` | — | `{plugs:[{id,version,class,adapter,transport,source,available,error}]}` | 内置 + `data/plugs/`；坏 manifest 进 `error`，不致命 |
| `GET /api/v1/plugs/{id}` | — | `{manifest, available, error, resolved}` | 详情；manifest 不含 secret；`resolved` 含默认 `listen_port`/`token_env` |
| `GET /api/v1/setup?plug=<id>` | query 可选 | `SetupState`（8 步） | 扩展现有端点；不带 `plug` 走默认解析 |
| `GET /api/v1/setup/step` | `?plug=&step=` | `{plug, step, next_step, blocked}` | 单步查询；不执行动作 |
| `GET /api/v1/setup/providers` | — | `{providers[], stages{}, stage_state{}}` | keys 表的渲染模型：每个 provider 一行（`fields`/`selected`/`available`/`required`/`ready`/`ready_reason`/`voices`/`default_voice`），每个 use 的 `selected`（链中第一个可用 provider，回退 `chain[0]`）/`selected_id`/`chain`/`ready`/`not_ready`/`required`/`satisfied`/`missing`；`ready` 与运行时装配同一判据；永不含值 |
| `POST /api/v1/setup/step` | `{plug, step, values?}` | `SetupState` | 执行 §3.4 的 allowlist 动作 |
| `POST /api/v1/setup/test-connection` | `{plug, device_id?}` | `{ok, device_id, checks[], error?}` | 不写配置；200 表示测试完成，`ok` 表示结果 |
| `POST /api/v1/setup/try` | `{text}` | `{ok, reply, audio_bytes, error?}` | 复用 `debug/say` 的 turn 路径；`audio_bytes>0` 写 `first_answer` |

> 不加 `GET /setup/diagnostics`：诊断包由前端从 `SetupState` + `last_error` 组装复制，避免新增可能泄漏日志/路径的下载端点。

### 4.3 形状

`SetupState`（顶层字段只加不改；`steps` 的 id/顺序替换为 8 步，旧 5 步测试同步更新；保留 `done` 布尔给旧组件）：

```json
{"plug":{"id":"xiaoai","version":"0.2.0","class":"A","adapter":"xiaoai","transport":"inbound","source":"builtin","available":true,"verified":true},
 "blocked":null,"next_step":"params","complete":false,"plane_error":"",
 "steps":[{"id":"keys","kind":"generic","required":true,"status":"done","done":true,"title_key":"setup.step.keys","detail":{},"last_error":null}],
 "device":{"id":"living","adapter":"xiaoai","host":"<speaker-ip>","room":"客厅","wake_words":["你好小瑞"],"enabled":true,"params":{}},
 "connect":{"configured":true,"token_env":"RUI_DEVICE_TOKEN","listen_port":4412},
 "keywords_file":"<data-dir>/keywords.txt","keywords_exists":true,"config_path":"<config-dir>/config.yaml","config_exists":true}
```

硬约束：`SetupState` 与 `GET /setup/step` **永不包含** token、key 值、`secrets.yaml` 内容；`connect` 只给 `configured`/`token_env`；`device.params` 只含非 secret 值，secret 参数只回 `{"<key>":{"has_value":true}}`。

`POST /setup/step` 请求：

```json
{"plug":"xiaoai","step":"params","values":{"host":"<speaker-ip>","room":"客厅",
 "wake_words":["你好小瑞"],"volume":35,"led_enabled":true,"listen_port":4412,
 "mic_gain_db":12.5,"params":{"volume_max":100}}}
```

keys 步骤的 provider 表提交（阶段都可省略，但至少一个；`provider` 省略时沿用当前 `chain[0]`）：

```json
{"step":"keys","action":"providers.save","payload":{
 "llm":{"provider":"deepseek_llm","keys":{"RUI_LLM_KEY":"<write-only>"}},
 "asr":{"provider":"volc_asr","keys":{"VOLC_API_KEY":"<write-only>"}},
 "tts":{"provider":"volc_tts","voice":"zh_female_xiaohe_uranus_bigtts",
        "keys":{"VOLC_API_KEY":"<write-only>"}}}}
```

响应仍是完整 `SetupState`；`secrets.yaml` 与 `config.yaml` 的值不回显。未知 provider、错误 stage、非该 provider 声明的 key 字段都是 422。

`prepare` 的 values 为 `{"confirmed":true,"rollback_ack":true}`（B 类必填）。`test-connection` 响应：

```json
{"ok":false,"device_id":"living","checks":[{"id":"adapter","ok":true,"ms":4},
  {"id":"plane","ok":false,"detail":{"speaker_connected":false,"plane_error":""}}],
 "error":{"code":"device_not_connected","retryable":true,
  "message":{"zh":"音箱还没连上 RUI","en":"The speaker is not connected yet"},
  "hint":{"zh":"确认设备端 client 已启动、地址和 token 与连接信息一致。","en":"Check that the device client is running and its URL/token match."}}}
```

### 4.4 错误码

错误放 FastAPI 的 `detail` 对象（兼容 `ApiError.detail`）；`message`/`hint` 同时给中英。

| HTTP | code | 场景 | retryable |
|---|---|---|---|
| 401 | `auth_required` | 未带控制台 token（现有中间件） | false |
| 404 | `unknown_plug`/`unknown_step`/`unknown_device` | 路径或 query 不存在 | false |
| 409 | `no_config` / `plug_unavailable` / `plug_incompatible` | 无可写 config；manifest 坏/版本不匹配/adapter 未注册 | false |
| 409 | `missing_prerequisite` / `step_in_progress` | 前一步未 done；同 plug 另一动作在跑 | true |
| 409 | `device_not_connected` / `audio_plane_unavailable` / `turn_in_progress` | 连接未通过；`try` 无平面或上一轮未结束 | true |
| 422 | `invalid_values` / `unsupported_param_type` / `invalid_secret_name` | 字段不合法；未知参数类型；secret key 不合法 | 见各 code |
| 400 | `secret_rejected` | `secrets.put` 拒绝（现有语义） | false |

`plane_error` 不是 HTTP 错误：`params`/`wake` 返回 200 + `plane_error` + step `status=error`，保持“配置已保存但房间还是聋的”可见（P1-6，`tests/test_setup.py` L270-318）。

## 5. UI（`web/src/views/SetupView.vue`）

### 5.1 布局与交互

- 页头：标题 + 插头下拉（无设备时可改）+ 进度 3/8 + 音频面徽标。
- 8 个 section 对应 §2；每个顶部固定“**你做** / **RUI 做**”，状态 chip 五色。
- `done` 的 section 折叠为摘要 + “修改”；点击展开可重跑。没有“上一步”游标，任意已完成步骤可回改。
- 失败面板固定动作：`重试`、`重新检查`（`GET /setup`）、`复制诊断`；请求期间按钮禁用并显示 running，409 `step_in_progress` 只提示“另一个标签页正在操作”。
- `state.steps` 由服务端顺序驱动，前端不硬编码 8 个 id；未知 id 用 `localizeBackend` 兜底（现有 `stepTitle`，L58-69）。
- `verify` 可跳过，跳过后 `skipped`，仍可回来试一句；完成后保留徽标和连接信息入口，不因 `complete` 跳走。

### 5.2 每步文案（zh / en）

| # | 用户做什么 | RUI 做什么 | 文案 zh | Copy en |
|---|---|---|---|---|
| 0 `keys` | 每个用途（LLM/ASR/TTS）选一个 provider，填它的凭据；TTS 再选默认音色 | 写 `secrets.yaml` 和选中 provider 的链/音色，只显示已配置 | “每种用途只需一个 provider；只显示它的凭据。保存后任何页面都读不回原文。” | “One provider per use; only its fields are shown. Saved values can never be read back.” |
| 1 `models` | 运行命令下载或确认已下载；点重新检查 | 检查 ASR/VAD/KWS 文件，不代下 | “模型不在这步下载；缺什么会列出来，下载后点重新检查。” | “Models are not downloaded here. Missing files are listed; re-check after fetching.” |
| 2 `prepare` | 按说明在音箱装 client（A）或固件（B），勾选 | 展示链接/最低版本/回滚；不执行设备命令 | “按说明完成设备侧准备；RUI 不会远程执行命令。” | “Prepare the device following the plug’s instructions. RUI runs nothing on it.” |
| 3 `params` | 填地址、房间和插头参数 | 按 manifest 校验，merge 写 `config.yaml`，重启平面 | “地址用局域网地址或主机名；保存会重启音频面。” | “Use a LAN address or hostname. Saving restarts the audio plane.” |
| 4 `wake` | 确认或修改 1–3 个唤醒词 | 重写 `keywords.txt`，重启平面 | “唤醒词最多 12 字、1–3 个；保存后重新生成 keywords.txt。” | “1–3 wake words, up to 12 characters each. Saving regenerates keywords.txt.” |
| 5 `connect` | 复制 ws 地址/token 到设备 client | 生成 `ws://<server>:<port>/?token=…`；token 只在连接端点返回 | “这个地址等于设备密钥，不要发到公开渠道；换 token 要重新填。” | “This URL contains the device secret. Do not share it; refill it after a token rotation.” |
| 6 `test` | 点测试，必要时重启 client | 探测 adapter/平面，返回每项检查 | “测试不会写配置；失败项会给出下一步。” | “The test writes nothing. Failed checks come with a next step.” |
| 7 `verify` | 输入一句话，或对音箱说唤醒词 | 跑一次真实 turn，记录 `audio_bytes` | “这句话走真实全链路；音箱应该真的出声。” | “This runs the real turn path. The speaker should actually make sound.” |

状态：`待完成/进行中/已完成/失败/已跳过`；`Pending/Running/Done/Failed/Skipped`。错误码映射 `setup.error.<code>`，缺 key 回退服务端 `message`，不拼原始异常。

### 5.3 失败、诊断、回退、secret

- **复制诊断**：前端组装 `{version, plug, step, statuses, last_error, plane, device:{id,adapter,room,enabled,params}, config_exists, keywords_exists}`。**禁止** token、key、`secrets.yaml` 值、完整 config、个人路径、完整 LAN IP；`host` 掩码为 `<device-host>`，`config_path` 只留 `config.yaml`。
- **回退**：没有破坏性“上一步”；已完成步骤可点开重跑；`params` 改地址/参数是 merge，不删 `listen_port`/LED/volume。
- **secret**：`type=password`、`autocomplete=off`、初始值永远空；只显示 `has_value` 徽标；保存后清空草稿。`connect` 的 token 默认 `••••••`，用户点“显示”才从 `GET /devices/{id}/connect` 取一次；不写 `localStorage`，不进诊断。
- **刷新**：只调 `GET /setup`，不恢复浏览器端步骤状态。用户重填 secret 是“只写不读”的代价，也是设计意图。

## 6. Manifest 驱动的插头槽位

### 6.1 哪些步骤来自 manifest

| manifest 字段（`rui-device/v0`） | 影响 | 说明 |
|---|---|---|
| `class: A \| B` | `prepare` / `test` 的警告与确认 | 只影响 UI 提示，不产生核心分支 |
| `device_side.artifact/name/source/version_min/install_steps/rollback` | `prepare` | 渲染链接、最低版本、步骤、回滚；`README` 不执行 |
| `params[]` | `params` | 表单一律由 manifest 生成；不硬编码机型字段 |
| `runtime.listen_port` / `runtime.token_env` | `connect` | 默认值来自 RUI 约定，manifest 可覆盖并在 UI 明示 |
| `transport: inbound \| outbound` | `connect` / `test` | inbound 复制 URL；outbound 跳过复制，用 `adapter.probe()` |
| `capabilities[]` | 设备页/降级提示 | 缺省=不支持；向导不承诺没有的能力 |
| `compatibility.rui_min` / `tested_with` | 全局 `blocked` | 不匹配显示“不兼容”，不静默尝试 |
| `acceptance.verified` / `trust` | `prepare` 徽标 / `available` | `false` 标“未验证，风险自担”；0.2 只让 `builtin`/`trusted` 可装 |
| `runtime.mode=sidecar` / `entrypoint` | 不适用 | 0.2 不加载代码；`available=false` + `sidecar_not_supported` |

测试动作**不新增 manifest 字段**：v0 没有 `setup.slots`，`test` 由 `transport` + adapter 推导（inbound 看平面，outbound 看 `probe`）。自定义探测等 P3 的 allowlist。

### 6.2 参数渲染

manifest `params[]` 是**表单 schema（列表）**；写入 config 后是 `devices[].params` **值字典**，两者不要混淆。

| type | 控件 | 校验/存储 |
|---|---|---|
| `string` | 文本框 | 可带 `placeholder`/`default`；去首尾空格 |
| `int` | 数字框 | 整数；v0 先只校验整数（范围 P2.1 补） |
| `bool` | 开关 | 存布尔 |
| `list` | 逗号/逐行输入 | 字符串数组；`max` 生效（唤醒词最多 3） |
| `choice` | 下拉 | **v0 缺 `options`**：未给 `options` 时标 `unsupported_param_type` 并禁用保存（P2.1 需补） |
| `secret` | 密码框 | 只写；key 匹配 `^[A-Z][A-Z0-9_]*$`，值进 `secrets.yaml`，`params` 不存值 |

- manifest 顶层未知字段：忽略（前向兼容）。
- `params[]` 未知 `type` 或 `choice` 无 `options`：字段显示只读错误卡片，`params` 步 `status=error`，**不写 config**。
- 提交里未声明的键：不渲染、不写入，422 `invalid_values`；`required` 缺省则前端标红、后端 422。

### 6.3 A 类 vs B 类

| | A 类（LX06，原厂共存） | B 类（Echo Dot 2，root/刷机） |
|---|---|---|
| `device_side.artifact` | `client` | `firmware`（或 `client`） |
| 向导动作 | 给 open-xiaoai 链接、最低版本、SSH 前提、卸载/恢复说明 | 给解锁/刷机/回滚链接；**不代刷、不托管固件** |
| 确认 | “已安装 client，并保留恢复方式” | 高风险横幅；`rollback_ack=true` 必填；`device_side.rollback` 为空则 manifest 不可用 |
| 测试 | 设备拨入后 `speaker_connected=true` | `adapter.probe()` + 平面状态；失败只报连接失败，不猜“刷机失败” |
| 回滚 | 卸载 client / 恢复原状态 | manifest 必须写原厂恢复路径；向导只展示，不执行 |
| 验收徽标 | `acceptance.verified` | 同左；未验证额外警告 |

## 7. 写入了什么

### 7.1 `config.yaml`（0600）

```yaml
devices:
  - id: living
    adapter: xiaoai
    host: <speaker-ip>          # 用户输入；日志也不要带 token
    room: 客厅
    enabled: true
    wake_words: [你好小瑞]
    listen_port: 4412           # manifest/默认；表单不显示时必须保留
    token_env: RUI_DEVICE_TOKEN
    allow_no_token: false
    params:                     # 新增：只放 manifest 声明的非 secret 值
      volume_max: 100
      mic_gain_db: 18
```

- `led`/`volume`/`listen_port` 等现有字段原样保留；`params` 浅合并，不存 secret、token、manifest 版本（版本以磁盘 manifest 为准，诊断里报告）。
- 插头识别默认用 `adapter` 反查；一个 adapter 对应多个 manifest 时用保留键 `params.plug_id`（v0 兜底，§9）。
- 老配置没有 `params` → 默认 `{}`，`DeviceConfig` 兼容（`schema.py` L120-147）。

### 7.2 其他文件

| 路径 | 内容 | 权限 | 写入者 |
|---|---|---|---|
| `<config-dir>/config.yaml` | `devices[]`、wake_words、params；keys 步还写 `providers.<stage>.chain=[selected, ...previous]`（去重）和 `providers.catalog.<tts>.voice` | **0600**，原子替换，5 份备份 | `save_config`（`config/store.py` L133-158） |
| `<data-dir>/secrets.yaml` | key 值、`type: secret` 参数、device token | **0600**，原子替换 | `secrets.put`（`secrets.py` L64-93） |
| `<config-dir>/keywords.txt` | `<pinyin> :2.00 #0.15 @<word>` | 0644（非 secret；实际按 umask） | `write_keywords_file`（`audio/kws.py` L71-90） |
| `.env` | 部署方写入的 key / device token | 0600（`install.sh` chmod） | **向导不写**；环境变量优先于 secrets（`secrets.py` L52-57） |
| `src/rui/device/plugs/*.yaml` / `data/plugs/<id>/...` | 内置/外部 manifest | 0644 只读；目录 0755；拒绝 symlink | 随镜像 / 运维、插件作者 |
| 诊断包 | 只在浏览器内存，复制到剪贴板 | — | 不落盘 |

## 8. 测试计划

### 8.1 pytest API（新增 `tests/test_setup_plugs.py`，复用 `tests/test_setup.py` fixture）

| # | 测试 | 断言 |
|---|---|---|
| 1 | `test_plugs_list_builtin_and_broken_manifest_is_not_fatal` / `test_plug_detail_and_unknown_plug` | `xiaoai` 可用；坏 YAML 只进 `error`；详情含 `device_side`/`params`/`class`；未知 id 404 |
| 2 | `test_setup_returns_eight_steps_in_order` | 顺序 `keys,models,prepare,params,wake,connect,test,verify`；旧 `done` 仍在 |
| 3 | `test_statuses_come_from_evidence_not_claims` | 初始 keys pending；填 key 后仍可能 pending（endpoint 缺失）；模型齐后 done |
| 4 | `test_prepare_confirmation_and_b_rollback_ack` | A `confirmed` 即 done；B 缺 `rollback_ack` 422 |
| 5 | `test_device_upsert_accepts_top_level_controls_and_validates_ranges` / `test_params_idempotent_and_merge_preserves_unshown` | 顶层 volume/LED/listen_port/mic_gain_db 写入且越界 422；重复提交不增设备、不丢未展示字段 |
| 6 | `test_unknown_param_type_blocks_without_writing` | 422 `unsupported_param_type`；config mtime 不变 |
| 7 | `test_secret_param_is_write_only` / `test_setup_never_contains_token_or_key` | 响应无明文；`secrets.yaml` 有值；`/setup`、`/setup/step` 无 token/key |
| 8 | `test_test_connection_reports_disconnected_without_writing` / `test_diagnostics_payload_has_no_secret_and_masks_host` | `ok=false`、`code=device_not_connected`、mtime 不变；诊断不含 token/完整 host |
| 9 | `test_try_marks_first_answer_only_with_audio` | `audio_bytes=0` 不置 done；`>0` 置 done |
| 10 | `test_plane_error_keeps_params_and_wake_not_done` / `test_concurrent_step_returns_409` | 200 + `plane_error`；两 step `error`；持锁时第二个请求 409 |
| 11 | `test_providers_save_*` / `test_providers_save_keeps_previous_fallbacks_across_saves` / `test_keys_step_is_satisfied_by_any_provider_in_a_fallback_chain` / `test_keys_step_local_sherpa_fallback_is_usable_without_a_cloud_key` / `test_provider_chain` | 选择写 `chain=[selected, ...previous]`（去重）+ TTS voice；重跑保留 fallback；值只写不读；未知/错 stage/key 字段 422；任一 provider 满足即 keys done，`selected` 指向可用项；`ready`/`not_ready` 与运行时一致；本地模型文件齐才算可用 |
| 12 | `test_keys_test_accepts_unsaved_values_without_storing_or_echoing` / `test_device_test_ssh_only_is_not_green_for_an_inbound_speaker` / `test_connect_url_uses_the_server_lan_ip_when_opened_via_loopback` | 临时 keys 不落盘且响应对应 `not saved`；SSH 通但未回连 test 不绿；回环 Host 换 LAN IP 且 `host_source=server` |

### 8.2 console 测试

`web/` 目前没有 JS runner（`package.json` 只有 build/typecheck）。本切片不新增 Vitest，用两层：

1. `cd web && npm run typecheck && npm run build`。
2. `tests/test_setup_console.py`（读源码，风格同 `tests/test_panels.py`）：8 个 step id 在 zh/en 两个字典都有 `setup.step.<id>`；secret 输入 `type=password` 且不绑 state 值；存在“复制诊断”，诊断函数不读 `token`/`api_key`/`secret`；`api.ts` 声明 `plugs`/`setupStep`/`setupTestConnection`/`setupTry`。

交互级 Vitest + `@vue/test-utils` 列为后续；如评审要求，加 0.5 天和 2 个 devDependency。

### 8.3 手工验收：LX06（A 类）

前置：LX06 已开 SSH，NAS 与音箱同 LAN；`install.sh` 已生成 `.env`；用 `<speaker-ip>` 代指真实地址。

1. 打开 `/setup`，页头显示 LX06 / A 类 / 已验证或未验证徽标。
2. `keys`：填 LLM + TTS（ASR 本地可不填）；保存后只显示已配置，刷新输入框为空。
3. `models`：运行 `fetch-models`，点重新检查，变 done。
4. `prepare`：按 open-xiaoai 链接装 client，勾选完成；LX06 不出现 B 类确认。
5. `params`：填 `<speaker-ip>`、房间、唤醒词；`config.yaml` 出现 `devices[0]`；`params` 只有 manifest 声明的键；平面重启成功。
6. `wake` + `connect`：`keywords.txt` 含 `@你好小瑞`；改一次确认更新；复制 ws URL 粘进 client 并重启。
7. `test`：`speaker_connected=true`；关掉 client 再测，得到 `device_not_connected` + hint，且不写配置。
8. `verify` + 幂等：输入“现在几点？”，音箱出声并 done；重复保存 `params` 仍只有 1 台设备；中途刷新 `next_step` 回到第一个未完成项。

### 8.4 手工验收：mock / fake 设备

内置 `mock` manifest：`class: A`、`transport: outbound`、`device_side.artifact: none`、`adapter: mock`。

1. 页头选 `Mock speaker (dev)`；`prepare` 自动 skipped。
2. `params`：`host` 填 `<mock-host>`，保存；`devices[0].adapter=mock`。
3. `connect` 自动 skipped；`test` 调 `adapter.probe()`，mock 成功 → done。
4. `wake` 写 `keywords.txt`；`verify` 可选（无 TTS/舞台时可跳过）。API 层用 `TestClient` + monkeypatched plane 覆盖，不依赖真机。

## 9. 风险、开放问题、延后

### 9.1 风险 / 开放问题

1. **`prepare` / `verify` 的确认不持久化**：只在内存，刷新/重启回 pending（A 类若 `test` 已过则自动 done；`verify` 是可选项，不阻塞 `complete`）。要持久化需新增 `data/setup.json`（0600），本切片不做。
2. **`choice` 缺 `options`**：v0 定义了 `type: choice` 但没给选项字段；P2.1 需补 `options: [string]`，否则该类型不可用。
3. **adapter → manifest 不唯一**：两个机型共用一个 adapter 时无法反查；兜底 `params.plug_id`，更干净是 P3 给 `DeviceConfig` 加 `plug` 字段。
4. **P2.1 loader 尚未落地**：本设计假设至少有 `xiaoai.yaml`、`mock.yaml`、`dot2.yaml`；若延期，先用 `tests/fixtures` manifest 跑 API，UI 只显示 xiaoai。
5. **错误对象 vs 现有字符串 detail**：新端点用对象 `detail`，`SetupView` 按 `code` 映射；旧页面/旧客户端仍按字符串，不全局改 `ApiError`。
6. **`/setup/try` 与 `/debug/say` 重复**：抽内部 `run_text_turn()`，两个路由都调它，不复制 turn 逻辑。
7. **B 类回滚无法验证 / token 与 host**：`rollback_ack` 只是用户确认，不是“已恢复”的证据；向导不生成 token，冷装依赖 `install.sh` 的 `.env`；现有 `setup_device` 会记录 host，诊断必须掩码，日志绝不能带 token。
8. **`skipped` 与 9 步骨架**：用户跳过 `verify` 后刷新回 pending，不要当持久设置；本文把选插头放页头、保存当副作用，若评审要求严格 9 步，需改 UI 和 `next_step` 顺序。
9. **v0 没有 test slot**：连接测试只能按 `transport` 推导；需要 manifest 声明探测方式的插头，P2.1 需补 `setup.test` 或 allowlist（`tcp.connect`/`http.get`）。

### 9.2 明确延后

| 项 | 去向 |
|---|---|
| 多设备/多音频面/仲裁、approve/pending、每设备 token | P3.4 |
| OTA/固件分发/校验和托管 | P3+；RUI 只给链接和版本 |
| sidecar/第三方可执行 adapter、SQLite/安装历史/指标 | P3；0.2 只加载 builtin/trusted manifest |
| USB/ADB/TWRP/刷机自动化、mDNS、TLS/自建 CA、每设备端口 | 永不做或 P3 |
| Vitest 组件测试 | 后续；本切片静态契约 + 手工验收 |

> 参考：`docs/device-management-research.md` §3；`docs/socket-plug.md` §5/§6/§8；`docs/ROADMAP.md` §3 P2.2。代码：`app.py` L3064-3210/L3270-3428/L3459-3515；`SetupView.vue`；`api.ts` L293-312/L731-745；`schema.py` L120-147；`store.py` L133-158；`secrets.py` L52-93；`audio/kws.py` L71-104。

## 10. 决策（Lead，2026-10-24，对第 9 节开放问题的答复）

| # | 问题 | 决定 |
|---|---|---|
| 1 | prepare/verify 确认是否持久化 | **持久化**到 `data/setup.json`（0600）：`{step_id: {status, evidence, at}}`；不引入 DB。刷新/重连后按 evidence 重新计算（证据缺失则回 pending）。 |
| 2 | manifest 缺 `choice.options` / `setup.test` 槽 | `choice.options` 已在 P2.1 loader 强制（非空且 default 在 options 内）；**v0 不新增 `setup.test` 槽**：test 步是通用 probe/try，插头专属测试留到 manifest v1（P3）。 |
| 3 | adapter→manifest 命名冲突 | P2 先加 `DeviceConfig.params: dict`（保留键 `plug_id`）并保留 `devices[].adapter` 给内置 LX06；P3 抽包时再加 `DeviceConfig.plug` 并兼容旧字段。 |
| 4 | 9 步契约 vs 8 步 UI | `socket-plug.md` §6 的 9 步是**契约**；设计稿的 8 步是**UI 布局**（插头选择放页头）。本文 §5 为准实现，契约不降级。 |
| 5 | 新端点错误对象 | 新增 setup/plugs 端点返回 `{code, message, details}` 放在 `detail` 里；**旧端点（字符串 detail）保持不动**，避免前端全量改。 |
| 6 | 非 install.sh 部署的 device token | P2 阶段：向导**不自动生成**，检测到缺失时显示“如何生成/填写”的说明；自动生成放到 P2.4 设备管理（与 `.env` 写入策略一起定）。 |
| 7 | verify 是否阻塞 | **不阻塞**：跳过时 setup 可完成但标记 `verified=false`（控制台显示“未验证”）；首句 evidence 优先取向导开始后的新 turn，否则用人工确认。 |
| 8 | loader 落地时机 | loader 已合入（`5c1f728`）；本切片用 docs/socket-plug.md 的示例作为 fixture manifest，不等外部插件。 |
