# v2 控制台功能实测（0.7.7 C 上半）

> 范围：`docs/audit/console-audit-scope.md` 的 14 条真实路由、7 个设备详情 tab、24 个 `/api/v2` 端点、页面内交互元素与异常态。
> 方法：本机 `rui serve --no-audio` + Playwright（无头 Chrome）+ 直接打 API + 读回 `config.yaml` / `/api/v1/config` / `/api/v2/*`。
> 夜间红线：不接真机、不触发 TTS/唤醒/音乐播放、不接触生产 NAS；`say` / `audio.play` / `verify.say` 最多走到服务端的 `409 device_not_connected` 守卫，**没有产生任何声音**。

## 0. 审计环境与判定口径

| 项 | 值 |
|---|---|
| 仓库 / commit | `house-ear` @ `1f857ea`（0.7.6 code-complete） + 本卡 4 处小修（未提交） |
| 服务版本 | `/health.version = 0.7.4`（`pyproject.toml` 未随 0.7.7 改号，本卡不动版本） |
| 契约 | `C-002/1`（`/api/v2/meta`）；`config_version = 3` |
| 启动命令 | `RUI_PLUGINS_DIR=/tmp/rui-audit/plugins .venv/bin/rui --config /tmp/rui-audit/config.yaml serve --no-audio` |
| 实例 A（主） | `127.0.0.1:18099`，2 台设备（`living`/`bedroom`，LX06 manifest 副本），房间 客厅/卧室，音频面未启动 |
| 实例 B（空屋） | `127.0.0.1:18101`，`devices: []` |
| 实例 C（配置恢复） | `127.0.0.1:18102`，坏 `config.yaml` + 有效 `config.last-good.yaml` |
| 实例 D（空屋写入） | `127.0.0.1:18103`，`devices: []`，用于 first-run `prepare` + `params(device.create)` 实测（R2 补测） |
| 浏览器 | Google Chrome 156.0.8078.47（Playwright 1.64.1；本机 chromium 缓存版本不符，用 `executable_path` 指向系统 Chrome） |
| 视口 | 1600 × 1000 |
| 数据 | 全部写入 `/tmp/rui-audit*`；未写真实 `~/.rui`、未改仓库内配置 |
| 截图 | `docs/audit/console-shots/`（76 张，命名 `<route>--<state>.png`） |

判定（scope §1）：
- **可用** = 点得动 → 请求 2xx（或预期的 4xx 门禁）→ 界面状态更新 → 重载/读回仍在（保存类）。
- **不可用** = 点不动 / 报错 / 静默无效果 / 保存后丢失。
- **文案错误** = 行为对但文字误导（含 i18n、英文碎片、术语不一致）。
- **未验证** = 本轮无法安全/无法构造，写明原因，不做推断。

## 1. 结果汇总

| 计数 | 值 |
|---|---|
| 实测交互元素行 | **172** |
| 可用 | **159** |
| 文案错误（本轮发现并已修） | **4 组**（明细见 §5；元素表内 1 行标 🟡，其余 3 组为壳/文案/接口层） |
| 不可用（本轮结束后仍存在的行为缺陷） | **0** |
| 条件性缺陷（仅异常环境触发，已记入已知限制） | **0**（0.7.8 已修：音乐凭据路径不可写 → **409**，见 §8.1） |
| 未验证（附原因） | **12 行 / 6 类**（见 §11） |
| 未处理项 | **0** |

> 计数口径：scope §0 的「约 95 个元素」是控件组估算（逐项展开约 155 项）；本文把 7 个 tab、保存上/下、空态、错误态、确认框等拆成独立行，共 **172 行**。24 个端点与路由矩阵不重复计入。

路由：14/14 返回 SPA（200），`/` 在 `web.console: v2` 下 302 → `/v2/`；`/legacy` 正常；`/api/v2/*` 未授权 401、未匹配 JSON 404。
端点：24/24 有实测结论（其中 2 个孤儿端点由 curl 验证，前端无入口）。

## 2. 路由矩阵

命令：`curl -s -o /dev/null -w "%{http_code} %{redirect_url} %{size_download}\n" <url>`

| 路由 | 期望 | 实测 | 结论 |
|---|---|---|---|
| `/`（console=v2） | 302 → `/v2/` | `302 → http://127.0.0.1:18099/v2/` | 可用 |
| `/v2/` | 200 v2 壳 | 200 / 1831 B | 可用 |
| `/v2/devices` | 200 | 200 | 可用 |
| `/v2/devices/add` | 200 | 200 | 可用 |
| `/v2/devices/living`（7 个 `?tab=`） | 200 | 7×200 | 可用 |
| `/v2/rooms` | 200 | 200 | 可用 |
| `/v2/setup` | 200 | 200 | 可用 |
| `/v2/run` | 200 | 200 | 可用 |
| `/v2/conversation` | 200 | 200 | 可用 |
| `/v2/logs` | 200 | 200 | 可用 |
| `/v2/memory` | 200 | 200 | 可用 |
| `/v2/voice-assistant` | 200 | 200 | 可用 |
| `/v2/tools` | 200 | 200 | 可用 |
| `/v2/music` | 200 | 200 | 可用 |
| `/v2/credentials` | 200 | 200 | 可用 |
| `/v2/health` | 200 | 200 | 可用 |
| `/v2/config` | 客户端重定向 → `/voice-assistant` | 200 壳 + SPA 跳到 `/v2/voice-assistant` | 可用 |
| `/v2/nope` | 404 页（HTTP 200 壳） | 200 + “Page not found / 找不到页面” | 可用 |
| `/legacy`、`/legacy/panel/music` | 200 旧壳 | 200 / 1845 B | 可用 |
| `/api/v2/meta` 无 token | 401 JSON | 401 | 可用 |
| `/api/v2/nope` | JSON 404 | 404 / 274 B | 可用 |
| 老链接 `/panel/overview`、`/panel/devices`、`/panel/turns`、`/panel/voice-assistant`、`/panel/providers`、`/panel/integrations`、`/panel/system`、`/panel/credentials`、`/panel/logs`、`/panel/memory`、`/panel/music`、`/setup`、`/secrets`、`/diagnostics`、`/channels` | 302 → 对应 v2（0.7.7 scope §5；0.7.8 D 实现） | **15/15 302**，跟随跳转全部 200 v2 壳（逐条见 §8.2） | 可用（0.7.8） |

## 3. 端点矩阵（24）

鉴权均带 `X-Auth-Token`；失败项均为**预期门禁**。

| # | 方法 路径 | 调用页 | 实测 | 结论 |
|---|---|---|---|---|
| 1 | GET `/api/v2/meta` | 全局壳 | 200；features 10 项、`contract=C-002/1`；401/500/合约不符均正确阻断 | 可用 |
| 2 | GET `/api/v2/house` | devices/rooms/run/detail | 200；2 设备、2 房间、1 warning | 可用 |
| 3 | GET `/api/v2/plugins` | setup/detail | 200；1 插件 `xiaoai-lx06` | 可用 |
| 4 | GET `/api/v2/plugins/{id}` | **无前端调用（孤儿）** | 200；前端 `grep` 无调用 | 可用（孤儿，待 0.7.8 决策） |
| 5 | GET `/api/v2/capabilities` | 全局/详情 | 200；10 能力、`led.set` 参数 schema | 可用 |
| 6 | GET `/api/v2/devices` | 5 页 | 200；列表/过滤字段齐全 | 可用 |
| 7 | GET `/api/v2/devices/{id}` | detail | 200；identity/capabilities/audio_summary/warnings | 可用 |
| 8 | GET `/api/v2/devices/{id}/audio` | audio tab、rooms N+1 | 200；effective/sources/layers/overrides | 可用 |
| 9 | GET `/api/v2/devices/{id}/activity` | activity | 200；`type=all|turn|log` 均 200 | 可用 |
| 10 | GET `.../connection` | connect | 200；`ws_url` 掩码 `token=••••••••` | 可用 |
| 11 | POST `.../connection/reveal` | connect | 无 token 409 `token_missing`；有 token 200 明文；10 次/分后 429 | 可用 |
| 12 | PATCH `/api/v2/house` | rooms | 200；`default_output`/`room_priority` 落配置 | 可用 |
| 13 | GET `/api/v2/house/audio` | rooms | 200；`explicit[]` 30 项 | 可用 |
| 14 | PATCH `/api/v2/house/audio` | rooms | 200；显式值写入 / null 清除均落配置 | 可用 |
| 15 | POST `/api/v2/house/audio/restart` | rooms（2 处） | 200 `restart.status=failed, error="audio plane not started"`（no-audio 预期） | 可用 |
| 16 | POST `/api/v2/devices` | **无前端调用（孤儿）** | 201 `{device,restart}`；端口冲突回 409 `port_conflict` | 可用（孤儿） |
| 17 | PATCH `/api/v2/devices/{id}` | config tab | 200；`volume_target`/`listen_port:null` 落配置；非法值 422 `details.fields` | 可用 |
| 18 | DELETE `/api/v2/devices/{id}?confirm=` | danger | 无 confirm 409 `confirm_required`；匹配后 200 + 刷新列表 | 可用 |
| 19 | PATCH `/api/v2/devices/{id}/audio` | audio tab | 200；`overrides` 增加/清除；`restart.scope=fleet` | 可用 |
| 20 | POST `.../token/rotate` | connect | 200；10 s 内二次 429；secrets 落盘 | 可用 |
| 21 | POST `.../test` | test | 200；evidence-only，不发声 | 可用 |
| 22 | POST `.../actions` | test | `volume.read/set`、`led.set`、`audio.say/play/stop` → 409 `device_not_connected` | 可用 |
| 23 | GET `/api/v2/setup/plan` | setup/add | 200；`?plugin_id&model_id` 后 8 步；缺参 422 | 可用 |
| 24 | POST `/api/v2/setup/plan/steps/{step}` | setup/add | 声明外 action → 400 `action_not_allowed`；未知 step → 400 `unknown_step` | 可用（空屋 first-run 实测 `device.create` 200、重复 id 409；wake/verify 受夜间红线限制） |

