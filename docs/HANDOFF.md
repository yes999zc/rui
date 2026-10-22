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
| S3 | speexdsp AEC（6 麦、4096 taps）+ KWS | ✅ 真机 ERLE 19.8–21.2 dB；放歌 75 s 唤醒 18/18 |
| S4 | VAD 分段、回合编排（ASR→LLM→TTS→推流）、trace | ✅ 20 轮 20/20，首句 p50 3563 ms（无工具） |
| S5a | 唤醒应答三档、preroll 回放、回声窗口 | ✅ `docs/ack.md` |
| S5b | 回合质量（复读、抱怨、编造、标记、唤醒词尾巴） | ✅ `docs/turn-quality.md` |
| S5c | 停止词、短噪声、filler、自听守卫 | 🟡 已做；缺 15 s 超时 + 告别语 + 音乐类跳过 filler |
| S5d | 控制台（六页 Vue + 助手页 + 密钥页） | 🟡 旧项目 10 个分区尚未逐项对齐 |
| S5e | 时间注入、weather/news/web_search、工具循环 | 🟡 已接；缺 reminder/timer + 到点主动出声 |
| 真机欠账 | 厂家让位验证、打断 300 ms | ⬜ 都要 9哥 出声配合 |

**186 tests passed，ruff clean**。最新提交 `1b8b0e3`。今天的数字都在 `docs/audio-latency.md`。

## 4. 已定决策（别推翻，改前先问 9哥）

1. **ASR 本地优先**：sherpa SenseVoice int8，threads=2（N100 210 ms）；云只做 fallback
   （0.38–2.5 s，还有 20 s 超时）。
2. **TTS 讯飞主用**：`x4_lingxiaowan_boy`，硅基 `charles` 兜底。硅基单独用首字节 3.7–21.6 s，
   不能用在语音回合。
3. **LLM 纯 API**：`api.deepseek.com` / `deepseek-flash` / temp 0.4 / max_tokens 3000 /
   **thinking 关**（老项目实测：开思考延迟翻 2–3 倍、零准确率收益）。
4. **模型不进镜像**：`house-ear fetch-models` 从 hf-mirror 拉，失败只警告、ASR 回落云端。
5. **唤醒冲突先到先得**（200 ms 窗口，平局按 device id）；播放走服务端解码 + PCM 推流。
6. **唤醒应答默认 160 ms 提示音**（`ack.mode: earcon`）。理由是我们自己的实测：TTS 应答
   （0.55 s 语音 + 1.2 s 压麦）在 S5a 里 0/20 整句被吞。老项目用「在呢」0.52 s 是通的，
   **这个差异没解释，属未解问题**（见 §6.5）。
7. **「回声窗口」不是聋窗**：hold 期间到达的音频进缓冲（600 ms），闸门打开后灌进 gate。
8. **厂家唤醒 = 让位**（`session.vendor_quiet_s` 12 s）：说「小爱同学」时不开我们的会话、
   不播提示音；我们的唤醒词立刻接管。细节 `docs/vendor.md`。
9. **公开仓库硬约束**：不许硬编码私人信息（API key、Mac 路径、内网 IP、默认城市）。
   密钥只走环境变量或 `secrets.yaml`（0600、写-only），API 只回 `has_key`。
   审计：`grep -rn "/Users/\|192\.168\.\|sk-\|Bearer " src/ docs/ README.md config.example.yaml`。
10. **第二款音箱**只留 adapter 接口（`device/contract.py` + registry），真机调试留到 v1 发布后。

## 5. 与老项目的参数差（照抄清单 + 差距性质）

| 项 | 老项目生产值 | 我们 | 性质 |
|---|---|---|---|
| AEC | speexdsp MDF 4096 taps + **双讲残差>35% 停自适应** + 收敛闸 `erf<0.1*ep` | 4096 taps，**无**双讲/收敛判据 | 差距 → ERLE 19.8–21.2 vs 老项目 21.8–32.0 |
| 采集移位 | mic `>>12`、ref `>>16` | 同 | ✅ |
| 高通 | 90 Hz，麦与参考同拐点 | 同 | ✅ |
| 唤醒门限 | 0.20 + boost 2.0 + **探针 0.10 只记日志** | 0.20，无 boost、无探针 | 缺探针 → 无法区分「门限高」还是「音频差」 |
| 阵列 | 6 麦各自 AEC 后按残差选一路（无波束成形） | 同 | 都没有阵增益（≈0 dB），这是「要大声」的物理原因 |
| ack | tts「在呢」0.52 s，`drain=False` | earcon 160 ms | 未解差异，见 §6.5 |
| filler | 1.8 s，「我想想／我查一下／我看看」，**音乐类跳过** | 1.8 s，不跳过 | 缺音乐判断；且我们实时合成（+0.4–0.9 s） |
| TTS 首字 | 讯飞 0.44–0.60 s | 861 ms（2026-10-22 真机） | 慢 ~0.3 s，原因未查 |
| 会话 | 超时 15 s + 两档告别 + end_words 11 个 | 只有 `vendor_quiet_s` | S5c 剩余 |
| 历史 | history_rounds 6 | 同 | ✅ |
| 播报 | 连续管道 + 句间 350 ms 真静音 + 只计真正写进 aplay 的块 | 每句一次 `speak()`，块间按实时时长 sleep，**无预缓冲** | **卡顿根因**，见 `docs/audio-latency.md` |
| 唤醒后端点 | `POST_WAKE_SILENCE = 1.0 s`，收完第一段回 0.5 s | 600 ms | 可能吃首字，未单独验 |

