# HANDOFF 2026-10-25 rui 0.6.0 · 多音箱完成 / 小爱剥离

phase-close: TBD

## 会话身份
- 模式：候选：A（双仓库：rui 上游 + rui-dot2-bridge 下游）
- 本会话负责的仓库：`/Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear`（rui）、`/Users/bakeyzhang/AI_Workspace/01_Code/projects/rui-dot2-bridge`（下游）
- 分支 / 最新 commit：rui `main` @ `3d41890`；dot2 `main` @ `ba0ba54`（`git log --oneline -1` → 上述 sha；`git status --short --branch` → 与 origin 同步、工作区干净）

## 版本与契约
| 项 | 值 | 证据（命令 → 输出） |
|---|---|---|
| 上游 rui | `0.6.0`，**未打 tag**；生产 base 镜像 `sha-3d41890`，digest `sha256:f75bf52652b7fdc69f298be4a5888e558e627ce784e14af932c65187f0e46b4d` | `grep '^version' pyproject.toml` → `version = "0.6.0"`；`gh api /user/packages/container/rui/versions` → `sha-3d41890` + 该 digest |
| 下游 plug | `rui_dot2_bridge` 插件 **1.2.0**；容器内 `builtin_plugins()` 同时列出 `dot2-echomuse 1.2.0`、`xiaoai-lx06 0.3.0` | `grep -n 'version:\|rui_compat' src/rui_dot2_bridge/plugin.yaml` → `1.2.0` / `">=0.6.0 <0.7.0"`；`docker exec rui python -c ...` → `2 [('dot2-echomuse','1.2.0'),('xiaoai-lx06','0.3.0')]` |
| 设备固件 | Echo Dot 2 / EchoMuse `20261024-1911-dev` | 生产日志 `dot2 link: dot2 registered (fw 20261024-1911-dev, fireos, ip …)` |
| 契约编号与文件 | C-001 `rui-device/v1`（manifest 增可选 `audio_defaults` + `ack_echo_ignore_ms`） | `docs/socket-plug-v1.md` §3.2/§3.2.1；`src/rui/config/schema.py` `class AudioDefaults` |
| 兼容边界 | rui `>=0.6.0, <0.7.0` | `src/rui_dot2_bridge/plugin.yaml:5`；内置 xiaoai plug 为 `>=0.3.0 <0.7.0` |

## 已完成（含证据）
- **小爱从内核剥离为 plug**（M2）：`src/rui/device/xiaoai/` 删除、改为 `plugs/xiaoai/`，经 manifest `runtime.entrypoint` 加载；兼容别名只在 `src/rui/adapter_aliases.py`。`git grep -l xiaoai -- src/` → 只剩 `src/rui/adapter_aliases.py`；生产容器 `builtin_plugins()` → 两个 plug 并列。commit `1fa96bd`。
- **三层音频合成 + 中性默认**（M1）：`rui-device/v1` 增 `audio_defaults`，顺序 设备默认 < 全局 config < `devices[].audio`；核心默认 16000/1ch/0ref；LX06 manifest 声明 48000/8/6/2；`save_config` 不固化默认值。commit `ae99f1b`。全量 `pytest -o addopts='' -q` → **1348 passed / 17 skipped**；`ruff check src tests` → clean。
- **全屋按房间路由 + 向导选设备**（M3）：告别/LED/提醒/音乐/transport/stop/音量/say 按 `turn_device`/`default_output`；多 plug 时向导 409 `plug_required`。commit `56f610a`；`web` typecheck+build 绿。
- **Dot2 自听修复**（M0，下游）：plugin 1.1.3 播放期静音上行麦克风；.34 合成回灌 4/4 无假 turn、窗口外真实请求 2/2 正常；`plug-test` 15/0/2/5。commit `fbf923f`；1.2.0 声明 `audio_defaults`/ack 2500，commit `ba0ba54`，`docs/contract-report-0.6.0.json` 记录镜像 tag+digest → 15 PASS / 0 FAIL / 2 WARN / 5 SKIP。
- **两个夜间 bug 修复**：`94d3f6a` 无回答语音（noise/唤醒残留/自回声/vendor）释放 `turn_open`；`3d41890` 只有真正 turn 才设 `turn_device`（否则回声把告别投到错误房间）。全量门禁同上。
- **生产切换**：NAS `/vol1/rui` 现跑 `rui-dot2:0.6.2`（healthy）；`/health` → version `0.6.0`、config_version `3`、devices `2`、device_errors `0`、planes `living`/`dot2` 均 `speaker_connected=true`。
- **真机已通过的部分**：Dot2 唤醒 →「现在几点？」→「现在是晚上十一点零四分。」（audio_bytes=63600、`playback_gap_count=0`）；LX06 唤醒 →「今天周几？」→「今天周日，10月25号。」（audio_bytes=62916、gap=0）；自回声被 `own voice heard back, no turn` 拦下。

