# HANDOFF — house-ear

> ## 动手之前（强制顺序，别跳）
>
> 1. **`~/AI_Workspace/01_Code/projects/xiaoai-voice-entry/docs/design-and-measurements.md`**
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
MIT，私有仓 `yes999zc/house-ear`（main），本地 `~/AI_Workspace/01_Code/projects/house-ear`。

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
cd ~/AI_Workspace/01_Code/projects/house-ear
uv run pytest && uv run ruff check src tests scripts
cd web && npm run typecheck && npm run build && cd ..

# 一整回合（假音箱，真 KWS/VAD/ASR/LLM/TTS；TTS 不发声）
uv run python scripts/measure_turn_pipeline.py \
    --config ~/.hermes/cache/scratch/house-ear-s5b/tools.yaml \
    --command ~/.hermes/cache/scratch/house-ear-s4/commands/01-weather.wav --rounds 20

# 纯文本质量（真 ASR + 真 DeepSeek，20 轮同句）
uv run python scripts/measure_reply_quality.py --question "今天天气怎么样" --rounds 20

# 起服务（真机；:4412）
set -a; . ~/.hermes/.env; set +a
export HOUSE_EAR_DEVICE_TOKEN="$(cat ~/.hermes/cache/scratch/house-ear-s4/device-token.txt)"
uv run house-ear serve --config ~/.hermes/cache/scratch/house-ear-s5b/tools.yaml   # 控制台 :8091
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

凭据在 `~/.hermes/.env`（`DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、`XFYUN_*`）。
**只写变量名，不写值。**

## 9. 现场状态（2026-10-22 21:05）

- 代码：HEAD `3f8a93c` fix(echo): close the follow-up gate before our own audio sounds；
  **340 passed**，ruff clean。让位 V1 待真机复验；自言自语修复待真机复验（见下）。
- **自言自语（9哥 20:16 报告，已修 `3f8a93c`）**：空闲告别语播进上一轮 reply 留开的 follow-up gate，
  回声过 AEC 残余 → VAD 收 3.1 s 段 → ASR 把告别语转成 `'没事的话，我先下吧。'`（啦→吧 一字之差绕过
  精确回声兜底）→ LLM 答了自己的告别。每次自答重置空闲钟 → 每 ~15 s 再来一次，是否触发取决于
  VAD 起始 vs gate 到期的 ~30 ms 竞态。修复两层：① plane 在自己发声前 disarm armed 的 gate
  （正常轮次 gate 本来就是关的，零变化；音乐恢复播放也不再开 follow-up 窗口，否则歌词必成幻影指令）；
  ② `is_echo` 加 0.75 模糊匹配。**复验判据**：闲置 15 s 出告别语前后，日志应有
  `gate: disarmed, our own audio is about to sound`，且告别语之后**不应**再出现 transcript 为告别语的
  turn；放歌时应出现 `follow-up gate stays shut: music owns the speaker`。
- **起服务（正确方式，2026-10-22 20:14 起在用）**：`nohup ~/.hermes/cache/scratch/house-ear-s5b/serve-dev.sh
  > ~/.hermes/cache/scratch/he-serve-b3t.log 2>&1 &`。脚本自己 grep `~/.hermes/.env` 取 6 个 provider key，
  **`set -a` 是关键行**——没有它 eval 出来的只是 shell 局部变量，子进程一个 key 都看不到（20:09–20:12
  事故根因）；`HOUSE_EAR_DEVICE_TOKEN` 故意不设（dev 配置走 `allow_no_token`，音箱启动脚本发不了 token，
  设了反而把音箱拒之门外）。重启先按端口杀 4399/8091。
- **设备**：`$SPEAKER_IP`，SSH 用老项目 `server/devshell.py`（凭证 `~/.hermes/.secrets/lan_media.json`
  的 `voice.device_host/device_ssh`，自带 ControlMaster 防 dropbear 限流）；客户端日志 `/tmp/client.log`。
- **B2 第一阶段（19:52–19:54，真机）**：① 端点 600 ms（live 配置就是 600，schema 默认 1000）——
  「帮我查一下（停 1 s）明天北京的天气」**整句保留**，未切后半句（1 样本）；② 连续会话「明天呢」免唤醒
  追问成功，灯效 wake→thinking→speaking→listening 全对；③ **厂家让位失败**：小爱答天气时说你好小瑞，
  小爱继续说了 11 s，且我们的 ASR 把小爱的话转成幻影指令（`'摄氏度到17摄氏度。'`）→ V1+V3 修复
  `3b4648e`（唤醒时若小爱在播 → 老项目的 mphelper ubus pause 停它），**复验被 20:09 运维事故作废，
  未验证**。
- **下一步**：9哥 重测 ③（小爱同学问天气 → 小爱开口后说你好小瑞问时间；判据日志两行
  `vendor takeover: pausing` + `vendor pause (wake takeover): before=1 after=2`，听觉判据小爱马上闭嘴、
  只有小瑞答）→ 第二阶段放歌打断+220 档远处唤醒（`POST /api/v1/debug/play`
  `{"file": "~/.hermes/cache/scratch/music-test.wav", "loop": true}`，音量已 220）→ C1 冷装 → D1 音乐。
