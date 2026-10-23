# 控制台（后台界面）通用型设计

2026-10-22 9哥 定：后台界面结合新项目特点重构，**重点是通用型**；派子 agent 调研了老项目
控制台全文报告（本文件为蒸馏，老项目引用均指 xiaoai-voice-entry，只读）。

## 1. 调研结论速览

**老项目形态**（server/webapp.py 561 行 + web/index.html + app.js 1250 行）：标准库 HTTP +
原生 JS 单文件 + 轮询；控制台与语音循环同进程但用 HOOKS 字典解耦（webapp.py:33，
assistant_v3.py:1493 注入 25 个钩子）。9 个页面：概览/人设/唤醒词/音色/音量音乐/灯光/记忆/
MCP/会话日志。

**最值钱的五件事（全部继承）**：
1. **数字必须真实**——概览页明写「所有数字来自真实调用，不是占位」（index.html:47）；
2. **生效时机标签**——每张卡片标「下次唤醒生效 / 需要重启」（index.html:82,116）；保存条按
   dirty 汇总生效方式（app.js:107-118）；
3. **声明式配置绑定**——`data-cfg="section.key"` 任意输入自动绑定（app.js:41-50），最接近
   「通用渲染」的雏形；
4. **轮询不打扰交互**——滑条拖动期间轮询回写被挡（app.js:671-682）；
5. **一键操作与语音同路径**——控制台按钮和语音/硬件键走同一条代码（assistant_v3.py:1270），
   错误原文可见、重启仅 idle 时允许（webapp.py:490）。

**历史包袱（避开）**：每个功能 4 处手写（HOOKS+路由+HTML+JS）；innerHTML 拼接；轮询定时器
散落；Basic Auth 弹窗体验差。

**新项目现状**：web/ 已是 **Vue3 + TS + Vite**（9 个 view，vue-router，Makefile 有 web 目标，
Docker 两阶段构建）——「零构建 vs 框架」已被项目回答：保持 Vue 栈，**要解决的是
「每个功能手写一个 view」**。API 全景见 app.py（/health /audio /devices /turns /traces
/tools /music(只读) /config(assistant,session 可写) /secrets /doctor /device/volume …）。

## 2. 通用型核心：面板注册表（Panel Registry）

**原则：面板的唯一事实来源在后端。** 后端知道功能是否启用（music.enabled）、音频面是否在；
前端不做「这个功能存在吗」的判断。**新增功能 = 后端注册一条 PanelSpec，控制台自动长出面板，
前端零改动。**

### GET /api/v1/panels → {"revision", "panels": [PanelSpec]}

```json
{
  "id": "music", "title": "音乐", "icon": "♪", "group": "living", "order": 30,
  "enabled": true, "enabled_reason": "",
  "source": "/api/v1/music", "refresh_s": 4,
  "blocks": [
    {"type": "stats", "cards": [
      {"label": "状态", "value": "$.state",
       "map": {"playing": "播放中", "paused": "已暂停", "idle": "空闲"},
       "tone": {"playing": "ok", "paused": "warn"}},
      {"label": "队列剩余", "value": "$.queue_left", "unit": "首"}]},
    {"type": "now", "title": "$.track.title", "sub": "$.track.artist",
     "empty": "没有在播的歌"},
    {"type": "actions", "buttons": [
      {"id": "pause", "label": "暂停", "method": "POST", "href": "/api/v1/music",
       "body": {"action": "pause"}, "enabled_when": "$.state=playing"},
      {"id": "stop", "label": "停止", "method": "POST", "href": "/api/v1/music",
       "body": {"action": "stop"}, "confirm": "停止并清空队列？", "danger": true}]},
    {"type": "list", "title": "最近爱听", "items": "$.top", "item_title": "$.artist",
     "item_sub": "$.plays 次", "empty": "还没有播放记录", "max": 5}
  ]
}
```

### 渲染规则

1. 前端拉注册表 → 按 group/order 生成导航 → 单个 `PanelHost.vue` 渲染一切。
2. `enabled=false` 的面板**灰化显示原因**，不消失（「为什么没有音乐面板」本身就是信息）。
3. 表达式刻意极小：`$.path` 取值 + `map` 翻译 + `enabled_when: "$.state=playing"` 单等值
   比较。**不搞图灵完备**；表达不了的交互走逃生舱（见 §2.4）。
   列表标题/副标题允许「一个字面串里混多个 `$.path`」（`panels.ts: format`），例如
   `"$.room · $.id"`、`"$.host · $.adapter · $.model · $.status"`。
