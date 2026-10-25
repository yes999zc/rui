# 控制台 IA v2 设计：从零重写的多音箱控制台

> 状态：**设计稿 v2（2026-10-25，按 9 哥 新指令改稿）**。原「混合改造」方案作废，改为**新控制台、旁路并存、从零写**。
> 架构：新增 `web/v2.html` + `web/src/v2/**`，路由 `/v2/*`；后端新增 `/api/v2/*`（additive）；旧控制台页面/路由代码不改（共享构建/后端仅 additive），0.7 切默认并把旧的挂 `/legacy`。
> 契约：**`docs/contracts/C-002-console-v2-api.md`（C-002/1）是本设计的 API 真源**；本文只写 IA、页面、决策与里程碑，字段细节以 C-002 为准。
> 兼容：`>=0.6.0 <0.7.0` 内 `/api/v2` 只增不改；v1 保持向后兼容（C-002 已承认 `/api/v1/turns` 的 additive `device_id`），C-001（`rui-device/v1`）不变；0.7 才切默认/删旧设备面板。
> 输入：`docs/console.md`、`docs/device-management-research.md`、`docs/multi-device-design.md`、`docs/HANDOFF.md`，以及 §1 中逐条标注的代码。

**结论先行（TL;DR）**

1. **决策 B：新 SPA、旁路并存、从零写**。v2 不 import 旧 `panels.ts`/`PanelHost`/`views/*`；只复用 `theme.ts`、`api.ts` 的 token/request 基础层与 `AppIcon` 这类纯展示件；v2 文案自有 `web/src/v2/i18n.ts`。旧控制台页面/路由代码不改；共享文件（`web/vite.config.ts`、`src/rui/web/api/app.py`）只做 additive；旧入口 URL/行为不变（资源 hash 可变）。
2. **成本约 11 人日**（M1–M8 + M9a/M9b + M10，每个 ≤1 人日）+ 评审/验证约 2 人日；估算基准：**1 人日 = 6 小时专注工时，按 0.6 里程碑的实际速率折算**。第一个里程碑 **M1 可独立上线**：v2 外壳 + `/v2/run` + `/v2/devices` + 设备详情只读 + 静态托管冒烟，**不碰旧控制台、不写配置**。
3. **接口泛化的落点是 capability**：`/api/v2/capabilities` + manifest 的 `declared/probed/effective`；v2 前端不出现 `lx06`/`dot2` 机型分支，未声明能力一律降级隐藏并给说明。
4. **每设备音频覆盖**走 `/api/v2/devices/{id}/audio` 的 provenance（`device-override` / `global-config` / `device-manifest` / `core-default` / `unresolved`）；全屋默认在 `/v2/rooms`。设备保存会重建 **全屋 fleet**，UI 必须二次确认并明示。
5. **0.6.x 零 breaking**：v1 全部保留，`/api/v2` 只增；`/meta.features` 可逐项关闭 v2 能力，回滚不动数据/配置。0.7 才把 `/` 指到 `/v2`、旧 shell 挂 `/legacy`、删除 `/panel/devices` 并重定向。

---

## 1. 现状诊断：哪些地方仍是「一台小爱」的语义

### 1.1 导航与路由只有「面板」层，没有设备实体

- 导航的唯一事实来源是后端 `PanelSpec.nav`（`src/rui/web/api/app.py:441-453`、`4901-4909`），前端 `buildNav()` 只按 `section/order/hidden/icon` 排序（`web/src/nav.ts:80-103`）；section 只有 `run` / `configure` 两个值（`web/src/panels.ts:115-129`）。
- 路由只有外壳 + `/panel/:id`：`/`、`/panel/:id`、`/setup`、`/channels`、`/secrets`、`/diagnostics` 和 catch-all（`web/src/router.ts:16-30`（`createWebHistory()` 在 :32））。
- `PanelHost` 用 `route.params.id` 找**一个面板**，再在面板内部用 `?tab=` 切块（`web/src/views/PanelHost.vue:32-33`、`60-86`、`239-277`）。**没有 `/devices/:id` 这种实体路由**，设备详情只能是 `/panel/devices?tab=manage` 里的一个局部状态。

后果：设备无法被收藏、无法从日志/对话深链到具体房间、刷新后回到列表默认态；两台以上设备时，运维只能靠一页里的局部选中态。

### 1.2 「设备与音频」一个面板塞了三件事

- 面板定义为 `devices`，三个 tab：`status`（状态与音量）/ `manage`（设备管理）/ `audio`（音频与唤醒）（`src/rui/web/api/app.py:508-548`）。
- `status` 是设备列表 + 一个 `volume` block（`app.py:527`）。该 block 的 `href` 固定为 `/api/v1/device/volume`，**不带设备参数**；而底层 API 明确支持 `?device=`（`app.py:3566-3577`），不传时走 `_primary_device_id`：`default_output`（在跑）→ 第一个 running 平面 → 第一台已配置设备（`app.py:1147-1172`、`3573-3576`）。也就是说：**列表里选中书房，滑块改的可能是客厅**。
- `manage` 是逃生舱组件 `DeviceManager`（`app.py:533-534`）；组件自述「One page owns the whole day-after lifecycle」，列表与详情同页（`web/src/components/DeviceManager.vue:5-24`、`1319-1426`）。
- `audio` 是 tier 2 的 `config` block，一次渲染 `multi_device`、`devices`、`audio.capture`、`audio.voice`、`audio.playback`、`audio.aec`、`ack` 七组（`app.py:537-548`）。其中 `devices` 组把每台设备的 20+ 字段按**数组下标**展开成平铺表单（`src/rui/config/catalog.py:1256-1278`），设备之间没有切换器，全局默认与每设备覆盖混在同一页。

### 1.3 安装向导是「一台设备的一次安装」

- `GET /api/v1/setup/step` 的状态只有一个目标设备：`_target_device()` 先选「显式声明该插头的 enabled 设备」，否则 `candidates[0]`（`src/rui/web/api/setup_api.py:173-185`）；`_setup_state()` 只解析这一个 device/manifest（`setup_api.py:307-311`）。
- `device.upsert` 在未传 `id` 时，**直接沿用 `current.devices[0].id`**，没有设备时才用 `living`（`setup_api.py:926-933`）。旧端点 `/api/v1/setup/device` 同样默认 `living`（`app.py:5384-5389`），`/api/v1/setup/wake` 只改 `devices[0]`（`app.py:5443`）。
- 前端一旦检测到已有设备就锁死插头选择（`web/src/views/SetupView.vue:373-374`），新增设备时 id 默认取「当前设备 id」（`SetupView.vue:1367`）。
- 外壳的首装引导只看全局 `complete`（`web/src/App.vue:90-93`、`114-126`），没有「这是第几台」的概念。

后果：**今天从 `/setup` 加第二台音箱，默认会覆盖第一台的记录**；即便手填新 id，后续 wake/connect/test 步骤仍会回到第一台（`_target_device` 取第一个 enabled）。

### 1.4 概览、信号条、体检默认只显示「一个音频面」

- 概览的 stats 只有「设备（数量）」「音频面（true/false）」，音频指标来自主平面（`app.py:481-496`）；`GET /api/v1/audio` 返回体是主平面的 `status()` 再补 `planes` 映射（`app.py:4926-4949`）。
- 顶部信号条只显示设备**数量**，不显示在线数/哪个房间（`web/src/App.vue:264-270`）；数据来自 `/health` 的 `devices`（数量）与 `audio_plane`（布尔）（`App.vue:55-83`）。
- `/health` 其实已经有每平面 `audio_devices[]` 与 `multi_device`（`app.py:3123-3133`），体检页底部「多设备」卡片也逐平面显示（`web/src/views/HealthView.vue:118-148`）；但同一页的音频诊断区只读主平面的 `audio.metrics`（`HealthView.vue:187-261`），页面顶部的 verdict/capacity 也是单份（`HealthView.vue:96-116`）。

后果：**书房平面挂了，顶部仍可能显示「服务 ok / 音频面 开」**；`/health` 的 `status` 恒为 `"ok"`（`app.py:3103`），故障只体现在底部的每设备行里，日常不会滚到那里。

### 1.5 设备管理器已按设备展示，但只是「生命周期工具」

- 列表卡片已有 room/id/状态/端口/token/能力/参数摘要（`DeviceManager.vue:1347-1414`），详情有状态、音频生效值、覆盖字段 chips、测试、连接、删除（`DeviceManager.vue:1417-1660`）。
- 但音频区只展示**生效值**和 `overrides[]` 路径列表（`DeviceManager.vue:1544-1556`），不显示每个字段来自哪一层；`PUT /api/v1/devices/{id}` 也不接受 `audio`（`app.py:5855-5914`），所以设备页无法直接编辑每设备覆盖，只能去配置中心按 `devices.N.audio.*` 下标改（`catalog.py:1276-1278`）。
- 保存任何设备字段都会走 `_write_devices()` → `_restart_audio()`，多设备模式下重建**整个 fleet**（`app.py:5184-5198`、`5284-5288`），但 UI 没有「会影响全部 N 台」的提示。

### 1.6 对话与日志没有设备维度

- `/api/v1/turns` 返回最近回合，但没有 `device` 过滤，`_record_turn()` 只补 `ts/ts_epoch`（`app.py:1941-1948`、`6380-6391`）；`TurnResult.to_dict()` 也不含 `device_id`（`src/rui/session/turn.py:104-125`）。虽然 `TurnTrace` 有 `device_id`（`src/rui/trace.py:48-53`），最近的 turn ring 并未把它暴露给对话页。
- `/api/v1/logs` 只有全局 tail 和行数参数（`app.py:6393-6398`），没有设备过滤。

