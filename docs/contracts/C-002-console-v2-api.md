# C-002 控制台 v2 API 契约

> **契约编号**：C-002/1  
> **状态**：定稿草案（2026-10-25），随 rui 0.6.x 以 **additive** 方式发布；不改变 C-001（`rui-device/v1`）。  
> **适用对象**：`web/src/v2/**` 前端实现者、`src/rui/web/api/**` 后端实现者、reviewer/verifier。  
> **一句话**：v2 控制台只依赖 `/api/v2/*` 与能力（capability）声明；机型差异来自插头 manifest，不来自前端 if/else。

---

## 0. 元信息

| 项 | 值 |
|---|---|
| 契约 ID / 版本 | `C-002/1` |
| API 版本 | `2.0`（路径前缀 `/api/v2`） |
| rui 版本窗口 | `>=0.6.0, <0.7.0`：只增不改，v1 全部保持可用 |
| 破坏性变更 | 本契约 **不含**；删除/重命名只能进 `/api/v3` 或 0.7 的 C-002/2 |
| 依赖契约 | C-001 `rui-device/v1`（manifest：`capabilities`、`params`、`audio_defaults`、`runtime`、`device_side`）**不变** |
| 认证 | 沿用 `X-Auth-Token`；无账号/角色/会话 |
| 前端入口 | `web/v2.html` + `web/src/v2/**`，路由前缀 `/v2/*`；生产由 FastAPI 托管 `v2.html` |
| 默认切换 | 0.6.x `/` 仍是旧控制台；v2 可达但非默认。0.7 起 `/` → `/v2`，旧控制台挂 `/legacy` 保留一个版本（见 §10） |
| 存储 | 不新增数据库；`config.yaml` + `secrets.yaml` + `data/plugs/` 仍是唯一事实源 |

**Owner 已拍板（不再作为开放问题）**

1. 新控制台从零写、旁路并存；旧控制台 `/` 不动。
2. 设备单独成一级导航段（运行 / 设备 / 配置）。
3. 添加第 N 台设备复用八步向导的 add 模式。
4. 每设备音频覆盖只在设备页编辑；全屋默认放房间页。
5. 0.6.x 接受“改一台也重启全屋音频面”，UI 必须明示并确认。
6. 旧 `/panel/devices`：0.6.x 隐藏、0.7 删除并重定向。

---

## 1. 范围与非目标

### 1.1 本契约定义

- `/api/v2` 的资源、端点、请求/响应、错误码、能力模型、覆盖来源（provenance）、认证与兼容/回滚。
- v2 前端可依赖的服务器行为；v2 **不得** import `web/src/panels.ts`、`web/src/views/PanelHost.vue`、`web/src/views/*.vue`（旧页面）。
- 允许 v2 复用基础层：`web/src/i18n.ts`、`web/src/theme.ts`、`web/src/api.ts` 的 `token`/`request` 基础能力、`AppIcon.vue` 这类纯展示组件。

### 1.2 非目标

- 不定义账号/角色/多租户、HA/ESPHome 依赖、OTA/刷机/ADB/WebUSB。
- 不定义非设备域的 v2 API（providers/integrations/music/memory 在 v2 首次交付仍可调用稳定 v1 端点；迁移时另立契约）。
- 不改变 v1 任何端点语义；v2 可在内部复用 v1 的服务层实现，但对外形状以本契约为准。
- 不新增配置版本；不要求 `CONFIG_VERSION` 升级。

---

## 2. 交付与托管（v2 前端边界）

### 2.1 构建

- Vite 多页入口：新增 `web/v2.html`，build 产出 `dist/v2.html` + `dist/assets/*`。
- 源码根：`web/src/v2/`；入口 `web/src/v2/main.ts`；路由 `web/src/v2/router.ts`（`createWebHistory('/v2/')`）。
- v2 自己声明路由与导航，不读 `/api/v1/panels`、不渲染 `PanelSpec.blocks`。
- v2 的 API 客户端放 `web/src/v2/api.ts`，类型由本契约生成/手写；不得调用 v1 的 `api.panels()` / `api.device*` 等旧方法（v2 自己实现同名能力）。

### 2.2 FastAPI 托管

| 路径 | 行为 |
|---|---|
| `GET /api/v2/*` | v2 API（本契约）；未匹配的 `/api/v2/*` 返回 JSON 404，**不得**落入 SPA shell |
| `GET /v2`、`GET /v2/*`（非 `/api`、非 `/v2/assets`） | 返回 `dist/v2.html`，支持 history 深链刷新 |
| `GET /v2/assets/*` | 静态文件，长缓存（hash 文件名，`immutable`） |
| `GET /`、`/panel/*`、`/setup` 等 | 0.6.x 保持旧 `index.html` 行为不变 |
| 0.7 | `/` → `/v2`；旧 shell 挂 `/legacy`（`/legacy/*` 返回旧 `index.html`），保留一个版本 |

实现约束：`/api/v2` 路由必须注册在 SPA catch-all 之前；`/v2` 的 catch-all 不得吞掉 `/api/*`（参考现有 `app.py:6405-6418` 的注册顺序）。

### 2.3 前端禁用清单（CI/评审可 grep）

`web/src/v2/**` 中不得出现：

- `from '../panels'`、`from '../views/PanelHost.vue'`、`from '../views/`、`api.panels(`；
- 任何 `model === 'lx06'` / `device.adapter === 'dot2'` / 机型名字面量做分支；
- 直接 `fetch('/api/v1/...')` 的旧**设备**端点（`/api/v1/devices*`、`/api/v1/setup/step`、`/api/v1/device/volume`、`/api/v1/debug/*`）；非设备域的过渡页面（turns/logs/memory/config/providers/music/doctor）可调用 v1 稳定端点，但必须经 `web/src/v2/api.ts` 封装，不得直接散落 fetch。

允许的机型差异只有一个来源：`GET /api/v2/plugins` / device 的 `capabilities` + `params` schema。

---

## 3. 资源模型

```text
House（单例，无 id）
 ├─ multi_device: {enabled, max_planes, default_output, room_priority, arbitration windows}
 ├─ global_audio: {audio: {capture, voice, playback, aec}, ack: {echo_ignore_ms}}
 ├─ rooms: 派生视图（room = devices[].room），不是可 CRUD 资源
 └─ warnings[]

Plugin（插头，只读）
 └─ devices[]（DeviceManifest：model_id, model, vendor, capabilities, params schema, audio_defaults, device_side）

Device（可控资源）
 ├─ identity: id, room, plugin_id, model_id, model, vendor, transport
 ├─ config: host, enabled, wake_words, volume_target, listen_port, params(public)
 ├─ token: token_env（只通过 rotate 路径写入/轮换；任何响应不回值）
 ├─ capabilities: declared / probed / effective / unknown
 ├─ audio: effective + sources + layers + overrides + manifest_defaults
 ├─ runtime: status, connected, last_seen, plane, warnings
 └─ connection: ws_url/token（reveal 才返回 token）

SetupPlan（八步向导的 add 模式）
 └─ steps[]: keys, models, prepare, params, wake, connect, test, verify

Activity（设备维度事件流）
 └─ turn | log（log 为字面匹配，标 matched_by）
```

**身份规则**：`Device.id` 是不可变主键；`plugin_id` / `model_id` 创建后不可变（换机型 = 删除 + 新建）。`room` 是字符串属性，不是实体；同一房间多台设备允许配置，但 `GET /house` 给出 `duplicate_room` 警告。

---

## 4. 通用约定

### 4.1 传输

- 全部 JSON：`Content-Type: application/json; charset=utf-8`。
- 单一资源直接返回对象；列表返回 `{"items":[...],"total":N}`；错误返回 `{"error":{...}}`。
- 无分页参数（M1–M4 设备量 ≤8）；`limit` 只用于 activity。

### 4.2 认证

- 所有 `/api/v2/*`（含 `GET /meta`）要求 `X-Auth-Token`（`app.py:3073-3096` 的同一中间件语义）。
- 缺失/错误 → `401 {"error":{"code":"unauthorized",...}}`；同一 IP 60 秒 10 次失败 → `429 rate_limited` + `Retry-After`。
- 无 cookie、无 CSRF token、无账号/角色；`web.allow_anonymous` 仅开发模式，语义与 v1 一致。
- v2 前端 token 存储沿用现有 `token` helper（localStorage `rui-token`）；401 弹既有 token 输入（v2 自绘）。

### 4.3 时间 / ID / 数字

| 类型 | 规则 |
|---|---|
| 时间戳 | `*_ts`、`checked_at`、`last_seen` 为 Unix 秒（float，允许小数）；`at` 类 ISO 字符串仅用于展示来源（如 `last_test_at`） |
| 设备 id | `^[a-z0-9][a-z0-9_-]{0,31}$`；创建后不可变 |
| plugin/model id | 来自 manifest；`^[a-z0-9][a-z0-9_-]*$` |
| 音量目标 | 整数 `0..100`（0 = 不接管） |
| 设备混音器 | 整数 `0..255`（仅 `volume` action） |
| 音频码率/毫秒 | 整数；单位写在字段名（`_ms`、`_hz`、`_db`） |
| 布尔 | JSON true/false；`connected` 允许 `null`（没有平面可看，不冒充 false） |

