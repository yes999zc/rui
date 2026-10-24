# 播放链路：从我们的 PCM 到音箱出声

> 2026-10-22 调查 + 当天修复。三个听感故障（首字被吞、句间爆破音、告别卡）的根因与修法
> 都在这里。**0a / 0b / 每轮新起播放器已落地并经 9哥 听感验收（16:10），只剩 0c 未做。**
> 每一节都写清楚「证据在哪」，下一个人不用重跑。

## 1. 链路本身（设备侧是我们自己的 fork）

```
控制面 turn.py ──speak()──▶ 音频面 _speak_loop ──PLAY_PCM 帧──▶ 设备客户端 (Rust)
                                                                   │
                                                  写 mpsc 通道（4 块）→ 写线程
                                                                   │
                                                  aplay stdin 管道 → aplay → ALSA
```

设备上跑的是 `yes999zc/open-xiaoai`（fork 分支 `bakey/device-events`），源码在
`老项目 xiaoai-client/packages/client-rust`。关键文件
`src/services/audio/play.rs`、`src/bin/client.rs`。

设备端已经有的三样东西（我们服务端只用了一样）：

1. `start_play` 会 **kill 旧 aplay 再 spawn 新的**，返回 `Response::success()` 是在
   `Command::spawn()` 之后——**进程起来了，ALSA 设备还没打开**。实测从 `start_play` 到
   `[aplay] chunk 1` 是 **88–93 ms**（0b 的 80 ms 静置之后），此前第一块会撞进初始化。
2. 每块写进 aplay 之后回报事件 `{"event": "consumed", "n": idx}`（`play.rs:182-198`）。
   **实测语义：它是「aplay 的 stdin 管道收下了这一块」，不是「ALSA 已经播完」**——
   报点在 `write_all` 返回之后（`play.rs:170-191`）。所以它适合当**在途上限**（防泄漏），
   不适合当播放头（按它控速会把 64 KB 管道灌满，每次写都等一次排空）。
3. 写入带 **500 ms 超时**，超时丢块并打日志（`[aplay] WRITE TIMEOUT dropped`），
   阻塞 ≥80 ms 时打 `[aplay] slow write {ms} ms ({bytes} bytes)`。
4. **aplay 跨应答不死，但会排空**：两次声音之间超过 ~300 ms（ALSA buffer 深度），
   aplay 打 `underrun!!! (at least N ms long)`。写进一台刚 underrun 的设备，第一块会被
   重启吃掉——这是 2026-10-22 首字被吞的真正根因（见 §3 A）。

设备日志在 `/tmp/client.log`（`client-run.sh` 里 `>> /tmp/client.log 2>&1`）。
它没有时间戳，只能按行序和"chunk N at X ms"的相对时刻读。

## 2. 老项目的做法（`xiaoai-voice-entry`，跑过两天真机）

| 做法 | 出处 |
|---|---|
| `open()` 里 `start_play` 之后 **`await asyncio.sleep(0.08)`**——"let aplay open before the first chunk" | `server/session.py` `Sink.open` |
| **按设备回报控速**：`Credit(window=2)`，永不比最新 `consumed` 超前 2 块（200 ms） | `server/session.py` `Credit` |
| 句间 **350 ms 真静音**（`gap_ms=350`），写在每句**之后**（末句也有），也是唤醒词穿过 AEC 的干净窗口 | `assistant_v3.py` `speak_stream` |
| **不做任何淡入淡出**；整句合成后按 **100 ms** 块写出 | `assistant_v3.py` `speak_stream` |
| **一次应答新开一个 Sink**（`speak_stream` 每次 new Sink + `open()` 一次），句与句之间不重启 aplay；回合与回合之间**不保活**播放器 | `assistant_v3.py:491-501` |
| 播放几何：`buffer_size = sr*0.300`、`period_size = sr*0.060`（60 ms 是 **ALSA period**，不是块长） | `Sink.open` |

**勘误（2026-10-22 侦察源码）**：本文早前写「老项目按 60 ms 块写出」是错的。
老项目 `CHUNK_MS = 100`（`assistant_v3.py:87`、`session.py:17`），git 全历史没有 60 ms 块。
因此「块长 100→60」这条（原 0d）**没有老项目实测背书，已取消**。

## 3. 三个故障与根因

### 故障 A：每轮回答的第一个字被吞 / 卡（已修，9哥 验收通过）

根因**两层**，缺一不可：

1. `start_play` 回执 ≠ ALSA 就绪。回执在 `Command::spawn(aplay)` 后立刻返回，紧接着写第一块
   会撞进 ALSA 初始化（设备日志 `slow write 112 ms (3200 bytes)`）。老项目用 80 ms 静置避开。
   → **0b** `9991773`：回执后再 `sleep(0.08)`。