后果：排查「书房刚才为什么没回答」时，无法在设备页看到「这台设备最近几轮/相关日志」，只能读全屋日志再人工筛。

### 1.7 多音箱下的 5 个具体不直观场景

| # | 用户动作 | 今天发生什么 | 根因（证据） |
|---|---|---|---|
| S1 | 在设备页选中书房，拖音量 | 改的是 `_primary_device_id` 选出的房间（default_output 优先） | volume block 不带 device（`app.py:527-530`、`3573-3576`） |
| S2 | 想「在书房试一句」 | 只有概览的「试一试」，没有房间选择；API 已支持 `device` 却没有 UI | `app.py:3637-3711`；概览 say block `app.py:478-480` |
| S3 | 改书房唤醒词 | 在一长串按 index 展开的表单里找；保存后全屋平面重启 | `catalog.py:1276-1278`；`app.py:5184-5198` |
| S4 | 加第二台音箱 | 向导默认 id 取第一台，保存即覆盖第一台；后续步骤仍指向第一台 | `setup_api.py:926-933`、`173-185`；`SetupView.vue:1367` |
| S5 | 书房不响应，想知道原因 | 概览/体检顶部仍「ok」，要滚到多设备卡片，再进设备面板选设备 | `app.py:3103`、`481-496`；`HealthView.vue:118-148`、`187-261` |

### 1.8 0.6 内核已经具备的（本设计不重做）

| 能力 | 位置 | 对 IA 的意义 |
|---|---|---|
| 每设备音频面 / fleet | `app.py:1104-1106`、`1599-1629`；`docs/multi-device-design.md:326-338` | 设备页可以逐台显示 running/connected/restarts |
| 每设备状态/测试/连接/启停/token | `app.py:5525-5541`、`5933-6010`、`5666-5741`、`5562-5602`、`5604-5664` | 单设备页无需新 API 就能做 80% |
| 按房间输出（告别/提醒/音乐/音量/试一句） | 告别 `app.py:1210-1220`、2875；提醒 `1311-1318`；音乐 `1321-1324`；音量 `3566-3605`；试一句 `3637-3711` | 房间页/测试页可以直接复用既有路由语义 |
| `/health` 每平面行 | `app.py:3123-3133` | 全屋概览与设备列表的数据源 |
| turn trace 带 `device_id` | `src/rui/trace.py:48-53` | 设备日志页有数据可用，只差暴露/过滤 |

### 1.9 EchoMuse 调研结论：哪些仍成立、哪些已过时

| 结论 | 状态 | 证据 |
|---|---|---|
| 设备列表 + 设备详情 tabs（Status/Activity/Config/Console/Updates/Logs）值得学，但 RUI 可砍到两层 | **成立**（UX 原则） | `docs/device-management-research.md:46-55` |
| 状态由证据计算、失败面板固定 Retry/Reconnect/Diagnostics、诊断包服务端脱敏 | **成立，继续用** | 调研 `:82-98`、`:296-299`、`:645-649` |
| 不做 approve/pending 队列，向导的「添加设备」就是人工审批 | **成立** | 调研 `:369`、`:871-872` |
| 继续用 `config.yaml`，不引入 SQLite | **成立** | 调研 `:691-720` |
| 账号/角色、fleet 的 Updates/Pending 摘要、每设备端口/CA、mDNS/TLS | **不抄** | 调研 `:39`、`:308-313`、`:370-371` |
| USB/ADB/WebUSB/TWRP/刷机/emOS/OTA、HA/ESPHome | **明确不抄** | 调研 `:306-313`、`:375-379` |
| 「0.2 一个音频面只驱动一台设备」「多设备是 P3」 | **已过时**（0.6 已多平面），只保留「不要照搬 EchoMuse 编排」的结论 | 调研 `:21-22`、`:345`；现状 `docs/HANDOFF.md:25`、`app.py:1599-1629` |

**EchoMuse → RUI v2 的映射**（学结构，不抄实现）：

| EchoMuse | RUI v2 | 说明 |
|---|---|---|
| Fleet 摘要（Online x/y / Active / Updates / Pending） | 全屋概览 + 设备列表页头「在线 2/2」 | 去掉 Updates/Pending（RUI 无 OTA/审批） |
| 设备卡片/列表 | `/devices` 卡片 | 在线/离线/平面/最近活跃 |
| 设备详情模态 tabs | `/devices/:id` 页 + tabs | 概览/配置/测试/日志/连接/危险区 |
| Provision Device 向导 | `/devices/add`（复用 8 步骨架，只保留设备相关步） | 不做刷机，设备侧只给链接/命令/表单 |
| Settings: Fleet / System / 账户设置 | 房间与全屋 / 系统 / （无 Users） | 单 token，无角色 |
| Device config 的 Fleet/Device 切换 | 设备页的「继承 / 覆盖」开关 + 来源 chips | 三层合并（§5） |

---

## 2. 用户任务清单（按真实频次排序）

> 角色说明：9 哥 是唯一运维者；家人只通过语音使用，不登录控制台。因此控制台的任务全部按「9 哥 的一天/一周」排序。

| # | 任务 | 频次 | 今天怎么做 | 今天的问题 | IA v2 落点 |
|---|---|---|---|---|---|
| T1 | 看全屋谁在线、哪个房间有声音、有没有故障 | 每天多次 | 概览 stats + 顶部信号条 | 只有设备数量/音频面布尔；一个房间挂了顶部仍 ok | `/v2/run`；`/v2/devices` |
| T2 | 临时调某个房间的音量/静音 | 每天 | `/panel/devices?tab=status` 的滑块 | 滑块目标不是选中的设备（S1） | `/v2/devices/:id?tab=test` + `/v2/rooms` |
| T3 | 在某个房间试一句、验证回答/告别从该房间出 | 每周（调路由时） | 概览「试一试」；或只在 setup verify 里 | 没有房间选择；API 支持但 UI 无入口（S2） | `/v2/devices/:id?tab=test`；告别靠空闲+日志 |
| T4 | 改某台设备的房间名/唤醒词/音量目标/LED/端口 | 每月或换房时 | 配置中心 `devices.N.*` 平铺表单 | 按 index 找设备，易改错；无来源提示（S3） | `/v2/devices/:id?tab=config` |
| T5 | 某房间不响应，定位是平面/token/端口/唤醒/音频参数 | 每周（故障时） | 体检页多设备卡片 → 设备面板 → 测试 | 入口深、信息分散；日志/对话无设备维度 | `/v2/devices/:id?tab=overview/test/activity` |
| T6 | 加第二/第三台音箱 | 很少 | `/setup` 八步向导 | 默认覆盖第一台；后续步骤指向第一台（S4） | `/v2/devices/add` |
| T7 | 设全屋默认输出、房间优先级、平面上限 | 很少 | 配置中心 `multi_device` 组 | 与设备字段混在「音频与唤醒」长页里 | `/v2/rooms` |
| T8 | 禁用/删除/换掉一台设备 | 很少 | 设备面板删除/开关 | 删除影响说明有，但「禁用 vs 删除」「全屋重启」不够显眼 | `/v2/devices/:id?tab=danger` |
| T9 | 看用量/额度 | 每月 | 体检页火山用量卡片 | 只有全屋总量；按房间分摊无数据也无必要 | `/v2/health`（M9a）；不做设备维度 |
| T10 | 设备侧安装/升级 client | 很少 | 向导里的链接/命令 | 把「设备侧动作」误以为控制台能代做 | `/v2/devices/add` 只给说明 + 证据校验 |

**今天做不到或有歧义的（必须在 v2 消除）**

1. 「设备与音频」到底是**看状态**还是**改配置**？今天两个 tab 各做一半，第三个 tab 又是全局+每设备混合表单。
2. 「在线设备」列表里的选中态与音量滑块/配置表单**没有绑定**（S1/S3）。
3. 「音频面 开」是**整个服务**还是**每个房间**？顶部/概览读取的是主平面（§1.4）。
4. 首装完成是**全局一次**还是**每台设备一次**？`complete` 由第一个目标设备计算（`setup_api.py:307-311`）。
5. 加设备与改设备走**同一个向导**，且默认动作是覆盖（S4）。

---

## 3. IA v2 信息架构：从零写的 v2

### 3.1 设计原则

1. **设备是唯一一等实体**；房间只是 `devices[].room` 属性（`src/rui/config/schema.py:255`），不建房间表。
2. **v2 真的从零**：`web/src/v2/**` 不 import `panels.ts`、`PanelHost.vue`、旧 `views/*`；v2 文案自有 `web/src/v2/i18n.ts`（§3.6）；允许复用 `theme.ts`、`api.ts` 的 token/request 基础层、`AppIcon` 纯展示件（C-002 §2.3）。
3. **能力驱动，不按机型分支**：控件是否出现由 manifest 的 `capabilities` + `/api/v2/capabilities` 决定；前端 grep 不到 `lx06`/`dot2` 字面量。
4. **只写证据**：`ready` = enabled + live link；`connected:null` 显示“未知”，不冒充离线；`status_reason.code` 稳定可翻译。
5. **重启范围可见**：任何设备写操作返回 `restart`（scope=`fleet`），UI 必须二次确认“保存将重启全屋音频面”。
6. **契约先行**：字段/错误码/降级以 C-002 为准；本文不另造 API 语义。

