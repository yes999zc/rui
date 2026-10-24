# Deploy

**生产目标是 NAS 上的 Docker：一条命令冷装。** Mac 只是开发机（`:8091` 的开发服务和设备
指向 Mac 的调试链路都在 `docs/HANDOFF.md`）。生产形态是 NAS 上 `deploy/install.sh` 拉镜像、
写配置、起服务；`--network host`，默认 `:8080`，配置在 `~/rui/config`。

运维手册（起停、日志、换配置、换镜像、回滚、常见故障）在
[`docs/runbook.md`](runbook.md)。

## 1. 前置条件

- x86_64 或 arm64，4 核 / 4 GB 内存 / 2 GB 可用磁盘（首次还要留 ~250 MB：镜像自带
  VAD/KWS，只需下 239 MB 的 SenseVoice ASR）
- Docker + `docker compose`，允许 `--network host`（设备音频是长连接 socket）
- **Docker 权限**：运行安装脚本的用户要能访问 Docker daemon——root，或已加入 `docker` 组
  并重新登录（`sudo usermod -aG docker "$USER"`）。`docker` 不存在或 `docker info` 失败时，
  `install.sh` 会在做任何事之前退出并给出提示。
- **可写安装目录**：默认 `~/rui`（`RUI_HOME` 可改）。NAS 上 `/vol1` 这类挂载点常是
  root-only；用 root 运行安装，或先建好目录并 `chown` 给安装用户，否则脚本会在建目录时失败。
- 音箱与 NAS 在同一 L2 网段；音箱端 client 指向 NAS 的 IP:4412
- **镜像架构**：GitHub Actions 发的是 `linux/amd64` + `linux/arm64` 多架构镜像（见
  `.github/workflows/docker-publish.yml`）。在 Mac（arm64）本地 `docker build` 出来的
  只有 arm64，**不能直接搬到 x86_64 NAS**：要么用 GHCR 镜像，要么在 NAS 上带
  `--platform linux/amd64` 构建。

### 控制台令牌（安全审计后必读）

`install.sh` 里的 `rui setup` 会生成随机 `web.auth_token` 并打印一次；错过了就进容器读：

```bash
docker compose exec -T rui rui token
docker compose exec -T rui rui token --rotate  # 轮换
```

控制台没有 token 会返回 401（页面会提示输入 `X-Auth-Token`）。**只有在本机开发**才可以显式
`web.allow_anonymous: true` 关闭鉴权；生产模板默认关闭，且非 loopback + 空 token 会拒绝启动。

> **分享前脱敏**：`install.sh` 的日志和 `setup` 输出都包含这个控制台 token（以及生成的
> device token）。贴日志、截图或发 issue 之前先把它替换成 `***`。

### 火山豆包语音（可选）

- 凭证：新版控制台 ASR/TTS 都只需要 `VOLC_API_KEY`（`X-Api-Key`），不需要 AppID 或
  AK/SK；AK/SK 只给额度卡片用。详见 [volc-credentials.md](volc-credentials.md)。
- TTS：`.env` 里填 `VOLC_API_KEY`（控制台 API Key），把 `config.yaml` 的
  `providers.tts.chain` 改成 `[volc_tts, xfyun_tts]` 即启用；
  音色在控制台「外部资源 → volc_tts → 音色」选并「试听」，保存后重启服务生效。
- 额度卡片：`.env` 再填 `VOLC_ACCESS_KEY_ID` / `VOLC_SECRET_ACCESS_KEY`（只用于只读查询账户余额/用量）。
- ASR：项目已接入豆包流式语音识别 2.0（`volc_asr`，双向 WebSocket）。先在火山控制台
  开通「大模型流式语音识别」资源并拿到 `VOLC_API_KEY`，再把 `providers.asr.chain` 改成
  `[volc-asr, local-asr]`；流式识别比本地 SenseVoice 多约 1 s 延迟，可在控制台随时切回。

## 2. 一条命令

滚动版本（`main` + `:latest`，适合先试）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

固定发布版本（生产推荐；脚本、compose 文件和镜像都锁在同一个 tag）：

