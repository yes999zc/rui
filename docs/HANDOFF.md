# HANDOFF — rui 阶段快照（0.7.8 已发布）

## 当前推进：0.8.0（2026-10-28）

- **A1 d.6 ASR 尾巴剥离**：已落地 `b91599a`；全量 `1722 passed / 6 skipped`；review PASS + verify VERIFIED。
  真机 N2 口径已反转（`停止你好` 现在命中），待 9 哥 按新口径复验 2 min。
- **A2 d.7 volume 容差**：已落地（`VOLUME_RECONCILE_TOLERANCE = 1`，设备 0–255 刻度）；
  d.7 已写明显式代价（1 刻度假漂移不纠），新增一步差用户 set 用例；全量 `1726 passed / 6 skipped`；
  review PASS + verify VERIFIED。真机静置 5 min 计数归零待验。
- **P0（2026-10-28 真机插入，优先于 B1）**：ack 回声漏切 → 自问自答。**根因**：`process.py:1862`
  用设备 48k rate 除 16k 渲染的 cue → 460ms 变 153ms，skip/hold=403/2653，cue 整段 replay。
  已实现修复（cue 用渲染 rate；hold=460+850 lead+2500，从播放结束起算；VAD 首唤醒晚 cue 先验），
  全量 `1733 passed / 6 skipped`；review PASS + verify VERIFIED（修掉 1 处核心禁词注释）。卡 task-13，证据记 known-limitations L17。
- **P1 LED/会话收敛追加（2026-10-28 04:29 真机，并入 task-17）**：`stop intent -> session closed` 后
  13s 又 `led: listening` 并 repaint 卡住。要求：session closed（stop/结束词/farewell）后不得重回 listening；
  若保留 followup 窗口 N=8s，窗口结束立即 disarm + `led: off`；任何一轮结束 LED 事件最后一条必须是 `off`；
  repaint 有最大次数/超时；回归：stop 后 LED 序列以 off 结尾且 30s 无新 listening。
- **P2 音量键回写（task-19，已收口 `58c845e`：5% 步进 + 10s 抑制 + 持久化；house 1745；review PASS + verify VERIFIED）**：真机 12:29–12:34 `volume: reconciled to 204 (config 80%)`
  每 16s 一条且无 `dot2 link: volume` 事件 ⇒ 物理音量键改了设备但配置未回写，16s 内被拉回。
  要求：volume_up/down 回写 `devices[].volume` 并持久化；按键后 10s 内 reconciler 不回拉；
  d.7 容差（`8961cbf`）叠加后静置仍无每 16s 日志；测试注入按键→配置降低且不回拉、±1 级不下发。
  跨桥/ house 两仓，两边 commit+push。
- **P2 物理按键（task-18，已收口：桥 `0698460` + house `de89e65`；桥 178 / house 1737；review PASS + verify VERIFIED）**：桥 `device_link.py:461-467` 收到 button 但全仓无 `on_button` 注入，
  按键全丢；设备上报 `clickType` + `muted`。需注入 handler、映射 mute/volume_up/down/action、health+控制台+LED `muted`；
  静音时照常转写/亮灯但跳过 ack。`DOT2_MUTE` 是部署 env，别混。
- **P1（紧随 P0）**：短回复「在，你说。」拆句 217 ms；≤8 字/≤1 句末标点不拆。卡 task-14（已实现；review PASS + verify VERIFIED，全量 1738/6；残余风险写入 d.10）。
- **P0/P1 均已收口**；B1（task-8）恢复，之后 B2→B3（桥双窗口先于 core）→C。
- **当前 HEAD**：`7b9035a`（B1）；P0 `82d45d9`、P1 `8344085`、B1 `7b9035a`。B2/B3/C 未开工。
- **桥仓 main 领先 v1.3.2 一个工具提交 `56e9bb8`**（onboard_device.py + docs/onboard-device.md + README 中英 + CHANGELOG；4 files，不进 src/wheel）。口径：v1.3.2 tag 仍指 `4536968`、不重打、不改 release 正文、不 bump 版本、不 force-push、不 revert；[Unreleased] 留到下次发布。
- **P0 根治（2026-10-28 真机，装主卧/卫生间前必须修）**：前提已更正——**manifest audio_defaults 解析是好的**
  （`GET /api/v2/devices/living/audio`：manifest_defaults.capture.rate=48000，layers source=global-config、
  effective=16000），是**显式全局配置压掉了型号硬件声明**。dot2-b audio 为空→playback 16000 vs 桥硬性 48000，
  每块 raise、LED 亮但无声。顺序改为：**task-26（字段归属/冲突：manifest 胜全局 explicit + WARN/拒绝）→
  task-24（R3 运行期自检 + 桥 play ValueError 上报为设备错误）→ task-25（catalog/runtime 一致性 + 三份值可见性）**。
  **与 task-22（params/layout）分开**。
- **进度（2026-10-28 17:0x）**：**task-24（R3）已实现，待 review/verify**（未提交）：运行期 wire 自检
  （inbound capture 由 probe、playback 由 manifest；outbound capture 仅告警不拒绝）在 `Ready` 前拒绝，
  `audio_config_mismatch:` + 两值/source/fix；`play()` ValueError 立即断 plane，其他异常 3 连击熔断；
  `Metrics.playback_errors/last_error` → `/health` + v2 `PlaneCard`/健康表；console `status_reason.code=
  audio_config_mismatch` → 中文「配置与设备不符」+ `audio_wire_mismatch` warning。新增
  `tests/test_audio_wire_check.py` 12 项；全量 **1842 passed / 6 skipped**；ruff 通过、web typecheck 通过。
  细节 `docs/audio-wire-check.md`。桥侧无需改动（`dot2 adapter.play()` 直接 raise ValueError，未吞）。
- **口径更新（2026-10-28 18:05，9哥）**：**0.8.0 不部署**，tag/release 保留作历史；0.8.1 合并 a–f 后发 v0.8.1 并直接部署。范围：a 告别/结束词不 arm 续听；b rs03qr 默认 followup_delay=300/post_mute=500（不动 lx06）；c 控制台暴露两字段；d task-23 part2 trace gap 块序号（protocol+trace additive）；e lx06 48k/16k 实测对齐 ADR + 冷启动可观测；f 文档。生产保持 0.7.9。D3 已改：followup 3000（非 0）、dot2-b params control_port=0 + loudness_file；dot2-b audio 保留。
- **进度（2026-10-28 18:55）**：c UI 卡片网格实现中（DevicesView/style + test_devices_view_grid）；d trace gap 块序号已实现（protocol/trace/app + 5 tests，全量 1887/6）；e/f 文档已写（ADR 0008、L23/L24、C-001 1.3.3、README 0.8.1 矩阵）。待 UI DONE + 三路 review/verify → 版本 bump 0.8.1 → tag/release → 部署 0.8.1。
- **进度（2026-10-28 18:50）**：task-27 已提交 `d633d80`（house 1878/6，review PASS + verify VERIFIED）；桥 **1.3.3 已发**（`9866c6d`，tag `v1.3.3`→`31195ee`，`rui_compat >=0.8.1 <0.9.0`，release https://github.com/yes999zc/rui-dot2-bridge/releases/tag/v1.3.3）。生产插件仍是 1.3.2（无 voice）直到 0.8.1 部署。0.8.1 剩余：c 设备卡片网格、d trace gap 块序号、e lx06 ADR/冷启动、f 文档 + 版本 0.8.1。
- **task-27（0.8.1 a/b/c）**：告别收尾不 arm + rs03qr 型号默认 + 控制台字段；house 1878/6、bridge 178/1；review 阻塞：`audio_defaults.voice` 被 <0.8.1 拒载 → **桥升 1.3.3，rui_compat >=0.8.1 <0.9.0**，生产插件 rs03qr 已回退到 1.3.2（无 voice）直到 0.8.1 部署。后续：task-28 卡片网格 UI、task-29 trace gap、task-30 lx06 ADR/冷启动/docs。生产 D3 已改 followup=3000、dot2-b params control_port=0 + loudness_file；dot2-b audio 保留；生产仍 0.7.9。
- **进度（2026-10-28 17:58）**：**全部代码门禁已收口**：task-26 `3d40266`、task-24 `4834201`、task-25 `1f3a035`、C2 `26659b0`、task-22 `36ad95f`；全量 **1870 passed / 6 skipped**。**core v0.8.0 已发**：annotated tag `v0.8.0`→`26659b0`（tag object `1c730f1`），release https://github.com/yes999zc/rui/releases/tag/v0.8.0。**生产 D0/1/3 已完成**：备份 `config.yaml.bak-20261028-165529`/`plugins.bak-20261028-165529`；插件已换 dot2 1.3.2 + xiaoai 0.2.3（`>=0.7.0 <0.9.0`）；全局 `audio.voice.followup_max_wait_ms: 0`（第 166 行，per-device 未写）；dot2-b audio 覆盖保留。等 GHCR 0.8.0 镜像 → D2 换 digest → up -d → D5 验证。
- **进度（2026-10-28 17:45）**：task-26 `3d40266`、task-24 `4834201`、task-22 `36ad95f` 已收口（1845/6）。task-25（catalog/runtime 一致 + 三来源）与 C2（提醒最小抢占/失败可见）已实现（1866/6），review 各 1 项 CHANGES（setup 误报 duplicate、fallback mid-turn release）修正中。生产 D0 备份已完成：`/vol1/rui/config/config.yaml.bak-20261028-165529`、`plugins.bak-20261028-165529`，回滚锚 `sha256:5aa362f6…b9f7`。下一步：25/C2 提交 → core tag/release → 部署 D1-D6。
- **进度（2026-10-28 16:40）**：task-22 已收口 `36ad95f`（5 台布局+params，1812/6）。task-26 已实现（1828/6；manifest 硬件事实胜全局，capture.rate 协商；冷启动 catalog 先注册；ADR 0007），review CHANGES 1 项（neutral capture 字段 provenance source 仍报 global-config）修正中。xiaoai 0.2.3 已发：tag `v0.2.3`→`0d82250`（tag object `2676664`），release https://github.com/yes999zc/rui-xiaoai-bridge/releases/tag/v0.2.3。task-24/task-25/C2 待 task-26 提交后做。
- **0.8.0 一次性授权（2026-10-28 9哥，含部署）**：tag/release/生产部署已授权；门禁=task-26→24→25 + task-22 收口 + **最小 C2（0.8.0，原“推迟 0.8.1”作废）** + `followup_max_wait_ms=0` + 四台真机验收。C2 最小范围：到点先停目标房间音乐→播报→不自动恢复；失败/全离线→default_output 播报+「<room>那台没在线」+health 失败计数；只停目标房间；播完 session 干净关闭、LED off、无免唤醒窗口；不做重试/排期/音乐恢复/去重。
- **发布顺序**：xiaoai 桥 0.2.3（commit `0d82250`，未 tag）先发 → dot2 1.3.2 已发不动 → core 0.8.0。发布正文注明 C2 抢占语义与 followup 默认 8000/生产设 0。
- **生产部署 runbook（已授权）**：SSH yes999zc@192.168.5.168；compose `/vol1/rui`（必须 -f docker-compose.yml -f rui-dot2-override.yml）；当前镜像 `sha256:5aa362f6b4f7d7fd24a7f01d41c31e1523ce2401296ac3b203b9f684a314b9f7`；plugins `/vol1/rui/plugins`、config `/vol1/rui/config/config.yaml`。D0 备份 config+plugins 并记 digest；D1 先换插件 1.3.2/0.2.3（旧窗口排除 0.8.0，不换会 fail-closed）；D2 换 core 0.8.0 新 digest；D3 只改 followup=0、保留 dot2-b audio 覆盖；D4 compose up；D5 验 version/devices/errors/registered/audio_summary/say/ followup/LED；D6 异常回滚到 digest+plugins 备份。边界：不改 rui_compat、不 force-push、不动 token/设备端/data。
- **⚠ task-26 开工先解 lx06 矛盾**：lx06.yaml 声明 capture 48k/8ch，但生产实际/设备在跑 16k/8ch；直接 manifest 胜会弄坏客厅。先确认真实 wire，再修正 lx06.yaml 或改为“仅当探测与 manifest 一致时 manifest 胜”，选择写进卡面。冷启动时序（dot2-b 首测 ack=1200）必须在 task-26 复现并一并修。
- **0.8.0 发布门禁（2026-10-28 9哥 决策）**：必须完成 **task-26 → task-24 → task-25**、task-22 收尾（三台最终 params/补齐步骤/五台布局）、发布说明设置 **`audio.voice.followup_max_wait_ms = 0`**（全局，0=每轮都要唤醒词；8s=保留一句自然续说；不做逐台覆盖），以及四台真机验收（living 4412 + 儿童房 8870 + 次卧 8871 + 主卧 8873）。**C2/task-21 推迟到 0.8.1**（新功能，不阻塞）；task-2（歌没停）/task-3（158ms gap）无样本不修、不列阻塞。
- **生产现状（2026-10-28 16:0x）**：0.7.9 / 3 台在线 / device_errors 0 / warnings 0；living + 儿童房 dot2(8870, 16:03 重新上线 .69) + 次卧 dot2-b(8871) 全 running+connected。dot2-b 固件已对齐 **20261024-1911-dev + ref_uplink**（原 v2.18.1），server_a 保留可回滚；audio.say 237104 B、无 got 16000。两台 dot2 同固件同版本，4 台混合验收前置已具备。
- **dot2-b `devices[].audio` 覆盖 = 永久显式声明，不是临时补丁，别删**（playback 48000 / capture 16k·1ch / ack 2500 / aec off）；根治后保留。
- **P0 追加三点（2026-10-28 15:5x）**：
  1) **冷启动时序疑点**：dot2-b 初次生效 ack=1200（manifest 2500、全局未写 ack，优先级解释不了）；怀疑冷启动时 catalog/plug 未就绪，重启后 `plug_resolved` 才 true。task-26 必须复现“冷启动读一次 effective”，确认时序问题并一并修（否则根治后仍有间歇性）。
  2) **dot2-b `devices[].audio` 覆盖决定：保留**（等同显式声明该型号硬件参数）；根治后不删除，根修也不得覆盖/抹掉设备级显式值。
  3) **硬门槛：主卧/卫生间在 task-26 + task-24 收口（review PASS + verify VERIFIED + commit）前不插**。
- **dot2-b 已由 9哥 授权修好（2026-10-28 15:52）**：只给该设备加 `devices[].audio`（playback 48000 / capture 16k·1ch /
  ack 2500 / aec.enabled=false）；备份 `config.yaml.bak-20261028-155210`；PATCH `/api/v2/devices/dot2-b/audio` +
  restart scope=fleet（三台各 5.3s，客厅配置未变）。验证：audio_summary 48000/2500；audio.say 70670 B、gaps 0；
  "got 16000" 0 次；/health 3 台、errors 0。**全局那份照 LX06 写死的配置没变 → 主卧/卫生间新建仍会哑，根治卡必须做。**

- **task-22 存量 null-params 决策 = (b) 明确不兜底**：读配置不做隐式补齐（不改桥/adapter 默认、不改 schema 默认）；task-22 文档必须写「对已有 null-params 设备执行一次补齐」的手工步骤 + 三台最终值：
  living（lx06, 4412）params 不适用/留空；dot2（儿童房, 8870）`control_port: 0`、`loudness_file: /var/lib/rui/loudness-dot2.json`；dot2-b（次卧, 8871）`control_port: 0`、`loudness_file: /var/lib/rui/loudness-dot2-b.json`。
  生效时机：params 在平面启动时读取，补齐后需重启该平面；建议与 0.8.0 生产部署合并为一次重启。真机现状已记：3 台在线，dot2-b 在次卧 8871，日志原文 `dot2 adapter: loudness control on 127.0.0.1:8872 unavailable ([Errno 98] address already in use); continuing without it`。
- **task-22 实测输入（2026-10-28）**：生产 3 台 living 4412 / dot2 8870 / dot2-b 8871（次卧，已 ready+connected）；dot2-b 建时 params 留 null → `loudness control on 127.0.0.1:8872 unavailable (Errno 98)`。task-22 必须覆盖 dot2-b 的 `params.control_port: 0` + `params.loudness_file=/var/lib/rui/loudness-dot2-b.json`，并把「控制台新增设备 params 不得留空」做成表单校验/提示。设备端事实：配置目录 `/data/local/etc/echomuse/`，`controller.json` endpoints 为有序回退；平面 TLS 口 = 设备在 rui 的 `listen_port`（tcp/wss 同口，生产不开明文）。
- **P0b 追加证据（2026-10-28 12:1x，9 哥）**：拔电后设备 `connection open` → 正好 10s → `closed` 死循环；
  设备完成 TLS/WS 握手（1503/2932 B）但不再发 register；**重启 rui 后 3s 恢复、60s 0 断连**
  ⇒ 桥异常断线后没有清理连接槽位/状态（P0 根因）。另：僵尸连接 887s、Send-Q 85775B、
  `speaker_connected` 粘性 true；会话关闭后 15s 仍能免唤醒进 turn（P1，卡 task-17）；
  关闭日志无 reason/字节数（P2）。**task-15 需补：连接槽位/状态重建 + 重连注册测试 + close/register 日志。**
- **P0b（2026-10-28 12:1x 真机，dot2 掉线）：三件 rui 侧修复，插在 B1 期间/之后**
  A 连接关闭必须丢弃未发送音频（禁止重放半句）；B 发送水位上限（256KB 或 2s 音频）+ 写超时→主动断开重连；
  C 半开连接检测（TCP keepalive 或 ping_timeout 30–60s）+ `speaker_connected` 改实时。每项回归测试 + 双闸门。
  证据：8870 连接 887s 未收包、`notsent 65160/unacked 15/retrans 1/25/backoff 15`、积压 85775 字节；
  拔电时正在播报的 ~0.9s @48k 留在队列。A/B 在桥仓，C 涉及桥+house 音频面/健康状态。卡 task-15（已收口：桥 `9065d19`、house `88400aa`；两仓 review PASS + verify VERIFIED）、task-16（C，blocked_by B1）。B1 已收口 `7b9035a`。
- **批次 B 进行中（2026-10-28 14:xx）**：B3a dot2 `4536968`、B3b xiaoai `0d82250` 已 push（双窗口，179/87 passed）；B2 house 已收口 `78d87df`（console/setup/save_config 均拒绝 xiaoai，1752/6；review PASS + verify VERIFIED）。B3c core 0.8.0 已收口 `e8f728c`（1752/6；版本/窗口/meta/fixtures/README/CHANGELOG；C-002 已同步；review PASS + verify VERIFIED）。- **桥 v1.3.2 已发布（2026-10-28，9哥 授权）**：annotated tag `v1.3.2` → commit `4536968`（tag object `cebcb30`），已 push；GitHub release https://github.com/yes999zc/rui-dot2-bridge/releases/tag/v1.3.2（双窗口 >=0.7.0 <0.9.0，0.8.0 后仍是 0.7.x 最后一个桥，不承诺 0.9.x）。core 0.8.0 未发、生产未动。rui README quickstart 已改 v1.3.2（`9f9e6a6`）。
**三仓 0.8.0 代码面完成**（桥 dot2 `4536968`、xiaoai `0d82250`；core `e8f728c`）。C1 task-20 已收口 `1f02983`（语音固定句式 + Reminder.room + 三级路由；1804/6；review PASS + verify VERIFIED）。C2 task-21（抢占/去重/失败重排）待做。
- **task-23（插桩，2026-10-28）**：应答 gap 158–221ms（barge 打断→停止→告别语）只加 INFO
  `reply stall: chunk #N/total gap=...ms state=...` + trace meta 记 gaps 块序号；**不修逻辑**。part 1 已实现并提交 `ee26458`（日志 + per-reply ledger，review PASS）；part 2 trace meta 被 pydantic Metrics/进程边界挡住，需 protocol.py + trace.py 两个 additive 字段（且需防跨回复串味），等授权。等 9哥 真机复现后三选一。
之后交 0.8.0 发布前报告（桥双窗口计划+回滚）。
- **task-22（goal 外前置，2026-10-28 下午插设备前，已实现 1810/6；review CHANGES 8 项修正中）**：5 台真实布局文档 + v2 逐台 params
  （control_port=0 / 独立 loudness_file）写盘透传测试；不碰桥仓、不改 goal 四项。C1 收口后做。

- **批次 B（破坏性三项）/ C（§f.2 提醒）**：未开工。顺序 B1 → B2 → B3（桥双窗口先于 core 0.8.0）→ C；
  不打 tag、不部署，真机与插设备等 9 哥 下午做。
- 未完成项不宣称完成；上下文压缩后先读本节 + 任务板。

## 多音箱前置（P0，2026-10-28）

phase-close: task-4

**本阶段结论**：5 项 dot2 全局 env 已可按设备配；`max_planes` 默认 5；secrets/token 可逐台；
P0-4 同房/跨房同词逻辑已自动化并有计数。**代码已 push；生产已由 9 哥 激活**（bridge `c64acb5`、manifest 已装、
max_planes=5、`/health` 已确认，2 台 plane running）。验收可按 **3 台设备**（lx06 + 2×dot2）开始。

## 版本与契约

- rui core：`cf1cf92`（§e/§f 文档）← `1a751ae`（d.8）← `6412b3a`（P0 收口）← `75de4b4`（task-3）
  ← `a36f678`（max_planes 5）← `7b5ca57`；**未打 tag、未发版**。
