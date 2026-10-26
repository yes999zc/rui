# Dot2 vs 小爱 LX06 —— 体验矩阵与归因（P0/P1）

- 生成时间：2026-10-26 15:2x（Asia/Shanghai）
- 任务：task-6（阻塞发布）。本文件只有数据与归因，**不含任何代码改动**。
- 采样工具：`scripts/fleet_say.py`（播放 + 生成 marks）、`scripts/fleet_matrix.py`（日志时间窗对齐 + 判定，每格带 `--log` 1-based 行号）
- 原始数据：`/tmp/rui-fleet3.log`（采样期 982 行；该文件之后仍在追加，采样期的行号不变）、`/tmp/fleet-marks3.csv`（30 行）；产物命令：
  ```
  python scripts/fleet_matrix.py --log /tmp/rui-fleet3.log --marks /tmp/fleet-marks3.csv \
      --log-tz UTC --window 12
  ```
- 日志时区：容器日志为 **UTC**（naive），marks 为 `+08:00`；`--log-tz UTC` 是必须的，否则整窗错 8 小时全判 unknown。

## 1. 采样条件（重要限制，先读这里）

| 项 | 值 |
|---|---|
| 被测设备 | Dot2 ×1（生产 NAS 168 上唯一在线 Dot2，`device_id=dot2`） |
| 音源 | 本机 Mac `say -v Tingting -r 190`，系统音量 100，与 Dot2 同一房间 |
| 协议 | 每句：先说唤醒词「你好小瑞」→ 停 1.5 s → 说该句；句间 ≥12 s；6 句 × 5 轮 = 30 句 |
| 小爱 LX06 | **未采样**：在另一个房间，Mac 音源覆盖不到（不搬设备、不用推断补数） |
| 样本量 | 30（Dot2）；每格 5 轮 |

**这不是人声**：TTS 音源的音量、频谱、发音都与真人不同，且源离设备很近（设备音量 100 时多次出现 `peak=1.0000` 削波）。所以本文件是**可复核的现象记录 + 归因线索**，不是最终设备结论；人声 A/B 必须由 9 哥 复测（§5）。

## 2. 矩阵（Dot2，5 轮 × 6 句）

| 句 | phrase_id | 成功 | 判定口径 |
|---|---|---|---|
| ①你好小瑞 | `hello` | **5/5** | 仅测唤醒（`--wake-only-phrase`）：有 admitted 即成功，不要求 turn |
| ②几点了 | `time` | **5/5** | admitted + turn（asr_done/play_start） |
| ③放首歌 | `play` | **5/5** | 同上 |
| ④下一首 | `next` | **2/5** | 同上（3 次 admitted 后无 turn 结果） |
| ⑤停止播放 | `stop` | **5/5** | 同上 |
| ⑥退下吧 | `bye` | **4/5** | 同上 |
| **合计** | — | **26/30** | success 26 / fail 4 / unknown 0 |

设备级（同一次采样）：

| 指标 | 值 | 备注 |
|---|---|---|
| wake candidates | 30 | 每句窗口恰好 1 条，无重复候选 |
| admitted | 30 | 唤醒→仲裁 100% admit，**0 拒绝**（无 `turn_open`/`lost_same_word`） |
| admit 延迟 | 1–410 ms | `admitted after N ms` |
| turns | 26 | 与 fail 的 4 个窗口对应 |
| `first_response_ms` | **证据不足** | 26 个 turn 的 trace 里该字段多为 `null`（旧 plane/未上报 play_start），工具按「证据不足」处理，不补 0 |
| barge turn | 0 | 本协议未触发 barge 路径 |
| 播放 gap | 25 个 turn 有、max 301.2 ms | `playback_gap_count` 显式存在才统计 |

## 3. ASR 文本质量（同一批 26 个 turn）

对数：26 个 turn 里，`transcript` **与用户所说一致**的只有 6 条（`几点了？`×2、`下一首。`×2、其余 2）。另外 20 条读到了助手自己的声音：

| transcript | 次数 | 用户实际说的 |
|---|---|---|
| `在呢。` / `在的。` / `在了。` / `在吗？` | 13 | 「几点了」「放首歌」「停止播放」等 |
| `没事。` / `没事的，那呢的话我先下吗？下一首。` | 2 | 「停止播放」「下一首」 |
| 空 | 5 | 对应 4 个 fail + 1 个 |

