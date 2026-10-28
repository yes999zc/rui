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

## a.4 0.8.1 范围标记（a–f，2026-10-28）

> 0.8.0 不部署、`v0.8.0` tag 仅作历史；**0.8.1 合并 a–f 后发版并直接部署**。生产在发版前仍是
> **0.7.9**。本表只记「这一项在 0.8.1 里的落点与证据」，0.8.0 的 §b–§f 原文保持不动。

| # | 0.8.1 项 | 状态 | 落点 / 证据 | 备注 |
|---|---|---|---|---|
| a | 告别/结束词**不 arm** 免唤醒续听 | **已落地** `d633d80` | `ReplyFinished.followup`（additive，默认 `true`）；`web/api/app.py::_finish_close_path` 发 `followup=False`；`audio/process.py` 只跳过 arm/replay，仍排空并套 `post_playback_mute_ms` | 旧控制面不发该字段 = 旧行为；测试 `tests/test_followup_window.py`、`tests/test_dismiss_turn_release.py` |
| b | rs03qr 型号 voice 默认 **300 / 500**（不动 lx06） | **已落地**（桥 `9866c6d`，tag `v1.3.3`→`31195ee`） | 桥 `devices/rs03qr.yaml` 的 `audio_defaults.voice`；core `schema.py::VoiceDefaults` + `effective_audio()` | 连带把桥 `rui_compat` 收到 `>=0.8.1 <0.9.0`；生产插件仍是 1.3.2 直到 0.8.1 部署 |
| c | 控制台暴露两个 voice 字段 | **已落地** `d633d80` | `web/src/v2/audio.ts` 的 `labelKey` + `AudioPanel.vue` 渲染；`catalog.py` 帮助文案改为「先继承型号 manifest，再全局」 | 仅设备音频面板；字段本就按设备可写，仍需重启音频面 |
| d | trace gap **块序号**（protocol + trace additive） | **工作区已实现，待提交/评审** | `audio/protocol.py` 的 `Metrics.playback_gap_chunks`（`[chunk_index, gap_ms]`，per-reply、additive）；`process.py` 仅在应答发声期间填充；`trace.py::note_playback_metrics` 写入 `meta.playback_gap_chunks`（空账本不记） | 控制面在 trace 前取 fresh snapshot，不用 2 s 周期快照；旧对端仍可解析 |
| e | lx06 48k/16k 对齐 ADR + 冷启动可观测 | **已落地**（文档） | `docs/adr/0008-lx06-capture-negotiation.md`：`capture.rate` 保持协商、不改 `lx06.yaml`、living 不变；冷启动注册/probe/≤3 次 ack 重读与残余（capture layout 迟到需重启） | 改 manifest 需 9 哥 批准 + 独立真机 probe；探针清单见 ADR |
| f | 文档收口 | **已落地**（本批，未提交） | `CHANGELOG` 0.8.1、`docs/known-limitations.md` L23/L24、本表、`docs/socket-plug-v1.md`、`docs/contracts`/C-001、README 版本矩阵 | **不做**版本号 bump；发版时再改 `pyproject.toml` / `__init__.py` |

**发版前复查**：d 的提交与 trace 侧字段是否随本次一起进 tag；README 矩阵的「当前代码」行在 bump 后要从 0.8.1 改成实际版本。

---

## b. 0.8.0 破坏性三项（删什么 / 谁受影响 / 怎么验）

### b.1 删 `/legacy` 面板与全部 15 条老链接 302 —— **已落地 @（本卡）**

- **落地记录（2026-10-28，task-8）**：已核对 404/302/200 矩阵、typecheck/build、全量与 ruff（见 HANDOFF「B1 / task-8」）。
  删除 `src/rui/web/api/legacy_links.py`、`app.py` 的
  `/legacy*` / `_legacy_shell` / `FALLBACK` / 根 catch-all，以及 `web/` 下全部旧面板源码
  （`index.html`、`main.ts`、`App.vue`、`router/api/i18n/panels/nav`、`views/`、旧 `components/`
  与 `providers/`）。保留 `/`→`/v2/` 302、`/v2`、`/api/v2`、`/api/v1/panels`，以及 v2 仍引用的
  `AppIcon.vue` / `theme.ts` / `style.css` / `vite-env.d.ts` / `public/*`。根静态资源
  （`/favicon.svg`、`/fonts/*`、`/logo*.svg`、`/assets/*`）改为显式 mount/route；`vite.config`
  只留 `v2` 输入；Dockerfile 检查 `dist/v2.html`；v2 内 `/legacy` 链接与 `?from=` 提示删除。
  测试矩阵见 `tests/test_console_switch.py`（404/302/200），`test_panels.py` 只保留
  `/api/v1/panels` 注册表。`web.console` 枚举保留、值被忽略（README/deploy §3.8 已写明）。
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

### b.2 删 `adapter: xiaoai` 的读入迁移与弃用告警 —— **已落地 @（task-9）**

