# 未验证项清单（三仓，2026-10-26 基线 0.7.2）

> 来源：`README.md` / `docs/**` / `CHANGELOG.md` 三仓共 165 处标记命中，去掉误报（`n/a`=不适用、
> `未覆盖`=配置继承/页面/构建参数、`未确认`=删除确认）后 **36 条真实声明**。
> 本清单是 0.7.3–0.7.8 的测试补齐输入（对应 ROADMAP §3.1 / §3.5 / §3.6）。
> 规则：每条必须有终态——`CI 测试`（给测试名）、`真机协议`（给命令 + 判据 + 记录表）、或
> `不修（写明原因）`。**不允许保留「未测」二字而无归属。**

## 0. 统计

| 分类 | 条数 | 归属版本 |
|---|---|---|
| CI 可测（pytest，不需要设备/镜像） | 6 | 0.7.3 batch-1（3 条：HE-03 / HE-12 / D2-06）、0.7.7 batch-2（3 条：HE-13 / HE-14 / HE-17） |
| 环境/流水线协议（docker 构建、计时、soak；不是 pytest） | 4 | 0.7.3 记录基线、0.7.7 补协议 |
| 真机/人工协议（需要设备或耳朵） | 26 | 0.7.4 的 Done 6、0.7.8 手册与 release notes |
| 其中已有部分 CI 夹具、只缺真机腿 | 9 | 0.7.3 补夹具，真机腿进 0.7.8 |
| 真机项分类：`可转测试`（主判据可无头 pytest 判定） | 2 | HE-01 / HE-02；候选测试见 §8.2 |
| 真机项分类：`只能真机`（设备/耳朵/声学） | 21 | 分组与单窗口覆盖估算见 §8.3；D2-12 的 CI 半见 §8.2 附注 |
| 真机项分类：`环境缺失`（缺设备/仪器/模型） | 3 | X-03（4 台未到）/ HE-15（缺自训模型）/ D2-09（缺仪器）；见 §8.4 |
| 真机项分类合计 | **26** | = 2 + 21 + 3，与 §2 逐条标注一致；边界项见 §8.5 |

## 1. CI 可测（batch-1 → 0.7.3；batch-2 → 0.7.7）

| ID | 声明 | 位置 | 现状 | 现有测试 | 变成什么测试 | 批次 |
|---|---|---|---|---|---|---|
| HE-03 | `first_response_ms` 常为 null，「证据不足」 | `docs/analysis/dot2-vs-xiaoai.md:45` | CI 测试 | `tests/test_he03_first_response_null.py`（5 条；`pytest -q tests/test_he03_first_response_null.py`） | 固定 trace fixture 回放：null 只计 skipped、不补 0；生产端 `play_start` 上报已正确（filler 不计），无需改代码 | 1 |
| HE-09 | 源码全量镜像构建 + 镜像体积（431 MB）未验证 | `docs/release/v0.7.0.md:40,55`、`HANDOFF` §「风险与未决」 | 待补 | 无（`docker-publish` 只走薄层） | CI 干净 checkout `docker build` → 断言构建成功 + `docker image inspect` 体积记录 | 流水线 |
| HE-10 | 每台设备 RSS/CPU（1 vs 2 plane）未测，「线性增长」是断言 | `audit/2026-10-code-health.md:40,250` | RSS 未测 | 无 | fake-plane soak + `docker stats` 采样，断言斜率阈值 | 环境协议 |
| HE-11 | boot → healthy 秒数未验证（只有文档 85 s） | `code-health:415,479`、`runbook:406` | 未验证 | 无 | CI：限时启动容器 → `/health` 200，3 次取中位 | 环境协议 |
| HE-12 | 源码运行路径（`plugs/` fallback，无镜像插件）未验证 | `code-health:487` | CI 测试 | `tests/test_he12_source_plug_fallback.py`（2 条；`pytest -q tests/test_he12_source_plug_fallback.py`） | 合成 `plugs/` 源树 + fake adapter：fallback manifest 授权 in-process 注册，且同名 user plug 不能覆盖 builtin 来源 | 1 |
| D2-02 | canary：amd64 未验证；`read_only`/`user 1000:1000`/`host` 组合未验证 | `dot2 docs/canary-0.6.0-official-image.md:252,254` | 未验 | 无 | `--platform linux/amd64` smoke + compose 组合矩阵 job | 流水线 |
| HE-13 | `RUI_ADAPTER_PLUGINS` 语法：`attr` 未校验、告警无上限、env 可替换 builtin、旧拼写未测 | `docs/socket-plug-impl-audit.md:615-633` | untested | `test_adapter_plugins.py`（部分；legacy 用例被删） | 表驱动 `_parse`：非法 attr / 100 条 / override 告警 / `HOUSE_EAR_*` fallback | 2 |
| HE-14 | 并发、非法值 fuzz、多设备、sidecar、下游进程路径未测；`test_audio_plane.py:67` 有 jitter flake | `socket-plug-impl-audit.md:709-729` | untested | `test_plug_contract.py`、`test_device_wake.py`（部分） | frame/text fuzz 表；可注入时钟；放宽 jitter 断言 | 2 |
| HE-17 | 「配置加载 + 恢复 未测」 | `code-health:406` | 未测（实为**耗时**） | `test_config_recovery.py`、`test_config.py` **已存在** | 测 `load_with_recovery` 耗时预算（不是重写功能测试）；同步改文档措辞 | 2 |
| D2-06 | `stop_playback` 在活跃流上 flush 可能吞掉下一条回复 | `dot2 docs/stutter-adapter-options.md:391` | CI 测试 | `rui-dot2-bridge/tests/test_d2_06_stop_playback_flush.py`（2 条；`cd rui-dot2-bridge && .venv/bin/python -m pytest -q tests/test_d2_06_stop_playback_flush.py`） | fake 设备建模 `discarding`/`eosPending`：play → stop（flush+EOS）→ 立即 play，断言首个 4096 B PCM 送达、0 丢帧 | 1 |

## 2. 真机 / 人工协议（26 条）

> 每条都要有：命令、设备与房间、距离、音源与电平、判据、记录表。0.7.8 前至少完成 **Done 6 相关**的
> 4 条（唤醒/指令/播放、音量、多设备、24h 初查），其余写进「已知限制」并给协议。
>
> **task-5（2026-10-27）**：本表 26 条已在「分类（task-5）」列逐条标注
> `可转测试` / `只能真机` / `环境缺失`；统计、候选测试与分组见 **§8**。分类不改变任何条目的
> 「已验证/未验证」状态（处置待 D-005 拍板）。