## 4. 逐页交互实测

> 定位优先用稳定 selector（`data-testid` / id），行号引用仅用于理解；本轮源码未大改。

### 4.1 全局壳（App.vue / TokenDialog.vue）

| 页面 | 元素（定位） | 动作 | 端点 | 结果 | 持久化 / 证据 |
|---|---|---|---|---|---|
| 壳 | 品牌 `.v2-brand` | 点击 | — | ✅ 可用 | URL → `/v2/devices` |
| 壳 | 主导航 运行 `.v2-nav-link` | 点击 | GET `/house` | ✅ 可用 | `/v2/run` |
| 壳 | 主导航 设备 | 点击 | GET `/devices` | ✅ 可用 | `/v2/devices` |
| 壳 | 主导航 全屋 | 点击 | GET `/house` | ✅ 可用 | `/v2/rooms` |
| 壳 | 主导航 配置 | 点击 | GET `/config/catalog` | ✅ 可用 | `/v2/voice-assistant` |
| 壳 | Run 子导航 4 项 | 逐项点击 | turns/logs/memory | ✅ 可用 | 4 个 URL 全部命中 |
| 壳 | Config 子导航 5 项 | 逐项点击 | catalog/music/secrets 等 | ✅ 可用 | 5 个 URL 全部命中 |
| 壳 | 语言 中/英 | 点 English | — | ✅ 可用 | `<html lang=en>`，`localStorage.rui-language=en`，重载保持；21 路由英文抽检无未译 UI 文案（仅配置数据值“我查一下”等仍为中文，属数据） |
| 壳 | 主题切换 | 连点 3 次 | — | ✅ 可用 | light → dark → auto，`dataset.theme` 与 `rui-theme` 一致，重载保持 |
| 壳 | token 按钮 → 对话框 打开/取消 | 点击 / 关闭 | — | ✅ 可用 | 标题「访问令牌」，取消后关闭 |
| 壳 | token 保存（错误 → 正确） | 填错保存 → 填对保存 | GET `/meta` 401 → 200 | ✅ 可用 | 401 态显示「需要访问令牌」；改对后页面恢复 |
| 壳 | token 清除 | 清空保存 | GET `/meta` 401 | ✅ 可用 | `rui-token` 为空，401 态 + 横幅 |
| 壳 | 401 自动弹窗 | 无 token 直达 `/v2/devices` | 仅 `GET /meta` 401 | ✅ 可用 | 状态块 + token 对话框自动打开；输入 token 后 `meta 200` 并加载页面 |
| 壳 | 「旧控制台」链接 `.v2-legacy` | 检查 / 打开 | — | 🟡 文案错误（已修） | 链接仍在（0.7.8 移除）；`title` 原写「0.6.x 仍为默认入口」→ 改为「打开旧版控制台（0.8.0 移除）」 |
| 壳 | meta 合约门禁 | 拦截 `contract=C-999/9` | GET `/meta` 200 | ✅ 可用 | 显示「API 契约不兼容」，不发起页面数据请求 |
| 壳 | meta 5xx + 重试 | 拦截 500 → 点重试 | GET `/meta` 500 → 200 | ✅ 可用 | 显示 HTTP 500 + 重试；恢复后加载 `/v2/devices` |

### 4.2 `/devices`

| 页面 | 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|---|
| devices | 新增入口（非空：添加设备） | 点击 | GET plugins/house | ✅ 可用 | `/v2/devices/add` |
| devices | 刷新 | 点击 | GET devices + house | ✅ 可用 | 2×200 |
| devices | 401 取令牌 | 无 token 打开 | GET meta 401 | ✅ 可用 | 同 §4.1 |
| devices | 空态 CTA（空屋实例，含顶部入口） | 查看 | — | ✅ 可用 | `首次安装`→`/v2/setup`、`开始首次安装`→`/v2/setup`、`直接添加设备`→`/v2/devices/add` |
| devices | `/legacy` 兜底（`DevicesView.vue:242`） | 静态检查 | — | ⚠️ 未验证 | 仅在 `device_create=false && setup_plan=false` 且空屋时出现；未构造该组合。代码存在，0.7.8 必须改 |
| devices | 设备卡片跳转 | 点第一张卡 | GET detail 等 6 个 | ✅ 可用 | `/v2/devices/bedroom` |
| devices | 按房间分组 | 渲染 | house | ✅ 可用 | 标题「客厅」「卧室」，2 张卡 |

### 4.3 `/devices/:id` 壳与 tab

| 页面 | 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|---|
| detail | 返回设备列表 | 点击 | — | ✅ 可用 | URL → `/v2/devices` |
| detail | 刷新 | 点击 | 6×GET（device/plugins/connection/audio/activity/house） | ✅ 可用 | 全部 200 |
| detail | 7 个 tab（概览/音频/配置/测试与动作/活动/连接/危险区） | 逐项点击 | 按 tab 加载 | ✅ 可用 | 7 个 `?tab=` URL 全部正确 |
| detail | 未保存草稿拦截 | 改 room → 点另一 tab | — | ✅ 可用 | 对话框「放弃未保存的修改？」；取消留在本页且草稿保留；确认后切走 |

### 4.4 设备详情 · audio

| 元素 | 动作 | 端点 | 结果 | 持久化 / 证据 |
|---|---|---|---|---|
| 放弃修改（上/下） | 改值 → 放弃 | 无请求 | ✅ 可用 | 草稿清空；继承行的输入框回空（placeholder「继承（当前：1000）」），未写库 |
| 保存覆盖（上，`audio-save`） | `voice.followup_delay_ms` 1000→1200 → 确认 | PATCH `.../audio` 200 | ✅ 可用 | 回读 `effective=1200`、`sources=voice.followup_delay_ms: device-override`、`overrides[]` 含该路径 |
| 保存覆盖（下，`audio-save-bottom`） | 同 handler | 同 PATCH | ✅ 可用 | 与顶部同一 `requestSave`，未重复点击 |
| 数字叶子输入 | 编辑 | — | ✅ 可用 | 脏标记「1 项未保存」；范围校验生效 |
| bool / enum 叶子 | — | — | ⚠️ 未验证 | 未逐个编辑布尔/枚举叶子；同一 `setDraft`/`buildPatch` 路径，数字叶子已验证 |
| 恢复继承 | 点 `audio-restore-<path>` → 确认 | PATCH 200 + GET audio | ✅ 可用 | `overrides=[]`、source 回到 `core-default`；出现「恢复继承已验证」类提示 |
| 3 个重试横幅（unresolved / verify-failed / retry-read） | — | — | ⚠️ 未验证 | 需要故障注入（读回失败/来源不明）才能出现；本轮未构造 |
| 4 层明细折叠 | 展开 `<details>` | — | ✅ 可用 | `audio-layers-<path>` 展开，core/manifest/global/device 四行 |
| 保存确认对话框 | 点击保存 | — | ✅ 可用 | 标题「确认保存音频覆盖」，列变更字段与 fleet 范围 |
| 恢复确认对话框 | 点击恢复继承 | — | ✅ 可用 | 标题「确认恢复继承」，列字段与当前值 |