## 未完成任务
| task | 状态 | owner | 卡点 / 下一步 |
|---|---|---|---|
| task-9（M4 发布+真机） | in_progress | lead | 等 9 哥 真机确认“告别只从本房间出”两轮；通过后打 `v0.6.0` tag → CI → 补 digest → Release |
| 控制台 IA v2 重构 | pending | - | 9 哥 要求按多音箱重做后台，参考 EchoMuse；先出设计文档再决定重写范围 |
| 第二台 Dot2 共存验收 | pending | - | 硬件未到货（9 哥 说“到了再测试”） |
| hot 层清理 | pending | lead | `rui-dot2:0.5.2-hot1..4` 与 `/home/yes999zc/hot`；0.6.0 正式发布后删 |

## 下一步（按顺序，接手方可以直接执行）
1. 跑门禁确认基线：`cd <rui> && .venv/bin/python -m pytest -o addopts='' -q` —— 验收：1348 passed / 17 skipped；`ruff check src tests` clean。
2. 核对生产：`curl -s http://<nas>:8080/health`（经 SSH）—— 验收：version 0.6.0、devices=2、device_errors=0、两 plane connected；镜像 `docker ps` → `rui-dot2:0.6.2`。
3. 等 9 哥 真机两轮（LX06 说一句等 15s、Dot2 说一句等 15s）—— 验收：`session closed` 前后 `output owner released` 的房号与说话房间一致，无 `rejected: turn_open`。
4. 通过后发版：`git tag -a v0.6.0 -m 'rui 0.6.0: device-agnostic core' && git push origin v0.6.0` → 等 `docker-publish` 绿 → `gh api /user/packages/container/rui/versions` 取 digest → 写进 `docs/releases/v0.6.0.md` → commit/push → `gh release create v0.6.0 --notes-file docs/releases/v0.6.0.md`。
5. 用官方 `0.6.0` 重建下游镜像并切生产（改 `/vol1/rui/rui-dot2-override.yml` 的 `image:` → `up -d --force-recreate`）→ 验收 `/health` 双 plane；然后删 hot 层。
6. 开《控制台 IA v2》设计（设备为中心；参考 EchoMuse 多设备后台），先写设计文档再动 `web/`。

## 约束与禁区
- 真机语音验收必须 9 哥 本人做（家人睡觉时不能测）。
- 生产变更**允许**（9 哥 已授权“有需要可以直接切”），但每次先备份 `/vol1/rui/.env`、`config/`、`rui-dot2-override.yml`，并保留回滚镜像。
- 不打印/不提交 token、密码、证书、完整内网 IP；仓库 secret-scan 会扫 docs 示例。
- 不 fork rui 源码；dot2 只做 out-of-tree adapter + 下游镜像层。
- 不每步 push（push main 触发多架构镜像重建）；发布/tag 一旦产出镜像不移动，发布前失败 tag 可删重打。
- 只改这两个仓库路径与 `04_Docs/` 内部文档。