```bash
curl -4 -fsSL https://raw.githubusercontent.com/yes999zc/rui/v0.2.2/deploy/install.sh \
  | RUI_REF=v0.2.2 RUI_IMAGE=ghcr.io/yes999zc/rui:0.2.2 sh
```

`RUI_REF` 决定脚本拉取的 `docker-compose.yml` / `config.example.yaml` 来自哪个 ref，
`RUI_IMAGE` 决定运行哪个镜像；两个一起固定，重装或回滚时才不会悄悄换版本。镜像拉取失败时，
源码回退也用同一个 `RUI_REPO` + `RUI_REF` 克隆/构建，并打印实际 ref 与 commit。

`install.sh` 做五件事（幂等：已有文件不覆盖）：

| 步骤 | 做什么 | 落在哪 |
|---|---|---|
| 1 | 拉 `docker-compose.yml` + `config.example.yaml`（参考件） | `~/rui/` |
| 2 | `setup` 写**生产模板**（provider 链、模型路径、playback 16k/1ch）+ 生成 `keywords.txt`（默认唤醒词），无设备/无密钥 | `~/rui/config/config.yaml`、`config/keywords.txt` |
| 3 | 生成 `.env`，含随机 device token | `~/rui/.env`（chmod 600） |
| 4 | 检查 ASR/VAD/KWS 三件套；任一缺失就 `fetch-models`（先从镜像 seed 复制 VAD/KWS，再下 SenseVoice） | `~/rui/data/models/` |
| 5 | `docker compose up -d` 并等 `/health` 通过；120 s 仍不健康则打印探针/日志命令并 `exit 1` | 容器 `rui` |

**重跑是安全的**：同一个 `RUI_HOME` 再跑一次时，如果 `rui` 容器的 compose project
`working_dir` 就是本目录，脚本直接 `docker compose up -d`（按需重建，并补齐缺失模型）；
如果容器属于其他目录/项目，脚本报出对方的 project 目录并退出，**不会停/删容器**；
0.1.x 的 `house-ear` 容器则报“旧安装，先停掉”，同样不自动处理。升级/换版本仍建议按 §7。

安装脚本里可用的环境变量（契约不变，只是补充说明）：

```sh
RUI_HOME=~/rui          # 安装目录
RUI_REPO=yes999zc/rui   # fork
RUI_REF=main                  # 分支/tag
RUI_IMAGE=ghcr.io/yes999zc/rui:0.2.2   # 生产建议固定 tag 或 digest
RUI_RAW=https://raw.githubusercontent.com/yes999zc/rui/main
                                    # 可选：raw 不通时换成镜像前缀，例如
                                    # https://ghproxy.net/https://raw.githubusercontent.com/yes999zc/rui/main
```

> **中国大陆网络**：脚本的 fetch 已加超时/重试并强制 IPv4；如果 `raw.githubusercontent.com`
> 仍不稳定，用 `RUI_RAW` 指到镜像前缀（注意有些代理会缓存旧文件，冷装前确认拿到的是 main）。
> 新镜像已内置 VAD/KWS（`/opt/seed-models`），`fetch-models` 先复制它们再联网，所以 Docker
> 冷装不再需要访问 GitHub release，只剩 hf-mirror 上的 SenseVoice ASR。自建镜像见 §3.3。

## 3. 冷装：到第一句回应

`install.sh` 跑完后，**不需要手改 YAML**。打开 `http://<nas-lan-ip>:8080/`，输入控制台
token，按 `/setup` 的 8 步向导走完即可：

`keys` → `models` → `prepare` → `params` → `wake` → `connect` → `test` → `verify`