### 4.4 错误对象（统一）

```json
{
  "error": {
    "code": "invalid_values",
    "message": "device room is required when multi-device is enabled",
    "hint": "Set a room for every enabled speaker, or disable multi-device.",
    "details": {
      "fields": [
        {"path": "devices[0].room", "code": "required", "message": "room is required"}
      ]
    },
    "retryable": false
  },
  "request_id": "req_01J..."
}
```

- `code` 稳定、可 i18n；`message` 英文技术句；`hint` 英文可操作建议（前端翻译为 zh/en 文案）。
- 响应头必须带 `X-Request-ID`；body `request_id` 同值。
- 422 必须给 `details.fields[]`（`path` 用点/数组下标，和 v1 的 `loc` 对齐），前端按 path 标红。

### 4.5 写操作与重启

| 写操作 | 重启行为 | 响应必须带 |
|---|---|---|
| `POST /devices`、`PATCH /devices/{id}` | 自动重建 **整个 fleet**（现有 `_restart_audio`/`_write_devices` 语义，`app.py:5184-5198`、`5247-5291`） | `restart` 对象 |
| `DELETE /devices/{id}` | 自动重建整个 fleet；响应体不返回 `restart`（按 owner 裁决只回 `{removed, device_id}`，UI 刷新 `/house`） | `{removed, device_id}` |
| `PATCH /devices/{id}/audio` | **自动重建整个 fleet**（与设备写同一条写路径，§7.16） | `{audio, device, restart}`，`restart.scope="fleet"`、`auto=true` |
| `PATCH /house`（`multi_device`） | 不自动重启；返回 `restart.required=true, scope="service"` | `restart`（含 `restart_fields`） |
| `PATCH /house/audio` | 不自动重启；返回 `restart.required=true, scope="audio_plane"` | `restart`（含 `restart_fields`） |
| `POST /house/audio/restart` | 显式重启全部音频面 | `restart` 结果 |

```json
"restart": {
  "scope": "fleet",
  "required": true,
  "auto": true,
  "status": "ok",
  "affected_devices": ["living", "dot2"],
  "duration_ms": 420,
  "error": ""
}
```

- 写配置失败（无 config、校验失败）→ 4xx，**不写盘、不重启**。
- 写盘成功但平面失败 → **2xx**，`restart.status="failed"`、`restart.error` 原文；UI 必须显示错误条，不能显示“已保存”了事。
- 删除/禁用最后一台 enabled 设备 → `restart.status="stopped"`、`restart.error=""`；不是失败。

### 4.6 Secret 纪律

- 任何响应不得包含 token、密码、API key、`secrets.yaml` 内容；`params` 只返回 manifest 声明的非 secret 键。
- 当前两个内置 manifest（LX06、Dot2）都**没有 secret param**；`POST/PATCH /devices` 不接受 secret。不要为了设备 token 新增 `secret_params` 或往 `secrets.yaml` 写一个 manifest secret。
- 设备 token 只有两条路径：`POST /devices/{id}/token/rotate` 写入 manifest `runtime.token_env` 指向的 token 环境（Dot2：`DOT2_EM_TOKEN`）；`POST /devices/{id}/connection/reveal` 读一次完整值。二者都不落日志、不缓存。
- `GET /devices/{id}/connection` 的 `ws_url` 必须掩码 token；只有 reveal 返回完整值（`Cache-Control: no-store`）。

### 4.7 幂等与确认

- `POST /devices`：同 id 已存在 → `409 device_exists`，不覆盖。这是幂等规则的**显式例外**：create 不做 upsert；要改已有设备用 `PATCH`。
- `PATCH`：同 body 重复执行结果一致。
- `POST /setup/plan/steps/{step}`：同参数重复执行幂等；并发同 step → `409 step_in_progress`。
- `DELETE /devices/{id}`：必须带 `?confirm=<id>`；缺失/不匹配 → `409 confirm_required` + `details.impact[]`，不删除。
- 写操作冲突（端口、并发重启）→ `409` 或 `422`，错误对象给可操作字段。

---

## 5. Capability 模型（适配多种音箱的落点）

### 5.1 能力 id（0.6 核心已知集合）

来自 `src/rui/device/contract.py:26-39` 与 C-001。**规范值 8 个**：`mic_array`、`reference`、`playback`、`led`、`keys`、`volume`、`device_wake`、`barge_via_device`；`native_kws` 是已废弃旧值（v1 manifest 拒绝），`streaming_asr` 保留但未启用。新 manifest 只能声明 8 个规范值。

| id | 含义 | 可执行 action | UI 降级 |
|---|---|---|---|
| `playback` | 能接收 PCM 播放 | `audio.say`、`audio.play`、`audio.stop` | 未声明：隐藏试一句/试音/停止；测试标 `not_applicable` |
| `mic_array` | 多麦阵列 | — | 未声明：隐藏阵列相关说明；不影响文本回合 |
| `reference` | 有播放参考回环（RUI AEC 需要） | — | 未声明：AEC `who=controller` 不可用，提示用设备侧 AEC |
| `led` | 灯环 | `led.set` | 未声明：隐藏灯效测试 |
| `keys` | 物理按键 | — | 未声明：只影响说明 |
| `volume` | 设备混音器可读写 | `volume.read`、`volume.set` | 未声明：隐藏实际音量滑块；音量目标仍可配 |
| `streaming_asr` | 可持续上行音频 | — | 未声明：说明唤醒/ASR 路径不同 |
| `device_wake` | 设备侧检测唤醒词并上报 | — | 未声明：唤醒来源显示 RUI KWS |
| `barge_via_device` | 设备可上报打断建议 | — | 未声明：打断依赖 RUI AEC/reference |
| `native_kws` | 已废弃 | — | 显示 deprecated 警告，不渲染控件 |

### 5.2 declared / probed / effective

- `declared`：manifest `capabilities` 声明。
- `probed`：adapter 运行期报告；`null` = 该能力不由 probe 决定（`led`/`keys`/`volume`）。
- `effective`：现有 `effective_capabilities()` 规则（`contract.py:64-96`）：
  - probe-bound（`mic_array/reference/playback/device_wake/barge_via_device`）= manifest ∩ probe；
  - `led/keys/volume` = manifest 权威；
  - manifest 未解析（开发/mock）= probe 权威；
  - probe 多报但 manifest 未声明 = **不提升**，进 `unknown/probe_only` 诊断。
- 设备响应同时返回：

```json
"capabilities": [
  {"id": "playback", "declared": true, "probed": true, "effective": true,
   "known": true, "reason": "ok"},
  {"id": "led", "declared": true, "probed": null, "effective": true,
   "known": true, "reason": "manifest_only"},
  {"id": "volume", "declared": true, "probed": null, "effective": true,
   "known": true, "reason": "manifest_only"},
  {"id": "future_sensor", "declared": true, "probed": null, "effective": false,
   "known": false, "reason": "unknown_capability"}
],
"effective_capabilities": ["playback", "led", "volume"],
"unknown_capabilities": ["future_sensor"]
```

`reason` 枚举：`ok | manifest_only | probe_missing | not_declared | unknown_capability | deprecated`。

> 0.6.x 的 manifest 校验仍会拒绝未知 capability（C-001 不变）；`unknown_capabilities` 从 0.7 起才有实际值。v2 UI 从第一天就按“可能未知”渲染，未来加能力不需要改前端。

### 5.3 能力目录端点

`GET /api/v2/capabilities` 返回 UI 可渲染的元数据（与设备无关）：

```json
{
  "items": [
    {
      "id": "volume",
      "label": {"zh": "音量", "en": "Volume"},
      "description": {"zh": "设备自己的混音器，0–255", "en": "The device mixer, 0–255"},
      "ui_hint": "slider",
      "actions": [
        {"id": "volume.read", "args_schema": {"type": "object", "properties": {}, "additionalProperties": false},
         "result_schema": {"type": "object", "required": ["value", "range"],
                           "properties": {"value": {"type": "integer"}, "range": {"type": "string"}}}},
        {"id": "volume.set", "args_schema": {"type": "object", "required": ["value"],
           "properties": {"value": {"type": "integer", "minimum": 1, "maximum": 255}},
           "additionalProperties": false},
         "result_schema": {"type": "object", "required": ["value"],
                           "properties": {"value": {"type": "integer"}}}}
      ],
      "degraded_hint": {"zh": "未声明音量能力；仍可在配置里设音量目标",
                        "en": "Volume is not declared; the config volume target still applies"}
    }
  ]
}
```

- `actions[].args_schema/result_schema` 只支持 JSON Schema 子集：`type/object/properties/required/additionalProperties/items/enum/minimum/maximum/minLength/maxLength`。
- `ui_hint`：`slider | switch | button | none`；未知能力 `none`。
- 完整 action schema（与 `v2_api.py:120-300` 的 `CAPABILITIES` 一致；上例只展示 volume）：

