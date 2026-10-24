# Runbook — rui 运维手册

面向 NAS 上的生产容器。安装/配置的完整说明在 [`docs/deploy.md`](deploy.md)；
本文只回答「现在怎么起停、出问题看哪里、怎么换、怎么退」。

约定：`DIR=~/rui`（`RUI_HOME`），容器名 `rui`，镜像
`ghcr.io/yes999zc/rui:<tag>`，默认端口 `:8080`、设备通道 `:4412`。
所有命令在 `DIR` 下执行。

## 1. 这台机器上有什么

```
Docker (network_mode: host)
└─ rui 容器
   ├─ rui serve          控制面 :8080（HTTP API + 控制台）
   │  └─ rui audio ...   音频面子进程（AEC/KWS/VAD/播放，Unix socket）
   └─ 音箱 client 反向连入 :4412（长连接，不是容器暴露的端口）
卷（bind mount）:
  ./config -> /config            config.yaml（+ 可选的音乐凭据 lan_media.json）
  ./data   -> /var/lib/rui service.log / secrets.yaml / memory / 历史 / 缓存
  ./data/models -> /data/models  SenseVoice / VAD / KWS 模型
  状态（在 data 卷内）: /var/lib/rui/state/audio.sock（音频面 IPC）
                        /var/lib/rui/state/channels（channel gateway）
```

容器以 **uid/gid 1000**（用户 `rui`）运行；`/app` 对运行用户只读，只有上面两个卷可写。
`./config`、`./data` 的宿主机属主必须是 `1000:1000`（或 compose 的 `RUI_UID`/`RUI_GID`）。
旧镜像的 socket 在 `/root/.local/state/rui`，升级后不再使用，也不需要迁移（重新配对即可）。

进程关系很重要：**控制面活着不等于音频面活着**。`/health` 里
`audio_plane: false` 就是「网页能开、音箱听不见」。配置了设备时健康检查会因此失败。

## 2. 起停

```bash
cd ~/rui

docker compose up -d          # 起（镜像不存在会先拉）
docker compose ps             # STATUS 里看 healthy/unhealthy
docker compose restart rui   # 只重启容器（配置改动后用这个）
docker compose stop           # 停（保留容器）
docker compose down           # 删容器（卷是 bind mount，数据还在）
docker compose up -d          # 用同一份 compose 重建
```

- **不要**用 `docker rm -f rui` 代替 `down`，也不要手工 `docker run`——compose
  里的日志轮转/健康检查/卷挂载会全部丢失。
- 升级/重建容器不会丢配置和数据（三个 bind mount）。
- 停机维护前正常 `stop`；`restart: unless-stopped` 下，**手动 stop 的容器不会在
  Docker 重启后自己回来**，维护完记得 `up -d`。断电不属于手动 stop（见 §7）。

## 3. 看日志

三个地方，用途不同：

```bash
# 1) 容器 stdout/stderr（uvicorn + 应用日志；json-file 10 MB x 3 轮转）
docker compose logs -f --tail=200 rui

# 2) 应用自己的文件日志（控制台「日志」页读同一份；2 MB x 3 轮转）
docker compose exec rui tail -n 200 /var/lib/rui/service.log

# 3) 健康检查最近一次的输出/状态（为什么 unhealthy）
docker inspect --format '{{json .State.Health}}' rui | python -m json.tool
```

常用过滤：

```bash
docker compose logs --since 30m rui | grep -E "turn:|audio plane|plane:|error|WARNING"
docker compose logs --since 60m rui | grep -E "xiaoai: speaker (connected|disconnected)"
# 音频面自愈（看门狗）
docker compose logs --since 60m rui | grep -E "audio plane (is down|restarted|restart failed)"
```

日志轮转是双层的：容器层 `max-size=10m / max-file=3`（compose），应用层
`service.log` 2 MB × 3（`log_setup.py`）。两层都不需要手工清；如果 `data/` 还在涨，
先看是不是音乐缓存（`data/music-cache`）。

## 4. 改配置

配置文件只有一份真相：`~/rui/config/config.yaml`（原子写 + 时间戳备份
`config.yaml.bak.*`）。

| 改什么 | 怎么生效 |
|---|---|
| `.env`（密钥、`RUI_IMAGE`） | `docker compose up -d`（recreate 容器，env_file 才会重读） |
| `config.yaml`（设备、provider、音频、音乐） | `docker compose restart rui` |
| 控制台 → 设置里的 `assistant` / `session` | 立即生效（写回 config.yaml），无需重启 |
| 控制台 → 密钥（`secrets.yaml`） | 立即生效，API 不可读回 |
| 记忆确认/删除 | 立即生效（`data/memory.json`） |

改之前先备份（配置本身也会留备份，但手工备份最省事）：

