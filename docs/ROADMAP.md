# ROADMAP — rui / 小瑞

> 计划：见 [plan-to-0.6.md](plan-to-0.6.md)（0.5.3 稳定化 → 0.6.0 设备无关内核）。
> 目标载体：**NAS**。当前：**0.5.2 已发布**（安装路径 #15 + 卫星就绪/连接测试 #7），生产跑 0.5.0 且健康；多设备 0.5（config v3 + Phase 2 多平面/仲裁）已就绪，`multi_device.enabled=false` 待 LX06+2×Dot2 真机验收。
> 每条都要有可复现判据；过程数字在 `HANDOFF` 与验收工作区，详细历史在
> `docs/archive/HANDOFF-history-to-2026-10-24.md`。

## 现状快照（2026-10-25，0.4.2 + 0.5 Phase 1）

- 版本/发布：0.4.2（Phase 4）已发布并部署；控制台导航收敛为单一 PanelSpec nav manifest（11 面板，7 个 legacy panel/旧路由退役），README 瘦身并新增多设备设计文档；生产 `/vol1/rui` 跑官方 `0.4.2`（回滚 `0.4.1` / `0.4.0`），healthy、音箱在线、非 root uid 1000。
- 质量：全量 **1256 passed / 18 skipped**；ruff clean；secret-scan 绿；web typecheck/build 绿；CI 原生 runner（PR #10）已合入，publish 不再被 QEMU arm64 卡 40min。
- 已交付：v1 四门槛；0.1–0.4 发布；Socket/Plug v1；控制台 IA Phase 1–4（两分组、合并页、单一 nav manifest、legacy 退役）；P3.5 安全；CHANGELOG/发布说明工具；Jellyfin/Emby 音源；首次登录 token 文件；dot2 修复；README/first-run 重写；多设备/卫星/唤醒仲裁设计文档 `docs/multi-device-design.md`（§9 决策已冻结）；0.5 config v3 Phase 1（每设备音频覆盖 + `multi_device` 块，仍单 active）；只读 YAML 配置视图（系统页 `GET /api/v1/config/raw`）；Phase 2 四个内部模块（音频面候选/准入 + `post_playback_mute_ms`、`fleet.py`、`arbiter.py`、`coordinator.py`）。
- 关键文档：`HANDOFF.md`、`docs/socket-plug-v1.md`、`docs/console-ia-audit.md`、`docs/install-flow-audit.md`、`docs/provider-presets.md`、`docs/music-sources.md`、`docs/integrations-mcp.md`、`docs/security.md`、验收工作区 `04_Docs/20261024-house-ear-acceptance/`。
- 已知问题/欠账：Phase 1b 控制台多设备状态；Phase 2 多平面/仲裁（真机未验）；dot2 设备内层 token 轮换契约；socket sidecar/播放 gap；README 截图待刷新。
- 下一步：**`app.py` 接线**（fleet 启动 + 逐设备回调 + 仲裁接线 + `devices[]` 健康），随后控制台状态、打磨批、v0.5.0 发布；dot2 已按 9 哥 安排开始 0.4.2 冷测试（.34 受控切换）；生产仍 0.4.2，多平面未经真机验证前 `multi_device.enabled=false`。

## 0. 先明确两个词

### 0.1 “冷装”是什么（为什么它是第一道门槛）

**定义**：在一台干净主机（或 NAS 的全新目录）上，**只用公开产物**（GHCR 镜像 + 公开仓脚本），
从执行安装命令开始，到音箱听到第一句回答，全程**不复用**既有 config / secrets / 模型 / 镜像缓存。

- 日常升级（改 `.env` 换镜像）不算冷装。
- 我们交付的是“别人或未来的自己能在 NAS 上重建”的系统；NAS 重装、换新 NAS、迁移，都走这条路。
- 它同时验证：`install.sh`、镜像发布、模型下载、Setup 向导、音箱接入——是唯一端到端手段。
- **本次验收做到哪**：423 s 到第一句，但用了缓存镜像 + 复制生产 config/secrets + 手工对齐 key，
  所以“真·陌生人冷装”（公开命令 → Setup 向导 → 自动下模型）仍记为欠账 P2.1。

### 0.2 发布门槛（v1，已全部通过）

