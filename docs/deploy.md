# Deploy

**生产目标是 NAS 上的 Docker：一条命令冷装。** Mac 只是开发机（`:8091` 的开发服务和设备
指向 Mac 的调试链路都在 `docs/HANDOFF.md`）。生产形态是 NAS 上 `deploy/install.sh` 拉镜像、
写配置、起服务；`--network host`，默认 `:8080`，配置在 `~/house-ear/config`。

运维手册（起停、日志、换配置、换镜像、回滚、常见故障）在
[`docs/runbook.md`](runbook.md)。

## 1. 前置条件

- x86_64 或 arm64，4 核 / 4 GB 内存 / 2 GB 可用磁盘（首次要再留 ~250 MB 下模型）
- Docker + `docker compose`，允许 `--network host`（设备音频是长连接 socket）
- 音箱与 NAS 在同一 L2 网段；音箱端 client 指向 NAS 的 IP:4412
- **镜像架构**：GitHub Actions 发的是 `linux/amd64` + `linux/arm64` 多架构镜像（见
  `.github/workflows/docker-publish.yml`）。在 Mac（arm64）本地 `docker build` 出来的
  只有 arm64，**不能直接搬到 x86_64 NAS**：要么用 GHCR 镜像，要么在 NAS 上带
  `--platform linux/amd64` 构建。

### 控制台令牌（安全审计后必读）

### 火山豆包语音（可选）

- TTS：`.env` 里填 `VOLC_API_KEY`（控制台 API Key），把 `config.yaml` 的
  `providers.tts.chain` 改成 `[volc_tts, xfyun_tts]` 即启用；
  音色在控制台「外部资源 → volc_tts → 音色」选并「试听」，保存后重启服务生效。
- 额度卡片：`.env` 再填 `VOLC_ACCESS_KEY_ID` / `VOLC_SECRET_ACCESS_KEY`（只用于只读查询账户余额/用量）。
- ASR：项目已接入豆包流式语音识别 2.0（`volc_asr`，双向 WebSocket）。先在火山控制台
  开通「大模型流式语音识别」资源并拿到 `VOLC_API_KEY`，再把 `providers.asr.chain` 改成
  `[volc-asr, local-asr]`；流式识别比本地 SenseVoice 多约 1 s 延迟，可在控制台随时切回。

`install.sh` 里的 `house-ear setup` 会生成随机 `web.auth_token` 并打印一次：

```bash
docker compose logs house-ear | grep "console token"   # 或：
docker compose exec house-ear house-ear token
docker compose exec house-ear house-ear token --rotate  # 轮换
```

控制台没有 token 会返回 401（页面会提示输入 `X-Auth-Token`）。**只有在本机开发**才可以显式
`web.allow_anonymous: true` 关闭鉴权；生产模板默认关闭，且非 loopback + 空 token 会拒绝启动。

## 2. 一条命令

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/rui/main/deploy/install.sh | sh
```

`install.sh` 做五件事（幂等：已有文件不覆盖）：

| 步骤 | 做什么 | 落在哪 |
|---|---|---|
| 1 | 拉 `docker-compose.yml` + `config.example.yaml`（参考件） | `~/house-ear/` |
| 2 | `setup` 写**生产模板**（provider 链、模型路径、playback 16k/1ch）+ 生成 `keywords.txt`（默认唤醒词），无设备/无密钥 | `~/house-ear/config/config.yaml`、`config/keywords.txt` |
| 3 | 生成 `.env`，含随机 device token | `~/house-ear/.env`（chmod 600） |
| 4 | 下 SenseVoice + Silero VAD + KWS 唤醒模型（~272 MB）到数据卷 | `~/house-ear/data/models/` |
| 5 | `docker compose up -d` 并等 `/health` 通过 | 容器 `house-ear` |

安装脚本里可用的环境变量（契约不变，只是补充说明）：

```sh
HOUSE_EAR_HOME=~/house-ear          # 安装目录
HOUSE_EAR_REPO=yes999zc/rui   # fork
HOUSE_EAR_REF=main                  # 分支/tag
HOUSE_EAR_IMAGE=ghcr.io/yes999zc/rui:0.0.1   # 生产建议固定 tag 或 digest
```

## 3. 冷装：到第一句回应

`setup` 写出的是**生产模板**（`src/house_ear/config/template.yaml`）：provider 链、模型
路径、16 kHz 单声道播放、ack TTS 都已按生产值配好，并自动生成 `config/keywords.txt`。
冷装只剩三件事：**加 devices[]、填 `.env` 密钥、下模型**（`install.sh` 自动完成模型这一步）。
`config.example.yaml` 是同一份配置的带注释版本，可对照。

### 3.1 设备（`devices[]`）

```yaml
devices:
  - id: living
    adapter: xiaoai
    host: speaker.local       # 音箱的 LAN 名/IP；inbound 传输下只用于显示和 doctor 的 SSH 探测
    room: 客厅
    wake_words: ["你好小瑞"]
    volume: 0                 # 0 = 不接管音量，沿用原厂
    listen_port: 4412         # 音箱 client 回连的端口
    token_env: HOUSE_EAR_DEVICE_TOKEN
    led:
      enabled: false          # 灯环默认关；亮度按真机调，不照抄
