# HANDOFF — rui 稳定版阶段快照（2026-10-26 13:45）

> 单一当前快照。历史快照段（11:43 暂停点 / 12:33 P0 / 13:42 当前状态）已删除；仍未解决的项并入 §下一步 与 §风险与未决。
> 任务板：completed = task-1/2/4/5/7/8/9/10/11；pending = task-3（B1，BLOCKED）、task-6（阻塞发布）、task-12（[phase] 发布收口，blocked_by task-6）。

## 版本与契约

| 项 | 值 |
|---|---|
| rui 核心 | tag `v0.7.1` @ `44cff05`（`v0.7.0` = `e9db990`）；HEAD `c825091`；镜像 `rui:0.7.1-a1p` = `sha256:f446e348e871e11404cb7d081952b458750a6f4a6d6d45512f64551cfdd5285c` |
| rui-dot2-bridge | tag `v1.3.0` @ `c71deb6`；plug `dot2-echomuse 1.3.0`（`ladder_correct=true`） |
| rui-xiaoai-bridge | tag `v0.2.0` @ `fcff8eb`；plug `xiaoai-lx06 0.2.0` |
| 生产 NAS 168（`/vol1/rui`） | 组合镜像 `rui-dot2:1.3.0-r071` = `sha256:252f568ca6b1d88aa2990e79856b7d8b143a7810bac452d294e9e5fcfbe7ecba`；底座 `ghcr.io/yes999zc/rui:sha-615e682`；2026-10-26 13:36 部署、healthy |
| 生产配置 | `/health.console=legacy`（M10 未切）；override：`DOT2_PLAYBACK_GATE_MS=0`、`DOT2_OWW_ON_DEVICE=off`、`DOT2_BARGE_IN_ENABLED=0`、`DOT2_CONTROL_PORT=8872`；config：`post_playback_mute_ms=2500`、`ack_echo_ignore_ms=2500`；备份 `rui-dot2-override.yml.bak.20261026-*` |
| 设备固件 | Dot2 client `fw 20261024-1911-dev`；二进制 `echomuse-dot2-ref/artifacts/server` sha256 `65f93891…` |
| 契约 | C-001 `rui-device/v1`；C-002/1（含 A-1..A-6）；兼容窗口 `>=0.6.0, <0.8.0`（三仓 manifest 已同步） |

## 已完成

每条一行，细节与证据见括号内文档。

1. 0.6.0 发布 + 真机验收（LX06/Dot2 应答、告别只从说话房间出）→ `docs/release/v0.7.0.md`
2. 内核剥离小爱（`src/` 仅剩 `adapter_aliases.py`）+ 小爱独立仓 Phase 1 被 rui 识别 → `docs/release/v0.7.0.md`
3. 控制台 v2 M1–M10（`/v2` 15 页，旧 logo 已迁移）→ `docs/release/v0.7.0.md`
4. Dot2 音质修正：100–300Hz vs 1–3kHz `+24.65 → +2.11 dB`、限幅 `−12.85 → −2.19 dB`、0 削波 → `docs/release/v0.7.0.md`
5. 播放中打断（A1'）：`GATE_MS=0` + 设备 AEC + RUI 中文 KWS，真机 8/8 `stop_play sent` → `docs/release/v0.7.1.md`
6. barge 唤醒不播 TTS cue（task-5），消除 barge 自循环 → `docs/release/v0.7.1.md`
7. P0 `turn_open` 泄漏：dismiss/退下后设备变聋 → 显式释放 + 20s 看门狗（task-9/10）→ `docs/release/v0.7.1.md`
8. 普通唤醒 cue 回声不再开 turn（task-11，envelope 匹配丢 cue 尾巴）→ `docs/release/v0.7.1.md`
9. 每设备测试历史隔离（task-2/7 + C-002 A-5）→ `docs/contracts/C-002-console-v2-api.md`
10. `/v2/tools` 排除 `tools.mcp_servers` + C-002 登记 `GET /api/v1/mcp` 只读例外（task-1/4）→ `docs/contracts/C-002-console-v2-api.md`
11. `rui_compat <0.8.0` 三仓 + manifest 覆盖测试（task-8）→ `docs/release/v0.7.0.md`
12. 代码健康审计 Top 10，2 条阻塞已修 → `docs/audit/2026-10-code-health.md`
13. 4 台接入 runbook（添设备 / listen_port / 验收字段）→ `docs/release/acceptance-4devices.md`
14. task-6 采样工具 `scripts/fleet_matrix.py`（日志 + 人工标记 → 矩阵，每个数字带 `--log` 行号证据，证明不了标「证据不足」；reviewer PASS + verifier VERIFIED，commit `9d0083b`）