| ID | 声明 | 位置 | 现状 | 判据 | 需要 | 分类（task-5） |
|---|---|---|---|---|---|---|
| X-01 | 音量控制从未系统验证（两台设备） | 三仓 README | 未测 | 设 5 档（v2 action `volume.set` args `{"value":V}`，V=1–255；或 v1 `PUT /api/v1/device/volume` body `{"value":V,"device":"<id>"}`）→ `volume.read` 回读一致、cap 处不失真 + 耳听 | 真机 | `只能真机` — 判据是两台设备的回读与失真；控制面映射只能补单测 |
| X-02 | 指示灯未测 | 三仓 README | 未测 | 唤醒/思考/告别序列与 `tests/test_led.py` 顺序一致（拍照留证） | 真机 + 照片 | `只能真机` — 灯序已有 test_led 覆盖，缺的是真实灯珠颜色/时序照片 |
| X-03 | 多设备并发未测（音量/LED/4 台） | 三仓 README、`release/v0.7.2.md:57` | 未测 | `acceptance-4devices.md` §5.3/§7 表格填满、0 串台、0 `lost_same_word`；任一 null = 失败 | 4 台（设备未到） | `环境缺失` — 缺 4 台同网设备（未到）；2 台填不满 §5.3 表格 |
| X-04 | LX06 语音指令成功率未统计 | house-ear README:110、xiaoai README:91 | 未测 | 6 句 × 5 轮 ≥26/30（同 Dot2 口径） | 真机 + 人声 | `只能真机` — 需 LX06 + 人声，6 句 × 5 轮 |
| X-05 | LX06 唤醒 19/20、打断 205.2 ms 是 2026-10-24 历史，无 0.7.2 重测 | xiaoai README:75,89,90 | 历史实测 | `acceptance-v1.py wake --trials 20` ≥19/20；`barge --trials 3` stop-push ≤300 ms | 真机 | `只能真机` — 需 LX06 真机重采唤醒/打断 |
| X-06 | Dot2 矩阵（30/30、26/30、8/8、10/10）测于 0.7.1/p21，0.7.2 未重测 | house-ear README:105、dot2 README:88 | 未重测 | 同分母重采；每个数字带 `--log` 行号 | 真机 + 校准音源 | `只能真机` — 需 Dot2 + 与历史同源的校准音源重采四个矩阵 |
| X-07 | EQ/限幅只过测量，没过耳朵 | dot2 README:98、`HANDOFF` §「风险与未决」 | 无听测 | 盲听 A/B：3 首、5 分制、随机顺序、≥4/5 且无失真 | 9 哥 耳朵 | `只能真机` — 盲听 A/B 必须人耳（9 哥） |
| HE-01 | D5 告别自回声误开 turn：加了 `reply_finished` 但仍「待验证」 | `docs/plan-to-0.6.md:21` | 仍待验证 | 把告别 PCM 当 mic 输入注入 → 0 额外 turn；真机 10 次告别 | CI 部分 + 真机 | `可转测试` — 告别 PCM 可回灌为 mic 输入并断言 0 额外 turn（§8.2） |
| HE-02 | cue 回声在「冷启动+不削波+lag>800 ms」或「重叠<110 ms」仍可能漏 | `release/v0.7.2.md:53`、`HANDOFF` §「已知限制（0.7.2 发布时点）」 | 已知限制 | 门禁测试补 lag 900/1200 ms、overlap 50 ms；真机 A/B | 真机 | `可转测试` — lag/overlap 极值是合成 PCM 参数，现有 gate 夹具可直接覆盖（§8.2） |
| HE-04 | 首声可闻延迟从未声学测量 | `docs/audio-latency.md:154` | 未测 | mic 录音 + `play_start` 时间相关，5 轮给 P50/P95（暂不设门） | 真机 + 录音 | `只能真机` — 首声可闻延迟要 mic 录音与物理时间对齐 |
| HE-05 | 3–4 m 唤醒距离从未尝试 | `docs/acceptance.md:99` | 未做 | `acceptance-v1.py wake --trials 10 --label far-field`（只记录） | 真机 | `只能真机` — 3–4 m 距离必须真实房间与设备 |
| HE-06 | NAS 断电自启未测 | `docs/runbook.md:173,403` | 推断 | 真断电一次：5 min 内 `/health` 200 + `audio_plane=true`，无需人工 | NAS | `只能真机` — 需生产 NAS 断电维护窗口（≤5 min 中断） |
| HE-07 | 真机重连时序（Wi-Fi 抖动/IP 变化）未测 | `runbook:404`、`device-management-research.md:973` | 未测 | 断开 AP / 改 IP → `speaker connected` <10 s、重试 ≤1 | 真机 + 路由器 | `只能真机` — 需可控 AP/路由器断流或改 IP |
| HE-08 | 24h/3 天长稳未满窗 | `release/v0.7.0.md:37` | 未满窗 | 0 5xx、0 plane 重启、RSS 斜率 <1 %/h | 真机 | `只能真机` — 24 h/3 天时间窗 + 真机运行 |
| HE-15 | 中文 OWW 模型效果未知；中文 onnx stem 未试 | `plan/speaker-tuning.md:448,454` | 效果待测 | 每变体 20 次唤醒 ≥19/20，否则砍功能 | 真机 + 自训模型 | `环境缺失` — 缺自训中文 OWW 模型/中文 onnx stem |
| HE-16 | 音乐中 2 m 短指令未测 | `HANDOFF` §「task-19 真机 A/B」 | 未测 | 10 次 ≥10/10（同 1 m 口径） | 真机 | `只能真机` — 音乐播放中的 2 m 语音采集必须真实声场 |
| D2-01 | canary 0.6.0：无真机，唤醒/播放/AEC/音量/token 未验证 | `dot2 docs/canary-0.6.0-official-image.md:251` | 未验证 | 接设备跑 wake/play/barge + token 重连 | 真机 | `只能真机` — 需一台 Dot2 + canary 0.6.0 arm64 镜像上机 |
| D2-03 | 0.2.2/0.3.4 部署从未验证设备链路；`install.sh` 未重跑 | `dot2 docs/deploy-0.3.4-user-report.md:296-299` | unverified | 设备侧部署验收 + headless 控制台点一遍 + 一次性容器重跑 `install.sh` | 真机 + 容器 | `只能真机` — 核心是设备链路；install.sh 可在一次性容器复跑但不覆盖设备侧 |
| D2-05 | `primeWait 14–22 ms` 未验证；那一轮没声音、没听测 | `stutter-adapter-options.md:390` | 未验证 | 日志断言 primeWait + 人工听测清单 | 真机 | `只能真机` — primeWait 需设备 debug 日志 + 人耳听测 |
| D2-07 | 设备 AEC 上限 20–24 dB 是估计；127 需要 31 dB | `aec-device-tuning.md:182,512` | 未实测 | 粉噪 10 min，`aec_erle_db` P50 ≥20 dB（目标 25） | 真机 | `只能真机` — 需 Dot2 + 粉噪源实测 ERLE |
| D2-08 | 房间 RT60 0.3–0.5 s 是假设 | `aec-device-tuning.md:84` | 未实测 | 扫频测 RT60，只记录 | 真机 | `只能真机` — RT60 是房间声学属性，必须现场扫频录音 |
| D2-09 | 绝对 SPL / +29 dB 增益失真与寿命 / #630 一致性未测 | `audit-device-audio.md:19,21,275` | 未测 | SPL 计 1 m；增益扫描 + THD + 1 h 耐久；第二台 A/B | 真机 + 仪器 | `环境缺失` — 缺 SPL 计/THD 声学分析仪与 1 h 耐久条件 |
| D2-10 | M1 门禁：严格 20 次唤醒待补；声学 barge 待测；RSS 未暴露 | `milestone-m1.md:21,22,26` | 待补 | 20 次 ≥19/20；mic 测静音 P50 ≤600 ms；`docker stats` | 真机 | `只能真机` — 20 次唤醒 + 声学 barge + 设备 RSS 采集 |
| D2-11 | 固件是否真的给 AEC 参考未验证 | `p3-firmware-patch.md:163` | 未验证 | 设备端 2ch 采集：`ref_active=true` 且播放时 ERLE >10 dB | 真机 | `只能真机` — 需设备端 2ch 采集确认 ref_active/ERLE |
| D2-12 | 0.2.2 兼容只做过静态分析（TLS hostname/4412、`oww_wake` 形状、trust 自定义） | `rui-0.2.2-dot2-compat.md:576-586` | 未实测 | 设备 TLS/端口实测；未知消息进 debug 分支的单测；定 trust 策略 | 真机 + CI | `只能真机` — 主判据是设备 TLS/4412 与 0.2.2 固件行为；未知消息/trust 只是次要子句（§8.2 附注） |
| D2-13 | 真机第一轮遗留：音量适配、长回复噪声、仅唤醒/多轮/距离、2 min 基线太短 | `real-machine-round-1.md:51-53` | 待补 | 静音基线 3×5 min；长回复听测；逐距离各 5 次 | 真机 | `只能真机` — 静音基线/长回复/逐距离都要真实声场 |

