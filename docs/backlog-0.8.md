# 0.8.0 backlog（开工顺序 / 破坏性三项 / 双 dot2 验证组 / 其余堆积）

> **用途**：0.8.0 开工时的第二份必读（第一份是 `docs/HANDOFF-0.8.md`）。本文件只写**要做什么、怎么验、
> 通过标准是什么**；计划与未决问题的权威版本仍是 `docs/HANDOFF-0.8.md` 与 `docs/ROADMAP-0.8.md` §5。
> **维护约定**：只增不删；某条做完就标「已做 @ `<commit>`」并保留原行。
> **引用约定**：一律用 ID / 文件名 / 符号 / commit，**不写会漂移的行号**；不带真实内网地址与凭证。
> **不在这里做的事**：不改代码、不改 CI。本文件是排期与验收口径，不是实现。

---

## a. 0.8 的开工顺序（**不可反**）

### a.1 新会话接手（按序，别凭记忆）

1. 读 `docs/HANDOFF.md` 的**收尾/交接**节（历史流水账，知道 0.7.x 停在哪）；
2. 读 `docs/HANDOFF-0.8.md`（0.8.0 定位、范围、未决问题、出口条件）；
3. 读**本文件**（开工顺序、破坏性细节、双 dot2 验证组、其余堆积）；
4. 读 `docs/acceptance-pending.md`（真机清单；0.8 的验收窗口直接用它，不要另抄一份）；
5. 跑基线再动手：`git status --short`（三仓都应为空）、`git log --oneline -5`、
   `uv run pytest`、`uv run ruff check src tests`；确认当前 HEAD 与要发布的东西一致。

> 任务板不跨会话。**不要**把未完成项只写在对话里；写进本文件或 HANDOFF。

### a.2 三步发布顺序

| # | 谁先动 | 产物 | 判据 | 禁止 |
|---|---|---|---|---|
| 1 | **两个桥**（`rui-dot2-bridge`、`rui-xiaoai-bridge`） | 各发一个**双窗口**版本：`rui_compat` 同时接受 0.7.x 与 0.8.x（形如 `>=0.7.0 <0.9.0`） | 同一个桥包在 **core 0.7.8** 与 **core 0.8.0（预发）** 上都能加载（`rui plug-test <plug> --adapter <adapter.py>:build` 全 PASS），且 `/api/v2/meta` 的 `compat` 窗口两侧都容得下 | 不许把桥的窗口收窄到只接受 0.8.x |
| 2 | **core** | `rui 0.8.0`（含 §b 三项破坏性变更） | 用第 1 步的桥跑通安装路径 + 升级/回滚演练（`docs/deploy-runbook-0.7.8.md` §8 的流程，版本号按 0.8.0 复跑） | 不许在桥没发出双窗口版本前发 core |
| 3 | **文档** | 迁移说明 + breaking notes（中英） | 写清「老配置会怎样报错」「怎么改」；`CHANGELOG` 与 release notes 中英各一份 | 不许把 §b 的删项写成「优化」 |

### a.3 为什么顺序不可反

core 0.8.0 删掉的是**读入期的兼容层**（`src/rui/config/legacy_adapters.py` 整模块 +
`src/rui/config/store.py` 迁移里的改名改写），这会同时拿走两件兜底：

1. 配置写 `adapter: xiaoai` 不再被改写成 `lx06` —— 旧配置在**加载设备时**就失败，不是跑到一半才坏；
2. `DEFAULT_LISTEN_PORT` 不再给「`rui-device/v0` 清单没写 `runtime.listen_port`」的设备兜底 —— 自制插件读入即报错。

反过来说：桥的 `rui_compat` 若上界还停在 `<0.8.0`，core 0.8.0 的加载器会**直接拒绝**这个插件
（README 的「两个约束必须同时成立，否则拒绝而不是半截运行」就是这条）。两种反序的后果一样：
**现网设备全下线，且没有能立刻退回的组合**。先发桥则 0.7.x core 只看到「插件的兼容窗口更宽」，
行为零变化，是最便宜的过渡。

---

## b. 0.8.0 破坏性三项（删什么 / 谁受影响 / 怎么验）

### b.1 删 `/legacy` 面板与全部 15 条老链接 302

