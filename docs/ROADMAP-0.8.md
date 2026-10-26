# ROADMAP 0.7.3 → 0.7.8（0.8.0 的前置路线图）

日期：2026-10-26（按 9 哥 本轮 A–F 要求重写；旧版把「小爱摘出」放在 0.8.0、且没有 0.7.8，已作废）
基线：`rui 0.7.2`（tag `v0.7.2` @ `ad11936`，镜像 `ghcr.io/yes999zc/rui:0.7.2@sha256:d830a2ca…8018`）、
`rui-dot2-bridge 1.3.1`（`v1.3.1`）、`rui-xiaoai-bridge 0.2.1`（`v0.2.1`）。
生产基线：NAS 168 跑 `rui-dot2:1.3.1`（核心 0.7.2），2 台设备在线。

> **0.7.8 是验收节点**：9 哥 会在 NAS 上亲手部署并做人肉实验。A–E 五组要求全部收口 + 人肉部署手册实测通过，才算 0.7.8 完成。
> 本文件是跨会话唯一路线图；每版发布后**先更新本文件与 `docs/HANDOFF.md`，再开下一版**。

## 0. 总原则（对本路线图所有版本生效）

1. **契约窗口不动**：三仓 `rui_compat` 保持 `>=0.6.0, <0.8.0`（`rui-dot2-bridge` 的 floor 是 `0.3.0`，同理不动）。本路线图**不新增契约编号**；0.7.5 的 adapter 名弃用只写成配置迁移说明。
2. **每版一个主题、2–3 张卡**；每张卡一个可独立验收的交付物；卡必须带写范围、验收标准、验证命令、评审者。
3. **门禁不可跳**：实现 → 冻结（报 md5/commit）→ 独立 review（PASS/CHANGES）→ 独立 verify（VERIFIED/FAILED）→（需要真机的）真机验收 → 才允许 complete。评审判 CHANGES 必须 reopen。
4. **证据是命令 + 真实输出**，不接受「应该可以」。真机项必须写明：设备、房间、距离、音源与电平、日期。
5. **每版发布纪律**（F）：tag + release notes + 镜像 digest + `/api/v2/meta` 核对 + 三仓工作树干净 + CI 四绿（ci / security / secret-scan / docker-publish）。
6. **不动 4 台联调**（设备未到货）、**不对外宣传**（除非 9 哥 明确说）。

## 1. A–F → 版本映射（一眼核对）

**配套清单（本轮产出，执行时直接取用）**

| 文档 | 内容 | 谁用 |
|---|---|---|
| `docs/audit/unverified-inventory.md` | 36 条「未验证」声明 + 10 条 CI 可测/26 条真机协议 + 8 处文档矛盾 + 判据 | 0.7.3 / 0.7.7 的测试卡、0.7.8 的覆盖率报告 |
| `docs/audit/console-audit-scope.md` | v2 14 路由 / 7 tab / 24 端点 / ~95 交互元素 + v1→v2 32 行对齐矩阵 + 老链接映射表 | 0.7.6 / 0.7.7 / 0.7.8 的审计与切换卡 |

**A–F 落点**

| 9 哥 要求 | 落在哪 | 最终验收证据（0.7.8） |
|---|---|---|
| **A** 测试补齐：未验证项 → 可重复测试；给覆盖率 / 未覆盖清单 / 每项变成了什么测试；CI 四绿 | 0.7.3（基建 + batch-1）、0.7.7（batch-2）、0.7.8（最终报告） | `docs/audit/coverage-0.7.8.md` + CI run 链接 |
| **B** 小爱真正摘出：删内置 lx06 与 `adapter_aliases`，改外置 plug；0.7.x 兼容告警；双跑对比 | 0.7.5 | `/health adapter_plugins.registered=["dot2","lx06"]`、核心 grep 0 命中、双跑对比表 |
| **C** 控制台功能审计（每页/每按钮/每保存）+ v1→v2 对齐 | 0.7.6（审计 + 修）、0.7.7（对齐补齐） | `docs/audit/console-functional.md` + `docs/audit/console-v1-v2-parity.md` |
| **D** 0.7.8 默认切 v2（`/` → v2、`/legacy` 弃用、老链接 302、README/文档改链） | 0.7.8 | 实测输出（见 §3.6） |
| **E** 安装路径补 TLS + 重开 Done 第 6 条（a–e 五条判据） | 0.7.4 | `docs/audit/done6-0.7.8.md`（五条判据逐条贴输出） |
| **F** 发布纪律（一版一主题、独立评审/验证、出口可判定） | 全程 | 每版 `docs/release/v0.7.x.md` + HANDOFF 更新 |
| **0.7.8 额外交付** | 0.7.8 | `docs/runbook/upgrade-0.7.2-to-0.7.8.md`，Lead 亲手跑过并贴输出 |

