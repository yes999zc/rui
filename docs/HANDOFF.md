# HANDOFF — house-ear

> ## 动手之前（强制顺序，别跳）
>
> 1. **老项目 `xiaoai-voice-entry` 的 `docs/design-and-measurements.md`**
>    —— 老项目作者写的核心设计说明 + 全部实测参数（一页纸，10 分钟读完）。**先读这份。**
>    里面每个数字都有日期和出处：思考模式、唤醒门限演变、AEC 参数、ack 演变、三道回声闸、
>    打断实测、TTS/ASR 时延、反模式清单、现役配置快照。
> 2. 老项目 `HANDOFF.md`（1300+ 行）按需查：门限扫描方法、`echo_tail_buf` 事故、
>    `[asr] 我想想。` 自听事故、打断接线、TTS 缓存预热。
> 3. 与本次任务相关的 `server/prompts.py`、`server/assistant_v3.py` 段落、`server/test_*.py`。
>    侦察结论已整理在 `docs/recon-old-build.md`（ASR/端点/TTS/记忆/音乐/灯环，带文件:行号）。
> 4. 本项目 `docs/audio-latency.md`（2026-10-22 的实测与根因）、`docs/ack.md`、
>    `docs/turn-quality.md`、`docs/vendor.md`。
>
> **工作方式（9哥 定的，2026-10-22）**
>
> - **参数与结论优先参考老项目**：它跑了两天真机，数字比我们的推理可靠。照抄就写「照抄」，
>   不同就写「差异」，落后就写「差距」，别把没量的东西写成结论。
> - **方法论取两者里更好的**：老项目的实现不是天花板。两边都有做法时选更好的那一套，
>   不为了「保持一致」折中。
> - **能并行就派子 agent**：互不依赖的排查/验证同时开，不排队等一个结果再做下一个。
> - **阶段性及时汇报**：给数字（ms、dB、条数、帧数、丢了几秒），不要「已完成」「已优化」。
>   没有数字就说「未测」。
>
> **两条规矩**
> - 同一个问题老项目解过 → 去读它的实现和实测数，不要重新造。
> - 新项目的新要求 → 自己设计；不以「老项目没做」为理由砍需求（老项目不是天花板）。
>
> 已经因此吃过两次亏：S5b 另写了一套去重 guard（老项目用提示词工具规则解决）；
> 2026-10-22 又自己发明了一版回声去重，而老项目 `_is_self_echo` 已经跑了两天。

> **路线图与「v1 完成」的定义在 `docs/ROADMAP.md`；分层与设备扩展契约在
> `docs/architecture.md`。** 本文记录现场状态、决策与坑，不重复那两份的内容。
>
> **⚠ 2026-10-22 晚：当前最痛的是播放链路（首字被吞 / 句间爆破音 / 告别卡）。
> 三个故障的根因已定位、修法已写好、代码未动——先读 `docs/playback-path.md`，
> 不要直接改 `_speak_loop` 或 `speak()`，那里有一条已知的错误设计（per-chunk 淡入）。**

## 1. 这是什么

**任何能 SSH 的音箱都能接的个人贾维斯，NAS 一键部署。** v1 只支持小米小爱音箱 Pro（LX06）。
MIT，公开仓 `yes999zc/house-ear`（main）。

技术栈：Python 3.12+ / FastAPI / Pydantic v2 控制面；音频面（AEC/KWS/VAD/播放）独立进程；
前端 Vue 3 + TS + Vite。旧项目 `xiaoai-voice-entry` **只读参考**：不 import、不改配置、
不占用它的端口（4399/4400）。

## 2. 架构契约（改了会破坏对接）

```
设备(LX06) --ws 拨入--> device adapter --> 音频面进程 --> IPC(unix socket) --> 控制面(FastAPI)
                                            AEC/KWS/VAD/播放                回合编排/provider/控制台
```

- 三档采样率：采集 16 kHz（8 ch：mic 6 + ref 2）、语音处理 16 kHz、播放 48 kHz/2 ch。
- **移位在设备做**：`mic_shift=12`、`ref_shift=16`（老项目生产值。统一 `>>8` 会把参考削掉
  33–75%，AEC 直接残废——这是老项目最关键的一次修复）。
- IPC 帧头 5 字节（type 1 + length 4），PCM payload 带 `seq + ts_ms`；控制消息 JSON。
- 音频面内部只有一种刻度：`[-1,1]` 浮点。进出各转一次，漏了就全削顶（KWS 无声但字节数正常）。
- 语音分段：跳过唤醒词尾巴、保留预录、600 ms 静音收尾、10 s 硬截断、唤醒后 5 s 无人说话放弃。

## 3. 进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 契约、config v2、工具注册表、会话状态机、六页控制台、compose/install.sh | ✅ |
| S1 | 音频面独立进程 + framed IPC + 丢帧/抖动/rtf 统计 | ✅ 14234 帧 0 丢，rtf 0.0055 |
| S2 | LX06 入站 adapter、线协议、假音箱 e2e | ✅ 真机采集 246 帧 |
| S3 | speexdsp AEC（4096 taps）+ KWS | ✅ **1 个滤波器消 6 麦加权混音**（`audio.aec.cancel: mix`，默认；`e10bdeb`）；嘻唰唰副歌循环 ERLE **23.5 dB@170 / 22.8 dB@220**（参考 -25.0 / -12.3 dBFS）；旧 6 滤波器同条件 21.2 / 16.3，老项目 170 档 25.2；rtf 0.066→0.011 |
| S4 | VAD 分段、回合编排（ASR→LLM→TTS→推流）、trace | ✅ 20 轮 20/20，首句 p50 3563 ms（无工具，11:xx 口径） |
| S5a | 唤醒应答三档、preroll 回放、回声窗口 | ✅ `docs/ack.md` |
| S5b | 回合质量（复读、抱怨、编造、标记、唤醒词尾巴） | ✅ `docs/turn-quality.md` |
| S5c | 停止词、短噪声、filler、自听守卫 | 🟡 15 s 超时 + 两档告别 + filler 预渲染已落地（`f200778`/`f814d6f`/`0575b57`）；告别真机已验（2026-10-22 16:05）；**提醒到点已接线并真机响过一次（16:37:48，408 ms 到首块）**；音乐类跳过 filler 待音乐 |
| S5d | 控制台（六页 Vue + 助手页 + 密钥页） | 🟡 概览页（状态 + 试一试 + 停止）、音量读写、唤醒门限真实值、`playback_lead_ms` 日志已加（`709c9e2`/`c7eba1e`）；**剩灯光（B1）、记忆（D2：house-ear 尚无记忆功能）** |
| S5e | 时间注入、weather/news/web_search、工具循环 | ✅ reminder/timer **已接线**（`1882d8c`：`build_local_tools` + 1 s tick + 到点经 `runner.say`，避开回合与占用）；真机 2026-10-22 16:37:48 到点、408 ms 出声 |
| S6 | 播报节奏（Pacer）+ 打断（barge-in） | ✅ 真机：队列 0 丢弃、打断丢 7.6 s（`4c4a69b`）；剩余 lead ≈420 ms 未按 300 ms 口径验收 |
| S7 | ASR 常驻 + filler 预渲染 | ✅ 真机 ASR 51–90 ms（原 500 ms + 每轮 4.7–5.3 s 加载），filler 0.73 s 预渲染（`f814d6f`） |
| S8 | 6 麦加权混音前端（`audio/frontend.py`） | ✅ 加权混音 + 放音冻结权重；真机唤醒 **6/6、4/4**（门限 0.15，`keywords-015.txt`） |
| S9 | 设备 shell 通道 + 音量读写 | ✅ `run_shell` + `read_volume`/`set_volume`（`0646fe6`）；`GET/PUT /api/v1/device/volume`；真机 mysoftvol **220/255**（numid=177）；默认不接管（`devices[].volume: 0` = 归原厂，当前无自动写入路径） |
| S10 | 说话/放歌互斥（ducking：暂停并续播） | ✅ 回复期间暂停音乐并记位，说完续播（rewind 0.55 s = 老项目 `PLAY_LAG_SECONDS`）；打断也走暂停（`e8994f2`/`eae1a24`）；修了循环文件位置累加、最后一句后不续播 |
| S12 | 播放链路修复（首字 / 接缝 / 控速） | ✅ **三项全关**：0a 句级边界+350 ms 真静音（`c555e1d`）、0b 80 ms 静置（`9991773`）、每轮回答新起播放器治长静默 underrun（`68b9876`）、consumed 在途上限（`e82a01a`）；9哥 16:10 听感验收「首字不吞、句间完美、告别不卡」；30 s 音乐 **slow write 0 / underrun 0 / TIMEOUT 0**。细节 `docs/playback-path.md` |
| S13 | 播放仲裁 + 会话收尾修正 | ✅ 喇叭单一 owner（P0 提示音 > P1 回答 > P2 提醒 > P3 音乐，`487cdcf`）；提示音占住播放权到播完（`2e56b9c`）；播放期间不吃空闲计时、告别只说一次（`a5d141d`/`8955a7c`） |
| S14 | filler 按意图选词 + 按工具计时 | ✅ 三条短语启动预渲染（我查一下/我想想/好的我看看），从"模型决定调工具"起算 0.6 s（`e498148`/`b5ad748`） |
| S11 | NAS 冷装验收（v1 门槛） | ⬜ 未开始：30 分钟到第一句、20 次唤醒 ≥19（0.15 门限） |
| 真机欠账 | 厂家让位验证、打断停推 ≤300 ms、220 档远处唤醒 | ⬜ 都要 9哥 出声配合 |
| P13b 灯环 | 设备层 + 四态映射 + 配置 | ✅ 2026-10-22 18:45 真机整轮确认（唤醒白圈 → 思考短脉冲 → 说话青覆盖整段 → 播完绿并保持 → 退出灭）；`music` 颜色待 D1 |
| 会话连续对话 | 一轮结束继续听（0.5 s 端点、无唤醒词、5 s 窗口） | ✅ 2026-10-22 真机：连续追问两轮成功（18:21:30 / 41:42 无需唤醒）；结束信号改为 `ReplyFinished`，不再用队列空猜 |