- **删的是什么**：`src/rui/web/api/app.py` 的 `/legacy` 与 `/legacy/{full_path}` 路由、旧控制台 shell、
  `src/rui/web/api/legacy_links.py` 的 302 映射表；前端旧面板（v2 早就不读的 `ConfigCenter.vue` /
  `DeviceManager.vue` 一套）。共 **15 条**老链接 = 11 个 `/panel/*` + `/setup`、`/secrets`、
  `/diagnostics`、`/channels`。
- **谁受影响**：把老链接加了书签的人；还在旧面板改配置的运维（已知限制 L8 里「两套渲染并存、
  同一能力两处保存语义不同」随之消失）；`docs/audit/console-ordering.md` 里依赖旧面板的 parity 条目。
- **怎么验**：
  ```bash
  for p in /legacy /legacy/panel/music /legacy/setup /panel/music /panel/devices \
           /setup /secrets /diagnostics /channels; do
    printf '%-24s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE$p")"
  done
  rg -n '/legacy|legacy_links' src/rui web/src
  ```
- **通过标准**：`/legacy` 与 `/legacy/**` 一律 **404**（不是 200、也不是 302）；15 条老链接全部
  **404**（不再 302）；`/` 仍是 v2 单入口；`rg` 在 `src/rui` 与 `web/src` 里只剩测试允许的白名单命中；
  前端构建与 `npm run typecheck` 绿。

### b.2 删 `adapter: xiaoai` 的读入迁移与弃用告警

- **删的是什么**：`src/rui/config/legacy_adapters.py`（`_LEGACY_ADAPTERS`、`DEFAULT_ADAPTER`、
  `DEFAULT_LISTEN_PORT`、`LEGACY_ADAPTER_HINT`）+ `src/rui/config/store.py` 迁移链路里把
  `adapter: xiaoai` 改写成 `lx06` 的那段与它的 WARNING。
- **谁受影响**：配置里仍写 `adapter: xiaoai` 的部署（读入即失败，必须改 `lx06`）；沿用
  `rui-device/v0` 清单、靠 `DEFAULT_LISTEN_PORT` 兜底的自制插件（必须自己写 `runtime.listen_port`）。
  **两个桥本身不受这条迁移影响**：xiaoai 桥按 `lx06` 上报、dot2 桥按 `dot2` 上报
  （设备清单 `rui-dot2-bridge/src/rui_dot2_bridge/devices/rs03qr.yaml` 的 `id: dot2`，插件 id `dot2-echomuse`），
  两者都不经过 `adapter: xiaoai` 这条改写。
- **怎么验**：
  ```bash
  curl -sS "$BASE/health" -H "X-Auth-Token: $TOKEN" | jq '.adapter_plugins'
  rg -n 'legacy_adapters|LEGACY_ADAPTER_HINT|DEFAULT_LISTEN_PORT' src tests
  # 反向：把一份老配置（adapter: xiaoai）喂给 0.8.0，直接起服务看报错
  ```
- **通过标准**：老配置启动时**显式报错**且报错里给出修复句（把 `xiaoai` 改成 `lx06`），不是静默忽略、
  也不是半加载；`/health.adapter_plugins.warnings` 不再因旧拼写出现；`rg` 无源码命中
  （测试里如需保留一条「旧拼写会被拒绝」的回归用例，属测试白名单，不算源码命中）。

### b.3 `rui_compat` 契约窗口调整

- **删的是什么（这里删的是旧窗口本身）**：**两个桥** manifest / `pyproject.toml` 里的 `rui_compat`
  与 **core 侧** `/api/v2/meta` 的 `compat`（`src/rui/web/api/v2_api.py`）一起从 `>=0.6.0 <0.8.0`
  换成同时容得下 0.7.x 与 0.8.x 的新窗口 `>=0.7.0 <0.9.0`（上界抬到 `0.9.0`；或按 **C-003** 重定——
  C-003 尚未成文，走这条要先立契约再改 manifest，属另卡）。设备清单的 `compatibility.rui_min`
  不变（dot2 `0.3.0`、xiaoai `0.6.0`），它管的是「设备至少要哪个 core」，不是这次要动的窗口。
- **谁受影响**：仍在 0.6.x 上跑 core 的人（插件将拒绝加载，必须升 core）；没先发双窗口版本的桥
  （core 0.8.0 会拒绝它，见 §a.3）；照旧文档抄 `>=0.6.0` 的第三方插件作者。
