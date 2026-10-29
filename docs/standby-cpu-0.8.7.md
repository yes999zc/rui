# 待机 CPU 归因与 KWS 门控（0.8.7 / task-6）

> 2026-10-29 ｜ 目标版本 0.8.7 ｜ 生产基线 0.8.6 ｜ 评审者 reviewer-audio
>
> 结论先说：待机 CPU 的大头是**每帧都跑的流式 KWS**（无语音时也 50 次推理/秒）。0.8.7 增加
> `audio.voice.standby_kws_*` 门控：真空闲（无唤醒、无问答、无播放、无续听）且麦克风电平持续
> 低于阈值时暂停 KWS；**第一帧有声音就恢复**，并把 `wake_preroll_ms` 的预录音频回灌给 KWS，
> 因此唤醒词开头不丢、唤醒延迟理论上不变。默认开启（-60 dBFS / 1 s），房间噪声大时永不暂停
> （不省电、但不退化）。真机 CPU <20% / load <2 必须在 0.8.7 部署后按 §7 验收，本文不伪造。

---

## 1. 实测数据（来源：9哥 2026-10-29，生产 0.8.6）

| 设备 | 待机 CPU | 关键观察 |
|---|---:|---|
| living（LX06） | **29%** | 常开上行（8ch/48 kHz），KWS/VAD/重采样/AEC 全跑 |
| dot2（儿童房） | **11%** | 有 mic 上行（静音帧也在推） |
| dot2-c | **10%** | 同上 |
| dot2-b（次卧） | **1%** | 物理静音、**无 mic 上行** → 帧循环没东西可处理 |
| 容器整体 | **57%** | 三台 plane + 控制面 |

P0（task-5）已单独修掉 `voice peak 0.0 dBFS` 的静音误报日志——那只是**日志噪声**，不是 CPU
主因；本任务处理真正的每帧计算。

**归因的关键证据是 dot2-b**：同一份代码、同一镜像，只因为它没有上行帧，CPU 就掉到 1%。
说明待机 CPU 与"每帧到达的麦克风数据"成正比，不是定时器、日志或 trace 的锅；有上行的三台
差距（29% vs 10-11%）主要来自采样率/通道数/重采样与 AEC 路径（LX06 48 kHz/8ch vs dot2 16 kHz/1ch）。

---

## 2. 代码级调查：待机时每帧到底在跑什么

`AudioPlane._capture_frames`（`src/rui/audio/process.py:1342`）对每个 20 ms 帧依次做：

| 步骤 | 位置 | 待机成本 |
|---|---|---|
| 降采样 + AEC + RMS | `_frame_to_voice`，process.py:1264 | 有（与帧率/通道数成正比，本任务**不动**） |
| **KWS 推理** | `WakeWord.accept`，kws.py:233；调用点 process.py:1373（0.8.7 起走 `_run_spotter`） | **有：每帧一次，50 次/秒** |
| VAD 推理 | `UtteranceGate.feed`，vad.py:435 | **无**：`if not self.armed: return None`，未唤醒时直接返回 |
| 厂商信号/设备唤醒 | process.py:1375 起 | 很小（队列判空） |
| preroll / follow-up 尾巴 / ring | process.py:1398-1414 | 很小（内存拷贝，毫秒级以下） |
| `cpu_s` 累计 | process.py:1415 | 计量本身 |

`WakeWord.accept` 每收到 20 ms 音频就推进一个流式 step（`step = rate // 50`，kws.py:242），
所以**帧数 ≈ 模型 step 数**：静音 30 s = 1500 帧 = 1500 次 encoder/decoder step。生产上 KWS
用 1 个 ONNX 线程（`kws_num_threads: 1`），但 50 step/s 的持续推理就是待机时最稳定的一块负载。

VAD 只在 `gate.armed`（唤醒后）才推理，所以它不是待机成本——这也是本任务只门控 KWS、不碰
VAD/barge/AEC/播放链路的原因。