```

### 3.2 provider（`providers.catalog` + 三条链）

`setup` 已经写入生产用的 provider 链，**正常不用改**：

```yaml
providers:
  asr: {chain: [local_asr, siliconflow_asr]}   # 本地优先，云端兜底
  tts: {chain: [xfyun_tts, volc_tts]}           # 讯飞优先，火山兜底（无硅基流动）
  llm: {chain: [deepseek_llm]}
```

需要做的只是把密钥填进 `.env`（对照各 provider 的 `api_key_env` / `app_id_env`）：

| 用途 | `.env` 变量 | 说明 |
|---|---|---|
| 本地 ASR | — | `fetch-models` 放模型，不需要 key；缺模型时走 `siliconflow_asr` |
| 云端 ASR 兜底 | `HOUSE_EAR_ASR_KEY` | SiliconFlow |
| TTS 主 | `XFYUN_APPID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET` | 讯飞 websocket TTS |
| TTS 兜底 | `VOLC_API_KEY` | 火山豆包 TTS 2.0（`volc_tts`） |
| LLM | `HOUSE_EAR_LLM_KEY` | DeepSeek（模板用 `deepseek-flash`） |
| HA | `HOUSE_EAR_HA_TOKEN` | 见 §5 |
| 搜索（可选） | `DEEPSEEK_API_KEY` | 模板里 `tools.search.enabled: false`，开了才需要 |

密钥只放 `~/house-ear/.env`（或控制台「设置→密钥」写入 `data/secrets.yaml`，写后不可读回），
**不要写进 config.yaml**。容器里 `env_file: .env` 注入。要换 provider（例如全用火山）
就改 `providers.catalog` + 三条链，`config.example.yaml` 有完整写法。

### 3.3 模型（`fetch-models` 一次装齐）

| 模型 | 怎么来 | 容器内路径 | 宿主机路径 |
|---|---|---|---|
| SenseVoice ASR（239 MB） | `install.sh` 自动 `fetch-models` | `/data/models/sensevoice` | `~/house-ear/data/models/sensevoice` |
| Silero VAD（~0.6 MB） | 同上（`--vad-dest`） | `/data/models/silero_vad.onnx` | `~/house-ear/data/models/silero_vad.onnx` |
| KWS 唤醒模型（32.9 MB 包） | 同上（`--kws-dest`，自动解包） | `/data/models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | `~/house-ear/data/models/kws/` |

compose 把 `./data:/var/lib/house-ear`，同时把 `./data/models:/data/models` 挂进容器：
数据卷负责持久化，`/data/models` 是模板和文档使用的路径。两者指向同一个宿主机目录，
删容器重建都不丢。手动重跑：

```bash
docker run --rm --user "$(id -u):$(id -g)" -v ~/house-ear/data:/data \
  ghcr.io/yes999zc/rui:<tag> fetch-models \
  --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx --kws-dest /data/models/kws
# 网络受限时可只装 ASR+VAD（唤醒词会失效）：加 --no-kws
```

**唤醒词**：`setup` 已按默认「你好小瑞」生成 `~/house-ear/config/keywords.txt`，内容形如
`n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞`（token、boost、阈值、显示文案）。改
`devices[].wake_words` 后必须重新生成，否则音箱还在听旧词：

