# 真机验收累计清单（acceptance-pending）

> 用途：发版前 9 哥 一屏看完「哪些必须上手、怎么验、验过算什么」。
> 维护约定：**只增不改语义**；验过的条目把 ☐ 改成 ☑ 并追加日期与证据位置，不重排、不删号。
> 本清单的条目来自 `docs/audit/unverified-inventory.md`（**只引用，不改它**）、task 卡与 `docs/待拍板.md`。
> 环境占位：`<BASE>` = 控制面地址、`<TOKEN>` = 控制台 token、`<ID>` = 设备 id、`<wav>` = 指令 wav 目录；
> `acceptance-v1.py` 从环境变量取 token：先 `export RUI_TOKEN=<token>`（与 `X-Auth-Token` 同值）。
> 命令默认在 `house-ear/` 根目录用 `uv run python scripts/...` 执行（`docs/audit/unverified-inventory.md` §6 的执行环境约定）。
> 真机窗口定义：9 哥 到场、LX06 与 Dot2 各一台在手、手机可录音、≥1 h 连续操作。
> **0.7.9 不测；以下全部并入 0.8 窗口**（0.7.9 只做代码/文档收口；窗口安排见 `docs/待拍板.md` D-002）。
> **例外（2026-10-28）**：**N1 已在生产真机验证**（00:54，见该行）→ 已勾；其余条目仍并入 0.8 窗口。

**21 条「只能真机」索引（A 14 + B 3 + C 1 + D 3）**；另附 1 条补充 HE-01 + 5 条本阶段新增 + 6 条 0.7.9 新增（不计入 21）。
A：X-01, X-02, X-04, X-05, X-06, HE-04, HE-05, HE-16, D2-05, D2-07, D2-08, D2-10, D2-11, D2-13
B：D2-01, D2-03, D2-12
C：X-07
D：HE-06, HE-07, HE-08

> fleet 模式（多平面）：`/api/v1/audio` 的指标在 `.planes["<ID>"].metrics.*`；单平面在顶层 `.metrics.*`。下面 D2-07/D2-11 的采样命令按单平面写。

> 不在 21 条内的三项（`环境缺失`，见 inventory §8.4）：X-03 需 4 台同网设备；HE-15 需中文 OWW 模型先训练；D2-09 需 SPL 计/THD 仪器/1 h 台架。它们不计入下面的窗口估算。

