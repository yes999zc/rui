# ROADMAP — rui / 小瑞

> 目标载体：**NAS**。当前：**0.4.2 已发布并部署（非 root）**，控制台 IA 重构 Phase 1–4 完成；下一步多设备 Phase 1（config v3，仍单 active）+ 只读 YAML 代码视图。
> 每条都要有可复现判据；过程数字在 `HANDOFF` 与验收工作区，详细历史在
> `docs/archive/HANDOFF-history-to-2026-10-24.md`。

## 现状快照（2026-10-25，0.4.0）

- 版本/发布：0.4.2（Phase 4）已发布并部署；控制台导航收敛为单一 PanelSpec nav manifest（11 面板，7 个 legacy panel/旧路由退役），README 瘦身并新增多设备设计文档；生产 `/vol1/rui` 跑官方 `0.4.2`（回滚 `0.4.1` / `0.4.0`），healthy、音箱在线、非 root uid 1000。
- 质量：全量 **1142 passed / 18 skipped**；ruff clean；secret-scan 绿；web typecheck/build 绿；CI 原生 runner（PR #10）已合入，publish 不再被 QEMU arm64 卡 40min。
- 已交付：v1 四门槛；0.1–0.4 发布；Socket/Plug v1；控制台 IA Phase 1–4（两分组、合并页、单一 nav manifest、legacy 退役）；P3.5 安全；CHANGELOG/发布说明工具；Jellyfin/Emby 音源；首次登录 token 文件；dot2 修复；README/first-run 重写；多设备/卫星/唤醒仲裁设计文档 `docs/multi-device-design.md`（设计稿）。
- 关键文档：`HANDOFF.md`、`docs/socket-plug-v1.md`、`docs/console-ia-audit.md`、`docs/install-flow-audit.md`、`docs/provider-presets.md`、`docs/music-sources.md`、`docs/integrations-mcp.md`、`docs/security.md`、验收工作区 `04_Docs/20261024-house-ear-acceptance/`。
- 已知问题/欠账：只读 YAML 代码视图（进行中）；多设备 Phase 1（config v3，§9 决策已冻结）；dot2 设备内层 token 轮换契约；socket sidecar/播放 gap；README 截图待刷新。
- 下一步：多设备 Phase 1（config v3，先保持 single-active；§9 决策已冻结）与只读 YAML 代码视图并行 → multi-plane/仲裁 → sidecar/播放 gap；dot2 已按 9 哥 安排开始 0.4.2 冷测试（.34 受控切换）；生产升级固定 `.env` 的 `RUI_IMAGE` + `docker compose pull && up -d`，先备份、失败回上一个 tag。

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