- rui-dot2-bridge：`c64acb5`；**未 bump 版本**（plug 仍 1.3.1）、`rui_compat` 仍 `>=0.6.0 <0.8.0`。
- 契约：`rui-dot2-bridge/docs/contracts/C-001-per-device-params.md`（5 参数表 + 优先级 + 降级 + 回滚）。
- 生产（2026-10-28，9 哥 执行）：NAS `/vol1/rui` 仍跑 0.7.9 镜像；bridge 源码已 pull 到 `c64acb5`、
  插件清单已装到 `/vol1/rui/data/plugs/dot2-echomuse/`、config `max_planes=5`；`/health` 已确认 `max_planes: 5`、2 台 plane running。
- 第二台 dot2 的 token 已由 9 哥 刷成与第一台相同 ⇒ **P0-3 不需额外凭据工程**；共享 token 形态按 C-001 属可接受。

## 已完成

1. **P0-1** 5 项按设备可配：`adapter.py` ctor 4 kwarg + `params > env > 设备默认`；loudness 默认 `dev/rui/<id>/loudness.json`，EQ 共享可覆盖；control port bind 失败 warning 降级不 abort。证据：bridge `155 passed`、test_per_device_params 10 条、C-001。
2. **P0-2** `multi_device.max_planes` 默认 4→5（`schema.py:1068`，1–8 不变）；5 台通过、6 台按原文案拒绝；控制台中英文案同步。证据：house `1718/6`（task-2 时点）。
3. **P0-3** secrets 隔离：`dev > params > DOT2_SECRETS_DIR > secrets/<id>(存在) > secrets`；token 逐台 `token_env` + 各自 `device_token`；共享仍兼容。证据：C-001 + 两设备独立复现。
4. **P0-4** `arbitration_cross_echo_suppressed` 计数（coordinator.metrics）+ 同房 cross_echo/turn_open/同批 lost_same_word + 跨房 admit/409 测试 5 条（HEAD 上 5 failed → 当前 5 passed）。证据：house `1723 passed, 6 skipped`；全量曾因并发 pytest 抢临时端口 flaky 1 次（xiaoai bind），隔离/重跑均绿。
5. **文档/决策**：`backlog-0.8.md §e` 两项已 ANSWERED（e.1 全场停止 = **B**，词族待 9 哥 定稿；
   e.2 提醒落点 = 三级规则：指令房间 → `default_output` → 离线回落并明说）；§f.1/§f.2 为后续落地卡（只登记）；
   `d.8` 登记「唤醒后首句偶发空 ASR」；`docs/待拍板.md` D-012（语音跨房打断）、D-013（default_output 配哪台）OPEN。
   代码核查纠正：rui 已有 `ReminderStore`/`build_scheduler`（真实缺口是语音入口/抢占/指定房间/离线明说）。

## 下一步（3 台设备验收起点 2026-10-28）

1. **确认第 2 台 dot2 已进配置**：`/health.devices` 应为 3（lx06 + dot2×2）；若仍 2 台，走 `/api/v2/devices`
   加第 2 台：独立 `listen_port`（8871）、`room`、`params.control_port`（0 或唯一端口，如 8874）、
   `params.loudness_file` 指可写卷（共享 token 已由 9 哥 刷好，无需另配 secrets）。
2. 真机跑 `docs/release/acceptance-4devices.md` §6.5：同房交叉回声（A 播放→B 唤醒应 `cross_echo` + 计数）、
   同房同词同时唤醒只一台接、跨房同词三个最不利用例；**录音/人耳**判「是否打断/双声」，异常升级 0.8 缺陷单。
3. 3 台验收 PASS 后再插第 3 台 dot2（`listen_port` 8873），不要一次插三台。
4. P1：`d.7` volume 精确比较（加容差/仅配置变化写）。
5. 后续功能（不进本批）：§f.1 全场停止（等 9 哥 定词族 + 确认决策 2）、§f.2 语音定时提醒（等 D-013 定 `default_output`）。
6. D-012：跨房语音唤醒当前会 `runner.abort()` 打断当前回合，真机听感后决定 A/B，不许实现者自行改。

## 收尾（0.7.9，2026-10-27）

- **版本**：core **0.7.9**（patch）。版本号三处一致：`pyproject.toml` / `src/rui/__init__.py` / `uv.lock`（`07f40f4`）。
- **tag**：`v0.7.9` @ `715a3c2`；GitHub release <https://github.com/yes999zc/rui/releases/tag/v0.7.9>（notes 如实写明「意图层只经 headless 判定、真机未验」）。
- **镜像（GHCR `ghcr.io/yes999zc/rui:0.7.9`）**：OCI index `sha256:0f6c89ceb217d79471017bf24860dd74f1135f20b2d612e106277d5abc15bf05`；**amd64 平台摘要 `sha256:5aa362f6b4f7d7fd24a7f01d41c31e1523ce2401296ac3b203b9f684a314b9f7`**（NAS 老 docker 只认平台摘要，索引会报 `unsupported media type`）。README 的三处安装指引已换成该摘要（`ecf1342`）。
- **CI**：tagged commit `715a3c2` 四个 workflow 全 `success`（ci / security / secret-scan / docker-publish）。
- **生产（Bakey-NAS `/vol1/rui`）**：**已升级到 0.7.9**（2026-10-27 22:4x，9 哥 指示「对齐」后由 Lead 执行）。
  `rui-dot2-override.yml` 的 `image:` 已 pin 到 amd64 摘要 `sha256:5aa362f6…b9f7`（改前备份 `rui-dot2-override.yml.bak.before079`）；
  `docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d --force-recreate` 后 `healthy`，
  `/health` = `version 0.7.9 / devices 2 / device_errors 0 / audio_plane true / speaker_connected true / adapter_plugins registered dot2,lx06`。
  **回滚**：把 `image:` 改回 0.7.8 的 amd64 摘要 `sha256:1f074bb3…ecaa`（或 `cp -p rui-dot2-override.yml.bak.before079`）→ 同两条 `-f` 命令。
  **生产验收（2026-10-27 22:3x 核过）**：`/health` = `version 0.7.9 / console v2 / devices 2 / device_errors 0 / audio_plane true / speaker_connected true`，
  `adapter_plugins.registered ["dot2","lx06"] / warnings 0`；两台 plane（`living` 客厅、`dot2` 卧室）均 `running true / speaker_connected true / restarts 0 / error ""`；
  `GET /api/v1/config/catalog` → **200**，`ack` 的 `tier 2 / order 5`（tier2 首位）→ **v2 语音助手页第 3 位（assistant → session → ack）**，D-011 在生产生效。
  **注意**：容器已跑 0.7.9 代码；**C1/C2/C3/C5 与 D-011 目视已于 2026-10-28 真机验证**（N1/N2/N3/ACK-1/ACK-2，见下方），**仅 C4 措辞未验**（C7–C10 明确不做）。
- **0.7.9 真机验收已完成（意图层 + 控制台）**：**N1 / N2 / N3 / ACK-1 / ACK-2 全部 ☑**（2026-10-28 本地 08:54–09:33；容器 UTC 00:54–01:33；**日志时间为容器 UTC，本地 = +8h**）。剩余 **R79-1…6** 与 21 条仍待 0.8 窗口（第二台 dot2 到位后一起做）。
- **本批做了**：意图层批次 1（C1 `ddb7cbb`）+ 批次 2/3（C3/C4/C5/C6/C11 `34e3629`）+ D-011=A（`ack` order 200→5、**tier 未动** `6117fb7`）+ 审计两件（`f5d3f46`/`7fde6aa`）+ 验收/限制/backlog 三份文档（`4f75079`/`715a3c2`）+ 收口报告（`35dc556`）。
- **本批没做（明确不做，见 `docs/known-limitations.md`）**：C7/C8/C9/C10（不新增结束词）、HE-02 回声切除（D-010 不修）、D-011 的 v2 第 1 位（需 tier 2→1 或页内排序，归 0.8 P1）、控制台「唤醒应答独立成页」、桥的任何发版（`rui_compat` 窗口未变，dot2 1.3.1 / xiaoai 0.2.2 无需重发）。
- **最终 HEAD（0.7.9 收尾时）**：`47882ec`（其父 `8de395a` 上测得测试基线；当时工作树干净、已 push，`origin/main` 与本地 0/0）；**2026-10-28 的真机验收回写未提交**（4 份 docs）。
- **测试基线**：`uv run pytest` → **`1716 passed, 6 skipped`**（113.5 s，2026-10-27 22:5x 在 `8de395a` 实测；`ruff check src tests tools` 绿）。
- **容器状态**：`rui` `Up (healthy)`，`created=2026-10-27T14:28:06Z`（=22:28 CST），`/health` `version 0.7.9 / devices 2 / device_errors 0 / warnings 0`。
- **在途卡**：只有 `task-10`（HE-02）保持 blocked（D-010 不修，并入 0.8 真机窗口）；其余全部 completed，无 in_progress / pending。
- **决策记录**：`docs/待拍板.md` 的 D-001…D-011 **全部 ANSWERED**（含结论行）；本批内我按 9 哥 授权自行裁决的两处写在 D-011 与 backlog d.1（order-only 收、ACK-1 判据 = 首屏可见）。

- **C1 真机已验证（2026-10-28 08:54 本地；容器日志 UTC 00:54）**：`00:54:16 tool music_play({"query": "播放一首周杰伦的青花瓷。"}) → 好，放《青花瓷》`；`00:54:35 stop intent: '停止播放。' -> media stopped, session closed`（旧行为 `media stopped, session open`）⇒ C1 成立，`docs/acceptance-pending.md` 的 N1 已勾；**C2/C3/C5 与 D-011 目视同批已验，仅 C4 措辞未验**。**本轮不改的陈旧表述（统一口径，以本条目为准）**：`README.md` 中英两份（含 `:54`/`:170`/`:241`/`:357`）、`docs/release-candidate-0.7.9.md` 全文（含 `:16`/`:143`/`:191`）、`CHANGELOG.md` 的「真机未验」段（`:24-29`）——它们是对外文本/发版记录，待 0.8 发版时统一改；「停止」「拜拜」两句按同批真机确认，未单独留日志。

- **0.7.9 真机验收（2026-10-28 08:54–09:33 本地 / 容器 UTC 00:54–01:33）**：N1 ☑（`stop intent: '停止播放。' -> media stopped, session closed`）、N2 ☑（`停止你好` 走模型、会话未关；音乐由模型主动调 `music_stop`，非误判）、N3 ☑（有歌暂停 / 无歌回「现在没在放音乐。」）、ACK-1 ☑（v2 第 3 组首屏可见可改、旧面板第 1 位、保存后生效）、ACK-2 ☑（四组硬核在页尾）；**剩余 R79-1…6 + 21 条**待 0.8 窗口。

## 若以后重启：先读这三个文件

1. `docs/backlog-0.8.md` —— 0.8 的开工顺序（**桥先发双窗口 → core 后发**，顺序不可反）、三项破坏性变更、以及**从未验过的「双 dot2 同型号」验证组**。
2. `docs/acceptance-pending.md` —— 真机验收累计（21 条只能真机 + 补充 + 本阶段新增），每条有可照抄命令与通过标准；0.7.9 的项全部并入 0.8 窗口。
3. `docs/known-limitations.md` —— L1–L16 已知限制（含 HE-02 缺口、C7–C10 不做、0.7.9 真机已验：**C1 正负/C2/C3/C5/D-011 目视 @ 2026-10-28**；仅 C4 措辞未验）。

补两份按需查：`docs/release-candidate-0.7.9.md`（这批发版依据与回滚步骤）、`docs/待拍板.md`（已答结论）。

**接手自检（2026-10-27 收尾时核过、2026-10-28 按真机验收结论更新，三处都够用）**：上面三个文件覆盖 ① 0.8 开工顺序（`>=0.7.0 <0.9.0` 双窗口 → core，顺序不可反）② 破坏性三项 ③ 双 dot2 六组 ④ D-011 的 P1 结构项 ⑤ 真机未测项（**N1/N2/N3/ACK-1/ACK-2 已验 @ 2026-10-28**；R79-1…6 与 21 条未测）；`backlog-0.8.md` §d.3 已写明「inventory 36/26 vs acceptance-pending 21+补充+新增」的条数口径差异，执行以 acceptance-pending 为准。

## 历史：新会话入口（2026-10-27，0.7.8 收尾时的版本）

> **只读本节 + 它指出的文件即可接手**；下面的历史节按需查，不要通读全篇。

> **下次第一件事**：0.8.0 开工前先拍板 `docs/HANDOFF-0.8.md` §3 的未决问题；**所有待拍板项已汇总到 `docs/待拍板.md`（D-001…D-006，含背景/选项/代价/建议），拍板结论回写到那里再落 HANDOFF**。在那之前不要动 0.8.0 的任何删除动作。

### a. 现状事实（截至 2026-10-27 12:50）

- **版本**：core **0.7.8**；tag `v0.7.8`（`a3205e9`）；release <https://github.com/yes999zc/rui/releases/tag/v0.7.8>
- **镜像**：GHCR 索引 `sha256:ca3b40ecaf3457158603af90e520ac2bf423e2f4570e12e36db15b2583bf24c3`（tag `0.7.8` / `0.7`）；**生产 pin 的是 amd64 平台摘要 `sha256:1f074bb3af1ef4854ef040b12c9146ed8a6ef2b1c4a5f6cc7858ac6ccac3ecaa`** —— NAS 上的老 docker 不认带 attestation 的 OCI index，按索引摘要 `pull` 会报 `unsupported media type`。
- **生产（NAS）**：`version=0.7.8`、`console=v2`、`devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`、`warnings=0`；**一个核心镜像 + 两个外挂插件目录**（`/vol1/rui/plugins/`）。**本阶段未做任何生产变更。**
- **回滚**：本地旧镜像已删（9 哥 决定）；**回滚走 GHCR 上一个正式版** —— `docker pull ghcr.io/yes999zc/rui:0.7.4` → 取 amd64 摘要 → 改 override 的 `image:` → **两个 `-f`** `up -d --force-recreate`（步骤见 `docs/deploy-runbook-0.7.8.md` §8）。
- **三仓 HEAD**：rui **`35dc556`**（工作树干净；用 `git log --oneline v0.7.8..HEAD` 自查）；dot2 `8a236f3`、xiaoai `5f0a207`；**三仓 open issue = 0**（24 条已逐条回复并关闭）。
- **CI（rui `7b24d2c`）四绿**：ci / security / secret-scan / docker-publish 全部 `success`（收口后复测，满足收口报告 §6 条件 ① 的要求：最终 HEAD 也四绿；`f23e412` 同样四绿）
- **CI（rui `f23e412`）四绿**：ci / security / secret-scan / docker-publish 全部 `success`（12:17–12:22）；`35dc556` 是纯文档提交，写作时刚 push。更早提交里出现的 `cancelled` **不是失败**，是后续 push 的并发 run 把旧 run 顶掉（`cancel-in-progress`）。查当前状态：`gh run list -L 8 --json name,headSha,status,conclusion`。
- **进 main 未发版 64 个提交（`git rev-list --count v0.7.8..HEAD`，截至 `35dc556`）**（按「不升 minor」节奏攒着）。用户可见的：
  `91cac91` 控制台把 Home Assistant 与 MCP 服务器分两段（仅呈现层，API 未动）、
  `d2c9895` dot2 音量回读取 mixer 回报值、`e85b4bb` 「唱首歌」走随机 + 老链接落页提示、
  `3ed312f` `mcp <name>: down` 重连日志节流（行为变更，已记入 CHANGELOG「已知变更」）、
  `2977c3a` audio `close()` 的 `ENOTCONN(57)` 不再覆盖 plane 的真实失败原因（并修掉该竞态导致的子进程泄漏）、
  `8321099` 被吞的 ENOTCONN 现在留一行 DEBUG；
  其余为 handoff/readme/roadmap/runbook/audit 文档与测试（新增 `tests/test_mcp_down_logging.py`、`tests/test_he01_goodbye_echo_no_extra_turn.py`）。
  **生产镜像仍是 0.7.8 → 生产控制台看到的仍是旧界面。**
- **事实行的维护约定**：每完成 1–2 张卡，更新本节的 HEAD / 未发版提交数 / CI 三行，不要等到收口。

### b. 已定决策（**不要再讨论**）

1. **0.7.5 / 0.7.6 / 0.7.7 合并进 0.7.8**，0.7.8 是唯一验收节点；**不为单条修复升 minor**，修复进 main 攒着。
2. **小爱插件彻底外置**：核心不再内置任何设备实现；仓内 `plugs/` 与 `adapter_aliases.py` 已删；`adapter: xiaoai` 在**读入时**迁移为 `lx06` 并打 WARNING（`/health.adapter_plugins.warnings`），**0.8.0 删这段迁移**。
3. **0.8.0 只做破坏性变更**，且**桥先发双窗口版本、core 后发**（顺序不可反）。
4. **`docs/` 禁止真实内网地址**：用 `<NAS_IP>` / `<LX06_IP>` 占位；CIDR（如 `docs/security.md`）不算，扫描器不匹配。
5. **MCP 维持现状，不在 0.8.x 做 server 端**：rui 只有 MCP **client**（`tools.mcp_servers`，生产为空）；**Home Assistant 走专用桥 `src/rui/tools/ha.py`**（六工具白名单 + 危险域硬拒绝）——准确表述是：**HA 不进入 rui 的通用 MCP 服务器列表、不是 rui 的 MCP 实例；专用桥内部以 MCP 客户端连接 HA 自己的 MCP Server 集成**。设备能力不暴露 MCP。理由：HA 有专用桥更安全；外部 agent 直接调设备、绕道 rui 无收益。
6. **在线音乐「接口公开、实现私有」**：只做合规版（自建库 + 官方元数据/官方试听），**不做 VIP 换源、不接聚合解析站**（调研见 §「在线音乐调研」）。

### c. 扩展点地图

| 想加什么 | 改哪里 | 要点 |
|---|---|---|
| 音乐源 | `src/rui/music/source.py` 的 `MusicSource` Protocol（`search` / `random` / `favorites` / `star` / `resolve` **五个方法**）+ `src/rui/music/sources/presets.py` 注册；现成实现：`navidrome.py` / `subsonic.py` / `jellyfin.py` | 新 provider 的**配置字段名未定**（见 d-2） |
| 设备厂商 | 外置插件：宿主机 `plugins/<包名>/` → `-v ./plugins:/opt/rui-plugins:ro` + `PYTHONPATH=/opt/rui-plugins` + `RUI_ADAPTER_PLUGINS=<id>=<module>:build`；或 `<data_dir>/plugs/*` 自动扫描 | **不需要造下游镜像** |
| 外部工具 | `tools.mcp_servers[]`（HTTP / stdio；stdio 有开关闸；生产为空） | HA **不在此列** |
| 契约 | `docs/contracts/C-002-console-v2-api.md`、`docs/socket-plug-v1.md` | 三仓 `rui_compat` 必须同窗口 |

### d. 未决 / 待拍板

**全部待拍板项已集中到 `docs/待拍板.md`（D-001…D-006：patch 版要不要发 / 回滚演练窗口 / HA 是否摘出 / 音乐字段名 / 26 条真机协议 / MCP server 已否决），每条含背景、选项、代价、Lead 建议；拍板后回写那里再落本节。**

1. **MCP server 端**：**已否决**（见 b-5）；若以后要做需重新拍板，不要当成遗留缺口。
2. **在线音乐 provider 的加载字段名**（config schema 里叫什么）：未定，登记在 `docs/ROADMAP-0.8.md`（= 待拍板 D-004，建议 `music.sources[]`）。
3. **36 条「未验证」里 26 条真机协议**的处置（继续测 / 转已知限制 / 删）：见 `docs/audit/unverified-inventory.md`（= 待拍板 D-005）。

### e. 打磨清单（下一阶段工作面，逐条可验证）

1. ~~**flaky 清点**~~ → **本阶段进行中**：`test_plane_failure_is_reported_with_its_reason` 根因=close() 的 `wait_closed()` 重抛 macOS `ENOTCONN(57)`，把 `_greet` 的 AudioPlaneError 覆盖掉（详见任务板 task-1）。
2. **CI 时长与缓存**：四个 workflow 当前全绿，量一次时长再决定是否加缓存（本阶段 task-4 正在做）。
3. **覆盖率提升**：基线在 `docs/audit/coverage-baseline.md`，只准升不准降。
4. ~~**日志噪音**~~ → **已完成**：`3ed312f` 给 `mcp <name>: down` 加节流（首次/原因变化/恢复各一条 INFO，同原因每 5 分钟一条，其余 DEBUG）。
5. **错误文案与中英一致性**：控制台 v2 与 phrasebook 两层（phrasebook 只有中文，见 `src/rui/session/phrases.py` 头注释——原写法 `phrases.py:31` 少了目录）。
6. **文档与代码一致性抽查**：今天已修「HA 走 MCP」（task-25）与「dot2 带下游镜像」两处事实错误，说明这类错误还在。
7. **升级/回滚演练文档化**：runbook §8 已改成走 GHCR；下次发版前按它真跑一遍（= 待拍板 D-002，需窗口）。
8. **未验证清单处置**：同 d-3（= 待拍板 D-005）。

### f. 红线

- **真机出声测试须 9 哥 在场**（唤醒/播放/音量提示音都算）。
- **生产变更**：先备份 → 记录 digest → 写好回滚命令 → 部署后核对 `/health`；不留半升级状态。
- **仓库禁止真实内网 IP / 密码 / token**（占位符可以）。
- **不留半绿状态**：每张卡 review PASS + verify VERIFIED 才允许完成。
- **每条 compose 命令必须两个 `-f`**（少一个会解析到 `.env` 里过期的 `RUI_IMAGE`）。

