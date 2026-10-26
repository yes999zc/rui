# 里程碑：双音箱打磨（LX06 vs Dot2）

来源：9 哥 2026-10-26 09:05 反馈 + 真机体验。目标不是加功能，而是**把两台音箱各自的长处喂给对方**。

## 已确认的事实
| 项 | LX06（小爱） | Dot2 | 备注 |
|---|---|---|---|
| 唤醒灵敏度 | 一般 | **很好**（9 哥：比小爱好） | Dot2 走设备侧 OWW 时更灵敏；当前生产日志显示 `device_wake` 回落 RUI KWS，需先确认实际生效路径 |
| 打断（barge-in） | 可用 | **不好用**（8:53 日志） | 待分析：VAD 门限/回声消除/播放期间收音/EQ 遮挡 |
| 音乐音质 | 可接受 | **沉闷、像在缸里** | 怀疑 EQ/扬声器保护/DSP 链路；Dot2 是否走喇叭 EQ 参数 |
| 按房间告别 | 已验证 | 已验证 | 两轮真机均通过 |

## 待办（按优先级）
1. **barge-in 复盘**：取 2026-10-26 08:53 前后 Dot2 会话日志，定位「说了没停」「停了没听」的具体环节（ASR/VAD/AEC/仲裁）。
2. **音质**：确认 Dot2 播放链路（采样率/位深/音量目标/EQ/限幅），对比 LX06；给出可测的 EQ 方案与 A/B 方法。
3. **唤醒灵敏度迁移**：把 Dot2 的 OWW 触发策略（阈值/双门限/上下文唤醒）评估能否用于 LX06；反之把 LX06 的误触发抑制用到 Dot2。
4. 每项都要：可复现证据 → 改动 → 真机 A/B（9 哥）→ 写回本文件。

## 约束
- 真机听感只能 9 哥 判断；Agent 侧只做可测指标（延迟、能量、频谱、成功率）与日志证据。
- 涉及设备侧参数时优先走 plug 的 `audio_defaults`/manifest 能力，避免内核硬编码。

---

# 诊断 1（2026-10-26 09:20，只读排查，未改代码）

> 范围：针对 9 哥 09:05 的四条反馈做「有证据的诊断」。只读生产日志 + 读源码 + 离线信号实验，全程未写生产、未改仓库源码。
> 结论按「可证伪」写：每条假设都给出证据行号与反证；测不出来的写进 §7。

## 0. 一句话结论

三条**相互独立**的缺陷叠加成了「打断不好用」，再加一条音质链路的实测偏色：

1. **播放期间 Dot2 的上行被插件整段静音**（`_gate_mic` 在 `ref_channels=0` 时返回全零），而设计里"打断不受影响"的前提是走设备侧 OWW 通道 —— 该通道当前**未生效**（日志明确 `RUI KWS stays in charge`）。⇒ 音乐播放中不存在可用的打断入口。
2. **打断后的音乐恢复有回退重播**：`PLAY_LAG_SECONDS=0.55` 每次暂停都回退，实测同一段音乐出现 `pause 43.9 s → resume 43.9 s → 1.0 s 后 pause 43.6 s → resume 43.6 s`。
3. **每次 Dot2 唤醒固定吃 ~407 ms 仲裁延迟**（LX06 的打断只吃 1 ms），加上 690 ms cue，用户说"打断"到有反应 ≈ 1.1 s。Dot2 的续听门还要 5.0 s 才开（LX06 是 1.0 s）。
4. **音质**：播放链路实测把 100–300 Hz 抬 **+23 dB**、3–6 kHz 压 **−2.7 dB**、限幅器平均衰减 **−10.1 dB**、波峰因数 14.5→7.7 dB。叠加源文件本身只有 **16 kHz**（上限 8 kHz）⇒「沉闷、像在缸里」有明确的量化来源，不是主观玄学。

## 1. 证据来源与环境快照

