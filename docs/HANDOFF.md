# HANDOFF — rui 稳定版阶段快照（2026-10-26 13:45）

> 单一当前快照。历史快照段（11:43 暂停点 / 12:33 P0 / 13:42 当前状态）已删除；仍未解决的项并入 §下一步 与 §风险与未决。
> 任务板：completed = task-1/2/4/5/7/8/9/10/11；pending = task-3（B1，BLOCKED）、task-6（阻塞发布）、task-12（[phase] 发布收口，blocked_by task-6）。

## 版本与契约

| 项 | 值 |
|---|---|
| rui 核心 | tag `v0.7.1` @ `44cff05`（`v0.7.0` = `e9db990`）；HEAD `c825091`；镜像 `rui:0.7.1-a1p` = `sha256:f446e348e871e11404cb7d081952b458750a6f4a6d6d45512f64551cfdd5285c` |
| rui-dot2-bridge | tag `v1.3.0` @ `c71deb6`；plug `dot2-echomuse 1.3.0`（`ladder_correct=true`） |
| rui-xiaoai-bridge | tag `v0.2.0` @ `fcff8eb`；plug `xiaoai-lx06 0.2.0` |
| 生产 NAS 168（`/vol1/rui`） | **当前 `rui-dot2:1.3.0-p22`**（= r071 + P2-1 vad.py + task-18 按房间隔离，未打 tag；部署时序：r071 13:36、p21 16:08、p22 17:15，均 healthy）；上一个稳定组合 `rui-dot2:1.3.0-r071` = `sha256:252f568ca6b1d88aa2990e79856b7d8b143a7810bac452d294e9e5fcfbe7ecba`；底座 `ghcr.io/yes999zc/rui:sha-615e682` |
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
15. task-6 P0 采样自动化 + 矩阵：`scripts/fleet_say.py`（唤醒词 + 1.5 s 后说指令、自动写 marks）+ `fleet_matrix --log-tz/--window/--wake-only-phrase`（commit `3c249f9`）
16. task-6 P1 归因：`docs/analysis/dot2-vs-xiaoai.md`（Dot2 30 句：唤醒 30/30 admitted、指令 26/30；两条掉分线索落到日志行；commit `850d19e`）
17. P2-1（task-15）：ack cue 回声不再进 ASR；真机 A/B **0/10 泄漏**（修复前 13/26）；commit `87bb704`，部署测试镜像 `rui-dot2:1.3.0-p21`（未打 tag）
20. task-16 分析完成（reviewer PASS）：`docs/analysis/wake-vs-command-range.md`，结论「唤醒链两台同量级、差距在 admitted→ASR；H2（播放后静音窗）被代码否证；H1/H3/H4 未做」；提交 `a301dce` + `2c88b0e`
21. task-19（P2-2 音乐中短指令）已开卡并把归因数据写进卡：音乐期 `[seg]` rms 中位 0.0232（比无音乐 0.0118 **更高**）、peak 到 1.000 → 失败模式是「音乐串音/削波」而非 mic 被静音
19. task-18（P1）播放态按房间隔离：一屋放歌不再饿死另一屋唤醒；提交 `3bbf93f`，reviewer PASS + verifier VERIFIED（1617/17），生产 `rui-dot2:1.3.0-p22`
18. 采样编排打通：Dot2 用 Mac Studio 播、LX06（`device_id=living`）用荣耀服务器 192.168.5.34 播，同一份 48k WAV、各自写 UTC 毫秒 marks，`fleet_matrix --log-tz UTC` 出两设备矩阵；设备接入索引见 `docs/device-access.md`（不含口令）

## 未提交

`docs/device-access.md`（新增，设备接入索引，无口令）

仅 `docs/HANDOFF.md`（本次整理 + task-13 收口记录，未提交）。2026-10-26 14:20 `git status --short` → `M docs/HANDOFF.md`；采样工具已单独提交为 `9d0083b`，三仓代码均已 commit + push。