## 3. 文档矛盾（0.7.3 终态，逐条已落地）

> 引用约定：`docs/HANDOFF.md` 一律用**段落锚点**（文件顶部会不断插入新快照，绝对行号必然漂移）；
> 其他文件仍用 file:line，因为它们的插入点稳定。

| # | 矛盾（原位置） | 判定（谁对） | 改动（本卡工作树，file:line） | 未改/备注 |
|---|---|---|---|---|
| 1 | `code-health:406`「配置加载 + 恢复 未测」vs 已有 `tests/test_config_recovery.py` | 功能测试**已存在**；真正缺的是**启动耗时**测量 | `docs/audit/2026-10-code-health.md:408` → 「功能已测（…）；**耗时**未测」 | 保留「耗时未测」；耗时预算归 HE-17（0.7.7），不是再写一遍功能测试 |
| 2 | READMEs「0.7.2 未重测」vs `HANDOFF` §「三仓 README 的分仓口径」把同批数字挂在 v0.7.2 下 | **README 对**：数字来自 0.7.1/p21 采样（2026-10-26 15:01） | `docs/HANDOFF.md` §「三仓 README 的分仓口径」加「0.7.1/p21 实测；0.7.2 未重测」；§「README 四项评审结论」标完成；README 矩阵原本已正确（house `README.md:105`、dot2 `README.md:88`） | 不改历史 commit/发布记录；不改 HANDOFF 其他轮次记录 |
| 3 | dot2 README「volume 未测」vs `milestone-m1:23`「音量/响度 ✅」 | 两句都对但**语义不同**：README 指**控制端点**，m1 指**响度/EQ** | `rui-dot2-bridge/README.md:97,211` 限定为控制端点；`rui-dot2-bridge/docs/milestone-m1.md:23-24` 改为「响度/EQ（不是控制端点）」「音量回读（控制端点）」 | 未改 m1 的 ✅ 结论（A/B 证据仍有效） |
| 4 | `socket-plug-v1.md:309`（旧文）说 plug-test 子进程 vs `plug-test-kit.md:22` in-process | **kit 文档/代码对**：in-process import，无子进程 | `docs/socket-plug-v1.md:387` 写死「kit 在调用方进程内 import `--adapter`（in-process，不是子进程/容器/沙箱）」 | `socket-plug-v1.md:268` 仍保留否定式「不启动子进程」（讲 adapter 入口，不是 kit）；本卡只固定 kit 执行模型措辞，不改 268。代码证据：`src/rui/plugins/contract.py:1065-1130`（`importlib` 进程内加载） |
| 5 | barge 指标 stop-push ≤300 ms vs acoustic P50 ≤600 ms；README 的 8/8、205.2 ms 未注明判据 | **两个指标都保留**：8/8 与 205.2 ms 是 **stop-push**（server 停播→设备丢队列），不是声学端到端 | `README.md:111,248`；`rui-dot2-bridge/README.md:95,209`；`rui-xiaoai-bridge/README.md:90,197`；`docs/HANDOFF.md` §「三仓 README 的分仓口径」全部加「stop-push / 非声学端到端」 | acoustic P50 ≤600 ms 仍是 m1 验收项（`milestone-m1.md:22`），不与 stop-push 合并 |
| 6 | 测试总数 1512 / 1518 / 1571 三口径 | **以 0.7.3 四计数为准**：`1651 / 1634 / 17 / 0`（collected/passed/skipped/xfail；见 `docs/audit/coverage-baseline.md`） | `docs/audit/2026-10-code-health.md:23,474,550`；`docs/HANDOFF.md` §「README 截图与稿件的当前状态」；本文件 §3 本条 | `docs/release/v0.7.0.md:25,39` 是 0.7.0 发布历史快照、`docs/ROADMAP-0.8.md:66` 是当时的任务描述，均不在本卡写范围，保留原数字；新文档一律引用 baseline |
| 7 | xiaoai `CHANGELOG` 把 19/20、205.2 ms 放 `### Verified` 下，README 却写 0.7.2 未复测 | **README 对**：19/20、205.2 ms 是 2026-10-24 历史实测 | `rui-xiaoai-bridge/CHANGELOG.md:15-23`：新增 `### Historical measurements (2026-10-24, not re-tested)`；`### Verified` 只留 9 项契约测试 + 未测项 | 数字本身不改；只移动归属并补 stop-push 判据 |
| 8 | canary「wake/playback/AEC/volume 未验证」vs dot2 README 30/30 | **不同轮次，两者都对**：canary = 0.6.0 官方镜像、arm64、无真机（2026-10-26）；README = rui 0.7.1/p21 真机轮次（2026-10-26 15:01） | `rui-dot2-bridge/docs/canary-0.6.0-official-image.md:249-252` 标注轮次/范围/不可互证；`rui-dot2-bridge/README.md:89,203` 反向加注 canary 日期与「无真机」 | 未改 canary 的「未验证」清单（该轮次确实未测） |

