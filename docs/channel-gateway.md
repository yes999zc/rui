# Channel Gateway — Telegram + 微信 iLink/ClawBot 开发清单

> 状态：**M0–M6 已实现**（2026-10-23）。M3/M4 的真实 Telegram 消息与微信扫码仍需要 9 哥 的手机/token 配合；
> 代码、测试和 Channels 控制台页已落地。
>
> 目标：让小瑞除音箱/控制台外，还能通过 **Telegram** 和 **微信 iLink Bot / ClawBot** 收发文字与语音。
> 原则：channel 只做 I/O 和身份，复用控制面现有 LLM/tools/session；**不依赖音频面**（音箱离线也能用）。

## 1. 总体架构

```
Telegram / 微信 iLink / 控制台
        │
        ▼
house-ear channel-gateway（独立进程，可选容器）
  ├─ adapter: telegram
  ├─ adapter: weixin-ilink
  ├─ allowlist / pairing / dedupe / rate limit
  ├─ inbound: text / voice / (image、file 后续)
  └─ outbound: text / TTS voice / 可选音箱播报
        │  HTTP + token（局域网/localhost）
        ▼
house-ear 控制面
  ├─ ConversationService（从 debug/say 抽出的正式文本回合）
  ├─ 现有 LLM / tools / session / memory
  └─ 可选：announce 到客厅音箱
```

关键决定：

1. **channel-gateway 不放进音频面**；它是独立进程/容器，崩溃不影响语音。
2. **微信和 Telegram 共用一个 gateway 核心**，只写两个 adapter；以后接其他平台只加 adapter。
3. **默认独立会话**：`telegram:<chat_id>` / `weixin:<user_id>`，不与客厅 session 混用；需要“继续客厅话题”时再显式关联。
4. **文字优先**：先跑通文字；语音输入直接下载原始 `.silk` 重转写；语音回复以**文件附件**发送，不承诺语音气泡。
5. **权限先只读**：channel 里可以查/说，但危险动作必须二次确认，不能直接执行。

## 2. 里程碑与任务

| ID | 任务 | 产出 | 依赖 | 需要 9 哥 |
|---|---|---|---|---|
| M0 | API/配置契约 | `docs/channel-gateway.md`、config schema、错误码 | — | 否 |
| M1 | 正式文本回合 | `POST /api/v1/chat`（text→reply，可选音频），复用 session/tools | M0 | 否 |
| M2 | gateway 骨架 | adapter 协议、allowlist、去重、持久化、任务队列、日志脱敏 | M0 | 否 |
| M3 | Telegram adapter | 长轮询、文字、语音输入、语音回复、命令、测试 | M1/M2 | **是**（独立 bot token + chat id） |
| M4 | 微信 iLink adapter | 扫码登录、getupdates、context_token、sendmessage、文字 | M1/M2 | **是**（扫码 + 测试消息） |
| M5 | 微信语音/媒体 | CDN AES 下载 `.silk` → pilk 转 WAV → ASR 重转写；回复发音频文件 | M4 | 是（语音测试） |
| M6 | 控制台 Channels 页 | 绑定/解绑、二维码、状态、allowlist、回复模式、日志 | M3/M4 | 否 |
| M7 | 部署/安全/验收 | 独立进程/容器、compose、文档、速率限制、E2E | M3–M6 | 是（真机 E2E） |

## 3. 详细任务清单

### M0 — 契约（0.5 天）

- [ ] 定义 `POST /api/v1/chat`：请求 `channel / peer_id / text / session_id? / want_audio? / speak_on_device?`；响应 `reply / audio? / session_id / latency_ms`。
- [ ] 定义 `POST /api/v1/asr`（音频→文本）和 `POST /api/v1/tts`（文本→音频），供 channel 复用 provider 链。
- [ ] 定义 channel 身份、会话 key、reset 命令、错误码、幂等键。
- [ ] 定义 `channels.*` 配置：enabled、allowlist、reply_mode(text/voice/both)、mirror_to_speaker、凭证路径。
- [ ] 安全约束：channel token、allowlist、日志不打印 token/context_token、危险动作二次确认。

### M1 — 控制面正式文本回合（1–2 天）

