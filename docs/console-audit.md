# 控制台逐页复核（2026-10-23）

> 方法：真跑的控制台（dev :8091）+ headless Chrome 走 DevTools Protocol 逐页开图。
> 每页记录 console error / 未捕获异常 / 4xx-5xx 请求 / 可见文本 / 全页截图；
> 对每个 `source` 与按钮 `href` 用 curl 真调一次。脚本 `/tmp/he-audit/audit.mjs`
> （一次性工具，不入库），结果 `/tmp/he-audit/out2/report.json`。
>
> **结论先行**：8 个注册表面板 + 6 个手写页全部能开、无 console 报错、无 4xx；
> 复核前的 7 个 P0 缺陷已修（见 §2），修完真机验证通过（§4）。剩余债在 §5，不阻塞。

## 1. 复核前的基线（真实缺陷）

| # | 级别 | 缺陷 | 证据 |
|---|---|---|---|
| 1 | P0 | **任何面板 deep link / 刷新 404**：`/panel/*`、`/assistant` 等直接返回 `{"detail":"Not Found"}` | `curl -o /dev/null -w '%{http_code}'` → `/` 200、`/panel/overview` **404**；截图是 JSON 文本页 |
| 2 | P0 | **`/health` 被 API 抢走**：体检页的路由叫 `/health`，服务端同名 JSON 端点先命中，刷新体检页拿到 JSON 而不是页面 | `GET /health` → `application/json`；页面只能从首页点进去 |
| 3 | P0 | **「试一试」从界面上消失**：唯一入口 `OverviewView.vue` 已死（router 不再引用），`POST /api/v1/debug/say` 无 UI | `grep -rn debugSay web/src` 只命中 api.ts；`/panel/overview` 只有「停止出声」 |
| 4 | P0 | **设备音量读写消失**：`DevicesView.vue` 已死；`GET/PUT /api/v1/device/volume` 无 UI | 同上，设备面板只有一行 `living` |
| 5 | P0 | **设备面板副标题空白**：spec 写 `"$.adapter · $.model · $.status"`，渲染器只支持单个 `$.path` | 页面文本只有 `living`，`BlockList` 的 `lookup` 对混合字符串返回 undefined |
| 6 | P0 | **`更多 → 日志` 是 M0 占位**：`LogsView.vue` 10 行空壳（「M2 接入 WebSocket 后…」），与真日志面板重复 | 页面文本 214 字；真日志在 `/panel/logs` |
| 7 | P0 | **`更多 → 助手` 与「设置」面板重复**；且设置面板缺 `assistant.system_prompt`（删掉助手页会丢字段） | 设置表单 11 字段，AssistantView 独有 `system_prompt` |
| 8 | P1 | **日志面板 95% 是噪声**：音频面每 2 s 一行 `[level] voice peak`，300 行 tail 里 41,839 字，读不到有效信息 | `/panel/logs` 文本 41,839 字，几乎全是 level 行 |
| 9 | P1 | **记忆面板来源显示英文枚举**：`voice` / `turn` 原样渲染 | `/panel/memory` 文本 `用户不吃香菜 voice` |
| 10 | P1 | **PanelHost 残留死代码** + docs 承诺的 `component` 逃生舱没实现 | `<component :is="null" />`；PanelSpec 声明 component 不会加载组件 |
| 11 | P1 | 侧栏「更多」7 个手写页里 2 个是重复/死入口（助手、日志） | 导航分组 |

## 2. 已修（本轮）

| 缺陷 | 修法 | 文件 |
|---|---|---|
| deep link 404 | SPA history fallback：挂 `/assets`，其余非 API 路径回 `index.html`；`/api/*` 仍 JSON 404 | `web/api/app.py` `console()` |
| `/health` 冲突 | 体检页挪到 `/diagnostics`（API 路径不动，控制台文档写明） | `router.ts`、`App.vue` |
| 试一试 | 新 block 类型 `say`（声明式：`{type:say, href, placeholder, hint}`）+ `BlockSay.vue` | `app.py`、`BlockSay.vue`、`PanelHost.vue` |
| 设备音量 | 新 block 类型 `volume`（GET/PUT 自己的 href）+ `BlockVolume.vue`，滑块 + 数字 + 写入/重读 | 同上 |
| 副标题空白 | `panels.ts` 加 `format()`：同一字符串里多个 `$.path` 逐个求值 | `panels.ts`、`BlockList/BlockNow` |
| 日志占位页 | 删除 `LogsView.vue`；`/logs` → `/panel/logs`；侧栏去掉重复项 | `router.ts`、`App.vue` |
| 助手页重复 | 删除 `AssistantView.vue`；`/assistant` → `/panel/settings`；设置面板补 `system_prompt` 字段 | `app.py`、`router.ts` |
| 日志噪声 | log block 支持 `hide` 模式 + 默认勾选「隐藏 level 噪声（264 行）」 | `app.py`、`BlockLog.vue` |
| 记忆来源 | `voice/turn/console` → 语音/对话里提到/控制台 | `BlockMemory.vue` |
| 逃生舱 | `PanelSpec.component` + `PANEL_COMPONENTS` 懒加载注册表（当前空，机制就位） | `panelComponents.ts`、`PanelHost.vue` |
| 契约漂移 | 新增 `tests/test_panels.py`（9 条）：所有控制台路由可用 URL 打开、API 不入 SPA、每个 source 200、每个按钮 href 是真路由、设置面板字段集合、say/volume 存在、日志隐藏模式 | `tests/test_panels.py` |