- **怎么验**：
  ```bash
  curl -s "$BASE/api/v2/meta" -H "X-Auth-Token: $TOKEN" | jq '.compat'
  rg -n 'rui_compat' ../rui-dot2-bridge/pyproject.toml ../rui-dot2-bridge/*/plugin.yaml \
                    ../rui-xiaoai-bridge/pyproject.toml ../rui-xiaoai-bridge/*/plugin.yaml
  # 反向：手改一个 manifest 的 rui_compat 为 '<0.8.0'，跑 plug-test 应 FAIL 且 detail 指出窗口
  ```
- **通过标准**：`/api/v2/meta` 的 `compat.min/max_exclusive`（core 侧）与**两个桥** manifest 里的
  `rui_compat` 完全一致；窗口外 manifest 被**拒绝**且 detail 说清「支持哪一段、当前是哪一段」；
  两桥 + core 的 CI 全绿。

**三项共同的出口（跨项，不是第四项）**：

- 升级**与**回滚各跑一次并留存真实输出（HANDOFF-0.8 §6 出口条件 2）；
- `docs/HANDOFF.md` 的「新会话入口」按 0.8.0 的事实重写；
- release notes **不得**把 b.1/b.2 写成「清理优化」——它们是「老配置/老链接会失效」，要写在最前面。

---

## c. 双 dot2 同型号验证组（**当前代码从未验过**）

> **现状必须先说清**：现网与全部真机记录都是 **dot2 + lx06 异型号**组合；**两台同型号 dot2
> 在代码里从未验证过**。下面 6 组不是「回归」，是**首次验证**——测出的任何异常先记录实际行为，
> 再决定改代码还是改文档，不在本文件里预判结论。

**开测前的准备（缺一项就不要开始）**

1. 第二台 dot2 到位，固件版本与第一台一致（不一致先记录版本差，别混着测）；
2. **清掉 `DOT2_CONTROL_PORT`**：它是**全局环境变量**，两台留着会抢同一个控制端口（README 的
   「故障排查」里已有这条）；
3. 逐台分配 `listen_port`（例如 8871 / 8873），不要两台同端口；
4. `multi_device: {enabled: true, max_planes: 2}`（默认只起第一台的平面）；
5. 两台各用自己的 token（`token_env`）；证书两台同一套或各一套，先记录用的是哪种；
6. 记下两台的 `id`，下文记作 `$ID1` / `$ID2`；`$BASE` / `$TOKEN` 口径与
   `docs/acceptance-pending.md` 一致。

### C-1 两台同唤醒词**同时**触发（谁抢到会话）

- **现象 / 担心什么**：同一个唤醒词在两台同时命中时，是否出现**双回复**（两台都答、声音叠加）、
  或两台互相把对方的回复打断。仲裁目前是**按房间**的，同房间与不同房间是两种完全不同的场景。
- **怎么验**：分两组，各 5 次——(i) 两台放**同一房间**，对两台同时说唤醒词；
  (ii) 两台放**不同房间**，同样同时说。每次记 `curl -sS "$BASE/api/v1/audio" -H "X-Auth-Token: $TOKEN"`
  里 `planes["$ID1"]` / `planes["$ID2"]` 的 `metrics` 与 `/health.planes`，并录一段音判断实际出声。
- **通过标准**：同房间 **5/5 只有一台**进入回合，另**不产生第二条回复**（无双重 TTS、无叠加）；
  不同房间的**实际**行为（各自独立 / 一台被拒）逐次记录并与 README 支持矩阵口径一致；一次都不能崩。
- **需要什么**：两台 dot2、可同时触发的说话方式（或两台各一人同时说）、录音。

### C-2 单会话 runner → 第二台发起对话被 409 时的实际表现

- **现象 / 担心什么**：runner 是**单会话**（README 支持矩阵已写：第二台并发对话会被
  `a turn is already running` 拒绝）。担心的是**用户看到什么**：第二台是否静默无反应、
  是否留下悬挂会话、409 之后第一轮结束能否正常恢复。
- **怎么验**：房间 A 发起一轮长回复（长文本或 `/api/v1/debug/say`），立即在房间 B 唤醒并发话；
  重复 5 次。抓 `/api/v2` 的响应码、日志里的 409 文案、第二台设备的灯/提示音，以及第一轮结束后
  再让第二台发起 5 次。
