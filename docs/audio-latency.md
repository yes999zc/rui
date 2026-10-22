# 回合延迟与播报卡顿（2026-10-22 实测）

真机（LX06）+ 假音箱两条链路都测过。每个数字都来自当天日志，复现命令在文末。
结论先行：**模型不慢，慢在我们自己的三处**——流式读被写成「先读完再处理」、
TTS 与解析共用一条协程、播报零预缓冲。

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

## 2. 三个根因

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

### 2.2 播报卡顿：零预缓冲 + 精确 1.0× 实时（未修）

`audio/process.py:_speak_loop`：

```python
now = time.monotonic()
due = max(due, now) + seconds          # 每块只按自身时长睡
await asyncio.sleep(max(0.0, due - time.monotonic()))
```

每块送到设备后，等到「这一块应播完」的时刻再送下一块 —— 设备缓冲永远是空的。
任何调度抖动（发送耗时 1–3 ms、GC、Linux/容器调度）都会让 aplay 欠载，听感就是卡顿。

**修法**（两句代码量级）：

```python
prebuffer = 0.3                        # 句首预缓冲 300 ms
due = max(due, now + prebuffer) + seconds * 1.02   # 1.02× 追帧，缓冲只增不减
```

老项目的等价物：连续 aplay 管道（由管道背压）+ 句间插 350 ms 真静音 +
「只计真正写进 aplay 的块」。它从不让缓冲见底，所以不卡。

### 2.3 filler 实时合成，2.2–2.7 s 才出声（未修）

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

## 3. 复现命令

```bash
set -a; . ~/.hermes/.env; set +a
# 20 轮端到端（假音箱，真 KWS/VAD/ASR/LLM/TTS）
uv run python scripts/measure_turn_pipeline.py \
    --config ~/.hermes/cache/scratch/house-ear-s5b/tools.yaml \
    --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav --rounds 20
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

## 4. 一条教训（写给下一个会话）

我今天先假定「模型慢」，跑了两个对照实验（带/不带 tools、首句切得更早）去排除它，
全部无差别——因为瓶颈根本不在那里。**正确的第一个动作是「量每一段的边界」**：
`llm_first` / `first sentence queued` / `tts_first` 三个打点，一眼就能看出是读流、
是合成、还是播放。没有打点之前的所有推理都是猜。