### g. 本阶段进展（打磨阶段，2026-10-27 10:20–11:45；9 哥 已授权自主推进到 0.8.0 拍板前）

> 11 张卡，**10 张走完 review PASS + verify VERIFIED**，1 张按纪律 park（task-10，等 9 哥 拍板）。
> 全部提交已 push 到 `main`，**未打 tag、未发版、未动生产**。测试基线从 `1668 passed, 6 skipped` 变为 **`1672 passed, 6 skipped`**（新增 2 个测试文件、11 个用例）。

| 卡 | 内容 | 状态 | 证据（commit / 结论） |
|---|---|---|---|
| task-1 | audio plane 关闭路径 socket-close 竞态 | ✅ | `2977c3a`；独立 worktree 单文件 10/10、全量 3×绿 |
| task-2 | `mcp <name>: down` 日志节流 | ✅ | `3ed312f`；12 次同因失败 = 1 INFO + 11 DEBUG |
| task-3 | G0 事实校准 + 建 `docs/待拍板.md` | ✅ | `30ab5f2`/`a46fb68`/`663997f` |
| task-4 | CI 四 workflow 时长 + 决定 | ✅ | `3ea6726`；**维持现状**；候选（ci image 缓存 ≈30 s）推迟 |
| task-5 | 26 条真机「未验证」分类 | ✅ | `97e49bb`；**2 可转测试 / 21 只能真机 / 3 环境缺失** |
| task-6 | 文档 ↔ 代码事实核查（40 条） | ✅ | `ed3b648`；32 一致 / 5 已修正 / 2 疑似 / 1 需拍板 |
| task-7 | 覆盖率基线对比 + 提升方案 | ✅ | `44fc0c7`；行 +0.35pp / 分支 +0.22pp；**3 个真降全是删测试导致** |
| task-8 | 清 8 条遗留 nit | ✅ | `8321099`；被吞的 ENOTCONN 现在留 DEBUG |
| task-9 | 2 条「可转测试」落成真测试 | ✅（只交 HE-01） | `1c5583a`；**HE-02 落了测试才暴露真缺口** |
| task-10 | HE-02 回声切除漏 cue（lag 900–1200 / 尾部 1740 ms） | 🚫 **BLOCKED（D-010）** | 复现 `4 failed / 2 passed`；根因链已写卡面 |
| task-11 | 测试卫生（日志归属断言 / 常量注释 / 正向控制） | ✅ | `f7b2484`；过度切除变异**只红新用例**，证明其独有价值 |

**待拍板 10 条**（`docs/待拍板.md` D-001…D-010）。**发版/生产/真机出声/契约/改 README 承诺一律等 9 哥。**

**9 哥 回来后的第一件事**：看 `docs/待拍板.md`，优先拍 D-001（要不要发 0.7.9 patch）、D-002（给一个真机窗口跑回滚演练+真机验收）、D-010（HE-02 修不修）。

## 审计阶段（2026-10-27 12:0x 起，9 哥 交办）—— 只审不改，拍板后分批修

| 卡 | 内容 | 状态 |
|---|---|---|
| task-12 | **审计 A：意图层触发词冲突** —— 产出 `tools/intent_matrix.py`（一条命令重算矩阵）+ `docs/audit/intent-collisions.md`（每条冲突给 file:line / 短句 / 现状 / **按 9 哥 定稿的目标行为** / 影响面 / P0-P2 + 修复批次）。必须逐条解释 5 条生产日志（停止/停止播放/拜拜/停止你好 分别在有无歌时走了 media_stop 还是 session close） | 进行中 |
| task-13 | **审计 B：v2 控制台分组上下顺序** —— `docs/audit/console-ordering.md`；唤醒应答必须提到「语音助手」页第 1–2 位，`audio.capture/voice/playback/aec` 放最后，双入口（设备页/语音助手页）给唯一入口建议 | 进行中 |

**审计 B 的前提已由 9 哥 纠正（记 `docs/待拍板.md` D-011）**：v2 与旧面板是两套渲染。v2 事实：`web/src/v2/views/VoiceAssistantView.vue:74-81` 的 `SHOWN_GROUPS` 含 `ack` → **唤醒应答就在「语音助手」页、且是第 8 组（末位）**；`web/src/v2/config.ts:205 orderedGroups()` 不排序，按 catalog 数组原序渲染；生产 catalog tier 2 顺序 `devices(10)…audio.*(100-130) ack(200)` → ack 末位。**落点：改 `src/rui/config/catalog.py:127` 的 ack order 值（服务端顺序，v2 与旧面板都生效，前端不用动）**。审计初稿误用了旧面板 `PanelSpec`（`app.py:548-550`/`:581-593`）与旧面板排序（`ConfigCenter.vue:175`）。

**审计结果（2026-10-27 12:0x，两件都走完 review PASS + verify VERIFIED，**只审不改**）**：

- **审计 A（意图层触发词冲突）→ `docs/audit/intent-collisions.md` + `tools/intent_matrix.py`（commit `f5d3f46`）**
  11 条冲突 = **P0 2 / P1 3 / P2 6**。复算命令：`uv run python tools/intent_matrix.py`（两次运行字节一致，md5 `a4fe3bb1…`）。
  原现象解释：结束词 + 有歌 → `media_stop`（`app.py:966-984`，日志 `:984`），无歌 → session closed（`app.py:1013`）；`停止你好` 靠 `rules.py:281-283` 的「任意位置子串、总长最多 +2」命中。
  建议第一批：**C1**（结束词+有歌 → 停歌+关会话）+ **C2**（收紧容差，但显式 `ALIASES` 保住 `停止播放/别放了`）。
- **审计 B（v2 控制台分组顺序）→ `docs/audit/console-ordering.md`（commit `7fde6aa`）**
  **唤醒应答（`ack`）现状 = v2 语音助手页第 8 组（末位、不在首屏）**（`VoiceAssistantView.vue:74-87` 的 `SHOWN_GROUPS` + `web/src/v2/config.ts:205-215` 不排序 + catalog tier2 order 200）。
  **落点 = 改 catalog `ack` 的 `order`（`catalog.py:127`）+ tier**：只改 order 只能到第 3 位（assistant/session 是 tier1），要第 1 位需 `tier 2→1 + order 5`；代价两条：`tests/test_config_catalog.py:95-96` 必须同步改、tier1 契约语义（`catalog.py:41`/`docs/config-catalog-contract.md:68`）需 9 哥 接受。
  建议顺序：`ack → assistant → session → devices → audio.voice → audio.capture → audio.playback → audio.aec`；计数 P0=1 / P1=3 / P2=3 类。
  v2 的 `devices` 组**已是只读+跳转**（`DeviceConfigCard.vue`），重复写入口只剩 legacy 面板；`id` 不能改只读（唯一改名入口）。

**进入「可发版」阶段（2026-10-27 12:2x 起，goal 第 1 轮）**：三张卡并行推进，**不打 tag、不部署、不做真机**。
| 卡 | 内容 | 状态 |
|---|---|---|
| task-14 | 修复批次 1（C1 结束词+有歌 → 停歌且关会话；C2 收紧 `stop_intent` + 显式别名） | ✅ **completed `ddb7cbb`**；PASS（rev-nits 两轮，第 1 轮抓到「`_release_turn` 无人守护」）→ VERIFIED（ver-noise：全量 `1708 passed, 6 skipped`、两次变异、矩阵两跑一致） |
| task-15 | `docs/acceptance-pending.md` + `docs/known-limitations.md` | ✅ **completed `4f75079`**；PASS（rev-inv 三轮）→ VERIFIED（ver-docs：21 条 `comm -3` 无缺无多、27 行四要素齐、无敏感串/漂移行号） |
| task-16 | **收口报告** `docs/release-candidate-0.7.9.md` | ✅ **completed `35dc556`**；PASS（rev-cov 两轮，抓出「本批起点」数字口径）→ VERIFIED（ver-timings 实跑 1708/6、核四绿时间戳、没把 cancelled 当绿） |

**收尾批次（0.7.9，2026-10-27 12:5x 起，9 哥 全权授权、出门无人应答）**：目标=批次2+3 修复 → D-011=A → 文档 → 发版 0.7.9 → 结束会话；**禁止项只有：部署生产 / 动 NAS / 真机出声**。
| 卡 | 内容 | 状态 |
|---|---|---|
| task-17 | 批次 2+3（C5 暂停词收窄 / C3 无歌暂停提示 / C4 措辞 / C6 文案） | ✅ **completed `34e3629`**；PASS（rev-inv）→ VERIFIED（ver-noise：全量 `1716 passed, 6 skipped`、C3 变异 3 红、运行时 `end_words` 仍 21、`pause` 只剩 2 词） |
| task-18 | D-011=A：`ack` order 200→5（**tier 保持 2**）+ 顺序断言同步 + 待拍板收口 + acceptance-pending 并批 | ✅ **completed `6117fb7`**；PASS（rev-timings，抓出「tier≤2 实为 12 组」）→ VERIFIED（ver-inv：全量 `1716 passed`、变异红、web/ 零改动）。实测 v2 第 **3** 组（首屏可见）/ 旧面板第 **1** / tier≤2 全量第 6 |
| task-19 | `docs/backlog-0.8.md`（283 行，四节含**双 dot2 同型号 6 组**）+ known-limitations L1–L16 + README（中英，0.7.9 标「候选未发布」） | review **PASS**（rev-facts 三轮）；ver-docs 第 1 轮 FAILED 仅因**我授权改写在验证窗口内**（基线 14/1→17/4）+ `:29` 自述矛盾 → 改完后冻结 sha 复验 |
| 发版-prep | 版本号 0.7.9（`pyproject.toml` / `src/rui/__init__.py` / `uv.lock`）+ CHANGELOG `[0.7.9]`（含「真机未验 / 未部署」诚实注） | ✅ done（`07f40f4`，待与 tag 一起 push） |
| 发版 | 版本号 0.7.9（pyproject / `src/rui/__init__.py` / `uv.lock`）+ CHANGELOG + tag `v0.7.9` + GitHub release（**如实写：意图层行为真机未验**） | Lead 做，在 task-17/18 合入后 |

**已定（本批内 Lead 裁决，不需再拍）**：D-011 **只改 order、不动 tier** → v2 语音助手页 ack 由第 8 位升到**第 3 位（首屏可见）**，旧面板「音频与唤醒」tab 变**第 1 位**；要 v2 第 1 位需 tier 2→1 或 v2 页内排序 → 归 0.8 P1。**ACK-1 判据 = 首屏可见，不是第 1 组**。

**收口报告已交付**：`docs/release-candidate-0.7.9.md`（`35dc556`）—— 六节：可见变化 / 稳定性（v0.7.8 **1644** → HEAD **1708** passed, 6 skipped，+64） / 文档 / 验收清单 / 回滚（两个 `-f` + amd64 摘要警告） / **能不能发：可以发**，三条条件（最终发布提交四绿、N1/N2 真机验过、接受 C2 收窄且不宣称 ACK/HE-02 已修）。**未打 tag、未部署、未做真机**（2026-10-27 当时；真机于 10-28 补做）。

**已知未决（不阻塞本轮，但会影响「能不能发」的措辞）**：D-011（唤醒应答 tier 2→1 取舍）、C3（无歌说「暂停」是否算结束）、C10（别名算不算新增词）、D-001（要不要发 0.7.9 patch）、D-002（真机窗口）、D-010（HE-02 修不修）。

**9 哥 定稿的目标策略（审计判据，修复时照此执行）**：
1. 暂停功能保留，默认触发词**只有**「暂停」「停一下」；其他暂停词（先停这儿/停会儿/停住/停在这）从暂停表**删除**、不挪进结束词；用户可自定义。
2. 结束词表**保持现状不新增**。
3. **任何结束词被说出且有音乐在放 → 音乐停止 + 会话结束，两者一起做**（取消「有歌就变 media_stop 保持会话」这条分支，它是现象来源）。
4. 暂停 = 保留进度/会话继续；结束词 = 音乐清零/会话关闭；**没有中间态**。
硬边界：两件都**只审不改**；修复等拍板；不动生产、不做真机出声、不碰 0.8.0 破坏性变更。

**已知残留 nit（都不阻塞，建议合成一张 micro 卡）**：T3 的能量阈值按 1740/790 标定（换生产常量会误红）；T1 用字面 logger 名；`MCPClient` 用例未对称断言 name；`make_gate` 与 `test_cue_echo_excision.py` 重复（风格债）；`ci-timings.md` 两处措辞；`tests/__pycache__/test_he02_*.pyc` 陈旧字节码（gitignore 忽略）。

## 版本节奏（2026-10-27 09:10，9 哥 定）

- **不再为单条小修复升 minor**（0.7.9 不因「音量回复滞后」而发）。修复直接进 `main`，**攒够一批再统一发版**。
- 0.7.8 已是验收节点（tag/release/镜像）；之后进 main 的修复在**下一个正式版**才进镜像。当前生产跑的是 0.7.8 已发布镜像，**不含** task-23 的音量修复。
- 下一个版本的定位仍是 **0.8.0**（删 `/legacy` + 删 `legacy_adapters` 迁移 + 契约窗口调整），破坏性改动集中在那里；日常修复攒着，跟它一起或单独出一个 patch，由 9 哥 决定时点。
- 小修复清单（进 main 未发版，两张卡都已在改）：
  ① **task-23 dot2 音量回读确认**：根因是 dot2 桥 `read_volume()` 返回**上一次设备上报**（滞后一次），core 把旧值当新值；修法三态（回读=新值直接用 / 仍为旧值短轮询 6×80ms / 仍不确认则 `ok_unconfirmed` + 新目标值，**绝不报旧值**）。LX06 走第一支、文案不变。旧代码 4 条测试全红 → 新代码绿；全量 1648/6。
  ② **task-24 小修批**：`?from=` 落页提示（`App.vue` 读 query + `router.replace` 清 query + `data-testid="legacy-from-note"`）；「唱首歌 / 唱个歌 / 唱一首 / 来一首 / 来一段 / 唱一段」加进 `MUSIC_RANDOM_WORDS`（`rules.py:111`），12 条参数化先红 7 条后全绿；全量 1660/6。

## 支持矩阵补测（2026-10-27 08:54）

- **致谢**：README 中英新增完整 `## 致谢 / Acknowledgements`（`5360a14`）——按「实际在链路里」与「设计参考」分组点名：open-xiaoai（LX06 客户端与协议）、sherpa-onnx（ASR/KWS + 模型分发）、Silero VAD、SenseVoice/FunASR（中文 ASR 权重）、FFmpeg、speexdsp、openWakeWord（设备端 OWW 模型）；设计参考 Rhasspy / Wyoming / EchoMuse；音乐对接 Navidrome。许可证细节指向 `THIRD_PARTY.md`。
- **音量控制补测（真机，`debug/say`）**：
  - LX06（客厅）：`音量调到30` → 回「音量调到 30%了」，日志 `device volume set to 76`（设备刻度≠百分比，见下）✅
  - dot2（卧室）：**回复滞后一步** —— `调30`→回「100%」、`调40`→回「30%」、`调70`→回「40%」；但 `config.yaml` 写入正确（living `volume: 30`、dot2 `volume: 40`）。**结论：设置生效，回读/回复用的是变更前的值**（新 bug，待修）
  - 另注：LX06 的 30% → 设备刻度 76，映射关系需在文档写清（或按设备刻度暴露）
- **指示灯补测（人眼，2026-10-27）**：Dot2 配色 —— **唤醒=绿、思考=蓝、说话=青、播放歌曲=紫**；与机器侧事件链 `listening → thinking → speaking → off` 一致。LX06 未测。
- **多设备并发补测**：两台平面可同时 `running`（两台 `speaker_connected=true`）；但**同时发起第二个对话被 409 拒**（`a turn is already running`）——单会话 runner，属设计限制，已写进 README 矩阵。
- **矩阵三行已从「未测」改为实测**（`22d1ba2`、`c2b0bda`）。
- **LX06 指示灯补测（人眼，2026-10-27）**：与 Dot2 **同配色** —— 唤醒=绿、思考=蓝、说话=青、放歌=紫。矩阵两个 LED 格都已填实（`d8502c1`）。
- **音量刻度口径已写进 README**：控制台/语音用**百分比**；LX06 设备刻度 0–255（30%→76≈30×2.55，正常映射），dot2 用 0–100（`DOT2_VOLUME_CAP`）。属正常，不是故障。
- **task-23 在途**：修 dot2 音量「回复滞后一步」——方案 A（设置后回读确认，回读失败退回中性文案，绝不报旧值），要求先红后绿；若 dot2 桥 1.3.1 无回读接口则退化为「用新目标值回答并标注期望值」。

## GitHub 收尾（2026-10-27 08:50）

- **三仓 README 已按 0.7.8 现状更新并推送**：core `b2e5713`+`8879500`、dot2 `b1a4b4c`+`0bd8a53`、xiaoai `a3abc83`+`5dab49f`。内容：0.7.8 + **默认 v2 + 15 条旧链接 302**、外置插件的三段装法（宿主目录 / `-v ./plugins:/opt/rui-plugins:ro` / `PYTHONPATH` + `RUI_ADAPTER_PLUGINS`）、「不需要造下游镜像」、amd64 摘要 `1f074bb3…`、0.7.8 兼容表 + 0.7.4 历史、已知限制三条。verifier 抓到的两处（缺「默认 v2/15 条」字样、`registered` 口径写反）已修。
- **Hermes 的 24 个 OPEN issue 全部回复并关闭**（core 16 + dot2 5 + xiaoai 2，以及最后一轮补上的 rui#16）：
  - close(fixed) 4：rui#17/#18（`cf42695`，0.7.4）、#25/#28（`4468521`，0.7.4；#25 镜像 xiaoai `8f3ac46`，0.2.2）
  - close(duplicate) 1：xiaoai#1 = rui#25
  - close(not planned) 19：全部登记在 `docs/ROADMAP-0.8.md` §8（0.8.1 / dot2 1.3.2·1.3.3 / xiaoai 0.2.3 / backlog），回复正文点名小节
  - 回复包与核对命令：`docs/audit/hermes-replies.md`（每条带 commit/tag/file:line，中英双语，未承诺日期）
- 三仓当前 **0 个 open issue**。

## 0.7.8 已发布（2026-10-27 08:30）

| 项 | 值 |
|---|---|
| tag | `v0.7.8`（commit `a3205e9`） |
| release | https://github.com/yes999zc/rui/releases/tag/v0.7.8 |
| 镜像（多架构索引） | `ghcr.io/yes999zc/rui@sha256:ca3b40ecaf3457158603af90e520ac2bf423e2f4570e12e36db15b2583bf24c3`（tag `0.7.8` / `0.7`） |
| amd64 平台摘要 | `sha256:1f074bb3af1ef4854ef040b12c9146ed8a6ef2b1c4a5f6cc7858ac6ccac3ecaa`（**生产用这个**，本地 image `74f6c560…`） |
| arm64 平台摘要 | `sha256:93f9930ab1b553a25ffd0c61cdeb0a6c5cc63a8f747de29c0fb1d2d5f7b5fc32` |
| 生产 | NAS 168 跑上面 amd64 摘要；`ver 0.7.8 / dev 1 / err 0 / console v2 / speaker True / planes[卧室 dot2 running]` |

> **NAS 上的坑**：老版 docker 不支持 OCI index 里的 attestation manifest（`unsupported media type application/vnd.oci.empty.v1+json`），所以**按索引摘要 pull 会失败**；要用 **amd64 平台摘要**（`1f074bb3…`）或 tag `0.7.8`（同样会解析到索引，仍可能失败）。已在 override 里 pin amd64 摘要。

## 0.7.8 生产验收（2026-10-27 08:20，真实部署）

**部署形态**：官方镜像 + 外置插件，**不再造下游镜像**。
- 镜像 `ghcr.io/yes999zc/rui@sha256:2bcbbec0f9de86affee3f2330a96b8260d629d837e410ff1112487b1c9591308`（CI 在分支 `rc/0.7.8` 上构建的多架构产物，`rui 0.7.8`）
- 插件：`/vol1/rui/plugins/rui_dot2_bridge` + `rui_xiaoai_bridge`，只读挂到 `/opt/rui-plugins`；`RUI_ADAPTER_PLUGINS=dot2=…,lx06=…`
- 配置：`web.console: v2`；**客厅 `living` 与卧室 `dot2` 都开启**（08:31 起两台都 running）
  - 客厅显式写 `adapter: lx06`（不再依赖 `xiaoai` 迁移），所以 `adapter_plugins.warnings` 由 1 降为 **0**
  - `/health`：`devices=2`、`device_errors=0`、`planes=[living 客厅 running, dot2 卧室 running]`、两台 `speaker_connected=true`
  - **没有为客厅造任何新镜像**：插件是 `/vol1/rui/plugins/rui_xiaoai_bridge` 目录，容器只读挂载即加载（这是 0.7.6 之后与 `rui-dot2:1.3.1` 那套下游镜像的根本区别）

**客厅真机实测（08:33，人在客厅说的）**
- `08:32:57` 唤醒 →「现在几点了？」→「现在是早上8点33分。」ASR 269 ms / LLM 867 ms / TTS 803 ms / 播放起始 1761 ms，62 972 B
- `08:33:10` 「唱首歌。」→ 走了 `music_play` 工具，但曲库匹配失败（`曲库里没找到「唱首」`，phrase 解析问题，记一笔）
- `08:33:25` 与 `08:33:35` **两次打断**：`silence_after_ms=0`、`dropped 0.0 s`
- `08:33:30` 「放首歌。」→ `music transport: random (music.started)` → `playback requested: .../music_cache/...pcm` → 播起来了；随后 `08:33:39` 对话**抢占了音乐**（`output: dialogue preempts music`），符合预期
- `08:33:57` 静默 15 s → farewell

