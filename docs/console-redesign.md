# 控制台重构 — 设计方向与系统

> 2026-10-23 · console-ui（task-6）· 契约 `docs/config-catalog-contract.md`
> **IA 更新（0.4.2 Phase 4，2026-10-25）：** §3 与 §11 已改为单一 `nav` manifest；
> 旧四组/18 入口和旧路径重定向作废。视觉系统与页面构图仍适用。
> 本文是实现的**设计合同**：实现、视觉 critic、回归都以它为准。
> 目标不是"好看的网页"，而是一台家用语音设备的**控制面板**：开机即懂、改一个参数知道会发生什么、
> 在手机上也像被设计过。

## 1. 产品 / 用户 / 目标

- **产品**：rui 是自托管的家庭语音助手（音箱 + 控制面）。控制台是唯一的人机操作面。
- **用户**：设备主人兼管理员（9哥）：懂技术但不耐烦；两种使用姿势——
  - 手机（390px）站在音箱旁边，装完/调完立刻试一句；
  - 桌面（1440px）坐在电脑前，检查状态、批量改配置、看日志/诊断。
  - 偶尔是访客/家人：只需要看懂"在不在线"。
- **5 秒内必须看懂**：服务在线吗？音频面在吗？音箱在吗？现在能改什么？改完什么时候生效？
- **主行动**：① 看一眼状态 → ② 改一个行为或试一句话 → ③ 知道保存/重启的后果。
- **反目标**：不是数据大屏；不堆图表；不假装有数据；不做 AI 味卡片墙。

## 2. 视觉方向（一句话）

**一台安静的仪器：暖纸色底、编辑式衬线标题、等宽遥测字、发丝分隔线，和一个铁锈色的信号焦点。**
参考气质：Braun/Teenage Engineering 的实体面板 × 一本排版克制的设备手册。不要 SaaS 蓝、不要紫色 AI 渐变、不要玻璃拟态。

**标记元素（签名）**：全球统一的 **signal ledger（信号行）**——页面标题上方一条等宽小字：
`● LISTENING · v0.0.1 · UP 02:14 · 1 SPEAKER · AUDIO ON · WAKE 12`。
它是真的数据（`/health` + `/api/v1/audio`），不是装饰；桌面完整、手机只留状态点与在线词。
第二个记忆点：配置中心的**分组编号（01/02/03）**与左侧索引轨的强调条——同一套"仪器刻度"语言。

## 3. 信息架构

```
Rui console
├─ 运行 RUN        overview（概览）· turns（对话）· memory（记忆）· logs（日志）
└─ 配置与维护 CONFIGURE
   voice-assistant（语音助手）· devices（设备与音频）· music（音乐）
   providers（模型与语音服务）· integrations（工具与集成）· system（系统）
   [hidden] credentials（凭据，只直达）· utilities: channels / diagnostics / setup
```

- **唯一 manifest**：后端 `GET /api/v1/panels` 的每条 PanelSpec 带
  `nav {section: "run" | "configure", order, hidden, icon}`；`section` 只允许两值，
  `order` 决定段内顺序，`hidden` 只移出侧栏，`icon` 是 `AppIcon.vue` 的图标键。
- **11 个面板**：run 4 + configure 6 + hidden credentials；原“四组/18 入口”是 0.3.x 历史，
  Phase 4 已不再适用。
- **前端 `nav.ts` 只做两件事**：section 标题 + 三条工具路由（`/channels`、`/diagnostics`、
  `/setup`，`setupOnly`）；`/secrets` 不进入侧栏，`/panel/credentials` 作为 hidden 面板直达。
- **7 个兼容面板删除**：`assistant-session`、`speaker`、`devices-admin`、`tools`、`mcp`、
  `foundation`、`phrases`；内容并入合并页，不保留 alias。
- **路由是外壳**：`/`（→ overview）、`/panel/:id`、`/setup`、`/channels`、`/secrets`、
  `/diagnostics`、catch-all。旧顶层别名已从 router 删除；retired `/panel/<id>` 显示
  `PanelHost` 的 not-found。这是有意的 breaking change。
- 桌面：左侧 **244px 索引轨**（两段小标 + 条目 + 底部语言/主题/令牌）；内容最大 1160px，左对齐。
- 手机：顶部 56px 工具条（品牌 + 信号点 + 菜单）；导航为全屏抽屉；内容单列；
  配置保存条固定底部（`env(safe-area-inset-bottom)`）。

### 3.1 Phase 4（0.4.2）单一 manifest