| action | capability | args_schema（required / properties） | result_schema |
|---|---|---|---|
| `audio.say` | playback | `text`（string 1..500） | `reply`(string), `audio_bytes`(integer) |
| `audio.play` | playback | `file`(string), `loop`(bool), `gain`(number) | `playing`(string) |
| `audio.stop` | playback | 无 | `playing`(null) |
| `led.set` | led | `effect`(enum `LED_STATES`) | `effect`(string) |
| `volume.read` | volume | 无 | `value`(integer), `range`(string) |
| `volume.set` | volume | `value`(integer 1..255) | `value`(integer) |
- 能力目录由核心拥有，不由插头提供文案；插头只声明 id。

### 5.4 泛化动作端点的执行规则

`POST /api/v2/devices/{id}/actions`：

1. 解析 `action` → 若不认识 → `400 unknown_action`。
2. 查 action 所属 capability → 若设备 `effective=false` → `409 capability_unsupported`，`details.missing_capability`。
3. 若设备 disabled → `409 device_disabled`。
4. 若 action 需要 live link（`audio.say/play/stop`、`volume.read/set`、`led.set`）且 `connected!==true` → `409 device_not_connected`。
5. 校验 `args`（按 schema）→ `422 invalid_values` + `details.fields[]`。
6. 执行；返回 `{action, ok, code, result, device_status}`；`result` 形状由 catalog 的 `result_schema` 决定。
7. 执行失败但设备仍在线 → `502 action_failed`（`retryable:true`）；适配器异常原文放 `details.error`（脱敏）。

动作清单（0.6）：

| action | capability | args | result | 副作用 |
|---|---|---|---|---|
| `volume.read` | `volume` | `{}` | `{value, range:"0-255"}` | 无 |
| `volume.set` | `volume` | `{value:1..255}` | `{value}` | 改设备混音器；不改配置 |
| `led.set` | `led` | `{effect:enum}`；enum 来自 `LED_STATES`：`off/listening/thinking/speaking/wake/error/music/music_paused/muted`（`schema.py:120-123`） | `{effect}` | 改灯效；需要 live link，无连接 → `409 device_not_connected` |
| `audio.say` | `playback` | `{text:1..500}` | `{reply, audio_bytes, tools[], error}` | 真的走一轮：会出声、更新 `last_active_room`/`turn_device`、写入最近回合；UI 必须确认 |
| `audio.play` | `playback` | `{file, loop?, gain?}` | `{playing:file}` | 文件必须在 data_dir/cache 内；否则 `403 path_not_allowed` |
| `audio.stop` | `playback` | `{}` | `{playing:null}` | 停止当前播放 |

---

## 6. 端点总览

| # | 方法 | 路径 | 用途 | 里程碑 |
|---|---|---|---|---|
| 1 | GET | `/api/v2/meta` | 契约/版本/feature flags | M1 |
| 2 | GET | `/api/v2/house` | 全屋聚合 | M1 |
| 3 | PATCH | `/api/v2/house` | 全屋策略 | M5 |
| 4 | GET | `/api/v2/house/audio` | 全局音频默认 | M5 |
| 5 | PATCH | `/api/v2/house/audio` | 写全局音频默认 | M5 |
| 6 | POST | `/api/v2/house/audio/restart` | 重启全部音频面 | M5 |
| 7 | GET | `/api/v2/plugins` | 插头列表 | M1 |
| 8 | GET | `/api/v2/plugins/{plugin_id}` | 插头 manifest 公开视图 | M1 |
| 9 | GET | `/api/v2/capabilities` | 能力目录 | M1 |
| 10 | GET | `/api/v2/devices` | 设备列表 | M1 |
| 11 | POST | `/api/v2/devices` | 创建设备 | M3 |
| 12 | GET | `/api/v2/devices/{id}` | 设备详情 | M1 |
| 13 | PATCH | `/api/v2/devices/{id}` | 改设备（含 audio，一次原子写） | M3/M4 |
| 14 | DELETE | `/api/v2/devices/{id}` | 删除（confirm） | M3 |
| 15 | GET | `/api/v2/devices/{id}/audio` | 音频 provenance | M4 |
| 16 | PATCH | `/api/v2/devices/{id}/audio` | 只写音频覆盖 | M4 |
| 17 | GET | `/api/v2/devices/{id}/connection` | 连接信息（掩码） | M1 |
| 18 | POST | `/api/v2/devices/{id}/connection/reveal` | 返回完整 token/ws_url | M2 |
| 19 | POST | `/api/v2/devices/{id}/token/rotate` | 轮换 token | M2 |
| 20 | POST | `/api/v2/devices/{id}/test` | 证据式连接测试 | M2 |
| 21 | POST | `/api/v2/devices/{id}/actions` | 泛化能力动作 | M2 |
| 22 | GET | `/api/v2/devices/{id}/activity` | 设备事件流 | M2 |
| 23 | GET | `/api/v2/setup/plan` | 八步 add 计划 | M6 |
| 24 | POST | `/api/v2/setup/plan/steps/{step}` | 执行一步 | M6 |

> `GET /meta.features` 全部为 `true`：24 个 `/api/v2` 端点已全部点亮（0.6.x 发布口径）；`features` 保留只是为了 0.7 前可按需紧急关闭某项，不再表示里程碑未完成。

---

## 7. 端点详细

### 7.1 `GET /api/v2/meta`

响应：

```json
{
  "contract": "C-002/1",
  "api_version": "2.0",
  "rui_version": "0.6.0",
  "compat": {"min": "0.6.0", "max_exclusive": "0.7.0"},
  "features": {
    "house": true,
    "house_audio": true,
    "plugins": true,
    "capabilities": true,
    "devices": true,
    "device_create": true,
    "device_audio_provenance": true,
    "device_actions": true,
    "device_activity": true,
    "setup_plan": true
  },
  "capabilities": ["mic_array", "reference", "playback", "led", "keys", "volume",
                   "native_kws", "streaming_asr", "device_wake", "barge_via_device"]
}
```

- 前端启动先读 meta；`contract` 不匹配 → 显示兼容错误页，不发后续请求。
- `rui_version` 来自 `__version__`（`app.py:3104`）。

### 7.2 `GET /api/v2/house`

```json
{
  "version": {"rui": "0.6.0", "config": 3},
  "devices": {"total": 2, "enabled": 2, "online": 2, "ready": 2},
  "multi_device": {
    "enabled": true,
    "max_planes": 4,
    "default_output": "living",
    "room_priority": ["living", "dot2"],
    "planes_running": 2
  },
  "rooms": [
    {"room": "客厅", "device_ids": ["living"], "online": 1, "default_output": true},
    {"room": "书房", "device_ids": ["dot2"], "online": 1, "default_output": false}
  ],
  "warnings": [
    {"code": "plane_not_running", "device_id": "dot2", "message": "...", "hint": "..."}
  ],
  "setup": {"complete": true, "verified": true, "next_step": "done"}
}
```

规则：

- `devices.online` = `ready` 的数量（enabled + live link；不是 `connected !== false`）。
- `rooms` 由 `devices[].room` 派生；空 room 直接以 `room:""` 作为 key（没有 `unassigned` 别名组）；同一 room 多设备 `device_ids` 多值，并加 `warning code=duplicate_room`。
- `warnings` 是 v1 `_device_warnings` 的 code（`fleet_start_failed`、`over_max_planes`、`plane_not_running`、`multiple_enabled_inbound`）**加 v2 新增 `duplicate_room`**；v2 统一包装为 `{code,device_id?,message,hint}`。
- `setup.complete` 指全屋首次安装完成（至少一台可用）；不等于每台设备都 ready。

### 7.3 `PATCH /api/v2/house`

请求（全部字段可选）：

```json
{
  "multi_device": {
    "enabled": true,
    "max_planes": 4,
    "default_output": "dot2",
    "room_priority": ["living", "dot2"]
  }
}
```

- `default_output: null` = 清除（回落到最近活跃房间）；`room_priority: []` = 清除。
- 校验沿用 `Config._multi_device_references`（`schema.py:1102-1156`）：引用不存在/禁用设备、重复项、超过范围 → `422 invalid_values`。
- `enabled=true` 要求每台 enabled 设备有 room（`schema.py:1132-1142`）。
- 响应：`{"house": {...同 7.2...}, "restart": {"scope":"service","required":true,"auto":false,"status":"pending","restart_fields":["multi_device.enabled", "..."]}}`。
- 不自动重启；UI 显示“需重启服务生效”与现有命令。

### 7.4 `GET /api/v2/house/audio`

