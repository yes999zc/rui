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
| S3 | speexdsp AEC（6 麦、4096 taps）+ KWS | ✅ 真机 ERLE 19.8–21.2 dB（**「按残差选一路」时代**，混音后未复测）；放歌 75 s 唤醒 18/18 |
| S4 | VAD 分段、回合编排（ASR→LLM→TTS→推流）、trace | ✅ 20 轮 20/20，首句 p50 3563 ms（无工具，11:xx 口径） |
| S5a | 唤醒应答三档、preroll 回放、回声窗口 | ✅ `docs/ack.md` |
| S5b | 回合质量（复读、抱怨、编造、标记、唤醒词尾巴） | ✅ `docs/turn-quality.md` |
| S5c | 停止词、短噪声、filler、自听守卫 | 🟡 15 s 超时 + 两档告别 + filler 预渲染代码已落地（`f200778`/`f814d6f`），`app.py` 正在接线（`0575b57` 已提交 1 s idle 轮询，**真机未验收**）；缺音乐类跳过 filler |
| S5d | 控制台（六页 Vue + 助手页 + 密钥页） | 🟡 旧项目 10 个分区尚未逐项对齐；`playback_lead_ms`/barge 数已上控制台（`348e77b`） |
| S5e | 时间注入、weather/news/web_search、工具循环 | 🟡 reminder/timer + 到点调度已落地（`f740819`），`app.py` 接线中，未真机验收 |
| S6 | 播报节奏（Pacer）+ 打断（barge-in） | ✅ 真机：队列 0 丢弃、打断丢 7.6 s（`4c4a69b`）；剩余 lead ≈420 ms 未按 300 ms 口径验收 |
| S7 | ASR 常驻 + filler 预渲染 | ✅ 真机 ASR 51–90 ms（原 500 ms + 每轮 4.7–5.3 s 加载），filler 0.73 s 预渲染（`f814d6f`） |
| S8 | 6 麦加权混音前端（`audio/frontend.py`） | 🟡 代码落地（`f814d6f`）；**真机唤醒灵敏度、ERLE 均未复测** |
| 真机欠账 | 厂家让位验证、打断停推 ≤300 ms、唤醒灵敏度 | ⬜ 都要 9哥 出声配合 |

**269 tests passed，ruff clean**（2026-10-22 13:28，HEAD `0575b57`）。今天的数字都在 `docs/audio-latency.md`。

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
   **这个差异没解释，属未解问题**（见 §6 第 5 条）。提交值 160 ms；下午工作树改成 320 ms
   （160 ms 填不满客户端 300 ms 启动缓冲，提示音会憋到答案一起出），未提交、未验收。
7. **「回声窗口」不是聋窗**：hold 期间到达的音频进缓冲（600 ms），闸门打开后灌进 gate。
8. **厂家唤醒 = 让位**（`session.vendor_quiet_s` 12 s）：说「小爱同学」时不开我们的会话、
   不播提示音；我们的唤醒词立刻接管。细节 `docs/vendor.md`。
9. **公开仓库硬约束**：不许硬编码私人信息（API key、Mac 路径、内网 IP、默认城市）。
   密钥只走环境变量或 `secrets.yaml`（0600、写-only），API 只回 `has_key`。
   审计：`grep -rn "/Users/\|192\.168\.\|sk-\|Bearer " src/ docs/ README.md config.example.yaml`。
10. **第二款音箱**只留 adapter 接口（`device/contract.py` + registry），真机调试留到 v1 发布后。

## 5. 与老项目的参数差（照抄清单 + 差距性质）