- **落地结果（2026-10-28）**：`src/rui/config/legacy_adapters.py` 已删除；`store.migrate()`
  不再改写拼写，而是在 Pydantic 之前用 `removed_adapters.py` 明确拒绝，报错原文
  `devices[i] (room): adapter 'xiaoai' is no longer accepted in 0.8.0; change devices[].adapter to 'lx06'`。
  `DeviceConfig.adapter` 必填（无默认值）；`RuntimeSpec.listen_port` 必填；
  `device_rules.effective_listen_port()` 不再兜底 4412（无法解析时返回 `None`，连接 URL 返回
  409 `listen_port_unresolved`）。`/health` 不再有 `legacy_adapter_spelling` 行或旧拼写计数。
  recovery 策略未改：坏文件保留、按 last-good/defaults 启动、`/api/v1/config/recovery.note`
  带同一句修复句。回归用例：`tests/test_removed_adapter_rejection.py`、
  `tests/test_plugins.py::test_runtime_without_listen_port_is_rejected`、
  `tests/test_setup.py::test_connect_info_reports_a_missing_listen_port_instead_of_guessing`。
- **代码内核的白名单**：旧拼写与新拼写只允许出现在
  `src/rui/config/removed_adapters.py`（修复句必须点名两者）；`tests/test_kernel_decoupling.py`
  的 `ALLOWED_FILES` 已改为该文件，并删掉了原 `web` 排除项（0.8.0 已无其它品牌命中）。

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
  rg -n 'legacy_adapters|LEGACY_ADAPTER_HINT|DEFAULT_LISTEN_PORT' src/rui
  # 反向：把一份老配置（adapter: xiaoai）喂给 0.8.0，直接起服务看报错
  ```
- **通过标准**：老配置启动时**显式报错**且报错里给出修复句（把 `xiaoai` 改成 `lx06`），不是静默忽略、
  也不是半加载；`/health.adapter_plugins.warnings` 不再因旧拼写出现；`src/rui` 无源码命中
  （唯一白名单是 `config/removed_adapters.py` 里的修复句；`tests/fixtures/legacy_lx06/` 是外置桥的
  冻结夹具副本，不属于核心源码；测试里那条「旧拼写会被拒绝」的回归用例属测试白名单）。

### b.3 `rui_compat` 契约窗口调整 —— **已落地 @（task-12）**

- **落地结果（2026-10-28）**：core `/api/v2/meta` 的 `compat` 已改为
  `{"min": "0.7.0", "max_exclusive": "0.9.0"}`（`src/rui/web/api/v2_api.py:1307`）；core 版本
  `0.7.9 → 0.8.0`（`pyproject.toml`、`src/rui/__init__.py`、`uv.lock` 三处一致）。两个桥**先于** core
  落地（顺序不可反）：`rui-dot2-bridge` **1.3.2**（`4536968`）、`rui-xiaoai-bridge` **0.2.3**（`0d82250`），
  两边 manifest 都是 `rui_compat: ">=0.7.0 <0.9.0"`。core 侧夹具与示例
  （`tests/fixtures/legacy_lx06/plugin.yaml`、`tests/fixtures/sample-plug/plugin.yaml`、
  `examples/plugin-example/plugin.yaml`）上限抬到 `<0.9.0`；`tests/_plugin_compat.fixture_compat_range()`
  仍由 `rui.__version__` 推导上限；`test_api_v2_read` 的 meta 断言与 `test_plugins` 的两条清单断言同步。
  设备清单 `compatibility.rui_min` 未动（dot2 `0.3.0`、xiaoai `0.6.0`）。反向证据：临时 manifest 写
  `rui_compat: ">=0.6.0 <0.8.0"` 被 loader 拒绝，报错原文
  `plugin 'sample-plug' is incompatible: it requires RUI >=0.6.0 <0.8.0, but this build is 0.8.0; refusing to load it`。
- **已做（2026-10-28，`053546a`）**：`docs/contracts/C-002-console-v2-api.md` 的旧窗口
  `>=0.6.0, <0.8.0` 已同步为 `>=0.7.0, <0.9.0`，三处都改（§1 版本窗口 `:16`、§5.4
  `meta.compat` 示例 `:372-373`、§7 兼容承诺 `:1137`）。发 tag 前不再欠契约侧同步。

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

### C-0 代码侧前置：`DOT2_*` 还有 5 项只能全局配（2026-10-28 查代码补记）

现有 `build(dev, cfg)` 已有优先级 **`DeviceConfig` > plug `params` > `DOT2_*` env**（`rui_dot2_bridge/adapter.py:906-916`），
所以**不是从零做**。但下面 5 项目前**只从 env 读、没有 per-device 通路**：

| 项 | 位置 | 两台的后果 |
|---|---|---|
| `DOT2_CONTROL_PORT` | `adapter.py:315`（bind `127.0.0.1:<port>`） | 第 2 台 bind 失败 → 该房间 plane 起不来（**生产 override 现在设着 `"8872"`**） |
| `DOT2_LOUDNESS_FILE` | `adapter.py:314`（默认 `dev/rui/loudness.json`） | 两台写同一文件，响度学习互相覆盖 |
| `DOT2_EQ_DIR` | `adapter.py:313` / `:324`（默认 `artifacts/eq-prototype`） | 两台共用同一 EQ profile 目录 |
| `DOT2_SECRETS_DIR` | `adapter.py:953` | 两台共用凭证目录（同型号同凭据，需确认可接受） |
| `DOT2_OWW_THRESHOLD` | `adapter.py:222` | OWW 阈值只能全局；两台麦克风要不同阈值时做不到 |

**做法**：把这 5 项接进 `dev`/`params` 读取路径，env 只作 fallback；文件类默认值带 `device_id` 区分。
**验收**：两台 plane 同时起来、各自写自己的 loudness/EQ、control port 不冲突（或两台都设 `0`）、阈值可按台设。
**优先级 P0**：不做完，第二台一插就起不来，C-1…C-6 全都测不了。

**已定（9 哥 2026-10-28）：两台用同词，按最不利情况设计与验收。**

- 理由：唤醒词本来就可以 per-device 自定义（`schema.py:265`），所以「错开」随时能做——
  **但那是运维技巧，不能当设计前提**。实际两个房间大概率互相听不到（开门、空调、大音量、回声都可能传过去），
  所以设计必须假设「**两台能互相听见**」，按同词的最不利条件做。
- 因此验收**一律用同词**跑（C-1…C-6 全部同词基线）；要证明的是「同词也只会一台接、不双回复、不互相打断、不跨房停别屋的歌」，
  而不是靠"两台听不见"侥幸成立。
- 「错开」只作为**可选优化**保留：配成不同词后应互不触发（`wake_words` per-device），验证一次即可，不作默认。

**同词必须额外覆盖的三个最不利用例**（超出 C-1 的"同时触发"）：

1. **弱间隔触发（同房）**：A 已答到一半，**同房间**的 B 才听到 A 的应答尾音并命中唤醒词 → 仲裁层按 `turn_open` 拒绝（`_in_open_turn`），不产生第二回合。
2. **A 放歌时 B 同词唤醒**：**同房间**的 B 按 `cross_echo` 抑制并计 `arbitration_cross_echo_suppressed`（日志写 `playing_device` / `suppressed_device` / `room`）；**跨房间**的 B 会被 admit，但不拿 barge、不打断 A 的歌（只有播放房间的 owner 可 barge）。
3. **A 在应答中 B 同词唤醒**：**同房间**按 `turn_open` 拒绝；**跨房间**在仲裁层 admit（按房间隔离）。文本/调试/渠道回合在 `runner.busy` 时返回 `409 a turn is already running`，不产生第二回合（已测）。语音链路**没有** busy 检查，且 `on_wake` 会 `runner.abort()` 当前回合；跨房 B 唤醒是否打断 A / 是否双声，属真机录音 + 人耳判据，本轮只记录实际、不预设结论；若出现打断/双声，升级为 0.8 缺陷单（不要在本卡改 app/runner）。`lost_same_word` **只用于同一 merge window 内同词竞争的 loser**，不用于跨房/回合中场景。

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
  **C6** 文案、**C11**（暂停回复「好，暂停了。」+ `docs/interaction.md` 口径）。**代码已进 main；
  真机已于 2026-10-28 验（C1 正负 / C2 / C3 / C5；C4 措辞未验）** —— 见 `known-limitations.md` L14 与 `docs/acceptance-pending.md` 的 N1/N2/N3。
- **不做**：批次 4 的 **C7**（单字「停」）、**C8**（无歌 transport）、**C9**（「大一点/小一点」）；
  以及批次 3 里踩决策 2 边界的 **C10**（「退一下」别名）。四条都写进
  `docs/known-limitations.md`（L10–L13），**不是**待办——要改先由 9 哥 改决策 2。
- **剩下的不是代码，是验收**：**C4 措辞**（无专项用例）与 **R79-1…6** 在
  `docs/acceptance-pending.md`，随 0.8 的真机窗口一起做（第二台 dot2 到位后与 §c 同场）。

  > **2026-10-28 更新**：**N1/N2/N3 已验**（C1 正负 / C2 / C3 / C5，本地 08:54–09:33；容器 UTC 00:54–01:33），
  > **ACK-1/ACK-2 已验**（D-011 目视）；仍未验的是 **C4 措辞**与 **R79-1…6**。
  > **2026-10-28 更正（d.6）**：N2 判据已**有意变更**为「`停止你好` / `停止播放你好` **命中**结束词」；
  > 上文「N2 已验」指的是**旧口径**（`停止你好` 走模型、会话未关），该口径**作废**，N2 需按新口径**真机复验（2 min）**。
  > N1 / N3 不受影响（C1 正负与暂停语义未改）。

> 批次表与每条判据的权威版本是 `docs/audit/intent-collisions.md`；本文件只列状态，不复制矩阵。

### d.3 真机未测项（**引用，不复制**）

- 入口只有一个：`docs/acceptance-pending.md`。**0.7.9 发布当时未做真机；2026-10-28 已补验 N1/N2/N3/ACK-1/ACK-2**（见下；**N2 见下方 d.6 更正**）。
  **仍未测**的是「21 条只能真机」+ HE-01 补充 + 0.7.9 新增 6 条（R79-1…6）。

  > **2026-10-28 更新**：**N1/N2/N3/ACK-1/ACK-2 均已验**（N1 `stop intent: '停止播放。' -> media stopped, session closed`；
  > N2 `停止你好` 走模型、会话未关；N3 暂停；ACK 目视），`docs/acceptance-pending.md` 已勾；
  > 剩余 **R79-1…6 + 21 条 + HE-01 补充**，窗口排期不再排这 5 条。
  > **2026-10-28 更正（d.6，有意变更）**：N2 判据已改为「`停止你好` / `停止播放你好` **命中**结束词」；
  > 上面「N2 `停止你好` 走模型、会话未关」是**旧口径**，**作废**（历史保留，勿删）；N2 需按新口径**真机复验 2 min**
  > （`停止你好` / `停止播放你好` → 停歌 + 关会话；`停止你好吗` / `你好停止` → 仍不命中）。
  > 排期口径同步：N2 **重新待验**，N1 / N3 / ACK-1 / ACK-2 不受影响。
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

### d.6 ASR 尾巴剥离（**已落地 @（本卡）**，2026-10-28）

- **落地记录（2026-10-28，task-6 / d.6）**：`src/rui/session/rules.py` 新增 `_WAKE_TAIL_WORDS`
  （按长度倒序）+ `_strip_wake_tail()`；`stop_intent()` 在 `ALIASES` 之前**只剥一次尾部**，其余整句判定
  不变，未新增任何长度/位置容差；`STOP_WORDS` / `ALIASES` 未改。测试：`tests/test_session.py::test_stop_intent_strips_one_fixed_wake_tail`
  （5 条必测 + 4 条边界，先红后绿）。N2 判据已同步改为「命中」，真机待按新口径复验。
- **决定（2026-10-28，9 哥）**：在 `stop_intent` 整句判定前，**先剥掉固定的唤醒词尾巴**，再按整句匹配。
- **尾巴表（唯一受控例外，独立于结束词表与 `ALIASES`）**：`你好` / `你好小瑞` / `你好小智` / `你好小志` / `你好晓智` / `嗨小瑞` / `嗨小智`。
- **只剥尾部**，且必须满足「整句 = 结束词 / 别名 / 结束词 + 语气词」+「一个尾巴词」的结构；剥完再整句判定。
- **不恢复**旧的「任意位置子串」「总长 +2 容差」；**不许新增任何长度/位置容差**。
- 在 `src/rui/session/rules.py` 的 docstring 写清「为什么允许这一条」。
- **必测（先红后绿）**：`停止你好` → 命中；`停止播放你好` → 命中（经别名）；`你好小瑞` → **不**命中（纯唤醒）；`你好停止` → **不**命中（只剥尾不剥头）；`停止了吗` → **不**命中（疑问语气词已排除）。
- **与 N2 的关系**：本卡落地后 `停止你好` 由「不命中」变为「命中」，`docs/acceptance-pending.md` 的 N2 判据需同步改写——这是**有意变更**，不要当回归。
- **判据**：5 条测试先红后绿 + 既有意图测试全绿；`STOP_WORDS` / `ALIASES` 语义不动（尾巴表单独存在）。
- **落点**：`src/rui/session/rules.py` + `tests/`。

### d.7 `_reconcile_volume` 精确相等导致每 ~16 s 重复 `set_vol`（P1 性能 / 日志噪音）

- **已落地 @（本卡）**（2026-10-28，task-7）：按方案 ① 加容差。`src/rui/audio/process.py`
  新增 `VOLUME_RECONCILE_TOLERANCE = 1`（单位是设备低层刻度 **0–255**，不是百分比），
  `_reconcile_volume()` 的判定改为 `abs(self.device_volume - want) <= VOLUME_RECONCILE_TOLERANCE`；
  配置变化 / `volume.set` 的写入路径未动（用户 set 仍立即写、并记为新的 target）。
  回归测试：`tests/test_volume_reconcile.py`（差 0/±1 不写，±2 写，配置变化仍写，用户 set 仍写
  ——含「mixer 219、用户设 220」一步差用例；先红后绿）。**剩余验收**：真机静置 5 min，
  `set_vol` / `volume: reconciled` 计数应降到 0。
  **显式代价**：设备读回若真的偏移 1 刻度（≈0.4% 满量程），重建器不再纠正，直到下一次
  配置变化或用户 set；>1 刻度仍会纠回。
- **现状（2026-10-28 查代码）**：`src/rui/audio/process.py:2907` 的 `_reconcile_volume()` 用 `self.device_volume == want` 精确比较；设备读回值有取整/量化差，导致每个 metrics 周期（~16 s）重复 `set_volume` 并打 `volume: reconciled to N`。
- **做法（择一，实现时定）**：① 加容差（例如 ≤1 设备刻度或 ±1%）后再写；② 只在配置变化时写，读回值只用于展示。
- **判据**：静置 5 min，`set_vol` / `volume: reconciled` 日志 0 次（或从 ~19 次降到 0）；音量调整仍生效；新增回归测试（float/取整差不再触发）。
- **来源**：9 哥 2026-10-28 真机 / 日志观察。

### d.8 唤醒后首句偶发空 ASR（P2，真机低频）

- **现象 / 证据（2026-10-28 10:05:53 真机）**：`[seg] 1.14 s rms=0.1370 speech=1.04 s`
  → `noise, no turn: '。'`。ASR 只返回句号，系统按噪声丢弃，用户感知为「说了没反应」。
  同一轮 12 句里出现 **1 次**。
- **对照（不算本条）**：10:08:19 那次 `rms=0.0352` 属声音过小，判噪声是**正确行为**。
- **要查的方向**：
  1. 唤醒应答窗口与 VAD 首段的边界（`src/rui/audio/vad.py:5`、`:554-558` 的 200 ms preroll 是否足够；
     唤醒应答（ack）期间的上行是否被门控吃掉开头）。
  2. `_segment` 起始裁剪是否吃掉首帧（`vad.py:575-577` 目前只裁尾部静音，需核对起始是否完整）。
  3. ASR 空结果与「只有标点」的归类：现在两者都落进 `rules.is_noise()`（`src/rui/session/rules.py:249-261`）
     → `app.py:2823-2825` 的 `noise, no turn`，**无法区分统计**。
- **优先级 / 纪律**：**P2**（低频、不阻塞）。**修之前先加计数指标**把发生率量出来
  （如 `asr_empty` / `asr_punct_only` 与 `noise` 分开计数），**不要直接改门限**。
- **来源**：9 哥 2026-10-28 真机日志。

### d.9 P0 ack 回声漏切（dot2 460 ms cue）—— **已落地 @（本卡）**

- **真机（2026-10-28 11:01:30）**：`cue_gate: cue=[0,460]ms dropped=[0,403]ms kept=[403,2653]ms` →
  `[seg] 1.18 s` → `turn: transcript='在呢？'`（4.18 s 白起一轮）；dot2 无设备 AEC、`ack_echo_ignore_ms=2500`。
- **根因**：`process.py` 用设备 48k rate 去除 16k 渲染的 ack（460→153、skip 403、hold 2653）；
  且首唤醒无 learned lag，cue 实际 740–850 ms（自 handover）超出 800 ms 一阶搜索后无人接。
- **修复**：`_ack_gate_windows()` 用**渲染 rate** 算 cue_ms；hold 从 cue 播放结束起算
  （`ACK_PLAYOUT_LEAD_MS=850` + cue + `ack_echo_ignore_ms`，dot2：460+850+2500=3810）；
  `vad.py` 在未学到 lag 时用实测先验（740 ms）跑窄搜索（严格匹配 + quiet-after，不写 EMA）；
  barge 路径（cue/hold 置 0）未动。
- **验证**：`tests/test_ack.py`（rate/window 与 no-mute）、`tests/test_ack_echo_gate.py`
  （860/900/930 ms 首唤醒不 replay；窗口后语音正常起回合；第二次唤醒仍可听）；
  全量 + ruff 见本卡报告。**真机复验：9 哥 2026-10-28 下午**。
- **已知限制**：`docs/known-limitations.md` **L17**（与 L1/HE-02 同族、触发条件不同，不合并）。

### d.10 P1 短回复不拆句（217 ms 空隙）—— **已落地 @（本卡）**

- **真机（2026-10-28 11:01:35）**：`reply: 1 gaps, max 217 ms`（`process.py` 的 underrun 指标）。
  先按卡的要求用 FakeTts 数 **vendor synth 请求数**，不把 gaps 日志直接当成两次 synth。
- **根因**：唯一拆句点是 `turn.py:split_sentences`；旧代码下**只有「短回复内部有 `\n`」**
  （`SENTENCE_ENDINGS` 含换行）会被拆成 2 段，每段一次 `_speak_sentence` → 两次 synth 之间留出空隙。
  `在，你说。` 本来就是 1 段；**无句末标点**的短回复旧代码走 tail 也是 1 次——那条测试是回归保护，
  不是「旧代码会拆」的证明。
- **修复**：`split_sentences(..., hold_short=True)` 在整段仍短（`SHORT_REPLY_CHARS=8`）且「会被拆成多段」
  （含内部换行，或 ≥2 个句末标点）时不发段；hold 有界——下一个使窗口结束的 delta、force/tail、
  或 `SHORT_REPLY_HOLD_MS=300` 释放。长回复/单句短回复不 hold，首段仍在首个句末标点发出。
  单请求仍受 `MAX_SENTENCE_CHARS=45` 与 turn 的字符预算约束。
- **残余风险（300 ms 不是墙钟定时器）**：hold 只在**下一个 delta 到达时**评估，没有独立计时器。若模型在
  短回复中间停顿 >300 ms，停顿后的首个 delta 会释放 hold → 该句仍拆 2 段。`好。行。` 在**同一 delta** 内
  达到 1 次 synth；分两个 delta 到达时仍 2 次。长回复首句 ≤8 字且同一 chunk 带 `\n` 时会多 hold 一个
  delta（内容不合并，只是延后；下次 delta 超过 8 字即恢复）。
- **验证**：`tests/test_turn.py` 新增 5 条（`在，\n你说。`→1 次 synth；`在，你说。`→1；
  长回复仍多段且首段不被合并；无句末标点的短回复 tail 1 次；hold 边界 300 ms/force）。真机复验留 9 哥 下午。
- **task-30 追加（2026-10-28，未提交）**：d.10 的残余风险被真机复现为新的「在，你说。」空隙：
  字面 5 字串本来只 1 段；实际 LLM 在逗号后输出换行（`在，\n你说。`），模型停顿 >300 ms 时
  `hold` 到期仍按**拆分**释放 → 2 次 synth，vendor 每段自带 80 ms 头/尾静音，seam **160 ms**
  （`playback_gap_count=0`，因为 PCM 连续）。修复：≤10 字且会拆的缓冲，在到期/force 时**合并为 1 段**
  （不再拆分）；首音频时刻不变（402 ms vs 402 ms）。新增 `tts_call#k` 日志、`reply:` 的
  `segments/silences/inner`、Metrics/Trace 的 per-reply segment 账本。复核脚本
  `scripts/measure_reply_seam.py`，分析与限制见 `docs/analysis/short-reply-seam-task30.md`。

