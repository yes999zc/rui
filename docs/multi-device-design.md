# 多设备 / 卫星音箱设计（rui 0.4.x → 0.5 设计提案）

> 状态：**设计已冻结（2026-10-25）**。§9 的 6 条关键决策由 9 哥 拍板（§9.1），其余 9 条按 §9.2 默认执行；
> 外部契约输入见 §11（dot2 桥 issue #11 的 Q1–Q7 已逐条答复）。Phase 1（config v3，仍单 active）已开工。
> 基于 0.4.1 的代码与 Socket/Plug v1 契约；本文件不含代码改动。
> 目标读者：9 哥（产品决策）、Lead（排期）、后续实现者。
> 阅读顺序：§0 是三个问题的直接答案；§1 是当前代码事实；§4/§5/§6 是设计本体；§8 是分阶段计划；§9 是**已冻结的决策记录**；§11 是外部契约输入与答复。

## 0. 三个问题的直接答案

### Q1：rui 现在是“卫星/主音箱”方案，还是“所有音箱统一管理”？

**两者都不是。** 现状是：

- 设备配置是一张**平表**（devices[]），每台设备有独立的 room、adapter、host、listen_port、token_env、volume、LED、wake_words；**没有** primary / satellite / 房间分组 / 主从角色字段。
- 运行时只允许**一个音频面进程驱动一台设备**：supervisor 取第一个可驱动的 enabled adapter，然后以 --device <id> 拉起唯一的音频面（src/rui/audio/client.py:387-415，src/rui/audio/process.py:2950-2953）。再多 enabled 的 inbound 设备只会给一条 multiple_enabled_inbound 警告（src/rui/web/api/app.py:1162-1188），其余设备“配置在、但不被驱动”。
- 因此也**没有**“所有音箱一起”的群组输出/群组播放：/health 只有一个 speaker_connected / speaker_device（app.py:2261+），播放 owner 在音频面进程内是单实例（process.py:522-526, 2458+）。

一句话：有“多设备配置”，没有“多设备运行”，更没有主/从角色。

### Q2：每个房间都有音箱时，需要一台“主音箱”吗？

**不需要“主音箱”作为必须的硬件角色。** 推荐的是**中枢（rui 控制面）统一仲裁、所有房间平权**的 hub-and-spoke：

- 中枢在 NAS 上的 rui 里，负责回合、会话、播放所有权、工具/音乐/提醒；每台音箱只是它的一条输入/输出通道。
- 房间是**策略属性**（room、优先级、最近活跃），不是层级。任何一台音箱都可以被说话人唤醒、都可以被选为回答出口。
- 可选一个 **default / fallback output**（例如客厅），但它的职责只有一个：当回合没有来源房间时（定时提醒、无来源通知、音乐续播）决定“声音从哪台出”。它不是主音箱，不参与唤醒归属，也不妨碍其他房间发起回合。
- 不推荐“主音箱负责所有回答、卫星只负责收音”：多一跳转发、延迟和故障面更大，且房间平权的体验更自然。

### Q3：两个相邻房间同时听到唤醒词，谁拿这一轮？

**由 rui 中枢仲裁，同一时刻只开一个回合。** 推荐的选择顺序（详见 §4）：

1. 若已有回合打开：**不抢**（只处理 barge/停止/重复抑制），第二个唤醒只记数；
2. 若某房间正在播放：**正在播放的房间**里的 barge=true 设备唤醒优先用于打断；非播放房间的唤醒不打断别的房间，只能作为“空闲候选”；
3. 空闲时，同一个重复窗口（默认 2 s）内的候选按：**房间优先级配置 → 唤醒证据质量（device_wake 是否被 probe 验证、score/threshold 归一化、KWS vs 设备唤醒）→ score 领先幅度 → 最近活跃房间 → 稳定 id 顺序** 选一个；
4. 赢家开 gate/回合；输家计 wake_lost_same_word 丢弃，**不产生第二个 turn**；
5. 赢家之后没有语音（wake_idle）就结束，**默认不自动换下一个候选**（避免“明明没人说话却突然换房间回答”）。

**没有“主音箱赢”这条规则**——主音箱概念不存在；排序里房间优先级是可配置的策略，不是硬件层级。

## 1. 当前现实（0.4.1 代码事实）

### 1.1 设备配置：平表 + 全局音频参数

| 层级 | 字段 | 位置 |
|---|---|---|
| 每设备 | id、adapter、host、room、enabled、wake_words、volume、listen_port、token_env、allow_no_token、led、params | src/rui/config/schema.py:170-201 |
| 全局 | audio.capture / voice / playback / aec | schema.py:204-323, 365-369 |
| 插件 manifest 提供 | runtime.listen_port / token_env、capabilities、wake、device_side 等默认值 | src/rui/device/registry.py:150-178；docs/socket-plug-v1.md §3/§4 |

要点：

- room 目前只用于展示/日志/trace 标注（设备列表、TurnTrace 由控制面补 device_id/room），**不参与路由或仲裁**。
- listen_port / token_env 有“manifest runtime 默认值”语义：config 不写时取 manifest（0.4.1 起 status/list/detail/connect/setup 都显示生效值，见 src/rui/web/api/device_rules.py:54-95）。
- **没有** devices[].aec / devices[].capture / devices[].playback / devices[].eq。AEC 只有全局 audio.aec（schema.py:316-323），EQ 不在核心（插头 params/adapter 自己做）。dot2 的 AEC 参数模型（/tmp/rui-dot2-bridge/docs/aec-parameter-model.md）明确把“每设备 AEC”列为对 rui 的 P0 需求。

