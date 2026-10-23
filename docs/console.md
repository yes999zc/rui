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
| **P6 ✅ 2026-10-23** | PhraseBook 编辑区（`/panel/phrases`，走 `component` 逃生舱 `phrasebook-editor`）+ 覆盖层 `data_dir/phrases.yaml` + 槽位硬校验 + 剧本契约（`tests/test_phrasebook.py` 两条 YAML 场景） | 49 键全量下发/搜索/分组/槽位 chips/示例/逐键恢复/全部恢复；非法覆盖 422 按 key 回显且不落盘；保存后下一句立即生效；HA 提示片段折叠为高级 |
| P4（旧，可选） | websocket 替换轮询（仅日志/进度需要亚秒级） | — |

**风险与对策**：协议过度设计 → 10 种 block + 单等值 + 逃生舱；面板与 API 漂移 →
`tests/test_panels.py` 钉住「每个 PanelSpec 的 source/href 都存在且 200 / 是真路由」。