### 3.2 v2 一级结构（`/v2`）

```text
运行 (run)
  全屋概览        /v2/run
  对话            /v2/conversation      （M8，v1 API 过渡）
  日志            /v2/logs              （M8）
  记忆            /v2/memory            （M8）
  体检            /v2/health            （M9a）

设备 (devices)
  设备            /v2/devices
  设备详情        /v2/devices/:id?tab=overview|config|test|activity|connect|danger
  房间与全屋      /v2/rooms
  添加设备        /v2/devices/add       （按钮/空态入口，不进侧栏）

配置 (configure)
  语音助手        /v2/voice-assistant   （M9a）
  音乐            /v2/music             （M9a）
  模型与语音服务  /v2/providers         （M9b）
  工具与集成      /v2/integrations      （M9b）
  系统            /v2/system            （M9b）
  凭据（只写）    /v2/credentials       （hidden，深链）

首次安装          /v2/setup             （house 无设备时；与 add 共用八步）
旧控制台          /                     （0.6.x 默认，页面/路由代码不改；0.7 挂 /legacy）
```

- **`/v2/run` 是「运行」段首页，读 `GET /api/v2/house` 渲染全屋概览，不是占位页**；`/v2/devices` 是设备列表，`/v2/config` 是配置段首页（在制路由已有 `/run`、`/devices`、`/config`）。
- 三段顺序固定 `run → devices → configure`；设备详情必须有 URL，深链可收藏。
- v2 自己声明导航/路由，不读 `/api/v1/panels`、不渲染 PanelSpec blocks。
- 旧 `/panel/devices` 在 0.6.x 隐藏（旧控制台侧栏），0.7 删除并重定向 `/devices`；v2 不依赖它。

### 3.3 页面清单（每页标注 M1 是否就做、依赖哪个 v2 端点、能力降级）

| 页面 | 路由 | M1 就做？ | 目标 | v2 数据源 | 现有组件复用度 | 新页面？ | 能力/版本降级 |
|---|---|---|---|---|---|---|---|
| 全屋概览 | `/v2/run` | ✅ | 谁在线、默认输出、告警、house setup | `GET /api/v2/house`、`GET /api/v2/meta` | 0%（仅 theme/api 基础层） | 是 | `meta.features.house=false` → 显示“此版本未启用”，不白屏 |
| 设备列表 | `/v2/devices` | ✅ | 一屏看全屋设备，异常优先 | `GET /api/v2/devices`、`GET /api/v2/house` | 0%（仅 theme/api 基础层） | 是 | 未知能力显示 badge，不渲染控件 |
| 设备详情·概览 | `/v2/devices/:id?tab=overview` | ✅（只读） | 身份/状态原因/能力/平面（活动 M2 加入） | `GET /api/v2/devices/{id}`、`GET /api/v2/house` | 0%（仅 theme/api 基础层） | 是 | `unknown_capabilities` 显示“v2 未支持”；`connected:null` 显示未知 |
| 设备详情·配置 | `?tab=config` | ❌ M3 | 改 room/wake/volume target/port/params/音频覆盖 | `PATCH /api/v2/devices/{id}`、`GET /api/v2/plugins/{plugin_id}` | 0%（仅 theme/api 基础层） | 是 | 未声明 `volume`：隐藏混音器，只留音量目标；未声明 `led`：隐藏灯效；secret 只写 |
| 设备详情·测试 | `?tab=test` | ❌ M2 | 连接测试/试一句/音量/LED/试音 | `POST /api/v2/devices/{id}/test`、`POST /api/v2/devices/{id}/actions` | 0%（仅 theme/api 基础层） | 是 | 按 `capabilities` 隐藏/置灰：`playback`→say/play/stop；`volume`→滑块；`led`→灯效；缺失项显示 `not_applicable` 原因 |
| 设备详情·活动 | `?tab=activity` | ❌ M2/M7 | 这台设备的回合+日志 | `GET /api/v2/devices/{id}/activity` | 0%（仅 theme/api 基础层） | 是 | 日志为字面匹配，标 `matched_by=literal`；无事件显示空态 |
| 设备详情·连接 | `?tab=connect` | ❌ M2 | 设备侧填什么、token 轮换 | `GET /api/v2/devices/{id}/connection`、`POST .../connection/reveal`、`POST .../token/rotate` | 0%（仅 theme/api 基础层） | 是 | outbound 显示“服务器主动拨号”；无 token 显示原因 |
| 设备详情·危险区 | `?tab=danger` | ❌ M3 | 禁用 vs 删除、全屋重启影响 | `PATCH /api/v2/devices/{id}`、`DELETE /api/v2/devices/{id}?confirm=` | 0%（仅 theme/api 基础层） | 是 | 删除必须输入 id 确认；最后一台提示停平面 |
| 房间与全屋 | `/v2/rooms` | ❌ M5 | 房间视图 + 默认输出/优先级/上限 + 全局音频默认 | `GET/PATCH /api/v2/house`、`GET/PATCH /api/v2/house/audio`、`POST /api/v2/house/audio/restart` | 0%（仅 theme/api 基础层） | 是 | 无 `multi_device` 能力概念；字段 422 按 path 回显 |
| 添加设备 | `/v2/devices/add` | ❌ M6 | 加第 N 台，不覆盖已有 | `GET /api/v2/setup/plan`、`POST /api/v2/setup/plan/steps/{step}` | 0%（仅 theme/api 基础层） | 是 | 插件不可用显示 `blocked`；house keys/models 未完成显示 `house_setup_incomplete` |
| 首次安装 | `/v2/setup` | ❌ M6 | 空配置装第一台 | 同上，`mode=first-run` | 0%（仅 theme/api 基础层） | 是 | 同 add；无设备时 `/v2/run` 给 CTA |
| 对话 | `/v2/conversation` | ❌ M8 | 全屋对话，按房间过滤 | 过渡期 `GET /api/v1/turns`；后续 v2 另立契约 | 0%（仅 theme/api 基础层） | 是 | 无 device_id 时标“全屋”，不假装单设备 |
| 日志 | `/v2/logs` | ❌ M8 | 全屋日志 | 过渡期 `GET /api/v1/logs` | 0%（仅 theme/api 基础层） | 是 | 设备过滤用 activity，不重复造 |
| 记忆 | `/v2/memory` | ❌ M8 | 记忆 CRUD | 过渡期 v1 `/api/v1/memory` | 0%（仅 theme/api 基础层） | 是 | 无 |
| 体检 | `/v2/health` | ❌ M9a | 全局诊断 + 每设备入口 | 过渡期 v1 `/health`、`/api/v1/doctor`、`/api/v1/audio` | 0%（仅 theme/api 基础层） | 是 | 每设备行链到 `/v2/devices/:id` |
| 语音助手/音乐/凭据 | `/v2/*` | ❌ M9a | 与旧控制台对等 | 过渡期 v1 稳定端点（`config/catalog`、`music` 等） | 0%（仅 theme/api 基础层） | 是 | `meta.features` 控制；不 import 旧 views |
| 模型与语音服务/工具与集成/系统 | `/v2/*` | ❌ M9b | 与旧控制台对等 | 过渡期 v1 稳定端点（`config/catalog`、`providers`、`mcp` 等） | 0%（仅 theme/api 基础层） | 是 | `meta.features` 控制；不 import 旧 views |
| 旧控制台 | `/` | — | 0.6.x 默认不变 | 既有 | 100% | 否 | 0.7 挂 `/legacy` 保留一个版本 |

> “M1 就做”只表示可独立上线的最小切片；M1 的详情页只读，写操作从 M2/M3 起。

### 3.4 v2 路由/静态托管契约

| 规则 | 内容 |
|---|---|
| 入口 | `web/v2.html` → `dist/v2.html`；源码 `web/src/v2/**` |
| 路由 | Vue Router `createWebHistory('/v2/')`；`/v2`、`/v2/*` 深链刷新都返回 `v2.html` |
| API | 只用 `/api/v2/*`；非设备域过渡期可用 v1 稳定端点，但**不得**调用 v1 设备端点（C-002 §2.3） |
| 旧控制台 | `/`、`/panel/*`、`/setup` 等页面/路由代码不改；共享文件只 additive；旧入口 URL/行为不变；v2 不 import 旧 views |
| 静态 | `/v2/assets/*` 长缓存；`v2.html` 不缓存；`/api/v2/*` 404 不落 SPA |
| 认证 | `X-Auth-Token`；v2 自己的 401 弹窗；无账号/角色 |
| 启动 | 先 `GET /api/v2/meta`；`contract` 不匹配显示兼容错误；`features` 关掉的页面隐藏/置灰 |
| 默认切换 | 0.6.x `/` 仍旧；0.7 `/` → `/v2`，旧 shell `/legacy` 保留一个版本（不在 M1–M8 + M9a/M9b + M10 内自动执行） |

### 3.5 信息密度与能力降级