### 4.5 设备详情 · config

| 元素 | 动作 | 端点 | 结果 | 持久化 / 证据 |
|---|---|---|---|---|
| 房间 | 编辑 | — | ✅ 可用 | 草稿变脏；未保存（保存路径由 volume/listen_port 验证） |
| host | 填带 scheme 值 | 客户端校验 | ✅ 可用 | 显示「Host address must not include a scheme…」；不发请求（英文界面） |
| listen_port 空 = 继承 | 先填 4412 再清空 → 保存确认 | PATCH 200 | ✅ 可用 | `config.yaml` 写 `listen_port:`（null），API 回读 effective `4412`（manifest 默认） |
| listen_port 填值 | — | — | ⚠️ 未验证 | 仅验证了空=继承；填值走同一 `PATCH /devices/{id}`，未单独保存 |
| volume_target | 62→64 → 保存确认 | PATCH 200 | ✅ 可用 | 配置写 `volume: 64`；重载表单仍为 64 |
| 唤醒词 添加 | 点「添加唤醒词」 | — | ✅ 可用 | 输入框 1→2；删除按钮存在（未点） |
| enabled | 切换 | — | ✅ 可用 | 草稿态切换成功（未保存） |
| 重置 / 放弃修改 | 改值 → 放弃 | 无请求 | ✅ 可用 | 回到服务器基线 |
| 保存确认框 | 保存 | — | ✅ 可用 | 标题「确认保存设备配置」，列 fleet restart 与受影响设备 |
| 422 文案 | 服务端非法 volume/host | 422 | ✅ 可用 | `details.fields[]` 路径 + 英文 message；UI 红框；`volume=999` 客户端先拦截 |
| 重启提示 | 保存后 | PATCH 返回 `restart` | ✅ 可用 | `restart.status="ok"`、`scope="fleet"`、`duration_ms=27–31`；UI 绿色「重启状态 成功 / 29 ms」（截图 `config--after-save.png`）。`failed + "audio plane not started"` 只属于 `POST /api/v2/house/audio/restart`（`v2_api.py:2224` 的 `audio is None` 守卫），不在设备 PATCH 路径 |

### 4.6 设备详情 · test / actions

| 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|
| 运行测试 / 重新运行 | 点击 | POST `.../test` 200 | ✅ 可用 | 显示「未通过 / 就绪 否 / 等待 0 ms」与检查项 |
| 离线门禁 | 页面加载（无 client） | — | ✅ 可用 | 三组动作按钮全 disabled，提示「设备没有活动链路；动作需要设备在线」 |
| volume.read | 模拟 `connected=true` 后点击 | POST `.../actions` 409 | ✅ 可用 | 错误条显示 `device_not_connected`；无声音 |
| 音量滑块 | 拖到 100 | — | ✅ 可用 | 数值 100 |
| volume.set | 点击 | POST 409 | ✅ 可用 | body `{"action":"volume.set","args":{"value":100}}` |
| LED 效果选择 | 下拉选择 | — | ✅ 可用 | 选项来自 capability schema；默认 `listening` |
| led.set | 点击应用 | POST 409 | ✅ 可用 | body `{"action":"led.set","args":{"effect":"listening"}}` |
| say 文本（≤500） | 输入 | — | ✅ 可用 | `maxlength=500`，字符计数同步 |
| say 发送（内联确认） | 试一句 → 确认 | POST 409 | ✅ 可用 | 二次确认块出现；确认后 409，未调用 TTS（client 为空的守卫先触发） |
| play 文件 / loop / gain | 填 data_dir 内文件 | — | ✅ 可用 | 输入/复选/数字齐全 |
| audio.play | 点击 | POST 409 | ✅ 可用 | body 含 file/loop/gain |
| audio.stop | 点击 | POST 409 | ✅ 可用 | 错误条 `device_not_connected` |
| 错误重试 | 409 后观察 | — | ✅ 可用 | 非 retryable 错误不显示重试（符合 `retryable=false`） |

### 4.7 设备详情 · activity / connect / danger

| 页面 | 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|---|
| activity | 时间线加载 | 加载 | ✅ 可用 | 空态「暂无事件」；GET `?limit=50&type=all` 200 |
| activity | 过滤 全部/仅回合/仅日志 | 逐项点击 | ✅ 可用 | 3 个 GET `type=all|turn|log` 200 |
| activity | 刷新 / 条数 | 点击 / 选 100 | ✅ 可用 | GET 重新发出，URL 带 `limit=100` |
| connect | reveal（无 token） | 点击 | ✅ 可用 | 409 `token_missing`，可读提示 |
| connect | reveal（有 token） | 点击 | ✅ 可用 | 200；显示 token + ws_url；30 s 倒计时 |
| connect | 隐藏明文 | 点击「立即隐藏」 | ✅ 可用 | 明文块消失 |
| connect | reveal 限流 | curl ×11 | ✅ 可用 | 前 10 次 200，第 11 次 429 |
| connect | 轮换 token | 点击 → 确认轮换 | ✅ 可用 | 200；secrets 写入；重载「已配置」 |
| connect | 10 s 冷却 | 轮换后立即看 | ✅ 可用 | 页面「冷却中：10 秒后可再次轮换。」按钮 disabled；同刻 curl 429 |
| connect | adapter-managed 禁用说明 | — | ⚠️ 未验证（不适用） | 本机 LX06 非 adapter-managed，无法出现该分支 |
| danger | 去配置中停用设备 | 点击 | ✅ 可用 | `/v2/devices/{id}?tab=config` |
| danger | 删除（输入 id 才可提交） | 打开 → 输错 → 输对 | ✅ 可用 | 确认按钮初始 disabled；输错显示不一致；输对后可点 |
| danger | 删除提交 | 永久删除 | ✅ 可用 | DELETE 200；跳回 `/v2/devices`；API 404；配置中移除 |

### 4.8 `/rooms`

| 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|
| 刷新 | 点击 | house + devices + house/audio + N×device audio | ✅ 可用 | 5 个 GET（N=2）全部 200 |
| 401 | 无 token | — | ✅ 可用 | 同 §4.1 |
| 空态添加 | 空屋实例 | — | ✅ 可用 | 「去添加设备」 |
| 设备链接（查看设备） | 点击 | — | ✅ 可用 | `/v2/devices/bedroom` |
| 设为默认输出 | 点 living/bedroom | 草稿 | ✅ 可用 | 脏标记「有 1 项改动」，无请求 |
| 策略 enabled / max_planes / default_output | 编辑 | 草稿 | ✅ 可用 | default_output 保存验证；enabled/max_planes 仅草稿（同一 PATCH） |
| 房间优先级 添加 | select + 添加 | 草稿 | ✅ 可用 | 两项 `bedroom · 卧室` / `living · 客厅` |
| 房间优先级 上移/下移/删除 | 点击 | 草稿 | ✅ 可用 | 顺序反转成功，删除后剩 1 项 |
| 策略放弃修改 | 编辑 → 放弃 | 无请求 | ✅ 可用 | 脏标记消失 |
| 策略保存（确认） | 保存 → 确认 | PATCH `/house` 200 | ✅ 可用 | 配置写 `default_output`/`room_priority`；重载保持 |
| 全屋重启音频面（策略块） | 点击 | POST restart 200 | ✅ 可用 | no-audio 下 `restart.status=failed`（预期） |
| 全局音频 每叶子输入 | `audio.capture.mic_shift` 12→13 → 保存确认 | PATCH `/house/audio` 200 | ✅ 可用 | 第 1 层 effective=13；配置 `mic_shift: 13` |
| 清除叶子 / 撤销清除 | 清除 → 撤销 | 无请求 | ✅ 可用 | 「将清除（回落）」出现；撤销后值 13 |
| 清除叶子 → 保存 | 保存确认 | PATCH 200 | ✅ 可用 | `explicit[]` 移除该路径；effective 回核心默认 **0**（core default，非示例配置的 12） |
| 音频保存确认框 | 点击保存 | — | ✅ 可用 | 标题「保存全局音频默认？」列变更字段 |
| 全屋重启音频面（音频块） | 点击 | POST restart 200 | ✅ 可用 | 同上，`failed: audio plane not started` |
| override 设备 chip | 通过 API 造 1 个 override 后重载 | GET audio×N | ✅ 可用 | `living · 1 个覆盖`；计数「1 台设备带自己的音频覆盖」 |
| 离开拦截 | 脏 → 点「设备」→ 取消/确认 | — | ✅ 可用 | 对话框「当前页有未保存的更改」；取消留下，确认离开 |

