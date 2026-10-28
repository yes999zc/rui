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

Latest release: https://github.com/yes999zc/rui/releases

---

# 中文

## 这是什么？给谁用？

rui 是一个**跑在你自己机器上的家庭语音助手端点**。家里已有的音箱继续用它们自己的麦克风和喇叭，唤醒、对话、记忆、音乐都由你的 rui 服务处理：一台常开的机器（NAS / 小主机 / 服务器）跑 rui 核心和 v2 控制台，每种音箱由一个桥接插件接入。模型、密钥、配置都在你自己的机器上，配置文件是唯一事实来源。

适合这样的家庭：想自己掌控语音与数据、不想换掉手头的音箱、需要多台设备分布在多个房间，并且愿意维护一台 Docker 主机。

## 核心能力

- **语音会话**：唤醒 → 对话 → 记忆，支持播放中打断（barge-in）与自回声抑制。
- **多设备、按房间隔离**：每台音箱一个音频平面，唤醒与播放按房间隔离；一屋放歌不会让另一屋变聋。
- **音乐**：接入你自己的音乐源（如 Navidrome / OpenSubsonic），支持「下一首」「停止播放」等播放中短指令。
- **提醒**：到点与分钟级相对时间提醒由本机播报，不依赖第三方定时服务。
- **控制台 v2**：设备、房间、配置、日志、轨迹、音乐、凭据与体检；中英双语，不用再手改 YAML。

## 新设备接入：一条命令，AI 可代劳