| 区域 | 必须显示 | 能力降级规则 |
|---|---|---|
| 设备卡片 | room、id/model、状态 chip + `status_reason`、平面 running/restarts、最近活跃、token 状态、能力 badge | 未知能力 badge“v2 未支持”；不显示对应按钮 |
| 设备页头 | room+id、ready/connected、是否默认输出、平面状态 | `connected:null` → “未知（无平面）”，不显示“离线” |
| 概览 | 身份（plugin/model/vendor/transport）、能力、`missing_for_barge_in`、平面指标、最近事件 | 缺 `reference`：提示 AEC 依赖设备；缺 `device_wake`：唤醒来源显示 RUI KWS |
| 配置 | 字段 + 生效方式（重启全屋音频面）+ 音频来源；音量目标/实际混音器分两行 | 缺 `volume`：隐藏混音器；`led` 缺：隐藏灯效；params 按 manifest schema |
| 测试 | 每项 check 的 ok/fail/not_applicable + code + hint | `playback` 缺失：say/play/stop 不出现；`led` 缺失：灯效不出现；`volume` 缺失：滑块不出现 |
| 连接 | 掩码 ws_url、token 状态、reveal、rotate 冷却 | outbound：无客户端 URL；无 token：显示 `token_missing` |
| 危险区 | 禁用/删除差异、fleet 重启、设备不会自动回来 | 删除 confirm 必须等于 id |

### 3.6 双语与文案规则

- v2 文案在 **`web/src/v2/i18n.ts`**：`MsgKey` 由 v2 自己的 `zh` 字典派生，`en: Record<MsgKey, string>` 在类型层强制完整；键前缀统一 `v2.`（如 `v2.audio.source.deviceOverride`）。语言状态沿用共享 `rui-language`（localStorage），切换后 `<html lang>`/`document.title` 同步。
- v2 不 import 旧 `web/src/i18n.ts` 的字典（只读取/复用同一个 `rui-language` 值）；能力名/描述来自后端 `/api/v2/capabilities` 的 `{zh,en}`，也不塞进 v2 字典。
- 后端错误只给 `code/message/hint`（C-002 §4.4），v2 映射为 zh/en；不把后端整句中文塞进 v2 字典。
- 统一词：设备/device、房间/room、音频面/audio plane、能力/capability、覆盖/override、默认输出/default output；禁用“主音箱”。
- AGENTS.md §3：核心 UI 文案 zh-Hans + en；M10 做全页 EN 截图验收。

### 3.7 空态/错误/加载（v2）

| 场景 | 行为 |
|---|---|
| `/v2/run` 零设备 | 空态 + “添加第一台” → `/v2/devices/add`；不自动跳旧 `/setup` |
| `meta.features.*=false` | 页面显示“此版本未启用 + 当前 rui 版本”，不请求后续端点 |
| 401/429 | v2 token 弹窗 / `Retry-After` 倒计时 |
| 设备 404 | `not_found` 页 + 返回设备列表；不白屏 |
| 写操作 422 | 按 `error.details.fields[].path` 标红字段，保留草稿 |
| 写操作 200 + `restart.status=failed` | 页头红色错误条显示 `restart.error`，不显示“已保存”了事 |
| 能力不支持 | 控件隐藏/置灰 + `degraded_hint`；不显示一个点了必失败的按钮 |
| 加载中 | skeleton；保留上一次数据并标 stale（不闪白） |

---

## 4. 关键决策：B（新 SPA、旁路并存、从零写）

### 4.1 三个方案

| 维度 | A 就地改造旧控制台 | **B 新 SPA 旁路并存（选定）** | C 一次性替换 |
|---|---|---|---|
| 做法 | 在 `PanelSpec`/`DeviceManager` 上加设备切换器、tab；路由仍 `/panel/:id` | 新增 `web/v2.html` + `web/src/v2/**` + `/api/v2/*`；旧 `/` 不动；0.7 切默认 | 直接改旧 shell/路由，删 panels 体系，一次上线 |
| 成本（人日） | 5–6 | **11（M1–M8 + M9a/M9b + M10）+ 评审 2** | 8–9 |
| “从零写” | ❌ 复用旧 panels 语义 | ✅ v2 不 import 旧 panels/PanelHost/views | ⚠️ 代码沿用旧 shell |
| 能力泛化 | ❌ 仍按旧面板字段写死 | ✅ manifest capabilities + `/api/v2/capabilities` | ⚠️ 容易沿用旧假设 |
| 风险 | 旧控制台回归 + 永远达不到新 IA | 两套界面并存、双份维护；用 feature flag/契约收敛 | 无回滚面；旧 URL/书签全断；回归巨大 |
| 迁移 | 0 迁移，但设备域继续挤在 tab | 并存 → `/v2` 可用 → 0.7 切默认 → `/legacy` | 一次性切换 |
| 回滚 | 改回组件 | 关 `meta.features` 或回退镜像；旧 `/` 仍在 | 回退整包，风险最高 |
| 结论 | 否（owner 已否决） | **采用** | 否（无回滚，不满足“旧控制台不动”） |

### 4.2 为什么是 B

1. **owner 指令**：控制台从零重写、参考 EchoMuse、接口泛化；A 的旧 `PanelSpec` 模型没有设备实体，也不适合承载 capability 驱动的控件。
2. **风险最低**：旧 `/` 页面/路由不改；v2 是 additive 入口，出问题只影响 `/v2`；`meta.features` 可逐项关闭。
3. **契约先行**：C-002 把后端/前端解耦；后端可先交只读端点，前端 M1 即可上线。
4. **为多机型准备**：能力/参数/音频默认都从 manifest 来；v2 不出现机型分支，新音箱只需加 plug manifest（C-001 不变）。
5. **0.7 可干净收口**：v2 对等后 `/` 切 `/v2`，旧 shell `/legacy` 保留一个版本；不是一次性大爆炸。

### 4.3 被否决的替代方案（硬理由）

- **A 就地改造旧控制台**：旧 `PanelHost` 只有 block 表达式（`web/src/panels.ts:157-202`），没有“设备实体/能力”状态；继续加 tab 会把 `DeviceManager` 变成第二个框架，且无法满足“从零写”的验收（v2 不得 import 旧 views）。owner 已明确否决。
- **C 一次性替换**：没有旁路回滚；旧书签/`/panel/*` 全部断；v2 首次上线必然有未覆盖页面；与“旧控制台不动、0.7 再切”冲突。除非有硬技术理由（例如双份构建不可维护），否则不选；目前没有。
- 硬技术理由检查：Vite 多页入口、FastAPI 静态托管、同源 token 均无阻塞；C-002 端点可复用 v1 服务层，不需要 DB/新依赖。因此维持 B。

### 4.4 不改什么（边界）

- **旧控制台 `/` 与所有旧路由/功能不动**：0.6.x 不修改 `web/src/App.vue`、`router.ts`、旧 `views/*`、旧 `PanelHost.vue`、旧 `api.ts` 行为。唯一例外是 Q5 已拍板的“旧侧栏隐藏 `/panel/devices` 入口”（M10，只改 `nav.hidden`，URL/页面行为不变）。
- **v1 API 不动**：不扩 v1 设备端点、不改 v1 响应语义；v2 只新增 `/api/v2/*`。
- **C-001 不变**：manifest 字段/能力集合/`audio_defaults`/`params` 不改；新机型靠 plug 声明。
- **配置 schema 不变**：不新增必填字段、不升 `CONFIG_VERSION`。
- **不引入 DB/账号/角色/OTA/HA 依赖**；不做设备侧刷机。
- **不在 v2 内复用旧 panels/PanelHost/旧 views**；只复用 theme/api 基础层/AppIcon；v2 文案自有 `web/src/v2/i18n.ts`。
- **共享构建文件只做 additive**：`web/vite.config.ts` 加 `rollupOptions.input` 的 `v2` 入口；旧入口 URL/行为不变（新增 input 可能改变共享 chunk/资源 hash，不作为字节级门槛）。
- **不在 0.6.x 切默认**：`/` 仍旧；切默认是 0.7 的独立发布动作。

### 4.5 成本与决策门

| 工作 | 人日 | 说明 |
|---|---|---|
| M1 v2 外壳 + `/v2/run` + `/v2/devices` + 设备详情只读 + 托管冒烟 | 1 | 首个可独立上线切片；含 connection 只读，不含 reveal/rotate/测试/写 |
| M2 测试/动作/活动/连接 reveal/rotate | 1 | capability 门禁 |
| M3 设备 create/patch/delete + 配置 tab | 1 | 写操作 + restart |
| M4 设备音频 provenance + 覆盖编辑 | 1 | 来源可视化 |
| M5 房间与全屋 + 全局音频 | 1 | house/rooms |
| M6 八步 add 模式 + 首装 | 1 | setup plan |
| M7 设备活动/日志/对话/概览串联 | 1 | activity |
| M8 运行类页面（对话/日志/记忆）v2 重写 | 1 | 用 v1 过渡 API |
| M9a 运行/配置页 A（语音助手/音乐/凭据/体检）v2 重写 | 1 | 用 v1 过渡 API |
| M9b 配置页 B（模型与语音服务/工具与集成/系统）v2 重写 | 1 | 用 v1 过渡 API |
| M10 双语/a11y/移动端/端到端/切默认预演 | 1 | 收口 |
| 评审 + 验证（独立 agent） | 2 | 每里程碑 + 最终集成 |

**决策门**：M1 合并前 9 哥 看 `/v2` 1440/390 截图与真实两设备数据；若 `/v2` 外壳被否决，只回退 v2 文件/镜像，旧控制台不受影响。

---

## 5. 覆盖来源可视化（v2 版）

### 5.1 三层模型

```text
插头默认 audio_defaults  <  全局 config（audio / ack）  <  devices[].audio
```