这不是 ASR 引擎的问题，是**输入音频里就是助手的声音**（见 §4 P1-A/B）。

## 4. P1 归因（每条都落到「哪一层 + 哪行日志」）

### P1-A　唤醒后的 ack cue 回声仍进入 ASR，抢走用户第一句

- 证据（窗口 #2，用户说「几点了」，ASR 却得到「在呢。」）：
  - `L403` `wake candidate dot2-… admitted after 408 ms`（07:01:44.656）
  - `L405` `cue: 540 ms of audio handed over in 39 ms`（07:01:44.719）← 助手开始播「在呢」
  - `L414` `[seg] 1.61 s rms=0.0461 peak=1.0000 speech=1.51 s`（07:01:46.959）← 被送入 ASR 的语音段，**削波**
  - `L425` `turn: {'transcript': '在呢。', 'reply': '我在，你说。', …}`（07:01:48.716）
- 层级判定：**core（音频门限/回声抑制）为主 + 采样协议为辅**
  - core：`src/rui/audio/vad.py` 的 `GateConfig.echo_block_ms`（20 ms）/`echo_match_min`（0.6）/`set_echo_reference()` —— 0.7.1 已上线，但本场景下 cue 与用户语音**重叠**且输入削波，包络匹配退化 → 回退旧行为，cue 尾音进入 ASR。
  - 协议：音源音量 100、距设备很近，`peak=1.0000` 削波；真人在 1–2 m 说话不会削波。
- 影响：用户说完唤醒词紧接指令时，可能被「在呢」抢答（`reply: 我在，你说。`），实际指令丢失 → 下一句才生效。
- 不归属：bridge / 固件（同场景 A1' 人声打断曾 8/8 正常）。

### P1-B　音乐播放中「下一首」命中率低（2/5），mic 段近乎静音

- 证据（窗口 #10，第 2 轮「下一首」，音乐正在播）：
  - `L595` `wake candidate dot2-… admitted after 1 ms`（07:04:00.614）← 唤醒正常
  - `L601` `[seg] 0.98 s rms=0.0061 peak=0.0415 speech=0.88 s`（07:04:01.827）← 送 ASR 的段几乎无声
  - 该窗口**无 turn 行** → §2 判 fail（admitted 后无 turn 结果）
- 另一轮（窗口 #28，第 5 轮「下一首」）：`[seg] 3.84 s peak=1.0000` → ASR 串成 `没事的，那呢的话我先下吗？下一首。`
- 层级判定：**硬件/固件 AEC + 播放期拾音为主**，core 的 `post_playback_mute_ms`（生产 2500 ms）可能叠加
  - 播放中 mic 段 rms 0.006/peak 0.04 = 信号被压到接近静音；`DOT2_PLAYBACK_GATE_MS=0` 已关闭 bridge 软件静音，所以剩余衰减来自设备侧 AEC + 音乐本身占据 mic。
  - 播放停止后 2500 ms 的 `post_playback_mute_ms` 会把紧随其后的用户语音切掉（这就是「下一首/停止播放」紧接音乐的原因）。
- 影响：**播放中改歌/停止的成功率明显低于其他句**；这是用户高频操作。
- 需真人复核：人声比 TTS 更近、更响，可能不存在该问题（A1' 人声 8/8），故本项标「待 9 哥 人声 A/B 确认」，不直接判为缺陷。

### P1-C　空闲唤醒后 5 s 无语音 → `wake without speech` → 10–11 s 后 idle goodbye

- 证据：`L422` `wake with no speech after 5010 ms; disarming`、`L423` `wake without speech (5.0 s); idle goodbye in 10 s`
- 层级：**core 产品行为**（非缺陷）。记录原因：采样中「只喊唤醒词」的 hello 句必然走这条路径，所以 hello 只能用 wake-only 口径判定（否则会被误算成 fail，已修）。

## 5. 待补：小爱 LX06 与真人 A/B（不推断、不补数）