## 4. 与 0.7.8 验收的关系

- **必须做完**（进 Done 6 / 手册）：X-01 音量、X-03 多设备（至少 2 台）、HE-06 断电自启、HE-08 24h 初查、D2-11 固件参考（若设备在）。
- **明确不做，写进「已知限制」**：X-02 LED 照片、X-07 EQ 耳测（等 9 哥）、HE-05 远场、HE-15 中文 OWW、D2-07/08/09 声学仪器项。
- **CI 可测项不留到 0.7.8**：batch-1 在 0.7.3 完成，batch-2 在 0.7.7 完成；0.7.8 只出最终覆盖率报告。

## 5. 覆盖率与未覆盖清单（0.7.3）

### 5.1 口径

- 基线文件：`docs/audit/coverage-baseline.md` / `docs/audit/coverage-baseline.json`（本机 macOS + Python 3.13.13，
  行 **80.95%**、分支 **69.55%**；命令 `uv run pytest --cov=rui --cov-branch --cov-report=term-missing`）。
- CI artifact 实测（commit `d21bb5f`，task-1 时点，尚未包含 task-2 新增测试）：Python 3.11 = **76.59%**、3.12 = **77.59%**，120 files。
  跨平台/解释器有差，引用时以「哪个 artifact + 哪个 Python」为准；本表引用 0.7.3 最终本机基线（`cd2d0a2`），不混用 CI 数字。
- 每个一级子包的行/分支数字与 covered/total 均来自基线 JSON；「未覆盖行」= statements − covered_lines。

### 5.2 每包数字 + 缺口主类

| 子包 | 行 % | covered / statements | 分支 % | 未覆盖行 | 缺口主类 | 说明 / 0.7.7 输入 |
|---|---:|---:|---:|---:|---|---|
| `(root)` | 82.94 | 851 / 1026 | 71.03 | 175 | 可单测未写 | `__main__.py` 101、`healthcheck.py` 27、`setup_state.py` 22；无 batch-2 对应，进 0.7.8 后 backlog |
| `audio` | 80.72 | 3248 / 4024 | 65.60 | 776 | 可单测未写（主）+ 需真机子集 | `process.py` 436、`client.py` 100、`kws.py` 81；fake-plane/时钟注入归 **HE-14**；ALSA/真实 KWS/AEC 走 §6 真机协议 |
| `channels` | 52.81 | 461 / 873 | 35.64 | 412 | 可单测未写 | `weixin.py` 119、`telegram.py` 113、`cli.py` 107；无 batch-2 卡，进 0.7.8 后 backlog |
| `config` | 94.05 | 1139 / 1211 | 86.22 | 72 | 可单测未写 | `store.py` 21、`catalog.py` 20、`recovery.py` 19；**HE-17** 只补 `load_with_recovery` 耗时预算（功能已测） |
| `device` | 82.46 | 738 / 895 | 74.67 | 157 | 可单测未写 | `loader.py` 113（adapter env/entrypoint 解析）、`registry.py` 24；归 **HE-13**；真实重连/token 走 §6 |
| `doctor` | 88.08 | 377 / 428 | 78.29 | 51 | 可单测未写 | `probes.py` 51（环境探测分支）；无 batch-2 卡 |
| `music` | 88.03 | 1529 / 1737 | 80.39 | 208 | 可单测未写 | `jellyfin.py` 43、`subsonic.py` 36、`names.py` 36（HTTP fake 可覆盖）；无 batch-2 卡 |
| `plugins` | 75.89 | 1473 / 1941 | 63.01 | 468 | 可单测未写 | `contract.py` 350 行缺失中仅 **23 行**是 pre-v1 兼容层 `_fallback_load`（`tests/test_plug_contract.py:459,478` 已覆盖其行为），其余 327 行是 contract kit 分支（0.7.7/backlog）；`loader.py` 36 + `models.py` 33 归 **HE-13** |
| `providers` | 72.83 | 839 / 1152 | 60.11 | 313 | 可单测未写 | `keycheck.py` 164（网络校验，可用 fake transport）、`openai_compat.py` 70；无 batch-2 卡 |
| `security` | 87.03 | 208 / 239 | 80.00 | 31 | 可单测未写 | `netguard.py` 31；无 batch-2 卡 |
| `session` | 90.66 | 1573 / 1735 | 83.17 | 162 | 可单测未写 | `turn.py` 72、`memory.py` 17、`phrasebook.py` 13；无 batch-2 卡 |
| `tools` | 77.54 | 1077 / 1389 | 64.77 | 312 | 可单测未写 | `info.py` 100、`mcp_client.py` 73、`ha.py` 45；无 batch-2 卡 |
| `web` | 80.37 | 4916 / 6117 | 68.40 | 1201 | 可单测未写（主）+ 需真机子集 | `app.py` 750、`v2_api.py` 337、`setup_api.py` 102；设备 action / 控制台真机流程走 §6 |
| **total** | **80.95** | **18429 / 22767** | **69.55** | **4338** | — | — |

