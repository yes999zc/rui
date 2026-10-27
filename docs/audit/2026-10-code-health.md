# rui 代码健康审计（2026-10 · v0.7.0 稳定性视角）

> **性质**：只读审计，只记录不动手。**未修改** `src/`、`web/`、`tests/`；未做任何 git 写操作；
> 未连接生产 NAS / 设备 / secrets；本次唯一写入是本文件。
> **对象**：`house-ear` @ `main ef7ff18` + 未提交工作树。
> **快照时刻**：2026-10-26 12:27（10 改 2 新增）。审计期间工作树**仍在被并发修改**
> （12:21 时是 7 改 1 新增，12:27 又多了 `setup_api.py` / `test_setup_api.py` /
> `C-002` 契约），本报告的行号与计数以 12:27 的快照为准；引用具体文件前请重跑 §4 的命令。
> **参考（只读）**：`../rui-dot2-bridge`、`../rui-xiaoai-bridge`。
> **时间**：2026-10-26，时间盒 ≤ 1 天。**审计不是重构**：不给大规模改造方案。
> **口径**：证据 = 代码 file:line 或实际命令输出；无法复现的标「未验证」。

---

## 0. 摘要（先看这里）

| 项 | 数量 | 标题 |
|---|---|---|
| **阻塞 v0.7.0 稳定版** | **2** | B-1 三个出厂插头的 `rui_compat` 上限都是 `<0.7.0`，0.7.0 构建会拒绝加载全部插头<br>B-2 生产 `0.7.0-pre9` 镜像含未提交的 `process.py` 补丁，无法从任何 commit 重建 |
| 非阻塞（进 post-release backlog） | 8 | 见 §1 #3–#10 |
| 明确不做 | — | 重构、删代码、性能压测、动生产、改下游仓库 |

**一句话结论**：代码本身的整洁度高于同类项目平均（ruff 全绿、0 个 TODO/FIXME、测试总数按 0.7.3
规范口径为 **1651 / 1634 / 17 / 0**（collected/passed/skipped/xfail，见
`docs/audit/coverage-baseline.md`；本审计当日 `--collect-only` 为 1518）、
零未引用前端模块、死代码仅 ~30 行）；真正卡住 0.7.0 的不是"代码烂"，而是**版本兼容上限**与
**已部署镜像不可重建**这两件流程性事实。

---

## 1. Top 10（每条：证据 / 影响 / 是否阻塞 / 建议）

| # | 标题 | 阻塞 v0.7.0 |
|---|---|---|
| 1 | 出厂插头 `rui_compat` 上限 `<0.7.0`，0.7.0 构建拒绝加载全部插头 | **是** |
| 2 | 生产 `0.7.0-pre9` 含未提交补丁，镜像不可从仓库重建 | **是** |
| 3 | LX06 适配器存在两份同 id 副本，且核外副本永远无法接管 builtin 名 | 否 |
| 4 | 内核仍有设备默认值（4412 端口 / 端口提示文案 / 别名） | 否 |
| 5 | `app.py` 单文件 6663 行 / 75 路由，且是 60 天最高 churn 源文件 | 否 |
| 6 | 两套控制台 + 两套 API 并存（26.9k vs 27.5k 行，75 vs 27 路由） | 否 |
| 7 | 启动串行：HA → MCP → `runner.warm()`（本地 ASR 1.1 GB）+ 每设备一个子进程 | 否 |
| 8 | 多设备默认关闭 + 每设备一子进程，CPU/内存随设备数线性增长（RSS 未测） | 否 |
| 9 | 死代码 5 个模块级函数（~30 行），无可删风险但有 2 个是"未来口子" | 否 |
| 10 | 配置无热更（手改文件需重启）+ setup 单槽/`by_id` 双写迁移债 | 否 |

### #1 出厂插头兼容上限 `<0.7.0`，0.7.0 构建拒绝加载全部插头 —— **阻塞**

**证据**

- 三个 manifest 的上限都排除 0.7.0：
  - `plugs/xiaoai/plugin.yaml:9` → `rui_compat: ">=0.3.0 <0.7.0"`
  - `../rui-dot2-bridge/src/rui_dot2_bridge/plugin.yaml:5` → `">=0.6.0 <0.7.0"`
  - `../rui-xiaoai-bridge/src/rui_xiaoai_bridge/plugin.yaml:9` → `">=0.6.0 <0.7.0"`
- 加载门在 `src/rui/plugins/loader.py:75-80`：`parse_range(manifest.rui_compat)` 不匹配
  `running_version()` 就 `raise PluginError(... refusing to load it)`；版本取自
  `src/rui/__init__.py:1` 的 `__version__ = "0.6.0"`。
- **实测复现**（内存里把 `rui.__version__` 改成 `0.7.0`，不改任何文件）：

  ```
  current version: 0.6.0
  loads now: xiaoai-lx06
  0.7.0: REFUSED -> PluginError: .../plugs/xiaoai/plugin.yaml: plugin 'xiaoai-lx06' is
         incompatible: it requires RUI >=0.3.0 <0.7.0, but this build is 0.7.0; refusing to load it
  ../rui-dot2-bridge/...: REFUSED -> ... 'dot2-echomuse' ... >=0.6.0 <0.7.0 ...
  ../rui-xiaoai-bridge/...: REFUSED -> ... 'xiaoai-lx06' ... >=0.6.0 <0.7.0 ...
  ```

