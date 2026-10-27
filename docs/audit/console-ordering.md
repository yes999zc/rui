# v2 控制台分组上下顺序审计（2026-10-27）

> 审计 B。只给**顺序/入口建议**，不改前端、catalog、既有 console 文档。
> **本版以 v2 为准**：v2 与旧面板是两套渲染，旧面板（`/api/v1/panels` + `PanelHost`）
> 只在 §2.8 作为「另一套界面」并列说明，不再当成 v2 事实。
> 判据来自 9 哥：布局样式不动，只调上下顺序；日常会主动改的往上提，硬核参数往下走。

## 0. 前提核对

### 0.1 唤醒应答在 v2 语音助手页第 8 组（末位）

9 哥 说的「唤醒应答在语音助手页最下面、首屏看不到」**是准确的**。证据链：

| 事实 | 证据 |
|---|---|
| v2 语音助手页允许的组：`assistant, session, audio.capture, audio.voice, audio.playback, audio.aec, ack`；`showGroup()` 还放行 `devices` 与所有 `audio.*` | `web/src/v2/views/VoiceAssistantView.vue:74-88` |
| 该页用 `orderedGroups()` 取组，**不排序**，按服务端返回的 catalog 数组顺序渲染 | `web/src/v2/views/VoiceAssistantView.vue:90-92`、`web/src/v2/config.ts:205-215` |
| 服务端 `group_specs()` 按 `(tier, order, id)` 排序 | `src/rui/config/catalog.py:192` |
| tier 2 的 order：devices 10 / multi_device 20 / audio.capture 100 / audio.voice 110 / audio.playback 120 / audio.aec 130 / **ack 200** | `src/rui/config/catalog.py:93,98,107,113,117,122,127` |

⇒ v2 语音助手页实际渲染 **8 组**，顺序为
`assistant → session → devices → audio.capture → audio.voice → audio.playback → audio.aec → ack`；
**ack 是第 8 组（末位）**。`multi_device` 不在本页（`showGroup` 未放行），在「设置/Rooms」页。

旧面板（legacy，0.8.0 前回滚用）是另一套：设备与音频 → 音频与唤醒 tab 的 7 组
（`app.py:519-550`），ack 同样在末位；它的排序由 `ConfigCenter.vue:165-175` 按 tier/order
重排。该界面与 9 哥 的 v2 反馈无关，仅作并列说明。

### 0.2 顺序的真实来源与 P0 落点（含一处关键修正）

- **v2**：吃服务端 `catalog.groups` 的数组顺序（`config.ts:205-215` 只 filter+map，无 sort）。
- **旧面板**：`ConfigCenter.vue:175` 按 `(tier, order)` 重排，并按 panel 声明的 tier 过滤
  （`:165-175`）。
- 因此「改 `app.py:548-550` 的数组」**对 v2 无效**；真正生效的是改 catalog `_GROUPS`
  （`catalog.py:127` 等），两套渲染都会跟着变。

**关键修正（实现前必须知道）**：`group_specs()` 是 `(tier, order)` 双键排序，tier 优先。
`assistant`/`session` 是 **tier 1**，`ack` 是 **tier 2**。如果只把 ack 的 order
从 200 改成 `<10`（tier 仍为 2），v2 上 ack 只会到 **第 3 位**（assistant、session 仍在
前），**不满足「第 1 或第 2 位」**。

要拿到第 1 位，最小改动是：
1. `ack`：`tier 2 → 1`、`order 200 → 5`（`catalog.py:127`）；
2. v2 前端**不用动**（服务端顺序即渲染顺序）；
3. 旧面板代价：它的「音频与唤醒」块声明并过滤 `tier: 2`（`app.py:548`，
   `ConfigCenter.vue:165-175`），ack 变 tier 1 后会从旧面板消失。若要求旧面板 parity，
   需同步改该 block 的 tier 声明（另一套 UI 的改动）；否则接受 legacy 少这一组
   （legacy 在 0.8.0 移除，本卡不代做决定）。

