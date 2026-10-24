# 回合延迟与播报卡顿（2026-10-22 实测）

真机（LX06）+ 假音箱两条链路都测过。每个数字都来自当天日志，复现命令在文末。
结论先行：**模型不慢，慢在我们自己的三处**——流式读被写成「先读完再处理」、TTS 与解析共用一条协程、
播报零预缓冲。

> **下午更正（见 §3）**：第三处不是「预缓冲不足」。补了预缓冲仍然卡，真因是回复队列按块数
> `maxsize=200` 截断整句（当天 118 条 `speak queue full`）。另外两处新 bug（ASR 每轮重载
> 1.1 GB、filler 现调讯飞）也是下午才量出来的。

## 1. 时延分解（同一句「今天天气怎么样」，真机，11:30 与 12:0x 两次）

```
asr                 486 ms    本地 sherpa SenseVoice int8 threads=2
工具轮（LLM 决定调工具） 755 ms    首 token
weather 工具            0.9 s     配置里写死经纬度后（原来是 3.4 s，每次重新 geocode）
答句轮首 token          952 ms
首句入队                511 ms    从答句轮开始算（改造前要等整段流读完）
TTS 首字节              861 ms    讯飞，首句 ~13 字
----
用户说完 → 第一声（filler）    1.4 s
用户说完 → 真答案首句          2.6–2.9 s
首响（从唤醒算）           p50 5617 ms（改造前 11553 ms）
```

> 口径提醒：`用户说完 → 第一声（filler） 1.4 s` 这一行与 `filler_after_s = 1.8 s` 对不上
> （当时没记是哪个起点），预渲染后口径也变了：现在是 1.8 s 策略等待 + 推流 lead，
> 不再叠 TTS 首字。见 §3.3。

## 2. 上午认定的三个根因（第三条下午被推翻）

### 2.1 流式读被写成「先读完再处理」（已修，`1b8b0e3`）

```python
# 改造前
chunks = [first]
async for chunk in stream:
    chunks.append(chunk)      # 整段流读干
for chunk in chunks:          # 才开始切句、合成
    ...
```

模型侧裸测：首 token 748 ms、29 字 912 ms（**31.8 字/秒**）。但线上这一轮 `llm round done`
是 4695 ms / 48 字（10 字/秒）—— 差的 4–5 倍全在「等整段读完再开始合成」。

改造后：每个 chunk 立即 ingest，切出的句子进 `asyncio.Queue`，TTS 合成 + 播放跑在独立
task 上，合成 0.2–0.8 s 不再阻塞读流。日志新增 `first sentence queued at N ms`，
下次回归一眼能看见，不用再猜是不是模型慢。

### 2.2 播报节奏：零预缓冲 + 精确 1.0× 实时（已修，`bf568c5`；**但不是卡顿主因**）

`audio/process.py:_speak_loop`：

```python
now = time.monotonic()
due = max(due, now) + seconds          # 每块只按自身时长睡
await asyncio.sleep(max(0.0, due - time.monotonic()))
```

每块送到设备后，等到「这一块应播完」的时刻再送下一块 —— 设备缓冲永远是空的。
任何调度抖动（发送耗时 1–3 ms、GC、Linux/容器调度）都会让 aplay 欠载，听感就是卡顿。

**修法**（上午的提案，两句代码量级；实际落地是 200 ms，见下）：

```python
prebuffer = 0.3                        # 提案值 300 ms；落地 PREBUFFER_S = 0.20
due = max(due, now + prebuffer) + seconds * 1.02   # 1.02× 追帧，缓冲只增不减
```

老项目的等价物：连续 aplay 管道（由管道背压）+ 句间插 350 ms 真静音 +
「只计真正写进 aplay 的块」。它从不让缓冲见底，所以不卡。

**实际落地（`bf568c5`，`Pacer`）**：目标 lead `PREBUFFER_S = 0.20 s`、上限 `MAX_LEAD_S = 0.35 s`、
每块追 2%（`CATCHUP = 1.02`），新增指标 `playback_lead_ms` 进 Metrics / 控制台。
但这只是把「送得太紧」修好；补上之后仍然卡，因为一半的块在进队列时就被丢了 —— 见 §3.1。

### 2.3 filler 实时合成，2.2–2.7 s 才出声（已修，`f814d6f`：启动预渲染）

```
filler_after_s = 1.8 s 等待
+ 讯飞首字节 0.44–0.9 s
= 实际 2.2–2.7 s 才听到「我查一下」
```

9哥 的体感正是这里：问句和「我查一下」之间空档大，而「我查一下」到播报没问题。

两个修法（可同时）：
- filler 走 ack 同款预渲染缓存（`audio/ack_render.py` 已经有这套机制，加一个文本键即可），
  唤醒后第一次用到就是本地文件，出声 ≈ 1.8 s + 0.1 s；
- `filler_after_s` 1.8 → 1.2 s（老项目 1.8 是建立在它 TTS 首字节 0.44 s 之上）。

## 3. 2026-10-22 下午：三个真 bug