- [ ] 从 `/api/v1/debug/say` 抽出 `ConversationService`（不依赖音频面、设备、播放）。
- [ ] `POST /api/v1/chat` 走同一套 session/LLM/tools；支持 `source=console|telegram|weixin`。
- [ ] 可选 `want_audio`：返回 TTS PCM/文件，供 channel 发语音。
- [ ] 可选 `speak_on_device`：把回复也投到指定音箱（复用现有 announce/debug 播放路径，默认关）。
- [ ] 单元测试：文本回合、session 隔离、超时、空回复、工具调用、无音频面。

### M2 — gateway 骨架（1–2 天）

- [ ] `ChannelAdapter` 协议：`start/stop/poll`、`send_text`、`send_voice`、`send_typing`、`capabilities`。
- [ ] `ChannelGateway`：配置加载、adapter 生命周期、每 peer 串行队列、重试、超时、优雅退出。
- [ ] allowlist / pairing；未授权消息静默丢弃并记一条脱敏日志。
- [ ] 去重（Telegram update_id / 微信 message_id）和持久化（offset/游标/context_token）。
- [ ] 日志脱敏：token、context_token、语音内容、手机号/用户 ID 部分打码。
- [ ] 单元测试：fake adapter、队列顺序、去重、异常恢复。

### M3 — Telegram（2–3 天；真机需 bot）

- [ ] 长轮询 `getUpdates`，不要求公网 webhook；`offset` 持久化。
- [ ] allowlist chat_id；`/start`、`/new`、`/stop`、`/status`、`/whoami`。
- [ ] 文字：调用 `/api/v1/chat`；4096 字符分片；HTML/纯文本转义；typing 状态。
- [ ] 语音输入：`getFile` 下载 OGG/Opus → ffmpeg 转 16k mono PCM → `/api/v1/asr`。
- [ ] 语音回复（可选）：TTS PCM → OGG/Opus → `sendVoice`。
- [ ] 错误处理：网络重试、超时、消息过长、文件过大、非法格式。
- [ ] 测试：fake Telegram API；真机需要**独立 bot token**（不要复用 Hermes 的 bot，避免 getUpdates 冲突）。

### M4 — 微信 iLink/ClawBot 文字（3–4 天；真机需扫码）

- [ ] 二维码登录：`get_bot_qrcode` → 轮询 `get_qrcode_status` → `confirmed`；凭证存 `data/channels/weixin/`，0600。
- [ ] 控制台“绑定微信”显示二维码；支持重新登录/退出绑定。
- [ ] 长轮询 `getupdates`；持久化 opaque 的 `get_updates_buf`；进程重启恢复。
- [ ] 消息解析：TEXT/VOICE/IMAGE/FILE/VIDEO；单聊优先，群聊先禁用。
- [ ] allowlist：只允许绑定账号的 `ilink_user_id`。
- [ ] `context_token` 按 `(account, peer)` 持久化；sendmessage 必须回传；无 token 时只等用户先发。
- [ ] 发送：2000 字符分片、client_id、typing（getconfig/sendtyping）、-14 重新扫码、-2 退避。
- [ ] 接入 `/api/v1/chat`：文字进、文字出，控制台/音箱可选。
- [ ] 测试：协议 fixture；真机需要**新的 iLink bot 账号扫码**（不要和 Hermes/OpenClaw 共用同一个 bot，长轮询只能一个消费者）。

### M5 — 微信语音/媒体（2–3 天）

- [ ] 入站 VOICE：**不用**腾讯 `voice_item.text`；按 Hermes 的做法下载原始 `.silk` → CDN AES-128-ECB 解密 → `pilk.silk_to_wav` 转 WAV → `/api/v1/asr` 重新转写。解码失败不 fallback 腾讯转写。
- [ ] `pilk` 作为单独的 `weixin-voice` optional extra（懒安装；NAS Telegram-only 镜像不装）；解码失败时明确报错/让用户重发，**不把腾讯 `voice_item.text` 当作用户输入**（仅日志留作诊断）。
- [ ] 出站语音：**作为文件附件发送（media_type=FILE）**，不是语音气泡；Hermes 的 `send_voice(force_file_attachment=True)` 已证明这条路径可用。
- [ ] 产物格式优先 `.mp3`（ffmpeg 从 TTS PCM 转），兼容性比 `.silk` 好；附一句“语音回复已作为文件发送”。
- [ ] 图片/文件：后续版本，不在第一版。
- [ ] 验收：微信发语音 → 重新转写正确；小瑞回音频文件 → 微信可下载/播放。

### M6 — 控制台 Channels 页（1–2 天）