```bash
cd ~/rui
cp config/config.yaml "config/config.yaml.bak.$(date +%Y%m%d-%H%M%S)"
$EDITOR config/config.yaml
docker compose restart rui
docker compose exec rui rui doctor
```

改坏了：`ls -t config/config.yaml.bak.* | head`，用最近的备份覆盖后重启。
**不要**直接改容器里的 `/config`——那是 bind mount，等价于改宿主机文件，但容易绕过备份。

## 5. 换镜像 / 升级

```bash
cd ~/rui
cp config/config.yaml "config/config.yaml.bak.upgrade.$(date +%Y%m%d-%H%M%S)"
docker compose pull
docker compose up -d
docker compose ps                       # 等 healthy
docker compose exec rui rui doctor
curl -s localhost:8080/health | python -m json.tool
```

- **首次升级到非 root 镜像（0.3.x 起）**：旧安装的 `config/`、`data/` 是 root 所有，
  非 root 容器无法读写；`up -d` 后会重启循环或 healthcheck 报 `cannot read config`。
  一次性迁移（只改属主，不改内容）：

  ```bash
  cd ~/rui
  scripts/backup.sh --out ~/rui-backups        # 或至少备份 config/ 和 data/
  sudo chown -R 1000:1000 config data
  docker compose pull && docker compose up -d --force-recreate
  docker compose exec -T rui rui-healthcheck   # 探针也以 uid 1000 运行
  ```

  重跑 `deploy/install.sh` 会自动 chown 并补 `.env` 的 `RUI_UID`/`RUI_GID`；边界与回滚见
  [security.md](security.md)。1000 被占用时用 `RUI_UID`/`RUI_GID` 覆盖，compose 会替换
  镜像默认 `USER`（无需重建镜像），但 `config/`、`data/` 必须 chown 成同一对身份。
  `install.sh` 保留已有 compose 文件（不覆盖自定义）；旧文件也能非 root 运行（镜像
  `USER rui`），但 `read_only`/`tmpfs`/`cap_drop` 等加固要手工合并新 compose 才会生效。
- 生产把版本写死在 `.env`：`RUI_IMAGE=ghcr.io/yes999zc/rui:0.3.0`
  （或 `@sha256:<digest>`）。`latest` 是滚动默认，不是唯一推荐。
  `install.sh` 会把安装时用的 image 写进生成的 `.env`；**手工装**时也要写，否则之后
  `docker compose up -d` 会回落到 `latest` 并尝试拉取（实测：本地 rehearsal 镜像没写
  `.env`，`up -d` 直接去拉 ghcr，TLS 超时失败）。
- 升级失败（容器起不来 / unhealthy）→ §6 回滚。
- 大版本升级后配置字段可能新增：旧配置会被迁移并保留原文件；**降级**到旧镜像前先恢复
  对应版本的配置备份，否则旧代码可能拒绝新字段（`config version` 比构建新会报错）。

## 6. 回滚

镜像回滚 + 配置回滚，两步都要做（只回镜像通常也够，但先把配置备份放好）：

```bash
cd ~/rui
# 1) 选回滚版本：上一个 tag（或 digest）
sed -i '' 's|^RUI_IMAGE=.*|RUI_IMAGE=ghcr.io/yes999zc/rui:0.0.0|' .env  # NAS 用 sed -i
# 2) 恢复上一版配置（如果升级后改过配置）
cp config/config.yaml.bak.upgrade.<时间戳> config/config.yaml
# 3) 重建并验收
docker compose up -d
docker compose ps && curl -s localhost:8080/health | python -m json.tool
docker compose exec rui rui doctor
```

回滚不会动 `data/`（记忆、提醒、日志、模型都在），所以回滚只影响代码和配置。

回滚到 root 镜像（0.3.x 之前）时，容器身份也要一起改：`.env` 里加
`RUI_UID=0`、`RUI_GID=0`（compose 的 `user:` 会覆盖镜像默认值），或临时把
`docker-compose.yml` 的 `user:` 改成 `"0:0"`。数据卷**不需要** chown 回 root；
root 可以读写 1000 所有的文件。继续用 `1000:1000` 跑旧镜像也可以（旧镜像没有
`USER` 指令，compose 仍会覆盖），只要旧代码不依赖 `/root/.local/state`。

## 7. 自愈矩阵（谁负责让服务回来）

