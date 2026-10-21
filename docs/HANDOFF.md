# HANDOFF — house-ear

写给下一个会话的自己（或另一个 agent）。读完这一页应当能直接开工，不必重新读一遍代码。

## 1. 这是什么

**任何能 SSH 的音箱都能接的个人贾维斯，NAS 一键部署。** v1 只支持小米小爱音箱 Pro（LX06）。
MIT，私有仓 `yes999zc/house-ear`，本地 `~/AI_Workspace/01_Code/projects/house-ear`。

- 旧项目 `xiaoai-voice-entry` 是**只读参考**：它的实测结论、参数、后台可配置项直接照抄；
  代码不 import、配置不改。设备同一时间只能连一个服务端，测完必须切回去。
- 技术栈：Python 3.12 + FastAPI + Pydantic v2 控制面；音频面（AEC/KWS/VAD/播放）独立进程；
  前端 Vue 3 + TS + Vite。Rust 已论证否决（瓶颈是网络与 C 库，不是语言）。

## 2. 架构要点（改了会破坏契约）

```
设备(LX06) --ws 拨入--> device adapter --> 音频面进程 --> IPC(unix socket) --> 控制面(FastAPI)
                                             AEC/KWS/VAD/回放                   回合编排/provider/控制台
```

- **三档采样率**：采集向设备请求 16 kHz（8 ch，mic 6 + ref 2）、语音处理 16 kHz、播放 16 kHz mono。
  设备硬件是 48k/2ch 播放、S32/8ch 采集，重采样在设备或音频面内部完成。
- **移位在设备做**：`mic_shift=12`、`ref_shift=16`（旧项目生产值，ERLE 24–30 dB）。
- **IPC 帧头 5 字节**（type 1 + length 4），PCM payload 带 `seq(uint32) + ts_ms(float64)`。
  控制消息 JSON，PCM 是二进制帧；`audio/protocol.py` 与 `device/xiaoai/protocol.py` 是两套，别混。
- **音频链路内部只有一种刻度**：`[-1,1]` 浮点。进出各转一次，漏了就会全削顶（KWS 无声但字节数正常）。
- **语音分段**：唤醒后跳过唤醒词尾巴 300 ms、保留 200 ms 预录、600 ms 静音收尾、10 s 硬截断、
  唤醒后 5 s 无人说话就放弃。Silero 只判人声，状态机在 `audio/vad.py` 自己拿。

## 3. 进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 契约、config v2、tools 注册表、会话状态机、仲裁、控制面、六页控制台、compose/install.sh、ADR | ✅ |
| S1 | 音频面独立进程 + framed IPC + 丢帧/抖动/rtf 统计 | ✅ mock 5 min：14234 帧、0 丢、jitter p50 21 ms、rtf 0.0055 |
| S2 | LX06 入站 adapter、设备线协议、fake speaker e2e | ✅ 真机采集 246 帧，mic RMS -45 dBFS |
| S3 | speexdsp AEC（6 麦克风、4096 taps）+ debug 播放 + KWS | ✅ 真机 ERLE 19.8–21.2 dB（后半段 24.7–25.1）；放歌 75 s 唤醒 18/18 |
| S4 | VAD 分段、回合编排（ASR→LLM→TTS→推流）、provider 链、trace 打点 | ✅ 20 轮 pipeline：20/20，首句出声 p50 3563 ms |
| S5 | 唤醒应答、会话控制、控制台补齐、打断、工具执行 | ⬜ 下一步 |

测试 **130 passed**，ruff clean，CI 三 job（py3.11 / py3.12 / image）绿。

## 4. 已定的决策（别推翻，改了先问 9哥）

1. **ASR 本地优先 + 云兜底**：本地 sherpa SenseVoice int8（N100 上 threads=2 → 210 ms，
   rtf 0.12；Mac 上 51 ms）。云 `FunAudioLLM/SenseVoiceSmall` 同一句实测 0.38–2.5 s，还有
   20 s 超时——所以云只做 fallback。
