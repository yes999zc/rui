# HANDOFF — house-ear

> ## ⚠️ 动手之前，先读老项目（最重要的一条，必须严格执行）
>
> **重读 `~/AI_Workspace/01_Code/projects/xiaoai-voice-entry`：尤其是它的 `HANDOFF.md`
> （1300+ 行）、`docs/`、`server/prompts.py`、`server/assistant_v3.py` 的相关段落、
> 以及 `server/test_*.py` 全套。那里沉淀了大量真机实测数据、阈值、踩过的坑。**
>
> - 参数、人格、提示词、禁用项、去重/工具规则、超时/告别/filler 行为：**照抄**，只重测
>   「新链路可能改变」的部分（例如 preroll 在新 IPC 下的表现）。
> - **不要重复造轮子**，不要用自己 20 轮的小实验替代老项目的结论；要复核就拿老项目的数据
>   对账，不一致时先解释差异，再决定改谁。
> - 已经因此吃过一次亏：S5b 我自作聪明另写了一套去重/报数 guard，而老项目早在
>   `server/prompts.py` 里用明确的工具规则解决编造，`clean_reply` 只管剥 DSML 标记，
>   `conversation.history_rounds` 是 6。这些都没先读，浪费了时间。
> - 检查顺序：`HANDOFF.md` 全文 → `docs/` → `prompts.py` → 与本次任务相关的 `test_*.py`
>   → 最后才看 `assistant_v3.py` 的实现细节。
>
> **反面同样致命（9哥 2026-10-22 补的一条）**：参考老项目是为了不重复造轮，**不是给新项目设天花板**。
> 老项目没有的东西，只要新项目需要就必须做，不能因为「老项目没做」就砍掉。判断标准只有一条：
> 这是「同一个问题老项目已经解过」→ 去查；还是「新项目的新要求」→ 自己设计。
> 例：S5b 的「重复问题不进 history」老项目没有，但它把复读从 40–65% 压到 85–95%，就该做。

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
| S5a | 唤醒应答、preroll 回放、回声窗口 | ✅ 见 `docs/ack.md` |
| S5b | 回合质量（复读、抱怨、编造、标记、唤醒词尾巴） | ✅ 见 `docs/turn-quality.md` |
| S5c | 会话控制（停止词、超时、告别语、filler）+ 厂家让位真机验证 | ⬜ 下一步 |
| S5d | 控制台补齐（旧项目 10 个分区 → 六页 Vue） | ⬜ |
| S5e | 打断 300 ms + 工具执行 | ⬜ |

测试 **164 passed**，ruff clean。S5a 相关提交 7 个（286b867 → 15157bd），已推 origin/main。

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
7. **唤醒应答默认 160 ms 提示音**（`chime_ms: 0` 恢复旧项目的自然长度），TTS 应答上限
   600 ms 在渲染时强制执行、超限回落提示音；喇叭播语音应答会压麦 ~1.2 s。
8. **"回声窗口"不是聋窗**：hold 期间到达的音频进缓冲（保留最近 600 ms），闸门打开后
   灌进 gate。旧项目用 `echo_tail_buf` 修过同一个错误，别再做成丢帧。
9. **厂家唤醒 = 让位**（`session.vendor_quiet_s`，默认 12 s）：说「小爱同学」时不开我们的
   会话、不说提示音，我们的唤醒词立刻接管。细节 `docs/vendor.md`。

## 5. 旧项目参数对照（照抄清单）

| 层 | 旧项目（生产值） | house-ear |
|---|---|---|
| ASR | sherpa SenseVoice int8 / threads 2 / itn / zh | ✅ 同 |
| LLM | deepseek-flash / temp 0.4 / max_tokens 3000 / thinking off | ✅ 同 |
| TTS | 讯飞 `x4_lingxiaowan_boy`，aue=raw，L16 16k，speed/vol/pitch 50 | ✅ 同 |
| TTS 兜底 | 硅基 `charles` | ✅ 同 |
| 人格 | 小瑞：简短干脆、先给结论、不客套、不知道就说不知道 | ✅ 同 |
| 唤醒 | 你好小瑞 / 门限 0.20 / boost 2.0 / kws_chunk 8 | ✅ 门限同，boost 未暴露 |
| 唤醒应答 | 默认 160 ms 提示音（earcon），TTS 应答可选 | ✅ 三档 + `ack.max_ms` 强制执行 |
| 会话 | 超时 10 s、history_rounds 6、停止词、告别语、filler 1.8 s | ⬜ S5c（代码里是 `history_turns=4`，要改） |
| 音频 | tts_speed 1.05、echo_tail 0.7、music_volume 0.68、device_volume 0（不接管） | ⬜ 部分 |