## 2. 版本表（主题 / 卡数 / 出口条件 / 是否破坏契约）

| 版本 | 主题 | 卡数 | 出口条件（可判定，缺一不算完成） | 破坏契约 |
|---|---|---|---|---|
| **0.7.3** | 测试基线与可复现 | 3 | ①覆盖率基线落库（`docs/audit/coverage-baseline.md`：`src/rui` 总体行/分支 + 分包数字，命令可复跑）；②CI `ci` job 产出覆盖率 artifact 并在 PR/日志可见；③未验证清单 batch-1（CI 可测项）逐项有测试名与通过输出；④`ruff` + 全量 pytest 绿（三仓） | 否 |
| **0.7.4** | 安装路径 TLS 与 Done 第 6 条重开 | 3 | ①`examples/two-speakers/compose.yaml` 挂 `./secrets:/opt/rui-secrets:ro` + `DOT2_SECRETS_DIR`，并给自签命令（`SAN=echomuse-controller`）；②「无 TLS / 有 TLS」两次冷启动实测输出贴出，README 与 HANDOFF 关于「TLS 是否必需」的表述**按实测统一**（两处引用同一次实测）；③Done 第 6 条 a–e 五条判据全过并留证（a healthy、b devices=2/device_errors=0/registered=["dot2","lx06"]、c audio_plane=true×2、d 真机唤醒→指令→播放、e 升级+回滚各一次） | 否 |
| **0.7.5** | 小爱真正摘出（B） | 3 | ①仓内 `plugs/xiaoai/` 删除、镜像不再内置 lx06；`adapter_aliases` 删除；②外置挂载后 `/health adapter_plugins.registered` 含 `["dot2","lx06"]`；③`adapter: xiaoai` 配置仍可用但**必须**打一条弃用告警（含迁移到 `lx06` 的提示）；④核心 grep 门禁白名单为空并通过（`src/rui` 无 `xiaoai|lx06|小爱` 设备专用代码）；⑤新旧双跑对比表（同一配置、同一设备，字段级 diff）；⑥三仓测试绿 + 契约窗口不变 | 否（配置层弃用告警；`rui_compat` 与 C-001/C-002 字段不动） |
| **0.7.6** | 控制台功能审计（C 上半） | 3 | ①`docs/audit/console-functional.md` 覆盖 v2 全部 14 条真实路由 + 全部交互元素（按钮/表单/保存/开关/危险操作/对话框/跳转），逐项结论 ∈ {可用, 不可用, 文案错误}，每项有截图 + 复现步骤；②不可用项 0 个「未处理」：要么修，要么标「实验性」并写进已知限制；③`docs/audit/console-v1-v2-parity.md`：v1 有而 v2 没有的逐条列明「补 / 在 v2 明确说明」的处置 | 否（只读 + 前端；若需新增 API 字段，按 C-002 additive 记录） |
| **0.7.7** | v2 收口与测试补齐 | 3 | ①v1→v2 矩阵每一行都有终态（已补 / 已说明 + 链接），无「待定」；②`/legacy` 可开且顶部有「已弃用，0.8.0 移除」横幅（**此时不切默认**）；③老链接 302 映射表逐条实测（v1 路径 → v2 对应页，含 404 兜底）；④未验证清单 batch-2 全部转成测试或「真机协议」；⑤覆盖率不低于 0.7.3 基线 | 否 |
| **0.7.8** | 默认切 v2 + 验收节点 | 3（含 `[phase]`） | ①全新部署 `/` 落 v2；②`/health console=v2`；③`/legacy` 200 + 弃用提示；④v2 每页可开、每个保存动作生效（实测明细见审计文档，0.7.6/0.7.7 结论 + 0.7.8 复跑）；⑤老链接 302 正确；⑥README hero = v2 主界面、文档内 `/v1` 链接全部改 `/v2`；⑦A 的最终报告（覆盖率 + 未覆盖清单 + 每条「未验证」→ 测试）；⑧CI 四绿；⑨人肉部署手册由 Lead 亲手跑通并贴输出 | 否（API 不变；默认 URL 入口变化，用 302 兼容） |
| **0.8.0**（不在本轮） | 删 `/legacy` + 契约窗口调整 | — | 见 §5 | **是** |

## 3. 每版的卡（写范围 / 验收标准 / 验证方式）

> 卡模板：目标、已定决策、涉及文件、下一步、验收命令。建卡时把本节的「验收标准」逐条抄进 `team_task_create`。
> 写范围用仓库前缀：`[house-ear]` / `[dot2]` / `[xiaoai]`；同一仓库同一时间只允许一个实现者。

