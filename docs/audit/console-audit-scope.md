# 控制台功能审计范围（v2 全量 + v1 对齐），2026-10-26 基线 0.7.2

> 用途：0.7.6 / 0.7.7 / 0.7.8 的审计执行清单（ROADMAP §3.4–§3.6）。
> 目标不是「页面能打开」，而是**每个按钮、每个保存动作都真点过，并且证明落库**。
> 产出：`docs/audit/console-functional.md`（逐项结论）+ `docs/audit/console-v1-v2-parity.md`（对齐矩阵）
> + `docs/audit/console-shots/`（截图）。
> 基线实例：本地 `rui serve --no-audio` + 示例数据（`Living Room Speaker` / `Bedroom Speaker`），
> 截图不得含内网 IP、token、真实房间名、人脸。

## 0. 审计范围总量（先定边界，避免漏页）

| 类别 | 数量 | 明细 |
|---|---|---|
| v2 真实路由 | **14** | `/devices`、`/devices/add`、`/devices/:id`（7 个 tab）、`/rooms`、`/setup`、`/run`、`/conversation`、`/logs`、`/memory`、`/voice-assistant`、`/tools`、`/music`、`/credentials`、`/health`（另 `/config` 为 302 → `/voice-assistant`） |
| v2 全局壳（每页都有） | 1 组 | 顶部导航、Run/Config 子导航、语言、主题、token 对话框、meta 门禁重试、「旧控制台」链接 |
| v2 设备详情 tab | **7** | overview / audio / config / test / activity / connect / danger |
| v2 后端端点 | **24** | 见 §2；其中 2 个孤儿（无前端调用） |
| v2 页面内交互元素 | **约 95** | 见 §3 分组清单（按钮 + 表单 + 开关 + 危险操作 + 对话框） |
| v1 注册面板 | **11** | overview / devices / turns / voice-assistant / providers / integrations / system / credentials / logs / memory / music |
| v1 手写页 | **7** | `/setup`、`/channels`、`/secrets`、`/diagnostics`、`/tools`、`/providers`、`/system` |
| 需要验证的异常态 | **6 类** | 401（无 token）、429（冷却）、409（记忆关闭）、404（资源没了）、422（字段错误）、5xx/离线 |

## 1. 结论口径（每行必须能判定）

每个交互元素一行，字段固定：

```
页面 | 元素(file:line) | 动作 | 端点 | 结果 ∈ {可用, 不可用, 文案错误} | 证据 | 备注
```

判定规则：
- **可用** = 动作发出 → 端点 2xx → **界面状态更新** → **重新加载页面/读 API 后值仍在**（保存类必须有这一步）；
- **不可用** = 点不动 / 报错 / 静默无效果 / 保存后重载丢失；
- **文案错误** = 行为对但文字误导（含 i18n 缺失、英文碎片、术语不一致、把「未测」写成「支持」）。
- 危险操作（删除设备/房间、清 token、恢复配置）必须在**测试实例**演练，记录二次确认文案与实际行为。
- 每个 tab、每个 `meta.features=false` 状态至少各一例（v2 有 8 个 feature gate：`devices`、`device_create`、`setup_plan`、`house`、`house_audio`、`device_audio_provenance`、`device_activity`、`device_actions`）。

## 2. v2 后端端点 → 页面映射（含孤儿）