Phase 4 把导航从“后端 group + 前端 id→group 映射 + i18n 标题覆盖”的三个事实来源，
收敛为 **PanelSpec.nav 单一 manifest**。前端不再维护 `PANEL_NAV_GROUPS` 或
`PANEL_TITLE_KEYS`；`nav.ts` 只剩 section 标题和工具路由。新增/移动页面只改后端一条
PanelSpec，侧栏、移动抽屉和深链同时生效。

**Breaking change**：旧 panel id（上列 7 个）不会被静默重定向到合并页，而是落到
`PanelHost` 的 not-found；旧顶层别名（`/overview`、`/devices`、`/logs`、`/assistant`、
`/settings`、`/voice`、`/models`、`/tools`）已从 router 删除，由 catch-all 兜底。
接受这个代价，是为了让“一个页面只有一个 URL”，删掉的兼容页不会以别名复活。

## 4. 视觉系统（token）

所有颜色/间距/字体/圆角/动效只允许通过 CSS 变量使用（`web/src/style.css :root`）。

### 4.1 颜色（语义，每个颜色有工作）

| 角色 | Light | Dark | 用途 |
|---|---|---|---|
| `--bg` | `#f4f1ea` | `#131312` | 页面底（暖纸/炭黑） |
| `--surface` | `#fbf9f3` | `#1a1a18` | 需要抬起的表面：输入、保存条、抽屉 |
| `--surface-2` | `#ece8de` | `#21211e` | 内嵌块（日志终端、代码、只读值） |
| `--ink` | `#191816` | `#f1ede4` | 主文字 |
| `--ink-2` | `#4b4741` | `#c3bdb1` | 次级文字 |
| `--muted` | `#6d675d` | `#8f887c` | 说明、元信息、placeholder |
| `--line` | `#ded8cb` | `#2b2a26` | 发丝分隔线 |
| `--line-strong` | `#c8c0b0` | `#3b3934` | 输入边框、表格头 |
| `--accent` | `#b4441f` | `#e07a4f` | 交互主色：主按钮、活动导航、焦点 |
| `--accent-ink` | `#fdf7ef` | `#1a0f09` | 主色上的文字 |
| `--live` | `#d99a2b` | `#e7b45a` | "正在听/正在播"的状态点（唯一允许脉动的颜色） |
| `--ok` | `#35694a` | `#6fbf8f` | 正常/已连接 |
| `--warn` | `#8f5c0d` | `#e0a94f` | 需注意、重启生效、tier1 提示 |
| `--fail` | `#a52f24` | `#e58074` | 校验失败、422、删除确认 |
| `--focus` | `--accent` | `--accent` | 2px 外描边 + 2px offset |

规则：主色只用于"可操作/当前所在"；状态色只用于状态；绝不给卡片加彩色边框/渐变。
明暗由 `data-theme="light|dark"` 控制，默认跟随 `prefers-color-scheme`，可手动切换并记忆。

### 4.2 字体与排版

- **Display（衬线，带性格）**：`"Iowan Old Style", "Palatino Linotype", Palatino, "Songti SC",
  "Noto Serif CJK SC", Georgia, serif`。用于品牌字、h1、Now Playing 标题、大数字。
- **UI/正文（安静的无衬线）**：`-apple-system, BlinkMacSystemFont, "PingFang SC",
  "Hiragino Sans GB", "Noto Sans CJK SC", "Segoe UI", system-ui, sans-serif`。14px/1.55。
- **Mono（数据/标签/路径）**：`ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace`。
  所有数字 `font-variant-numeric: tabular-nums`；微标签大写 + `letter-spacing: .08em`。
- 刻度：11 微标签 / 12 元信息 / 13 小字 / 14 正文 / 16 lead / 20 h2 / 26–30 h1 / 34 hero 数字。
- 标题字重 550–600，不用 800；正文不用全粗；链接/按钮用 500。

### 4.3 间距 / 圆角 / 深度

- 间距只取 `4 8 12 16 24 32 48 64 96`（`--s1..--s9`）；段落间距 24，区块间距 40–48。
- 圆角：`--r-xs 4px`（徽章内）、`--r-sm 6px`（按钮/输入）、`--r-md 10px`（表面/抽屉条目）、
  `--r-lg 16px`（移动端抽屉/大表面）；徽章才允许 pill。
- **深度只给覆盖层**：抽屉、保存条、popover 用一层柔和阴影；普通区块用发丝线，不用阴影。
- 卡片不是默认容器：**默认是"区块 + 发丝线"**；只有主行动（试一试、配置保存条）和覆盖层有 surface。