### 3.1 0.7.3 测试基线与可复现（A 段 1）

**K-0.7.3-1 `[test] 覆盖率基建与基线`**
- 目标：让覆盖率成为每次 CI 都产出的数字，并记录当前基线（不设拍脑袋阈值）。
- 写范围：`[house-ear] pyproject.toml`、`.github/workflows/ci.yml`、`scripts/coverage_report.py`（新增）、`docs/audit/coverage-baseline.md`（新增）
- 验收标准：
  1. `dev` extra 增加 `pytest-cov`（或等价），`uv run pytest --cov=rui --cov-report=term-missing` 可跑通；
  2. CI `ci` job 上传覆盖率 artifact（含 `term-missing` 输出），Python 3.11/3.12 都跑；
  3. `docs/audit/coverage-baseline.md` 给出：总体行/分支覆盖率、`src/rui` 每个一级子包的行覆盖率、命令与日期；**并给出规范测试口径**（collected / passed / skipped / xfail 四个数，结束 1512/1518/1571 三种口径并存的漂移）；
  4. `ci.yml` 增加一条 `rui plug-test` 冒烟（用仓内最小示例 plug 目录，**不得**依赖 `plugs/xiaoai`，保证 0.7.5 删除后仍可跑），退出码进 CI；
  5. 基线文件被后续版本引用为「不得回退」的比较基准（格式机器可读，如 `coverage-baseline.json`）。
- 验证：`uv run pytest --cov=rui --cov-branch --cov-report=term-missing` 与 CI run 链接。

**K-0.7.3-2 `[test] 未验证清单 batch-1 → 可重复测试`**
- 目标：把清单里**不需要真机**的「未验证/未测」项变成自动化测试。
- 写范围：`[house-ear] tests/**`、必要时 `[house-ear] src/rui/**`（仅修坏行为，不改契约）；清单来源 `docs/audit/unverified-inventory.md`
- 验收标准：
  1. batch-1 每一项在清单里从「未验证」更新为「测试名 + 命令」；新增/修改测试逐项能单独跑通；
  2. 每个新测试做反向验证（旧代码/变异体必须失败），把反向证据写进卡报告；
  3. 全量 pytest 绿、ruff 绿；三仓测试绿。
- 验证：`pytest -o addopts='' -q tests/<new files>` + 全量。

**K-0.7.3-3 `[docs] 未覆盖清单、缺口台账与文档口径统一`**
- 目标：把「测不到的」说清楚，而不是假装覆盖了；顺手清掉互相矛盾的文档口径。
- 写范围：`[house-ear] docs/audit/unverified-inventory.md`、`scripts/coverage_gaps.py`（新增，可选）、`docs/audit/2026-10-code-health.md`（仅改口径矛盾处）、`docs/HANDOFF.md`
- 验收标准：
  1. 每个 `src/rui` 一级子包有覆盖数字与缺口说明；缺口分三类：可单测未写（排到 0.7.7）、需真机（给协议与判据）、已弃用（写明）；
  2. 与 batch-1 的测试名一一对应；
  3. **已知 8 处文档矛盾逐条给终态**（以实测或代码为准，写明「哪个对、哪个改、commit」）：
     ① `code-health:406`「配置加载+恢复未测」vs 已有 `test_config_recovery.py`（实为**耗时**未测）；
     ② READMEs「0.7.2 未重测」vs `HANDOFF:223` 把同批数字标成 v0.7.2；
     ③ dot2 README「volume 未测」vs `milestone-m1:23`「音量/响度 ✅」；
     ④ `socket-plug-v1.md:309` 说 plug-test 子进程 vs `plug-test-kit.md:22` 说 in-process；
     ⑤ barge 指标 stop-push ≤300 ms vs acoustic P50 ≤600 ms（README 的 8/8、205.2 ms 未说明是哪个）；
     ⑥ 测试总数 1512 / 1518 / 1571 三口径；
     ⑦ xiaoai `CHANGELOG` 把历史 19/20、205.2 ms 放在 `### Verified` 下，而 README 说没有 0.7.2 重测；
     ⑧ canary 文档「wake/playback/AEC/volume 未验证」vs dot2 README 的 30/30（不同轮次，需互相加注日期与范围）。
  4. 每条未验证项给出**归类**：`CI 可测`（进 0.7.3/0.7.7 卡）或 `真机协议`（进手册/审计文档，含命令 + 判据 + 记录表）。

### 3.2 0.7.4 安装路径 TLS 与 Done 第 6 条重开（E）

