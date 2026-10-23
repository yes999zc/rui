<div align="center">
  <img src="docs/screenshots/brand-mark.svg" width="72" alt="小瑞 / Rui">
  <h1>小瑞 · Rui</h1>
  <p><strong>把家里的音箱，变成只属于你的语音助手。</strong><br>
  <em>Turn the speaker you already own into a private voice assistant.</em></p>
  <p>
    <a href="https://github.com/yes999zc/rui/actions/workflows/ci.yml"><img src="https://github.com/yes999zc/rui/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
    <a href="https://github.com/yes999zc/rui/actions/workflows/secret-scan.yml"><img src="https://github.com/yes999zc/rui/actions/workflows/secret-scan.yml/badge.svg?branch=main" alt="secret scan"></a>
    <a href="https://github.com/yes999zc/rui/actions/workflows/docker-publish.yml"><img src="https://github.com/yes999zc/rui/actions/workflows/docker-publish.yml/badge.svg?branch=main" alt="docker publish"></a>
    <a href="https://github.com/yes999zc/rui/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-0e6b63?style=flat-square" alt="MIT license"></a>
    <img src="https://img.shields.io/badge/version-0.0.1-d98a3e?style=flat-square" alt="version 0.0.1">
    <img src="https://img.shields.io/badge/status-v1%20in%20progress-8a4f0a?style=flat-square" alt="v1 in progress">
  </p>
  <p><a href="#中文">中文</a> · <a href="#english">English</a> · <a href="https://github.com/yes999zc/rui">GitHub</a></p>
</div>

> **项目名 / 旧名**：仓库和镜像是 `rui`，Python 包与 CLI 仍是 `house-ear`（保证旧安装、配置路径和命令兼容）。完整改名要单独迁移，见 [docs/remaining-work.md](docs/remaining-work.md)。

---

## 中文

### 这是什么

小瑞（Rui）是一个**自托管、本地优先**的家庭语音终端。它不把麦克风音频交给云端语音助手，而是让一台你拥有的音箱通过 WebSocket 拨入家里的服务：

- **唤醒词、VAD、回声消除、播放都在本地音频面完成**；ASR 优先本地 SenseVoice，可选火山流式识别 2.0，云端只做兜底；
- **TTS 链可选讯飞与火山豆包 2.0**，控制台可按音色/语速/情感试听；**完整回合**：唤醒 → 分段 → ASR → LLM（可调工具）→ 流式 TTS → 推回音箱；
- **能力**：Home Assistant（经 MCP）、通用 MCP 服务、天气/新闻/联网搜索、提醒、记忆、Navidrome/Subsonic 音乐；
- **控制台 2.0**：中英双语、明暗主题、四层配置目录、AI 供应商链路与音色试听、安装向导、诊断/日志；
- **一条命令部署到 NAS**：Docker Compose + 多架构镜像，数据/配置/模型都在卷里；音频面崩溃由 watchdog 拉起。

v1 目前只支持 **Xiaomi 小爱音箱 Pro（LX06）**，且音箱需要运行 **open-xiaoai client** 来拨入本服务。一键安装只部署 NAS 服务，不会修改音箱。

### 界面

<p align="center">
  <img src="docs/screenshots/console-overview-zh.png" width="860" alt="小瑞控制台总览">
</p>
<p align="center"><em>总览：真实信号、试一试、音频面遥测——不是占位数据。</em></p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/console-providers-zh.png" width="420" alt="AI 供应商"></td>
    <td width="50%"><img src="docs/screenshots/console-setup-en.png" width="420" alt="First-run setup"></td>
  </tr>
  <tr>
    <td><em>AI 供应商：三段链路、供应商卡片、音色选择与音箱/本机试听。</em></td>
    <td><em>首次运行向导：密钥、模型、音箱、唤醒词、试一句。</em></td>
  </tr>
</table>

> 截图来自本地开发实例，已隐去 LAN 地址和个人路径；没有公开任何密钥或 token。

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
  ctl --> music["Navidrome / Subsonic"]