**「在呢」为什么会吞话**（旧项目注释里写死的事实）：TTS 应答播放期间麦克风被静音约 1.2 s，
唤醒词后面的请求开头被吃掉；`play()` 必须 fire-and-forget（await 会卡住帧消费者，丢约 0.7 s，
队列只 4 格）；KWS 晚约 0.5 s 报命中，靠 preroll 回放补；TTS 应答还要开 1.2 s + 应答时长的
回声忽略窗口。结论：**默认用 160 ms 提示音，TTS 应答上限 600 ms**。

## 6. 已知问题（在案）

- ~~无工具的回合会编造事实~~ **已收口（S5b）**：实测 4 句 × 20 轮，编造具体数值 0/20，
  并且模型不再宣称能开关灯/放音乐（一个工具都没接时那是编造能力）。机制是提示词里那段
  「本轮没有查证工具」的降级注释（照老项目 OFFLINE_NOTE 的做法），不是后置检测。
  真正让天气/时间能答的是 S5e 的工具，见 `docs/turn-quality.md`。
- ~~history 会让回复退化成复读~~ **已收口（S5b）**：同句 20 轮不相同率 40–65% → 85–95%，
  最长一字不差连跑 12 → 1–2 轮，抱怨/威胁（「再问我就装没听见」）消失。根因不是历史长度，
  是「模型能看到自己对同一个问题的上一轮回答」→ 复制；`TurnRunner.history_messages()`
  把重复问题的旧副本从窗口里去掉。**顺带修了一个真 bug**：`history[-2 * 0:]` 等于整段历史，
  `history_rounds: 0` 以前等于「无限历史」。
- ~~唤醒前 0.5 s 音频被丢~~ **已修（S5a）**：`voice.wake_preroll_ms: 500`，实测无回放时
  20/20 丢首字（「天气怎么样？」），有回放时完整。KWS 命中延迟实测 1.09–1.22 s（从唤醒词
  开头算），不是「0.5 s」。
- ~~说「小爱同学」时两个助手一起答~~ **已实现并真机验证（2026-10-22 08:33）**：说「小爱同学，
  今天天气怎么样」只有厂家回答，日志只有 `vendor wake ('今天天气怎么样'), we yield`，没有我们的
  `[asr]`；窗口内接着说「你好小瑞」立刻接管。细节与日志原文 `docs/vendor.md`。
- **回复曾以 3 倍速播放**（16 k PCM 当 48 k 播）：S4 起就存在，S5a 修在音频面重采样里。
  记在这里是因为它只靠耳朵能发现，别再用「字节数/时长统计正常」当证据。
- **打断链路（barge-in）未实现，`trace` 里的 `barge_in/silence` 阶段目前没有数据。**
  真机基线（2026-10-22 08:35–08:36，已量化，S5e 的验收起点）：
  - 我们播报 8.8 s 的长回答期间说「你好小瑞」，**KWS 命中**（AEC 在真机成立，ERLE 19–21 dB）
  - 新回合照常跑起来（`停一下。` → `好，停了。`），**但旧的播报没有被停**：日志无 `stop_play`，
    8.8 s 的音频继续播到结束，与新回答重叠约 3 s
  - 从开口到新回答首音 **4563 ms**（KWS 1.3 s + ASR 0.48 s + LLM 0.92 s + TTS 1.5 s）——
    这是「回答延迟」，与 S5e 要的「停推 ≤300 ms」是两个不同的数，验收时必须分开量