### d.11 有限续听窗口 N（**已落地 @（本卡，task-17）**，2026-10-28）

- **语义**：回答 drain / 会话关闭后的免唤醒窗口 = `min(session.timeout_s×1000, audio.voice.followup_max_wait_ms)`，
  默认 **N = 8000 ms**（cap）；本路径还有更小的 override（音乐 hold、音量确认的 0）再取 `min`。
  窗口到点由 `UtteranceGate` 的 `max_wait_ms` 关麦（发 `WakeIdle`），之后必须唤醒词才能起一轮。
- **`session.timeout_s=0` 是「会话不过期」，不是「0 秒窗口」**：旧公式 `min(0, cap)` 会得出 0 ms；
  现在 timeout≤0 时直接取 cap（仍为 8 s）。**cap=0 才表示完全不续听**（每个句子都要唤醒词）。
- **有歌的已知不同步**：`music.resume_hold_ms > 8000` 被 cap 截断的只是**续听门**的 `max_wait_ms`；
  `process.py` 的 `_music_hold_until` 仍按**完整** hold（schema 上限 10 s）计时，所以 8–10 s 之间
  音乐仍暂停、免唤醒窗口已关。登记为取舍（`docs/known-limitations.md` **L19**），本卡不修。
- **落点**：`src/rui/audio/process.py` 的 `_followup_wait_ms()` / `_arm_followup_later(reason=...)`；
  `src/rui/config/schema.py` 的 `VoiceConfig.followup_max_wait_ms`、`MusicConfig.resume_hold_ms`。