## A 组：1 m 单机协议（14 条，一个 4–5 h 窗口清完）

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☐ | X-01 | 音量五档回读与 cap 边界 | 对 V=51/102/153/204/255 各发一次：`curl -sS -X POST "$BASE/api/v2/devices/$ID/actions" -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"action":"volume.set","args":{"value":V}}'`；随后同一 URL 发 `-d '{"action":"volume.read","args":{}}'`（或 v1 `PUT /api/v1/device/volume`，body 带 `device`） | 默认（cap 未生效）回读 `== V`；cap<100 时回读 = `percent_to_device_volume(min(device_volume_to_percent(V), cap))`（允许设备取整）；5 档无失真/跳变 | Dot2 + LX06 各一遍；耳朵 | inventory X-01、§6 |
| ☐ | X-02 | LED 灯序与截图 | 对 wake/listening/thinking/error 各发一次：`curl -sS -X POST "$BASE/api/v2/devices/$ID/actions" -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' -d '{"action":"led.set","args":{"effect":"wake"}}'` | 灯序与 `tests/test_led.py` 一致；每态截图留档 | 两台设备；相机 | inventory X-02、§6 |
| ☐ | X-04 | LX06 语音指令成功率 | `uv run python scripts/measure_turns.py --url "$BASE" --command-dir <wav> --rounds 5 --out /tmp/lx06-turns.jsonl`（6 句） | 6 句 × 5 轮 **≥26/30** | LX06；6 句 wav；人声 | inventory X-04、§6 |
| ☐ | X-05 | LX06 唤醒与打断延迟 | `uv run python scripts/acceptance-v1.py wake --url "$BASE" --trials 20 --criterion 0.95`；`... barge --url "$BASE" --trials 3 --auto-say` | 唤醒 **≥19/20**；stop-push **≤300 ms** | LX06；安静窗口 | inventory X-05、§6 |
| ☐ | X-06 | Dot2 四个矩阵同分母重采 | `uv run python scripts/measure_turns.py --url "$BASE" --command-dir <6 句 wav 目录> --rounds 5 --out /tmp/fleet.jsonl`；`uv run python scripts/fleet_matrix.py --log "$LOG" --json`；每个数字带 `--log` 行号 | 30/30、26/30、8/8、10/10；**同分母、同源校准音源**；每个数字可追到日志行 | Dot2；校准音源；日志 | inventory X-06、§6、§8.5 |
| ☐ | HE-04 | 首声可闻延迟 | `uv run python scripts/measure_turns.py --url "$BASE" --command-dir <5 句 wav 目录> --rounds 5` + 手机录音（或 Mac 内置麦克风：`ffmpeg -f avfoundation -i ":0" -t 120 /tmp/mic.wav`） | 给出 **P50/P95**（记录值，不设门） | 设备；手机录音（或 Mac 麦克风）；安静房间 | inventory HE-04、§6 |
| ☐ | HE-05 | 3–4 m 远场唤醒 | `acceptance-v1.py wake --url "$BASE" --trials 10 --label far-field --no-gate` | **只记录** 10 次远场结果，不判 PASS/FAIL | 3–4 m 距离；正常说话音量 | inventory HE-05、§6 |
| ☐ | HE-16 | 音乐播放中短指令 | `measure_turns.py --command-dir <音乐中短指令 wav> --rounds 10` | **10/10**；音乐不断、指令不丢 | 设备；正在播放的音乐 | inventory HE-16、§6 |
| ☐ | D2-05 | Dot2 primeWait 与卡顿 | 配 `system.log_level: debug`，`cd rui-dot2-bridge && docker compose -f deploy/downstream/docker-compose.yml logs --since 30m \| grep primeWait`（每轮 10 次） | `primeWait` **14–22 ms**；无 1 s 异常；人耳无卡顿 | Dot2；debug 日志窗口 | inventory D2-05、§6 |
| ☐ | D2-07 | Dot2 AEC ERLE | `uv run python scripts/measure_erle.py --config <cfg> --device dot2 --file <粉噪 wav> --seconds 600 --json`；同时每 5 s 采一次 `curl -s -H "X-Auth-Token: $TOKEN" "$BASE/api/v1/audio" \| jq -r '.metrics.aec_erle_db'`（采满 10 min） | metrics `aec_erle_db` **P50 ≥20 dB**（目标 25）；脚本 JSON 的 `per_channel[].erle_all_db`/`erle_second_half_db` 作旁证 | Dot2；粉噪源；安静房间 | inventory D2-07、§6 |
| ☐ | D2-08 | 房间 RT60 | `sox -n -r 48000 -c 1 /tmp/sweep.wav synth 10 sine 20 20000`；手机录音；按 `docs/aec-device-tuning.md` 公式算 | 记录每个房间 RT60（假设 0.3–0.5 s） | 待测房间；手机；sox | inventory D2-08、§6 |
| ☐ | D2-10 | Dot2 唤醒 / barge / 资源 | `uv run python scripts/acceptance-v1.py wake --url "$BASE" --trials 20 --criterion 0.95`；`... barge --url "$BASE" --trials 3 --auto-say`（读 `summary.median_ms`）；`docker stats --no-stream`；声学 P50 用手机录音对齐（选做） | 唤醒 **≥19/20**；barge `summary.median_ms`（server stop-push）**≤300 ms**；RSS 记录；声学 P50 记录（选做） | Dot2；20 次唤醒窗口 | inventory D2-10、§6 |
| ☐ | D2-11 | Dot2 AEC ref_active 与播放中 ERLE | `uv run python scripts/measure_erle.py --config <cfg> --device dot2 --file <粉噪 wav> --seconds 300 --json`；同时每 5 s 采 `curl -s -H "X-Auth-Token: $TOKEN" "$BASE/api/v1/audio" \| jq -c '{ref_active:.metrics.ref_active, erle:.metrics.aec_erle_db}'` | 采样期间 `ref_active=true` **且**播放时 `aec_erle_db` **>10 dB**；脚本 `chain_erle_db` 作旁证 | Dot2；粉噪源 | inventory D2-11、§6 |
| ☐ | D2-13 | 静音基线 / 长回复 / 距离 | `uv run python scripts/measure_erle.py --config <cfg> --device dot2 --file <粉噪 wav> --seconds 300 --json` ×3；`uv run python scripts/measure_turns.py --url "$BASE" --command-dir <wav 目录> --rounds 5 --out /tmp/d213.jsonl`；长回复/逐距离各 5 次 | 静音基线 3×5 min；长回复无噪声；距离逐档记录 | Dot2；多距离点位 | inventory D2-13、§6 |