2. **TTS 纯 API，讯飞主用**：`x4_lingxiaowan_boy`（9哥 现用音色），首字节 383–935 ms；
   硅基 `CosyVoice2-0.5B:charles` 兜底。硅基单独用时首字节 3.7–21.6 s，不可用于语音回合。
3. **LLM 纯 API，DeepSeek 官方**：`api.deepseek.com` / `deepseek-flash` / temp 0.4 /
   max_tokens 3000 / **thinking disabled**（旧项目实测 thinking 让延迟翻 2–3 倍）。
4. **模型不进镜像**：`house-ear fetch-models --dest /data/models/sensevoice` 从 hf-mirror
   拉 239 MB 到数据卷；`install.sh` 第 4 步调用，失败只警告、ASR 自动回落云端。
5. **唤醒冲突先到先得**（200 ms 窗口，平局按 device id）；播放走服务端解码 + PCM 推流。
6. **打断 ≤300 ms 的变量在设备 ALSA 缓冲**，只做本地 flush 永远差一截。

## 5. 旧项目参数对照（照抄清单）

| 层 | 旧项目（生产值） | house-ear |
|---|---|---|
| ASR | sherpa SenseVoice int8 / threads 2 / itn / zh | ✅ 同 |
| LLM | deepseek-flash / temp 0.4 / max_tokens 3000 / thinking off | ✅ 同 |
| TTS | 讯飞 `x4_lingxiaowan_boy`，aue=raw，L16 16k，speed/vol/pitch 50 | ✅ 同 |
| TTS 兜底 | 硅基 `charles` | ✅ 同 |
| 人格 | 小瑞：简短干脆、先给结论、不客套、不知道就说不知道 | ✅ 同 |
| 唤醒 | 你好小瑞 / 门限 0.20 / boost 2.0 / kws_chunk 8 | ✅ 门限同，boost 未暴露 |
| 唤醒应答 | **默认 160 ms 提示音**（earcon），TTS 应答可选 | ⬜ 未实现 |
| 会话 | 超时 10 s、history_rounds 6、停止词、告别语、filler 1.8 s | ⬜ 未实现 |
| 音频 | tts_speed 1.05、echo_tail 0.7、music_volume 0.68、device_volume 0（不接管） | ⬜ 部分 |

**「在呢」为什么会吞话**（旧项目注释里写死的事实）：TTS 应答播放期间麦克风被静音约 1.2 s，
唤醒词后面的请求开头被吃掉；`play()` 必须 fire-and-forget（await 会卡住帧消费者，丢约 0.7 s，
队列只 4 格）；KWS 晚约 0.5 s 报命中，靠 preroll 回放补；TTS 应答还要开 1.2 s + 应答时长的
回声忽略窗口。结论：**默认用 160 ms 提示音，TTS 应答上限 600 ms**。

## 6. 已知问题（在案）

- **无工具的回合会编造事实**：连问 20 轮天气出现过「今天晴天最高20度」「我在北京 今天晴天」
  这类自相矛盾的具体数值。工具只注册未执行。
- **history 会让回复退化成复读**：同一句连问 20 轮，第 13 轮起连续 8 轮一字不差；另一次
  20 轮里语气逐步变冲（「再问我要罢工了哦」）。需要去重 + 禁止模仿上轮措辞。
- **唤醒前 0.5 s 音频被丢**：「你好小瑞，现在几点」一口气说完时开头会缺，需要 preroll 回放。
- 打断链路（barge-in）未实现，`trace` 里的 `barge_in/silence` 阶段目前没有数据。
- Mac 本机音频输出曾整机哑掉（`AudioQueueStart failed 0xe000002ec`，`voice_rms_db` 恒 -53 dBFS），
  恢复要 `sudo killall coreaudiod`。声学那组（放歌 + 20 次唤醒）因此没跑成。

## 7. 后续计划（S5）

按收益排序，每步都要实测数字，不接受「已完成」：

1. **S5a 唤醒应答**：`wake.ack_mode`（none/earcon/tts）、`ack_text`、`chime` preset、`ack_max_ms`；
   本地 160 ms 双音合成、fire-and-forget、preroll 回放、回声忽略窗口。
   验收：三档各 20 轮「唤醒后立刻说指令」，量化首字丢失率；TTS 应答 ≤600 ms 时丢失 0 次，
   并给出 0.5 s vs 1.5 s 应答的对比数据（把「不能太长」变成配置上限）。