- **回归**：`tests/test_followup_window.py`（默认 8 s、timeout 更短取小、`timeout_s=0` 仍 8 s、cap=0 不武装、
  1 s 内接受 / 9 s 与 20 s 拒绝；音乐 hold 的 cap 截断由代码保证、当前未单测）。
- **未做**：**真机未验证**（无专项真机窗口）；发版说明不得写「续听窗口真机已验证」。


## e. 房间策略待 9 哥 拍板

> **状态：两项均已于 2026-10-28 拍板（ANSWERED）。本批只登记，不实现；落地卡见 §f。**
> 决策权在 9 哥；实现者不许自行选词/改落点；实现前词表与配置仍需 9 哥 最终确认。
> 两项目前只在 backlog 登记，`HANDOFF`/`ROADMAP` 未收录，落地时补。

### e.1 跨房间「全场停止」要不要做

- **状态**：**ANSWERED（2026-10-28，9 哥 拍板：选 B）**。
- **决定（B）**：做跨房间全场停止。词族初稿（9 哥 已同意方向，**具体词由 9 哥 后补定稿，实现前须再确认一次**）：
  `全屋停止` / `都停下` / `全部停止` / `全都别放了`。
- **约束**：整句匹配 + 独立词表；**不进结束词表**；**不新增任何长度/位置容差**（与 0.7.9 的 C2 同框架）。
  词族属新增动作触发词；**决策 2 的边界 9 哥 本次未单独表态**——若实现前认定需要改决策 2，
  须再确认，**不能默认「独立词表就自动合规」**（参见 `docs/audit/intent-collisions.md` C10）。
