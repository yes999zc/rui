# 从现在到 0.6.0 的计划

> 2026-10-25 夜定稿。生产已切到 `rui-dot2:0.5.2-hot4`（临时层，仅观察）。
> 规则：**从此刻起不再打 hotfix 镜像补丁**；所有修复走正式版本，一个批次一次发布。

## 0. 现状与冻结

- 生产：`/vol1/rui`，`rui-dot2:0.5.2-hot4`，devices=2（客厅 LX06 + 卧室 Dot2），`multi_device.enabled=true`，`config_version=3`。
- 备份：`/vol1/rui-backups/pre-dot2-20261025-204238.tar.gz` + `config.bak.20261025-204238`；回滚镜像 `ghcr.io/yes999zc/rui:0.5.0` 与 `rui-dot2:0.5.2` 保留。
- 本地未发布代码：`src/rui/audio/process.py`（播放走 effective audio）、`src/rui/web/api/app.py`（会话按房间 / idle 互斥 / 告别补 reply_finished），均带部分测试。
- hot1–hot4 是临时层，**0.5.3 发布后全部删除**，不复用。

## 1. 已知缺陷（今晚现场记录，作为 0.5.3 验收用例）

| 编号 | 现象 | 状态 |
|---|---|---|
| D1 | Dot2 把唤醒应答「在呢」当请求（13:11:42 `transcript: 在呢。`） | 未修 |
| D2 | 播放路径读全局 `playback.rate`，设备覆盖失效（Dot2 无声） | 已改，未发布 |
| D3 | 告别语/灭灯投递到主设备（客厅替卧室道别） | 已改，未发布 |
| D4 | 告别与唤醒竞争 → `turn_open` 卡死，后续唤醒全被拒 | 已改，未发布 |
| D5 | 告别自身回声产生假回合（hot4 加了 `reply_finished`，仍待验证） | 部分修 |
| D6 | 全屋状态分散：session / owner / turn 各有一套“主设备”默认 | 结构性，0.6 解决 |

## 2. 0.5.3 稳定化（目标 2 天内）

范围：只修 D1–D5 + 可观测，不加功能。

1. **播放路径统一 effective audio**：6 处 `cfg.audio.playback.*` → `self.effective.*`；ack 从全局速率重采样到设备速率。测试：进程级（adapter 收到的 sample_rate）+ 速率不一致时启动即报错。
2. **ack 自听**：把 ack 的静音窗口做成设备级（`devices[].audio.voice.ack_echo_ignore_ms`，默认继承 `ack.echo_ignore_ms`），Dot2 先设 2500；用真机量回声尾长（目标：ack 结束后 0 条假 turn）。测试：把上一段播放 PCM 注入麦克风的 mock 回声场景。
3. **会话归属**：告别/灭灯/停止按 `turn_device`（`_session_client`），不再固定主设备。
4. **idle 与唤醒互斥**：唤醒仲裁中或 `turn_open` 为真时跳过告别；告别后清 `turn_open`（安全网）。
5. **告别收尾**：告别播完补 `reply_finished`，让音频面排空 + 进入播放后静音。
6. **可观测（doctor 三行）**：
   - `audio_rates`：每设备 wire rate（插件声明）vs 生效 `playback.rate`，不一致 → degraded；
   - `channels`：Telegram 轮询 `last_error`/409 直接报出来；
   - `console_token`：`console-token.txt` 与 `web.auth_token` 一致性。
7. **文档**：`docs/deploy.md` 增补多设备模板（每设备 room/rate/aec/mute、`max_planes`、`default_output`）。

发布门槛：
- 全量 `pytest` / `ruff` / `sh -n` / `web typecheck+build`；
- `.34` 金丝雀 24 h（LX06 + Dot2 两台），8 项真机验收：唤醒→应答、告别只在发起房间、连喊三次都进、ack 不自听、打断、重启重连、LX06 无回归、回滚 0.5.0；
- 生产备份 + 回滚演练记录。

## 3. 0.6.0 设备无关内核（目标 1.5–2 周）

契约：
- `rui-device/v1` 增 `audio_defaults`（capture/playback）与可选 `ack_echo_ignore_ms`；
- 核心默认改中性（16000 / 1ch / 0ref / earcon），LX06 的 8ch/6mic/2ref 由它自己的 manifest 声明；
- `rui_compat` 上界抬到 `<0.7.0`；dot2 同步 plugin **1.2.0** 并跑 plug-test。

代码：
- `src/rui/device/xiaoai/` → `plugs/xiaoai/`，走与 dot2 相同的 manifest 加载；`adapter: xiaoai` 保持不变，镜像自带，**现有安装零迁移**；
- 去掉 core 对 xiaoai 的硬引用（`plugins/builtin.py`、`registry.py`、catalog 文案、web 默认值）；
- 首次运行向导第一步改为「选择设备」，由已安装 plugs 驱动；
- 全屋状态收口：session/owner/reminder/music 一律按房间（`turn_device` / `default_output`）。

验收：
- 现有 config v3 原样启动、LX06 行为零差异；
- `.34` 金丝雀 2 天 + 生产回滚演练；
- dot2 `plug-test` on 0.6.0 = 15 PASS / 0 FAIL / 2 WARN / 5 SKIP。

## 4. 之后（0.6.x）

- Phase 3：房间策略与输出体验（`room_priority` / `last_active` / 全局 stop）；
- Dot2 进生产：下游镜像、`dot2-secrets`、`multi_device` 稳定后再评估；
- 设备端唤醒（中文 OWW 模型，设备侧）；
- 多设备真机指标（每平面 RSS/CPU、唤醒→首字）。

## 5. 分工（两项目）

| 事项 | 归属 |
|---|---|
| 契约、内核、控制台、安装器、doctor、发布 | rui |
| 设备协议、adapter、下游镜像、设备侧音频参数、OWW | dot2 |
| 0.6 契约变更 | rui 定稿 → dot2 抬 `rui_compat` → 双方各跑 plug-test + 真机 |

## 6. 风险与应对

- **LX06 回归**：plug 化保留一个 minor 的内置兼容；金丝雀不过不上生产。
- **ack 静音太长吃掉首字**：设备级配置，默认只对 device-AEC 插件生效；先量尾长再定值。
- **时间**：真机窗口与生产切换均需 9 哥 点头；0.5.3 只动代码与镜像，不动生产架构。

## 7. 明确不做

- 不再往生产塞 hotfix 镜像层；
- 0.5.x 不加新功能；
- 不在 0.6 之前改 `multi_device` 的仲裁语义。