**K-0.7.4-1 `[impl] 安装路径补 TLS 材料`**
- 背景（实测事实）：`rui-dot2-bridge/src/rui_dot2_bridge/device_link.py:261` 在 `tls_port` 开启时要求 `secrets/server.pem|key`，缺失即 `RuntimeError: missing TLS material`；`examples/two-speakers/compose.yaml` 只挂 `./data`，且没有 `DOT2_SECRETS_DIR`。所以「带两个桥冷启动」时音频平面起不来。
- 写范围：`[house-ear] examples/two-speakers/compose.yaml`、`scripts/make_dev_tls.sh`（新增）、`README.md`、`docs/deploy.md`、`[dot2] README.md`、`[xiaoai] README.md`
- 验收标准：
  1. compose 增加 `- ./secrets:/opt/rui-secrets:ro` 与 `DOT2_SECRETS_DIR: /opt/rui-secrets`，注释写明「已有原 controller 证书就拷贝；没有就用脚本自签」；
  2. `scripts/make_dev_tls.sh` 用 `openssl` 生成 `server.pem/server.key`（`subjectAltName=DNS:echomuse-controller`，RSA 2048，有效期与用途写进注释），并打印 `openssl x509 -noout -text | grep -A1 "Subject Alternative Name"` 的校验输出；
  3. 三仓 README 的「让 AI 帮你装」加入这一步（≤10 行提示词，含失败与回滚）；
  4. **风险必须写明**：自签证书只能让音频平面起得来；真机是否接受取决于设备端信任的 CA（原 controller 证书丢失 = 设备需重新配网），手册与 README 都要讲。
- 验证：`bash scripts/make_dev_tls.sh && ls -l secrets/ && openssl x509 -in secrets/server.pem -noout -subject -ext subjectAltName`。

**K-0.7.4-2 `[measure] TLS 必需性实测与表述统一`**
- 目标：用一次实验结束 README:126（「TLS 不是必需」）与 HANDOFF:325（「缺 TLS 音频平面拒绝启动」）的矛盾。
- 写范围：`[house-ear] docs/audit/tls-required-2026-10.md`（新增）、`README.md`、`docs/HANDOFF.md`
- 验收标准：
  1. 三个矩阵各跑一次并贴真实输出：①只挂 dot2、无 `./secrets`；②挂两个桥、无 `./secrets`；③挂两个桥、有自签 `./secrets`；
  2. 每次记录：容器状态、`/health` 的 `devices`/`device_errors`/`audio_plane`/`adapter_plugins`、日志第一条相关错误（`RUI_DEVICE_TOKEN is empty` / `missing TLS material`）；
  3. 结论写成一句可判定的话（谁需要 TLS、谁不需要、缺了会怎样），README 与 HANDOFF 引用同一次实测，不再各写一套；
  4. 若 ① 与 ② 结论不同，必须在文档里点明差异来源（哪个插件、哪一行代码）。

**K-0.7.4-3 `[accept] Done 第 6 条重开：五条判据实测`**
- 目标：把 Done 6 的判定标准升级为 a–e，五条缺一不可，不许照抄旧结论。
- 写范围：`[house-ear] docs/audit/done6-0.7.8.md`（新增）、`docs/release/acceptance-4devices.md`、`docs/HANDOFF.md`
- 验收标准（每条都要命令 + 输出）：
  - a) `docker inspect -f '{{.State.Health.Status}}' rui` → `healthy`；`RestartCount=0`；
  - b) `/health`：`devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`；
  - c) `/health`：`audio_plane=true` 且两台设备各有一个 plane（贴出 plane/设备对应关系）；
  - d) 真机一条完整链路：唤醒 → 指令 → 播放（贴日志行号），证明不是「起来了但不工作」；
  - e) 升级一次 + 回滚一次（记录镜像、digest、耗时、恢复后 health）。
- 说明：c/d/e 需要 NAS + 两台真机；执行者按 `docs/device-access.md` 接入（不含口令），Lead 复核原始输出。

### 3.3 0.7.5 小爱真正摘出（B）