1. **同一音源**：`say -v Tingting -r 190 -o /tmp/fleet6.aiff` 已可生成 6 句音频；在 LX06 所在房间用手机/音箱放同一份文件，即可得到「同源、不同房间」的对比样本。
2. **人声 A/B（必须 9 哥 本人）**：对同一 30 句协议用真人说一遍（同房间、音量 40），重点复核 P1-A（唤醒后立刻说指令）与 P1-B（音乐播放中说「下一首」）。
3. LX06 那一路的命令与 marks 格式相同，跑完直接 `fleet_matrix` 合并即可。

## 6. P2 候选（≤2 项，等 9 哥 选；选定后各自独立开卡、过门禁 + A/B）

| 候选 | 改哪里 | 预期收益 | 风险 |
|---|---|---|---|
| P2-1 cue 回声抑制加强 | `src/rui/audio/vad.py` + `src/rui/audio/process.py`（cue 期间/之后按已知 cue 音频做逐样本相减或更强包络门限；匹配失败时记录 match 分数并用保守窗） | 消除「唤醒后立刻说指令被『在呢』抢答」（P1-A，13/26 次污染） | 过强会切掉用户开头 200–300 ms；需 A/B |
| P2-2 播放中短指令拾音 | `rui-dot2-bridge`（播放期 AEC 参考通道/增益）+ core `post_playback_mute_ms` 收敛 | 提升「下一首/停止播放」在音乐中的命中率（P1-B，2/5） | 放开静音会引入回声自激；需真机 A/B |

不做：不改核心架构、不刷固件、不为追平小爱重写链路。

## 7. 结论（一句话）

Dot2 的**唤醒与仲裁**在这次采样里是干净的（30/30 admitted、0 拒绝）；掉分集中在**唤醒后的第一句话被 ack cue 回声污染**（13/26）与**音乐播放中的短指令拾音**（2/5）。两者都还需真人 A/B 才能定性为缺陷还是 TTS 音源伪影。

## 8. 双腿同源复测（2026-10-26 16:16–16:41，P2-1 部署后）

条件：同一份 48 kHz/16-bit WAV（6 句，`/tmp/fleet6/*.wav`），每句「唤醒词 → 1.5 s → 指令」，句间 10 s，5 轮。
- Dot2（卧室）：Mac Studio `afplay -v 1` 播放（`marks-dot2b.csv`，日志 `/tmp/rui-fleet6.log`）
- LX06（客厅，`device_id=living`）：荣耀服务器 `aplay -plughw:2,0` 播放（`marks-lx06.csv`，日志 `/tmp/rui-fleet5.log`）

| 指标 | Dot2 | LX06（living） |
|---|---|---|
| wake candidates | 45（含重复检测） | 30 |
| admitted / rejected | 45 / 0 | 24 / 6（`lost_playing×2`、`lost_same_word×2`、`turn_open×2`） |
| turns | 34 | 21 |
| 逐窗口有证据（30 个窗口） | **11** | **28** |
| 逐窗口无 wake 行（unknown，不计失败） | **19** | 2 |

**关键对比**：同样一份音频，LX06 那一路 30 次尝试里 30 次都产生了 wake candidate；Dot2 那一路只有 11 个窗口有证据，其余 19 个窗口**没有任何 Dot2 wake 行**。时间线（`/tmp/rui-fleet6.log` 每分钟行数）显示这些空窗成段出现，且集中在「放首歌」之后的几分钟——这与「音乐播放中/刚播完，mic 被设备侧 AEC 或 core 的 `post_playback_mute_ms` 压住」一致（见 §4 P1-B）。

**电平实验（同一台 Dot2，wake-only 5 次）**：`afplay -v 1` → **5/5** 唤醒；`afplay -v 2` → **3/5**。即「更响」反而更差——削波会伤 KWS，不是「越响越好」。这条直接支持 task-16 的假设①（电平/增益）方向，但也说明**不能靠调大音量**验证距离。

**限制（必须遵守）**：Dot2 那 19 个 unknown 窗口**不是失败**，也不计入成功率分母；它们只说明「窗口内没有该设备的 wake 证据」。要判定是「设备没听见」还是「日志被音乐/播放期压住」，需要 task-16 的分级距离实验 + `[seg]`/`cue_gate` 逐行核对。
