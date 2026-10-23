# Runbook — house-ear 运维手册

面向 NAS 上的生产容器。安装/配置的完整说明在 [`docs/deploy.md`](deploy.md)；
本文只回答「现在怎么起停、出问题看哪里、怎么换、怎么退」。

约定：`DIR=~/house-ear`（`HOUSE_EAR_HOME`），容器名 `house-ear`，镜像
`ghcr.io/yes999zc/house-ear:<tag>`，默认端口 `:8080`、设备通道 `:4412`。
所有命令在 `DIR` 下执行。

## 1. 这台机器上有什么

```
Docker (network_mode: host)
└─ house-ear 容器
   ├─ house-ear serve          控制面 :8080（HTTP API + 控制台）
   │  └─ house-ear audio ...   音频面子进程（AEC/KWS/VAD/播放，Unix socket）
   └─ 音箱 client 反向连入 :4412（长连接，不是容器暴露的端口）
卷（bind mount）:
  ./config -> /config            config.yaml（+ 可选的音乐凭据 lan_media.json）
  ./data   -> /var/lib/house-ear service.log / secrets.yaml / memory / 历史 / 缓存
  ./data/models -> /data/models  SenseVoice / VAD / KWS 模型
```

进程关系很重要：**控制面活着不等于音频面活着**。`/health` 里
`audio_plane: false` 就是「网页能开、音箱听不见」。配置了设备时健康检查会因此失败。

## 2. 起停

```bash
cd ~/house-ear

docker compose up -d          # 起（镜像不存在会先拉）
docker compose ps             # STATUS 里看 healthy/unhealthy
docker compose restart house-ear   # 只重启容器（配置改动后用这个）
docker compose stop           # 停（保留容器）
docker compose down           # 删容器（卷是 bind mount，数据还在）
docker compose up -d          # 用同一份 compose 重建
```

- **不要**用 `docker rm -f house-ear` 代替 `down`，也不要手工 `docker run`——compose
  里的日志轮转/健康检查/卷挂载会全部丢失。
- 升级/重建容器不会丢配置和数据（三个 bind mount）。
- 停机维护前正常 `stop`；`restart: unless-stopped` 下，**手动 stop 的容器不会在
  Docker 重启后自己回来**，维护完记得 `up -d`。断电不属于手动 stop（见 §7）。

## 3. 看日志

三个地方，用途不同：

```bash
# 1) 容器 stdout/stderr（uvicorn + 应用日志；json-file 10 MB x 3 轮转）
docker compose logs -f --tail=200 house-ear

# 2) 应用自己的文件日志（控制台「日志」页读同一份；2 MB x 3 轮转）
docker compose exec house-ear tail -n 200 /var/lib/house-ear/service.log

# 3) 健康检查最近一次的输出/状态（为什么 unhealthy）
docker inspect --format '{{json .State.Health}}' house-ear | python -m json.tool
```

常用过滤：

```bash
docker compose logs --since 30m house-ear | grep -E "turn:|audio plane|plane:|error|WARNING"
docker compose logs --since 60m house-ear | grep -E "xiaoai: speaker (connected|disconnected)"
```

日志轮转是双层的：容器层 `max-size=10m / max-file=3`（compose），应用层
`service.log` 2 MB × 3（`log_setup.py`）。两层都不需要手工清；如果 `data/` 还在涨，
先看是不是音乐缓存（`data/music-cache`）。

## 4. 改配置

配置文件只有一份真相：`~/house-ear/config/config.yaml`（原子写 + 时间戳备份
`config.yaml.bak.*`）。

| 改什么 | 怎么生效 |
|---|---|
| `.env`（密钥、`HOUSE_EAR_IMAGE`） | `docker compose up -d`（recreate 容器，env_file 才会重读） |
| `config.yaml`（设备、provider、音频、音乐） | `docker compose restart house-ear` |
| 控制台 → 设置里的 `assistant` / `session` | 立即生效（写回 config.yaml），无需重启 |
| 控制台 → 密钥（`secrets.yaml`） | 立即生效，API 不可读回 |
| 记忆确认/删除 | 立即生效（`data/memory.json`） |

改之前先备份（配置本身也会留备份，但手工备份最省事）：

```bash
cd ~/house-ear
cp config/config.yaml "config/config.yaml.bak.$(date +%Y%m%d-%H%M%S)"
$EDITOR config/config.yaml
docker compose restart house-ear
docker compose exec house-ear house-ear doctor
```

改坏了：`ls -t config/config.yaml.bak.* | head`，用最近的备份覆盖后重启。
**不要**直接改容器里的 `/config`——那是 bind mount，等价于改宿主机文件，但容易绕过备份。