| 项 | 值 |
|---|---|
| 生产容器 | `rui-dot2:0.7.0-pre6-615e682`，Up 36 min（08:32 起），healthy |
| 日志时区 | `service.log` 用 **UTC**；本文时间除标注外一律 **CST = UTC+8** |
| 日志区间 | `2026-10-26 00:40–01:00 UTC` = 08:40–09:00 CST，406 行（去掉 `[level] voice peak` 噪声后约 90 行有效事件） |
| Dot2 生效音频 | capture 16 kHz / 1ch / 0 ref / S16；playback **48 kHz**；aec **off**；`DOT2_PLAYBACK_GATE_MS=400`、`DOT2_MIC_GAIN_DB=18`、`DOT2_VOLUME_CAP=100`、`DOT2_EQ_DIR` 已设 |
| LX06 生效音频 | capture 16 kHz / 8ch（6 mic + 2 ref）/ S32；playback **16 kHz**；aec on（`who: controller`） |
| 设备音量 | Dot2 `100`，LX06 `80` |
| 设备侧 OWW | **未启用**（`oww_on_device` 默认 `off`，无中文 OWW 模型） |
| 音乐源 | `music_cache/73da4a99….pcm` = 8,581,722 B / 268.2 s = **32,000 B/s ⇒ 16 kHz mono S16** |
| Dot2 续听门 | `followup_delay_ms 2500` + `post_playback_mute_ms 2500` ⇒ 日志里固定 `follow-up gate open (5.0 s after the last block)`（LX06 为 `1.0 s`） |

## 2. barge-in 时间线（08:43:05–08:55 CST，Dot2 为主）

「停止延迟」拆成三段：`wake→admit`（仲裁）、`admit→stop_play`（服务端停机）、设备侧 ALSA 残留（固定 ~300 ms，日志不计）。

| # | CST | 事件 | 日志行 | 延迟 |
|---|---|---|---|---|
| 1 | 08:43:05.5 | Dot2 `wake: 你好小瑞`（静默期，音乐尚未开始） | 10162–10163 | admit **406 ms** |
| 2 | 08:43:17.3 | `music_play` → 08:43:18.2 `playback 268.2 s at 48000 Hz`（Dot2 开始放歌） | 10170–10177 | — |
| 3 | 08:43:23.3 | `follow-up gate stays shut: music owns the speaker`（第二次 08:52:19.4 同样） | 10179 / 10425 | — |
| 4 | **08:43:18–08:45:11** | **约 113 s 音乐播放期内：0 次 wake、0 次 barge** | — | **无打断记录** |
| 5 | 08:45:09.9 | `music_stop` → `output owner released`；同刻 `debug say: '停止'` 证明这是**控制台注入、非语音** | 10190–10191 / 10196 / 10202 | — |
| 6 | 08:49:22.8 | Dot2 `wake`（此时**没有音乐在播**）→ 08:49:23.18 admit 408 ms → `barge: dropped 0.0 s` / `silence_after_ms=3` | 10244–10250 | admit 408 + stop 3 ms |
| 7 | 08:49:25.6 | `[seg] 1.46 s rms=0.0348 peak=0.1849` → `noise, no turn: '嗯。'`（**说了但被判噪声丢弃**） | 10253 起 | — |
| 8 | 08:51:27.7 | **LX06** 打断：wake → admit **1 ms** → `dropped 2.8 s` / `silence_after_ms=0` | 10328–10333 | admit 1 + stop 0 ms |
| 9 | 08:52:08.1 | Dot2 `wake`（音乐尚未开始，6 s 后才开播）→ admit 409 ms → `barge: dropped 0.0 s` | 10403–10408 | admit 409 + stop 3 ms |
| 10 | 08:52:14.4 | 第二次 `playback … at 48000 Hz`（Dot2 再次放歌） | 10421 | — |
| 11 | 08:52:25.9–27.7 | **设备物理按键** 4 次：`clickType 113`（muted→true）、`113`（muted→false）、`138` down/up（heldMs 191） | 10427/10429/10430 | 只记日志，**无任何服务端动作** |
| 12 | **08:52:14–08:53:31** | **约 77 s 音乐播放期内：0 次 wake、0 次 barge** | — | **无打断记录** |
| 13 | 08:52:58.5 | `dialogue preempts music` → `playback paused for speech at 43.9 s` | 10437–10438 | — |
| 14 | 08:53:08.5 | `playback resumed after speech at 43.9 s`（音乐会自己回来） | 10460 | — |
| 15 | 08:53:09.5 | 再次抢占 → `playback paused for speech at **43.6 s**`（**位置回退 0.3 s**） | 10467 | — |
| 16 | 08:53:19.9 | `resumed after speech at 43.6 s` → 又重播同一段 | 10484 | — |
| 17 | 08:53:30.6 | `music_stop`（控制台注入，非语音） | 10491–10494 | — |