上午的结论有一半是错的。`Pacer` 补上预缓冲后 9哥 仍说卡。真机日志（12:54–13:22，5 次起停）
给出三个彼此独立的原因，没有一个是「模型慢」。原始日志：
`/tmp/house-ear-scratch/he-serve-4399{,b,c,d,e}.log`（只读，时间戳是唯一口径）。

### 3.1 播报卡顿：不是缓冲不足，是回复队列被截断（`f814d6f`）

12:56 与 13:00 两轮里，音频面反复打同一行：

```
WARNING __main__: speak queue full, dropping a chunk
```

全天 **118 条**：12:56:51–53 共 **47 条**，13:00:25–27 共 **71 条** —— 一轮几十条，不是偶发。

原因：`_speak_queue = asyncio.Queue(maxsize=200)`，按**帧数**限长。控制面把整句 TTS 好的 PCM
切成 100 ms 帧、一次性灌进来（`client.speak(chunk_ms=100)`，合成远快于实时）；**200 格只有 20 s
音频**，长回答从第 201 帧起直接丢。丢的位置在句中任意处，所以听感是「一句话里突然少一截」，
补预缓冲救不了。

修法：队列改**无界**，另加 `MAX_SPEAK_QUEUE_S = 120 s` 的**秒数**预算，只防生产者不报
`SpeakFinished` 的泄漏（超了打 `speak queue over 120 s`）。「多少块」没有正确答案，
「多少秒音频」有。

结果：从 13:04 那次运行起（c/d/e 三次运行、6 个回合）`speak queue full` **0 条**；
`frames_dropped` 计数 **0**。丢块这个源头消失；人耳复听没有单独记录。

### 3.2 ASR 每轮重载 1.1 GB 模型（`f814d6f`）

`transcribe()` 每轮调一次 `asr_chain()`，新建 provider → 本地 SenseVoice 首次使用要加载
1.1 GB int8 模型。日志里每一轮都有一条 `local asr ready`：

| 唤醒 → `local asr ready` | asr 阶段 |
|---|---|
| 13:02:47.837 → 13:02:53.178 = **5.341 s** | 528.6 ms |
| 13:03:13.538 → 13:03:18.588 = **5.050 s** | 540.7 ms |
| 13:03:42.273 → 13:03:46.979 = **4.706 s** | 518.2 ms |
| 13:06:17.871 → 13:06:22.991 = **5.120 s** | 499.6 ms |

即每轮唤醒后先有 **4.7–5.3 s** 在装模型，然后 ASR 本身还要 **499.6–540.7 ms**。

修法：provider 链（ASR/LLM/TTS）在 `TurnRunner.__init__` 建一次（`_asr = asr_chain(...)`），
`warm()` 预热**同一条链**（不是另建一条），`app.py` 启动时 `await runner.warm()`。

结果：启动时一条 `local asr ready`，之后不再出现。d/e 两次运行（4 轮）asr 阶段实测
**72.6 / 79.0 / 90.6 / 51.3 ms**。

### 3.3 filler 现调讯飞（`f814d6f`）

上午的 filler 是「1.8 s 等待 + 现合成」：等待结束才连讯飞，首字再等 0.44–0.9 s，
所以「我查一下」实际 2.2–2.7 s 才出声。

修法：启动时 `render_filler_pcm()` 合成一次，**0.73 s 音频**留在内存，需要时直接推 PCM。
4 次运行都在启动后 0.4–0.6 s 打出：

```
INFO house_ear.audio.ack_render: filler: rendered '我查一下。' with xfyun-tts (0.73 s)
```

触发时日志是 `filler after 1.8 s (cached)`，紧接着 1–8 ms 内 `speak finished, 4 chunks still queued`
—— 讯飞那 0.44–0.9 s 归零，只剩 1.8 s 策略等待 + 推流 lead。**没做声学计时**，
人耳第几秒听到仍未测。

### 3.4 顺带落地的两处：6 麦加权混音、打断

**6 麦前端（新文件 `audio/frontend.py`，`f814d6f`）**

以前是「每路 AEC 后按残差 argmax 硬选一路」。这等于扔掉唯一的阵增益，而且选的是**消除得最差**
的那一路（残差大 = 没消干净）。现改为：

- 每通道按「高于自身噪声底多少」加权（`FLOOR_RATIO = 1.5`；噪声底快降 0.90 / 慢升 0.9995），
  权重归一后求和，电平不塌；
- **放音时冻结权重**（`ref_active`），否则权重会追着回声跑、在词中间跳变；
- **每通道高通始终推进**，不再只推进被选中的一路（旧实现切通道时另外 5 个滤波器带旧状态，
  起来第一块就是爆音）；
- ERLE 改在**混音后**的流上统计（与老项目同口径，将来数字可比）。

老项目在这台硬件上量过：6 麦两两相关 0.93–0.97、通道间延迟 < 1 样本，**简单平均比最好单路好
1.6 dB**。我们的阵增益仍未单测；唤醒率今天下午测了（0.20 门限 4/5，0.15 门限 4/4，见 §3.6）。