- 核心默认中性；LX06 的 8ch/48k/6mic+2ref 由 manifest 声明（`schema.py:285-315`、`docs/multi-device-design.md:269-291`）。
- 合并只调用 `effective_device_audio()` / `effective_device_ack_echo_ignore_ms()`（`schema.py:650-661`），v2 不重写。
- “全局是否显式写过”用 `_explicit()`（`model_fields_set` 或值 ≠ 核心默认，`schema.py:545-555`）；因此不能只比较数值。

### 5.2 provenance 来源（与 C-002 §7.15 一致）

| 优先级 | `source` | 判定 |
|---|---|---|
| 1 | `device-override` | `devices[].audio.<path>` 非 null |
| 2 | `global-config` | 全局 `audio.<path>` / `ack.echo_ignore_ms` 显式 |
| 3 | `device-manifest` | manifest `audio_defaults.<path>` 非 null |
| 4 | `core-default` | 以上都无 |
| — | `unresolved` | 插件不可解析，且该字段无 device/global 显式值（不知道 manifest 本应声明什么） |

- 显式设备值即使等于全局值，来源仍是 `device-override`；UI 不得用值相等推断继承（`app.py:1113-1115`）。
- `capture`/`playback` 有 manifest 层；`voice`/`aec` 没有，`manifest:null`。
- `ack` 特例：device 覆盖在 `voice.ack_echo_ignore_ms`，manifest 在顶层 `ack_echo_ignore_ms`，global 在 `ack.echo_ignore_ms`；`sources` key 统一 `ack.echo_ignore_ms`。
- 插件不可解析：无 device/global 的字段标 `unresolved` + `plug_resolved=false`，UI 横幅“插头不可用，manifest 默认未知”；不要静默显示 `core-default`。

### 5.3 API 形状（v2，不扩 v1）

- `GET /api/v2/devices/{id}/audio` 返回：

```json
{
  "effective": {"capture": {}, "voice": {}, "playback": {}, "aec": {},
                "ack_echo_ignore_ms": 2500},
  "sources": {"capture.rate": "global-config", "aec.who": "device-override",
              "ack.echo_ignore_ms": "device-override"},
  "layers": {
    "capture.rate": {"effective": 16000, "source": "global-config",
                     "core": 16000, "manifest": 48000, "global": 16000,
                     "global_explicit": true, "device": null}
  },
  "overrides": ["aec.who", "aec.enabled", "voice.ack_echo_ignore_ms"],
  "manifest_defaults": {"capture": {"rate": 48000}, "ack_echo_ignore_ms": 1200},
  "plug_resolved": true
}
```

- 写：`PATCH /api/v2/devices/{id}/audio`（或 `PATCH /api/v2/devices/{id}` 内的 `audio`，同一次原子写）。对象=按字段 merge；`null`=清除字段；`audio:null`=清除全部；省略=不动。
- 响应带 `restart`（scope=`fleet`），UI 必须确认“重启全屋音频面”；`restart.status=failed` 时显示 `restart.error`。
- 全屋默认：`GET/PATCH /api/v2/house/audio` + `POST /api/v2/house/audio/restart`；不在设备页编辑全局。

### 5.4 UI：设备页「配置 → 音频参数」

```text
音频参数（这台设备实际会用到的值）        [保存将重启全屋 2 个音频面]
┌ 采集 ───────────────────────────────────────────────────────────┐
│ 采样率  16000 Hz   [全局]  ⓘ 各层   [覆盖此设备 ▢]              │
│ 通道    1 ch       [全局]  ⓘ 各层   [覆盖此设备 ▢]              │
├ 回声消除 ───────────────────────────────────────────────────────┤
│ 归属    device     [设备覆盖] ⓘ 各层   [恢复继承]                │
│ RUI AEC 关         [设备覆盖] ⓘ 各层   [恢复继承]                │
├ 唤醒确认 ───────────────────────────────────────────────────────┤
│ 回声窗  2500 ms    [设备覆盖] ⓘ 各层   [恢复继承]                │
└─────────────────────────────────────────────────────────────────┘
「为什么改了全局没变？」本设备覆盖了 3 个字段：aec.who / aec.enabled / ack…
```

交互规则：

1. 每行：参数、生效值、来源 chip、`ⓘ 各层`（manifest/global/device/core 四层）、覆盖开关。
2. 覆盖开关打开=以当前生效值写 override；恢复继承=`null`；保存后 chip 变实际来源。
3. 一个页面一个保存条：所有设备字段与 `audio` 走同一个 `PATCH /api/v2/devices/{id}`（C-002 §7.13），一次校验/一次写盘/一次重启。
4. 保存前弹确认：`保存将重启全屋 {count} 个音频面（约 {seconds} 秒）`；保存后 `restart.error` 非空必须红色显示。
5. 全局默认只在 `/v2/rooms`；如果设备有覆盖，全局保存后提示 `全局已保存，但 {count} 台设备有覆盖，不会改变`，并给设备链接。
6. `device-manifest` 行只读，提示更新 plug manifest；`unresolved` 显示插头不可用。
7. 422 按 `error.details.fields[].path` 定位到行（如 `audio.capture.rate`）；保留草稿。
8. 移动端每行两行（参数+值 / 来源+开关），无横向滚动。

### 5.5 文案规则（zh/en，进 `web/src/v2/i18n.ts`）

| key | zh-Hans | en |
|---|---|---|
| `v2.audio.source.deviceOverride` | 设备覆盖 | Device override |
| `v2.audio.source.globalConfig` | 全局 | Global |
| `v2.audio.source.deviceManifest` | 插头默认 | Plug default |
| `v2.audio.source.coreDefault` | 核心默认 | Core default |
| `v2.audio.source.unresolved` | 插头不可用 | Plug unavailable |
| `v2.audio.overrideOn` | 覆盖此设备 | Override for this device |
| `v2.audio.revert` | 恢复继承 | Inherit again |
| `v2.audio.layers` | 各层取值 | Values by layer |
| `v2.audio.blastRadius` | 保存将重启全屋 {count} 个音频面（约 {seconds} 秒） | Saving restarts all {count} audio planes (~{seconds}s) |
| `v2.audio.shadowed` | 全局已保存，但 {count} 台设备有覆盖，本设备不会改变 | Global saved, but {count} device(s) override it |
| `v2.audio.whyDevice` | 这台设备有显式覆盖，优先于全局 | This device has an explicit override |
| `v2.audio.whyManifest` | 这是插头声明的硬件默认，控制台只读 | Declared by the plug manifest |

后端只发 `source` 枚举，不发句子；能力名/描述来自 `/api/v2/capabilities` 的双语 label。

### 5.6 验收矩阵（M4）

| 用例 | 配置 | `sources[f]` | effective |
|---|---|---|---|
| 显式全局 vs manifest | global `capture.rate=16000`，manifest 48000 | `global-config` | 16000 |
| 纯 manifest | global 未写，manifest 48000 | `device-manifest` | 48000 |
| 纯核心 | 两者无 | `core-default` | 16000/1ch |
| 设备覆盖 | `devices[].audio.capture.rate=48000` | `device-override` | 48000 |
| 设备值=全局值 | device 16000，global 16000 | `device-override` | 16000 |
| 清覆盖 | `audio:{capture:{rate:null}}` | 回 `global-config`/`device-manifest` | 对应值 |
| ack 三层 | manifest 1200，global 1500，device 2500 | `device-override` | 2500 |
| ack 无覆盖 | manifest 1200，global 未写 | `device-manifest` | 1200 |
| 插件不可解析 | 删插件 | 无 device/global 的字段 `unresolved` | core 合并值 + `plug_resolved=false` |

测试：`tests/test_api_v2_provenance.py`（新）+ `tests/test_config_v3.py`。

### 5.7 房间页的全局音频

- `/v2/rooms` 的“全屋默认”编辑 `GET/PATCH /api/v2/house/audio`；只显示全局块 + `explicit`；保存后显示 `restart`（audio_plane）与“只重启音频面”按钮（`POST /api/v2/house/audio/restart`）。
- 设备页不提供全局编辑入口；旧 `/panel/devices?tab=audio` 只在旧控制台存在，v2 不引用。

---

## 6. 后端 / 契约影响清单

> C-002/1 是唯一 API 真源；本节只列对现有系统的影响。

| # | 变更 | 端点/字段 | 类型 | 0.6.x | 0.7 | C-001 |
|---|---|---|---|---|---|---|
| V2-1 | 新 v2 只读面 | `GET /api/v2/meta`、`/house`、`/plugins`、`/capabilities`、`/devices`、`/devices/{id}`、`/devices/{id}/connection` | 新增，非 breaking | ✅ | 保持 | 否 |
| V2-2 | 设备写 | `POST/PATCH/DELETE /api/v2/devices` | 新增，非 breaking | ✅（M3） | 保持 | 否 |
| V2-3 | 音频 provenance/写 | `GET/PATCH /api/v2/devices/{id}/audio`、`PATCH /api/v2/devices/{id}` 内 `audio` | 新增，非 breaking | ✅（M4） | 保持 | 否 |
| V2-4 | 全屋策略/音频 | `PATCH /api/v2/house`、`GET/PATCH /api/v2/house/audio`、`POST /api/v2/house/audio/restart` | 新增，非 breaking | ✅（M4/M5） | 保持 | 否 |
| V2-5 | 泛化动作 | `POST /api/v2/devices/{id}/actions` | 新增，非 breaking | ✅（M2） | 保持 | 否 |
| V2-6 | 活动 | `GET /api/v2/devices/{id}/activity` | 新增，非 breaking | ✅（M2/M7） | 保持 | 否 |
| V2-7 | 八步 add | `GET /api/v2/setup/plan`、`POST /api/v2/setup/plan/steps/{step}` | 新增，非 breaking | ✅（M6） | 保持 | 否 |
| V2-8 | v2 静态托管 | `/v2`、`/v2/*` → `dist/v2.html` | 新增，非 breaking | ✅（M1） | 保持 | 否 |
| V2-9 | v1 | 所有 `/api/v1/*`、旧 `/`、`/panel/*` | **不改** | ✅ | 保留到 0.7 后一个版本 | 否 |
| V2-10 | 旧设备面板 | `/panel/devices` 隐藏（旧控制台侧栏） | UI 变更，非 API | ✅ | 删除并重定向 `/devices` | 否 |
| V2-11 | 默认切换 | `/` → `/v2`，旧 shell `/legacy` | **breaking（UI）** | ❌（0.6.x 不做） | ✅ 切默认 | 否 |
| V2-12 | C-001/manifest/schema | 不变 | — | — | — | **无变更** |

