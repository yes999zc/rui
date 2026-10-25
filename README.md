<div align="center">
  <img src="docs/screenshots/brand-mark.svg" width="72" alt="小瑞 / Rui">
  <h1>小瑞 · Rui</h1>
  <p><strong>把家里的音箱，变成只属于你的语音助手。</strong><br>
  <em>Turn the speaker you already own into a private voice assistant.</em></p>
  <p>
    <a href="https://github.com/yes999zc/rui/actions/workflows/ci.yml">
      <img src="https://github.com/yes999zc/rui/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI">
    </a>
    <a href="https://github.com/yes999zc/rui/actions/workflows/secret-scan.yml">
      <img src="https://github.com/yes999zc/rui/actions/workflows/secret-scan.yml/badge.svg?branch=main" alt="secret scan">
    </a>
    <a href="https://github.com/yes999zc/rui/actions/workflows/docker-publish.yml">
      <img src="https://github.com/yes999zc/rui/actions/workflows/docker-publish.yml/badge.svg?branch=main" alt="docker publish">
    </a>
    <a href="LICENSE">
      <img src="https://img.shields.io/badge/license-MIT-0e6b63?style=flat-square" alt="MIT license">
    </a>
    <a href="https://github.com/yes999zc/rui/releases/latest">
      <img src="https://img.shields.io/github/v/release/yes999zc/rui?style=flat-square" alt="latest release">
    </a>
  </p>
  <p><a href="#中文">中文</a> · <a href="#english">English</a></p>
</div>

> **Name note:** repository, image, Python package, CLI, config paths and environment variables are all
> `rui`; 0.1.x upgrades use the [migration guide](docs/migration-house-ear-to-rui.md).

---

## 中文

### 0.4.2 有什么新东西