**结论 1（可直接证伪的硬事实）**：Dot2 在音乐播放的约 190 s 里**一次唤醒都没有**；而它在静默期的 3 次唤醒（#1/#6/#9）全部成功。这不是"偶发"，是"播放中必然失败"。
**结论 2**：打断的**服务端停机本身很快**（3 ms / 0 ms），慢的是 `wake→admit` 的 407 ms（Dot2）与"能不能听见"。
**结论 3**：每次抢占都会把音乐往回倒 0.3–0.55 s 再重播，听感上是"卡一下、倒带、又回来"。

## 3. 根因假设（按可能性排序）

### H1（最高置信，已可判定）：播放期间 Dot2 上行被整段静音，而设备侧打断通道未生效 ⇒ 播放中物理上无法打断

**证据链（代码读出来的，不是猜的）**：
- `rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:283-301` 的注释写明策略：*"the uplink is muted while our audio is on the speaker and stays muted for the post-playback window. Barge-in is unaffected -- it rides the device-wake channel (barge_via_device)"*。
- `adapter.py:119` `PLAYBACK_LIVE_S = 0.0`，`adapter.py:296-299` 用 `DOT2_PLAYBACK_GATE_MS`（生产 = 400）；`adapter.py:622-635` `_silence_window_active()`：`elapsed` 从**每个 `play()` 块**起算（`adapter.py:676` 刷新 `_last_play_at`），`live_s=0` ⇒ **整个播放期都落在静音窗内**，最后一个块之后再静音 400 ms。
- `adapter.py:637-640` `_gate_mic()`：`if self.ref_channels == 0: return bytes(len(piece))` ⇒ **没有参考通道时返回全零块**。生产 `devices[dot2].audio.capture.ref_channels: 0`。
- 全零上行 ⇒ 跑在服务端 plane 里的 RUI KWS 听到的是数字静音 ⇒ 不可能命中唤醒词。
- 设计上的替代通道（设备侧 OWW）**没有生效**：`service.log:10101` 与 `10111`（每次重启都打）`device_wake: manifest declares wake.source=device, but probe() does not advertise it; falling back to RUI KWS` / `re-probe budget spent (12 attempts); RUI KWS stays in charge`。原因见 `adapter.py:145-147`（`DEFAULT_OWW_MODEL = ""`，EchoMuse 无中文 OWW 模型）与 `214-219`（`oww_on_device` 默认 `off`）。
- 日志反证检查：窗口内**没有**任何 `device_wake during playback ignored` / `device_wake ... barge=true` 行 ⇒ 设备侧通道确实一条事件都没发。

**⇒ 这条可以直接判定：当前构建下，"音乐播放中用唤醒词打断 Dot2" 不可能成功。** 9 哥 的听感（"打断不好用"）与日志（0 次播放中唤醒）完全吻合。

**次要但仍成立**：`_barge_allowed()`（`src/rui/plugins/contract.py:1700-1711`）要求 `source=="device"` 且能力集含 `{device_wake, barge_via_device, playback}`。设备**已声明** `oww_trigger / oww_shadow / aec_hw_ref / ref_uplink`（`service.log:10103` 的 caps 列表），所以是"模型没推上去"，不是"硬件不支持"。

### H2（高置信）：Dot2 每次唤醒固定吃 407 ms 仲裁，打断也不例外；LX06 走快路径只要 1 ms