**兼容纪律**

- v1 向后兼容：不删/不改旧字段；唯一已知 additive 是 `/api/v1/turns` 每条新增可选 `device_id`（旧消费者可忽略），v2 activity 复用；不扩 v1 设备端点、不改其既有字段。
- `/api/v2` 在 `>=0.6 <0.7` 内只增；字段删除/重命名只能 `/api/v3` 或 0.7。`meta.features` 可逐项关闭，回滚不动数据。
- C-002 不新增 manifest 字段；新机型 = 新 plug manifest（C-001 现有能力/参数/audio_defaults）。
- 需要钉住的回归：`tests/test_panels.py`、`tests/test_devices_management.py`、`tests/test_setup_api.py`、`tests/test_config_v3.py`、`tests/test_api.py`；新增 `tests/test_api_v2_read.py`、`tests/test_api_v2_provenance.py`。
- 唯一 0.7 breaking 是 V2-11 默认切换（UI 路由）+ V2-10 删除旧面板；都有 `/legacy` 与重定向过渡，不涉及 API 删除。设计比 C-002 §10.2 更保守：**不删除 legacy setup API**（`/api/v1/setup*`），只停止调用；若后端要删，另行评审。

---

## 7. 里程碑拆分（v2 重写路径，每个 ≤1 人日）

> **估算基准**：1 人日 = **6 小时专注工时**，按 0.6 里程碑的实际速率折算（不是 8 小时日历日）；每个里程碑超出 1 人日就按页/按端点再拆。
>
> **共同验收**：`cd web && npm run typecheck && npm run build`，必须同时产出 `dist/index.html` 与 `dist/v2.html`；`python -m pytest tests/test_api_v2_read.py -q`；provenance 文件存在后再跑 `tests/test_api_v2_provenance.py`（M4 起）；curl `/api/v2/meta`、`/api/v2/house`、`/api/v2/devices`；`/v2` 1440/390 截图、无 console error；旧 `/` 行为不变 + 冒烟（旧概览/旧设备页）。
>
> **grep 钩子（C-002 §2.3）**：
> ```bash
> ! grep -R "from '../panels'\|from '../views/\|api.panels(" web/src/v2
> ! grep -R "lx06\|dot2" web/src/v2
> ! grep -R "/api/v1/devices\|/api/v1/device/volume\|/api/v1/debug/" web/src/v2
> ```
> 允许的例外只有测试 fixture/注释中的历史说明；实现代码命中即失败。
> 每个里程碑都可独立合并；未完成的页面由 `meta.features` 关掉，不阻塞前面的。

### M1（1 人日）v2 外壳 + `/v2/run` + `/v2/devices` + 设备详情只读 + 托管冒烟

- **交付**：
  - 后端 T1 只读：`/api/v2/meta`、`/house`、`/plugins`、`/capabilities`、`/devices`、`/devices/{id}`、`/devices/{id}/connection`（掩码）；不做 connection reveal/rotate、test/actions/写，不扩 v1。
  - 前端：`web/v2.html`（root `#v2-app`，供切默认冒烟识别）、`web/vite.config.ts`（`rollupOptions.input` 增加 `v2`，旧入口保持）、`web/src/v2/main.ts`、`web/src/v2/router.ts`、`web/src/v2/App.vue`、`web/src/v2/i18n.ts`、`RunView.vue`（house 数据）、`DevicesView.vue`、`DeviceDetailView.vue`（只读 overview）。
  - FastAPI：挂 `/api/v2` 与 `/v2` 静态托管；`/api/v2` 未知路径回 JSON 404，不落 SPA。
- **验收**：
  - `/v2/run` 显示 2 台、online/ready、default_output、warnings；`/v2/devices` 2 张卡；`/v2/devices/dot2` 显示 capability/status/plane；`connected:null` 显示“未知”。
  - 深链 `/v2/devices/dot2` 刷新可开；401 弹 token；无写操作。
  - `npm run typecheck && npm run build` 绿，且同时产出 `dist/index.html` 与 `dist/v2.html`；旧 `/` 行为不变 + 冒烟（旧概览/旧设备页各打开一次）；`/api/v2/nope` 返回 JSON 404。
  - `pytest tests/test_api_v2_read.py -q`（只读用例）；§7 共同验收的 grep 钩子通过。
  - 截图 `/v2/run`、`/v2/devices`、`/v2/devices/dot2` 1440/390。
- **依赖**：C-002 已定稿；后端 T1 与前端 M1 并行。**不改旧页面/路由代码；共享文件只 additive**；回滚=回退 v2 文件/镜像。
- **明确不做**：connection reveal/rotate、test/actions/audio/setup/rooms 表单；任何写操作。M1 的 `GET /devices/{id}/connection` 只读掩码已包含。

### M2（1 人日）设备详情：测试/动作/活动/连接 reveal/rotate

- **交付**：`POST /test`、`POST /actions`（volume.read/set、led.set、audio.say/play/stop）、`GET /activity`、`POST /connection/reveal`、`POST /token/rotate`；前端 test/activity 两 tab + connect reveal/rotate。
- **验收**：
  - 给 mock fleet：`playback`/`volume`/`led` 有/无两种设备，按钮按 capability 出现/隐藏；缺能力 action 返回 409 `capability_unsupported`，UI 显示原因。
  - `audio.say` 在 dot2 页只从 dot2 出声（双房间 harness，`tests/test_app_fleet.py:377`）；UI 事前提示“会真的出声并更新 `last_active_room`（控制台试一句的副作用）”。
  - 测试页 checks 显示 ok/fail/not_applicable；不播放、不改灯/音量。
  - token 默认掩码；reveal 才出值；rotate 429/409 有文案；activity 日志标 `matched_by=literal`。
  - `pytest tests/test_api_v2_read.py tests/test_app_fleet.py -q`。
- **依赖**：M1。**回滚**：`meta.features.device_actions/device_activity=false`，页面隐藏。

### M3（1 人日）设备创建/修改/删除 + 配置 tab

- **交付**：`POST/PATCH/DELETE /api/v2/devices`；前端 config/danger tab；create 只在 `/v2/devices/add` 的 M6 入口之外也可 API 用；写操作 `restart` 确认。
- **验收**：
  - 新建第二台（mock plug）不覆盖第一台；重复 id 409；无 model_id 多机型 422；端口冲突 `422 port_conflict`；最后一台 disabled/删除 → `restart.status=stopped`。
  - PATCH 只改目标 id；token/secret 不回显；不可变字段 422；保存前 UI 二次确认“重启全屋音频面”；`restart.error` 红色显示。
  - `pytest tests/test_api_v2_read.py tests/test_devices_management.py tests/test_config_v3.py -q`。
- **依赖**：M2。**回滚**：features.device_create=false；v1 不受影响。

### M4（1 人日）音频 provenance + 每设备覆盖编辑

- **交付**：`GET/PATCH /api/v2/devices/{id}/audio`；前端音频表、来源 chips、各层抽屉、覆盖/恢复、全屋重启确认、全局影子提示。
- **验收**：
  - §5.6 的 9 个用例全绿；`sources` 五枚举；ack 特例；`null` 清除；`plug_resolved=false`。
  - UI：设备值等于全局仍显示“设备覆盖”；保存确认含真实平面数；`restart.error` 可见。
  - `pytest tests/test_api_v2_provenance.py tests/test_config_v3.py -q`。
- **依赖**：M3。**回滚**：features.device_audio_provenance=false；只读仍可用。

### M5（1 人日）房间与全屋 + 全局音频

- **交付**：`PATCH /api/v2/house`、`GET/PATCH /api/v2/house/audio`、`POST /api/v2/house/audio/restart`；前端 `/v2/rooms`（房间卡片、default_output、room_priority、max_planes、全局音频、warnings）。
- **验收**：
  - default_output 改到 dot2 与 `/health.multi_device.default_output` 一致；422 按 path 回显；重复房间 warning。
  - 全局音频保存后带覆盖设备不变，页面提示“N 台有覆盖”；不自动重启，按钮触发 `restart`。
  - `pytest tests/test_config_v3.py tests/test_config_catalog.py tests/test_api_v2_read.py -q`。
- **依赖**：M4。**回滚**：features.house_audio/house=false；v1 不变。

### M6（1 人日）八步 add 模式 + 首次安装