**K-0.7.5-1 `[impl] 删除仓内 lx06 与 adapter_aliases，改纯外置加载`**
- 现状（已核）：仓内仍有 `plugs/xiaoai/`（`plugin.yaml: trust: builtin`、`xiaoai_plug/`、`devices/lx06.yaml`），`Dockerfile:168-209` 把它烤进镜像并硬编码冒烟测试，`pyproject.toml:61` 把 `plugs/xiaoai` 写进 pytest `pythonpath`，`src/rui/adapter_aliases.py` 保留 `xiaoai→lx06` 与 `LEGACY_DEFAULT_ADAPTER`。外置版本在 `rui-xiaoai-bridge`（`0.2.1`，`trust: trusted`）已存在且功能等价。
- **Lead 已定决策（解决「删掉映射又要映射」的措辞矛盾）**：`adapter_aliases.py` **模块删除**；`adapter: xiaoai` 的一次性改名搬进既有配置迁移链路 `src/rui/config/store.py`（v1→v2 migrate 已在此），读取时重写为 `lx06` 并记一条弃用告警；告警经 `config_warnings()`（`config/schema.py:1167`）→ 日志 WARNING + `/health.adapter_plugins.warnings` + 设备级 `legacy_adapter_spelling` 三个出口。0.8.0 删的正是这段迁移改写（与「删 adapter_aliases + 契约窗口调整」对应）。
- **发布顺序（硬约束）**：先发 `rui-xiaoai-bridge`（tag + GHCR digest 记录），确认外置 plug 在 0.7.4 镜像里能作为 image root 加载、`GET /api/v1/plugs` 只看到外置 `xiaoai-lx06`、`/health.device_errors=0`，**再**发 core `0.7.5`。禁止反序（core 先发会让现有生产配置哑掉）。
- 写范围：`[house-ear] plugs/xiaoai/**`（删除）、`src/rui/adapter_aliases.py`（删除）、`src/rui/config/**`、`src/rui/plugins/**`、`src/rui/device/**`、`Dockerfile`、`pyproject.toml`、`config.example.yaml`、`tests/**`、`docs/**`
- 验收标准：
  1. `plugs/` 目录不再随仓/镜像交付；`Dockerfile` 的插件冒烟改为扫描 `plugs/*/plugin.yaml` 的通用逻辑（无设备名硬编码）；
  2. `adapter_aliases.py` 删除，所有调用点改为「外部 plug 的 canonical id」；`DeviceConfig.adapter` 缺省值不再回退到 `xiaoai`；
  3. 配置写 `adapter: xiaoai` 时：**仍能解析到外置 `lx06` plug**，同时打 `WARNING` 一条（含 `xiaoai is deprecated, rename to lx06; removal in 0.8.0`），并在 `/health.adapter_plugins.warnings` 计数 +1；
  4. `tests/test_kernel_decoupling.py` 白名单清空并扩展到 `src/rui/web/**`；`git grep -in 'xiaoai\|lx06\|小爱' -- src/` 只剩 `web/` 里**非设备专用**的 manifest 渲染路径（若还有，进 K-0.7.5-2）；
  5. 外置挂载场景 `/health adapter_plugins.registered` 含 `["dot2","lx06"]`；不挂 xiaoai 目录时只有 `["dot2"]`，且给可读错误（不是静默无设备）；
  6. 三仓测试绿；`rui_compat` 与 C-001/C-002 字段一字不改。
- 验证：`git grep -in 'xiaoai\|lx06\|小爱' -- src/ plugs/`、`pytest -q`、容器内 `/health`、`docker compose logs | grep -i deprecat`。

**K-0.7.5-2 `[impl] 核心/控制面去耦：不再猜设备参数`**
- 背景（来自 `docs/audit/xiaoai-decoupling.md` §2.3，均为未修项；行号以 0.7.2 代码为准，实施前先复跑 grep）：`src/rui/web/api/device_rules.py:32-34` 用 `DEFAULT_LISTEN_PORT = 4412` 给未知 plug 兜底；`src/rui/web/api/app.py:2625` 硬编码厂商唤醒词「小爱同学/小爱/小爱小爱」；`web/src/i18n.ts` 16 处把 open-xiaoai 安装步骤写死（应渲染 manifest `device_side.install_steps`，C-001 已有字段）。
- 写范围：`[house-ear] src/rui/web/api/device_rules.py`、`src/rui/web/api/app.py`、`src/rui/device/**`、`web/src/i18n.ts`、`web/src/**`（仅相关组件）、`tests/**`
- 验收标准：
  1. 未声明 `listen_port` 的 plug 不再默认 4412；缺省时要么拒绝并要求配置（给出字段路径），要么由 manifest 必填（选一并写进 C-002 注记）；
  2. 厂商唤醒词由 manifest/配置提供（如 `wake.vendor_words`），核心无中文品牌字面量；
  3. 向导/设备详情页的安装步骤从 manifest `device_side.install_steps` 渲染，i18n 只留通用标签；
  4. 三个改动都有测试（含「未知 plug 缺 listen_port」与「manifest 无 vendor_words」的降级路径）；
  5. `git grep` 门禁（含 web）通过。