### 5.3 缺口三类（每类落到归属）

| 类别 | 含义 | 本基线中的代表缺口 | 归属 |
|---|---|---|---|
| **可单测未写** | 用 fake/临时目录即可覆盖，不需要设备 | `audio` 776、`web` 1201、`plugins` 468（含 contract kit 327 + loader/models 69 + 其他 72）、`device` 157、`tools` 312、`channels` 412、`providers` 313、`music` 208、`session` 162、`(root)` 175、`doctor` 51、`security` 31、`config` 72 | 0.7.7 batch-2：**HE-13**（device/plugins 的 env/entrypoint 语法与 override）、**HE-14**（audio frame/text fuzz、可注入时钟、jitter）、**HE-17**（config 加载耗时预算）；其余进 0.7.8 后 backlog |
| **需真机** | 需要真实设备/耳听/声学环境，CI 只能给协议 | `audio/process.py` 的 ALSA/KWS/AEC 路径、`web` 的设备 action、`device` 的重连/token、music 的真实媒体源 | §6 的 26 条协议（命令 + 判据 + 记录表），0.7.4 / 0.7.8 |
| **已弃用** | 仍随代码保留但正常路径不再走，不为它补测试 | 仅 `adapter_aliases.py` 的 `xiaoai→lx06` 别名（临时，0.8 前移除）；`contract.py` 的 `_fallback_load` 不归此类（23 行未覆盖，行为已由 `tests/test_plug_contract.py:459,478` 覆盖） | 不排测试；删除/下线时同步删测试与清单 |

### 5.4 batch-1 测试 ↔ 覆盖对应

| 条目 | 测试（file::test） | 对 `src/rui` 覆盖/缺口的作用 |
|---|---|---|
| HE-03 | `tests/test_he03_first_response_null.py::test_null_first_response_is_skipped_not_counted_as_zero` 等 5 条；`pytest -q tests/test_he03_first_response_null.py` | 锁定 `scripts/fleet_matrix.py` 的「null 不补 0」与 `TurnTrace` 上报；工具脚本不计入 `--cov=rui`，不改变包级数字 |
| HE-12 | `tests/test_he12_source_plug_fallback.py::test_source_plugs_fallback_backs_the_in_process_adapter` 等 2 条；`pytest -q tests/test_he12_source_plug_fallback.py` | 覆盖 `plugins/builtin.py` 的源码 fallback 与 `device/loader.py` 的 manifest 授权分支（覆盖 +4 行：device +1、plugins +3） |
| D2-06 | `rui-dot2-bridge/tests/test_d2_06_stop_playback_flush.py::test_active_stop_clears_discarding_before_the_next_reply` 等 2 条；`cd rui-dot2-bridge && .venv/bin/python -m pytest -q tests/test_d2_06_stop_playback_flush.py` | 在桥仓，不计入 house `--cov=rui`；守护 `stop_playback` flush→EOS 顺序 |

## 6. 真机项三件套（命令 / 判据 / 记录表）

> 适用于 §2 的 26 条真机/人工项。每条都必须**现场执行命令 → 按判据记录 → 落表**；没测到写 `null + 原因`，不补、不猜。
> 环境：`BASE=http://<rui-host>:8080`、`H='X-Auth-Token: <token>'`、`ID=<device-id>`、`$LOG`=该设备平面/容器日志。
> 记录表模板（复制到 0.7.4/0.7.8 验收记录，一行一次运行）：

| 日期 | 设备/房间 | 距离/音源 | 命令 | 分母 n | 结果 | 判据 | 日志/JSON/截图 |
|---|---|---|---|---|---|---|---|

> **执行环境**：所有 `scripts/*.py` 命令必须在 `house-ear` 的 `.venv` 下（`.venv/bin/python scripts/...`）或 `uv run python scripts/...` 执行；系统 Python 3.12 缺少 `ruamel` 等依赖。
> **cwd 规则**：默认在 `house-ear/` 根目录执行；D2-01 build、D2-05 compose、D2-09 tools 需先 `cd rui-dot2-bridge`；D2-07/D2-11 的 `scripts/measure_erle.py` 在 `house-ear/`；X-*/HE-* 默认在 `house-ear/`。
> 逐台「添加→入网→唤醒→应答→播放→停止→删除」的固定记录表用 `docs/release/acceptance-4devices.md §5.3`；
> 证据格式（截图命名、4xx/5xx 片段、镜像 tag/commit/视口）用 `docs/audit/console-audit-scope.md §7`。