## B 组：部署 / 兼容（3 条，2–3 h，含烧镜像与回滚）

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☐ | D2-01 | 下游镜像构建 + 设备回连 | `cd rui-dot2-bridge && docker build --platform linux/amd64 -f deploy/downstream/Dockerfile .`；`uv run python ../house-ear/scripts/acceptance-v1.py wake --url "$BASE" --trials 5 --criterion 0.8`（required=4）；`curl -sS -X POST "$BASE/api/v1/debug/say" -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' -d "{\"text\":\"测试\",\"device\":\"$ID\"}"`（播放；双引号让 `$ID` 展开）；`uv run python ../house-ear/scripts/acceptance-v1.py barge --url "$BASE" --trials 1 --auto-say`；设备重连后 `curl -sS -H "X-Auth-Token: $TOKEN" "$BASE/api/v2/devices/$ID" \| jq '{connected,status}'` | 构建/运行成功；wake **≥4/5**；`debug/say` **1/1 出声**；barge **1/1** 且 `summary.median_ms` ≤300 ms；token 断开重连后 **60 s 内** `connected=true` 且无 401（`speaker_rejected` 不增长） | Dot2；镜像构建窗口；`canary-0.6.0-official-image.md §八` 清单 | inventory D2-01、§6 |
| ☐ | D2-03 | install.sh 一次性容器 | `bash deploy/install.sh`（一次性容器）→ `curl -fsS "$BASE/health" -H "X-Auth-Token: $TOKEN" \| jq`；控制台逐页点一遍 | 设备链路 ready；`device_errors=0`；无 5xx | 一次性容器；Dot2 | inventory D2-03、§6 |
| ☐ | D2-12 | plug 契约 + TLS/4412 | `uv run python -m rui plug-test <plug dir> --adapter <adapter.py>:build --json`；`openssl s_client -connect <device>:8870 -servername <host> -brief` | 契约全 PASS；TLS hostname/4412 握手成功；未知消息进 debug 不崩 | Dot2（0.2.2 固件）；TLS 设备 | inventory D2-12、§6、§8.5 |

## C 组：耳听主观（1 条，0.5 h，可搭 A 组窗口）

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☐ | X-07 | Dot2 EQ/限幅盲听 | `cd rui-dot2-bridge && python tools/eq_report.py --json`（客观）；盲听 3 首、随机顺序、5 分制 | 客观指标不劣化；盲听 **≥4/5** 且无失真 | 9 哥 耳朵；音箱；3 首曲目 | inventory X-07、§6 |

## D 组：服务端 / 网络 / 长稳（3 条，两次 0.5 h + 一次过夜）

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☐ | HE-06 | NAS 断电自启 | 断电重启 NAS；每 30 s `curl -fsS "$BASE/health" -H "X-Auth-Token: $TOKEN" \| jq '{status,audio_plane}'`，共 10 次 | **≤5 min** 内 200 且 `audio_plane=true`，无需人工 | 生产 NAS；≤5 min 中断窗口 | inventory HE-06、§6、§8.5 |
| ☐ | HE-07 | Wi-Fi 抖动 / IP 变化重连 | 断 AP / 改 IP；每 5 s `curl -sS -H "X-Auth-Token: $TOKEN" "$BASE/api/v2/devices/$ID" \| jq '{connected,status}'` | **<10 s** 回到 `connected=true / status=ready`；重试 **≤1** | 可控 AP/路由器；设备 | inventory HE-07、§6 |
| ☐ | HE-08 | 24 h 长稳 | 每 300 s `docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}'`；`curl -fsS "$BASE/health" -H "X-Auth-Token: $TOKEN" \| jq '{status,audio_plane,restarts:[.audio_devices[]?.restarts]}'`；另记 `curl -s -o /dev/null -w '%{http_code}' "$BASE/health"` | 24 h **0 5xx**、`audio_devices[].restarts` 无增长（**0 plane 重建**）、RSS 斜率 **<1 %/h** | 过夜窗口；监控落 CSV | inventory HE-08、§6 |