**打断（`4c4a69b`）**

播报中 KWS 命中 → 音频面 `_flush_speech()` 清队列 + `_stop_playback()` 发 `stop_play`，
`Wake` 事件带 `barge=true` / `barged_s`；控制面收到后 `runner.abort()`，正在合成的答案在句间/块间停。
13:22:28 实测：

```
barge: 1 wake during playback, dropped 7.6 s queued        # 音频面
barge-in: 7.7 s of queued speech dropped                    # 控制面
```

差 0.1 s 是同一批音频的两种口径（音频面按已入队秒数，控制面按整句累计）。已交给设备的
**剩余 lead 约 420 ms** 必须播完 —— 老项目自己量的是 `PLAY_LAG_SECONDS = 0.55`，量级合理，
但「停推 ≤300 ms」还没按这个口径验收。

另外 `89c5dae` 修了一个边界：提示音（cue）期间第二次唤醒曾被当成打断，把提示音掐了。
现在只有「回复/文件在播」才算 `barged`，cue 不算。

### 3.5 14:02：AEC 改成「一个滤波器消混音」（`e10bdeb`）

此前是 6 个滤波器各消一路、再加权混音。老项目是**先混音、再消**，它的 25.2 dB 就是在混音上量的
（滤波器看到的是 6 麦能给出的最好 SNR）。今天照这个拓扑改回来，配置
`audio.aec.cancel: mix|per_channel`，默认 `mix`；`per_channel` 只留给「逐通道残差」诊断。

同一段嘻唰唰副歌循环（设备音量 mysoftvol 170 / 220）：

| 拓扑 | 170（参考 -25.0 dBFS） | 220（参考 -12.3 dBFS） | CPU rtf |
|---|---|---|---|
| 1 个滤波器消混音（现） | **23.5 dB** | **22.8 dB** | 0.011 |
| 6 个滤波器各消一路（旧） | 21.2 dB | 16.3 dB | 0.066 |
| 老项目（同口径，170） | 25.2 dB | — | — |

两个结论：

- **旧方案音量越高塌得越多**（170→220 掉 4.9 dB），这就是「220 要很近」的直接原因；
  混音后只掉 0.7 dB。
- 170 档仍比老项目低 1.7 dB，原因未查，先记差异，不当结论。

按 ERLE 定义推算回声残差电平：170 档 -48.5 dBFS（-25.0 − 23.5），220 档 -35.1 dBFS
（-12.3 − 22.8），220 档比 170 档高 13.4 dB——这是 220 档唤醒要靠近的量化解释（见归档 HANDOFF §6.1）。

### 3.6 还没验的（别当已完成）

唤醒已测：0.20 门限近场 4/5（漏的那次探针也没中），一次 -40.3 dBFS 探针中、生产不中；
门限降到 0.15 后 4/4；放歌时 170/220 各中 1 次、都当场暂停音乐。剩下：

- **220 档远处唤醒**：0.15 门限只在「开窗、平时音量、近处」4/4；220 档只验到放歌时近距离 1 次。
- **设备侧缓冲**：`aplay`/ALSA 深度没量过，420 ms 里有多少在设备侧不清楚。
- **`playback_lead_ms`**：只进 Metrics/控制台，日志不打印，今天没逐轮抄表。

## 4. 复现命令

```bash
set -a; . $HOUSE_EAR_ENV_FILE; set +a
# 20 轮端到端（假音箱，真 KWS/VAD/ASR/LLM/TTS）
uv run python scripts/measure_turn_pipeline.py \
    --config /tmp/house-ear-scratch/house-ear-s5b/tools.yaml \
    --command /tmp/house-ear-scratch/house-ear-s4/commands/01-weather.wav --rounds 20
# 只看模型侧（不经过我们的循环）
uv run python - <<'PY'
import asyncio, httpx, json, os, time
async def main():
    t0=time.monotonic(); n=0; first=None
    async with httpx.AsyncClient(timeout=60) as c:
        async with c.stream("POST","https://api.deepseek.com/chat/completions",
            headers={"Authorization":f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
            json={"model":"deepseek-flash","stream":True,
                  "messages":[{"role":"user","content":"沈阳今天天气怎么样"}],
                  "thinking":{"type":"disabled"},"max_tokens":3000}) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data: ") or line[6:]=="[DONE]": continue
                d=json.loads(line[6:])["choices"][0]["delta"].get("content")
                if d:
                    first = first or time.monotonic()-t0; n+=len(d)
    print(f"first={first*1000:.0f}ms total={(time.monotonic()-t0)*1000:.0f}ms chars={n}")
asyncio.run(main())
PY
```

## 5. 一条教训（写给下一个会话）

我今天先假定「模型慢」，跑了两个对照实验（带/不带 tools、首句切得更早）去排除它，
全部无差别——因为瓶颈根本不在那里。**正确的第一个动作是「量每一段的边界」**：
`llm_first` / `first sentence queued` / `tts_first` 三个打点，一眼就能看出是读流、
是合成、还是播放。没有打点之前的所有推理都是猜。