### 1.2 运行模型：一个音频面 = 一台设备

- AudioPlaneSupervisor._pick_device() 只取第一个 adapter：src/rui/audio/client.py:387-393；start() 用 --device 拉起唯一子进程（client.py:395-418）。
- python -m rui.audio.process --device 是必填参数（process.py:2950-2953）；AudioPlane(cfg, device_id, socket_path) 一个实例只打开一个 adapter（process.py:370-371, 591+）。
- build_adapters(cfg) 会为**所有 enabled 设备**构建 adapter 对象（device/registry.py:182+），但音频面只取其中一个；其余 adapter 只是控制面用来 probe/展示，**没有在跑**。
- 一个控制面 → 一个 AudioPlaneSupervisor → 一个 Unix socket（默认 ~/.local/state/rui/audio.sock，process.py:2914-2918）。

### 1.3 控制面/API 明确承认“只有一个 active”

- _active_device_id()：问 supervisor 实际驱动哪台，回退到第一个 enabled（app.py:1140-1159）。
- _device_warnings()：多于一台 enabled inbound 时返回 multiple_enabled_inbound，max_active_inbound=1，列出 ignored devices（app.py:1162-1188）。
- 设备列表每行带 active 布尔（app.py:4123-4134）；/health 只有单组 audio_plane/speaker_connected/speaker_device/speaker_last_seen（app.py:2261+）；_plane_snapshot() 也只读一个 supervisor（app.py:1039-1058）。

### 1.4 唤醒与仲裁今天只发生在“单平面内部”

- 设备唤醒策略在音频面进程里：_configure_device_wake()（process.py:988）、_drain_device_wakes()（:1247）、_handle_device_wake()（:1385）。
- 已有单设备策略：500 ms 去抖（:121）、5 次/10 s 限频（:129）、厂商 wake 同 tick 优先、barge 的 voice.barge_in/能力/播放状态门（:1359-1383）、VAD 硬门。
- DeviceWake 只有六字段（ts_ms/score/threshold/barge/level/source），**没有 device_id/room/playing**；v1 契约规定由 RUI 从配置补 device_id/room（docs/socket-plug-v1.md §4.2）。
- 控制面收到的 DeviceWakeMsg 只用于观测（process.py:1470-1483）；turn 的打开/打断是在**平面内**决定的。这决定了多设备时不能简单“多个平面各自决策”。

### 1.5 播放 owner 也在平面内

- 每个平面自己维护 _owner 与 P0/P1/P2/P3 优先级（process.py:522-526, 2458+）。单平面时它等价于全局 owner。
- 若直接起 N 个平面，**每个平面都会有自己的 owner**，两个房间可以同时出声——这与 docs/architecture.md §3 的“one owner”目标冲突。多设备必须在控制面增加**全局输出所有权**（§6.1）。

### 1.6 真实设备约束

| 设备 | 传输 | 唤醒 | 约束 |
|---|---|---|---|
| LX06 | inbound ws，4412，query token | rui KWS（持续上行 8ch/48k） | 6 mic + 2 ref；全局 audio.capture 按它量；AEC 在 rui |
| Dot2 | inbound wss，8870，X-EM-Token | 目前 rui KWS（设备 OWW 模型未就位）；就位后 device_wake | 单声道/无 ref；AEC 在设备；**设备只拨 8870，同一时刻只能有一个进程监听 8870**；真机验证只能“停旧 → 起新 → 验 → 回滚”的受控切换 |

dot2 侧的相关诉求与记录：/tmp/rui-dot2-bridge/docs/rui-0.3-requirements.md §2（每设备端口/协议、唤醒仲裁由 rui 定）、docs/aec-parameter-model.md（每设备 AEC + devices[].aec 是 P0）、docs/cold-start-0.4.2.md / docs/watch-log.md（8870 单监听、受控切换、0.4.2 冷启动窗口）。它们没有提出“主音箱”概念，诉求正是多设备/仲裁。

## 2. 设计目标与不变量

沿用 docs/architecture.md 已有不变量，并补三条多设备专属：

1. **只有音频面碰麦克风/扬声器**（不变）；控制面不直接写音频。
2. **只有中枢决定谁说、谁先开口**（不变，但需从“单平面内”上移为“跨设备仲裁”）。
3. **设备事件是建议**：device_wake 只是候选，RUI 拥有 turn/barge 最终裁决（v1 已冻结的规则，延续）。
4. 新：**房间平权、中枢唯一**。设备没有主从；主/备只用于“无来源输出”的 fallback。
5. 新：**一个家庭同一时刻一个回合、一个输出 owner**（第一版）。多房间同时说话是后续产品，不是默认。
6. 新：**故障隔离**。一个房间/音频面崩了，其他房间仍可正常唤醒与回答；控制面不能因为一台设备掉线而整体不可用。
7. 新：**隐私显式**。LX06 这类 KWS 在 rui 的音箱会持续上行；device_wake 的开放设备只在唤醒后上行。多房间部署时，文档必须把这个差异说清楚，控制台也要能看出每台设备的唤醒来源。

## 3. 方案对比与推荐

### 方案 A（推荐）：中枢仲裁 + 每设备一个音频面

