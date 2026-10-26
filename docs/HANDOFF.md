# HANDOFF — rui 阶段快照（2026-10-26 10:50）

## 版本与契约

| 项 | 值 |
|---|---|
| rui main | `ef7ff18`（2026-10-26 门禁后提交：/v2/tools 8 文件 + C-002 只读例外 + 契约测试；**未 push**）。工作区另有 2 个未提交路径：`docs/plan/speaker-tuning.md`、`docs/HANDOFF.md` |
| rui 发布版 | tag `v0.6.0` @ `adc4c2b`；官方镜像 `ghcr.io/yes999zc/rui@sha256:abef1f276c3c21e9155d97c98913ee6c2e98fdfe842e43b90f64a5db6c12b241` |
| 生产 NAS 168 | **`rui-dot2:1.3.0-r071`**（2026-10-26 13:36 部署；`rui` 0.7.1 + `dot2` 1.3.0；digest 见 `docs/release/v0.7.1.md`）@ `/vol1/rui`；`/health.console=legacy`（**默认未切**）。override：`DOT2_PLAYBACK_GATE_MS=0`、`DOT2_OWW_ON_DEVICE=off`、`DOT2_BARGE_IN_ENABLED=0`；config：dot2 `post_playback_mute_ms=2500`/`ack_echo_ignore_ms=2500`；备份 `rui-dot2-override.yml.bak.20261026-*` |
| 下游 rui-dot2-bridge | `1971fc5`（EQ/限幅修正）；plug `dot2-echomuse 1.2.0`；`plug.json ladder_correct=true` |
| 下游 rui-xiaoai-bridge | `6f5f4dd` + tag `v0.1.0`（plugin `xiaoai-lx06`，`rui_compat >=0.6.0 <0.7.0`） |
| 设备固件 | Dot2 client `fw 20261024-1911-dev`；二进制 `echomuse-dot2-ref/artifacts/server` sha256 `65f93891…` |
| 契约 | C-002/1（含 A-1..A-6）；C-001 `rui-device/v1` |

## 已完成（证据 = 命令 → 结果）

1. **0.6.0 发布 + 真机验收**：`gh release view v0.6.0` → 存在，notes 含真机验收段；9 哥 10-26 09:05 确认 LX06/Dot2 均应答、告别只从说话房间出、「在呢」正常。
2. **内核剥离小爱**：`git grep -in -e xiaoai -e lx06 -e 小爱 -- src/` → 仅 `src/rui/adapter_aliases.py`；`pytest tests/test_kernel_decoupling.py` → passed。
3. **小爱独立仓库 Phase 1**：`rui-xiaoai-bridge` 被 rui 识别 → `GET /api/v1/plugs` 显示 `source=user, adapter=lx06`；`rui plug-test` → 16 PASS。
4. **控制台 v2 M1–M10**：`/v2` 15 页；旧 logo 已迁移；阶段已 phase_close（当时 marker task-24）。
5. **Dot2 音质修正**：`tools/eq_report.py` → 100–300Hz vs 1–3kHz `+24.65 → +2.11 dB`（六档单调）、限幅 `−12.85 → −2.19 dB`、0 削波、20Hz `−15.9 dB`；下游 `pytest` → 141 passed；生产 `/loudness` → `corrected=true / lowcut 0 / highshelf 0`。
6. **打断根因诊断**：`docs/plan/speaker-tuning.md` 诊断 1 —— 播放期上行被整段静音（`adapter.py:622-640`）+ `device_wake` 回落 RUI KWS ⇒ 播放中物理不可打断（190s 播放期 0 次唤醒）。
7. **B1 可行性评估（只评估未实施）**：结论 **(a) 纯配置/文件下发，不需要刷固件** —— 设备二进制已含 `owwModel/owwOnDevice` 与 `[shadow]` ONNX 打分器，模型放 `/data/local/share/echomuse/oww`，可经 `/shell`（shell_open+base64+md5）或 adb 写入，config 即时重建 scorer、无需重启。证据与前置条件见 `docs/plan/speaker-tuning.md:343-461`。
8. **诚实性修复（v1/v2 一致）**：设备测试步不再被「链路通」掩盖（`setup_api.py:69/568`、`v2_api.py:862`）；`pytest -o addopts='' -q` → 1488 passed / 17 skipped；`ruff` → All checks passed。