- **通过标准**：5/5 返回 409、**不产生半截 TTS**、无悬挂 turn（`/health` 的 planes 状态可回收）；
  第一轮结束后第二台 **5/5 能正常发起**；第二台有可解释反馈（灯效或提示音二者之一——记录实际，
  若两者都没有，这条要升级为体验问题）。
- **需要什么**：两台 dot2、可重复的长回复文本、能读日志的窗口。

### C-3 `output_owner` 房间归属（同屋双设备时谁出声）

- **现象 / 担心什么**：同房间两台 dot2 时，「谁出声」由 `output_owner`（音频平面回报控制面的字段）
  决定。担心出现「两台同时放」或「声音从没被唤醒的那台出来」，以及一台停止后 owner 没释放。
- **怎么验**：
  ```bash
  # 定向播放到指定设备
  curl -sS -X POST "$BASE/api/v1/debug/say" -H "X-Auth-Token: $TOKEN" \
       -H 'Content-Type: application/json' -d "{\"text\":\"测试一\",\"device\":\"$ID1\"}"
  curl -sS "$BASE/api/v1/audio" -H "X-Auth-Token: $TOKEN" | jq '.planes | to_entries[] | {id:.key, owner:.value.output_owner}'
  ```
  对 `$ID2` 重复一次；再发一次**不带** `device` 的请求，看落到哪台；最后停止播放再观察 owner。
- **通过标准**：指定 `$ID1`/`$ID2` 时**只有该台出声**（2/2，人耳或录音确认）；不带 device 时同房间
  **每次落到同一台**（5/5 一致，不来回切）；`output_owner` 与实际出声设备一致；停止后 owner 释放，
  另一台可接管（`_release_owner` 生效）。
- **需要什么**：两台 dot2 同房间、`debug/say`、录音或人耳。

### C-4 每台各自的 `DOT2_VOLUME_CAP`（互不影响）

- **现象 / 担心什么**：`DOT2_VOLUME_CAP` 是 **adapter 进程级 env**（百分比上限，不是设备刻度；
  dot2 设备刻度是 0–127）。两台同型号时若只设 env，**两台上限相同**；要各自不同必须用**设备级**
  `devices[].params.volume_cap_percent`（dot2 桥先读 params、再回落到 env）。这条 per-device 路径
  **从未在双 dot2 上验过**。
- **怎么验**：配置里给 `$ID1` 写 `params: {volume_cap_percent: 60}`，`$ID2` 不写（回落到 env 或默认 100）；
  两台各发 `volume.set 80`（`POST /api/v2/devices/$ID/actions`，`{"action":"volume.set","args":{"value":80}}`），
  再 `volume.read` 回读；然后把 env 改成 70 重启，重复一次。
- **通过标准**：`$ID1` 回读 **≤60%**、`$ID2` 按其自身上限生效；两台**互不影响**（4/4）；
  改 env 不影响已写 per-device 的那台；日志/`/api/v2/devices/$ID` 能查到各自 `volume_cap_percent`。
- **需要什么**：两台 dot2、可改 config 并重启平面的窗口。

### C-5 双设备 LED 状态是否各自独立

- **现象 / 担心什么**：LED 由平面下发到设备。同型号两台时担心事件串共用或状态互相覆盖
  （「唤醒一台，两台一起亮」），以及状态机在 `off` 之后残留。
- **怎么验**：对 `$ID1` 依次发 wake / listening / thinking / error 四种灯效
  （`POST /api/v2/devices/$ID/actions`，`{"action":"led.set","args":{"effect":"wake"}}`），
  **每次只盯另一台**（它不应变化）；再对 `$ID2` 逆序重复。人眼 + 照片/短视频。
- **通过标准**：8 次里**0 次串灯**；两台各自的灯色与 README 支持矩阵的实测口径一致
  （**唤醒=绿、思考=蓝、说话=青、放歌=紫**，2026-10-27 人眼实测）；一台进入 `off` 不影响另一台。
  > 注：dot2 的灯色**没有自动化测试**（core 的 `tests/test_xiaoai_led.py` 测的是 xiaoai ring 的状态委派，
  > 不是 dot2 灯色）；xiaoai 侧的状态委派另见 `rui-xiaoai-bridge/tests/test_led.py`。本组判定以人眼+照片为准。
- **需要什么**：两台 dot2 同处可观察的桌面、相机。

### C-6 双平面并发与重连（断一台不影响另一台）