**289 passed**（2026-10-22 15:40，HEAD `8955a7c`；此前 270/1 的 jitter 失败是当时 load 10.8 造成的假红，清掉孤儿 Chrome 进程后单跑通过）。那次失败的是 `tests/test_audio_plane.py` 的 jitter 断言
（`test_plane_decimates_a_mock_device_over_a_socket`，23.1 ms vs 20±3）：当时 load 10.8（一堆孤儿 Chrome
渲染进程占满 CPU），`git stash` 后同样失败，**不是代码问题**；清掉那些进程后单跑已通过。
AEC/唤醒的数字已补进 `docs/audio-latency.md` §3.5/§3.6。

## 4. 已定决策（别推翻，改前先问 9哥）

1. **ASR 本地优先**：sherpa SenseVoice int8，threads=2（N100 210 ms）；云只做 fallback
   （0.38–2.5 s，还有 20 s 超时）。
2. **TTS 讯飞主用**：`x4_lingxiaowan_boy`，硅基 `charles` 兜底。硅基单独用首字节 3.7–21.6 s，
   不能用在语音回合。
3. **LLM 纯 API**：`api.deepseek.com` / `deepseek-flash` / temp 0.4 / max_tokens 3000 /
   **thinking 关**（老项目实测：开思考延迟翻 2–3 倍、零准确率收益）。
4. **模型不进镜像**：`house-ear fetch-models` 从 hf-mirror 拉，失败只警告、ASR 回落云端。
5. **唤醒冲突先到先得**（200 ms 窗口，平局按 device id）；播放走服务端解码 + PCM 推流。
6. **唤醒应答默认提示音**（`ack.mode: earcon`）。理由是我们自己的实测：TTS 应答
   （0.55 s 语音 + 1.2 s 压麦）在 S5a 里 0/20 整句被吞。老项目用「在呢」0.52 s 是通的，
   **这个差异没解释，属未解问题**（见 §6 第 5 条）。160 ms 填不满客户端 300 ms 启动缓冲，
   提示音会憋到答案一起出；`9948765` 改成 320 ms、PEAK 0.05→0.12、cue 改突发送出，
   **未真机验收**。
7. **「回声窗口」不是聋窗**：hold 期间到达的音频进缓冲（600 ms），闸门打开后灌进 gate。
8. **厂家唤醒 = 让位**（`session.vendor_quiet_s` 12 s）：说「小爱同学」时不开我们的会话、
   不播提示音；我们的唤醒词立刻接管。细节 `docs/vendor.md`。
9a. **会话内连续对话**（2026-10-22）：一轮真正播完后，音频面等排空 + 1 秒回声尾巴，再以
   0.5 s 端点开一次「续听门」（无唤醒词、5 秒无人说话自动关）；控制面用 `ReplyFinished`
   明确告知轮次结束，不能用「队列空」判断——9.6 秒的回答会在第 2.4 秒误开。
9b. **退出词可配置**（`session.end_words`，默认 24 个）：含旧项目 11 个 + 停止播放类；
   短句命中即结束会话并灭灯。
9. **公开仓库硬约束**：不许硬编码私人信息（API key、Mac 路径、内网 IP、默认城市）。
   密钥只走环境变量或 `secrets.yaml`（0600、写-only），API 只回 `has_key`。
   审计：`grep -rnE "/User[s]/|192\.168\.|sk-[A-Za-z0-9]{16}|Bearer [A-Za-z0-9_-]{20}" src/ docs/ README.md config.example.yaml`。
10. **第二款音箱**只留 adapter 接口（`device/contract.py` + registry），真机调试留到 v1 发布后。
11. **AEC 消在混音后**（`audio.aec.cancel: mix`，默认）：一个 speexdsp 滤波器消 6 麦加权混音，与老项目同构。
    实测 170/220 = 23.5/22.8 dB，旧 6 滤波器 21.2/16.3，rtf 0.066→0.011；`per_channel` 只留给诊断用
    （逐通道残差）。别因为「每路都消」听起来更彻底而改回去——音量越高，它塌得越快。
12. **设备音量默认不接管**：代码里没有自动写音量的路径（`devices[].volume: 0` 的语义是归原厂）；
    可以读（`GET /api/v1/device/volume`），写只在显式请求或测试时做。厂商会自己改 mysoftvol
    （老项目见过 160→255→160，几分钟内）。

## 5. 与老项目的参数差（照抄清单 + 差距性质）