## 3. 修后逐页结果（2026-10-23 12:2x，headless Chrome）

| 页面 | 可见文本 | console error | 4xx/5xx | 备注 |
|---|---|---|---|---|
| `/panel/overview` | 234 字 | 0 | 0 | 试一试 + 状态 + 停止出声 |
| `/panel/devices` | 286 字 | 0 | 0 | `客厅 · living` / `<音箱内网 IP> · xiaoai · LX06 · ready` + 音量 178/255 |
| `/panel/turns` | 130 字 | 0 | 0 | 最近两轮上下文开关 |
| `/panel/logs` | **4,978 字**（原 41,839） | 0 | 0 | 36 行有效日志，隐藏 264 行 level |
| `/panel/memory` | 153 字 | 0 | 0 | 已确认条目 + 来源「语音」 |
| `/panel/settings` | 337 字 | 0 | 0 | 助手 12 字段（含系统提示词）+ 会话 6 字段 |
| `/panel/music` | 218 字 | 0 | 0 | 状态/正在播/六个按钮/最近爱听 |
| `/assistant` | 337 字 | 0 | 0 | → 重定向到设置面板 |
| `/voice` | 913 字 | 0 | 0 | 采样率/AEC/门限只读参考 |
| `/models` | 693 字 | 0 | 0 | 三条 provider 链 |
| `/tools` | 777 字 | 0 | 0 | 25 个已注册工具 |
| `/secrets` | 749 字 | 0 | 0 | 3/3 已配置 |
| `/diagnostics` | 1,288 字 | 0 | 0 | 体检（原 `/health`） |
| `/logs` | 4,978 字 | 0 | 0 | → 日志面板 |

## 4. 真机验证（音箱已可出声，2026-10-23 12:09–12:2x）

| 项 | 动作 | 结果 |
|---|---|---|
| 试一试 / 整轮 | `POST /api/v1/debug/say {"text":"现在几点"}` | 1.81 s 返回 `现在是中午12点09分。`，`audio_bytes=74240`（4.6 s @16k），`playback_s` 2.7 → 3.5，设备 owner `dialogue` 播完释放 |
| 音乐 transport | `POST /api/v1/music` play/pause/resume/next/stop | `playing→paused→playing→playing(壹块钱)→idle`，真出声；队列 20 首 |
| 设备音量 | `GET/PUT /api/v1/device/volume` | 178 → 150 → 178（已还原）；range 0–255 |
| 音频面 | `/api/v1/audio` | `speaker_connected=true`，wake_count 0（本轮无唤醒） |

**顺手发现并修掉的运行环境坑**：dev 启动脚本里没有 pin PATH，重启后 `resolve` 每首
歌都报 `No such file or directory: 'ffmpeg'`（音乐"能点歌但立刻 skip 全部曲目"）。
`serve-dev.sh` 已加 `export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"`；
生产侧同一问题由 Dockerfile 装 ffmpeg 解决（P15）。

## 5. 剩余债（不阻塞，进 C-UI P4–P6 / D1c）

1. `语音 / 模型 / 工具 / 密钥 / 体检` 仍是手写页：内容是精选说明文字，迁 PanelSpec 要么加 block 类型，要么就地保留（P4–P6 决定）。
2. 音乐面板没有「点歌/搜索」输入（`GET /api/v1/music/search?q=` 与 `POST /api/v1/music {action:play, query}` 都已存在）。价值高，排 C-UI P4–P6。
3. 概览缺播放缓冲余量 / 唤醒计数细节（在体检页有），两份数据同源，可合。
4. 体检页有英文碎片：「可带设备 ~54 台 assumed 0.35 core/device」「barge-in target…」。
5. 日志里 plane 行是「客户端前缀 + 平面自己的时间戳」双前缀（`... audio.client: plane: ... INFO __main__:`），纯观感。
6. 音频面 level 行仍每 2 s 写进文件（UI 已隐藏）。若要降噪，改 `audio/process.py` 的间隔或级别，属音频面改动，先记着。
7. 音乐面板空闲时「暂停/继续」都在（置灰），可改成只显示可用动作；低优先级。
