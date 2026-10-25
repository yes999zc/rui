# Deploy

**生产目标是 NAS 上的 Docker：一条命令冷装。** Mac 只是开发机（`:8091` 的开发服务和设备
指向 Mac 的调试链路都在 `docs/HANDOFF.md`）。生产形态是 NAS 上 `deploy/install.sh` 拉镜像、
写配置、起服务；`--network host`，默认 `:8080`，配置在 `~/rui/config`。

运维手册（起停、日志、换配置、换镜像、回滚、常见故障）在
[`docs/runbook.md`](runbook.md)。

## 1. 开始前：音箱端与要记录的信息

RUI 的安装起点是「音箱已经能运行 open-xiaoai client，并能回连 NAS」。**刷机、拿 root/SSH、安装 client 都不属于 RUI**：按 [open-xiaoai](https://github.com/coderzc/open-xiaoai) 项目完成；RUI 不刷机、不托管固件，也不会在音箱上执行命令。v1 只支持 **Xiaomi 小爱音箱 Pro（LX06）**，音箱与 NAS 在同一 LAN/L2，client 回连 NAS 的 `4412`。

### 1.1 刷机/装 client 时记录什么

别等 SSH 进不去再猜。下面这张表就是向导 `prepare` / `connect` 要用的全部设备侧信息：

| # | 记什么 | 后面用在哪 |
|---|---|---|
| 1 | 音箱型号/固件版本，open-xiaoai client 的版本或 commit | `prepare` 兼容性确认、排障、回滚 |
| 2 | 音箱局域网 IP + 路由器 **DHCP 固定租约** | `params` 的“音箱地址”；IP 变了 SSH 和回连全断 |
| 3 | SSH/root 登录方式：`ssh root@<speaker-ip>`、密码/密钥、刷机工具版本 | `prepare`、看 client 日志、回滚 |
| 4 | client 安装路径（常见 `/data/open-xiaoai/`） | 重启 client、找日志、卸载/回滚 |
| 5 | client 保存回连地址/token 的位置：`server.txt`、`config.json`，或启动脚本里的 `WS_URL` / `SERVER_URL` | `connect` 填 ws 地址 + token |
| 6 | 重启 client 的命令 + 日志路径/查看命令（如 `tail -f <log>`） | `connect` / `test` 失败时先看这里 |
| 7 | 回滚方法：停止/移除 client、恢复原启动脚本；原厂固件/分区不要动 | `prepare` 的确认项、出问题时恢复 |
| 8 | RUI 的 device token 要填进 client 的哪个字段（完整 ws URL 或单独 token 项） | `connect`；值由 RUI 生成，两边必须一致 |

> ws 地址和 device token 等于音箱控制权。不要贴到公开群/截图/issue；分享日志前先脱敏。

### 1.2 主机前置条件

- x86_64 或 arm64，4 核 / 4 GB 内存 / 2 GB 可用磁盘（另留约 250 MB：镜像自带
  VAD/KWS，只需下 239 MB 的 SenseVoice ASR）
- Docker Engine + `docker compose` 插件，允许 `--network host`（设备音频是长连接 socket）
- **Docker 权限**：运行安装脚本的用户要能访问 Docker daemon（`/var/run/docker.sock`）——root，或已加入 `docker` 组
  并重新登录（`sudo usermod -aG docker "$USER"`）。`docker` 不存在或 `docker info` 失败时，
  `install.sh` 会在做任何事之前退出并给出提示。
- **可写安装目录**：默认 `~/rui`（`RUI_HOME` 可改）。NAS 上 `/vol1/rui` 这类挂载点常是
  root-only；用 root 运行安装，或先建好目录并 `chown` 给安装用户，否则脚本会在建目录时失败。
  容器以 **uid/gid 1000** 运行，`install.sh` 会把 `config/`、`data/` chown 到这个身份；
  1000 已被占用时用 `RUI_UID`/`RUI_GID` 覆盖（见 [security.md](security.md)）。
- **出网**：`raw.githubusercontent.com`、`ghcr.io`、`hf-mirror.com`。镜像拉取走 Docker daemon
  的代理；模型下载可选宿主机代理（安装脚本把 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` /
  `NO_PROXY` 传给 `fetch-models`）。`raw` 不稳定时用 `RUI_RAW` 镜像前缀；GHCR 不通时脚本会
  尝试按 `RUI_REF` 从源码构建（需要 `git`）。
- **镜像架构**：GitHub Actions 发的是 `linux/amd64` + `linux/arm64` 多架构镜像（见
  `.github/workflows/docker-publish.yml`）。在 Mac（arm64）本地 `docker build` 出来的
  只有 arm64，**不能直接搬到 x86_64 NAS**：要么用 GHCR 镜像，要么在 NAS 上带
  `--platform linux/amd64` 构建。

## 2. 一条命令安装

### 2.1 命令

滚动版本（`main` + `:latest`，适合先试）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

固定发布版本（生产推荐；脚本、compose 文件和镜像都锁在同一个 tag）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/deploy/install.sh \
  | RUI_REF=v0.4.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2 sh
```

装到 `/vol1/rui`（目录需可写）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/deploy/install.sh \
  | RUI_HOME=/vol1/rui RUI_REF=v0.4.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2 sh
```

`RUI_REF` 决定脚本拉取的 `docker-compose.yml` / `config.example.yaml` 来自哪个 ref，
`RUI_IMAGE` 决定运行哪个镜像；两个一起固定，重装或回滚时才不会悄悄换版本。镜像拉取失败时，
源码回退也用同一个 `RUI_REPO` + `RUI_REF` 克隆/构建，并打印实际 ref 与 commit。

安装脚本里可用的环境变量：

```sh
RUI_HOME=~/rui          # 安装目录
RUI_REPO=yes999zc/rui   # fork
RUI_REF=main            # 分支/tag
RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2   # 生产建议固定 tag 或 digest
RUI_RAW=https://raw.githubusercontent.com/yes999zc/rui/main
                        # 可选：raw 不通时换成镜像前缀，例如
                        # https://ghproxy.net/https://raw.githubusercontent.com/yes999zc/rui/main
```

### 2.2 脚本做什么

`install.sh` 做五件事（已有文件不覆盖，重跑安全）：

| 步骤 | 做什么 | 落在哪 |
|---|---|---|
| 1 | 拉 `docker-compose.yml` + `config.example.yaml`（参考件），并拉运维脚本 `backup.sh` / `restore.sh` / `acceptance-v1.py` | `~/rui/`、`~/rui/scripts/` |
| 2 | `setup` 写**生产模板**（provider 链、模型路径、playback 16k/1ch）+ 生成 `keywords.txt`（默认唤醒词）和**控制台 token**，无设备/无密钥 | `~/rui/config/config.yaml`、`config/keywords.txt` |
| 3 | 生成 `.env`，含随机 device token | `~/rui/.env`（chmod 600） |
| 4 | 检查 ASR/VAD/KWS 三件套；任一缺失就 `fetch-models`（先从镜像 seed 复制 VAD/KWS，再下 SenseVoice） | `~/rui/data/models/` |
| 5 | `docker compose up -d` 并等 `/health` 通过；健康后把控制台 token 存到 `console-token.txt`（0600，已存在不覆盖）；120 s 仍不健康则打印探针/日志命令并 `exit 1` | 容器 `rui`、`~/rui/console-token.txt` |

### 2.3 期望输出与耗时

```text
[1/5] compose file
[2/5] config + wake words
console token: <console-token>          ← 只在这里打印一次；[5/5] 会另存到 console-token.txt
[3/5] env
      wrote .env with a generated device token (48 hex chars)
[4/5] local models: ASR + VAD + wake word
      missing: asr-model; running fetch-models (image seed first)
[5/5] starting
      waiting for the service to answer /health
      healthy
      console token saved: ~/rui/console-token.txt (mode 600)
done. open http://<nas-lan-ip>:8080/
  console: token saved to ~/rui/console-token.txt (mode 600) - the 401 dialog points here
```

镜像已在本机（或拉取很快）且只需下载 ASR 时，实测约 **85 秒**到 `healthy`；首次含镜像拉取和
239 MB 模型，按网络通常 **4–8 分钟**（验收记录里一次 `install.sh` 为 234 s）。脚本最后打印的
`open http://<nas-lan-ip>:8080/` 里的 IP 取自 `hostname -I`；多网卡/容器网桥环境可能不准，
以你 NAS 的实际 LAN IP 为准。

### 2.4 控制台 token

`rui setup` 在 `[2/5]` 生成随机 `web.auth_token` 并打印一次。安装脚本在服务 healthy 后会把
它存到 **`~/rui/console-token.txt`**（chmod 600），不用再从滚屏日志里找：

```bash
cat ~/rui/console-token.txt                       # 安装时保存的那份
cd ~/rui && docker compose exec -T rui rui token  # 容器内实时读取（文件丢失/过期时用）
```

- **重跑/升级不覆盖**：`install.sh` 发现 `console-token.txt` 已存在就原样保留；如果它和当前
  token 不一致（例如执行过 `--rotate`），脚本会提示文件过期，按下面的命令刷新即可。
- **轮换**：`docker compose exec -T rui rui token --rotate`，重启服务后生效；随后刷新文件：
  ```bash
  cd ~/rui
  docker compose exec -T rui rui token > console-token.txt.new \
    && mv console-token.txt.new console-token.txt && chmod 600 console-token.txt
  ```
- **401 恢复**：控制台没有 token 会返回 401（页面登录框提示粘贴 `X-Auth-Token`）；先
  `cat ~/rui/console-token.txt`，文件不在就跑容器里的 `rui token`。**只有在本机开发**才可以
  显式 `web.allow_anonymous: true` 关闭鉴权；生产模板默认关闭，且非 loopback + 空 token 会
  拒绝启动。
- `console-token.txt` 和 `.env` 的 **device token 不是同一个**；device token 给音箱 client
  用（§3.0.6），控制台 token 只用于网页/API 的 `X-Auth-Token`。

> **分享前脱敏**：`install.sh` 的日志和 `setup` 输出都包含这个控制台 token（以及生成的
> device token）。贴日志、截图或发 issue 之前先把它替换成 `***`。

### 2.5 重跑与容器名冲突

**同一个 `RUI_HOME` 重跑是安全的**：如果 `rui` 容器的 compose project `working_dir` 就是本目录，
脚本直接 `docker compose up -d`（按需重建，并补齐缺失模型）。

- 容器属于**其他目录/项目**：脚本报出对方的 project 目录并退出，**不会停/删容器**。去那个目录
  `docker compose down`，或先停掉旧安装，再重跑。
- 遇到 **0.1.x `house-ear` 容器**：按提示先
  `docker stop house-ear && docker rm house-ear`；0.2.x 不会接管旧安装。
- 容器名固定为 `rui`，所以同一台机器不要同时跑两份安装；换目录/换版本都先停旧的。

### 2.6 镜像拉取失败 / 源码回退

- `docker pull ghcr.io/...` 报 TLS handshake timeout / 403：给 Docker daemon 配代理或 registry
  mirror；或先在能出网的机器 `docker pull` + `docker save`，到 NAS `docker load`。
- 拉取失败时脚本自动 `git clone --branch "$RUI_REF"` 到 `~/rui/src` 并构建同一 tag；需要 `git`，
  日志会打印 ref/commit。构建失败时先修网络/代理，或改用本地已有镜像 tag。
- `raw.githubusercontent.com` 卡住：用 `RUI_RAW` 指到镜像前缀；有些代理会缓存旧文件，冷装前确认
  拿到的是目标 ref。

### 2.7 手动 Docker Compose（可选）

只用于排障/离线。正常路径用上面的 `install.sh`；手动部署要自己准备配置、`.env` 和模型：

```bash
mkdir -p /vol1/rui/config /vol1/rui/data/models /vol1/rui/data/state /vol1/rui/scripts && cd /vol1/rui
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/docker-compose.yml -O
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.4.2/config.example.yaml -O
IMAGE=ghcr.io/yes999zc/rui:0.4.2
# 容器以 1000:1000 运行，先让两个可写卷归它所有（root/sudo）。
chown -R 1000:1000 config data
docker run --rm --user 1000:1000 -v /vol1/rui/config:/config "$IMAGE" \
  setup --config /config/config.yaml
# 按 §3.4 写 .env：至少 RUI_DEVICE_TOKEN、RUI_IMAGE、RUI_UID=1000、RUI_GID=1000；密钥可选
# 按 §3.3 跑 fetch-models 准备模型（同样用 --user 1000:1000）
docker compose up -d
```

`docker-compose.yml` 的 `user: "${RUI_UID:-1000}:${RUI_GID:-1000}"` 决定容器身份；
手工部署时 `.env` 里也写上同一对 `RUI_UID`/`RUI_GID`，否则 reboot 后 compose 会回落到默认值。
`config/` 与 `data/` 的属主必须与它一致，否则健康检查/服务会因读不到配置或写不了日志而失败。


## 3. 配置：第一次打开控制台到第一句话

`install.sh` 跑完后，**不需要手改 YAML**。用 NAS 的局域网 IP 打开
`http://<nas-lan-ip>:8080/`，输入 `[2/5]` 打印的控制台 token，进入 `/setup`：

`keys` → `models` → `prepare` → `params` → `wake` → `connect` → `test` → `verify`

每一步都可点开重跑；完成状态由服务端证据计算，不靠浏览器 cookie。下面按用户视角写清
“点之前准备什么 / 这一步做什么 / 完成是什么样”。

### 3.0 八步向导

#### 3.0.1 `keys`：每个用途选一个 provider

**点之前准备**：决定 LLM / ASR / TTS 各用哪家，拿到对应凭据。

| 用途 | 向导里选 | 需要准备 | 说明 |
|---|---|---|---|
| LLM | DeepSeek（模板默认） | `RUI_LLM_KEY` | 也可选已配置的其他 OpenAI 兼容 provider；LLM 必须有一个可用项 |
| ASR | 本地 SenseVoice（默认） | 无 key | 模型由 `fetch-models` 准备；免密钥 |
| ASR | 火山豆包流式识别 2.0 | `VOLC_API_KEY` | 新版控制台只要 API Key；资源 ID `volc.seedasr.sauc.duration` 或 `...concurrent` |
| ASR | SiliconFlow | `RUI_ASR_KEY` | 云端兜底，需要联网 |
| TTS | 讯飞（模板默认） | `XFYUN_APPID` + `XFYUN_API_KEY` + `XFYUN_API_SECRET` | 三项缺一不可 |
| TTS | 火山豆包语音合成 2.0 | `VOLC_API_KEY` + 音色 | 新版控制台只要 API Key；AK/SK 只用于额度卡片，不要填这里 |

- **每个用途只选一个 provider**；未选中的 fallback 不需要配。但 LLM/ASR/TTS 每一步都必须有
  一个可用项——ASR 可选本地模型，所以最少是 LLM + TTS 两组密钥。
- 点“测试”会用当前输入做一次真实小请求（未保存的值只用于本次探测，不写 `secrets.yaml`，
  也不回显）；保存后控制台只显示“已配置”。
- 火山/讯飞凭据的字段和来源见 [volc-credentials.md](volc-credentials.md)；同一把
  `VOLC_API_KEY` 可以同时给 ASR 和 TTS。

**这一步做什么**：把凭据写进 `data/secrets.yaml`（只写不读），把该用途的
`providers.<stage>.chain` 写成 `[selected]`；TTS 还会写 `providers.catalog.<name>.voice`。

**完成**：三个用途都显示可用/已配置；`keys` 状态为 done。也可以把同样的值写进
`~/rui/.env`（环境变量优先），但不要把密钥写进 `config.yaml`。

#### 3.0.2 `models`：只检查，不代下

**点之前准备**：无。`install.sh` 已经准备 ASR/VAD/KWS。

**这一步做什么**：检查 SenseVoice ASR（`tokens.txt` + `model.int8.onnx`/`model.onnx`）、
Silero VAD 文件和 KWS 四件套；缺失项会列出来。点“重新检查”只重算状态。

**完成**：ASR、VAD、KWS 三项都是绿色。若缺模型，在宿主机补：

```bash
cd ~/rui
IMAGE=$(sed -n 's/^RUI_IMAGE=//p' .env)
docker run --rm --user "${RUI_UID:-1000}:${RUI_GID:-1000}" -v "$PWD/data:/data" "$IMAGE" \
  fetch-models --dest /data/models/sensevoice \
  --vad-dest /data/models/silero_vad.onnx --kws-dest /data/models/kws
```

跑完回向导点“重新检查”。镜像 seed 会先复制 VAD/KWS，只联网下 SenseVoice；更多参数见 §3.3。

#### 3.0.3 `prepare`：设备侧准备，不碰 token

**点之前准备**：按 §1.1 的清单确认：client 已安装、音箱 IP 已固定、SSH 登录方式/重启命令/
日志位置/回滚方式都记下了。

**这一步做什么**：显示 open-xiaoai 的链接、最低版本、安装步骤和回滚说明；只让你勾选确认，
**不在音箱上执行任何命令**。需要 `connect` 的 ws 地址/token 的条目会作为“接好线后再做”的
提示，不要求在这里勾选。

**完成**：勾完设备侧检查项并点确认；确认状态写入 `data/setup.json`（0600，不含密钥）。
token 留到 `connect`。

#### 3.0.4 `params`：音箱参数

**点之前准备**：音箱 LAN IP（§1.1 第 2 项）、房间名、1–3 个唤醒词，以及音量/LED/端口/增益的选择。

| 字段 | 填什么 / 从哪来 | 默认 |
|---|---|---|
| 音箱地址 | 音箱 LAN IP，只填地址，不带 `http://`、不能有空格 | 必填 |
| 房间 | 自定义名字，如“客厅” | 客厅 |
| 唤醒词 | 1–3 个，每个最多 12 字 | 你好小瑞 |
| 音量 | 0–100；**0 = 不接管音量**，沿用原厂 | 60 |
| 灯效 | 新 LX06 默认开；关掉则灯环不亮 | 开 |
| 监听端口 | client 回连端口；只有冲突才改，client 的 ws URL 要同步 | 4412 |
| 麦克风增益 | 0–42 dB，可留空；越大越灵敏也越容易收噪声 | 空/0 |

**这一步做什么**：按 manifest 校验后 merge 写入 `devices[]`，并重启音频面；未展示的字段
（灯环配色等）保存时保留。`host` 在 inbound 模式下主要用于显示、doctor 的 SSH 探测和排障，
真正的连接是音箱 client 拨入。

**完成**：保存成功且没有 `plane_error`；client 会短暂断开后重新回连。

#### 3.0.5 `wake`：1–3 个唤醒词

**点之前准备**：确定唤醒词（每个最多 12 字，不能含空格/换行）。

**这一步做什么**：同时更新 `devices[].wake_words` 和 `config/keywords.txt`（每个词一行 `... @词`），
并重启音频面。两者必须一致；`keywords.txt` 是服务端 KWS 的唯一来源，client 不需要另配一份。
若 client/固件里也配了唤醒词，两边必须一致。

**完成**：`keywords.txt` 存在，且每个唤醒词都有对应的 `@词` 行。手动改过
`devices[].wake_words` 后，用 §3.3 的 `keywords` 命令重新生成，否则音箱还在听旧词。

#### 3.0.6 `connect`：把 ws 地址和 token 填进 client

**点之前准备**：用 **NAS 局域网 IP** 打开控制台（不要用 `localhost`/SSH 隧道，否则生成的地址
会是 `ws://127.0.0.1:4412`）；打开 §1.1 第 5 项记下的 client 配置文件。

**这一步做什么**：只读展示 `ws://<nas-lan-ip>:<listen_port>/?token=<device-token>`、device token
和监听端口。token 由服务端从环境变量解析，控制台只在这里按需显示；不会写配置。

操作顺序：

1. 复制完整 ws URL（含 `?token=`），写进 client 的 `server.txt` / `config.json` / `WS_URL`。
2. 重启 client，确认日志出现 `connected` / `已连接`。
3. 回向导点“等待连接”（每 3 秒查一次，最多 1 分钟）或“刷新连接状态”（旧版叫“检测”）。

| `connection_message` / 现象 | 含义 | 先查/处理 |
|---|---|---|
| `waiting_for_client` | 服务端在等 client 回连 | client 是否启动、地址是否指向 NAS LAN IP/4412；看 client 日志；重启 client |
| `connected` | 已回连 | 可以继续 `test` |
| `token_mismatch` / client 401 / close 1008 / NAS 日志 `bad or missing token` | 服务端没 token，或两边 token 不同 | 在 `connect` 重新复制当前 token，更新 client 并重启；确认 `.env` 的 `RUI_DEVICE_TOKEN` 没被改 |
| `port_blocked` / 连接被拒绝 | 服务端端口没监听 | `ss -ltnp \| grep 4412`；确认音频面在跑、`listen_port` 一致、容器 host network、防火墙放行 |
| 一直连不上/频繁断 | 可能地址错、跨网段，或同一 4412 有多个 client | 同一 LAN、DHCP 固定租约；`ss -tnp \| grep 4412` 只应有一个 ESTAB，停掉多余调试 client |

**完成**：状态显示“已连接”，服务端 `speaker_connected=true`；`connect` 步骤变 done。

#### 3.0.7 `test`：只读连接测试

**点之前准备**：无。

**这一步做什么**：`device.test` 只探测、不写配置，返回三项：

- `plane`：`speaker_connected` 是否为 true（inbound 的唯一通过条件）；
- `host`：TCP/SSH 探测，仅作诊断，**SSH 通不等于接线成功**；
- `token`：device token 是否已配置。

outbound 机型没有 inbound 回连，才用 host probe 作为通过证据。

**完成**：显示“通过”；失败时按提示回 `connect` 核对地址/token/端口，或重启 client。

#### 3.0.8 `verify`：试一句

**点之前准备**：一句想测试的话，例如“现在几点？”。

**这一步做什么**：`try.run` 走真实全链路（ASR → LLM → TTS → 播放），记录 `audio_bytes`。
音频 >0 只证明 TTS 产出了音频，最终要人耳确认音箱真的出声；也可以点“我听到了回答”人工确认。

**完成**：听到回答，或人工确认；`verified=true`。`verify` 是**可选步**：跳过不会阻止整体完成，
但控制台标记“未验证”，且跳过只是本次界面的标记，不写成已验证；刷新后仍可回来试一句。

下面的 YAML / `.env` 片段是**参考和手工排障用**的；正常冷装由安装脚本和向导写入。
`setup` 已写好生产模板（provider 链、模型路径、16 kHz 单声道播放、ack TTS），并自动生成
`config/keywords.txt`；`config.example.yaml` 是同一份配置的带注释版本。

### 3.1 设备（`devices[]`）

正常不用手写：向导 `params` 步会 merge 写入第一台设备。LX06 新设备**默认开灯环**
（`led.enabled: true`）；音量（默认 60）、回连端口（4412）和麦克风增益可以在向导/控制台的
设备参数里调整，没展示的字段保存时保留。下面的 YAML 只用于对照或手工恢复：

```yaml
devices:
  - id: living
    adapter: xiaoai
    host: <speaker-ip>        # 向导 params 步填写；inbound 下只用于显示和 doctor 的 SSH 探测
    room: 客厅
    wake_words: ["你好小瑞"]
    volume: 60                # 默认 60；0 = 不接管音量，沿用原厂
    listen_port: 4412         # 音箱 client 回连的端口
    token_env: RUI_DEVICE_TOKEN
    led:
      enabled: true           # 向导创建的新 LX06 默认开灯环
```

### 3.2 provider（`providers.catalog` + 三条链）

正常路径在向导 `keys` 步用下拉选 provider，保存后自动把对应 stage 的
`chain` 写成 `[selected]`，**不需要手改 `config.yaml`**。只有手工/非向导部署时才参考
下面的模板链和 `.env`：

```yaml
providers:
  asr: {chain: [local_asr, siliconflow_asr]}   # 本地优先，云端兜底
  tts: {chain: [xfyun_tts, volc_tts]}           # 讯飞优先，火山兜底（无硅基流动）
  llm: {chain: [deepseek_llm]}
```

密钥优先在向导 `keys` 步填写（写入 `data/secrets.yaml`，0600，只写不读），也可以写进
`~/rui/.env`（环境变量优先）。密钥只放 `~/rui/.env`、`data/secrets.yaml` 或控制台
「AI 供应商 / 密钥」页，**不要写进 config.yaml**。容器里 `env_file: .env` 注入。要换 provider
时，向导里改选即可；只有非向导部署才需要改 `providers.catalog` + 三条链，
`config.example.yaml` 有完整写法。

火山/讯飞要点：

- **火山 ASR/TTS**：新版控制台只要 `VOLC_API_KEY`（请求头 `X-Api-Key`），不需要 AppID 或
  AK/SK；AK/SK（`VOLC_ACCESS_KEY_ID` / `VOLC_SECRET_ACCESS_KEY`）只给控制台额度卡片用。
  ASR 资源 ID 用 `volc.seedasr.sauc.duration`（小时版）或 `...concurrent`（并发版）；TTS
  资源 ID `seed-tts-2.0`，音色在 `keys` 步选、保存后可在「AI 供应商」页试听。
- **讯飞 TTS**：`XFYUN_APPID` + `XFYUN_API_KEY` + `XFYUN_API_SECRET` 三项缺一不可。
- 同一把 `VOLC_API_KEY` 可以同时用于 ASR 和 TTS；两边各保存一次即可。字段来源和排错见
  [volc-credentials.md](volc-credentials.md)。

### 3.3 模型（`fetch-models` 一次装齐）

镜像在构建时把两个小模型放进 `/opt/seed-models/`：`silero_vad.onnx` 和
`kws/<KWS_REPO>/`（Dockerfile 从 GitHub release 下载并校验 SHA256；GitHub Actions
的构建网络不需要 mainland 的镜像）。`fetch-models` **在联网前**先把它们复制到
`--vad-dest` / `--kws-dest`，所以新镜像的冷装只需要下载 SenseVoice ASR（239 MB，
hf-mirror 约 1 MB/s），不再等 GitHub 上那个 33 MB 的 KWS 包。这就是 Docker 安装不再
依赖 GitHub release 的原因。

| 模型 | 怎么来 | 容器内路径 | 宿主机路径 |
|---|---|---|---|
| SenseVoice ASR（239 MB） | `install.sh` 自动 `fetch-models`，走 hf-mirror | `/data/models/sensevoice` | `~/rui/data/models/sensevoice` |
| Silero VAD（~0.6 MB） | 新镜像从 `/opt/seed-models` 复制；旧镜像/源码安装回退下载 | `/data/models/silero_vad.onnx` | `~/rui/data/models/silero_vad.onnx` |
| KWS 唤醒模型（32.9 MB 包） | 同上；seed 已是解包后的目录，复制时无需再解包 | `/data/models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | `~/rui/data/models/kws/` |

**旧镜像/无 seed**：`/opt/seed-models` 不存在时，`fetch-models` 完全保持原行为
（VAD 依次试 hf-mirror / ghproxy / GitHub；KWS 依次试 ghproxy / gh-proxy / GitHub）。
所以滚动升级期间不需要改 `install.sh`，也不需要额外参数。

compose 把 `./data:/var/lib/rui`，同时把 `./data/models:/data/models` 挂进容器：
数据卷负责持久化，`/data/models` 是模板和文档使用的路径。两者指向同一个宿主机目录，
删容器重建都不丢。手动重跑（默认会先复制 seed）：

```bash
docker run --rm --user 1000:1000 -v ~/rui/data:/data \
  ghcr.io/yes999zc/rui:<tag> fetch-models \
  --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx --kws-dest /data/models/kws
# 网络受限时可只装 ASR+VAD（唤醒词会失效）：加 --no-kws
# 显式不用镜像 seed（强制从网络重下 VAD/KWS）：加 --seed-dir '' --force
```

#### 自建模型镜像（非 Docker 用户/内网）

`fetch-models` 支持三个环境变量，用来替换对应下载列表的第一项；该项失败后仍会
回退到内置镜像（所以镜像临时离线不会让安装必然失败）：

| 变量 | 含义 |
|---|---|
| `RUI_ASR_URL` | SenseVoice `model.int8.onnx` 的**完整文件 URL**；`tokens.txt` 先取同一目录，失败再回退 hf-mirror |
| `RUI_VAD_URL` | `silero_vad.onnx` 完整 URL，替换 VAD 列表第一项 |
| `RUI_KWS_URL` | KWS `.tar.bz2` 完整 URL，替换 KWS 列表第一项（下载后仍会解包） |

> 新镜像默认先从 `/opt/seed-models` 复制 VAD/KWS，此时 `RUI_VAD_URL` /
> `RUI_KWS_URL` 不会触发下载。要让这两个 URL 生效（或强制从网络重下），加
> `--seed-dir ''`；`--force` 单独用时仍优先使用 seed。

```bash
docker run --rm --user 1000:1000 -v ~/rui/data:/data \
  -e RUI_ASR_URL=https://models.example/sensevoice/model.int8.onnx \
  -e RUI_VAD_URL=https://models.example/silero_vad.onnx \
  -e RUI_KWS_URL=https://models.example/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2 \
  ghcr.io/yes999zc/rui:<tag> fetch-models \
  --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx --kws-dest /data/models/kws
```

用 compose 时把它们写进 `~/rui/.env` 即可（`env_file` 会传入容器，改完
`docker compose up -d`）。`--kws-url` / `--vad-base` 仍是单 URL 覆盖（优先于对应环境变量）；
`--base` 是 ASR 的内置回退 base，环境变量仍排第一。

**唤醒词**：`setup` 已按默认「你好小瑞」生成 `~/rui/config/keywords.txt`，内容形如
`n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞`（token、boost、阈值、显示文案）。改
`devices[].wake_words` 后必须重新生成，否则音箱还在听旧词：

```bash
docker run --rm -v ~/rui/config:/config ghcr.io/yes999zc/rui:<tag> \
  keywords --config /config/config.yaml
```

模板里 `audio.voice.kws_enabled: true`、`kws_model_dir` 指向上面目录、
`kws_keywords_file: {config_dir}/keywords.txt`。如果确实没装 KWS 模型，把
`kws_enabled` 设为 false；否则 plane 起不来 KWS，`doctor` 会报 `wake_path`。

### 3.4 `.env`

```bash
RUI_LLM_KEY=...        # DeepSeek（模板 llm 链）
RUI_ASR_KEY=...        # SiliconFlow（云端 ASR 兜底）
VOLC_API_KEY=...             # 火山豆包 TTS/ASR（模板 tts 兜底；ASR 可选）
XFYUN_APPID=...              # 讯飞 TTS（模板 tts 主）
XFYUN_API_KEY=...
XFYUN_API_SECRET=...
RUI_HA_TOKEN=...       # 接 Home Assistant 时填
RUI_DEVICE_TOKEN=...   # install.sh 生成的，音箱 client 用同一个值
RUI_IMAGE=ghcr.io/yes999zc/rui:0.4.2   # 安装脚本写入；固定版本，回滚时改这里
```

只填实际用到的即可。**没有讯飞 key 时**，在向导 `keys` 步把 TTS 选成 `volc_tts` 并填
`VOLC_API_KEY`；向导会把 `providers.tts.chain` 写成 `[volc_tts]`。如果是手工部署且不改链，
模板的 `xfyun_tts` 仍是 `chain[0]`，不会因为 `.env` 留空就自动跳过。
没有 SiliconFlow ASR key 时，向导里选本地 ASR（需已跑 `fetch-models`）。硅基流动 TTS 已从
控制台与模板链下线（2026-10-23），旧的 `RUI_TTS_KEY` 不再使用。

改完 `.env` / `config.yaml` 后：`docker compose up -d`（配置读盘，无需重建镜像）。

### 3.5 验收

```bash
docker compose ps                       # STATUS 应为 healthy
curl -s localhost:8080/health | python -m json.tool
curl -s localhost:8080/api/v1/doctor | python -m json.tool
docker compose logs --tail=100 rui
```

期望：`/health` 的 `status=ok`、`audio_plane=true`（配置了设备时）；`/api/v1/doctor`
`verdict=ok|degraded`，`wake_path` PASS 表示模型与 `keywords.txt` 已就位。`degraded` 常见但
可接受的原因：音箱 SSH 22 探测不到（inbound 设备本来就不要求 SSH）、`music.enabled` 关闭。
非 loopback 且没设 `web.auth_token` 时服务会**拒绝启动**，不属于可接受的 degraded。

### 3.6 装好之后：密钥、token、升级/回滚

以下命令默认在 `~/rui` 下执行；装在 `/vol1/rui` 就把路径替换掉。

| 位置 | 内容 | 权限/备注 |
|---|---|---|
| `~/rui/config/config.yaml` | `devices[]`、provider 链、`web.auth_token` | 0600；改动前先备份 |
| `~/rui/console-token.txt` | 安装时保存的控制台 token（同 `web.auth_token`） | 0600；重跑不覆盖，轮换后按 §2.4 刷新 |
| `~/rui/data/secrets.yaml` | 向导保存的密钥 | 0600，只写不读；环境变量优先 |
| `~/rui/.env` | `RUI_DEVICE_TOKEN`、`RUI_IMAGE`、`RUI_UID`/`RUI_GID`、可选 key | 0600；`docker compose` 注入 |
| `~/rui/config/keywords.txt` | 唤醒词/KWS 参数 | 改唤醒词后重新生成 |
| `~/rui/data/models/` | SenseVoice / VAD / KWS | 删了可重新 `fetch-models` |
| `~/rui/data/setup.json` | 向导确认/时间戳 | 0600；不含密钥 |

- **换密钥**：控制台「AI 供应商 / 密钥」页或重跑 `keys`；密钥只写不读，不要写进
  `config.yaml`。同一把 `VOLC_API_KEY` 改了要确认 ASR/TTS 两个用途都重新保存。
- **换 device token**（音箱 client 用）：在 `connect` 步重新显示/复制；或改
  `~/rui/.env` 的 `RUI_DEVICE_TOKEN`，`docker compose up -d`，再把新值写进 client 并重启；
  最后回 `test` 确认 `speaker_connected=true`。旧 client token 会得到 401/close 1008，
  NAS 日志是 `xiaoai: rejected ... (bad or missing token)`。
- **换控制台 token**：`docker compose exec -T rui rui token --rotate`，再 `docker compose restart rui` 让服务加载新值；重启后旧 token 失效，并按 §2.4 刷新 `console-token.txt`（重跑 `install.sh` 不会自动覆盖旧文件）。
- **升级**：`.env` 固定 `RUI_IMAGE` → `docker compose pull && docker compose up -d` →
  `docker compose ps` 等 healthy → `docker compose exec -T rui rui doctor`。
- **升级到非 root 镜像（已有安装，一次性）**：先 `scripts/backup.sh --out ~/rui-backups`，
  再 `sudo chown -R 1000:1000 config data`（只改属主，不改内容；models 会一起遍历），
  然后 `docker compose pull && docker compose up -d --force-recreate`，最后
  `docker compose exec -T rui rui-healthcheck`。重跑 `install.sh` 会自动做 chown 并补
  `.env` 的 `RUI_UID`/`RUI_GID`；完整边界见 [security.md](security.md)。
- **回滚**：把 `.env` 的 tag 改回上一版，先备份 `config/config.yaml`（旧镜像可能不认新字段），
  再 `docker compose up -d`；回滚到 root 镜像时把 `.env` 的 `RUI_UID`/`RUI_GID` 改成 `0`，
  或临时用 `user: "0:0"`。详见 §7 和 [runbook.md](runbook.md#6-回滚)。
- **备份/恢复**：`cd ~/rui && scripts/backup.sh --out ~/rui-backups`；恢复步骤见
  [runbook.md §9](runbook.md#9-备份与恢复)。

## 4. 音乐：Navidrome

音乐默认 **关闭**（`music.enabled: false`）：NAS 不可达时工具只会反复说「曲库里没找到」，
不如不注册。启用需要三样东西：

1. Navidrome（或任意 Subsonic 兼容服务）的地址/账号，写成 JSON **放在卷上**（
   `config.example.yaml` 默认放数据卷：`/var/lib/rui/lan_media.json`，
   宿主机 `~/rui/data/lan_media.json`；放配置卷 `config/lan_media.json` 也可以，
   路径以 `music.secrets_path` 为准），权限 `chmod 600`：

   ```json
   {"url": "http://navidrome.local:4533", "user": "<user>", "password": "<password>"}
   ```

   代码只发 `md5(password+salt)` token，不把密码放进 URL；这个文件的值不进日志、不进配置。
2. 配置指向它，并把转码缓存放到数据卷（schema 默认 `/tmp`，容器重建即丢）：

   ```yaml
   music:
     enabled: true
     source: navidrome
     secrets_path: /var/lib/rui/lan_media.json   # 与上面放的位置一致
     cache_dir: /var/lib/rui/music_cache
     cache_tracks: 8
     queue_min_tracks: 2
     queue_batch: 20
     history_max: 60
   ```

   `music.enabled: true` 时 `secrets_path` 必须非空（配置校验会报错）。

3. 起歌需要 ffmpeg 做 16 kHz 单声道转码。**镜像已带 `ffmpeg`**：Dockerfile 在
   `debian:bookworm-slim` 构建阶段从固定版本源码（FFmpeg 7.1.5 + SHA-256 校验）编译
   **LGPL-only** 静态二进制，只链接 libopus / libmp3lame，不装 Debian 的 ffmpeg 包、
   也不拖共享库；构建时会自动断言许可（GPL/nonfree 标志会让镜像构建直接失败）。
   容器内自检：

   ```bash
   docker compose exec rui ffmpeg -version   # configuration: 不含 --enable-gpl/--enable-nonfree
   docker compose exec rui ffmpeg -L          # GNU Lesser General Public ... version 2.1
   ```

   二进制装到 `/usr/local/bin/ffmpeg`（旧镜像是 `/ffmpeg`），但代码一直按 PATH 里的
   `ffmpeg` 调用，所以配置和调用方都不用改。缺了就是用了旧镜像。Navidrome 侧能直出
   mp3 时也仍然会走本地转码，行为一致。来源、构建参数和 source-offer 说明见
   [THIRD_PARTY.md](../THIRD_PARTY.md#ffmpeg)。

音乐相关的环境变量：**没有**。账号在 JSON 里，其余都是配置项。控制台「音乐」面板在
`music.enabled: true` 后才出现。

## 5. Home Assistant（MCP）

HA 走 **MCP over HTTP**（`ha.py`），不是直接调 HA REST。需要：

1. Home Assistant 安装 MCP Server 集成并开放 `http://<ha>:8123/api/mcp`。
2. 生成长期访问令牌，写进 `~/rui/.env`：

   ```bash
   RUI_HA_TOKEN=<long-lived token>
   ```

3. 配置：

   ```yaml
   tools:
     ha:
       base_url: http://homeassistant.local:8123   # 代码会自动补 /api/mcp
       token_env: RUI_HA_TOKEN
   ```

   `tools.mcp_servers[]` 是**通用 MCP 预留位**：schema 已接受
   `{name, transport: http|stdio, url, command, enabled}`，但当前版本只有 HA 一条实际接线，
   不要指望填进去就多出一套工具。HA 挂了只会让设备类工具降级，语音/音乐/本地工具不受影响。

## 6. 健康检查与日志

- compose 健康检查 `python /usr/local/bin/rui-healthcheck`：**从 config 读端口**
  （改 `web.port` 不会误报），`/health` 必须 200，且**配置了设备时 `audio_plane` 必须为
  true**——控制面活着但音频面死了，是这台机器最常见的「看起来在线、其实听不见」。
  `start_period: 90s`，之后每 30s 一次、3 次失败标记 unhealthy。
- Docker 不因为 unhealthy 自动重启容器；unhealthy 是**告警**，处理见 runbook。
- 容器 stdout/stderr：`json-file`，`max-size=10m`、`max-file=3`（30 MB 封顶）。
- 应用自己的 `data/service.log`：2 MB × 3 轮转，控制台「日志」页读它；
  容器内路径 `/var/lib/rui/service.log`。
- 查看：`docker compose logs -f --tail=200 rui`；按时间/关键字过滤：
  `docker compose logs --since 30m rui | grep -E "turn:|audio plane|error"`。

## 7. 镜像版本与回滚

- 不要只依赖 `latest`。生产在 `~/rui/.env` 里固定：
  `RUI_IMAGE=ghcr.io/yes999zc/rui:<版本>`（或 `@sha256:<digest>`）。
- 发布 tag：`vX.Y.Z` 由 CI 自动发同名 tag + `<major>.<minor>` + `latest` + `sha-<hash>`；
  `main` 分支发 `latest` 与 `sha-<hash>`。
- 回滚：把 `.env` 的 tag 改回上一个版本 → `docker compose up -d`，然后按 §3.5 验收。
  配置和数据在卷上，不会因回滚丢失；但**降级到旧镜像前先备份 config**（新配置字段旧版本
  可能不认识）：`cp config/config.yaml config/config.yaml.bak.$(date +%Y%m%d-%H%M%S)`。
- 升级：`docker compose pull && docker compose up -d`，看 `docker compose ps` 变 healthy，
  再跑一次 doctor。
- **非 root 迁移（0.3.x 起镜像 USER=rui:1000）**：旧安装的 `config/`、`data/` 是 root
  所有，直接 `up -d` 会因无法写配置/日志而失败。升级前：备份 → `sudo chown -R 1000:1000
  config data` → `docker compose pull && docker compose up -d --force-recreate`。健康检查
  也以 uid 1000 运行，能读 0600 的 config 即通过。回滚到 root 镜像时用
  `RUI_UID=0 RUI_GID=0`（或 `user: "0:0"`），数据卷不需要改回 root。
- 本机 OrbStack 构建的是 arm64；给 NAS 构建/发布用 CI 多架构，或在 x86 NAS 上
  `docker build --platform linux/amd64 ...`（慢，但可用）。构建镜像的国内镜像参数：

  ```bash
  docker build \
    --build-arg APT_MIRROR=mirrors.aliyun.com \
    --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
    --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    -t rui:dev .
  ```

### 7.1 升级带下游 adapter / 插头的安装（dot2 #12 C1/C5）

安装里有 out-of-tree adapter（`RUI_ADAPTER_PLUGINS` / entry point）或第三方插头时，升级不只是
换个 tag：

- **先备份**：`config/`、`data/` 与 `.env`（含 `RUI_IMAGE`、`RUI_DEVICE_TOKEN` 和下游自己的
  `DOT2_*` 等变量）。重跑 `install.sh` **不会**改现有 `RUI_IMAGE`；升级是显式改 `.env` 那一行。
- **`rui_compat` 必须对齐**：loader 用 range 匹配 **rui 包版本**（`rui.__version__`），语义没变。
  0.5.0 会让 `>=0.3.0 <0.5.0` 直接失效；内置 LX06 / `examples/plugin-example` 已抬到 `<0.6.0`，
  下游插头（含 dot2）应在 0.5.0 发布前把 `plugin.yaml` 上界改成 `<0.6.0`，并在新版本上先跑
  `rui plug-test`。
- **healthcheck 失败要区分根因**：`/health` 的 `device_errors>0` → 「设备不可驱动（adapter 缺失
  或 manifest 不兼容），restart 不会修复」；`multi_device.error` → 平面启动失败并带真因；
  `audio_devices[]` 会点名哪台/哪个房间的平面没起来、子进程报了什么错。先看
  `docker compose logs --tail=100 rui` 与 `/api/v1/devices`，不要盲目重启。
- **compose 被自定义过**：下游通常改过 `docker-compose.yml`（挂 adapter、加环境变量）。升级前
  用 `docker compose config` 留档；`up -d` 之后确认自定义挂载/变量仍在。
- **vanilla ↔ downstream 镜像切换**：`RUI_IMAGE` 指官方镜像时不会加载下游 adapter，`.env` 里
  残留的 `DOT2_*` 变量不会被读取（留着无害，切回 downstream 镜像才生效）；反过来切回官方镜像前，
  先确认 `devices[]` 不依赖只在下游镜像里存在的 adapter。
- **音量换算**：config/UI 是 0–100，`DeviceServices.set_volume/read_volume` 是设备混音器 0–255；
  唯一允许的换算是 `device/contract.py` 的 `percent_to_device_volume()` /
  `device_volume_to_percent()`，adapter 不要复制 magic number（详见 `socket-plug-v1.md` §4.1）。

`rui_compat` 兼容矩阵（range 语义不变，只随发布抬上界）：

| rui 版本 | 可接受的插头 `rui_compat` | 说明 |
|---|---|---|
| 0.3.x / 0.4.x | 覆盖该版本的 range（当时为 `<0.5.0`） | 0.4.2 仍是单平面；内置插头已同步 `<0.6.0`，两个版本都能加载 |
| 0.5.x | 上界必须 > 0.5.0（建议 `>=0.3.0 <0.6.0`） | 多平面/仲裁随 0.5.0 落地，但全部在 rui 内部；adapter/manifest 契约、`drain_wakes()` 时序、`DeviceWake` 六字段、enrich 责任均不变 |
| 0.6+ | 上界再按当次 release note 抬 | 每次 Major/Minor 发布会在 CHANGELOG 写清需要的上界 |
## 8. 卷与备份

| 宿主机 | 容器内 | 内容 | 丢了会怎样 |
|---|---|---|---|
| `~/rui/config` | `/config` | `config.yaml`、`keywords.txt`（+ 可选 `lan_media.json`） | 要重配设备/provider/唤醒词 |
| `~/rui/data` | `/var/lib/rui` | `service.log`、`secrets.yaml`、`memory.json`、提醒/会话历史、音乐缓存 | 记忆/提醒/密钥消失，模型还在（在 `data/models`） |
| `~/rui/data/models` | `/data/models`（同一目录的第二个挂载点） | SenseVoice / VAD / KWS 模型 | ASR 重下 ~239 MB；VAD/KWS 从镜像 seed 恢复 |

备份只要 `config/` + `data/`（models 可重下）；恢复时把目录放回、`docker compose up -d`。
`config.yaml` 每次写盘都有时间戳备份（`config.yaml.bak.*`），改坏了用最近的备份覆盖。

**属主**：两个可写卷必须归 compose 的 `user:`（默认 `1000:1000`）所有。恢复备份/从别的机器
拷贝目录后，重新执行一次 `sudo chown -R 1000:1000 config data`，否则容器会报
`Permission denied`（配置读不到或 `/var/lib/rui` 写不进）。

## 9. 本地开发（不走容器）

```bash
git clone https://github.com/yes999zc/rui && cd rui
uv sync && uv run rui doctor
uv run rui serve
```

## 10. 已知缺口（历史 P15 记录；0.4.0 仍适用部分）

- **`setup` 仍不写 devices**：这是有意的（不替用户猜设备）。模板已带 provider 链、模型路径、
  `keywords.txt`；正常冷装由 `/setup` 向导的 `params` 步写入 `devices[]`、`keys` 步写密钥，
  不需要手改 YAML。只有非向导部署才手工加 `devices[]` + 填 `.env`。
- **AI 之外的人工项**：真机接线（音箱 client 指向 NAS IP:4412、device token 一致）仍要人做；
  §3.0.6 和 `docs/runbook.md` 的故障表覆盖了排查路径。
- **损坏的模型文件会让采集静默停摆**：无效 `silero_vad.onnx` 下 plane 不报错、
  `/api/v1/audio` 的 `frames_in` 一直是 0（实测，见 runbook §11.6）。换回有效模型后恢复；
  排查时先跑 `fetch-models` 重新拉模型。
- **音频面崩溃后的自愈已补**（`AudioPlaneSupervisor.watch()`，1 s 轮询 + 1→60 s 退避）：
  实测 kill plane 后 ~3 s 新 pid、`restarts=1`；旧的「必须手动 restart」说法已过时。
  长稳（反复崩溃的退避封顶）仍看 3 天观察。

## 11. 安装与接线常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| client 日志 401 / close 1008；NAS 日志 `xiaoai: rejected ... (bad or missing token)` | client 还带着旧 token，或两边不是同一个值 | 到 `/setup` 的 `connect` 步重新复制当前 ws URL + device token，更新 client 配置并重启；确认 `.env` 的 `RUI_DEVICE_TOKEN` 没被改过。控制台 token 不用于这里 |
| 重跑 `install.sh` 报容器 `rui` 已存在 | 同一 `RUI_HOME` 重跑是安全的；容器属于另一个 compose project 时脚本拒绝接管 | 按脚本打印的目录去执行 `docker compose down`，或在旧目录停掉旧安装后再重跑。0.1.x 的 `house-ear`：`docker stop house-ear && docker rm house-ear` |
| `docker pull ghcr.io/...` TLS handshake timeout / 403 | Docker daemon 没有代理/镜像源，或 GHCR 不可达 | 给 daemon 配代理或 registry mirror；或 `docker pull` + `save` 到 NAS `load`。脚本会自动按 `RUI_REF` 从源码构建同一 tag（需 `git`）；`raw` 卡住用 `RUI_RAW` |
| 灯环不亮 | 0.4.0 新建 LX06 默认 `led.enabled: true`；老配置/手动配置可能仍是关闭 | 到 `params` 的“灯效”打开，或检查 `config.yaml` 的 `devices[].led.enabled`；保存会重启平面 |
| 控制台 401 / 找不到 console token | `[2/5]` 的 `console token:` 只打印一次，容易被日志刷走 | 先 `cat ~/rui/console-token.txt`（安装时已保存）；文件不在或过期就用 `cd ~/rui && docker compose exec -T rui rui token` 找回。`--rotate` 轮换后重启服务并刷新该文件。它和 `.env` 的 device token 不是同一个 |
| `install.sh` 最后报 120 s 不健康 | 容器起了但 `/health` 未通过；常见是 token/端口/模型或配置问题 | 按脚本提示跑 `docker compose exec -T rui rui-healthcheck` 和 `docker compose logs --tail=100 rui`；修好后重跑脚本（同目录安全） |
| 升级后容器反复重启 / 日志 `Permission denied` / healthcheck `cannot read config` | 非 root 镜像（uid 1000）读不了 root 所有的 bind mount | 备份后 `sudo chown -R 1000:1000 config data`，再 `docker compose up -d --force-recreate`；见 [security.md](security.md) |
