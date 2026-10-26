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

## 1. CI 可测（batch-1 → 0.7.3；batch-2 → 0.7.7）

| ID | 声明 | 位置 | 现状 | 现有测试 | 变成什么测试 | 批次 |
|---|---|---|---|---|---|---|
| HE-03 | `first_response_ms` 常为 null，「证据不足」 | `docs/analysis/dot2-vs-xiaoai.md:45` | CI 测试 | `tests/test_he03_first_response_null.py`（5 条；`pytest -q tests/test_he03_first_response_null.py`） | 固定 trace fixture 回放：null 只计 skipped、不补 0；生产端 `play_start` 上报已正确（filler 不计），无需改代码 | 1 |
| HE-09 | 源码全量镜像构建 + 镜像体积（431 MB）未验证 | `docs/release/v0.7.0.md:40,55`、`HANDOFF:415` | 待补 | 无（`docker-publish` 只走薄层） | CI 干净 checkout `docker build` → 断言构建成功 + `docker image inspect` 体积记录 | 流水线 |
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

| ID | 声明 | 位置 | 现状 | 判据 | 需要 |
|---|---|---|---|---|---|
| X-01 | 音量控制从未系统验证（两台设备） | 三仓 README | 未测 | 设 5 档 → 读回一致、cap 处不失真；`POST /api/v1/device/volume` ×5 + 耳听 | 真机 |
| X-02 | 指示灯未测 | 三仓 README | 未测 | 唤醒/思考/告别序列与 `tests/test_led.py` 顺序一致（拍照留证） | 真机 + 照片 |
| X-03 | 多设备并发未测（音量/LED/4 台） | 三仓 README、`release/v0.7.2.md:57` | 未测 | `acceptance-4devices.md` §5.3/§7 表格填满、0 串台、0 `lost_same_word`；任一 null = 失败 | 4 台（设备未到） |
| X-04 | LX06 语音指令成功率未统计 | house-ear README:110、xiaoai README:91 | 未测 | 6 句 × 5 轮 ≥26/30（同 Dot2 口径） | 真机 + 人声 |
| X-05 | LX06 唤醒 19/20、打断 205.2 ms 是 2026-10-24 历史，无 0.7.2 重测 | xiaoai README:75,89,90 | 历史实测 | `acceptance-v1.py wake --trials 20` ≥19/20；`barge --trials 3` stop-push ≤300 ms | 真机 |
| X-06 | Dot2 矩阵（30/30、26/30、8/8、10/10）测于 0.7.1/p21，0.7.2 未重测 | house-ear README:105、dot2 README:88 | 未重测 | 同分母重采；每个数字带 `--log` 行号 | 真机 + 校准音源 |
| X-07 | EQ/限幅只过测量，没过耳朵 | dot2 README:98、`HANDOFF:410` | 无听测 | 盲听 A/B：3 首、5 分制、随机顺序、≥4/5 且无失真 | 9 哥 耳朵 |
| HE-01 | D5 告别自回声误开 turn：加了 `reply_finished` 但仍「待验证」 | `docs/plan-to-0.6.md:21` | 仍待验证 | 把告别 PCM 当 mic 输入注入 → 0 额外 turn；真机 10 次告别 | CI 部分 + 真机 |
| HE-02 | cue 回声在「冷启动+不削波+lag>800 ms」或「重叠<110 ms」仍可能漏 | `release/v0.7.2.md:53`、`HANDOFF:386` | 已知限制 | 门禁测试补 lag 900/1200 ms、overlap 50 ms；真机 A/B | 真机 |
| HE-04 | 首声可闻延迟从未声学测量 | `docs/audio-latency.md:154` | 未测 | mic 录音 + `play_start` 时间相关，5 轮给 P50/P95（暂不设门） | 真机 + 录音 |
| HE-05 | 3–4 m 唤醒距离从未尝试 | `docs/acceptance.md:99` | 未做 | `acceptance-v1.py wake --trials 10 --label far-field`（只记录） | 真机 |
| HE-06 | NAS 断电自启未测 | `docs/runbook.md:173,403` | 推断 | 真断电一次：5 min 内 `/health` 200 + `audio_plane=true`，无需人工 | NAS |
| HE-07 | 真机重连时序（Wi-Fi 抖动/IP 变化）未测 | `runbook:404`、`device-management-research.md:973` | 未测 | 断开 AP / 改 IP → `speaker connected` <10 s、重试 ≤1 | 真机 + 路由器 |
| HE-08 | 24h/3 天长稳未满窗 | `release/v0.7.0.md:37` | 未满窗 | 0 5xx、0 plane 重启、RSS 斜率 <1 %/h | 真机 |
| HE-15 | 中文 OWW 模型效果未知；中文 onnx stem 未试 | `plan/speaker-tuning.md:448,454` | 效果待测 | 每变体 20 次唤醒 ≥19/20，否则砍功能 | 真机 + 自训模型 |
| HE-16 | 音乐中 2 m 短指令未测 | `HANDOFF:177` | 未测 | 10 次 ≥10/10（同 1 m 口径） | 真机 |
| D2-01 | canary 0.6.0：无真机，唤醒/播放/AEC/音量/token 未验证 | `dot2 docs/canary-0.6.0-official-image.md:251` | 未验证 | 接设备跑 wake/play/barge + token 重连 | 真机 |
| D2-03 | 0.2.2/0.3.4 部署从未验证设备链路；`install.sh` 未重跑 | `dot2 docs/deploy-0.3.4-user-report.md:296-299` | unverified | 设备侧部署验收 + headless 控制台点一遍 + 一次性容器重跑 `install.sh` | 真机 + 容器 |
| D2-05 | `primeWait 14–22 ms` 未验证；那一轮没声音、没听测 | `stutter-adapter-options.md:390` | 未验证 | 日志断言 primeWait + 人工听测清单 | 真机 |
| D2-07 | 设备 AEC 上限 20–24 dB 是估计；127 需要 31 dB | `aec-device-tuning.md:182,512` | 未实测 | 粉噪 10 min，`aec_erle_db` P50 ≥20 dB（目标 25） | 真机 |
| D2-08 | 房间 RT60 0.3–0.5 s 是假设 | `aec-device-tuning.md:84` | 未实测 | 扫频测 RT60，只记录 | 真机 |
| D2-09 | 绝对 SPL / +29 dB 增益失真与寿命 / #630 一致性未测 | `audit-device-audio.md:19,21,275` | 未测 | SPL 计 1 m；增益扫描 + THD + 1 h 耐久；第二台 A/B | 真机 + 仪器 |
| D2-10 | M1 门禁：严格 20 次唤醒待补；声学 barge 待测；RSS 未暴露 | `milestone-m1.md:21,22,26` | 待补 | 20 次 ≥19/20；mic 测静音 P50 ≤600 ms；`docker stats` | 真机 |
| D2-11 | 固件是否真的给 AEC 参考未验证 | `p3-firmware-patch.md:163` | 未验证 | 设备端 2ch 采集：`ref_active=true` 且播放时 ERLE >10 dB | 真机 |
| D2-12 | 0.2.2 兼容只做过静态分析（TLS hostname/4412、`oww_wake` 形状、trust 自定义） | `rui-0.2.2-dot2-compat.md:576-586` | 未实测 | 设备 TLS/端口实测；未知消息进 debug 分支的单测；定 trust 策略 | 真机 + CI |
| D2-13 | 真机第一轮遗留：音量适配、长回复噪声、仅唤醒/多轮/距离、2 min 基线太短 | `real-machine-round-1.md:51-53` | 待补 | 静音基线 3×5 min；长回复听测；逐距离各 5 次 | 真机 |