| 场景 | 现在的行为 | 自动恢复？ | 人工动作 |
|---|---|---|---|
| 容器主进程退出/被杀 | `restart: unless-stopped` 拉起 | ✅ 是 | 无；看日志找原因 |
| Docker daemon / 宿主机重启 | restart policy 恢复容器（未手动 stop 的） | ✅ 是（**NAS 断电未实测**） | 起来后跑 doctor |
| 音频面子进程崩溃 | `AudioPlaneSupervisor.watch()`（1 s 轮询；失败退避 1→2→4→8→16→30→60 s）拉起新进程，回调随新客户端重绑 | ✅ 是——实测 kill 后 **2.0 s** 出现新 pid、`/api/v1/audio.restarts=1`；`/health` 全程 200、`audio_plane=true`，容器保持 healthy | 无；看 `audio plane restarted (#n, pid=…)`。反复崩溃先查模型/token/`listen_port` |
| 音箱断线/Wi-Fi 抖动 | inbound 适配器一直监听；client 重连后自动接上 | ✅ 是（适配器层） | 无 |
| 音箱换 IP / 重连风暴 | 新连接替换旧连接；旧连接被 close(1012) | ✅ 是 | 看 `xiaoai: speaker connected` 日志确认 |
| 配置改坏 | 进程可能起不来或状态异常 | ❌ | 用 `config.yaml.bak.*` 覆盖 → `up -d` |
| 升级后新镜像起不来 | 容器退出/重启循环 | 部分（restart 循环 = 一直失败） | §6 回滚 |
| HA 离线 | 设备工具降级说话术，其它功能正常 | ✅ 降级 | 恢复 HA 后自动重连（指数退避，最长 15 s） |
| Navidrome 离线 | 音乐工具报不可达，语音其它功能正常 | ✅ 降级 | 恢复 NAS 后直接用，无需重启 |
| 磁盘满 | 写配置/日志/模型失败 | ❌ | 清理 `data/music-cache`、旧日志、`config.yaml.bak.*` |

**健康检查与看门狗分工**：Docker 不会因为 `unhealthy` 自动重启容器（restart policy 只看
容器是否退出）；容器**内**的音频面崩溃由 `AudioPlaneSupervisor.watch()` 负责重建（实测
2 s 级），健康检查只是告警层。主进程本身退出才走 Docker 的 restart policy。

## 8. 常见故障

| 症状 | 先查 | 处理 |
|---|---|---|
| 容器 `Restarting` / 起不来 | `docker compose logs --tail=100 rui` | 配置 YAML 语法/字段错：恢复备份；端口占用：改 `web.port`/`listen_port` |
| `unhealthy`，`/health` 还 200 | `docker inspect ... .State.Health`；`/api/v1/audio` 的 `restarts`；`docker compose logs \| grep "audio plane"` | 音频面崩溃由看门狗自动重建（约 2 s，`restarts` +1），一般无需人工；若 `restarts` 持续增长，查退避日志和 plane 错误（模型/token/端口） |
| 网页打不开 | `docker compose ps`；`ss -ltnp \| grep 8080`；`curl -v localhost:8080/health` | 端口被占：改配置；容器没起：`up -d` |
| 音箱不出声、不响应唤醒 | `/api/v1/devices`（`connected`）、`docker compose logs \| grep xiaoai`、`ss -ltnp \| grep 4412` | token 不一致（`.env` 的 `RUI_DEVICE_TOKEN` 与音箱 client 不同）→ 改一致后重启；4412 被旧进程占 → 停旧的；音箱 client 没指向 NAS IP |
| 能连上但永远不唤醒 | `rui doctor` 的 `wake_runtime`（import/onnxruntime）和 `wake_path`（模型路径）；`kws_enabled` / `kws_model_dir`；`config/keywords.txt`；日志 `kws off:` / `vad off:` | 模型由 `fetch-models` 自动装（KWS 32.9 MB，`--kws-dest`）；`setup` 已生成 keywords；改过 `devices[].wake_words` 后跑 `rui keywords --config /config/config.yaml` 重生成；日志写 `failed to load: libonnxruntime.so...` 时按 §14 处理 |
| `audio_plane=false` 且日志 `audio plane did not start` | `docker compose logs rui \| grep -A2 "audio plane"` | 有设备但 device token 空 → 填 `.env`（plane 会拒绝未认证连接）；`listen_port` 被占 → 改端口；没有 `devices[]` 时这是**正常状态**（只开控制台）。看门狗只重建「已经起来过」的进程，起不来的原因仍看这条日志 |
| 音频面反复重启、`restarts` 持续增长 | `/api/v1/audio` 的 `restarts`；日志 `audio plane restart failed`、`restarting in N s` | 退避到 60 s 封顶；按 `audio plane did not start` 那条查根因（token/端口/模型），修好后看门狗自动恢复，无需重启容器 |
| 音箱连着但 `frames_in` 一直 0、没有转写 | `/api/v1/audio` 的 `frames_in`；`vad_enabled` | 模型文件损坏/不匹配会让采集循环静默停摆（实测：换回有效 `silero_vad.onnx` 立即恢复）→ 重新 `fetch-models` 或换有效模型后重启 |
| 唤醒后没回答 | 日志 `turn:` / `asr`；provider 密钥 | `.env` 密钥空/错 → 填好 `up -d`；本地 ASR 模型缺失会走云 ASR（慢但可用） |
| 音乐没声音 | `/api/v1/music`；`music.enabled`；配置的 `music.secrets_path` 是否可读（`docker compose exec rui test -r <path>`）；`ffmpeg -version` | 枚举：enabled=false / secrets 文件不存在或权限不是 600 可读 / Navidrome 地址不可达 / 镜像太旧没有 ffmpeg |
| HA 指令没反应 | 日志 `grep "ha:"`；`tools.ha.base_url`；token | HA MCP Server 集成没开 / token 过期 / base_url 少了端口；HA 挂了不影响说话 |
| 控制台 401 | `web.auth_token` | 在「设置→密钥」或控制台登录框填同一个 token |
| 磁盘满 | `docker system df`；`du -sh data/*` | 清 `data/music-cache`、旧 `*.bak.*`、`docker image prune`（谨慎） |
| 时间/提醒不准 | 宿主机时区（`date`） | 容器用宿主机时钟；提醒存 `data/`，时区改动后核对一条 |

