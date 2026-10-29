# Audio pipeline

The part that decides whether the product feels good. Everything else is plumbing.

## Three rates, three jobs

| path | rate | layout | why |
|---|---|---|---|
| capture | 48000 | 8 ch, 32 bit | what the LX06 actually sends |
| voice | 16000 | 1 ch per filter | AEC / KWS / VAD; 4096 taps = 256 ms of tail |
| playback | 48000 | 2 ch, 16 bit | what the LX06 amplifies |

Measured, not assumed: `cat /proc/asound/card*/pcm*/sub0/hw_params` on the device
`S32_LE` 8 ch 48000 Hz while capturing, `S16_LE` 2 ch 48000 Hz while playing.
Music never squeezes through the 16 kHz path — downsample only the copy that goes
to the voice pipeline.

## The frame

`voice.frame_ms` (20 ms) frames at `voice.rate` (320 samples). Capture arrives
wider and faster: 20 ms at 48 kHz is 960 samples per channel, so the adapter
decimates 3:1 before anything else touches the samples.

Eight interleaved channels: `ch0..ch5` microphones, `ch6` and `ch7` the same
playback-reference loopback. Reference travels **inside** the frame, which is why
`AudioFrame` carries `reference_channels` instead of an `is_reference` flag.

## wire 率：48 kHz 还是 16 kHz（0.8.9）

**默认跟随设备 manifest 的原生率。** `rui setup` 的模板（`src/rui/config/template.yaml`）
和 `config.example.yaml` 都不再写 `audio.capture.rate`：插件 manifest 的
`audio_defaults.capture.rate` 声明硬件原生率（LX06 = 48000，Dot 2 = 16000），
`effective_audio()` 直接采用。16 kHz 降为「想省带宽时显式写」的选项。

### 带宽：PCM payload 768 KB/s

桥收到的 `record` 是一条 **binary websocket 帧，帧内是 JSON**：PCM 放在 `bytes` 字段里，
默认是 int 数组（`list(pcm)`，见 `rui-xiaoai-bridge/src/rui_xiaoai_bridge/protocol.py` 的
`stream()` / `decode_stream()`），fork 也接受 base64。所以要把三个口径分开：

| 口径 | 48 kHz × 8 ch | 16 kHz × 8 ch |
|---|---|---|
| PCM payload（int16，2 B/样本） | **768 KB/s**（≈6 Mbit/s） | **256 KB/s**（≈2 Mbit/s） |
| WS 帧内 int 数组 JSON（约 4 B/PCM 字节） | ≈3 MB/s | ≈1 MB/s |
| WS 帧内 base64（fork 支持） | ≈1 MB/s | ≈341 KB/s |

旧注释里的「6 MB/s」是**32-bit 口径**：32-bit PCM 是 1.5 MB/s，再乘约 4× 的 JSON 编码
开销 ≈ 6 MB/s（与 `docs/xiaoai-protocol.md` 的旧估算一致）。线上的 PCM 实际是 int16，
对应的是 768 KB/s；上面第二行列出了 JSON 编码后的真实线上字节。规划带宽时看「PCM 多少」
用第一行，看「socket 上多少」用后两行——别再拿 6 MB/s 当默认基准。

### 显式 16 kHz 的代价（实测）

`capture.rate: 16000` 是**协商**而不是硬件事实（见下）：显式值赢，设备端 open-xiaoai
自己把 48k 重采样成 16k 再发。2026-10 生产 NAS 上的 8ch LX06 实测：

- 语言带能量损失约 **15 dB**：`[seg] rms 0.006 / 0.008`（16 kHz wire）对
  `0.035`（48 kHz wire，同一房间、同一音量）。
- 后果是 **ASR 空转/超时**，而不是听不见：**唤醒照常成功**，所以「能不能唤醒」查不出这个问题。
- 48 kHz wire 换回来的直接收益是 ASR 一次通过。

### CPU：实测与口径边界

生产 NAS（8ch LX06，容器内）：

| 指标 | 16 kHz wire | 48 kHz wire |
|---|---|---|
| 进程 CPU（单核口径） | 27.9 % | **214 %** |
| 容器 CPU | 68 % | **329 %** |
| `asr_done` | 179 ms | **807 ms** |
| load average | — | **4.67**（4 核） |