| 项 | 老项目生产值 | 我们（2026-10-22 下午） | 性质 |
|---|---|---|---|
| AEC | speexdsp MDF 4096 taps + **双讲残差>35% 停自适应** + 收敛闸 `erf<0.1*ep` | 4096 taps；无双讲/收敛判据；**ERLE 改在加权混音后的流上统计**，新值未测 | 差距；旧的 19.8–21.2 dB 是选一路时代，不可比 |
| 采集移位 | mic `>>12`、ref `>>16` | 同 | ✅ |
| 高通 | 90 Hz，麦与参考同拐点 | 同；**每通道始终推进**（放音暂停也不冻结），修掉切通道时 5 个滤波器带旧状态爆音 | ✅ 已对齐，且比老实现更稳 |
| 唤醒门限 | 0.20 + boost 2.0 + **探针 0.10 只记日志** | 0.20，无 boost、无探针 | 缺探针；前端换成混音后，门限仍是选一路时代的值，**必须重扫** |
| 阵列 | 6 麦按各自噪声底加权混音（`FLOOR_RATIO=1.5`），老项目实测均值比最好单路 **+1.6 dB** | 同款加权混音（`audio/frontend.py`）+ 放音时冻结权重 | 方法已对齐；我们的阵增益/唤醒率**未复测** |
| ack | tts「在呢」0.52 s，`drain=False` | earcon 160 ms（提交值）；工作树 320 ms、PEAK 0.05→0.12，未验收 | 未解差异，见 §6 第 5 条 |
| filler | 1.8 s，「我想想／我查一下／我看看」，**音乐类跳过** | 1.8 s + **启动预渲染 0.73 s 音频**（内存，不再现调讯飞）；不跳过音乐 | 时延已对齐（TTS 那段归零），音乐判断仍缺 |
| TTS 首字 | 讯飞 0.44–0.60 s | 861 ms（12:0x 真机，下午未复测） | 慢 ~0.3 s，原因未查 |
| ASR | sherpa SenseVoice int8，threads=2，N100 210 ms | 同模型，**常驻内存**；真机 **51–90 ms**（4 轮） | ✅ 反超；不再每轮加载 1.1 GB |
| 会话 | 超时 15 s + 两档告别 + end_words 11 个 | 代码同构（`session/idle.py`、`session/filler.py`），`app.py` 正在接线 | 接线未验收 |
| 历史 | history_rounds 6 | 同 | ✅ |
| 播报 | 连续 aplay 管道（背压）+ 句间 350 ms 真静音 + 只计真正写进 aplay 的块 | `Pacer`：目标 200 ms、上限 350 ms、2% 追帧；新指标 `playback_lead_ms`；队列无界 + 120 s 秒数预算 | 意图对齐（缓冲不见底），实现不同；设备侧缓冲未测 |
| 唤醒后端点 | `POST_WAKE_SILENCE = 1.0 s`，收完第一段回 0.5 s | 600 ms | 可能吃首字，未单独验 |

## 6. 已知问题（按严重度，全部有实测出处或明确写「未测」）

1. **唤醒灵敏度未复测**（6 麦加权混音 2026-10-22 下午上线后）。今天 KWS 命中正常
   （e 日志 13:22:28 一次播报中命中，直接触发打断），但没做距离/音量/唤醒率扫描；
   S3 的「放歌 75 s 唤醒 18/18」是「按残差选一路」时代。**「要很大声」是否缓解，没有数字。**
2. **打断后的剩余 lead ≈420 ms**。wake 时音频面清队列 + `stop_play`，但已交给设备的部分
   必须播完（Pacer 上限 350 ms + 设备侧缓冲）。实测这一轮丢排队 7.6 s（控制面记 7.7 s），
   剩余约 420 ms；**「停推 ≤300 ms」没有按这个口径量过**，设备侧 aplay/ALSA 缓冲深度
   也未实测（老项目自量 `PLAY_LAG_SECONDS = 0.55`）。
3. **ERLE 换了测点，没有新数字**。现在统计的是加权混音后的流（`audio/aec.py`，与老项目同口径），
   旧 19.8–21.2 dB 属「选一路」时代：既不能代表新实现，也不能直接和老的 21.8–32.0 比。
   AEC 仍缺双讲残差停自适应 + 收敛闸。
4. **会话超时/两档告别/到点提醒未真机验收**。`session/idle.py`、`session/filler.py`、
   `tools/reminders.py` 代码已落地，`app.py` 正在接线（`0575b57` 提交了 1 s idle 轮询）；
   15 s 超时、告别语、提醒到点出声都没在音箱上跑过。
5. **`ack.mode: tts` 与老项目结果相反**：老项目「在呢」0.52 s 正常；我们 S5a 实测 0/20 整句被吞。
   现在默认 earcon 绕开了它，但只要想把「在呢」做成默认，就得先解释这个差异
   （怀疑 `echo_ignore_ms=1200` + 门控比老项目粗）。
6. **音乐类跳过 filler 未做**：现在任何回合 1.8 s 后都会说「我查一下」，放歌/调音量类指令
   也会说。老项目按音乐意图跳过。
7. **`playback_lead_ms` 只进 Metrics/控制台，日志不打印**。今天判断「队列不再丢块」靠的是
   `speak queue full` 0 条 + 队列不再积压，没有逐轮 lead 抄表；回归要看控制台（`348e77b`）。
8. **ASR 对技术词不可靠**（「回声消除」→「回程校除」）；模型用反问兜住，行为可以但体验打折。
   `0575b57` 补了老项目的 0.8 峰值归一化（安静片段先抬到 0.8 再送解码），效果未单测。
9. **已修，值得记住**：短噪声成轮（`session/rules.is_noise`）、自听成轮（`SpokenMemory`）、
   **提示音期间的第二次唤醒曾被当成 barge**（`89c5dae`：只有回复/文件才算「正在说话」，
   cue 不算）。
10. **已修但值得记住的坑**：`system.log_level` 从未生效（无 `basicConfig`）；音频面 stderr 是
    没人读的 PIPE（64 KB 卡死子进程）；`history[-2*0:]` 等于整段历史；回复曾以 3 倍速播放
    （16 k PCM 当 48 k 播，只有耳朵能发现）；**回复队列按「块数」限长（maxsize=200）会截断整句**
    （当天日志 118 条 dropping）；**`asr_chain()` 每轮新建 provider 把 1.1 GB 模型加载算进每一轮**
    （每轮 4.7–5.3 s 死气）；**filler 现调 TTS 把首字时延叠在 1.8 s 等待上**。

