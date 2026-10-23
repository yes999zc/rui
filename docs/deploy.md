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

## 2. 一条命令

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/house-ear/main/deploy/install.sh | sh
```

`install.sh` 做五件事（幂等：已有文件不覆盖）：

| 步骤 | 做什么 | 落在哪 |
|---|---|---|
| 1 | 拉 `docker-compose.yml` + `config.example.yaml`（参考件） | `~/house-ear/` |
| 2 | `setup` 写 schema 默认配置（无设备、无 endpoint） | `~/house-ear/config/config.yaml` |
| 3 | 生成 `.env`，含随机 device token | `~/house-ear/.env`（chmod 600） |
| 4 | 下 SenseVoice + Silero VAD 到数据卷 | `~/house-ear/data/models/` |
| 5 | `docker compose up -d` 并等 `/health` 通过 | 容器 `house-ear` |

安装脚本里可用的环境变量（契约不变，只是补充说明）：

```sh
HOUSE_EAR_HOME=~/house-ear          # 安装目录
HOUSE_EAR_REPO=yes999zc/house-ear   # fork
HOUSE_EAR_REF=main                  # 分支/tag
HOUSE_EAR_IMAGE=ghcr.io/yes999zc/house-ear:0.0.1   # 生产建议固定 tag 或 digest
```

## 3. 冷装：到第一句回应

`setup` 写出的是**能启动但不能说话**的最小配置（没设备、没 provider endpoint、没唤醒
模型）。冷装验收前必须手工补完下面四块，全部改 `~/house-ear/config/config.yaml`；
`config.example.yaml` 是可直接照抄的完整样例。

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

`setup` 的默认 catalog 只有 `asr/tts/llm` 三个空 endpoint。可用样例（SiliconFlow 全链路；
讯飞 TTS 需要 `XFYUN_APPID / XFYUN_API_KEY / XFYUN_API_SECRET` 三个环境变量）：

```yaml
providers:
  catalog:
    local_asr:                          # 本地 ASR，链里排第一
      kind: sherpa
      model: /data/models/sensevoice
      threads: 2
    siliconflow_asr:
      kind: openai_compat
      base_url: https://api.siliconflow.cn/v1
      model: FunAudioLLM/SenseVoiceSmall
      api_key_env: HOUSE_EAR_ASR_KEY
    siliconflow_llm:
      kind: openai_compat
      base_url: https://api.siliconflow.cn/v1
      model: deepseek-ai/DeepSeek-V3   # 或 DeepSeek 官方 deepseek-flash
      api_key_env: HOUSE_EAR_LLM_KEY
    siliconflow_tts:
      kind: openai_compat
      base_url: https://api.siliconflow.cn/v1
      model: FunAudioLLM/CosyVoice2-0.5B
      voice: alex
      api_key_env: HOUSE_EAR_TTS_KEY
  asr:
    chain: [local_asr, siliconflow_asr]  # 云端是兜底，不是默认
  tts:
    chain: [siliconflow_tts]
  llm:
    chain: [siliconflow_llm]
