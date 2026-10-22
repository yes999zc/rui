# 播放链路：从我们的 PCM 到音箱出声

> 2026-10-22 晚的调查记录。三个听感故障（首字被吞、句间爆破音/不顺、告别卡）全部定位到
> 这条链路上，**根因已定，修复未做**——9哥 要的是先调查再动手。
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
`~/AI_Workspace/01_Code/projects/xiaoai-client/packages/client-rust`。关键文件
`src/services/audio/play.rs`、`src/bin/client.rs`。

设备端已经有的三样东西（我们服务端只用了一样）：

1. `start_play` 会 **kill 旧 aplay 再 spawn 新的**，返回 `Response::success()` 是在
   `Command::spawn()` 之后——**进程起来了，ALSA 设备还没打开**。
2. 每块写进 aplay 之后回报事件 `{"event": "consumed", "n": idx}`（`play.rs:182-198`）。
   **我们的 adapter 明确忽略这个事件**（`device/xiaoai/adapter.py`：
   `elif event.get("event") not in {"consumed"}: log.debug(...)`）。
3. 写入带 **500 ms 超时**，超时丢块并打日志（`[aplay] WRITE TIMEOUT dropped`），
   阻塞 ≥80 ms 时打 `[aplay] slow write {ms} ms ({bytes} bytes)`。

设备日志在 `/tmp/client.log`（`client-run.sh` 里 `>> /tmp/client.log 2>&1`）。
它没有时间戳，只能按行序和"chunk N at X ms"的相对时刻读。

## 2. 老项目的做法（`xiaoai-voice-entry`，跑过两天真机）

| 做法 | 出处 |
|---|---|
| `open()` 里 `start_play` 之后 **`await asyncio.sleep(0.08)`**——"let aplay open before the first chunk" | `server/session.py` `Sink.open` |
| **按设备回报控速**：`Credit(window=2)`，永不比最新 `consumed` 超前 2 块（200 ms） | `server/session.py` `Credit` |
| 句间 **350 ms 真静音**（`gap_ms=350`），也是唤醒词穿过 AEC 的干净窗口 | `assistant_v3.py` `speak_stream` |
| **不做任何淡入淡出**；一块话整体合成后按 60 ms 块写出 | `assistant_v3.py` `speak_stream` / `session.py` `Sink.write` |
| 一次应答只 `open()` 一次，句与句之间不重启 aplay | 同上 |
| 播放几何：`buffer_size = sr*0.300`、`period_size = sr*0.060` | `Sink.open` |

## 3. 我们的三个故障与根因

### 故障 A：每轮回答的第一个字被吞 / 卡

- 现象：`现在几点`、新闻，都是开头第一个字不清；9哥 连报三轮。
- 根因：`start_play` 回执 ≠ aplay 就绪。回执在 `Command::spawn` 后立刻返回，紧接着我们
  就写第一块，此时 aplay 还没打开 ALSA → 写入阻塞（设备日志 `slow write 112 ms`），
  阻塞超 500 ms 就是整块丢弃（`WRITE TIMEOUT`）。老项目用 80 ms 静置避开这个窗口。
- 已做但不充分：`8955a7c` 把 start_play 改成等回执（`call()`），去掉了"发完就算"。
  少了 80 ms 静置，症状仍在。

### 故障 B：句子/分块衔接处有爆破音，整段"不顺畅"

- 现象：新闻 216 字，两句话衔接处有爆破音；整体发毛。
- 根因：**今天引入的 per-chunk 淡入**。`turn.py` 是按讯飞每一块调用 `speak(chunk)`：

  ```python
  async for chunk in stream:      # 讯飞一块约 300–500 ms
      await speak(chunk)
  ```

  `client.speak()` 每次调用 `seq` 都从 0 开始，而 `_speak_loop` 把 `seq == 0` 当作
  "新句起点" 做 12 ms 淡入 → **每 300–500 ms 就给波形做一次淡入**。只淡入不淡出，
  上一块结尾停在非零值、下一块从 0 起 = 电平阶跃 = 爆破音；且切点常落在字中间。
- 老项目根本不淡入，靠"整块话 + 句间 350 ms 静音"自然接缝。

### 故障 C：告别语卡，还连说了三遍

- 连说三遍：`check_idle` 的 busy 分支无条件 `idle.touch()`，而告别语本身在播放
  （`output_owner` 非空）→ 把已关闭的会话又打开 → 15 s 后又告别。
  日志：15:13:30 / 15:13:49 / 15:14:06 各一次。已在 `8955a7c` 改成"只延长已开启的会话"。
- 卡：告别语是短句，冷管道起播，撞的是故障 A 同一个窗口；再叠上故障 B 的分块淡入。

### 故障 D（已修，记一笔）：播放吃掉了 15 s 空闲计时

`check_idle` 只判 `_in_turn`，不管喇叭。念 14 s 新闻时窗口照走，告别语会落在新闻尾巴上；
放歌更糟。`a5d141d` 起用 `metrics.output_owner` 当 busy，播放期间只推窗口不倒数。

## 4. 修复方案（按依赖顺序，**未实施，等 9哥 点头**）

1. **接 `consumed` 回报，用设备播放头控速**（`Credit window=2`），删掉墙钟 `Pacer`。
   - 改动面：`audio/protocol.py`（Metrics 增加 `consumed`）、`adapter.py`（不再忽略该事件，
     转成音频面消息）、`audio/process.py`（`_speak_loop` 等待授信）。
   - 收益：从根上消除"推得比 aplay 快 → 客户端队列满 → 写入阻塞 → 掉块"。
   - 估时：1–2 h 含验证。
2. **`start_play` 之后静置 80 ms** 再送第一块（照抄老项目），治首字。
3. **边界按"句"不按"块"**：`speak()` 增加 `segment_start` 标志（或控制面在每次 `say()`
   前发一条 `SpeakStart`），只有真正的句首才淡入；句间插 150–350 ms 真静音；
   句尾 10 ms 淡出与句首 10 ms 淡入成对出现，块内部一律不动。
4. **块长对齐 aplay 周期 60 ms**（现在是 100 ms），减少半周期写入。

修复顺序建议 3 → 2 → 1 → 4：3 和 2 是几行的事、立刻能听出差别；1 是结构改造，压轴。

## 5. 怎么量（下一轮直接用）

```bash
# 服务端：唤醒 / 提示音 / 首块 / 轮次 trace
grep -E "wake: |cue: |reply: first|start_play|turn \{" ~/.hermes/cache/scratch/he-serve-*.log | tail -20

# 设备端：写入是否被阻塞、是否掉块（无时间戳，看行序与 chunk N at X ms）
sshpass -p 'open-xiaoai' ssh -o StrictHostKeyChecking=no -o HostKeyAlgorithms=+ssh-rsa \
  root@<LAN-IP> "grep -nE 'start_play|stop_play|slow write|TIMEOUT|chunk 1 ' /tmp/client.log | tail -20"

# 音频面指标：谁占着喇叭、缓冲余量、参考电平、ERLE
curl -s localhost:8091/api/v1/audio | python3 -c \
  "import json,sys;m=json.load(sys.stdin)['metrics'];print({k:m.get(k) for k in
   ['output_owner','playback_lead_ms','ref_active','aec_erle_db','device_volume']})"
```

基线数字（2026-10-22 15:2x，1.00× 无追帧、提前量 250 ms）：35 s 连续播放
**slow write 17 次（80–107 ms）、TIMEOUT 0 次**。修完第 1 条后 slow write 应趋近 0。
