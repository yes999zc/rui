# 老项目侦察笔记（2026-10-22）

> 两个只读侦察 agent 去 `xiaoai-voice-entry` / `xiaoai-client` 抄回来的参数与实现。
> 用途：D1 音乐、D2 记忆、B1 灯环的迁移依据；A3 的口径纠正。每条都带 文件:行号。

## 1. ASR 峰值归一化（已对齐）

老项目（`server/assistant_v3.py:748-757`）：

```python
peak = float(np.abs(samples).max())
if 0 < peak < 0.25:
    samples = np.clip(samples * (0.8 / peak), -1.0, 1.0)
```

我们的 `providers/sherpa_asr.py:46-48` 逐行等价，多了空数组保护。**触发是峰值 <0.25，目标是 0.8**，不是「所有片段都到 0.8」。现场 33 段里 26 段 peak<0.25，所以这个门槛是常态、不是兜底。

差异：老项目在调用方打印 `[seg] X.XXs rms=… peak=…`，我们在 VAD 关段处补了同一行（`ed77191`）。

## 2. 唤醒后端点（已对齐到 1.0 s）

- 老项目：`POST_WAKE_SILENCE = 1.0`，`wake_grace` 布尔；**首段**用 1.0 s，收到第一段后 `wake_grace=False` 并重建 0.5 s 的 VAD（`assistant_v3.py:238, 2874-2875, 3344-3350`）。
- 起因：15:53:54 用户唤醒后稍停，600 ms 级静音把请求切成 1.52 s 碎片「你好，小。」（归档 HANDOFF:1042-1044）。
- 我们：gate 每轮 disarm，所以只有「唤醒后第一段」这一种情形 → `vad_end_silence_ms` 默认 1000（`ed77191`）。B2 做 600 vs 1000 对照。
- **不照搬**：老项目无 10 s 硬截断（`max_speech_duration=45 s`，真机有 43.28 s 单段）；我们已改 45 s。

## 3. TTS 口径（纠正）

**老项目 0.44–0.60 s 不是首字节，是整句合成完**（`/tmp/house-ear-scratch/xf_lat.py`，3 句各 1 次，13/23/13 字；`docs/tts-model-eval.md:64-73`）。

| 口径 | 老项目 | 我们（10 句，2026-10-22） |
|---|---|---|
| 首包/首字节 | 180 ms | p50 292 / p95 518 ms |
| 整句合成完 | 0.44–0.60 s | p50 346 / p95 612 ms |

两个口径都不能混用。老项目把 `close_timeout` 从默认（1041 ms 死等）改到 0.1 才落进 0.44–0.60；我们已从 0.5 改 0.1（`ed77191`）。

## 4. 记忆（D2 依据）

- 文件：`server/memory.py` + `memory.json`，`{"items":[{id,text,source,status,pinned,created,updated}]}`，`MAX_ITEMS=200`，`DUP_RATIO=0.8`（SequenceMatcher）。
- 抽取：**会话收尾**跑一次（超时/停止/原厂 stop 三条路径，30 s 内合并）；提示词 `MEMORY_PROMPT`（`assistant_v3.py:1405-1411`）：一行一条、最多 3 条、排除一次性指令/常识/当天新闻；输入最近 8 条消息、`text[:2000]`。
- 参数：`deepseek-flash`、temp 0.1、**max_tokens 600**、reasoning low（160 会吃光预算返回空——归档 HANDOFF:459-463）。
- 状态闸：自动抽取进 `candidate`，**人工确认后才注入**；「记住…」走 `remember` 工具直接写 `confirmed`（`tools/recall.py:34-55`）。D2 验收必须搬后者。
- 注入：每轮拼进 system prompt；pinned 在前、新的在前、第一条必进，预算默认 800。
- 控制台：开关、条目列表、从最近对话提取、待确认候选、注入预览（`web/index.html:365-410`）。
- 我们：**完全没有**；只让控制面写（双进程架构，音频面不碰）。

## 5. 音乐（D1 依据）