**镜像清理（08:34–08:38）**：先删分支构建的 `ghcr.io/yes999zc/rui:edge`；随后按 9 哥 指示删掉回滚胎 `rui-dot2:1.3.1`（`a62e2cf4`）。**NAS 上 rui 相关只剩 1 个镜像**：`ghcr.io/yes999zc/rui@sha256:1f074bb3…`（本地 `74f6c560`，运行中）。

> **回滚路径随之改变**：不再有本地旧镜像，回滚改走 GHCR 上一个正式版（`0.7.4` / `0.7.3` / `0.7.2` 都在），命令见 `docs/deploy-runbook-0.7.8.md` §8。
- 数据：纯净装后只恢复了凭据与音乐资产（`secrets.yaml`/`lan_media.json`/`library_index.json`/`music_aliases.json`/`music_history.json`/`setup.json`），日志/ack/DB/缓存保持全新

**验收结果**
| 项 | 判据 | 实测 |
|---|---|---|
| a healthy | Up (healthy) | `Up (healthy)`，`/health` 200 |
| b 设备识别 | devices/errors | `devices=1`、`device_errors=0`、`registered=["dot2","lx06"]`、`warnings=1`（`xiaoai→lx06` 迁移） |
| c 音频平面 | planes running | `planes=[{device: dot2, room: 卧室, state: running}]`、`audio_plane=true`、`speaker_connected=true` |
| d 真机走一遍 | 唤醒→指令→播放 | **通过（真实语音，3 个回合 + 打断 ×3 + 告别）**：08:17 唤醒 406 ms → ASR 211 ms（`现在几点？`）→ LLM 289 ms → TTS 700 ms → 播放起始 1084 ms，回复「现在是早上8点17分。」63 066 B；08:18 再次唤醒 → `今天周几？` → ASR 202 ms / LLM 480 ms →「今天周二，10月27号。」63 502 B（日期星期均正确）；打断 `silence_after_ms=3`、`dropped 0.0 s queued`（×3）；静默 15 s 触发 farewell；播放中唤醒被 `turn_open` 正确拒绝 |
| e 升级+回滚 | 各一次 | **已做**：升级到 0.7.8 → 回滚到 `rui-dot2:1.3.1`（重建，`a62e2cf4a0be`，`rui 0.7.2`，`devices=1/errors=0`，`/` 仍 302 `/v2/`）→ 切回 0.7.8 并恢复 `speaker_connected=true` |

**今晚踩过并修掉的三件事（记录，避免重犯）**
1. **arm64 镜像**：Mac 构建、NAS 是 amd64 → `exec format error`。现在只从 CI（原生双架构）取镜像。
2. **纯净装把凭据一起归档** → LLM/TTS 变「no ready provider」。恢复 `data/secrets.yaml` 即好；`data/` 归档在 `/vol1/rui/backups/0.7.8-preclean-20261027-075452/data-state/`。
3. **CI 红**：`tests/test_console_switch.py` 用了 PyYAML（项目依赖是 ruamel），已改（`7adf5c9`、`0993d08`）。教训：本地绿 ≠ CI 绿，测试里别引入非依赖包。


**时间类回答（记一笔，不是 0.7.8 回归）**：真实语音问「现在几点」答 8:17（正确）、「今天周几」答周二 10月27号（正确）；两次 `tools=[]`，说明时间/日期是模型从上下文拿的，没走时钟工具。08:26 那次 `debug/say` 答「8点16分」比实际早约 10 分钟，值得单独查一次时间注入的时机。

**未完成**
- ~~d 的真实语音回合~~（已完成，见上表）。
- **打 tag**：0.7.8 目前只有分支产物 `:edge`，`v0.7.8` tag / `:0.7.8` 镜像 / release 均未创建。
- 客厅那台（lx06）本轮关闭；`warnings=1` 来自它的 `adapter: xiaoai`，想消掉就把第 23 行改成 `adapter: lx06`。

## 0.7.8 状态（2026-10-27 02:20，进行中）

- **D 已合入 `efab571`**（task-17，reviewer-d PASS + verifier-tests VERIFIED）：
  - `web.console` 默认 **v2**（`legacy` 保留为一版回滚）；`/health.console` 两态实测。
  - **15 条老链接 302** 到 v2（9 个 panel + `/setup` + `/secrets` + `/diagnostics`；`providers`/`system`/`channels` 三个 v2 没有对应页 → `/v2/?from=<旧路径>`）；`/legacy/**` 永不重定向（无循环）；静态资源与 v2 深链不受影响。
  - `/legacy` 旧壳 + 中英弃用横幅（`data-testid="legacy-deprecation"`，CTA 到 `/v2/`），v2 不出现；legacy 模式下 `/` 也有横幅。
  - README hero 换 v2 设备页（`docs/assets/console-v2-devices.png`，无内网 IP/token）；中英「控制台入口」段。
  - 测试：**1642 passed / 6 skipped**；`test_legacy_links.py` 15 条参数化断言精确 Location；typecheck + build 绿。
- **task-18 文档收口已完成待评审**（工作树未提交，6 文件）：`docs/deploy.md` §3.8 改「默认 v2 / legacy 一版回滚」、CHANGELOG 0.7.8 段（写明「未写 console 键的老配置会静默变 v2」）、新增 `docs/release/v0.7.8.md` 中英、README 英文 hero、`legacy_links` 注释修正、`?from=` 已知限制。
- **版本口径（Lead 已定）**：**0.7.5 / 0.7.6 / 0.7.7 不单独打 tag** —— 它们的出口条件（Done 6 真机、双跑、升级回滚）要等 0.7.8 代码齐了一次做完；事后补 tag 等于假装「发版时已验证」。**0.7.8 是唯一验收节点**，CHANGELOG/release notes 必须覆盖四块（TLS、两台平面可见性、小爱摘出、审计+默认切 v2）。
- **task-18 已合入 `687d7de`**（PASS+VERIFIED）：deploy.md 控制台章节、CHANGELOG 四块（TLS/平面/摘出/审计+切 v2，六个基线数字齐全）、`docs/release/v0.7.8.md` 中英（升级 4 步 + 回滚 `console: legacy`）、README 英文 hero、`?from=` 已知限制。
- **task-19 已合入 `89a10f1`**（PASS+VERIFIED）：core **0.7.8**（pyproject/`__init__`/uv.lock 一致 + `uv lock --check` 绿）、README 中英 pin 0.7.8（`0.7.4` 只留历史行）、`/api/v2/meta` 实测 `rui_version=0.7.8` + `compat 0.6.0..<0.8.0`；全量 **1642/6**、ruff 绿。README 的 LX06 规则已按 0.7.6 事实重写（外置插件必需、旧拼写读入迁移 + WARNING、无别名无内置回退、0.8.0 删迁移）。
- **生产事实已实测并入 runbook（`dbb4ee0`）**：.168 只读核对得到——生效 compose = `docker-compose.yml` + `rui-dot2-override.yml`；**不带 `-f` 会解析出 `image: ghcr.io/yes999zc/rui:0.5.0`（`.env` 的 `RUI_IMAGE` 残留）**，`up` 会把生产切到错底座；当前镜像 `sha256:92ea98f4…`、health `0.7.2/2/0/legacy`、`/opt/rui-secrets` 五项齐、`/opt/rui-plugins` 只有 `rui_dot2_bridge`+`xiaoai`（老镜像层、无挂载）、config `devices[0] adapter xiaoai + plug_id xiaoai-lx06`。runbook 已加「外置 lx06 插件」四步与「镜像 docker save | ssh docker load」流程，执行人改为 9 哥。
- **task-20 已合入 `2e9da6c`**（PASS+VERIFIED）：音乐凭据路径不可写时 `PUT /api/v1/music/credentials` 从 **500 → 409**（`MusicCredentialsWriteError` 带路径 + OS 原因 + 修复句；写入与清空两支都覆盖；启动迁移语义不变）；1644/6、ruff 绿；审计 §8.1 条件性缺陷 **1→0**。release notes 与 CHANGELOG 的已知限制已同步改成「已修（409）」。
- **0.7.8 仍未打 tag**：出口条件是 Done 6 c/d/e 真机 + B 双跑 + 升级/回滚 + 人肉手册（白天）。代码与文档已全部就位，白天通过即可 `git tag v0.7.8` + release + 镜像。
- **下载/部署注意**：0.7.8 镜像默认 v2；生产升级前确认三件事（外置 lx06 插件挂载、TLS 材料、`multi_device`），命令见 `docs/release/v0.7.8.md` 升级四步。
- **白天要用的两份新文档（已提交）**：
  - `docs/deploy-runbook-0.7.8.md`（`e89a0c7`）：从 `rui-dot2:1.3.1` 升到 0.7.8 的人肉手册——备份 → 用 0.7.8 底座重建下游镜像 → 三个配置前置 → 切换 → 只读验收 → 回滚；末尾有「实测记录」表待填。
  - `docs/acceptance-done6-0.7.8.md`（`c68fb75`）：Done 6 的 a–e 验收表——每条给命令、期望值与记录位；**`registered` 的坑已写清**（镜像 catalog 不进 `registered`，以 `planes`/`devices` 为准）。
  - **runbook 已用真实构建校验过**（`868db10`）：`ghcr.io/yes999zc/rui:0.7.8` 要打 tag 后才存在，所以验收前用本地候选 `rui:0.7.8-rc1`（实测 `sha256:fe65d3a2…`）；下游镜像必须用 `deploy/downstream/Dockerfile`（仓库根没有 Dockerfile）；`rui` 没有 `--version`，要用 `python -c "import rui"`。本地链路已验证：下游镜像 `sha256:b4b40a88…`、`rui 0.7.8`、`/opt/rui-plugins` 含 `rui_dot2_bridge`。
- **另记**：`docs/release/`（0.7.x 实际目录）与 `docs/releases/README.md` 的路径口径不一致，留一次 docs 小修。
- **0.7.8 不发版**：剩余出口 = Done 6 c/d/e 真机 + 双跑对比 + 升级/回滚 + 人肉手册（全在白天）。

## 明早第一件事（2026-10-27 02:50）

### 1. 做到哪（A–F）
| 主线 | 状态 | 证据 |
|---|---|---|
| **A** 测试补齐 | 覆盖率基建 + batch-1 三条 + 配置台账完成；36 条「未验证」里 6 条变测试、4 条环境、26 条真机（协议已写） | 1644 passed / 6 skipped；覆盖率 80.95% 行 / 69.55% 分支（基线 `docs/audit/coverage-baseline.md`） |
| **B** 小爱摘出 | **代码完成**：内置 `plugs/xiaoai` 与 `adapter_aliases` 已删、配置读入迁移 + 告警、`wake_words_residual` 数据驱动、54 条设备语义测试移桥仓、核心 grep 0 | `c713916` / `c2ed7fd` / `300b2a9` / 桥 `0810885` `575b443`；剩双跑（真机）与删测试夹具（task-15，0.7.7） |
| **C** 控制台审计 | **完成**：172 行元素结论（159 可用 / 12 未验证 / 1 文案错误行 / 0 不可用 / 0 未处理）+ 76 张截图；条件性缺陷 1→0 | `db179f8` + `2e9da6c`；`docs/audit/console-functional.md` |
| **D** 默认切 v2 | **完成**：`/` 302 `/v2/`、`/health.console=v2`、15 条老链接 302、`/legacy` 弃用横幅、`console: legacy` 一版回滚 | `efab571`；`docs/release/v0.7.8.md` |
| **E** TLS + Done 6 | TLS 安装路径 + 三矩阵实测 + 两台平面可见性完成；**Done 6 c/d/e 未做**（真机） | `43d18df` / `258d72a` / `5e16026`；`docs/acceptance-done6-0.7.8.md` |
| **F** 每版纪律 | 0.7.3、0.7.4 已发；0.7.5–0.7.7 合并进 0.7.8（不单独打 tag，理由见下）；每卡 review PASS + verify VERIFIED | `efab571` / `687d7de` / `89a10f1` |

**版本口径（已定）**：0.7.5 / 0.7.6 / 0.7.7 **不单独打 tag** —— 它们的验收项（Done 6 真机、双跑、升级回滚）要等 0.7.8 代码齐了一次做完；事后补 tag 等于假装「发版时已验证」。**0.7.8 是唯一验收节点**。

### 2. 下一步（白天，按序）
1. 按 `docs/deploy-runbook-0.7.8.md` 在 NAS 上升级（本地候选 `rui:0.7.8-rc1` → 下游 `rui-dot2:1.3.1-r078`，链路已本地验证）；
2. 按 `docs/acceptance-done6-0.7.8.md` 走 a→c（只读）→ d（会出声）→ e（回滚一次）；
3. 全过后 `git tag v0.7.8` → `gh release create` → 记录镜像 digest → 回填 HANDOFF 发布表与 runbook 实测表；
4. B 双跑对比（0.7.2 内置 vs 0.7.8 外置，同一 LX06、同配置、字段级 diff）。

### 3. 等你（只有三件）
1. **Hermes 分诊确认**：`docs/audit/hermes-triage.md` §7（3 条 P0 降级、#27 否决去均值、#32 不改 pip 语义、版本重排）。
2. **两个口径**：①`adapter_plugins.registered` 不作 0.7.8 验收唯一判据（镜像里要同时带 `RUI_ADAPTER_PLUGINS` + `PYTHONPATH`），建议改看 `planes[].state`；②两音箱默认只起第一台，要不要把 `multi_device` 默认改开（影响单设备用户）。
3. **你亲手部署**：0.7.8 的最终验收由你做。

### 4. 已知限制（当晚新增）
- 自签证书只保证音频平面能起，真机接受取决于设备信任的 CA；
- 两音箱默认只起第一台平面（需 `multi_device`）；
- 未声明 `wake_words_residual` 的第三方清单不做 vendor 短语兜底（已知行为变更）；
- `?from=` 仅标记，v2 未消费（providers/system/channels 三个老链接落设备页）；
- 测试夹具 `tests/fixtures/legacy_lx06` 仍在（task-15，0.7.7 删）。

### 5. 生产
**今晚动过 0 次。** 当前仍是 `rui-dot2:1.3.1`（核心 0.7.2，本地镜像 ID `sha256:92ea98f4…`），`/health.console=legacy`。0.7.3 / 0.7.4 镜像已发布但**未部署**；0.7.8 未打 tag（故 registry 上没有 0.7.8 镜像）。

## 0.7.7 状态（2026-10-27 02:00，进行中）

- **C 上半（控制台功能审计）已合入 `db179f8`**（task-16，reviewer-audit 独立评审 2 轮 + verifier 独立验证）：
  - `docs/audit/console-functional.md`：**172 行元素结论 = 159 可用 / 12 未验证 / 1 文案错误行 / 0 不可用 / 0 未处理**；14 v2 路由 + 24 端点 + 异常态（401/409/422/429/500）+ v1→v2 差异 + 已知限制 + 复现命令。
  - 证据：4 个本地实例（主 18099 / 空屋 18101、写入 18103 / 配置恢复 18102），**76 张全页截图** `docs/audit/console-shots/`（25 MB，已入库）。
  - 4 组修正：setup 未加载 plan 不再误报「全屋无设备」、legacy 提示改「0.8.0 移除」、删 2 个死 i18n key、7 条中文校验 message 改英文（C-002 §4.4）。
  - 12 条「未验证」全部有原因与替代证据（真出声/真机拨入 = 夜间红线；keys/models = 无真实凭据）。
- **C 剩余（0.7.7 下半，未开工）**：①老链接 302 映射（`/panel/*`、`/setup`、`/secrets`、`/diagnostics`、`/channels` 15 条现在仍 200 旧壳）——**已决定挪到 0.7.8 D 第 5 项**；②`PUT /music/credentials` 在不可写路径返回 500 → 应转 4xx；③v1 未迁移清单（§7）逐条处置（本版只登记，不补功能）；④task-15（删 `legacy_lx06` 夹具）。

## 0.7.6 状态（2026-10-27 00:10，进行中）

- **B1a 已合入 `c713916`**（task-12，PASS+VERIFIED）：仓内 `plugs/xiaoai/` 删除（移到 `tests/fixtures/legacy_lx06` 测试夹具，不进镜像）、`src/rui/adapter_aliases.py` 删除；`adapter: xiaoai` 改为配置迁移（`config/store.py` 重写为 `lx06` + 弃用 WARNING + `.bak` 备份 + `/health.adapter_plugins.warnings` + 设备级 `legacy_adapter_spelling`）；默认 `adapter` 改 `lx06`（与旧行为等价、不告警）；Dockerfile 去掉 `COPY plugs`，`/opt/rui-plugins` 归 `rui:rui` 且构建期降权验证可写。全量 **1654/17**、`docker build` 绿。
- **B1b 已合入 `c2ed7fd` / 桥 `0810885`**（task-13，PASS+VERIFIED）：lx06 语义测试移到桥仓（**44 passed**）；核心 `src` 门禁 `xiaoai|lx06|小爱|4412` **0 命中**（仅 `legacy_adapters.py` 豁免）；4412 与旧拼写都收进 `legacy_adapters.py`（整模块 0.8.0 删）；**vendor 唤醒兜底改为数据驱动**：设备清单新增通用字段 `wake_words_residual`（lx06 声明三条），未声明则不过滤——已作为「已知行为变更（0.7.6）」写进 `docs/socket-plug-v1.md`。
- **`registered` 口径（实测）**：`adapter_plugins.registered` 只统计 `RUI_ADAPTER_PLUGINS` / entry point / `<data_dir>/plugs/*`，不含 `RUI_PLUGINS_DIR` 的 catalog；镜像挂外置插件**必须同时**带 `PYTHONPATH=/opt/rui-plugins`，否则 `registered=[]`。**0.7.8 验收看 `planes[].state=="running"` + `devices`/`device_errors`，不能只看 `registered`**。
- **B1c-1 已合入 `300b2a9` / 桥 `575b443`**（task-14，PASS+VERIFIED）：54 条设备语义测试（credit 5 / device-services 7 / kws 5 / led 18 / playback 1 / turn-audio 6 / vendor 11 / console-secret 1）**逐条字节一致**搬到桥仓；core 1655/17 → **1612/6**，桥仓 44 → **87/11**；`test_security_fixes` 通用 20 条留在 core。
- **B 剩余两件**：①**双跑对比**（0.7.2 内置 vs 0.7.6 外置，需 LX06 真机 → 白天）；②**删测试夹具** `tests/fixtures/legacy_lx06`（9 个通用测试仍依赖 lx06 注册/4412/params；需把 sample-plug 扩成替代 → **task-15，排 0.7.7**，不阻塞 0.7.6）。
- **0.7.6 暂不发版**：出口条件里的双跑要真机，按纪律不打 tag；白天补双跑后再发。
- **0.7.7（C 控制台审计）已开工**（task-16，交付 `docs/audit/console-functional.md`）：无头逐页逐按钮实测，夜间不接真机/不出声；基线清单位于 `docs/audit/console-audit-scope.md`（14 路由 / 7 tab / 24 端点 / ~95 元素）。
  - 2026-10-27 01:30 进度（Lead 维护，实现者只报事实）：`docs/audit/console-functional.md` 已成稿（4 个实例：主 18099 / 空屋 18101、写入 18103 / 配置恢复 18102，76 张截图）；172 条元素结论 = **159 可用 / 12 未验证 / 1 文案错误行 / 0 不可用 / 0 未处理**（空屋 first-run 的 prepare/params/create 已补测）；72→76 张截图；4 组文案修正（setup 空屋误报、legacy 提示、死 i18n key、7 条中文校验 message）。reviewer-audit 首轮 CHANGES：R1 config tab 的 restart 实为 `ok`（原写 failed）、R2 空屋 first-run 的 params/prepare 可测（原写被 keys/models 阻断），等修完后由 verifier 独立验证。
- 版本重排已写进 `docs/ROADMAP-0.8.md`（`b9a9059`）：0.7.4 = P0 修复、0.7.5 = TLS/Done6、0.7.6 = B、0.7.7 = C、0.7.8 = D。

## 0.7.5 状态（2026-10-26 23:35，进行中）

- task-9（TLS 安装路径）PASS+VERIFIED，提交 `43d18df`（house）/`1826e87`（dot2）/`1c1aa35`（xiaoai）：compose 挂 `./secrets`、自签脚本 `scripts/make_dev_tls.sh`、三仓 AI 提示词加 TLS 步。
- task-10（TLS 三矩阵实测）PASS+VERIFIED，提交 `258d72a`。**结论（已统一 README/审计文档）**：
  - dot2 的 `listen_port`/`tls_port` 开启时必须有 `secrets/server.pem|key`；缺 → `audio_plane=false` + `missing TLS material`；
  - 两桥同时配置时，**默认单平面模式只起配置里第一台设备**的平面（`multi_device.enabled=false`，`app.py:3077`）；两台都起需 `multi_device: {enabled: true, max_planes: 2}`（`_start_fleet`，单台失败不拖垮其他）；
  - 证书 + 两个 token 齐备（dot2 在前）→ `audio_plane=true` + `dot2 link: wss listening`；
  - 自签只保证平面能起；真机是否接受取决于设备信任的 CA（原 controller 证书丢失需重新配网）。
  - 6 次实测表 + 可执行复现命令：`docs/audit/tls-required-2026-10.md`。