- **交付**：`GET /api/v2/setup/plan`、`POST /api/v2/setup/plan/steps/{step}`；前端 `/v2/devices/add`、`/v2/setup`；复用八步 UI 新写（不 import 旧 SetupView）。
- **验收**：
  - 已有 2 台时 add 第 3 台：`device.create` 成功、前两台字段不变；重复 id 409；house keys/models 未完成 → blocked。
  - prepare 对 B 类要 rollback_ack；wake/connect/test/verify 指向新设备；首装 `mode=first-run` 正常。
  - `pytest tests/test_setup_api.py tests/test_setup.py tests/test_api_v2_read.py -q`（旧 5/8 步回归保留）。
- **依赖**：M3（设备服务）、M5（house 状态）。**回滚**：features.setup_plan=false；旧 `/setup` 不受影响。

### M7（1 人日）活动/日志/对话/概览串联

- **交付**：设备 activity 全量；v2 对话页（`/v2/conversation`，过渡用 v1 `/turns`）、日志页（`/v2/logs`，v1 `/logs`）、全屋概览“异常优先 + 房间行”、设备页活动 tab。
- **验收**：
  - 停一台 mock 平面：`/v2/run` 显示 1/2 + 具体房间；点击到设备；对话/日志标注“全屋/字面匹配”，不冒充单设备。
  - `pytest tests/test_api_v2_read.py tests/test_api.py tests/test_app_fleet.py -q`。
- **依赖**：M2/M5。**回滚**：features.device_activity=false；页面隐藏。

### M8（1 人日）运行类页面 v2 重写（对话/日志/记忆）

- **交付**：`/v2/conversation`、`/v2/logs`、`/v2/memory` 的 v2 组件（新写，不 import 旧 views）；过渡期调 v1 稳定端点；i18n/空态/错误。
- **验收**：三页功能与旧页对等（过滤/复制/空态）；无旧组件 import；`npm run typecheck && npm run build`；截图。
- **依赖**：M7。**回滚**：features 关页；旧页不受影响。

### M9a（1 人日）页面组 A：语音助手 / 音乐 / 凭据 / 体检

- **交付**：`/v2/voice-assistant`、`/v2/music`、`/v2/credentials`、`/v2/health`；新写 v2 组件，过渡期用 v1 稳定端点；v2 字典补齐。
- **每页必测字段**：

| 页面 | 必测字段/动作 |
|---|---|
| 语音助手 | `assistant.persona/name`、回复风格、`session.timeout_s`、话术保存/逐键恢复/全部恢复、422 按 key 回显 |
| 音乐 | `music.enabled`、`source`、凭据只写、`test`、播放/暂停/继续/停止/下一首、队列与最近播放 |
| 凭据 | secret 名列表、写入/删除、永不回显、401/422 |
| 体检 | `doctor.verdict/checks[]`、每平面 `speaker_connected/restarts/wake_*`、火山额度字段（余额/今日/7 天 TTS/ASR） |

- **验收**：四页与旧页逐字段对等；写操作 422 按 path 回显；无旧组件 import；`npm run typecheck`；zh/en + 1440/390 截图；grep 钩子通过。
- **依赖**：M8。**回滚**：页面级 features 关闭；旧页不动。

### M9b（1 人日）页面组 B：模型与语音服务 / 工具与集成 / 系统

- **交付**：`/v2/providers`、`/v2/integrations`、`/v2/system`；新写 v2 组件，过渡期用 v1 `config/catalog`、`config/values`、providers/mcp 端点。
- **每页必测字段**：

| 页面 | 必测字段/动作 |
|---|---|
| 模型与语音服务 | 三条链 LLM/ASR/TTS 顺序、主用/兜底、provider 的 `model/voice/api_key_env` 状态、试听（不写配置）、保存后重启提示 |
| 工具与集成 | 内置工具数/names、MCP 服务器地址/token 只写、HA 连接状态/错误、暴露策略 `domain|whitelist|all` |
| 系统 | `web.host/port`、`web.auth_required`、配置原文 `GET /config/raw` 的 `path/mtime/redacted_keys/truncated`、`system.log_level` 只在日志页、重启命令 |

- **验收**：三页与旧页逐字段对等；provider 试听不污染 dirty；MCP token 不回显；`config/raw` 只读、无写入路径；`npm run typecheck`；zh/en + 1440/390 截图；grep 钩子通过。
- **依赖**：M9a。**回滚**：页面级 features 关闭；旧页不动。

### M10（1 人日）双语 / a11y / 移动端 / 端到端 / 0.7 切默认 runbook

- **交付**：
  - v2 全量 zh/en（`web/src/v2/i18n.ts`，键前缀 `v2.`，`en: Record<MsgKey,string>` 类型完整）、390 抽屉/表格/滚动、focus/tab 语义。
  - `/v2` 端到端清单与截图；旧侧栏隐藏 `/panel/devices`（Q5，只改 `nav.hidden`，旧页 URL 仍可开）。
  - 0.7 切默认 runbook（步骤级，见下）。
- **每页最终必测字段**（在 M9a/M9b 表之上再跑一遍）：

| 范围 | 必测 |
|---|---|
| 全屋/设备 | online 计数、default_output、warnings、每设备 status_reason、connected=null、capability 降级 |
| 设备详情 | 配置保存 + `restart.status`、音频来源 chips、activity 的 `matched_by`、连接 reveal/rotate、删除 confirm |
| 房间/添加 | default_output/room_priority 422、global audio 覆盖提示、setup plan 的 blocked/next_step、第二台不覆盖第一台 |
| 运行/配置页 | 对话/日志/记忆、语音助手/音乐/凭据/体检、providers/integrations/system 的字段表（M8/M9a/M9b） |
| 全局 | zh/en、light/dark、1440/390 无横向溢出；401/429；旧 `/` 行为不变 |

**0.7 切默认 runbook（M10 只演练，不实际切）**

1. **旧 router base（选定做法）**：`web/src/router.ts:32` 现在是 `createWebHistory()`（根 base）。0.7 前把它改成**按路径推导 base** 的一行：
   ```ts
   const base = window.location.pathname.startsWith('/legacy') ? '/legacy/' : '/'
   history: createWebHistory(base)
   ```
   旧 app 同一份产物既能在 `/` 也能在 `/legacy/` 工作；不新增 legacy 入口/第二套 router。0.6.x 不合并这一行（旧控制台保持根 base）。
2. **FastAPI 挂载**（`src/rui/web/api/app.py`，注册在 SPA catch-all 之前）：
   - `/api/v2/*` → v2 API；
   - `/v2`、`/v2/*` → `dist/v2.html`（深链刷新）；
   - `/legacy`、`/legacy/*` → 旧 `dist/index.html`；
   - `/` → 0.7 默认 `dist/v2.html`；回滚开关 `RUI_CONSOLE_DEFAULT=legacy` 时 → 旧 `dist/index.html`；
   - `/panel/devices` → `308 /devices`；`/devices` 是 0.7 的短路径 alias（302 `/v2/devices`）；
   - 其它 `/panel/*`、`/secrets`、`/channels`、`/setup`、`/diagnostics` → `308 /legacy/<原路径>`；
   - `/assets/*` 同时服务旧/v2 的 hash 资源。
3. **验收命令**：
   ```bash
   curl -s http://127.0.0.1:8080/ | grep -q 'id="v2-app"'
   curl -sI http://127.0.0.1:8080/panel/devices | grep -i '^location: /devices'
   curl -s http://127.0.0.1:8080/legacy/ | grep -q 'id="app"'
   curl -sI http://127.0.0.1:8080/panel/overview | grep -i '^location: /legacy/panel/overview'
   echo 'RUI_CONSOLE_DEFAULT=legacy' >> /vol1/rui/.env   # compose 用 env_file: .env
   docker compose -f /vol1/rui/docker-compose.yml up -d --force-recreate rui
   curl -s http://127.0.0.1:8080/ | grep -q 'id="app"'
   ```
   再在浏览器逐项：`/` v2、`/legacy` 旧、旧书签 `/panel/overview` → `/legacy/panel/overview`、`/v2/devices/dot2` 刷新可开。
4. **回滚开关**：把 `/vol1/rui/.env` 的 `RUI_CONSOLE_DEFAULT` 改回 `v2`（或删掉该行）后 `docker compose ... up -d --force-recreate rui`；旧 router 的 base 由路径推导，无需重新构建/改代码；数据/配置无迁移。
- **验收**：上述命令全绿；zh/en + light/dark + 1440/390 截图；旧 `/` 与 `/v2` 同时可用；演练记录写进 `docs/HANDOFF.md`；`pytest tests/ -q` 或 CI 等效。
- **依赖**：M1–M9b。**不在本里程碑真正切默认**（0.7 发布动作）。

**依赖图**

```text
M1 ─ M2 ─ M3 ─ M4 ─ M5 ─ M6 ─ M7 ─ M8 ─ M9a ─ M9b ─ M10
```

> 单写者约束：M1–M8 + M9a/M9b + M10 都改 `web/src/v2/**` 与 `web/src/v2/i18n.ts`；不要并行派两个实现者改同一文件。i18n key 由 M1 先落命名空间，后续只增。

## 8. 不做清单（明确排除）