**证据**：Dot2 三次唤醒 admit 分别 406 / 408 / 409 ms（行 10162→10163、10244→10245、10403→10404）；LX06 同窗口 08:51:08 普通唤醒 404 ms，但 08:51:27 的**打断**只要 **1 ms**（10328→10329）。
**解释**：`arbiter.py` 文档明确「only the current output owner device may barge; the barge fast path does not wait for the merge window」。Dot2 那两次"barge"发生时控制面快照是"没在播"，所以按普通候选走 400 ms 合并窗 —— 和 H4 的误判是同一件事的两面。
**⇒ 影响可测**：`wake→stop` 服务端已 3 ms，但总体感观是 407+690(cue)≈1.1 s。若修好 H1，dot2 打断走快路径后目标应 ≤150 ms。

### H3（高置信，音质主因之一）：桥接 DSP 的「stock EQ ladder + 音色搁架 + 限幅」把声音做成低音肥、动态平、高频缺

见 §4 实测。要点：`dsp.py:49-61` 默认 `trim_db=-2.0`、`lowcut 250 Hz / -6 dB`、`highshelf 4000 Hz / -2 dB`、`limiter -1 dBFS`，`mode="stock"` 时再叠 `EQ_100` FIR（1024 taps）。

### H4（中置信，需一行日志才能定论）：barge 判定输入没有打点，出现两次"无声音来源的 barge"

**证据**：08:49:22.8 与 08:52:08.1 都打了 `barge: N wake during playback, dropped 0.0 s queued`，但没有配对出现 `playback paused for speech`（对照 08:51:39.1 LX06 的打断是有的，行 10355–10360），且当时按日志没有文件在播。判定式在 `src/rui/audio/process.py:1713-1715`（`_speak_queued_s > 0 or _playback_task 未结束`），两个输入都没进日志 ⇒ 无法从现有日志区分"确实还有一段尾巴"与"状态变量没清干净"。
**反证/边界**：`_speak_queued_s` 被 `max(0.0, …)` 夹住（`process.py:2423-2425`），理论上不会留残值；因此更可能是 `_playback_task` 生命周期问题。
**⇒ 动作**：把两个输入打进那行日志（1 行改动），下一次真机就能定论；同时它会让 dot2 打断没法走快路径（见 H2）。

### H5（中置信）：打断后音乐恢复会回退重播（`PLAY_LAG_SECONDS = 0.55` 叠加二次抢占）

**证据**：`process.py:2649` `PLAY_LAG_SECONDS = 0.55`，`_pause_for_speech()`（`process.py:2651-2670`）每次暂停都把 `_file_position` 往回减 0.55 s；时间线 #13–#16 实测 `43.9 → resume 43.9 → 1.0 s 后又 pause 43.6 → resume 43.6`。两次抢占间隔 0.95 s，用户听到的是"倒退 + 卡顿"。
**注意**：同一套逻辑 LX06 也在用，但 Dot2 放歌是主场景，所以只有 Dot2 被抱怨。

### H6（中置信，影响"第二句话"而非打断）：Dot2 续听门 5.0 s 才开

**证据**：`process.py:743-748` 把 `followup_delay_ms(2500)` + `post_playback_mute_ms(2500)` 相加；日志固定 `follow-up gate open (5.0 s after the last block)`（Dot2）vs `1.0 s`（LX06）。⇒ 回答结束后 5 s 内说的"再来一首/声音小点"只能靠唤醒词。

### H7（低置信，未证实 / 已排除项）