**这条 P0 还有两条落地代价，需要 9 哥 明确接受（二选一）：**
- **代价 1：既有测试会红**。`tests/test_config_catalog.py:94-96` 断言
  `{"audio.capture","audio.voice","audio.playback","audio.aec","ack","devices"} <= groups[2]`
  （ack ∈ tier 2）。P0 落地必须同步把 ack 移到 `groups[1]` 断言——这是**改既有测试**，
  不是可选单测。
- **代价 2：tier 语义/契约冲突**。`catalog.py:41` 把 tier 1 定义为「底子（不轻易动）」，
  `docs/config-catalog-contract.md:68` 要求「`tier=1` 前端要弱化 + 警示」。把最常改的
  ack 归进 tier 1 与这个分类冲突（v2 不渲染 tier 标签，但契约与测试按 tier 分类）。
- **替代方案（不改 tier）**：保留 ack tier 2，在 v2 语音助手页显式把 ack 排到最前
  （frontend 排序，例如 `VoiceAssistantView.vue:90-92` 的 `orderedGroups` 结果里把 ack
  提前）；代价是旧面板仍把 ack 放最后，且要改 v2 前端。两条路都要 9 哥 拍板，本卡不代选。

## 1. 判据、标注与首屏估算口径

- **日常可调**：用户能自助改、改完立刻可感知（唤醒应答、回复风格、设备房间、播放控制），
  或装机后仍会反复调。
- **硬核参数**：需要理解音频/网络/进程细节才能改对，一次调完基本不动（采集布局、AEC、
  分段门限、采样率）。
- **首屏估算口径**：1440×900 视口；v2 页面顶部标题 + 保存条约 100–140 px，每个组卡
  （标题 + 说明 + 首字段）约 100–130 px；保守按 **首屏 5 个组**估算，第 6 组起需要滚动。
  不是截图实测；本卡未启动浏览器。
- **P0 / P1 / P2**：P0 = 9 哥 日常会改但被埋，或同一项有两个入口/两个保存语义；
  P1 = 常用但偏后，或结构性建议（另卡）；P2 = 纯整理或维持现状。

## 2. 逐页审计（v2）

### 2.1 语音助手（P0）

- **现有顺序（8 组，渲染序）**：`assistant → session → devices → audio.capture →
  audio.voice → audio.playback → audio.aec → ack`（证据见 §0.1；页面允许的组：
  `VoiceAssistantView.vue:74-96`）。
- **建议顺序**：`ack → assistant → session → devices → audio.voice → audio.capture →
  audio.playback → audio.aec`。

| # | 组 | 标注 | 理由（按改动频率） |
|---|---|---|---|
| 1 | `ack` 唤醒应答 | 日常可调 | 9 哥 最常改、也最想改：提示音/播报词/音量，改完下一声唤醒就能听见；现在第 8 位 |
| 2 | `assistant` 助手 | 日常可调 | 回复风格/人设/称呼，试一句话就能感知 |
| 3 | `session` 会话 | 日常可调 | 超时、告别语；比音频参数常用，但低于助手本身 |
| 4 | `devices` 音箱 | 日常可调（本页只读呈现，见 §3.1） | 加/换设备时看；真正写入应在设备详情页 |
| 5 | `audio.voice` 唤醒与分段 | 半硬核 | 唤醒词在这里（常用），但同组还有 VAD/门限（一次调完），放硬核组最前 |
| 6 | `audio.capture` 采集 | 硬核参数 | 采样率/声道/位深/移位，改错直接没声音 |
| 7 | `audio.playback` 播放 | 硬核参数 | 播放速率/声道/缓冲，正常部署不动 |
| 8 | `audio.aec` 回声消除 | 硬核参数 | 滤波器/归属，只在排查自回声/打断时动 |