---

## 3. 改了什么

### 3.1 门控状态机（`StandbyKwsGate`，process.py:360）

对每帧只做一个**廉价**判断（`voice_rms_db` 是 `_frame_to_voice` 已经算好的 RMS，不额外跑模型）：

```
engaged（有问答/播放/cue/续听/设备唤醒/暂停…）      → 保持或恢复全速
rms >= standby_kws_db                              → 有声音：冷→热，回灌 preroll 后处理本帧
rms <  standby_kws_db 且已连续安静 >= hold_ms       → 冷：跳过本帧 KWS
否则（安静但还没到 hold）                           → 照常处理
```

- `process.py:600-609`：启动时按配置创建；`wake_preroll_ms == 0` 时强制关闭（没有可回灌的
  onset 音频，宁可不省）并打 warning。
- `process.py:1373`：`_capture_frames` 的 KWS/probe 调用收敛到 `_run_spotter`。
- `process.py:1417`：`_run_spotter` 执行判断、恢复、探测/唤醒；冷帧直接返回。
- `process.py:1472`：`_reset_and_replay_spotter` 在"冷→热且因语音触发"时 `reset()` 后按时间戳
  回灌 `_preroll`（不 drain，VAD 的 `_take_preroll` 语义不变），再喂当前帧。
- `process.py:1491`：`_kws_activity` 定义"不能冷"的状态：gate armed/speaking、turn pending、
  reply open、speaker owner（cue/dialogue/music）、播放任务、`_device_audio_pending`、
  `_music_hold_until`、cue hold、暂停、续听窗口、`_stale_speech_blocked`、device wake。
  最后一行的 `bool(self.device_wake_active)` 是保守项：**device wake 处于 active 的机器上门控
  永不暂停**（每帧都算 engaged），待机零收益、日志里不会出现 `suspended`。如果某台设备验收时
  一直没 suspended，先确认它的 device wake 是否 active；这也正是 §8 的设备侧 `wake_word_off`
  /OWW 才有意义的原因。
- 冷→热分两种：**语音触发**（`RESUME`，回灌 preroll）与**活动触发**（`RESTART`，只 reset
  不replay——此时不是唤醒词开头，回灌反而可能重放旧音频）。

失败方向是 **fail-open**：拿不到电平（`None`）按"未知"处理并保持/恢复全速；噪声高于阈值就
永不暂停；配置 probe（排障用第二 spotter）时完全保持 0.8.6 的常开行为。

### 3.2 配置（三处一致，effect = restart）

| 配置 | 默认 | 范围 | 含义 |
|---|---:|---:|---|
| `audio.voice.standby_kws_gate` | `true` | bool | 待机门控开关 |
| `audio.voice.standby_kws_db` | `-60.0` | -90 … -20 dBFS | 低于此电平算"安静"；默认远低于说话声 |
| `audio.voice.standby_kws_hold_ms` | `1000` | 0 … 10000 ms | 连续安静多久才暂停；只防句间停顿，不增加唤醒延迟 |

- schema：`src/rui/config/schema.py:359-373`
- catalog：`src/rui/config/catalog.py:703-729`（`effect="restart"`，控制台可编辑、明示重启）
- 示例：`config.example.yaml:98-107`
- 每次冷/热切换各一行 INFO 日志（`kws standby: suspended ...` / `resumed on speech` /
  `resumed for activity`），suspended 行带 `quiet frames peaked at X dBFS`，供真机调阈值。
- 门控计数器（`skipped`/`suspends`/`resumes`）目前只在进程内，未加进 metrics：`Metrics` 是
  control-plane 的跨版本契约，加字段需要单独的 additive 变更（列 P2）。

**为什么默认开启**：默认阈值 -60 dBFS 远低于正常说话与任何 KWS 能识别的唤醒词；门控只在
"确实安静且确实空闲"时生效，并靠 preroll 回灌保护 onset。代价见 §5。若现场发现异常，一条
配置即可回到 0.8.6 行为（§6）。