| # | 方法 路径 | 调用页 | 审计要点 |
|---|---|---|---|
| 1 | GET `/api/v2/meta` | 全局壳 | 版本、features、合约拒绝态 |
| 2 | GET `/api/v2/house` | devices/rooms/run/detail | 房间聚合、warnings、setup 状态 |
| 3 | GET `/api/v2/plugins` | setup/detail | 插件与模型目录 |
| 4 | GET `/api/v2/plugins/{id}` | **无（孤儿）** | 0.7.6 决定：删 or 用起来 |
| 5 | GET `/api/v2/capabilities` | devices/detail | 能力标签与降级 |
| 6 | GET `/api/v2/devices` | 5 页 | 列表/过滤 |
| 7 | GET `/api/v2/devices/{id}` | detail | 参数、plane、setup |
| 8 | GET `/api/v2/devices/{id}/audio` | audio tab、rooms（N+1） | 4 层、来源、override |
| 9 | GET `/api/v2/devices/{id}/activity` | activity | 轮次+日志混合 |
| 10 | GET `.../connection` | connect | 掩码 URL |
| 11 | POST `.../connection/reveal` | connect | 限流 + 明文 token |
| 12 | PATCH `/api/v2/house` | rooms | 多设备策略 + 重启语义 |
| 13 | GET `/api/v2/house/audio` | rooms | 全局音频 + 显式值 |
| 14 | PATCH `/api/v2/house/audio` | rooms | null 清除叶子 |
| 15 | POST `/api/v2/house/audio/restart` | rooms（2 处） | 全屋重启 |
| 16 | POST `/api/v2/devices` | **无（孤儿）** | 0.7.6 决定：删 or 接进向导 |
| 17 | PATCH `/api/v2/devices/{id}` | config tab | 原子字段写 |
| 18 | DELETE `/api/v2/devices/{id}?confirm=` | danger | 输入 id 才可删 |
| 19 | PATCH `/api/v2/devices/{id}/audio` | audio tab | override 写 + 回读校验 |
| 20 | POST `.../token/rotate` | connect | 10 s 冷却 |
| 21 | POST `.../test` | test | 只收集证据 |
| 22 | POST `.../actions` | test | 能力门控（say/volume/led/play/stop） |
| 23 | GET `/api/v2/setup/plan` | setup/add | 8 步计划 |
| 24 | POST `/api/v2/setup/plan/steps/{step}` | setup/add | 每步只跑声明的 action |

v2 仍调用的 v1 端点（C-002 §1.2 白名单，共 21 条）：`config`、`config/catalog`、`config/values`、`mcp`、`music*`、`secrets`、`providers/*`、`health`、`doctor`、`config/recovery*`、`turns`、`logs`、`memory`。审计时**逐条确认返回字段与界面一致**（不只看 200）。

## 3. 逐页交互清单（95 项，审计时逐行填结论）

> 行号是 0.7.2 基线；实现改动后以卡片报告为准。`§` 见上表。

### 3.1 全局壳（10）
| 元素 | file:line | 动作 |
|---|---|---|
| 品牌 → `/devices` | `App.vue:191` | 导航 |
| 顶部 4 个主入口 | `App.vue:207-216` | Run/Devices/Rooms/Config |
| Run 子导航 4 项 | `App.vue:277-286` | run/conversation/logs/memory |
| Config 子导航 5 项 | `App.vue:295-304` | 5 个配置页 |
| 语言 中/英 | `App.vue:221-238` | localStorage，切换后**所有页面文案**抽检 |
| 主题切换 | `App.vue:241-249` | localStorage |
| token 对话框打开/保存/清除 | `App.vue:251-261`、`TokenDialog.vue:70-90` | 无 API，401 后行为 |
| 「旧控制台」链接 | `App.vue:263-266` | **0.7.8 必须移除** |
| meta 门禁重试 | `App.vue:335,345-349` | 合约不匹配态 |
| token 横幅 | `App.vue:319-321` | 401 引导 |

### 3.2 `/devices`（6）
新增入口（空屋→`/setup`，非空→`/devices/add`）、刷新（3 个 GET）、401 取令牌、空态两条 CTA、`/legacy` 兜底（`DevicesView.vue:242`，**0.7.8 必须改**）、设备卡片跳转、按房间分组渲染。

### 3.3 `/devices/:id`（7 tab + 2 全局 = 9）
返回、刷新（6 个 GET）、7 个 tab 切换、未保存草稿拦截对话框（`DeviceDetailView.vue:430-439`）。