- **现象 / 担心什么**：`max_planes=2` 下是两个平面进程。担心一台断线/重启拖垮另一台，
  或 `restarts` 计数被错误地记到健康的那台身上。
- **怎么验**：两台在线 → 断掉 `$ID1` 的网络（或断电）30 s → 期间对 `$ID2` 说唤醒词 3 次 →
  恢复 `$ID1`。全程每 5 s 采一次：
  ```bash
  curl -sS "$BASE/health" -H "X-Auth-Token: $TOKEN" \
    | jq '{audio_plane, planes, restarts:[.audio_devices[]?.restarts]}'
  ```
- **通过标准**：`$ID2` **3/3 正常应答**、无 5xx；`$ID1` 恢复后 **60 s 内** 回到 `running`；
  期间 `$ID2` 的 restarts **增长 0**；两台 plane 状态各自独立、无相互 `error` 传播。
- **需要什么**：两台 dot2、可控的断电/断网方式、30 s 以上窗口。

---

## d. 其余堆积（不在 §b/§c 里，但 0.8 前后要处理）

### d.1 D-011（**A 已落地 @ `6117fb7`；B 两项未做**）

| 事项 | 现状 | 落地判据 |
|---|---|---|
| **A：只调顺序**（`ack` 的 `order` 200→5，**tier 仍 2**） | **已落地 @ `6117fb7`**：`src/rui/config/catalog.py` 的 `_GROUPS` 里 `ack` 现为 `(…, 2, 5, …)` ⇒ v2 语音助手页**第 3 组（首屏可见）**、旧面板「音频与唤醒」tab **第 1 位**、tier≤2 全量里**第 6**（`assistant(1,10)/session(1,20)/system(1,100)/web(1,110)/security(1,120)` 这五个 tier1 组在前） | 顺序已核实（`group_specs()` 按 `(tier, order, id)`）：v2 页 = `assistant → session → ack → devices → audio.capture → audio.voice → audio.playback → audio.aec`；`tests/test_config_catalog.py` 已随本批同步。**要 v2 第 1 位仍需 `tier 2→1` 或 v2 页内显式排序 → 0.8 P1** |
| **B：结构**——唤醒应答是否**独立成页** | **未做**（A 已落地，独立成页仍未做） | 决策后：独立路由 + 导航项 + `docs/audit/console-ordering.md` 对应行标「已做 @ commit」 |
| **B：结构**——`audio.capture/voice/playback/aec` 四组是否**挪出**语音助手页 | **未做**，四组仍在语音助手页（且是页内最后 4 组） | 决策后：四组迁到设备详情或独立音频页；`tests/test_config_catalog.py` 的组断言同步改；ACK-1/ACK-2 真机项重跑 |

> **A 已随 `6117fb7` 落地**；**B 两项**属「归属/结构」，要 9 哥 说「可以动结构」才做。
>
> **A 的验收口径（Lead 2026-10-27 定，9 哥 明确「不要动 tier」，按 order-only 收）**：
> **ACK-1 判据 = 「首屏可见」而不是「第 1 组」** —— v2「配置 → 语音助手」页里，唤醒应答在
> **首屏第 3 组、无需滚动即可看到并能改**（`assistant(1,10) → session(1,20) → ack(2,5) → devices(2,10) → …`）；
> 旧面板「音频与唤醒」tab 里它是**第 1 位**。**order-only 已达成这一批的目标**（首屏按 5 组估算，第 3 组在首屏内）；
> **要 v2 第 1 位**需 `tier 2→1` 或 v2 页内显式排序，会撞 `tests/test_config_catalog.py` 的 tier-2 断言与 tier 契约
> → 归 **0.8 的 P1 结构项**（本文件 §d.1 的 B 两项），不纳入本批。
>
> 另一个容易踩的点：**`tests/test_config_catalog.py` 的 `ack ∈ groups[2]` 断言不用改**——A 不动 tier，
> 断言绑的是 tier 序（本批只加了「ack 在 tier2 里第一」的断言）。`docs/acceptance-pending.md` 的
> ACK-1/ACK-2 已由 **task-18** 按上面的口径同步改（「v2 第 3 组首屏可见 / 旧面板第 1 位」）。

### d.2 意图层：0.7.9 之后还剩什么

