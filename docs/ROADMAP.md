# ROADMAP — house-ear / 小瑞

> 目标载体：**NAS**。一句话现状：**v0.1.0 已发布并部署，四道 v1 门槛通过**；下一步按 P1 → P2 → P3。
> 每条都要有可复现判据；过程数字在 `HANDOFF` 与验收工作区，详细历史在
> `docs/archive/HANDOFF-history-to-2026-10-24.md`。

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

## 1. 当前状态（2026-10-24，v0.1.0）

- 版本/发布：tag `v0.1.0`（release commit `1966db6`）；Release 已发；GHCR `0.1.0`/`0.1`/`latest`/`sha-1966db6`。
- 生产：NAS 跑 `ghcr.io/yes999zc/rui@sha256:694fc924…`，healthy；音箱在线；Telegram on、微信 off。
- 回滚：NAS 保留 tag `acceptance-f2abcef` 与部署前备份。
- 质量：749 passed / 17 skipped（无模型环境）、765/1（模型环境）；ruff clean；web typecheck/build 绿。
- 附加验收：Telegram 4/4、微信接口保留、配置恢复 smoke、备份/恢复 smoke。
- 文档：验收 `docs/acceptance.md`；运维 `docs/runbook.md`；历史 `docs/archive/HANDOFF-history-to-2026-10-24.md`。

## 2. 欠账清单（截至 v0.1.0）

| 组 | 欠账 | 为什么现在记 |
|---|---|---|
| P1 | 空 config（0 字节）→ crash loop；旧 secrets 的 LLM key 命名；`fetch-models` httpx 无超时；验收产物落盘；发布镜像不跑测试 | 验收过程真实踩到，会影响下一个装的人 |
| P2 | **真·陌生人冷装**；远场 3–4 m 唤醒计数；barge 设备缓冲量化；微信真机；音乐真凭据/收藏；控制台手机化；真恢复/换机；mihomo 运行时依赖与长稳 | 门槛之外的真实体验与可运维性 |
| P3 | 第二台设备；多类型音箱标准与“插座/插头”；EchoMuse 设备管理；Agent/新渠道/Doctor Fix；安全三项 | 冻结解除后的功能线，不能混进 0.1.x |
| 杂项 | Buy Me a Coffee 真实链接；音箱 root 密码 squashfs 限制 | 发布门面/已知限制 |

## 3. 路线图

### P1 · 0.1.x 可靠性（1–2 天，先发 0.1.1）

| # | 任务 | 判据 |
|---|---|---|
| P1.1 | 空 config 走 ConfigError/last-good，不再 defaults→crash loop + 回归测试 | 清空 config 重启仍 healthy + banner；无 last-good 时给可读错误 |
| P1.2 | 旧 secrets 兼容：`HOUSE_EAR_LLM_KEY` 缺失时映射 `DEEPSEEK_API_KEY` 或 doctor 明确提示 | 复用旧 secrets 冷装不再“唤醒后不回答” |
| P1.3 | `fetch-models` httpx 超时（connect 10 s / read 60 s），直连失败快速切镜像 | 断网/直连失败 ≤30 s 内切到可用源或明确失败 |
| P1.4 | 验收脚本输出默认落到宿主可见目录（避免容器重建丢 JSON） | 一次 barge/wake 后产物在工作区可读 |
| P1.5 | GHCR 发布前跑测试（tag 触发的 workflow 依赖测试 job） | tag 推送时测试失败则镜像不发布 |
| P1.6 | 0.1.1 回归：全量测试 + 一次冷装循环 | 时间不劣化（≤10 min 到第一句）；HANDOFF 记录 |

### P2 · 体验与真机遗留（3–5 天，可并行）

| # | 任务 | 判据 |
|---|---|---|
| P2.1 | **真·陌生人冷装**：全新目录、只用公开命令、Setup 向导填 key、自动下模型、不复制生产配置 | 从零到第一句 ≤30 min，全程只按文档操作 |
| P2.2 | 远场 3–4 m 唤醒正式计数（20 次）+ barge 设备缓冲耳朵口径量化 | 留下 N/M 与听感/秒表记录，写入 acceptance 附录 |
| P2.3 | 微信 iLink 真机扫码/文字/语音；QR 登录补服务端取消 | 文字/语音各 2 条通过后再决定是否默认启用 |
| P2.4 | 音乐：NAS 真实 Navidrome 凭据确认、收藏/口味 UI、farewell 静音续歌边角、音量对账 | 真机点歌/收藏/续歌路径通过 |
| P2.5 | 控制台手机卡片化（390 px 无横向滚动）、控件 ≥44 px、CJK webfont、Logo GIF | 手机逐页截图通过；桌面无回归 |
| P2.6 | 备份/恢复真恢复 + 换机演练（install → restore → fetch-models） | 新目录恢复后 healthy、doctor 通过、设备在线 |
| P2.7 | 运维韧性：mihomo 节点失联提示/重选指引；3 天长稳 + NAS 断电自起演练 | 断电重启全链路自起；节点失效可诊断 |
| P2.8 | Buy Me a Coffee 真实链接 + README 收尾 | 链接可用，不再占位 |

### P3 · 功能线（0.2.0，冻结解除后）

| # | 任务 | 判据 |
|---|---|---|
| P3.1 | 第二台设备（P12/E1）：每设备音频配置 config v3、无播放能力降级 | 假 4 麦设备零改 `src/` 跑通 |
| P3.2 | 多类型音箱适配标准 → **RUI 插座 / 音箱插头**：纯声明 manifest；Dot2（B 类）首个真实插头；LX06（A 类）样板；控制台“从插头添加设备” | manifest 契约 + 一个第三方机型不改核心接入 |
| P3.3 | 设备管理后台参考 EchoMuse Device Management（注册/审批/每设备配置/状态） | 管理面可用 |
| P3.4 | Agent/OpenClaw/Hermes 接入、新渠道、完整 Doctor Fix | 单独窗口排期，不混进 0.2 |
| P3.5 | 安全：SSRF/端口探测 allowlist、容器非 root、依赖 CVE 扫描 | 三项各有报告/修复记录 |
| P3.6 | 0.2.0 发布：tag + Release + README 更新 | 发布清单同 0.1 |

**顺序原则**：P1 先合入 0.1.1；P2 与 P3.2/P3.3 可并行；每项完成必须带判据与实测记录。

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