- task-11（两台平面可见性）PASS+VERIFIED，提交 `5e16026`：`/health.planes` 逐台状态/原因（running|error|not_started；multi_device_disabled|plane_not_started|audio_not_started，含 error 文本）、`rui doctor` 在「2 台且 multi_device 关」时 WARN + 修复句、`config.example.yaml` 注释示例、C-002 A-8。7 条新测试；全量 **1659/17**。
- **0.7.5 状态**：代码与文档全绿，但 **Done 6 的 c/d/e（真机 audio_plane ×2、唤醒→指令→播放、升级+回滚）未做** → 按「出口条件未全过不打 tag」，0.7.5 **暂不发版**；白天补齐 c/d/e 后再发。
- **下一步（夜间）**：0.7.6 小爱摘出的**代码卡**（删 `plugs/xiaoai` + `adapter_aliases`、配置迁移告警、Dockerfile/pyproject 去耦）可以夜间做；双跑对比（需 LX06 真机）留白天。

## 0.7.4 状态（2026-10-26 23:15，已发布）

- 主题按 9 哥 指示改为 **P0/P1 修复**（`docs/audit/hermes-triage.md`）：原「TLS + Done6」顺延到 0.7.5。
- task-5（P0-A：rui#18 请求体上限 + rui#17 `_greet` 忙等）**review PASS + verify VERIFIED**，已合入 `cf42695`：全量 **1645/17**（连续 3 次），flaky 0/20，三态实测 413/401/422，greet ticker 865/s、冷启动 3.6 ms；反向变异 5 组。
- task-6（P0-B：rui#28 微信渠道 + rui#25/xiaoai#1 重连复位）**review PASS + verify VERIFIED**，已合入：house `4468521`、xiaoai `8f3ac46`。house 全量 **1652/17**、xiaoai **13**、dot2 **145**；两仓 adapter 等价（仅文件头/import 差异）；4 条新重连测试 + 3 条微信测试；变异各 1 组红后还原。
- **下一张卡（压缩后开）**：0.7.4 发布材料（版本 0.7.4 + CHANGELOG + `docs/release/v0.7.4.md` + README pin + C-002 A-7 已就绪），然后决定桥版本（dot2 1.3.2 / xiaoai 0.2.2）并打 tag。
- 已知小项（记录，不阻塞）：①`weixin` 的**流式累计超限分支**未被测试直接覆盖（现有测试走 Content-Length 预检）；②`getupdates` JSON 响应本身未设上限（本卡只限语音 CDN 下载）；③413 响应缺 `Permissions-Policy`（其余 4 个安全头齐全）；④xiaoai 仓测试借用 house 解释器。

## 0.7.3 状态（2026-10-26 22:05）

### 任务板（本会话）
| 卡 | 状态 | 说明 |
|---|---|---|
| task-1 `[impl] 覆盖率基建与基线` | **completed** | reviewer PASS + verifier VERIFIED + CI 四绿（`d21bb5f`）；commit `d21bb5f` |
| task-2 `[test] 未验证 batch-1（HE-03/HE-12/D2-06）` | **completed** | PASS + VERIFIED（verifier 自做变异并还原）；house `cd2d0a2`、dot2 `18ad114` |
| task-3 `[docs] 缺口台账 + 8 处矛盾` | **completed** | 两轮 CHANGES + verifier 两轮 FAILED 后修完；house `49df73f`、dot2 `517623d`、xiaoai `0889427` |
| task-4 `[release] 0.7.3 版本/CHANGELOG/release notes/README pin` | **completed** | tag `v0.7.3` + release + 镜像 digest 已记录（见下） |

### 0.7.3 发布（2026-10-26 22:05 起）
| 项 | 值 |
|---|---|
| commit | `c26cc46`（release: 0.7.3） |
| tag | `v0.7.3`（tag 对象 `13ab512`）已 push |
| 镜像 | `ghcr.io/yes999zc/rui:0.7.3` = `sha256:b6e8284fa688fe5327d9dff1ea259167c99aa95f06cc5d4360f5197c01652982`（amd64 + arm64，`docker manifest inspect` 已核；`:0.7` 与 `:latest` 同 digest） |
| release | `https://github.com/yes999zc/rui/releases/tag/v0.7.3`（非 draft、非 prerelease） |
| CI | tag `c26cc46` 上 **ci / security / secret-scan / docker-publish 四绿**（两个 docker-publish run 均 success：main + tag） |

### 数字（0.7.3 工作树，commit `cd2d0a2`）
- 测试四计数：**collected 1651 / passed 1634 / skipped 17 / xfail 0**（failed 0）；dot2 **145 passed**；xiaoai 9 passed。
- 覆盖率（本机 macOS/Py3.13）：行 **80.95%**（18429/22767）、分支 **69.55%**（5158/7416）、合并 78.15%。
- CI（`d21bb5f`）：ci / security / secret-scan / docker-publish **四绿**；artifact `coverage-3.11`（Python 3.11.17）/ `coverage-3.12`（3.12.15），CI 覆盖率 76.59% / 77.59%。
- 基线文件：`docs/audit/coverage-baseline.md` + `.json`（0.7.4 起不得回退的比较基准）。

### 未验证清单与审计范围（0.7.3 产出）
- `docs/audit/unverified-inventory.md`：36 条「未验证」（6 CI 可测 / 4 环境协议 / 26 真机），8 处文档矛盾终态，覆盖率缺口三分类，26 条真机三件套（命令+判据+记录表）。
- `docs/audit/console-audit-scope.md`：v2 14 路由 / 7 tab / 24 端点 / ~95 交互元素；v1→v2 32 行对齐；老链接映射表。
- `docs/audit/hermes-triage.md`：24 个第三方 issue 的分诊台账（**待 9 哥 确认**，见下）。

### 夜间纪律（9 哥 2026-10-26 21:25 授权，到明早）
1. 可做：生产 NAS 部署/升级/回滚（有纪律）、git push/tag/release、docker build/推镜像、本地容器、无头浏览器审计。
2. **红线：不许让真音箱出声**（TTS、音乐、唤醒音、对设备喊全禁）；听音类验收（音质/距离/打断/唤醒率）留白天。
3. 生产每次部署：备份 override+config（`.bak.日期`）→ 记录当前 digest → 回滚命令写进本文件 → 部署后核对 `/health` → 记录新 digest → 失败立刻回滚。
4. 每张卡完成更新本文件；60% 上下文前必须写一次；压缩失败最多重试 2 次并记进本文件。
5. 连续两轮无可推进 → pause goal，写清原因与「明早第一件事」。
6. 门禁不降：review PASS + verify VERIFIED 缺一不可；不合格 park 并写原因。

### 生产变更日志（今晚）
| 时间 | 动作 | 结果 | digest |
|---|---|---|---|
| — | 尚未动生产 | — | 当前生产 `rui-dot2:1.3.1`（本地 ID `sha256:92ea98f4…`，见下「版本与契约」） |

> 每做一次生产部署/回滚，在此表加一行（时间、动作、`/health` 结果、新 digest、回滚命令）。

### 待 9 哥 决定（明早）
1. **Hermes 台账确认**：`docs/audit/hermes-triage.md` §7 —— 3 条 P0 降级、#27 否决去均值修法、#32 不改 pip 语义、版本重排（0.7.4=P0 修复；TLS+Done6→0.7.5；小爱摘出→0.7.6；控制台审计→0.7.7）。
2. 0.7.3 发布号确认（core 0.7.3；两个桥本版不 bump）。
3. 白天真机项：Done 6 的 c/d/e、0.7.5 双跑、EQ 耳测、唤醒/打断复测。

### 白天项（今晚不做）
- 唤醒→指令→播放、音质/距离/打断、EQ 耳测、4 台联调、24h 稳定性满窗。
- 生产升级演练的「出声验证」部分（可先做不出声的升级/回滚 + `/health` 核对）。

### 0.7.4 发布（2026-10-26 23:15）
| 项 | 值 |
|---|---|
| commit | house `13469a4`；xiaoai `749d4d3` |
| tag | `v0.7.4`（tag 对象 `42262b4`）、xiaoai `v0.2.2` |
| 镜像 | `ghcr.io/yes999zc/rui:0.7.4` = `sha256:de26c5bacc00fbbb807da11d9eb1d7427125aab154703997d5a0131c23d8e749`（amd64 + arm64；`:0.7`/`:latest` 同 digest） |
| release | `rui/releases/tag/v0.7.4`、`rui-xiaoai-bridge/releases/tag/v0.2.2`（均非 draft/prerelease） |
| CI | tag 上 ci / security / secret-scan / docker-publish **四绿**（main + tag 两个 publish run 均 success） |
| 版本 | core 0.7.4；dot2 1.3.1（无改动）；xiaoai 0.2.2 |

## 版本与契约

| 项 | 值 |
|---|---|
| rui 核心 | tag `v0.7.1` @ `44cff05`（`v0.7.0` = `e9db990`）；HEAD `c825091`；镜像 `rui:0.7.1-a1p` = `sha256:f446e348e871e11404cb7d081952b458750a6f4a6d6d45512f64551cfdd5285c` |
| rui-dot2-bridge | tag `v1.3.0` @ `c71deb6`；plug `dot2-echomuse 1.3.0`（`ladder_correct=true`） |
| rui-xiaoai-bridge | tag `v0.2.0` @ `fcff8eb`；plug `xiaoai-lx06 0.2.0` |
| 生产 NAS 168（`/vol1/rui`） | **当前 `rui-dot2:1.3.0-p22`**（= r071 + P2-1 vad.py + task-18 按房间隔离，未打 tag；部署时序：r071 13:36、p21 16:08、p22 17:15，均 healthy）；上一个稳定组合 `rui-dot2:1.3.0-r071` = `sha256:252f568ca6b1d88aa2990e79856b7d8b143a7810bac452d294e9e5fcfbe7ecba`；底座 `ghcr.io/yes999zc/rui:sha-615e682` |
| 生产配置 | `/health.console=legacy`（M10 未切）；override：`DOT2_PLAYBACK_GATE_MS=0`、`DOT2_OWW_ON_DEVICE=off`、`DOT2_BARGE_IN_ENABLED=0`、`DOT2_CONTROL_PORT=8872`；config：`post_playback_mute_ms=2500`、`ack_echo_ignore_ms=2500`；备份 `rui-dot2-override.yml.bak.20261026-*` |
| 设备固件 | Dot2 client `fw 20261024-1911-dev`；二进制 `echomuse-dot2-ref/artifacts/server` sha256 `65f93891…` |
| 契约 | C-001 `rui-device/v1`；C-002/1（含 A-1..A-6）；兼容窗口 `>=0.6.0, <0.8.0`（三仓 manifest 已同步） |

## 已完成

每条一行，细节与证据见括号内文档。

1. 0.6.0 发布 + 真机验收（LX06/Dot2 应答、告别只从说话房间出）→ `docs/release/v0.7.0.md`
2. 内核剥离小爱（`src/` 仅剩 `adapter_aliases.py`）+ 小爱独立仓 Phase 1 被 rui 识别 → `docs/release/v0.7.0.md`
3. 控制台 v2 M1–M10（`/v2` 15 页，旧 logo 已迁移）→ `docs/release/v0.7.0.md`
4. Dot2 音质修正：100–300Hz vs 1–3kHz `+24.65 → +2.11 dB`、限幅 `−12.85 → −2.19 dB`、0 削波 → `docs/release/v0.7.0.md`
5. 播放中打断（A1'）：`GATE_MS=0` + 设备 AEC + RUI 中文 KWS，真机 8/8 `stop_play sent` → `docs/release/v0.7.1.md`
6. barge 唤醒不播 TTS cue（task-5），消除 barge 自循环 → `docs/release/v0.7.1.md`
7. P0 `turn_open` 泄漏：dismiss/退下后设备变聋 → 显式释放 + 20s 看门狗（task-9/10）→ `docs/release/v0.7.1.md`
8. 普通唤醒 cue 回声不再开 turn（task-11，envelope 匹配丢 cue 尾巴）→ `docs/release/v0.7.1.md`
9. 每设备测试历史隔离（task-2/7 + C-002 A-5）→ `docs/contracts/C-002-console-v2-api.md`
10. `/v2/tools` 排除 `tools.mcp_servers` + C-002 登记 `GET /api/v1/mcp` 只读例外（task-1/4）→ `docs/contracts/C-002-console-v2-api.md`
11. `rui_compat <0.8.0` 三仓 + manifest 覆盖测试（task-8）→ `docs/release/v0.7.0.md`
12. 代码健康审计 Top 10，2 条阻塞已修 → `docs/audit/2026-10-code-health.md`
13. 4 台接入 runbook（添设备 / listen_port / 验收字段）→ `docs/release/acceptance-4devices.md`
14. task-6 采样工具 `scripts/fleet_matrix.py`（日志 + 人工标记 → 矩阵，每个数字带 `--log` 行号证据，证明不了标「证据不足」；reviewer PASS + verifier VERIFIED，commit `9d0083b`）
15. task-6 P0 采样自动化 + 矩阵：`scripts/fleet_say.py`（唤醒词 + 1.5 s 后说指令、自动写 marks）+ `fleet_matrix --log-tz/--window/--wake-only-phrase`（commit `3c249f9`）
16. task-6 P1 归因：`docs/analysis/dot2-vs-xiaoai.md`（Dot2 30 句：唤醒 30/30 admitted、指令 26/30；两条掉分线索落到日志行；commit `850d19e`）
17. P2-1（task-15）：ack cue 回声不再进 ASR；真机 A/B **0/10 泄漏**（修复前 13/26）；commit `87bb704`，部署测试镜像 `rui-dot2:1.3.0-p21`（未打 tag）
20. task-16 分析完成（reviewer PASS）：`docs/analysis/wake-vs-command-range.md`，结论「唤醒链两台同量级、差距在 admitted→ASR；H2（播放后静音窗）被代码否证；H1/H3/H4 未做」；提交 `a301dce` + `2c88b0e`
21. task-19（P2-2 音乐中短指令）已开卡并把归因数据写进卡：音乐期 `[seg]` rms 中位 0.0232（比无音乐 0.0118 **更高**）、peak 到 1.000 → 失败模式是「音乐串音/削波」而非 mic 被静音
19. task-18（P1）播放态按房间隔离：一屋放歌不再饿死另一屋唤醒；提交 `3bbf93f`，reviewer PASS + verifier VERIFIED（1617/17），生产 `rui-dot2:1.3.0-p22`
18. 采样编排打通：Dot2 用 Mac Studio 播、LX06（`device_id=living`）用荣耀服务器（见 device-inventory.md）播，同一份 48k WAV、各自写 UTC 毫秒 marks，`fleet_matrix --log-tz UTC` 出两设备矩阵；设备接入索引见 `docs/device-access.md`（不含口令）

## 未提交

`docs/device-access.md`（新增，设备接入索引，无口令）

仅 `docs/HANDOFF.md`（本次整理 + task-13 收口记录，未提交）。2026-10-26 14:20 `git status --short` → `M docs/HANDOFF.md`；采样工具已单独提交为 `9d0083b`，三仓代码均已 commit + push。

**task-15（P2-1 ack cue 回声，第四轮返工完成待复评，文件已冻结未提交）**：窄匹配增加 `_quiet_after`（`vad.py:367`，cue 结束后 100 ms 必须安静，±20 ms 容差）：把「教过 EMA 后 mute 场景回复被误丢」挡住（回复比 cue 长、不会安静）；窄命中不再 `_learn_lag`（EMA 只由首轮高质量命中更新，`vad.py:381`）；日志 `kept` 区间改为 `min(skip+drop, hold)` 并新增 `match_offset`（`vad.py:493`）；`CUE_POST_SILENCE_MS=100`（:69）。新增测试：`tests/test_cue_echo_excision.py:450` 教 EMA(760/820) 后跑 mute 几何（cue 700/hold 1690/skip 950）→ captured **1.31 s**、`dropped=[0,950]ms`、fallback=false；`:482` 窄命中后 EMA 不变。无削波 850/950 仍 corr_full 0.030 / tail 0.353 / retained 0.91/1.00。全量 **1613 passed / 17 skipped**，ruff 绿；冻结 md5：vad.py `cf8e558c2d11b7be3041ebb16e0cd7ae`，test `412ecfb749d8488d9951fe0320b31f14`；process.py 未改（`176e8508…`）。

**task-18（P1 播放态按房间隔离，实现完成待门禁，未提交）**：arbiter 的 `turn_open` 增加 `turn_device`（`arbiter.py:187/343`），拒绝改为 `_in_open_turn`（:533，仅同房/同设备）；`_playing_reason`（:550）只在候选属于正在播放的房间时回 `lost_playing`/`cross_echo`，其它房间放行；pending 批量按候选逐条过滤（:480 保留 rejected 列表）。coordinator `set_turn_open` 同步房间、新增 `set_playing`（`coordinator.py:87/101`）；`_reconcile_owner` 把**所有**出声房间写入 playing（`app.py:1582`，无播放时清空 :1562）。旧全局单槽行为在测试中已更新（arbiter/app_fleet 各 1 处），新增 6 个隔离用例（`test_wake_coordinator.py:253/268/283/297/310`、`test_arbiter.py:233`）。反向：旧代码上 A 放歌→B wake `WakeReject`（应 admit）、A turn_open→B `turn_open`。全量 **1617 passed / 17 skipped**，ruff 绿。真机 A/B 由 Lead 执行。

**task-19（P2-2 音乐中短指令，实现完成待门禁，未提交）**：归因结论 H-A 不成立（音乐期高 rms/peak=1.0 的段都紧邻 `cue:`，见 fleet3:414/952；无 cue 的音乐段 rms 0.02–0.06、peak 0.2–0.23）、H-B **成立**（`wake_preroll_ms=500 > wake_skip_ms=300`，barge 时回放环里 200 ms 停播前音频进段；fleet3:596–601 barge→stop_play→seg，07:12:30–35 3.84 s 段串进告别语「没事的话，我先下啦」）、H-C 仅个别成立（07:04:01 rms .006、07:12:13 rms .0022 近似静音，非全局）。最小改动：`process.py:1895-1902` barge 唤醒不再回放预唤醒 ring（仍清空 ring），live 语音不受影响；非 barge 路径不变。新测试 `tests/test_music_barge_head.py`（barge 不回放停播音乐、非 barge 仍回放、stop_play ≤7 ms）。反向：旧 process.py 上 `assert MUSIC_PCM not in gate.fed` 失败。三仓：house-ear **1620 passed/17 skipped**、dot2 **143**（其 .venv）、xiaoai **9**；ruff 绿。真机 A/B 由 Lead。

**task-19（P2-2，第五轮返工待门禁，未提交）**：把 disarm 出口统一收口到 `process.py::_disarm_gate(reason)`（约 :2663）——`gate.armed` 时 disarm 并清 `_stale_speech_blocked`；`_disarm_gate_for_our_audio` 改走它（消除第四条漏清：reminder/console 语音 disarm 不经 `_feed_gate`）；`_feed_gate` 的 `armed -> disarmed` 转换也清位（约 :1959，覆盖 task-15 的 `_cue_excised` 静默超时，无 `idle_events`）；`idle_events` 变化清位保留。三处同一布尔赋值、均在事件循环单写者上，幂等无竞态。新测试 `tests/test_stale_speech_after_wake.py:170`（真实 vad 的 `_cue_excised` 超时 disarm → 后续 PLAY_PCM 入队）、`:214`（唤醒在听时我方 reminder disarm → 音频正常入队）；反向变异（去掉 helper 清位/armed 转换清位）→ 两用例分别 `AssertionError`。三仓：house-ear **1626 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。

**task-17（P2-1b ack cue AEC-lite，实现完成待门禁，未提交）**：已知参考（cue PCM）的分块逐频点 NLMS，`vad.py:451 _aec_lite`（`GateConfig` :121 起：frame=512/hop=256/taps=4/mu=0.5/passes=2/erle_min_db=12/residual_max=0.25/lag_max_ms=1500）；`:477 _aec_lag` 归一化互相关找延迟（分数<0.55 视为无 cue，音频不动）；`:496 _aec_filter` 逐 bin 4-tap 频域 NLMS + overlap-add；`:538 _aec_corr` 残差相关；`:704` 在 `_consume` 段末调用，残差 ≥0.25 时不产生 turn（降级），绝不把 cue 送 ASR；`set_echo_reference` 把 s16 cue 归一化为 float（:310）。四象限（lag 740/950 × clip × gap 30/300）实测 corr `-0.01…-0.001`、retained `1.000`；冷启动不依赖 EMA；mute 回归 pass。CPU 1.7 s hold 中位 **8.2 ms**（单核约 0.5%）。变异：关 AEC → 8 个四象限用例 corr=0.394 失败；关 `_consume` 集成 → segment never reached AEC-lite。全量 **1639 passed/17 skipped**、dot2 143、xiaoai 9，ruff 绿。风险：真实房间混响 >64 ms 时残差可能超限 → 保守丢 turn；真机 A/B 归 Lead。