## 9. 备份与恢复

用仓库里的脚本，不要手写 tar 命令（手工 tar 容易把模型打进去、漏 `.env`、或忘 sha256）。
`deploy/install.sh` 会把 `scripts/backup.sh`、`scripts/restore.sh` 和验收记录脚本
`scripts/acceptance-v1.py` 拉到 `~/rui/scripts/`；老安装或手工部署可从仓库补齐：

```bash
mkdir -p ~/rui/scripts
for f in backup.sh restore.sh acceptance-v1.py; do
  curl -fsSL "https://raw.githubusercontent.com/<repo>/main/scripts/$f" \
    -o ~/rui/scripts/$f && chmod 755 ~/rui/scripts/$f
done
```

```bash
cd ~/rui
scripts/backup.sh --out ~/rui-backups
# 可选：
#   --with-models   连 data/models（~272 MB）一起备份；默认排除，可重下
#   --no-env        不备份 .env（默认包含；归档 0600，脚本只打印“included”不打印值）
#   --home DIR      非默认安装目录
```

脚本行为：

- 归档 `rui-backup-<UTC时间>.tar.gz`，**0600**；旁边写 `.manifest.json`
  （backup_format、时间、config_version、镜像 ref/digest/version、包含项、排除项、文件数）
  和 `.sha256`；
- 只读 `--home` 下的 `config/`、`data/`、`.env`、`docker-compose.yml`；
- 默认排除 `data/models`（可 `fetch-models` 重下）；`.env` 与音乐凭据包含在归档里，
  所以归档/备份目录必须当密钥对待（`chmod 700` 目录，0600 文件）。

恢复：

```bash
# 先演练：恢复到临时目录，绝不碰生产目录
scripts/restore.sh ~/rui-backups/<archive>.tar.gz --home /tmp/rui-restore-smoke
diff -r ~/rui/config /tmp/rui-restore-smoke/config   # 允许 .bak 差异

# 真恢复（会先把旧树移走，不删除）：
cd ~/rui
docker compose stop
scripts/restore.sh ~/rui-backups/<archive>.tar.gz --home ~/rui --force
docker compose up -d
docker compose ps                         # healthy
docker compose exec -T rui rui doctor
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
```

- 目标目录非空时 restore **拒绝执行**（exit 4），除非 `--force`；
- `--force` 会把旧 `config/ data/ .env docker-compose.yml` 改名为 `*.pre-restore-<ts>`，
  **不会删除**；恢复错了就把它们移回去；
- 脚本会校验 `.sha256`（有的话）、拒绝绝对路径/`..` 条目、先解到临时目录再落位；
- 恢复后权限统一收紧到 `go-rwx`（密钥/历史/配置 0600）；
- 换机器：新机器先跑 `install.sh`，再 `restore.sh --force`，最后
  `fetch-models --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx
  --kws-dest /data/models/kws` 补模型。

### 9.1 配置恢复（last-good / defaults）

服务启动时会用 `config/recovery.py`：配置读不出来时**不崩溃**，而是把坏文件保留为
`config.yaml.failed-<ts>`，尽量用 `config.last-good.yaml` 启动（没有则 schema defaults），
并在 `data/config-recovery.json` 写标记；控制台会显示 banner。

```bash
# 看当前恢复状态
curl -s -H "X-Auth-Token: $RUI_TOKEN" \
  http://127.0.0.1:8080/api/v1/config/recovery | python3 -m json.tool

# 用最近一次成功启动时的快照覆盖当前配置（需重启服务）
docker compose exec -T rui rui config restore --last-good
docker compose restart rui

# 回到全新模板（会丢 devices/providers 配置，慎用；不删密钥/模型）
docker compose exec -T rui rui config restore --defaults
docker compose restart rui
```

