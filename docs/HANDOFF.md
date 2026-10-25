# HANDOFF 2026-10-26 rui 0.6.0 已发布 · 控制台 v2 进行中

phase-close: task-8

## 会话身份
- 模式：A（双仓库：rui 上游 + rui-dot2-bridge 下游），单会话单任务板
- 仓库：`/Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear`（rui）、`/Users/bakeyzhang/AI_Workspace/01_Code/projects/rui-dot2-bridge`（下游）
- 分支/commit：rui `main` @ `8400fdd`，tag `v0.6.0` @ `adc4c2b`；dot2 `main` @ `ba0ba54`

## 版本与契约
| 项 | 值 | 证据 |
|---|---|---|
| 上游 rui | **0.6.0 已发布**：tag `v0.6.0` @ `adc4c2b`，镜像 `ghcr.io/yes999zc/rui@sha256:abef1f276c3c21e9155d97c98913ee6c2e98fdfe842e43b90f64a5db6c12b241`（tags `0.6.0`/`0.6`/`sha-adc4c2b`/`latest`，amd64+arm64），Release <https://github.com/yes999zc/rui/releases/tag/v0.6.0> | `docker-publish` run 40209595988 → success；`gh api /user/packages/container/rui/versions` → 上述 digest |
| 下游 plug | `rui_dot2_bridge` **1.2.0**（`dot2-echomuse`），内置 `xiaoai-lx06 0.3.0` | 未变，沿用 `ba0ba54` |
| 设备固件 | Echo Dot 2 / EchoMuse `20261024-1911-dev` | 生产日志 `dot2 registered (fw 20261024-1911-dev, fireos, …)` |
| 契约 C-001 | `rui-device/v1`（manifest `audio_defaults` + `ack_echo_ignore_ms`） | `docs/socket-plug-v1.md` §3.2；`src/rui/config/schema.py` `AudioDefaults` |
| 契约 C-002（新） | `/api/v2/*` 设备为中心控制台 API，1193 行，**已 PASS 冻结**（sha256 `9ce7a201…`） | `docs/contracts/C-002-console-v2-api.md`；reviewer-contract PASS 记录在 task-6 卡 |
| 兼容边界 | rui `>=0.6.0, <0.7.0`；0.6.x 内 v1 API 零 breaking（`/turns` 仅 additive `device_id`） | `src/rui_dot2_bridge/plugin.yaml:5`；契约 §10.1 |

## 已完成（含证据）
- **0.6.0 正式发布**：`adc4c2b`（内核去厂商命名 + 守卫测试 + scripts P0 修复）→ tag → CI 多架构镜像绿 → digest 写进 `docs/releases/v0.6.0.md` → Release 已建。tag 内容审计：`git ls-tree -r --name-only v0.6.0 | grep -cE 'web/src/v2|api/v2'` → **0**（v2 工作不进 0.6.0）。
- **内核彻底剥离小爱**（`adc4c2b`）：74 处注释/帮助文案中性化（实测数字全部保留，AST 归一化验证 19/19 结构不变）、新增守卫 `tests/test_kernel_decoupling.py`（`src/rui` 出现 xiaoai/lx06/小爱 即失败，只放行 `adapter_aliases.py`，web 除外）、`scripts/measure_*.py` 的坏 import 改走 plug manifest（`scripts/_xiaoai_plug.py`）。审计报告 `docs/audit/xiaoai-decoupling.md`（含分类表与 web 转交表）。门禁 `pytest --ignore-glob='tests/test_api_v2_*.py'` → **1349 passed / 17 skipped**，`ruff check src tests scripts` clean。
- **小爱独立建库 topic 已评估（结论：0.7 再议）**：见报告 §4 —— 现在拆库边际收益小、发布/CI/pin 成本不对称；触发条件（C-001 v1 冻结、镜像可 pin digest、双侧 plug-test CI、真机人力）未满足。0.7 与删 alias 同批评估。
- **控制台 v2（从零重写）三个交付物已过评审**：设计 `docs/design/console-ia-v2.md`（708 行，reviewer-design 终审 PASS）、契约 `docs/contracts/C-002-console-v2-api.md`（1193 行，reviewer-contract PASS，4 BLOCKER 全修）、后端 `src/rui/web/api/v2_api.py`（3238 行，24/24 端点，64 个契约测试）。
- **前端 v2 M1**：`web/v2.html` + `web/src/v2/**`（22 文件 ~4.7k 行，`/run`+`/devices`+`/config` 三段导航、设备列表按房间分组、设备详情含能力 declared/probed/effective + 音频 provenance 五来源 + activity + 掩码连接信息、zh/en 双语、401/404/500/空/feature-off 态）；`vite.config.ts` 只加多页入口，旧控制台零改动，`npm run build` 同产 `dist/index.html` + `dist/v2.html`。截图证据 `/tmp/rui-v2-evidence/`。
- **生产今晚状态**（本地 0.6.2 构建，label revision `3d41890`）：`/health` → version 0.6.0、config_version 3、devices=2、device_errors=0、living/dot2 双 plane `speaker_connected=true`、`adapter_plugins.registered=["dot2"]`；RestartCount=0。
- **真机已通过**：Dot2 唤醒→「现在几点？」→答；LX06 唤醒→「今天周几？」→答；自回声被 `own voice heard back, no turn` 拦下；audio_bytes 63600/62916，gap=0。