- **落地改动**（catalog，一处改动两套渲染生效）：
  - `ack`: tier 2→1、order 200→5（`catalog.py:127`）——满足第 1 位；
  - `audio.voice`: order 110→90（`catalog.py:113`），`audio.capture` 保持 100
    （`catalog.py:107`）——落地「voice 在 capture 之前」；
  - `audio.playback` 120、`audio.aec` 130 不变（`catalog.py:117,122`）；
  - v2 前端零改动；旧面板 tier 过滤的代价见 §0.2；
  - **必须同步改既有断言** `tests/test_config_catalog.py:94-96`（ack 从 `groups[2]`
    移到 `groups[1]`），否则 P0 一落地全量 pytest 红；
  - tier 语义/契约冲突与「保留 tier 2 + v2 显式排序」的替代方案见 §0.2。
- **首屏结论**：改后第 1 组即 ack，在首屏第 1 位（按 §1 的 5 组口径：ack/assistant/
  session/devices/audio.voice 可见），**无需滚动**。
- **P0 验证口径**：
  1. `uv run python -c "from rui.config.catalog import group_specs; print([g['id'] for g in group_specs()])"`：`ack` 在 `assistant` 之前、且排在 `devices` 之前；
  2. `catalog.py:127` 为 `("ack", ..., 1, 5, ...)`；`catalog.py:113` 的 order 小于 `:107`；
  3. 打开 `/v2/voice-assistant`：ack 是第 1 个组卡，首屏可见（估算口径 §1）；
  4. **必须同步改** `tests/test_config_catalog.py:94-96`（ack 从 tier 2 断言移到 tier 1）；
     改完 `uv run pytest tests/test_config_catalog.py -q` 绿，才算 P0 落地完成。
- **P1 结构建议（另卡，不在本卡动）**：v2 语音助手页把 4 组硬核音频参数与助手/会话混在
  一页，这是**归属问题**，不是顺序问题。建议把 `audio.*`（至少 capture/playback/aec）
  挪到「设备详情 → 音频」或独立「音频」页；本卡按 9 哥「布局不动、只调顺序」只给顺序方案。

### 2.2 工具与集成（P2 维持）

- **现有顺序（4 段）**：`weather → news → mcp → other`，由常量定义并顺序渲染
  —— `web/src/v2/tools.ts:31`、`web/src/v2/views/ToolsView.vue:508`。
- **建议**：**维持**。天气/新闻是最常拨的开关；MCP/HA 是装机后偶发；`other` 杂项最后。
- 标注：weather/news 日常可调；mcp 半日常；other 杂项。
- 首屏：第一段 weather 在首屏。

### 2.3 音乐（P2 维持）

- **现有顺序（3 张卡）**：播放状态/控制（`MusicView.vue:309`）→ 音源（`:390`）→
  凭据（`:456`）。
- **建议**：**维持**。播放控制是每天用的；音源与凭据是一次性配置；凭据在音源之后符合
  「先选源再填凭据」的操作顺序。
- 标注：播放=日常可调；音源/凭据=一次性。
- 首屏：播放状态卡在首屏。

### 2.4 密钥（P2 整理）

- **现有顺序**：单张 `secrets-list`，按密钥名**字母序**排（`CredentialsView.vue:76` 的
  `rows` 排序；列表渲染 `:383-392`），分类只作 badge（`:412-416`）。
- **建议**：按「provider → search → music → ha → device → console」的**分类分组**排，
  组内保持字母序；provider（LLM/TTS/ASR）最常轮换，console/device 只在登录/重装时动。
  这是分组/排序整理，P2。
- 标注：provider/search 日常可调；music 行只读+链到音乐页；device/console 低频。
- 首屏：provider 组在首屏（分组后）。

### 2.5 设备（P1：详情页 tab 顺序）

- 设备列表：标题 → 汇总 → 告警 → 按房间分组 → 添加
  —— `DevicesView.vue:162,246,281,306-307`；建议维持（概览→异常优先→设备是合理的）。