- 每次成功启动会自动刷新 `config.last-good.yaml`（见 `app.py: remember_last_good`）；
- 手改坏配置前先 `scripts/backup.sh`，这是比 last-good 更完整的回退；
- 恢复后至少核对：`/health`、`doctor`、设备在线、音乐/HA 工具数。

### 9.2 备份节奏

- 升级/改配置/验收实验前：各做一次 `backup.sh`；
- 日常：每周或每次改完配置后一次即可；保留最近 3–5 份，旧归档可删（`data/models`
  已排除，所以每份通常只有几 MB / 取决于历史与缓存）；
- 备份目录不要放进 `data/`（会被再次打包）；放在 NAS 独立目录或另做异地副本。

## 10. 安全

- **容器非 root**：镜像以 uid/gid 1000 运行，`/app` 只读，根文件系统只读（`/tmp` 为
  256 MB tmpfs），持久可写的只有 `config/` 和 `data/`；`cap_drop: ALL` +
  `no-new-privileges`，没有 gosu/提权入口。卷属主见 §5 的迁移命令；状态路径
  （`/var/lib/rui/state`）与回滚见 [security.md](security.md)。
- 控制台默认无鉴权。只在内网用；要暴露就设 `web.auth_token`，并优先走反代 + TLS。
- `.env` 权限 600，含 provider key 与 device token；不要提交进 git（`.gitignore`/`.dockerignore` 都已排除）。
- 音乐凭据（`music.secrets_path`，默认放在数据卷 `/var/lib/rui/lan_media.json`，
  也可以放配置卷）同样 600；不进日志、不进配置。
- 设备通道 token 不一致会直接拒绝连接（fail closed，`allow_no_token` 默认 false）。
- 公开仓审计：配置文件里只允许 `*_env` 变量名，不允许明文 key/内网 IP/绝对路径。
- 出站 SSRF：HA / 通用 MCP / Navidrome / 联网搜索的用户可配 URL 由 `security.outbound`
  统一校验（云元数据、链路本地、组播、保留地址永封；默认放内网、默认禁回环）。
  放行自建服务、关闭策略和 DNS rebinding 等残余风险见 [security.md](security.md)。

## 11. P15/P15b 演练记录（2026-10-23）

> 环境：Mac / OrbStack（arm64）。两代镜像：`rui:0.0.1-p15`（P15 首轮）和
> `rui:rehearsal`（Lead 的 setup 模板 + KWS 自动安装 + 看门狗之后重建）。临时目录
> `/tmp/p15-install`（P15）与 `/tmp/p15-cold/run2`（冷装排练）；假音箱 `:14412`。真机
> （NAS + 音箱）不参与；Lead 的 dev 服务（`:8091`/`:4399`）全程只读。数字均为本轮实测。

### 11.1 构建与镜像

| 项 | 结果 |
|---|---|
| 命令 | `docker build --network host --build-arg VERSION=rehearsal --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn --build-arg PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -t rui:rehearsal .` |
| 冷构建耗时（P15 首轮，`--no-cache`） | **91.8 s**（npm install + npm run build + apt + pip 全从零） |
| 增量构建耗时 | **40.5 s**（P15b：src 变更触发 pip 重装，其余层命中缓存） |
| 镜像大小 | P15b rehearsal **428,964,012 B ≈ 409 MiB**；P15 首轮 428,815,328 B（差异只在构建参数元数据）。静态 ffmpeg 自身 104,424,600 B ≈ 100 MiB |
| 平台 | 本机 arm64；生产 x86_64 由 CI 多架构构建（workflow 已写，尚未在 CI 实跑） |
| 镜像内检查 | `rui.config/template.yaml` **4804 B** 可读；`ffmpeg 7.1`（flac/mp3/opus）；HEALTHCHECK 30 s/10 s/start 90 s/retries 3；label `version=rehearsal` |

构建参数说明：本机 OrbStack 容器到 deb.debian.org 只有 ~64 KB/s、到 pypi.org TLS 握手
超时，而 tuna 实测 **2.6 MB/s / 642 KB/s**。Dockerfile 里这些 build arg 默认为空（官方源），
正常网络的 CI/NAS 不需要传。

### 11.2 setup / fetch-models（P15b 新行为）

| 步骤 | 命令 | 实测 |
|---|---|---|
| `setup` 写生产模板 | `docker run --rm --user $(id -u):$(id -g) -v config:/config rui:rehearsal setup --config /config/config.yaml` | **~0.5 s**；写出 4912 B 配置 + `keywords.txt`。`providers` 链齐全：`asr [local_asr, siliconflow_asr]`、`tts [xfyun_tts, siliconflow_tts]`、`llm [deepseek_llm]`；`kws_model_dir=/data/models/kws/...`、`vad_model_path=/data/models/silero_vad.onnx`、playback `16000/1ch` |
| keywords 生成 | 同上 | 逐字匹配预期：`n ǐ h ǎo x iǎo r uì :2.00 #0.15 @你好小瑞`（50 B，含换行） |
| `fetch-models --no-kws` | `docker run --rm --network host -v data:/data rui:rehearsal fetch-models --no-kws --dest /data/models/sensevoice --vad-dest /data/models/silero_vad.onnx --base <local-mirror> --vad-base <local-mirror>` | **0.53 s**；文件落 `./data/models/{sensevoice,silero_vad.onnx}` |
| KWS 真下载 | `fetch-models --kws-dest /data/models/kws` | **未验**：本机容器到 GitHub release TLS 握手超时（见 §12）；Lead 在本机实测 tarball **32.9 MB / 解包 39 MB / 597.7 s**，解包布局与 `fetch-models` 一致 |