| ID | 命令（可复制） | 判据 | 记录表 |
|---|---|---|---|
| X-01 | v2：`curl -sS -X POST "$BASE/api/v2/devices/$ID/actions" -H "$H" -H 'Content-Type: application/json' -d '{"action":"volume.set","args":{"value":V}}'`，V=51/102/153/204/255（1–255）；再 `{"action":"volume.read","args":{}}`。或 v1：`curl -sS -X PUT "$BASE/api/v1/device/volume" -H "$H" -H 'Content-Type: application/json' -d '{"value":V,"device":"<id>"}'` | 默认配置（cap 未生效）下断言回读 `== V`；cap<100 时回读 = `percent_to_device_volume(min(device_volume_to_percent(V), cap))`（允许设备取整）。5 档无失真、无跳变 | 模板（V/回读）+ `acceptance-4devices §5.3` |
| X-02 | `curl -sS -X POST "$BASE/api/v2/devices/$ID/actions" -H "$H" -H 'Content-Type: application/json' -d '{"action":"led.set","args":{"effect":"wake"}}'`；对 listening/thinking/error 各一次 | 灯序与 `tests/test_led.py` 一致；每态截图 | 模板 + `led-<state>.png` |
| X-03 | `python scripts/fleet_say.py --rounds 1 --gap 8 --out /tmp/fleet.csv`；`python scripts/fleet_matrix.py --log "$LOG" --marks /tmp/fleet.csv` | 4 台表格填满；0 `lost_same_word`、0 串台；任一 null = 失败 | `acceptance-4devices §5.3`（4 行） |
| X-04 | `python scripts/measure_turns.py --url "$BASE" --command-dir <6 句 wav 目录> --rounds 5 --out /tmp/lx06-turns.jsonl` | 6 句 × 5 轮 ≥26/30 | 模板 + `/tmp/lx06-turns.jsonl` |
| X-05 | `python scripts/acceptance-v1.py wake --url "$BASE" --trials 20 --criterion 0.95`；`python scripts/acceptance-v1.py barge --url "$BASE" --trials 3 --auto-say` | 唤醒 ≥19/20；stop-push ≤300 ms | `acceptance-runs/*.md`（脚本自动写） |
| X-06 | `python scripts/measure_turns.py --url "$BASE" --command-dir <6 句 wav 目录> --rounds 5 --out /tmp/fleet.jsonl`；`python scripts/fleet_matrix.py --log "$LOG" --json` | 30/30、26/30、8/8、10/10 同分母重采；每个数字带 `--log` 行号 | 模板 + `acceptance-runs` + 行号 |
| X-07 | `cd rui-dot2-bridge && python tools/eq_report.py --json`（客观）；盲听 3 首、随机顺序、5 分制 | 客观指标不劣化；盲听 ≥4/5 且无失真 | 模板（曲目/顺序/评分） |
| HE-01 | `python scripts/measure_turns.py --url "$BASE" --command-dir <告别 wav 目录> --rounds 10 --out /tmp/goodbye.jsonl` | 告别后 0 个额外 turn；`reply_finished` 生效 | 模板 + JSONL |
| HE-02 | `python scripts/measure_turns.py --url "$BASE" --command-dir <wav 目录> --gap 0.9 --rounds 5 --out /tmp/he02-09.jsonl`；`--gap 1.2 --out /tmp/he02-12.jsonl` 各一次 | 0 自回声 turn；cue 后无假请求 | 模板 + JSONL |
| HE-04 | `python scripts/measure_turns.py --url "$BASE" --command-dir <5 句 wav 目录> --rounds 5`；手机/麦克风录音 `ffmpeg -f avfoundation -i ":0" -t 120 /tmp/mic.wav` | 给出首声 P50/P95（记录，不设门） | 模板（server 值 + 录音偏移） |
| HE-05 | `python scripts/acceptance-v1.py wake --url "$BASE" --trials 10 --label far-field --no-gate` | 只记录 10 次远场结果（不判 PASS/FAIL） | `acceptance-runs/*.md` |
| HE-06 | 断电重启 NAS 后：`curl -fsS "$BASE/health" -H "$H" \| jq '{status,audio_plane}'`，每 30 s 一次共 10 次 | ≤5 min 内 200 且 `audio_plane=true`，无需人工 | 模板（时间戳/状态） |
| HE-07 | 断 AP/改 IP 后：`curl -sS -H "$H" "$BASE/api/v2/devices/$ID" \| jq '{connected,status}'`，每 5 s 一次 | <10 s 回到 `connected=true/status=ready`；重试 ≤1 | 模板（时间序列） |
| HE-08 | `docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}'` 每 300 s；`curl -fsS -o /dev/null -w '%{http_code}' "$BASE/health"` | 24h 0 5xx、0 plane 重建、RSS 斜率 <1 %/h | 模板/CSV（24h） |
| HE-15 | `python scripts/measure_wake.py --config <cfg> --device "$ID" --seconds 60 --save-voice /tmp/oww.wav`；每个变体 20 次 | 20 次 ≥19 次；否则砍该变体 | 模板（变体/命中） |
| HE-16 | `python scripts/measure_turns.py --url "$BASE" --command-dir <音乐中短指令 wav 目录> --rounds 10` | 10/10；音乐不断、指令不丢 | 模板 + JSONL |
| D2-01 | `cd rui-dot2-bridge && docker build --platform linux/amd64 -f deploy/downstream/Dockerfile .`；`python ../house-ear/scripts/acceptance-v1.py wake --url "$BASE" --trials 5`；设备侧 `curl` 回连 | 构建/运行成功；唤醒/播放/barge/token 重连逐项记录 | `canary-0.6.0-official-image.md §八` 命令清单 + 模板 |
| D2-03 | `bash deploy/install.sh`（一次性容器）→ `curl -fsS "$BASE/health" -H "$H" \| jq`；控制台逐页点一遍 | 设备链路 ready；`device_errors=0`；无 5xx | `acceptance-4devices §5.3` + `console-audit-scope §7` |
| D2-05 | 先把 rui 配置 `system.log_level: debug`（`primeWait` 只在 `device_link.py:417` 的 `log.debug`），再 `cd rui-dot2-bridge && docker compose -f deploy/downstream/docker-compose.yml logs --since 30m \| grep primeWait`（每轮 10 次） | `primeWait` 14–22 ms；无 1 s 异常；人耳无卡顿 | 模板（primeWait/听感 + `system.log_level: debug` 截图） |
| D2-07 | `python scripts/measure_erle.py --config <cfg> --device dot2 --file <粉噪 wav> --seconds 600 --json` | `aec_erle_db` P50 ≥20 dB（目标 25） | 模板 + JSON |
| D2-08 | `sox -n -r 48000 -c 1 /tmp/sweep.wav synth 10 sine 20 20000`；手机录音；按 `docs/aec-device-tuning.md` 公式算 RT60 | 只记录每个房间 RT60（假设 0.3–0.5 s） | 模板（房间/RT60） |
| D2-09 | `cd rui-dot2-bridge && python tools/eq_report.py --wav <48 kHz mono S16 wav> --json`（脚本没有 `--gain`）；SPL 计 1 m 读数、增益档位按部署参数（`DOT2_LOUDNESS_*` / config）逐档记录；1 h 耐久 | SPL 记录；THD 不劣化；1 h 无热衰 | 模板（SPL/THD/时长 + 档位） |
| D2-10 | `python scripts/acceptance-v1.py wake --url "$BASE" --trials 20 --criterion 0.95`；`... barge --trials 3 --auto-say`；`docker stats --no-stream` | 20 次 ≥19/20；声学 P50 ≤600 ms；RSS 记录 | `acceptance-runs` + 模板 |
| D2-11 | `python scripts/measure_erle.py --config <cfg> --device dot2 --file <粉噪 wav> --seconds 300 --json` | `ref_active=true` 且播放时 ERLE >10 dB | 模板 + JSON |
| D2-12 | `python -m rui plug-test <plug dir> --adapter <adapter.py>:build --json`；`openssl s_client -connect <device>:8870 -servername <host> -brief` | 契约全 PASS；TLS hostname/4412 握手成功；未知消息进 debug 不崩 | 模板 + `contract-report.json` |
| D2-13 | `python scripts/measure_erle.py --config <cfg> --device dot2 --file <粉噪 wav> --seconds 300 --json` ×3；`python scripts/measure_turns.py --url "$BASE" --command-dir <wav 目录> --rounds 5 --out /tmp/d213.jsonl`；长回复/逐距离各 5 次 | 静音基线 3×5 min；长回复无噪声；距离逐档记录 | 模板（轮次/距离/结果） |