## 5. 换镜像 / 升级

```bash
cd ~/house-ear
cp config/config.yaml "config/config.yaml.bak.upgrade.$(date +%Y%m%d-%H%M%S)"
docker compose pull
docker compose up -d
docker compose ps                       # 等 healthy
docker compose exec house-ear house-ear doctor
curl -s localhost:8080/health | python -m json.tool
```

- 生产把版本写死在 `.env`：`HOUSE_EAR_IMAGE=ghcr.io/yes999zc/house-ear:0.0.1`
  （或 `@sha256:<digest>`）。`latest` 是滚动默认，不是唯一推荐。
- 升级失败（容器起不来 / unhealthy）→ §6 回滚。
- 大版本升级后配置字段可能新增：旧配置会被迁移并保留原文件；**降级**到旧镜像前先恢复
  对应版本的配置备份，否则旧代码可能拒绝新字段（`config version` 比构建新会报错）。

## 6. 回滚

镜像回滚 + 配置回滚，两步都要做（只回镜像通常也够，但先把配置备份放好）：

```bash
cd ~/house-ear
# 1) 选回滚版本：上一个 tag（或 digest）
sed -i '' 's|^HOUSE_EAR_IMAGE=.*|HOUSE_EAR_IMAGE=ghcr.io/yes999zc/house-ear:0.0.0|' .env  # NAS 用 sed -i
# 2) 恢复上一版配置（如果升级后改过配置）
cp config/config.yaml.bak.upgrade.<时间戳> config/config.yaml
# 3) 重建并验收
docker compose up -d
docker compose ps && curl -s localhost:8080/health | python -m json.tool
docker compose exec house-ear house-ear doctor
```

回滚不会动 `data/`（记忆、提醒、日志、模型都在），所以回滚只影响代码和配置。

## 7. 自愈矩阵（谁负责让服务回来）

| 场景 | 现在的行为 | 自动恢复？ | 人工动作 |
|---|---|---|---|
| 容器主进程退出/被杀 | `restart: unless-stopped` 拉起 | ✅ 是 | 无；看日志找原因 |
| Docker daemon / 宿主机重启 | restart policy 恢复容器（未手动 stop 的） | ✅ 是（**NAS 断电未实测**） | 起来后跑 doctor |
| 音频面子进程崩溃 | 控制面继续活着，`/health` 仍 200 但 `audio_plane=false`；健康检查约 **80 s**（3 次 × 30 s）后转 unhealthy | ❌ **不会**——`AudioPlaneSupervisor` 只在启动时拉起一次（实测：崩溃后 90 s 内未自建） | `docker compose restart house-ear`（实测 1 s 恢复 HTTP、5 s healthy）；根治要给 supervisor 加重启循环（改 `src/`，P15 已记缺口） |
| 音箱断线/Wi-Fi 抖动 | inbound 适配器一直监听；client 重连后自动接上 | ✅ 是（适配器层） | 无 |
| 音箱换 IP / 重连风暴 | 新连接替换旧连接；旧连接被 close(1012) | ✅ 是 | 看 `xiaoai: speaker connected` 日志确认 |
| 配置改坏 | 进程可能起不来或状态异常 | ❌ | 用 `config.yaml.bak.*` 覆盖 → `up -d` |
| 升级后新镜像起不来 | 容器退出/重启循环 | 部分（restart 循环 = 一直失败） | §6 回滚 |
| HA 离线 | 设备工具降级说话术，其它功能正常 | ✅ 降级 | 恢复 HA 后自动重连（指数退避，最长 15 s） |
| Navidrome 离线 | 音乐工具报不可达，语音其它功能正常 | ✅ 降级 | 恢复 NAS 后直接用，无需重启 |
| 磁盘满 | 写配置/日志/模型失败 | ❌ | 清理 `data/music-cache`、旧日志、`config.yaml.bak.*` |

**健康检查只报警不重启**：Docker 不会因为 `unhealthy` 自动重启容器（自带 restart
policy 只看容器是否退出）。所以「audio plane 崩溃」这类**进程内**故障必须靠监控发现
（`docker compose ps` / 外部探针），再人工 `restart`。

## 8. 常见故障