### 4.4 图标

- 内联 SVG 一套（`AppIcon.vue`），1.6 stroke、16/18px、round caps；禁止 emoji/Unicode 当图标。
- 后端 PanelSpec 的 `icon` 字符只作为映射键，不直接渲染。
- 语言/主题/菜单/复制等工具图标同一套。

### 4.5 动效

- 性格：精确、安静。时长 `120ms`（hover）`180ms`（控件）`240ms`（抽屉/页面）`2000ms`（live 脉动）。
- 缓动 `cubic-bezier(.2,.7,.2,1)`。
- 只用 transform/opacity；路由切换 4px 上移淡入；抽屉从右侧滑入；保存条从底部上滑。
- `prefers-reduced-motion: reduce`：全部时长归零、脉动停止、只保留 opacity；意义不丢失。
- 若人先注意到动画而不是内容，就是太强。

## 5. 页面构图

### 5.1 概览（唯一有"仪表"感的页面）

- 12 列网格，桌面 **7/5 不对称**：左侧 = 状态 hero（服务/音频面/设备用一句人话 + 大号 mono 数字）
  + 试一试 composer（主行动，surface 抬起）；右侧 = 遥测 ledger（唤醒、占用、ERLE、运行时长）用行式排列，
  **不是三张同尺寸卡片**。
- 手机：状态 hero → 试一试 composer → 遥测行，单列；数字不缩成不可读。
- "停止出声"是破坏性操作，放遥测区底部，`btn-danger`，不与主行动并排。

### 5.2 配置中心（ConfigCenter）

- 顶部 sticky 工具条：标题 + 生效图例（`保存生效` / `重启生效`）+ 搜索 + "显示高级" + dirty 计数。
- 4 个 tier 作为**目录**，不是 4 张卡片：每个 group 是 `<section>`，序号 `01`，发丝线分隔；
  字段行 = 左栏 label + hint + 徽章（280px），右栏控件（≤420px）；手机上下堆叠、控件满宽。
- tier1（底子）：整页顶部一条 warn 警示条；group 标 `高级` 徽章；字段仍可用，不隐藏。
- 每个字段必须显示：label、hint（改这个能达到什么效果）、效果徽章、单位/范围/选项。
  `writable=false` 只读显示锁图标；`secret` 只显示 has_value 并链到密钥页。
- 保存控件：桌面放 **sticky 顶部工具栏**（编辑第一个字段时就能看到并保存），手机是 **fixed 底条**（仅
  dirty 时出现），显示 `N 处修改 · X 立即生效 · Y 重启生效`；422 时按字段红框 + 行内错误 +
  自动滚到第一个错误，工具栏同时给校验汇总；`restart_required` 时给命令与「仅重启音频面」。
- 无 catalog 时：说明 + 后端原话 + 重试，不空白、不假装。

### 5.3 其余页面

- 统一的 `page-head`：kicker（组名）> h1（衬线）> lede（一句话）；右侧可放元信息/主按钮。
- 日志 = 终端表面（两种主题都深色），工具条右对齐行数；表格用等宽、行 hover、横向滚动容器。
- 安装向导 = 编号步骤（01–05），每步一个 section，进度在顶部一条 ledger；手机单列。
- 维护类页面（设备/MCP/话术/密钥/体检）沿用同一套表单行、徽章、按钮，不做独立视觉。

## 6. 响应式策略（按断点重新构图，不是压缩）

| 断点 | 策略 |
|---|---|
| ≥1200 | 244 侧轨 + 内容 1160；概览 7/5 网格；字段行左右两栏 |
| 720–1199 | 侧轨 212；概览 1 列但遥测 2 列；配置字段行左栏 200 |
| <720 | 顶部工具条 + 抽屉导航；内容 16px 边距；所有 grid 单列；输入/按钮满宽；表格进 `.scroll-x`；保存条 fixed bottom；`h1` 26px |

硬约束：390px 下 `document.documentElement.scrollWidth <= innerWidth`（逐页断言）；
长英文 key / 日志 / 路径必须 `overflow-wrap:anywhere` 或滚动容器，不能撑破页面。

## 7. 国际化（i18n）

- `web/src/i18n.ts`：扁平字典（`type MsgKey = keyof typeof zh`），`zh` + `en` 必须键集完全一致（类型检查保证）。
- 语言：侧栏底部 `中文 / EN` 分段控件；`localStorage['rui-language']`；`<html lang="zh-Hans|en">`；
  `document.title` 随页面与语言变化。