**影响**：0.7.0 一升版本，镜像自带插头与两个下游插头**全部拒绝加载**。LX06 / Dot2 都没有
adapter，音箱直接不可用。这不是降级，是硬失败，且发生在"发布后第一次重启"。

**是否阻塞**：**是** —— 现有三个 manifest 的上限字面排除 0.7.0，升级即全设备失效。

**建议（只修这条）**：先改三个 manifest 的上限（例如 `<0.8.0`），下游先发版并记录 tag/digest，
之后才 bump `rui.__version__`；同时补一条测试断言"出厂 manifest 的 `rui_compat` 覆盖
`rui.__version__`"，防止下次再踩。

### #2 生产 `0.7.0-pre9` 含未提交补丁，镜像不可从仓库重建 —— **阻塞**

**证据**

- `git status --porcelain`：7 个已跟踪文件被改，1 个新增未跟踪：

  ```
   M docs/HANDOFF.md                                   ?? docs/audit/2026-10-code-health.md（本报告）
   M docs/contracts/C-002-console-v2-api.md            ?? tests/test_device_test_history.py
   M docs/plan/speaker-tuning.md
   M src/rui/audio/process.py    M src/rui/audio/protocol.py
   M src/rui/setup_state.py      M src/rui/web/api/setup_api.py
   M src/rui/web/api/v2_api.py
   M tests/test_barge.py         M tests/test_setup_api.py
  ```
  `git diff --stat`（2026-10-26 12:27）：10 files changed, 527 insertions(+), 180 deletions(-)；
  其中 `process.py` 54 行、`protocol.py` 3 行、`setup_state.py` 61 行、`setup_api.py` 18 行、
  `v2_api.py` 37 行、`tests/test_barge.py` +121、`tests/test_setup_api.py` +60。
- `docs/HANDOFF.md:9` 明确写着生产镜像是「底座 = `pre8-eq` + **打补丁 `process.py`**，
  唯一 delta = barge 不播 cue」。
- 工作区 `process.py` 的 diff 恰好就是这件事（`if barge: audio = np.zeros(0)…`
  `process.py` 唤醒路径），与 HANDOFF 描述一一对应。
- HANDOFF:9 同时记 `main ef7ff18` **未 push**。

**影响**：正在跑的生产镜像 = 某个 commit + 手工补丁，**无法由任何 tag 重建**；回滚、
冷装复验、`git bisect` 都失去基准。下一次正常 build 会拿 `main`，行为与 pre9 不同
（cue 回来了，barge 又会被自己的「在呢」打断）。

**是否阻塞**：**是** —— 稳定版的判据之一是"能用仓库重建出跑着的那个东西"，当前不成立。

**建议（只修这条）**：把这 7+1 个文件提交（或明确 revert 掉），打 tag 重建镜像并记录
digest，再拿新镜像替换 pre9；pre9 只能定位为临时验证镜像。

### #3 LX06 适配器两份同 id 副本，核外副本无法接管 —— 不阻塞

**证据**

- 核内副本：`plugs/xiaoai/plugin.yaml`（`id: xiaoai-lx06`、`trust: builtin`），
  设备 `plugs/xiaoai/devices/lx06.yaml:52` → `entrypoint: xiaoai_plug.adapter:build`，
  `adapter.py` 604 行。
- 核外副本：`../rui-xiaoai-bridge/src/rui_xiaoai_bridge/plugin.yaml`（**同一 id**
  `xiaoai-lx06`、`trust: trusted`），`devices/lx06.yaml:56` →
  `entrypoint: rui_xiaoai_bridge.adapter:build`，`adapter.py` 612 行；两树逐文件 diff
  仅 8–20 行（包名/注释差异）。
- 镜像插头把 **device.id**（`lx06`）注册成 builtin：`src/rui/device/registry.py:126-143`；
  builtin 名**不允许替换**：`registry.py:185-191`。实测：

  ```
  builtin names: ['lx06', 'mock']
  out-of-tree lx06 override: REFUSED -> BuiltinAdapterName: adapter name 'lx06' is built into
  this image and cannot be replaced; register a new name and set devices[].adapter to it
  ```
- 同名注册在别处只是 warn-only：`src/rui/device/loader.py:753-760`。

**影响**：只要镜像里还带 `plugs/xiaoai`，out-of-tree 的 `rui-xiaoai-bridge` 永远不会生效
（已实测拒绝）；两份 600 行代码靠手工同步，修一边另一边静默落后。HANDOFF 说
`/api/v1/plugs` 显示 `source=user`，但实际执行路径仍是内置副本 —— 这个说法容易误导验收。

**是否阻塞**：**否** —— 功能不死，内置副本照跑；但"设备代码在核外"这条 0.7.0 目标
无法被验证。

**建议（backlog）**：在 0.7.0 的时间窗内做一次**决策记录**：要么删核内副本（bridge 文档写
"0.7.0 是最早时点"），要么给 out-of-tree 一个不同的 adapter 名并写进文档。补一条测试：
断言"内置 adapter 名集合"与"下游插头 adapter 名"不重叠。