### 11.3 自愈与一次真实回合

| 演练 | 命令 | 实测 |
|---|---|---|
| 服务进程退出 → 自动重启 | `docker exec rui python -c "import os,signal; os.kill(1, signal.SIGTERM)"` | 容器退出后 `restart: unless-stopped` 拉起：`RestartCount 0→1`，HTTP **2 s**、healthy **6 s**、`uptime` 归零、`audio_plane=true` |
| 手动 `docker kill -9` | `docker kill --signal=SIGKILL rui` | 容器 `Exited (137)`，**没有**自动重启（`RestartCount=0`）——显式 kill 被视为手动停机；`docker start` 后恢复 |
| 计划重启 | `docker compose restart rui` | HTTP **1 s**、healthy **5–7 s**、`audio_plane=true` |
| **音频面崩溃（P15b 看门狗）** | 容器内对 plane pid 发 `SIGKILL` | **2.0 s** 后出现新 pid（42→168）、`/api/v1/audio.restarts=1`；`/health` 全程 200、`audio_plane=true`，容器 `RestartCount=0` 且保持 healthy；日志 `audio plane is down (child exited); restarting in 1 s` → `audio plane restarted (#1, pid=168)` |
| 崩溃后音箱重连 + 回合 | 新假音箱连 `:14412` → `POST /api/v1/debug/say {"text":"再说一遍"}` | 重连 ~1 s；回合返回 `reply=现在三点。, error="", audio_bytes=32000`，stages `llm=rehearsal_llm / tts=rehearsal_tts`；假音箱收到 `start_play` 并上报 consumed（14–15 帧 / 43,200 B） |
| 假音箱断线/重连（P15） | 假客户端两轮 connect/disconnect | `speaker_connected` 两次 True/False；plane `frames_in 100→200→300`、`dropped=0`、`rtf=0.158` |
| NAS 断电 | — | **未验**：没有 NAS 停机窗口 |

### 11.4 日志轮转

| 项 | 实测 |
|---|---|
| compose（主容器） | `docker inspect`：`{"Type":"json-file","Config":{"max-size":"10m","max-file":"3"}}` |
| 轮转有效性（独立容器，同参数缩小） | 写 3000 行、`max-size=2k`/`max-file=2`：`docker logs` 只剩 **30 行**（首行 line 2970、末行 2999），总量被 2k×2 封顶 → driver 轮转生效 |
| 应用文件日志 | `data/service.log` 存在（11.5 KB）；2 MB × 3 由 `RotatingFileHandler` 设置，未强制写满 2 MB 验证 |
| 健康检查输出 | 正常：`ok: uptime=21.1s devices=0 audio_plane=False`（无设备状态）；配设备后 `devices=1 audio_plane=True` |

### 11.5 卷持久化

| 项 | 实测 |
|---|---|
| config / data / models 三类 | 三处各写 marker + 模型文件；`docker compose down`（删容器）→ `up -d` 后全部仍在 |
| 模型路径映射 | 宿主机 `./data/models/sensevoice/...` 在容器内 `/data/models/sensevoice/...` 可见；`/var/lib/rui/models` 是同一宿主机目录的第二个挂载点（预期） |
| 重建耗时 | `down` + `up` 后 HTTP 1 s、healthy 6 s；配置里的设备/端口原样生效 |

### 11.6 本轮实测发现