## 3. 文档矛盾（0.7.3 卡片 K-0.7.3-3 必须逐条给终态）

| # | 矛盾 | 判定基准 |
|---|---|---|
| 1 | `code-health:406`「配置加载+恢复未测」vs 已有 `test_config_recovery.py` | 代码在 → 缺的是**耗时**测量；改措辞 |
| 2 | READMEs「0.7.2 未重测」vs `HANDOFF:223` 标成 v0.7.2 | 采样日期 2026-10-26 15:01 的镜像是 0.7.1/p21 → **README 对**，改 HANDOFF |
| 3 | dot2 README「volume 未测」vs `milestone-m1:23`「音量/响度 ✅」 | 前者指**控制端点**，后者指**响度/EQ**；两处各自限定语义 |
| 4 | `socket-plug-v1.md:309` 说 plug-test 子进程 vs `plug-test-kit.md:22` 说 in-process | 以代码为准（audit P2-14：in-process）→ 改前者 |
| 5 | barge 指标：stop-push ≤300 ms vs acoustic P50 ≤600 ms | 两个指标都留，README 的 8/8、205.2 ms 必须注明是 stop-push |
| 6 | 测试总数 1512 / 1518 / 1571 | 以 0.7.3 的规范口径（collected/passed/skipped/xfail）为准，三处统一 |
| 7 | xiaoai CHANGELOG 把历史 19/20、205.2 ms 放 `### Verified` 下 | 改为 `### Historical measurements (2026-10-24, not re-tested)` |
| 8 | canary「wake/playback/AEC/volume 未验证」vs dot2 README 30/30 | 不同轮次；互相加注日期 + 范围（canary 指 0.6.0 官方镜像、无真机） |

## 4. 与 0.7.8 验收的关系

- **必须做完**（进 Done 6 / 手册）：X-01 音量、X-03 多设备（至少 2 台）、HE-06 断电自启、HE-08 24h 初查、D2-11 固件参考（若设备在）。
- **明确不做，写进「已知限制」**：X-02 LED 照片、X-07 EQ 耳测（等 9 哥）、HE-05 远场、HE-15 中文 OWW、D2-07/08/09 声学仪器项。
- **CI 可测项不留到 0.7.8**：batch-1 在 0.7.3 完成，batch-2 在 0.7.7 完成；0.7.8 只出最终覆盖率报告。
