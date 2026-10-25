# HANDOFF 2026-10-26 · 0.6.0 已发布 / 控制台 v2 M1+M2 已合并

phase-close: task-16

## 会话身份
- 模式：A（双仓库：rui 上游 + rui-dot2-bridge 下游），单会话单任务板
- 仓库：`$HOME/AI_Workspace/01_Code/projects/house-ear`（rui）、`$HOME/AI_Workspace/01_Code/projects/rui-dot2-bridge`（dot2）
- 分支/commit：rui `main` @ `b1d2e0c`，tag `v0.6.0` @ `adc4c2b`；dot2 `main` @ `9c696cd`

## 版本与契约
| 项 | 值 | 证据 |
|---|---|---|
| 上游 rui 发布 | tag `v0.6.0` @ `adc4c2b`；镜像 `ghcr.io/yes999zc/rui@sha256:abef1f276c3c21e9155d97c98913ee6c2e98fdfe842e43b90f64a5db6c12b241`（tags `0.6.0`/`0.6`/`sha-adc4c2b`/`latest`，amd64+arm64）；Release <https://github.com/yes999zc/rui/releases/tag/v0.6.0> | `docker-publish` run 40209595988 success；`gh release view v0.6.0` |
| 下游 pin | dot2 的 `deploy/downstream/Dockerfile` 与 compose 默认值 pin 上述 digest；`docs/canary-0.6.0-official-image.md` 记录 arm64 + x86_64 金丝雀；`docs/contract-report-0.6.0.json` 增 `upstream_pin` 块 | commit `9c696cd` |
| 生产（NAS 168） | 容器镜像 `rui-dot2:0.6.0-adc4c2b`（= 官方 0.6.0 digest + dot2 1.2.0）；`/health` version 0.6.0、config_version 3、devices 2、device_errors 0、living/dot2 双 plane connected；回滚镜像 `rui-dot2:0.6.2`；备份 `/vol1/rui/backup/20261026-0025/` | 见 §已完成；构建需 `DOCKER_BUILDKIT=0`（飞牛镜像站 401，已写入 dot2 README） |
| 契约 C-001 | `rui-device/v1`（manifest `audio_defaults` + `ack_echo_ignore_ms`） | `docs/socket-plug-v1.md` §3.2；`src/rui/config/schema.py` |
| 契约 C-002 | 控制台 v2 `/api/v2/*` 24 端点，1193 行，**冻结 PASS** | `docs/contracts/C-002-console-v2-api.md`；task-6 记录 |
| 兼容边界 | rui `>=0.6.0, <0.7.0`；0.6.x v1 仅 `/turns` 加 additive `device_id` | 契约 §10.1 |

## 已完成（含证据）
- **0.6.0 发布**：内核去厂商命名（74 处文案/注释中性化 + 守卫测试 + scripts P0 修）→ tag → CI 多架构镜像绿 → digest 入 release notes → Release 建立。tag 内不含 v2：`git ls-tree -r --name-only v0.6.0 | grep -cE 'web/src/v2|api/v2'` → 0。
- **内核彻底剥离小爱**：`docs/audit/xiaoai-decoupling.md`（分类 575 命中、web 27 条转交表、独立建库 topic 结论「0.7 再议」）；守卫 `tests/test_kernel_decoupling.py`；门禁 1349/17（当时口径）+ ruff clean。
- **控制台 v2 M1+M2 合并**（`d550d95`）：设计 `docs/design/console-ia-v2.md`（708 行，PASS）、契约 C-002（1193 行，PASS）、后端 `src/rui/web/api/v2_api.py`（24 端点）、前端 `web/v2.html` + `web/src/v2/**`（三段导航、`/run` 全屋、`/devices` 按房间、设备详情四 tab：概览/测试与动作/活动/连接）。旧控制台文件零改动；`npm run build` 双 dist。独立 verifier 用真实进程 + 两设备 + headless Chrome 验到 POST /test、activity type 过滤、reveal/rotate 429 冷却、`/v2` 深链、旧 `/` 不变；全量 1434 passed / 17 skipped。
- **写盘不变量统一**（`b1d2e0c`）：新增 `src/rui/web/api/device_write.py`，v1（enable/disable、PUT、DELETE）+ v2 + 向导共用「repair → 校验 → 端口冲突」；`_write_devices` 在 `save_config` 前兜底，任何写者无法绕过。修前 v1 可在多设备房子写出 `load_config` 失败的配置（已复现），修后正常用例响应逐字节一致、非法用例 422 且文件不变。`led.set` 加入 live-gate（断连 409 `device_not_connected` + hint）。全量 **1445 passed / 17 skipped**，ruff clean。
- **生产切换 + hot 层清理**：切到官方 digest 底座，`/health` 双 plane connected、`dot2 registered`、无 Traceback；hot1–hot4 与 `/home/yes999zc/hot` 已删，回滚镜像保留。
- **下游 repo**：pin 改动 + canary 文档已提交并推送（`9c696cd`）。