**K-0.7.5-3 `[verify] 新旧双跑对比 + 迁移文档`**
- 目标：证明外置加载与 0.7.2 内置加载行为等价，且旧配置可平滑迁移。
- 写范围：`[house-ear] docs/audit/xiaoai-extraction-0.7.5.md`（新增）、`docs/audit/xiaoai-decoupling.md`（追加结论）、`docs/contracts/C-002-console-v2-api.md`（只加注记）、三仓 `CHANGELOG.md`
- 验收标准：
  1. 双跑矩阵：0.7.2（内置）vs 0.7.5（外置）在同一配置、同一台 LX06 上，比较 `adapter_plugins.registered`、`devices`、`device_errors`、`audio_plane`、首次唤醒→应答→播放日志；差异逐条解释；
  2. 旧配置（`adapter: xiaoai`）与新配置（`adapter: lx06`）各跑一次，前者必须出现弃用告警且功能正常；
  3. 迁移步骤三段：不改配置（有告警可用）/ 改名（推荐）/ 0.8.0 后必须改名；
  4. 明确记录「`plug-test` 与 `registered` 的判定口径」，避免再次出现「同名 id 被内置遮蔽」的旧问题。

### 3.4 0.7.6 控制台功能审计（C 上半）

**K-0.7.6-1 `[audit] v2 逐页逐按钮实测`**
- 范围：`/v2` 下 14 条真实路由（`/devices`、`/devices/add`、`/devices/:id`、`/rooms`、`/setup`、`/run`、`/conversation`、`/logs`、`/memory`、`/voice-assistant`、`/tools`、`/music`、`/credentials`、`/health`；`/config` 为 302 → `/voice-assistant`）+ 全局壳（顶部导航、Run/Config 子导航、语言、主题、token 对话框、meta 门禁重试）。
- 写范围：`[house-ear] docs/audit/console-functional.md`（新增）、`docs/audit/console-shots/**`（新增截图）
- 验收标准：
  1. 文档首表：路由 × 页面标题 × 打开结果（200/302/404）× 截图文件名；
  2. 每个交互元素一行：`file:line`、动作说明（点击/输入/保存/开关/危险操作）、调用的 API（方法 + 路径）、结论（可用/不可用/文案错误）、证据（截图 + 日志或响应）；
  3. 每个「保存」动作必须证明**落库**（保存后重新加载页面/读 API 看到新值），不能只看 toast；
  4. 危险操作（删除设备/房间、重置配置、清空记忆）必须在测试环境演练并记录二次确认行为；
  5. 空状态、错误态（API 500/离线）、权限态（无 token）各至少一例；
  6. 审计基线实例：本地 `rui serve --no-audio` + 示例数据（`Living Room Speaker`/`Bedroom Speaker`），截图不得含内网 IP/token/真实房间名。
- 验证：Chrome headless 抓图命令 + `curl` API 对照（命令写进文档附录）。

**K-0.7.6-2 `[impl] 修 P0/P1 不可用项（或标实验性）`**
- 写范围：`[house-ear] web/src/v2/**`、`src/rui/web/api/v2_api.py`（必要时）、`tests/**`、`docs/audit/console-functional.md`
- 验收标准：
  1. 审计出的 P0（页面打不开/保存不生效/数据丢失）与 P1（主要按钮无反应）全部修掉，或经 Lead 同意标「实验性」；
  2. 每修一项，审计文档对应行改为「已修（commit + 复测截图）」；
  3. 标「实验性」的项：页面上有可见标识 + 写进「已知限制」（`docs/release/v0.7.8.md` + README 已知限制段）；
  4. 新增/修改行为有测试；无死调用（前端调了不存在的端点）与孤儿端点清单。
- 验证：`npm run typecheck && npm run build`、v2 回归测试、审计文档复测行。

**K-0.7.6-3 `[docs] v1 → v2 功能对齐矩阵`**
- 写范围：`[house-ear] docs/audit/console-v1-v2-parity.md`（新增）
- 验收标准：
  1. v1 每个页面/功能一行：v1 路径/入口、v2 对应路由或元素、状态 ∈ {有, 部分, 无}、差异说明；
  2. 「无」的项给出处置 ∈ {0.7.7 补, v2 明确说明（位置）, 有意删除（原因）}——**不允许空白**；
  3. 结论段列出「v1 有而 v2 真缺」的清单（0.7.7 的实现输入）。

### 3.5 0.7.7 v2 收口与测试补齐

**K-0.7.7-1 `[impl] v2 补齐 v1 缺口或明确说明`**
- 写范围：`[house-ear] web/src/v2/**`、`src/rui/web/api/**`（必要时，C-002 只做 additive 注记）、`tests/**`、`docs/audit/console-v1-v2-parity.md`
- 验收标准：
  1. 0.7.6 矩阵里每个「补」项实现并复测；每个「说明」项在 v2 界面有可见入口（帮助/提示/空状态文案）；
  2. 矩阵每一行状态为终态，无「待定」；
  3. 新增 API 字段（若有）在 C-002 有 additive 记录并保持 `api_version=2.0`。