## 未提交

仅 `docs/HANDOFF.md`（本次整理 + task-13 收口记录，未提交）。2026-10-26 14:20 `git status --short` → `M docs/HANDOFF.md`；采样工具已单独提交为 `9d0083b`，三仓代码均已 commit + push。

## 下一步（按序）

1. **task-6（阻塞发布）**：P0 采样（6 句 × 5 轮 × 两台，音量 40）→ P1 归因（硬件/固件/adapter/核心 + file:line）→ 9 哥 选 ≤2 项 P2 → 改进过门禁 + 9 哥 A/B → 发布验收。
   - 采样工具已就绪：`docker logs --since 48h rui > /tmp/rui-fleet.log` 后在仓库根跑 `python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks /tmp/fleet-marks.csv`（marks 表头 `phrase_id,device_id,round,ts,heard_ok,notes`，phrase_id ∈ hello/time/play/next/stop/bye；`--marks` 可省）。逐句「日志侧」列恒为「证据不足」（日志无 phrase_id），句级数字来自人工 marks。
   - **P1 收口时必须做的事（易忘）**：task-6 写范围只有 `docs/analysis`，P2 的代码改动**不得**塞进 task-6；P1 归因一 PASS，就把选定的 P2 拆成 1–2 张独立 `[impl]` 卡（带各自代码 write_scopes + 门禁 + A/B），并把 `task-12.blocked_by` 更新为 `task-6 + 全部 P2 卡`。