```json
{
  "audio": {
    "capture": {"rate": 16000, "channels": 1, "mic_channels": 1, "ref_channels": 0,
                "bits": 16, "mic_shift": 0, "ref_shift": 0},
    "voice": {"rate": 16000, "frame_ms": 20, "vad_min_speech_ms": 200,
              "vad_end_silence_ms": 1000, "kws_enabled": true, "barge_in": true},
    "playback": {"rate": 16000, "channels": 1, "queue_ms": 200, "fade_out_ms": 120,
                 "prebuffer_ms": 0},
    "aec": {"enabled": true, "filter_len": 4096, "cancel": "mix", "who": "controller"}
  },
  "ack": {"echo_ignore_ms": 1200},
  "explicit": ["audio.capture.rate", "ack.echo_ignore_ms"],
  "effects": {"restart": "audio_plane", "fields": ["audio.capture.rate", "..."]}
}
```

- `audio` 序列化等价于 `Config.audio.model_dump(mode="json")`；`voice`/`playback` 字段以 `src/rui/config/schema.py` 的 `VoiceConfig`/`PlaybackConfig` 为准，不裁剪。
- `explicit` 是“配置文件/内存里显式写过”的 dotted path（`model_fields_set` 或值 ≠ 核心默认）；这是 provenance 的全局层依据。
- `effects.fields` 列出本次响应中“改了会重启音频面”的字段（`catalog.py` tier 2 的 restart 字段）。

### 7.5 `PATCH /api/v2/house/audio`

请求：

```json
{
  "audio": {"capture": {"rate": 16000}, "aec": {"who": "controller"}},
  "ack": {"echo_ignore_ms": 1200}
}
```

- 递归 merge；字段 `null` = 从显式配置中移除，回落核心默认（不是写 0/false）。
- 校验合并后的完整 `AudioConfig`/`AckConfig`；非法 → `422 invalid_values`，不写盘。
- 响应同 7.4 + `restart: {scope:"audio_plane", required:true, auto:false, status:"pending", restart_fields:[...]}`。
- 不自动重启；UI 调 7.6。

### 7.6 `POST /api/v2/house/audio/restart`

- 无 body；重启整个 fleet（所有平面），返回：

```json
{"restart": {"scope": "fleet", "required": true, "auto": true, "status": "ok",
             "affected_devices": ["living","dot2"], "duration_ms": 380, "error": ""}}
```

- 失败：HTTP 200 + `restart.status="failed"` + `restart.error`（与 v1 的“配置已保存但平面未起”语义一致）；无平面时 `restart.status="failed"`、`restart.error="audio plane not started"`。
- 与设备写操作共用同一把串行锁（`setup_locks`/重启锁），避免并发重建。

### 7.7 `GET /api/v2/plugins`

```json
{
  "items": [
    {
      "id": "dot2-echomuse",
      "version": "1.2.0",
      "name": "Echo Dot 2 (EchoMuse) plug",
      "vendor": "Amazon",
      "class": "B",
      "trust": "trusted",
      "transport": "inbound",
      "capabilities": ["playback", "led", "keys", "volume", "device_wake", "barge_via_device"],
      "available": true,
      "error": null,
      "devices": [
        {
          "model_id": "dot2",
          "model": "RS03QR",
          "name": "Echo Dot 2",
          "vendor": "Amazon",
          "class": "B",
          "transport": "inbound",
          "capabilities": ["playback", "led", "keys", "volume", "device_wake", "barge_via_device"],
          "params": [
            {"key": "host", "label": "音箱地址", "type": "string", "required": true,
             "placeholder": "<speaker-host>"},
            {"key": "room", "label": "房间名", "type": "string", "default": "客厅"},
            {"key": "wake_words", "label": "唤醒词", "type": "list",
             "default": ["你好小瑞"], "max": 3},
            {"key": "port", "label": "入站端口", "type": "int", "default": 8870},
            {"key": "oww_model", "label": "设备端唤醒模型", "type": "string", "default": ""}
          ],
          "audio_defaults": {"capture": {"rate": 16000, "channels": 1},
                             "ack_echo_ignore_ms": 2500},
          "wake": {"source": "device", "barge_via_device": true},
          "device_side": {"artifact": "firmware", "name": "EchoMuse device firmware (RS03QR)",
                          "source": "https://github.com/yes999zc/echomuse-dot2-ref",
                          "version_min": "v2.18.1", "rollback": "Boot the original firmware slot"}
        }
      ]
    }
  ],
  "total": 2
}
```

- `params` 是 manifest schema；`secret` 类型只给 key/label/required，不给值、不给 default（C-001 禁止 secret default）。
- `available=false` 时 `error` 给原因；坏 manifest 不致命，列表仍返回。
- `class` 仅展示/提示，核心不按 class 分支。

### 7.8 `GET /api/v2/plugins/{plugin_id}`

- 返回同一 plugin 的完整公开 manifest 子集：`schema`、`id`、`name`、`version`、`rui_compat`、`license`、`maintainer`、`homepage`、`trust`、`devices[]`（插件层）与每个 device 的 `id`、`model`、`name`、`vendor`、`class`、`transport`、`protocol`、`capabilities`、`wake`、`audio_defaults`、`device_side`、`runtime`、`params`、`compatibility`、`acceptance`。
- 404 `not_found` 未知 plugin。
- 不返回 `entrypoint` 的可执行细节（trust 边界，C-001 不变）。

### 7.9 `GET /api/v2/capabilities`

见 §5.3；`items` 顺序稳定（核心定义顺序），供 v2 UI 渲染。

### 7.10 `GET /api/v2/devices`

```json
{
  "items": [
    {
      "id": "living",
      "room": "客厅",
      "plugin_id": "xiaoai-lx06",
      "model_id": "lx06",
      "model": "LX06",
      "vendor": "Xiaomi",
      "transport": "inbound",
      "enabled": true,
      "ready": true,
      "status": "ready",
      "connected": true,
      "status_reason": {"code": "ready", "message": "connected", "hint": ""},
      "last_seen": 1761390000.12,
      "latency_ms": 12.4,
      "listen_port": 4412,
      "capabilities": ["mic_array", "reference", "playback", "led", "keys", "volume"],
      "unknown_capabilities": [],
      "audio_summary": {"capture_rate": 16000, "playback_rate": 16000,
                        "aec_who": "controller", "ack_echo_ignore_ms": 1200},
      "warnings": []
    }
  ],
  "total": 2
}
```

- `status`：`ready | unknown | disconnected | disabled | unavailable | error`（`_device_link` 语义，`app.py:1487-1511`）。
- `connected:null` = 没有平面可看（不是离线）；此时 `status="unknown"`，`status_reason.code` 由下面清单按优先级选出（通常是 `audio_plane_stopped` 或 `not_connected`），**不另造 `no_audio_plane`**。
- `status_reason.code` 稳定清单（与 `v2_api.py` 的 `_STATUS_REASONS`/`_REASON_PRIORITY` 一致）：`ready | device_disabled | probe_failed | plug_unresolved | adapter_unavailable | token_missing | audio_plane_error | audio_plane_stopped | not_connected`。多码同时出现时按此顺序取第一个。`token_managed_by_adapter` **只属于 connection 语义，不是 status reason**。
- `ready=true` 仅当 enabled 且 status=ready（`app.py:1797-1872`）。
- 列表最多 8 台，无分页；顺序：异常优先（status != ready）→ room → id；前端可重排，但服务端顺序必须稳定。

### 7.11 `POST /api/v2/devices`

请求：

```json
{
  "id": "study",
  "room": "书房",
  "plugin_id": "dot2-echomuse",
  "model_id": "dot2",
  "host": "speaker-a.local",
  "listen_port": null,
  "wake_words": ["你好小瑞"],
  "volume_target": 60,
  "enabled": true,
  "params": {"oww_model": ""}
}
```

规则：

- `id` 必填、slug、唯一；重复 → `409 device_exists`（这是幂等规则的显式例外，见 §4.7）。
- `plugin_id` 必填；`model_id` 是**设备 manifest 的 `id`**（Dot2 为 `dot2`，不是硬件字符串 `RS03QR`）；插件只有一个 device manifest 时可省略，多个时省略 → `422 model_required`；不存在 → `409 plug_unavailable`。`DeviceConfig.adapter` 存的就是这个 id（`resolve.py:14-18`），`model` 只用于展示。
- `room`：当 `multi_device.enabled=true` 时必填（`schema.py:1132-1142`）；否则可空。
- `host` 必填（现有 `_host` 校验：非空、无协议、≤64、无空格/控制字符）。
- `wake_words` 0–3 个、每个 ≤12 字；空 = 继承默认 `["你好小瑞"]`。
- `volume_target` 0–100；`listen_port` 1–65535 或 null（manifest runtime 默认）。
- `params` 兼容规则（重要）：
  - 只放 manifest 声明的非 secret 参数（Dot2：`host/room/wake_words/port/oww_model` 中未提升为顶层字段的项）。
  - **读取**：`devices[].params` 里 manifest 未声明的 key（例如 v1 向导遗留的 `mic_gain_db`）**原样保留**，在 `DeviceDetail.unknown_params` 列出；不得丢弃。
  - **写入**：请求里 manifest 未声明的 key **不报 422**，忽略且不删除已存值；响应的 `device.warnings` 增加 `{code:"unknown_params_ignored", params:[...], message, hint}`。只有 manifest 已声明 key 的类型/范围错误才 `422 invalid_values`。