- 曲库：Navidrome/Subsonic HTTP API（`tools/music.py:145-152, 346, 424-439`）。
- 在线：官方 netease 搜索 + `outer/url` 取流，`_probe_audio` 看 content-type 排 VIP/试听；kuwo CDN 兜底（返回 python dict，要 `ast.literal_eval`）（`online_music.py:26-33, 103-144, 240-296`）。
- 缓存：`/tmp/xiaoai_online/<id>.mp3|flac`（<200 KB 判失败）→ `ffmpeg -f s16le -ac 1 -ar 16000` 成 `.pcm`；45 s 下载超时。
- **我们必须改**：`_play_file` 只读 WAV（`audio/wav.py`），转码目标要落 **WAV 容器**（16k/48k mono），不能写裸 PCM；或给 `PlayFile` 加直送 PCM 的字段。
- 队列：随机 20 首，两首之间补 20；去重「最近 60 首 + 未播」；在线下一首在当前曲播放中预取。
- 暂停/续播：`rewind` 用已写块数回退 `PLAY_LAG_SECONDS=0.55`；我们已经同值（`process.py:_pause_for_speech`）。暂停不算活动。
- 播放必须走我们的音频面 owner（`music` P3），不要自己 `adapter.play`，否则重演两个写者抢喇叭。
- 音量：`mysoftvol` 是唯一真旋钮；语音工具是 0–100 ↔ 0–255 换算，`music_volume 0.68` 只是静态配平（当 `PlayFile.gain`）。
- 原厂守卫（照抄命令）：按键 114 会同时起原厂 Mi Music；起播后 +0.9 s 检查 `mphelper mute_stat`，`1|3` 时发
  `ubus -t 2 call mediaplayer player_play_operation '{"action":"pause","media":"common"}'`。

## 6. 灯环（B1 依据）

- 通道：`services.shell(script)` → 设备 `run_shell` → `/bin/sh -c`；一条动画一个脚本，sleep 在设备端；**绝不逐帧往返、绝不在关键路径 await**（20:25 事故：await 把续播拖后 3 s）。
- 设备路径：18 颗，`echo "<i> 0xBBGGRR" > /sys/devices/i2c-0/0-003a/led_rgb`，`i=0..17`。
- 颜色（峰值 RGB）：off(0,0,0)、listening(0,72,0)、thinking(0,0,96)、speaking(0,48,72)、wake(72,72,72)、error(72,0,0)、music(72,24,96)、music_paused(96,40,0)、muted(60,0,0)。
- 亮度：`gain=1.9`（线上 2.45），夜间 `00:00–07:00` × `night_factor=0.6`（线上 0.75）；`int(v*brightness())` clamp 255。**容器 UTC 陷阱**：夜间窗口必须用 `Asia/Shanghai`。
- 时间常量：FADE_IN 180 / FADE_OUT 320（off 专用）/ STEP 40 / WAKE_STEP 90 / THINK_FRAME 120 / ERROR_PULSE 140。
- `fade_steps` 用 `round(duration/40)`（180→4 帧、每帧 45 ms、总 sleep 135 ms）——别改成 int(x+0.5)，观感会变。
- 动画：breathe 18 帧/cycle、tail 6；wake 遮罩 (8,9)→(6..11)→(3..14) 后 fade 到绿；comet 18 帧/33 ms/tail 5/0x60B0FF。
- 状态语义：`set_state` 幂等、force 可重绘、每段 fade 末帧必须是目标色；`error_pulse`/`refresh` 在旧项目**没有调用者**，要用就显式接线。
- 我们的形状：新增 `device/xiaoai/led.py`（脚本构建器 + LedRing）、`Capability.LED`、`DeviceServices.set_led`；执行放音频面（fire-and-forget），控制面只发状态。配置放 **per-device**（`devices[].led`），不学旧项目的全局 `led:`。

## 7. 明确不照搬

- 10 s 硬截断（已改 45 s）。
- 唤醒词只靠音频跳过（老项目是「回放 + 文本剥 + 残留续听」；我们两者都有，保持）。
- `@@PLAY/@@NEXT` 字符串协议（我们改结构化工具返回）。
- 旧 Sink/Credit 直接搬（我们的 owner + credit 已在音频面，音乐作为 P3 owner 接入）。
- 旧的全局 `led:` 配置（多设备模型下放 `devices[].led`）。