### 3.4 audio tab（10）
放弃草稿（上/下）、保存（上/下）→ `PATCH .../audio` + 回读、每个叶子数字输入、bool/enum 选择、恢复继承（置 null + 回读）、3 个重试横幅、4 层明细折叠、保存确认对话框、恢复确认对话框。
**审计要点**：保存后必须看到「effective 值变化 + inherited/overridden 标记正确」；只改草稿时不能写库。

### 3.5 config tab（9）
房间、host、`listen_port`（空=继承）、`volume_target`、唤醒词 ×3（增/删）、enabled、重置、保存（确认框 → `PATCH /devices/{id}`）、错误/重启提示。
**审计要点**：`listen_port` 留空与填值两种；保存后重载仍在新值；非法值给 422 文案。

### 3.6 test tab（test + actions，11）
Run/rerun test（`POST .../test`）、volume.read、音量滑块、volume.set、LED 效果选择、led.set、say 文本（≤500）、say 发送（内联确认）、play 文件/loop/gain、audio.play、audio.stop、错误重试。
**审计要点**：能力未声明时按钮应隐藏/置灰（capability gating）；say 实际出声（真机）/ 无设备时给明确错误。

### 3.7 activity（2）
时间线加载、刷新/重试。

### 3.8 connect（7）
三种重试、Reveal token（限流）、隐藏明文、Rotate（确认 → 10 s 冷却）、隐藏、adapter-managed 禁用说明。

### 3.9 danger（3）
去配置页禁用链接、删除设备（输入 id 才可提交）、提交后返回列表。

### 3.10 `/rooms`（16）
刷新（house+devices+house/audio+N×device audio）、401、空态添加、设备链接、「设为默认输出」、策略 enabled / max_planes / default_output、房间优先级 上/下/删、优先级添加、策略放弃/保存（确认）、**全屋重启音频面**、全局音频每叶子输入、清除叶子 / 撤销清除、音频放弃/保存（确认）、重启（第二处）、override 设备 chip、离开拦截。

### 3.11 `/run`（5）
刷新、401、异常清单跳转、空态 `/legacy` 兜底（`RunView.vue:247`，**0.7.8 必须改**）、房间设备 chip。

### 3.12 `/conversation`（7）
20/50 条切换、5 s 自动刷新、刷新、设备过滤、房间过滤、清除过滤、重试。

### 3.13 `/logs`（8）
200–2000 行、3 s 自动刷新、刷新、级别过滤、自动滚动、复制全部、显示更多 +300、恢复自动滚动、单行复制。

### 3.14 `/memory`（6）
新增（≤200 字）、只看未确认、刷新、单条确认、单条删除（确认框）、显示全部。

### 3.15 `/setup` 与 `/devices/add`（8 步 ≈ 14 控件）
插件选择、模型选择、刷新计划、错误重试、8 步折叠、params（id/房间/host/端口/唤醒词/manifest 参数/提交）、id 冲突处理、keys（保存/自定义槽位/测试/服务商）、models 刷新、prepare（勾选 + 回滚确认）、wake（3 词）、connect（地址证据/刷新）、test（运行）、verify（真说话 + 确认）、429 冷却。
**审计要点**：每一步失败后的可恢复性；`device.create` 未返回 id 前后续步骤必须禁用。

### 3.16 `/voice-assistant`（6）
刷新（脏确认）、分组字段编辑、设备只读卡与跳转、重置、保存（重启确认）、4 个跳转链接。

### 3.17 `/tools`（6）
刷新（脏确认）、`tools.*` 字段编辑、MCP/HA 只读表、重置、保存（重启确认）、4 个跳转。
**审计要点**：只读表与 v1 写入能力的差距 → 进对齐矩阵。

### 3.18 `/music`（7）
刷新、音源选择（客户端）、文档链接、凭据槽输入、清空槽位（危险确认）、测试（未保存草稿）、保存/切换音源。
**审计要点**：**没有播放控制**（v1 有 6 个按钮）→ 对齐矩阵必须给结论。