- **语义**：命中后清空**所有**房间的音乐 + 结束各房间会话；只在「至少一个房间在放音乐」时有意义，
  其它时候给一句提示，**不许静默成功**。
- **排期**：**不在多音箱前置批实现**；落地卡见 `§f.1`，排在 P0 收口之后。
  落地时同步改 **D3**（`docs/multi-device-design.md:452`「不提供『全屋停止』口令（第一版）」）与
  §4.4:198 的「见开放项」表述，并在 §9.3 追加带日期变更记录。
- **背景**：现在「停止」只作用于 output owner 所在房间（`app.py:973-1013`）；A 房间放歌时，在 B 房间说「停止」
  只会关 B 的会话，歌照放。`docs/multi-device-design.md:198` 把它标成开放项，一直没做。
  5 台设备上线后，「隔着房间喊停」会变成日常诉求。
- **选项 A（不做）**：保持「停止只作用于当前房间 / owner 房间」。代价：人不在放歌那间就得走过去说；
  好处：零新增词、零误命中风险。
- **选项 B（做）**：新增**独立的全场停止词族**（最终词族以上方「决定」为准；原候选「全都停」已替换为「全都别放了」），
  命中后清空**所有**房间的音乐并结束各房间会话。代价：新增触发词，必须**整句匹配 + 独立词表**，
  **不进结束词表**；只在「至少一个房间在放音乐」时才有意义，其它时候应给提示而不是静默；
  要真机验多房间同时有会话的场景。
  ⚠️ **口径收紧**：B 新增的是**动作触发词族**，不是既有结束词的别名。是否算触碰「不新增结束词」的决策 2 边界
  （参见 `docs/audit/intent-collisions.md` C10 的判据），由 9 哥 一并拍板；**不预设「独立词表就自动合规」**。
  0.7.9 的 C2 只作「整句匹配」机制参考，两者词族性质不同。