| 门槛 | 结果 |
|---|---|
| 1. NAS 冷装 30 min 到第一句 | ✅ 423 s（7 min 03 s；install 234 s；镜像 `e3ea158`；测试手法见 0.1） |
| 2. 80% 音量放歌 20 次唤醒 ≥19 | ✅ 19/20（音量 204、音乐在放、2–3 m、AEC ERLE≈31.6 dB） |
| 3. 打断 ≤300 ms | ✅ 24.2 / 64.5 / 205.2 ms（服务端停推，max ≤300）；设备缓冲听感干脆 |
| 4. 公开仓可用 | ✅ repo/GHCR public、README 双语+截图、MIT、无 secret |

## 1. 历史状态（v0.1.0，保留作基线）

- 版本/发布：tag `v0.1.0`（release commit `1966db6`）；Release 已发；GHCR `0.1.0`/`0.1`/`latest`/`sha-1966db6`。
- 生产：NAS 跑 `ghcr.io/yes999zc/rui@sha256:694fc924…`，healthy；音箱在线；Telegram on、微信 off。
- 回滚：NAS 保留 tag `acceptance-f2abcef` 与部署前备份。
- 质量：749 passed / 17 skipped（无模型环境）、765/1（模型环境）；ruff clean；web typecheck/build 绿。
- 附加验收：Telegram 4/4、微信接口保留、配置恢复 smoke、备份/恢复 smoke。
- 文档：验收 `docs/acceptance.md`；运维 `docs/runbook.md`；历史 `docs/archive/HANDOFF-history-to-2026-10-24.md`。

## 2. 欠账清单（截至 v0.1.0，按“基础设施优先”重排）

| 组 | 欠账 | 说明 |
|---|---|---|
| P1 可靠性 | 空 config crash loop；旧 secrets 的 LLM key 命名；`fetch-models` httpx 无超时；验收产物随容器丢失；发布镜像不跑测试；ffmpeg GPL 包袱与第三方声明 | 影响下一个装的人/发布合规 |
| P2 基础设施 | **Socket & Plug 标准**（插头 manifest/打包/校验/信任）；**安装向导**（manifest 驱动）；**真·陌生人冷装**；设备管理后台；真恢复/换机；长稳/mihomo；生产微信 | 这是当前主线 |
| P3 功能/重构 | LX06 抽成插件包；dot2 桥按标准改造成第一个外部插头；第二台设备（config v3）；设备管理增强；安全三项；0.2.0 | 插件标准定稿后做 |
| P4 使用后打磨 | 音乐真凭据/收藏/续歌；控制台手机化；微信 QR 取消；咖啡链接；Logo GIF 等 | 9 哥 用几天后统一处理 |

**已由 9 哥 实测、不再记欠账**：远场 3–4 m 唤醒（实测通过，未留 N/M，不作为门槛证据）；
微信 iLink 真机文字/语音（真机通过；**生产机**仍未验，保留在 P2.6）。

## 3. 路线图（基础设施优先）

### P1 · 0.1.1 基础设施可靠性（1–2 天）

| # | 任务 | 判据 |
|---|---|---|
| P1.1 ✅ | 空 config 走 ConfigError/last-good，不再 defaults→crash loop + 回归测试（`f71958a`） | 清空 config 重启仍 healthy + banner；无 last-good 时给可读错误 |
| P1.2 ✅ | 旧 secrets 兼容：`RUI_LLM_KEY` 缺失时映射 `DEEPSEEK_API_KEY`；doctor 明确提示（`ae2105c`） | 复用旧 secrets 冷装不再“唤醒后不回答” |
| P1.3 ✅ | `fetch-models` httpx 超时（connect 10 s / read 60 s），失败快速切镜像（`ae2105c`） | 断网/直连失败 ≤30 s 内切源或明确失败 |
| P1.4 ✅ | 验收脚本输出默认落到宿主可见目录（`bfee677`） | 一次 barge/wake 后产物在工作区可读 |
| P1.5 ✅ | GHCR 发布前跑测试（`3ee86cc`） | 测试失败则镜像不发布 |
| P1.6 ✅ | ffmpeg 换成 LGPL-only 构建（pinned source 7.1.5 + build 内 license gate）+ `THIRD_PARTY.md` + README 致谢（`b71a7e7`；本机 amd64 已验证） | 镜像内 `ffmpeg -version` 无 GPL 组件；第三方清单齐全（open-xiaoai / EchoMuse / sherpa-onnx / SenseVoice / silero-vad / speexdsp / FFmpeg） |
| P1.7 🟡 | 0.1.1 回归：全量测试 ✅（815/17）+ 一次冷装循环（部署后做） | 时间不劣化（≤10 min 到第一句）；HANDOFF 记录 |