### 3.19 `/credentials`（7）
刷新（3 个 GET）、设置/替换、打开音乐密钥、Verify probe、preset 选择、提交（空值 = 危险清除）、清除确认。

### 3.20 `/health`（6）
刷新（health/doctor/recovery）、异常跳设备、下载坏配置、恢复（确认 + mode）、健康时跳配置、3 个跳转链接。

## 4. v1 → v2 功能对齐矩阵（0.7.6 产出，0.7.7 收口）

| # | v1 功能 | v2 现状 | 状态 | 处置（0.7.6 定，0.7.7 执行） |
|---|---|---|---|---|
| 1 | 概览「试一试」 | test tab `audio.say` | 部分 | 说明「按设备」；可选：加全屋 say |
| 2 | 概览「停止出声」 | `audio.stop` | 部分 | 同上 |
| 3 | 概览音频面统计 | `/run` + `/health` | 有 | 说明拆两页 |
| 4 | 设备列表 | `/devices` | 有 | — |
| 5 | 混音器音量 0–255 | test tab | 部分 | 说明位置 |
| 6 | 设备增删改 | `/devices/add`、config、danger | 有 | — |
| 7 | 设备探测（`POST /api/v1/devices/probe`） | 无 | **缺** | 补按钮 或 在 v2 明确说明 |
| 8 | 设备测试 | TestPanel | 有 | — |
| 9 | connect / token 轮换 | Connection | 有 | — |
| 10 | LED / 参数旋钮 | `led.set`；参数只读 | 部分 | 说明「创建时填」 |
| 11 | 音频与唤醒 ~50 字段 | audio + config（C-001 override 子集） | 部分 | 说明可编辑范围与只读范围 |
| 12 | 对话 | `/conversation` | 有 | — |
| 13 | 助手/会话配置 | `/voice-assistant` | 有 | — |
| 14 | **话术编辑器（49 模板）** | 无 | **缺** | 补 或 在 v2 明确说明（0.7.7） |
| 15 | **模型/语音服务（卡片/试听/音色）** | 仅 `/credentials` probe | **缺** | 同上 |
| 16 | 工具内置/暴露 | `/tools` | 有 | — |
| 17 | MCP/HA 写入管理 | 只读表 | 部分 | 补 或 说明 |
| 18 | 工具注册表清单 | 无 | **缺** | 说明（低价值） |
| 19 | **系统/Web/安全 13 字段** | 无 | **缺** | 说明（改 config.yaml）或补 |
| 20 | 凭据/密钥 | `/credentials` | 有 | — |
| 21 | 日志 | `/logs` | 有 | — |
| 22 | 记忆 | `/memory` | 有 | — |
| 23 | **音乐播放控制（6 按钮）** | 无 | **缺** | 补（v1 有、用户会用）或说明 |
| 24 | 音乐状态/来源/凭据 | `/music` | 有 | — |
| 25 | `music.enabled` / music 配置组 | 无 | **缺** | 补或说明 |
| 26 | 安装向导 8 步 | `/setup`、`/devices/add` | 有 | — |
| 27 | **渠道 Telegram/微信** | 无 | **缺** | 说明（0.7.8 不补，写已知限制） |
| 28 | 体检 + billing 配额 | `/health`（无配额） | 部分 | 说明或补配额 |
| 29 | 只读工具注册表页 | 无 | **缺** | 说明 |
| 30 | （v2 独有）房间/全屋策略 + 全局音频 | `/rooms` | v2+ | — |
| 31 | （v2 独有）配置恢复下载/回滚 | `/health` | v2+ | — |
| 32 | v1 `/panel/*` 深链 | 无 | 设计删除 | 0.7.7 给 302 映射（不复活） |

## 5. 0.7.8 默认切换的验收清单（D）