| 步 | 用户做什么 | 写入哪里 |
|---|---|---|
| `keys` | 每种用途（LLM/ASR/TTS）选一个 provider，填它的凭据；TTS 选音色 | `data/secrets.yaml`（只写不读）；选中 provider 写成 `chain=[selected]` |
| `models` | 缺模型时按提示跑 `fetch-models`，再点“重新检查” | `data/models/` |
| `prepare` | 在音箱上装 open-xiaoai client，勾选确认（向导只给链接/步骤/回滚） | 无 |
| `params` | 填音箱地址、房间、唤醒词并保存 | `config/config.yaml` 的 `devices[]` |
| `wake` | 确认/修改 1–3 个唤醒词 | `config/keywords.txt` |
| `connect` | 复制 ws 地址与 device token 到 client | 无（只读展示） |
| `test` | 等音箱回连，点测试 | 无（只探测） |
| `verify` | 试一句，听到回答；可跳过 | 仅运行时证据 |

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
`~/rui/.env`（环境变量优先）。密钥只放 `~/rui/.env` 或控制台「设置→密钥」，
**不要写进 config.yaml**。容器里 `env_file: .env` 注入。要换 provider 时，向导里改选即可；
只有非向导部署才需要改 `providers.catalog` + 三条链，`config.example.yaml` 有完整写法。

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
docker run --rm --user "$(id -u):$(id -g)" -v ~/rui/data:/data \
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
docker run --rm --user "$(id -u):$(id -g)" -v ~/rui/data:/data \
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
RUI_IMAGE=ghcr.io/yes999zc/rui:0.2.2   # 安装脚本写入；固定版本，回滚时改这里
```

只填实际用到的即可。**没有讯飞 key 时**，在向导 `keys` 步把 TTS 选成 `volc_tts` 并填
`VOLC_API_KEY`；模板的 `xfyun_tts` 仍是 `chain[0]`，不会因为 `.env` 留空就自动跳过。
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
- 本机 OrbStack 构建的是 arm64；给 NAS 构建/发布用 CI 多架构，或在 x86 NAS 上
  `docker build --platform linux/amd64 ...`（慢，但可用）。构建镜像的国内镜像参数：

  ```bash
  docker build \
    --build-arg APT_MIRROR=mirrors.aliyun.com \
    --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
    --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    -t rui:dev .
  ```

## 8. 卷与备份

| 宿主机 | 容器内 | 内容 | 丢了会怎样 |
|---|---|---|---|
| `~/rui/config` | `/config` | `config.yaml`、`keywords.txt`（+ 可选 `lan_media.json`） | 要重配设备/provider/唤醒词 |
| `~/rui/data` | `/var/lib/rui` | `service.log`、`secrets.yaml`、`memory.json`、提醒/会话历史、音乐缓存 | 记忆/提醒/密钥消失，模型还在（在 `data/models`） |
| `~/rui/data/models` | `/data/models`（同一目录的第二个挂载点） | SenseVoice / VAD / KWS 模型 | ASR 重下 ~239 MB；VAD/KWS 从镜像 seed 恢复 |

备份只要 `config/` + `data/`（models 可重下）；恢复时把目录放回、`docker compose up -d`。
`config.yaml` 每次写盘都有时间戳备份（`config.yaml.bak.*`），改坏了用最近的备份覆盖。

## 9. 本地开发（不走容器）

```bash
git clone https://github.com/yes999zc/rui && cd rui
uv sync && uv run rui doctor
uv run rui serve
```

## 10. 已知缺口（P15 记录，本版 0.0.1-p15；部分已由 Lead 接）

- **`setup` 仍不写 devices**：这是有意的（不替用户猜设备）。模板已带 provider 链、模型路径、
  `keywords.txt`；正常冷装由 `/setup` 向导的 `params` 步写入 `devices[]`、`keys` 步写密钥，
  不需要手改 YAML。只有非向导部署才手工加 `devices[]` + 填 `.env`。
- **AI 之外的人工项**：真机接线（音箱 client 指向 NAS IP:4412、device token 一致）仍要人做；
  `docs/runbook.md` 的故障表覆盖了排查路径。
- **损坏的模型文件会让采集静默停摆**：无效 `silero_vad.onnx` 下 plane 不报错、
  `/api/v1/audio` 的 `frames_in` 一直是 0（实测，见 runbook §11.6）。换回有效模型后恢复；
  排查时先跑 `fetch-models` 重新拉模型。
- **音频面崩溃后的自愈已补**（`AudioPlaneSupervisor.watch()`，1 s 轮询 + 1→60 s 退避）：
  实测 kill plane 后 ~3 s 新 pid、`restarts=1`；旧的「必须手动 restart」说法已过时。
  长稳（反复崩溃的退避封顶）仍看 3 天观察。