## 7. 后续（task-2 reviewer 非阻塞观察，0.7.7 前处理）

1. **HE-03 filler 端到端断言**：当前 `tests/test_he03_first_response_null.py` 只做了 `TurnTrace` 级断言
   （filler 不产生 `first_response_ms`）；补一条 `TurnRunner` 端到端用例：让 LLM/工具延迟触发
   `assistant.filler_after_s`，断言 filler 播出后 `play_start` 仍来自首个答案 chunk。
2. **HE-12 `catalog.get("shadow-plug")` 防空断言**：当前用 `resolve()` 证明 builtin 胜出；
   补断言 `PlugCatalog.get("shadow-plug")` 的 `source == "user"`，证明 user plug 确实被加载、
   只是解析优先级输给 builtin，避免「user plug 根本没载入」也能让测试通过的空过。

## 8. 真机 / 人工项分类（task-5，2026-10-27）

> 范围：§2 的全部 **26 条**。本卡**只分类**，不删条目、不改任何条目的「已验证/未验证」状态；
> 处置方式（继续测 / 转已知限制 / 删）待 9 哥 在 D-005 拍板。
> 分类依据：条目原文的「判据」列 + 现有无头夹具（`tests/test_cue_echo_excision.py`、
> `tests/test_dismiss_turn_release.py`、`rui-xiaoai-bridge/tests/test_led.py`、`rui-dot2-bridge/tests/*` 等）。

### 8.1 口径（三选一，互斥）

| 标签 | 判定标准 | 条数 |
|---|---|---:|
| `可转测试` | 条目**主判据**（直接复现其声称失败模式的那半句）可完整无头判定；真机半句只作确认，已在 §8.2「测不了什么」逐条列明 | **2** |
| `只能真机` | 主判据必须依赖设备、声学环境或人耳；CI 只能覆盖次要子句（D2-12 即此类） | **21** |
| `环境缺失` | 缺的是环境/硬件/模型/拓扑（不是测试能力），写出缺什么 | **3** |
| **合计** | 与 §0 统计、§2 逐条标注一致 | **26** |

> 注意：§0 的「其中已有部分 CI 夹具、只缺真机腿 **9** 条」**不等于**可转测试。那 9 条只是子步骤
> （LED 顺序、音量映射、契约等）已有夹具，主判据仍要设备。本表按「**主判据**能否完整无头判定」划线：
> HE-01/HE-02 的主判据是回声回灌与门禁极值（合成 PCM 可完整复现）；D2-12 的主判据是设备 TLS/4412 与
> 0.2.2 固件行为，未知消息/trust 只覆盖次要子句，故归 `只能真机`（CI 半仍列在 §8.2 附注）。
>
> diff 说明：§2 表是为新增「分类（task-5）」一列而整行重写（`git diff` 会显示 26 行删除 + 26 行新增），
> 原 6 列（声明/位置/现状/判据/需要）逐字节未改、条目数与 ID 集合前后一致（§1 10 条 + §2 26 条 = 36）。

### 8.2 `可转测试` 候选清单（2 条，含测试名与断言要点）

| ID | 建议测试（file::test） | 断言要点 | 能测什么 | 测不了什么 |
|---|---|---|---|---|
| HE-01 | `tests/test_he01_goodbye_echo_no_extra_turn.py::test_goodbye_pcm_replayed_as_mic_opens_no_turn` | ① 把 D5 告别回复 PCM 设为 `UtteranceGate.set_echo_reference`，再按 lag 0/150/400 ms 当 mic 回灌（复用 `tests/test_cue_echo_excision.py` 的 `_feed/_close` 夹具）：`gate.arm()` 后捕获必须为 `None`（0 utterance）；② **idle goodbye 路径**：用 `tests/test_dismiss_turn_release.py:170-189` 的 `_ReplyHandle` + `_PlaneLink.reply_finished`，跑「idle watchdog 告别（`src/rui/web/api/app.py:3327-3330`）→ `reply_finished`」：只产生 1 个 turn、follow-up window 未开（0 额外 turn）。注意 dismissal（退下吧）**故意不调** `reply_finished`（`app.py:913-917`、`:999-1003`），不能用它测这一句 | 回声回灌下的门禁切除 + idle goodbye 的 `reply_finished` 记账；主判据「告别 PCM 当 mic 输入 → 0 额外 turn」可完整判定 | 真机扬声器→mic 的回声电平与 AEC 残差；原判据后半「真机 10 次告别」仍建议保留 |
| HE-02 | `tests/test_he02_cue_echo_extremes.py::test_cue_echo_is_excised_at_lag_900_and_1200_ms`、`::test_a_50ms_overlap_never_feeds_the_cue_to_asr` | 用 `_one_wake(gate, cue, lag_ms=900/1200)`：`_best_corr(segment, cue) < 0.4` **且** `_max_windowed_corr(segment, cue) < 0.6`（清音/削波**同阈值**；削波用 `clip_cue=True`）；用户回复保留 ≥80%。overlap 50 ms **不能**走 `_one_wake`（负 gap 被 `_silence` 截成 0 字节，用户必从 cue 尾部起），改用 `_mix`（参照 `test_a_clipped_overlap_never_feeds_the_cue_to_asr` :245-248），让用户从 `lag + CUE_MS - 50` 处起说话，同样断言 `_best_corr < 0.4` 且 `_max_windowed_corr < 0.6`；全程 0 额外 turn | 门禁在 lag 900/1200 ms、overlap 50 ms 的切除行为（合成 PCM，完全确定；阈值与现契约一致） | 真机 AEC / 房间回声下的 A/B 听测（原判据后半） |