**task-15（P2-1 ack cue 回声，第四轮返工完成待复评，文件已冻结未提交）**：窄匹配增加 `_quiet_after`（`vad.py:367`，cue 结束后 100 ms 必须安静，±20 ms 容差）：把「教过 EMA 后 mute 场景回复被误丢」挡住（回复比 cue 长、不会安静）；窄命中不再 `_learn_lag`（EMA 只由首轮高质量命中更新，`vad.py:381`）；日志 `kept` 区间改为 `min(skip+drop, hold)` 并新增 `match_offset`（`vad.py:493`）；`CUE_POST_SILENCE_MS=100`（:69）。新增测试：`tests/test_cue_echo_excision.py:450` 教 EMA(760/820) 后跑 mute 几何（cue 700/hold 1690/skip 950）→ captured **1.31 s**、`dropped=[0,950]ms`、fallback=false；`:482` 窄命中后 EMA 不变。无削波 850/950 仍 corr_full 0.030 / tail 0.353 / retained 0.91/1.00。全量 **1613 passed / 17 skipped**，ruff 绿；冻结 md5：vad.py `cf8e558c2d11b7be3041ebb16e0cd7ae`，test `412ecfb749d8488d9951fe0320b31f14`；process.py 未改（`176e8508…`）。

**task-18（P1 播放态按房间隔离，实现完成待门禁，未提交）**：arbiter 的 `turn_open` 增加 `turn_device`（`arbiter.py:187/343`），拒绝改为 `_in_open_turn`（:533，仅同房/同设备）；`_playing_reason`（:550）只在候选属于正在播放的房间时回 `lost_playing`/`cross_echo`，其它房间放行；pending 批量按候选逐条过滤（:480 保留 rejected 列表）。coordinator `set_turn_open` 同步房间、新增 `set_playing`（`coordinator.py:87/101`）；`_reconcile_owner` 把**所有**出声房间写入 playing（`app.py:1582`，无播放时清空 :1562）。旧全局单槽行为在测试中已更新（arbiter/app_fleet 各 1 处），新增 6 个隔离用例（`test_wake_coordinator.py:253/268/283/297/310`、`test_arbiter.py:233`）。反向：旧代码上 A 放歌→B wake `WakeReject`（应 admit）、A turn_open→B `turn_open`。全量 **1617 passed / 17 skipped**，ruff 绿。真机 A/B 由 Lead 执行。

**task-19（P2-2 音乐中短指令，实现完成待门禁，未提交）**：归因结论 H-A 不成立（音乐期高 rms/peak=1.0 的段都紧邻 `cue:`，见 fleet3:414/952；无 cue 的音乐段 rms 0.02–0.06、peak 0.2–0.23）、H-B **成立**（`wake_preroll_ms=500 > wake_skip_ms=300`，barge 时回放环里 200 ms 停播前音频进段；fleet3:596–601 barge→stop_play→seg，07:12:30–35 3.84 s 段串进告别语「没事的话，我先下啦」）、H-C 仅个别成立（07:04:01 rms .006、07:12:13 rms .0022 近似静音，非全局）。最小改动：`process.py:1895-1902` barge 唤醒不再回放预唤醒 ring（仍清空 ring），live 语音不受影响；非 barge 路径不变。新测试 `tests/test_music_barge_head.py`（barge 不回放停播音乐、非 barge 仍回放、stop_play ≤7 ms）。反向：旧 process.py 上 `assert MUSIC_PCM not in gate.fed` 失败。三仓：house-ear **1620 passed/17 skipped**、dot2 **143**（其 .venv）、xiaoai **9**；ruff 绿。真机 A/B 由 Lead。

**task-19（P2-2，第五轮返工待门禁，未提交）**：把 disarm 出口统一收口到 `process.py::_disarm_gate(reason)`（约 :2663）——`gate.armed` 时 disarm 并清 `_stale_speech_blocked`；`_disarm_gate_for_our_audio` 改走它（消除第四条漏清：reminder/console 语音 disarm 不经 `_feed_gate`）；`_feed_gate` 的 `armed -> disarmed` 转换也清位（约 :1959，覆盖 task-15 的 `_cue_excised` 静默超时，无 `idle_events`）；`idle_events` 变化清位保留。三处同一布尔赋值、均在事件循环单写者上，幂等无竞态。新测试 `tests/test_stale_speech_after_wake.py:170`（真实 vad 的 `_cue_excised` 超时 disarm → 后续 PLAY_PCM 入队）、`:214`（唤醒在听时我方 reminder disarm → 音频正常入队）；反向变异（去掉 helper 清位/armed 转换清位）→ 两用例分别 `AssertionError`。三仓：house-ear **1626 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。