## 已知风险与未决问题
- **混合房子必须写每设备覆盖**：全局 `audio.capture`（8ch/6mic/2ref）与 `ack.echo_ignore_ms`(1200) 会盖过 Dot2 manifest 默认 → 已写进 `docs/deploy.md §7.4` 与生产配置（Dot2 capture 1ch/0ref/S16、ack 2500）。
- **adapter 静音窗**：1.1.3 默认“播放期+2500 ms”会吃掉用户第一句（表现为 ASR 空 → `noise, no turn`）；生产用 `DOT2_PLAYBACK_GATE_MS=400` 规避，默认值是否改需真机再定。
- **`lost_playing`**：告别播放瞬间唤醒会被仲裁拒绝；不影响正常使用，待评估是否放宽。
- **`adapter_aliases.py` 只能活到 0.7**：新代码不得再写死 `xiaoai`。
- **0.6.0 release notes 的 digest 仍写 PENDING TAG**：发 tag 后必须补。
- **第二台 Dot2 行为** *(unverified)*：无硬件，共存/仲裁未验。

## 关键文件
- `docs/roadmap-0.6.md` —— 0.6.0 目标、DoD、里程碑、风险。
- `docs/releases/v0.6.0.md` —— 待发布的 release notes（digest 待补）。
- `docs/deploy.md §7.4` —— 混合房子每设备 capture/ack 模板。
- `src/rui/adapter_aliases.py` —— 唯一 `xiaoai → lx06` 兼容别名（0.7 删）。
- `src/rui/config/schema.py`（`AudioDefaults` / `effective_audio` / `effective_ack_echo_ignore_ms`）—— 三层合成契约实现。
- `plugs/xiaoai/` —— 内置 LX06 plug（manifest + adapter）。
- `src/rui/web/api/app.py` —— 房间路由、`_session_client`、`_no_turn`、doctor 路由。
- dot2：`src/rui_dot2_bridge/{adapter.py,device_link.py,plugin.yaml,devices/rs03qr.yaml}`、`deploy/downstream/`、`docs/contract-report-0.6.0.json`。
- 内部：`04_Docs/rui-0.6.0-handoff-2026-10-25.md`、`04_Docs/rui-0.5.3-audit.md`、`04_Docs/rui-dot2-maintainer-charter-2026-10-25.md`、`01_Code/device-inventory.md`（凭证，0600）。

## 核验命令（接手方先跑一遍，再相信上面的版本号）
```bash
git -C /Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear status --short
git -C /Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear log --oneline -5
git -C /Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear tag --sort=-v:refname | head -3
git -C /Users/bakeyzhang/AI_Workspace/01_Code/projects/rui-dot2-bridge log --oneline -3
cd /Users/bakeyzhang/AI_Workspace/01_Code/projects/house-ear && .venv/bin/python -m pytest -o addopts='' -q
.venv/bin/python -m ruff check src tests
cd web && npm run typecheck && npm run build
# 生产（paramiko + sudo -S，见 04_Docs 交接文档 §3）:
#   docker ps --filter name=rui --format '{{.Image}}|{{.Status}}'
#   curl -s http://127.0.0.1:8080/health
```

## 没做的事 / 不要相信的地方
- **没做**：0.6.0 的 tag/Release（等真机验收）；第二台 Dot2 验收（无硬件）；控制台 IA v2（未开始）。
- **没做**：0.6.0 官方多架构镜像的 digest（当前只有 `sha-*` 候选 digest），release notes 里的 digest 是占位。
- **不要相信**：旧 `docs/HANDOFF.md` 的历史内容（已归档为 `docs/archive/HANDOFF-history-to-2026-10-25.md`），其中版本/生产描述停在 0.5.x。
- **不要相信**：`.34` 上的实例状态（当前 `rui-dot2:0.5.3`、无真机；仅作金丝雀机）。
- **未验证**：合成客户端能证明管线，不证明真机麦克风/AEC/TLS 重连；真机结论以 9 哥 两轮验收为准。

> 本会话停在：0.6.0 代码全部完成并已部署生产、两台设备各自唤醒应答通过，只差 9 哥 的两轮“告别房间”真机确认；下一个会话第一步应该：读本文件 → 跑门禁与 `/health` 核对 → 等 9 哥 验收结果，通过即打 `v0.6.0` tag 并发版。