---

## 4. 改前 / 改后（理论 + 单测数据）

### 4.1 静音 30 s（20 ms/帧，1500 帧）

| | KWS 模型 step | 说明 |
|---|---:|---|
| 改前（0.8.6） | **1500** | 每帧 1 step，50/s |
| 改后（默认 hold=1 s） | **50** | 前 1 s 的 50 帧正常处理，随后进入 cold；其余 1450 帧跳过 |
| 跳过比例 | **96.7%** | 合成脚本：`processed=50 skipped=1450 suspends=1` |

冷态持续期间 KWS step = **0/s**；AEC/降采样/ring 仍按帧运行（本任务不改）。

### 4.2 语音出现（冷→热）

- 第一个 `rms >= -60 dBFS` 的帧触发 `RESUME`：`reset()` + 回灌 preroll（默认 500 ms = 25 个
  20 ms 块）+ 处理当前帧。
- onset 一次性突发 ≤ **25 + 1 = 26 step**（约 0.5 s 音频的推理量），之后恢复 50 step/s。
- **命中时间戳/命中位置不变**：KWS 命中的时间戳仍来自音频帧本身；回灌是同一段音频按原时间戳
  重新送入，命中位置（唤醒词结束处）与常开路径相同。门控没有插入任何等待/定时器——恢复发生
  在**语音起始的那一帧**，不是 hold 到期后。
- **墙钟检测时延最多增加一次 onset 突发**：冷→热的那一帧要先把 ≤26 个 KWS step 一次性跑完
  （25 个回灌块 + 当前帧）才可能得到命中；若单 step 在目标 CPU 上耗时 t，则相对常开路径的
  墙钟唤醒检测至多晚 26t（一次性、只发生在冷→热帧；cold 稳态与 hot 稳态都没有这项开销）。
  这与 §5.3 是同一口径，真机延迟按 §7 用 wake→cue 日志对比，不在本文伪造。
- 单测证据（`tests/test_standby_kws_gate.py`）：
  - `test_a_wake_word_arriving_while_cold_is_still_fed_and_fires`：冷态跳过的 2 帧在恢复时被
    回灌，当前帧的唤醒词被 KWS 收到并触发 `_on_wake`（`wake_count == 1`），且命中时间戳就是
    该 live 帧的 `ts_ms == 40.0`（"命中位置不变"的单测证据）。
  - `test_the_first_frame_at_or_above_the_threshold_resumes_on_speech`：阈值处 `>=` 即恢复。
  - `test_the_gate_cools_only_after_the_hold_and_counts_every_skipped_frame`：帧计数/比例正确。
  - `test_a_turn_resumes_the_spotter_without_replaying_the_quiet_ring` /
    `test_a_playback_owner_freezes_the_cold_state_without_replaying` /
    `test_an_active_device_wake_keeps_the_spotter_hot`：有活动立即全速、不回灌；也证明
    `_owner`/`device_wake_active` 这两个"不能冷"的状态。
  - 14 例全部通过；全量测试见交付报告。

### 4.3 频率语义

`LEVEL_LOG_EVERY`、metrics 2 s tick、每帧 `writer.write(metrics)` 均未改；本任务只决定
"这一帧要不要喂 KWS"，不改控制面/日志节拍。

---

## 5. 代价与风险（诚实清单）

1. **整段低于阈值的唤醒词会被漏掉**。默认 -60 dBFS 远低于语音，但远场/轻声唤醒若全程低于
   阈值，门控听不到。不要把 `standby_kws_db` 调到接近唤醒词电平；调参依据是日志里的
   `quiet frames peaked at X dBFS` 与实测唤醒成功率。
2. **噪声大的房间不会暂停**（例如持续电视声/风扇 > -60 dBFS）：没有省电，也没有退化。
   这类环境可以尝试 -55/-50，但必须做唤醒 A/B。