1. **不做账号 / 角色 / 登录会话**：沿用单 `X-Auth-Token`（`app.py:3073-3096`），不引入 users/roles/HA ingress。
2. **不照抄 EchoMuse 的 USB/ADB/WebUSB/TWRP/刷机/emOS/OTA/firmware 发布**：设备侧安装只给链接/命令/参数表单，完成度由连接证据判定（`docs/device-management-research.md:308-313`、`:367`）。
3. **不引入 Home Assistant / ESPHome 依赖**：`integrations` 页里的 HA 仍是可选 MCP 集成，不是控制台前置。
4. **不做 approve/pending 队列、每设备证书/CA、mDNS 发现、TLS 设备链路、Wi-Fi 切换**（`docs/device-management-research.md:369-371`、`:375-379`）。
5. **不引入 SQLite / 新设备注册表**：继续 `config.yaml` + `devices[]` + `params`（`docs/device-management-research.md:691-720`）。
6. **不做房间实体/房间 CRUD**：`room` 只是 `DeviceConfig.room` 字符串（`schema.py:255`）；不建 rooms 表、不做房间分组层级。
7. **不做多房间同时出声 / 全屋广播 / 主音箱角色**：守住「一个回合、一个输出 owner」不变量（`docs/multi-device-design.md:99-100`）。
8. **不做按房间的用量/成本分摊**：火山额度仍是全屋总量（`HealthView.vue:150-185`）；没有可靠数据源，不做假数字。
9. **不做按设备的 provider/密钥链**：LLM/ASR/TTS 链仍是全局（`schema.py` 的 `ProvidersConfig`），设备页不提供第二条链。
10. **不做每平面单独重启按钮（0.6.x）**：后端保存会重建整个 fleet（`app.py:5184-5198`），UI 如实说影响范围；per-plane restart 留 0.7 评估。
11. **不修改旧控制台的功能/路由/页面**（`App.vue`、旧 `router.ts`、旧 `views/*`、`PanelHost.vue` 等）；v2 也不 import 它们。唯一例外：Q5 的 0.6.x 旧侧栏隐藏 `/panel/devices` 入口（M10，只改 `nav.hidden`，URL 仍可直达）；0.6.x 不删除 `/panel/:id`、`/secrets`、`/channels`、`/diagnostics`。
12. **不在 0.6.x 删除任何 v1 API/旧 URL**；旧设备面板仅隐藏，0.7 才删除并重定向（§6 V2-10）。
13. **不做自动发现/网络扫描**：保留现有 `/api/v1/devices/probe`（TCP/SSH banner）与向导的 host 探测，不做 mDNS/SSDP。
14. **不在公开仓库/截图/日志里出现 token、完整 LAN IP、真实 key**：连接页默认掩码，诊断包脱敏（沿用 `docs/device-management-research.md:645-649`）。
15. **不做强制告别 / 模拟唤醒**：告别由 `session.timeout_s` 空闲时钟触发（`app.py:2875`），控制台不注入假 wake；设备页的「试一句」只走文本回合（`app.py:3637-3711`），不伪装成麦克风/唤醒验证。
16. **不在 C-002 里定义非设备域 v2 API**：对话/日志/记忆/配置页在 M8/M9a/M9b 过渡期调用 v1 稳定端点，v2 只重写 UI；设备域之外不新造一套 API。
17. **不在 M1–M8 + M9a/M9b + M10 内切默认**：`/` 在 0.6.x 始终是旧控制台；`/` → `/v2`、旧 shell → `/legacy` 是 0.7 的独立发布动作。

---

## 9. 已由 Lead 代 9 哥 拍板的决策

> 2026-10-25，9 哥 在睡觉，Lead 代决；以下不再作为开放问题。C-002 是 API 真源；若实现中发现契约冲突，回 Lead 决策，不自行改契约。

| # | 决策 | 结论 | 落点 |
|---|---|---|---|
| Q1 | 设备是否单独成一级导航段？ | **是**：运行 / 设备 / 配置三段 | §3.2；v2 侧栏 |
| Q2 | 添加第 N 台设备路径？ | **是**：复用八步向导的 add 模式（显式 id/room，keys/models 折叠） | §3.3、M6；C-002 §7.23/7.24 |
| Q3 | 每设备音频覆盖在哪编辑？ | **只在设备页**；全屋默认在 `/v2/rooms` | §5.4/§5.7；M4/M5 |
| Q4 | 0.6.x 接受“改一台也重启全屋音频面”？ | **接受**；UI 必须二次确认并明示“保存将重启全屋音频面” | §5.4、M3/M4；C-002 restart |
| Q5 | 旧 `/panel/devices` 删除时机？ | **0.6.x 隐藏旧侧栏入口、0.7 删除并重定向 `/devices`** | §6 V2-10；旧控制台不变 |

**技术待确认**：无阻塞项。以下已由 C-002 固定，不需要再问：v2 静态托管路径、capability 枚举、provenance 五来源、错误对象、setup add 端点、restart 语义、secret 纪律、0.7 回滚方案。

---

## 附录 A：实现文件清单（供派工，不在本任务范围）

| 类型 | 文件 | 里程碑 |
|---|---|---|
| 契约 | `docs/contracts/C-002-console-v2-api.md`（已交付） | 先行 |
| 前端入口/构建 | `web/v2.html`、`web/vite.config.ts`（`rollupOptions.input` 增加 v2，旧入口不变） | M1 |
| 前端壳（在制实际） | `web/src/v2/main.ts`、`web/src/v2/router.ts`（`/run`+`/devices`+`/config`）、`web/src/v2/App.vue`、`web/src/v2/style.css`（复用 tokens） | M1 |
| 前端字典 | `web/src/v2/i18n.ts`（键前缀 `v2.`；`MsgKey` 自有，`en: Record<MsgKey,string>` 类型完整；语言状态 `rui-language`） | M1 |
| 前端 API | `web/src/v2/api.ts`（C-002 类型/错误对象/`X-Auth-Token`） | M1 |
| 前端页面（在制实际） | `web/src/v2/views/{RunView,DevicesView,DeviceDetailView,ConfigView,NotFoundView}.vue`；M5/M6 新增 Rooms/Add/Setup 按实际落地名 | M1–M6 |
| 前端组件（在制实际） | `web/src/v2/components/{StateBlock,StatusBadge,CapabilityChips,ProvenanceTag,TokenDialog}.vue`；M3–M7 的 Config/Test/Activity/Danger 组件按实际落地名 | M2–M7 |
| 前端运行/配置页 | `web/src/v2/views/{ConversationView,LogsView,MemoryView,HealthView,VoiceAssistantView,MusicView,ProvidersView,IntegrationsView,SystemView,CredentialsView}.vue` | M8/M9a/M9b |
| 后端 | 实际落成单文件 `src/rui/web/api/v2_api.py`（不是 `v2/` 包）；测试/路由注册在 `app.py` | M1–M7 |
| 后端服务 | 复用 v1 的服务层（`_write_devices`/`_restart_audio`/`setup_store`/`effective_device_audio`），v2 不复制 merge 逻辑 | M1–M7 |
| 后端托管 | `src/rui/web/api/app.py` 挂 `/api/v2` 与 `/v2` 静态（只加路由，不改旧路由） | M1 |
| 测试 | `tests/test_api_v2_read.py`、`tests/test_api_v2_provenance.py`、`tests/test_api_v2_setup.py`；保留全部 v1 回归 | 各里程碑 |

## 附录 B：关键证据索引（本文引用）

- 契约：`docs/contracts/C-002-console-v2-api.md`（C-002/1，全部 v2 端点/字段/错误码/来源/回滚）。
- 现状/旧控制台：`web/src/nav.ts:15`、`43-47`、`80-103`；`web/src/router.ts:16-30`（`createWebHistory()` 在 :32）；`web/src/panels.ts:115-202`；`web/src/i18n.ts:1684`（`MsgKey`）；`web/src/views/PanelHost.vue:32-33`、`239-277`。
- 旧面板/设备：`src/rui/web/api/app.py:441-453`、`471-501`、`508-548`、`649-657`、`4901-4909`、`5184-5198`、`5288-5291`、`5525-5541`、`5562-5602`、`5604-5664`、`5666-5741`、`5855-5931`、`5933-6010`、`6012-6024`；`src/rui/web/api/setup_api.py:173-185`、`307-311`、`926-933`。
- 音频模型：`src/rui/config/schema.py:230-283`、`525-555`、`558-625`、`650-661`、`1055-1063`、`1102-1156`；`src/rui/config/catalog.py:1256-1278`、`1539-1585`；`app.py:1109-1144`。
- 能力/健康：`src/rui/device/contract.py:26-96`、`168-203`；`app.py:1487-1511`、`1685-1755`、`1797-1804`、`3098-3148`。
- 对话/日志：`app.py:1941-1948`、`6380-6398`；`src/rui/trace.py:48-53`、`src/rui/session/turn.py:104-125`。
- 文档：`docs/HANDOFF.md:13-17`、`25`、`86`；`docs/device-management-research.md:46-55`、`82-98`、`296-299`、`306-313`、`367-371`、`645-649`、`691-720`、`835-837`、`981-984`；`docs/multi-device-design.md:15-28`、`99-100`、`269-291`、`314`、`326-338`；`docs/console.md:8-32`、`270-276`。

---

> **下一步**：Lead 按 §7 建 M1–M8 + M9a/M9b + M10 任务卡；**M1 可独立上线**（v2 外壳 + `/v2/run`、`/v2/devices`、设备详情只读、静态托管冒烟，不碰旧控制台）。后端按 C-002 先交 T1 只读端点；M1 通过后逐项点亮 `meta.features`，0.7 再做默认切换与 `/legacy`。