~~~text
                 ┌───────────── rui 控制面（NAS）─────────────┐
                 │  Coordinator：回合 / 会话 / 工具 / 音乐      │
                 │  WakeArbiter（跨设备候选选择）              │
                 │  OutputOwner（全局播放所有权 + 路由）        │
                 └───┬───────────────┬───────────────┬────────┘
                     │ IPC           │ IPC           │ IPC
             ┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼────────┐
             │ 音频面 lx06  │ │ 音频面 dot2  │ │ 音频面 ...   │
             │ :4412 ws     │ │ :8870 wss    │ │              │
             │ KWS/AEC/VAD  │ │ KWS/AEC/VAD  │ │              │
             └───────┬──────┘ └──────┬───────┘ └──────────────┘
                     │               │
                 客厅 LX06       书房 Dot2
~~~

- 每个 enabled inbound 设备一个子进程，各自持有一个 inbound listener、自己的 adapter、自己的 AEC/捕获布局、自己的 gate/VAD/KWS。
- 控制面新增 **WakeArbiter**：所有平面把“候选唤醒”上报，中枢选一个，给赢家下发“开回合”，输家抑制。回合开始后的 turn/session/输出仍在控制面。
- 控制面新增 **OutputOwner**：语音/提示/提醒/音乐统一在中枢决定出哪台设备；平面内的 _owner 保留为“最后一道本地安全闸”，但不再是跨房间的裁决者。
- 优点：故障隔离（一台崩不影响其他）、每设备 AEC/采集布局独立（LX06 有 ref、Dot2 无 ref）、每设备独立端口/证书/token、符合现有 supervisor 进程模型、可逐台启用/回滚。
- 代价：N 份 KWS/VAD/AEC 模型内存与 CPU；控制面多路 IPC；需要新增候选/准入协议。

### 方案 B：单音频面多 adapter

- 一个进程内跑 N 个 adapter + N 条帧循环，共享一个事件循环/时钟；仲裁天然在同一进程内。
- 优点：无跨进程候选协议，延迟最低，单点观测。
- 代价：一个 adapter/模型异常可能拖垮整个进程（当前 fail-soft 只在“单设备”语义下设计）；不同设备的 capture/AEC/端口/证书要在同一进程里隔离；测试与重启粒度变粗；模型内存仍 N 份。
- 结论：**不作为第一版**；如果方案 A 的 IPC/进程开销被证明是瓶颈，再评估。

### 方案 C：主音箱层级（卫星把回合交给主音箱）

- 每台卫星只负责收音，把音频/文本转发给主音箱；主音箱统一回答再下发。
- 问题：多一跳延迟与故障点；主音箱成为单点；房间平权的体验反而退化；与 rui “设备即 socket” 的模型冲突。
- 结论：**否决**。只保留“可选 fallback output”这个更弱的概念（§0 Q2）。

### 方案 D：每个房间独立助手（完全隔离）

- 每台设备独立会话、独立播放、互不共享上下文/工具状态。
- 问题：同一家庭的多房间记忆/提醒/音乐不连续；用户预期是“一个助手”。可作为未来“隐私隔离模式”的研究项，不做默认。

### 推荐

**方案 A 第一版**：中枢仲裁 + 每设备一个音频面；房间平权；可选 fallback output；全局 one-turn/one-output。先在 LX06 + Dot2 两台上验证，再推广到 3–4 台。

## 4. 仲裁策略（WakeArbiter）

### 4.1 候选事件（WakeCandidate）

控制面为每个候选补齐（adapter 不填，延续 v1 的“RUI enriches”原则）：

| 字段 | 来源 | 用途 |
|---|---|---|
| device_id / room | config | 定位、trace、日志 |
| source | device_wake 或 rui KWS | 证据类型 |
| score / threshold | DeviceWake；KWS 无分数时用“命中即 1.0/阈值不可比”并标记 | 质量比较（只在同类内可比） |
| ts_ms | DeviceWake；KWS 用单调时钟 | 去重窗口 |
| barge | DeviceWake | 播放中打断建议 |
| playing | 该设备所在平面的 _speak_queued_s/播放任务 | 是否播放中 |
| aec | 该设备生效配置（device / controller / none，是否有 ref） | 回声归因与降权 |
| priority | config multi_device.room_priority / devices 顺序 | 平局与可预期性 |
| last_active | 最近一次该房间回合/按键/音量操作 | tie-break |

### 4.2 选择顺序（默认策略）

1. **已有回合打开**：不抢。收到的候选只记 wake_lost_turn_open；除非是同一房间的 barge/停止意图（由该平面按现有规则处理）。
2. **某设备播放中**：只有该设备（正在出声的房间）的 barge=true + 现有 barge 门全过，才允许打断；其他房间的候选不能停它，按“空闲候选”处理。
3. **空闲**：在重复窗口（默认 2000 ms，可配）内收集候选，按下列顺序：
   1. room_priority（显式配置优先；未配置则跳过）；
   2. 证据质量：被 probe 验证的 device_wake > KWS；同类内按归一化 score 与 threshold 的余量比较（不同来源的 score 不可直接比，必须按来源分组）；
   3. score 余量更大者（同组）；
   4. last_active 更近者（默认窗口 120 s，可配）；
   5. 稳定顺序（config devices 下标 / id 字典序）。