## 未完成任务
| 项 | 状态 | 卡点 / 下一步 |
|---|---|---|
| 控制台 v2 M3–M10 | pending | 设备增删改 + 配置 tab（M3）、音频覆盖编辑（M4）、房间/全屋 + 全局音频（M5）、八步向导 add/edit（M6）、运行类页面（M7–M8）、配置页组（M9a/M9b）、0.7 切默认（M10）。设计文档 §7 有每步验收 |
| verifier 记录的偏差（非阻断） | pending | ① `led.set` 已修；② disabled 设备的错误码不自洽（`device_not_connected` / `capability_unsupported` / `device_disabled` 并存）需产品裁决；③ C-002 §7.1 启动门禁在浏览器端未成立（`meta.ts:11` 初值 + `App.vue:225` 提前渲染导致资源重复请求） |
| v2 DELETE 的端口检查口径 | pending | v2 DELETE 仍 `check_ports=True`，与 v1 不一致；建议传 `check_ports=False` |
| `PUT /api/v1/config/values` | pending | 可写 `devices.N.*` 但不走共享不变量（全量校验保证不写坏文件，只是不享受 repair） |
| 真机语音验收（两轮告别路由） | pending | 必须 9 哥 本人；0.6.0 release notes 已写明这一项未勾 |
| 第二台 Dot2 共存 | pending | 硬件未到货 |
| 生产磁盘 ~11.3GB 可回收 | pending | 0.1.x–0.4.x 老镜像 + 3 个未挂载 volume；要不要清由 9 哥 定 |

## 下一步（按顺序，接手方可直接执行）
1. 门禁基线：`pytest -o addopts='' -q` → 0 failed / 17 skipped（当前 1445 passed）；`ruff check src tests` clean；`cd web && npm run typecheck && npm run build` → 双 dist。
2. 核对生产：`/health`（version 0.6.0、devices=2、errors=0、双 plane connected）；镜像 `rui-dot2:0.6.0-adc4c2b`；回滚镜像在位。
3. 控制台 v2：先让 9 哥 打开 `/v2` 试；再按 M3 → M4 → M5 → M6 推进写操作（每步独立可回滚，`meta.features` 可关）。动手前先修上面偏差 ③（meta 启动门禁），否则每个页面会重复请求。
4. 处理偏差 ②（disabled 设备错误码优先级）——建议契约层裁决：`device_disabled` 优先于 capability，随后同步测试与 UI 文案。
5. 等 9 哥 真机两轮验收；通过即 0.6.0 验收闭环，否则 0.6.1。
6. 0.7 规划：`/` → `/v2`、旧壳挂 `/legacy`、删 `src/rui/adapter_aliases.py`、评估小爱独立建库（审计报告 §4 的触发条件）。