- **选项 C（折中）**：B 的词族但**只清音乐、不结束其它房间的会话**（更保守，但语义变复杂）。
- **建议**：**B**。理由：这是放歌场景的自然诉求，而整句匹配的框架已经成熟（0.7.9 的 C2 就是干这个）；
  拿它当「已有机制的复用」，比新造一套模糊匹配安全。触发词合规性仍由 9 哥 拍板。
- **拍板结果（2026-10-28）**：**B**；词族由 9 哥 后补定稿、实现前再确认；**决策 2 边界本次未表态，实现前再确认**。

### e.2 提醒 / 定时播报 / 音乐续播落到哪个房间（`default_output` 策略）

- **状态**：**ANSWERED（2026-10-28，9 哥 拍板：三级落点规则）**，不再是开放项。
- **决定（三级规则，按序）**：
  1. **指令里指定的房间优先**（如「叫客厅的小瑞…」）；
  2. 没指定 → `multi_device.default_output`（现在为空，**需配一台**，建议 `living`）；
  3. **目标房间设备离线 → 回落到 `default_output` 播报，并明确说出「客厅那台没在线」**，不许静默丢弃。
- **与 K4 的关系（变更面比“改一个回落分支”大）**：本决定**整体替代** K4 的提醒落点结论
  （K4 见 `docs/multi-device-design.md:442`：“第一版固定走 default_output；未配置时回落最近活跃并记 warning”），
  并**新增**两条：① 指令指定房间优先；② 目标离线 → 回落 default_output 且明说。
  落地时需同步修订 K4 / §4.4:197-198、空 `default_output` 的 warning（`schema.py` 中 `default_output is empty` 段）、
  `_last_active_room`/`_default_room`/`_reminder_client`（`app.py:1387-1442`、`:1435`）与
  `tests/test_app_fleet.py:485`，并按 `docs/multi-device-design.md:433` 在 **§9.3 追加带日期变更记录**；
  **本轮只登记、不改代码/文档**。