1. **音频面自愈已由看门狗解决**（旧 P15 发现已关闭）：`AudioPlaneSupervisor.watch()` 1 s 轮询、退避 1→60 s；实测 kill 后 2.0 s 重建、`restarts=1`。旧版「崩溃后 90 s 不自建、需手动 restart」不再成立。
2. **坏 VAD 模型文件会让采集静默停摆**：把 `silero_vad.onnx` 换成随机字节后，plane 不报错、`frames_in` 一直 0；换回有效模型立即恢复（100 帧、rtf 0.158）。建议模型加载做校验/超时并把失败打进日志。
3. **设备有配置但 token 空时的确切报错**：plane 进程 `RuntimeError: RUI_DEVICE_TOKEN is empty: refusing to accept an unauthenticated speaker connection`；控制面 `WARNING … audio plane did not start: audio plane refused: …`；`/health` 仍 200（`devices=1, audio_plane=false`），healthcheck 转 unhealthy（`FailingStreak` 最终 20）。
4. **无设备的默认配置是合法状态**：`/health` ok、`audio_plane=false`、healthcheck healthy、`doctor` degraded（`wake_path` 等）；日志 `audio plane did not start: no device this build can drive`。
5. **Navidrome + ffmpeg 真机链路（容器内）**：真实 `resolve` 成功——`search: 2 track(s)`，输出 **8,581,722 B / 16 kHz / mono / s16 / 268.2 s**；ffmpeg 对其中一首 flac 打了 `invalid sync code / decode_frame() failed` 警告但退出码 0、音频完整（疑源文件问题，未深究）。
6. **冷装一键脚本依赖 GitHub raw**：本机访问 `raw.githubusercontent.com/yes999zc/rui/main/...` 返回 **404**（仓库当前为私有/不可匿名访问），install.sh 的 `[1/5]` 会失败；排练时预先放好 `docker-compose.yml`/`config.example.yaml` 才跑通。见 §11.7。
7. **compose 不写 `.env` 的 image 会回落 latest**：`install.sh` 之外的 `docker compose up -d` 若 `.env` 没有 `RUI_IMAGE`，会去拉 `latest`（本机 TLS 超时失败）。已在 install.sh 生成的 `.env` 里写入 pin；实测 run3 之后不带任何 env 前缀的 `docker compose up -d` 直接使用了本地 `rui:rehearsal`，没有尝试拉取。
8. **无设备时看门狗会空转刷日志**：`devices: []` 时 plane 本来就不该启动，但 `watch()` 仍会按退避重试并打印 `audio plane is down (no device this build can drive); restarting in 1 s` / `audio plane restart failed`。不影响健康检查（devices=0 时不查 plane），但首次装完、还没加音箱时日志会周期性出现。建议 `watch()` 在无设备（或 `start_audio=False`）时不启动。

### 11.7 容器冷装排练（install.sh 全流程，本机）

准备：`RUI_HOME=/tmp/p15-cold/run2`；把本机模型预置到 `data/models/`（SenseVoice 239 MB + VAD 0.6 MB + KWS 39 MB；APFS 拷贝共 **0.36 s**，避免 272 MB 下载）；因 raw 404，手工预置 compose/config.example。

| 步骤 | 实测 |
|---|---|
| `[1/5] compose` | 保留已有 compose；config.example 拉取 404（warning，不影响） |
| `[2/5] setup + keywords` | +1–2 s（镜像已热）/ +13 s（rebuild 后首次 `docker run` 冷启动）；配置来自**新模板**（provider 链齐全）、`/config/keywords.txt` 逐字正确 |
| `[3/5] .env` | 同轮；生成 48 hex device token，并写入 `RUI_IMAGE=rui:rehearsal` |
| `[4/5] models` | 同轮；检测到预置模型，走「models already present」分支（真下载路径由 §11.2 单独验证） |
| `[5/5] up + health` | 容器 1–2 s 起；脚本等待 healthcheck：+1 s `healthy` |
| **install.sh 总计** | **3 s**（热镜像/热 page cache）；**14 s**（重建镜像后首次启动容器的冷路径）。均不含真实模型下载 |
| 验收 | `docker compose ps` → `Up (healthy)`；`docker compose exec rui rui-healthcheck` → `ok: uptime=21.1s devices=0 audio_plane=False`；`/health` 200；`doctor` `verdict=degraded`，PASS：arch/cpu/disk/capture/playback(16k/1ch)/aec_rtf 0.010/wake_path，WARN：ram 3.9(VM)、container_runtime(镜像无 docker CLI)、web_auth、ack_wav(未填 TTS key) |
| 加设备后 | 配置加 `devices[0]`（host 127.0.0.1、listen 14412、token_env 指向生成的 token）→ `up -d`：**3 s** 后 `audio_plane=true`、`devices=1`、KWS/VAD `true`；无设备时 plane 不启动且 healthcheck 仍 healthy（两种状态都合法） |

未验：真实网络下的模型下载耗时（本轮预置跳过）、raw 拉取（404）、NAS 上 `hostname -I` 分支。

## 12. 未验项与原因