> **0.4.2（2026-10-25）完成控制台 IA Phase 4 收尾：导航只有一个事实来源，旧路由正式退场。**
>
> - **单一 nav manifest**：后端 PanelSpec 直接声明 section/order/hidden/icon，前端只消费；删掉重复的导航映射与标题覆盖。
> - **11 个面板、7 个 legacy panel 退役**：旧 `/panel/assistant-session`、`/panel/speaker` 等旧书签不再兼容；`/api/v1/*` 不变。
> - **首页瘦身**：README 从 512 行压到 366 行，安装/向导/排障细节移入 [`docs/first-run.md`](docs/first-run.md)；截图稍后与内容一起刷新。
> - **多设备设计文档**：[`docs/multi-device-design.md`](docs/multi-device-design.md) 回答主/卫星、房间与双唤醒仲裁（设计稿，未改运行时）。
>
> 0.4.1 / 0.4.0 的更新见 [v0.4.1](https://github.com/yes999zc/rui/releases/tag/v0.4.1) / [v0.4.0](https://github.com/yes999zc/rui/releases/tag/v0.4.0)。
>
> [v0.4.2 Release](https://github.com/yes999zc/rui/releases/tag/v0.4.2) · [CHANGELOG](CHANGELOG.md) ·
> [对比 v0.4.1...v0.4.2](https://github.com/yes999zc/rui/compare/v0.4.1...v0.4.2)

### 这是什么

小瑞（Rui）是一个**自托管、本地优先**的家庭语音终端：一台你拥有的音箱通过 WebSocket 拨入 NAS，唤醒、AEC、VAD、播放和本地 ASR 都在你自己的机器上完成。默认不依赖厂商云；只有你显式配置的 provider 会收到请求。

- **完整回合本地编排**：唤醒 → 分段 → ASR → LLM（可调工具）→ 流式 TTS → 推回音箱。
- **一个控制台管全部**：中英双语、明暗主题、两分组导航；供应商/音源预设、只写凭据、设备管理、向导、诊断与日志。
- **一条命令部署到 NAS**：Docker Compose + amd64/arm64 镜像；配置/数据/模型在卷里，音频面崩溃由 watchdog 拉起。

### 界面

<p align="center"><img src="docs/screenshots/console-overview-zh.png" width="860" alt="小瑞控制台总览"></p>
<p align="center"><em>总览：真实信号、试一试、音频面遥测。</em></p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/console-providers-zh.png" width="420" alt="AI 供应商"></td>
    <td width="50%"><img src="docs/screenshots/console-setup-en.png" width="420" alt="First-run setup"></td>
  </tr>
  <tr>
    <td><em>AI 供应商：三段链路、预设/自定义、就绪徽章与试听。</em></td>
    <td><em>首次运行向导：密钥、模型、设备准备/接线、测试、试一句。</em></td>
  </tr>
</table>

> 截图来自真实控制台，已隐去 LAN 地址和个人路径；截图稍后与内容一起刷新。

### 30 秒快速开始

**前置**：Linux NAS（amd64/arm64，4 核 / 4 GB 内存 / 2 GB 磁盘），Docker Engine + `docker compose`；
音箱已运行 [open-xiaoai](https://github.com/coderzc/open-xiaoai) client，并与 NAS 同 LAN/L2。

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/deploy/install.sh \
  | RUI_REF=v0.4.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2 sh
```

首次含镜像拉取和 239 MB 模型通常 **4–8 分钟**；镜像已就绪、只需 ASR 时约 **85 秒**。滚动版本把 tag 换成 `main` 即可。

安装后打开 `http://<nas-lan-ip>:8080/`，输入 console token（0.4.1+ 存在 `~/rui/console-token.txt`；
缺失时运行 `cd ~/rui && docker compose exec -T rui rui token`）。**必须用 NAS 的局域网 IP**，
否则 `connect` 会生成 `127.0.0.1` 地址。目录权限、代理、源码回退和手动 Compose 见 [docs/deploy.md](docs/deploy.md)。

### 第一次运行

安装后控制台进入 `/setup`，8 步都在浏览器完成；最少凭据是 **LLM + TTS**（ASR 默认本地 SenseVoice）。
8 步是 `keys → models → prepare → params → wake → connect → test → verify`，每步可重跑，正常路径不需要手改 YAML。
完整 8 步表、参数来源、连接排障和安装后命令见 [docs/first-run.md](docs/first-run.md)：
[8 步向导](docs/first-run.md#八步向导) · [参数来源](docs/first-run.md#音箱参数从哪来) ·
[连接排障](docs/first-run.md#连接接线与排障) · [安装后命令](docs/first-run.md#安装后命令速查)。

### 功能

| 领域 | 能力 |
|---|---|
| 语音回合 | 唤醒 → 分段 → ASR → LLM + 工具 → 流式 TTS → 播放；音频面负责 AEC、KWS、VAD、播放仲裁 |
| ASR / TTS | 本地 SenseVoice（默认）、火山流式 ASR 2.0、讯飞/火山豆包 TTS；按音色/语速/情感试听 |
| 供应商 | 11 个 LLM 预设 + 自定义 OpenAI 兼容端点；预设创建、保存前探测、就绪/缺配置徽章、只写密钥 |
| 音乐 | Subsonic 家族（Navidrome/Airsonic/Gonic/Ampache）+ Jellyfin/Emby + custom；搜索/随机/收藏/播放，真实连接测试 |
| 工具与集成 | 天气/新闻/搜索/提醒/记忆；Home Assistant 第一方 MCP（六工具白名单/危险域硬拒）；通用 MCP（headers + 只写 `token_env`/stdio 闸） |
| 控制台 | 中英双语、明暗主题、两分组约 12 个入口；总览/对话/记忆/日志/体检 |
| 设备与插头 | Socket/Plug v1（manifest/能力/`device_wake`）、LX06 参考插头、设备状态/启停/token 轮换 |
| 通道与运维 | Telegram/微信 iLink；多架构镜像、非 root 容器、last-good 配置恢复、备份/恢复、CVE 扫描、SSRF 出站守卫 |

### 架构

```mermaid
flowchart LR
  browser["浏览器控制台<br/>Vue 3 · 中/英 · 明/暗"] <-->|REST /api/v1| ctl["控制面<br/>FastAPI · 回合/会话/工具/配置"]
  ctl <-->|Unix socket<br/>PCM + JSON 控制帧| plane["音频面<br/>AEC · KWS · VAD · 播放仲裁"]
  plane <-->|WebSocket :4412<br/>8ch 麦克风 + 2ch 参考| speaker["小爱音箱 Pro LX06<br/>open-xiaoai client"]
  ctl --> providers["Provider 链<br/>ASR · LLM · TTS"]
  ctl --> tools["工具<br/>天气 · 新闻 · 搜索 · 提醒"]
  ctl --> ha["Home Assistant<br/>via MCP"]
  ctl --> mcp["通用 MCP 服务<br/>HTTP / stdio"]
  ctl --> music["Subsonic + MediaBrowser"]
```

几条固定规则：

1. **只有音频面碰麦克风和音箱**；控制面不直接向设备写音频。
2. **播放只有一个 owner**：提示音 P0 → 回答 P1 → 提醒 P2 → 音乐 P3。
3. **设备拨入，控制面不主动连设备**。
4. 机型差异只进 adapter/plugin + manifest。

分层/进程/IPC 见 [docs/architecture.md](docs/architecture.md)，音频链路见 [docs/audio-pipeline.md](docs/audio-pipeline.md)，
插头契约见 [docs/socket-plug-v1.md](docs/socket-plug-v1.md)。

### 支持与状态

| 范围 | 状态 |
|---|---|
| 发布 | **0.4.2**（2026-10-25）；Phase 4 已完成 |
| 镜像 / 质量 | `ghcr.io/yes999zc/rui:0.4.2`，amd64+arm64；1,142 passed / 18 skipped；ruff、web build、secret-scan 绿 |
| 宿主 | Linux NAS（amd64/arm64），Docker + compose |
| 音箱 | LX06 + open-xiaoai client（同 LAN/L2，回连 `4412`）；dot2 走外部 `rui-dot2-bridge` |
| 音源 | Subsonic 家族 + Jellyfin/Emby（MediaBrowser）；Plex 尚未实现 |
| 开发 | macOS/Linux 可 `uv run rui serve`；生产形态是 NAS 容器 |

**v1 验收**（2026-10-24，真实 NAS + LX06）：冷装 423 s；80% 音量放歌 19/20 唤醒；打断 max 205.2 ms。

### 已知限制

- **单设备**：0.4.x 一个 `:4412` 入口只接一个音箱 client；多设备仲裁/每设备配置 v3 在路线图上。
- **只支持 LX06**：需要 open-xiaoai client、同 LAN/L2；刷机/装 client 属于上游，Rui 不代刷。
- **第三方插头隔离**：sidecar、签名/allowlist、多设备 owner 仲裁尚未交付；in-process 需可信 manifest。
- **音乐协议**：Plex 尚未实现；新增协议需要适配器。
- **控制台收尾**：Phase 4 已完成；旧 legacy 面板/路由不再兼容（有意），截图稍后与内容一起刷新。
- **体验欠账**：手机对话、音乐收藏/口味 UI 等见 [docs/remaining-work.md](docs/remaining-work.md)。
- **生产微信**：Telegram 已生产验证；微信 iLink 接口保留，生产验证未完成。

### 文档

- 入门：[first-run](docs/first-run.md) · [deploy](docs/deploy.md) · [runbook](docs/runbook.md) · [open-xiaoai 接入](docs/open-xiaoai-client.md)
- 参考：[architecture](docs/architecture.md) · [security](docs/security.md) ·
  [security-scanning](docs/security-scanning.md) · [config catalog](docs/config-catalog-contract.md)
- 控制台与音频：[console](docs/console.md) · [console IA audit](docs/console-ia-audit.md) · [audio pipeline](docs/audio-pipeline.md)
- 功能：[provider presets](docs/provider-presets.md) · [music sources](docs/music-sources.md) ·
  [MCP/HA](docs/integrations-mcp.md) · [Socket/Plug v1](docs/socket-plug-v1.md)
- 项目：[CHANGELOG](CHANGELOG.md) · [releases](docs/releases/) · [ROADMAP](docs/ROADMAP.md) · [remaining work](docs/remaining-work.md)
- 开发：[deploy](docs/deploy.md) · [runbook](docs/runbook.md)
- 版本：[v0.4.2](https://github.com/yes999zc/rui/releases/tag/v0.4.2) · [v0.4.1](https://github.com/yes999zc/rui/releases/tag/v0.4.1) · [v0.4.0](https://github.com/yes999zc/rui/releases/tag/v0.4.0)

### 安全与隐私

- **本地优先**：音频、KWS/VAD/AEC、本地 ASR 都在你的机器上；只有你配置的 LLM/TTS/搜索/音乐/HA/MCP 服务收到请求。
- **密钥只写不读**：`.env` / `data/secrets.yaml`（0600）；provider 草稿密钥只在内存；音乐凭据在 `lan_media.json`（0600）；接口只回 `has_key`/`has_value`。
- **控制台与非 root 容器**：随机 token，非 loopback 无 token 拒绝启动；容器 uid/gid 1000、只读根文件系统、`cap_drop: ALL` + `no-new-privileges`。
- **出站与供应链**：SSRF 守卫（默认允许内网、拒绝回环/云元数据）；CI 跑 pip-audit/Trivy/secret-scan；设备 token 等同音箱控制权。细节见 [docs/security.md](docs/security.md)。
- 漏洞请勿公开开 issue，先通过 GitHub 私下联系维护者。

### 致谢

- [open-xiaoai](https://github.com/coderzc/open-xiaoai) — 音箱端 client 与 LX06 回连协议（MIT）
- [EchoMuse](https://github.com/wilbowes/EchoMuse) — 设备管理后台参考实现（MIT，未直接分发）
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — 本地 ASR / 唤醒词运行时（Apache-2.0）
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — 本地语音识别模型（FunASR Model License，非 MIT）
- [Silero VAD](https://github.com/snakers4/silero-vad) — 语音活动检测（MIT）
- [SpeexDSP](https://github.com/xiph/speexdsp) — 回声消除（BSD-3-Clause）
- [FFmpeg](https://ffmpeg.org/) — 音乐/语音转码（LGPL-2.1+）

各组件保留自己的许可证；完整清单见 [THIRD_PARTY.md](THIRD_PARTY.md)。

### License 与品牌

代码以 **MIT** 发布，见 [LICENSE](LICENSE)；`小瑞 / Rui` 名称与 logo 是项目品牌资产，不随 MIT 授权用于其他产品品牌。

---

## English

### What's new in 0.4.2

> **0.4.2 (2026-10-25) finishes the console IA Phase 4: one navigation source, legacy routes retired.**
>
> - **Single nav manifest:** PanelSpec now carries section/order/hidden/icon; the frontend only consumes it, with the duplicate maps and title overrides deleted.
> - **11 panels, 7 legacy panels retired:** old bookmarks such as `/panel/assistant-session` or `/panel/speaker` no longer resolve; `/api/v1/*` is unchanged.
> - **Slimmer homepage:** README drops from 512 to 366 lines; install/wizard/troubleshooting detail moved to [`docs/first-run.md`](docs/first-run.md). Screenshots will be refreshed with the next content pass.
> - **Multi-device design doc:** [`docs/multi-device-design.md`](docs/multi-device-design.md) answers primary/satellite, rooms and double-wake arbitration (design only, runtime unchanged).
>
> 0.4.1 / 0.4.0 notes: [v0.4.1](https://github.com/yes999zc/rui/releases/tag/v0.4.1) / [v0.4.0](https://github.com/yes999zc/rui/releases/tag/v0.4.0).
>
> [v0.4.2 Release](https://github.com/yes999zc/rui/releases/tag/v0.4.2) · [CHANGELOG](CHANGELOG.md) ·
> [Compare v0.4.1...v0.4.2](https://github.com/yes999zc/rui/compare/v0.4.1...v0.4.2)

### What it is

**Rui (小瑞)** is a self-hosted, local-first voice endpoint for a speaker you already own: a Xiaomi LX06
running the open-xiaoai client dials into your NAS over WebSocket, while wake word, AEC, VAD, playback and
local ASR stay on your machine. No vendor cloud is required; only the providers you configure see requests.

- **The full turn runs locally:** wake → segment → ASR → LLM (with tools) → streaming TTS → back to the speaker.
- **One console for everything:** bilingual zh/en, light/dark, two groups; provider/music presets,
  write-only credentials, devices, wizard, diagnostics and logs.
- **One command to a NAS:** Docker Compose + amd64/arm64 images; config/data/models in volumes, with an audio-plane watchdog.

### Screenshots

<p align="center"><img src="docs/screenshots/console-overview-zh.png" width="860" alt="Rui console overview"></p>
<p align="center"><em>Overview: live signals, try-it composer and audio-plane telemetry.</em></p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/console-providers-zh.png" width="420" alt="AI providers"></td>
    <td width="50%"><img src="docs/screenshots/console-setup-en.png" width="420" alt="First-run setup"></td>
  </tr>
  <tr>
    <td><em>AI providers: chains, presets/custom endpoints, readiness and audition.</em></td>
    <td><em>First-run wizard: keys, models, device prep/hookup, test and one sentence.</em></td>
  </tr>
</table>

> Captures come from a real console with LAN addresses and personal paths redacted. They show the 0.4.x console; a screenshot refresh is planned with the next content pass.

### 30-second quick start

**Prerequisites:** Linux NAS (amd64/arm64, 4 cores / 4 GB RAM / 2 GB disk) with Docker Engine +
`docker compose`; the speaker already runs [open-xiaoai](https://github.com/coderzc/open-xiaoai) on the same LAN/L2.

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/deploy/install.sh \
  | RUI_REF=v0.4.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2 sh
```

A first run with an image pull and the 239 MB model usually takes **4–8 minutes**; with the image already
local and only ASR to fetch, about **85 seconds**. Replace the tag with `main` for the rolling release.

Open `http://<nas-lan-ip>:8080/` and enter the console token (0.4.1+ writes `~/rui/console-token.txt`; if
missing, run `cd ~/rui && docker compose exec -T rui rui token`). **Use the NAS LAN IP**, or `connect`
generates a `127.0.0.1` URL. Host requirements, proxies, source fallback and manual Compose: [docs/deploy.md](docs/deploy.md).

### First run

The console opens `/setup`; all eight steps are in the browser, and the minimum credentials are
**LLM + TTS** (ASR defaults to local SenseVoice). The steps are
`keys → models → prepare → params → wake → connect → test → verify`; each can be rerun and the normal path
needs no YAML edits. Full step tables, parameter sources, connection troubleshooting and after-install
commands: [docs/first-run.md](docs/first-run.md) · [8-step wizard](docs/first-run.md#the-8-step-wizard) ·
[parameter sources](docs/first-run.md#where-each-param-comes-from) ·
[connect troubleshooting](docs/first-run.md#connect-troubleshooting) ·
[after-install commands](docs/first-run.md#after-install-command-reference).

### Features

| Area | What you get |
|---|---|
| Voice turn | Wake → segment → ASR → LLM + tools → streaming TTS → playback; the audio plane owns AEC, KWS, VAD and playback arbitration |
| ASR / TTS | Local SenseVoice (default), Volc ASR 2.0, XFYUN/Volc TTS; audition by voice/rate/emotion |
| Providers | 11 LLM presets + custom OpenAI-compatible endpoints; create/probe presets, readiness badges, write-only keys |
| Music | Subsonic family (Navidrome/Airsonic/Gonic/Ampache) + Jellyfin/Emby + custom; search/random/favorites/playback, real connection test |
| Tools & integrations | Weather/news/search/reminders/memory; Home Assistant first-party MCP (six-tool whitelist, dangerous-domain refusal); generic MCP (headers + write-only `token_env`/stdio gate) |
| Console | Bilingual zh/en, light/dark, two groups and about 12 destinations; overview/turns/memory/logs/diagnostics |
| Devices & plugs | Socket/Plug v1 (manifest/capabilities/`device_wake`), LX06 reference plug, device status/start-stop/token rotation |
| Channels & ops | Telegram/WeChat iLink; multi-arch images, non-root container, last-good config recovery, backup/restore, CVE scanning, SSRF egress guard |

### Architecture

```mermaid
flowchart LR
  browser["Browser console<br/>Vue 3 · zh/en · light/dark"] <-->|REST /api/v1| ctl["Control plane<br/>FastAPI · turn/session/tools/config"]
  ctl <-->|Unix socket<br/>PCM + JSON control frames| plane["Audio plane<br/>AEC · KWS · VAD · playback arbitration"]
  plane <-->|WebSocket :4412<br/>8ch mic + 2ch reference| speaker["AI Speaker Pro LX06<br/>open-xiaoai client"]
  ctl --> providers["Provider chains<br/>ASR · LLM · TTS"]
  ctl --> tools["Tools<br/>weather · news · search · reminders"]
  ctl --> ha["Home Assistant<br/>via MCP"]
  ctl --> mcp["Generic MCP servers<br/>HTTP / stdio"]
  ctl --> music["Subsonic + MediaBrowser"]
```

Hard rules:

1. **Only the audio plane touches mic/speaker**; the control plane never writes audio.
2. **Playback has one owner**: cue P0 → reply P1 → reminder P2 → music P3.
3. **Devices dial in**; the control plane does not dial out.
4. Device differences live in the adapter/plugin + manifest.

Layers/process/IPC: [docs/architecture.md](docs/architecture.md); audio: [docs/audio-pipeline.md](docs/audio-pipeline.md);
plug contract: [docs/socket-plug-v1.md](docs/socket-plug-v1.md).

### Support and status

| Scope | Status |
|---|---|
| Release | **0.4.2** (2026-10-25); Phase 4 complete |
| Images / quality | `ghcr.io/yes999zc/rui:0.4.2`, amd64+arm64; 1,142 passed / 18 skipped; ruff, web build, secret-scan green |
| Host | Linux NAS (amd64/arm64), Docker + compose |
| Speaker | LX06 + open-xiaoai client (same LAN/L2, dials `4412`); dot2 via external `rui-dot2-bridge` |
| Music | Subsonic family + Jellyfin/Emby (MediaBrowser); Plex not implemented |
| Development | macOS/Linux can run `uv run rui serve`; the production form is a NAS container |

**v1 acceptance** (2026-10-24, real NAS + LX06): 423 s cold install; 19/20 wakes at 80% volume; barge max 205.2 ms.

### Known limitations

- **Single device:** one `:4412` entry accepts one speaker client in 0.4.x; multi-device arbitration/per-device config v3 remain on the roadmap.
- **LX06 only:** needs the open-xiaoai client and same LAN/L2; flashing/client install are upstream, not done by Rui.
- **Third-party plugs:** sidecar isolation, signing/allowlists and multi-device owner arbitration are not shipped; in-process needs a trusted manifest.
- **Music protocols:** Plex is not implemented; a new protocol needs an adapter.
- **Console cleanup:** Phase 4 is complete; legacy panel routes no longer resolve (intentional), and screenshots will be refreshed with the next content pass.
- **UX debt:** mobile turns, music favorites/taste UI and more are in [docs/remaining-work.md](docs/remaining-work.md).
- **WeChat in production:** Telegram is production-verified; the WeChat iLink interface is kept, but production validation is still pending.

### Documentation

- Start: [first-run](docs/first-run.md) · [deploy](docs/deploy.md) · [runbook](docs/runbook.md) · [open-xiaoai hookup](docs/open-xiaoai-client.md)
- Reference: [architecture](docs/architecture.md) · [security](docs/security.md) ·
  [security-scanning](docs/security-scanning.md) · [config catalog](docs/config-catalog-contract.md)
- Console & audio: [console](docs/console.md) · [console IA audit](docs/console-ia-audit.md) · [audio pipeline](docs/audio-pipeline.md)
- Features: [provider presets](docs/provider-presets.md) · [music sources](docs/music-sources.md) ·
  [MCP/HA](docs/integrations-mcp.md) · [Socket/Plug v1](docs/socket-plug-v1.md)
- Project: [CHANGELOG](CHANGELOG.md) · [releases](docs/releases/) · [ROADMAP](docs/ROADMAP.md) · [remaining work](docs/remaining-work.md)
- Development: [deploy](docs/deploy.md) · [runbook](docs/runbook.md)
- Releases: [v0.4.2](https://github.com/yes999zc/rui/releases/tag/v0.4.2) · [v0.4.1](https://github.com/yes999zc/rui/releases/tag/v0.4.1) · [v0.4.0](https://github.com/yes999zc/rui/releases/tag/v0.4.0)

### Security and privacy

- **Local-first:** audio, KWS/VAD/AEC and local ASR stay on your machine; only the LLM/TTS/search/music/HA/MCP services you configure receive requests.
- **Write-only secrets:** `.env` / `data/secrets.yaml` (0600); provider draft keys stay in memory;
  music credentials live in `lan_media.json` (0600); APIs return only `has_key`/`has_value`.
- **Console and container:** random console token, non-loopback bind without one is refused; container runs
  as uid/gid 1000 with a read-only root filesystem, `cap_drop: ALL` + `no-new-privileges`.
- **Egress and supply chain:** SSRF guard (private networks allowed by default, loopback/cloud metadata
  refused); CI runs pip-audit/Trivy/secret-scan; the device token is equivalent to speaker control.
  Details: [docs/security.md](docs/security.md).
- Report vulnerabilities privately through GitHub instead of opening a public issue.

### Credits

- [open-xiaoai](https://github.com/coderzc/open-xiaoai) — speaker-side client and LX06 dial-in protocol (MIT)
- [EchoMuse](https://github.com/wilbowes/EchoMuse) — device-management console reference (MIT; no code shipped)
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — local ASR and wake-word runtime (Apache-2.0)
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — local speech-recognition model (FunASR Model License, not MIT)
- [Silero VAD](https://github.com/snakers4/silero-vad) — voice-activity detection (MIT)
- [SpeexDSP](https://github.com/xiph/speexdsp) — echo cancellation (BSD-3-Clause)
- [FFmpeg](https://ffmpeg.org/) — music/voice transcoding (LGPL-2.1+)

Each keeps its own license; the full inventory is in [THIRD_PARTY.md](THIRD_PARTY.md).

### License and brand

Code is released under **MIT**; see [LICENSE](LICENSE). The `小瑞 / Rui` name and logo are project brand assets, separate from the MIT grant.