- [ ] “通道”页：Telegram/微信 两个卡片，启用/禁用、状态、最后消息时间、错误。
- [ ] 微信：显示二维码/登录状态/解绑；Telegram：token 配置、allowlist、测试消息按钮。
- [ ] 回复模式：仅文字 / 文字+语音 / 仅语音（按 adapter 能力灰显）。
- [ ] “同时让客厅说”开关；默认关。
- [ ] 中英双语、明暗主题、手机布局。

### M7 — 部署与验收（1–2 天）

- [ ] 独立进程 `house-ear channel-gateway`；可选 compose profile；不扩大主镜像时可做独立镜像/extra。
- [ ] NAS/Mac 配置文档：长轮询无需公网；凭证权限 0600；日志脱敏。
- [ ] 速率限制、超时、重试、取消、健康检查。
- [ ] E2E 验收（需 9 哥）：
  - Telegram 文字/语音 → 正确回复；
  - 微信扫码 → 文字/语音 → 正确回复；
  - 上下文连续；`/new` 清空；
  - 非 allowlist 用户被拒；
  - 2000/4096 长消息切分；
  - 音箱离线时 channel 仍可用；
  - 可选“同时客厅说”；
  - 日志无 token/context_token。

## 4. 需要 9 哥 配合的点（其余我都能先做）

1. **Telegram**：创建一个**独立于 Hermes 的 bot**（BotFather），给我 token 和你的 chat_id；不要复用现有 Hermes bot，否则两个进程抢 `getUpdates`。
2. **微信 iLink/ClawBot**：用手机扫码绑定一个**新的 iLink bot 账号**给小瑞；Hermes/OpenClaw 正在用的账号不要共用（同一 bot 只能一个长轮询消费者）。
3. 决定默认回复：仅文字（推荐先这样）/ 文字+语音 / 仅语音。
4. 决定是否允许“channel 消息同时让客厅音箱播报”，默认关。
5. 真机测试时发 2–3 条文字 + 1 条语音，帮我确认链路。

## 5. 验收标准

- 文字：从发送到收到回复 p50 < 3 s（不含人工等待）。
- 语音输入：≤10 s 的语音消息在 5 s 内转写并回复（网络正常）。
- 上下文：同一 peer 连续 3 轮保持；`/new` 后清空。
- 安全：非 allowlist 静默拒绝；token/context_token 不出现在日志；危险动作需确认。
- 可靠性：gateway 重启后 offset/游标/context_token 恢复；Telegram/微信断线自动重连。
- 独立性：音箱离线/音频面失败不影响文字 channel。

## 6. 风险

- 微信 iLink 处于 Public Beta，协议可能变；adapter 必须隔离，不能侵入控制面。
- 微信语音编码多为 SILK，ffmpeg 不原生支持，需要额外解码；第一版可用 `voice_item.text` 绕过。
- 微信**语音气泡**发送没有稳定公开 helper；**音频文件附件**已验证可行（Hermes 当前实现），第一版用文件方式。
- 同一 bot 账号只能一个 `getupdates` 消费者：不要与 Hermes/OpenClaw 共用。
- `context_token` 是会话锚点；主动推送不可靠，agent 通知优先走 Telegram/控制台。
- Telegram/微信消息经平台云，非端到端加密；控制台需明示。
- 工具/危险动作在 channel 里必须确认；不要在 channel 里开放 shell。

## 7. 推荐顺序

1. M0 → M1 → M2（我先做，不需要你）。
2. M3 Telegram 文字 + 语音输入（需要独立 bot token）。
3. M4 微信 iLink 文字（需要扫码）；M5 语音/媒体按需。
4. M6 控制台、M7 部署/验收。
5. 最后再考虑 Hermes/Agent 接入；channel 是 I/O，Agent 是执行，两者不要混在一起。



## 8. 实现状态（2026-10-23）

- 控制面：`POST /api/v1/chat`（文本回合，可选 TTS）、`POST /api/v1/asr`（语音转写）、
  `GET/PUT /api/v1/channels`（通道配置，token 只回 `token_configured`）。
- 文本回合不依赖音频面；`audio=false` 时跳过 TTS，直接返回文字。
- channel gateway：`src/house_ear/channels/`，独立进程 `house-ear-channels`。
  - Telegram：长轮询、allowlist（默认拒绝未知 chat）、文字/语音输入、语音回复、typing、分片。
  - 微信 iLink：QR 登录、getupdates 游标、context_token、2000 字分片、typing、`.silk` 入站重转写、音频文件回复。
  - 未知 peer 默认拒绝；临时联调可加 `--telegram-allow-all` / `--weixin-allow-all`（不要对公网 bot 用）。