### P2 · 基础设施主线：Socket & Plug + 安装向导 + 设备管理（3–5 天）

| # | 任务 | 判据 |
|---|---|---|
| P2.0 | EchoMuse Device Management 调研 ✅ | `docs/device-management-research.md`（984 行）：流程映射、8 步向导、config.yaml+params、单设备 CRUD；**0.2 边界**：只做 manifest-only/内置可信插头，第三方可执行代码默认关闭（sidecar 后续）；不做 SQLite/approve/OTA/多设备仲裁 |
| P2.1 ✅ | **`docs/socket-plug.md` 标准 v0**：插头 manifest（机型/A/B 等级/能力/参数 schema/协议/兼容版本/交付物+可选 adapter）、打包与校验、A/B 最小清单、信任模型、运行形态（in-process vs sidecar） | 标准文档可让第三方不看源码写出插头；LX06 与 dot2 都能套进去 |
| P2.2 ✅ | **安装向导设计稿 + 后端切片**：`docs/install-wizard-design.md`（`5c7a0c4`）；`GET /plugs`、`/setup/step`（证据驱动 + setup.json + allowlist）与内置 LX06 manifest（`cfea073`）；UI 进行中 | 设计稿评审通过；后端 110 测试通过 |
| P2.3 ✅ | **真·陌生人冷装**：干净主机（无上下文智能体）85s 到 healthy，向导 8 步完成、首句真机走通；VAD/KWS 内置镜像 | 从零到第一句 ≈3m40s（含 239MB ASR）；文档/向导已验收 |
| P2.4 ✅ | 设备管理后台 + 控制台：设备/状态/参数编辑/连接测试/删除；后续 P3.4 加启停/令牌轮换/端口校验 | 已交付 |
| P2.5 🟡 | 备份/恢复 + 换机 smoke 已过；mihomo 失联提示/3 天长稳/断电自起待做 | 已记录 RUNLOG；长稳待补 |
| P2.6 ⏸ | 生产微信验证（真机已过）；等待 9 哥 手机/决策 | 保持 enabled=false |

### P3 · Socket/Plug v1 + 设备唤醒 + 安全（0.3.x，已完成）

| # | 任务 | 状态/判据 |
|---|---|---|
| P3.0 ✅ | Socket/Plug v1 规范：`docs/socket-plug-v1.md`（能力/manifest/adapter/device_wake/策略/迁移/测试工具） | Lead 决策 Q1–Q8 冻结；dot2 issue #3/#7 已答复 |
| P3.1 ✅ | 契约核心 + 测试工具 + LX06 参考插头：`DeviceWake/drain_wakes`、manifest v0/v1、音频策略/metrics/trace、`rui plug-test`、`plugins/rui-plugin-xiaoai` | 948→962 tests；LX06 v0/KWS 不回归；参考插头 kit 16 PASS |
| P3.2 ✅ | dot2 标准对齐：v1 契约 + 官方 downstream adapter 钩子（`RUI_ADAPTER_PLUGINS`/`rui.adapters`）；外部项目按规范迁移 | 0.3.1 提供钩子；不在核心写 dot2 分支 |
| P3.3 ✅ | 第二台设备/示例插头：`examples/plugin-example` + 契约测试；无需改核心 | 契约工具可本地跑通 |
| P3.4 ✅ | 设备管理增强：status/启停/token 轮换/端口冲突/多设备显式 warning + 控制台 | 0.3.0 交付 |
| P3.5 ✅ | 安全三项：SSRF 出站守卫、非 root 容器（read_only/cap_drop/state 迁移）、CVE CI（pip-audit/Trivy/SARIF） | 0.3.2 交付；扫描 green（16 条 Debian 已知 CVE 日期化 allowlist） |
| P3.6 ✅ | 发布：v0.3.0 / v0.3.1 / v0.3.2；生产已升级 0.3.2（非 root；本机构建镜像，官方 0.3.2 镜像 CI 仍在构建） | tag/Release 已推 |

### P4 · 使用后统一打磨（等 9 哥 用几天再排）
音乐真实凭据/收藏/续歌、控制台手机化（390 px/44 px/CJK/Logo）、微信 QR 取消、咖啡链接、其它体验债。