- **短噪声会成一轮**：真机上「你好小瑞」后面轻轻一声「嗯。」被判成一整句话，回了「嗯，你说。」。
  `vad_min_speech_ms=200` 挡不住这种；老项目靠最小语音时长 + 文本过滤压的，属 S5c。
- **ASR 对技术词不可靠**：「回声消除」被听成「回程校除」。模型用反问兜住了（这一轮回复是
  「你说的回程校除我没听明白，是不是回程校准」），行为可以，但技术问题的真机体验会打折。
- **时间/天气/新闻/搜索一条都答不了**：它们全是工具（老项目 `server/tools/`），house-ear
  一个都没接（S5e）。老项目还在 system prompt 里拼了「现在是X年X月X日 星期X X点X分」，
  所以它答「现在几点」不靠工具——house-ear 要不要照抄这条，S5e 接工具时一起定。
- **`ack.mode: tts` 的文本级回声去重没移植**（老项目 `_is_ack_echo`）：默认是 160 ms 提示音，
  而语音应答实测整句被吞（0/20），所以现在没有可去重的回声；等 tts 应答重新启用时再移植。
- **单连接多回合端到端跑不通（S5b 发现，且 pre-S5b 一样）**：`measure_turn_pipeline.py`
  5 轮里只有第 1 轮真的落回合。证据（`~/.hermes/cache/scratch/house-ear-s5b/e2e-5b.log`）：
  第 2–5 轮 KWS 都命中（lag 1.25–1.31 s）、提示音都播了（ack_plays 5）、gate 都 arm 了，
  但 `utterances` 始终 =1；每次唤醒时音频面报 `wake while a turn was open: 900 ms discarded`，
  整条连接 325 帧（每轮只送出 ~3.9 s）；且每轮之间隔 55 s（两处 `wait_s` 25 s 各超时一次）。
  **A/B 结论**：`git stash` 回到 S5a 代码，同一条命令结果一模一样 —— 不是 S5b 引入的。
  已排除：harness 轮间不读 socket（已改成整段一个 reader，结果不变）、音频面日志缺失（已修）。
  下一步：给 harness 记每轮送帧/收帧数，重点查「回复播放期间设备侧读取是否被拖住」。
  **S5c（超时/告别/filler）与 S5e（打断/工具）的多轮验收都依赖它，先修再往下走。**
- **`system.log_level` 以前根本没生效**：没有一处调用 `logging.basicConfig`，音频面所有
  `log.info`（gate 放弃、utterance 丢弃、播放结束）全部不输出，控制面的 `turn:` 也没有。
  S5b 已加 `house_ear/log_setup.py` 并在 `serve` 与音频面进程里调用；同时把音频面的 stderr
  从「没人读的 PIPE」改成持续读取（不读会在 64 KB 处把子进程卡死在写日志里）。
- Mac 本机音频输出曾整机哑掉（`AudioQueueStart failed 0xe000002ec`，`voice_rms_db` 恒 -53 dBFS），
  恢复要 `sudo killall coreaudiod`。声学那组（放歌 + 20 次唤醒）因此没跑成。

## 7. 后续计划

按收益排序，每步都要实测数字，不接受「已完成」：

0. ~~S5a~~ **已完成**（提交 286b867…15157bd）：三档应答、preroll 回放、hold 缓冲、
   `ack.max_ms` 强制执行、doctor 的 `wake_path`/`ack_wav` 检查、`fetch-models` 拉 silero。
   数据与复现命令在 `docs/ack.md`。
2. ~~S5b~~ **已完成**：提示词层照抄老项目（人格/长度分档/唤醒应答别再打招呼）、`clean_reply`、
   `strip_wake`、`history_rounds=6`、无工具降级注释、重复问题不进 history。
   验收数字：同句 20 轮不相同率 **85–95%**（原 40–65%），最长一字不差 **1–2 轮**（原 8–12），
   编造数值 **0/20**，抱怨/威胁 **0**。数据与复现命令在 `docs/turn-quality.md`。
   **先决条件**：多回合端到端还没修（见 §6），S5b 用的是 `scripts/measure_reply_quality.py`。