- **VAD 门限偏高**：反证 —— `vad_min_speech_ms=200 / vad_end_silence_ms=600` 与 LX06 共用，且 08:49:25 的 `[seg] 1.46 s rms=0.0348` 是被 `noise, no turn: '嗯。'` 丢的（ASR 侧判噪声），不是 VAD 没切出来。**→ 不是 VAD 门限问题。**
- **AEC 未接（作为 Dot2 播放期问题）**：Dot2 侧 `aec.enabled: false`、`ref_channels: 0` ⇒ 服务端 AEC 没有参考信号可用（`src/rui/audio/process.py` 里 `ref_active` 为假）。但注意：**在当前构建里 AEC 不是瓶颈，全零上行使 AEC 无意义**（H1 先决）。
- **`wake arbiter` 的 merge window 400 ms 太小**：反证 —— LX06 普通唤醒 404 ms 也够用（08:51:08 一次命中），说明窗口够；问题是 Dot2 打断被当普通候选（H2）。

## 4. 音质链路事实：Dot2 与 LX06 差在哪

| 环节 | Dot2 | LX06 |
|---|---|---|
| 源文件 | 同一份 `*.pcm`，**16 kHz / mono / S16**（32,000 B/s） | 同 |
| 服务端重采样 | 16 k→**48 k**，`src/rui/audio/resample.py:13` **线性插值**（自述"不是音乐引擎"） | 16 k→16 k（恒等，无损） |
| 插件 DSP | **有**：`EQ_100` FIR + lowshelf 250 Hz −6 dB + highshelf 4 kHz −2 dB + trim −2 dB + limiter −1 dBFS | **无** |
| 播放音量 | 100（cap 100） | 80 |
| 声学 | 单个小喇叭 + 无 AEC | 6 麦阵列 + 设备级 AEC |

**离线实测（用生产同一条代码路径跑，非估算）**：
- `EQ_100.cfg`（从生产拉的真实曲线，1024 taps）相对 1 kHz：`+20.0 dB @100 Hz`、`+26.6 dB @174 Hz`、`+19.9 dB @250 Hz`、`+2.7 @500`、`−7.9 @2k`、`−12.1 @3k`、`−6.6 @4k`、`−10.0 @6k`。
- 叠上两个搁架 + trim 后：`+21.8 dB @174 Hz`、`+16.9 @250`、`−8.0 @2k`、`−12.6 @3k`、`−11.6 @6k`。
- 用 `LoudnessProcessor`（`dsp.py`）对 10 s 音乐样（粉噪 + 60 Hz 鼓点、限带 8 kHz）整链跑一遍：**100–300 Hz 相对 1–3 kHz 抬 +23.0 dB**、300–1k +11.5 dB、3–6k **−2.7 dB**；**限幅器平均增益衰减 −10.1 dB**（最深 −18.4 dB）；波峰因数 **14.5 → 7.7 dB**；RMS −15.4 → −8.7 dBFS。

**"沉闷、像在缸里"最可能的来源（按贡献排序）**：
1. **源就是 16 kHz**（上限 8 kHz）：气声、齿音、镲片全在 8 kHz 以上，物理上不存在。LX06 也是这份源，但它是"窄而亮"，Dot2 是"窄 + 低音肥 + 压缩" ⇒ 只有 Dot2 被形容成"缸里"。
2. **100–250 Hz +17~22 dB 的低频包 + 1–6 kHz −8~−12 dB 的塌陷**（H3）⇒ 典型"桶声/箱声"。
3. **限幅器常驻 −10 dB**：低频大头持续压住整条链，鼓点一来整体往下抽 ⇒ 动态被压平、听感"闷而吵"。
4. **等响度**：小喇叭在 100% 音量下低频放不出来，EQ 抬的 200 Hz 反而变成"糊"，高频又不够 ⇒ 音量再大也不"开"。
5. EQ 阶梯本身**退化**：`EQ_50/60/70` 三个文件 md5 完全相同（`5ae935…`），只有 80/90/100 不同 ⇒ 音量 50/60/70 用同一条曲线；音量 100 用 `EQ_100`。所谓"按音量分档"其实只有 4 档。

## 5. 唤醒灵敏度：为什么 Dot2 比小爱灵（事实部分）