> **附：D2-12（标签 `只能真机`）的 CI 半可以先补，但不改变标签**：
> `rui-dot2-bridge/tests/test_d2_12_unknown_message.py::test_unknown_event_logs_debug_and_keeps_the_link`
> （未知事件进 debug、连接不崩、后续消息仍处理）；
> `tests/test_plugin_trust_pinned.py::test_manifest_trusted_is_honored_today_risk_pinned`
> （钉**现行**行为：manifest 自声明 `trust: trusted` 会被采信并允许 in-process——`src/rui/plugins/models.py:558`
> 默认 `third_party`，`loader.py:231-236` 只拦 `third_party` 的 in-process；这是**已知风险**，不是安全边界。
> 若要钉「用户可写目录的 `trust: trusted` 应被拒绝」，那是**待实现策略**，须另立卡，不能写成当前行为）。

### 8.3 `只能真机` 分组与单窗口覆盖估算（21 条）

> 「一个真机窗口」= 9 哥 可到场、LX06 与 Dot2 各一台在手、手机可录音、有 ≥1 h 连续操作时间。

| 组 | 场景 | 条目 | 条数 | 一个窗口的估算 |
|---|---|---|---:|---|
| A | 1 m 单机协议（唤醒/指令/播放/音量/LED，手机录音 + 9 哥耳朵旁听） | X-01, X-02, X-04, X-05, X-06, HE-04, HE-05, HE-16, D2-05, D2-07, D2-08, D2-10, D2-11, D2-13 | 14 | **4–5 h 可全部覆盖**（每条约 10–25 min；D2-07 粉噪 10 min、D2-10 20 次唤醒最久） |
| B | 部署/兼容（Dot2 上机、TLS/端口、一次性容器） | D2-01, D2-03, D2-12 | 3 | 2–3 h（含烧镜像/回滚；D2-03 的 `install.sh` 可在一次性容器复跑，设备链路仍需现场；D2-12 的 TLS/4412 实测约 0.5 h 并入） |
| C | 耳听主观 | X-07 | 1 | 0.5 h（3 首盲听，需 9 哥在场；可搭 A 组窗口，A+C 共 15 条） |
| D | 服务端/网络/长稳 | HE-06, HE-07, HE-08 | 3 | HE-06 0.5 h（含 ≤5 min 生产中断）、HE-07 0.5 h、HE-08 起跑后 24 h 读数 |
| **合计** | | | **21** | 约「2 个工作日 + 1 次过夜」清空（设备在手的假设） |

**结论：一次 4–5 h 真机窗口可覆盖 A 组 14 条（含 X-07 耳听则 15 条）；剩余 6 条（B 组 3 + D 组 3）走部署/网络窗口 + 24 h 观测。**

- A 组内部分工：X-04/X-05/HE-16 偏 LX06；X-06/D2-05/D2-07/D2-08/D2-10/D2-11/D2-13 偏 Dot2；X-01/X-02/HE-04/HE-05 两台都要各做一遍。
- 每个条目的命令、判据与记录表已在本文件 §6；窗口现场只需按表执行并落记录，不再另写协议。
- 若只有一台设备在手：A 组按设备拆成两个半窗（各 2–2.5 h），总数不变。

### 8.4 `环境缺失`（3 条，写清缺什么）

| ID | 缺什么 | 现有替代 / 建议 |
|---|---|---|
| X-03 | 4 台同网设备（设备未到） | 先用 2 台完成 X-01/X-02 与部分并发观察；`acceptance-4devices §5.3` 的四设备表格保持 `null + 原因`，不补不猜 |
| HE-15 | 自训中文 OWW 模型 / 中文 onnx stem（当前无该变体；**模型需先训练**，不是等设备/固件） | 先用现有模型测基线唤醒（X-05 同口径）；模型训练完成后再按每变体 20 次，≥19/20 才保留 |
| D2-09 | SPL 计、THD 分析仪、1 h 耐久台架 | 先记录增益档位与 1 h 温升/听感；手机 SPL app 只作参考，不写入门 |

### 8.5 拿不准（需 9 哥 拍板，仍计入上方统计）

| ID | 当前标签 | 另一种可能 | 拍板点 |
|---|---|---|---|
| X-06 | `只能真机` | 若已无与 0.7.1/p21 同源的校准音源 → `环境缺失` | 是否要求四个矩阵数字与历史**可比**（同源音源）；不要求则可降级为「只记录新值」 |
| HE-06 | `只能真机` | 若不允许生产 NAS 断电且无第二台 NAS → `环境缺失` | 是否允许 ≤5 min 的生产中断（`/health` + `audio_plane` 判据不变） |
| D2-12 | `只能真机` | 若 0.2.2 固件不再维护 → 按「已知限制」处置 | 0.2.2 兼容是否还在支持范围；CI 半（未知消息/trust）可先补（§8.2 附注），但结论取决于设备 TLS/4412 实测（B 组窗口） |

> 注：§8.3 的「2 个工作日 + 1 次过夜」只估算 21 条 `只能真机` 的现场执行；**D2-09（SPL 计/THD 仪器准备）与 HE-15（中文 OWW 模型训练）不含在内**，排窗口时要另算（X-03 的 4 台设备同理未计入）。

### 8.6 结论

1. **本卡不改变任何条目的「已验证/未验证」状态**：26 条全部保持原状，只是各加一个处置标签；
   删除、转「已知限制」或继续排测，均待 **D-005** 拍板。本卡不改 README、不改代码，未删任何条目。
2. 统计：`可转测试` 2 + `只能真机` 21 + `环境缺失` 3 = **26**，与 §0 统计表、§2 逐条标注三方一致。
3. 给 D-005 的输入建议（不代替拍板）：
   - 2 条 `可转测试` 不需要设备，可立即排成普通 pytest 卡（§8.2）；D2-12 的 CI 半可一并补（§8.2 附注），但不改变其 `只能真机` 标签；
   - 21 条 `只能真机` 按 §8.3 的两个工作日 + 一次过夜排窗口，其中 A 组一次 4–5 h 可清 14 条；
   - 3 条 `环境缺失` 等设备/仪器/模型到位，或直接转「已知限制」并保留 §6 协议；
   - §8.5 的 3 个边界项需要 9 哥 先定口径；它们只影响标签归属，不改变 26 条的总数。