3. **S5c 会话控制**：停止词、超时、告别语、filler。验收：停止词生效、超时后告别、filler 只在超阈值出现。
   **开工前先修 §6 的多回合端到端**，否则验收跑不起来。
4. **S5d 控制台补齐**：把旧项目 10 个分区的可配置项搬进六页 Vue，带恢复默认/备份/原子写。
5. **S5e 打断 300 ms + 工具执行**：设备侧丢缓冲 + 停推；工具注册表接进回合
   （`weather`/`news`/`web_search` 直接借老项目的 schema 与 open-meteo，免 key）。
6. ~~厂家让位真机验证~~ **已完成（2026-10-22 08:33–08:36）**：见 `docs/vendor.md`。同一窗口顺带
   拿到打断基线（KWS 命中但不停播、开口到首音 4563 ms），已写进 §6。

## 8. 操作手册

```bash
cd ~/AI_Workspace/01_Code/projects/house-ear
uv run pytest && uv run ruff check src tests scripts
cd web && npm run typecheck && npm run build && cd ..

# 一整回合（不需要房间；假音箱送 8ch 帧，真实 KWS/VAD/ASR/LLM/TTS/播放）
uv run python scripts/measure_turn_pipeline.py \
    --config ~/.hermes/cache/scratch/house-ear-s4/real-device.yaml \
    --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav --rounds 20

# 唤醒应答 / preroll 扫描（假音箱、真 KWS/VAD/ASR，不需要房间）
uv run python scripts/measure_turn_pipeline.py \
    --config ~/.hermes/cache/scratch/house-ear-s4/real-device.yaml \
    --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav \
    --rounds 20 --gap-ms 0 --speed 1.0 --tight --asr-only \
    --ack-mode earcon --preroll-ms 500 --gate-preroll-ms 350 \
    --out ~/.hermes/cache/scratch/house-ear-s5a/earcon-gap0.jsonl
uv run python scripts/summarize_ack.py ~/.hermes/cache/scratch/house-ear-s5a

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

## 9. 交接时的现场状态（2026-10-22 凌晨 04:0x）

- **S5a 已完成并提交**（`286b867` → `7032f12`）：唤醒应答三档、preroll 回放、hold 缓冲
  （不是聋窗）、`ack.max_ms` 强制执行、doctor 的 `wake_path`/`ack_wav`、`fetch-models`
  一并拉 silero、厂家让位（离线测试覆盖，真机未验）。
- **S5b 已完成**（本次）：`session/prompts.py`（照抄老项目提示词 + 无工具降级注释）、
  `session/text.py`（`clean_reply` / `strip_wake`）、`assistant.*` 配置分区、
  `session.history_rounds=6`、`TurnRunner.history_messages()`（重复问题不进 history）、
  每轮重建系统提示词、DSML 句子不进 TTS。数据在 `docs/turn-quality.md`。
- 顺带修的两个真 bug：`history[-2 * 0:]` 等于整段历史（`history_rounds: 0` 变成无限历史）；
  `system.log_level` 从没生效（无 `basicConfig`）+ 音频面 stderr 是没人读的 PIPE。
- 测试 **174 passed**，ruff clean，web typecheck 通过。CI 待推后核对三个 job。
- 实测数据都在 `~/.hermes/cache/scratch/house-ear-s5b/`（24 h 会清，结论已进 docs）：
  `baseline-*.jsonl` 4 句 × 20 轮、`final-*.jsonl` 同 4 句 × 20 轮、`e2e-5b.log` 端到端。
- 音箱：**全程没有切**，仍是旧入口 `ws://<LAN-IP>:4399`，今晚所有测试都用假音箱。
- 未提交的东西：无。
- 下一步第一个动作：**修 §6 的「单连接多回合端到端跑不通」**（证据已固定，pre-S5b 一样），
  然后才进 S5c；要 9哥 出声的两条（厂家让位、打断 300 ms）留到他白天配合时做。
- 真机还欠两条：厂家让位验证、打断 300 ms —— 都要 9哥 白天配合出声，按 §8 切换并还原。