- **待 9 哥 定的小配置**：`multi_device.default_output` 配哪台（建议 `living`）；已记入 `docs/待拍板.md` D-013。
- **原记录（归档，供追溯）**：
  - 背景：`default_output` 当前为空（`/health` 可见）；5 台后「提醒落哪台」会明显。
  - 选项 A（固定默认房间）：可预测、无需改代码；代价：人在卧室时提醒在客厅响。
  - 选项 B（最近活跃 + 时间窗）：跟随人；代价：边界要定参数（半夜唤醒也算活跃）。
  - 选项 C（全屋播报）：设计明确不做（回声/延迟）。
  - 原建议：A 先配置落地，B 时间窗排队。**2026-10-28 已由上面的三级规则取代**。

## f. 后续功能卡（只登记，不实现）

> **状态：PLANNED。** 不进「多音箱前置 P0」批；顺序 **f.1 → f.2**；每张实现前需 9 哥 最终确认词表/配置。

### f.1 跨房间「全场停止」（来源 §e.1 = B，PLANNED）

- **范围**：新增独立的全场停止词族（初稿见 §e.1），**整句匹配 + 独立词表**，不进结束词表，
  不加任何长度/位置容差。**不得用 `ALIASES` 实现**——ALIASES 会落进普通停止分支、退化成只停 owner 房间，
  必须走独立判定分支。
- **语义**：命中 → 清空**所有**房间音乐 + 结束各房间会话；没有房间在放音乐时给一句提示，**不静默成功**。
- **验收要点**：
  1. 同房/跨房各 5 次：命中后所有房间 `media_stop` + 会话关闭，无残留 owner；
  2. 无歌时说「全屋停止」→ 有提示、无状态变化；
  3. 负例：`停止`（不带全屋）仍只作用于当前房间/owner；`停止播放` 不误命中；
     **`别放了` / `别放了吧`（既有 ALIASES）仍走既有结束词语义，不被全场停止吃掉**；
  4. 词族最终由 9 哥 定稿，实现者不许自己加词。
- **文档同步**：落地时改 D3（`multi-device-design.md:452`）与 §4.4:198，并在 §9.3 记变更。
- **排期**：多音箱前置 P0 收口之后第一张；**不挤进本批**。

### f.2 语音定时提醒（固定句式 first，PARTIAL — C1 入口/落点 + C2 最小抢占/失败可见已落地；重试/恢复待 0.8.1）