4. 赢家：控制面下发“admit”；该平面 arm gate、等 VAD 出句，后续 Utterance 进入正常 turn。
5. 输家：控制面下发“reject/suppress”，平面丢弃本地候选，计 wake_lost_same_word；**不产生第二个 turn**。
6. **全局重复抑制**：同一唤醒词、同一个 2 s 窗口内只允许一个 admit；每设备自己的 500 ms 去抖与 5/10 s 限频继续保留（作为防刷第二道）。
7. **赢家无语音**：wake_idle 结束，不自动晋升第二名（避免“换房间突然说话”）；是否允许一次重试是开放项（§9）。

### 4.3 回声归因与“自听”

- 一个房间的 TTS/音乐只会从该房间的音箱出来；**只有该房间自己的回声风险**，跨房间只可能通过空气传播（开门/大音量），不属于常态。
- 因此规则是**局部的**：正在播放的房间，任何候选必须通过既有 barge 门（voice.barge_in、能力、_paused_explicit、cue/echo hold、vendor 静默窗口）；未播放的房间不受别家播放影响，它的唤醒就是用户在该房间说话。
- 设备侧 AEC（Dot2）与 rui AEC（LX06）必须能按设备选择：Dot2 用 devices[].aec.who=device + followup_delay_ms=2500/followup_replay=false（0.4.1 已有全局版本，v3 下沉到每设备）；LX06 保留 controller AEC，不能因为引入多设备就把现网 25.2 dB 的 AEC 路径切走（dot2 aec-parameter-model.md §8.3 的警告）。
- 残余风险：如果两个房间物理相邻、音量很大，B 房间的 KWS 可能听到 A 房间的 TTS；当前无法用声学定位区分。缓解顺序：房间优先级/最近活跃、把该候选的 score 与 threshold 余量纳入比较、把“B 正在播放”作为 barge 门、必要时由 9 哥为房间设静态优先级（§9）。

### 4.4 重复抑制与播放所有权

- **重复抑制**：全局 2 s 窗口（multi_device.duplicate_window_ms）；每设备 500 ms 去抖保留；同一词在窗口内只 admit 一个；计数器 wake_candidates/wake_admitted/wake_lost_same_word/wake_lost_playing/wake_barge/wake_duplicate 按设备+全局统计。
- **播放所有权**：
  - 语音回答 → 起源房间（唤醒/打断的那台设备）。
  - 定时提醒/无来源通知 → multi_device.default_output（fallback output）；若未配置则用最近活跃房间（开放项）。
  - 音乐 → 第一版只允许一个房间播放（全局 owner）；在 A 房间放歌时 B 房间的回合不应自动停 A 的歌（除非用户说“全场停止”，见开放项）。
  - 停止/打断：只作用于当前输出 owner 所在设备；“停止”在非 owner 房间只结束该房间的会话，不跨房间停播（第一版）。
  - **不做**“所有房间同时播同一段回答”（多房间回声/延迟问题，非当前产品需求）。

### 4.5 同房间交叉回声（2026-10-25 冻结；dot2 #11 提出）

同一个 room 里有两台设备时，A 播放、B 的麦克风会听到 A（设备侧 AEC 只消自己的回声），B 可能误唤醒抢回合。规则（冻结）：

- A 正在播放时，**同 room 的 B 候选默认抑制**，计 `arbitration_cross_echo_suppressed`；trace 写明"谁在播放、谁被抑制"。
- 例外：B 候选 `barge=true` 且该设备自身的 barge 门（能力、`voice.barge_in`、设备侧门控）全过，才按"播放中 barge"参与仲裁。
- `multi_device.enabled=true` 时 `devices[].room` **必填**（同 room 判定依赖它）；缺 room 的设备不参与该规则并给明确 warning。
- 跨房间（不同 room）只按 §4.3 的空气传播残余风险处理，**不**额外做同房间式抑制。
- 这条不需要 adapter 上报 `playing`/`room`：播放由 RUI 驱动，room 来自配置（延续 v1 "RUI enriches"）。

## 5. 配置 v3（提案）

### 5.1 目标

- 全局 audio.* 变成**默认值**；每设备可以覆盖 aec、capture、playback、voice 中与其硬件相关的最小集合（第一阶段至少 aec + followup）。
- 新增 multi_device 顶层块：开关、仲裁策略、fallback output、上限。
- 保持 v2 语义：不开多设备时，行为与今天完全一致（单 active 平面 + multiple_enabled_inbound 警告）。

### 5.2 示例

~~~yaml
version: 3

# 全局默认值：旧配置原样保留，作为每设备未覆盖时的继承来源
audio:
  capture: {rate: 48000, channels: 8, mic_channels: 6, ref_channels: 2}
  voice: {followup_delay_ms: 1000, followup_replay: true}
  playback: {rate: 16000, channels: 1, prebuffer_ms: 0}
  aec: {enabled: true, filter_len: 4096, cancel: mix}

devices:
  - id: lx06
    adapter: xiaoai
    room: 客厅
    host: lx06.local
    listen_port: 4412
    token_env: RUI_DEVICE_TOKEN
    volume: 60
    audio:                      # 可选覆盖（缺省继承全局）
      aec: {enabled: true, who: controller}

  - id: dot2
    adapter: dot2
    room: 书房
    host: dot2.local
    listen_port: 8870
    token_env: DOT2_EM_TOKEN
    volume: 70
    audio:
      aec: {enabled: false, who: device}
      voice: {followup_delay_ms: 2500, followup_replay: false}