离线 profile（`docs/analysis/decimate-profile-0.8.9.md`，M2 Ultra，10 s 8ch 音频）：
`Decimator` 旧全速率约 **0.098 s** → 多相约 **0.027 s**（约 3.7×），生产实际取 7 列时约
**0.035 s**（相对旧 8 列约 2.8×）；该 host 的 run-to-run 波动是几个百分点（旧 0.0934–0.0986 s、
新 8 列 0.0255–0.0268 s、7 列 0.0333–0.0359 s，见剖面文档的 Setup 段，以当次运行为准）。
48 kHz 路径上抽取器约占单核 0.3 %（旧约 1 %）。

**口径边界（别过度宣称）：**多相化只解释了抽取器本身——离线实测整条 core 侧采集路径的
48k−16k 差值只有约 0.036 s/10 s 音频，换算到 NAS 约 5–7 % 单核，**不可能解释 +186 个
百分点**。剖面文档已把 ring/preroll 拷贝实测为 rate-invariant（两列均 0.0003 s）并排除；
仍未证实的是：plug/adapter 与 wire decode（binary → PCM，另一个仓）、WS send/recv 线程
与 3× payload 的拷贝、ASR/VAD worker 线程、设备侧采集负载。要在生产上确认只能用
`py-spy` 在 NAS 上逐项 profile。不要把「多相抽取已落地」说成「生产 CPU 已解释/已解决」。

### `AudioDefaults` 的 `capture.rate` 协商语义

合并顺序是 `core default < 全局配置 < manifest audio_defaults < devices[].audio`，
所有字段里**只有 `capture.rate` 是协商项**（其余 capture 字段是硬件事实，manifest 赢）：

1. **配置没有写 `rate`**（key 不在 `model_fields_set`，值也等于 core default）→
   manifest 的原生率生效，不产生任何 conflict。模板默认走这条。
2. **配置显式写了 `rate`**（key 在 `model_fields_set`，或值 ≠ core default）→
   显式值赢，`winner: global-config`、`kind: negotiated`，消息形如
   `audio.capture.rate: global 16000 vs model 48000; using 16000 (explicit wire wins; the client resamples)`。
   **重采样由客户端/设备负责**，服务端收到的已经是所请求的率。
3. `devices[].audio.capture.rate` 是设备级的显式值，优先于全局与 manifest（同 0.8.8）。
4. core default 仍是 **16000**，它只为**没有 manifest 的旧 plug**（解析不到
   `audio_defaults`）兜底，不是「生产推荐值」。

想给老 8ch 阵列省带宽就显式写 `rate: 16000`，并接受上面的 15 dB 与 ASR 风险；这属于
operator 的选择，源码不再替任何部署默认做这个选择。

## The one number that matters

Mic full scale is `2**23`, reference full scale is `2**28` — a factor of 32, five
binary bits. A client that converts every channel to 16-bit with one `>>8` clips:

- 52% of reference samples at volume 110
- 78% at volume 170

A clipped reference cannot cancel echo. Measurements on the same algorithm and
the same music: **5.5 dB before, 23.0 dB after** at volume 110; **4.4 dB → 25.2
dB** at volume 170. Per-channel scaling (`mic >>8`, `ref >>13`) is the whole fix.

If you change one thing in this repo, keep this one.

## Echo canceller

speexdsp MDF, 4096 taps (256 ms of tail at 16 kHz), one filter per microphone, fed
128-sample speex frames from arbitrary block sizes. The reference arrives 2.4 ms before
the microphones hear it, so alignment is a fixed delay, not a search.

Same geometry the vendor front end uses (`speex_echo_state_init(0x80, 0x1000)`), which
is why the measured behaviour matches the old deployment's 24-30 dB ERLE instead of
being a filter we would have to tune from scratch.

Offline benchmark (`scripts/bench_aec.py`, synthetic 50 ms and 200 ms rooms, six
microphones, 20 s of music-like reference):

| room tail | ERLE whole run | ERLE converged | CPU rtf (6 filters) |
|---|---|---|---|
| 50 ms | 22.9 dB | 30.7 dB | 0.066 |
| 200 ms | 22.0 dB | 30.5 dB | 0.066 |