- **落地记录（C1，2026-10-28，未提交/待评审）**：
  - **语音入口**：`src/rui/tools/reminders.py` 的 `parse_voice()`（固定句式，非自由 NL）+
    `app.py` 的 `_voice_reminder_command()`，接在 `_stop_command` 之后、verbless/LLM 之前；
    LLM 工具路径（`local.set_reminder`）**未改**。
  - **句式**：`提醒我/叫我/提醒` + `今天/明天/后天`（可省，省略=下一次）+ `早上/上午/中午/下午/晚上`
    （可省）+ `N点/半/一刻/三刻/N分/整/HH:MM`（中文或阿拉伯数字）+ 动作；动作前可带
    `在<room>`，room **必须与 `devices[].room` 精确相等**（未知房间不猜，留在动作文本里）。
  - **落点（§e.2 三级）**：`Reminder.room` 新增（`reminders.json` 缺字段读作 `None`，向后兼容）；
    播报时 `_reminder_route()`：指定房间在线 → 该房间；离线 → `default_output` 并说
    「<room>那台没在线」；未指定 → `default_output`。`multi_device.default_output` 按 D-013
    定为 **`living`（客厅）**；未显式配置时也优先 enabled 的 id `living`（或 room 客厅），
    都没有才回落最近活跃并记 warning。
  - **失败口径**：解析失败/过去时间 → 明确追问或说明，**不创建**；存盘失败 → 说明没存下。
    **相对时间、区间与句式外的时间线索不拦截**（「十分钟后提醒我关火」「七点以后」
    「明早八点」「3天后」「每周一」「七点零一刻」）——仍交给既有 LLM 工具 `parse_at`，
    避免把本来能办的句子改成反问；弱触发「叫我」无可解析时间时同样交给 LLM，避免
    「叫我小王」被误追问「什么时候」。
  - **测试**：`tests/test_reminder_voice.py`（时间表/23:59 边界/动作与房间/持久化/三级落点/播报入口）、
    `tests/test_app_fleet.py` 的落点用例。
  - **C2 已补（2026-10-28，见下条）**；**仍未做（0.8.1 及以后）**：失败重试/排期、
    被让出的音乐自动恢复、真机验收（重启不重播/不丢、离线回落实际出声）。
  - 另修一处既有缺陷：tick 与 listener **重复入队**导致每条提醒播两遍；现只保留 tick 一个生产者
    （`tests/test_app_fleet.py::test_a_due_reminder_is_spoken_exactly_once` 为回归）。
  - **C1 review 修复（2026-10-28）**：一刻/三刻按 15/45 分解析（不再存成 1/3 分 + 「刻吃药」）；
    有时间线索的句子不再误问「什么时候」；`default_output` 留空时显式优先 `living`/客厅。
  - **C2 最小到点抢占 + 失败可见（task-21，2026-10-28，代码侧已落地；真机未验证）**：
    - **只抢占目标房间**：`_deliver_reminder()` 先按 §e.2 解析落点。目标有歌（playing/paused）且
      `output_owner`/`music_room` 指向该设备 → `MusicService.stop()`（队列+平面+LED）并
      `StopAudio`；目标在应答 → 只向该设备 `StopAudio` 清已排队音频，播报后
      `runner.abort()` 停剩余合成。**其它房间的音乐/会话/owner 不动；被停的音乐不自动恢复。**
    - **失败可见**：`/health.reminders` 新增 `fired / spoken / failed / offline /
      last_error / last_failure_at / last_room`（进程内内存，重启清零）。目标离线 →
      `default_output` 播报并说「<room>那台没在线」，同时计 `offline`+`failed`；
      无任何在线音箱/TTS 失败 → `failed` + **ERROR** 日志（不再只有 `log.warning`）。
    - **收尾（task-17 口径）**：不发 `reply_finished`（不给免唤醒续听窗口），目标会话
      `idle.stop()`，LED 最后一条 `off`；被抢占回合的 `reply_finished` 由 30 s TTL 标记
      吞掉。`reminder_speak` 只投递一次，**不做重试/排队**。
    - **测试**：`tests/test_reminder_delivery.py`（目标放歌只停该房间/不恢复/其它房间不动、
      应答中断、离线回落+提示、无音箱/TTS 失败计数、LED off/无 `reply_finished`）；
      `tests/test_app_fleet.py` 的 `/health.reminders` 与离线到点集成用例。
    - **残余取舍**：详见 `docs/known-limitations.md` **L22**（计数易失、`failed` 含降级、
      fallback 房间不抢、30 s 后迟到的 `reply_finished` 仍可能开窗）。

- **现状（2026-10-28 查代码，纠正原描述）**：rui **已有**提醒/定时能力——
  `src/rui/tools/reminders.py`（`ReminderStore`、`reminders.json` 原子落盘、`build_scheduler`、
  `fired_at` 防重播）、`app.py:3390-3429`（1 s tick + 播报队列 + `_reminder_client`）、
  `tests/test_app_fleet.py:485`（落点测试）。**当时真实缺口四条**：
  ① 语音固定句式入口（只有 LLM 工具调用）；② 到点抢占（当时等空闲、不 `stop` 音乐、120 s 超时只 `log.warning`）；
  ③ 指令指定房间（当时固定 `default_output`）；④ 目标离线要明说。
  **2026-10-28 状态：① 由 C1 补齐；②/④ 由 C2 补齐；③ 由 C1 的 `Reminder.room` 补齐。**
- **第一版范围（固定句式，不做任意自然语言）**：
  - 句式：「明天早上七点叫我在客厅…」「下午三点提醒我在书房…」；
  - 只识别 **时间 + 动作 + 房间** 三要素；句式外的话不承诺。
- **到点行为**：先 `service.stop()` 让出音乐 → 在**目标房间**播报；落点按 §e.2 三级规则
  （指令房间优先 → `default_output` → 目标离线时回落 `default_output` 并明说「客厅那台没在线」）。
  **C2 落地口径（2026-10-28）**：只有目标设备在放歌时才 `stop`，其它房间的歌不动；
  播报后不自动恢复音乐；失败/离线计数见 `/health.reminders`。
  - **失败口径**：播报失败/目标全离线必须留用户可感知痕迹或至少 ERROR 计数，**不许只 `log.warning`**（对齐 e.1 的「不静默」）。
  - **等待上限与音乐恢复（C2 已定）**：**不再保留**「等 owner/空闲最多 120 s」的全局等待；
    目标房间到点即抢占，fallback 直接播；被 `stop` 的音乐**不自动恢复**（提醒优先）。
    失败重试/排期/恢复一律留 0.8.1，本卡不做。
  - 新增播报文案（如「客厅那台没在线」）需进 `src/rui/session/phrases.py`，中英口径随实现确认。
    **C1 已加** `reminder.set / set_room / need_time / need_text / past_time / save_failed / offline`
    七条（当前话术表只有中文，沿用既有口径）。
- **调度**：**沿用**进程内调度（`ReminderStore` + 1 s tick），**不引入外部 cron**；
  **持久化已实现**（`reminders.json` + `fired_at` 先落盘再播），**保持**，真机需验重启后不重复、不丢。
- **明确不做**：重复提醒、日历同步、跨设备静音、自然语言复杂时间推理。
- **排期**：f.1 之后；与 0.8 破坏性三项**并行不冲突**即可，但不许挤进多音箱前置批。
- **来源**：9 哥 2026-10-28；现状与缺口由 2026-10-28 代码核查补正。
