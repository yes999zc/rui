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
   比较。**不搞图灵完备**；表达不了的交互走逃生舱：PanelSpec 声明 `"component": "xxx"`，
   前端按 id 懒加载专有 Vue 组件（覆盖 80% 常规 + 20% 手写，防协议被扭曲）。
4. 轮询统一 `usePanelPoll(spec)`：不可见不轮、document.hidden 暂停、滑条拖动期跳过回写、
   失败显示 errbox 保留旧数据。
5. actions 统一：busy → 带token请求 → 失败显示后端 detail 原文 → `refresh:true` 重拉。
6. form block 统一：draft/baseline/dirty 三态 + `PUT /api/v1/config` + 422 按 loc 回字段
   红框（抽 AssistantView 的 issueKey 成 `useConfigForm`）。

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
| **P1** | PanelSpec + /panels + POST /music + search；前端 usePanels/PanelHost + stats/now/actions/list 四种 block + usePanelPoll；迁移概览/设备/音乐三面板 | 切 `music.enabled` 导航自动出现/灰化音乐面板；控制台按钮与语音「暂停」同一条路径 |
| P2 | form block + useConfigForm；密钥/体检/系统迁入；401 时 token 输入 UI；全局 toast + 生效时机标签 | 配置改动全走注册表 |
| P3 | /logs + LogStream（轮询版）；最近对话面板（turns/traces） | 日志页不再是占位 |
| P4（可选） | websocket 替换轮询（仅日志/进度需要亚秒级） | — |

**风险与对策**：协议过度设计 → 只 6 种 block + 单等值 + 逃生舱；面板与 API 漂移 →
test_api 加「每个 PanelSpec 的 source/href 都存在且 200」契约测试。