multi_device:
  enabled: true                 # 默认 false：保持 0.4.x 单平面行为
  max_planes: 4                 # 资源上限，超过则只启用前 N 台并警告
  default_output: lx06          # 无来源输出（提醒/通知）的 fallback；可选
  arbitration:
    mode: score                 # score | room_priority | last_active（首版只用 score + 静态 priority）
    source_priority: [device_wake, kws]   # 组间优先级（§9.1 K3）：组内比 score，不跨来源比
    candidate_merge_window_ms: 400        # 同一 utterance 的多设备命中合并窗口（§11 Q3）
    duplicate_window_ms: 2000             # 同词全局去重窗口（与合并窗口分离）
    room_priority: [lx06, dot2] # 显式优先级；缺省按 devices 顺序
    prefer_playing_room: true   # 播放中房间的 barge 优先
    last_active_window_s: 120
    on_no_speech: end           # end | retry_next（默认 end）
~~~

### 5.3 字段与校验

| 字段 | 类型 | 默认 | 规则 |
|---|---|---|---|
| version | int | 3 | 只有启用多设备/覆盖字段时才升；v2 文件可无损加载 |
| devices[].audio | mapping | 空 | 可覆盖 aec/voice/capture/playback 的子集；未知键报错 |
| audio.aec.who | device｜controller｜both｜none | controller（LX06 现状） | v3 新；device 时 rui 不跑 AEC，依赖设备；none 只允许 ASR 无 barge |
| devices[].audio.voice.followup_* | 同全局 | 继承 | Dot2 用 2500/false；LX06 保持 1000/true |
| multi_device.enabled | bool | false | true 时才允许多个 enabled inbound 平面 |
| multi_device.max_planes | int | 4 | 超过上限的 enabled 设备不启动并给明确 warning（不是静默） |
| multi_device.default_output | device id | 空 | 必须是 enabled、inbound、playback 能力的设备；空 = 用最近活跃房间 |
| arbitration.duplicate_window_ms | int | 2000 | 50–10000；同词全局去重，与合并窗口分离 |
| arbitration.candidate_merge_window_ms | int | 400 | 100–2000；同一 utterance 跨设备命中的合并窗口（§11 Q3） |
| arbitration.source_priority | list[str] | `[device_wake, kws]` | 只允许 `device_wake`/`kws`；组间优先级，组内仍比 score 余量 |
| arbitration.room_priority | list[device id] | 空 | 必须全部存在；未列出的设备按 devices 顺序排后 |
| arbitration.on_no_speech | end｜retry_next | end | 首版只实现 end，retry_next 留开放项 |

校验/约束：

- 两台 enabled inbound 设备不得使用同一 listen_port（现有 port_conflict 扩展到“多平面”语义；错误信息给出两个生效端口）。
- default_output / room_priority 只能引用存在的设备；禁用设备不参与仲裁。
- 每个设备的 aec.who=device 必须显式关闭 rui AEC（audio.aec.enabled=false 或设备覆盖），否则双 AEC 级联；配置校验给 warning。
- 每设备 token_env / listen_port 继续由 manifest runtime 默认值兜底（0.4.1 已实现生效值显示）。

### 5.4 迁移与回滚