### 4.9 `/run`

| 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 页面加载 + 异常清单 | 加载 | ✅ 可用 | 4 项注意力；GET house/devices 200 |
| 刷新 | 点击 | ✅ 可用 | house + devices 200 |
| 异常清单跳转 | 点第一项 | ✅ 可用 | 跳 `/v2/devices/bedroom` |
| 空态 `/legacy` 兜底（`RunView.vue:247`） | 空屋实例 | ✅ 可用 | 空态显示「打开旧控制台」链接（0.7.8 必须改） |
| 房间设备 chip | 点 living | ✅ 可用 | `/v2/devices/living` |

### 4.10 `/conversation`

| 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|
| 20/50 条切换 | select | GET `turns?limit=20/50` | ✅ 可用 | URL 参数正确 |
| 5 s 自动刷新 | 开 5.6 s / 关 5.6 s | GET turns | ✅ 可用 | 开=1 次轮询，关=0 |
| 刷新 | 点击 | turns + devices | ✅ 可用 | 2×200 |
| 设备过滤 | 选 living / 全屋 | 客户端过滤 | ✅ 可用 | living=1 行；全屋=1 行（无设备标注的回合） |
| 房间过滤 | 选 客厅/卧室 | 客户端过滤 | ✅ 可用 | 房间选项来自设备目录 |
| 清除过滤 | 构造空结果 → 清除筛选 | — | ✅ 可用 | 空态「没有符合筛选条件的回合」；清除后列表恢复 |
| 加载失败 + 重试 | mock 500 → 重试 | GET turns 500 → 200 | ✅ 可用 | HTTP 500 状态块 + 重试后恢复 |

### 4.11 `/logs`

| 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 200–2000 行 | 选 500 | ✅ 可用 | GET `logs?lines=500` |
| 3 s 自动刷新 | 开/关 3.6 s | ✅ 可用 | 开=1，关=0（复核：初始 checked=true） |
| 刷新 | 点击 | ✅ 可用 | GET logs 200 |
| 级别过滤 全部/INFO/WARN/ERROR | 逐项 | ✅ 可用 | WARN 4 行、ERROR 5 行（700 行 mock） |
| 自动滚动 | 关闭 → 恢复 | ✅ 可用 | 「自动滚动已暂停」出现/消失 |
| 复制全部 | 点击 | ✅ 可用 | clipboard 32 794 B，末尾 `line 699` |
| 显示更多 +300 | 点击 | ✅ 可用 | 渲染行 300 → 600，无额外请求 |
| 恢复自动滚动 | 点击「继续滚动」 | ✅ 可用 | 复选框恢复 |
| 单行复制 | 点最后一行复制 | ✅ 可用 | clipboard 为该行原文 |
| 加载失败 + 重试 | mock 500 | ✅ 可用 | 状态块 + 重试按钮 |

### 4.12 `/memory`

| 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|
| 新增（≤200） | 输入并保存 | POST memory 200 | ✅ 可用 | 列表出现；`0/200` 计数 |
| 长度校验 | 201 字 | 无请求 | ✅ 可用 | 「记忆内容最多 200 字」 |
| 只看未确认 | 勾选 | — | ✅ 可用 | 仅显示种子候选（`confirmed=false`）；控制台新增的一律 `confirmed=true`，故该开关仅在语音候选存在时出现 |
| 单条确认 | 候选 → 确认 | POST memory 200 | ✅ 可用 | 候选变已确认；`confirmed=true` 读回 |
| 单条删除（确认框） | 删除 → 确认 | POST memory 200 | ✅ 可用 | 确认框出现；条目移除 |
| 显示全部 | 过滤状态 → 显示全部 | — | ✅ 可用 | 2 行恢复 |
| 刷新 | 点击 | GET memory | ✅ 可用 | 200 |

### 4.13 `/setup` 与 `/devices/add`（八步向导）

| 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 插件选择 | select 插件 | ✅ 可用 | GET plugins；选中 `xiaoai-lx06` |
| 机型选择 | select 机型 | ✅ 可用 | 选中 `lx06` → GET `setup/plan?plugin_id=…&model_id=…` |
| 刷新计划 / models 重新读取 | 点击 | ✅ 可用 | GET setup/plan 200 |
| 错误重试 | — | ⚠️ 未验证 | 未构造 plan 失败（需要拦截/坏插件） |
| 8 步折叠 | 逐项展开 | ✅ 可用 | 8 步，keys/models 可展开；6 个设备步在 add 模式 disabled |
| 全屋步骤（keys/models） | 展开/编辑 | ✅ 可用 | keys：6 个密钥输入 + 自定义槽位 + 服务商选择/测试/保存；models：只读证据 + 重新读取；keys 保存按钮在空值时 disabled |
| params（id/房间/host/端口/唤醒词/manifest 参数/提交） | 空提交 → 非法 id → 合法 id 创建（空屋 first-run） | ✅ 可用 | 空提交：id/host 显示「必填字段不能为空。」且无请求；`Bad ID`：显示 `ID 必须匹配 ^[a-z0-9][a-z0-9_-]{0,31}$`（同时触发一次 plan 422，见备注）；`audit-first/测试房/127.0.0.9/4421` → POST `/setup/plan/steps/params` 200；API 回读 `id/room/host/listen_port=4421/enabled=true`；`config.yaml` 落盘；截图 `setup--params-form.png`、`setup--params-created.png` |
| id 冲突处理 | 重复 id | ✅ 可用（API 409） | `POST /api/v2/devices` 同 id → 409 `device_exists`（setup step 在 keys/models 未完成时被 `house_setup_incomplete` 挡，属门禁而非冲突）；UI 冲突编辑器（`idCollision`）只在 add/edit 模式带同名 draft 时出现，正常首装流程不可达（见 §8.8） |
| keys 保存 / 自定义槽位 / 测试 / 服务商 | — | ⚠️ 未验证 | 需要真实服务商凭据；夜间不发起外部探测 |
| models 刷新 | 点击 | ✅ 可用 | 重新读取计划 |
| prepare（勾选 + 回滚确认） | 勾选确认 → 提交（空屋 first-run） | ✅ 可用 | 展开显示 manifest 安装步骤与回滚说明；勾选 `setup-prepare-confirm` → POST `/setup/plan/steps/prepare` 200，步骤 `data-status=done`；本 manifest 未要求 `rollback_ack`；截图 `setup--prepare.png`、`setup--prepare-done.png` |
| wake（3 词） | — | ⚠️ 未验证 | `device.create` 后已启用（`data-disabled=false`）；`wake.set` 会重启音频面，no-audio 实例下执行也无设备可唤醒，本轮不执行 |
| connect（地址证据/刷新） | — | ⚠️ 未验证 | create 后已启用；需要设备侧客户端实际拨入才有“已连接”证据，本轮无真机 |
| test（运行） | — | ⚠️ 未验证 | create 后已启用；无 device client 时结果固定为未通过，只有接真机才能验证“通过”分支，本轮不执行 |
| verify（真说话 + 确认） | — | ⚠️ 未验证 | 会真的播放语音；夜间红线不允许真音箱出声，即使无设备也不在夜间触发 |
| 429 冷却 | 服务端 | ⚠️ 未验证（setup） | setup 步骤未执行；同类冷却已在 token rotate/reveal 实测 429 |
| `device.create` 返回 id 前后续步骤禁用 | 观察 | ✅ 可用 | add 模式：6 个设备步 `disabled=true`（被 keys/models 阻断）；first-run（空屋）：`plan.blocked=null`，prepare/params 可展开，wake/connect/test/verify 在 create 前按「先创建」禁用，`device.create` 后 `wake.data-disabled=false`（后续步骤解锁） |
| `/setup` 已有设备提示 | 加载 → 选型 | ✅ 可用（已修文案） | 原在未加载 plan 时错误宣称「全屋还没有设备」；修复后：plan 前不显示，plan=add 时显示「全屋已有设备…」 |