- 面板标题：`title_en` 优先，缺失回退中文；catalog 的 `label/hint/unit` 是 `{zh,en}`，按语言取。
- 后端 PanelSpec 里的自然语言（block title/label/map/empty/按钮文案等）仍是中文字符串：
  前端 `localizeBackend()` 用**字典内**的精确映射与少量模式兜底翻译；未命中保留原文并记入待对齐
  （后续由后端提供 `*_en` 或 i18n key）。英文模式下不允许前端自造中文文案。
- 文案基线：短、具体、说人话；参数 hint 回答"改这个能达到什么效果"。

## 8. 状态设计

每个交互都必须有：default / hover / active / focus-visible / disabled / loading / empty / error。
- 加载：骨架行（不发脉冲动画，尊重 reduced-motion）；不做全屏 spinner。
- 空：一句话 + 下一步动作（例："还没有音箱 → 添加音箱"）。
- 错误：后端原话 + 重试；422 定位到字段。
- 成功：短暂内联提示（保存条变化），不用 toast 墙。

## 9. 反 AI 味审计（交付前必跑）

- 禁止：默认圆角卡片网格、任意渐变、玻璃拟态、彩色 glow、pill 泛滥、居中大标题 + 弱正文、
  假图表/假指标、"Trusted by"、装饰性 blob。
- 三个最泛化的地方（若存在）必须替换：① 同尺寸三卡 stats → 行式 ledger；
  ② 每块一个 card → 区块 + 发丝线；③ 蓝色 SaaS 主色 → 铁锈色仪器主色。
- 作者测试：遮住 logo，仍能认出这是"一台语音设备的控制面板"吗？十个人工智能会生成一样的东西吗？

## 10. 验收

- `npm run typecheck` + `npm run build` 绿。
- headless Chrome：1440 桌面 + 390 手机，逐页无 console error / 无横向溢出；
  截图 `/tmp/console-ui/desktop.png`、`/tmp/console-ui/mobile.png`（另有配置中心/英文/暗色附加图）。
- deep-design-qa：render → critic（只读）→ fix → re-render → compare，报告评分与已修项。
- 功能：配置中心 dirty/save/reset、422 红框、restart 提示、音频面重启、语言切换、明暗切换、深链刷新。

## 11. 实现映射（2026-10-23；Phase 4 更新 2026-10-25）

| 目标 | 落点 |
|---|---|
| 语言 | `web/src/i18n.ts`：`MsgKey = keyof typeof zh`，en 字典类型上必须键齐全；`localizeBackend()` 双向桥接后端 PanelSpec 文案/枚举；侧栏 `中文 / EN`，localStorage + `<html lang>` + `document.title` |
| 明暗 | `web/src/theme.ts` + `index.html` 首帧内联脚本；`system / light / dark` 三段开关并记忆，`data-theme` 驱动 token |
| 设计系统 | `web/src/style.css`：颜色/间距/字体/圆角/阴影/动效 token、focus-visible、reduced-motion、移动断点；对比度已按 WCAG AA 校准（`--muted #6d675d`、`--warn #8f5c0d`） |
| 外壳 | `App.vue` + `components/SideNav.vue`：两段导航（run/configure）由 `PanelSpec.nav` 驱动，`nav.ts` 只提供 section 标题与工具路由；手机抽屉（关闭时 `inert` + `aria-hidden`）、signal ledger、语言/主题/令牌 |
| 概览构图 | `views/PanelHost.vue` 的 `.blocks-overview`：7/5 不对称网格，试一试 + 操作在左、遥测 ledger 在右；右栏 4 格遥测按 2×2 排 |
| 配置中心 | `components/ConfigCenter.vue`：catalog 驱动；config block 按 `tier`/`groups` 渲染（devices → tier2，system → tier1 + `warning`）；tier1 警示；组索引条；字段 label/hint/效果徽章/单位/范围/选项；只读字段折叠原始值；密钥从 `catalog.secrets` 渲染写入口（`POST /api/v1/secrets`）；dirty/save/reset；422 字段红框 + 自动滚动；`restart_required` 命令 + 音频面单独重启；`warnings` 展示 |
| 导航 IA | `PanelSpec.nav {section,order,hidden,icon}` 是唯一事实来源；`web/src/nav.ts` 只放两个 section 标题 + `/channels`、`/diagnostics`、`/setup` 三条工具路由；`/secrets` 不进入侧栏 |
| 其余页面 | blocks 与合并页组件统一重绘；`SetupView`/`DeviceManager`/`McpManager`/`PhrasebookEditor`/`SecretsView`/`HealthView`/`ToolsView` 全部走字典与 token；7 个兼容 panel 已删除，旧路径不再映射 |
| 路由 | 只保留 `/`→overview、`/panel/:id`、`/setup`、`/channels`、`/secrets`、`/diagnostics` 和 catch-all；7 个旧 panel id 与 8 个旧顶层别名不再映射，retired `/panel/<id>` 显示 not-found |