## 补充（不计入 21）：HE-01 真机告别

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☐ | HE-01 | 告别后不再自问自答（D5） | `uv run python scripts/measure_turns.py --url "$BASE" --command-dir <告别 wav 目录> --rounds 10 --out /tmp/goodbye.jsonl` | 告别后 **0 个额外 turn**；`reply_finished` 生效（日志） | 设备；10 次告别 wav；10–15 min | inventory HE-01、§6 |

## 本阶段新增（task-14 / 控制台改序）

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☑ | N1 | 有歌说结束词：停歌 **且** 关会话 | 放歌 → 说「停止」；再放歌 → 说「拜拜」；再放歌 → 说「停止播放」；每句后等 3 s 再说任意一句 | 每次音乐停止，**且**后续那句不被接（会话已关；日志 `session_action` 为 **`stop_and_dismiss`**，可 `grep stop_and_dismiss`） | 设备 + 可控音乐；一次 5–10 min | 已合入 `ddb7cbb`、`docs/audit/intent-collisions.md` C1；**已验 2026-10-28 00:54（生产真机）**：`stop intent: '停止播放。' -> media stopped, session closed`（旧行为 `session open`）——本次证据为「停止播放」一句，「停止」「拜拜」未单独留证 |
| ☐ | N2 | `停止你好` 不再误命中 | 空闲时说「停止你好」；有歌时再说一次 | 不进入停止分支（落模型/忽略）；会话不关、音乐不停 | 设备；2 min | 已合入 `ddb7cbb`、intent-collisions C2 |
| ☐ | N3 | 「暂停 / 停一下」只暂停、会话保持 | 放歌 → 说「暂停」；说「停一下」恢复再暂停；无歌时说「暂停」 | 有歌：音乐 paused、会话仍开；**无歌：答「现在没在放音乐。」且会话保持** | 设备 + 音乐 | 已合入 `34e3629`（C3/C5）、9 哥 策略 1/4 |
| ☐ | ACK-1 | 唤醒应答在语音助手页**第 3 组、首屏可见** | 进 `/v2/voice-assistant`，不滚动，看第 3 组即「唤醒应答」；改文本/提示音 → 保存 → 下一次唤醒；旧面板「音频与唤醒」tab 应为第 1 组 | v2 第 3 组首屏可见且可改；旧面板第 1 位；保存后下一次唤醒生效 | 控制台 + 设备 | 已落地（本卡 `ack order 200→5`）、`docs/audit/console-ordering.md`「落地记录」 |
| ☐ | ACK-2 | 改序后硬核组仍在页尾 | 同上页面，滚到底 | `audio.capture/voice/playback/aec` 依次在最后 | 控制台 | 已落地（本卡）、console-ordering「落地记录」 |

> N1/N2 已合入 `ddb7cbb`（**N1 已于 2026-10-28 00:54 真机验证**）；N3（C3/C5）已合入 `34e3629`；ACK-1/2 已由本卡落地（`catalog.py` ack order 5）。以上**除 N1 外**仍需真机确认，未验前不要当回归失败。

## 0.7.9 新增待验（6 条，并入 0.8 窗口）