### P5 · 多设备（**发布版本已定：v0.5.0**，2026-10-25）

> 0.4.2 之后的下一个发布就是 **v0.5.0**：Phase 1（config v3）+ Phase 2（多平面/中央仲裁）+ 打磨批合并为一次发布。
> **不切 0.4.3，不公开 alpha/beta tag**：真机验证用 CI 推出的 `sha-<commit>` 镜像，最后一次只打 `v0.5.0`。
> 版本号在发布时升（`pyproject.toml` / `src/rui/__init__.py`），`CONFIG_VERSION` 保持 3。

| # | 任务 | 状态/判据 |
|---|---|---|
| P5.1 ✅ | Phase 1：config v3（`devices[].audio` 覆盖、`multi_device` 块、`effective_audio()` 继承、引用/房间校验、v2 文件保持 v2） | `tests/test_config_v3.py` 21 用例；全量 1173/18；LX06 行为零差异 |
| P5.2 ✅ | Phase 2 全部落地（`1b71584` 音频面、`a34cba5` fleet、`c0c2fc9` arbiter、`bd7c737` coordinator、`fce8d8c` 抽取接线、`e84fac4` fleet+仲裁接线、`ec8cf44` 逐设备健康）：候选/准入 + `post_playback_mute_ms`、每设备平面与看门狗、中央仲裁、fleet 启动/逐设备回调/`audio_devices` 健康/告警替换/设备写 fleet 重建 | app 级 fake `PlaneFleet` 测试 7 例；全量 **1266 passed / 18 skipped**；2×mock 真进程 smoke（2 plane、干净退出、无残留）；真机 §7.2 由 9哥 保留 |
| P5.3 ✅ | Phase 1b：控制台“未启用多设备”状态（体检页多设备卡片）+ `multi_device.*` / `devices[].audio.*` 目录字段（catalog + `设备与音频` 组的 `multi_device` 面板） | 目录契约测试（字段/effect/nullable 空值往返）；面板组断言；`web` typecheck+build 绿；全量 **1269 passed / 18 skipped** |
| P5.4 ✅ | 打磨批（dot2 #12）：HA 控制台表单/状态（0.4.2 已交付）、install.sh 升级提示（current `RUI_IMAGE` + 显式升级 + prune）、healthcheck 根因（device_errors / fleet / 逐房间）、`doctor` 的 `container_healthcheck` 一致性行、0.4.2 release note 占位注释已清、adapter 音量换算指向 `device/contract.py` helper、`docs/deploy.md` §7.1 下游升级 + `rui_compat` 兼容矩阵 | 随 v0.5.0 一起发，不单独发版；K3（vanilla `DOT2_*` 空转）已在 §7.1 说明；dot2 docs PR 当前 0 open，发布前再查 |
| P5.5 | Phase 3（v0.5.x）：房间策略与输出体验（room_priority/last_active、房间视图、全局 stop 语义） | 多房间一周误唤醒/抢占统计 |
| P5.7 ✅ | 安装路径 #15 + 卫星管理 #7：`docker compose exec` 两处 `</dev/null`（+ 端到端管道测试与静态不变量）、`ready`/连接测试按传输方式判定（内拨不再探 SSH，3 s 等待拨入）、`token_managed_by_adapter`、控制台新增设备默认空闲 id / 不适用徽章 | **v0.5.2**（`a26af81` + `fe16846`）；全量 1298/17、ruff/`sh -n`/web 绿；dot2 #15 的复测判据 = 管道安装 exit 0 + token 文件 |
| P5.6 ✅ | 冷装回归修复（dot2 #13/#14）：`7dbfecb`/`ef2affd` install.sh 引号 heredoc + 不再打印 token；`8433113`/`f032931` fetch-models 429/5xx 重试 + 安装失败非零退出 + `RUI_SKIP_MODELS=1`；`b55dcdb` doctor `models` 就绪行；`907fcd1` 文档 | **v0.5.1 已发布**（tag `v0.5.1`/`c0d7048`，digest `sha256:25d67cf88c62…`）；全量 1293/18、ruff/`sh -n` 干净；生产保持 0.5.0（无需升级） |

**v0.5.0 发布判据（全部满足才 tag）**