**task-19（P2-2 音乐中短指令，第四轮返工待门禁，未提交）**：清位时机补齐——`_stale_speech_blocked` 现在在 `process.py:1962-1967`（`_feed_gate` 里 `idle_events` 变化，即 `wake without speech`/`WakeIdle`/超时 disarm）清除，也在本 turn utterance 落地时清除（:1988）；`_begin_wake_turn:1823` 在无 gate 时不置位。这样安静唤醒后 reminder/console debug say 的 PLAY_PCM 能正常播出。新用例 `tests/test_stale_speech_after_wake.py:117`（silent wake → 后续 PLAY_PCM 必须入队）；反向变异（注释掉 idle 清位）→ `AssertionError` at `assert plane._stale_speech_blocked is False`。已修好项保持：goodbye 已入队 chunk 仍被 flush/drop、farewell `say(keep_abort)` 早退、cue 正常。三仓：house-ear **1624 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。单会话限制已记：`runner.abort()` 全局 flag，A/B 两房共用 runner，跨房隔离仅 arbiter 层（task-18），不在本卡改。

**task-19（P2-2 音乐中短指令，第三轮返工待门禁，未提交）**：阻塞 1——plain wake 只 abort flag，已入队 goodbye 音频仍播放（07:12 30.65–30.97 已有 1.4–2.0 s 入 plane）。修法：`process.py` 新增 `_stale_speech_blocked`（唤醒置位、本 turn utterance 落地时清除，`_accept_play_pcm` 丢弃此间 PLAY_PCM，SpeakStart/SpeakFinished 同步忽略），`_begin_wake_turn` 开头 `_flush_speech()` 清空已入队；不用 StopAudio 因为会连 wake cue「在呢」一起停。阻塞 2——`turn.say()` 无条件 `_abort.clear()`：新增 `keep_abort=True`，farewell 用它并在 abort 已置位时直接不播；`farewell` 仍先 `idle.stop()`。app 侧保留「任何 wake 都 `runner.abort()`」。新测试 `tests/test_stale_speech_after_wake.py`（预置/迟到 chunk 均不进 gate，utterance 后恢复）、`tests/test_turn.py:780`（abort 后 farewell 零播放）。反向变异：丢弃 guard 后 `assert 0.10015625 == 0.0`；turn 移除 abort 早退后 `the aborted goodbye still played`。三仓：house-ear **1623 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。真机 A/B 归 Lead。

**task-19（P2-2 音乐中短指令，返工后待门禁，未提交）**：归因更正——07:12 headline 不是 barge：`:1110` 30.080 farewell 先触发、`:1111` 30.489 wake（TTS 未入队/barge=false）、`:1125` 30.969 起告别语 live 播放、`:1128` 3.84 s 段把告别语+「下一首」送进 ASR；`:596-601` 只是 H-C 近静音个例，H-B 不成立。修法：`app.py:2841-2848` 任何唤醒都 `runner.abort()`（不只 barge）中止上一轮/告别 TTS；`turn.py` farewell 改为先 `idle.stop()` 再 say，避免被中止的告别语事后清掉新唤醒的 idle 会话。撤销上一版 barge ring 裁剪（`process.py` 已还原，ring 仍回放，skip 之后用户首字保留）。新测试 `test_app_fleet.py:718` 普通 wake 必须 abort（旧代码 FAILED）、`tests/test_music_barge_head.py:104` barge 仍回放 ring（防回归）。三仓：house-ear **1621 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。真机 A/B 归 Lead。

## 已批 / 待批（2026-10-26 16:58 9 哥 全部按建议批准）

**已批（可继续，不用再问）**
1. **对外版本号**：暂不打 tag；等 task-16/17/18/19 收口后一次性定，建议 rui `0.7.2` + dot2 `1.3.1`（P2-1 是行为修复），**打 tag 前仍要 9 哥 一句确认**。
2. **M10 默认切 v2**：**先不切**，等 4 台联合验收通过、9 哥 点头。
3. **P2-2（音乐中短指令拾音）已批准** → task-19。P2 上限 2 项到此为止（P2-1 已完成，P2-2 在做）。
4. **`lost_playing` 跨房间饿死：发布前必须修** → task-18（P1）。

**仍待批（不阻塞当前工作）**
- 4 台新设备到位后的添加/验收（需要 9 哥 手动加设备）。
- 24h 稳定性结果出来后是否接受。

## 下一步（按序）

1. **task-6（阻塞发布）**：P0 矩阵 + P1 归因**已交付**（`docs/analysis/dot2-vs-xiaoai.md`，commit `850d19e`；Dot2 唤醒 30/30、指令 26/30）。**当前卡在 9 哥 选 P2（≤2 项）**，选定后各开独立 `[impl]` 卡，过门禁 + 9 哥 A/B，再回来 1 条收口。
   - P2 候选两条（详见分析文档 §6）：**P2-1** ack cue 回声抑制加强（`vad.py`+`process.py`，消除「唤醒后立刻说指令被『在呢』抢答」，13/26 次污染）；**P2-2** 播放中短指令拾音（dot2 bridge AEC + `post_playback_mute_ms`，音乐里「下一首」2/5）。
   - 采样工具已就绪：`python scripts/fleet_say.py --rounds 5 --rate 190 --gap 12`（默认已带唤醒词 + 1.5 s 停顿），矩阵：`python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks <marks.csv> --log-tz UTC --window 12`（日志时区是 UTC，必须带）。
   - **LX06 那一路仍未采**（不同屋、Mac 音源覆盖不到）：需用同一份 `say -o` 音频在它房间放一遍，或 9 哥 人声复测。
   - **P1 收口时必须做的事（易忘）**：task-6 写范围只有 `docs/analysis`，P2 的代码改动**不得**塞进 task-6；P1 归因一 PASS，就把选定的 P2 拆成 1–2 张独立 `[impl]` 卡（带各自代码 write_scopes + 门禁 + A/B），并把 `task-12.blocked_by` 更新为 `task-6 + 全部 P2 卡`。