```bash
docker run --rm -v ~/house-ear/config:/config ghcr.io/yes999zc/rui:<tag> \
  keywords --config /config/config.yaml
```

模板里 `audio.voice.kws_enabled: true`、`kws_model_dir` 指向上面目录、
`kws_keywords_file: {config_dir}/keywords.txt`。如果确实没装 KWS 模型，把
`kws_enabled` 设为 false；否则 plane 起不来 KWS，`doctor` 会报 `wake_path`。

### 3.4 `.env`

```bash
HOUSE_EAR_LLM_KEY=...        # DeepSeek（模板 llm 链）
HOUSE_EAR_ASR_KEY=...        # SiliconFlow（云端 ASR 兜底）
VOLC_API_KEY=...             # 火山豆包 TTS/ASR（模板 tts 兜底；ASR 可选）
XFYUN_APPID=...              # 讯飞 TTS（模板 tts 主）
XFYUN_API_KEY=...
XFYUN_API_SECRET=...
HOUSE_EAR_HA_TOKEN=...       # 接 Home Assistant 时填
HOUSE_EAR_DEVICE_TOKEN=...   # install.sh 生成的，音箱 client 用同一个值
HOUSE_EAR_IMAGE=ghcr.io/yes999zc/rui:0.0.1   # 可选：固定版本
```

只填模板链里实际用到的即可：没有讯飞 key 就留空（自动落到火山 `volc_tts`，需要
`VOLC_API_KEY`）；没有 SiliconFlow ASR key 时本地 ASR 顶上（需已跑 `fetch-models`）。
硅基流动 TTS 已从控制台与模板链下线（2026-10-23），旧的 `HOUSE_EAR_TTS_KEY` 不再使用。

改完 `.env` / `config.yaml` 后：`docker compose up -d`（配置读盘，无需重建镜像）。

### 3.5 验收

```bash
docker compose ps                       # STATUS 应为 healthy
curl -s localhost:8080/health | python -m json.tool
curl -s localhost:8080/api/v1/doctor | python -m json.tool
docker compose logs --tail=100 house-ear
```

期望：`/health` 的 `status=ok`、`audio_plane=true`（配置了设备时）；`/api/v1/doctor`
`verdict=ok|degraded`，`wake_path` PASS 表示模型与 `keywords.txt` 已就位。`degraded` 常见但
可接受的原因：没设 `web.auth_token`（只在内网问题不大）、音箱 SSH 22 探测不到（inbound
设备本来就不要求 SSH）、`music.enabled` 关闭。

## 4. 音乐：Navidrome

音乐默认 **关闭**（`music.enabled: false`）：NAS 不可达时工具只会反复说「曲库里没找到」，
不如不注册。启用需要三样东西：

1. Navidrome（或任意 Subsonic 兼容服务）的地址/账号，写成 JSON **放在卷上**（
   `config.example.yaml` 默认放数据卷：`/var/lib/house-ear/lan_media.json`，
   宿主机 `~/house-ear/data/lan_media.json`；放配置卷 `config/lan_media.json` 也可以，
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
     secrets_path: /var/lib/house-ear/lan_media.json   # 与上面放的位置一致
     cache_dir: /var/lib/house-ear/music_cache
     cache_tracks: 8
     queue_min_tracks: 2
     queue_batch: 20
     history_max: 60
   ```

   `music.enabled: true` 时 `secrets_path` 必须非空（配置校验会报错）。

3. 起歌需要 ffmpeg 做 16 kHz 单声道转码。**镜像已带 `ffmpeg`**（Dockerfile 用
   `mwader/static-ffmpeg:7.1` 多阶段拷贝静态二进制，约 100 MB，不拖 Debian 共享库），
   容器内自检：`docker compose exec house-ear ffmpeg -version`；缺了就是用了旧镜像。
   Navidrome 侧能直出 mp3 时也仍然会走本地转码，行为一致。

音乐相关的环境变量：**没有**。账号在 JSON 里，其余都是配置项。控制台「音乐」面板在
`music.enabled: true` 后才出现。

## 5. Home Assistant（MCP）

HA 走 **MCP over HTTP**（`ha.py`），不是直接调 HA REST。需要：

1. Home Assistant 安装 MCP Server 集成并开放 `http://<ha>:8123/api/mcp`。
2. 生成长期访问令牌，写进 `~/house-ear/.env`：

   ```bash
   HOUSE_EAR_HA_TOKEN=<long-lived token>
   ```