2. **保活的 aplay 在长静默里 underrun，第一块被重启吃掉**。我们让播放器跨应答保活
   （`8955a7c`），提示音结束到答案首块之间隔 5.5 s，aplay 排空；设备日志在答案首块前正好有
   `underrun!!! (at least 5373.550 ms long)`。那一声「叮」就是重启瞬态，被吃掉的是第一个音节。
   → **`68b9876`**：每轮回答首块前先 `stop_play`，让 `play()` 重新 `start_play` + 80 ms 静置
   （即老项目「一次应答一个 Sink」的形状）。
   验收数字：`reply: first chunk` 与 `player: start_play` 同一毫秒（16:09:37.215 / 48.317 /
   59.663），设备侧 `chunk 1 at 88–93 ms`，答案前无长 underrun。

### 故障 B：句子/分块衔接处有爆破音（已修，9哥 验收通过）

根因：**我们自己引入的 per-chunk 淡入**。`turn.py` 按讯飞每一块调 `speak(chunk)`，
`client.speak()` 每次 `seq` 从 0 起，音频面把 `seq==0` 当句首做 12 ms 淡入 → 每 300–500 ms
一次淡入，且只淡入不淡出 = 电平阶跃；切点还常落在字中间。

→ **0a** `c555e1d`：
- 边界改为**声明式**：`client.speak(..., segment_start=True)` 发 `SpeakStart`，音频面只在那里淡入；
- 句内 chunk 原样拼接（同一次合成的切片，本来就不该有边界）；
- 每句（含预渲染 filler）之后写 **350 ms 真静音**，照抄老项目 `speak_stream`，句前不加。
验收：9哥「第一个字和1一样吞（那是 A），衔接处完美，顺利」。

### 故障 C：告别语卡（已修）

- 连说三遍：`8955a7c` 修（busy 不再重开已关闭会话）。
- 卡：A+B 的叠加，随 A/B 一起消失。验收：9哥「不卡，顺利」。

### 故障 D（已修，记一笔）：播放吃掉了 15 s 空闲计时

`a5d141d` 起用 `metrics.output_owner` 当 busy，播放期间只推窗口不倒数。

## 4. 修复状态

| 步骤 | 内容 | 状态 |
|---|---|---|
| 0a | 句级边界（`SpeakStart`）+ 句后 350 ms 真静音 + 去 per-chunk 淡入 | ✅ `c555e1d` |
| 0b | `start_play` 回执后静置 80 ms | ✅ `9991773` |
| 0b+ | 每轮回答新起播放器（治长静默 underrun） | ✅ `68b9876` |
| 0c | 接 `consumed` 回报、用设备回报做在途上限（`Credit`），墙钟控速 | ✅ `5d875ec`/`fecd410`/`e82a01a`，见下 |
| 0d | 块长 100→60 ms | ❌ 取消：60 ms 是 ALSA period，老项目块长就是 100 ms（见 §2 勘误） |

**0c 实测（同一段 30 s 音乐，2026-10-22 16:2x–16:3x）**：

| 配置 | `slow write` | `queue full` 增长 | `underrun` 增长 | `TIMEOUT` |
|---|---|---|---|---|
| 只按 `consumed` 控速（window=2） | 228 次 / 30 s | 0 | 0 | 0 |
| `consumed` 上限 window=2 + 墙钟 | 130 次 / 30 s | 0 | 0 | 0 |
| `consumed` 上限 window=8 + 墙钟（现默认） | **0 次 / 30 s** | 0 | 0 | 0 |

结论：设备回报只能当**泄漏上限**，不能当播放头。老项目 `Credit(window=2)` 的语义
（它自己测的是服务端 `slow send`）在我们这条链路上会保持管道常满；`window` 做成配置项
（`audio.playback.credit_window`，默认 8，0 = 只按墙钟），设备停止回报时一次 1.5 s 超时
后退回墙钟，且粘性——与老项目一致。

**0c 的照抄清单**（侦察自老项目 `server/session.py:52-138`，含四个审计洞）：

1. `Credit(window=2, timeout=1.5)`：`take()` 在每块 `ws.send` **之前**，`sent - acked >= window` 就等；
2. `ack(n)` 要 **clamp 到 `sent`**——上一轮的迟到回执不能顶穿新窗口；
3. `reset()` **不换 Event**，用世代 token 让停在 `take()` 的旧任务失效；
4. 超时一次置 `dead=True`，退回 socket 背压（否则之后每块都等满 1.5 s，静默慢 15 倍）；
5. `abort()` 立即放行（暂停时客户端 kill aplay，永远不会再回报）。

我们的接线形状：`adapter.py` 不再忽略 `consumed`，转成音频面消息；音频面的 `_speak_loop`
等待授信；设备序号每次 `start_play` 从 1 重新计数，因此每次新开播放器都要 `reset()`。
验收：35 s 连续播放 `slow write` 从现基线（新闻十几条 80–120 ms）**趋近 0**、`TIMEOUT` 0。