- 验证：审计文档 + 逐页截图 + typecheck/build。

**K-0.7.7-2 `[impl] /legacy 弃用页与老链接 302 映射（预演，不切默认）`**
- 写范围：`[house-ear] src/rui/web/**`、`web/src/**`、`tests/test_console_default.py`（新增）、`docs/audit/console-redirects.md`（新增）
- 验收标准：
  1. `/legacy` 返回 200，顶部固定横幅「已弃用，0.8.0 移除」（中英），文案里给 `/v2` 链接；
  2. 老链接映射表（至少覆盖 v1 的全部顶层入口：`/`、`/setup`、`/devices`、`/rooms`、`/config`、`/logs`、`/conversation`、`/memory`、`/health`、`/tools`、`/music` 等，按实际 v1 路由表补全）→ 302 到 v2 对应页；未匹配路径 302 到 `/v2/` 而不是 404；
  3. **本版不切默认**：`/` 仍进旧面板（或维持当前行为），只把映射与横幅做好并测试；
  4. 单测覆盖每条映射（含 query 保留规则）。
- 验证：`curl -sI localhost:PORT/<old>` 逐条 + `pytest tests/test_console_default.py`。

**K-0.7.7-3 `[test] 未验证清单 batch-2 + 覆盖率补齐`**
- 写范围：`[house-ear] tests/**`、`docs/audit/unverified-inventory.md`、`docs/audit/coverage-baseline.md`
- 验收标准：
  1. batch-2（0.7.3 剩余的可 CI 项）全部落地；真机项写成「协议卡」：设备、命令、判据、记录表模板；
  2. 覆盖率不低于 0.7.3 基线，逐包列出增量；
  3. 三仓全绿 + ruff。

### 3.6 0.7.8 默认切 v2 + 验收节点（D + A 收尾 + `[phase]`）

**K-0.7.8-1 `[impl] 默认入口切换到 v2`**
- 写范围：`[house-ear] src/rui/web/**`、`web/src/**`、`web/index.html`（或构建入口）、`README.md`、`docs/**`（链接）、`tests/**`
- 验收标准（每条贴命令 + 输出）：
  1. 全新部署（空 `data/`）访问 `http://<host>:8080/` → 落到 v2（`/v2/` 或 v2 直接挂在 `/`，二选一并写明理由）；
  2. `/health` 的 `console` 字段 = `v2`（不再是 `legacy`）；
  3. 旧面板不再是默认入口，且**不出现在 v2 导航里**；`/legacy` 200 + 弃用横幅；
  4. 老书签/老链接 302 到 v2 对应页（与 0.7.7 映射表一致，逐条实测）；
  5. `README` hero 截图换成 v2 主界面（示例数据、无内网 IP/token）；文档内 `/v1` 链接全部改 `/v2`（`grep -rn '/v1' README.md docs/*.md` 只剩历史 release notes/archive）；
  6. 回滚一行可写：`RUI_CONSOLE=legacy`（或等价配置）切回旧面板，实测一次。
- 验证：`curl -sI http://<host>:8080/`、`curl -s /health | jq .console`、逐条 `curl -sI` 老链接、截图对比。

**K-0.7.8-2 `[docs] 人肉部署手册（Lead 亲手跑通）`**
- 写范围：`[house-ear] docs/runbook/upgrade-0.7.2-to-0.7.8.md`（新增）
- 内容（必须逐步、可复制粘贴）：前置检查（当前镜像/health/备份）→ 拉取新镜像并核对 digest → 配置变更（TLS/secrets/adapter 名/console）→ `compose up -d` → 健康检查五条（Done 6 a–e 的精简版）→ v2 验收 → 失败回滚（改回 `rui-dot2:1.3.1` + `--pull never`）→ 回滚后核对。
- 验收标准：
  1. 从生产 `rui-dot2:1.3.1`（核心 0.7.2）**真实升级到 0.7.8**，逐步贴 expected 输出与实际输出；
  2. 每条命令的 expected 与实际一致；不一致处必须解释并修正手册；
  3. 末尾附「回滚演练」实测（真回滚一次再升回去，或至少在生产机跑一遍回滚后核对）；
  4. 手册由 Lead 本人执行，不接受「按文档推断」。