3. 配置：

   ```yaml
   tools:
     ha:
       base_url: http://homeassistant.local:8123   # 代码会自动补 /api/mcp
       token_env: HOUSE_EAR_HA_TOKEN
   ```

   `tools.mcp_servers[]` 是**通用 MCP 预留位**：schema 已接受
   `{name, transport: http|stdio, url, command, enabled}`，但当前版本只有 HA 一条实际接线，
   不要指望填进去就多出一套工具。HA 挂了只会让设备类工具降级，语音/音乐/本地工具不受影响。

## 6. 健康检查与日志

- compose 健康检查 `python /usr/local/bin/house-ear-healthcheck`：**从 config 读端口**
  （改 `web.port` 不会误报），`/health` 必须 200，且**配置了设备时 `audio_plane` 必须为
  true**——控制面活着但音频面死了，是这台机器最常见的「看起来在线、其实听不见」。
  `start_period: 90s`，之后每 30s 一次、3 次失败标记 unhealthy。
- Docker 不因为 unhealthy 自动重启容器；unhealthy 是**告警**，处理见 runbook。
- 容器 stdout/stderr：`json-file`，`max-size=10m`、`max-file=3`（30 MB 封顶）。
- 应用自己的 `data/service.log`：2 MB × 3 轮转，控制台「日志」页读它；
  容器内路径 `/var/lib/house-ear/service.log`。
- 查看：`docker compose logs -f --tail=200 house-ear`；按时间/关键字过滤：
  `docker compose logs --since 30m house-ear | grep -E "turn:|audio plane|error"`。

## 7. 镜像版本与回滚

- 不要只依赖 `latest`。生产在 `~/house-ear/.env` 里固定：
  `HOUSE_EAR_IMAGE=ghcr.io/yes999zc/rui:<版本>`（或 `@sha256:<digest>`）。
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
    -t house-ear:dev .
  ```

## 8. 卷与备份

| 宿主机 | 容器内 | 内容 | 丢了会怎样 |
|---|---|---|---|
| `~/house-ear/config` | `/config` | `config.yaml`、`keywords.txt`（+ 可选 `lan_media.json`） | 要重配设备/provider/唤醒词 |
| `~/house-ear/data` | `/var/lib/house-ear` | `service.log`、`secrets.yaml`、`memory.json`、提醒/会话历史、音乐缓存 | 记忆/提醒/密钥消失，模型还在（在 `data/models`） |
| `~/house-ear/data/models` | `/data/models`（同一目录的第二个挂载点） | SenseVoice / VAD / KWS 模型 | 重下 ~272 MB |

备份只要 `config/` + `data/`（models 可重下）；恢复时把目录放回、`docker compose up -d`。
`config.yaml` 每次写盘都有时间戳备份（`config.yaml.bak.*`），改坏了用最近的备份覆盖。

## 9. 本地开发（不走容器）

```bash
git clone https://github.com/yes999zc/rui && cd house-ear
uv sync && uv run house-ear doctor
uv run house-ear serve
```

## 10. 已知缺口（P15 记录，本版 0.0.1-p15；部分已由 Lead 接）

- **`setup` 仍不写 devices**：这是有意的（不替用户猜设备）。模板已带 provider 链、模型路径、
  `keywords.txt`，冷装只剩「加 devices[] + 填 .env」。
- **AI 之外的人工项**：真机接线（音箱 client 指向 NAS IP:4412、device token 一致）仍要人做；
  `docs/runbook.md` 的故障表覆盖了排查路径。
- **损坏的模型文件会让采集静默停摆**：无效 `silero_vad.onnx` 下 plane 不报错、
  `/api/v1/audio` 的 `frames_in` 一直是 0（实测，见 runbook §11.6）。换回有效模型后恢复；
  排查时先跑 `fetch-models` 重新拉模型。
- **音频面崩溃后的自愈已补**（`AudioPlaneSupervisor.watch()`，1 s 轮询 + 1→60 s 退避）：
  实测 kill plane 后 ~3 s 新 pid、`restarts=1`；旧的「必须手动 restart」说法已过时。
  长稳（反复崩溃的退避封顶）仍看 3 天观察。