4. 轮询统一 `usePanelPoll(spec)`：不可见不轮、document.hidden 暂停、滑条拖动期跳过回写、
   失败显示 errbox 保留旧数据。
5. actions 统一：busy → 带token请求 → 失败显示后端 detail 原文 → `refresh:true` 重拉。
6. form block 统一：draft/baseline/dirty 三态 + `PUT /api/v1/config` + 422 按 loc 回字段
   红框（抽 AssistantView 的 issueKey 成 `useConfigForm`）。
7. **控制台路由是 SPA**：任何非 `/api/*` 路径由服务端回 `index.html`（`web/api/app.py: console`），
   刷新/收藏/深链都必须能开；`/api/*` 打错仍回 JSON 404。`/health` 已归 API，体检页在
   `/diagnostics`。

### 2.4 block 词表（2026-10-23 复核后）

| type | 用途 | 关键字段 |
|---|---|---|
| `stats` | 数字卡片 | `cards[{label,value,unit,map,tone}]` |
| `now` | 现在在播/当前状态 | `title,sub,empty` |
| `actions` | 按钮（声明 method/href/body） | `buttons[{id,label,method,href,body,confirm,danger,enabled_when,refresh}]` |
| `list` | 简单列表 | `items,item_title,item_sub,empty,max` |
| `form` | 配置表单（PUT /api/v1/config） | `section,fields[],apply` |
| `table` | 表格 | `items,columns[],empty` |
| `log` | 日志 tail | `follow,filter,hide,hide_label` |
| `memory` | 记忆 CRUD | `title,empty` |
| `say` | 试一试：打字走一整轮 | `href,placeholder,hint` |
| `volume` | 设备混音器读写 | `href,label,min,max,note` |

### 2.5 逃生舱（已实现，当前无使用者）

PanelSpec 声明 `"component": "<id>"` → `PanelHost` 从 `components/panelComponents.ts`
懒加载手写 Vue 组件替换 blocks。注册表现在是空的：现有页面都被 block 覆盖，
机制先就位，等第一个真需要专属交互的面板（P4 向导/设备探测）再用。


### 需新增后端端点（最小集）

- `GET /api/v1/panels`——注册表（`create_app` 里一张纯 Python 表，音乐面板在
  `if cfg.music.enabled:` 装配分支旁注册）。
- `POST /api/v1/music` `{action: play|pause|resume|stop|next|prev, query?}`——play 空 query
  = 随机电台；MusicService 方法全有。
- `GET /api/v1/music/search?q=`——query 管道 + source.search，music enabled 才注册。
- （P3）`GET /api/v1/logs?lines=`——日志 tail。