## 约束与禁区
- 真机语音验收必须 9 哥 本人（家人睡觉时不能测）。
- 生产变更允许（已授权），但每次先备份 `/vol1/rui/.env`、`config/`、override，并保留回滚镜像。
- 不打印/不提交 token、密码、证书、内网 IP（secret-scan 会扫 docs：`/Users/` 与 RFC1918 都会红）。
- 不 fork rui 源码；dot2 只做 out-of-tree adapter + 下游镜像层。
- **已产出的 tag/镜像不移动**（v0.6.0 已出镜像；审计报告的 6 条 MINOR 修订跟 main 走、不进 tag）。
- 飞牛上 docker build 用 `DOCKER_BUILDKIT=0`。

## 已知风险与未决问题
- **混合房子必须写每设备覆盖**：全局 `audio.capture`/`ack.echo_ignore_ms` 会盖过 Dot2 manifest 默认 → `docs/deploy.md §7.4` + 生产配置。
- **adapter 静音窗**：`DOT2_PLAYBACK_GATE_MS=400` 是生产规避值，默认值仍待真机再定。
- **`lost_playing`**：告别播放瞬间唤醒会被仲裁拒绝。
- **`adapter_aliases.py` 只能活到 0.7**；web 面 27 条小爱字样仍在（旧控制台，随 v2 退役）。
- **v2 未切默认**：旧控制台仍是 `/`，v2 在 `/v2`；0.7 才切。
- **`/health` 离线即 unhealthy**：`rui-healthcheck` 在无音箱连上时 exit 1，属上游既有语义（切换后等设备回连约 12–60s）。
- **第二台 Dot2 行为** *(unverified)*：无硬件。

## 关键文件
- `docs/releases/v0.6.0.md` —— digest 与未验证项。
- `docs/contracts/C-002-console-v2-api.md` —— 控制台 v2 契约（冻结）。
- `docs/design/console-ia-v2.md` —— IA v2 设计（含 M1–M10 与 0.7 切默认 runbook）。
- `src/rui/web/api/v2_api.py` —— `/api/v2/*` 24 端点。
- `src/rui/web/api/device_write.py` —— v1/v2/向导共用的写盘不变量（repair → 校验 → 端口冲突）。
- `web/v2.html`、`web/src/v2/**` —— v2 控制台（`/run`、`/devices`、`/devices/:id` 四 tab）。
- `docs/audit/xiaoai-decoupling.md` —— 小爱剥离审计 + 独立建库 topic。
- dot2：`deploy/downstream/`、`docs/canary-0.6.0-official-image.md`、`docs/contract-report-0.6.0.json`。
- 内部：`04_Docs/rui-2026-10-26-night-report.md`（给 9 哥 的夜间报告）。

## 核验命令（接手方先跑一遍，再相信上面的版本号）
```bash
cd $HOME/AI_Workspace/01_Code/projects/house-ear
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
- **没做**：真机两轮告别路由（等 9 哥）；第二台 Dot2（无硬件）；v2 M3–M10；0.7 切默认。
- **不要相信**：`/tmp/rui-v2-evidence/`（mock harness）与 `/tmp/rui-m2-verify/`（真实进程但无真机）里的截图当生产证据。
- **不要相信**：`.34` 上的实例当作生产状态（它是金丝雀机，跑 `rui-dot2:0.6.0-official`）。
- **不要相信**：v0.6.0 tag 内的 `docs/audit/xiaoai-decoupling.md` 是最新修订（6 条 MINOR 修订在 tag 之后进 main，tag 不移动）。
- **未验证**：合成客户端证明管线，不证明真机麦克风/AEC/TLS 重连。

> 本会话停在：0.6.0 已发布并切到官方镜像底座；控制台 v2 的 M1（只读）+ M2（设备动作）已合并进 main 并通过独立评审与真实进程 E2E；写盘不变量已统一到 v1/v2/向导。下一个会话第一步：读本文件 → 跑门禁与 `/health` → 修偏差 ③（meta 启动门禁）→ 按 M3 起继续 v2。