| # | 项 | 判定 | 命令 |
|---|---|---|---|
| 1 | 全新部署 `/` 落 v2 | 302 → `/v2/`，页面是 v2 壳 | `curl -sI http://host:8080/` |
| 2 | `/health.console` | `v2` | `curl -s /health \| jq .console` |
| 3 | 导航无旧面板 | v2 壳里无「旧控制台」链接 | `grep -rn '/legacy' web/src/v2` 只在允许处 |
| 4 | `/legacy` 弃用 | 200 + 横幅「已弃用，0.8.0 移除」 | 截图 |
| 5 | 老链接 302 | v1 路径逐条 302 到 v2 对应页 | `curl -sI` ×N |
| 6 | v2 每页可开 + 保存生效 | 14 路由 + 7 tab + 保存动作 | 审计文档复测 |
| 7 | README/docs | hero 换 v2 主界面；`/v1` 链接清空（历史文档除外） | `grep -rn '/v1' README.md docs/*.md` |
| 8 | 回滚 | 一行配置切回 legacy 并实测 | `web.console: legacy` + 重启 |

老链接映射（0.7.7 实现、0.7.8 验收）：`/panel/overview` → `/v2/run`、`/panel/devices` → `/v2/devices`、`/panel/turns` → `/v2/conversation`、`/panel/voice-assistant` → `/v2/voice-assistant`、`/panel/providers` → `/v2/credentials`、`/panel/integrations` → `/v2/tools`、`/panel/system` → `/v2/health`、`/panel/credentials` → `/v2/credentials`、`/panel/logs` → `/v2/logs`、`/panel/memory` → `/v2/memory`、`/panel/music` → `/v2/music`、`/setup` → `/v2/setup`、`/secrets` → `/v2/credentials`、`/diagnostics` → `/v2/health`、`/channels` → `/v2/`（无对应，写说明）、其余 → `/v2/`。

## 6. 已知孤儿/死代码（0.7.6 必须给结论）

| 项 | 证据 | 结论选项 |
|---|---|---|
| `GET /api/v2/plugins/{plugin_id}` 无前端调用 | `v2_api.py:1336` | 删 / 用起来 |
| `POST /api/v2/devices` 无前端调用（创建只走 setup plan） | `v2_api.py:2240`、`api.ts:1457` | 删 / 接进向导 |
| `v2.config.stub` / `.stubHint` 文案残留 | `i18n.ts:631,2160` | 删 |
| `/rooms` 对每台设备额外 GET audio（N+1） | `RoomsView.vue:138` | 记录性能，不阻塞 |
| v1 独有端点清单（20+） | 见 §3 表末 | 进对齐矩阵，不逐个补 |

## 7. 证据规范

- 截图命名：`<route>--<state>.png`（如 `devices--empty.png`、`audio--save-confirm.png`、`legacy--banner.png`），放 `docs/audit/console-shots/`。
- 每个保存动作：截图（改前/改后）+ `curl` 回读 JSON 片段（键值）。
- 每个 4xx/5xx：状态码 + 响应体前 200 字 + 触发步骤。
- 审计环境必须写明：镜像 tag / commit、`/health.version`、浏览器与视口（1600×1000）。
- 结果汇总表放文档首部：可用 / 不可用 / 文案错误 三个计数 + 未处理项（应为 0）。

## 8. 与旧审计文档的关系

- `docs/console-audit.md`（2026-10-23）：**只覆盖 v1**，路由表已过时（`/assistant`、`/voice`、`/models`、`/tools` 已不存在）→ 只作历史，不作基线。
- `docs/console-ia-audit.md`（v0.3.2）：panel 清单过时（现 11 个）→ 只取「重复入口」结论。
- `docs/audit/2026-10-code-health.md`：有「两套控制台并存」的定性结论与体量数字 → 引用，不重复审计。
- 本清单是**第一份 v2 元素级审计范围**，0.7.6 的执行结果写进 `console-functional.md`。