- **KWS 门限对两台是同一个**：`keywords.txt` 只有一行 `n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞`（boost 2.00 / threshold 0.15），且 `audio.voice.kws_threshold` 是**死字段**（`src/rui/audio/kws.py:270-276` 明确"nothing at runtime reads that field"，文件后缀才是唯一门限）。⇒ **灵敏度差异不来自门限。**
- 差异来自输入链：Dot2 = 1 个麦克风 + `DOT2_MIC_GAIN_DB=18` 的 18 dB 增益 + 近场；LX06 = 6 麦阵列 + 设备级 AEC + `mic_shift 12 / ref_shift 16` 的定点处理。AEC 与阵列波束本身就会吃掉一部分近讲能量。
- 所以"互相借鉴"的正确方向不是搬门限，而是：**给 LX06 提高上行增益/降低 VAD 起始门槛** 换灵敏度（会换来误触发），**给 Dot2 补 AEC/参考通道** 换抗噪（见 §6 A2）。
- ⚠️ 未定论：两台 plane 都在静默期打 `[level] voice peak 0.0 dBFS over 30 s`（`process.py:2882-2890`，> −55 dBFS 才打）。0.0 dBFS 是"满刻度/削波"还是"指标定义问题（例如 gate 清零或统计口径）"，仅凭日志无法判定，**先别拿它当灵敏度指标**。

## 6. 可测的下一步

### A. 不改设备固件就能做（按性价比排序）

| # | 改什么 | 怎么测 | 预期指标 / 判据 |
|---|---|---|---|
| **A1** | **先证伪 H1（零风险，只改 env + 音量）**：`DOT2_PLAYBACK_GATE_MS=0`（或 `DOT2_PLAYBACK_LIVE_MS=350`），Dot2 音量降到 40，播放中喊唤醒词 | 真机 3 次；看 `service.log` 是否有 `wake` + `barge:` 行；记录 `dropped x s` 与 `silence_after_ms` | 若音量 40 能命中 ⇒ 只是遮挡，可做"低音量可打断"分级策略；若 40 仍 0 命中 ⇒ KWS 在音乐下 SNR 不足，只能走 A2/B1 |
| **A2** | **打开参考通道 + 服务端 AEC**：`devices[dot2].audio.capture.ref_channels: 1`、`aec.enabled: true`、`who: controller`（设备 caps 已声明 `aec_hw_ref`/`ref_uplink`，插件侧 `adapter.py:343` 与 `637-645` 已支持"只清 mic 保留 ref"） | 启动后看日志 `uplink block … channels=2`；播放中喊唤醒词；量 ERLE | 目标 ERLE ≥ 10 dB 且播放中唤醒命中 ≥ 2/3；失败则回落 A1/B1 |
| **A3** | **给 barge 判定打点**（1 行日志）：把 `_speak_queued_s`、`_playback_task.done()` 打进 `process.py:1758` 那行 | 下次真机对比"有无声音来源"的 barge | 消除 H4 的不确定性；顺带确认快路径为何没走（H2） |
| **A4** | **修回退重播**：`PLAY_LAG_SECONDS` 只在实际有在途音频时回退，或改用设备 play head（`reports_consumption`） | 同一首歌连续打断 3 次，看 `playback paused/resumed … at X s` 是否单调不减 | 位置回退 ≤ 50 ms；听感无"倒带" |
| **A5** | **音质三分法 A/B（今天就能做）**：插件已有 loopback 控制口 `PUT /loudness`。① `mode: off` ② `mode: stock` + `lowcut_db: 0, highshelf_db: 0` ③ 现状 | 同一首歌、同一音量，9 哥 盲听 A/B/C；同时用耳机录一段对比频谱 | `off` 仍闷 ⇒ 16 kHz 源是主因；变亮 ⇒ DSP 有罪，按 ② 分离"EQ ladder vs 搁架 vs 限幅" |
| **A6** | **提高源采样率**：music provider 若能出 44.1/48 kHz，Dot2 直通 48 k（免插值），LX06 走 16 k | 看缓存 pcm 的字节率（现 32,000 B/s）；测 8 kHz 以上能量 | 8 kHz 以上能量 > 0（现在恒为 0） |
| **A7** | **续听门对齐**：Dot2 `post_playback_mute_ms: 2500 → 1000`（或与 LX06 一致），使 `follow-up gate open` 从 5.0 s 降到 2.0 s | 回答结束后 3 s 内说"再来一首" | 续听成功率 ≥ 2/3 |