| 项 | 老项目生产值 | 我们（2026-10-22 下午） | 性质 |
|---|---|---|---|
| AEC | speexdsp MDF 4096 taps + **双讲残差>35% 停自适应** + 收敛闸 `erf<0.1*ep` | **一个滤波器消 6 麦加权混音**（与老项目同构，`audio.aec.cancel: mix`），4096 taps；仍无双讲/收敛判据；嘻唰唰副歌实测 **170 = 23.5 dB、220 = 22.8 dB**（老项目 170 = 25.2） | 结构已对齐；170 差 1.7 dB，原因未查，先记差异。旧 6 滤波器同条件 21.2 / 16.3——**音量越高塌得越多**，这就是 220 档要靠近的原因 |
| 采集移位 | mic `>>12`、ref `>>16` | 同 | ✅ |
| 高通 | 90 Hz，麦与参考同拐点 | 同；**每通道始终推进**（放音暂停也不冻结），修掉切通道时 5 个滤波器带旧状态爆音 | ✅ 已对齐，且比老实现更稳 |
| 唤醒门限 | 0.20 + boost 2.0 + **探针 0.10 只记日志** | **0.15**（本地 `keywords-015.txt` 的 `#0.15`，不再用共享文件）+ 探针 0.10 只记日志；无 boost | 门限按真机定：0.20 时 4/5（漏的那次探针也没中）；一次 -40.3 dBFS 探针中、生产不中；降到 0.15 后 4/4，探针与生产命中次数不再拉开 |
| 阵列 | 6 麦按各自噪声底加权混音（`FLOOR_RATIO=1.5`），老项目实测均值比最好单路 **+1.6 dB** | 同款加权混音（`audio/frontend.py`）+ 放音时冻结权重 | 方法已对齐；**真机唤醒率已测**（0.20 → 4/5，0.15 → 4/4；放歌 170/220 各中 1 次）；阵增益未单测 |
| ack | tts「在呢」0.52 s，`drain=False` | earcon 320 ms（`9948765`；PEAK 0.12、突发送出），未真机验收 | 未解差异，见 §6 第 5 条 |
| filler | 1.8 s，「我想想／我查一下／我看看」，**音乐类跳过** | 1.8 s + **启动预渲染 0.73 s 音频**（内存，不再现调讯飞）；不跳过音乐 | 时延已对齐（TTS 那段归零），音乐判断仍缺 |
| TTS | 首包 **180 ms**；整句合成完 **0.44–0.60 s**（13–23 字，3 样本） | 首字节 p50 292 / p95 518 ms；整句合成完 p50 **346** / p95 612 ms（10 句，9–28 字） | 同口径：整句快 0.1–0.25 s；首包慢约 0.11 s。原 861 ms 是单样本离群值（`03cc6da`）；口径见 `docs/recon-old-build.md` §3 |
| ASR | sherpa SenseVoice int8，threads=2，N100 210 ms | 同模型，**常驻内存**；真机 **51–90 ms**（4 轮） | ✅ 反超；不再每轮加载 1.1 GB |
| 会话 | 超时 15 s + 两档告别 + end_words 11 个 | 同构（`session/idle.py`、`session/filler.py`）；15 s 超时 + 告别**真机验一次**（日志 `session closed: '没事的话，我先下啦。'`） | 告别路径已通；提醒到点未验 |
| 放歌与说话 | 回复前暂停音乐、说完续播；`PLAY_LAG_SECONDS = 0.55` 回退 | 同语义：回复期间暂停并记位，说完续播，rewind 0.55 s；**打断也走暂停**（`e8994f2`/`eae1a24`） | ✅ 已对齐；真机放歌时唤醒 2 次都当场暂停（170/220 各 1 次） |
| 历史 | history_rounds 6 | 同 | ✅ |
| 播报 | 连续 aplay 管道（背压）+ 句间 350 ms 真静音 + 只计真正写进 aplay 的块 | `Pacer`：目标 200 ms、上限 350 ms、2% 追帧；新指标 `playback_lead_ms`；队列无界 + 120 s 秒数预算 | 意图对齐（缓冲不见底），实现不同；设备侧缓冲未测 |
| 唤醒后端点 | `POST_WAKE_SILENCE = 1.0 s`，收完第一段回 0.5 s | 600 ms | 可能吃首字，未单独验 |

## 6. 已知问题（按严重度，全部有实测出处或明确写「未测」）

0. **播放链路：听感已验收，剩控速（0c）**——细节全在 `docs/playback-path.md`。
   a) **首字被吞**（连报三轮）已修，根因两层：`start_play` 回执 ≠ ALSA 就绪（补 80 ms 静置，
      `9991773`）；保活的 aplay 在提示音→答案的 5.5 s 静默里排空（设备日志
      `underrun!!! (at least 5373.550 ms long)`），第一块被重启吃掉——改为每轮回答新起播放器
      （`68b9876`）。验收：`reply: first chunk` 与 `start_play` 同一毫秒，`chunk 1 at 88–93 ms`。
   b) **句间爆破音**已修：per-chunk 淡入（`2e56b9c`/`b5ad748` 自己引入）改为声明式
      `SpeakStart`（`c555e1d`），句后 350 ms 真静音。9哥：「衔接处完美，顺利」。
   c) **告别卡/连说三遍**已修（`8955a7c` + a/b）。9哥：「不卡，顺利」。
   d) **控速已做（0c）**：adapter 不再忽略 `consumed`，音频面用它做**在途上限**
      （`audio.playback.credit_window`，默认 8）。实测该回报是「aplay 管道收下」而非「播完」，
      所以 window=2 会把管道灌满（228 次 slow write / 30 s）；window=8 + 墙钟 = **0 次**。
      设备停止回报时一次 1.5 s 超时后退回墙钟（粘性）。
   e) 顺手修掉：歌曲播完不释放 `output_owner`（下一轮回答白等 2 s、空闲计时永不告别），
      `fecd410` 起播放结束释放、续播重新 claim。
1. **220 档（接近满音量）下唤醒仍需靠近**。0.15 门限的 4/4 是「开窗、平时音量、近处」；
   220 档只验到「放歌时近距离命中一次」。原因量出了一半：AEC 在 220 档 ERLE 22.8 dB，但参考是
   -12.3 dBFS，按 ERLE 定义推算回声残差电平 ≈**-35.1 dBFS**；170 档是 **-48.5 dBFS**
   （-25.0 − 23.5），**220 档残差比 170 档高 13.4 dB**。**220 档在正常距离/远处的唤醒率没有数字。**
2. **`config.audio.voice.kws_threshold` 是假旋钮**。运行时没人读它，真门限在 keywords 文件里
   （现在是 `keywords-015.txt` 的 `#0.15`）。控制台已把这一项标成「只读来源」，但配置字段还留着，
   改它没有任何效果——下一个人会以为调了门限。要么删掉，要么让它生成 keywords 文件。
3. **4399 被 `house-ear` 临时接管**。旧项目的 4399 现在跑的是我们（`allow_no_token`，测试期行为），
   与 §2/§8「不占 4399/4400」的契约不一致。发布/重启前必须改回 4412 并关掉 `allow_no_token`；
   两套服务混在一个端口上，出问题分不清是谁。
4. **打断后的剩余 lead ≈420 ms**。wake 时音频面清队列 + `stop_play`，但已交给设备的部分
   必须播完（Pacer 上限 350 ms + 设备侧缓冲）。实测这一轮丢排队 7.6 s（控制面记 7.7 s），
   剩余约 420 ms；**「停推 ≤300 ms」没有按这个口径量过**，设备侧 aplay/ALSA 缓冲深度
   也未实测（老项目自量 `PLAY_LAG_SECONDS = 0.55`）。
5. **`ack.mode: tts` 与老项目结果相反**：老项目「在呢」0.52 s 正常；我们 S5a 实测 0/20 整句被吞。
   现在默认 earcon 绕开了它，但只要想把「在呢」做成默认，就得先解释这个差异
   （怀疑 `echo_ignore_ms=1200` + 门控比老项目粗）。
6. **音乐类跳过 filler 未做**：现在任何回合 1.8 s 后都会说「我查一下」，放歌/调音量类指令
   也会说。老项目按音乐意图跳过。
7. **`playback_lead_ms` 只进 Metrics/控制台，日志不打印**。今天判断「队列不再丢块」靠的是
   `speak queue full` 0 条 + 队列不再积压，没有逐轮 lead 抄表；回归要看控制台（`348e77b`）。
8. **ASR 对技术词不可靠**（「回声消除」→「回程校除」）；模型用反问兜住，行为可以但体验打折。
   `0575b57` 补了老项目的 0.8 峰值归一化（安静片段先抬到 0.8 再送解码），效果未单测。
9. **提醒到点出声未真机验收**。`tools/reminders.py` + `f740819` 的到点调度已落地；
   15 s 超时和告别语已在真机各验一次（§3 S5c），提醒还没在音箱上响过。
10. **已修，值得记住**：短噪声成轮（`session/rules.is_noise`）、自听成轮（`SpokenMemory`）、
    **提示音期间的第二次唤醒曾被当成 barge**（`89c5dae`：只有回复/文件才算「正在说话」，
    cue 不算）。
11. **已修但值得记住的坑**：`system.log_level` 从未生效（无 `basicConfig`）；音频面 stderr 是
    没人读的 PIPE（64 KB 卡死子进程）；`history[-2*0:]` 等于整段历史；回复曾以 3 倍速播放
    （16 k PCM 当 48 k 播，只有耳朵能发现）；**回复队列按「块数」限长（maxsize=200）会截断整句**
    （当天日志 118 条 dropping）；**`asr_chain()` 每轮新建 provider 把 1.1 GB 模型加载算进每一轮**
    （每轮 4.7–5.3 s 死气）；**filler 现调 TTS 把首字时延叠在 1.8 s 等待上**。