- 设备 token 不在 create 请求里，分两步：`POST /api/v2/devices` 创建 → `POST /api/v2/devices/{id}/token/rotate` 生成/轮换，并写入 manifest `runtime.token_env` 指向的 token 环境（Dot2：`DOT2_EM_TOKEN`，见 `device_rules.py:82-105`）。禁止把 token 塞进 `devices[].params`，也不要为它新增 manifest secret param 或写进 `secrets.yaml` 的 secret 字段。
- 端口冲突 → `422 port_conflict`，`details` 必须符合 §4.4 的统一形状：`fields:[{path:"host", code:"port_conflict", message:"..."}]`，并附 `port`/`device_ids`（实现复用 `device_rules.py:158-168`）。
- 成功 `201`：`{"device": {...DeviceDetail，见 7.12...}, "restart": {...Restart...}}`；`restart.scope="fleet"`、`auto=true`。
- `POST` 与 setup `params` 步的 `device.create` **必须走同一个应用服务**，不得两套校验。

### 7.12 `GET /api/v2/devices/{id}`

```json
{
  "id": "dot2",
  "room": "书房",
  "plugin_id": "dot2-echomuse",
  "model_id": "dot2",
  "model": "RS03QR",
  "vendor": "Amazon",
  "transport": "inbound",
  "enabled": true,
  "ready": true,
  "status": "ready",
  "connected": true,
  "status_reason": {"code": "ready", "message": "connected", "hint": ""},
  "host": "speaker-a.local",
  "listen_port": 8870,
  "last_seen": 1761390000.12,
  "latency_ms": 18.0,
  "wake_words": ["你好小瑞"],
  "volume_target": 60,
  "params": {"oww_model": ""},
  "capabilities": [
    {"id": "playback", "declared": true, "probed": true, "effective": true,
     "known": true, "reason": "ok"}
  ],
  "effective_capabilities": ["playback", "led", "keys", "volume"],
  "unknown_capabilities": [],
  "missing_for_barge_in": ["reference"],
  "audio": {"effective": {}, "sources": {}, "layers": {}, "overrides": [],
            "manifest_defaults": {}, "plug_resolved": true},
  "plane": {
    "running": true, "speaker_connected": true, "speaker_rejected": 0,
    "restarts": 0, "pid": 1234, "error": "",
    "wake_candidates": 12, "wake_admitted": 10, "wake_rejected": {},
    "utterances": 8
  },
  "setup": {"configured": true, "last_test_ok": true,
            "last_test_at": "2026-10-25T21:00:00+08:00"},
  "warnings": []
}
```

- `audio` 形状见 §8；`params` 只公开 manifest 声明的非 secret 键；token 不在此响应中出现。
- `plane` 字段来自该设备自己的平面（`app.py:1599-1652`，`pid` 在 `1652`），不是主平面；`pid` 可空。
- `missing_for_barge_in` 沿用 `DeviceInfo.missing_for_barge_in()`（`contract.py:185-203`）。
- 404 `not_found`。

### 7.13 `PATCH /api/v2/devices/{id}`

请求（全部可选；一次原子写）：

```json
{
  "room": "书房",
  "host": "speaker-a.local",
  "enabled": true,
  "wake_words": ["你好小瑞"],
  "volume_target": 60,
  "listen_port": 8870,
  "params": {"oww_model": ""},
  "audio": {"voice": {"ack_echo_ignore_ms": 2500}}
}
```

- 不可变字段：`id`、`plugin_id`、`model_id`、`adapter`；出现 → `422 immutable_field`（`details.field`）。
- `params`：对象按 key merge；`null` 删除该键；**manifest 未声明的 key 忽略、不报 422、不删除已存值**（同 §7.11 兼容规则，`device.warnings` 回 `unknown_params_ignored`）；manifest 已声明 key 的类型/范围错误才 422；当前内置 manifest 没有 secret param，接口不接受 secret。
- `audio`：对象按字段 merge；`null` 清除全部覆盖；省略 = 不动；merge 规则以 §7.16 为唯一来源。
- `enabled=false` 最后一台 enabled → 停平面（`restart.status="stopped"`）；`enabled=true` 端口冲突 → `422 port_conflict`（`details.fields[].path="host"`，复用 `device_rules.py:158-168`）。
- 成功 `200`：`{"device": {...DeviceDetail...}, "restart": {...Restart...}}`；写盘成功/平面失败 → 200 + `restart.status="failed"`。
- 不允许通过此端点改 `token` 值（只有 rotate/reveal 路径）；token 仍由 `POST /devices/{id}/token/rotate` 写入 `token_env`。

### 7.14 `DELETE /api/v2/devices/{id}?confirm=<id>`

- 缺 `confirm` / 不等于 `id` → `409 confirm_required`：

```json
{"error":{"code":"confirm_required","message":"confirm the device id to delete",
 "details":{"impact":["The audio fleet restarts.","The device will not re-register by itself.",
                     "Removing the last enabled device stops the audio plane."]},
 "retryable":false}}
```

- 成功 `200`：`{"removed": true, "device_id": "dot2"}`（只返回这两个字段；删除仍会重建 fleet，UI 刷新 `/api/v2/house` 取最新状态）。
- 删除后设备若仍拨入会持续失败；不做 pending/自动重新登记（C-001 无 approve 队列）。

### 7.15 `GET /api/v2/devices/{id}/audio`

以下示例是**同一台 Dot2** 的 profile（manifest `id: dot2`，capture 16000/1ch、playback 48000/1ch、ack 2500；全局 `capture.rate=16000` 显式，playback 未显式；设备覆盖 AEC 与 ack）：

```json
{
  "effective": {
    "capture": {"rate": 16000, "channels": 1, "mic_channels": 1, "ref_channels": 0,
                "bits": 16, "mic_shift": 0, "ref_shift": 0},
    "voice": {"rate": 16000, "frame_ms": 20, "vad_end_silence_ms": 1000},
    "playback": {"rate": 48000, "channels": 1, "queue_ms": 200},
    "aec": {"enabled": false, "who": "device", "filter_len": 4096, "cancel": "mix"},
    "ack_echo_ignore_ms": 2500
  },
  "sources": {
    "capture.rate": "global-config",
    "playback.rate": "device-manifest",
    "aec.who": "device-override",
    "aec.enabled": "device-override",
    "ack.echo_ignore_ms": "device-override"
  },
  "layers": {
    "capture.rate": {"effective": 16000, "source": "global-config",
                     "core": 16000, "manifest": 16000, "global": 16000,
                     "global_explicit": true, "device": null},
    "playback.rate": {"effective": 48000, "source": "device-manifest",
                      "core": 16000, "manifest": 48000, "global": 16000,
                      "global_explicit": false, "device": null},
    "aec.who": {"effective": "device", "source": "device-override",
                "core": "controller", "manifest": null, "global": "controller",
                "global_explicit": true, "device": "device"},
    "ack.echo_ignore_ms": {"effective": 2500, "source": "device-override",
                           "core": 1200, "manifest": 2500, "global": 1200,
                           "global_explicit": true, "device": 2500}
  },
  "overrides": ["aec.who", "aec.enabled", "voice.ack_echo_ignore_ms"],
  "manifest_defaults": {"capture": {"rate": 16000, "channels": 1},
                        "playback": {"rate": 48000, "channels": 1},
                        "ack_echo_ignore_ms": 2500},
  "plug_resolved": true
}
```

**provenance 规则（必须逐字段实现）**

| 优先级 | `source` | 判定 |
|---|---|---|
| 1 | `device-override` | `devices[].audio.<path>` 显式且非 null |
| 2 | `global-config` | 全局 `audio.<path>`（或 `ack.echo_ignore_ms`）显式：`model_fields_set` 包含 或 值 ≠ 核心默认 |
| 3 | `device-manifest` | manifest `audio_defaults.<path>` 非 null |
| 4 | `core-default` | 以上都无 |
| — | `unresolved` | 插件不可解析，且该字段既无 device 也无 global 显式值（无法知道 manifest 本应声明什么） |

- 示例只列 4 个 leaf 以说明来源；实际 `sources/layers` 覆盖**全部 leaf**（capture/voice/playback/aec 的所有字段 + ack）。
- `layers` 同时给四层原始值 + `global_explicit`；UI 不得用“值相等”推断继承（显式设备值可以等于全局值，`app.py:1113-1115`）。
- `voice`/`aec` 没有 manifest 默认层，`manifest` 为 null；`playback`/`capture` 有。
- `ack_echo_ignore_ms` 特殊：device 覆盖路径是 `voice.ack_echo_ignore_ms`，manifest 是顶层 `ack_echo_ignore_ms`，global 是 `ack.echo_ignore_ms`；`sources` 的 key 统一为 `ack.echo_ignore_ms`，`overrides` 里保留 `voice.ack_echo_ignore_ms`。
- `overrides` 是相对于 `audio` 的路径（`capture.rate`、`voice.ack_echo_ignore_ms`），供 UI 标记覆盖。
- `effective` 用 `effective_device_audio()` / `effective_device_ack_echo_ignore_ms()` 计算（`schema.py:650-661`），不得在 v2 重新实现 merge。