### #4 内核仍有设备默认值（4412 / 提示文案 / 别名）—— 不阻塞

**证据**（内核里仅 6 处**功能路径**命中，其余 24 处是注释/文档字符串，明细见 §2.6）

- `src/rui/plugins/models.py:203`：`listen_port: int = Field(default=4412, ...)` ——
  通用插头契约的默认端口是 LX06 的端口。
- `src/rui/web/api/device_rules.py:34`：`DEFAULT_LISTEN_PORT = 4412`（兜底链最后一层）。
- `src/rui/web/api/setup_api.py:56/58`：`port_blocked` 提示文案写死
  「检查 4412 是否被占用」；同一响应里已经有 `listen_port` 字段（`setup_api.py:249-256`）
  却没用于插值。
- `src/rui/adapter_aliases.py:21,27`：`"xiaoai": "lx06"` 与 `LEGACY_DEFAULT_ADAPTER`
  （注释写明临时、0.7.0 后删）。
- 防回归测试**主动排除** web 层：`tests/test_kernel_decoupling.py:33`
  `EXCLUDED_DIRS = {"web"}`（docstring 已把"4412 fallback port / 厂商唤醒词"列为待清项）。

**影响**：第三方插头若忘记写 `listen_port`，会默认去抢 4412 → 与 LX06 端口冲突；
Dot2（8870）出现 `port_blocked` 时，向导让用户去查 4412，排查方向是错的。

**是否阻塞**：**否** —— 现有两个 manifest 都显式声明了端口（lx06:4412、dot2:8870），
兜底没被触发；影响是"第二个设备接入时的误导与潜在冲突"。

**建议（backlog，~20 行）**：`RuntimeSpec.listen_port` 改 `None` 并在解析处给出明确错误；
提示文案用响应里的 `listen_port` 插值；`test_kernel_decoupling.py` 去掉 web 例外。

### #5 `app.py` 6663 行 / 75 路由，60 天 churn 最高 —— 不阻塞

**证据**

- `wc -l src/rui/web/api/app.py` → 6663；路由装饰器 75 个（`grep -cE '@app\.(get|post|…)\(`），
  其中 70 个带 `Depends(auth)`，5 个是不需要鉴权的静态/健康路由。
- 60 天 churn（`git log --since='60 days ago' --name-only`）：`app.py` 出现在 142 次提交里
  （旧路径 `src/house_ear/…` 109 + 新路径 33），是源文件第一；其后 `audio/process.py` 67、
  `session/turn.py` 40、`config/schema.py` 40。
- 规模分布：9 个文件 >1000 行（app 6663、v2_api 3478、process 3174、catalog 1982、
  contract 1929、turn 1261、schema 1199、setup_api 1171、keycheck 1071）；
  20 个文件 >500 行；120 个 .py 共 45,098 行。

**影响**：评审 diff 与 merge 冲突成本高；v1/v2 共用服务层都在这一个文件里装配
（`app.py:5744-5759` 把 `_write_config/_write_devices/_restart_audio/...` 交给 v2），
小改动也要在 6.6k 行里定位。

**是否阻塞**：**否** —— 纯可维护性，无正确性风险。

**建议（backlog）**：只在 v1 冻结后做机械拆分（按域拆 router，无行为变化）；不新建功能进
v1 侧，避免继续加高这个文件。

### #6 两套控制台 + 两套 API 并存 —— 不阻塞

**证据**

- 前端：`web/src` 合计 54,445 行（ts/vue/css）；其中 `web/src/v2` **26,910** 行，
  v1 侧 **27,535** 行（`components` 11,464 + `views` 8,361 + 根级 7,673 + `composables` 37）。
  多入口构建：`web/vite.config.ts:19-22`（`main: index.html`、`v2: v2.html`）。
- 文案：`web/src/i18n.ts` 1,647 键 vs `web/src/v2/i18n.ts` 1,454 键，**交集 0**
  （命名空间完全不同，是两套完整翻译，不是复用）。
- 客户端：`web/src/api.ts` **46** 个不同端点（57 处引用）vs `web/src/v2/api.ts` **24** 个（29 处），
  其中 **15 个路径完全相同**
  （`/api/v1/config*`、`/api/v1/mcp`、`/api/v1/providers/*`、`/api/v1/music/*`…）。
- 后端：v1 75 路由 / v2 27 路由。同一资源两套写入口，例：
  `PUT /api/v1/config`（`app.py:3343`）、`PUT /api/v1/config/values`（`app.py:3545`）
  vs `PATCH /api/v2/house`（`v2_api.py:2044`）、`PATCH /api/v2/house/audio`（`v2_api.py:2135`）。
  **缓解事实**：v2 的写操作走的是 app.py 传入的同一批函数（`app.py:5744-5759`），
  端口冲突等规则共用 `device_rules.py`，未发现语义分裂。

**影响**：任何行为修复要动两处（HANDOFF 已记录一次"诚实性修复"同时改 `setup_api.py` 与
`v2_api.py`）；v1 仍是默认控制台（HANDOFF:9 `/health.console=legacy`），双改会持续到 M10。

**是否阻塞**：**否** —— 写入口共用，没看到"两个真相"；风险是 0.7.0 后的持续双写。