## 7. 下一步（顺序以 `docs/ROADMAP.md` §2 为准；2026-10-22 重排，音乐放最后）

**当前顺序 A1–A4 → B1 灯环 → B2 真机 session → C1 冷装（第 2 条门槛等音乐）→ D1 音乐 → D2 门槛 → E1 设备中立。**
A1 提醒接线是最大的一块「写了没接」：`tools/reminders.py` 与调度有 44 条测试，但 `app.py`
只 import 了 `tools.info.build_tools`，`build_scheduler` 全仓没有构造点。

**（原顺序记录，细节保留）**

**（1）音乐播放能力 —— 老项目有一整块，我们还没开始。**
这是现在最大的功能缺口：S10 只把「说话不打歌」打通，还没有「放歌」本身；`NO_MUSIC_NOTE`
（`e8994f2`）是让模型承认做不到的补丁，起因是真机对着正在放的歌回答「好，接着放。」。
要做：音乐源（NAS 曲库 / URL）→ 播放队列 → 暂停/继续/下一首 → 音量（S9 已能读写 mysoftvol）
→ 意图到工具的映射 → 去掉 `NO_MUSIC_NOTE` → 音乐类跳过 filler（§6.6）。
验收：真机「放一首嘻唰唰」出声；说话暂停、说完续播（rewind 0.55 s）；打断后不自己续播。
需要 9哥：选歌与听感。

**（2）真机欠账（一次 session 全做，需 9哥 出声）**

- 厂家让位（§4.8）：说「小爱同学」时我们不开会话、不播提示音。
- 打断停推 ≤300 ms：现在剩余 lead ≈420 ms，按设备侧 aplay 缓冲 + `playback_lead_ms` 同一口径量（§6.4）。
- 220 档远处唤醒：0.15 门限只在近场 4/4；要测正常距离/远处的命中率（§6.1）。
- 唤醒后端点：我们 600 ms 单一值（gate 每轮 disarm，所以只有「唤醒后第一段」这一种情形），
  老项目首段 1.0 s、之后 0.5 s。对照法：同一句中间停 0.7 s，600 会提前收尾、ASR 吃掉后半句；
  1000 应完整。两次各说 2 遍，比转写文本。
预期：关闭这三项欠账，220 档「要很近」第一次有数字。

**（3）S11 NAS 冷装验收（v1 门槛，做完才算 v1）**

- 空机器按 `docs/deploy.md` 装：**30 分钟到第一句**。
- 平时音量、0.15 门限：**20 次唤醒 ≥19**。
- 每步记实际耗时；不达标就回来改安装脚本，不改门槛。
需要 9哥：出声配合最后一项。

## 8. 操作手册

```bash
cd ~/house-ear
uv run pytest && uv run ruff check src tests scripts
cd web && npm run typecheck && npm run build && cd ..

# 一整回合（假音箱，真 KWS/VAD/ASR/LLM/TTS；TTS 不发声）
uv run python scripts/measure_turn_pipeline.py \
    --config /tmp/house-ear-scratch/house-ear-s5b/tools.yaml \
    --command /tmp/house-ear-scratch/house-ear-s4/commands/01-weather.wav --rounds 20

# 纯文本质量（真 ASR + 真 DeepSeek，20 轮同句）
uv run python scripts/measure_reply_quality.py --question "今天天气怎么样" --rounds 20

# 起服务（真机；:4412）
set -a; . $HOUSE_EAR_ENV_FILE; set +a
export HOUSE_EAR_DEVICE_TOKEN="$(cat /tmp/house-ear-scratch/house-ear-s4/device-token.txt)"
uv run house-ear serve --config /tmp/house-ear-scratch/house-ear-s5b/tools.yaml   # 控制台 :8091
```

**真机切换**（LX06，`root@$SPEAKER_IP`，密码从 `$SPEAKER_PASSWORD` 读，必须
`-o HostKeyAlgorithms=+ssh-rsa`；dropbear 限流，被拒等 40–60 s）：

1. 先起 house-ear（`listen_port: 4412`）。**先确认 4399/4400 上是谁**——今天 4399 是我们自己
   临时接管（§9），不是旧服务；按本节流程前先把它停掉/改回 4412。
2. 备份 `client-run.sh`、`server.txt` 为 `*.bak-houseear`，URL 改成
   `ws://$SERVER_IP:4412/?token=<TOKEN>`，kill 客户端，等看门狗 30 s 拉起。
3. `lsof -nP -iTCP:4412` 看到 `$SPEAKER_IP` ESTABLISHED 才算连上。
4. **测完立刻还原**两个 `.bak-houseear`，kill 客户端，确认 `:4399` ESTABLISHED。

**注意**：SSH 命令里不要出现 `pkill -f "/data/open-xiaoai/client"` 这种字面量——它会匹配到
自己的 ssh 命令行，把会话打断。用 `pkill -f 'open-xiaoai/clien[t]'`。