- **已合入（有 commit，`ddb7cbb`）**：批次 1 的 **C1**（结束词 + 有歌 = 停歌且关会话，
  `stop_and_dismiss`）与 **C2**（`stop_intent` 只认整句，`_STOP_TAIL_CHARS` 已删）。
- **已合入（`34e3629`）**：批次 2 的 **C3/C4/C5**（无歌说「暂停/停一下」→ 回「现在没在放音乐。」、会话保持
  `session_action="pause_no_music"`；paused 也算「有歌」；暂停词表收窄为「暂停」「停一下」）与批次 3 的
  **C6** 文案、**C11**（暂停回复「好，暂停了。」+ `docs/interaction.md` 口径）。**代码已进 main，
  但真机仍未验** —— 见 `known-limitations.md` L14 与 `docs/acceptance-pending.md` 的 N1/N2/N3。
- **不做**：批次 4 的 **C7**（单字「停」）、**C8**（无歌 transport）、**C9**（「大一点/小一点」）；
  以及批次 3 里踩决策 2 边界的 **C10**（「退一下」别名）。四条都写进
  `docs/known-limitations.md`（L10–L13），**不是**待办——要改先由 9 哥 改决策 2。
- **剩下的不是代码，是验收**：N1/N2（C1/C2 真机）、N3（C3）在
  `docs/acceptance-pending.md`，随 0.8 的真机窗口一起做（第二台 dot2 到位后与 §c 同场）。

> 批次表与每条判据的权威版本是 `docs/audit/intent-collisions.md`；本文件只列状态，不复制矩阵。

### d.3 真机未测项（**引用，不复制**）

- 入口只有一个：`docs/acceptance-pending.md`。0.7.9 之后仍未测的是「21 条只能真机」+ HE-01 补充
  + 本阶段新增里的 N3/ACK-1/ACK-2（N1/N2 在 0.7.9 窗口验）。
- 来源与口径：`docs/audit/unverified-inventory.md`（全量 36 条 = §1 10 条 + §2 26 条真机协议）。
  **两处条数不同**（inventory 36/26、acceptance-pending 21+补充+新增）——以 acceptance-pending
  为执行口径，inventory 只作来源索引。若以后要按「某一固定条数」对齐，先统一这两份文件的统计口径，
  再在 release notes 里引用。
- 与 §c 的关系：§c 的 6 组是**新增**验证项，第二台 dot2 到位后与上面这些窗口**同场**做，
  但**不要**把 §c 的结论写成「旧项已验」。

### d.4 控制台其它排序建议（低优先，均为 P2「维持/整理」）

| 页面 | 建议 | 理由 | 落地判据 |
|---|---|---|---|
| 设置（Rooms / 多设备与全局音频） | **维持**现有 5 段顺序：汇总 → 告警 → 房间卡 `RoomsPanel` → **多设备策略 `HousePolicyEditor`** → 全局音频 `HouseAudioEditor` | 汇总/告警先给状态，房间卡是日常；`HousePolicyEditor`（多设备启用、房间/输出策略，`PATCH /house` 只发改动字段）是**装机/扩容**频率，不该上移；全局音频是硬核参数，放最后正确 | 只要不动；若将来加「加设备」快捷入口，加在房间卡之后、策略之前 |
| 密钥（P2 整理） | 按**分类分组**（provider 优先），组内字母序 | 现在是单张 `secrets-list` 按名字母序，找 provider key 要扫全表 | 分组后 provider 组在首屏；组内顺序稳定（字母序） |
| 工具与集成 / 音乐 / 日志 | **维持** | 现有顺序已符合「日常在前、硬核在后」 | 不动 |

> 排序的完整判据与首屏估算在 `docs/audit/console-ordering.md` §4/§5；本文件只留 0.8 期间要复查的三条。

### d.5 与 0.8.0 并行但不进 0.8.0 的（登记）

- `docs/HANDOFF-0.8.md` §3 的四个未决问题（HA 是否从 MCP 实例模型摘出、在线音乐 provider 的配置键名、
  未验证清单的口径、MCP server 端已否决）；
- §4 的打磨项（flaky 的 `test_plane_failure_is_reported_with_its_reason`、`mcp ha: down` 日志节流、
  覆盖率不低于 `docs/audit/coverage-baseline.md`、phrasebook 只中文、升级/回滚演练）；
- `runner.abort()` 跨房隔离（0.7.x 已知限制，0.8.x 再评估，不在 0.8.0）。