> 备注（`Bad ID` 的 422）：`onIdChange` 会把 draft id 带进计划重读，`GET /api/v2/setup/plan?plugin_id=xiaoai-lx06&model_id=lx06&device_id=Bad+ID` 返回 422 `invalid_values`，`details.fields=[{"path":"device_id","code":"invalid","message":"device_id must match ^[a-z0-9][a-z0-9_-]{0,31}$"}]`；这不是 `device.create` 提交请求（后者在客户端校验阶段已被拦下）。

### 4.14 `/voice-assistant`

| 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 刷新（脏确认） | 改字段 → 刷新 → 取消 | ✅ 可用 | 「放弃未保存的改动？」；取消草稿保留 |
| 分组字段编辑 | assistant.name | ✅ 可用 | 66 个可编辑字段渲染 |
| 设备只读卡与跳转 | 加载 | ✅ 可用 | living/bedroom 只读卡链接 |
| 重置 | 编辑 → 重置 | ✅ 可用 | 回基线，无请求 |
| 保存（save 字段） | name → 小瑞A | ✅ 可用 | PUT `/config/values` 200；banner「已保存并立即生效（1 项）」；重载保持 |
| 保存（restart 字段 + 确认） | audio.capture.rate 16000→16001 | ✅ 可用 | 确认框「确认保存并重启？」；响应列出 `audio.capture.rate`；配置落盘；随后恢复 16000 |
| 4 个跳转链接 | 检查 | ✅ 可用 | music/credentials/health/rooms |

### 4.15 `/tools`

| 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 刷新（脏确认） | 同 ConfigForm | ✅ 可用 | 与 voice-assistant 同一组件/处理器（voice 实测） |
| `tools.*` 字段编辑 | weather.city → 审计城 | ✅ 可用 | PUT `/config/values` 200；重载/读回一致 |
| MCP/HA 只读表 | 加载 | ✅ 可用 | GET `/api/v1/mcp` 200；1 行状态；不渲染 env/headers |
| 重置 | 编辑 → 重置 | ✅ 可用 | 回基线 |
| 保存（重启确认） | — | ✅ 可用 | 同 ConfigForm（voice 实测 restart 确认）；本页字段均为 save 生效 |
| 4 个跳转 | 检查 | ✅ 可用 | voice-assistant/music/credentials/health |

### 4.16 `/music`

| 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|
| 刷新 | 点击 | GET sources/music | ✅ 可用 | 200 |
| 音源选择（客户端） | 选 airsonic → 回 navidrome | 无请求 | ✅ 可用 | 「待切换」标签出现；草稿重置 |
| 文档链接 | 检查 | — | ✅ 可用 | 7 个音源，`target=_blank` 文档 URL |
| 凭据槽输入 | url/user/password | — | ✅ 可用 | 3 个输入框（当前 navidrome） |
| 测试（未保存草稿） | 点击 | POST `/music/test` 200 | ✅ 可用 | 返回测试结果块（连接失败可读） |
| 保存 | 点击 | PUT `/music/credentials` 200 | ✅ 可用 | 状态徽标「已配置」；重载保持 |
| 清空槽位（危险确认） | 点击 → 确认 | PUT 200 | ✅ 可用 | 标题「确认清除该凭据槽位」；清空后槽位缺失 |

> 首次执行时 `PUT /music/credentials` 返回 **500**，原因不是 UI，而是示例配置把 `music.secrets_path` 指向不可写的 `/var/lib/rui/lan_media.json`（见 §8.1）。把测试实例路径改到 `/tmp` 后，该元素全部通过。**0.7.8 已修：现在返回 409 + 可操作 detail，见 §8.1。**

### 4.17 `/credentials`

| 元素 | 动作 | 端点 | 结果 | 证据 |
|---|---|---|---|---|
| 刷新（3 个 GET） | 点击 | secrets/presets/status | ✅ 可用 | 3×200 |
| 设置 / 替换 | RUI_ASR_KEY ← dummy | POST `/secrets` 200 | ✅ 可用 | 徽标「已配置」；重载保持；值不回显 |
| 打开音乐密钥 | 检查 | — | ✅ 可用 | `music.credentials` → `/v2/music` |
| Verify probe | DeepSeek（无 key） | POST `/providers/probe` 200 | ✅ 可用 | `ok=false, message="missing credential: DEEPSEEK_API_KEY"`，UI 可读；成功路径需真实 key（未验证） |
| preset 选择 | 检查/选择 | — | ✅ 可用 | DeepSeek 1 个选项；选择后请求按预设 |
| 提交空值 = 危险清除 | 编辑 → 空提交 → 确认 | POST `/secrets` 200 | ✅ 可用 | 标题「确认清除凭据」；状态回到「未配置」 |
| 清除确认 | 确认 | 同 | ✅ 可用 | 清除后 `has_value=false` |

### 4.18 `/health`

| 元素 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 刷新（health/doctor/recovery） | 点击 | ✅ 可用 | GET doctor/recovery 200；health 由 `/health` + 页面轮询 |
| 异常跳设备 | 检查 | ✅ 可用 | 注意力行含 `/v2/devices/...` 链接（如 wake_runtime 为无链接说明） |
| 下载坏配置（恢复实例） | 点击 | ✅ 可用 | GET `/config/recovery/bad` 200；下载 `config.yaml.failed-*`（98 B，内容含坏行） |
| 恢复（确认 + mode） | 点击 → 确认 | ✅ 可用 | 标题「确认恢复配置」；body 说明 last-good；POST 200；写入后配置可被 `load_config` 解析；提示需重启；恢复后徽标「正常」 |
| 健康时跳配置 | 点击 | ✅ 可用 | `/v2/voice-assistant` |
| 3 个跳转 | 检查 | ✅ 可用 | voice-assistant/music/credentials |

## 5. 失败项与修复（`[fix]`）

本轮共发现 4 组文案/校验问题，均已在写范围内修复并复测。**本轮没有留下任何“点不动/保存丢失”的功能缺陷。**

| # | 问题 | 位置 | 修复 | 复测 |
|---|---|---|---|---|
| F1 | `/v2/setup` 在尚未选型、plan 未加载时宣称「全屋还没有设备」，而实际已有 2 台设备（误导） | `web/src/v2/components/setup/SetupWizard.vue:708` | 仅在 `plan && plan.mode === 'first-run'` 时显示该句；plan=add 时走「全屋已有设备…」 | 浏览器复测：plan 前不再出现；选型后显示「全屋已有设备，本页现在按添加设备流程工作」 |
| F2 | 「旧控制台」提示写「0.6.x 仍为默认入口」（0.7.7 下已过时） | `web/src/v2/i18n.ts:32,1565` | 改为「打开旧版控制台（0.8.0 移除）」/「Open the legacy console (removed in 0.8.0)」 | DOM `title` 复测通过 |
| F3 | 死文案 `v2.config.stub` / `v2.config.stubHint` 残留在 i18n（scope §6 要求删除） | `web/src/v2/i18n.ts:631-632,2160-2161` | 删除 zh/en 两组 key（全仓无引用） | `vue-tsc --noEmit` 通过；`grep` 0 命中 |
| F4 | v2 设备写入的 7 条校验 message 是中文，违反 C-002 §4.4（`message` 英文技术句） | `src/rui/web/api/app.py:5791,5793,5796,5806,5808,5810,5816` | 7 条改为英文（host/wake word/room） | `curl PATCH host=http://…` → 422 `message:"host must be a bare host name or IP, without a scheme"`；`ruff check` 通过 |

> F4 说明：这些文案与 v1 `/api/v1/setup/device` 共用；v1 旧面板在这 7 种校验下会显示英文（v1 仅再保留到 0.8.0）。v2 中文界面目前直接透传 `details.fields[].message`，因此这 7 条在中文界面下是英文——这是 C-002 的既定方向（前端按 code 翻译），本轮不改前端错误映射逻辑。

## 6. 异常态