3. **onset 突发**：语音出现时一次性补跑最多 `wake_preroll_ms` 的音频（25 回灌 + 当前帧
   = ≤26 step）。这是短脉冲，不是持续负载；但冷→热帧的墙钟唤醒检测至多增加这段计算
   （§4.2 的 26t），若真机测到唤醒时延抖动，先看这一段。
4. **依赖 preroll**：`wake_preroll_ms: 0` 时门控自动关闭（启动 warning），避免"省了 CPU
   但丢唤醒词开头"。
5. **probe 配置时门控不生效**（保持排障期常开）；这会让排障环境 CPU 与 0.8.6 相同。
6. **未覆盖的部分**：AEC/降采样/重采样（living 的 29% 里可能有它们）、控制面、trace 落盘。
   living 专项 profile 留 P2。
7. 门控计数器没有进 `/health`/metrics（协议契约，P2）。
8. **device wake 开启 = 门控零收益**：`_kws_activity` 末尾的 `bool(self.device_wake_active)`
   让 device wake active 的机器每帧都算"有活动"，永不进 cold、日志不会出现 `suspended`，
   待机 CPU 与 0.8.6 相同。这是为保住 device wake/barge 链路的保守选择；也说明设备侧省电要
   靠 `wake_word_off`/设备 OWW（§8），而不是 NAS 侧门控。

---

## 6. 如何回滚 / 如何调参（方向别弄反）

**想保唤醒、减小影响：**
- 完全回 0.8.6：`audio.voice.standby_kws_gate: false`，重启音频面（catalog 明示 `restart`）。
  行为立即回到 KWS 常开，无数据迁移、无协议变化。
- 只减弱门控：把 `standby_kws_db` **调小**到 -70（更严的"安静"判定 → 更难进 cold、暂停更少、
  省电也更少），或把 `standby_kws_hold_ms` 调大（如 3000，需要更长的连续安静才暂停）。

**想更省电（先读 §5 代价并做唤醒 A/B）：**
- 把 `standby_kws_db` 调到约 -50（高于常见房间噪声、仍低于正常说话/唤醒词）；
- 或把 `standby_kws_hold_ms` 调小（0–500 ms）。注意 **hold=0 是更激进**：第一个安静帧就暂停，
  暂停更频繁、冷→热突发也更多，不是"减小影响"。

版本回滚仍按 HANDOFF runbook 换镜像 digest；本功能没有持久化状态。

---

## 7. 真机验收方法（部署 0.8.7 后由 9哥 执行，本任务不填假数）

1. **基线/改后 CPU**：待机 5 min 后
   `docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'` 记录容器；
   若 plane 是独立进程，用 `top -H -p <pid>`/`ps -o %cpu,cmd -C rui` 记录每台。
   目标：待机 **CPU < 20%、load < 2**（以 9哥 卡面为准）。
2. **门控生效**：`docker logs rui 2>&1 | grep 'kws standby'`。安静房间应在约 1 s 后出现
   `suspended ... (quiet frames peaked at X dBFS)`；说话/唤醒时出现 `resumed on speech`。
   若 5 min 内没有 suspended，先确认该设备的 **device wake 是否 active**（active 时门控按设计
   永不暂停，§5.8，CPU 不会下降）；否则说明该房间噪声高于阈值（读 X 调 `standby_kws_db`）。
3. **唤醒不回归**：正常距离 10 次 + 远场/轻声 5 次，目标 ≥ 9/10 与改造前同档；每次记录
   `wake` 事件时间与 cue 日志时间差（现有 trace/日志即可），与 0.8.6 基线对比。
4. **打断/播放**：播放回答或音乐时各喊 3 次唤醒词，确认 barge 正常（门控在播放期必须保持热，
   `_kws_activity` 已覆盖；这条是回归检查）。