## 未完成任务
| task | 状态 | 卡点 / 下一步 |
|---|---|---|
| 控制台 v2 M1 收口 | 代码完成、评审 CHANGES 待修 | 前端 3 MAJOR（`unknown_params` 展示、`DeleteDeviceResponse.removed` 改 boolean、删 `RestartResponse.restarted`）+ `/v2/run` 要真渲染 house 数据；后端 4 点已补齐。改完 → 独立 verifier E2E（本地 18080 起真实进程）→ commit |
| v2 工作落 main | pending | 前端/后端/文档（design/contract/audit 修订）一起提交，走 0.7 线；**不进 0.6.0 tag** |
| 下游 pin 官方 0.6.0 | pending（task-11） | 改 `rui-dot2-bridge` base → `ghcr.io/yes999zc/rui@sha256:abef1f27…`，生产 168 重建 + `/health` 验收 + 备份 |
| hot 层清理 | pending | `rui-dot2:0.5.2-hot1..4` 与 `/home/yes999zc/hot`；保留 0.6.0/0.6.2 回滚 |
| 告别只从本房间出（两轮真机） | pending | 必须 9 哥 本人；0.6.0 已带着这一项未验收发布（release notes 已写明） |
| 第二台 Dot2 共存验收 | pending | 硬件未到货 |
| `.34` 金丝雀部署 v2 | pending | 授权可用（`192.168.5.34`，`~/rui` 现 0.5.3）；用本地构建 + 双假设备配置验 `/v2` 与 `/api/v2` |
| `PATCH /house/audio` 显式写 null/核心默认值不落盘 | pending | 受 `config/store.py::_audio_for_disk` 限制，需单独立项（v2 声音层遗留） |

## 下一步（按顺序，接手方可直接执行）
1. 门禁基线：`.venv/bin/python -m pytest -o addopts='' -q` → 0 failed / 17 skipped（当前 1429 collected，含未提交 v2 测试）；`ruff check src tests scripts` → clean；`cd web && npm run typecheck && npm run build` → 双 dist 产物。
2. 核对发布：`git describe --tags` → `v0.6.0`；`gh release view v0.6.0`；生产 `/health`（version 0.6.0、devices=2、双 plane connected）。
3. 前端 v2 修 CHANGES → 独立 verifier E2E（真实进程 + `/api/v2/*` + `/v2` 深链 + `/api/v2/nope` JSON 404）→ 一起 commit 到 main（含 `docs/design`、`docs/contracts`、`docs/audit` 修订）。
4. 下游 task-11：pin 官方 digest → 生产 168 切换 → `/health` 验收 → 删 hot 层。
5. 等 9 哥 真机两轮告别路由验收；通过则视为 0.6.0 验收闭环，否则 0.6.1 修。
6. v2 继续 M2+：写操作（设备/音频编辑、能力动作、连接 reveal/rotate、向导 add/edit）按设计文档 `M2..M10` 拆任务；M10 才切默认（`/` → `/v2`，旧 shell 挂 `/legacy`）。

## 约束与禁区
- 真机语音验收必须 9 哥 本人（家人睡觉时不能测）。
- 生产变更允许（已授权），但每次先备份 `/vol1/rui/.env`、`config/`、override 文件，并保留回滚镜像。
- 不打印/不提交 token、密码、证书、完整内网 IP；secret-scan 会扫 docs。
- 不 fork rui 源码；dot2 只做 out-of-tree adapter + 下游镜像层。
- 不每步 push（push main 触发多架构镜像重建）；**已产出的 tag/镜像不移动**（v0.6.0 已产出镜像，不得重打）。
- 只改这两个仓库与 `04_Docs/`。