- 设备详情 tab 现有顺序：`overview → audio → config → test → activity → connect → danger`
  —— `DeviceDetailView.vue:49-55`；渲染 `:318-334`。
- **建议**：`overview → config → audio → test → activity → connect → danger`。理由：
  `config`（身份/房间/地址/唤醒词）是换设备/搬家时的日常入口，`audio` 是硬核覆盖参数
  （`AudioPanel`，`:356`），应后置；`danger` 保持最后。
- 标注：overview/config 日常；audio 硬核；test/activity/connect 排障；danger 危险区。
- P1（常用入口偏后）。

### 2.6 设置（v2 = Rooms / 多设备与全局音频）（P2 维持）

- **现有顺序（5 段）**：汇总（`RoomsView.vue:315`）→ 告警（`:343`）→ 房间/设备卡
  `RoomsPanel`（`:375`）→ 多设备策略 `HousePolicyEditor`（`:389-397`）→ 全局音频
  `HouseAudioEditor`（`:399`）。
- **建议**：**维持**。汇总与告警先给状态，房间卡是日常查看；多设备策略是装机/扩容频率
  （`HousePolicyEditor.vue:1-9`：多设备策略、`PATCH /house` 只发改动字段）；全局音频
  （采集/播放/AEC）是硬核，放最后正确。
- 标注：汇总/告警/房间卡=日常；HousePolicy（多设备启用、房间/输出策略）=装机/扩容；
  HouseAudio=硬核参数。
- 首屏：汇总 + 告警在首屏。
- P2（维持；`multi_device` 的编辑入口就在这里，与 §0.1 的说明一致）。

### 2.7 日志（P2 维持，无 catalog 组）

- v2 日志页是「级别过滤 + 日志流」（`LogsView.vue:34-41,83-95`），没有 catalog 分组；
  与旧面板不同，v2 这里**没有** `system.log_level` 编辑入口。顺序无需调整。
- 体检页只读，不参与排序。

### 2.8 旧面板并列说明（legacy，不计入 v2 页数）

- 设备与音频 → 音频与唤醒 tab 的组：`multi_device, devices, audio.capture, audio.voice,
  audio.playback, audio.aec, ack`（`app.py:548-550`）；渲染序按 tier/order：
  `devices → multi_device → audio.capture → audio.voice → audio.playback → audio.aec → ack`
  （`ConfigCenter.vue:175` + `catalog.py:93…127`）。
- 若 0.8.0 前仍要维护旧面板，它的顺序会随同一份 catalog 自动变化；ack 变 tier 1 后
  旧面板该 tab 丢失 ack（§0.2 的代价），是否需要补偿属另卡决策。

## 3. 重复入口（同一项可在两处修改）

### 3.1 v2 语音助手页的 `devices` 组：**已满足（只读 + 跳转）**，无需改动

- `DeviceConfigCard.vue:1-5` 的 docstring 明确：catalog 的 `devices` 组**故意只读**，
  唤醒词/音量/地址的编辑在设备页，本卡片是「摘要 + 能真正编辑的页面链接」。
- 模板已渲染 `v2.common.readOnly` 徽标（`:58`）与每行 `RouterLink` → `/devices/{id}`
  （`:88-96`；空态还链到 `/devices`）；文案见 `web/src/v2/i18n.ts:1276`
  「设备相关字段（唤醒词、音量、地址等）在此只读；请到设备页修改…」。
- 保存 diff 也不含该组：`editableFields` 排除 `devices`（`VoiceAssistantView.vue:94-96`），
  `changedPaths` 只遍历它（`:106-115`）。这是**有意的只读呈现**，不是「看起来能改、
  实际不生效」的 bug。
- 唯一写入口：设备详情 `ConfigPanel`（`DeviceDetailView.vue:372`）。v2 无重复写入口；
  legacy 的重复入口见 §3.2。

### 3.2 旧面板的同类双入口（legacy，仅记录）

