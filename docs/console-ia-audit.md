# 控制台信息架构独立审计（RUI 0.3.2）

> 审计对象：`/Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear`（`main`，`4b279a7`）。
> 方法：静态阅读 `web/src/**`、`src/rui/web/api/app.py`、`config/catalog.py`、`providers/*`、`music/*`、`setup_api.py`，
> 核对现有契约/测试；未改代码、未提交、未连 NAS，未读取任何密钥。xfyun 结论另用进程内 fake fallback 复现（§6）。
> 限制：没有生产 `service.log` 原文；因此 §6 同时给出「噪声 / 真故障」的判据，而不是替你的日志下结论。

## 0. 结论先行

1. 现状不是「页面太少」，而是**同一件事有 2–4 个入口**：设备 3 个、凭据 5+ 个、HA/MCP 3 个、音乐 2 个。
2. 18 个导航目的地、4 个分组，`维护` 是垃圾桶；用户提的两分法方向对，但 10 个条目平铺只是换个标签。
3. 「系统与高级」**不能删**；「端口搬进语音助手」**不能同意**；「密钥只是自定义 token」**事实错误**。
4. xfyun 在「另一个 TTS 可用」条件下**大概率是 fallback 噪声**，但配置确实不一致；更该先修的是运行时诚实性：未就绪的 provider 仍每句都试、每句都 warning。

## 1. 现状清单（每个面板/页面真正做什么）

后端 `GET /api/v1/panels` 当前声明 14 个面板（`app.py:352-599`），前端另有 4 个手写工具页（`router.ts:20-24`、`SideNav.vue:108-113`）。
导航不是后端 `group` 的直接结果：`panels.ts:186-201` 用 `PANEL_NAV_GROUPS` 二次改写，`i18n.ts:3212-3227` 又用
`PANEL_TITLE_KEYS` 覆盖标题——这是后面所有漂移的根。