**task-19（P2-2 音乐中短指令，第四轮返工待门禁，未提交）**：清位时机补齐——`_stale_speech_blocked` 现在在 `process.py:1962-1967`（`_feed_gate` 里 `idle_events` 变化，即 `wake without speech`/`WakeIdle`/超时 disarm）清除，也在本 turn utterance 落地时清除（:1988）；`_begin_wake_turn:1823` 在无 gate 时不置位。这样安静唤醒后 reminder/console debug say 的 PLAY_PCM 能正常播出。新用例 `tests/test_stale_speech_after_wake.py:117`（silent wake → 后续 PLAY_PCM 必须入队）；反向变异（注释掉 idle 清位）→ `AssertionError` at `assert plane._stale_speech_blocked is False`。已修好项保持：goodbye 已入队 chunk 仍被 flush/drop、farewell `say(keep_abort)` 早退、cue 正常。三仓：house-ear **1624 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。单会话限制已记：`runner.abort()` 全局 flag，A/B 两房共用 runner，跨房隔离仅 arbiter 层（task-18），不在本卡改。

**task-19（P2-2 音乐中短指令，第三轮返工待门禁，未提交）**：阻塞 1——plain wake 只 abort flag，已入队 goodbye 音频仍播放（07:12 30.65–30.97 已有 1.4–2.0 s 入 plane）。修法：`process.py` 新增 `_stale_speech_blocked`（唤醒置位、本 turn utterance 落地时清除，`_accept_play_pcm` 丢弃此间 PLAY_PCM，SpeakStart/SpeakFinished 同步忽略），`_begin_wake_turn` 开头 `_flush_speech()` 清空已入队；不用 StopAudio 因为会连 wake cue「在呢」一起停。阻塞 2——`turn.say()` 无条件 `_abort.clear()`：新增 `keep_abort=True`，farewell 用它并在 abort 已置位时直接不播；`farewell` 仍先 `idle.stop()`。app 侧保留「任何 wake 都 `runner.abort()`」。新测试 `tests/test_stale_speech_after_wake.py`（预置/迟到 chunk 均不进 gate，utterance 后恢复）、`tests/test_turn.py:780`（abort 后 farewell 零播放）。反向变异：丢弃 guard 后 `assert 0.10015625 == 0.0`；turn 移除 abort 早退后 `the aborted goodbye still played`。三仓：house-ear **1623 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。真机 A/B 归 Lead。

**task-19（P2-2 音乐中短指令，返工后待门禁，未提交）**：归因更正——07:12 headline 不是 barge：`:1110` 30.080 farewell 先触发、`:1111` 30.489 wake（TTS 未入队/barge=false）、`:1125` 30.969 起告别语 live 播放、`:1128` 3.84 s 段把告别语+「下一首」送进 ASR；`:596-601` 只是 H-C 近静音个例，H-B 不成立。修法：`app.py:2841-2848` 任何唤醒都 `runner.abort()`（不只 barge）中止上一轮/告别 TTS；`turn.py` farewell 改为先 `idle.stop()` 再 say，避免被中止的告别语事后清掉新唤醒的 idle 会话。撤销上一版 barge ring 裁剪（`process.py` 已还原，ring 仍回放，skip 之后用户首字保留）。新测试 `test_app_fleet.py:718` 普通 wake 必须 abort（旧代码 FAILED）、`tests/test_music_barge_head.py:104` barge 仍回放 ring（防回归）。三仓：house-ear **1621 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。真机 A/B 归 Lead。

## 已批 / 待批（2026-10-26 16:58 9 哥 全部按建议批准）

**已批（可继续，不用再问）**
1. **对外版本号**：暂不打 tag；等 task-16/17/18/19 收口后一次性定，建议 rui `0.7.2` + dot2 `1.3.1`（P2-1 是行为修复），**打 tag 前仍要 9 哥 一句确认**。
2. **M10 默认切 v2**：**先不切**，等 4 台联合验收通过、9 哥 点头。
3. **P2-2（音乐中短指令拾音）已批准** → task-19。P2 上限 2 项到此为止（P2-1 已完成，P2-2 在做）。
4. **`lost_playing` 跨房间饿死：发布前必须修** → task-18（P1）。

**仍待批（不阻塞当前工作）**
- 4 台新设备到位后的添加/验收（需要 9 哥 手动加设备）。
- 24h 稳定性结果出来后是否接受。

## 下一步（按序）