## 已知风险与未决问题
- **混合房子必须写每设备覆盖**：全局 `audio.capture` 与 `ack.echo_ignore_ms`(1200) 会盖过 Dot2 manifest 默认 → 见 `docs/deploy.md §7.4` 与生产配置（Dot2 capture 1ch/0ref/S16、ack 2500）。
- **adapter 静音窗**：1.1.3 默认“播放期+2500 ms”会吃第一句；生产用 `DOT2_PLAYBACK_GATE_MS=400` 规避，默认值待真机再定。
- **`lost_playing`**：告别播放瞬间唤醒会被仲裁拒绝，待评估。
- **`adapter_aliases.py` 只能活到 0.7**：新代码不得再写死 `xiaoai`（守卫测试已兜住 `src/rui`，`src/rui/web/**` 与 `web/src` 的 27 条遗留见审计报告转交表）。
- **v2 前端 `DeviceDetailView.vue` 843 行**：M1 允许不拆，加写操作前必须拆（评审已记录）。
- **v2 产物引用 `/assets/*`**：后端同时服务 `/assets` 与 `/v2/assets`；契约 §2.2 与 §2.1 对此表述不一致，M2 前需统一。
- **第二台 Dot2 行为** *(unverified)*：无硬件。

## 关键文件
- `docs/releases/v0.6.0.md` —— 0.6.0 release notes（digest 已补、未验证项已写明）。
- `docs/audit/xiaoai-decoupling.md` —— 小爱剥离审计 + web 转交表 + 独立建库 topic。
- `docs/contracts/C-002-console-v2-api.md` —— 控制台 v2 API 契约（冻结）。
- `docs/design/console-ia-v2.md` —— IA v2 设计（决策 B：新 SPA、旁路 `/v2`、0.7 切默认）。
- `src/rui/web/api/v2_api.py` —— `/api/v2/*` 24 端点实现（3238 行）。
- `web/v2.html`、`web/src/v2/**` —— v2 控制台（`/run`+`/devices`+`/config`）。
- `src/rui/adapter_aliases.py` —— 唯一 `xiaoai → lx06` 兼容别名（0.7 删）。
- `src/rui/config/schema.py`（`AudioDefaults`/`effective_audio`/`effective_ack_echo_ignore_ms`）—— 三层合成契约实现。
- dot2：`src/rui_dot2_bridge/{adapter.py,device_link.py,plugin.yaml,devices/rs03qr.yaml}`、`deploy/downstream/`、`docs/contract-report-0.6.0.json`。
- 内部：`04_Docs/rui-0.6.0-handoff-2026-10-25.md`、`04_Docs/rui-dot2-maintainer-charter-2026-10-25.md`、`01_Code/device-inventory.md`（凭证，0600）。

## 核验命令（接手方先跑一遍，再相信上面的版本号）
```bash
cd /Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear
git status --short && git log --oneline -3 && git describe --tags
.venv/bin/python -m pytest -o addopts='' -q
.venv/bin/python -m ruff check src tests scripts
cd web && npm run typecheck && npm run build
gh release view v0.6.0 --json url,tagName
# 生产（paramiko + sudo -S，见 04_Docs 交接文档 §3）：
#   docker ps --filter name=rui --format '{{.Image}}|{{.Status}}'
#   curl -s http://127.0.0.1:8080/health
```

## 没做的事 / 不要相信的地方
- **没做**：真机两轮「告别只从本房间出」（等 9 哥）；第二台 Dot2（无硬件）；v2 M2–M10（写操作、向导、M10 切默认）。
- **没做**：下游 pin 官方镜像 + 生产切换（task-11）；hot 层清理。
- **不要相信**：`/tmp/rui-v2-evidence/` 的截图是 mock harness 产物，不是真实后端渲染；真实 E2E 由 verifier 用本地真实进程跑。
- **不要相信**：`.34` 上的实例状态（当前 `rui-dot2:0.5.3`，仅金丝雀）。
- **不要相信**：v0.6.0 tag 内的 `docs/audit/xiaoai-decoupling.md` 是最新修订版（这 6 条 MINOR 修订在 tag 之后改，跟 main 走，tag 不移动）。
- **未验证**：合成客户端证明管线，不证明真机麦克风/AEC/TLS 重连；真机结论以 9 哥 两轮验收为准。

> 本会话停在：0.6.0 已发布（tag/镜像/Release 齐），内核剥离完成，控制台 v2 的设计/契约/后端全过评审、前端 M1 等最后 3 个 MAJOR 修完并做真实 E2E。下一个会话第一步：读本文件 → 跑门禁 → 修完前端 CHANGES → E2E → commit v2 → 下游 pin 与 hot 清理。