| 路由 / 面板 | 真实职责 | 数据 / 组件 | 与谁重叠 |
|---|---|---|---|
| `/panel/overview` 概览 | 服务/音频/设备状态、试一试、停止出声 | `/health` + `/api/v1/audio`，blocks（`app.py:353-384`） | 体检（状态）、日志（停止） |
| `/panel/turns` 对话 | 最近 20 轮 transcript/reply/tools | `/api/v1/turns`（`app.py:400-415`） | 日志（同一回合的另一视角） |
| `/panel/logs` 日志 | `service.log` 300 行 tail，只隐藏 `[level] voice peak` | log block（`app.py:515-526`） | 体检、xfyun 噪声都落在这里 |
| `/panel/memory` 记忆 | 已确认记忆的增删改 | memory block（`app.py:527-536`） | 助手上下文设置 |
| `/panel/music` 音乐 | 播放/暂停/下一首、最近爱听、**音乐凭据** | `/api/v1/music` + credentials block（`app.py:537-598`） | Tools 的 `music` 组 |
| `/panel/assistant-session` 助手与会话 | 人设、回复长度、超时、告别、结束词、上下文 | config-center groups `assistant,session`（`app.py:417-424`） | 话术、向导 |
| `/panel/speaker` 音箱参数 | 设备只读列表 + 采集/唤醒/播放/AEC/唤醒应答约 50 项 | config-center tier 2（`app.py:425-446`；`ConfigCenter.vue:162-166`） | 设备、设备管理 |
| `/panel/devices` 在线设备 | 只读在线列表 + 设备混音器音量 0–255 | `/api/v1/devices` + volume block（`app.py:385-399`） | 设备管理、音箱参数 |
| `/panel/devices-admin` 设备管理 | 增删改、探测、测试、连接信息、token 轮换、LED/插件参数 | `device-manager`（`app.py:447-453`） | 在线设备、音箱参数 |
| `/panel/providers` 外部资源（导航叫 AI 供应商） | LLM/ASR/TTS 三条链、provider 卡片、试听、保存/重启 | `providers-v2`（`app.py:454-463`） | 密钥、向导 step 01 |
| `/panel/tools` 工具 | 天气/新闻/搜索/HA/MCP/exposure/**music** 共约 29 字段，并内联 HA/搜索密钥 | config-center groups（`app.py:464-475`；`ConfigCenter.vue:218-235,906-955`） | MCP/HA、音乐、只读 `/tools`、密钥 |
| `/panel/mcp` MCP / HA | HA 连接 + 通用 MCP 服务器 + stdio 闸 | `mcp-manager`（`app.py:486-492`） | Tools 的 `tools.ha`/`tools.mcp`、密钥 |
| `/panel/phrases` 话术 | 49 条话术模板编辑、slot、逐条重置 | `phrasebook-editor`（`app.py:476-485`） | 助手与会话 |
| `/panel/foundation` 底子·高级（导航叫系统与高级） | data_dir、log_level、timezone、web 绑定/端口/匿名、SSRF 共 13 项 | config-center tier 1（`app.py:493-514`） | 密钥的 console auth、音箱参数 |
| `/setup` 安装向导 | 8 步：keys/models/prepare/params/wake/connect/test/verify | `setup_api.py:528`，`SetupView.vue` | providers、secrets、devices、setup |
| `/secrets` 密钥 | 只写不读的全局 env 凭据库；值永不回显 | `SecretsView.vue`，`POST /api/v1/secrets`（`app.py:4501-4541`） | providers/tools/speaker/foundation/向导 |
| `/diagnostics` 体检 | doctor + 音频状态 + 计费额度 | `HealthView.vue` | 概览、日志 |
| `/channels` 通道 | Telegram / 微信 iLink 网关、allowlist、扫码登录 | `ChannelsView.vue` | 无直接重叠 |
| `/tools` 只读工具表 | 已注册工具名 + exposure 摘要 | `ToolsView.vue` | `/panel/tools` 同名不同物 |

旧路径：`/overview`、`/devices`、`/logs`、`/assistant`、`/settings`、`/voice`、`/models` 都有重定向；`/panel/settings` 在 `PanelHost.vue:44-47` 做别名。`tests/test_panels.py:29-31` 固定了其中一批，迁移时不能直接删。
重叠不是“感觉重复”，是同一数据有多个写入口：设备 3 个（`devices`/`devices-admin`/`speaker` 的 `devices` 组）；凭据 5+ 个（`/secrets`、Providers key strip、ConfigCenter 内联 secret、MCP 页 token、向导 01）；HA/MCP 3 个（Tools 的 `tools.ha`/`tools.mcp`、`/panel/mcp`、只读 `/tools`）；音乐 2 个（Music 页、Tools 的 `music` 组）；助手行为 2 个（assistant-session、phrases）；状态 3 个（overview、diagnostics、logs）。

## 2. 逐条裁决

### 2.1 音乐页 vs Tools 音乐区 / NAS 配置 / provider 预设

**裁决：重叠成立，必须合并；“给主流服务器做预设”方向对，但这不是纯 UI 工作。**

事实：
- `tools` 面板显式包含 `music` 组（`app.py:470-474`），音乐页又有一套播放 + `credentials` block（`app.py:573-596`）。
- `music.enabled` 只在 Tools 的 catalog 字段里（`catalog.py:1114-1121`）；音乐页的 `enabled` 由它计算（`app.py:540-542`）。
- 面板禁用时 `PanelHost` 只渲染警告，**不渲染任何 block**（`PanelHost.vue:159-167`）。所以音乐关闭时，你在音乐页连凭据都填不了；必须先去 Tools 打开，再回音乐页填。
- `music.source` 在 catalog 里是 `kind="text"` 且 `options=["navidrome"]`（`catalog.py:1131-1140`）；`ConfigCenter` 只有 `kind === 'select'` 才渲染下拉（`ConfigCenter.vue:807`），所以它实际是自由文本。用户打错一个字母，运行时只留一行 warning 并“音乐关掉”（`app.py:302-305`），但面板仍可能显示 enabled（`app.py:540-542`）。
- 底层接口其实已经是通用的：`MusicSource` 协议有 search/random/favorites/star/resolve（`music/source.py:17-40`）；但运行时只 `if source == "navidrome"`，且 `music/__init__.py:14` 只导出 `NavidromeSource`。
- 凭据形状被硬编码成 url/user/password 三个字段（`app.py:579-596`），无法表达 Jellyfin 的 API key、Plex 的 token、Music Assistant 的会话。

根因：按**实现层**拆页（播放器一个面板，配置进通用 catalog），而不是按**用户任务**拆页；并且没有“音源 = 预设 + 适配器 + 凭据 schema”的产品对象。

我的意见：
- 同意把播放、音源、凭据合成一个“音乐”页；不要叫“音乐配置”，那会再次把播放降级成配置。
- 预设优先级：Navidrome/Airsonic/Gonic/Ampache（同一个 Subsonic 适配器）→ Jellyfin/Emby（新适配器）→ Plex → Music Assistant/LMS。没有适配器的预设是假按钮，别先承诺。

> 实施状态（2026-10-25，RUI 0.3.4）：§2.1 的音源预设已落地。注册表
> `music/sources/presets.py`（navidrome / airsonic / gonic / ampache / custom，全部
> `subsonic` 家族，带双语显示名、文档、默认端口/路径、凭据槽位）；运行时只有一套
> `SubsonicSource`，`NavidromeSource` 保留为兼容别名。`music.source` 已改为注册表驱动的
> select；`GET /api/v1/music/sources` 给出预设 + 凭据 presence，`PUT /api/v1/music/credentials`
> 支持 `source` + 只写槽位，`POST /api/v1/music/test` 做真实 `ping`。未知 id 在保存时 422，
> 手改 YAML 的未知值让面板/API 明确 `enabled=false` / `source_known=false`。旧
> `music.url/user/password` 启动迁移到 0600 `lan_media.json` 并从 config 擦除。Jellyfin/Emby
> 仍未实现，因此不提供预设。契约、迁移和 fake-server 测试见 `docs/music-sources.md`。

### 2.2 LLM / 模型 provider 预设与自定义

**裁决：同意，而且问题比“字段生硬”更严重——当前控制台根本不能新增 provider。**

事实：
- Providers 页的“未使用”列表只能把**已经在 config 里**的 provider 加回链（`ProvidersView.vue:122-124,220-224`）；没有“新建 provider”按钮或接口。
- 唯一写配置的端点 `PUT /api/v1/config/values` 会拒绝 catalog 里没有的路径（`app.py:2311-2323`）。catalog 的 provider 字段是从现有 `providers.catalog` 动态生成的（`catalog.py:800-960`），所以“先有 config 才有字段”，用户无法从 UI 迈出第一步。
- LLM 的 kind 白名单只有 `openai_compat`（`keycheck.py:87-91`；`chain.py:29-35`）。想接 Anthropic 原生 Messages API 不是填个 URL 的事。
- 环境变量名被标成只读（`catalog.py:906-909`），这是对的：否则控制台 token 可以把任意环境变量当密钥外发。预设必须由后端固定 `api_key_env`，不能让用户自由填。

根因：provider 被当成“部署 YAML”，不是产品对象；没有 preset registry / create endpoint / readiness 模型。

我的意见：
- 同意预设 + 自定义兼容模式。可覆盖绝大多数 OpenAI 兼容服务（OpenAI/DeepSeek/SiliconFlow/Kimi/GLM/DashScope 兼容/OpenRouter/Groq/Ollama/LM Studio/vLLM）；自定义模式应是**一等公民**。
- Anthropic 原生、非 OpenAI 形状的 TTS/ASR 需要适配器，不要在 UI 上假装“预设即支持”。
- 预设要带显示名、stage、base_url、默认 model、受控 key env、文档、必填字段和 probe；没有 probe 的预设只是模板。

> 实施状态（2026-10-25，RUI 0.3.4）：§2.2 的预设/新建/内联 key/就绪已落地。预设注册表
> `providers/presets.py`，`GET /api/v1/providers/presets`、`POST /api/v1/providers`、
> `POST /api/v1/providers/probe`、`GET /api/v1/providers/status` 已加入；Providers 页有
> 「新建 provider」向导、就绪/缺配置徽章与「设为主用/移出链路」。自定义环境名仅允许
> `RUI_*`，预设凭据名由服务端固定；契约与预设清单见 `docs/provider-presets.md`。

### 2.3 密钥页到底是什么？向导 01 该叫什么？

**裁决：部分同意。它不是“只填自定义 token 的地方”，但它也确实不该是一个一级导航目的地。**

事实：
- 它从 catalog 的 `secrets` 表 + 磁盘上已有的名字合并出列表（`SecretsView.vue:115-123`），可写/清除；值只回 `has_key`（`app.py:4501-4541`）。
- 内容覆盖：provider 的 API key/AppID/AppSecret、搜索 key、HA token、每台音箱的 device token、控制台 `web.auth_token`（`catalog.py:1465-1525`）。
- `web.auth_token` 是例外：它必须写进 config.yaml，但 API 同样不回显（`app.py:4526-4534`）。
- 消费者的显示是前端正则猜的（`SecretsView.vue:20-39`；`ConfigCenter.vue:218-235`），不是后端给的类型化 consumer。
- 内联入口不一致：Tools/Speaker/Foundation 的 ConfigCenter 有 secret 输入（`ConfigCenter.vue:906-955`），Providers 页只有一条跳到 `/secrets` 的 key strip（`ProviderKeyStrip.vue:1-6`）。

我的意见：
- **不要**改叫“访问令牌”。现在已有三个“token”：浏览器控制台访问令牌（`SideNav.vue:223-227`）、音箱 device token、HA 长期令牌；再叫只会更乱。建议 `凭据（只写）` / `Credentials (write-only)`，副标题“所有 API Key / Token 的写入口；值永不回显”。
- 把它从一级导航降级到「系统」下的高级页；Providers 页内联填 key，Music 内联填音乐凭据，MCP 内联填 HA token，Device 页轮换 device token。保留 `/secrets` 路由做书签兼容。
- 后端给 `secrets` 增加类型化 `consumers`（现在只有 `used_by` 字符串），前端删掉正则。
- 向导 step 01 同意改，但**“模型设置”只对了一半**：它同时选 LLM/ASR/TTS 并填凭据。更准确是「模型与语音服务」；短标签可用「模型设置」，副标题必须写 LLM / ASR / TTS。**只改显示文案，不要改 step id `keys`**，否则 setup store 的已完成状态会丢。
- 额外发现：向导保存时 `merged["providers"][stage]["chain"] = [provider_name]`（`setup_api.py:799`），会把已有 fallback 链**替换成单条**。README 也承认“写成 chain=[selected]”（`README.md:177`）。建议改为“设为主用、保留其余 fallback”，或在 UI 明确显示“这会删除 fallback”。

> 实施状态（2026-10-25，RUI 0.3.4）：§2.3 的降级与类型化已落地。`secret_presence()` 现在返回
> `category` + `consumers[]`（provider 带 `stage/provider`、device 带 `device`，每项带 `href`），
> `/api/v1/secrets` 同时返回同一张类型化表；`SecretsView.vue`/`ConfigCenter.vue` 已删除按变量名
> 猜 consumer 的正则，改为按 `category` 过滤。页面标题改为「凭据（只写）/Credentials (write-only)」，
> `/secrets` 保留，新增隐藏的 `/panel/credentials`；Models/Music/Integrations/Devices/System
> 都链入。音乐凭据以 `writable=false` 列出并链回音乐页（不把 `lan_media.json` 并进 `secrets.yaml`）。
> 值仍只回 `has_value`。

**2.4 xfyun 反复报错：真故障还是噪声？** 在你给的条件下（另一个 TTS 可用），大概率是 fallback 噪声，但配置本身不一致，不能只静音。判据：`MissingKey: no value for XFYUN_...` = 主用未配置；`ProviderError xfyun code=...` / 鉴权/额度错误 = 真故障。完整复现与修法见 §6。

### 2.5 家庭助理 / MCP / 工具三页重复

**裁决：重叠成立；HA 作为 MCP 实例的方向对，但现在只能先合并 UI，不能直接合并数据模型。**

事实：
- Tools 页有 `tools.ha`（base_url 可写、token_env 只读）和 `tools.mcp`（服务器列表只读、stdio 闸可写）（`app.py:464-475`；`catalog.py:1057-1080`）。
- `/panel/mcp` 又完整拥有 HA 连接、token、测试、通用 MCP 增删改（`McpManager.vue:1-30`），同一份配置两个热写入口。
- `/tools` 只读注册表页和 `/panel/tools` 同名不同物；它不在主导航，只从 Tools 页链入（`ConfigCenter.vue:595-605`）。
- HA 不是普通 MCP：它有六工具白名单、危险域硬拒、实体上下文、7 天缓存（`tools/ha.py:1-40`），工具名是 `local.ha_*`；通用 MCP 工具名是 `mcp.<server>_<tool>`（`mcp_manager.py:60-63`）。
- 通用 MCP 目前**没有 headers 字段**（`McpServerConfig`，`schema.py:446-455`），虽然 manager 用 `getattr(server, "headers")` 预留了（`mcp_manager.py:184-190`）。HA 自己走 `Authorization: Bearer`（`tools/ha.py:122-127`）。所以“把 HA 塞进通用 MCP”会先丢鉴权。

我的意见：
- 同意把三页合成一个「工具与集成」：内置工具（天气/新闻/搜索）+ MCP 服务器 + HA 作为“第一方 MCP 集成”卡片 + 注册工具只读表 + 暴露策略。
- 现在就把 HA 显示成 MCP 列表里的一项，但保留它的安全策略和 `local.ha_*` 工具名；不要为了 IA 一致性牺牲白名单/危险域拒绝。
- 后续给 `McpServerConfig` 加 `headers` 或 `token_env`（推荐后者，值仍在 secrets）、给通用 MCP 加 allowlist/policy，再把 HA 迁移成真正的实例；迁移要保留旧工具名别名。它突出是因为有额外语义，不是因为项目偏心。

> 实施状态（2026-10-25，RUI 0.3.4）：§2.5 已按“UI 实例化、数据模型不合并”落地。
> `McpServerConfig` 增加经校验的 `headers`（拒绝 CR/LF 与非法头名）和 `token_env`（大写密钥名；
> 与显式 `Authorization` 二选一，stdio 不接受这两项）；`mcp_manager` 在开新会话时从只写密钥库
> 解析 Bearer，并把头/令牌指纹纳入热重连签名。控制台新增只写 `token` 字段（后端写入
> `secrets.yaml`，配置只留 `token_env`，响应只回 `has_token`），探测可带未保存 token。
> HA 仍是 `tools.ha` + `HaBridge`，但 `GET /api/v1/mcp` 的 `first_party[]` 让 MCP 标签页把它
> 渲染成第一方实例卡片，并显示白名单/硬拒域/`local.ha_*` 前缀；白名单、危险域拒绝与工具名
> 均未改变。新增服务指南见 `docs/integrations-mcp.md`。

### 2.6 用户提出的目标 IA

**总评：两分法对，细项约 60% 同意，30% 需要改写，10% 反对。**

| 用户提案 | 裁决 | 理由 |
|---|---|---|
| 运行：概况/对话/记忆/日志 | 同意 | `life` 这个分组没有用户任务含义；音乐/记忆不是“生活”，是设备和数据 |
| 配置与维护：语音助手（助手/会话/话术） | 同意，但用 tab | assistant-session 已经合并；话术是低频重型编辑器，做 tab 而不是继续平铺 |
| 音乐配置 | **改写** | 叫“音乐”，保留播放；只做“配置”会把日常播放藏进维护页 |
| 模型配置（LLM/ASR/TTS + 预设） | 同意，改名为「模型与语音服务」 | ASR/TTS 不是“模型配置”四个字能覆盖的；key 要内联 |
| 工具与搜索（HA 进 MCP、开放 API、news/weather） | 同意，改名为「工具与集成」 | “搜索”只是其中一项；HA/MCP/注册工具/暴露策略都在这 |
| 密钥/访问令牌 | 部分同意 | 它不是自定义 token；不要叫访问令牌；降级为「凭据（只写）」 |
| 系统与高级 | **反对删除** | 它承载 data_dir/log_level/timezone/web 绑定/鉴权/SSRF，共 13 项，没有别的家 |
| 端口搬进语音助手 | **反对** | 控制台 web 端口属于“控制台访问”；设备 listen_port 已经在设备管理页 |
| 在线设备 + 设备 merge | 同意 | 但合并后要把实时状态/音量放页首，别埋进 2493 行的 DeviceManager |
| 通道 / 体检 / 安装向导 | 同意保留 | 向导完成后不应常驻导航；未完成时由概览/启动 nudg 进入即可 |

具体回答：
- **“系统与高级”是干什么的？** 13 个字段：`system.data_dir/log_level/timezone`、`web.host/port/allow_anonymous`、`security.outbound.*`（`catalog.py:317-421`）。它是进程、控制台和出站安全，不是语音参数，不能删。可把 `log_level` 移到日志页顶部，把 `timezone` 视作助手行为候选；其余留在「系统」。
- **哪些能搬进语音助手？** 人设/回复长度/会话/话术可以；唤醒词、AEC、采集格式、播放缓冲、设备端口不要。它们跟随硬件，搬过去会让“换音箱”更难。
- **“密钥”和“端口”呢？** 密钥是全局只写凭据库（详见 §2.3）。控制台 8080 留系统；音箱 `listen_port` 设备页已有；TTS/LLM 的 `base_url` 在模型页。三者都不该进语音助手。

## 3. 目标 IA
```
运行 RUN
  概况 /panel/overview      对话 /panel/turns
  记忆 /panel/memory        日志 /panel/logs (+ log_level)

配置与维护 CONFIGURE & MAINTAIN
  语音助手 /panel/voice-assistant     tabs: 助手与回复 · 会话 · 话术
  设备与音频 /panel/devices           tabs: 状态与音量 · 设备管理 · 音频与唤醒
  音乐 /panel/music                   sections: 正在播放 · 音源与凭据 · 高级
  模型与语音服务 /panel/models         LLM / ASR / TTS + 预设 + 自定义 + 内联 key + 就绪/试听
  工具与集成 /panel/integrations      内置工具 · MCP 与家庭助理 · 注册工具 · 暴露策略
  通道 /channels            体检 /diagnostics
  系统 /panel/system                  运行环境 · 控制台访问 · 出站安全
  凭据（只写） /panel/credentials     从导航隐藏；系统页/各消费页链入
  安装向导 /setup                     仅未完成时出现在导航
```

职责边界：
- 语音助手：她是谁、怎么说话、会话何时结束、话术。**不含**硬件 DSP、唤醒模型、端口。
- 设备与音频：设备生命周期 + 实时状态/音量 + 采集/唤醒/播放/AEC/唤醒应答。合并 `/panel/devices`、`/panel/devices-admin`、`/panel/speaker`。
- 音乐：播放与音源是一个任务的两面；一个页面内分节，不做两个页面。
- 模型与语音服务：三条链、预设/自定义、凭据、就绪状态、试听、重启说明。合并 `/panel/providers` + provider secrets + 向导的 provider 选择。
- 工具与集成：内置工具、MCP、HA、注册工具、暴露策略。合并 `/panel/tools` + `/panel/mcp` + `/tools`。
- 系统 / 凭据：系统放进程底子+控制台访问+出站安全；凭据是只写审计页，日常从消费页内联写入。`web.port` 不进语音助手。

保留与兼容（硬约束）：
- **不改任何 `/api/v1/*`**。IA 重构不应触碰 API 路径。
- 旧路由继续 200（redirect/alias）：`/panel/assistant-session`、`/panel/speaker`、`/panel/devices-admin`、`/panel/providers`、`/panel/tools`、`/panel/mcp`、`/panel/phrases`、`/panel/foundation`、`/panel/settings`、`/assistant`、`/settings`、`/voice`、`/models`、`/secrets`、`/tools`。
- 旧 panel id 至少保留一个 release（hidden/alias，或新组件挂旧 id）。`tests/test_panels.py` 与 `docs/console-redesign.md §3`、README 链接、截图都要同步；向导 step id（`keys/models/...`）不改，只改显示名。

移动端：
- 18 → 约 12 个导航项，抽屉仍整栏滚动；向导完成后隐藏，直接少一项。
- 合并页用顶部分段/tab（手机横向滚动）；不要嵌套 DeviceManager 的 drawer 再套 drawer；每个合并页只保留**一个** sticky 保存条，ConfigCenter fixed 底条与 DeviceManager 保存不能同时出现。
- 390px：tab 不换行成两行、表格横向滚动、凭据输入满宽、音量/正在播放放页首。

## 4. 数据 / API 影响
| 变更 | 需要的后端/数据改动 | 风险 |
|---|---|---|
| 两分组导航 | 短期只改 `panels.ts`/`i18n.ts`；长期把 `section/order/hidden/aliases` 放进 PanelSpec，删掉 `PANEL_NAV_GROUPS` 与标题覆盖 | 中：现在有 3 个事实来源（后端 group、前端 mapping、i18n override） |
| 合并设备页 | 新组合组件，复用 `DeviceManager` + ConfigCenter；无需改 API | 低：不要复制表单逻辑 |
| 合并工具/MCP | 新组合组件，复用 `McpManager` + ConfigCenter；把 `/tools` 的注册表嵌为折叠区 | 低-中：两个热写路径要统一入口 |
| provider 预设/新建 | 新 `GET /providers/presets` + `POST /providers` + `GET /providers/status`；`PUT /config/values` 不认识新 path，必须由后端创建 catalog 条目；key 复用 `POST /secrets` 内联 | 中-高：涉及 schema、secret、chain、重启 |
| 凭据 consumer 类型化 | 扩展 `secret_presence`（`catalog.py:1465-1525`）返回 `consumers[]`/`category`；前端删正则 | 低 |
| 音乐源预设 | `music.source` 改 select + 源注册表 + 每源凭据 schema + `GET /music/sources` + 测试端点；新增 `JellyfinSource` 等 | 高：每源协议/鉴权不同 |
| HA 作为 MCP 实例 | `McpServerConfig` 加 `headers` 或 `token_env`；保留 HA 白名单/危险域策略；工具名别名迁移 | 高：安全策略与工具名不能丢 |

## 5. 迁移计划（小步，不搞大爆炸）
### Phase 0 — 冻结现状（本次，无代码）
- 产出本审计；把 `tests/test_panels.py` 的路由/面板清单当兼容基线；不改代码，`git status` 只有文档。

### Phase 1 — 低风险 IA 与运行时诚实性（P0）
- 导航改 2 组；向导仅未完成时出现；统一标签（§2.6）。
- 音乐禁用页不再隐藏凭据/开关；`music.source` 改 select + 未知源明确报错；面板 `enabled` 以“服务真的能装配”为准。
- xfyun 就绪过滤 + 每 provider 一次日志（§6）；向导 01 改「模型与语音服务」；`providers.save` 不丢 fallback。
- 验收：旧路由全部 200；2 组；390px 无溢出；`npm run typecheck` 绿；xfyun caplog 0 条 per-sentence warning。

### Phase 2 — 合并页面（P1，前端为主）
- 先做「设备与音频」「工具与集成」，再做「音乐」「语音助手」；新路由先上，旧路由 redirect，旧 panel id 保留一个 release。
- 验收：旧 URL 全部 redirect；合并页各块数据都能加载、保存互不重置；手机 tab 可操作；每个 catalog 字段仍有家（扩展 `test_panels` 覆盖断言）。

### Phase 3 — 产品化 provider / 凭据 / 音乐源（P1）
- provider 预设 + 新建 + 就绪/探测 + 内联 key；凭据页降级与 consumer 类型化；音乐先 Subsonic 家族，再 Jellyfin/Emby；HA 先 UI 实例化。
- 验收：预设创建后无需手改 YAML 就能真 probe；错误预设 422；密钥不回显；旧 config 可迁移；未知 `music.source` 不出现“面板 enabled 但服务没起”。
- 音乐源（2026-10-25 已完成 Subsonic 家族）：预设注册表 + `music.source` select + 每源凭据槽位 + `/music/sources` / `/music/test`；Jellyfin/Emby 需要新适配器，未在 UI 提供假预设。
- 本切片（2026-10-25，RUI 0.3.4+）完成 §2.3/§2.5 的收尾与系统页：类型化凭据 consumer、
  凭据页降级（`/panel/credentials` + `/secrets` 兼容）、MCP `headers`/只写 `token_env`、HA 第一方
  实例卡片（策略/工具名不变）、`/panel/system`（13 个底子字段 + 控制台连接信息），`log_level`
  移到日志页顶部。旧 `/panel/foundation` 仍 200。验收测试：`tests/test_mcp_manager.py`
  （headers/token/fake transport）、`tests/test_audit_fixes.py`（consumers/无正则）、
  `tests/test_panels.py`（system/logs/credentials 与旧路由）。

### Phase 4 — 收尾（P2）
- 单一 nav manifest；删旧 panel/路由（至少一个 release 后）；更新 `docs/console-redesign.md`、README、截图；补契约测试。
- 验收：仓库内无旧路由引用；每个 panel 都有 nav 归属或显式 hidden；文档与截图一致。

**不要做的大爆炸**：一次性重命名 panel id、删除旧路由、改 API 路径、把 HA 直接塞进通用 MCP、把音乐凭据文件改成统一 secrets。每一步都要有 redirect/alias 和回滚。

## 6. xfyun 日志专项（复现 / 根因 / 建议）

### 6.1 复现路径（不接 NAS、不需要真 key）
1. 配置 `providers.tts.chain: [xfyun_tts, volc_tts]`（模板默认，`config.example.yaml:183` / `template.yaml:155`），`xfyun_tts` 的三个 env 名存在但 `XFYUN_*` 无值，`VOLC_API_KEY` 有值；`ack.mode: tts`（`config.example.yaml:110`）。
2. 启动：`render_ack_wav` 先试 xfyun，失败后 volc 成功；`_warm_filler` 又试 3 条 filler；每轮对话每句再试一次。
3. 我在进程内用 fake volc fallback 复现：3 句话 → **3 条** `WARNING rui.session.turn: tts: xfyun_tts failed (MissingKey)`，音频仍由 volc 产出。

### 6.2 根因（四处叠加）
- `TurnRunner.__init__` 一次性构建完整链，不按就绪过滤（`turn.py:294-296`）；`_speak_sentence` 对每句遍历整条链，失败即 `log.warning`（`turn.py:530,569`）。
- ack 渲染与 filler 各自有循环和 warning（`ack_render.py:77,119`；`ack.py:36`），启动时就会先刷 4–5 条。
- 就绪判断其实已经存在（`keycheck.py:426-474`），但只有向导用；运行时不用。向导的 `stage_satisfied` 是“链里任意一个可用即满足”（`keycheck.py:458-474`），所以 volc 有 key 时 step 显示 done，而 xfyun 仍是第一顺位。
- 日志面板只隐藏 `[level] voice peak`（`app.py:523-524`），不会聚合 provider 重复行。

### 6.3 推荐行为
1. 启动时按 `provider_ready` 把链分成 ready / not-ready；只把 ready 的放进运行时链；未就绪只记一次 INFO，全部未就绪才记一次 ERROR。
2. 区分 `MissingKey`（未配置，INFO+一次）与 `ProviderError`（真故障，WARNING；按 provider 状态变化或时间窗限流，不要每句刷）。
3. 每次 stage 结束记录 `used=volc_tts`；Models 页显示 ready/missing/needs-restart 徽章和“设为主用/移除”动作，不静默改链。
4. 日志面板做重复行聚合/计数，而不是新增 hide 规则；真错误仍要出现。
5. 加测试：链 `[xfyun, fake_fallback]` + 3 句，断言 0 条 per-sentence WARNING、1 条 fallback INFO、音频来自 fallback；全链未就绪断言 1 条 ERROR。

> 实施状态（2026-10-25，RUI 0.3.2）：1/2/5 已落地。运行时链由 `build_runtime_stage` 按
> `keycheck.provider_readiness` 过滤（`MissingKey`/本地模型/`base_url` 三种原因），未就绪者每进程
> 一条 INFO，整条 stage 无 ready 时一条 ERROR 且不再逐句尝试；运行期真故障（含 `ProviderError`）
> 一条 WARNING / provider / 60 s。`session/ack.py`、`audio/ack_render.py`（ack 与 filler）走同一策略。
> 向导 `providers.save` 改为 selected 首位 + 保留旧 fallback（去重），`GET /api/v1/setup/providers`
> 增加 `ready` / `ready_reason`、`stage_state.ready` / `not_ready` 供 Models 页徽章使用。3 的
> “设为主用/移除”动作与 4 的日志聚合仍属前端/后续项。

## 7. Backlog（P0/P1/P2）
| 优先 | 事项 | 文件区域 | 验收 |
|---|---|---|---|
| P0 | xfyun 就绪过滤 + 单次日志（已实现） | `providers/chain.py`、`keycheck.py`、`session/turn.py`、`session/ack.py`、`audio/ack_render.py` | caplog：3 句 0 warning，1 info；全失败 1 error |
| P0 | 两分组导航 + 标签 + 向导条件显示 | `web/src/panels.ts`、`SideNav.vue`、`i18n.ts`、`App.vue` | 旧路由 200；2 组；390px 无溢出 |
| P0 | 音乐禁用页死路 / source 校验 / enabled 诚实性 | `app.py:537-598`、`PanelHost.vue`、`catalog.py:1114-1140` | 关闭状态也能配置；未知 source 不显示 enabled |
| P0 | 向导 01 文案 + 不丢 fallback（后端已实现） | `SetupView.vue`、`i18n.ts`、`setup_api.py:739-801` | 重跑向导后 fallback 仍在 |
| P1 | 合并设备三视图 | `app.py`、`DeviceManager.vue`、`ConfigCenter.vue`、新 `DevicesView.vue` | 状态/音量/管理/音频都可到；旧 URL redirect |
| P1 | 合并 Tools + MCP/HA + `/tools` | `app.py`、`ConfigCenter.vue`、`McpManager.vue`、`ToolsView.vue`、新 `IntegrationsView.vue` | 一个入口；两个热写路径不冲突 |
| P1 | 合并音乐播放 + 音源 + 凭据 | `app.py:537-598`、新 `MusicView.vue`、`BlockCredentials.vue` | 一页完成播放/开关/凭据/测试 |
| P1 | 合并助手/会话/话术 | `app.py:417-485`、`ConfigCenter.vue`、`PhrasebookEditor.vue`、新 `AssistantView.vue` | tab 切换不丢草稿 |
| P1 | provider 预设 + 新建 + 内联 key + 就绪 | 新 `providers/presets.py`、`app.py`、`schema.py`、`ProvidersView.vue`、`ProviderKeyStrip.vue` | 预设创建后真实 probe 通过；env 名不可自填 |
| P1 | 凭据页降级 + consumer 类型化（✅ 2026-10-25） | `SecretsView.vue`、`catalog.py:1465-1525`、`ConfigCenter.vue` | 消费页内联；`/secrets` 仍可书签 |
| P2 | 音乐源适配器（Subsonic 家族 ✅ 2026-10-25；Jellyfin/Emby→Plex 待做） | `music/sources/*`、`music/source.py`、`app.py`、`schema.py` | Subsonic 家族已有 fake server 测试（Navidrome+Airsonic）；未知源 422；凭据不回显；Jellyfin/Emby 仍需新适配器 |
| P2 | HA 真正实例化 / MCP headers（✅ UI 实例化 + headers/token，数据模型未合并） | `schema.py:446-455`、`mcp_manager.py`、`tools/ha.py`、`app.py`、`McpManager.vue` | headers 生效；HA 白名单/危险域不退化；工具名兼容 |
| P2 | 单一 nav manifest + hidden/alias | `app.py`、`panels.ts`、`SideNav.vue`、`tests/test_panels.py` | 每个 panel 有归属；测试防回归 |
| P2 | 文档/截图/飞书技术文档 | `docs/console-redesign.md`、`README.md`、`docs/console-*` | 路由表与截图一致；从 manifest 生成 |

不做：API 路径重命名、panel id 大爆炸、无适配器的音乐“预设”、为了合并 HA 而删白名单/危险域。飞书式技术文档是 P2，且应由路由/panel manifest 生成，避免“文档一套、导航一套、代码第三套”。待确认：音乐是否日常播放（决定概览是否放“正在播放”）；HA 是否是唯一高价值 MCP；向导完成后是否允许从系统页重跑。