| 症状 | 先查 | 处理 |
|---|---|---|
| 容器 `Restarting` / 起不来 | `docker compose logs --tail=100 house-ear` | 配置 YAML 语法/字段错：恢复备份；端口占用：改 `web.port`/`listen_port` |
| `unhealthy`，`/health` 还 200 | `docker inspect ... .State.Health`；`curl /health` 看 `audio_plane` | `audio_plane=false` → `docker compose restart house-ear`；若反复出现看 plane 日志（`plane:` 前缀） |
| 网页打不开 | `docker compose ps`；`ss -ltnp \| grep 8080`；`curl -v localhost:8080/health` | 端口被占：改配置；容器没起：`up -d` |
| 音箱不出声、不响应唤醒 | `/api/v1/devices`（`connected`）、`docker compose logs \| grep xiaoai`、`ss -ltnp \| grep 4412` | token 不一致（`.env` 的 `HOUSE_EAR_DEVICE_TOKEN` 与音箱 client 不同）→ 改一致后重启；4412 被旧进程占 → 停旧的；音箱 client 没指向 NAS IP |
| 能连上但永远不唤醒 | `house-ear doctor` 的 `wake_path`；配置 `kws_enabled` / `kws_model_dir` | KWS 模型或 keywords 缺失 → 按 deploy.md §3.3 放模型；临时验证可看控制台「设备」页 `kws` 状态 |
| `audio_plane=false` 且日志 `audio plane did not start` | `docker compose logs house-ear \| grep -A2 "audio plane"` | 有设备但 device token 空 → 填 `.env`（plane 会拒绝未认证连接）；`listen_port` 被占 → 改端口；没有 `devices[]` 时这是**正常状态**（只开控制台） |
| 音箱连着但 `frames_in` 一直 0、没有转写 | `/api/v1/audio` 的 `frames_in`；`vad_enabled` | 模型文件损坏/不匹配会让采集循环静默停摆（实测：换回有效 `silero_vad.onnx` 立即恢复）→ 重新 `fetch-models` 或换有效模型后重启 |
| 唤醒后没回答 | 日志 `turn:` / `asr`；provider 密钥 | `.env` 密钥空/错 → 填好 `up -d`；本地 ASR 模型缺失会走云 ASR（慢但可用） |
| 音乐没声音 | `/api/v1/music`；`music.enabled`；配置的 `music.secrets_path` 是否可读（`docker compose exec house-ear test -r <path>`）；`ffmpeg -version` | 枚举：enabled=false / secrets 文件不存在或权限不是 600 可读 / Navidrome 地址不可达 / 镜像太旧没有 ffmpeg |
| HA 指令没反应 | 日志 `grep "ha:"`；`tools.ha.base_url`；token | HA MCP Server 集成没开 / token 过期 / base_url 少了端口；HA 挂了不影响说话 |
| 控制台 401 | `web.auth_token` | 在「设置→密钥」或控制台登录框填同一个 token |
| 磁盘满 | `docker system df`；`du -sh data/*` | 清 `data/music-cache`、旧 `*.bak.*`、`docker image prune`（谨慎） |
| 时间/提醒不准 | 宿主机时区（`date`） | 容器用宿主机时钟；提醒存 `data/`，时区改动后核对一条 |

## 9. 备份与恢复

备份（关不关服务都行，配置是原子写）：

```bash
cd ~
tar czf house-ear-backup-$(date +%Y%m%d).tar.gz house-ear/config house-ear/data --exclude=house-ear/data/models
```

- `data/models` 可重下（~250 MB），不备份；
- `config/config.yaml` 有版本历史（`*.bak.*`），打包 config 即可；
- 恢复：解包回 `~/house-ear/`（保持目录名），`docker compose up -d`；
- 换机器：新机器先跑 `install.sh`，再覆盖 `config/` + `data/`（不含 models），
  最后 `fetch-models` 补模型（命令见 deploy.md）。

## 10. 安全

- 控制台默认无鉴权。只在内网用；要暴露就设 `web.auth_token`，并优先走反代 + TLS。
- `.env` 权限 600，含 provider key 与 device token；不要提交进 git（`.gitignore`/`.dockerignore` 都已排除）。
- 音乐凭据（`music.secrets_path`，默认放在数据卷 `/var/lib/house-ear/lan_media.json`，
  也可以放配置卷）同样 600；不进日志、不进配置。
- 设备通道 token 不一致会直接拒绝连接（fail closed，`allow_no_token` 默认 false）。
- 公开仓审计：配置文件里只允许 `*_env` 变量名，不允许明文 key/内网 IP/绝对路径。

## 11. P15 演练记录（2026-10-23）

> 环境：Mac / OrbStack（arm64），镜像 `house-ear:0.0.1-p15`（arm64），临时目录
> `/tmp/p15-install`，控制台 `:18080`，假音箱 `:14412`。真机（NAS + 音箱）不参与本轮；
> Lead 的 dev 服务（`:8091`/`:4399`）全程只读。数字均来自本轮实测。

### 11.1 构建与镜像