- `DeviceManager`（设备管理 tab，`DeviceManager.vue:103-109`、保存 patch `:1201-1209`）与
  catalog `devices` 组（音频与唤醒 tab，`catalog.py:1281-1345`）交集为
  **adapter / host / room / enabled / wake_words / listen_port（6 项）**。
- **`id` 不是双入口**：编辑态只读（`DeviceManager.vue:1753`），patch 不含 id（`:1201-1209`），
  `PUT /api/v1/devices/{id}` 不处理 id（`app.py:6392-6435`，id 来自 URL）；唯一改名入口是
  catalog `devices.N.id`（`catalog.py:1281`），建议**保留可写**，否则设备无法改名。

### 3.3 已核实的「非双入口」

| 项 | 结论 | 查法/证据 |
|---|---|---|
| 音乐凭据 | 单一写入口 | `music.credentials` 行 `writable=false` 并链到音乐页 —— `catalog.py:1951-1964`；音乐页写独立 0600 文件 |
| HA / MCP | 单一写入口 | v2 tools 页只读展示 MCP 状态、显式排除 `tools.mcp_servers` 写 —— `web/src/v2/tools.ts:45-53` |
| 控制台 token | 单一写入口 | v2 凭据页写；v2 没有 system 页的 token 编辑入口 |
| `system.log_level` | v2 无编辑入口 | v2 日志页只有级别过滤（`LogsView.vue:34-41`），不渲染 catalog 字段 |

## 4. 优先级汇总

| 优先级 | 条目 | 页面 | 类型 |
|---|---|---|---|
| P0 | ack 第 1：`catalog.py:127` tier 2→1、order 200→5；`audio.voice` 110→90 / capture 100（`catalog.py:113/107`） | 语音助手 | 纯顺序（catalog） |
| P1 | `audio.*` 硬核组与助手/会话混在一页 → 结构上挪去设备详情/独立音频页 | 语音助手 | 结构变更（另卡） |
| P1 | 设备详情 tab：`config` 提到 `audio` 之前 | 设备 | 纯顺序（前端） |
| P1 | 旧面板 parity 决策：ack 变 tier 1 后旧面板音频 tab 丢失 ack | 旧面板 | 结构/取舍（另卡） |
| P2 | 工具与集成、音乐、设置（Rooms）、日志：维持现状（理由见 §2） | 工具/音乐/设置/日志 | 整理 |
| P2 | 密钥页：按分类分组（provider 优先），组内字母序 | 密钥 | 整理 |
| P2 | 确认：语音助手页 `devices` 组已只读 + 跳转（`DeviceConfigCard.vue:1-5/58/88-96`），无需改 | 语音助手 | 确认 |

计数：**P0 = 1，P1 = 3，P2 = 3 类**（P2 覆盖 4 个维持页、密钥整理、devices 只读确认）。

## 5. 首屏可见性汇总（估算口径 §1）

| 页面 | 建议后第 1 屏（约 5 组） | 关键项首屏 |
|---|---|---|
| 语音助手 | ack / assistant / session / devices / audio.voice | **ack 是**（第 1 位） |
| 工具与集成 | weather / news / mcp / other | 是 |
| 音乐 | 播放状态卡 | 是 |
| 密钥 | provider 分类组 | 是（分组后） |
| 设备 | 标题/汇总/告警/首个房间组 | 是 |
| 设置（Rooms） | 汇总 + 告警 | 是 |
| 日志 | 级别过滤 + 日志流 | 是 |

## 6. 边界与不做

- 只改本文档：未改前端、catalog、旧面板、既有 `docs/console*.md`；未启动浏览器，首屏为组数估算。
- 无真实内网地址/凭证；行号以当前工作树为准。
- ack 的 tier 调整会让旧面板音频 tab 丢失该组（§0.2）；「是否补偿 legacy」与「硬核组是否
  挪出语音助手页」都需 9 哥 拍板，本卡只给建议。