1. **task-6（阻塞发布）**：P0 矩阵 + P1 归因**已交付**（`docs/analysis/dot2-vs-xiaoai.md`，commit `850d19e`；Dot2 唤醒 30/30、指令 26/30）。**当前卡在 9 哥 选 P2（≤2 项）**，选定后各开独立 `[impl]` 卡，过门禁 + 9 哥 A/B，再回来 1 条收口。
   - P2 候选两条（详见分析文档 §6）：**P2-1** ack cue 回声抑制加强（`vad.py`+`process.py`，消除「唤醒后立刻说指令被『在呢』抢答」，13/26 次污染）；**P2-2** 播放中短指令拾音（dot2 bridge AEC + `post_playback_mute_ms`，音乐里「下一首」2/5）。
   - 采样工具已就绪：`python scripts/fleet_say.py --rounds 5 --rate 190 --gap 12`（默认已带唤醒词 + 1.5 s 停顿），矩阵：`python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks <marks.csv> --log-tz UTC --window 12`（日志时区是 UTC，必须带）。
   - **LX06 那一路仍未采**（不同屋、Mac 音源覆盖不到）：需用同一份 `say -o` 音频在它房间放一遍，或 9 哥 人声复测。
   - **P1 收口时必须做的事（易忘）**：task-6 写范围只有 `docs/analysis`，P2 的代码改动**不得**塞进 task-6；P1 归因一 PASS，就把选定的 P2 拆成 1–2 张独立 `[impl]` 卡（带各自代码 write_scopes + 门禁 + A/B），并把 `task-12.blocked_by` 更新为 `task-6 + 全部 P2 卡`。