2. **4 台联合验收**：两台新 Dot2 到位 → EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT=8872` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除。步骤见 `docs/release/acceptance-4devices.md`。
3. **M10 默认切换**：等 9 哥 点头；验收 `/health.console=v2`、`/` 302 `/v2/`、`/legacy` 200，回滚一行。
4. **24h 稳定性复查**：自 **2026-10-26 17:15（p22 部署）** 起计；提醒已设 2026-10-27 13:10（将改为 17:40）；结果补进 `docs/release/v0.7.1.md`。注：13:36 那一轮因中途多次 recreate 已作废。
5. **task-12 发布收口**：task-6 完成 + 发布号经 9 哥 确认后，更新本文件 → `phase_close` → complete。

## 采样进度（2026-10-26 16:40）

- Dot2 腿：Mac Studio `afplay /tmp/fleet6/*.wav`（同源 48kHz WAV），5 轮 × 6 句，marks `/tmp/fleet6/marks-dot2b.csv`。
- LX06 腿：荣耀服务器 `aplay -plughw:2,0`，同一份 WAV，marks `/tmp/fleet6/marks-lx06.csv`（`device_id=living`）。
- 日志：`/tmp/rui-fleet5.log`（LX06 腿）与 `/tmp/rui-fleet6.log`（Dot2 复跑腿，2000 行窗口、3 s 轮询）。
- **电平实测**：`afplay -v 1` → 唤醒 5/5；`afplay -v 2` → **3/5**（更响反而更差，削波伤 KWS）——与 task-16「距离/电平」假设直接相关。


## 多设备互斥实测（2026-10-26 16:44–16:50，两屋同时说同一唤醒词）

- 拒绝分布：`lost_playing` **18 次**、`turn_open` **4 次**；admitted：`living` 11、`dot2` 5。
- 机制：一屋在放音乐（`living` 侧 `barge: N wake during playback`）+ 一屋有未结束的 turn 时，另一台设备的唤醒会被拒（`turn_open` 由协调器全局持有）。所以「Dot2 卡住」不是设备问题，是**串行仲裁**。
- 结论：**采样必须串行**（一次只开一台音源）；同时这是 4 台「互不串台」的 P1 候选（`lost_playing` 让另一台完全无法工作，需要按房间隔离播放态）。
- 采样脚本改为：单台、顺序、去掉 `放首歌/下一首`（避免自己把 mic 压住），只用 `hello/time/stop/bye` × 5 个电平 × 2 轮。

## 最近状态（2026-10-26 17:55，压缩前）

- 生产 `rui-dot2:1.3.0-p22` healthy，`RestartCount=0`，started 17:15:58；两台设备 `living`/`dot2` 均 connected+ready。
- 24h 稳定性提醒已改到 **2026-10-27 17:40**（schedule-80a81833），从 17:15 起计。
- 任务板：completed = task-1/2/4/5/7/8/9/10/11/13/14/15/16/18；进行中 task-19；未开工 task-17；release 卡 task-12 blocked_by = task-6/16/17/18/19（16、18 已完成）。

## 最近状态（2026-10-26 17:25）

- **已上线测试镜像** `rui-dot2:1.3.0-p22`（r071 + P2-1 + task-18）；回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **task-18 真机回归待跑**：两屋同时放歌 → 各自唤醒各 5 次，期望两屋都能应答、`lost_playing` 只在同屋出现。
- **task-16 分析稿已提交**（`a301dce`）：`docs/analysis/wake-vs-command-range.md`；核心发现：唤醒链两台同量级（0.75→0.15 区间），差距在 admitted→ASR；H2 被代码否证；「afplay -v 2 → 3/5」仅为现场手记（低强度），不证明削波阈值。
- **task-19（P2-2）已开工**（impl-barge-ack）：先判 H-A/H-B/H-C 三条假设、再选最小改动；P2 上限 2 项已满。

## 当前状态（2026-10-26 17:30，压缩前快照）

- **生产镜像**：`rui-dot2:1.3.0-p22`（r071 + P2-1 vad.py + task-18 按房间隔离），17:15 部署、`/health` 正常、2 台设备在线。**未打 tag**；回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **已提交**：`87bb704`（P2-1）、`3bbf93f`（task-18）、`3b3b0d1` / `8ee75a1` / `c3eba68`（文档）。全部已 push。
- **已过门禁**：task-15（PASS + VERIFIED）、task-18（PASS + VERIFIED + 真机回归）。
- **task-16 分析稿**：`docs/analysis/wake-vs-command-range.md`（已提交 `a301dce`），已按 reviewer 两轮意见全部修正；采样镜像锁定 p21，数字全部重算（Dot2 0.15 = cand 5/adm 4；LX06 0.15 = cand 6/adm 4），H2 已被代码否证（`post_playback_mute_ms` 只作用于有 TTS 回复的 follow-up）。
- **进行中**：task-19（P2-2 音乐中短指令拾音）。**未开工**：task-17（AEC-lite）。P2 上限 2 项已满（P2-1 完成、P2-2 待做）。
- **task-18 真机回归结果**：客厅放歌时卧室 Dot2 连续 4 次 admitted、`lost_playing` 0 次（修复前 6 分钟 18 次）；但音乐中 ASR 只剩碎片（`'了。'`）——归 task-19。
- **采样环境**：Mac（Dot2 房间）用 `afplay`，荣耀服务器（客厅，见 device-inventory.md）用 `aplay -plughw:2,0`；两台 NTP 同步，跨机对齐不确定度 ≤150 ms；**采样必须串行**（两屋同时说同一唤醒词会互相饿死）。
- **待批**：4 台新设备到位后手动添加；打 tag 前的版本号确认；24h 稳定性结果。

## task-19 收口（2026-10-26 18:05，**第 5 轮为 timebox 上限，9 哥 已定**）

- **门禁**：reviewer-barge **PASS**（出口穷举：process 内唯一裸 `gate.disarm()` 在 `_disarm_gate:2661`，无第五条绕过）；verifier-tools-2 **VERIFIED**（真实 vad `_cue_excised` 静音超时 → `blocked=False`、`queued_s=0.040`；我方音频 disarm → 同样放行；幂等两出口 `queued=2`）。全量 **1626/17**、ruff 绿（I001 已修）。
- **实现**：`process.py:508 _stale_speech_blocked` + `:2647 _disarm_gate(reason)`（唯一 disarm 出口，armed 时清位）+ `_flush_speech()` + `_accept_play_pcm:2942` 丢迟到 PLAY_PCM；`turn.py:1127 say(keep_abort=False)`、`:1135` abort 已置位则 return 0；`app.py:2841-2848` 任何 wake 都 `runner.abort()`。
- **提交**：`989baa1`；镜像 **`rui-dot2:1.3.0-p23`**（18:00 部署，healthy、2 台在线、未打 tag）。回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **真机 A/B（18:00–18:02，第一次）结果不可用**：10 次「唤醒+指令（音乐中）」只 3 次 admitted、1 条 turn（transcript `有点。`），`rejected=0`。判断为**采样协议问题**（`afplay` 起播后 8 s 即说唤醒词、音乐+TTS 混叠、且句间节奏与 `stop` 后音乐重启重叠），**不是结论**。需要按「每轮先确认播放态、唤醒前留足稳定播放、指令用 1.5 s lead」重跑，并用 `fleet_matrix` 出表后再下结论。
- **已知限制（不再扩范围，发布后开卡）**：① `runner.abort()` 是全局单 flag，B 房唤醒会中止 A 房正在合成的回复（runner 单会话）；② 已播出的告别尾音无法追回（只能阻止后续 chunk），彻底消音需「本机 TTS 参考消减」；③ `_feed_gate:1962/:1967` 是直接清位而非调 helper（功能等价，表述差异）。

## 音源校准（9 哥 2026-10-26 18:10 实机反馈，**强制前置**）

**9 哥 真人试 Dot2 的结论**：音乐播放中「下一首」**是可以的**，只是要离麦克风近一点；并指出 **Mac 播放的测试音太小**。→ 早期采样存在**系统性激励偏差**，task-16 / task-19 此前「唤醒灵敏、指令要凑近」的结论**可能部分或全部是装置伪影**，在重新校准前不得作为产品结论引用。

必须做（不是可选）：
1. **校准音源**：目标 1 m 处 ≈ **60 dB(A)**（正常人 1 m 说话）。
   - Mac 系统音量 100；音频文件峰值归一到 **−1 dBFS**（不削波，用 `ffmpeg -af volume=...` 或 `loudnorm` 后校验 `peak<=0.891`）。
   - 用手机 dB 表在 1 m 处实测并**记录数值**（写入分析文档）。
   - 不够响就换**荣耀服务器喇叭**（`aplay -plughw:2,0`）或蓝牙音箱当音源。
2. **校准后重跑**：① task-16 距离梯度 1 / 2 / 3 m，每档 5 次；② 「音乐播放中 + 短指令」Dot2 与 LX06 各 5 次；两次都用 `fleet_matrix --log-tz UTC` 出表。
3. 所有分析文档必须写明：**音源是什么、音量/声压级多少、怎么校准的、距设备多远**。
4. 「早期采样音源偏小」作为**已知偏差**写进 `docs/analysis/*` 的限制段（不悄悄改结论）。
5. **结论更正规则**：校准后若「唤醒灵敏 / 指令要凑近」消失 → 原结论是装置伪影，在 HANDOFF 与分析文档里**显式更正**；若仍存在 → 才继续查增益/AEC/VAD。

**同时作废的旧 A/B**：18:00–18:02 那次「音乐中 3/10 admitted、1 条 `有点。`」按本偏差处理，不作为 p23 的验收数据；校准后重跑才算。

## 结论更正（2026-10-26 18:20，按 9 哥「先校准再下结论」）

**「唤醒灵敏、指令要凑近」是装置伪影，已显式更正。** 校准后（WAV 峰值 −1.0 dBFS、Mac 音量 100、以设备侧 `[seg]` 自检 rms 0.02–0.10）用**真人**在 Dot2 上复测：

| 距离 | 唤醒 | 「几点了」 | 「放首歌」 | 「下一首」 |
|---|---|---|---|---|
| 1 m | 12/12 admitted、0 拒绝 | 3/3 | 3/3 | 3/3（换歌，无文本：`music owns the speaker`） |
| 2 m | 9/9 admitted、0 拒绝 | 3/3 | 3/3 | 3/3（同上） |

- 早期 TTS 采样的语音段 rms 只有 **0.006–0.02**（比校准后低 6–20 dB），9 哥 也判「Mac 测试音太小」→ §1–§6 的「差距在 admitted→ASR」只在低电平激励下成立。
- 详细数据与更正写进 `docs/analysis/wake-vs-command-range.md` §7。
- **task-19 的 A/B 作废重做**：必须用校准后的人声；「下一首」允许以 `playback requested` 作为功能成功，文本仅辅助。

## task-19 真机 A/B（2026-10-26 18:20–18:29，**用人声 + p23 通过**）

装置：Dot2（卧室），人声（9 哥），1 m 左右；音乐由设备自己播；日志 `/tmp/rui-p2p2.log`（`--log-tz UTC`）。

| 组 | 做法 | 结果 |
|---|---|---|
| 下一首 ×5 | 音乐播放中「你好小瑞 → 1.5 s → 下一首」 | **5/5 换歌成功**（每次 barge `silence_after_ms 2–7 ms`；`playback requested` 5 次） |
| 停止播放 ×5（用户自控放歌/停歌） | 「放首歌」→ 音乐起 → 「你好小瑞 → 停止播放」，重复 5 轮 | **5/5 命中**：`stop intent: '停止播放。' -> media stopped` 共 5 次（10:27:10 / 27:33 / 27:53 / 28:16 / 28:37）；seg rms 0.025–0.050、peak 0.19–0.53（无削波） |

- **合计 10/10 功能成功**，达到卡面 ≥4/5 的判据；本批 transcript 为空是设计使然（`music owns the speaker`），不算失败。
- 之前两次「2/5」「3/10」作废：一次是 TTS 音源过小（装置伪影），一次是我用 TTS「放首歌」当起歌手段、与用户说话撞车（协议伪影）。**真机判据必须以本批（人声 + 无 TTS 干扰）为准。**
- 结论：P2-2 的 headline case（音乐中短指令）在 1 m 人声下**可用**；2 m 音乐场景未测（待 4 台验收时补）。

## 发布决定（2026-10-26 18:40，9 哥 已定，不用再问）

- **版本**：`rui` **0.7.2** / `rui-dot2-bridge` **1.3.1** / `rui-xiaoai-bridge` **0.2.1**；三仓 **MIT**。
- **顺序**：① 清工作树 → ② xiaoai 全历史扫描 + 公开化（task-20）→ ③ 三仓 README 产品级重做（task-21）→ ④ CHANGELOG/tag/release + 发布后核对（task-22）→ ⑤ 宣传素材最后做。
- **README 规范**：中英双语、统一 10 段结构、架构图矢量工具（源文件+PNG/SVG）、截图必须是 0.7.2 真实界面且无内网 IP/token/序列号/人脸/房间照片；措辞红线 = 不提 VPN/内网穿透/代理、不写破解/刷机/root（改写「设备前置条件」）、不用小米/小爱 logo、不暗示官方关联。
- **task-17（AEC-lite）设一轮 timebox**：过不了就 park + 写「已知限制」，不阻塞发布。
- 每张卡独立 review + verify；README 按「真实性/脱敏/许可/措辞」四项审；**全部完成前不标 [phase]、不打宣传 tag**。

## 待设备（4 台联调，pending）

- 两台新 Dot2 到位后：EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除 → 4 台联合验收（`docs/release/acceptance-4devices.md`）。
- 依赖 9 哥 手动添加设备；设备未到期间不阻塞 README/发布准备。

## task-17 已 PARK（2026-10-26 19:35，按 goal 熔断）

- 第一轮门禁 reviewer-barge = **CHANGES**，按新 goal「task-17 一轮不过即 park」**不返工**。
- 代码已回滚：`src/rui/audio/vad.py` 还原、`tests/test_aec_lite.py` 删除，工作树干净。
- 实现留档：**`/tmp/parked/task-17/{vad.py,test_aec_lite.py}`**（临时目录，重启前需转存到仓库外持久位置）。
- park 原因（reviewer）：① `_aec_filter` 前 taps 帧 `clean` 恒 0 → cleaned 段首 ~32 ms 被置零，可能切用户首字；② `aec_lite=True` 默认开启且残差 ≥0.25 就**静默丢 turn**，真实混响 >64 ms 时可能频繁丢正常指令；③ `aec_lag_max_ms=1500` 与 task-15 的 800 ms 上界冲突，同源回复可能被当 cue 相减；④ 阈值/混响需真机 A/B。
- **不阻塞发布**：0.7.1/0.7.2 内的包络 + EMA + `quiet_after` 已覆盖真机观测类（削波 + lag≈740 ms）；剩余边界（冷启动不削波、用户与 cue 重叠 <110 ms）写进 0.7.2 release notes 的 Known limitations。

## 发布准备进度（2026-10-26 19:50）

- **task-17**：PARKED（见上），不阻塞。
- **task-20（xiaoai 扫描 + 公开）✅ DONE（verifier VERIFIED，HEAD `d741876`）**：仓库已 `PUBLIC`（`gh repo view --json visibility` 复核）；Lead 自测扫描：4 个 commit，**0 个 token/私钥、0 个内网 IP、0 条个人路径、0 个 MAC**；仅作者邮箱 `yes999zc@163.com` 出现在提交元数据里（非机密，属可接受）。**仍待该卡 reviewer + verifier 走完**。
- **task-21（三仓 README）**：已开工，**先做 house-ear 与 rui-dot2-bridge 两份**；xiaoai 那份等 task-20 收口后再做（同仓 README 会打架）。
- **task-22（CHANGELOG/tag/release + 核对）**：blocked_by task-20/21。
- CI：`cf05f58` 上 4 个 job 全绿（ci / security / docker-publish / secret-scan）；此后仅文档提交。
- 工作树：干净（house-ear 只有已提交内容；xiaoai 干净）。

## README 三个口径（Lead 决定 2026-10-26 19:55）

1. **quickstart 固定 tag = `v0.7.2`**（不用 `main`）。顺序：README 先合入仓库但**最后推送**，task-22 建好 tag 后再 push，避免 404。
2. **截图 = 本地实例 + 示例设备名**（`Living Room Speaker` / `Bedroom Speaker`）抓**当前 0.7.x 真实界面**，并在 README/PR 注明「本地演示实例，数据为示例」。**不允许**用生产界面（含真实房间/设备名）；也**不允许**用设计稿或模型生成图冒充界面截图。
3. **支持矩阵可公开实测数字**，但必须带「口径 + 日期 + 分母」，未测项一律写「未测」：
   - Dot2：唤醒 30/30 admitted、指令 26/30（2026-10-26，6 句×5 轮，校准后 TTS）；barge 8/8（2026-10-26，真机）；音乐中「下一首/停止播放」10/10（2026-10-26，人声）。
   - LX06（living）：唤醒 19/20、打断 205.2 ms（引用历史验收日期，若找不到原始记录就标「历史实测，原始日志未留存」并降级为「已测（有限样本）」）。
   - 音量/指示灯/多设备并发：**未测**。
   - 禁止把「未测」写成「支持」，禁止把单次样本写成稳定结论。

### 三仓 README 的分仓口径（Lead 定 2026-10-26 20:05，写稿按此执行）

| 仓 | quickstart tag | demo 素材 | 支持矩阵可写项 |
|---|---|---|---|
| house-ear（rui） | `v0.7.2` | 本地 `rui serve --no-audio` 实例 + Chrome headless 抓 `/v2`，设备名换 `Living Room Speaker`，注明「本地演示实例，示例数据」 | **0.7.1/p21 实测（2026-10-26 15:01）；0.7.2 未重测**——Dot2 唤醒 30/30、指令 26/30（TTS 校准后）；barge 8/8（真机，**stop-push 判据**）；音乐中短指令 10/10（人声）；音量/指示灯/多设备 **未测** |
| rui-dot2-bridge | `v1.3.1` | **真实终端记录**（桥加载/plug-test），脱敏 | 同上（只写桥相关项）；「已解锁/刷机」措辞全部改写为**设备前置条件** |
| rui-xiaoai-bridge | `v0.2.1` | **真实终端记录**（`rui plug-test` / 插件加载日志），无界面截图 | LX06 唤醒 19/20、打断 205.2 ms（**历史实测**，查不到原始日志就降级为「已测（有限样本）」）；音量/指示灯/多设备 **未测** |

架构图各自三段：house-ear（服务端/平面/设备三层）、dot2（rui ⇄ plug ⇄ Dot2）、xiaoai（rui 核心 ⇄ `rui-device/v1` plug ⇄ LX06）；均为 Mermaid 源 + SVG/PNG、同一配色。

- **release notes 草稿**：`docs/release/v0.7.2.md`（commit `e69b9c9`）——变更/门禁/已知限制/合规已写实；**digest 与 tag 链接待 task-22 构建后补**。

## README 截图与稿件的当前状态（2026-10-26 20:10，压缩前）

- **版本已落库并提交**（未 push）：house-ear `dee693f` = 0.7.2、dot2 `72a97c0` = 1.3.1、xiaoai `39d9273` = 0.2.1；**v0.7.2 快照**测试 house **1626/17**、dot2 **143**、xiaoai **9** 全绿，ruff 绿。
- **0.7.3 当前工作树**（task-2 后，commit `cd2d0a2`）：house **1651/1634/17/0**（collected/passed/skipped/xfail，见 `docs/audit/coverage-baseline.md`）、dot2 **145**（task-2 新增 2）、xiaoai **9**；ruff 绿。
- **release notes 草稿**：`docs/release/v0.7.2.md`（`e69b9c9`），digest 待 task-22 补。
- **截图候选**（本地 demo 实例 `rui serve --no-audio` @ 127.0.0.1:18080，示例房间 `Living Room`、设备 `living-room-speaker`，页脚 `rui 0.7.2`）：
  - `docs/assets/console-v2-devices.png`（**不要用**：设备卡片含 `Xiaomi · 设备接入` 且状态为「状态未知/音频面已停止/从未上报」，既触红线也不像产品图）
  - `/tmp/rui-demo/v2-run.png`（运行页，含「需要处理：设备离线/配置未完成」两条告警 → 不适合 hero）
  - `/tmp/rui-demo/v2-voice-assistant.png`、`/tmp/rui-demo/v2-tools.png`、`/tmp/rui-demo/v2-config.png`（**未查看**，下轮先看这三张挑一张干净的作为 hero）
- **v2 真实路由**：`/devices`、`/devices/add`、`/rooms`、`/setup`、`/run`、`/conversation`、`/logs`、`/memory`、`/config`、`/voice-assistant`、`/tools`、`/music`、`/credentials`、`/health`（`web/src/v2/router.ts`）。
- **抓图命令模板**：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 --virtual-time-budget=6000 --screenshot=<out.png> "http://127.0.0.1:18080/v2/<route>"`。
- **尚未开始**：`docs/assets/architecture.mmd`（mermaid-cli 在 `/tmp/mmdc`）、README 重写、dot2/xiaoai 两份 README。

### README 素材已就绪（2026-10-26 20:20）

- **hero 截图定稿**：`docs/assets/console-v2-config.png`（本地 demo 实例 `/v2/voice-assistant`，中文界面、无 Xiaomi 字样、无离线告警、页脚 `rui 0.7.2`；旧 `console-v2-devices.png` 已删，因为它含 `Xiaomi · 设备接入` 且状态为「音频面已停止/从未上报」）。
- **架构图已导出**：源文件 `docs/assets/architecture.mmd`（Mermaid，三层：Server control plane / Audio plane per device / Devices plug adapters；配色 ink `#1f2a2e`、teal `#2f6f6a`、sand `#e8e2d6`、accent `#b4744a`），导出 `architecture.svg` + `architecture.png`。
  - 渲染命令（Chrome 而非 chrome-headless-shell）：`printf '{"executablePath":"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome","args":["--no-sandbox","--disable-gpu"]}' > /tmp/mmdc/pptr.json && /tmp/mmdc/node_modules/.bin/mmdc -p /tmp/mmdc/pptr.json -i docs/assets/architecture.mmd -o docs/assets/architecture.svg -b '#ffffff'`
- **README 正文尚未写**（house-ear / dot2 / xiaoai 三份都未动）；quickstart 事实已核：`deploy/install.sh` 存在、Python `>=3.11`、`config.example.yaml` + `docker-compose.yml` 在根目录、LICENSE MIT。

### README 进度（2026-10-26 20:35）

- **house-ear README 已完成并提交**（`e8e0d95`）：中英各 10 段、202 行；hero 截图 `docs/assets/console-v2-config.png`、架构图 `architecture.{mmd,svg,png}`；支持矩阵全部带分母/日期、未测项显式「未测」；红线自检通过（无内网 IP、无 token、无 VPN/穿透/代理，小米只作设备型号并注明「无官方关联」，刷机只出现在「不提供/不建议」的否定句里）。
- **rui-dot2-bridge 架构图已导出**（源文件 `docs/assets/architecture.mmd`，SVG+PNG，同配色），**README 正文未写**（旧版 233 行仍是 0.3.4→0.6.0 口径、含「已解锁/EchoMuse 固件/播放门控 1.1.3」等过时说法，需整体重写）。
- **rui-xiaoai-bridge**：README 未动（旧版）；按口径用终端记录、quickstart `v0.2.1`。

### 三仓 README 全部完成（2026-10-26 20:50）

| 仓 | commit | 篇幅 | 素材 |
|---|---|---|---|
| house-ear | `e8e0d95` | 202 行 | 截图 `docs/assets/console-v2-config.png` + `architecture.{mmd,svg,png}` |
| rui-dot2-bridge | `4227654` | 201 行 | `architecture.{mmd,svg,png}`（插件三层图） |
| rui-xiaoai-bridge | `7eaba4a` | 约 200 行 | `architecture.{mmd,svg,png}`（rui ⇄ plug ⇄ LX06） |

- 三份都是中英双语、结构一致（定位/badge/hero/15 秒 demo/架构图/特性/三行 quickstart/前置条件/支持矩阵/故障排查/已知限制/致谢/MIT）。
- 自检结果：资源引用全部存在；无内网 IP、无 token、无个人路径；无 VPN/穿透/代理/破解/解锁/刷机/root 字样（改成「设备前置条件」表述）；支持矩阵未测项全部显式标「未测」，历史数字带日期。
- **待办**：按 9 哥 要求「每张卡独立 review + verify」，README 需过「真实性/脱敏/许可/措辞」四项清单；通过后三仓 push + tag + release。

### 发布物料进度（2026-10-26 21:05）

- **CHANGELOG**：house-ear `2f5e5ef`（0.7.2 条目）、dot2 `730d6d6`（1.3.1）、xiaoai `288454e`（新建，含 0.2.0/0.1.0 历史 + 0.2.1）。
- **release notes 中英双语**：`docs/release/v0.7.2.md`（`07a702f`）——中文正文 + English 摘要段（release body 可直接取这一段）。
- 三仓 README 已 push（house-ear `d2b2b52`、dot2 `4227654`、xiaoai `7eaba4a`）。
- **仍待做**：README 四项评审结论回收 → 打 tag（`v0.7.2`/`v1.3.1`/`v0.2.1`）→ `gh release create`（body 用 v0.7.2.md 的英文段 + 中文段）→ 从 **tag commit** 构建镜像并记 digest → 核对 `/api/v2/meta` 与 digest 写回 HANDOFF。

### README 四项评审结论（CHANGES，2026-10-26 21:15）→ 待修清单

评审通过：脱敏（图片实看无 IP/token/序列号/人脸/房间照）、许可（三仓 MIT 一致）、措辞（无 VPN/穿透/代理/翻墙；无刷机/root 教程；无厂商 logo、写明第三方）、资产与文件存在性、未测项标注、EQ 数字与 CHANGELOG 一致。

**待修（按优先级，全部是文档改动）**
1. **版本真实性**：✅ 已完成（task-3，2026-10-26）：house-ear/dot2 README 矩阵标题均为「0.7.1 / p21 实测（2026-10-26 15:01），0.7.2 未重测」；xiaoai README 为「2026-10-24 历史实测（0.7.2 未复测）」；本文件 §「三仓 README 的分仓口径」同加版本标签；xiaoai CHANGELOG 的 19/20、205.2 ms 已移入 `### Historical measurements (2026-10-24, not re-tested)`。原始要求保留如下（历史）：三份都写「在 0.7.2 上实测」，但那些数字来自 15:01 的 0.7.1/p21 采样。要求：矩阵标题改「0.7.1 / p21 实测（2026-10-26 15:01），0.7.2 未重测」；xiaoai `:58/:147` 与 `:91/:180` 自相矛盾 → 统一为「插件 9 项契约测试通过；LX06 真机指标为 2026-10-24 历史」。
2. **能力表述与已知限制矛盾**：house-ear `:44/:137`「跨房间互不影响」 vs `:99/:192`（runner 单会话，跨房回复会被中止）→ 改成「跨房间的播放与回合仲裁隔离；对话运行器仍是单会话」；xiaoai `:86/:175` 同改。house-ear `:40`/xiaoai `:21/:110`「核心不含设备专用逻辑/没有小爱专属代码」与 `src/rui/adapter_aliases.py:21`（`"xiaoai": "lx06"`、`LEGACY_DEFAULT_ADAPTER`）不符 → 改「无设备实现，仅保留 0.6→0.7 的临时 adapter 别名」。
3. **有限样本**：LX06 `19/20`、`205.2 ms` 实为 2026-10-24 三轮（24.2/64.5/205.2，max=205.2）→ 逐格标日期 + `max/n=3`。
4. **结构一致**：house-ear 用「分层架构 / Layered architecture」，dot2/xiaoai 用「架构 / Architecture」→ 统一；hero 图口径要写明（house-ear 有 console 截图，dot2/xiaoai 无 → 要么补图要么明确「桥仓不设 hero」）。
5. **tag 不存在**：`v0.7.2`/`v1.3.1`/`v0.2.1` 还没建，README 的 `git clone --branch` 会 404 → **先建并推 tag，再 push README**（README 已 push，因此打完 tag 后再确认链接）；house-ear release badge 仍显示 v0.6.0，tag 后会自更新。
6. **镜像不存在**：`ghcr.io/yes999zc/rui:0.7.2` 尚未发布（GHCR 只有 latest/sha-7a81ec6）→ 必须先构建并推镜像，或在 quickstart 显式给 `RUI_IMAGE=`；`deploy/install.sh` 默认用的是 `:latest`，需在 README 里写明。
7. **dot2 命令/CI**：dot2 README 的 CI badge 指向不存在的 workflow（该仓无 `.github/workflows`）→ 删掉或补 workflow；`rui plug-test` 缺必填 `directory` 参数（`src/rui/__main__.py:708`），镜像内应写 `rui plug-test /opt/rui-plugins/rui_dot2_bridge --adapter …`（见 `deploy/downstream/Dockerfile:23`）；xiaoai `:87/:176` 同改。

### 视觉修改（9 哥 2026-10-26 21:20 补充，压缩后第一优先）

1. **控制台截图留白太多**（1600×1000 但内容居中、两侧大片空白）：裁到内容区（约 1200×1000），或抓图时把窗口调窄；**hero 换成「运行」或「设备」页**（有状态/设备卡片的那张），配置表单页不够抓人。
   - 注意：`/v2/run` 在 demo 实例上有「设备离线 / 配置未完成」两条告警，`/v2/devices` 的卡片显示「状态未知 / 音频面已停止」，且含 `Xiaomi`。**抓图时要么裁掉告警区，要么改 demo 数据后再抓**，不得让告警或厂商名进图。
2. **架构图太扁**（784×168，README 里会被缩得很小）：改成**竖向分层**（服务端 → 音频平面 → 设备），或至少导出 ≥1600px 宽、字号加大；`.mmd` 源文件三仓一起改并重新导出 SVG/PNG，保持同一配色。

**执行顺序（不变）**：修 1–5、7 文本 → 建 tag `v0.7.2`/`v1.3.1`/`v0.2.1` → 从 tag commit 构建并推镜像 → 回改 README 里的 tag/镜像引用 → 核对 `/api/v2/meta` 与 digest → 停下等验收。视觉两项插在「修文本」之后、打 tag 之前做（因为要重新提交 README 与 assets）。

### 发布完成（2026-10-26 19:31）

| 项 | 结果 |
|---|---|
| tag | house-ear `v0.7.2`（commit `ad11936`）、dot2 `v1.3.1`（`620426a`）、xiaoai `v0.2.1`（`9479988`），均已 push |
| GitHub release | 三条已建：`rui/releases/tag/v0.7.2`、`rui-dot2-bridge/releases/tag/v1.3.1`、`rui-xiaoai-bridge/releases/tag/v0.2.1`（中英双语 + Known limitations） |
| 镜像 | **`ghcr.io/yes999zc/rui:0.7.2` = `sha256:d830a2ca029b27f5c25115680746ae533c7e0093e0fe61f1541bd5a0a4dd8018`**（docker-publish 从 tag 构建，含 `0.7`/`latest`/`sha-ad11936`） |
| 下游镜像 | `rui-dot2:1.3.1` 本地 ID `sha256:92ea98f4…`，`FROM` 上面的 0.7.2 digest + dot2 plug 1.3.1；**未推 registry**（无 GHCR 写凭据），发布复现靠 tag + 上面的 base digest |
| 生产 | NAS 168 override → `rui-dot2:1.3.1`，19:31 起 healthy；`/api/v2/meta`：`rui_version=0.7.2`、`compat.min=0.6.0`、`compat.max_exclusive=0.8.0`、`contract=C-002/1`、`api_version=2.0` |
| 回滚 | override 改回 `rui-dot2:1.3.0-p23`（上一状态）或稳定基线 `rui-dot2:1.3.0-r071`，`docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d --pull never`；备份 `rui-dot2-override.yml.bak.20261026-*` |
| CI | `v0.7.2` 上 security / secret-scan / docker-publish 全 success |

### 发布验证（2026-10-26 19:40）

- **verifier-tools-2：VERIFIED**（独立复跑）——三仓 tag `v0.7.2`/`v1.3.1`/`v0.2.1` 存在；三条 release 均非 draft/pre-release；GHCR `rui:0.7.2` digest `sha256:d830a2ca…8018` 与 `docs/release/v0.7.2.md` 一致、`docker manifest inspect` rc 0；生产 `rui Up (healthy) rui-dot2:1.3.1`；`/api/v2/meta` → `contract=C-002/1`、`api_version=2.0`、`rui_version=0.7.2`、`compat={min:0.6.0,max_exclusive:0.8.0}`；`/health` version 0.7.2、devices 2；三仓 `git status` 空；tag CI（security/docker-publish/secret-scan）success；`git clone --branch v0.7.2` 成功。
- 过程中的坑：验证者最初用 `bakeyzhang`/`root` 登录 NAS 失败（免密用户是 `yes999zc`）；改用户后一次通过。token 只以 `token_len=32` 出现，未打印值。
- **已知偏差**：下游镜像 `rui-dot2:1.3.1` 只在 NAS 本地构建（ID `sha256:92ea98f4…`），**未推 registry**（该仓无 docker-publish workflow，环境无 GHCR 写凭据）；可复现性以「tag + base digest」为准。

### 评审判定的收尾（2026-10-26 19:50）

- **release body 已修**：`gh release edit v0.7.2 --notes-file docs/release/v0.7.2.md`，正文不再有 `_待填_`，标题去掉「草稿」字样（`docs/release/v0.7.2.md` 提交 `c4da73c` 之前的 `25870ac`+ 本次）。
- **tag 不移动**：`v0.7.2` 仍指向 `ad11936`（代码 commit）。理由：tag 已公开，改写已发布 ref 风险大于收益；产物表（digest/meta）在 release body 与 HANDOFF 均已是实测值。代价：`git show v0.7.2:docs/release/v0.7.2.md` 仍是当时的草稿表——**已在这里记录，属已知偏差**；下一个版本（0.7.3）自然修正。
- **quickstart 已 pin**：`RUI_IMAGE=ghcr.io/yes999zc/rui:0.7.2 bash deploy/install.sh`（house-ear `c4da73c`），说明默认是 `:latest`。
- **xiaoai plug-test 已补 `--adapter`**：`rui plug-test <插件目录> --adapter rui_xiaoai_bridge.adapter:build`（`4853bbd`），troubleshooting 同步。

### 新手安装路径实测（2026-10-26 20:05，Done 第 6 条）

- 新增 `examples/two-speakers/compose.yaml`（house-ear `64c9f34`），env 全部来自实测：`RUI_CONFIG=/config/config.yaml`、`RUI_WEB_DIR=/app/web/dist`、`PYTHONPATH=/opt/rui-plugins`、`RUI_ADAPTER_PLUGINS="dot2=rui_dot2_bridge.adapter:build,lx06=rui_xiaoai_bridge.adapter:build"`，两个插件目录 bind mount 到 `/opt/rui-plugins/`。
- NAS 实测（`ghcr.io/yes999zc/rui:0.7.2`）：`/health` → `status=ok`、`version=0.7.2`、`devices=2`、`enabled_devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`、`warnings=0`。
- **如实记录的两个坑**：① `web.auth_token` 必须在 **config.yaml**（不是 secrets.yaml），否则容器直接 `config error` 退出；② 没有 TLS 材料（`secrets/server.pem|key`）时音频平面拒绝启动（控制台与 health 正常，日志 `missing TLS material`）；③ `devices[].host` 对 lx06 是必填，缺了会 `config recovery … devices.1.host` 回落到默认值。
- 文档已写：house-ear README 加「三个仓库是什么关系 + 兼容表 + 让 AI 帮你装 + 安装路径实测输出」；两个桥仓 README 加同一段「让 AI 帮你装」。
- README 里已标注：小爱仍走核心内置 legacy 适配器（`adapter_aliases: xiaoai→lx06`），Phase 2 才转外置插件；0.7.2 上同名 id 可能被内置实现遮蔽。

### task-23 评审 CHANGES 修复（2026-10-26 20:25）

评审（reviewer-fleet-auto-2，自己起容器）抓出 6 条，已全部修完并 push（house-ear `e29fb8d`、dot2 `348335c`、xiaoai `54576f7`）：

1. **compose 路径自洽**：顶部写明「先 `cd examples/two-speakers`」+ `--project-directory .` 等价写法；并注明 `deploy/install.sh` 会另起一套 8080 实例，**不要与 compose 同时用**。
2. **device token / TLS**：compose 增加 `RUI_DEVICE_TOKEN`、`DOT2_EM_TOKEN`；README 改为「缺 device token **或** 缺 TLS 材料都会让 `audio_plane=false`，以日志第一条报错为准」；`lx06` 的 `host` 标必填。
3. **legacy 说法更正**（评审实测为准）：外置 lx06 插件**已加载**（`adapter plugins: registered dot2, lx06`，`/opt/rui-plugins/rui_xiaoai_bridge/adapter.py`）；核心别名只在未挂载时回退；不挂 xiaoai 目录 → `registered=["dot2"]`。
4. **health 证明**：改为选字段输出（含 `audio_plane`），贴真实 JSON；`audio_plane: False` 如实写。
5. **兼容表**：加 `compatibility.rui_min`（dot2 `0.3.0` / xiaoai `0.6.0`）。
6. **AI 提示词**：中英各 ≤10 行，含精确 clone/cp、config 字段（lx06 host 必填）、`RUI_DEVICE_TOKEN`、验收/失败/回滚；两个桥仓同步。

- verifier-tools-2 已对**修复前**版本给出 VERIFIED（自建容器：`devices=2`、`device_errors=0`、`registered=["dot2","lx06"]`；反向：无 token → `Exited(2)`、不挂 xiaoai → `registered=["dot2"]`）。
- **当前状态**：等 reviewer 对修复后的 delta 复评；PASS 后把 task-23 置 complete，并重跑一次 verifier（验证 token/TLS 前置条件的表述）。

### task-23 收尾（2026-10-26 20:15）

- reviewer-fleet-auto-2：**PASS**（TLS/token 表述、桥仓提示词路径、compose 可解析、三仓 clean）。
- verifier-tools-2：**NOT VERIFIED → 已修**——他按提示词字面执行 `git clone --branch v0.7.2 … && cd rui/examples/two-speakers` 失败，因为 `examples/two-speakers/` 是 tag `v0.7.2`（`ad11936`）**之后**才加的。修法：三份 README 的安装提示词改为 `git clone --branch main …`，并注明「main 才有本示例；**镜像仍 pin 在 `ghcr.io/yes999zc/rui:0.7.2`**」。commit：house-ear `0029097`→ 本次、dot2、xiaoai 各一条。
- **教训**：README 里 `git clone --branch <tag>` 的目录结构必须与该 tag 实际内容一致；这次是「README 在 main 写、tag 在旧 commit」的典型错配。以后凡是新增 `examples/`，要么先合再打 tag，要么在 README 明确「示例在 main」。
- 待办：verifier 复跑一次字面步骤（clone main → `cd examples/two-speakers` → `cp -r … plugins/` → `ls`）。

## 发布收口（2026-10-26 20:10）——goal 六条 Done 全部满足，停下等 9 哥 验收

| # | Done 项 | 证据 |
|---|---|---|
| 1 | 三仓 README 中英双语重做（矢量架构图+导出、真实脱敏截图、措辞合规） | house-ear `0029097`、dot2 `c79c402`(→`d8bfbc8`)、xiaoai `5126938`(→`6899c49`)；架构图 Mermaid 源 + SVG + PNG（三仓同配色）；hero `docs/assets/console-v2-devices.png`（本地实例、示例数据、无 Xiaomi/无告警）；reviewer 四项清单检过（脱敏/许可/措辞 PASS，真实性与可执行性 CHANGES 已修并 PASS） |
| 2 | xiaoai 全历史扫描通过并公开 | `d741876`；0 token/私钥、0 内网 IP、0 个人路径、0 MAC；PUBLIC；secret scanning + push protection enabled；alerts 0；reviewer PASS + verifier VERIFIED |
| 3 | CHANGELOG / tag / release notes（中英）/ 镜像 tag | CHANGELOG：`2f5e5ef`(0.7.2) / `730d6d6`(1.3.1) / `288454e`(0.2.1)；tag `v0.7.2`/`v1.3.1`/`v0.2.1`；三条 GitHub release（中英 + Known limitations）；镜像 `ghcr.io/yes999zc/rui:0.7.2` = `sha256:d830a2ca…8018`（tag commit `ad11936` 构建）；下游 `rui-dot2:1.3.1` 本地 ID `sha256:92ea98f4…`（未推 registry，见已知偏差） |
| 4 | 发布后 `/api/v2/meta` 与 digest 核对并写进 HANDOFF | 生产 `/api/v2/meta`：`contract=C-002/1`、`api_version=2.0`、`rui_version=0.7.2`、`compat={min:0.6.0,max_exclusive:0.8.0}`；`/health` version 0.7.2、devices 2、enabled 2；生产容器 `rui-dot2:1.3.1` healthy；三处 digest 已对照（见 §发布完成） |
| 5 | CI 全绿、工作树干净 | `v0.7.2` tag 上 security/secret-scan/docker-publish success；`67db5d8`(main) 上 ci/security/docker-publish/secret-scan 4/4 success；三仓 `git status --short -uall` 均为空 |
| 6 | 新手安装路径实测通过 | `examples/two-speakers/compose.yaml`（house-ear）+ 三仓 README 的「让 AI 帮你装」；实测 `/health`：`devices=2`、`enabled_devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`；verifier 自起容器复现（含反向：无 token → 退出、少挂插件 → `registered=["dot2"]`）；reviewer PASS + verifier VERIFIED |

**已知偏差（不阻塞验收，已如实记录）**
- 下游镜像 `rui-dot2:1.3.1` 只在 NAS 本地构建，未推 registry（该仓无 docker-publish workflow，环境无 GHCR 写凭据）；可复现性以「tag + base digest」为准。
- tag `v0.7.2` 指向 `ad11936`，其树内 `docs/release/v0.7.2.md` 仍是待填草稿表；发布页 body 与 HEAD 文件已是最新值，tag 未移动（改写已发布 ref 风险更大）。
- `examples/two-speakers/` 在 tag 之后才加入，故 README 安装提示词用 `--branch main`（镜像仍 pin 0.7.2）。

**不做（按 goal 约束）**：4 台联合调试（设备未到）、M10 / console v2 默认切换、任何新功能。

## 交接（给下一个会话，2026-10-26 20:20）

> **任务板不跨会话**：本会话的 `task-*` 在新会话里看不到。所有未完成项都已写进本文件与 `docs/ROADMAP-0.8.md`；开新会话时**先读这两份**，不要凭记忆。

### 现状（已交付）

- 三仓已发布：`rui 0.7.2`（tag `v0.7.2`，镜像 `ghcr.io/yes999zc/rui:0.7.2@sha256:d830a2ca…8018`）、`rui-dot2-bridge 1.3.1`（`v1.3.1`）、`rui-xiaoai-bridge 0.2.1`（`v0.2.1`，仓库已公开）。
- 生产：NAS 168 跑 `rui-dot2:1.3.1`（核心 0.7.2 + 桥 1.3.1），healthy，2 台设备在线；`/api/v2/meta` = 0.7.2 / `compat 0.6.0..<0.8.0`。
- CI：`v0.7.2` tag 与 `67db5d8`(main) 上 job 全绿；三仓工作树干净。
- 新手安装路径：`examples/two-speakers/compose.yaml` + 三仓 README 的「让 AI 帮你装」，实测 `adapter_plugins.registered=["dot2","lx06"]`、`devices=2`、`device_errors=0`。

### 待设备（等 9 哥，不许先动手）

1. 两台新 Dot2 到位后：EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除 → 4 台联合验收（`docs/release/acceptance-4devices.md`）。
2. 4 台联调通过后才谈 24h 稳定性满窗与版本验收。

### 已知限制（0.7.2 发布时点）

1. cue 回声在「冷启动 + 不削波 + 设备延迟 >800 ms」或「用户与 cue 重叠 <110 ms」时仍可能进 ASR（AEC-lite 已 park，重开前置见 0.7.5 卡）。
2. `runner.abort()` 是全局单 flag：一台设备唤醒会中止另一台正在合成的回复（按房隔离只覆盖播放与回合仲裁）。
3. 已播出的告别尾音追不回，只能阻止后续 chunk。
4. 唤醒词不按房间隔离；多设备并发未测。
5. 下游镜像 `rui-dot2:1.3.1` 只在 NAS 本地构建（`sha256:92ea98f4…`），未推 registry；复现靠「tag + base digest」。
6. tag `v0.7.2` 树内的 `docs/release/v0.7.2.md` 仍是待填草稿表；发布页 body 与 HEAD 已是最新（tag 未移动）。
7. `uv.lock` 内版本号陈旧（0.2.0），不影响运行。
8. 生产 NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理输出中回显（未落仓库）→ 建议轮换。

### 下一步第一张卡（明确）

**`[impl] 0.7.3 / 跨房中止策略`**：把 `runner.abort()` 从全局单 flag 改为按房作用（或明确文档化并暴露到 `/health`），同时把 `turn_open` 单布尔改成按房结构；出口条件写在新卡的验收标准里，参考 `docs/ROADMAP-0.8.md` 的 0.7.3 段。

### 如何接手（按序）

1. 读本文件 + `docs/ROADMAP-0.8.md`；
2. `git -C 01_Code/projects/house-ear status --short`、`gh run list --limit 3` 确认基线；
3. 从「下一步第一张卡」开始建卡（新会话的任务板是空的，需 `team_task_create`）；
4. 设备到位后按「待设备」段的顺序走，不要跳过 EQ 耳测。

## 风险与未决

- **task-6 P1 已交付，等 P2 决策**：Dot2 唤醒 30/30 admitted、指令 26/30；掉分两处（ack cue 回声 13/26、音乐中「下一首」2/5）已落日志行；LX06 与小爱对比仍缺样本（不同屋）。
- **B1 中文唤醒（task-3，BLOCKED）**：V0 英文 stock 模型对产品无效（中文「你好小瑞」只走 RUI KWS，英文零事件、`wake_candidates=0`）。真前置不是固件：① 中文 OWW 模型需 `oww_forge` 自训（25GB/数小时，效果待测）；② bridge 无 `/shell` 通道（`adapter.py:81`），最小验证需一次性 adb 推 ~1MB 分类器。待 9 哥 决定：是否再跑 V0 / 是否接受自训成本 / 生产机是否允许一次性 adb / barge 门槛是否 0.25。
- **EQ 耳测未做**：测量指标改善已证，但 9 哥 尚未 A/B；`trim_db=-2.0` 未重调（A 计权响 4.1 dB）。
- **cue 回声残留**：envelope 匹配对设备延迟 >~950 ms 或与用户语音完全重叠时回退旧行为，可能切掉 ~300 ms。
- **多设备尚未实测**：`multi_device.enabled=true`、`max_planes=4`，但只有 1 台 Dot2 在线；唤醒词不按房间隔离（`lost_same_word`）；`DOT2_CONTROL_PORT` 是全局 env，加第 2/3 台前必须清掉；多设备默认共享 secrets。
- **24h 稳定性未复查**：从 p22 部署（17:15）起计，尚未满窗；13:36 那轮作废。
- **凭证**：NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理会话输出中回显（未落仓库）→ 建议轮换 NAS 密码；本会话临时口令文件（本地 + 远端 `/tmp`）已删除。
- **源码全量镜像构建仍阻塞**：NAS 上 `deb.debian.org` 超时、换清华源后仍 `Unable to locate package nasm/libopus-dev`，故当前走薄层镜像（底座 + overlay），可复现性只到「tag → 薄层 digest」。
- **backlog**：`uv.lock` 停在 0.2.0；下游 docs 兼容窗口文案陈旧；`coordinator.turn_device`/`drop_device` 生产路径未用；`turn_tasks` 仅在 `start_audio` 时初始化；覆盖缺口（`_wake_in_flight`、output_owner/gate_speaking）；whitespace-id 归一化无用例。

## 关键文件

- 契约：`docs/contracts/C-002-console-v2-api.md:1`、`docs/socket-plug-v1.md`
- 计划/分析：`docs/plan/speaker-tuning.md:23`（诊断）、`:343`（B1 评估）、`docs/analysis/`（task-6 产出）
- v2 后端：`src/rui/web/api/v2_api.py`、`src/rui/web/api/app.py`（`TURN_STALE_S`、`_PlaneLink.release_turn`）
- 音频：`src/rui/audio/process.py`、`src/rui/audio/vad.py`（`set_echo_reference`）、`src/rui/audio/coordinator.py`
- 控制台：`web/src/v2/App.vue`、`web/src/v2/router.ts`、`web/src/v2/tools.ts:42`
- 下游：`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:81`（无 `/shell`）、`dsp.py:196`
- 发布：`docs/release/v0.7.0.md`、`docs/release/v0.7.1.md`、`docs/release/acceptance-4devices.md`

## 约束与禁区

**协作规则**
1. 需要 9 哥 拍板时用 `ask_user_question` 提问并等待，不要在总结里只写「等你决定」。
2. 要真正停手交接或等真机时明确说「请 pause goal」，等 pause 后再继续。
3. 9 哥 发消息不取消 goal；范围变更先写进任务卡或本文件，再动手。
4. **对外发布用哪个版本号必须先问 9 哥 确认**（「按需升版」保留）。

**禁区**
5. 任何改动先过 fresh reviewer + verifier 再谈 commit；不碰 secrets、不自行宣传。
6. 不要相信「`/v2/tools` 未过门禁」的旧说法：已过门禁并提交 `ef7ff18`，且已进 0.7.1 镜像。
7. 不要相信「打断已修复」的旧说法：A1' 真机 8/8 可用，但 P2 类体验差距待 task-6 量化。
8. 不要相信「Dot2 音质已变好」：只证测量指标改善，未经 9 哥 耳测。
9. 不要相信「设备侧中文唤醒已可用」：中文 OWW 模型不存在，V0 英文路径对产品无效。

## 核验命令（接手先跑）

```
cd 01_Code/projects/house-ear && .venv/bin/python -m pytest -o addopts='' -q   # 期望 1613 passed / 17 skipped
.venv/bin/python -m ruff check src tests                                       # All checks passed
cd web && npm run typecheck && npm run build                                   # exit 0，dist/index.html + dist/v2.html
cd ../rui-dot2-bridge && PYTHONPATH=src ../house-ear/.venv/bin/python -m pytest -q   # 期望 143 passed
cd ../rui-xiaoai-bridge && PYTHONPATH=src ../house-ear/.venv/bin/python -m pytest -q # 期望 9 passed
gh release view v0.7.1 --json tagName,url                                      # tag 与 release 存在
```

## 没做的事

- **没做**：B1 中文唤醒模型与 adb 验证（task-3 BLOCKED）、task-6 矩阵采样、4 台联合验收、M10 默认切换、9 哥 耳测、24h 稳定性复查、源码全量镜像构建。
- **没做**：宣传/公告/营销文案（明确不做）。
- 任务板会随会话消失；跨会话只信本文件 + `docs/release/*`。

> 非 [phase] 收口任务，故不写 phase-close 标记；task-12 收口时才由 Lead 追加 `phase-close: task-12`。

## task-3 进度检查点（2026-10-26 21:2x，工作树未提交）

- **任务**：`[docs] 0.7.3 覆盖率缺口台账 + 8 处文档矛盾收口`（impl-core，in_progress）。
- **已完成**：8 处矛盾全部落改（house README:111,248；dot2 README:89,95,97,203,209,211；dot2 milestone-m1:23-24；dot2 canary:249-252；xiaoai CHANGELOG:15-23、README:90,197；socket-plug-v1:387；code-health:23,408,474,550；HANDOFF §「三仓 README 的分仓口径」/§「README 截图与稿件的当前状态」/§「README 四项评审结论」）。
- **已完成**：`docs/audit/unverified-inventory.md` 新增 §3 终态表、§5 覆盖率台账（**12 个一级子包 + root = 13 个 scope**，三类缺口，HE-13/14/17 输入，batch-1 对应）、§6 真机 26 条三件套、§7 task-2 reviewer backlog。
- **已完成**：三仓 grep 前后计数（house README+docs 90→**92**；其中 inventory 27→**28**、排除 inventory 核心 63→**64**（按 per-file 实测求和），核心 +1 来自 HANDOFF §「README 四项评审结论」新加的已核对历史标签；dot2 14→14；xiaoai 10→10）与 `git diff --stat`；§3 8 行、§6 26 条真机行均通过结构自检。
- **CHANGES 轮（reviewer-core，2026-10-26 21:27）**：基线按 0.7.3 最终值重建（cd2d0a2，1651/1634/17/0，行 80.95 / 分支 69.55）；plugins 缺口改归「可单测未写」；HANDOFF §「README 截图与稿件的当前状态」恢复 v0.7.2 快照并另起一行 0.7.3；X-01/D2-05/D2-09 命令与 cwd 修正；dot2 volume 口径三拆；§3 #4 备注改准。
- **待做**：重跑 ruff/全量/grep/基线命令，send_message 给 reviewer（经 lead）；等复评。
- **未做**：没有 commit；没有改代码/版本号；没有碰卡面写范围外文件（`docs/ROADMAP-0.8.md` 的未提交改动是 Lead 的，不是本卡）。

### B1 / task-8 进行中（implementer，2026-10-28 12:0x，HEAD `8344085`）

- **范围**：删 `/legacy` 旧面板与 15 条老链接 302；保留 `/`→`/v2/`、`/v2`、`/v2/assets`、`/api/v2`、`/api/v1/panels`。
- **已确认事实**：v2 只跨树引用 `components/AppIcon.vue`、`theme.ts`、`style.css`、`vite-env.d.ts`、`public/*`；`web/v2.html` 产品名 `dist/v2.html`，字体/favicon 走根路径 `/fonts/*`、`/favicon.svg`；旧 catch-all 一旦删除，这些根静态资源必须显式 mount。
- **已定做法**：删 `legacy_links.py`、`app.py` 的 `/legacy*`、`_legacy_shell`、`FALLBACK`、根 catch-all；`/` 恒 302 `/v2/`（保留 query）；`web.console` 枚举保留但值被忽略（文档写明）；v2 内 `/legacy` 链接与 `?from=` 提示一并删；`vite.config` 仅 `v2` 输入；Dockerfile 检查 `dist/v2.html`；根静态资源显式 mount（favicon.svg / fonts / logo*.svg），用 200 矩阵测试钉住。
- **验证计划**：`cd web && npm run typecheck && npm run build`；`uv run pytest -o addopts=""`（基线 1738/6）；`uv run ruff check src tests`；新增 404/302/200 矩阵。
- **状态（2026-10-28 12:2x 完成）**：代码/前端/测试/文档全部落改，**未 commit**；验证见下。
- **验证**：`npm run typecheck` 绿；`npm run build` 绿（`dist/v2.html`，无 `index.html`）；
  `uv run pytest -o addopts=""` → **1710 passed, 6 skipped**（基线 1738 − 删除的 28 条：`test_legacy_links.py`
  7 条、2 条已删 Vue 的 UI 源码契约测试、其余为改写后参数化条数变化）；`ruff check src tests` 绿；
  `grep '/legacy|legacy_links'` 在 `src/rui` + `web/src` 只剩注释。
- **遗留/风险**：`/api/v1/config/raw` 与 HA 表单在 v2 没有对应 UI（原 UI 源码契约测试随之删除）；
  `/api/v1/panels`（v1 冻结）里的 href 仍指 `/panel/*`，但页面已 404；`web.console` 值被忽略。
  后续接手先看 `git status` 与任务卡 task-8。
- **复评修正（2026-10-28，review CHANGES）**：`config.example.yaml` / `template.yaml` /
  `schema.py` 的 `web.console` 注释统一为「compat-only、值被忽略、`/` 恒 302、无回滚」；
  `test_panels.py` / `test_audit_fixes.py` 的 McpManager/ConfigCenter 注释改为「v1 payload only，
  UI 已删」；`App.vue` 空 `<style scoped>` 删除；`known-limitations.md` 新增 **L18**（HA `base_url` 在 v2 ToolsView 仍可改；缺 HA token 表单 与
  `/api/v1/config/raw` 视图，API 仍在）。删除项已在 Lead 的 `c28e5e4`（本地未 push）；
  B1 已最终 review PASS + verify VERIFIED，re-commit 为 `7b9035a`（feat(console)!，已 push）。验证：目标 54 passed；全量 1710/6；ruff 绿；typecheck 绿。

### P1 / task-17 进行中（implementer，2026-10-28，HEAD `4b8b8b0`）

- **范围**：会话关闭必须 disarm（或明确 followup 窗口 N），并修 `idle goodbye in inf s`；不改 ack/chime/barge/music/契约。
- **已查明根因（下一轮直接实现，不要重新考古）**：
  1. `inf` 不是配置超时：`src/rui/session/idle.py` 的 `IdleWatch.remaining()` 在「没有打开的会话」或 `timeout_s<=0` 时返回哨兵 `float("inf")`；`app.py` 的 `on_wake_idle` 用 `log.info("... idle goodbye in %.0f s", runner.idle.remaining())` 直接打印， farewell 已 `idle.stop()` 后就会打 `inf s`。修法：日志按 `idle.active` 分支——活动时打印有限剩余秒数，非活动时打印「session already closed / no countdown」；不改 `remaining()` 语义（其他调用方依赖哨兵）。
  2. `session closed` 不 disarm：`app.py:idle_watch` 在说完告别后调用 `client.reply_finished()`（本意是让音频面开 post-playback mute），但 `process.py:_arm_followup_later` 收到 `ReplyFinished` 后会用 `max_wait_ms = session.timeout_s*1000`（15000）重新武装 followup gate → 告别后最多 15 s 的免唤醒窗口（04:15:26 close → 04:15:43 turn）。
  3. 选定语义：**有限 followup 窗口 N 秒**（不是无限监听）。`VoiceConfig` 新增 `followup_max_wait_ms`（默认 8000，0=立即关闭/不续听），plane 用 `min(session.timeout_s*1000, followup_max_wait_ms)` 作为 gate `max_wait_ms`；告别后的窗口因此 ≤8 s，20 s 后必须唤醒词。文档写清 N，known-limitations/backlog 各登记一条。
- **必测（先红后绿）**：告别后 1 s 说话 → 固定为「接受（followup 窗口内）」；告别后 20 s 说话 → 无唤醒必须拒绝；`idle goodbye in inf s` 不再出现（日志断言/单测 `remaining()` 非活动时日志文案）。
- **未做**：代码/测试/文档、验证、commit。工作区干净（本卡尚未动文件）。