| 项 | 原因 | 影响 |
|---|---|---|
| NAS 断电后全链路自起 | 没有 NAS 停机窗口；本机验了「进程退出→自动重启」和「手动 restart」 | 结论依赖 Docker daemon 开机自启 + restart policy，属推断 |
| 真音箱重连时序（Wi-Fi 抖动、换 IP、耗时） | 本轮不动真机（声音资源归 Lead） | 用假音箱验了协议层 connected/disconnected；真机时序未测 |
| 3 天长稳观察 | P15 时间窗不足 | 启动/自愈已验，长稳后续看 |
| x86_64 NAS 上运行 | 本机是 arm64；CI 多架构 workflow 未实跑 | ✅ 2026-10-24 NAS 冷装通过（`e3ea158`，423 s 到第一句） |
| 真实模型下载（hf-mirror / GitHub release） | 本机容器到 hf-mirror、GitHub release 均 TLS 握手超时；冷装排练用了预置模型 | ✅ 2026-10-24 NAS 冷装真实下载成功（ASR 239 MB / VAD 643,854 B / KWS 39 MB）；国内网络需镜像 fallback |
| `install.sh` 一条命令（`curl \| sh`） | raw 匿名可读已确认；NAS 到 GitHub 偶发 TLS stall | ✅ 2026-10-24 NAS 一键安装通过（install.sh 234 s）；脚本已加超时/IPv4/`RUI_RAW` 换源 |
| 容器内音乐真机出声 | 需要真实音箱（声音归 Lead） | resolve/转码已验，播放未验 |

## 13. 冷装网络与并发接入排障（2026-10-24 验收日记录）

- **`raw.githubusercontent.com` 卡住**：表现为 `curl` 连上后 TLS 不返回、install.sh 停在 `[1/5]` 无输出。
  install.sh 已加 `--connect-timeout 10 --max-time 120 --retry 3` 并强制 IPv4；仍不稳定时用
  `RUI_RAW=https://ghproxy.net/https://raw.githubusercontent.com/yes999zc/rui/main`。
  注意代理可能缓存旧 `install.sh`/`docker-compose.yml`，冷装前确认内容是最新（`container_name`、脚本版本）。
- **模型下载**：ASR 走 hf-mirror；VAD/KWS 的 GitHub release 有 `ghproxy.net` / `gh-proxy.com` fallback
  （`fetch-models` 按顺序试，日志会打印 `mirror failed`）。
- **同一 4412 入口只能有一个音箱客户端**：如果另一台设备（或另一套调试桥）也用同一个 NAS `4412`
  连进来，日志会出现 `replacing existing speaker connection`，表现为反复断连、唤醒失灵、播放卡顿。
  排查：`ss -tnp | grep 4412` 只应有一个 ESTAB；停掉多余客户端后客户端会自动重连恢复。
- **recovery 缺口（待修）**：config 被清空成 0 字节时不会走 last-good，会用 schema defaults →
  `insecure_bind` 拒绝 → crash loop。手改配置前先 `scripts/backup.sh`。

## 14. Linux 裸机（无 Docker）：sherpa/onnxruntime 加载失败

[issue #2](https://github.com/yes999zc/rui/issues/2)：Debian 裸机上 `uv sync --extra kws`
可能装上了 sherpa-onnx，但 C 扩展加载不了 `libonnxruntime.so`。服务照常启动，KWS/VAD
静默关闭。典型报错：

```text
ImportError: libonnxruntime.so: cannot open shared object file: No such file or directory
# 或
ImportError: .../libonnxruntime.so: version `VERS_1.28.2' not found
```

先区分「没装包」和「装上了但加载失败」：

```bash
cd rui
uv run rui doctor        # wake_runtime FAIL 会带真实异常文本
uv run python -c "import sherpa_onnx; print(sherpa_onnx.__version__)"
ldd .venv/lib/python*/site-packages/sherpa_onnx/lib/_sherpa_onnx*.so | grep onnxruntime
```

`doctor` 的 `wake_runtime` 才真正 import（并在模型可读时加载 Silero）；`wake_path` 只检查
配置的模型路径是否存在/可读。两者都 PASS 才说明裸机的 KWS/VAD 真的可用。日志里现在会写
`sherpa-onnx is installed but failed to load: <异常>`，不再是笼统的 "not installed"。

修复（onnxruntime 1.28.2 与 wheel 的 ABI 一致；arm64 把 `x64` 换成 `aarch64`）：

```bash
curl -fL -o /tmp/ort.tgz \
  https://github.com/microsoft/onnxruntime/releases/download/v1.28.2/onnxruntime-linux-x64-1.28.2.tgz
tar -xzf /tmp/ort.tgz -C /tmp
cp -a /tmp/onnxruntime-linux-x64-1.28.2/lib/libonnxruntime.so* \
  .venv/lib/python*/site-packages/sherpa_onnx/lib/
uv run python -c "import sherpa_onnx; print(sherpa_onnx.__version__)"
uv run rui doctor        # wake_runtime 应为 PASS
```

- 重新 `uv sync --reinstall-package sherpa-onnx-core`（或重建 venv）会覆盖手放的库，需重做。
- 生产优先用 Docker 镜像（镜像自带可用的 sherpa/ORT，不需要这一步）。
- 部署总览见 [`docs/deploy.md`](deploy.md)（本文 §14 是裸机专用排障）。