## 未提交文件

**无。工作区干净**（2026-10-26 13:42，`git status --short` 为空）。三仓均已 commit + push：
house-ear `main` → `2b42f46`（含 0.7.1 addendum）；dot2 `c71deb6` + tag `v1.3.0`；xiaoai `fcff8eb` + tag `v0.2.0`。

## 下一步（按序）

1. **两台新 Dot2 到位**：先做 EQ 耳测（保留 `/loudness`）→ 再清 override 里的 `DOT2_CONTROL_PORT=8872` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 4 台联合验收。命令/证据字段见 `docs/release/acceptance-4devices.md`。
2. **M10 默认切换**：等 9 哥 点头；验收 `/health.console=v2`、`/` 302 `/v2/`、`/legacy` 200，回滚一行。
3. **最终真机语音验收**（9 哥 一次做）：退下/空闲告别后连说 3 次「你好小瑞」、播放中打断（音量 40/100）、普通唤醒不再自问自答；同时按 task-6 的 6 句 × 5 轮采 Dot2 vs 小爱矩阵。
4. **24h 稳定性**：从 2026-10-26 13:36 起计；已设 2026-10-27 13:10 提醒（可改为 13:40）复查 5xx/掉线/重启恢复，结果补进 `docs/release/v0.7.1.md`。

## 风险与未决

- **B1 真前置不是固件**：① 中文唤醒词模型无现成，需 `oww_forge` 自训（英文 embedding，效果待测，25GB/数小时）；② bridge 现无 `/shell` 通道（`adapter.py:81-87`），最小验证需一次性 adb 推 ~1MB 分类器。4 个待 9 哥 决定：是否先跑 V0 / 是否接受中文自训成本 / 生产机是否允许一次性 adb / barge 门槛是否 0.25。
- **打断**：当前构建物理不可打断（诊断已写）。
- **EQ**：A 计权响 4.1 dB、中高频节目略轻，`trim_db=-2.0` 未重调；9 哥 尚未 A/B。
- **凭证**：NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理会话输出中回显（未落仓库）→ 建议轮换。
- **setup store 单槽**：设备详情「上次测试」可能为空（不串台）。
- **`data/plugs/` 为空**：dot2 清单报 `source=builtin`，下游 README 说法过时。

## 关键文件

- 契约：`docs/contracts/C-002-console-v2-api.md:1`、`docs/socket-plug-v1.md`
- 计划：`docs/plan/speaker-tuning.md:23`（诊断 1）、`:343`（B1 评估）、`docs/plan/xiaoai-pro-bridge.md`
- v2 后端：`src/rui/web/api/v2_api.py:862`、`:2270`；`src/rui/web/api/device_write.py`
- v1 兜底：`src/rui/web/api/setup_api.py:568`、`src/rui/web/api/app.py:6626`
- 控制台：`web/src/v2/App.vue:48`、`web/src/v2/router.ts:66`、`web/src/v2/views/ToolsView.vue:1`
- 下游：`rui-dot2-bridge/src/rui_dot2_bridge/dsp.py:196`、`adapter.py:81`（无 /shell 通道）

## 约束与禁区（长期有效，压缩/新会话后仍须遵守）

**协作规则**
1. **需要 9 哥 拍板时用 `ask_user_question` 提问并等待**，不要在总结里只写「等你决定」——否则 goal 会自动开下一轮，等不到人。
2. **要真正停手做交接、或等 9 哥 操作真机时，明确说「请 pause goal」**，等 pause 后再继续；不要一边总结一边开新活。
3. **9 哥 发消息不会取消 goal**；处理完按任务板继续。若消息改变范围，先把变更写进任务卡或本文件，再动手。

**禁区**
4. 未提交文件原样保留；任何改动先过 fresh reviewer + verifier 再谈 commit；不 push、不碰生产、不动 secrets。
5. 不要相信「`/v2/tools` 已可用」的旧说法：它已过独立门禁（2026-10-26，PASS+VERIFIED），但**未 commit、未进生产镜像**（pre8-eq 镜像不含 tools 页）。
6. 不要相信「打断已修复」「Dot2 音质已变好」：B1 只有评估、打断只有诊断，均零实施。

## 核验命令（接手先跑）