**建议（backlog）**：按既有 M10 计划把默认切到 v2 并将 v1 标 frozen；新功能只进 v2。

### #7 启动串行：HA → MCP → warm（1.1 GB ASR）+ 每设备一个子进程 —— 不阻塞

**证据**

- 实测导入成本：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c "import rui.web.api.app"`
  → **364 ms / 726 个模块**（Python 侧不是瓶颈）。
- 启动序列（`src/rui/web/api/app.py`）：`await ha.start()`（2425）→
  `await mcp_manager.apply(...)`（2440，内部并发起服务器、有握手超时
  `tools/mcp_manager.py:518-519`）→ `await runner.warm()`（2447）。
- `runner.warm()`（`src/rui/session/turn.py:430-453`）会按 asr/tts/llm 链逐个 warm；
  注释写明本地 ASR 会拉 1.1 GB 模型、"冷首轮曾花 5 s 才出转写"。
  ASR 链是每轮复用同一实例（`turn.py:298-304`），不会每轮重载。
- 每个设备另起一个 `python -m rui.audio.process` 子进程并在子进程里建 KWS/VAD
  （`src/rui/audio/process.py:662-710`），即启动成本随设备数增加。
- 仓库里没有 boot-to-healthy 的计时脚本；`scripts/measure_plane.py` 只测运行期指标。
  文档口径：`docs/first-run.md:91`「镜像已在本机、只需下载 ASR 时约 85 秒到 healthy」。

**影响**：首启时间 = 控制面固定成本（导入 0.36 s + HA/MCP 握手 + warm）+ 设备数 × 模型装载；
控制台/健康检查在这段时间内不可用。NAS 场景可接受，但没有可复现的回归基线。

**是否阻塞**：**否** —— 没有证据表明超时或不健康；只是缺度量。

**建议（backlog）**：加一个只读 `scripts/measure_boot.py`（t0 → `/health` ready），
发布前跑一次把数字写进验收文档；不放进 CI。

### #8 多设备资源：默认关闭 + 每设备子进程 —— 不阻塞（未验证内存）

**证据**

- `multi_device.enabled` 默认 **False**、`max_planes` 默认 **4**
  （`src/rui/config/schema.py:1067-1068`）；关闭时只起单平面
  （`app.py:2939-2941`），多余 enabled inbound 设备只给警告。
- 每设备一个 `AudioPlaneSupervisor` 子进程（`src/rui/audio/fleet.py:106-133`），
  超限直接拒绝启动并点名设备。
- 每平面 KWS 线程默认 1（`src/rui/config/schema.py:357`）。
- 已知 CPU 事实（`docs/remaining-work.md:82-83`）：KWS 用 2 个 ONNX 线程、
  3 个 worker 时容器 CPU 约 319%；`kws_num_threads: 1` 后约 31%。
- 本地 ASR（1.1 GB）在**控制面**，不在每个平面里（`turn.py:298-304`），所以不会 ×N。

**影响**：2 台设备 = 2 份 KWS/VAD/AEC 常驻 + 2 个子进程；4 GB NAS 上 RSS 未测量，
不能判断是否够。另外"默认关闭"意味着装了两台音箱的用户会看到一台"配了不驱动"。

**是否阻塞**：**否** —— 缺数据，不能下结论；且默认关闭时不会崩。

**建议（backlog）**：真机测 1 / 2 台设备的 RSS 与 CPU 并写进 `docs/multi-device-design.md`；
文档把"默认关闭 + 需要显式开启"讲成一句话。

### #9 死代码 5 个模块级函数（~30 行）—— 不阻塞

**证据**：用 AST 抽取 `src/rui/**/*.py` 的 558 个模块级公开符号，在
`src + tests + scripts + plugs + web/src` 全语料里统计引用次数，**5 个零引用**：

| 符号 | 位置 | 备注 |
|---|---|---|
| `recent_marker` | `src/rui/setup_state.py:220` | 同文件新增的 `by_id` 迁移逻辑未使用它 |
| `secret_names` | `src/rui/config/catalog.py:1840` | 只在 `tests/test_audit_fixes.py:232/311` 的**测试函数名**里出现，非调用 |
| `pcm_of` | `src/rui/audio/ack_render.py:139` | 无调用方 |
| `total_hits` | `src/rui/audio/kws.py:266` | 无调用方 |
| `path_for` | `src/rui/session/phrasebook.py:68` | 仅出现在 `docs/phrasebook-editor.md:344` 的设计说明里 |

反向证据（正面）：前端 128 个 ts/vue/css 文件**没有一个是未被引用的**（按 basename 全语料
检索，0 命中）；800 行以上的大组件（`DeviceManager.vue` 2573 行等）都由
`web/src/components/panelComponents.ts` 的懒加载表引用。
`grep -rn 'TODO|FIXME|XXX|HACK' src --include='*.py'` → **0**。

**影响**：~30 行，可读性噪音；`secret_names` / `path_for` 像是给迁移/编辑器留的口子。

**是否阻塞**：**否**。

**建议（backlog）**：删除前各自再 grep 一次（命令见 §4）；不建议在 0.7.0 前动。

### #10 配置无热更 + setup 双写迁移债 —— 不阻塞

**证据**

- 无文件监听：`grep -rn 'watchfiles|SIGHUP|reload_config' src/rui` 无命中；
  只有音乐索引按 `mtime` 老化（`music/library_index.py:306`）与日志视图读 `mtime`。
  控制台写入后主动重启平面（`app.py:5334` `_restart_audio`），但**手改 config.yaml 不会
  重载**：`cfg` 在 `create_app` 时进入 `app.state`。
- `src/rui/setup_state.py:112-118`：`test/device/test`（旧房子级单槽）与
  `test/device/by_id/<id>`（每设备历史）**并存"for one release"**，两个写入方共用
  `record_device_test()`；快照时工作树里 v1 的写入已改走 `record_device_test`
  （`setup_api.py:1137-1140`，未提交），迁移在收敛 —— 旧槽仍在双写（`setup_state.py:168-169`）。

**影响**：手改配置后 `/health`、`/api/*` 全部继续用旧值直到重启（排查时容易误判
"配置没生效"）；双写是明确的迁移债，谁先退化谁就出现"上次测试"串台/为空。

**是否阻塞**：**否** —— 有明确注释与共享写入函数，属于有计划的过渡。

**建议（backlog）**：文档里把"改文件必须重启"写成显式一句；双写窗口记进 ROADMAP，
在 v1 退役窗口一起删。

---

## 2. 维度明细

### 2.1 LOC（按目录/模块，`src/` 与 `web/src/` 分开）

**总量**：`src/rui` **45,098** 行 / 120 个 py；`web/src` **54,445** 行 / 128 个 ts+vue+css；
`tests` **32,450** 行 / 121 个 py（测试:源码 ≈ 0.72:1）。

`src/rui` 按模块（含子目录）：

| 模块 | 行数 | 文件 | 模块 | 行数 | 文件 |
|---|---|---|---|---|---|
| `web/` | 11,942 | 9 | `device/` | 1,906 | 5 |
| `audio/` | 7,333 | 19 | `channels/` | 1,283 | 7 |
| `config/` | 3,689 | 5 | `doctor/` | 816 | 2 |
| `session/` | 3,612 | 17 | `security/` | 491 | 2 |
| `plugins/` | 3,469 | 8 | `src/rui` 根级 | 1,863 | 10 |
| `music/` | 3,461 | 15 | | | |
| `providers/` | 2,646 | 11 | | | |
| `tools/` | 2,587 | 10 | | | |

`src/rui` 最大 10 个文件：

| # | 文件 | 行数 |
|---|---|---|
| 1 | `web/api/app.py` | 6,663 |
| 2 | `web/api/v2_api.py` | 3,478 |
| 3 | `audio/process.py` | 3,174 |
| 4 | `config/catalog.py` | 1,982 |
| 5 | `plugins/contract.py` | 1,929 |
| 6 | `session/turn.py` | 1,261 |
| 7 | `config/schema.py` | 1,199 |
| 8 | `web/api/setup_api.py` | 1,171 |
| 9 | `providers/keycheck.py` | 1,071 |
| 10 | `device/loader.py` | 913 |

`web/src` 最大 10 个文件：

| # | 文件 | 行数 |
|---|---|---|
| 1 | `views/SetupView.vue` | 4,289 |
| 2 | `i18n.ts` | 3,738 |
| 3 | `v2/i18n.ts` | 3,322 |
| 4 | `v2/style.css` | 3,192 |
| 5 | `components/DeviceManager.vue` | 2,573 |
| 6 | `v2/api.ts` | 1,671 |
| 7 | `api.ts` | 1,650 |
| 8 | `components/ConfigCenter.vue` | 1,630 |
| 9 | `components/McpManager.vue` | 1,400 |
| 10 | `views/ChannelsView.vue` | 1,334 |

`web/src` 分区：`v2/` 26,910（`components` 10,608 / `views` 5,632 / `setup` 2,688 /
`config` 409 / 其余根级 ts）；v1 侧 27,535（`components` 11,464 / `views` 8,361 /
根级 7,673 / `composables` 37）。

> 读法：**两套控制台行数几乎相等**（26.9k vs 27.5k），这是本仓库最大的结构性成本；
> 后端则明显集中在 `web/api/`（11,942 行，占 src 的 26%）。

### 2.2 重复模块 / 重复实现

| 重复点 | 证据 | 性质 |
|---|---|---|
| 两套控制台 | `vite.config.ts:19-22` 多入口；`web/src/v2` 26,910 vs v1 27,535；i18n 1,647 vs 1,454 键、交集 **0**；CSS 选择器 75 vs 306（交集 1） | 迁移期的平行实现，非复制粘贴 |
| 两套 API | v1 75 路由 vs v2 27；`api.ts` 46 个不同端点 vs `v2/api.ts` 24，其中 15 个相同 | **服务层共用**（`app.py:5744-5759`），只有响应塑形重复 |
| LX06 适配器两份 | `plugs/xiaoai/xiaoai_plug/*.py` vs `../rui-xiaoai-bridge/src/rui_xiaoai_bridge/*.py`：`adapter` 604↔612、`protocol` 220↔228、`led` 530↔538、`services` 43↔51，逐文件 diff 8–20 行 | **真·fork**，同 `id: xiaoai-lx06`、同 adapter 名 `lx06` |
| v1/v2 设备写语义 | `PUT /api/v1/devices/{id}`（`app.py:6053`，整表单）vs `PATCH /api/v2/devices/{id}`（`v2_api.py:2256`，部分字段） | 同一资源两种写契约，但都落到 `_write_devices` |

**没发现的重复**（避免误报）：核心没有为某个机型写分支（`grep -E '(==|!=|in \()\s*["'\''](lx06|dot2|…)'` 零命中）；
adapter 与 core 之间没有第二份 ws 协议实现（core 只经 `runtime.entrypoint` 导入插头）。

### 2.3 死代码 / 未使用符号（有限抽样）

- 抽样方式：AST 取 558 个模块级公开符号 → 全语料词频；结果见 §1 #9（5 个零引用）。
- 私有的、单下划线开头的函数**未抽样**（无法用词频可靠判断）。
- 前端：128 个文件 0 个未引用（`panelComponents.ts` 懒加载表覆盖了所有 800+ 行大组件）。
- 注释卫生：`TODO/FIXME/XXX/HACK` **0**；`noqa` 208 处；`type: ignore` 27 处。
- **不确定项**：`secret_names`、`path_for` 出现在设计文档里，可能是"给下一版留的 API"，
  删之前需要 owner 确认 —— 标**未验证**。

### 2.4 依赖重量

| 项 | 数值 | 说明 |
|---|---|---|
| 运行期依赖 | **12** | fastapi/uvicorn/pydantic/ruamel.yaml/numpy/psutil/websockets/httpx/mcp/pypinyin/segno/socksio（`pyproject.toml:8-21`） |
| 开发依赖 | 3 | pytest / httpx / ruff |
| 可选组 | 4 | `kws`(sherpa-onnx ~30 MB)、`channels`(cryptography)、`weixin-voice`(pilk，可能源码编译)、`dev` |
| 锁定包 | 53 | `grep -c '^\[\[package\]\]' uv.lock` |
| `.venv` | 139 MB | 本地 |
| `web/node_modules` | 61 MB | 仅 vue + vue-router 2 个运行期依赖（`web/package.json:12-14`） |
| `web/dist` | 1.7 MB | 两入口构建产物 |
| 镜像 | **431 MB**（2026-10-23 口径） | `docs/remaining-work.md:78-79`：base python 119 MB + 静态 ffmpeg 129 MB + site-packages 178 MB；`/app` 只 1.9 MB，模型在数据卷 |

**可疑/待确认**：`socksio` 在 `src/` **零直接 import**（`grep -rn 'import socksio'` = 0），
它是 `httpx` 走 SOCKS 代理时的插件（NAS 的 mihomo 场景需要）。属于"隐式运行期依赖"，
标**未验证**是否仍被需要；不建议现在删。`numpy` 15 处 import、`mcp` 6 处，都是真实使用。
**没有发现"重依赖换小功能"的情况**；`sherpa-onnx` 已被正确隔离为可选组。

### 2.5 启动时间（读代码判断 + 现成证据）

| 层 | 成本 | 证据 |
|---|---|---|
| Python 导入 | **364 ms / 726 模块**（实测） | `import rui.web.api.app` 计时 |
| 配置加载 + 恢复 | 功能已测（`tests/test_config_recovery.py`）；**耗时**未测 | `__main__.py:509-528`（`load_with_recovery`） |
| HA over its own bridge (not MCP; corrected 2026-10-27) | 一次握手（有超时，见 `tools/ha.py` / `tools/mcp_client.py`） | `app.py:2425` |
| 通用 MCP | 并发启动，单服务器失败不阻塞 | `app.py:2440`，`mcp_manager.py:518-519` |
| `runner.warm()` | **可能是最大项**：本地 ASR 1.1 GB 模型 + TTS/LLM 连接 | `turn.py:430-453`；`app.py:2447`（串行 await） |
| 每个音频面子进程 | 建 KWS/VAD ONNX（每设备一份） | `process.py:662-710`；`fleet.py:106-133` |
| 端到端口径 | 「镜像在本机、只差 ASR 下载时约 85 s 到 healthy」 | `docs/first-run.md:91` |

**判断**：启动瓶颈不在 Python，而在 **warm 的模型装载** 与 **每设备一个平面**。
仓库里**没有** boot-to-healthy 的自动化计时（`scripts/measure_plane.py` 只测运行期），
所以"0.7.0 启动多久"目前只有安装文档口径。真实秒数标**未验证**（本审计按要求未起服务）。

### 2.6 设备专用代码是否泄漏进核心（关键项）

检索：`grep -rniE 'dot2|xiaoai|lx06|echomuse|rs03qr' src/rui`（已排除 tests/docs/plugs）；
另查数字型泄漏 `4412`（LX06 端口）。**合计 30 处**：`web` 16 处、非 web 14 处。
用 AST 去掉注释与 docstring 后，**功能路径只有 6 处**。

**功能路径（会被执行/会显示给用户）**

| file:line | 内容 | 判断 |
|---|---|---|
| `plugins/models.py:203` | `listen_port: int = Field(default=4412, …)` | **泄漏**：通用插头契约默认值 = LX06 端口 |
| `web/api/device_rules.py:34` | `DEFAULT_LISTEN_PORT = 4412` | **泄漏（兜底）**：manifest 都没声明时落到 LX06 端口 |
| `web/api/setup_api.py:56` | 中文提示「检查 **4412** 是否被占用…」 | **泄漏（文案）**：Dot2 在 8870 时提示错误 |
| `web/api/setup_api.py:58` | 英文同上 | 同上 |
| `adapter_aliases.py:21` | `"xiaoai": "lx06"` | **兼容别名**：注释写明临时、0.7.0 后删，可接受 |
| `adapter_aliases.py:27` | `LEGACY_DEFAULT_ADAPTER = "xiaoai"` | 同上 |

**只有注释/文档字符串（24 处，不影响行为）**

| 文件 | 命中行 | 内容性质 |
|---|---|---|
| `web/api/app.py` | 1224, 1847, 1849, 3266, 5808, 5866, 6155 | `dot2 #N` issue 编号、`DOT2_EM_TOKEN` 示例、LX06 客户端 URL 说明 |
| `web/api/device_rules.py` | 9, 10, 13, 59, 60 | 说明兜底链与 dot2:8870 的例子 |
| `web/api/v2_api.py` | 994 | `dot2 #7` issue 编号 |
| `web/api/setup_api.py` | 288, 1040 | LX06 冷装/灯环的注释 |
| `config/schema.py` | 204 | `dot2 #7` 引用 |
| `plugins/models.py` | 406 | `"RS03QR"` 作为 hardware model 字符串示例（显示用） |
| `doctor/probes.py` | 261, 765 | `dot2 #14` 编号 |
| `healthcheck.py` | 62 | `dot2 #12 C3/C4` 编号 |
| `audio/process.py` | 147 | dot2 设备唤醒时序说明 |
| `__main__.py` | 108, 206, 237 | `dot2 #13/#14` 编号 |

**判断**：

1. **功能性耦合 = 0**：核心没有 `if adapter == "lx06"` 这类分支（专项 grep 零命中），
   硬件参数（8ch/48k/6mic）由 `plugs/xiaoai/devices/lx06.yaml` 的 `audio_defaults` 声明，
   核心读 manifest。
2. **默认值与文案仍有泄漏**：3 处功能默认值 + 2 行用户可见文案（见上表）。这是
   `tests/test_kernel_decoupling.py:33` 主动把 `web` 列入 `EXCLUDED_DIRS` 的结果 ——
   守卫测试自己承认"这批留给另一个任务"。
3. **`dot2 #N` 这类 issue 编号**不是设备耦合，是任务追踪残留；它们会触发未来的
   "品牌字样"守卫，但不是风险。

### 2.7 其他风险（多设备 / 内存 / 并发 / 配置热更）

| 风险 | 证据 | 阻塞? |
|---|---|---|
| 并发：同一时刻一个回合 | `audio/arbiter.py`（637 行）；`turn.py` 单轮状态机；每设备一 `PlaneHandle` | 否 |
| 平面崩溃隔离 | `fleet.py:1-40` 文档 + `tests/test_audio_fleet.py`（SIGKILL 一个另一个继续） | 否 |
| 内存随设备线性 | 每平面一份 KWS/VAD/AEC（`process.py:662-710`）；ASR 在控制面单份（`turn.py:298-304`）；RSS **未测** | 否（未验证） |
| 端口冲突防护 | `device_rules.py:112-155` 对 enabled+inbound 端口去重，null 与默认端口等价 | 否（有防护） |
| 多设备默认关闭 | `schema.py:1067`；关闭时只起单平面 `app.py:2939-2941` | 否（体验/文档问题） |
| 配置热更 | 无 watcher；手改需重启（§1 #10） | 否 |
| 文档滞后 | `docs/multi-device-design.md:19-25` 仍写"运行时只允许一个音频面驱动一台设备"，而 `fleet.py` 已实现多平面（Phase 2）；该文档是 0.4.1 口径 | 否 |
| CI 缺类型检查 | `.github/workflows/ci.yml` 只跑 `ruff check` + `pytest`，无 mypy/pyright；代码里 27 处 `type: ignore` | 否 |
| 健康信号（正面） | `ruff check src web` → **All checks passed**；`pytest --collect-only` → **1651** 条（0.7.3 工作树；四计数 1651/1634/17/0，见 `coverage-baseline.md`）；`tests/test_kernel_decoupling.py` → passed | — |
| 凭据卫生（交接风险） | HANDOFF「风险与未决」记录 NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理输出回显，建议轮换 | 否（非代码问题，但发布前应处理） |

---

## 3. 未验证清单（不要当结论用）

1. **真机 boot-to-healthy 秒数**：本审计未启动服务（遵守"不起生产"），只有
   `docs/first-run.md:91` 的 85 s 历史口径。
2. **每设备 RSS / CPU（1 台 vs 2 台）**：无测量数据。
3. **镜像 431 MB 是否仍是当前值**：该数字来自 2026-10-23 的 `docs/remaining-work.md`，
   Dockerfile 与依赖此后有变动，未重建验证。
4. **生产 `data/plugs/` 是否真的为空**：HANDOFF 正文说 bridge 显示 `source=user`，
   风险段又说 "`data/plugs/` 为空" —— 两处自相矛盾，无法在只读条件下判定。
5. **纯源码运行（不装镜像 plug 目录）时 LX06 的胜者**：本审计只验证了"镜像插头已注册
   builtin 时，核外同名 adapter 被拒绝"；源码运行路径（`plugs/` 作为开发回退）未验证。
6. **`socksio` 是否仍有必要**：无直接 import，仅经 httpx SOCKS 使用。
7. **5 个零引用函数是否被未来代码/文档承诺**：`secret_names` / `path_for` 有文档提及。

---

## 4. 复现命令（全部只读）

```bash
cd /Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear

# 规模
find src -name '*.py' -not -path '*__pycache__*' | xargs wc -l | tail -1          # 45098
find web/src -type f \( -name '*.ts' -o -name '*.vue' -o -name '*.css' \) | xargs wc -l | tail -1  # 54445
find web/src/v2 -type f \( -name '*.ts' -o -name '*.vue' -o -name '*.css' \) | xargs wc -l | tail -1 # 26910

# #1 兼容上限（实测拒绝）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python - <<'EOF'
import rui
from pathlib import Path
from rui.plugins.loader import load_plugin
rui.__version__ = "0.7.0"
for p in ["plugs/xiaoai", "../rui-dot2-bridge/src/rui_dot2_bridge",
          "../rui-xiaoai-bridge/src/rui_xiaoai_bridge"]:
    try: load_plugin(Path(p)); print(p, "LOADED")
    except Exception as e: print(p, "REFUSED ->", e)
EOF

# #3 builtin 名不可替换（实测拒绝）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:plugs/xiaoai .venv/bin/python - <<'EOF'
from rui.device import registry
registry._ensure_image_adapters()
print(sorted(registry.BUILTIN_NAMES))
try: registry.register("lx06", lambda cfg: None); print("ACCEPTED")
except Exception as e: print("REFUSED ->", type(e).__name__, e)
EOF

# #4 设备字面量 / 功能路径
grep -rniE 'dot2|xiaoai|lx06|echomuse|rs03qr' src/rui --include='*.py' | grep -v __pycache__ | wc -l  # 30
grep -rnE '\b4412\b' src --include='*.py' | grep -v __pycache__                                        # 端口泄漏
grep -rnE '(==|!=|in \()\s*["'\''](lx06|dot2|RS03QR)' src --include='*.py' | grep -v __pycache__        # 0，分支为零

# #5 集中度 / churn
wc -l src/rui/web/api/app.py
git log --since='60 days ago' --pretty=format: --name-only | grep -v '^$' | sort | uniq -c | sort -rn | head -8

# #6 双控制台
grep -n 'input\|main:\|v2:' web/vite.config.ts
grep -oE "'/api/v[12][^']*'" web/src/api.ts | sort -u | wc -l        # 46 不同（57 处引用）
grep -oE "'/api/v[12][^']*'" web/src/v2/api.ts | sort -u | wc -l     # 24 不同（29 处引用）
comm -12 <(grep -oE "'/api/v[12][^']*'" web/src/api.ts | sort -u) \
         <(grep -oE "'/api/v[12][^']*'" web/src/v2/api.ts | sort -u) | wc -l   # 15 相同
grep -cE '@app\.(get|post|put|patch|delete)\(' src/rui/web/api/app.py        # 75
grep -cE '@app\.(get|post|put|patch|delete)\(' src/rui/web/api/v2_api.py     # 27

# #7 启动
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -c \
  "import time;t=time.perf_counter();import rui.web.api.app;print(f'{(time.perf_counter()-t)*1000:.0f} ms')"

# 健康信号
.venv/bin/ruff check src web                    # All checks passed
.venv/bin/python -m pytest --collect-only -q | awk -F': ' '/^tests\//{s+=$2} END{print s}'   # 1651（0.7.3；四计数见 coverage-baseline.md，审计当日 1518）
```

---

## 5. 建议落点（只修阻塞项 + 其余进 backlog）

**发布前必须做（阻塞项，2 条）**

1. 三个 manifest 的 `rui_compat` 上限放开到 `<0.8.0`；下游先发版、记 tag/digest，
   最后才 bump `rui.__version__`。加一条"manifest 覆盖当前版本"的测试。
2. 把工作区 7+1 个文件提交（或 revert），打 tag 重建镜像、记录 digest 后替换 `pre9`。

**backlog（不阻塞 0.7.0，按性价比排序）**

3. `RuntimeSpec.listen_port` 默认改 `None`；`port_blocked` 文案改用 `listen_port` 插值；
   去掉 `test_kernel_decoupling.py` 的 `web` 例外（~20 行）。
4. LX06 唯一 owner 决策（删核内副本 or 改名），并补一条名字不冲突的测试。
5. `scripts/measure_boot.py` + 真机 1/2 设备 RSS/CPU，把数字写进验收文档。
6. M10 默认切 v2 后把 v1 标 frozen，只做机械拆分 `app.py`。
7. 删 5 个零引用函数（先各 grep 一次）；双写窗口在 v1 退役时一起收掉。

**不建议做**（本次明确排除）：大规模重构、目录重排、换框架、把 `app.py`/`SetupView.vue`
重新设计。
