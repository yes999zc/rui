<div align="center">

# 小瑞 · rui

**把家里的音箱变成只属于你的语音助手。**
*Turn the speakers you already own into a voice assistant that answers to you.*

[![CI](https://github.com/yes999zc/rui/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yes999zc/rui/actions/workflows/ci.yml)
[![secret scan](https://github.com/yes999zc/rui/actions/workflows/secret-scan.yml/badge.svg?branch=main)](https://github.com/yes999zc/rui/actions/workflows/secret-scan.yml)
[![docker publish](https://github.com/yes999zc/rui/actions/workflows/docker-publish.yml/badge.svg?branch=main)](https://github.com/yes999zc/rui/actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/github/license/yes999zc/rui?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/yes999zc/rui?style=flat-square)](https://github.com/yes999zc/rui/releases)

</div>

![控制台 v2 · 设备页（本地演示实例，示例数据）](docs/assets/console-v2-devices.png)

---

# 中文

## 15 秒了解

家里已有的音箱继续用它们自己的麦克风和喇叭，但唤醒词、对话、记忆、音乐都走你自己的服务：一台机器上的 `rui` 服务 + 每种音箱一个桥接插件。控制台 `/v2` 可以加设备、改配置、看日志和轨迹，不用再手改 YAML。

```text
说「你好小瑞」→ 它答「在呢」→ 说「放首歌」→ 播放中再说「下一首」→ 说「退下吧」→ 结束这一轮
```

## 架构

![架构](docs/assets/architecture.png)

架构图源文件：[`docs/assets/architecture.mmd`](docs/assets/architecture.mmd)；导出物 [`architecture.svg`](docs/assets/architecture.svg) / [`architecture.png`](docs/assets/architecture.png)（Mermaid；改配色后重新导出即可）。

| 层 | 职责 | 关键点 |
|---|---|---|
| **服务端（控制面）** | 会话与回合、工具调用、LLM/TTS/ASR 供应商、唤醒仲裁、v2 控制台 API、配置与密钥 | 一轮对话只由一台设备承担；播放态与回合按**房间**隔离，一屋放歌不会让另一屋变聋 |
| **音频平面** | 每台设备一个独立进程：拾音、KWS/VAD 门限、预录缓冲、回声切除、送 ASR、播放队列 | 门限在本地做，服务端只做决策；播放与唤醒的自回声有专门的剔除路径 |
| **设备（插件）** | 每种音箱一个 `rui-device/v1` 桥接插件：设备长连接、上下行音频、指示灯与按键 | 核心不含设备实现（仅保留 0.6→0.7 的临时 adapter 别名）；加一种音箱 = 加一个插件仓库 |

## 三个仓库是什么关系

| 仓库 | 角色 | 版本与发布 |
|---|---|---|
| **rui**（本仓） | 服务端 + 控制台：会话/回合、供应商、唤醒仲裁、音频门限、`/v2` 控制台 | 独立发布 |
| **[rui-dot2-bridge](https://github.com/yes999zc/rui-dot2-bridge)** | Dot2（RS03QR）的 `rui-device/v1` 插件 + 下游薄层镜像 | 独立版本、独立发布 |
| **[rui-xiaoai-bridge](https://github.com/yes999zc/rui-xiaoai-bridge)** | LX06 的 `rui-device/v1` 插件 | 独立版本、独立发布 |

约束有两条，缺一不可：设备清单里的 `compatibility.rui_min`（这台设备至少要哪个 rui）与插件清单里的 `rui_compat`（这个插件支持哪一段 rui）。两者不满足时，加载器会**拒绝**这个插件，而不是带着半截功能跑。

| rui 核心 | rui-dot2-bridge | rui-xiaoai-bridge |
|---|---|---|
| **0.7.4**（当前） | **1.3.1**：`rui_compat >=0.6.0 <0.8.0`、设备清单 `compatibility.rui_min=0.3.0`（本版无改动） | **0.2.2**：`rui_compat >=0.6.0 <0.8.0`、设备清单 `compatibility.rui_min=0.6.0` |
| 0.7.2（历史） | 1.3.1 | 0.2.1 |

> 小爱的外置插件**已在本示例中生效**（日志 `adapter plugins: registered dot2, lx06`，加载 `/opt/rui-plugins/rui_xiaoai_bridge/adapter.py`）。核心仍保留 `adapter_aliases: xiaoai→lx06` 兼容别名，**只在未挂外置插件时**回退到内置实现；不挂 xiaoai 目录实测 `registered=["dot2"]`。

## 让 AI 帮你装

把下面这段直接贴给任意 AI 助手（Claude / ChatGPT / DeepSeek 都行）：

```text
目标：用 Docker 跑起 rui 0.7.4，接一台 Dot2 和一台 LX06。
前置：docker compose version 通过；8080 空闲；5 GB 磁盘。
1) git clone --branch main https://github.com/yes999zc/rui.git && cd rui/examples/two-speakers   # main 才有本示例；镜像仍 pin 在 0.7.4
2) git clone --branch v1.3.1 https://github.com/yes999zc/rui-dot2-bridge.git && git clone --branch v0.2.2 https://github.com/yes999zc/rui-xiaoai-bridge.git
3) mkdir -p plugins && cp -r rui-dot2-bridge/src/rui_dot2_bridge rui-xiaoai-bridge/src/rui_xiaoai_bridge plugins/
4) 写 ./config/config.yaml：web.auth_token、devices[]（id/adapter/room/host/listen_port；lx06 的 host 必填）
5) TLS（dot2 的 tls_port 开启时必需）：把原 controller 证书放到 ./secrets/（server.pem + server.key）；没有就运行 `bash ../../scripts/make_dev_tls.sh` 自签。自签只保证音频平面能起，真机是否接受取决于设备信任的 CA（详见 `docs/audit/tls-required-2026-10.md`）。**默认只起第一台设备的平面；两台都起需在 config 设 `multi_device: {enabled: true, max_planes: 2}`**
6) export RUI_DEVICE_TOKEN=<设备令牌>; docker compose up -d
7) 验收：/health 里 devices=2、device_errors=0、adapter_plugins.registered=["dot2","lx06"]；audio_plane=false 时看日志的 RUI_DEVICE_TOKEN / missing TLS material
失败看：docker compose logs --tail=80 rui 的 config error / adapter / TLS；回滚：down 后把 image 换回上一版 tag；TLS 失败可先移走 ./secrets/ 确认是否 TLS 引起
不需要自己编译 ffmpeg（镜像已内置）
```

## 特性

- **多设备、按房间隔离**：设备按房间分组，唤醒与播放的互斥只在本房间生效，跨房间的播放与回合仲裁互不影响。
- **播放中打断（barge-in）**：播放时唤醒立即停播并进入对话，服务端停播延迟以毫秒计。
- **自回声抑制**：唤醒提示音（「在呢」）实测 0/10 泄漏；普通唤醒撞上进行中的告别语时，会丢弃已排队的我方语音。冷启动/重叠 <110 ms 的边界仍可能漏（见已知限制）。
- **可插拔设备**：`dot2` / `xiaoai` 两个桥接插件遵循同一份 `rui-device/v1` 契约，桥与核心可独立升级。
- **控制台 v2**：设备、房间、配置分组、日志、轨迹、音乐、凭据、体检；中英双语，字段说明来自服务端目录。
- **本地优先**：模型与密钥都在你自己的机器上；配置文件是唯一事实来源，控制台只提交你改过的字段。

## 三行启动

```bash
git clone --branch v0.7.4 https://github.com/yes999zc/rui.git && cd rui
RUI_IMAGE=ghcr.io/yes999zc/rui:0.7.4 bash deploy/install.sh   # pin 到本版本（默认是 :latest）
cat console-token.txt                       # 控制台 token（也可用 rui token 重新读取）
```

装完打开 `http://127.0.0.1:8080/v2/`，用上面的 token 登录，在「设备」页添加第一台音箱。

## 兼容性与前置条件

- Python **3.11+**；Docker 与 Docker Compose（推荐镜像 `ghcr.io/yes999zc/rui:0.7.4`）。
- 需要自备 **LLM / TTS / ASR** 供应商（在配置文件里填，或使用本地模型）。没有凭据时服务能起，但对话链路不会工作。
- 设备前置条件（第三方设备，与厂商**无官方关联**）：
  - EchoMuse Dot2（RS03QR）：固件需已具备设备本地接口，并与本服务在同一局域网；设备侧音频参数由桥接插件下发。
  - 小米 LX06 一类设备：仅支持已开放本地接口的固件版本；本项目不提供、也不建议任何刷机或越权步骤。
- 播放音乐需要自备音乐源（如 Navidrome）；未配置时「放首歌」会明确报错，不会静默失败。

## 支持矩阵

> 数字来自 **0.7.1 / p21** 的实测（2026-10-26 15:01）；**0.7.2 未重测**，未测项一律标「未测」。

| 能力 | Dot2（EchoMuse RS03QR） | LX06（第三方设备） |
|---|---|---|
| 唤醒 | 30/30 admitted（2026-10-26，6 句 × 5 轮，校准后 TTS 音源） | 19/20（2026-10-24，n=20） |
| 语音指令 | 26/30（同上，含音乐占据扬声器的场景） | 未测 |
| 播放中打断 | 8/8（2026-10-26，真机人声；**stop-push 判据**：server 停播 → 设备丢队列，非声学端到端） | 停播 205.2 ms（2026-10-24 三轮 24.2/64.5/205.2，max n=3；**stop-push，非声学端到端**） |
| 音量控制 | 未测 | 未测 |
| 指示灯 | 未测 | 未测 |
| 多设备并发 | 未测 | 未测 |
| 音乐中短指令（下一首 / 停止播放） | 10/10（2026-10-26，人声，1 m） | 未测 |

### 安装路径实测（2026-10-26）

在 `examples/two-speakers/` 下按本文件的步骤起容器（镜像 `ghcr.io/yes999zc/rui:0.7.2`，两个插件目录 bind mount）：

```console
$ curl -s localhost:8080/health | python3 -c 'import json,sys;d=json.load(sys.stdin);print({k:d[k] for k in ("status","version","devices","enabled_devices","device_errors","audio_plane","adapter_plugins")})'
{'status': 'ok', 'version': '0.7.2', 'devices': 2, 'enabled_devices': 2, 'device_errors': 0, 'audio_plane': False, 'adapter_plugins': {'registered': ['dot2', 'lx06'], 'warnings': 0}}
```

如实说明：`adapter_plugins.registered`、`devices`、`device_errors` 在任何情况下都成立。**`audio_plane` 取决于「哪台设备的平面被启动」**（实测见 `docs/audit/tls-required-2026-10.md`）：
- **默认 `multi_device.enabled=false`：单平面，只启动配置里的第一台设备**。第一台是 dot2（`listen_port` 即 `tls_port`）且 `secrets/server.pem|key` 缺失 → `audio_plane=false`，日志 `missing TLS material`；有证书（原证书或自签）→ `true`，`wss listening`。第一台是 xiaoai 且 `RUI_DEVICE_TOKEN` 为空 → `false`，日志 `RUI_DEVICE_TOKEN is empty`。
- **两台都起：需 `multi_device.enabled=true` 且 `max_planes >= 2`**，按台起平面、单台失败不拖垮其他；本卡实测的是默认单平面。
- 两个 token + 证书齐备、dot2 在前时实测 `audio_plane=true`。
自签只解决「平面能起」，真机是否接受取决于设备信任的 CA；原 controller 证书丢失时设备需重新配网。

## 故障排查

| 现象 | 原因 | 处置 |
|---|---|---|
| 喊了唤醒词没反应 | 设备未连接，或被同房间的另一轮对话占住 | 看「运行」页的告警项；确认该房间没有未结束的回合 |
| 唤醒后它自问自答 | 唤醒提示音被当成用户输入 | 真机 A/B 已 0/10 泄漏；若仍出现（尤以冷启动/紧接说话），附 `cue_gate` 日志行提 issue |
| 播放中说什么都不理 | 音乐占着扬声器时部分回答会被设计性跳过 | 属预期行为：换歌 / 停播仍会执行，只是不回话 |
| 放歌后要靠近才听得清 | 早期是测试音源电平太低造成的假象 | 先按 `docs/analysis/wake-vs-command-range.md` §7 校准音源，再判断是否设备问题 |
| 加第二台 Dot2 后第一台失效 | `DOT2_CONTROL_PORT` 是全局环境变量 | 加设备前清掉该变量，按设备分配 `listen_port` |
| 控制台打不开 | 端口 / 防火墙，或 token 过期 | `docker compose ps` 看容器；`rui token` 重新取 token |
| 升级后设备掉线 | 新镜像未包含对应桥接插件版本 | 用下游组合镜像（`rui-dot2:x.y.z`），不要用纯核心镜像 |

## 已知限制

- 唤醒提示音的回声在「冷启动 + 不削波 + 设备播放延迟 > 800 ms」或「用户与提示音重叠 < 110 ms」时仍可能漏进 ASR。
- 对话运行器目前是单会话：一台设备唤醒时会中止另一台正在合成的回复（按房间隔离目前只覆盖播放与回合仲裁）。
- 已播出的告别语尾音无法追回，只能阻止后续语音块。
- 唤醒词暂不按房间隔离；多台设备的并发能力未测。

## 致谢

设计上参考了同类项目的公开做法：Rhasspy 的分域式服务拆分、Wyoming 的协议层抽象、openWakeWord 对「未测 / 限制」的诚实标注。实现全部为本仓库原创代码。

## License

[MIT](LICENSE)。

---

# English

## In 15 seconds

Your existing speakers keep their own microphones and speakers, but wake words, dialogue, memory and music run on your own machine: one `rui` service plus one bridge plug per speaker model. The `/v2` console adds devices, edits configuration and shows logs and traces — no YAML editing required.

```text
"Hey Rui" → "I'm here" → "play some music" → "next" (while playing) → "stand down" → turn ends
```

## Architecture

![architecture](docs/assets/architecture.png)

Source: [`docs/assets/architecture.mmd`](docs/assets/architecture.mmd); exports [`architecture.svg`](docs/assets/architecture.svg) / [`architecture.png`](docs/assets/architecture.png) (Mermaid; re-export after changing colours).

| Layer | Responsibility | Why it matters |
|---|---|---|
| **Server (control plane)** | Sessions and turns, tool calls, LLM/TTS/ASR providers, wake arbitration, the v2 console API, configuration and secrets | Exactly one device owns a turn; playing state and turns are scoped **per room**, so music in one room never deafens another |
| **Audio plane** | One process per device: capture, KWS/VAD gate, preroll buffer, echo excision, ASR feed, playback queue | Gating stays local, decisions stay on the server; self-echo has a dedicated excision path |
| **Devices (plugs)** | One `rui-device/v1` bridge per speaker model: device link, uplink/downlink audio, LED and buttons | The core carries no device implementation (only a temporary 0.6→0.7 adapter alias); a new speaker is a new plug repository |

## How the three repositories fit together

| Repo | Role | Versioning |
|---|---|---|
| **rui** (this repo) | Server + console: sessions/turns, providers, wake arbitration, audio gate, `/v2` console | released on its own |
| **[rui-dot2-bridge](https://github.com/yes999zc/rui-dot2-bridge)** | `rui-device/v1` plug for Dot2 (RS03QR) plus a thin downstream image | own version, own release |
| **[rui-xiaoai-bridge](https://github.com/yes999zc/rui-xiaoai-bridge)** | `rui-device/v1` plug for LX06 | own version, own release |

Two constraints must both hold: `compatibility.rui_min` in the device manifest (the minimum core this device needs) and `rui_compat` in the plug manifest (the core range the plug supports). If either fails, the loader **refuses** the plug instead of running it half-working.

| rui core | rui-dot2-bridge | rui-xiaoai-bridge |
|---|---|---|
| **0.7.4** (current) | **1.3.1**: `rui_compat >=0.6.0 <0.8.0`, manifest `compatibility.rui_min=0.3.0` (no change in this release) | **0.2.2**: `rui_compat >=0.6.0 <0.8.0`, manifest `compatibility.rui_min=0.6.0` |
| 0.7.2 (historical) | 1.3.1 | 0.2.1 |

> The external Xiaoai plug **is active in this example** (`adapter plugins: registered dot2, lx06`, loaded from `/opt/rui-plugins/rui_xiaoai_bridge/adapter.py`). The core keeps an `adapter_aliases: xiaoai→lx06` alias that **only falls back to the built-in** when the plug dir is not mounted; without it `registered=["dot2"]`.

## Let an AI install it

Paste this into any assistant (Claude / ChatGPT / DeepSeek):

```text
Goal: run rui 0.7.4 with Docker, one Dot2 and one LX06.
Preflight: docker compose version; port 8080 free; 5 GB disk.
1) git clone --branch main https://github.com/yes999zc/rui.git && cd rui/examples/two-speakers   # main 才有本示例；镜像仍 pin 在 0.7.4
2) git clone --branch v1.3.1 https://github.com/yes999zc/rui-dot2-bridge.git && git clone --branch v0.2.2 https://github.com/yes999zc/rui-xiaoai-bridge.git
3) mkdir -p plugins && cp -r rui-dot2-bridge/src/rui_dot2_bridge rui-xiaoai-bridge/src/rui_xiaoai_bridge plugins/
4) write ./config/config.yaml: web.auth_token and devices[] (id/adapter/room/host/listen_port; host required for lx06)
5) TLS (required when dot2's tls_port is on): put the original controller cert into ./secrets/ (server.pem + server.key); if you do not have it, run `bash ../../scripts/make_dev_tls.sh`. Self-signed only proves the audio plane starts; whether the device accepts it depends on its trusted CA (see `docs/audit/tls-required-2026-10.md`). **By default only the first configured device gets a plane; to run both, set `multi_device: {enabled: true, max_planes: 2}` in the config**
6) export RUI_DEVICE_TOKEN=<device token>; docker compose up -d
7) verify: /health shows devices=2, device_errors=0, adapter_plugins.registered=["dot2","lx06"]; if audio_plane=false read the log for RUI_DEVICE_TOKEN / missing TLS material
On failure: docker compose logs --tail=80 rui (config error / adapter / TLS); rollback: down, switch image back; for TLS failures move ./secrets/ away first to isolate
No ffmpeg build needed (the image ships it)
```

## Features

- **Per-room isolation**: devices are grouped by room; wake, playback and turn arbitration are scoped to a room (the dialogue runner itself is still single-session).
- **Barge-in**: a wake during playback stops the audio immediately and starts a turn, with millisecond-scale stop latency on the server side.
- **Self-echo suppression**: the acknowledgement cue leaked 0/10 on the device; a stale goodbye still queued is dropped when a wake arrives. Cold-start or <110 ms overlap can still leak (see Known limitations).
- **Pluggable devices**: the `dot2` and `xiaoai` bridges share one `rui-device/v1` contract and version independently.
- **Console v2**: devices, rooms, grouped configuration, logs, traces, music, credentials and health checks; bilingual UI fed by a server-side catalog.
- **Local first**: models and keys stay on your machine; the config file is the source of truth and the console submits only changed fields.

## Three-line quickstart

```bash
git clone --branch v0.7.4 https://github.com/yes999zc/rui.git && cd rui
RUI_IMAGE=ghcr.io/yes999zc/rui:0.7.4 bash deploy/install.sh   # pin this release (default is :latest)
cat console-token.txt                       # console token (or: rui token)
```

Open `http://127.0.0.1:8080/v2/`, log in with that token, and add your first speaker on the Devices page.

## Compatibility and prerequisites

- Python **3.11+**, Docker and Docker Compose (recommended image: `ghcr.io/yes999zc/rui:0.7.4`).
- Your own **LLM / TTS / ASR** providers (configured in the config file, or local models). The service starts without credentials, but the dialogue path stays idle.
- Device prerequisites (third-party hardware, **not affiliated with any vendor**):
  - EchoMuse Dot2 (RS03QR): firmware must already expose the device's local interface and be on the same LAN; audio parameters are pushed by the bridge.
  - Xiaomi LX06-class devices: only firmware that already exposes a local interface is supported. No flashing, rooting or privilege-escalation steps are provided or endorsed.
- Music needs your own source (e.g. Navidrome); without one, "play some music" fails loudly instead of silently.

## Support matrix

> Measured on **0.7.1 / p21** on 2026-10-26 15:01 (0.7.2 not re-measured). Anything untested is marked **untested**.

| Capability | Dot2 (EchoMuse RS03QR) | LX06 (third-party) |
|---|---|---|
| Wake | 30/30 admitted (2026-10-26, 6 phrases × 5 rounds, calibrated TTS source) | 19/20 (2026-10-24, n=20) |
| Voice commands | 26/30 (same run, including music-owns-speaker cases) | untested |
| Barge-in during playback | 8/8 (2026-10-26, real voice; **stop-push criterion**: server stop → device queue drop, not acoustical end-to-end) | 205.2 ms stop (2026-10-24, three runs 24.2/64.5/205.2, max n=3; **stop-push, not acoustical end-to-end**) |
| Volume control | untested | untested |
| LED | untested | untested |
| Concurrent multi-device | untested | untested |
| Short commands during music (next / stop) | 10/10 (2026-10-26, human voice, 1 m) | untested |

### Install-path proof (2026-10-26)

Started from `examples/two-speakers/` following the steps in this file (image `ghcr.io/yes999zc/rui:0.7.2`, both plug dirs bind-mounted):

```console
$ curl -s localhost:8080/health | python3 -c 'import json,sys;d=json.load(sys.stdin);print({k:d[k] for k in ("status","version","devices","enabled_devices","device_errors","audio_plane","adapter_plugins")})'
{'status': 'ok', 'version': '0.7.2', 'devices': 2, 'enabled_devices': 2, 'device_errors': 0, 'audio_plane': False, 'adapter_plugins': {'registered': ['dot2', 'lx06'], 'warnings': 0}}
```

To be precise: `adapter_plugins.registered`, `devices` and `device_errors` always hold. **`audio_plane` depends on which device's plane is started** (measured; see `docs/audit/tls-required-2026-10.md`):
- **Default `multi_device.enabled=false`: one plane, only the first configured device.** dot2 first (`listen_port` is `tls_port`) without `secrets/server.pem|key` → `audio_plane=false`, log `missing TLS material`; with a certificate (original or self-signed) → `true`, `wss listening`. xiaoai first with an empty `RUI_DEVICE_TOKEN` → `false`, log `RUI_DEVICE_TOKEN is empty`.
- **Both devices: set `multi_device.enabled=true` and `max_planes >= 2`**; each device gets a plane and one failure does not take down the others. This card measured the default single-plane path.
- With both tokens and the certificate, dot2 first, the measured value is `audio_plane=true`.
Self-signed only makes the plane start; real-device acceptance depends on the device's trusted CA, and a lost original controller certificate means the device must be re-provisioned.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Wake word does nothing | Device offline, or another turn owns the room | Check the alerts on the Run page; make sure no turn is left open in that room |
| It answers itself | The acknowledgement cue was transcribed as user speech | Device A/B is now 0/10 leaks; if it still happens (cold start or immediate speech), attach the `cue_gate` log lines |
| No response while music plays | When music owns the speaker, some replies are skipped by design | Expected: track changes and stop still execute, they just do not speak |
| Commands only work up close | Earlier runs used a test source that was too quiet | Calibrate the source first (`docs/analysis/wake-vs-command-range.md` §7) before blaming the device |
| A second Dot2 breaks the first | `DOT2_CONTROL_PORT` is a global environment variable | Unset it, then assign a `listen_port` per device |
| Console does not load | Port/firewall, or an expired token | `docker compose ps`, then `rui token` for a fresh token |
| Devices drop after an upgrade | The new image lacks the matching bridge version | Deploy the combined image (`rui-dot2:x.y.z`), not the bare core |

## Known limitations

- Cue echo can still slip into ASR on a cold start with no clipping and >800 ms device latency, or when the user overlaps the cue by less than 110 ms.
- The dialogue runner is single-session: a wake on one device aborts a reply being synthesised for another (per-room isolation currently covers playback and turn arbitration only).
- A goodbye that has already been spoken cannot be recalled; only queued chunks are dropped.
- Wake words are not yet isolated per room, and concurrent multi-device behaviour is untested.

## Credits

The design borrows patterns that are public in comparable projects: Rhasspy's domain split, Wyoming's protocol layer, and openWakeWord's habit of labelling untested areas. All code here is original to this repository.

## License

[MIT](LICENSE).