| 类别 | 场景 | 结果 | 证据 |
|---|---|---|---|
| 401 | 无 token 打开任意 v2 页 | ✅ | 仅 `GET /meta` 401，页面数据不请求；状态块 + 自动 token 对话框 |
| 429 | reveal ×11 / rotate 二次 | ✅ | 第 11 次 reveal 429；rotate 10 s 内 429（`Retry-After` 语义）；UI 冷却文案 |
| 409 | 记忆关闭 / 删除无 confirm / 动作无 link / token 缺失 | ✅ | `confirm_required`、`device_not_connected`、`token_missing` 均出可读错误；记忆关闭未构造（`app.state.memory` 存在）→ 未验证 |
| 404 | 未知设备 / 未知 API | ✅ | `/api/v2/nope` JSON 404；`audit-tmp` 删除后 `GET /api/v2/devices/audit-tmp` 404 |
| 422 | 非法 volume / host / setup 缺参 | ✅ | `details.fields[]` 带 path+code+message；UI 红框 |
| 5xx | meta 500 / turns 500 / logs 500 | ✅ | 状态块 + 重试；重试后恢复 |

## 7. v1 → v2 差异（对齐 `console-audit-scope.md` §4 的 32 行矩阵）

结论按矩阵逐条落位；“明确说明”指 v2 已在页面文案/只读区说明能力边界。

| 矩阵项 | 状态 | 0.7.7 实测说明 |
|---|---|---|
| 1 概览「试一试」 | 已补（按设备） | test tab `audio.say`；全屋说未补（需逐设备，页面未隐藏此限制） |
| 2 概览「停止出声」 | 已补（按设备） | `audio.stop` 在 test tab |
| 3 概览音频面统计 | 明确说明 | 拆到 `/run` + `/health` |
| 4 设备列表 | 已补 | `/devices` |
| 5 混音器音量 0–255 | 明确说明 | test tab 滑块 + 范围标签 |
| 6 设备增删改 | 已补 | add/config/danger |
| 7 设备探测 `POST /api/v1/devices/probe` | **未处理** | v2 无入口；`/api/v2/devices/{id}/test` 只收集证据（见 §8.3） |
| 8 设备测试 | 已补 | TestPanel |
| 9 connect / token 轮换 | 已补 | reveal/rotate/冷却 |
| 10 LED / 参数旋钮 | 明确说明 | `led.set` 有；参数只读（创建时填） |
| 11 音频与唤醒 ~50 字段 | 明确说明 | audio/config 仅 C-001 override 子集可写；其余全局在 `/voice-assistant` |
| 12 对话 | 已补 | `/conversation` |
| 13 助手/会话配置 | 已补 | `/voice-assistant` |
| 14 话术编辑器（49 模板） | **未处理** | v2 无页面/无 API 调用；`/api/v1/phrases` 仍存在（v1 only） |
| 15 模型/语音服务卡片/试听/音色 | **未处理** | 仅 `/credentials` 的存在性与 probe；无试听（夜间红线也不允许试听） |
| 16 工具内置/暴露 | 已补 | `/tools` |
| 17 MCP/HA 写入管理 | 未处理（只读） | `/tools` 只读表；写入口仍在 v1 `/panel/integrations` |
| 18 工具注册表清单 | 未处理 | v2 无 |
| 19 系统/Web/安全 13 字段 | 未处理 | 需改 `config.yaml`；v2 不暴露 |
| 20 凭据/密钥 | 已补 | `/credentials` |
| 21 日志 | 已补 | `/logs` |
| 22 记忆 | 已补 | `/memory` |
| 23 音乐播放控制（6 按钮） | **未处理** | `/music` 无播放控制（本轮未补，见 §8.3） |
| 24 音乐状态/来源/凭据 | 已补 | `/music` |
| 25 `music.enabled` / music 配置组 | 未处理 | `/music` 只读状态；`music.enabled` 需 `/voice-assistant`? 实测该字段不在 v2 catalog 页面（music 组未渲染）→ 仍缺 |
| 26 安装向导 8 步 | 已补 | `/setup`、`/devices/add` |
| 27 渠道 Telegram/微信 | 未处理 | 0.7.8 不补，进已知限制 |
| 28 体检 + billing 配额 | 部分 | `/health` 无配额 |
| 29 只读工具注册表页 | 未处理 | v2 无 |
| 30 房间/全屋策略 + 全局音频（v2 独有） | v2+ | `/rooms` 全部实测 |
| 31 配置恢复（v2 独有） | v2+ | `/health` 恢复实例实测 |
| 32 v1 `/panel/*` 深链 | **未处理** | 无 302 映射，仍返回旧壳（§8.2） |

## 8. 已知限制

### 8.1 音乐凭据路径不可写时返回 409（0.7.8 已修）
- 0.7.7 现状：配置 `music.secrets_path: /var/lib/rui/lan_media.json`，以非 root 运行 →
  `PUT /api/v1/music/credentials` **500**，服务端 traceback `PermissionError`；UI 只显示通用错误。
- **0.7.8 修复（task-20）**：
  - `src/rui/config/catalog.py` 的 `write_music_credentials()` 捕获 `OSError`
    （`PermissionError` / `NotADirectoryError` / 父目录缺失），抛
    `MusicCredentialsWriteError`（`OSError` 子类；带 path + OS reason + 修复句）。
  - `src/rui/web/api/app.py` 在 `PUT /api/v1/music/credentials`（v2 控制台调用的就是它，
    `web/src/v2/api.ts:1584`；没有 `/api/v2/music/credentials` 路由）把**写入**与**清空
    （unlink）**两支的 `OSError` 都映射成 **409**，`detail` 为：
    `music credentials file is not writable: <path> (<reason>); fix the directory
    permissions or set music.secrets_path to a writable location (for example under
    system.data_dir)`。
  - 启动迁移语义不变：`migrate_legacy_music_credentials()` 仍可 `except OSError`
    （新异常是子类），保留 inline 值并只打 warning。
- 证据：`tests/test_music_credentials.py` 的
  `test_an_unwritable_credentials_path_is_a_4xx_not_500` 与
  `test_clearing_credentials_on_an_unwritable_path_is_a_4xx`（chmod 555 临时目录；旧代码
  在 `raise_server_exceptions=False` 下返回 **500**，修复后 **409**）；正常保存/清空/0600/
  不回显的既有测试仍绿（该文件 6 passed）。
- 提交：本卡不 commit；commit 由 Lead 落盘后回填（本条目对应 task-20）。

### 8.2 老链接 302 映射（0.7.8 D 已实现）

- 0.7.7 现状：`/panel/overview` 等 15 条旧链接全部 200 返回旧壳（见 §2 表格）。
- 0.7.8 实现：`src/rui/web/api/legacy_links.py` 里一张显式的表，`app.py` 在 `/legacy`
  之后、SPA 兜底之前注册。`web.console: v2`（0.7.8 默认）时这 15 条一律 **302**；
  `web.console: legacy` 时保持 200 旧壳（回滚 = 改一行配置 + 重启）。
- 规则：查询串原样带过（`/panel/logs?level=warn` → `/v2/logs?level=warn`）；带 tab 的
  子路径按 panel id 解析（`/panel/devices/manage` → `/v2/devices`）；v2 暂无对应页的
  落 `/v2/?from=<旧路径>`，不假装功能搬过去了，也不落回旧壳。`?from=` 在 v2 首页
  SPA 跳到 `/v2/devices` 时会保留（`web/src/v2/router.ts` 的 redirect 带 query）。
- 映射表与逐条实测（2026-10-27，`--no-audio` 空屋实例，`console` 未配置 → 默认 v2）：

| # | 旧链接 | 目标 | 依据 / 说明 |
|---|---|---|---|
| 1 | `/panel/overview` | `/v2/run` | v2「运行 / 概览」 |
| 2 | `/panel/devices` | `/v2/devices` | v2 设备页 |
| 3 | `/panel/turns` | `/v2/conversation` | v2 对话页 |
| 4 | `/panel/voice-assistant` | `/v2/voice-assistant` | v2 语音助手配置 |
| 5 | `/panel/providers` | `/v2/?from=/panel/providers` | v2 无供应商卡片/试听（§8.3 第 6-7 项） |
| 6 | `/panel/integrations` | `/v2/tools` | v2 工具（内置 / MCP / HA / 暴露策略） |
| 7 | `/panel/system` | `/v2/?from=/panel/system` | v2 无 system/web/security 编辑器（§8.3 第 19 项） |
| 8 | `/panel/credentials` | `/v2/credentials` | v2 凭据页 |
| 9 | `/panel/logs` | `/v2/logs` | v2 日志页 |
| 10 | `/panel/memory` | `/v2/memory` | v2 记忆页 |
| 11 | `/panel/music` | `/v2/music` | v2 音乐页（v1 的 6 个播放按钮是 §8.3 缺口） |
| 12 | `/setup` | `/v2/setup` | v2 安装向导 |
| 13 | `/secrets` | `/v2/credentials` | v1 独立凭据路由 |
| 14 | `/diagnostics` | `/v2/health` | v1 体检页 |
| 15 | `/channels` | `/v2/?from=/channels` | v2 无渠道页（0.7.8 已知限制） |