| 类别 | 判据 |
|---|---|
| 测试 | 全量 pytest 绿（含 Phase 2 表驱动仲裁、IPC admit/reject、N 平面 supervisor、崩溃隔离）；ruff clean；web typecheck+build 绿 |
| 真机 | LX06 + 2×Dot2：共存 `devices=2` 且双 `speaker_connected`；各自房间唤醒；同词双命中只产生 1 个 turn；播放中 barge 只归播放设备；杀一个平面其余可用；`multi_device.enabled=false` 回滚后 = 0.4.x 语义；记录 2/4 平面 RSS/CPU 与唤醒→首字延迟 |
| 迁移 | v2 config 原样加载、行为零差异；启用多设备缺 room 报 422；config 备份 + 回滚 0.4.2 路径演练 |
| 兼容 | dot2 `plug-test` 不回归；HA/音乐/渠道/控制台现有功能回归清单通过 |
| 文档 | `multi-device-design.md` §9/§12 冻结、console.md、deploy.md 下游升级章节、CHANGELOG、`docs/releases/v0.5.0.md` |
| 产物 | tag `v0.5.0`；GHCR `0.5.0` = `0.5` = `latest` 同一 manifest（amd64+arm64）；GitHub Release 含 digest 与升级/回滚说明 |
| 生产 | 部署/重启需 9 哥 明确指令；部署后 `/health` devices=2、设备在线、真实回合通过；失败回滚 0.4.2 |

生产仍 0.4.2；多平面未经真机验证前 `multi_device.enabled=false`。

**v0.5.0 已发布（2026-10-25 17:4x）**：tag `v0.5.0`；GHCR `0.5.0`=`0.5`=`sha-406785e`，digest `sha256:01a3afe18faa116f318223b8ce6596825fb61e2cd04789bc7c44d37de6184b15`（amd64+arm64）；发布时 `latest` 指向同一 manifest，之后随 `main` 移动。CI secret-scan/security/ci/docker-publish 全绿；GitHub Release 已发，#7 已公布「相对 0.4.2 契约变化=无」。

**生产已升级（2026-10-25 18:02，9哥 点头）**：`RUI_IMAGE=ghcr.io/yes999zc/rui:0.5.0`，healthy、uid 1000；`/health` version 0.5.0、config_version 仍 2、`audio_devices` living/客厅 running+connected、healthcheck ok、真实回合 `audio_bytes=120,912`；`multi_device.enabled=false`。备份 `.env.bak.20261025-180259`、`config/config.yaml.bak.20261025-180259`、`/vol1/rui-backups/rui-backup-20261025-180734.tar.gz`（sha256 `17711958…`）。真机 LX06+2×Dot2 验收与回滚演练待 9哥 安排。

**运行形态待定（P2.1 里定）**：音箱端 client/固件跑在音箱上（不在 NAS）；
RUI 侧 adapter 有两种形态——in-process（可信内置插头，简单）与 sidecar 容器（第三方插头隔离，NAS 上会多一个镜像）。
默认建议：内置 LX06 in-process；第三方插头默认 sidecar，除非签名并声明可信。

**顺序原则**：P1 先合入 0.1.1；P2 四项（标准/向导/冷装/管理）按序但可并行设计；P3 在标准定稿后开始。

## 4. 历史里程碑（详情见归档 HANDOFF）

M0 契约 → S1 音频面 → S2 设备接入 → S3 AEC+KWS → S4 回合 → S5 应答/质量 → S6-S14 播放链路/仲裁/灯环/音乐 →
2026-10-23 控制台 2.0 + HA/MCP + Channels + 公开仓/NAS 真机 → 2026-10-24 v1 验收 + v0.1.0 发布部署。
具体 commit、实测数字与踩坑见 `docs/archive/HANDOFF-history-to-2026-10-24.md`。

## 5. 明确不做（0.2 及以前）

- 多房间 / 多设备同时出声（一台设备同一时间一个播放 owner）
- 唤醒词自训练；云端 ASR 做主路；通用语音助手框架

## 6. 规矩

1. 一步一提交；提交信息写“改了什么 + 实测到什么”。
2. 没有数字的“已完成”不算完成；没有判据的步骤不进路线图。
3. 参数优先参考老项目；机型差异只进 adapter 层。
4. 阶段结束更新本文件 + `HANDOFF`（详细过程进归档）。
5. 生产变更等 9 哥 明确指令；token/密码/LAN IP 不进聊天/日志/仓库。