2. **4 台联合验收**：两台新 Dot2 到位 → EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT=8872` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除。步骤见 `docs/release/acceptance-4devices.md`。
3. **M10 默认切换**：等 9 哥 点头；验收 `/health.console=v2`、`/` 302 `/v2/`、`/legacy` 200，回滚一行。
4. **24h 稳定性复查**：自 **2026-10-26 17:15（p22 部署）** 起计；提醒已设 2026-10-27 13:10（将改为 17:40）；结果补进 `docs/release/v0.7.1.md`。注：13:36 那一轮因中途多次 recreate 已作废。
5. **task-12 发布收口**：task-6 完成 + 发布号经 9 哥 确认后，更新本文件 → `phase_close` → complete。

## 采样进度（2026-10-26 16:40）

- Dot2 腿：Mac Studio `afplay /tmp/fleet6/*.wav`（同源 48kHz WAV），5 轮 × 6 句，marks `/tmp/fleet6/marks-dot2b.csv`。
- LX06 腿：荣耀服务器 `aplay -plughw:2,0`，同一份 WAV，marks `/tmp/fleet6/marks-lx06.csv`（`device_id=living`）。
- 日志：`/tmp/rui-fleet5.log`（LX06 腿）与 `/tmp/rui-fleet6.log`（Dot2 复跑腿，2000 行窗口、3 s 轮询）。
- **电平实测**：`afplay -v 1` → 唤醒 5/5；`afplay -v 2` → **3/5**（更响反而更差，削波伤 KWS）——与 task-16「距离/电平」假设直接相关。


## 多设备互斥实测（2026-10-26 16:44–16:50，两屋同时说同一唤醒词）

- 拒绝分布：`lost_playing` **18 次**、`turn_open` **4 次**；admitted：`living` 11、`dot2` 5。
- 机制：一屋在放音乐（`living` 侧 `barge: N wake during playback`）+ 一屋有未结束的 turn 时，另一台设备的唤醒会被拒（`turn_open` 由协调器全局持有）。所以「Dot2 卡住」不是设备问题，是**串行仲裁**。
- 结论：**采样必须串行**（一次只开一台音源）；同时这是 4 台「互不串台」的 P1 候选（`lost_playing` 让另一台完全无法工作，需要按房间隔离播放态）。
- 采样脚本改为：单台、顺序、去掉 `放首歌/下一首`（避免自己把 mic 压住），只用 `hello/time/stop/bye` × 5 个电平 × 2 轮。

## 最近状态（2026-10-26 17:55，压缩前）

- 生产 `rui-dot2:1.3.0-p22` healthy，`RestartCount=0`，started 17:15:58；两台设备 `living`/`dot2` 均 connected+ready。
- 24h 稳定性提醒已改到 **2026-10-27 17:40**（schedule-80a81833），从 17:15 起计。
- 任务板：completed = task-1/2/4/5/7/8/9/10/11/13/14/15/16/18；进行中 task-19；未开工 task-17；release 卡 task-12 blocked_by = task-6/16/17/18/19（16、18 已完成）。

## 最近状态（2026-10-26 17:25）

- **已上线测试镜像** `rui-dot2:1.3.0-p22`（r071 + P2-1 + task-18）；回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **task-18 真机回归待跑**：两屋同时放歌 → 各自唤醒各 5 次，期望两屋都能应答、`lost_playing` 只在同屋出现。
- **task-16 分析稿已提交**（`a301dce`）：`docs/analysis/wake-vs-command-range.md`；核心发现：唤醒链两台同量级（0.75→0.15 区间），差距在 admitted→ASR；H2 被代码否证；「afplay -v 2 → 3/5」仅为现场手记（低强度），不证明削波阈值。
- **task-19（P2-2）已开工**（impl-barge-ack）：先判 H-A/H-B/H-C 三条假设、再选最小改动；P2 上限 2 项已满。

## 当前状态（2026-10-26 17:30，压缩前快照）

- **生产镜像**：`rui-dot2:1.3.0-p22`（r071 + P2-1 vad.py + task-18 按房间隔离），17:15 部署、`/health` 正常、2 台设备在线。**未打 tag**；回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **已提交**：`87bb704`（P2-1）、`3bbf93f`（task-18）、`3b3b0d1` / `8ee75a1` / `c3eba68`（文档）。全部已 push。
- **已过门禁**：task-15（PASS + VERIFIED）、task-18（PASS + VERIFIED + 真机回归）。
- **task-16 分析稿**：`docs/analysis/wake-vs-command-range.md`（已提交 `a301dce`），已按 reviewer 两轮意见全部修正；采样镜像锁定 p21，数字全部重算（Dot2 0.15 = cand 5/adm 4；LX06 0.15 = cand 6/adm 4），H2 已被代码否证（`post_playback_mute_ms` 只作用于有 TTS 回复的 follow-up）。
- **进行中**：task-19（P2-2 音乐中短指令拾音）。**未开工**：task-17（AEC-lite）。P2 上限 2 项已满（P2-1 完成、P2-2 待做）。
- **task-18 真机回归结果**：客厅放歌时卧室 Dot2 连续 4 次 admitted、`lost_playing` 0 次（修复前 6 分钟 18 次）；但音乐中 ASR 只剩碎片（`'了。'`）——归 task-19。
- **采样环境**：Mac（Dot2 房间）用 `afplay`，荣耀服务器 `192.168.5.34`（客厅）用 `aplay -plughw:2,0`；两台 NTP 同步，跨机对齐不确定度 ≤150 ms；**采样必须串行**（两屋同时说同一唤醒词会互相饿死）。
- **待批**：4 台新设备到位后手动添加；打 tag 前的版本号确认；24h 稳定性结果。

## task-19 收口（2026-10-26 18:05，**第 5 轮为 timebox 上限，9 哥 已定**）

- **门禁**：reviewer-barge **PASS**（出口穷举：process 内唯一裸 `gate.disarm()` 在 `_disarm_gate:2661`，无第五条绕过）；verifier-tools-2 **VERIFIED**（真实 vad `_cue_excised` 静音超时 → `blocked=False`、`queued_s=0.040`；我方音频 disarm → 同样放行；幂等两出口 `queued=2`）。全量 **1626/17**、ruff 绿（I001 已修）。
- **实现**：`process.py:508 _stale_speech_blocked` + `:2647 _disarm_gate(reason)`（唯一 disarm 出口，armed 时清位）+ `_flush_speech()` + `_accept_play_pcm:2942` 丢迟到 PLAY_PCM；`turn.py:1127 say(keep_abort=False)`、`:1135` abort 已置位则 return 0；`app.py:2841-2848` 任何 wake 都 `runner.abort()`。
- **提交**：`989baa1`；镜像 **`rui-dot2:1.3.0-p23`**（18:00 部署，healthy、2 台在线、未打 tag）。回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **真机 A/B（18:00–18:02，第一次）结果不可用**：10 次「唤醒+指令（音乐中）」只 3 次 admitted、1 条 turn（transcript `有点。`），`rejected=0`。判断为**采样协议问题**（`afplay` 起播后 8 s 即说唤醒词、音乐+TTS 混叠、且句间节奏与 `stop` 后音乐重启重叠），**不是结论**。需要按「每轮先确认播放态、唤醒前留足稳定播放、指令用 1.5 s lead」重跑，并用 `fleet_matrix` 出表后再下结论。
- **已知限制（不再扩范围，发布后开卡）**：① `runner.abort()` 是全局单 flag，B 房唤醒会中止 A 房正在合成的回复（runner 单会话）；② 已播出的告别尾音无法追回（只能阻止后续 chunk），彻底消音需「本机 TTS 参考消减」；③ `_feed_gate:1962/:1967` 是直接清位而非调 helper（功能等价，表述差异）。

## 音源校准（9 哥 2026-10-26 18:10 实机反馈，**强制前置**）

**9 哥 真人试 Dot2 的结论**：音乐播放中「下一首」**是可以的**，只是要离麦克风近一点；并指出 **Mac 播放的测试音太小**。→ 早期采样存在**系统性激励偏差**，task-16 / task-19 此前「唤醒灵敏、指令要凑近」的结论**可能部分或全部是装置伪影**，在重新校准前不得作为产品结论引用。

必须做（不是可选）：
1. **校准音源**：目标 1 m 处 ≈ **60 dB(A)**（正常人 1 m 说话）。
   - Mac 系统音量 100；音频文件峰值归一到 **−1 dBFS**（不削波，用 `ffmpeg -af volume=...` 或 `loudnorm` 后校验 `peak<=0.891`）。
   - 用手机 dB 表在 1 m 处实测并**记录数值**（写入分析文档）。
   - 不够响就换**荣耀服务器喇叭**（`aplay -plughw:2,0`）或蓝牙音箱当音源。
2. **校准后重跑**：① task-16 距离梯度 1 / 2 / 3 m，每档 5 次；② 「音乐播放中 + 短指令」Dot2 与 LX06 各 5 次；两次都用 `fleet_matrix --log-tz UTC` 出表。
3. 所有分析文档必须写明：**音源是什么、音量/声压级多少、怎么校准的、距设备多远**。
4. 「早期采样音源偏小」作为**已知偏差**写进 `docs/analysis/*` 的限制段（不悄悄改结论）。
5. **结论更正规则**：校准后若「唤醒灵敏 / 指令要凑近」消失 → 原结论是装置伪影，在 HANDOFF 与分析文档里**显式更正**；若仍存在 → 才继续查增益/AEC/VAD。

**同时作废的旧 A/B**：18:00–18:02 那次「音乐中 3/10 admitted、1 条 `有点。`」按本偏差处理，不作为 p23 的验收数据；校准后重跑才算。

## 风险与未决

- **task-6 P1 已交付，等 P2 决策**：Dot2 唤醒 30/30 admitted、指令 26/30；掉分两处（ack cue 回声 13/26、音乐中「下一首」2/5）已落日志行；LX06 与小爱对比仍缺样本（不同屋）。
- **B1 中文唤醒（task-3，BLOCKED）**：V0 英文 stock 模型对产品无效（中文「你好小瑞」只走 RUI KWS，英文零事件、`wake_candidates=0`）。真前置不是固件：① 中文 OWW 模型需 `oww_forge` 自训（25GB/数小时，效果待测）；② bridge 无 `/shell` 通道（`adapter.py:81`），最小验证需一次性 adb 推 ~1MB 分类器。待 9 哥 决定：是否再跑 V0 / 是否接受自训成本 / 生产机是否允许一次性 adb / barge 门槛是否 0.25。
- **EQ 耳测未做**：测量指标改善已证，但 9 哥 尚未 A/B；`trim_db=-2.0` 未重调（A 计权响 4.1 dB）。
- **cue 回声残留**：envelope 匹配对设备延迟 >~950 ms 或与用户语音完全重叠时回退旧行为，可能切掉 ~300 ms。
- **多设备尚未实测**：`multi_device.enabled=true`、`max_planes=4`，但只有 1 台 Dot2 在线；唤醒词不按房间隔离（`lost_same_word`）；`DOT2_CONTROL_PORT` 是全局 env，加第 2/3 台前必须清掉；多设备默认共享 secrets。
- **24h 稳定性未复查**：从 p22 部署（17:15）起计，尚未满窗；13:36 那轮作废。
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
cd 01_Code/projects/house-ear && .venv/bin/python -m pytest -o addopts='' -q   # 期望 1613 passed / 17 skipped
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