凭据在 `$HOUSE_EAR_ENV_FILE`（`DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、`XFYUN_*`）。
**只写变量名，不写值。**

## 9. 现场状态（2026-10-22 22:55 自测收线）

- 代码：HEAD `3be4825`；**344 passed**，ruff clean。服务在跑、音箱已连、音乐已停。
  今晚累计修复四件：自言自语 `3f8a93c`、退下吧双告别 `ec3815f`、音乐 0.4 s 复播致聋
  `2bfd356`、间隙冒头+停止词停乐 `3be4825`——全部真机复验通过（细节见下）。
- **让位 V1 复验 ✅（20:38、20:41 两轮，真机）**：`vendor takeover: pausing` → pause RPC
  `before=1 {"code": 0}` → 厂家 `playing Idle` ≤0.2 s 且不再回来，小瑞独答；第二轮
  「你好小瑞，今天天气怎么样」端到端完整（ASR/天气工具/8.7 s 答句全对）。
  **判据修正**：`after=0` 才是对的（老项目 HANDOFF：mute_stat 0=idle 1=playing 2=paused；
  TTS 对话被 pause 即终结→idle，`after=2` 是**音乐**被暂停的情形，B2 放歌时核对）。
- **自言自语复验 ✅（20:39）**：farewell 前出现 `gate: disarmed, our own audio is about to
  sound`，告别语播完无幻影 turn、无第二次 farewell 循环。根因（20:16 事故）：告别语播进上一轮
  reply 留开的 follow-up gate，回声过 AEC 残余被 ASR 转回文字（啦→吧 一字之差绕过精确兜底），
  LLM 答了自己的告别，每 ~15 s 循环一次、是否触发看 ~30 ms 竞态。修复两层：plane 自我发声前
  disarm armed gate（音乐恢复也不开 follow-up 窗口）；`is_echo` 加 0.75 模糊匹配。
- **退下吧双告别（9哥 20:42 报告，修复 `ec3815f` 复验 ✅ 20:50）**：原 stop-intent 只答「好。」没关
  空闲钟，17.5 s 后 farewell 又道一次别。修复 `runner.dismiss()` 先 `idle.stop()` 再答。复验：
  20:50:27 stop intent → 一句「好。」→ 之后 **46 s+ 无 farewell**（整份日志 farewell 计数 0）。
- **本段日志另两处亮点（20:49–20:50）**：① 第三轮流让位照常成立；② **真打断首次实测成立**：
  自己答句播到一半喊「你好小瑞」→ `barge: 1 wake during playback, dropped 4.4 s queued` →
  即起新轮、答对「今天星期几」。
- **观察项（不阻塞）**：① 20:38 首轮 ASR 把「现在几点」听成 `'南方3。'`（疑提示音叠话头，后续
  多轮转写全对，单样本）；② **天气工具今晚 1/3**（20:39 ✗、20:42 ✓、20:50 ✗，报「没查到」，
  答句降级正常）——上游问题为主，B2 后值得单查。
- **起服务（正确方式）**：`nohup /tmp/house-ear-scratch/house-ear-s5b/serve-dev.sh
  > /tmp/house-ear-scratch/he-serve-b3t.log 2>&1 &`。脚本自己 grep `$HOUSE_EAR_ENV_FILE` 取 6 个
  provider key，**`set -a` 是关键行**（20:09–20:12 丢 key 事故根因）；`HOUSE_EAR_DEVICE_TOKEN`
  故意不设（dev 配置走 `allow_no_token`）。重启先按端口杀 4399/8091；带证据的旧日志先轮转。
- **设备**：`$SPEAKER_IP`，SSH 用老项目 `server/devshell.py`（凭证 `music.secrets_path 指向的凭据 JSON`
  的 `voice.device_host/device_ssh`，自带 ControlMaster 防 dropbear 限流）；客户端日志 `/tmp/client.log`。
- **B2 第一阶段（19:52–19:54，真机）**：① 端点 600 ms 整句保留（1 样本）✅；② 连续追问+灯效 ✅；
  ③ 厂家让位 ❌→修复 `3b4648e`→**复验 ✅（见上）**。
- **B2 第二阶段首轮（20:53–20:56，失败→已修 `2bfd356` 待复验）**：放歌循环期间 4 次唤醒全部
  「只响提示音、音乐不停、听不到说话」。链条：唤醒暂停音乐 ✓ → speak_loop 空队列 tick 0.5 s 一跳
  把「队列空」当「说完」→ **0.4 s 后音乐复播**压住用户说话 → 复播的 `_play_file` 又 disarm 了
  唤醒刚 arm 的 gate → 小瑞变聋。修复照老项目 `MusicPlayer` 语义（`assistant_v3.py:1690-2200`）：
  **歌等完整个 turn**——只有 gate 关了才续（回复播完，或 no-speech 超时 disarm，即老项目
  `vendor_silence_watchdog` 的角色）；另 `_pause_for_speech` 幂等（双重 pause 曾把位置 5.5→4.9 s
  回退两次）。**复验判据**：放歌中唤醒 → 音乐停、提示音、说话有 [seg]、答完音乐自动续；不说话
  则 15 s 后音乐自动续。
- **老项目音乐打断体系（9哥 指路，已核）**：`pause(for_speech)` 说话压低+对话完续 / `paused_by_barge`
  键打断不自动续 / 静默看门狗 4 s / 帧循环与 `speech_task` 分离否则 KWS 不跑（老 HANDOFF:254，我们
  已对）/ CREDIT 拉模式（已抄）/ 「全音量下只有厂家 AEC 听得清人声，小爱同学是打断音乐的方式」——
  我们 KWS 在 220 档 4/4 命中（probe -10~-15 dBFS），比老项目强，但人声段 VAD/ASR 质量以复验为准。
- **B2 第二阶段（21:12–21:13 复验通过，音乐链路四轮）**：放歌循环中三轮「你好小瑞+问句」
  全部：音乐即停（服务端 0-4 ms 下发）→ 话完整听到（转写一字不差）→ 答对 → **间隙无冒头**
  （`2bfd356`+`3be4825` 生效：utterance 后到 ReplyFinished 之间音乐全程压着）→ 答完自动续。
  第四轮「结束音乐」→ `stop intent` → **音乐停且不再回来**（停止词分流真机验证 ✓）。
- **遗留到 D1（9哥 定：不查了，随音乐接入一并处理）**：① 音乐续播后 `follow-up gate open`
  仍会出现（21:13:06/18，`_arm_followup_later` 的 owner 检查没拦住，原因未查；今晚四轮都
  即刻说话所以没起歌词幻影 turn）——D1 接真音乐时 gate/owner 语义一起理；② 3-4 米远处唤醒
  未正式计数（今晚多轮唤醒全中，含 -10 dBFS 弱信号）；③ 长文本/新闻打断机制已具备
  （barge 20:50:08 实测），随 D1 真机细验。
- **下一步（22:55 更新，9哥 已去忙，全部自测完成）**：
  1. **D1 音乐 ✅ 核心完成**（`a11bd00`..`2edec7b`，12 个 commit）：多源接口 + Navidrome 首源 +
     队列服务 + 9 工具 + 确定性词直通 + 显式暂停 + 起歌压 vendor。**真机自测全通**：点歌（晴天/
     林俊杰/陈奕迅…）/随机/暂停/继续/上下首/停止/搜索/口味；**自然播完 17ms 无缝切歌 + 队列自动
     补齐**（soak3 22:27 实测）；对话压低答完自动续 ✓。设计 `docs/music.md`。
  2. **控制台 P1 ✅**（`0ff8922`）：PanelSpec 注册表（后端声明、前端渲染、disabled 灰化不隐藏）+
     `POST /api/v1/music`（与语音同路径）+ 搜索端点 + usePanels/PanelHost/四 block + 概览/设备/
     音乐三面板。**打开 `http://127.0.0.1:8091/` 即见**（音乐面板：状态/正在播/六按钮/口味）。
     设计 `docs/console.md`（含老项目调研全文与 P2–P4 分期）。
  3. **遗留（不阻塞）**：远场 20 唤醒计数需 9哥；音量对账接入起歌点；farewell 静音续歌边角；
     控制台 P2（form block/token 输入/日志页/对话面板）；在线兜底源只留接口位。
  4. **最后一步 C1 冷装**：代码门槛全绿（367 passed、ruff、vue-tsc、镜像预检曾通），等 9哥
     在场时做（30 分钟到第一句的验收需要他的耳朵和嘴）。

## 10. 无声开发期（2026-10-23 凌晨起，9哥 定：音箱全程不出声，出声验证统一排队）

- **模式**：dev 配置 `voice.kws_enabled: false`（无唤醒→无提示音/无应答，控制台照常在线）；
  音乐/试一说全程未碰。**统一验收时恢复 `kws_enabled: true` 并重启。**
- **V1 语音交互系统层 ✅ `5821ccd`**：PhraseBook（全局话术表）+ ToolResult 结构化 +
  InteractionSpec 声明协议 + `_call_tool` 渲染路径 + 轮级 degrade（turn.failed 不再沉默）。
  音乐 9 工具全部迁移，AST 扫描测试证明工具代码零中文字面话术。验收：每工具话术出自
  PhraseBook 的 pytest 全绿。
- **D1b ✅**：① gate-over-music 根因修复 `50d2d32`——grace 分支过早调 `_resume_after_speech`
  时 claim 失败但状态被消耗，音乐无主播放、follow-up 门开在「空」speaker 上（21:13 生产案
  例完整复现为测试）；② V4/V5 反向让位 `6ca7ed4`——vendor 唤醒或其播放器（键114同一事件）起
  时停驻我方歌（StopAudio 保留停驻位置），其对话结束（idle+0.6s）或 4s 静默看门狗还回；
  ③ 音量对账 `ada5fa0`——devices[].volume 百分比目标在 metrics 周期读上对账 mysoftvol（0=不管）；
  ④ farewell 避开还回窗口；⑤ filler 音乐跳过随 D1 工具注册自动生效；⑥ V2（vendor 转写我方
  唤醒词）由既有 12s 静默窗覆盖。
- **统一验收排队清单**（恢复出声后按序）：音乐 V1 话术真机听感（放歌/暂停/继续的 PhraseBook
  句式）；V4 让位（小爱唤醒→歌停→还回，ubus 注入法见 vendor.md）；远场 20 唤醒计数；灯环
  音乐态；B2 收尾数字；最后 C1 冷装。
- **下一步**：HA-MCP（多服务器 MCP 客户端 + HA 经 MCP + V2 三原语 clarify/choose/confirm，
  HA 为首个验证用例；设计 docs/interaction.md §3/§5、老项目方案行号在差距报告）→ C-UI P2。

## 11. 统一验收（2026-10-23 早上，9哥 配合）——进度

**已通过（真机，日志为证）**：