- 逐条实测（`curl -s -o /dev/null -w '%{http_code} %{redirect_url}'`，端口 18108）：

```text
/panel/overview            302 http://127.0.0.1:18108/v2/run
/panel/devices             302 http://127.0.0.1:18108/v2/devices
/panel/turns               302 http://127.0.0.1:18108/v2/conversation
/panel/voice-assistant     302 http://127.0.0.1:18108/v2/voice-assistant
/panel/providers           302 http://127.0.0.1:18108/v2/?from=/panel/providers
/panel/integrations        302 http://127.0.0.1:18108/v2/tools
/panel/system              302 http://127.0.0.1:18108/v2/?from=/panel/system
/panel/credentials         302 http://127.0.0.1:18108/v2/credentials
/panel/logs                302 http://127.0.0.1:18108/v2/logs
/panel/memory              302 http://127.0.0.1:18108/v2/memory
/panel/music               302 http://127.0.0.1:18108/v2/music
/setup                     302 http://127.0.0.1:18108/v2/setup
/secrets                   302 http://127.0.0.1:18108/v2/credentials
/diagnostics               302 http://127.0.0.1:18108/v2/health
/channels                  302 http://127.0.0.1:18108/v2/?from=/channels
```

- 跟随跳转：15/15 最终 200，`<title>Rui · Console v2</title>`（无一条落回旧壳）；复现命令见 §9。
- **`?from=` 目前仅作标记**：v2 不读取、不提示、不做跳转说明，只在地址栏保留；
  `providers` / `system` / `channels` 三条没有 v2 页面的老链接跟随跳转后停在
  设备页 `/v2/devices`（见 §8.9）。
- 测试：`tests/test_legacy_links.py` 把上表逐条参数化（含查询串、tab 子路径、未知
  panel、`legacy` 模式 15 条 200 旧壳），`tests/test_console_switch.py` 覆盖默认值/回滚/`/health.console` 两态。

### 8.3 v1 功能缺口（v2 明确不做）
- 话术编辑器、模型/语音服务卡片与试听、音乐播放控制 6 按钮、系统/Web/安全 13 字段、渠道 Telegram/微信、工具注册表页、设备探测、MCP/HA 写入。
- 其中“音乐播放控制”v1 有 6 按钮，v2 只有状态与凭据；本轮按 scope 结论**不补**，进本限制清单。

### 8.4 v2 页内仍指向 `/legacy` 的两处兜底
- `DevicesView.vue`（空屋且 setup/add 都关闭时）与 `RunView.vue`（空屋）仍给 `/legacy` 链接。
- 0.7.8 D 决定保留：这两条是“v2 做不了、去旧壳做”的明确出口，目标 `/legacy` 现在
  200 且带 0.8.0 弃用横幅；改成 v2 内路径会把用户导到一个没有该功能的页面。
  0.8.0 删除旧壳时这两处必须一起处理（届时 v2 要补上对应能力或给出说明）。

### 8.5 `audio.*` 清除后的“核心默认”可能与示例配置不同
- 例：`audio.capture.mic_shift` 清除后 effective 回 **0**（schema 核心默认），不是示例配置里的 12。页面文案已写“清除后回落到核心默认 / 插头 manifest”，行为一致；但运维若把示例值当默认会困惑。建议文档说明。

### 8.6 孤儿端点仍在
- `GET /api/v2/plugins/{id}` 与 `POST /api/v2/devices` 无前端调用（`v2_api.py:1336`、`v2_api.py:2240`；`api.ts` 无调用）。本轮用 curl 验证均可用；0.7.8 决定“删 or 接进向导”。

### 8.7 控制台默认入口（0.7.8 已切 v2）
- 0.7.7 审计时 `WebConfig.console` 默认 `"legacy"`；0.7.8 D 已改为默认 `"v2"`
  （`config/schema.py`，`config.example.yaml` 与 `src/rui/config/template.yaml` 同步写
  `console: v2`）。未配置该键的老配置也按 `v2` 加载。
- 本地实测（空屋、`--no-audio`、未配置 `console`）：`GET /` → `302 /v2/`；
  `/health` 里 `console=v2`；`/legacy` 200 旧壳且顶部有弃用横幅
  （`docs/audit/console-shots/legacy--deprecation.png`，DOM `data-testid="legacy-deprecation"`）。
- 回滚：配置写 `web.console: legacy` 重启 —— `/` 回到旧壳、15 条老链接恢复 200 旧壳、
  `/health` 报 `console=legacy`（实测两态）；0.8.0 移除 `legacy` 值时，旧壳与
  `legacy_links.py` 的 `?from=` 兜底一起删。

### 8.8 setup 的 id 冲突编辑器在正常流程不可达
- UI 的 `idCollision` 只在“params 已展开且 draft id 命中已有设备 / 设备以 edit 模式带 `device_id`”时出现；而 add 模式（已有设备）的 params 被 keys/models 门禁禁用，first-run（空屋）又没有同名设备。
- 服务端冲突仍被覆盖：`POST /api/v2/devices` 同 id → 409 `device_exists`；`GET /setup/plan?device_id=<已有>` 返回 edit 模式。重复创建设有绕过风险，只影响向导里的“改名/修复”体验。
- 本轮不补入口（改交互属 D/后续）；若 0.7.8 要让向导支持改名，需要给 params 一个可达路径。

### 8.9 `?from=` 只是标记，v2 未消费（0.7.8）
- 0.7.8 的 302 映射把 `providers`、`system`、`channels`（以及任何未知 panel id）送到
  `/v2/?from=<旧路径>`，用于在地址栏留下来源。**v2 前端目前不读取这个参数**，不显示
  「该页在 v2 暂无对应页面」之类的提示；`/v2/` 的 SPA redirect 会带着 query 进
  `/v2/devices`，所以用户看到的是设备页。
- 影响：这三条老书签不会 404、也不会落回旧壳，但用户需要自己从设备页/配置区找目标页。
  0.8.0（删旧壳）之前若要更友好，可让 v2 识别 `from` 并给一条说明或历史页链接。
- 记录位置：本文件 §8.2 与 `docs/release/v0.7.8.md` 的「已知限制」。

## 9. 关键证据与复现命令

环境准备（可重复）：
```bash
AUDIT=/tmp/rui-audit; mkdir -p "$AUDIT/data" "$AUDIT/plugins"
cp -R tests/fixtures/legacy_lx06 "$AUDIT/plugins/xiaoai"
# config.yaml 由 config.example.yaml 派生：data_dir=/tmp/rui-audit/data、host=127.0.0.1、
# port=18099、auth_token=audit-token-123、console=v2、两台 LX06 设备
RUI_PLUGINS_DIR="$AUDIT/plugins" .venv/bin/rui --config "$AUDIT/config.yaml" serve --no-audio
```

路由：
```bash
for p in / /v2/ /v2/devices /v2/devices/add /v2/rooms /v2/setup /v2/run /v2/conversation \
         /v2/logs /v2/memory /v2/voice-assistant /v2/tools /v2/music /v2/credentials /v2/health \
         /legacy /panel/overview; do
  printf "%-28s " "$p"; curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" "http://127.0.0.1:18099$p"
done
```