### 7.16 `PATCH /api/v2/devices/{id}/audio`

- body 为 `audio` 子对象（与 §7.13 的 `audio` 字段同一形状；merge 规则只在本节定义）：

```json
{"audio": {"capture": {"rate": 48000}, "voice": {"ack_echo_ignore_ms": 2500}}}
{"audio": {"aec": {"who": null}}}
{"audio": null}
```

- 语义：对象=递归 merge（`null` 清除单字段）；`audio:null`=清除全部覆盖（`devices[].audio=None`）；省略=不动。
- 校验合并后的 `DeviceAudioOverride` + `effective_device_audio`；非法 → 422，不写盘。
- 响应：`{"audio": {...同 7.15...}, "device": {...DeviceDetail...}, "restart": {...fleet...}}`。
- 与 7.13 `PATCH` 共用同一把锁与同一写路径；不要两次写盘。

### 7.17 `GET /api/v2/devices/{id}/connection`

```json
{
  "device_id": "dot2",
  "transport": "inbound",
  "configured": true,
  "token_configured": true,
  "token_managed_by_adapter": false,
  "token_env": "DOT2_EM_TOKEN",
  "allow_no_token": false,
  "listen_port": 8870,
  "ws_url_masked": "wss://nas.local:8870/?token=••••••••",
  "host_source": "request",
  "hint": {"zh": "把 ws_url 填到设备侧；token 已包含", "en": "Paste ws_url into the device client; the token is included"},
  "host_hint": null
}
```

- inbound：`ws_url_masked` 保留 host/port/scheme，token 参数替换为 `••••••••`；`transport="outbound"` 时 `ws_url_masked=null`，hint 说明“服务器主动拨号，无需客户端 URL”。
- **adapter-managed token**：当设备已连接但 rui 的 token store 没有值（`token_managed_by_adapter=true`）时，`configured=true`、`token_configured=false`、`token_env` 仍是 manifest 的 runtime token_env（Dot2：`DOT2_EM_TOKEN`）；`hint` 固定为 `{"zh":"token 由设备适配器管理，请在设备侧轮换","en":"The token is managed by the device adapter; rotate it on the device side"}`。这不是 error，也不建议写 `secrets.yaml`。
- 不返回 token 值、不返回明文查询串。
- `host_source`：`request | server`（沿用现有 loopback 处理）；`host_hint` 为 `{zh,en}` 或 null。

### 7.18 `POST /api/v2/devices/{id}/connection/reveal`

- 无 body；响应：

```json
{"device_id":"dot2","ws_url":"wss://nas.local:8870/?token=abc...","token":"abc...",
 "token_env":"DOT2_EM_TOKEN","configured":true,"allow_no_token":false,
 "host_source":"request","hint":{"zh":"...","en":"..."}}
```

- `Cache-Control: no-store`；日志只记 device id/token_env，不记值。
- **adapter-managed token 优先判断**：`token_managed_by_adapter=true`（已连接但 rui 无 token）→ `409 token_managed_by_adapter`，`details:{device_id,token_env}`，hint 固定「token 由设备适配器管理，请在设备侧轮换」；不得返回任何 token，也不得写 `secrets.yaml`。
- 否则：无 token 且 `allow_no_token=false` → `409 token_missing` + hint；`allow_no_token=true` → `token:""`，`ws_url` 仍按现有 `_device_ws_url` 带 `?token=`（空值），并带“同网可开麦”的安全警告 hint。
- 速率限制：每设备 10 次/分钟 → `429 rate_limited`。

### 7.19 `POST /api/v2/devices/{id}/token/rotate`

- 无 body；响应 `{"token":"...","ws_url":"...","token_env":"..."}`；只显示一次（UI 关闭即丢）。
- **adapter-managed token 优先判断**：`token_managed_by_adapter=true`（已连接但 rui 无 token）→ `409 token_managed_by_adapter`，`details:{device_id,token_env}`，hint 固定「token 由设备适配器管理，请在设备侧轮换」；**不得写 `secrets.yaml`**，也不返回新 token。
- 否则：若 token 由进程环境提供 → `409 token_from_environment`，`details.token_env`；提示改环境后重启。
- 冷却：**10 秒**内重复 → `429 rate_limited` + `Retry-After` + `details.retry_after_s`；复用现有 `app.state.token_rotations`（`app.py:2067`，`TOKEN_ROTATE_INTERVAL_S=10.0`）。
- 轮换后不自动重启设备；设备下次重连需新 token（UI 必须提示）。
- 不记录 token 日志。

### 7.20 `POST /api/v2/devices/{id}/test`

- 无 body；证据式、不写配置、不播放、不改灯/音量。
- 响应：

```json
{
  "ok": false,
  "ready": false,
  "waited_ms": 3000,
  "checked_at": 1761390000.12,
  "transport": "inbound",
  "checks": [
    {"id": "plugin", "status": "ok", "code": "plug_resolved",
     "detail": {"plugin_id": "dot2-echomuse", "model_id": "dot2"}, "hint": ""},
    {"id": "link", "status": "fail", "code": "device_not_connected",
     "detail": {"connected": false, "plane_running": true, "listen_port": 8870},
     "hint": "Start the device-side client and confirm it dials the port."},
    {"id": "plane", "status": "not_applicable", "code": "not_applicable",
     "detail": {"transport": "outbound"}, "hint": ""},
    {"id": "token", "status": "ok", "code": "token_configured",
     "detail": {"token_env": "DOT2_EM_TOKEN", "managed_by_adapter": false}, "hint": ""},
    {"id": "capabilities", "status": "ok", "code": "capabilities_declared",
     "detail": {"effective": ["playback","led","keys","volume"],
                "missing_for_barge_in": ["reference"]}, "hint": ""}
  ],
  "errors": [{"code": "device_not_connected", "message": "...", "hint": "..."}]
}
```

- `checks[].status`: `ok | fail | not_applicable | skipped`；`code` 稳定；`detail` 不含 secret。
- checks 的组合与 code：
  - `plugin`：始终出现；`plug_resolved | plug_unresolved`。
  - `link`：始终出现；inbound `connected | device_not_connected`，outbound `host_reachable | device_unreachable`。
  - `plane`：仅 outbound 出现，`not_applicable`（inbound 不出现）。
  - `token`：`token_configured | token_missing`（adapter-managed 时 `managed_by_adapter=true`，仍 `token_configured`）。
  - `capabilities`：`capabilities_declared | capabilities_unknown`。
- inbound 的 `link` 以平面 live link 为准，不拿 SSH/TCP 探活替代；等待最多 3 秒（现有 `DEVICE_TEST_WAIT_S`）。
- outbound 的 `link` 用 host/probe；`plane` 必须为 `not_applicable`。
- 顶层 `ok = all(check.status in {ok, not_applicable, skipped})`；`ready` 同设备状态。
- 测试结果可缓存到 setup store（现有 last_test），但响应必须实时计算。

### 7.21 设备动作（见 §5.4）

请求/响应示例：

```json
// POST /api/v2/devices/dot2/actions
{"action":"volume.set","args":{"value":120}}

{"action":"volume.set","ok":true,"code":"ok",
 "result":{"value":120},"device_status":{"ready":true,"connected":true}}
```

- 动作执行后不刷新设备详情；UI 需要时重拉 7.12。
- `audio.say` 结果额外带 `turn`：

```json
{"action":"audio.say","ok":true,"code":"ok",
 "result":{"reply":"现在 21:03","audio_bytes":91234,"tools":["time"],"error":""},
 "turn":{"turn_id":"...","device_id":"dot2"}}
```

### 7.22 `GET /api/v2/devices/{id}/activity`

查询：`limit`（默认 50，1–200）、`type`（`all|turn|log`，默认 all）。

```json
{
  "items": [
    {"id": "turn:abc123", "ts": 1761390000.12, "type": "turn", "level": "info",
     "title": "把客厅灯关了", "detail": {"reply": "已关闭", "tools": ["local.light_off"],
     "error": ""}, "matched_by": "device_id"},
    {"id": "log:1761390001", "ts": 1761390001.0, "type": "log", "level": "warn",
     "title": "dot2 link: still waiting for client", "detail": {"line": "..."},
     "matched_by": "literal"}
  ],
  "total": 2,
  "truncated": false
}
```

- `turn` 来自最近回合（`recent_turns`/trace），`matched_by="device_id"`；v1 `/turns` 新增可选 `device_id`（additive：不删/不改旧字段，旧消费者可忽略），v2 activity 直接复用。
- `log` 来自 `service.log` 尾部，按 device id 或 room 字面匹配，`matched_by="literal"`；UI 必须标注“文本匹配，非结构化”。
- 排序：`ts` 降序；同 ts 按 `type` turn 优先。
- 404 设备不存在；无事件返回空 items，不是 404。