1. **音乐话术 V1**：暂停/继续/下一首/停止 全部走 PhraseBook，出声正常。
2. **「停止」停音乐**：`stop intent: '停止。'` → 出声「好，不放了。」→ 音乐停（修 fadd0a0：
   原判断用 output_owner，唤醒压低后 owner 已释放）。
3. **对话全程音乐压低**：慢工具（天气 9 s）从唤醒到答完 31 s 音乐一声不冒，答完 6 s 后
   才续播（修 fadd0a0/d5cb8d5：窗口持有截止时间 + 12 s 无答兜底 + 失败轮次补 reply_finished）。
4. **V4 反向让位**：小爱答天气 15 s，我方音乐压低 16 s，`playing Idle` 后 0.6 s 还歌
   （修 b1bfe6f：控制面自跟踪 vendor 状态 + Dialog.Finish 作为结束信号 + 看门狗只覆盖"唤醒
   后从未开播"）。V5 同一路径（键 114 起播也是 playing 事件）。
5. **音量工具**（修 9195bfd/19ff6dc）：原本没有工具，模型谎报「调到80%了」；现真调用 mixer
   并学习为 devices[].volume（防 16 s 后对账回退）。

**待测**：远场 20 次唤醒计数（D3）；B2 打断 ≤300 ms 正式数字；灯环音乐态肉眼确认；
C1 冷装。

**运维注意**：重启服务用 `>>` 追加且先 `mv` 轮转日志；用 `>` 截断会让新旧进程交错写同一
文件，日志时序不可信（08:47 踩过）。

**2026-10-23 上午补充（音量与小爱「哎」）**：

- **音量工具真正可用了**：① `TurnRunner.set_tools()`——晚注册的工具（音量/HA 六个）之前只在
  registry 里，模型根本看不到（`self.tools = tuple(tools)` 是拷贝）；② 音频面学习用户设定值，
  不再 16 s 后被对账回退；③ **音量词走确定性快路径**（「音量50%」「大一点」「静音」不进模型），
  修「模型口头答应但不调工具」；④ 音量确认后音乐**在答句排空的同一毫秒**恢复（`ReplyFinished.
  hold_music_s=0`，不用等 6 s 窗口）。
- **小爱「哎」时刻让位**：厂家最终转写要 3 s 后才到，但中间识别结果（`vendor_listening`）在唤醒
  后 ~0.7 s 就有——用它只做停音乐，不开 12 s 静默窗（避免吞掉我们自己的轮次）。9哥 实测通过。
- 教训：协议加字段必须同步改 `protocol.py` 的模型并加 CONTROL 往返测试——`hold_music_s` 漏了，
  音频面当场崩溃（已补测试）。

## 12. 2026-10-23 白天：V2 / HA 调试 / 控制台 P2–P3 / D2 完成

**代码**：HEAD 见 git log，**410 passed**，ruff clean，前端 `vue-tsc` + build 通过。
**部署目标**：**生产是 NAS 上的 Docker**（`docs/deploy.md`：`install.sh` 一条命令、`:8080`、
`network_mode: host`、配置在 `~/house-ear/config`）；Mac 只是开发机（`:8091`、dev 版 serve-dev.sh）。
C1 冷装验收在 NAS 上做。

**运行（开发机）**：`nohup /tmp/house-ear-scratch/house-ear-s5b/serve-dev.sh >> /tmp/house-ear-scratch/he-serve-b3t.log 2>&1 &`
（注意 `>>` 追加；重启前先 `mv` 轮转旧日志，用 `>` 截断会让新旧进程交错写同一文件，时序不可信）。
**acek**：`ack.mode: tts`「在呢。」；`devices[].volume: 86`（≈220 档）。

### 12.1 语音交互系统层 V1 ✅（`5821ccd`）
`PhraseBook`（全局话术表）+ `ToolResult`（status/payload）+ `InteractionSpec`（exec/choose/danger/filler/
answer/guard）+ `_call_tool` 渲染 + 轮级 degrade。音乐 9 工具全部迁移，AST 测试证明工具代码零中文字面话术。

### 12.2 HA-MCP 核心 ✅（`4e6cce6`）
`MCPClient`（streamable-HTTP 常驻/队列/指数重连）+ `HaBridge`（六工具白名单、实体清单剥状态、120 上限、
7 天缓存、lock/alarm/valve 调用路径硬拒、离线降级）+ `POST /api/v1/debug/tool`。儿童房主灯开/关/查状态实测。
**后续修复**：`b001091` 模型传裸 service（`turn_off`）时从 entity_id 推 domain、JSON 字符串实体列表；
`9292f96` 回复用实体名而不是 ID。

### 12.3 V2 三原语 ✅（`2859b08`）
- clarify：动词缺失问回（「儿童房的灯」→「要开还是关？」）、谎报检测（无工具调用却称成功 → 强制重试 → 如实说没执行）
- choose：同名多实体全部传入（主灯+筒灯一起开），不问用户
- confirm：lock/alarm/valve 继续硬拒；复述确认待有真实危险操作时再做

### 12.4 控制台 P2/P3 ✅（`c9d9fb6`/`4fbeedb`/`86576bb`）
PanelSpec 新增 `form`/`table`/`log`/`memory` 四种 block；面板：概览/设备/对话/日志/音乐/记忆/设置；
form 422 按字段标红、token 输入（401 自动提示）、日志 tail（2MB×3 轮转，带过滤与跟随）、对话 ring（50 轮）。

### 12.5 D2 记忆 + 上下文开关 ✅（`4e78a8a`/`582b0ee`/`8824113`）
`session/memory.py`（策展式：确认条目注入，候选待控制台确认；`data_dir/memory.json`）+
`tools/memory.py`（remember/forget/recall）+ `GET/POST /api/v1/memory` + 记忆面板；
`session.keep_context`：后台「设置→会话」开关 + 语音「关闭/打开上下文」确定性路径（落盘立即生效）；
`GET /api/v1/debug/prompt` 提示词预览。

### 12.6 今日其它修复（实测驱动）
- 唤醒不再启动空闲钟（空唤醒不再 15 s 后告别）；**每次唤醒清空 history**（老项目 `begin_session` 规则，
  修「答完天气又问周杰伦」）
- 点歌：模型参数不可信 → query 一律用用户原话（见过 `{}` 和上一轮 query 两种）；空/串台都根治
- 别名解析（`music/names.py`，模型只翻译、曲库做决定、别名持久化）+ 拼音容错（`session/pinyin.py`，
  简繁/同音/停止词韵母）+ 标点剥离（「…的歌。」不再拆出歌名「歌。」）
- 简短化：`MUSIC_HINT`（音乐在放时 ≤20 字、不介绍歌曲）+ 禁止复述旧轮次
- 「歌就是答案」：音乐工具 `answer="silent"`，点歌/换歌不念确认
- 停止词停音乐（含「退下吧」）；音量工具（真调 mixer + 学习为目标值 + 确定性词路径）
- 天气：一次自动重试（open-meteo 偶发空消息传输错误）+ 日志带异常类型
- 语言请求走流派池（J-Pop/K-Pop）优先于字面搜索

### 12.7 下一步（9哥 定）
1. **D2 完成后逐页复核控制台**（一页一页过，功能债与文案都在这一轮暴露）→ ✅ 2026-10-23 12:2x 完成，见 §12.8
2. 然后：**D1c 调研落地**（library_index 语言索引、semantic 描述式、停止词二分 media/bare、日报直通、
   点歌多候选）→ **C-UI P4–P6**（安装向导/设备添加/MCP-HA 管理页，功能定稿后才做，排在部署测试前）
   → **P15 生产化** → **验收日**（D3 20 次唤醒 + 灯环 + 控制台肉眼 + C1 冷装）→ E1
3. 真机仍需耳朵确认：让位 V4（已通过）、音乐话术、音量、留声/告别边界

### 12.8 2026-10-23 中午：控制台逐页复核完成 / D1c 阶段 1 / P15 进行中

**9哥 本轮指示**：他有事不能配合真机验收；**音箱可以随便用、可以出声**；继续开发、打磨、测试、
及时更新文档并 push；老项目的实测数据是可以参考的宝库。