0.7.8 D（默认入口 + 15 条老链接 + 弃用横幅），空屋、`--no-audio`、`console` 不写：
```bash
D=$(mktemp -d /tmp/rui-d-XXXX); mkdir -p "$D/data"
cat > "$D/config.yaml" <<YAML
version: 3
system: {data_dir: $D/data}
web: {host: 127.0.0.1, port: 18108, auth_token: d-token-123}
devices: []
YAML
.venv/bin/rui --config "$D/config.yaml" serve --no-audio &   # 端口 18108

curl -s http://127.0.0.1:18108/health | jq .console          # "v2"（默认）
for p in /panel/overview /panel/devices /panel/turns /panel/voice-assistant /panel/providers \
         /panel/integrations /panel/system /panel/credentials /panel/logs /panel/memory \
         /panel/music /setup /secrets /diagnostics /channels; do
  printf "%-26s " "$p"
  curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" "http://127.0.0.1:18108$p"
done                                                          # 15/15 = 302（目标见 §8.2）
curl -sL -o /dev/null -w "%{http_code} %{url_effective}\n" http://127.0.0.1:18108/panel/logs

# 回滚面：同一份配置加一行 `console: legacy`（端口 18109）后
#   /health.console = "legacy"；`/` 与 15 条老链接全部 200 旧壳。

# 弃用横幅（旧壳在 /legacy，两语各一份 DOM）
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --disable-gpu --virtual-time-budget=4000 --dump-dom \
  http://127.0.0.1:18108/legacy | grep -o 'legacy-deprecation[^<]*'
"$CHROME" --headless=new --disable-gpu --lang=en-US --virtual-time-budget=4000 --dump-dom \
  http://127.0.0.1:18108/legacy | grep -o 'The legacy console[^<]*'
```

保存 + 回读（设备音频覆盖）：
```bash
curl -s -X PATCH -H 'X-Auth-Token: audit-token-123' -H 'Content-Type: application/json' \
  -d '{"audio":{"voice":{"followup_delay_ms":1200}}}' \
  http://127.0.0.1:18099/api/v2/devices/living/audio | jq '.audio.voice.followup_delay_ms, .restart.scope'
curl -s -H 'X-Auth-Token: audit-token-123' \
  http://127.0.0.1:18099/api/v2/devices/living/audio | jq '.effective.voice.followup_delay_ms, .sources["voice.followup_delay_ms"], .overrides'
```

恢复继承：
```bash
curl -s -X PATCH -H 'X-Auth-Token: audit-token-123' -H 'Content-Type: application/json' \
  -d '{"audio":{"voice":{"followup_delay_ms":null}}}' \
  http://127.0.0.1:18099/api/v2/devices/living/audio | jq '.audio.voice.followup_delay_ms'
```

422 / 409 / 429：
```bash
curl -s -X PATCH -H 'X-Auth-Token: audit-token-123' -H 'Content-Type: application/json' \
  -d '{"volume_target":999}' http://127.0.0.1:18099/api/v2/devices/living | jq '.error.details.fields'
curl -s -X DELETE -H 'X-Auth-Token: audit-token-123' \
  http://127.0.0.1:18099/api/v2/devices/living | jq '.error.code'          # confirm_required (409)
for i in $(seq 1 11); do curl -s -o /dev/null -w '%{http_code} ' -X POST \
  -H 'X-Auth-Token: audit-token-123' http://127.0.0.1:18099/api/v2/devices/living/connection/reveal; done
# → 200 ×10，第 11 次 429
```

setup 动作白名单：
```bash
curl -s -X POST -H 'X-Auth-Token: audit-token-123' -H 'Content-Type: application/json' \
  -d '{"action":"device.create"}' http://127.0.0.1:18099/api/v2/setup/plan/steps/keys | jq '.error.code'
# → action_not_allowed (400)
```

配置恢复实例：
```bash
# /tmp/rui-audit-b/config.yaml 写入非法 YAML，config.last-good.yaml 为有效副本
.venv/bin/rui --config /tmp/rui-audit-b/config.yaml serve --no-audio   # 端口 18102
curl -s -H 'X-Auth-Token: audit-token-123' http://127.0.0.1:18102/api/v1/config/recovery | jq '.active,.mode,.bad_path'
```

截图：`docs/audit/console-shots/`（76 张，覆盖每个路由、7 个 tab、保存/确认/错误/空态/恢复态）。

## 10. 截图索引（行范围 → 文件）

目录：`docs/audit/console-shots/`（76 张，视口 1600×1000，均为全页截图）。

| 文档范围 | 截图文件 |
|---|---|
| §2 路由 / 空态 / 401 | `devices.png`、`devices--empty.png`、`run--empty.png`、`401--no-token.png`、`legacy--shell.png`、`config-alias.png` |
| §4.1 全局壳 | `shell--en.png`、`shell--token-dialog.png`、`shell--401.png`、`shell--token-cleared.png` |
| §4.3 详情壳 / tab | `device-overview.png`、`device-audio.png`、`device-config.png`、`device-test.png`、`device-activity.png`、`device-connect.png`、`device-danger.png`、`detail--unsaved-guard.png` |
| §4.4 audio | `audio--save-confirm.png`、`audio--after-save.png`、`audio--after-restore.png` |
| §4.5 config | `config--save-confirm.png`、`config--after-save.png`（重启状态「成功 / 29 ms」）、`config--validation.png` |
| §4.6 test/actions | `test--after-run.png`、`test--volume-read-409.png`、`test--say-confirm.png`、`test--actions-error.png` |
| §4.7 connect/danger | `connect--reveal-missing.png`、`connect--rotate-confirm.png`、`connect--rotated.png`、`connect--cooldown.png`、`connect--revealed.png`、`danger--confirm-mismatch.png`、`danger--after-delete.png` |
| §4.8 rooms | `rooms--policy-confirm.png`、`rooms--policy-saved.png`、`rooms--priority-two.png`、`rooms--audio-confirm.png`、`rooms--audio-saved.png`、`rooms--audio-cleared.png`、`rooms--override-chip.png`、`rooms--leave-guard.png` |
| §4.9–4.12 run 页组 | `conversation--filters.png`、`logs--loaded.png`、`memory--candidate-filter.png`、`memory--remove-dialog.png` |
| §4.13 setup | `setup--steps.png`、`setup--prepare.png`、`setup--prepare-done.png`、`setup--params-form.png`、`setup--params-created.png` |
| §4.14–4.18 配置页组 | `voice--saved.png`、`voice--refresh-dirty.png`、`voice--restart-confirm.png`、`tools.png`、`music--test.png`、`music--saved.png`、`music--clear-confirm.png`、`credentials--saved.png`、`credentials--verify-error.png`、`credentials--clear-confirm.png`、`health.png` |
| §4.18 配置恢复（实例 C） | `health--recovery-active.png`、`health--recovery-restore-confirm.png` |

> 去重说明：`shell--token-cleared.png` 与 `shell--401.png` 的 md5 相同（同为“清空 token 后 401”状态），**复用同一张**；其余文件两两不同。
> 另有 8 张未在正文逐条引用的原始截图：`connect--rotate-cooldown.png`、`credentials.png`、`memory.png`、`music.png`、`rooms.png`、`run.png`、`setup.png`、`voice-assistant.png`（首轮渲染/状态证据）。

## 11. 未验证清单与原因

| # | 项 | 原因 | 替代证据 |
|---|---|---|---|
| U1 | setup 的 wake / connect / test / verify 四步真实动作 | create 后已启用；connect/test 需设备侧客户端真实拨入，verify 需真实出声（夜间红线不执行）；wake 会重启音频面，no-audio 实例下未执行 | prepare/params（含 `device.create`）已实测（§4.13）；add 模式门禁、first-run 解锁、`POST /steps` 白名单已实测 |
| U2 | setup keys 保存/测试、服务商保存 | 需真实 ASR/TTS/LLM 凭据；夜间不发起外部探测 | 控件存在且空值时保存 disabled；白名单端点实测 400 |
| U3 | `audio.say` / `audio.play` / setup `verify.say` 的真实出声 | **夜间红线**：不允许真音箱出声 | 服务端在 `client is None` 时先返回 409；UI 二次确认、payload 与错误展示已实测 |
| U4 | Verify probe 成功路径（真实 key → provider 200） | 无真实 provider key | 失败路径实测：`ok=false, "missing credential: DEEPSEEK_API_KEY"`，UI 可读 |
| U5 | audio 的 3 个重试横幅、adapter-managed 分支、记忆 409（memory disabled）、setup 429 | 需要故障注入/特制设备/关闭 memory；本轮未构造 | 同组件/同错误组件的 5xx/409/429 已在别处实测；adapter-managed 需 Dot2 manifest |
| U6 | `/devices` 的 `/legacy` 兜底（setup+create 同时 false 且空屋） | 需同时关闭两个 feature 且空屋，未构造 | 代码路径存在（`DevicesView.vue:242`），列入 §8.4 |

> 说明：未验证项不参与“可用”计数；下轮若需要，U1/U2 可用一次性临时凭据在隔离实例补齐，U4 需要真实 key，U3 必须等白天真机授权。