**一个未收的小口子**（不影响听感，0c 顺手看）：提示音有时会复用上一轮回答留下的、
已排空的播放器（16:09:41.939 那次 cue 没有前置 `start_play`）。同一个 underrun 机制，
只是 320 ms 的叮短，听不出来。

## 5. 怎么量（下一轮直接用）

```bash
# 服务端：唤醒 / 提示音 / 首块 / 轮次 trace
grep -E "wake: |cue: |reply: first|player: start_play|turn \{" /tmp/rui-scratch/he-serve-*.log | tail -20

# 设备端：写入是否被阻塞、是否掉块（无时间戳，看行序与 chunk N at X ms）
sshpass -p "$SPEAKER_PASSWORD" ssh -o StrictHostKeyChecking=no -o HostKeyAlgorithms=+ssh-rsa \
  "root@$SPEAKER_IP" "grep -nE 'start_play|stop_play|slow write|TIMEOUT|underrun|chunk 1 ' /tmp/client.log | tail -20"

# 音频面指标：谁占着喇叭、缓冲余量、参考电平、ERLE、P2.9 的 gap
curl -s localhost:8091/api/v1/audio | python3 -c \
  "import json,sys;m=json.load(sys.stdin)['metrics'];print({k:m.get(k) for k in
   ['output_owner','playback_lead_ms','playback_gap_count','playback_max_gap_ms',
    'ref_active','aec_erle_db','device_volume']})"
```

基线（2026-10-22 15:2x，1.00× 无追帧、提前量 250 ms）：35 s 连续播放
**slow write 17 次（80–107 ms）、TIMEOUT 0 次**。
2026-10-22 16:10 复测（新闻，约 35 s 播放）：**slow write 十余次（80–120 ms）、TIMEOUT 0 次**。
2026-10-22 16:3x（0c 完成，30 s 音乐，`credit_window: 8`）：**slow write 0 次、
underrun 0、queue full 0、TIMEOUT 0**。

## 6. P2.9：回复预缓冲、gap 观测、有序起播（issue #1）

> 2026-10-24。issue #1 的现象：树外 Echo Dot 2 适配器上，RUI 发出 PLAY_PCM 后第一批
> 到了，约 400 ms 后其余批次才到，中间可听见断层。根因是两件事叠加：
> (a) 首块前的 `stop_playback()` 与控制/数据两条独立 socket 上的首块 PCM 竞争；
> (b) Pacer 的预缓冲（`MAX_LEAD_S=0.32`，部分路径 250 ms）小于 ~400 ms 的到达间隔。
> 适配器侧已有 workaround（有序 flush + pong、0.45 s 预缓冲）；本轮只改 RUI 侧。

### 6.1 `audio.playback.prebuffer_ms`：默认 0，按链路开启

```yaml
audio:
  playback:
    prebuffer_ms: 450   # 默认 0；issue #1 的树外适配器建议 400–500
```

- **范围 0–1000 ms，默认 0。** 默认 0 不是"没实现"，而是保留现有 LX06 行为：
  LX06 的控制面/TTS 把一句一次性推进来，Pacer 250/320 ms 的提前量已经够用，
  起播前再等只会把 `first_response_ms` 推后。默认值由 `tests/test_pacing.py` 的
  既有基线守住：`prebuffer_ms=0` 时 Pacer 仍是 `PREBUFFER_S=0.25`、
  `MAX_LEAD_S=0.32`（`_speak_loop` 用 `max(PREBUFFER_S, prebuffer_s)` 兜底）。
- **什么时候开**：链路实测"首批 PCM → 停顿 → 余下 PCM"的间隔 ≥ 当前提前量时。
  按 issue #1 的 ~400 ms，取 450 ms（间隔 + 50 ms 余量）。代价是最多 +450 ms 的
  首音频延迟；这是用一次可预期的等待换掉句中可听的断层。
- **起播条件**：`_await_reply_prebuffer()` 等到 `_speak_queued_s >= prebuffer_ms`
  才放行首块；上限为 `prebuffer_ms + 250 ms`（`PREBUFFER_TIMEOUT_EXTRA_S`）。
  更短的回复（或生产者不再送数据）超时后照常起播，不会死等；barge 冲刷队列时
  用 `_speak_generation` 丢弃已经在门槛里取出的旧块。
- **稳态**：`prebuffer_ms > 0` 时 Pacer 的 `prebuffer` 与 `max_lead` 同样抬到该值
  （否则起播等到了 450 ms，稳态又被 320 ms 上限排空）；`prebuffer_ms=0` 时不动。

### 6.2 gap / underrun 观测

音频面在"回复队列空、播放器仍在放、下一块到达时设备已经放完"的时刻记一次 gap：