```

密钥只放 `~/house-ear/.env`（或控制台「设置→密钥」写入 `data/secrets.yaml`，写后不可读回），
**不要写进 config.yaml**。容器里 `env_file: .env` 注入。

### 3.3 模型（三个路径，别混）

| 模型 | 怎么来 | 容器内路径 | 宿主机路径 |
|---|---|---|---|
| SenseVoice ASR（239 MB） | `install.sh` 自动 `fetch-models` | `/data/models/sensevoice` | `~/house-ear/data/models/sensevoice` |
| Silero VAD（2 MB） | 同上（`--vad-dest`） | `/data/models/silero_vad.onnx` | `~/house-ear/data/models/silero_vad.onnx` |
| KWS 唤醒模型（~6 MB） | **手动**，见下 | `/data/models/kws/<模型目录>` | `~/house-ear/data/models/kws/` |

compose 把 `./data:/var/lib/house-ear`，同时把 `./data/models:/data/models` 挂进容器：
数据卷负责持久化，`/data/models` 是文档和 `config.example.yaml` 一直用的路径。两者指向
同一个宿主机目录，删容器重建都不丢。

**唤醒模型（KWS）目前不在 `fetch-models` 里**（Lead 正在补，下一版会进自动安装；
在那之前按下面手动放）。手动步骤：

1. 从 sherpa-onnx 的 KWS 预训练模型页下载 `sherpa-onnx-kws-zipformer-zh-en-3M-*` 目录，
   国内网络用 `hf-mirror.com`；至少需要：
   `tokens.txt`、`encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx`、
   `decoder-epoch-13-avg-2-chunk-8-left-64.onnx`、`joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx`
   （文件名由 `audio/voice.kws_chunk` 决定，默认 chunk-8）。
2. 把目录放到 `~/house-ear/data/models/kws/`。
3. 写关键词文件，每行是模型 token 序列 + 阈值 + 显示文案，例如「你好小瑞」：

   ```text
   n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞
   ```

   行格式跟着模型自带的 `keywords.txt` 写；token 必须在该模型的词表里。把文件命名为
   `keywords.txt` 放模型目录，或在配置里显式指 `kws_keywords_file`。
4. 配置：

   ```yaml
   audio:
     voice:
       kws_enabled: true
       kws_model_dir: /data/models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20
       kws_keywords_file: /data/models/kws/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/keywords.txt
       vad_model_path: /data/models/silero_vad.onnx
   ```

没有 KWS 模型时保持 `kws_enabled: false`；此时服务在线、控制台可用，但不会被唤醒，
`doctor` 会提示 `wake_path`。

### 3.4 `.env`

```bash
HOUSE_EAR_LLM_KEY=...        # 对应 config 里的 api_key_env
HOUSE_EAR_ASR_KEY=...
HOUSE_EAR_TTS_KEY=...
HOUSE_EAR_HA_TOKEN=...       # 接 Home Assistant 时填
HOUSE_EAR_DEVICE_TOKEN=...   # install.sh 生成的，音箱 client 用同一个值
HOUSE_EAR_IMAGE=ghcr.io/yes999zc/house-ear:0.0.1   # 可选：固定版本
```

改完 `.env` / `config.yaml` 后：`docker compose up -d`（配置读盘，无需重建镜像）。

### 3.5 验收

```bash
docker compose ps                       # STATUS 应为 healthy
curl -s localhost:8080/health | python -m json.tool
curl -s localhost:8080/api/v1/doctor | python -m json.tool
docker compose logs --tail=100 house-ear
```

期望：`/health` 的 `status=ok`、`audio_plane=true`（配置了设备时）；`/api/v1/doctor`
`verdict=ok|degraded`。`degraded` 常见但可接受的原因：没设 `web.auth_token`（只在内网
问题不大）、音箱 SSH 22 探测不到（inbound 设备本来就不要求 SSH）、`music.enabled` 关闭。

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
  `HOUSE_EAR_IMAGE=ghcr.io/yes999zc/house-ear:<版本>`（或 `@sha256:<digest>`）。
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
| `~/house-ear/config` | `/config` | `config.yaml`（+ 可选 `lan_media.json`） | 要重配设备/provider |
| `~/house-ear/data` | `/var/lib/house-ear` | `service.log`、`secrets.yaml`、`memory.json`、提醒/会话历史、音乐缓存 | 记忆/提醒/密钥消失，模型还在（在 `data/models`） |
| `~/house-ear/data/models` | `/data/models`（同一目录的第二个挂载点） | SenseVoice / VAD / KWS 模型 | 重下 ~250 MB |

备份只要 `config/` + `data/`（models 可重下）；恢复时把目录放回、`docker compose up -d`。
`config.yaml` 每次写盘都有时间戳备份（`config.yaml.bak.*`），改坏了用最近的备份覆盖。

## 9. 本地开发（不走容器）

```bash
git clone https://github.com/yes999zc/house-ear && cd house-ear
uv sync && uv run house-ear doctor
uv run house-ear serve
```

## 10. 已知缺口（P15 记录，本版 0.0.1-p15；部分已由 Lead 接）

- **唤醒模型不在自动安装里**：`fetch-models` 只下 SenseVoice + VAD；KWS 模型和
  `keywords.txt` 要手工放（§3.3）。Lead 已接（下一版进 `fetch-models`/`setup` 默认配置），
  在那之前 P14 冷装仍需手工一步。
- **`setup` 默认配置不能直接说话**：本版没有设备/provider endpoint/KWS，必须按 §3 补配置。
  这是有意的（不替用户猜设备），Lead 正在补 model 路径与 provider endpoint，设备仍需手填。
- **音频面子进程崩溃不会自愈**：`AudioPlaneSupervisor` 只在启动时拉起一次；plane 死掉后
  控制面继续应答 `/health`（`audio_plane=false`，健康检查会变 unhealthy），需要
  `docker compose restart house-ear`。修复要改 `src/`（给 supervisor 加重启循环），
  归 Lead 决定。详见 `docs/runbook.md` 的自愈矩阵与 P15 实测数字。
- **损坏的模型文件会让采集静默停摆**：无效 `silero_vad.onnx` 下 plane 不报错、
  `/api/v1/audio` 的 `frames_in` 一直是 0（实测，见 runbook §11.6）。换回有效模型后恢复；
  排查时先跑 `fetch-models` 重新拉模型。