## 6. 已知问题（按严重度，全部有实测出处）

1. **播报过程中卡顿**（9哥 2026-10-22 体感）。根因在 `audio/process.py:_speak_loop`：
   每块只按自身时长 `sleep`，**零预缓冲**、精确 1.0× 实时 —— 任何调度抖动都让设备缓冲见底。
   修法：句首预缓冲 300 ms（`due = now + prebuffer`）+ 1.02× 追帧；老项目的等价物是
   连续 aplay 管道（背压）+ 句间 350 ms 静音。详见 `docs/audio-latency.md`。
2. **问句到 filler 之间空档大**（同上体感）。filler 是实时合成：1.8 s 等待 + 0.4–0.9 s TTS
   = 实际 2.2–2.7 s 才出声。修法二选一：filler 走 ack 同款预渲染缓存；或 `filler_after_s`
   降到 1.2 s。老项目也是实时合成，所以它同样 2.2 s 起——但它 TTS 更快。
3. **唤醒要很大声**。结构原因：6 麦只「选一路」，阵增益 ≈0 dB（波束成形能拿 4–6 dB）；
   叠加 ERLE 比老项目低 3–10 dB。先做探针分清「门限 vs 音频」，再谈波束成形。
4. **打断（barge-in）未实现**：播报中喊「你好小瑞」KWS 命中、新回合会跑，但旧播报不停，
   与新回答重叠约 3 s。开口到新回答首音 4563 ms（KWS 1.3 + ASR 0.48 + LLM 0.92 + TTS 1.5），
   与「停推 ≤300 ms」是两个数，验收要分开量。
5. **`ack.mode: tts` 与老项目结果相反**：老项目「在呢」0.52 s 正常；我们 S5a 实测 0/20 整句被吞。
   现在默认 earcon 绕开了它，但只要想把「在呢」做成默认，就得先解释这个差异
   （怀疑我们的 `echo_ignore_ms=1200` + 门控比老项目粗）。
6. **提醒/定时/到点主动出声未做**。注入时间已在提示词里（零延迟），写时间是工具（未做）。
7. **ASR 对技术词不可靠**（「回声消除」→「回程校除」）；模型用反问兜住，行为可以但体验打折。
8. **短噪声曾成轮**（「你好小瑞」+ 轻轻「嗯。」→「嗯，你说。」）**已修**：`session/rules.is_noise`。
9. **自听成轮**（我们自己的答案被麦克风听到又答一遍）**已修**：`session/rules.SpokenMemory`
   （最近 12 句、10 s 窗、相等/包含判定，照老项目 `_is_self_echo`）。
10. 已修但值得记住的坑：`system.log_level` 从未生效（无 `basicConfig`）；音频面 stderr 是
    没人读的 PIPE（64 KB 卡死子进程）；`history[-2*0:]` 等于整段历史；回复曾以 3 倍速播放
    （16 k PCM 当 48 k 播，只有耳朵能发现）。

## 7. 下一步（按收益排序，每步要实测数字）

| # | 事 | 需要 9哥 出声 | 预期收益 |
|---|---|---|---|
| A | 播报预缓冲 + 追帧（§6.1） | 否 | 消除卡顿 |
| B | filler 预渲染缓存 / 或降到 1.2 s（§6.2） | 否 | 问完到人声 2.5 s → 1.3 s |
| C | 唤醒探针 + 门限扫描（0.60/0.45/0.35/0.25/0.15 + 探针 0.10） | **是**，3 分钟 | 分清门限 vs 音频 |
| D | AEC 双讲残差 + 收敛闸 + 参考缩放排查 | 否（可台架） | ERLE 21 → 28+，自听/打断更稳 |
| E | 6 麦延迟求和波束成形 | 否 | +4–6 dB 阵增益，根治「要大声」 |
| F | S5c 剩余：15 s 超时、两档告别、音乐类跳过 filler | 否 | 会话完整性 |
| G | S5e 剩余：reminder/timer + 到点主动出声（需设备写入锁与排队） | 否 | 新能力 |
| H | S5d 控制台对齐老项目 10 个分区 | 否 | 可配置性 |
| I | 真机欠账：厂家让位、打断 300 ms | **是** | 验收闭环 |

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

## 9. 现场状态（2026-10-22 12:0x）

- 代码：`1b8b0e3` 已推 origin/main；186 tests passed；ruff clean；web typecheck/build 通过。
- 今日新增：停止词/短噪声不进模型、自听守卫（SpokenMemory）、filler 1.8 s、
  weather 经纬度 + 5 分钟缓存、ack 预渲染（`audio/ack_render.py`）、**LLM 流式边读边切**。
- 今日实测（详见 `docs/audio-latency.md`）：首响 p50 11553 → 5617 ms；首句入队 511 ms；
  weather 工具 3.4 → 0.9 s；模型侧裸测 29 字 / 912 ms（31.8 字/秒）。
- 音箱：`4412` 连着 house-ear（本次会话结束时仍是），**下次开工先确认是否已还原到 4399**。
- 未提交：无。
- 下一步第一件事：**A（播报预缓冲）** —— 卡顿是 9哥 现在最直接的体感问题。