**控制台逐页复核 ✅（Lead）**：14 条路由（8 面板 + 6 手写页）headless Chrome 逐页开图 +
真调每个 source/按钮，0 console error、0 4xx。修掉 11 项，明细与证据在 **`docs/console-audit.md`**。
最重要的四条：① `/panel/*` 深链/刷新返回 JSON 404（SPA 无 history fallback）→ 服务端 catch-all 修掉，
`/api/*` 仍 JSON 404；② 体检页路由 `/health` 被 API 同名端点抢走 → 移到 `/diagnostics`；
③ 「试一试」`POST /api/v1/debug/say` 与设备音量读写**只存在于已死的手写页面里**，界面上丢了 →
新增声明式 `say` / `volume` 两种 block，控制台恢复入口；④ 日志面板 41,839 字里 95% 是 2 s 一行的
`[level] voice peak` → log block 支持 `hide`，默认隐藏 264 行。
另：删除 4 个死页面（Assistant/Overview/Devices/Logs 的旧手写版）、2 个重复入口；
新增 `tests/test_panels.py` 9 条注册表契约测试（深链可开 / API 不入壳 / source 与 href 是真的 /
设置面板字段集合 / 日志隐藏模式）。

**真机验证（音箱已可出声，12:09–12:2x）**：① 试一试「现在几点」→ 1.81 s 返回
「现在是中午12点09分。」`audio_bytes=74240`、设备 owner `dialogue`、`playback_s` 2.7→3.5；
② 音乐 transport `playing→paused→playing→next(壹块钱)→idle`，真出声、队列 20；
③ 设备音量 `178→150→178`（已还原）。
**顺手修的环境坑**：dev 启动脚本没 pin PATH，重启后 `resolve` 每首歌报
`No such file or directory: 'ffmpeg'`（症状是「能点歌但全部 skip」）；`serve-dev.sh` 已加
`export PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH`。生产侧同一缺口由 P15 在
Dockerfile 里装 ffmpeg 解决。

**D1c 阶段 1 ✅（d1c agent，task-2）**：`music/library_index.py`（每首歌语言索引，标题优先、
kana/hangul 直判、汉字日文靠 override 表、7 天 TTL 原子缓存）+ `music/semantic.py`
（描述式点歌 = 语言/流派/关键词/情绪/年代取交集，空交集按情绪→流派→年代→关键词放宽，
**点歌路径不调 LLM**）+ `local.music_search` 候选通道 + `music_play` 带 `options`。
真实 Navidrome 实测：索引 **5736 首 / 12 请求 / 0.78 s / 1189 KiB**；「放几首日文歌」首次 790 ms
（含建索引）之后 1–3 ms；「来点安静的钢琴」4 首、「九十年代的粤语老歌」20 首；
「晴天」3 候选、「富士山下」5 版本。测试 456 passed。方案与风险 `docs/d1c-notes.md`。
**阶段 2（③停止词二分 ④日报直通）等 app.py 交接。**

**P15 进行中（p15 agent，task-3）**：compose 日志轮转（10m×3）、healthcheck 脚本、
`./data/models:/data/models` 第三挂载（原来 install.sh 的 fetch-models 写 `/data/models`，
compose 只挂 `./data:/var/lib/house-ear`，容器里路径根本不存在——真 bug）、`stop_grace_period`、
Dockerfile 加 ffmpeg（Navidrome 转码依赖）与 OCI labels/VERSION、install.sh 用 `--user uid:gid`、
新增 `.github/workflows/docker-publish.yml`（原来 CI 只 build 不 push，而 install.sh 默认拉
`ghcr.io/...:latest`——**镜像从来没有发布过**）、docs/deploy.md 重写 + docs/runbook.md 新建。
**P15 报告的 src 缺口（Lead 接下来做）**：① `AudioPlaneSupervisor` 只在启动时 start，plane 子进程
崩溃不会重建（Docker 也不会因 unhealthy 重启容器）→ 要加 watch/重启；② `fetch-models` 只下
SenseVoice+VAD，**KWS 唤醒模型和 keywords.txt 不在自动安装里**、`setup` 默认配置也没有
local_asr/vad/provider endpoint → 「一条命令到第一句」目前不成立（C1/P14 的硬缺口）；
③ `config/schema.py` 的 `secrets_path` 生产默认值带个人目录约定。

### 12.9 2026-10-23 下午：生产化 / D1c 收尾 / 冷装补齐 / 平面看门狗（已 push）

**HEAD `3fa2e2a`，487 passed，ruff clean，前端 typecheck+build clean。** 9哥 本轮不配合真机，
音箱可自由出声，全部自测。

**控制台（上午，§12.8）**：`c5c0b20` 逐页复核 + 11 项修复；`docs/console-audit.md`。

**AudioPlaneSupervisor 看门狗（`9c38a90`）**：plane 只在启动时 spawn 一次，崩了没人管
（Docker 不会因 unhealthy 重启容器）。新增 `watch()`：1 s 轮询、1→60 s 退避、成功即重置；
`PlaneHandle` 把回合回调（on_utterance/on_wake/on_vendor/on_playback_finished）在每次重连时
重新绑定——否则重连出来的 plane「健康但聋」。启动异常改为全捕获（设备配置错不能杀控制台）。
**真机实测**：`kill -9` plane pid → 1 s 内 running=false → **2 s 内新 pid（restarts=1）** →
音箱自动重连 → 一个 `debug/say` 回合 1.17 s 成功（78080 B）。单测覆盖 handle 重绑 + SIGKILL 重启。

**D1c ✅ 阶段 1/2A/2B（`4f57127`/`3b8946a`/`e8a821a`）**：
- 阶段 1：`library_index`（每首歌语言索引，真实库 5736 首/0.78 s）+ `semantic` 描述式点歌
  （「放几首日文歌」790 ms 首次、之后 1–3 ms；「来点安静的钢琴」4 首；「九十年代粤语老歌」20 首）
  + `music_search` 多候选（晴天 3 候选、富士山下 5 版本）。
- 阶段 2：停止词二分接线（放歌中=停歌不散场、没放歌=散场、退下吧放歌中先停歌）；
  日报直通（`Briefing` 原文播报、跳过模型第二轮、原文进 history 供「第三条再说说」，
  模型消息只含 notice）。真实 60s 日报实测：1 次 LLM round、9 个 TTS 片、reply==speech_text。
  **未验**：③ 的媒体分支真机听感（需要真唤醒，9哥嗓子）；④ 真机出声由 d1c 2C 补。

**冷装补齐（`7aacbd5`，P14/C1 的硬缺口）**：
- `setup` 不再写 schema 空壳，改写生产模板 `src/house_ear/config/template.yaml`
  （实测音频值、providers 三条链、VAD/KWS 模型路径、`devices: []`、无密钥），并**自动生成
  keywords.txt**（`n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞`，与生产文件逐字一致）。
- `fetch-models` 增加 **KWS 唤醒模型**下载解包（GitHub release 32.9 MB，chunk-8 文件，
  `--kws-dest /data/models/kws`、`--kws-url`、`--no-kws`）；`house-ear keywords --config`
  从 `devices[].wake_words` 重生成关键词文件（控制台/向导换唤醒词后不会留旧词）。
- **playback 默认 48000/2ch 是错的**：LX06 客户端只收 mono，adapter 会拒绝 → 默认改成
  16000/1ch（config.example 同步改）；`music.enabled` 无 `secrets_path` 现在校验报错，
  生产默认不再指向个人目录。
- 测试：`tests/test_install.py` 7 条（模板可加载/关键词逐字/换唤醒词/fetch 解包布局/
  playback 默认可播/music 凭据校验）。**「一条命令到第一句」从「不可能」变成「差 devices 一行」。**