| ☐ | ID | 一句话 | 怎么验（可照抄） | 通过标准（可判定） | 需要什么 | 来源 |
|---|---|---|---|---|---|---|
| ☐ | R79-1 | dot2 音量回读取 mixer 实际值 | 对 V=51/102/153/204/255 发 `volume.set`，随后同 URL 发 `volume.read`（同 §A 的 X-01 命令） | 回读 == 当前 mixer 实际值（不再落后一档）；5 档无跳变 | dot2；耳朵 | `d2c9895` |
| ☐ | R79-2 | 「唱首歌」走随机播放 | 连说 3 次「唱首歌」（每次等开播再下一次）；点一个旧版收藏链接 | 3 次不固定同一首；旧链接落到提示页而不是死链 | 设备 + 音乐 | `e85b4bb` |
| ☐ | R79-3 | 唤醒打断与告别 | `acceptance-v1.py barge --url "$BASE" --trials 3 --auto-say`；`measure_turns.py --url "$BASE" --command-dir <告别 wav> --rounds 10` | barge `summary.median_ms` ≤300 ms；告别后 **0 个额外 turn** | 设备；10 次告别 wav | `1c5583a`、`f7b2484`、inventory HE-01 |
| ☐ | R79-4 | 停止与暂停（C1+C2+C5） | 有歌说「停止」「拜拜」「停止播放」；另说「先停这儿」「停会儿」「停住」「停在这」；再说「暂停」「停一下」 | 结束词 → 停歌且关会话（`stop_and_dismiss`）；四个旧暂停词**不再暂停**（落模型）；「暂停/停一下」→ pause | 设备 + 音乐 | `ddb7cbb`、`34e3629` |
| ☐ | R79-5 | C3 无歌暂停提示 | 无歌时说「暂停」 | 回复「现在没在放音乐。」；会话保持（不关） | 设备 | `34e3629` |
| ☐ | R79-6 | D-011 排序生效 | 进 `/v2/voice-assistant` 不滚动；再开旧面板「设备与音频 → 音频与唤醒」 | v2 第 3 组即「唤醒应答」且可改；旧面板第 1 组；两者保存后下一次唤醒生效 | 控制台 + 设备 | 本卡、`docs/audit/console-ordering.md`「落地记录」 |

## 推荐执行顺序（按窗口分组）

| 顺序 | 窗口 | 覆盖条目 | 预计耗时 | 一次能清 | 备注 |
|---|---|---|---|---|---|
| 1 | **A 组窗口**（1 m 单机） | X-01, X-02, X-04, X-05, X-06, HE-04, HE-05, HE-16, D2-05, D2-07, D2-08, D2-10, D2-11, D2-13；顺带 HE-01（补充）、N2 | **4–5 h** | **14 条 + 补充 HE-01**（含 N2 约 +10 min；**N1 已于 2026-10-28 真机验证，不再排**） | 两台设备都要在场；X-04/X-05/HE-16 偏 LX06，X-06/D2-05/07/08/10/11/13 偏 Dot2；D2-10 的 20 次唤醒最久 |
| 2 | **A+C 延伸**（同一窗口加耳听） | X-07 | **+0.5 h** | **+1 条（累计 15）** | 需要 9 哥 在场盲听；可与窗口 1 合并 |
| 3 | **B 组窗口**（部署/兼容） | D2-01, D2-03, D2-12 | **2–3 h** | **3 条** | 含烧镜像/回滚；D2-03 用一次性容器；D2-12 TLS 约 0.5 h |
| 4 | **D 组现场**（服务端/网络） | HE-06, HE-07 + HE-08 起跑 | **1 h**（各 0.5 h） | **2 条 + HE-08 起跑** | HE-06 需要 ≤5 min 生产中断窗口；HE-07 需要可控 AP/路由器 |
| 5 | **D 组读数**（过夜） | HE-08 | 起跑后 **24 h** | **1 条** | 次日读 CSV/健康检查即可，不需 9 哥 守夜 |
| 6 | **控制台**（已落地） | ACK-1, ACK-2, R79-6 | **5–10 min** | **3 条** | 排序已生效；可与任意窗口合并 |

**总量**：21 条 = 一个 4–5 h 窗口（14 条）+ 0.5 h 耳听（1 条）+ 2–3 h 部署窗口（3 条）+ 1 h 现场与 24 h 过夜（3 条）。只有一台设备在手时，A 组按设备拆成两个 2–2.5 h 半窗，总数不变。

## 验收记录表（复制到发版验收）

| 日期 | 设备/房间 | 距离/音源 | 条目 ID | 命令/说话序列 | 分母 n | 结果 | 判据 | 日志/JSON/截图 |
|---|---|---|---|---|---|---|---|---|
| 2026-10-28 | 生产容器（日志未记设备/房间） | — | N1 | 说「播放一首周杰伦的青花瓷。」→ 好，放《青花瓷》→ 说「停止播放。」 | 1 | `media stopped, session closed`（旧行为 `media stopped, session open`） | 停歌 + 关会话（C1）；该日志行只由 `session_action="stop_and_dismiss"` 分支打印（`src/rui/web/api/app.py:1018`） | 生产日志 2026-10-28 00:54:16 / 00:54:35 |