### 7.23 `GET /api/v2/setup/plan`

查询：`plugin_id`（无设备时必填）、`device_id`（add 模式的 draft id 或已有 id）、`model_id`（可选）。

响应：

```json
{
  "mode": "add",
  "device_id": "study",
  "plugin": {"id": "dot2-echomuse", "version": "1.2.0", "name": "Echo Dot 2"},
  "steps": [
    {"id": "keys", "scope": "house", "required": true, "status": "done",
     "evidence": {"stages": {"asr": true, "tts": true, "llm": true}, "missing": []},
     "actions": ["keys.save", "keys.test", "providers.save"]},
    {"id": "models", "scope": "house", "required": true, "status": "done",
     "evidence": {"asr": true, "vad": true, "kws": true}, "actions": []},
    {"id": "prepare", "scope": "device", "required": true, "status": "pending",
     "evidence": {"confirm": false, "rollback_ack": false},
     "detail": {"links": [{"label": "设备侧部署说明", "url": "https://..."}],
                "install_steps": ["..."]},
     "actions": ["manual.confirm"]},
    {"id": "params", "scope": "device", "required": true, "status": "pending",
     "evidence": {"device_id": null},
     "detail": {"params_schema": [], "defaults": {"room": "书房"}},
     "actions": ["device.create"]},
    {"id": "wake", "scope": "device", "required": true, "status": "pending",
     "evidence": {"words": [], "keywords_exists": false}, "actions": ["wake.set"]},
    {"id": "connect", "scope": "device", "required": true, "status": "pending",
     "evidence": {"configured": false, "ws_url_masked": null}, "actions": []},
    {"id": "test", "scope": "device", "required": true, "status": "pending",
     "evidence": {"ok": false, "checks": []}, "actions": ["device.test"]},
    {"id": "verify", "scope": "device", "required": false, "status": "pending",
     "evidence": {"text_tested": false}, "actions": ["verify.say"]}
  ],
  "next_step": "prepare",   // string | null；全部 done 时为 null
  "complete": false,
  "verified": false,
  "blocked": null
}
```

规则：

- 步骤顺序固定：`keys, models, prepare, params, wake, connect, test, verify`（与现有 8 步一致，`setup_api.py:571-572`）；`verify` 可选。
- `mode`：`first-run`（house 无设备）、`add`（house 有设备且 `device_id` 不存在）、`edit`（`device_id` 已存在；只用于修配置，UI 不把它当向导入口）。`add` 模式下 `keys`/`models` 必须 done，否则 `blocked={"code":"house_setup_incomplete","message":...}`。
- `mode=edit` 的 `params` 步 action 是 `device.update`（PATCH 语义，必须已有 `device_id`）；`mode=add/first-run` 才是 `device.create`。`edit` 不创建第二台设备。
- `device_id` 必须是合法 slug；不创建草稿，不写配置；不存在是正常 draft。
- `plugin_id`：`add/first-run` 必填；`edit` 可省略（从 device 推断）；两者都给但不匹配 → `409 plug_mismatch`。
- `model_id`：可选；插件有多个 device manifest 时必填，否则 `422 model_required`（与 §7.11 一致）。
- `params` 步的 `detail.params_schema` 来自 manifest；`defaults` 只给非 secret 默认 + 建议 room（不强制）。
- `blocked` 只用于无法继续的全局原因（`plug_unavailable`：manifest 加载失败或 `rui_compat` 不匹配；`house_setup_incomplete`：house keys/models 未完成）；步骤级失败放 `status:error` + `evidence`。
- **HTTP 语义**：`GET /setup/plan` 遇 `plug_unavailable` 返回 **200 + `blocked`**（不是 409）；只有写操作（`POST /devices` 等）才用 409 `plug_unavailable`。

### 7.24 `POST /api/v2/setup/plan/steps/{step}`

请求：

```json
{
  "plugin_id": "dot2-echomuse",
  "device_id": "study",
  "action": "device.create",
  "values": {
    "id": "study", "room": "书房", "host": "speaker-a.local",
    "wake_words": ["你好小瑞"], "volume_target": 60,
    "params": {"oww_model": ""}
  }
}
```

- `step` 必须是 8 个之一；`action` 必须属于该 step 的允许集合；否则 `400 unknown_step` / `400 action_not_allowed`。
- 同一 step 并发 → `409 step_in_progress`（每 step 一把锁）；`params` 成功创建后，后续 step 的 `device_id` 自动可解析。
- 响应：

```json
{
  "plan": {"...同 7.23，更新后的状态..."},
  "device": {"...DeviceDetail 或 null..."},
  "restart": {"scope":"fleet","required":true,"auto":true,"status":"ok",
              "affected_devices":["living","dot2","study"],"duration_ms":450,"error":""}
  // 或 "restart": null（keys/models/connect/test/verify 等不写设备配置的 step）
}
```

- action 表：

| step | action | values | 说明 |
|---|---|---|---|
| keys | `keys.save` | `{values: {ENV: "..."}}` | write-only；成功后可跟 `keys.test` |
| keys | `keys.test` | `{stage: string, provider?: string, keys?: Record<string,string>, values?: Record<string,string>}`（`keys` 是实际字段，`values` 是 v1 兼容别名） | 不写配置；`keys/values` 只用于本次临时测试，不持久化 |
| keys | `providers.save` | `{stage, provider, voice?}` | 写 provider 链 |
| prepare | `manual.confirm` | `{confirm:true, rollback_ack?:bool}` | class B 必须 `rollback_ack=true` |
| params | `device.create` | 同 7.11（无 secret；token 走两步 rotate） | 仅 `mode=add/first-run`；重复 id → 409 `device_exists` |
| params | `device.update` | 同 7.13（PATCH 语义） | 仅 `mode=edit`；设备必须已存在 |
| wake | `wake.set` | `{wake_words:[...]}` | 写 `devices[].wake_words` + 重生成 keywords + fleet 重启 |
| connect | — | — | 只读；没有 action |
| test | `device.test` | `{}` | 等价 7.20；写 setup evidence，不改配置 |
| verify | `verify.say` | `{text:"..."}` | 走 7.21 `audio.say`；写 evidence |

- `device.create` 后返回的 `device` 必须与 7.12 同形；UI 据 `plan.next_step` 继续。
- step `status` 只有 `pending|done|skipped|error`；`next_step` 可为 `null`；不写配置的 step 返回 `restart:null`。
- 首次安装（`mode=first-run`）与 add 模式共用端点；旧 `/api/v1/setup/step` 保持不动，v2 不调用它。
- 错误码补充：`house_setup_incomplete`（409）、`plug_mismatch`（409）、`plug_unavailable`（409）、`device_exists`（409）、`missing_prerequisite`（422，`details.step`）、`step_in_progress`（409）。

---

## 8. 共享 Schema

### 8.1 `DeviceSummary`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 设备 id |
| `room` | string | 是 | 可空串 |
| `plugin_id` | string | 是 | 解析后的插头 id |
| `model_id` | string | 是 | manifest device id |
| `model` | string | 是 | 硬件型号（展示） |
| `vendor` | string | 是 | 厂商 |
| `transport` | `inbound|outbound` | 是 | |
| `enabled` | bool | 是 | |
| `ready` | bool | 是 | enabled 且 live |
| `status` | enum | 是 | 见 7.10 |
| `connected` | bool|null | 是 | null = 无平面 |
| `status_reason` | object | 是 | `{code,message,hint}` |
| `last_seen` | number|null | 是 | Unix 秒 |
| `latency_ms` | number|null | 是 | |
| `listen_port` | int|null | 是 | |
| `capabilities` | string[] | 是 | effective ids |
| `unknown_capabilities` | string[] | 是 | 未知 id |
| `audio_summary` | object | 是 | `capture_rate/playback_rate/aec_who/ack_echo_ignore_ms` |
| `warnings` | object[] | 是 | `{code,message,hint}` |

### 8.2 `DeviceDetail`

= `DeviceSummary` +：

| 字段 | 类型 | 说明 |
|---|---|---|
| `host` | string | LAN 地址/主机名 |
| `wake_words` | string[] | 0–3 |
| `volume_target` | int | 0–100 |
| `params` | object | 公开参数（manifest 声明 + 保留的 legacy 未知 key） |
| `unknown_params` | string[] | `devices[].params` 中 manifest 未声明的 key（原样保留，只读展示） |
| `capabilities` | object[] | 见 §5.2；覆盖 Summary 的 string[] |
| `effective_capabilities` | string[] | 便捷过滤 |
| `missing_for_barge_in` | string[] | |
| `audio` | object | 见 §7.15 |
| `plane` | object | `running/speaker_connected/speaker_rejected/restarts/pid/error/wake_candidates/wake_admitted/wake_rejected/utterances` |
| `setup` | object | `configured/last_test_ok/last_test_at` |
| `warnings` | object[] | |