### B. 需要设备侧配合

| # | 要什么 | 为什么非它不可 | 判据 |
|---|---|---|---|
| **B1** | **推一个中文 OWW 模型到设备**（`oww_on_device: on` + `DOT2_OWW_MODEL=…`） | 这是 `adapter.py:283-301` 设计里"打断不受上行静音影响"的**唯一替代通道**；现在 `RUI KWS stays in charge` 就是缺口 | 日志出现 `oww_wake` / `device_wake`；播放中打断成功且 `barge=true` |
| **B2** | **设备侧硬件 AEC**（fw 已声明 `aec_hw_ref`）：开启后 uplink 直接给已消回声的 mic | 比服务端 AEC 更省延迟、更稳；也让播放中可以放开上行 | ERLE ≥ 15 dB；播放中唤醒命中 ≥ 2/3 且无自听（不出现自己回答自己） |
| **B3** | **把物理按键接进 rui**：`clickType 113`（静音）/`138`（动作）当前只记日志（`adapter.py` 无 handler） | 08:52:25–27 的 4 次按键说明 9 哥 播放中确实在找"手动打断" | 按键 → `StopAudio`/`PauseMusic`，延迟 ≤ 200 ms |
| **B4** | **确认 EchoMuse 半双工语义**：播放期间设备到底给不给 mic、给不给 ref | 若固件在播放期只给 ref 不给 mic，A2 也救不了，只能靠 B1 | 用 `DOT2_UPLINK_DUMP` 录一段播放期上行，看是否全零/是否含 ref |

## 7. 只能由 9 哥 真机 A/B 判断的点（代码与日志判不了）

1. **"沉闷"到底是"低音过肥"还是"缺高频"**：需要盲听 A5 的三档，给出偏好排序（我能给的只有频谱数字）。
2. **TTS 是否也闷**：`在呢` / 回复走的是**同一条 DSP**。若语音也闷 ⇒ 全链路问题；若只有音乐闷 ⇒ 源采样率（16 kHz）成分更大。这条决定先修 DSP 还是先修音源。
3. **Dot2 音量 100 是否本来就"太响而闷"**：降到 60 / 40 是否"不闷但太轻"（等响度曲线），只能靠耳朵。
4. **打断时他实际说了什么、隔多久**：cue 690 ms + admit 407 ms ≈ 1.1 s，如果他的第一句在 1.1 s 内说完，那"打断不好用"里还有一半是**首句被 cue 盖掉**（日志里 08:49:25 的 `noise, no turn: '嗯。'` 很像这种情况）。
5. **LX06"可接受"是不是因为它更窄反而更亮**（16 kHz 直通、无压缩、无低音包），需要同一首歌两台对照听。
6. **卧室 vs 客厅的声学差异**（靠墙/桌面反射）对"缸声"的贡献占比。

## 8. 复现方式（脱敏，只读）

- 日志：生产 `data/service.log`，按 `00:4[0-9]|00:5[0-9]`（UTC）过滤得 406 行；关键行号见 §2 表（行号对应当前 `service.log`，日志轮转后失效，届时用关键词 `barge:` / `playback paused for speech` / `dot link: button` 重定位）。
- 时间换算：容器日志写 **UTC**；宿主 `date` 实测 **CST（UTC+8）** ⇒ `CST = UTC + 8`，看日志时先做这一步换算，否则会把 00:53 当成"半夜"。
- 曲线与 DSP 实验：把生产 `EQ_*.cfg` 拷到 `/tmp/eqdir`，用 `LoudnessProcessor(LoudnessConfig(), Path("/tmp/eqdir"), rate=48000)` 跑合成样；EQ 频响用 1024-tap FIR 的 FFT（相对 1 kHz）。
- 未做的事：没有改任何源码、没有写生产、没有重启容器、没有触发真机语音。