| 项 | 结果 |
|---|---|
| 命令 | `docker build --network host --build-arg VERSION=0.0.1-p15 --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn --build-arg PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -t house-ear:0.0.1-p15 .` |
| 冷构建耗时 | `--no-cache` 全量：**91.8 s**（npm install + npm run build + apt + pip 全从零） |
| 增量构建耗时 | **49.5 s**（基础层/npm 层命中缓存） |
| 镜像大小 | **428,815,328 B ≈ 409 MiB**（冷构建 428,915,937 B，差异只是构建参数元数据）；其中静态 ffmpeg 104,424,600 B ≈ 100 MiB |
| 平台 | 本机 arm64；生产 x86_64 由 CI 多架构构建（workflow 已写，尚未在 CI 实跑） |
| 镜像内检查 | `ffmpeg 7.1`（flac/mp3/opus 解码可用）；HEALTHCHECK 30 s/10 s/start 90 s/retries 3；label `org.opencontainers.image.version=0.0.1-p15` |

构建参数说明：本轮传国内镜像是因为本机 OrbStack 容器到 deb.debian.org 只有 ~64 KB/s、
到 pypi.org TLS 握手超时，而 tuna 实测 **2.6 MB/s / 642 KB/s**。Dockerfile 里这些
build arg 默认为空（官方源），正常网络的 CI/NAS 不需要传。

### 11.2 冷装链路

| 步骤 | 命令 | 实测 |
|---|---|---|
| `setup` 写配置 | `docker run --rm --user $(id -u):$(id -g) -v config:/config house-ear:0.0.1-p15 setup --config /config/config.yaml` | **0.49 s**；写出 schema 默认配置（无 devices、provider endpoint 空） |
| `fetch-models` 路径 | `docker run --rm -v data:/data … fetch-models --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx --base http://127.0.0.1:18081` | **0.45 s**；文件落在宿主机 `./data/models`，容器内 `/data/models/sensevoice/model.int8.onnx` 与 `/data/models/silero_vad.onnx` 可见。真 URL 下载未验（本机容器到 hf-mirror TLS 超时） |
| `serve` 启动 | `HOUSE_EAR_IMAGE=house-ear:p15 docker compose up -d` | `/health` **1 s** 可访问；Docker `healthy` **6 s** |
| `/health` | `curl :18080/health` | `status=ok, devices=1, device_errors=0, audio_plane=true, uptime=5.2 s` |
| `/api/v1/doctor` | `curl :18080/api/v1/doctor` + `docker compose exec house-ear house-ear doctor` | HTTP 200，`verdict=degraded`；PASS：arch / cpu_cores / disk_free / capture_layout / playback_layout / `aec_rtf=0.004` / wake_path / device ssh；WARN：`ram_gb=3.9`（OrbStack VM 限制）、`container_runtime=none`（镜像里没有 docker CLI，属预期）、`web_auth`（测试配置未设 token） |

补充：容器内 `ffmpeg -version` 正常输出；Navidrome 真实 `resolve` 见 11.6。

### 11.3 自愈

| 演练 | 命令 | 实测 |
|---|---|---|
| 服务进程退出 → 自动重启 | `docker exec house-ear python -c "import os,signal; os.kill(1, signal.SIGTERM)"` | 容器退出后被 `restart: unless-stopped` 拉起：`RestartCount 0→1`，HTTP **2 s**、healthy **6 s**、`uptime` 归零、`audio_plane=true` |
| 手动 `docker kill -9` | `docker kill --signal=SIGKILL house-ear` | 容器 `Exited (137)`，**没有**自动重启（`RestartCount=0`）——Docker 把显式 kill 当手动停机，restart policy 不生效；`docker start` 后恢复 |
| 计划重启 | `docker compose restart house-ear` | HTTP **1 s**、healthy **5–7 s**、`audio_plane=true` |
| 音频面崩溃 | 在容器内找到 `house_ear.audio.process` 的 pid 并 `SIGKILL` | 控制面仍 200；`audio_plane=false` **2 s** 内可见；日志 `audio plane reader stopped: IncompleteReadError`；**90 s 内不会自建**；健康检查 `FailingStreak 1→2→3`，约 **82 s** 转 `unhealthy`；`docker compose restart` 后 HTTP 1 s、healthy 5 s、plane 恢复 |
| 假音箱断线/重连 | `.venv/bin/python /tmp/p15-fake-speaker.py --port 14412 --token … --rounds 2 --seconds 2 --gap 2` | `speaker_connected` 两次 True/False 循环；plane `frames_in` 100→200→300、`frames_dropped=0`、`rtf=0.158`；适配器日志两次 connected/disconnected（累计 100/200 帧） |
| NAS 断电 | — | **未验**：没有 NAS 停机窗口 |