```

几条固定规则：

1. **只有音频面碰麦克风和音箱**；控制面不直接向设备写音频。
2. **播放只有一个 owner**：唤醒提示音 P0 → 回答 P1 → 提醒 P2 → 音乐 P3；高优先级可以暂停/打断低优先级。
3. **设备拨入，控制面不主动连设备**（除了设备 client 自己暴露的有限 shell 通道）。
4. 设备、DSP、provider、工具之间只通过接口通信；新增一款音箱理论上只改一个 adapter 模块 + 一行注册。

更深入的分层、进程模型和 IPC 契约见 [docs/architecture.md](docs/architecture.md)，音频链路细节见 [docs/audio-pipeline.md](docs/audio-pipeline.md)。

### 快速开始

#### 前置条件

| 需要 | 说明 |
|---|---|
| 一台 Docker 主机 | NAS / Linux，x86_64 或 arm64，建议 4 核 / 4 GB / 2 GB 可用磁盘 |
| 小爱音箱 Pro（LX06） | 已刷/已装 **open-xiaoai client**，与主机在同一 LAN |
| LLM 与 TTS 密钥 | 至少一组；ASR 可以先用本地模型 |
| 可选 | Home Assistant、Navidrome、火山/讯飞语音账号 |

#### 一条命令（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

安装脚本做五件事：

| 步骤 | 内容 |
|---|---|
| 1 | 拉取 `docker-compose.yml` 和 `config.example.yaml` 到 `~/house-ear/` |
| 2 | `house-ear setup` 写入生产配置模板，并生成默认唤醒词文件 `keywords.txt` |
| 3 | 生成 `.env`（含随机设备 token，0600） |
| 4 | 下载本地模型：SenseVoice ASR + Silero VAD + KWS 唤醒模型（约 272 MB，一次） |
| 5 | `docker compose up -d`，等待 health 通过 |

镜像：`ghcr.io/yes999zc/rui:latest`（公开，多架构 amd64/arm64）。如果镜像拉取失败，脚本会**从公开源码自动构建**同一 tag；也可以手动：

```bash
git clone https://github.com/yes999zc/rui.git
cd rui
docker build -t ghcr.io/yes999zc/rui:latest .
```

安装完成后：

```bash
cd ~/house-ear
# 控制台访问令牌（安装时生成；也可以稍后读取）
docker compose exec house-ear house-ear token
```

打开 `http://<nas-lan-ip>:8080/`，输入控制台 token，按**首次运行向导**完成：密钥 → 模型 → 添加音箱 → 唤醒词 → 试一句。

#### 音箱端（必须单独做）

音箱必须运行 open-xiaoai client 并回连：

```text
ws://<nas-lan-ip>:4412/?token=<HOUSE_EAR_DEVICE_TOKEN>
```

设备 token 在 `~/house-ear/.env` 的 `HOUSE_EAR_DEVICE_TOKEN`；服务端和 client 必须一致。详细接线、音量/LED 能力、协议说明见 [docs/deploy.md](docs/deploy.md) 和 [docs/xiaoai-protocol.md](docs/xiaoai-protocol.md)。

#### 手动 Docker Compose

```bash
mkdir -p ~/house-ear && cd ~/house-ear
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/docker-compose.yml -O
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/config.example.yaml -O
# 按 docs/deploy.md 填 .env，再：
docker compose up -d
```

### 配置速览

配置目录由服务端声明，控制台按目录渲染，不需要为每个参数改前端。重点区域：

| 区域 | 关键项 | 说明 |
|---|---|---|
| AI 供应商 | `providers.catalog` + `providers.{asr,tts,llm}.chain` | 每个 stage 一条有序链；音色/语速/情感可在控制台改并试听 |
| 音箱 | `devices[]` | adapter、回连端口、房间、唤醒词、音量、LED；v1 仅 LX06 |
| 音频 | `audio.capture / voice / playback / aec` | 采样率、帧长、VAD、AEC、播放缓冲；改完重启音频面 |
| 会话 | `session.*` | 空闲超时、结束词、上下文开关、filler 策略 |
| 音乐 | `music.*` | `navidrome` 源、凭据写入、缓存；凭据只写不读 |
| 工具 | `tools.*` | 搜索、天气、新闻、Home Assistant、MCP servers、工具暴露策略 |
| 控制台 | `web.*` | 端口、鉴权 token、日志级别 |

- 完整字段/效果/重启规则：[docs/config-catalog-contract.md](docs/config-catalog-contract.md)
- 部署、密钥、音乐/HA 配置：[docs/deploy.md](docs/deploy.md)
- 音乐层：[docs/music.md](docs/music.md)；通用 MCP：[docs/mcp-generic.md](docs/mcp-generic.md)
- 日常运维（起停、日志、升级、回滚）：[docs/runbook.md](docs/runbook.md)

### 远程通道（Telegram / 微信）

小瑞可以额外通过 Telegram 或微信 iLink/ClawBot 接收文字和语音，不需要音箱在线：

- 独立进程 `house-ear-channels`，复用控制面的 LLM/工具/会话，不依赖音频面；
- Telegram：长轮询、allowlist、语音输入、语音回复；
- 微信 iLink：扫码登录、`context_token`、`.silk` 重转写、音频文件回复；
- 控制台 **通道 / Channels** 页：启用、allowlist、回复模式、绑定/启动指令。

详见 [docs/channel-gateway.md](docs/channel-gateway.md)。

### 状态与已知缺口

**状态：v1 开发中。** 已跑通：完整语音回合、本地 ASR、音乐、HA/MCP、记忆、控制台、品牌/双语文案、Docker 多架构镜像与一键安装（含源码回退）。