```
cd 01_Code/projects/house-ear && .venv/bin/python -m pytest -o addopts='' -q   # 期望 1492 passed / 17 skipped
.venv/bin/python -m ruff check src tests                                       # All checks passed
cd web && npm run typecheck && npm run build                                   # exit 0，dist/index.html + dist/v2.html
gh release view v0.6.0 --json tagName,url                                      # tag 与 release 存在
```

## 没做的事 / 不要相信的地方

- **没做**：B1 中文模型、A2 实施、M10 默认切换、0.7.0 发布；task-2 已实现但未过门禁。
- **不要相信**「`/v2/tools` 未过门禁」的旧说法：已过独立门禁并提交 `ef7ff18`（未 push），但**未进生产镜像**（pre8-eq 不含 tools 页）。
- **不要相信**「打断已修复」的旧说法：A1' 在真机 5/5 播放中打断可用（`GATE_MS=0`+设备 AEC+中文 KWS），但**自听循环未修**（task-5 待实现并部署），生产现在仍会自问自答。
- **不要相信** Dot2 音质「已变好」：只证明测量指标改善，未经 9 哥 耳测。
- **不要相信** 设备侧中文唤醒已可用：中文 OWW 模型不存在，V0 英文路径对产品无效。

> 非 [phase] 收口任务，故不写 phase-close 标记、不调用 phase_close。

## 当前状态（2026-10-26 13:42）

- **版本**：rui **0.7.1**（commit `44cff05` + tag `v0.7.1`；0.7.0 = `e9db990` + tag `v0.7.0`）；dot2 **1.3.0**（`c71deb6` + `v1.3.0`）；xiaoai **0.2.0**（`fcff8eb` + `v0.2.0`）。三仓已 push。
- **生产**（NAS 168，13:36 部署，healthy）：`rui-dot2:1.3.0-r071` = `sha256:252f568ca6b1d88aa2990e79856b7d8b143a7810bac452d294e9e5fcfbe7ecba`；核心 `rui:0.7.1-a1p` = `sha256:f446e348e871e11404cb7d081952b458750a6f4a6d6d45512f64551cfdd5285c`；底座 `ghcr.io/yes999zc/rui:sha-615e682`。容器内 `rui.__version__=0.7.1`、`vad.py` 含 `set_echo_reference`、`app.py` 含 `TURN_STALE_S`；设备 `dot2` 已重连。
- **任务板**：completed = task-1/2/4/5/7/8/9/10/11；pending = task-3（B1/V0，BLOCKED，等中文模型/adb 决策）、task-6（Dot2 vs 小爱矩阵，并入最终验收）。
- **门禁**：全部交付均 reviewer PASS + verifier VERIFIED；最终全量 house-ear **1523 passed / 17 skipped**、ruff 绿；dot2 **143 passed**；xiaoai **9 passed**。
- **发布文档**：`docs/release/v0.7.0.md`（门禁矩阵 + DoD）、`docs/release/v0.7.1.md`（0.7.1 两个修复 + digest + 限制）、`docs/release/acceptance-4devices.md`（4 台接入 runbook）、`docs/audit/2026-10-code-health.md`（Top 10，2 条阻塞已修）。
- **9 哥 授权**：可随时用 NAS 部署、可 push GitHub；最终统一做真机验收，不再逐项找 9 哥 测试。
- **待确认的体验点**：空闲告别（“我先下啦”）后仍可被唤醒（预期行为，待最终验收确认）；显式「退下吧」后 3 连唤醒（task-9 修复，待真机确认）。

## P0 / 阻塞（2026-10-26 12:33，真机）