The chain picks the microphone that survives best (hysteresis 6 dB) and refuses to
return a residual louder than its input; a diverged filter resets instead of leaking
garbage into KWS. While the reference is silent the canceller is bypassed - there is no
echo to cancel, and adapting a filter to silence only drifts it.

`rui doctor` still prices AEC at rtf 0.004 because it measures the filter
arithmetic alone; the 0.066 above is what running the real library costs.

### On the speaker (LX06, 2026-10-21, `scripts/measure_erle.py`)

Pink-noise-plus-tones at the speaker, 30 s, reference RMS −25.5 dBFS / peak −10.7 dBFS,
no clipping, six microphones:

| | value |
|---|---|
| raw microphone | −17.5 to −20.1 dBFS (the echo is the loudest thing in the room) |
| residual after AEC | −38.6 to −39.9 dBFS |
| ERLE over the whole run | 19.8 - 21.2 dB (includes convergence) |
| ERLE over the second half | 24.7 - 25.1 dB |
| ERLE per 5 s window, ch0 | 17.3, 15.1, 26.7, 23.5, 23.1, 29.3 dB |

Inside the old deployment's 24-30 dB band once converged. The window-by-window curve
carries the finding that matters operationally: **the filter needs ~10 s of continuous
playback to converge**. A wake word spoken in the first seconds of a song sees 15-17 dB
of cancellation, not 29 dB. Priming the filter on startup is a possible follow-up; for
now the honest statement is that barge-in is strongest after the music has been playing
for a while.

## Barge-in

1. Playback runs through a queue of ~200 ms (`playback.queue_ms`).
2. KWS stays armed during playback; AEC keeps the mic clean.
3. On wake: fade out over `playback.fade_out_ms`, flush the queue **and whatever
   the device has already buffered**, transition to `listening`.
4. Target: fully silent within `playback.target_silence_ms` (300 ms) of the user
   starting to speak.

Step 3 is where the risk lives: flushing our own queue is microseconds, flushing
the speaker's ALSA buffer needs a control message the device client must honour.
Measure it (wake → mic energy floor) before believing the 300 ms number.

Without a reference channel this cannot work. Say so in the UI.

## Wake word

sherpa-onnx keyword spotting on the echo-cancelled mono stream, with the parameters the
old deployment ran in production: zipformer `chunk 8` (160 ms of context), boost 2.0,
threshold `#0.20`, two threads. Not re-tuned - those values already wake reliably at high
volume, and a new threshold would be a new experiment with a new regression surface.

Measured on the LX06 with music playing (`scripts/measure_wake.py`, 75 s, wake word
spoken about every 4 s): **18 wakes**, `rtf 0.0935` for AEC + KWS together, 0 dropped
frames, reference active throughout.

Two things the run showed:

- **ERLE reported at wake time reads 6-13 dB, not the 21-28 dB the canceller reaches on
  its own.** The person speaking adds energy to the microphone that the canceller is not
  supposed to remove, so the ratio drops. ERLE is only meaningful on a stretch with no
  speech; the live number is a health indicator, not a test result.
- The offline tests need silence on both sides of the phrase, because the spotter needs
  context before the first phoneme. Production never has this problem - the microphone is
  already running when someone speaks.

## Latency marks

`rui.trace` stamps the boundaries that cost time: `wake`,
`end_of_speech`, `asr_first`, `asr_done`, `llm_first`, `llm_done`, `tts_first`,
`play_start`, `barge_in`, `silence`. It computes `first_response_ms`
(wake → play_start) and `silence_after_ms` (barge_in → silence). A stage without a
number is a stage nobody has measured.

## Known traps

- **Bit-depth** above. Silent failure mode: sounds fine, fails only with music.
- **Clock drift.** Device clock and server clock diverge; the jitter buffer must
  adapt or the queue grows until latency is unusable.
- **Decimation is not free.** 8 ch × 48 kHz of int16 PCM is ~768 KB/s of payload; the
  bytes on the socket are higher because the frame body is JSON (int array ≈3 MB/s,
  base64 ≈1 MB/s) — see the wire-rate section above. The old "1.5 MB/s / 6 MB/s" figures
  were the 32-bit PCM rate and its JSON-expanded estimate. One bad WiFi hop shows up as a
  gap in `frames()`, not as an error.