**P15 生产化 ✅（`3fa2e2a`，p15 agent）**：compose 日志轮转 10m×3、config-aware healthcheck、
`./data/models:/data/models` 第三挂载（修 fetch-models 与 config 路径对不上的真 bug）、
stop_grace、Dockerfile 加 **ffmpeg 静态二进制**（音乐 resolve 依赖；容器内实测真实 Navidrome
resolve 8,581,722 B / 16k mono s16 / 268.2 s）、OCI labels/VERSION、国内镜像 build args、
**`.github/workflows/docker-publish.yml`（原来 CI 只 build 从不 push，而 install.sh 默认拉
ghcr:latest——镜像根本没发布过）**、`docs/runbook.md` 新建 + `docs/deploy.md` 重写。
实测：冷构建 91.8 s / 增量 49.5 s / 镜像 428,815,328 B（409 MiB，arm64）；容器 `/health` 1 s、
healthy 6 s；三类卷 down→up 全保留；SIGTERM 进程退出自动重启（HTTP 2 s / healthy 6 s）；
`docker kill -9` 视为手动停机不重启（已写 runbook）；哑设备与空 token 都保持 `/health` 200
（plane down）、不崩。**未验**：NAS 断电、真机重连时序、x86_64 NAS、3 天长稳。

**下一步**：
1. **P15b**（p15 进行中）：与 `7aacbd5` 对齐 install.sh/deploy.md/runbook（KWS 自动安装）、
   重建镜像验证看门狗自愈与 template 进镜像、公开仓清债（tests 内网 IP / 个人模型路径）。
2. **d1c 2C**：真机声音验证日报直通 + 音量 + 音乐工具（已授权）。
3. **Lead：C-UI P4–P6**（首次安装向导、设备添加/探测、MCP/HA 管理页）——功能已定稿，可以做。
4. **容器冷装排练**（本机 OrbStack 按 install.sh 全流程走一遍，含真 setup/fetch-models/health），
   然后再谈 NAS 上 C1（需要 9哥 点头动他的生产机）。

### 12.10 2026-10-23 傍晚：C-UI P4 安装向导（已 push）

**`/setup` 首次安装向导 + 5 个后端端点（`6197201` + 重定向修复 `9837191`）**：`GET /api/v1/setup`
返回进度（keys/models/device/wake/verify，全部由服务器上的 key、模型文件、config、keywords 文件
**计算**出来）；`POST /setup/keys`（write-only）、`/setup/device`（写 devices[] → 重生成 keywords.txt
→ 重启音频面；schema 默认配置没有 keywords 路径时落在 config 同目录）、`/setup/wake`、`/setup/probe`
（只做一次 SSH TCP 连接，不登录不下命令）。设备列表/health 改读 `app.state.adapters`，向导加的音箱
不重启控制面就能看到。前端是手写 Vue（`SetupView.vue`，正是 console.md 里逃生舱说的「顺序性交互」），
首装落在向导页（`/` 与 `/panel/overview` 两种拼写都覆盖——App 挂载时 router 可能还在解析重定向）。
隔离 :18090 实例（生产模板配置、无设备）实测：`/` → `/setup`、三步勾选状态随真调用翻转、
keywords.txt 内容逐字一致、probe 返回不可达原因、无音频面时「试一句」如实 409。
**未验**：向导里「模型」步只给命令（272 MB 下载不进 HTTP 回调）；真机端到端由冷装排练覆盖。
**P4.1 修三处诚实性问题（同日晚些）**：① key 步改为按配置里真实 provider 链的
`api_key_env/app_id_env/api_secret_env` 判断（dev 配置用 DEEPSEEK_*/SILICONFLOW_*/XFYUN_*，
原先硬编码 HOUSE_EAR_* 会把配好的 dev 判成「没填 key」）；本地 sherpa 条改看模型文件，
且接受 `model.onnx` 与 `model.int8.onnx` 两种命名。② 新增 `complete` 标志（前四步都完成）：
只有真未装好才强制首装落到向导页；「试一句」只是提示，不会让日常打开控制台永远跳向导。
③ 向导保存设备改为按 id 合并到已有设备上，`listen_port`/`led`/`volume` 等不在表单里的字段
不会被清空。dev 实测：`complete=true`、`/` 停在概览、`/setup` 四步全绿、音频面在线。

### 12.11 2026-10-23 傍晚：P15b 收尾 / 本机冷装排练 / 公开仓清债（已 push）

**HEAD `b259417`。** P15b（p15 agent）把部署面与 `7aacbd5` 对齐，并做了本机容器冷装排练：

- **install.sh**：step1/2 说明改成「setup 写生产模板 + 自动生成 keywords」；step4 一次拉
  ASR+VAD+KWS（老装缺 KWS 会补），`.env` 增加 `XFYUN_*` 并**写入 `HOUSE_EAR_IMAGE=$IMAGE`**
  （否则后续 `docker compose up -d` 会回落 ghcr:latest，把本地镜像顶掉）。
- **镜像内模板**：`resources.files('house_ear.config')/template.yaml` = 4804 B，provider 链齐全；
  `setup` 0.49 s（热）/ ~13 s（rebuild 后首次容器冷启），config 4912 B，keywords 50 B 逐字一致；
  `fetch-models --no-kws` 0.53 s 路径布局正确。
- **冷装排练（本机 install.sh 全流程，预置模型）**：总计 **3 s（热）/ 14 s（冷路径）** →
  `compose ps` Up(healthy)、`house-ear-healthcheck` ok、`/health` 200、doctor degraded 仅环境项。
  镜像 `house-ear:rehearsal` = **428,964,012 B（409 MiB，arm64）**，增量构建 40.5 s。
- **看门狗容器实测**：假设备 up 后 3 s `audio_plane=true`；kill plane pid → **2.0 s 新 pid、
  restarts=1、/health 全程 200、容器 RestartCount=0**；假音箱 ~1 s 重连；`debug/say` 返回
  `reply=现在三点。 audio_bytes=32000`、假音箱收到 15 帧/43,200 B。
- **`devices: []` 空转修掉（`5f4bc72`）**：看门狗在无设备时不再空跑退避，`watch()` 直接 idle；
  向导写设备的路径会重新 arm。
- **公开仓清债（同一个 commit）**：10 个测试文件的私网 IP → TEST-NET；4 个测试的作者模型目录改成
  `HOUSE_EAR_TEST_MODELS` opt-in（未设 skip）；measure 脚本 `--env`/`$HOUSE_EAR_ENV_FILE`；
  docs/scripts 里 `~/AI_Workspace`、`~/.hermes`、私有 checkout 引用全清；HANDOFF 写「公开仓」。
  验证：全量 pytest 绿、ruff clean、`sh -n install.sh` ok、安全 grep 全清。

**⚠ C1 阻塞项（需 9哥 决定）**：`raw.githubusercontent.com/yes999zc/house-ear/main/...` 目前
**404（仓库不可匿名访问）**，`install.sh` 的 `[1/5]` 拉不到 compose/config.example——一键冷装
在自己机器上排练时是手工预置文件绕过的。C1「一条命令装好」要求仓库公开（或给出带认证的分发路径），
这件事只能由 9哥 拍板。

**P4 后的向导复验（d1c 2D-A/B）与修复见 §12.10；P6 设计在 `docs/phrasebook-editor.md`（d1c），
Lead 已拍板：49 键全开/占位符硬拒绝/只交 pytest 场景契约；d1c 正在实现后端。**
**P4.2 修 d1c 复验剩余的 4 条（同一轮）**：① 手机未完成态 progress 里的「缺 key」长串把页面撑宽
（实测 innerW 454 / scrollW 857）→ 缺失项改成每 stage 一行、可断词；② 首装未完成时深链被偶发弹回
`/setup`（`main.ts` 在 `router.isReady()` 前就 mount，App 看到的是瞬态 `/`）→ 改成等首帧导航完成
再 mount，实测 `/panel/devices`、`/diagnostics` 等深链保持原地；③ `setup/keys` 接受非字符串 value
（会写进 `secrets.yaml: None` 并让 stage 谎报 true）→ 422；④ host 松校验（`http://…`、`-bad-`、
`[::1]`）→ 拒绝 URL/连字符开头结尾、`[v6]` 自动去括号，向导设备 id 统一小写。
验证：fresh 实例 390px 三页 overflow 0px、深链不弹回、`/setup` 无 console 异常；全量 pytest 绿、ruff clean。