部分实测（开发/本机硬件，不是通用 benchmark）：

| 项目 | 结果 | 来源 |
|---|---|---|
| 音频面（mock 48 kHz/8ch，5 分钟） | 14,234 帧，0 丢帧，rtf 0.0055 | `scripts/measure_plane.py` |
| AEC（6ch，doctor） | rtf ≈ 0.004（约单核 0.4%） | `house-ear doctor` |
| 本地 ASR（SenseVoice int8，短句） | 开发机 51–90 ms；NAS N100 约 210 ms | [docs/audio-latency.md](docs/audio-latency.md) |
| 完整回合（讯飞 TTS + deepseek-flash） | 20/20 回复，首响 p50 约 3.6 s | 同上 |

**还没闭环的**（完整列表和顺序见 [docs/remaining-work.md](docs/remaining-work.md)）：

- v1 验收项：80% 音量放歌 20 次唤醒 ≥19、打断 ≤300 ms 口径、完整发布检查；
- 真机遗留：远场唤醒计数、音量对账、音乐收藏/口味 UI、第二台设备（config v3）；
- 体验：手机对话卡片化、Providers 阶段标题/手机控件等细节；
- 完整改名：仓库/镜像已是 rui，Python 包与 CLI 仍是 house-ear（兼容旧安装）。

> README 不承诺未实现的功能；以 `docs/remaining-work.md` 和实际控制台为准。

### 开发

```bash
# Python 控制面 / 音频面
uv sync --extra dev
uv run pytest
uv run ruff check src tests

# 本地开发服务（默认 :8080；开发脚本也可能用 :8091）
uv run house-ear serve

# 控制台
cd web
npm install
npm run typecheck
npm run build
```

- 部署与生产运维：[docs/deploy.md](docs/deploy.md) · [docs/runbook.md](docs/runbook.md)
- 设计/控制台契约：[docs/console.md](docs/console.md) · [docs/brand.md](docs/brand.md)
- 音频时延与调参：[docs/audio-latency.md](docs/audio-latency.md) · [docs/audio-pipeline.md](docs/audio-pipeline.md)

### 安全与隐私

- **本地优先**：音频、KWS/VAD/AEC 和本地 ASR 都在你自己的机器上；只有你配置的 LLM/TTS/搜索/音乐/HA 服务会收到相应请求。
- **密钥只写不读**：`.env` 或 `secrets.yaml`（0600）注入；控制台只显示 `has_key`，所有接口都不回显值。
- **控制台鉴权**：安装时生成随机 token；非 loopback 且无 token 时服务拒绝启动。不要把 `:8080` 直接暴露到公网；远程访问请走 Tailscale/VPN 或带 TLS 的反向代理。
- 设备 token 决定音箱回连权限，和音箱管理员权限等同，按家庭网络边界保护。
- 漏洞/安全问题请不要公开开 issue，先通过 GitHub 私下联系维护者。

### License 与品牌

代码目前以 **MIT** 发布，见 [LICENSE](LICENSE)；许可证变更不在本轮范围，需要单独讨论。`小瑞 / Rui` 名称与 logo 是项目品牌资产，不随 MIT 授权用于其他产品品牌；截图仅用于本项目展示。

---

## English

> **Name note:** the GitHub repo and container image are `rui`; the Python package and CLI
> remain `house-ear` so existing installs, paths and commands keep working. A full rename is
> tracked in [docs/remaining-work.md](docs/remaining-work.md).

### What it is

**Rui (小瑞)** is a self-hosted, local-first voice endpoint for a speaker you already own. A Xiaomi 小爱音箱 Pro (LX06) running the **open-xiaoai client** dials into your home server over WebSocket; the local audio plane handles wake word, VAD, echo cancellation and playback, and the control plane runs the full turn:

> wake → segment → local ASR (cloud fallback) → LLM + tools → streaming TTS → PCM back to the speaker.

ASR prefers local SenseVoice, with Volc streaming ASR 2.0 and a cloud fallback as optional chain entries; TTS can use Xfyun and Volc Doubao 2.0, with voice/rate/emotion audition in the console. It also ships a bilingual admin console (four-tier config catalog, provider-chain UX, setup wizard, diagnostics), Home Assistant over MCP, generic MCP servers, weather/news/web search, reminders, memory, and Navidrome/Subsonic music.

### Quick start

Requirements: a Docker host (NAS/Linux, amd64 or arm64), an LX06 with the **open-xiaoai client**, and at least one LLM/TTS key. The local SenseVoice ASR does not need a cloud key.

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

The installer writes `~/house-ear/`, generates a `.env` with a random device token, downloads the ASR/VAD/KWS models (~272 MB, one time), starts `docker compose`, and waits for health. Public image: **`ghcr.io/yes999zc/rui:latest`** (multi-arch). If the registry is unreachable, the installer builds the same tag from public source; manual build:

```bash
git clone https://github.com/yes999zc/rui.git
cd rui && docker build -t ghcr.io/yes999zc/rui:latest .
```

Open `http://<nas-lan-ip>:8080/`, enter the console token printed at install time, and complete the first-run wizard. The speaker itself is configured separately:

```text
ws://<nas-lan-ip>:4412/?token=<HOUSE_EAR_DEVICE_TOKEN>
```

See [docs/deploy.md](docs/deploy.md) for volumes, keys, music/HA configuration and the speaker client, [docs/open-xiaoai-client.md](docs/open-xiaoai-client.md) for the step-by-step client hookup, and [docs/xiaoai-protocol.md](docs/xiaoai-protocol.md) for the LX06 wire protocol.

### Architecture

```mermaid
flowchart LR
  browser["Browser console<br/>Vue 3 · zh/en · light/dark"] <-->|REST /api/v1| ctl["Control plane<br/>FastAPI · turn/session/tools/config"]
  ctl <-->|Unix socket<br/>PCM + JSON| plane["Audio plane<br/>AEC · KWS · VAD · playback"]
  plane <-->|WebSocket :4412<br/>8ch mic + 2ch ref| speaker["LX06 speaker<br/>open-xiaoai client"]
  ctl --> providers["Provider chains<br/>ASR · LLM · TTS"]
  ctl --> tools["Tools<br/>weather · news · search · reminders"]
  ctl --> ha["Home Assistant via MCP"]
  ctl --> mcp["Generic MCP servers"]
  ctl --> music["Navidrome / Subsonic"]
```

Hard rules: only the audio plane touches mic/speaker; playback has a single owner (cue P0 → reply P1 → reminder P2 → music P3); devices dial in, the control plane does not dial out. Deeper details: [docs/architecture.md](docs/architecture.md) · [docs/audio-pipeline.md](docs/audio-pipeline.md).

### Configuration

Provider order and fields live in `config.yaml`: `providers.catalog` plus one ordered chain per stage (`providers.asr/tts/llm.chain`). Speakers are `devices[]`; the audio plane is `audio.*`; session, music, tools and console/security settings are grouped in the console's four-tier catalog. Everything is editable in the console (422 validation maps back onto fields), or via the CLI/config file. See [docs/config-catalog-contract.md](docs/config-catalog-contract.md) and [docs/deploy.md](docs/deploy.md).

### Status and known gaps

**v1 is in progress.** Working: full turn, local ASR, music, HA/MCP, memory, console, brand/i18n, multi-arch image and the one-command install (with source fallback). Open items include the v1 acceptance numbers (19/20 wake at 80% volume, interrupt ≤300 ms), far-field/volume reconciliation, the second device (config v3), and some console polish. The authoritative list is [docs/remaining-work.md](docs/remaining-work.md); this README does not claim features that are not implemented.

### Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv run house-ear serve          # :8080

cd web
npm install
npm run typecheck
npm run build
```

Deploy/ops: [docs/deploy.md](docs/deploy.md) · [docs/runbook.md](docs/runbook.md). Audio latency notes: [docs/audio-latency.md](docs/audio-latency.md). Console contract: [docs/console.md](docs/console.md).

### Security and privacy

Audio and local recognition stay on your box; only the providers you configure receive the corresponding requests. Keys are injected via `.env` / `secrets.yaml` (0600) and are write-only through the API. The console uses a generated token and refuses to start on a non-loopback bind without one. Keep `:8080` on a trusted network (or put it behind TLS/VPN); the device token is equivalent to speaker control. See [docs/runbook.md](docs/runbook.md).

### 配置恢复

成功启动会把当前配置存为 `config.last-good.yaml`。如果下次启动时 `config.yaml` 校验失败，服务会保留坏文件、回退到 last-good（没有则用 schema 默认值），并在控制台顶部显示恢复 banner，可一键恢复；也可以：

```bash
house-ear config restore --last-good
house-ear config restore --defaults
```

详见 [docs/config-recovery.md](docs/config-recovery.md)。

### Support

如果小瑞帮到了你，可以请我喝杯咖啡：

<!-- TODO: replace the placeholder with the real Buy Me a Coffee page when ready. -->
<a href="https://www.buymeacoffee.com/" target="_blank" rel="noopener">
  <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-☕-d98a3e?style=flat-square" alt="Buy Me a Coffee">
</a>

If Rui is useful to you, you can buy me a coffee — the link is a placeholder for now.

### License and brand

Code is currently **MIT**; see [LICENSE](LICENSE). A license change is out of scope for this round and needs a separate discussion. The 小瑞 / Rui name and logo are project brand assets, separate from the code license.