2. **S5b 回合质量**：相同问句去重、禁止复读/抱怨、无工具时禁止报具体数值。
   验收：同句 20 轮回复不相同率、编造具体数值 0 次。
3. **S5c 会话控制**：停止词、超时、告别语、filler。验收：停止词生效、超时后告别、filler 只在超阈值出现。
4. **S5d 控制台补齐**：把旧项目 10 个分区的可配置项搬进六页 Vue，带恢复默认/备份/原子写。
5. **S5e 打断 300 ms + 工具执行**：设备侧丢缓冲 + 停推；工具注册表接进回合。

## 8. 操作手册

```bash
cd ~/AI_Workspace/01_Code/projects/house-ear
uv run pytest && uv run ruff check src tests scripts
cd web && npm run typecheck && npm run build && cd ..

# 一整回合（不需要房间；假音箱送 8ch 帧，真实 KWS/VAD/ASR/LLM/TTS/播放）
uv run python scripts/measure_turn_pipeline.py \
    --config ~/.hermes/cache/scratch/house-ear-s4/real-device.yaml \
    --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav --rounds 20

# 声学版（需要房间 + Mac 音频可用）
uv run python scripts/measure_turns.py --url http://127.0.0.1:8091 \
    --wake tests/fixtures/wake-nihao-xiaorui.wav \
    --command-dir ~/.hermes/cache/scratch/house-ear-s4/commands --rounds 20

# 启动控制面 + 音频面（真机）
set -a; . ~/.hermes/.env; set +a
export HOUSE_EAR_DEVICE_TOKEN="$(cat ~/.hermes/cache/scratch/house-ear-s4/device-token.txt)"
uv run house-ear serve --config ~/.hermes/cache/scratch/house-ear-s4/real-device.yaml   # web :8091
```

**真机切换**（LX06，`root@<LAN-IP>`，密码 `<redacted>`，必须
`-o HostKeyAlgorithms=+ssh-rsa`；dropbear 会限流，被拒等 40–60 s）：

1. 先起 house-ear（`listen_port: 4412`；4399/4400 被旧服务占着，4410 也被旧项目监听）
2. 备份 `client-run.sh`、`server.txt` 为 `*.bak-houseear`，把 URL 改成
   `ws://<LAN-IP>:4412/?token=<HOUSE_EAR_DEVICE_TOKEN>`，kill 客户端等看门狗 30 s 拉起
3. 用 `lsof -nP -iTCP:4412` 看到 `<LAN-IP>` 的 ESTABLISHED 才算连上
4. **测完立刻还原**两个 `.bak-houseear`，kill 客户端，确认 `:4399` 有 ESTABLISHED

NAS（Bakey-NAS，Intel N100，`<LAN-IP>`，`yes999zc`）：pip 必须走
`-i https://pypi.tuna.tsinghua.edu.cn/simple`；sshd 限流，用 ControlMaster 单连接复用。

凭据位置：`~/.hermes/.env`（`DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、`XFYUN_APPID/API_KEY/API_SECRET`）。
**只写变量名，不写值进任何文档、日志、聊天。**

## 9. 交接时的现场状态（2026-10-21）

- 音箱已恢复旧入口 `ws://<LAN-IP>:4399`，与旧服务 ESTABLISHED；house-ear 全停，端口释放。
- 最近提交 `5eb7dff`，CI 绿。工作树干净。
- 配置样本与测量产物在 `~/.hermes/cache/scratch/house-ear-s4/`（24 h 后被清）：
  `real-device.yaml`、`commands/*.wav`、`pipeline-xfyun-20.jsonl`、`device-token.txt`。
  需要长期保留的东西要复制进仓库或 `04_Docs/`。
- 下一步第一个动作：**S5a**，先读旧项目 `server/assistant_v3.py` 的 `ack_sound()`、
  `_ACK_ECHO` 和 preroll 回放那三段，再动手。