## 3. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| **P1** ✅ | PanelSpec + /panels + POST /music + search；前端 usePanels/PanelHost + stats/now/actions/list 四种 block + usePanelPoll；迁移概览/设备/音乐三面板 | 切 `music.enabled` 导航自动出现/灰化音乐面板；控制台按钮与语音「暂停」同一条路径 |
| **P2** ✅ | form block + useConfigForm；5 个 form 面板（设置/密钥/体检/系统/会话）；401 时 token 输入 UI；生效时机标签 | 配置改动全走注册表 |
| **P3** ✅ | `GET /api/v1/logs` + log block（轮询/过滤/跟随）；table block + 对话面板；memory block + 记忆面板 | 日志页不再是占位 |
| **P3.5** ✅ 2026-10-23 | **逐页复核**：SPA 深链 fallback、`/diagnostics`、`say`/`volume` 两种 block、列表串插值、日志隐藏噪声、删掉 4 个死页面与 2 个重复入口、9 条注册表契约测试 | `docs/console-audit.md` |
| **P4 ✅ 2026-10-23** | 首次安装向导（`/setup` + `GET /api/v1/setup` + keys/device/wake/probe）：状态由服务器上的 key/模型文件/config 计算；**key 是否齐全按各 provider 链的 `api_key_env/app_id_env/api_secret_env` 实际判断**（不硬编码 key 名，本地 sherpa 条看模型文件）；加设备会写 config（**按 id 合并，保留 listen_port/LED/volume 等向导不展示的字段**）、重生成 keywords、重启音频面；只有前四步真没完成时首装才落地向导页（`complete` 标志，验证「试一句」只是提示不强制） | 隔离 :18090 实例实测：`/` → `/setup`；三步勾选随真调用翻转、keywords.txt 逐字一致、探测返回不可达原因；dev 配置实测 `complete=true` 时 `/` 不再跳向导；`tests/test_setup.py` 7 条 |
| **P5 ✅ 2026-10-23** | 设备管理（`/panel/devices-admin`：编辑/添加/删除/探测，保存即重启音频面并保留表单不展示的字段）+ MCP/HA 管理页（`/panel/mcp`：地址/token 写入、运行期重连、测试连接、通用 MCP 只读说明） | `GET/PUT/DELETE /api/v1/devices/{id}`、`POST /api/v1/devices/probe`、`GET/PUT /api/v1/mcp`、`POST /api/v1/mcp/test`；dev 实测 HA connected、86 工具/75 实体/3.8 ms；`tests/test_devices_admin.py` 7 条 + `tests/test_mcp_admin.py` 7 条 |
| **P6 ✅ 2026-10-23** | PhraseBook 编辑区（`/panel/phrases`，走 `component` 逃生舱 `phrasebook-editor`）+ 覆盖层 `data_dir/phrases.yaml` + 槽位硬校验 + 剧本契约（`tests/test_phrasebook.py` 两条 YAML 场景） | 50 键全量下发/搜索/分组/槽位 chips/示例/逐键恢复/全部恢复；非法覆盖 422 按 key 回显且不落盘；保存后下一句立即生效；HA 提示片段折叠为高级 |
| P4（旧，可选） | websocket 替换轮询（仅日志/进度需要亚秒级） | — |

**风险与对策**：协议过度设计 → 10 种 block + 单等值 + 逃生舱；面板与 API 漂移 →
`tests/test_panels.py` 钉住「每个 PanelSpec 的 source/href 都存在且 200 / 是真路由」。

---

## 12. 控制台 2.0（2026-10-23，含审计修复）

设计契约全文：**`docs/console-redesign.md`**（方向/信息架构/设计系统/QA 报告）；参数契约：
**`docs/config-catalog-contract.md`**；审计与修复状态：**`docs/audit-round-1.md`**。

**一句话**：控制台从「手写设置面板」变成「catalog 驱动的配置中心 + 双语气象仪器界面」。

### 12.1 界面

- **设计**：暖纸底 + 墨色 + 铁锈色唯一强调；衬线 h1、等宽遥测、发丝线分块；签名元素是页面顶部
  signal ledger（`● 正在听 · 音频面 开 · 运行 05:45 · 音箱 1 · 唤醒 0`，全部是 /health+/audio 真数据）。
  概览是 7/5 不对称：左「试一试 + 停止出声」，右行式遥测 ledger；不做卡片墙。
- **i18n**：zh-Hans / en，`web/src/i18n.ts` 类型完备字典（en 缺键 typecheck 失败）、侧栏切换、
  localStorage、`<html lang>`、document.title；后端 PanelSpec `title_en` 优先、缺失回退中文。
- **主题**：light / dark / system，CSS 变量 token，记忆用户选择，`prefers-reduced-motion` 生效。
- **导航**：运行（概览/对话/日志）· 生活（音乐/记忆）· 配置（助手与会话/音箱参数/外部资源/工具/
  底子·高级）· 维护（设备/设备管理/话术/MCP-HA/密钥/体检/安装向导）；桌面 244px 索引轨，
  手机抽屉（inert + 滚动）。旧路径 `/assistant`、`/settings`、`/voice`、`/models` 等全部重定向。
- **响应式**：1440 与 390 双端截图与 DOM 断言：0 横向溢出；配置保存控件桌面 sticky 顶栏、手机 fixed 底条。

### 12.2b 音色试听与火山额度（2026-10-23 晚）

- 外部资源页每个 TTS provider 的「音色」是官方音色下拉（火山 2.0 20 个，含小荷/Vivi/邻家男孩），
  旁边「试听」用当前选择（即使未保存）合成 15 字示例并在浏览器播放；试听不写配置、不影响 dirty。