- 判定：`arrival - pacer.due`（设备本应放完的时刻），不是"队列空的时刻"。
  小于 `PLAYBACK_GAP_MIN_MS = 20 ms` 视为调度抖动，不计数。
- `playback_gap_count`：进程启动以来的累计次数；
  `playback_max_gap_ms`：累计最坏值。两者进 `Metrics`（控制面每 2 s 或
  `QueryMetrics` 时收到），控制面可再写入轮次 trace 的 `meta`：
  `TurnTrace.note_playback_metrics(metrics)` 会复制
  `playback_gap_count` / `playback_max_gap_ms` 到 `meta`。
- 每次回复结束只在有 gap 时打一行 `reply: N gaps, max X ms`；不逐块打日志。
- 已知取舍：句间 TTS 合成停顿如果超过设备缓冲，也会被记为 gap——那是真实
  underrun，不是误报；要区分"故意留的句间静音"，应把静音作为 PCM 发出来。

### 6.3 有序起播：本轮做的最小改动

`_speak_loop` 首块前原本无条件 `stop_playback()`，用途是让 `play()` 重新
`start_play`，修复长静默后 aplay 排空导致的首字被吞（§3 A）。本轮加入
`_device_audio_pending`：

- `play()` / cue / 文件播放成功后置 True；任何显式 `stop_playback()` 成功后置 False。
- 首块前若为 False（设备播放器已经停了，自上次 stop 后没再送过音频），跳过
  `stop_playback()`；否则照旧发送。LX06 上 `play()` 自己会发 `start_play`，行为不变。
- 效果：barge / StopAudio 之后的每轮回复不再向控制通道插入一个多余的 stop，
  也就少一次与首块 PCM 竞争的机会。**这不是完整修复**：真正"必须 stop"的场景
  （上一条回复/歌曲还在设备里）仍然存在跨 socket 竞争，需要 P3 的屏障契约。

### 6.4 P3：起播/flush 屏障契约（设计，本轮不实现）

适用对象：控制面与数据面是两条 socket 的适配器（Echo Dot 2 等）。LX06 的同一条
websocket 已有天然顺序，继续走旧路径，不需要改。

**目标**：RUI 永远不把新回复的 PCM 发到"还没 flush 完的旧播放器"上；flush 与首块
之间不存在可观察的竞争窗口。

**能力协商**：`Ready` 增加 `ordered_start: bool`（或协议版本）。为 False 时 RUI
沿用现有 stop/start 路径；为 True 时走下面的契约。这样不会破坏 LX06。

**契约**（所有消息都在**数据通道**上，或由适配器定义成单一有序流）：

1. `flush(epoch: uint64)`：适配器停止当前 aplay/播放器，丢弃所有尚未播放的 PCM，
   并以 `flushed(epoch)` 回应。`epoch` 由 RUI 单调递增，每次起播一个新值。
2. RUI 收到 `flushed(epoch)` 后才发送 `start(epoch, sample_rate, channels)`；
   适配器在 `start` 回执后才可以接收该 epoch 的 PCM。
3. PCM 帧携带 `epoch`；适配器丢弃 epoch 不等于当前 epoch 的帧（迟到的旧数据
   永远不可能灌进新播放器）。这与 P2.9 的 `_speak_generation` 是同一语义，
   只是下沉到适配器。
4. **超时**：RUI 等 `flushed` 最多 300 ms（可配置，≤ 起播预算）。超时则放弃该
   epoch、记录 `start_barrier_timeout`，不发首块 PCM；宁可本轮静音，也不发
   "可能被旧播放器接受"的音频。
5. **幂等/空闲优化**：适配器已知播放器空闲且无排队 PCM 时，`flush` 可直接回
   `flushed`（仍须回执，保持顺序不变）。RUI 侧 `_device_audio_pending == False`
   时同理不发 flush，直接 `start`。
6. **barge**：barge 走同一屏障：`flush(new_epoch)`，收到回执后本轮回复的
   `start` 才能发出；中途到达的旧 epoch PCM 由适配器丢弃。
7. **可观测**：`Ready.ordered_start`、`barrier_timeout_count`、
   `pcm_stale_dropped`（适配器计数）进日志/指标；验收时要求
   "首块 PCM 之前一定先看到同一 epoch 的 flushed"。

**验收**（P3）：
- 用 issue #1 的 400 ms 双批源回放：`playback_gap_count` 不增长。
- 设备日志中 `stop_play`/`flush` 与 `chunk 1` 严格有序，无 "chunk 1 早于 flush"。
- barge `silence_after_ms` 不明显回退（屏障只加一次回执，目标 ≤ 300 ms 预算内）。
- LX06（`ordered_start: false`）行为不变，既有 `test_turn_audio` / `test_playback`
  基线全绿。