- v2 → v3：只新增可选字段；multi_device.enabled 默认 false → **行为与 0.4.x 完全一致**（单 active 平面、warning、单 owner）。
- 启用多设备后才改变运行形态；回滚只需把 multi_device.enabled 改回 false（保留第一台设备为 active，其余停止），不需要改 config version。
- 旧二进制（0.4.x）读 v3 会因 version > CONFIG_VERSION 拒绝启动；因此**升级与回滚都要保留 config 备份**（与现有 0.4.0/0.4.1 升级规范一致）。
- 兼容承诺：不改 devices[] 现有字段语义；不改 /api/v1/* 现有路径；新增字段/新增 per-device 输出，不删除任何旧字段。

## 6. 运行时形态（方案 A 细化为可实现）

### 6.1 每设备一个音频面（推荐）

- **Supervisor**：从单实例变成 dict[device_id, AudioPlaneSupervisor]；每台 enabled + inbound + 在 max_planes 内的设备各起一个子进程，socket 命名 audio-<device_id>.sock（沿用 resolve_socket_path 的短路径逻辑，process.py:2914-2932）。
- **端口**：每台设备的平面绑定自己的 effective_listen_port（LX06 4412 / Dot2 8870），互不冲突；不再需要协议 demux，因为 adapter/port 已一一对应。
- **IPC**：现有平面协议 + 三个新消息（名字待实现定）：WakeCandidate（平面→控制面）、WakeAdmit/WakeReject（控制面→平面）。平面在收到 admit 前**不 arm gate**、不启动 VAD 回合；这样跨房间不会重复起 turn。
- **平面内保留**：500 ms 去抖、限频、DeviceWake 校验、barge 门、followup gate、本地播放安全锁——它们负责“这台设备自己的输入是否可信”，不负责“跨房间谁赢”。
- **控制面新增**：
  - WakeArbiter：§4 的顺序表，输入 WakeCandidate，输出 admit/reject；所有计数器进 trace。
  - OutputOwner（跨设备）：收到 speak/play 请求时决定目标 device，向对应平面发 Speak/PlayFile；平面内 _owner 继续作为本地安全闸。第一版“语音回答只去起源房间”；提醒/音乐用 default_output。
  - 每个 device 一个 AudioPlaneClient；控制面的 /api/v1/audio、/health、设备状态按 device 聚合。
- **健康/指标**：/health 保留顶层聚合字段（向后兼容），新增 devices: [{id, room, audio_plane, speaker_connected, last_seen, restarts, error, wake_count, ...}]；控制台设备列表/详情/状态按设备展示；删掉 max_active_inbound=1 的 warning，改为“某设备平面崩溃/未启动”的明确 warning。
- **故障隔离**：一个平面崩溃只影响该房间；其 supervisor 独立重启；输出 owner 在该设备 crash 时释放 claim 并按 default_output 回退（或静默结束，按 §9 决策）。
- **配置重载**：改动某台设备的端口/AEC/params 只重启该设备的平面；全局音频默认值变化 → 重启所有平面。

### 6.2 备选：单平面多 adapter（何时考虑）

- 如果实测多进程开销（内存/CPU/IPC 延迟）不可接受，再把 N 条帧循环收进一个平面；那时需要先把“平面内 fail-soft”升级为“每条 adapter 独立协程 + 独立错误域”，否则一个设备会拖垮全屋。
- 在这之前不推荐，因为当前进程模型、watchdog、测试与回滚都是按单设备设计的（docs/architecture.md §2）。

### 6.3 资源与端口规划（NAS）

- 每平面重复加载 KWS/VAD/AEC 模型（LX06 侧数 MB 级 ONNX；Dot2 侧 KWS 若也启用）；4 台以内预计可控，但需要在 NAS 上实测 RSS/CPU（§7 验收）。
- 上限先设 4 台（max_planes 可配），超过上限明确告警而不是排队。
- 端口：4412/8870 由各自平面绑定；未来设备默认从 8880 起分配；配置保存时做冲突检查。
- 隐私：LX06 持续上行（KWS 在 rui）；Dot2 的 device_wake 就位后只在唤醒后上行。控制台应显示每台设备的 wake.source 与“是否持续上行”，让用户知道自己开了什么。

### 6.4 失败/回滚/安全

- 单平面崩溃：该房间掉线，其他房间不受影响；/health 明确标出。
- 控制面崩溃：全部停（与今天一致），supervisor 随进程重启恢复。
- 安全：每设备 token 独立（已有）；WSS 证书/dot2 内层 X-EM-Token 属设备侧，不进 rui 配置；manifest 信任不变（内置/可信 in-process）。
- 回滚：multi_device.enabled=false + 保留 config 备份 + 镜像回上一个 tag；单设备模式不应受 v3 字段影响。

## 7. 测试与验收

### 7.1 单元/集成

- **Arbiter 表驱动**：空闲多候选、播放中 barge、播放中非 barge、turn 已开、跨房间重复、房间优先级、last-active、无候选、候选质量不可比（device_wake vs KWS）、on_no_speech=end/retry（后者可先跳过）。
- **IPC 候选/准入**：赢家收 admit 才 arm gate；输家收 reject 后不产生 Utterance；超时/平面崩溃时控制面不悬挂。
- **Supervisor N 平面**：每台独立启动/停止/重启；端口冲突拒绝；一台崩溃其他不受影响；socket 命名与清理。
- **配置**：v2 文件加载为 v3 默认行为；非法 default_output/room_priority/重复端口报 422；迁移/回滚 round-trip。
- **API/健康**：/health 顶层兼容 + devices 数组；设备列表 active/last-seen/restarts；/api/v1/audio 按设备。
- **契约/插头**：既有 rui plug-test 不回归；fake adapter 注入两个房间的 DeviceWake；LX06（KWS）与 Dot2（device_wake）混合候选。

### 7.2 真机验收（LX06 + Dot2）

| 项 | 判据 |
|---|---|
| 共存 | 两台都 online：4412 与 8870 各由对应平面监听；/health devices=2、每台 speaker_connected=true；无 multiple_enabled 警告 |
| 各自唤醒 | 在客厅说 → 客厅答；在书房说 → 书房答；另一房间不被误触发 |
| 同时听到 | 同一唤醒词两房间同时命中 → **只产生一个 turn**；日志/trace 有 candidate/lost 计数；回答只从赢家出 |
| 播放中打断 | 正在播放的房间 barge=true → 停播并起 turn；另一房间说话不打断它 |
| 提醒/通知 | 有 default_output 时从该房间出；未配置时的行为符合最终决策 |
| 故障隔离 | 杀掉 Dot2 平面 → 只有书房掉线；LX06 仍可唤醒回答；supervisor 自动重启并恢复 |
| 资源 | 记录 2 台/4 台的 RSS、CPU、唤醒→首字延迟；不超过 NAS 预算 |
| 回滚 | 改 multi_device.enabled=false + 重启 → 回到 0.4.x 单设备语义；设备仍可单独工作 |
| Dot2 受控切换 | 在 0.4.2 冷启动窗口按“停 0.3.4 → 起新 → 验 → 回滚”执行；不另起并行实例（8870 单监听） |

### 7.3 需要记录的指标

每房间：candidate 数、admitted、lost_same_word、lost_playing、barge、duplicate、wake→turn 延迟、ASR 首字延迟、播放 gap；全局：candidates/s、admit 率、平面 restarts、RSS/CPU。

## 8. 分阶段计划

### Phase 0（现在，0.4.2 窗口）：设计冻结 + 基线

- 只写本设计文档；不改运行时代码。
- 保持 0.4.1 的修复稳定：effective listen_port/token_env、followup 2500/false（需要时）、wake re-probe；LX06 默认 1000/true 不动。
- 记录单设备基线（唤醒率、latency、CPU/RSS），作为多设备的对照。
- 产出：本文件 + 9 哥对 §9 的决策。

### Phase 1（0.5.0-alpha）：config v3 + 每设备覆盖（仍单 active）

- **状态（2026-10-25）：解析/继承/校验/迁移已落地**（`src/rui/config/schema.py`、`store.py`、
  `tests/test_config_v3.py`）：`devices[].audio`（aec/voice.followup/capture/playback.prebuffer_ms）、
  `multi_device` 块、`effective_audio()` 合并与再校验、引用/房间校验、`config_warnings()`；
  v2 文件保持 v2，只在用到 v3 字段时写 `version: 3`。控制台"未启用多设备"状态展示属 Phase 1b。
- 仍只跑一台 active；行为不变（`multi_device.enabled=true` 合法但只记录 warning，多平面在 Phase 2）。
- 判据：v2→v3 round-trip（`test_v2_config_becomes_v3_once_a_device_override_is_used`）；旧配置行为零差异；
  per-device AEC 解析有测试；LX06 不回归。

### Phase 2（0.5.0-beta）：多平面 + 中央仲裁（目标：LX06 + Dot2）

- Supervisor N 平面、每设备 socket/端口；WakeArbiter + WakeCandidate 协议；OutputOwner 第一版（回答去起源房间，提醒/音乐去 default_output）。
- 全局重复抑制；每设备健康/指标；控制台 devices 数组；去掉 max_active=1 警告。
- 判据：§7.2 中“共存/各自唤醒/同时听到/播放中打断/故障隔离”通过；两台 fake adapter 的自动化测试全绿。
- Dot2 真机：在 0.4.2 冷启动窗口做一次受控切换验证（8870 单监听，需 9 哥同意）；此时若两台各占 4412/8870，可首次同时在线。

### Phase 3（0.5.x）：房间策略与输出体验

- room_priority / last_active 策略上线；console 房间视图；每设备 EQ/音量/AEC 展示与编辑；提醒/音乐路由策略定稿；全局 stop 语义。
- 判据：多房间真实使用一周的误唤醒/抢占统计；用户不需要“主音箱”。

### Phase 4（0.6+）：可选增强

- sidecar/第三方插件隔离；全屋播报（announcement，非对话）；voice identity（若未来做）；跨房间音乐跟随（人走到哪音乐到哪）。

### 明确不在第一版

- 不做“所有房间同时说同一段回答”（回声与延迟问题）。
- 不做“主音箱转发卫星音频”（方案 C）。
- 不靠声学定位/距离推断房间；房间来自配置与 user 行为，不猜。
- 不在没有 device_wake 的设备上假装“设备侧唤醒”；KWS 持续上行的隐私差异必须可见。

## 9. 决策记录（2026-10-25 冻结）

> 9 哥 已拍板：6 条关键决策按 §9.1 冻结，其余 9 条按 §9.2 默认执行。冻结含义：Phase 1/2 按此实现，
> 后续修改必须在 §9.3 追加带日期的变更记录，不静默改。dot2 桥在 #11 的输入已并入（§4.5、§11）。

### 9.1 关键决策（已冻结，K1–K6）

| # | 决策 | 冻结结论 |
|---|---|---|
| K1 | 主音箱 | **不设**必须的主音箱，房间平权。只保留可选 `multi_device.default_output` 作为“无来源输出”的 fallback（定时提醒、无来源通知、音乐续播）；fallback 不参与唤醒归属。 |
| K2 | 回合模型 | 全屋同一时刻**一个 turn、一个输出 owner**（第一版）。第二个唤醒只记候选，不产生第二个 turn。 |
| K3 | 同时听到谁赢 | 显式 `room_priority` → 来源组间 `source_priority`（默认 device_wake > KWS）→ 组内 score 与 threshold 余量 → last_active（120 s）→ devices 顺序。**score 不跨来源比较**。 |
| K4 | 提醒/通知 | 第一版固定走 `multi_device.default_output`；未配置时回落最近活跃房间并记 warning。不做全屋播报。 |
| K5 | 每设备 AEC/EQ | `devices[].audio.aec {enabled, who: device\|controller\|both\|none}` + `devices[].audio.voice.followup_*` 覆盖；**EQ/limiter/trim 留在插头 `params`，不进核心**。`who=device` 时 RUI 不跑 AEC，配置校验给 warning。 |
| K6 | 平面模型 | **每设备一个音频面**（方案 A）：一个控制面 + N 个音频面子进程。不是“每设备一个 rui 实例”——后者只是过渡部署手法，两个控制面无法全局仲裁，违反 K2，不作为契约。 |

### 9.2 按文档默认执行（D1–D9）

| # | 决策 | 执行结论 |
|---|---|---|
| D1 | 赢家无后续语音 | `on_no_speech: end`，不自动晋升第二名（避免“换房间突然回答”） |
| D2 | 音乐 | 第一版单房间全局 owner；A 放歌时 B 的回合**不**自动暂停 A |
| D3 | “停止”作用域 | 只停当前输出 owner 房间；不提供“全屋停止”口令（第一版） |
| D4 | 资源上限 | `max_planes: 4`，超限明确告警、不排队；每平面独立重启 |
| D5 | 隐私 | 允许 KWS 持续上行设备全屋部署；控制台设备列表/详情显示 `wake.source` 与“持续采集”标记 |
| D6 | Dot2 device_wake 前置 | OWW 运行时/模型未就位前 Dot2 走 KWS（持续上行），控制台按 D5 标注 |
| D7 | 房间模型 | 不引入顶层 `rooms`；用 `devices[].room` + `arbitration.room_priority`；`multi_device.enabled=true` 时 `room` 必填 |
| D8 | voice identity | 不做声纹；只按房间仲裁（多用户同时说话无法区分，接受） |
| D9 | 回滚 | Phase 2 起生产默认 `multi_device.enabled=false`；LX06+Dot2 验收通过后才开 |

### 9.3 变更记录

- **2026-10-25**：冻结 K1–K6 + 接受 D1–D9（9 哥 确认，Lead 记录）。同时并入 dot2 #11 的三点补充：同房间交叉回声抑制（§4.5）、来源分层/不跨来源比 score（K3）、可解释 trace 字段（§11 Q6）。

## 10. 参考（代码/文档路径）

- src/rui/device/registry.py：adapter 注册/构建、manifest runtime 默认值。
- src/rui/audio/client.py：单平面 supervisor、_pick_device()、--device 子进程。
- src/rui/audio/process.py：AudioPlane、单设备 open、device_wake 策略/去抖/限频/barge、播放 owner、socket 路径、main --device。
- src/rui/web/api/app.py：_active_device_id、_device_warnings、/api/v1/devices、/health、_plane_snapshot。
- src/rui/config/schema.py：DeviceConfig / AudioConfig / CaptureConfig / VoiceConfig / AecConfig。
- src/rui/device/contract.py：Capability、DeviceInfo、DeviceWake、effective_capabilities。
- docs/socket-plug-v1.md：§1 非目标（多设备不在 v1）、§4.2 六字段、§5 device_wake 策略、§7 迁移。
- docs/architecture.md：不变量、进程模型、播放仲裁、§6 债务（全局 audio 配置、单设备假设）。
- dot2 桥：/tmp/rui-dot2-bridge/docs/rui-0.3-requirements.md §2、docs/aec-parameter-model.md、docs/cold-start-0.4.2.md、docs/watch-log.md、docs/handoff-2026-10-25-p4.md。
- 相关 issue：#7（多设备/端口/仲裁问答）、#3（device_wake）。

## 11. 外部契约输入与答复（dot2 桥 issue #11，2026-10-25）

dot2 桥在 [#11](https://github.com/yes999zc/rui/issues/11) 提出多设备仲裁契约提案（进程模型、来源分层、同房间交叉回声、可解释仲裁）。逐条答复如下；若 #11 与本文档冲突，**以 §9 冻结结论为准**。

| # | 问题 | RUI 答复 |
|---|---|---|
| Q1 | 多实例 vs 单实例多 plane，0.5 先做哪个？ | **单实例多 plane**（K6 / 方案 A）。多实例（每设备一个 rui 实例）只是过渡部署手法，不做契约：两个控制面无法全局仲裁，违反 K2。 |
| Q2 | 来源分层优先级硬编码还是可配置？ | 可配置 `arbitration.source_priority`，默认 `[device_wake, kws]`；只决定组间先后，组内仍比 score 与 threshold 余量（K3）。 |
| Q3 | 同一 utterance 合并窗口 | 新增 `arbitration.candidate_merge_window_ms`，默认 **400 ms**（100–2000），与 `duplicate_window_ms`（同词去重 2000 ms）**分开**两个窗口。 |
| Q4 | 同房间交叉回声抑制是否 RUI 做？room 是否必填？ | RUI 做（§4.5）；`multi_device.enabled=true` 时 `room` 必填（D7）。 |
| Q5 | barge 是否只允许“正在播放的设备”？ | 是：只有当前输出 owner 设备可接管 barge；其他设备同 utterance 的 barge 丢弃并计数（§4.2/§4.5）。 |
| Q6 | trace/metrics 字段命名 | 采纳 #11 E 表：`arbitration_candidates`、`arbitration_winner` / `arbitration_reason`、`arbitration_dropped{device}`、`arbitration_ties`、`arbitration_cross_echo_suppressed`、每候选 `wake_source`（Phase 2 实现）。 |
| Q7 | 单实例多设备的进程/资源上限 | `max_planes: 4`（D4），超限告警不排队；NAS 上实测 RSS/CPU 后再调（§7.3）。 |

其它确认：

- **4412 不再有“默认设备口”语义**：端口一律来自 `devices[].listen_port` / manifest `runtime.listen_port`（0.4.1 的 `effective_listen_port` 已修诊断侧）；LX06 4412、Dot2 8870 并存。
- **adapter 不上报 `device_id`/`room`/`playing`/`last_active`**：延续 v1 “RUI enriches”，只上报六字段候选；room 来自配置，播放状态来自 RUI。
- **不做距离/声学推断**：房间来自配置与行为，不猜。
- **多实例部署（临时）**：允许用户用两个独立 rui 实例分别驱动两台设备，但这不是“多设备支持”，不进入验收矩阵；Phase 2 单实例多 plane 交付后应迁回单实例。
- 设备侧 OWW 未就位前（D6），Dot2 的 `wake.source` 保持 KWS，双方都按 0.4.x 现状实现，不预留特例。