**K-0.7.8-3 `[phase] 0.7.8 验收收口`**
- 写范围：`[house-ear] docs/HANDOFF.md`、`docs/ROADMAP-0.8.md`、`docs/audit/coverage-0.7.8.md`（新增）、`docs/release/v0.7.8.md`（新增）
- 验收标准：
  1. A：`coverage-0.7.8.md` 含覆盖率、未覆盖清单、每条「未验证」→ 测试/协议的映射表；CI 四绿（贴 run 链接）；
  2. B：`/health` registered、核心 grep 0 命中、双跑表；
  3. C：两份审计文档终态；
  4. D：本版第一条卡的六条实测；
  5. E：Done 6 a–e；
  6. HANDOFF 三节（`## 版本与契约` / `## 已完成` / `## 下一步`）+ `phase-close: <task-id>`，`phase_close` 通过后才 complete；
  7. 三仓 tag/release/镜像 digest 记录；三仓工作树干净；**停下等 9 哥 亲手部署**，不自行开 0.8.0。

## 4. 跨版本硬约束

| 约束 | 具体做法 |
|---|---|
| 契约窗口 | 三仓 `rui_compat` 保持 `>=0.6.0, <0.8.0`；本路线图不新增契约编号；0.7.5 的 adapter 弃用写成 `C-002` 注记 + 迁移说明 |
| 发布顺序 | 上游（house-ear）先发兼容版本并记 tag/digest → 下游桥 pin 住再升级；禁止下游依赖未发布的上游改动。**唯一例外在 0.7.5**：`rui-xiaoai-bridge` 必须先发（它才是删掉内置实现后的唯一 lx06 来源），core 后发 |
| 版本号 | core `0.7.3…0.7.8`；桥只在确有改动时 patch bump（dot2 `1.3.x`、xiaoai `0.2.x`），且始终 `<0.8.0` |
| CI | 每版必须 ci / security / secret-scan / docker-publish 四绿；0.7.3 起 ci 产出覆盖率 artifact |
| 证据 | 每个出口条件写进任务卡，报告里给命令 + 真实输出；失败如实写，不隐瞒 |
| 事件熔断 | 任一卡最多 3 轮；超过即 park，写原因与已知限制，不阻塞后面的版本 |
| 上下文 | 每版收口更新 HANDOFF；≥50% 上下文先写 HANDOFF 再继续 |

## 5. 0.8.0 剩余范围（本路线图只登记，不执行）

0.7.5 已把「删内置 lx06 插件」与「删 `adapter_aliases.py` 模块」提前做掉；而且 0.7.5 保留的
`adapter: xiaoai` 一次性改名（搬进 `config/store.py` 迁移 + 告警）**按定义为 0.7.x 兼容期存在**。
因此 0.8.0 只剩：

1. 删除 `/legacy` 面板与全部老链接 302（0.7.x 的兼容层）；
2. 移除 `adapter: xiaoai` 的迁移改写与弃用告警（0.7.x 弃用期结束）——这就是「删 adapter_aliases」在 0.8.0 的落点；
3. 契约窗口调整（三仓 `rui_compat` 上界抬到 `0.9.0` / 或按 C-003 重定）；
4. 迁移文档 + breaking notes（桥先发双窗口版本，core 后发；禁止反序）。

> **需 9 哥 确认这一读法**：要求里「删内置 lx06 与 adapter_aliases」放在 0.8.0，但同一段又要求
> 0.7.8 之前完成小爱摘出且保留兼容映射。本路线图按「0.7.5 删实现与别名模块 + 保留一次性改名告警；
> 0.8.0 删改名告警」执行——这是唯一能同时满足两条的读法，已写进 §3.3 决策。

**禁止提前动**：0.8.0 的任何一项都不许在 0.7.8 之前顺手做。

## 6. 不在本路线图

- **4 台设备联调**：设备到货后再插队（先 EQ 耳测 → 清 `DOT2_CONTROL_PORT` → 逐台 `listen_port` → v2 添加 → 逐台验收）。
- **对外宣传/公告**：除非 9 哥 明确说。
- **`runner.abort()` 跨房隔离**：0.7.8 不修，写进已知限制（单会话限制；按房隔离只覆盖播放与回合仲裁），0.8.x 再评估。
- **B1 中文唤醒模型 / adb 验证**（task-3 仍 BLOCKED，前置未变）。
- **24h 稳定性满窗**：0.7.8 手册里给「部署后 24h 复查」清单，但结果不必等它才发版。

## 7. 每版收口清单（Lead 逐条打勾）

1. 三仓 `git status --short -uall` 空；`git diff --stat` 与卡范围一致；
2. Lead 亲自重跑关键测试（至少一次全量）；
3. 任务板无 in_progress/pending 残留，或被写成「遗留项」（带原因）；
4. `docs/HANDOFF.md` 更新：版本与契约 / 已完成（命令+结果）/ 下一步；
5. `docs/ROADMAP-0.8.md` 对应行的状态更新（出口条件逐条标 ✅/❌ + 证据链接）；
6. tag + release + 镜像 digest + `/api/v2/meta` 核对（发布版）；
7. 报告给 9 哥：变更、证据、评审/验证结论、风险、下一步。