## 12. QA（deep-design-principles + deep-design-qa）

- 渲染：headless Chrome（CDP），1440×1100 桌面 + 390×844 手机，真 API（`dev :8091`）逐页深链；
  证据 `/tmp/console-ui/shots/`，交付图 `/tmp/console-ui/desktop.png`、`/tmp/console-ui/mobile.png`。
- 结果：32 个场景 0 console error / 0 exception / 0 4xx；所有页面 `scrollWidth == innerWidth`（包括体检页）。
- 交互断言：dirty 条 sticky、422 行内错误 + 本地化汇总 `校验失败：1 个字段需要修正。` + 自动定位；
  重启条显示字段 chip、`docker compose restart`、复制、「仅重启音频面」、服务端 warning；
  EN 切换后 `<html lang=en>`、标题与导航即时更新；暗色记忆；手机抽屉关闭时 inert。
- 已知缺口（待后端/内容对齐）：
  1) `doctor` 的 `value/hint` 仍混有英文技术句（如 `assumed 0.35 core/device`）——前端不猜译；
  2) select 的 `options` 是裸枚举值（`off/short/full`），catalog 未给 `label{zh,en}`；
  3) 后端 `PUT` 的 `warnings` 是中文句子，已按已知 3 条桥接，未来新增需同步字典或返回 warning code；
  4) `tools.mcp_servers`/`devices` 只读列表目前以折叠 JSON 呈现，若需要结构化只读表格需 block/字段扩展；
  5) 密钥与面板的归属按 name 模式启发（LLM/ASR/TTS/HASS/DEVICE/AUTH），更稳的做法是 catalog 给 `secret.groups`。

### 12.1 Critic 评分与修复（render → critic → fix → compare）

critic（只读 subagent，读 12 张截图 + 源码）首轮评分：
Hierarchy 6 · Composition 5 · Typography 7 · Spacing 7 · Alignment 6 · Consistency 6 ·
Distinctiveness 6 · Usability 5 · Responsiveness 7 · Motion 6 · Color 7 · Polish 6。

**Top-5 已修（并逐项用重渲染 + DOM 断言验证）**：
1. 保存不可达：桌面保存控件移入 **sticky 顶部工具栏**（始终可见），手机保留 `position:fixed` 底条（仅 dirty）；422 时工具栏直接显示 `校验失败：N 个字段需要修正。`。
2. 概览两块 2×2 网格：右栏改为**行式 ledger**（每项一行 label/value，发丝线），保留 390px 下 2×2；左栏保留主行动 + 操作。
3. 禁用主按钮 `opacity` 造成的"粉色可点"错觉：改为 `--surface-2` + `--muted` 中性禁用态，只有可用时才是铁锈色。
4. 输入框 token 泄漏：补 `input:not([type])` 选择器（浅色 `#fbf9f3` / 深色 `#1a1a18`，实测不再纯白/纯灰），focus 与按钮统一为 2px outline。
5. 抽屉藏入口：手机端整个侧栏滚动（密钥/体检/向导在页脚前可见），关闭时 `inert`；手机 signal ledger 去掉与顶栏重复的状态词。

**额外修复**：删除每页装饰性 panel slug；field-row 18→16、hint margin 5→4、label 13.5→14、hint 12→12.5；概览加"数字来自真实调用"脚注；422 pydantic 英文消息本地化（`must be < = 300` → `不能大于 300`）；PUT `warnings` 三条已知句子英文化。
对比后自评（critic 无法二次接收消息，故由同一 QA 脚本 + DOM 复测）：8 / 7 / 8 / 8 / 8 / 8 / 7 / 8 / 8 / 6 / 8 / 8（Motion 保留原实现）。
残余：概览只有 4 个真实 block，1440 下页面上半屏结束于 ~600px（有意留白 + 脚注，不造假图表）；reduced-motion 采用全局 0.001ms 兜底而非仅淡出。