## 7. 下一步（按收益排序，每步要实测数字）

| # | 事 | 需要 9哥 出声 | 预期收益 |
|---|---|---|---|
| A | 唤醒探针 + 门限扫描（0.60/0.45/0.35/0.25/0.15 + 探针 0.10），同时记距离与命中率（§6.1） | **是**，3 分钟 | 给混音前端定门限；分清「门限高」vs「音频差」 |
| B | 打断停推实测：设备侧 aplay 缓冲 + `playback_lead_ms` 抄表，目标 ≤300 ms（§6.2） | 是（喊一次打断） | 把 420 ms 压到 300 ms 以内 |
| C | 会话/告别/提醒真机验收：15 s 超时、两档告别、到点出声（§6.4） | 是 | 关闭 S5c/S5e |
| D | ERLE 复测（混音后）+ AEC 双讲残差 + 收敛闸（§6.3） | 否（可台架） | 拿回可比的 ERLE，自听/打断更稳 |
| E | 音乐类跳过 filler（§6.6） | 否 | 放歌时少一句废话 |
| F | 长回答 lead 上限听感验证（350 ms 够不够，§5 播报行） | 是 | 防长播报欠载复发 |
| G | S5d 控制台对齐老项目 10 个分区 | 否 | 可配置性 |
| H | 厂家让位验证 | **是** | 验收闭环 |

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

**真机切换**（LX06，`root@<LAN-IP>`，密码 `<redacted>`，必须
`-o HostKeyAlgorithms=+ssh-rsa`；dropbear 限流，被拒等 40–60 s）：

1. 先起 house-ear（`listen_port: 4412`；旧服务占着 4399/4400）。
2. 备份 `client-run.sh`、`server.txt` 为 `*.bak-houseear`，URL 改成
   `ws://<LAN-IP>:4412/?token=<TOKEN>`，kill 客户端，等看门狗 30 s 拉起。
3. `lsof -nP -iTCP:4412` 看到 `<LAN-IP>` ESTABLISHED 才算连上。
4. **测完立刻还原**两个 `.bak-houseear`，kill 客户端，确认 `:4399` ESTABLISHED。

**注意**：SSH 命令里不要出现 `pkill -f "/data/open-xiaoai/client"` 这种字面量——它会匹配到
自己的 ssh 命令行，把会话打断。用 `pkill -f 'open-xiaoai/clien[t]'`。

凭据在 `~/.hermes/.env`（`DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、`XFYUN_*`）。
**只写变量名，不写值。**

## 9. 现场状态（2026-10-22 13:28）

- 代码：`0575b57`（13:26）是最后提交；**269 tests passed / 0 failed / 0 skipped，ruff clean**
  是 13:28 的读数。13:28 之后工作树又出现 4 个未提交文件（`audio/chime.py`、`audio/process.py`、
  `config/schema.py`、`tests/test_ack.py`，本轮 ack 实验：PEAK 0.05→0.12、提示音 160→320 ms、
  cue 不再按实时节奏送），未复跑测试、未验收。
- 今天下午的提交：`bf568c5` Pacer（200/350 ms、2% 追帧）、`f200778` 会话超时 + 告别 + filler 时长、
  `f740819` reminders + 到点调度、`348e77b` 控制台显示 lead/barge、`f814d6f` provider 链常驻 +
  filler 预渲染 + 6 麦加权混音、`4c4a69b` 打断落地、`89c5dae` 提示音期间二次唤醒不算打断、
  `0575b57` 会话收尾接线 + ASR 0.8 峰值归一化 + 设备操作只回一句。
- 今日实测（`docs/audio-latency.md` §1/§3）：上午首响 p50 5617 ms（改造前 11553 ms）；
  下午 ASR 51–90 ms（原 500 ms + 每轮 4.7–5.3 s 加载）、filler 预渲染 0.73 s、
  回复队列 dropping 118 条 → 0 条、打断丢排队 7.6 s。
- 音箱：今天 5 次起停都用 scratch 配置，设备口 **4399**（`allow_no_token`，仅内网）、控制台 8091；
  `<LAN-IP>` 在 e 日志结束（13:22:36）时仍连着。**下次开工先确认 4399 上跑的是哪个进程**
  （§8 的 4412 是发布配置，今天没用）。注意 §2/§8 写的是「不占旧项目 4399/4400」，
  今天实际起在 4399，和契约不一致——先对齐配置再起，别把两套服务混在同一个端口上。
- 未提交：本次 docs 两处（按约定不 add/commit）；另有上条列出的 4 个 src/tests 文件，不是本次改动。
- 下一步第一件事：**A（唤醒探针 + 门限扫描）** —— 前端换了加权混音，门限还是「选一路」时代的值。