- 体检页「火山用量与额度」行式卡片：可用余额、今日/近 7 天 TTS 字符、资源包、更新时间；
  数据来自 `GET /api/v1/billing/quota`（火山账单/用量 API，缓存 5 分钟，缺 AK/SK 时给提示）。
- 同组还有「语速」（-50..100，0 正常）、「情感」（默认/开心/难过/生气/惊讶/害怕/厌恶/兴奋/冷漠/平静）、
  「情感强度」（1–5）；保存后重启生效，点「试听」会把未保存的这三项一起合成，先听再存。
- 截图：`docs/console-shots/voice-audition.png`、`quota-card.png`。

### 12.2 配置中心（四类参数 + 效果标注）

`GET /api/v1/config/catalog` 返回 4 类（tier）21 组、按配置动态生成字段（dev 157 个）：
① 底子（system/web：data_dir、log_level、timezone、host/port、allow_anonymous）——
   弱化 + 「改错会影响整条语音链路」警示；
② 音箱相关（devices 每台 26 字段含 LED/唤醒词/音量/端口；audio.capture/voice/playback/aec；ack）——
   「跟随音箱硬件变化，换音箱要重新量」；
③ 外部资源（三条 chain：llm/asr/tts；每 provider 的 kind/base_url/model/api_key_env/timeout/retries/…）——
   「改完重启服务让整条链重新加载」；
④ 工具（weather/news/search/ha/mcp_servers/music）——「多数保存即生效；音源本身改完要重启」。

每个字段：`label`、`hint`（改这个能达到什么效果 / 什么时候用）、`effect`（**保存生效 / 重启生效**）、
单位、范围、选项、`advanced`、`writable`、`nullable`、`virtual`。效果落地：38 个 save 字段热生效；
restart 字段保存后返回 `restart_fields`，界面显示 `docker compose restart` 命令与「仅重启音频面」按钮
（`POST /api/v1/system/audio/restart`，实测 0.4 s、音箱重连）。

**音乐源路径自动更新**：`music.source` 改为 `navidrome` 时自动填 `<data_dir>/lan_media.json` 与
`<data_dir>/music_cache`；未知源保留手动值并 warning。音乐凭据可在配置中心填写
（`PUT /api/v1/music/credentials`，写 0600 文件、值不回显）并有「测试连接」（`POST /api/v1/music/test`）。

**密钥**：`catalog.secrets` 由 provider 链实际引用的 `api_key_env/app_id_env/api_secret_env` +
设备 `token_env` + search/HA + `web.auth_token` 派生，每项带 `used_by` 与 hint；只回 `has_value`，
写入走 `POST /api/v1/secrets`。控制台写的 key 现在对 LLM/ASR/TTS/HA/device 全部生效
（`secrets.get` 统一解析）。

### 12.3 鉴权与安全

- 非 loopback 绑定且 `web.auth_token` 为空时**拒绝启动**；`house-ear setup` 生成 token 并打印一次，
  `house-ear token` 可打印，`--rotate` 轮换。控制台在 401 时提示输入 `X-Auth-Token`。
- `web.allow_anonymous` 只在本机开发显式打开（catalog 里标「危险」并写明后果）；
  dev 配置 `tools-4399-dev.yaml` 已开，生产模板默认关闭。
- `hmac.compare_digest` + 每 IP 失败限速（10 次/分钟 → 429）。
- debug/play 只允许 data_dir 与 music cache 内文件；`*_env` 字段只读；HA 拒绝
  lock/alarm/valve/script/scene/automation/shell_command/python_script/hassio；
  工具输出与实体名加「不可信外部数据」清洗；config/backup/secrets/service.log 全部 0600。

### 12.4 验收证据

- Lead 集成探针（截图存 `docs/console-shots/`）：17 页 × headless Chrome，0 意外 console error、0 横向溢出；
  zh/en + light/dark 截图；**真实 UI 保存** 120→121→120（API 复核一致）；真实 422 → 红框 + 中文行内；
  手机 390 概览/配置 0 溢出。
- console-ui QA：32 场景截图报告 `docs/console-shots/qa-report.md`（原始 JSON 在会话产物中），
  critic 首轮 top-5 已修（保存控件常驻、概览行式 ledger、禁用主按钮中性、focus 统一、手机抽屉滚动）。