### 11.4 日志轮转

| 项 | 实测 |
|---|---|
| compose（主容器） | `docker inspect`：`{"Type":"json-file","Config":{"max-size":"10m","max-file":"3"}}` |
| 轮转有效性（独立容器，同参数缩小） | 写 3000 行、`max-size=2k`/`max-file=2`：`docker logs` 只剩 **30 行**（首行 line 2970、末行 2999），总量被 2k×2 封顶 → driver 轮转生效 |
| 应用文件日志 | `data/service.log` 存在（11.5 KB）；2 MB × 3 由 `RotatingFileHandler` 设置，未强制写满 2 MB 验证 |
| 健康检查输出 | 正常：`ok: uptime=4.9s devices=1 audio_plane=True`；plane 死后：`unhealthy: audio plane not running (1 device(s) configured); ...`（`FailingStreak` 最终 20） |

### 11.5 卷持久化

| 项 | 实测 |
|---|---|
| config / data / models 三类 | 三处各写 `keep.marker` + 模型文件；`docker compose down`（删容器）→ `up -d` 后全部仍在 |
| 模型路径映射 | 宿主机 `./data/models/sensevoice/...` 在容器内 `/data/models/sensevoice/...` 可见；`/var/lib/house-ear/models` 是同一宿主机目录的第二个挂载点（预期） |
| 重建耗时 | `down` + `up` 后 HTTP 1 s、healthy 6 s；配置里的设备/端口原样生效 |

### 11.6 本轮实测发现（需后续处理，已报 Lead）

1. **音频面不会自愈**（数据见 11.3）：`AudioPlaneSupervisor` 只在启动时拉起一次。Docker 不会因 unhealthy 自动重启容器，需要在 `src/` 加看门狗，或运维侧接外部探针。
2. **坏 VAD 模型文件会让采集静默停摆**：把 `silero_vad.onnx` 换成随机字节后，plane 不报错、`frames_in` 一直 0；换回有效模型立即恢复（100 帧、rtf 0.158）。建议模型加载做校验/超时，并把失败打进日志。
3. **设备有配置但 token 空时的确切报错**：plane 进程 `RuntimeError: HOUSE_EAR_DEVICE_TOKEN is empty: refusing to accept an unauthenticated speaker connection`；控制面记录 `WARNING … audio plane did not start: audio plane refused: HOUSE_EAR_DEVICE_TOKEN is empty: …`；`/health` 仍 200（`devices=1, audio_plane=false`），healthcheck 按设计转 unhealthy。
4. **无设备的默认配置是合法状态**：`setup` 原样启动——`/health` ok、`audio_plane=false`、healthcheck healthy、`doctor` degraded（`wake_path` 等）；日志 `audio plane did not start: no device this build can drive`。
5. **Navidrome + ffmpeg 真机链路（容器内）**：真实 `resolve` 成功——`search: 2 track(s)`，输出 **8,581,722 B / 16 kHz / mono / s16 / 268.2 s**；ffmpeg 对其中一首 flac 打了 `invalid sync code / decode_frame() failed` 警告，但退出码 0、音频完整（疑为源文件问题，未深究）。说明镜像里的 ffmpeg 可以工作。

## 12. 未验项与原因

| 项 | 原因 | 影响 |
|---|---|---|
| NAS 断电后全链路自起 | 没有 NAS 停机窗口；本机验了「进程退出→自动重启」和「手动 restart」 | 结论依赖 Docker daemon 开机自启 + restart policy，属推断 |
| 真音箱重连时序（Wi-Fi 抖动、换 IP、耗时） | 本轮不动真机（声音资源归 Lead） | 用假音箱验了协议层 connected/disconnected；真机时序未测 |
| 3 天长稳观察 | P15 时间窗不足 | 启动/自愈已验，长稳后续看 |
| x86_64 NAS 上运行 | 本机是 arm64；CI 多架构 workflow 未实跑 | 首次 NAS 冷装确认 |
| 真实模型下载（hf-mirror） | 本机容器到 hf-mirror TLS 握手超时 | 用本地 HTTP 假镜像验证了 `fetch-models` 的路径逻辑；真下载在 NAS 网络验证 |
| 容器内音乐真机出声 | 需要真实音箱（声音归 Lead） | resolve/转码已验，播放未验 |
| KWS 唤醒模型自动安装 | `fetch-models` 只下 SenseVoice+VAD；KWS 需手放（deploy.md §3.3），Lead 已接 | 冷装到「能唤醒」仍有手工步骤 |