- Channels 控制台页：`/channels`，启用/allowlist/回复模式、微信绑定指令、gateway 启动命令；中英双语。
- 测试：`tests/test_channels.py`、`tests/test_chat_api.py`；全量 `743 passed, 1 skipped`。

### 运行

```bash
# 1) 控制面已启动（NAS/Mac），提供 token
export HOUSE_EAR_CONTROL_URL=http://127.0.0.1:8080
export HOUSE_EAR_CONSOLE_TOKEN=...
# 2) Telegram：独立 bot token；先填 allowlist，或临时 --telegram-allow-all 配对
export HOUSE_EAR_TELEGRAM_TOKEN=...
house-ear-channels --telegram-token "$HOUSE_EAR_TELEGRAM_TOKEN" \
  --telegram-allow "<your_chat_id>" --channels-config /data/channels.json
# 3) 微信：手机扫码（二维码 URL 会打印）
house-ear-channels --login-weixin --weixin-data /data/channels/weixin
house-ear-channels --weixin-data /data/channels/weixin \
  --weixin-allow "<your_user_id>" --channels-config /data/channels.json
```

### 仍需 9 哥 配合

1. Telegram：给 bot 发一条消息，提供/确认 chat_id（默认 allowlist 会拒绝未知会话）。
2. 微信：手机扫描 `--login-weixin` 打印的二维码，并允许小瑞使用一个**独立** iLink bot 账号。
3. 真机验收：两个通道各发 2 条文字 + 1 条语音。


## 9. 控制台 0-1 与网关管理（2026-10-23 补充）

- `/channels` 页现在提供：
  - 网关状态（运行/停止、PID/uptime/last_error）与 `启动 / 停止 / 重启` 按钮；
  - Telegram token 的 write-only 输入 + “保存并验证”（服务端 getMe，失败本地化提示）；
  - 微信“扫码绑定”按钮：控制面发起 QR 登录、每 2s 轮询、内联 SVG 二维码；确认后保存账号；
  - 保存配置/Token/绑定后不自动启动，显示“重启网关后生效”的 banner。
- 控制面新增：
  - `ChannelSupervisor`（子进程管理，非音频面）；
  - `POST /api/v1/channels/gateway/{start|stop|restart}`；
  - `PUT /api/v1/channels/telegram/token`（写 secrets，永不回显）；
  - `POST/GET /api/v1/channels/weixin/login`（QR 状态）。
- **注意**：控制台托管模式与 Mac 外部 `house-ear-channels` 只能二选一；同一 bot 不能同时长轮询。


## 10. M7：控制面托管的常驻 gateway（2026-10-23）

- `ChannelSupervisor` 在控制面进程里管理 `house-ear-channels` 子进程：
  - 启动时若 `channels.json` 有已启用通道，自动启动；
  - 5 秒 watchdog：进程异常退出且不是手动 Stop，会自动拉起；
  - 控制台 Start/Stop/Restart 直接操作这个 supervisor；
  - 日志写入 `data/channels-gateway.log`。
- 当前 NAS 部署策略：**只启用 Telegram**；微信保留 adapter/API/控制台绑定接口，但 `weixin.enabled=false`，不参与长轮询。
- 同一 bot/账号只能有一个长轮询消费者：切换托管模式前必须停掉外部 `house-ear-channels` 进程。


### 10.1 NAS rollout (2026-10-24)

- Control plane image now installs the channel code and auto-starts a managed gateway
  when `channels.json` has an enabled channel. The supervisor logs to
  `data/channels-gateway.log`, restarts a crashed child, and the console Start/Stop/Restart
  buttons operate that child.
- **NAS `.168` currently enables Telegram only** (`telegram.enabled=true`,
  `weixin.enabled=false`). WeChat adapter, APIs and QR UI remain available for later.
- NAS has no direct Telegram egress; the gateway child inherits `HTTPS_PROXY`/`HTTP_PROXY`
  pointing at a small LAN CONNECT proxy on the Mac (`192.168.5.27:7899`). If the Mac sleeps,
  Telegram stops — move the proxy to the NAS/router or give the NAS a VPN before relying
  on this permanently.
- Gateway status is exposed in `GET /api/v1/channels` and the Channels page.