### 8.3 `Error`

统一 `{error:{code,message,hint,details,retryable},request_id}`；`details` 任意 JSON，但 422 必须是 `{fields:[{path,code,message}]}`。

### 8.4 `Restart`

`{scope:"fleet"|"service"|"audio_plane", required:bool, auto:bool, status:"ok"|"failed"|"stopped"|"pending", affected_devices:string[], duration_ms:int|null, error:string, restart_fields?:string[]}`。

---

## 9. 错误码表

| HTTP | code | 何时 | `details` |
|---|---|---|---|
| 400 | `bad_request` | JSON 不合法/缺 body | `{}` |
| 400 | `unknown_action` | 动作 id 不认识 | `{action, allowed[]}` |
| 400 | `action_not_allowed` | setup step 不允许该 action | `{step, action, allowed[]}` |
| 400 | `unknown_step` | setup step 不存在 | `{steps:string[]}` |
| 400 | `secret_rejected` | token 写入被服务端凭据存储拒绝 | `{token_env}` |
| 401 | `unauthorized` | 缺/错 token | `{}` |
| 403 | `path_not_allowed` | play 文件越界 | `{}` |
| 404 | `not_found` | 设备/插件不存在 | `{resource, id}` |
| 409 | `device_exists` | create 重复 id | `{id}` |
| 409 | `confirm_required` | delete 未确认 | `{impact[]}` |
| 409 | `device_disabled` | 对 disabled 设备执行 action | `{id}` |
| 409 | `device_not_connected` | 需要 live link 的 action | `{id}` |
| 409 | `capability_unsupported` | 设备未声明能力 | `{capability, declared[]}` |
| 409 | `plug_unavailable` | 插头不可用（写操作；`GET /setup/plan` 用 200+blocked） | `{plugin_id, error}` |
| 409 | `plug_mismatch` | setup 的 plugin/device 不匹配 | `{plugin_id, device_id}` |
| 409 | `house_setup_incomplete` | add 模式但 house keys/models 未完成 | `{missing_steps[]}` |
| 409 | `step_in_progress` | 同 step 并发 | `{step}` |
| 409 | `token_managed_by_adapter` | 已连接但 token 由适配器管理；reveal/rotate 拒绝 | `{device_id,token_env}`，hint「token 由设备适配器管理，请在设备侧轮换」 |
| 409 | `token_from_environment` | rotate 时 token 来自 env | `{token_env}` |
| 409 | `no_config` | 没有可写 config | `{}` |
| 422 | `invalid_values` | 字段/类型/范围/模型选择等一般校验失败 | `{fields:[{path,code,message}]}` |
| 422 | `port_conflict` | 两个 enabled inbound 设备共用生效端口 | `{fields:[{path:"host",code:"port_conflict",message}],port,device_ids}`（现有 `device_rules.py:158-168`） |
| 422 | `immutable_field` | 改 id/plugin/model/adapter | `{fields:[{path:"<field>",code:"immutable",message}]}` |
| 422 | `model_required` | 插件多机型但未给 model_id | `{fields:[{path:"model_id",code:"required",message}]}` |
| 422 | `missing_prerequisite` | setup 前序未完成 | `{fields:[{path:"step",code:"missing_prerequisite",message}],missing[]}` |
| 429 | `rate_limited` | 认证失败/轮换/reveal 过频 | `{retry_after_s}`；带 `Retry-After` |
| 502 | `action_failed` | 适配器执行失败但设备在线 | `{action, error}` |
| 503 | `unavailable` | 服务/音频面尚未就绪，重试可恢复 | `{reason}` |
| 500 | `internal` | 未预期异常 | `{}` |

所有错误 `retryable` 明确：4xx 默认 false（429 true，502 true，503 true）；503 的 `retryable=true`。

---

## 10. 兼容、发布与回滚

### 10.1 兼容窗口

- `>=0.6.0 <0.7.0`：`/api/v2` 只增不改；字段不删除、不重命名、不改语义；`meta.features` 用于渐进点亮。
- v1 端点与形状保持向后兼容；唯一已知 additive 是 `/api/v1/turns` 每条新增可选 `device_id`（不删不改旧字段，旧消费者可忽略）。v2 不修改 `GET /api/v1/devices`、`PUT /api/v1/devices/{id}`、`/api/v1/setup/step` 的既有字段。
- C-001 不变：v2 只消费 manifest 的 `capabilities/params/audio_defaults/runtime/device_side`，不新增字段。

### 10.2 发布顺序

1. 后端合入 `/api/v2` 全部 24 个端点；`meta.features` 全部为 `true`（代码按 T1→T5 顺序交付，但对外契约按全量点亮）。
2. 前端 v2 M1 上线到 `/v2`（旧 `/` 不变）。
3. create/patch/audio/setup/activity 按契约测试验收；`features` 仅作为 0.7 前紧急关闭开关。
4. 0.6.x 末：v2 功能对等并通过验收；`/` 仍旧；旧控制台侧栏隐藏 `/panel/devices`（只改 `nav.hidden`，旧页 URL 仍可直达）。
5. 0.7.0：`/` → `/v2`；旧 shell `/legacy` 保留一个版本；删除 `/panel/devices`（重定向 `/devices`）与可选 legacy setup 端点。

### 10.3 回滚

| 层 | 回滚动作 | 影响 |
|---|---|---|
| 前端 | 回退镜像/恢复旧 `index.html` 为默认 | `/v2` 仍在但无人使用；无数据迁移 |
| 后端 | `meta.features` 全部关闭 | v2 显示“未启用”；v1 正常 |
| 配置 | 本契约不写新配置字段；设备配置可 `git`/备份回退 | 无 schema 迁移 |
| 数据 | 无 DB/无新文件；仅 `config.yaml`/`secrets.yaml` 原子写 | 备份恢复即可 |
| 0.7 默认切换 | 路由开关从 `/v2` 改回旧 shell；`/legacy` 变回 `/` | 旧 URL 仍可解析 |

### 10.4 测试要求（后端）

- 契约测试覆盖：每个端点的 200/4xx 形状、错误码、`request_id`、`X-Auth-Token` 缺失 401。
- capability：declared/probed/effective 三种组合；led/keys/volume manifest-only；unknown 不崩溃；未声明能力 action 返回 `capability_unsupported`。
- provenance：device-override、global-config（显式）、device-manifest、core-default、unresolved；显式设备值等于全局仍报 device；ack 三层；null 清除。
- 创建：重复 id 409；无 model_id 多机型 422；端口冲突 422；最后一台 disabled/删除 → stopped。
- setup add：已有 2 台时创建第 3 台，前两台字节级不变；house keys/models 未完成 → blocked；重复 create 409。
- secret：所有响应不含 token/secret；reveal 之外不出现 token；日志不含。
- v1 回归：现有 `tests/test_panels.py`、`tests/test_devices_management.py`、`tests/test_setup_api.py`、`tests/test_config_v3.py` 全绿。

建议新增 `tests/test_console_v2_api.py`（契约）与 `tests/test_console_v2_provenance.py`（来源矩阵）；前端 `npm run typecheck && npm run build`。

---

## 11. 与 v1 的映射（实现提示，不是对外契约）

| v2 端点 | 可复用的 v1 内部 | 备注 |
|---|---|---|
| `GET /house` | `_audio_fleet`、`_audio_devices_health`、`_device_warnings` | 重新组装，不改 v1 |
| `GET /devices` | `devices()` 的 `_device_link`/`capabilities` | 加 plugin/model id、status_reason |
| `GET /devices/{id}` | `_device_detail` / `_device_status` | v2 重新命名，不复用响应 |
| `POST/PATCH/DELETE` | `_write_devices`、`_restart_audio`、`_merge_params` | 统一应用服务，避免双写 |
| `/audio` | `effective_device_audio`、`_device_audio_summary` | 新增 sources/layers |
| `/actions` | `_client_for`、`device_volume`、`debug_led`、`_run_debug_say`、`debug_play` | 按 capability 门禁 |
| `/setup/plan` | `setup_api._setup_state`、`setup_store` | 新形状，旧端点不动 |
| `/activity` | `recent_turns`、`traces`、`tail_lines` | turn 精确，log 字面 |

---

## 12. 验收清单（reviewer 用）

1. 契约里每个端点都有方法/路径/请求/响应/错误码，且没有“视情况而定”。
2. `capabilities` 全部来自 manifest；v2 前端 grep 不到机型字面量。
3. provenance 的五种 source 与 ack 特例都能在测试中复现。
4. 0.6.x 不改 v1；`meta.features` 可关闭全部 v2 能力。
5. 设备写入返回 `restart`，UI 有“重启全屋音频面”的确认点。
6. secret 不出现在任何响应/日志/截图。
7. `/v2` 深链刷新可开；`/api/v2` 404 不落 SPA。
8. 回滚路径不依赖数据迁移。