2. **4 台联合验收**：两台新 Dot2 到位 → EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT=8872` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除。步骤见 `docs/release/acceptance-4devices.md`。
3. **M10 默认切换**：等 9 哥 点头；验收 `/health.console=v2`、`/` 302 `/v2/`、`/legacy` 200，回滚一行。
4. **24h 稳定性复查**：自 2026-10-26 13:36 起计；提醒已设 2026-10-27 13:10（可改 13:40）；结果补进 `docs/release/v0.7.1.md`。
5. **task-12 发布收口**：task-6 完成 + 发布号经 9 哥 确认后，更新本文件 → `phase_close` → complete。

## 风险与未决

- **B1 中文唤醒（task-3，BLOCKED）**：V0 英文 stock 模型对产品无效（中文「你好小瑞」只走 RUI KWS，英文零事件、`wake_candidates=0`）。真前置不是固件：① 中文 OWW 模型需 `oww_forge` 自训（25GB/数小时，效果待测）；② bridge 无 `/shell` 通道（`adapter.py:81`），最小验证需一次性 adb 推 ~1MB 分类器。待 9 哥 决定：是否再跑 V0 / 是否接受自训成本 / 生产机是否允许一次性 adb / barge 门槛是否 0.25。
- **EQ 耳测未做**：测量指标改善已证，但 9 哥 尚未 A/B；`trim_db=-2.0` 未重调（A 计权响 4.1 dB）。
- **cue 回声残留**：envelope 匹配对设备延迟 >~950 ms 或与用户语音完全重叠时回退旧行为，可能切掉 ~300 ms。
- **多设备尚未实测**：`multi_device.enabled=true`、`max_planes=4`，但只有 1 台 Dot2 在线；唤醒词不按房间隔离（`lost_same_word`）；`DOT2_CONTROL_PORT` 是全局 env，加第 2/3 台前必须清掉；多设备默认共享 secrets。
- **24h 稳定性未复查**：从 13:36 起计，尚未满窗。
- **凭证**：NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理会话输出中回显（未落仓库）→ 建议轮换 NAS 密码；本会话临时口令文件（本地 + 远端 `/tmp`）已删除。
- **源码全量镜像构建仍阻塞**：NAS 上 `deb.debian.org` 超时、换清华源后仍 `Unable to locate package nasm/libopus-dev`，故当前走薄层镜像（底座 + overlay），可复现性只到「tag → 薄层 digest」。
- **backlog**：`uv.lock` 停在 0.2.0；下游 docs 兼容窗口文案陈旧；`coordinator.turn_device`/`drop_device` 生产路径未用；`turn_tasks` 仅在 `start_audio` 时初始化；覆盖缺口（`_wake_in_flight`、output_owner/gate_speaking）；whitespace-id 归一化无用例。

## 关键文件

- 契约：`docs/contracts/C-002-console-v2-api.md:1`、`docs/socket-plug-v1.md`
- 计划/分析：`docs/plan/speaker-tuning.md:23`（诊断）、`:343`（B1 评估）、`docs/analysis/`（task-6 产出）
- v2 后端：`src/rui/web/api/v2_api.py`、`src/rui/web/api/app.py`（`TURN_STALE_S`、`_PlaneLink.release_turn`）
- 音频：`src/rui/audio/process.py`、`src/rui/audio/vad.py`（`set_echo_reference`）、`src/rui/audio/coordinator.py`
- 控制台：`web/src/v2/App.vue`、`web/src/v2/router.ts`、`web/src/v2/tools.ts:42`
- 下游：`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:81`（无 `/shell`）、`dsp.py:196`
- 发布：`docs/release/v0.7.0.md`、`docs/release/v0.7.1.md`、`docs/release/acceptance-4devices.md`

## 约束与禁区

**协作规则**
1. 需要 9 哥 拍板时用 `ask_user_question` 提问并等待，不要在总结里只写「等你决定」。
2. 要真正停手交接或等真机时明确说「请 pause goal」，等 pause 后再继续。
3. 9 哥 发消息不取消 goal；范围变更先写进任务卡或本文件，再动手。
4. **对外发布用哪个版本号必须先问 9 哥 确认**（「按需升版」保留）。

**禁区**
5. 任何改动先过 fresh reviewer + verifier 再谈 commit；不碰 secrets、不自行宣传。
6. 不要相信「`/v2/tools` 未过门禁」的旧说法：已过门禁并提交 `ef7ff18`，且已进 0.7.1 镜像。
7. 不要相信「打断已修复」的旧说法：A1' 真机 8/8 可用，但 P2 类体验差距待 task-6 量化。
8. 不要相信「Dot2 音质已变好」：只证测量指标改善，未经 9 哥 耳测。
9. 不要相信「设备侧中文唤醒已可用」：中文 OWW 模型不存在，V0 英文路径对产品无效。

## 核验命令（接手先跑）

```
cd 01_Code/projects/house-ear && .venv/bin/python -m pytest -o addopts='' -q   # 期望 1523 passed / 17 skipped
.venv/bin/python -m ruff check src tests                                       # All checks passed
cd web && npm run typecheck && npm run build                                   # exit 0，dist/index.html + dist/v2.html
cd ../rui-dot2-bridge && PYTHONPATH=src ../house-ear/.venv/bin/python -m pytest -q   # 期望 143 passed
cd ../rui-xiaoai-bridge && PYTHONPATH=src ../house-ear/.venv/bin/python -m pytest -q # 期望 9 passed
gh release view v0.7.1 --json tagName,url                                      # tag 与 release 存在
```

## 没做的事

- **没做**：B1 中文唤醒模型与 adb 验证（task-3 BLOCKED）、task-6 矩阵采样、4 台联合验收、M10 默认切换、9 哥 耳测、24h 稳定性复查、源码全量镜像构建。
- **没做**：宣传/公告/营销文案（明确不做）。
- 任务板会随会话消失；跨会话只信本文件 + `docs/release/*`。

> 非 [phase] 收口任务，故不写 phase-close 标记；task-12 收口时才由 Lead 追加 `phase-close: task-12`。
