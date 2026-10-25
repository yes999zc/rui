# 首次运行 · First run

> 适用于 rui **0.4.x**。本页承接 [README](../README.md) 里省略的细节：设备准备、安装命令、
> 8 步向导、参数来源、连接排障和安装后命令。部署/升级/回滚的完整说明见
> [deploy.md](deploy.md)，日常运维见 [runbook.md](runbook.md)。
>
> [中文](#中文) · [English](#english) · [返回 README](../README.md)

---

## 中文

### 设备准备清单

Rui 的起点是「音箱已经运行 open-xiaoai client，并能回连 NAS」。刷机、root/SSH、装 client
不属于 Rui：按 [open-xiaoai](https://github.com/coderzc/open-xiaoai) 完成。v1 只支持
**Xiaomi 小爱音箱 Pro（LX06）**，音箱与 NAS 在同一 LAN/L2，client 回连 NAS 的 `4412`。

| # | 记什么 | 后面用在哪 |
|---|---|---|
| 1 | 音箱型号/固件版本，open-xiaoai client 的版本或 commit | `prepare` 兼容性确认、排障、回滚 |
| 2 | 音箱局域网 IP + 路由器里的 **DHCP 固定租约** | `params` 的“音箱地址”；IP 变了回连和 SSH 全断 |
| 3 | SSH/root 登录方式：`ssh root@<speaker-ip>`、密码/密钥、刷机工具版本 | `prepare`、看 client 日志、回滚 |
| 4 | client 安装路径（常见 `/data/open-xiaoai/`） | 重启 client、找日志、卸载/回滚 |
| 5 | client 保存回连地址/token 的位置：`server.txt`、`config.json`，或启动脚本里的 `WS_URL` / `SERVER_URL` | `connect` 填 ws 地址 + token |
| 6 | 重启 client 的命令 + 日志路径/查看命令（如 `tail -f <log>`） | `connect` / `test` 失败时先看这里 |
| 7 | 回滚方法：停止/移除 client、恢复原启动脚本；原厂固件/分区不要动 | `prepare` 确认项、出问题时恢复 |
| 8 | Rui 的 device token 要填进 client 的哪个字段（完整 ws URL 或单独 token） | `connect`；服务端与 client 必须一致 |

> ws 地址和 device token 等于音箱控制权。不要发到公开群/截图/issue；分享日志前先脱敏。

### 安装命令

主机前置条件：

- Docker Engine + `docker compose` 插件；安装用户能访问 Docker daemon（root，或已加入
  `docker` 组并重新登录；`docker info` 必须能通）。
- 可写安装目录：默认 `~/rui`（`RUI_HOME` 可改）。容器以 uid/gid **1000** 运行；1000 已被
  占用时用 `RUI_UID`/`RUI_GID` 覆盖。
- 出网：`raw.githubusercontent.com`、`ghcr.io`、`hf-mirror.com`。
- amd64 或 arm64；建议 4 核 / 4 GB 内存 / 2 GB 可用磁盘（另留约 250 MB 给模型）。

滚动版本（`main` + `:latest`，先试最省事）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

生产固定版本（脚本、compose 和镜像都锁到同一个 tag；当前发布为 **0.4.2**）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/deploy/install.sh \
  | RUI_REF=v0.4.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2 sh
```

- `RUI_REF` 决定 `docker-compose.yml` / `config.example.yaml` 来自哪个 ref，`RUI_IMAGE`
  决定运行哪个镜像；生产建议两个一起固定。
- `raw.githubusercontent.com` 卡住时用 `RUI_RAW` 指向镜像前缀，例如
  `RUI_RAW=https://ghproxy.net/https://raw.githubusercontent.com/yes999zc/rui/main`。
- `ghcr.io` 拉不动时，脚本会按 `RUI_REF` 从公开源码克隆并构建同一 tag（需要 `git`）。
- 装到 `/vol1/rui` 时，在管道后加 `RUI_HOME=/vol1/rui`。

安装脚本做五件事：

| 步骤 | 内容 | 落在哪 |
|---|---|---|
| 1 | 拉取 `docker-compose.yml`、`config.example.yaml`（参考件）和 `backup.sh` / `restore.sh` / `acceptance-v1.py` | `~/rui/`、`~/rui/scripts/` |
| 2 | `rui setup` 写入生产配置模板，生成默认唤醒词和**控制台 token** | `~/rui/config/config.yaml`、`config/keywords.txt` |
| 3 | 生成 `.env`，含随机 device token（0600） | `~/rui/.env` |
| 4 | 准备本地模型：SenseVoice ASR（约 239 MB，一次）；VAD/KWS 从镜像 `/opt/seed-models` 复制 | `~/rui/data/models/` |
| 5 | `docker compose up -d`，等待 `/health` 通过；健康后把控制台 token 存到 `console-token.txt`（0600，已存在不覆盖） | 容器 `rui`、`~/rui/console-token.txt` |

期望输出（路径/地址随环境不同）：

```text
[1/5] compose file
[2/5] config + wake words
console token: <console-token>          ← rui setup 的输出来自 [2/5]；[5/5] 会另存
[3/5] env
      wrote .env with a generated device token (48 hex chars)
[4/5] local models: ASR + VAD + wake word
      missing: asr-model; running fetch-models (image seed first)
[5/5] starting
      waiting for the service to answer /health
      healthy
      console token saved: ~/rui/console-token.txt (mode 600)
console: token saved to ~/rui/console-token.txt (mode 600) - the 401 dialog points here
done. open http://<nas-lan-ip>:8080/
```

镜像已在本机、只需下载 ASR 时约 **85 秒**到 `healthy`；首次含镜像拉取和 239 MB 模型通常
**4–8 分钟**。安装脚本在健康检查通过后把控制台 token 写到 `~/rui/console-token.txt`（0600，
重跑不覆盖）；文件缺失或已轮换时用下面的命令找回/轮换：

```bash
cd ~/rui
docker compose exec -T rui rui token
docker compose exec -T rui rui token --rotate
```

### 八步向导

安装完成后打开 `http://<nas-lan-ip>:8080/`，输入 console token。**必须用 NAS 的局域网 IP**：
用 `localhost` 或 SSH 隧道打开会让 `connect` 生成 `ws://127.0.0.1:4412`，音箱永远连不上。
控制台进入 `/setup`，8 步都在网页里完成，**不需要手改 YAML**；已完成步骤可重跑。

| # | 步骤 | 点之前准备/决定 | 这一步做什么 | 完成标志 |
|---|---|---|---|---|
| 1 | `keys` | 决定 LLM / ASR / TTS 各用哪个 provider；可在预设/自定义里新建并先探测；准备凭据 | 写本地密钥文件，把该用途的 provider 设为选中项并保留旧 fallback，保存 TTS 音色 | 每个用途都有可用 provider；控制台只显示“已配置” |
| 2 | `models` | 通常不用准备：安装脚本已下好 | 只检查 ASR / VAD / KWS 文件，不代下 | ASR、VAD、KWS 三项都打勾 |
| 3 | `prepare` | 拿出上面的清单：client 已装、IP 已固定、SSH/重启/日志/回滚都记下 | 展示 open-xiaoai 链接和机型检查清单；不执行任何设备命令 | 设备侧项目勾完并确认；token 留到 `connect` |
| 4 | `params` | 音箱 LAN IP、房间名、唤醒词、音量/LED/端口/增益策略 | 写入 `devices[]`，重启音频面 | 保存成功且没有 `plane_error`；client 随后重连 |
| 5 | `wake` | 1–3 个唤醒词（每个 ≤12 字） | 重写 `config/keywords.txt`，重启音频面 | 文件里每个词都有对应的 `@词` 行 |
| 6 | `connect` | 打开上面的 client 配置文件；控制台用 LAN IP 打开 | 生成只读的 ws 地址 + device token；填进 client 并重启 | 状态“已连接”，`speaker_connected=true` |
| 7 | `test` | 无 | 只读探测音频面、主机/SSH、token 配置 | 显示“通过”，且依据是音箱真的回连（SSH 通不算） |
| 8 | `verify` | 一句想测试的话 | 跑一次真实 turn，记录 `audio_bytes` | 听到回答，或点“我听到了回答”；可跳过但会标记未验证 |

`keys` 的 provider 表（每个用途只选一个；未选中的 fallback 不需要配）：

| 用途 | 向导里选 | 需要准备 | 说明 |
|---|---|---|---|
| LLM | DeepSeek（模板默认） | `DEEPSEEK_API_KEY` | 任意 OpenAI 兼容 provider；11 个预设 + 自定义，LLM 必须有一个可用项 |
| ASR | 本地 SenseVoice（默认） | 无 key | 免密钥，模型由安装脚本下好；推荐 |
| ASR | 火山豆包流式识别 2.0 | `VOLC_API_KEY` | 新版控制台只要 API Key，不需要 AppID；资源 ID 选 `volc.seedasr.sauc.duration` 或 `...concurrent` |
| ASR | 自定义 OpenAI 兼容 | `RUI_*` 环境名或无需 key | 在供应商页用预设/自定义新建并探测 |
| TTS | 讯飞（模板默认） | `XFYUN_APPID` + `XFYUN_API_KEY` + `XFYUN_API_SECRET` | 三项缺一不可 |
| TTS | 火山豆包语音合成 2.0 | `VOLC_API_KEY` + 音色 | 新版控制台只要 API Key；不要填 AK/SK |
| TTS | 自定义 OpenAI 兼容 | `RUI_*` 环境名或无需 key | 走 `/audio/speech` 的兼容端点 |

- **每个用途都必须有一个可用 provider**，但 ASR 可选本地模型，所以最少只需要 LLM + TTS
  两组凭据；TTS 和 ASR 不需要同时用云端。
- “测试”会用当前输入发起一次真实小请求（未保存的值只用于本次探测，不落盘）；保存后任何
  页面都读不回原文。
- 火山 API Key 在火山控制台创建，和 AK/SK 不是一回事；讯飞三项在讯飞控制台同一条应用里取。
  详见 [volc-credentials.md](volc-credentials.md)。
- 新建/探测 provider 的预设清单、字段和环境名规则见
  [provider-presets.md](provider-presets.md)。

### 音箱参数从哪来

| 字段 | 填什么 / 从哪来 |
|---|---|
| 音箱地址 | 上面的 LAN IP，只填地址，不要带 `http://`，不能有空格 |
| 房间 | 自定义名字（如“客厅”），用于控制台和多设备区分 |
| 唤醒词 | 1–3 个，每个最多 12 字；默认“你好小瑞” |
| 音量 | 0–100，默认 60；**0 = 不接管音量**，沿用原厂 |
| 灯效 | 0.4.0 新建 LX06 默认打开；不想要就关掉 |
| 监听端口 | 默认 4412；只有端口冲突才改，client 的 ws URL 要同步改 |
| 麦克风增益 | 0–42 dB，可留空；越大越灵敏，也更容易收环境噪声 |

保存 `params` / `wake` 会重启音频面，client 会短暂断线后自动重连。`wake` 保存时会同时更新
`devices[].wake_words` 和 `config/keywords.txt`，**两者必须一致**；`keywords.txt` 是服务端
KWS 的唯一来源。手动改过 `devices[].wake_words` 后要重跑 `keywords` 命令重新生成文件。

### 连接接线与排障

`connect` 的接线步骤：

1. 在控制台点“显示/复制”拿到完整 `ws://<nas-lan-ip>:4412/?token=<device-token>`。
2. 把它写进上面记下的 client 配置（`server.txt` / `config.json` / 启动脚本的 `WS_URL`）。
3. 重启 client，确认日志出现 `connected` / `已连接`。
4. 回到向导点“等待连接”（每 3 秒查一次，最多 1 分钟）或“刷新连接状态”，直到状态变“已连接”。

| 现象 | 先查什么 | 处理 |
|---|---|---|
| client 日志 401 / close 1008，NAS 日志 `xiaoai: rejected ... (bad or missing token)` | 两边 token 是否同一个 | 在 `connect` 重新复制当前 token，更新 client，重启 client；不是控制台 token |
| `port_blocked` / 连接被拒绝 | `4412` 是否在监听 | `ss -ltnp \| grep 4412`；确认音频面在跑、`listen_port` 一致、容器用 host network、防火墙放行 |
| 一直 `waiting_for_client` | client 是否在跑、地址是否指向 NAS LAN IP | 看 client 日志；确认同一 LAN、DHCP 固定租约、地址不是 `127.0.0.1`；重启 client 再点“等待连接” |
| 连上又频繁断 | 是否只有一个 client 占用 4412 | `ss -tnp \| grep 4412` 只应有一个 ESTAB；停掉多余的调试 client |

`test` 的“运行设备测试”只读、不写配置。它检查音频面（`speaker_connected`）、主机/SSH
（诊断用）、device token 是否配置。**inbound 音箱只有真的回连才算通过**；SSH 探测通过不代表
接线成功。

`verify` 的“说一句”走真实全链路：ASR → LLM → TTS → 播放，`audio_bytes>0` 表示有音频产出，
但最终要人耳确认音箱真的出声；也可以点“我听到了回答”。跳过验证不会阻止整体完成
（`verify` 是可选步），但控制台会标记“未验证”。

### 安装后命令速查

以下命令默认在 `~/rui` 下执行；装在 `/vol1/rui` 就把路径替换掉。

| 位置 | 内容 | 备注 |
|---|---|---|
| `~/rui/config/config.yaml` | `devices[]`、provider 链、`web.auth_token` | 0600；改动前先备份 |
| `~/rui/data/secrets.yaml` | 向导里保存的密钥 | 0600，只写不读；环境变量优先 |
| `~/rui/.env` | `RUI_DEVICE_TOKEN`、`RUI_IMAGE`、`RUI_UID`/`RUI_GID`、可选 key | 0600；`docker compose` 注入 |
| `~/rui/config/keywords.txt` | 唤醒词/KWS 参数 | 改唤醒词后重新生成 |
| `~/rui/data/models/` | SenseVoice、VAD、KWS | 删了可重新 `fetch-models` |
| `~/rui/data/setup.json` | 向导确认/时间戳 | 0600；不存密钥 |
| `~/rui/console-token.txt` | 安装时保存的控制台 token | 0600；重跑不覆盖；轮换后刷新 |

- **换密钥**：控制台「凭据（只写）」页（`/panel/credentials`，书签 `/secrets` 仍可用）或
  消费页内联填写，也可重跑 `keys` 步；密钥只写不读，不要写进 `config.yaml`。
- **换 device token**：在 `connect` 步重新显示/复制；或改 `.env` 的 `RUI_DEVICE_TOKEN` 后
  `docker compose up -d`，再把新值写进 client 并重启，最后回 `test` 确认。
- **换控制台 token**：`docker compose exec -T rui rui token --rotate`，再
  `docker compose restart rui` 让服务加载新值；重启后旧 token 失效，并刷新 `console-token.txt`。
- **升级**：在 `.env` 固定 `RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2`；
  `docker compose pull && docker compose up -d`，等 healthy 后跑
  `docker compose exec -T rui rui doctor`。
- **升级到非 root 镜像（已有安装）**：先 `scripts/backup.sh --out ~/rui-backups`，再
  `sudo chown -R 1000:1000 config data`（只改属主，不改内容），然后
  `docker compose pull && docker compose up -d --force-recreate`，最后
  `docker compose exec -T rui rui-healthcheck`。重跑 `deploy/install.sh` 会自动做这一步。
- **回滚**：把 `.env` 的 `RUI_IMAGE` 改回上一个 tag，先备份 `config/config.yaml`（旧版本
  可能不认新字段），再 `docker compose up -d`。回滚到 root 镜像时把 `.env` 的
  `RUI_UID/RUI_GID` 改成 `0/0`；数据卷不必 chown 回 root。
- **备份/恢复**：`cd ~/rui && scripts/backup.sh --out ~/rui-backups`；恢复见
  [runbook.md](runbook.md)。
- **配置恢复**：启动失败时服务会回退到 last-good 并在控制台显示 banner；也可运行
  `rui config restore --last-good` 或 `rui config restore --defaults`。
- **日常验收**：`curl -fsS http://localhost:8080/health`、
  `docker compose exec -T rui rui doctor`、`docker compose logs -f --tail=200 rui`。

### 常见安装故障

| 现象 | 原因/处理 |
|---|---|
| client 日志 401 / close 1008；NAS 日志 `bad or missing token` | client 还带着旧 token。到 `connect` 复制当前 ws URL/token，更新 client 配置并重启；确认 `.env` 的 `RUI_DEVICE_TOKEN` 没被改过 |
| 重跑安装时报容器 `rui` 已存在 | 同一个 `RUI_HOME` 重跑是安全的；若属于另一个目录，按提示去那个目录 `docker compose down`，或先停旧安装 |
| `docker pull ghcr.io/...` TLS handshake timeout / 403 | 给 Docker daemon 配代理或 registry mirror；或先在能出网的机器 `pull` + `save`，到 NAS `load`。脚本拉取失败会自动从源码构建（需要 `git`） |
| 灯环不亮 | 0.4.0 新建 LX06 默认 `led.enabled: true`；到 `params` 的“灯效”或设备配置确认。老配置升级上来可能仍是关闭状态 |
| 控制台 401 / 找不到 console token | 0.4.1+ 的安装脚本会存到 `~/rui/console-token.txt`（0600）；先 `cat ~/rui/console-token.txt`，文件不在或过期用 `cd ~/rui && docker compose exec -T rui rui token` 找回。它和 device token 不是同一个 |
| `install.sh` 最后报 120 s 不健康 | 容器起了但 `/health` 未通过；常见是 token/端口/模型或配置问题。按脚本提示跑 `docker compose exec -T rui rui-healthcheck` 和 `docker compose logs --tail=100 rui` |
| 升级后容器反复重启 / `Permission denied` | 非 root 镜像（uid 1000）读不了 root 所有的 bind mount。备份后 `sudo chown -R 1000:1000 config data`，再 `docker compose up -d --force-recreate` |

---

## English

### Device checklist

Rui starts from "the speaker already runs the open-xiaoai client and can dial back to the
NAS". **Flashing, gaining root/SSH and installing the client are not part of Rui**: follow
the [open-xiaoai](https://github.com/coderzc/open-xiaoai) project for that. v1 supports
only the **Xiaomi AI Speaker Pro (LX06)**, on the same LAN/L2 as the NAS, dialing back on
port `4412`.

| # | Record | Used later for |
|---|---|---|
| 1 | Speaker model/firmware, plus the open-xiaoai client version or commit | `prepare` compatibility check, troubleshooting, rollback |
| 2 | Speaker LAN IP and a **DHCP reservation** in the router | `params` "speaker address"; if the IP changes, SSH and the dial-in URL break |
| 3 | SSH/root login: `ssh root@<speaker-ip>`, password or key, flashing tool/version | `prepare`, reading client logs, rollback |
| 4 | Client install path (usually `/data/open-xiaoai/`) | Restarting the client, finding logs, uninstall/rollback |
| 5 | Where the client keeps the dial-in URL/token: `server.txt`, `config.json`, or `WS_URL` / `SERVER_URL` in a startup script | `connect`: ws URL + token |
| 6 | The command that restarts the client, and the log path/command (e.g. `tail -f <log>`) | First thing to check when `connect` / `test` fails |
| 7 | Rollback: stop/remove the client, restore the stock startup script; leave stock firmware/partitions alone | `prepare` confirmation, recovery |
| 8 | Which field in the client receives Rui's device token (full ws URL or a separate token value) | `connect`; both sides must use the same value |

> The ws URL and device token are equivalent to speaker control. Do not post them in
> public chats, screenshots or issues; redact logs before sharing.

### Install commands

Host prerequisites:

- Docker Engine + the `docker compose` plugin. The install user must be able to access the
  Docker daemon (root, or in the `docker` group and logged in again; `docker info` must work).
- A writable install directory: `~/rui` by default (`RUI_HOME` overrides it). The container
  runs as uid/gid **1000**; override with `RUI_UID`/`RUI_GID` if 1000 is taken.
- Outbound network: `raw.githubusercontent.com`, `ghcr.io`, `hf-mirror.com`.
- amd64 or arm64; 4 cores / 4 GB RAM / 2 GB free disk recommended (plus ~250 MB for models).

Rolling release (`main` + `:latest`, easiest first try):

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

For production, pin a release (script, compose files and image all come from the same tag;
the current release is **0.4.2**):

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/deploy/install.sh \
  | RUI_REF=v0.4.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2 sh
```

- `RUI_REF` selects the ref for `docker-compose.yml` / `config.example.yaml`; `RUI_IMAGE`
  selects the image. Pin both for production.
- If `raw.githubusercontent.com` stalls, point `RUI_RAW` at a mirror prefix, e.g.
  `RUI_RAW=https://ghproxy.net/https://raw.githubusercontent.com/yes999zc/rui/main`.
- If `ghcr.io` cannot be pulled, the installer builds the same tag from public source at
  `RUI_REF` (requires `git`).
- To install under `/vol1/rui`, add `RUI_HOME=/vol1/rui` after the pipe.

What the installer does:

| Step | What | Where |
|---|---|---|
| 1 | Fetches `docker-compose.yml`, `config.example.yaml` (reference) and `backup.sh` / `restore.sh` / `acceptance-v1.py` | `~/rui/`, `~/rui/scripts/` |
| 2 | `rui setup` writes the production config, default wake words and the **console token** | `~/rui/config/config.yaml`, `config/keywords.txt` |
| 3 | Generates `.env` with a random device token (0600) | `~/rui/.env` |
| 4 | Prepares models: SenseVoice ASR (~239 MB, once); VAD/KWS copied from the image's `/opt/seed-models` | `~/rui/data/models/` |
| 5 | `docker compose up -d`, waits for `/health`, then saves the console token to `console-token.txt` (0600, existing file kept) | container `rui`, `~/rui/console-token.txt` |

Expected output (paths/addresses vary):

```text
[1/5] compose file
[2/5] config + wake words
console token: <console-token>          ← printed by rui setup at [2/5]; [5/5] saves it
[3/5] env
      wrote .env with a generated device token (48 hex chars)
[4/5] local models: ASR + VAD + wake word
      missing: asr-model; running fetch-models (image seed first)
[5/5] starting
      waiting for the service to answer /health
      healthy
      console token saved: ~/rui/console-token.txt (mode 600)
console: token saved to ~/rui/console-token.txt (mode 600) - the 401 dialog points here
done. open http://<nas-lan-ip>:8080/
```

With the image already local and only ASR to download, the tested path reaches `healthy` in
about **85 seconds**; the first run with an image pull and the 239 MB model usually takes
**4–8 minutes**. After health passes, the installer writes the console token to
`~/rui/console-token.txt` (0600; reruns keep the file); recover or rotate it with:

```bash
cd ~/rui
docker compose exec -T rui rui token
docker compose exec -T rui rui token --rotate
```

### The 8-step wizard

After install, open `http://<nas-lan-ip>:8080/` and enter the console token. **Use the NAS
LAN IP**: opening it through `localhost` or an SSH tunnel makes `connect` generate
`ws://127.0.0.1:4412`, which the speaker can never reach. The console opens `/setup`; all
eight steps happen in the browser — **no YAML editing** — and completed steps can be rerun.

| # | Step | Prepare/decide before clicking | What the step does | Done looks like |
|---|---|---|---|---|
| 1 | `keys` | Pick LLM/ASR/TTS providers; presets or custom; credentials ready | Writes secrets, sets primaries and keeps fallbacks, saves the TTS voice | Every use has a usable provider |
| 2 | `models` | Normally nothing: the installer already fetched them | Only checks ASR / VAD / KWS files; never downloads | All three (ASR, VAD, KWS) are green |
| 3 | `prepare` | Client installed, IP reserved, SSH/restart/logs/rollback recorded | Shows the open-xiaoai link and device checklist; runs nothing on the speaker | Device-side items confirmed |
| 4 | `params` | Speaker LAN IP, room name, wake words, volume/LED/port/gain choices | Writes `devices[]` and restarts the audio plane | Saved with no `plane_error`; the client reconnects afterwards |
| 5 | `wake` | 1–3 wake words, up to 12 characters each | Rewrites `config/keywords.txt` and restarts the audio plane | The file contains an `@word` line for every word |
| 6 | `connect` | Open the client config from above; open the console via the LAN IP | Generates a read-only ws URL + device token; you paste it into the client and restart it | Status "connected", `speaker_connected=true` |
| 7 | `test` | Nothing | Read-only probe of the audio plane, host/SSH and token configuration | "Passed", based on a real speaker dial-in (SSH alone is not enough) |
| 8 | `verify` | One sentence to test | Runs a real turn and records `audio_bytes` | You hear the answer, or click "I heard the reply"; skipping marks it unverified |

Provider table for `keys` (one provider per use; unselected fallbacks need no credentials):

| Use | Choose in the wizard | Prepare | Notes |
|---|---|---|---|
| LLM | DeepSeek (template default) | `DEEPSEEK_API_KEY` | Any OpenAI-compatible provider; 11 presets + custom; one LLM is required |
| ASR | Local SenseVoice (default) | No key | Keyless; the installer fetched the model; recommended |
| ASR | Volc Doubao streaming ASR 2.0 | `VOLC_API_KEY` | New console needs only an API key (no AppID); resource id `volc.seedasr.sauc.duration` or `...concurrent` |
| ASR | Custom OpenAI-compatible | `RUI_*` env name or keyless | Create and probe it on the providers page |
| TTS | XFYUN (template default) | `XFYUN_APPID` + `XFYUN_API_KEY` + `XFYUN_API_SECRET` | All three are required |
| TTS | Volc Doubao TTS 2.0 | `VOLC_API_KEY` + voice | New console needs only an API key; do not put AK/SK here |
| TTS | Custom OpenAI-compatible | `RUI_*` env name or keyless | OpenAI-compatible `/audio/speech` endpoint |

- **Every use needs one usable provider**, but ASR can be local, so the minimum is LLM + TTS
  credentials; TTS and ASR do not both have to be cloud.
- "Test" makes one real, small request with the current input (unsaved values are used for
  that probe only and are not persisted); after saving, values can never be read back.
- Volc API keys are created in the Volc console and are not AK/SK; the iFlytek trio lives in
  one iFlytek app. See [volc-credentials.md](volc-credentials.md).
- Preset ids, fields and environment-name rules are in
  [provider-presets.md](provider-presets.md).

### Where each param comes from

| Field | Value / source |
|---|---|
| Speaker address | The LAN IP from above; address only, no `http://`, no spaces |
| Room | Any name (e.g. "Living room"), used by the console and multi-device views |
| Wake words | 1–3 words, up to 12 characters each; default "你好小瑞" |
| Volume | 0–100, default 60; **0 = do not take over volume**, keep the stock level |
| LED | New LX06 devices default to on in 0.4.0; switch it off if you do not want the ring |
| Listen port | Default 4412; change only on a port conflict and update the client ws URL too |
| Mic gain | 0–42 dB, optional; higher is more sensitive and picks up more room noise |

Saving `params` / `wake` restarts the audio plane; the client disconnects briefly and
reconnects. The `wake` step updates both `devices[].wake_words` and
`config/keywords.txt`; **the two must match**. `keywords.txt` is the single source for
server-side KWS. After manually editing `devices[].wake_words`, regenerate it with the
`keywords` command.

### Connect troubleshooting

`connect` hookup:

1. In the console, click "show/copy" to get the full
   `ws://<nas-lan-ip>:4412/?token=<device-token>`.
2. Put it into the client config recorded above (`server.txt` / `config.json` / `WS_URL` in
   the startup script).
3. Restart the client and confirm its log says `connected`.
4. Back in the wizard, click "Wait for connection" (polls every 3 s for up to 1 minute) or
   "Refresh status" until it says connected.

| Symptom | Check first | Fix |
|---|---|---|
| Client logs 401 / close 1008; NAS logs `xiaoai: rejected ... (bad or missing token)` | Whether both sides use the same token | Copy the current token from `connect`, update the client, restart it; this is not the console token |
| `port_blocked` / connection refused | Whether `4412` is listening | `ss -ltnp \| grep 4412`; confirm the audio plane runs, `listen_port` matches, the container uses host networking, and the firewall allows it |
| Stuck on `waiting_for_client` | Whether the client runs and points at the NAS LAN IP | Read the client log; same LAN, DHCP reservation, not `127.0.0.1`; restart the client and wait again |
| Connects then keeps dropping | More than one client on 4412 | `ss -tnp \| grep 4412` should show one ESTAB; stop extra debug clients |

`test` ("Run device test") is read-only and writes no config. It checks the audio plane
(`speaker_connected`), host/SSH (diagnostics only) and whether a device token is configured.
**An inbound speaker passes only when it is actually connected**; a successful SSH probe is
not a working hookup.

`verify` ("Say a sentence") runs the real chain: ASR → LLM → TTS → playback. `audio_bytes>0`
proves audio was produced, but a human still has to hear the speaker; you can also click
"I heard the reply". Skipping does not block completion (`verify` is optional), but the
console marks the setup unverified.

### After-install command reference

The commands below assume `~/rui`; replace the path if you installed under `/vol1/rui`.

| Location | Contents | Notes |
|---|---|---|
| `~/rui/config/config.yaml` | `devices[]`, provider chains, `web.auth_token` | 0600; back it up before edits |
| `~/rui/data/secrets.yaml` | Keys saved by the wizard | 0600, write-only; environment variables win |
| `~/rui/.env` | `RUI_DEVICE_TOKEN`, `RUI_IMAGE`, `RUI_UID`/`RUI_GID`, optional keys | 0600; injected by `docker compose` |
| `~/rui/config/keywords.txt` | Wake words / KWS parameters | Regenerate after changing wake words |
| `~/rui/data/models/` | SenseVoice, VAD, KWS | Re-fetch with `fetch-models` if deleted |
| `~/rui/data/setup.json` | Wizard confirmations/timestamps | 0600; contains no secrets |
| `~/rui/console-token.txt` | Console token saved at install | 0600; reruns keep it; refresh after rotation |

- **Change keys**: use the console Credentials (write-only) page (`/panel/credentials`; the
  bookmarked `/secrets` still works) or the inline slot on the consuming page, or re-run
  `keys`; keys are write-only and must not go into `config.yaml`.
- **Change the device token**: show/copy it again in `connect`, or edit `RUI_DEVICE_TOKEN` in
  `.env` and run `docker compose up -d`; then update the client, restart it, and re-run
  `test`.
- **Rotate the console token**: `docker compose exec -T rui rui token --rotate`, then
  `docker compose restart rui` to load it; the old token stops working after the restart, so
  refresh `console-token.txt` too.
- **Upgrade**: pin `RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2` in `.env`;
  `docker compose pull && docker compose up -d`, wait for healthy, then run
  `docker compose exec -T rui rui doctor`.
- **Upgrade to the non-root image (existing installs)**: back up first
  (`scripts/backup.sh --out ~/rui-backups`), then `sudo chown -R 1000:1000 config data`
  (owner only; contents are untouched), then
  `docker compose pull && docker compose up -d --force-recreate`, and finally
  `docker compose exec -T rui rui-healthcheck`. Re-running `deploy/install.sh` does the
  chown automatically.
- **Roll back**: set `RUI_IMAGE` back to the previous tag, back up `config/config.yaml`
  first (old code may reject new fields), then `docker compose up -d`. When rolling back to
  a root image, set `RUI_UID`/`RUI_GID` to `0/0` in `.env`; the volumes do not need to be
  chowned back.
- **Backup/restore**: `cd ~/rui && scripts/backup.sh --out ~/rui-backups`; restore steps
  are in [runbook.md](runbook.md).
- **Config recovery**: on a failed start the service falls back to last-good and shows a
  banner in the console; you can also run `rui config restore --last-good` or
  `rui config restore --defaults`.
- **Daily checks**: `curl -fsS http://localhost:8080/health`,
  `docker compose exec -T rui rui doctor`,
  `docker compose logs -f --tail=200 rui`.

### Common install failures

| Symptom | Cause / fix |
|---|---|
| Client logs 401 / close 1008; NAS logs `bad or missing token` | The client still has an old token. Copy the current ws URL/token from `connect`, update the client config, restart it, and confirm `.env`'s `RUI_DEVICE_TOKEN` was not changed |
| Re-running the installer says container `rui` already exists | Re-running in the same `RUI_HOME` is safe; if it belongs to another directory, go there and run `docker compose down`, or stop the old install first |
| `docker pull ghcr.io/...` TLS handshake timeout / 403 | Configure a proxy or registry mirror for the Docker daemon, or `pull` + `save` on a connected machine and `load` on the NAS. The installer falls back to a source build (requires `git`) |
| LED ring stays off | New LX06 devices default to `led.enabled: true` in 0.4.0; check the `params` LED toggle or the device config. An upgraded old config may still have it off |
| Console 401 / cannot find the console token | 0.4.1+ writes it to `~/rui/console-token.txt` (0600); try `cat ~/rui/console-token.txt`, and if missing or stale use `cd ~/rui && docker compose exec -T rui rui token`. It is not the device token |
| `install.sh` ends with "not healthy after 120 s" | The container started but `/health` failed; usually token/port/model/config. Run `docker compose exec -T rui rui-healthcheck` and `docker compose logs --tail=100 rui` as prompted |
| Container restart loop / `Permission denied` after upgrade | The non-root image (uid 1000) cannot read root-owned bind mounts. Back up, `sudo chown -R 1000:1000 config data`, then `docker compose up -d --force-recreate` |