[`scripts/onboard_device.py`](https://github.com/yes999zc/rui-dot2-bridge/blob/main/scripts/onboard_device.py) 把「备份 → 写入 → 逐字节读回校验 → 重启设备服务 → 等 rui 报 connected」收进一条命令：主机、端口、令牌、序列号全是参数，仓库里没有任何真实部署信息。目前面向已解锁并刷好 EchoMuse 的 Echo Dot 2；LX06 在控制台或 `config.yaml` 里添加即可。

把这个脚本交给任意 AI 助手，它可以代你执行并解释每一步：

```bash
python scripts/onboard_device.py --device-id dot2-b --rui http://<your-rui>:8080 \
  --rui-token-file ~/.rui/console-token.txt --secrets-dir ~/rui/dot2-secrets   # 先加 --dry-run 只打印计划
```

参数、两种模式（只改地址 / 换部署）、回滚与常见错误见 [docs/onboard-device.md](https://github.com/yes999zc/rui-dot2-bridge/blob/main/docs/onboard-device.md)。

## 架构

![架构](docs/assets/architecture.png)

一句话：**rui 核心（控制面）**负责会话与回合、LLM/TTS/ASR 供应商、唤醒仲裁和控制台 API；**音频平面**在每台设备上本地拾音、门控与播放，只把决策交给服务端；**桥接插件**（`rui-device/v1`，如 dot2 / xiaoai）把音箱接进来，桥与核心可独立升级。

架构图源文件：[`docs/assets/architecture.mmd`](docs/assets/architecture.mmd)；导出物 [`architecture.svg`](docs/assets/architecture.svg) / [`architecture.png`](docs/assets/architecture.png)。

## 快速开始

```bash
git clone https://github.com/yes999zc/rui.git && cd rui
bash deploy/install.sh        # 默认拉取最新发布的镜像（ghcr.io/yes999zc/rui:latest）
cat console-token.txt         # 控制台 token；之后也可用 `rui token` 重新读取
```

打开 `http://127.0.0.1:8080/v2/`，用上面的 token 登录，在「设备」页添加第一台音箱。两台音箱（Dot2 + LX06）的完整示例见 [`examples/two-speakers/`](examples/two-speakers/)。

## 兼容性与版本窗口

核心与每个桥都独立发布；设备清单里的 `compatibility.rui_min` 和插件清单里的 `rui_compat` 必须同时满足，加载器会对越界插件直接拒载。当前兼容窗口以各仓最新 release 的说明为准。

桥的 release：[rui-dot2-bridge](https://github.com/yes999zc/rui-dot2-bridge/releases) / [rui-xiaoai-bridge](https://github.com/yes999zc/rui-xiaoai-bridge/releases)。

## 三个仓库

| 仓库 | 角色 |
|---|---|
| **rui**（本仓） | 服务端 + v2 控制台：会话/回合、供应商、唤醒仲裁、音频门限、配置与密钥 |
| **[rui-dot2-bridge](https://github.com/yes999zc/rui-dot2-bridge)** | Echo Dot 2（RS03QR）的 `rui-device/v1` 插件，以及「一条命令接入新设备」脚本 |
| **[rui-xiaoai-bridge](https://github.com/yes999zc/rui-xiaoai-bridge)** | 小米 AI 音箱 Pro（LX06）的 `rui-device/v1` 插件 |

## 已知限制

- 回声切除仍有边界缺口：冷启动且设备播放延迟较大、或用户与唤醒提示音重叠很小时，提示音回声仍可能进入 ASR。
- 对话运行器是单会话：一台设备唤醒会中止另一台正在合成的回复；第二台同时发起对话会被 409 拒绝。
- 唤醒词暂不按房间隔离；两台同型号 Dot2 同屋的并发行为尚未验证。

完整清单见 [`docs/known-limitations.md`](docs/known-limitations.md)。

## 更多文档

- [`docs/deploy.md`](docs/deploy.md) —— 部署、镜像与排障
- [`docs/architecture.md`](docs/architecture.md) —— 分层与数据流
- [`docs/known-limitations.md`](docs/known-limitations.md) —— 编号限制清单
- [`CHANGELOG.md`](CHANGELOG.md) —— 版本变化只记录在 release 正文与 CHANGELOG

## 致谢

这个项目站在很多人的工作上。按它们**在这套系统里的实际位置**分两类；许可证与分发细节见 [THIRD_PARTY.md](THIRD_PARTY.md)。

**跑在链路里的**

- [@coderzc/open-xiaoai](https://github.com/coderzc/open-xiaoai)（MIT）—— LX06 的设备端客户端与 WebSocket 协议；设备靠它拨进来，核心只讲协议、不内置它的代码。
- [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）—— 本地 ASR 与关键词唤醒运行时，镜像里的唤醒模型与 VAD 也经由它的 release 分发。
- [snakers4/silero-vad](https://github.com/snakers4/silero-vad)（MIT）—— 语音活动检测模型。
- [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / [modelscope/FunASR](https://github.com/modelscope/FunASR) —— 本地中文 ASR 模型；权重按 FunASR Model License 使用，`rui fetch-models` 下载，不随镜像分发。
- [FFmpeg](https://ffmpeg.org/)（LGPL-2.1-or-later，构建时禁用 GPL/nonfree）—— 音乐转码与渠道语音编码；镜像里随附 libopus、libmp3lame 的许可证。
- [xiph/speexdsp](https://github.com/xiph/speexdsp)（BSD-3-Clause）—— 回声消除，播放中打断靠它。
- [openWakeWord](https://github.com/dscripka/openWakeWord)（Apache-2.0）—— 设备端 OWW 模型（如 `hey_jarvis`）来自这套方案；目前设备端唤醒是关闭的，走服务端 KWS。

**设计上参考了公开做法**

- [Rhasspy](https://github.com/rhasspy/rhasspy) 的分域式服务拆分、[rhasspy/wyoming](https://github.com/rhasspy/wyoming) 的协议层抽象。
- [wilbowes/EchoMuse](https://github.com/wilbowes/EchoMuse) —— 设备管理控制台的状态机与「可解释向导」思路（未复制代码）。
- openWakeWord 对「未测 / 限制」照实标注的习惯，我们也沿用了。

音乐播放对接 [Navidrome](https://github.com/navidrome/navidrome) / OpenSubsonic；控制台用 Vue + Vite，服务端 FastAPI。实现代码除上述依赖外均为本仓库原创。

## License

[MIT](LICENSE)。

---

# English

## What this is, and who it is for

rui is a **voice-assistant endpoint for your own home**. Your existing speakers keep their own microphones and speakers; wake words, dialogue, memory and music are handled by your rui service: one always-on machine (NAS / mini PC / server) runs the rui core and the v2 console, and each speaker model is connected through a bridge plug. Models, keys and configuration stay on your machine, with the config file as the single source of truth.

It is for households that want to own their voice data, keep the speakers they already have, run several devices across rooms, and are comfortable maintaining a Docker host.

## Core capabilities

- **Voice sessions**: wake → dialogue → memory, with barge-in during playback and self-echo suppression.
- **Multiple devices, isolated by room**: one audio plane per speaker; wake and playback are scoped per room, so music in one room never deafens another.
- **Music**: your own music source (e.g. Navidrome / OpenSubsonic), including short commands such as next and stop while playing.
- **Reminders**: due and minute-scale relative reminders are spoken locally, without a third-party timer service.
- **Console v2**: devices, rooms, configuration, logs, traces, music, credentials and health checks; bilingual, no hand-editing YAML.

## Onboarding a new device: one command, an AI can do it

[`scripts/onboard_device.py`](https://github.com/yes999zc/rui-dot2-bridge/blob/main/scripts/onboard_device.py) folds backup → write → byte-for-byte read-back → restart the device server → wait until rui reports it connected into one command: host, port, token and serial are all flags, and no real deployment details live in the repository. It currently targets an unlocked Echo Dot 2 flashed with EchoMuse; an LX06 is added from the console or `config.yaml`.

Hand the script to any AI assistant and it can run it for you, explaining each step:

```bash
python scripts/onboard_device.py --device-id dot2-b --rui http://<your-rui>:8080 \
  --rui-token-file ~/.rui/console-token.txt --secrets-dir ~/rui/dot2-secrets   # add --dry-run to preview
```

Parameters, the two modes (repoint only vs. re-provision), rollback and troubleshooting: [docs/onboard-device.md](https://github.com/yes999zc/rui-dot2-bridge/blob/main/docs/onboard-device.md).

## Architecture

![architecture](docs/assets/architecture.png)

In one sentence: the **rui core (control plane)** owns sessions and turns, LLM/TTS/ASR providers, wake arbitration and the console API; **audio planes** capture, gate and play locally on each device and send only decisions to the server; **bridge plugs** (`rui-device/v1`, such as dot2 / xiaoai) connect the speakers, and plugs version independently of the core.

Source: [`docs/assets/architecture.mmd`](docs/assets/architecture.mmd); exports [`architecture.svg`](docs/assets/architecture.svg) / [`architecture.png`](docs/assets/architecture.png).

## Quickstart

```bash
git clone https://github.com/yes999zc/rui.git && cd rui
bash deploy/install.sh        # pulls the latest published image (ghcr.io/yes999zc/rui:latest)
cat console-token.txt         # console token; `rui token` re-reads it later
```

Open `http://127.0.0.1:8080/v2/`, log in with that token, and add your first speaker on the Devices page. A complete two-speaker (Dot2 + LX06) example lives in [`examples/two-speakers/`](examples/two-speakers/).

## Compatibility and version windows

The core and every bridge are released independently; `compatibility.rui_min` in the device manifest and `rui_compat` in the plug manifest must both hold, and the loader refuses out-of-range plugs outright. The current windows are whatever the latest release of each repository says.

Bridge releases: [rui-dot2-bridge](https://github.com/yes999zc/rui-dot2-bridge/releases) / [rui-xiaoai-bridge](https://github.com/yes999zc/rui-xiaoai-bridge/releases).

## The three repositories

| Repository | Role |
|---|---|
| **rui** (this repo) | Server + v2 console: sessions/turns, providers, wake arbitration, audio gate, configuration and secrets |
| **[rui-dot2-bridge](https://github.com/yes999zc/rui-dot2-bridge)** | `rui-device/v1` plug for Echo Dot 2 (RS03QR), plus the one-command onboarding script |
| **[rui-xiaoai-bridge](https://github.com/yes999zc/rui-xiaoai-bridge)** | `rui-device/v1` plug for Xiaomi AI Speaker Pro (LX06) |

## Known limitations

- Echo excision still has edge gaps: on a cold start with high device latency, or when the user overlaps the acknowledgement cue by a very short window, cue echo can still reach ASR.
- The dialogue runner is single-session: a wake on one device aborts a reply being synthesised for another; a second simultaneous turn is refused with 409.
- Wake words are not isolated per room yet, and two same-model Dot2 units in one room have not been verified.

The full list is in [`docs/known-limitations.md`](docs/known-limitations.md).

## More documentation

- [`docs/deploy.md`](docs/deploy.md) — deployment, images and troubleshooting
- [`docs/architecture.md`](docs/architecture.md) — layers and data flow
- [`docs/known-limitations.md`](docs/known-limitations.md) — the numbered limitation list
- [`CHANGELOG.md`](CHANGELOG.md) — release-by-release changes live here and in release notes

## Credits

This project stands on other people's work. Grouped by where each one actually sits; licenses and distribution details live in [THIRD_PARTY.md](THIRD_PARTY.md).

**In the pipeline**

- [@coderzc/open-xiaoai](https://github.com/coderzc/open-xiaoai) (MIT) — the LX06 device client and WebSocket protocol. The speaker dials in through it; we speak the protocol and ship none of its code.
- [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache-2.0) — local ASR and keyword-spotting runtime; the wake-word and VAD models in the image come through its releases.
- [snakers4/silero-vad](https://github.com/snakers4/silero-vad) (MIT) — voice-activity detection model.
- [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / [modelscope/FunASR](https://github.com/modelscope/FunASR) — the local Chinese ASR model; weights are used under the FunASR Model License and fetched at runtime, not shipped.
- [FFmpeg](https://ffmpeg.org/) (LGPL-2.1-or-later, built without GPL/nonfree) — music transcoding and channel voice encoding; libopus and libmp3lame notices ship with the image.
- [xiph/speexdsp](https://github.com/xiph/speexdsp) (BSD-3-Clause) — echo cancellation, which is what makes barge-in work.
- [openWakeWord](https://github.com/dscripka/openWakeWord) (Apache-2.0) — the device-side OWW models (e.g. `hey_jarvis`); on-device wake is off today and the server-side KWS does the work.

**Borrowed in design**

- [Rhasspy](https://github.com/rhasspy/rhasspy)'s domain split and [rhasspy/wyoming](https://github.com/rhasspy/wyoming)'s protocol layering.
- [wilbowes/EchoMuse](https://github.com/wilbowes/EchoMuse) — state machine and explainable-wizard ideas for the device console (no code copied).
- openWakeWord's habit of labelling untested areas plainly.

Music talks to [Navidrome](https://github.com/navidrome/navidrome) / OpenSubsonic; the console is Vue + Vite on top of FastAPI. Everything else here is original to this repository.

## License

[MIT](LICENSE).