- **P0：dismiss/「退下吧」后 turn_open 永不复位，设备变聋直到重启**（task-9，impl 修复+扩展中）。**确定性复现**（pre9-a1p）：`04:38:48 wake admitted → 04:38:52 stop intent: '退下吧。' -> session closed → 04:39:05 wake rejected: turn_open`；重复 12:26:15 那次（之后 11 连拒）。设备 connected/ready、按键正常、容器 healthy；只有重启容器恢复。根因：dismiss 路径故意跳过 `reply_finished`，而 `turn_open` 唯一常规释放点在该函数的 finally；修复=新增 `_PlaneLink.release_turn()` + dismiss/stop_elsewhere 后显式释放，并扩展到 `_context_command`、clarify 裸设备名分支。生产已于 12:43 重启恢复。
- **A1' 真机（pre9-a1p）已通过的部分**：8 次播放中打断全部 `stop_play sent`（服务端 2–7ms + ALSA ~300ms）；打断后「几点了/播放周杰伦/刘德华/日文歌」均一次识别正确；`在呢` cue 不再出现、无自循环。**task-5 待 30 分钟 soak + P0 修复后一起收口**。
- **发布阻塞（审计 Top 1/2，task-8）**：三仓 `rui_compat` 上限 `<0.7.0`，0.7.0 会拒载全部插头；生产 pre9-a1p 含未提交补丁、不可从 commit 重建。
- 审计报告：[docs/audit/2026-10-code-health.md](01_Code/projects/house-ear/docs/audit/2026-10-code-health.md)（Top 10，2 条阻塞，其余进 backlog）。

## 暂停点（2026-10-26 11:43，9 哥 已 pause goal）

- **A1'（中文唤醒 + 播放中打断）真机结论（12:07）**：播放期听不到的主因是 **bridge 软件静音**（`rui-dot2-bridge/.../adapter.py:622-640` `_gate_mic()`，ref_channels=0 时清零 mic），不是固件半双工；`DOT2_PLAYBACK_GATE_MS=0` + 设备 AEC + RUI 中文 KWS 后，**打断 5/5 成功**（`barge: N wake during playback`、`stop_play sent`、服务端停播 2–7ms，设备 ALSA ~300ms）。小爱 LX06 走的是同构路线（全双工 6mic+2ref + 服务端 KWS），其唤醒引擎不可移植但事件/停播链路 Dot2 已具备。
- **新问题（task-5）**：barge 后播 TTS cue「在呢」及其回声被 ASR 转成新回合并再次唤醒（`12:07:03 barge → 12:07:05 "在呢。" → 12:07:07 回复 → 12:07:12 再次 barge`）。`post_playback_mute` 调低会放开回声、调高会切断用户第一句，零和。修法：**barge 唤醒不播 cue**（改 `src/rui/audio/process.py`），已建 task-5 并由 impl-barge-ack 实现中；通过门禁后需**构建新镜像**（核心 rui 镜像 → 下游 Dockerfile FROM 它）再 recreate，版本号待定。
- **V0（英文 stock OWW）BLOCKED**：产品只用中文，英文 V0 不作验收；设备侧无打分事件，模型资产未验证（需 adb）。当前生产 OWW 已 off。

- **task-2 已完成**（v2 侧，reviewer PASS + verifier VERIFIED）：`setup_state.py` 新增 `device_test_record`/`record_device_test`（by_id 优先、legacy 回退需 device_id 匹配、非 dict 层按无记录、单次锁内双写）；`v2_api.py` 读/写委托；`tests/test_device_test_history.py` 5 用例；全量 1501/17。收口条件转 **task-7**（v1 wizard 写入统一 + C-002 A-5）：代码已实现，reviewer 判 **CHANGES**（仅 A-5 一处回退规则表述与实现相反，修复中）。
- **待办（按序）**：① task-9（P0 turn_open）→ 门禁 → 构建新镜像（含 task-5+task-9）→ 真机复测；② task-7（v1 wizard 写入 + C-002 A-5）→ 门禁；③ task-8（三仓 rui_compat `<0.8.0` + manifest 覆盖测试）→ 提交/tag/镜像可重建；④ task-5 soak 30 分钟 + 收口；⑤ 单元②（`speaker-tuning.md` + 本文件 + 审计）送审后提交；⑥ task-6（Dot2 vs 小爱 P0/P1，不改代码）低优先级；⑦ 两台新 Dot2 到位后加设备并做 4 台联合验收；⑧ M10 默认切换等 9 哥 点头。
- **生产访问**：`ssh yes999zc@192.168.5.168`（key 可用）；`sudo` 需口令，**不要写入仓库**（本机 `01_Code/device-inventory.md` 可查）。本会话曾因重定向错误回显一次口令 → 建议轮换 NAS 密码。
- **工作区**：HEAD `ef7ff18`（未 push）；未提交约 10 路径：task-5（process/protocol/test_barge）、task-7（setup_state/setup_api/C-002/test_setup_api/test_device_test_history）、task-2（v2_api）、单元②（HANDOFF/speaker-tuning）、审计（docs/audit/）。