5. **稳定性**：24 h 内 `/health` `audio_devices[].restarts` 无增长；`kws standby` 日志不应
   每几秒刷屏（正常是每次空闲/说话各一行）。
6. **调参 A/B**：若某房间 CPU 仍高，用日志的 quiet peak 选择 `standby_kws_db`（高于噪声、
   低于唤醒词），一次只改一个值并复测唤醒成功率；不达标就 `standby_kws_gate: false` 回滚。

---

## 8. dot2 `wake_word_off` 调研结论（只读，不实现设备侧）

- `wake_word_off` 只出现在 **EchoMuse 固件的 capabilities 列表**：
  `echomuse-dot2-ref/patches/0001-ref-uplink-v2.19.0-ea.1.patch:47`，以及
  `echomuse-dot2-ref/artifacts/server` 的字符串中（同处还能看到 `wakeword:` 本地引擎日志）。
  它是**设备能力声明**，不是当前 bridge/rui 调用的控制命令。
- 在 `rui-dot2-bridge` 与 `house-ear` 全仓检索：**没有任何代码读取或发送 `wake_word_off`**；
  bridge 的配置下发只有 `owwOnDevice`/`owwModel`/`owwThreshold` 等 OWW 参数。
- 设备侧 OWW 本来就是关闭的：`oww_on_device` 默认 `off`、固件没有中文 OWW 模型
  （`docs/plan/speaker-tuning.md:49,90`；`rui-dot2-bridge/docs/p4-device-wake-feasibility.md`
  明确"运行时与模型故意不放进固件"）。生产日志也是 `RUI KWS stays in charge`。
- **结论**：`wake_word_off` 省的是**设备端**本地唤醒引擎的 CPU/功耗；对 NAS 侧 rui KWS 的
  待机 CPU **没有影响**。当前设备侧 OWW 已 off，开启它反而需要推运行时+中文模型（p4 路线 C），
  与本任务目标相反。**本任务不采用、不实现设备侧**；若将来启用设备侧 OWW，再评估用它做
  设备端降频（届时 NAS 侧 KWS 也可随 device_wake 一起退居二线）。

---

## 9. P2（本任务明确不做）

- living 专项 profile：用 `cpu_s` 分解 + `py-spy` 看 48 kHz/8ch 降采样、AEC、重采样各占多少，
  决定是否做"空闲时跳过 AEC/降采样"（会碰播放期参考信号语义，需单独设计）。
- 自适应噪声底：用滑动噪声估计替代固定阈值，减少手工调参（但要防止把轻声唤醒词当地噪）。
- 门控计数器进 metrics/trace（协议 additive，需跨版本契约）。
- dot2 设备侧 `wake_word_off` 的落地（依赖 OWW 模型决策）。
- probe 模式下的门控（排障工具，保持现状）。

---

## 10. 变更文件

| 文件 | 内容 |
|---|---|
| `src/rui/audio/process.py` | `StandbyKwsGate`（:360）、启动装配（:600）、捕获循环调用（:1373）、`_run_spotter`（:1417）、恢复回灌（:1472）、活动判定（:1491） |
| `src/rui/config/schema.py` | `standby_kws_gate` / `standby_kws_db` / `standby_kws_hold_ms`（:359-373） |
| `src/rui/config/catalog.py` | 三条控制台字段，`effect="restart"`（:703-729） |
| `config.example.yaml` | 三个默认值 + 说明（:98-107） |
| `tests/test_standby_kws_gate.py` | 新增 14 例：状态机、跳过计数、恢复/回灌、活动保持（playback owner/device wake）、probe/禁用、配置三处一致 |
| `tests/test_config_v3.py` | `_VOICE_GOLDEN` 快照补 3 个新字段（schema 新增字段，纯快照同步） |
| `docs/standby-cpu-0.8.7.md` | 本文 |

未 commit；工作树中其他任务的改动（music/joke/turn/web）不属于本任务。
