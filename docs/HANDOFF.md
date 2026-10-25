# HANDOFF — rui / 小瑞

> 接手入口：先读这一份。历史开发日志（2026-10-20 → 2026-10-24，旧 §1–§12.35）已归档在
> `docs/archive/HANDOFF-history-to-2026-10-24.md`；正文里出现的旧 § 编号都指那份归档。
> **目标载体是 NAS**（Bakey-NAS / fnOS）：生产形态是 NAS 上的 Docker 容器；Mac 只是开发/操作端。

## 0. 一分钟了解

| 项 | 当前值 |
|---|---|
| 项目 | 自托管语音终端：LX06 音箱 + FastAPI 控制面 + 独立音频面 + Vue 控制台；MIT；公开仓 `yes999zc/rui` |
| 版本 | **v0.5.2**（安装路径 #15 修复 + 卫星就绪/连接测试口径，dot2 #7）；生产仍 **0.5.0**（0.5.1/0.5.2 无音频运行时变化） |
| 生产镜像 | `/vol1/rui` 跑 **`ghcr.io/yes999zc/rui:0.5.0`**（digest `sha256:01a3afe18faa…`，amd64+arm64）；回滚 `0.4.2`（digest `sha256:3791ccfdb…`）；非 root uid 1000，healthy、音箱在线、`multi_device.enabled=false` |
| 生产路径 | `/vol1/rui`（compose 项目 rui）；config/data 已 chown 1000；旧 `/vol1/house-ear` 保留回滚 |
| 质量 | **1298 passed / 17 skipped**（0.5.2；跳过数随平台差 1，总数不变）；ruff clean；`sh -n deploy/install.sh` 干净；web typecheck/build 绿；CI 原生架构 runner（PR #10）已合入 |
| v1 四门槛 | 冷装 423 s；唤醒 19/20；barge 24.2/64.5/205.2 ms；公开仓 ✅（历史基线） |
| 当前主线 | **v0.5.2 已备好**（dot2 #15 管道安装 + #7 卫星就绪/连接测试）；生产仍是 0.5.0；真机验收 9哥 保留 |

### 0.1 新会话交接（2026-10-25 17:25 更新：Phase 2 接线完成）

- **当前发布/生产**：**v0.5.0 已上线**（2026-10-25 18:02；`ghcr.io/yes999zc/rui:0.5.0`，digest `sha256:01a3afe18faa…`，amd64+arm64）。回滚镜像 `0.4.2`；部署前备份：`.env.bak.20261025-180259`（root 0600）、`config/config.yaml.bak.20261025-180259`、`/vol1/rui-backups/rui-backup-20261025-180734.tar.gz`（sha256 `17711958…`）。验收：healthcheck ok、`/health` version 0.5.0、config_version 仍 2、`audio_devices` 仅 living/客厅 running+connected、真实回合 `audio_bytes=120,912`（deepseek_llm + volc_tts）；`multi_device.enabled=false`。
- **NAS 单机配置补齐（2026-10-25 18:2x）**：时区 `Asia/Shanghai`（真实回合已显示 18:2x）、天气城市沈阳、DeepSeek 搜索开启（`DEEPSEEK_API_KEY` 已存、工具注册）、Telegram 渠道启用（token 已验签、gateway running、reply_mode=both、沿用旧 allowlist 1 个 chat）、音乐 Navidrome 恢复（沿用 `/vol1/house-ear/data/lan_media.json` 凭据，`credentials_ready=true`、搜索返回 20 首）。NAS 无直接外网，Telegram 需容器代理：`.env` 增加 `ALL_PROXY`/`HTTP(S)_PROXY=http://127.0.0.1:7890` + `NO_PROXY` 后 `up -d --force-recreate`；已备份 `.env.bak.*`。HA 仍 connected（86 工具 / 75 实体 / ~5 ms）。
- **发布状态（2026-10-25 17:4x）**：`main` 已推（HEAD `406785e`）+ tag **`v0.5.0`**；CI 全绿（secret-scan/security/ci/docker-publish，tag 与 main 各一轮）。GHCR `0.5.0`=`0.5`=`sha-406785e` 同一 manifest digest **`sha256:01a3afe18faa…`**（amd64+arm64；发布时 `latest` 也是同一 manifest，之后随 `main` 移动）；`docs/releases/v0.5.0.md` 已补 digest；GitHub Release 已发；#7 已公布 tag/digest/平台/「契约变化=无」。
- **本会话新完成**（`git log origin/main..main`）：
  - HA 控制台逻辑：测试用表单里的一次性 token（不落盘）、保存并重连覆盖新 token、测试后表单不被清空（`6003700`/`7ae83cf`）；
  - §9 决策冻结 + `docs/multi-device-design.md` §11 答复 dot2 #11（`aa0bdb4`），Phase 2 契约草案 v0.1 写入 §12（`f3f0135`）；
  - 发布版本定为 **v0.5.0** 并写发布判据（`7c66dde`）；
  - Phase 2 四块：音频面候选/准入 + `post_playback_mute_ms`（`1b71584`）、`src/rui/audio/fleet.py`（`a34cba5`）、`src/rui/audio/arbiter.py`（`c0c2fc9`）、`src/rui/audio/coordinator.py`（`bd7c737`）；
  - `rui_compat` 上界抬到 `<0.6.0`（内置 LX06 / example / v1 文档 / 测试；`a4740b1`）——不改会让 0.5.0 拒绝加载内置插头；
  - `PlaneHandle` 重启回调补 `on_wake_idle` 与 Phase 2 的 `on_wake_candidate`（`43a268b`）。
- **本段（17:25 前）完成 Phase 2 接线**：`fce8d8c` 抽取 `_wire_plane`（零行为变化，`git diff -w` 只移动）、`e84fac4` `PlaneFleet` 启动 + 逐设备回调 + `WakeCoordinator` + turn/owner 生命周期 + 单平面端点主平面化、`ec8cf44` 逐设备健康/告警/状态（`/health.audio_devices`、`/api/v1/audio.planes`、设备写 fleet 重建）；新增 `tests/test_app_fleet.py` 7 例（fake `PlaneFleet`，不 spawn），并用 2×mock 真进程 smoke 验证 2 plane 共存与干净退出。
- **Phase 1b（17:35）**：`multi_device` 目录组（启用/max_planes/default_output/来源优先级/两个窗口/房间优先级/最近活跃窗口）+ 每设备 13 个 `devices[].audio.*` 覆盖字段（AEC 归属/开关、播放后静音、续听、预缓冲、采集布局；空值 = 继承）；体检页多设备卡片（未启用说明 / 逐平面状态 / 超 cap / 启动失败）；`web` typecheck+build 绿。
- **打磨批（dot2 #12）**：healthcheck 根因（device_errors / fleet / 逐房间）；`doctor` 新增 `container_healthcheck` 一致性行；install.sh 重跑打印当前 `RUI_IMAGE` + 显式升级提示 + prune；0.4.2 release note 占位注释已清；`docs/deploy.md` §7.1 下游升级 + `rui_compat` 兼容矩阵；adapter 音量换算指向 helper。
- **dot2 干净安装结果（2026-10-25 18:19）+ 冷装回归修复**：0.5.0 vanilla 干净安装通过（约 5 分钟），operator 配置后 `/health`/`plug-test`/`playback_gap=0` 全过，B 路线（`post_playback_mute_ms`）在 0.5.0 单平面也成立（两次长回复 gate 5.0 s、45 s 内 0 条伪造 turn）。他们开了两个 issue：**#13** install.sh 未加引号 heredoc（反引号被执行、console token 打到 stdout）、**#14** fetch-models 429 无重试且安装器 exit 0。修复已落 main：`7dbfecb` #13a、`ef2affd` #13b、`8433113` #14.1、`f032931` #14.2、`b55dcdb` #14.3（doctor `models` 行）、`907fcd1` #14.4 文档；全量 **1293 passed / 18 skipped**、ruff/`sh -n` 干净。**v0.5.1 已发布**（tag `v0.5.1`/`c0d7048`）；随后 dot2 复测发现 `curl | sh` 吃 stdin 的 **#15**，已在 v0.5.2 修复。
- **质量**：全量 **1293 passed / 18 skipped**；ruff clean；`sh -n deploy/install.sh` 干净；`web` typecheck/build 绿。
- **下一步**：**生产部署 v0.5.0 需 9哥 点头**（备份 `.env`/config → 改 `RUI_IMAGE` → `up -d` → `/health` devices=2 验收）；真机 LX06+2×Dot2 验收与回滚演练等 9哥 安排；dot2 会在发布后做干净安装验收（卡点单独开 issue）。
- **dot2**：0.4.2 受控切换完成（回滚 21 s / 前滚 11 s，`plug-test` 与 0.3.4/0.4.0/0.4.1 逐项一致）；实测「撤 adapter 门控失败」→ 已定走 B 路线（上游 `devices[].audio.voice.post_playback_mute_ms`，默认 0，Dot2 建议 2500）；#11 确认 Phase 2 内部 IPC、adapter 零改动；#12 下游原地升级冷启动反馈已答复（docs PR 欢迎、install/healthcheck/release-note 进打磨批）。**他们会在 0.5.0 发布后做一次全新安装验收并反馈**；卡点单独开 issue。发布时需在 #7 公布 tag `v0.5.0` + GHCR digest + 平台 + 「相对 0.4.2 契约变化 = 无」。
- **HA（生产待办，需 9 哥 点头）**：生产 `config/config.yaml` 的 `tools.ha.base_url` 为空、`data/secrets.yaml` 无 `RUI_HA_TOKEN`（`.env` 里是空值）。HA 的地址与长期令牌见 9哥 的本机配置（不入库）；`/api/mcp` 已实测 200、86 工具。0.4.2 控制台上正确顺序：填地址+token → **保存并重连** → 再点测试（0.4.2 的测试按钮只用已保存 token；该坑已在本分支修复）。
- **环境坑（新会话必读）**：见 §0.3。

### 0.2 下一步（Phase 2 已接通：控制台状态 → 打磨批 → v0.5.0）

Phase 2 接线已完成并验证，**不要再改** `src/rui/audio/{protocol,process,client,fleet,arbiter,coordinator}.py` 的契约；app 侧行为的判据是 `tests/test_app_fleet.py` + 全量测试。

1. **Phase 1b ✅**（P5.3）：`multi_device.*` / `devices[].audio.*` 已进目录（`multi_device` 组 + 每设备 13 个 audio 覆盖字段），体检页有多设备卡片（未启用/逐平面状态/超 cap/启动失败）；`web` 构建绿。
2. **打磨批 ✅**（P5.4）：healthcheck 根因 + doctor 一致性行、install.sh 升级提示 + prune、0.4.2 占位注释清理、`docs/deploy.md` §7.1 下游升级/兼容矩阵、音量换算 pointer；dot2 docs PR 当前无 open，发布前再查。
3. **v0.5.0 发布 + 生产部署 ✅**（2026-10-25）：tag `v0.5.0`、digest `sha256:01a3afe1…`、CI 全绿、Release 已发、#7 已公布；生产已切 0.5.0（healthcheck ok、真实回合通过）。
4. **v0.5.1 发布 ✅**（2026-10-25 18:5x，dot2 #13/#14）：tag `v0.5.1`（commit `c0d7048`）、digest `sha256:25d67cf88c62…`（0.5.1=`0.5`=`latest`=`sha-c0d7048` 同一 manifest，amd64+arm64）；CI 全绿；Release 已发；#7/#13/#14 已通知复测。**生产保持 0.5.0**（0.5.1 只改安装器/doctor/文档，无运行时变化）。**剩真机 LX06+2×Dot2 验收与回滚演练（9哥 安排）**。
5. **v0.5.2 发布**（2026-10-25）：`a26af81` 管道安装 stdin（dot2 #15，端到端管道测试 + 静态不变量）、`fe16846` 卫星就绪/连接测试按传输方式（dot2 #7）+ 控制台新增设备默认 id / 不适用徽章；全量 **1298/17**、ruff/`sh -n`/web 全绿；tag 与 digest 待 9哥 点头后补。**生产保持 0.5.0**。
6. **真机验收**（9哥 保留）：LX06 + 2×Dot2 的共存/各自唤醒/同词单 turn/barge 归属/故障隔离/回滚；记录 2/4 平面 RSS/CPU 与唤醒→首字延迟。

### 0.3 环境与命令（新会话必读）

- **工作区**：`~/AI_Workspace`；仓库 `01_Code/projects/house-ear`；`.venv`（Python 3.13）；Node 22；`cd web && npm run typecheck && npm run build`。
- **测试**：`.venv/bin/python -m pytest -o addopts="" -q`；`.venv/bin/python -m ruff check src tests`。全量约 67 s。
- **SSH（NAS）**：`ssh` 密码认证必须；**本机 expect 无法分配 pty**，请用 paramiko（系统 python3 已装 5.0.0）登录 `yes999zc@<NAS 地址>`（Tailscale 主机名 `nas-bakey`；具体地址见 9哥 的本机凭证文件）。密码位置：`~/AI_Workspace/01_Code/device-inventory.md` 第 18–32 行（0600，**不要打印/入库**）。docker 需 sudo：远端命令前缀 `sudo -S -p ''`，把密码写进 stdin。短间隔重连会被拒，失败等 40 s。
- **GitHub**：bash 里 `curl https://github.com` 可能 SSL 失败，但 `api.github.com` 可用；token 用 `printf 'protocol=https\nhost=github.com\n\n' | git credential fill` 取（不打印）。`web_fetch` 可读 GitHub API/页面。
- **推送策略**：**不要每步 push**。push main 会触发 `docker-publish` 重建 `latest`。攒到需要真机镜像/发布时一次推；推后 CI 产出 `sha-<commit>` 可用于测试。
- **发布**：v0.5.0（不发 0.4.3、无公开 alpha/beta tag）；流程见 `docs/releases/README.md` + `Makefile` 的 `changelog`/`release`；发布判据见 `docs/ROADMAP.md` P5。
- **仓库硬约束**：不写内网 IP / 家目录绝对路径 / 明文密码（`secret-scan` workflow 会红）；生产变更需 9 哥 明确点头；一步一提交、英文 conventional commits；数字/判据才算完成。

## 1. 项目与架构契约

```
设备(LX06) --ws 拨入 :4412--> device adapter --> 音频面进程 --IPC(unix socket)--> 控制面(FastAPI :8080)
                                                  AEC/KWS/VAD/播放                     回合/provider/控制台
```

- 采样：采集 16 kHz（8 ch = mic 6 + ref 2，硬件 48 kHz，设备侧移位 `mic_shift=12` / `ref_shift=16`）；
  语音 16 kHz；播放 16 kHz PCM（adapter 负责设备侧格式）。
- IPC：5 字节帧头（type 1 + length 4）+ PCM（带 seq/ts_ms）；控制消息 JSON。音频面内部统一 `[-1,1]` 浮点。
- 分段：跳过唤醒尾巴、保留 preroll、600 ms 静音收尾、10 s 硬截断、唤醒后 5 s 无人说话放弃；
  一轮结束后开「续听门」（无唤醒词，5 s 自动关）。
- 设备差异全部收在 `src/rui/device/`（`DeviceAdapter` + `Capability` + `registry.register`）；
  控制面/音频面不为品牌加分支。新增机型 = 一个 adapter（详见 `remaining-work.md` §I/§K）。
- 播放仲裁：提示音 > 回答 > 提醒 > 音乐；同一时刻一个 owner。
- 渠道：`src/rui/channels/` 独立进程（Telegram / 微信 iLink），复用 `POST /api/v1/chat`，不依赖音频面。

## 2. 环境与部署

> 0.4.0 起为**非 root**：容器 uid/gid 1000；`config/`、`data/`、`data/state` 必须可写（升级前一次性 `chown -R 1000:1000 config data`）；compose 含 `read_only`/`cap_drop`/`no-new-privileges`。旧非 root 迁移步骤见 `docs/deploy.md`/`docs/runbook.md`。

| 项 | 值 |
|---|---|
| 生产主机 | Bakey-NAS（fnOS，x86_64，Docker 28.5.2 / Compose v2.40.3） |
| 安装目录 | `/vol1/rui`（compose 项目）；配置 `config/config.yaml`；数据 `data/`；模型 `data/models/` |
| 备份 | `/vol1/rui-backups/`（0600，含 `.env`；最近：`rui-backup-20261024-125842.tar.gz`，sha256 `9c879d60…`） |
| 冷装证据 | `/vol1/rui-verify/`（269 MB，可删；`install.log` 与冷装 service.log 已拷到验收工作区） |
| 端口 | 控制台 `8080`、设备入口 `4412`（host network）；`allow_no_token: false` |
| 开发机 | Mac：仓库 + `.venv`（Python 3.13）+ Node 22；**无 docker CLI**（只有 OrbStack 的 `orb/orbctl`），compose/冷装只能在 NAS 上跑 |
| GHCR | `0.4.2` / `0.4` 同一 manifest（digest `sha256:3791ccfdb…`，amd64+arm64）；`latest` 随 `main` 自动重建、digest 会变（安装请固定 `0.4.2` 或 digest）；历史 tag 保留 |

## 3. 已定决策（改前先问 9 哥）

1. **目标载体 NAS**：生产只跑在 NAS 容器里；Mac 不作为运行宿主。
2. **ASR**：本地 SenseVoice int8 优先；NAS 现配置为火山 ASR 单链路（省内存），本地模型保留可切回。
3. **TTS**：讯飞主用、火山兜底；`ack.mode: tts`（NAS `ack.max_ms: 800`），冷装模板同链。
4. **LLM**：`deepseek-flash`，thinking 关；冷装模板读 `RUI_LLM_KEY`，旧部署 `secrets.yaml` 用
   `DEEPSEEK_API_KEY`（待 P1.2 统一）。
5. **模型不进镜像**：`fetch-models` 下载到 data 卷；源顺序 hf-mirror → GitHub release → ghproxy 镜像
   （VAD 上游 hf 仓 2026-10-24 已失效，GitHub release 是当前主源）。
6. **设备音量默认不接管**：`devices[].volume: 0` 表示归原厂；读写只在显式 API/测试调用。
7. **唤醒应答**：当前 `tts` 模式（短句）；老项目 earcon 差异未完全解释，改动前先看归档 §6.5。
8. **厂家让位**：说「小爱同学」时不开我们的会话、不播提示音；我们的唤醒词立即接管。
9. **音量不自动写**，厂商会自己改 mysoftvol（见过 160→255→160）。
10. **渠道**：NAS 只启用 Telegram（托管 gateway，出网走本机 mihomo SOCKS）；微信只保留接口
    `enabled=false`。同一 bot 只能一个长轮询消费者；**同一 4412 入口只能一个音箱客户端**。
11. **配置恢复**：last-good + 启动回退 + 控制台 banner + `rui config restore`；
    密钥/用户数据 0600；坏配置不静默覆盖（空文件例外，见 P1.1）。
12. **公开仓硬约束**：不硬编码 key / 内网 IP / 私人路径；MIT；发布走 GHCR 多架构。
13. **生产变更需 9 哥 明确点头**（部署/重启 NAS）；测试期间也不要同时跑 Mac 外部 gateway。
14. **Socket & Plug（2026-10-24 定）**：RUI 只做插座；插头 = 声明必需 + 代码包可选（manifest 描述机型/AB 等级/能力/参数/协议/兼容版本/交付物）；
    内置 LX06 作为参考插头；第三方插头默认 sidecar 容器隔离，签名且声明可信的才 in-process。标准文件 `docs/socket-plug.md`（P2.1 起草）。
15. **音箱端依赖上游**：open-xiaoai（[coderzc/open-xiaoai](https://github.com/coderzc/open-xiaoai)）；我们验证用的 fork 分支在文档里写清最低 client 版本。
16. **ffmpeg 换 LGPL-only 构建**；MIT 只覆盖本项目代码，第三方组件进 `THIRD_PARTY.md` 并在 README 致谢（open-xiaoai / EchoMuse / sherpa-onnx / SenseVoice / silero-vad / speexdsp / FFmpeg）。
17. **0.1.x 不拆 LX06**：核心保留 `device/xiaoai` 作为参考插头；0.2 再抽成独立包，避免契约未定稿就拆。
18. **安装向导 manifest 驱动**；EchoMuse Device Management 作为参考，调研产出 `docs/device-management-research.md`。
19. **0.2 插头边界（EchoMuse 调研后定）**：只支持 manifest-only 与内置/本地可信插头；第三方可执行 adapter 默认关闭，
    后续走 sidecar（P3 安全项），0.2 不做签名市场。
20. **设备注册表**：继续用 `config.yaml` + 新增 `DeviceConfig.params`；不引入 SQLite、不做 approve/pending 队列、
    不做每设备 token/证书；设备列表/详情/connect/probe/test/delete 足够 0.2。
21. **P2.4 范围**：单设备 CRUD + 参数表单 + 连接测试 + 删除；多设备/每设备端口/配置 v3/仲裁归 P3.3/P3.4。


## 4. 运维手册（详见 `docs/runbook.md`）

```bash
cd ~/rui                                  # NAS 上
docker compose ps
curl -s http://127.0.0.1:8080/health
docker compose exec -T rui rui doctor
docker compose logs --tail=100 rui
docker compose exec -T rui rui token    # 只留在 shell 里，不要回显/上传
scripts/backup.sh --out ~/rui-backups
scripts/restore.sh <archive> --home /tmp/rui-restore-smoke
docker compose exec -T rui rui config restore --last-good
```

- 升级/回滚：改 `.env` 的 `RUI_IMAGE`（当前 `ghcr.io/yes999zc/rui:0.4.2`）→ `docker compose up -d`；
  回滚改回 `ghcr.io/yes999zc/rui:0.4.1`（或 `0.4.0`）再 `docker compose up -d`；改 `.env` 前先备份
  （最近：`.env.bak.20261025-152606` 为 0.4.2 部署前）。
- 验收记录器：`python3 scripts/acceptance-v1.py wake|barge --url http://<NAS-IP>:8080 --out <dir>`（纯 stdlib）。
- 开发：`.venv/bin/python -m pytest -o addopts="" -q`；`ruff check .`；`cd web && npm run typecheck && npm run build`。
- 模型测试：`RUI_TEST_MODELS=/path/with/silero_vad.onnx+model dirs`。

## 5. 已知问题与欠账（与 `ROADMAP` §2/§3 对齐，基础设施优先）

**P1 可靠性（0.1.1）**
- 空 config（0 字节）→ schema defaults → `insecure_bind` 拒绝 → crash loop（未走 last-good）
- 新模板 `RUI_LLM_KEY` vs 旧 `secrets.yaml` 的 `DEEPSEEK_API_KEY`
- `fetch-models` 的 httpx `timeout=None`，直连失败会拖住 fallback
- 验收脚本输出曾在容器内 `/data/...`，容器重建后 barge JSON 丢失
- GHCR 发布 workflow 不跑测试（tag push 直接构建）
- 静态 ffmpeg 的 GPL 包袱与第三方声明缺失（换 LGPL 构建 + `THIRD_PARTY.md`）

**P2 基础设施（当前主线）**
- **Socket & Plug 标准 v0**（`docs/socket-plug.md`）：manifest/打包/校验/信任/in-process vs sidecar
- **安装向导**：manifest 驱动、通用步 + 插头步、失败诊断、幂等续跑
- **真·陌生人冷装**：公开命令 + Setup 向导 + 自动下模型，不复制生产配置（验收时用了复制/缓存）
- 设备管理后台最小版（EchoMuse-inspired）
- 运维韧性：真恢复/换机、mihomo 失联提示、3 天长稳/断电自起
- 生产微信验证（真机已过，生产未过）

**P3 功能/重构（0.2.0）**
- LX06 抽成 `rui-plugin-xiaoai`；`rui-dot2-bridge` 按标准改造成第一个外部 B 类插头
- 第二台设备（config v3、无播放降级）；设备管理增强（approve/OTA/多设备仲裁）
- 安全：SSRF/端口探测 allowlist、容器非 root、CVE 扫描

**P4 使用后打磨（9 哥 用几天再排）**
- 音乐真凭据/收藏/续歌；控制台手机化；微信 QR 取消；咖啡链接；Logo GIF

**已由 9 哥 实测、不再记欠账**：远场 3–4 m 唤醒（实测通过，未留 N/M，不作为门槛证据）；
微信 iLink 真机文字/语音（真机通过；生产机仍待验，归 P2）。

其它已知：音箱 root 密码受 squashfs 只读限制；Telegram 出网依赖 NAS mihomo 节点运行时状态。

## 6. 最近时间线（细节在归档）

- 10-25 下午：0.4.2（Phase 4）发布并部署：单一 nav manifest（PanelSpec nav）、11 面板、7 个 legacy panel/旧路由退役、providers 更名「模型与语音服务」、ConfigCenter/ToolsView 死分支清理、README 512→366 行、多设备设计文档；测试 1142/18，ruff/web 绿；GHCR `0.4.2`/`0.4`/`latest` digest `3791ccfd…`，[Release](https://github.com/yes999zc/rui/releases/tag/v0.4.2) 已发；生产切 0.4.2（uid 1000、音箱在线），真实 say `audio_bytes=191,198`；dot2 受控切换已获 9哥同意并在 #7 公布 tag/digest。

- 10-25 中午：0.4.1 发布并部署：Jellyfin/Emby 音源（真实 Jellyfin 12.3.0 / Emby 4.10.2 验收）、首次登录 `console-token.txt`、dot2 生效端口/token_env/冲突 + follow-up 配置 + register 后 wake 重探测、README/first-run 重写；CI/publish 全绿（1141 passed / 18 skipped），GHCR `0.4.1`/`0.4`/`latest` 同一 digest `380327e5…`；生产切 0.4.1（uid 1000、音箱在线），真实 say `audio_bytes=280,312`，`console-token.txt` 0600。

- 10-25 中午：0.4.0 重切并发布：CI/publish 全绿（pytest 1104/17、ruff、secret-scan、多架构），GHCR `0.4.0`/`0.4`/`latest` 同一 digest `c4cd1a61…`，GitHub Release 已发；生产 11:06 切官方镜像（backup `.env.bak.20261025-110622`），uid 1000、音箱在线，真实 say 回合 `audio_bytes=95,864`（deepseek_llm + volc_tts）。dot2 兼容 PR #1 已开，#3/#7 已回复。

- 10-25 上午：控制台 IA 重构 Phase 1–3 → 0.4.0：两分组/合并页、provider 预设+新建、音乐 Subsonic 家族预设、
  凭据类型化+隐藏、MCP headers+HA 卡片、系统页；部署时发现 builtin rui_compat 仍 <0.4.0 导致启动失败，已修并重发 0.4.0。

- 10-25 凌晨：两个独立审计（控制台 IA、Socket/Plug 契约+实现）完成；0.3.3 修 P0（导航两分组、向导 01 改名、
  xfyun 就绪过滤、音乐死路、provider fallback 保留）与 socket P0/P1（trust 门控、能力交集、音量契约、DeviceWake、
  音频守护、ws 上限、ts 对齐），issue #3/#7 更正已回。生产 0.3.3 非 root 运行。

- 10-25 凌晨：0.3.2 P3.5 安全：SSRF 出站守卫、非 root 容器 + 状态/属主迁移、pip-audit/Trivy CI；
  生产已升级 0.3.2（非 root uid1000、read_only/cap_drop；一次性 chown + 新 compose；本机构建镜像因 CI 多架构慢，官方 0.3.2 镜像仍在构建）。

- 10-25 凌晨：0.3.1：官方 downstream adapter 钩子（环境变量/entry points，fail-soft），dot2 issue #7 的 Q1–Q6 已答复；
  生产已升级 0.3.1（官方镜像；adapter 钩子/契约/设备管理）。

- 10-25 凌晨：P3.0–P3.4 完成并发布 0.3.0（生产暂留 0.2.2：NAS 拉 GHCR TLS 超时，网络恢复后升级）：Socket/Plug v1（device_wake 契约、契约测试工具、LX06 参考插头）、
  设备管理（状态/启停/token 轮换/端口冲突/多设备 warning）；全量 948 tests；下一步 P3.5 安全。

- 10-24 晚：全仓改名 house-ear→rui（包/CLI/env/路径/容器），发布 0.2.0；干净冷装（新智能体只读公开文档）
  85s healthy，装在 `/vol1/rui`，旧 `/vol1/house-ear` 停用保留。等待人工走向导配置 keys/设备。

- 10-24 深夜：0.1.3 官方 `latest`（rev 50df3b4）含 VAD/KWS 镜像内置、doctor sherpa 运行时探测（issue #2）、
  模型下载 IPv4/代理/镜像策略修复、安装脚本容器预检；生产已切官方镜像 healthy。

- 10-24 傍晚：0.1.1 打 tag（CI 多架构构建中，arm64 QEMU 编译 ffmpeg 很慢）；为不阻塞验证，用同一 Dockerfile
  在 Mac 上构建 amd64 并 `docker save/scp/load` 到 NAS，先部署 0.1.1（revision 74c6c42），再构建/部署
  0.1.2（含向导 UI、设备管理 UI、播放节奏 P2.9，revision ee772dd）。官方镜像完成后应把 `.env` 切回
  `ghcr.io/yes999zc/rui:0.1.2` 并清理 `*-local` tag。

- 10-24 傍晚：P1 全部落地（空 config 恢复、旧 key 兼容、下载超时、验收落盘、发布测试门禁、LGPL ffmpeg）；
  P2.1 Socket & Plug 标准 + manifest loader、P2.2 向导设计 + 后端切片（plugs/setup-step/params）完成；准备 0.1.1。

- 10-24 下午：确定 Socket & Plug 为长期架构（声明必需 + 可选代码包；内置 LX06 参考插头；第三方默认 sidecar）；
  定 ffmpeg 走 LGPL-only；定上游 open-xiaoai；派智能体调研 EchoMuse Device Management；路线图按“基础设施优先”重排。

- 10-22：真机回合、AEC/KWS、播放链路、灯环、音乐核心、连续对话；289→487 tests。
- 10-23：HA/MCP + V2、控制台 2.0/安装向导/设备管理/话术编辑、公证归档/公开仓、NAS 真机部署、
  Channels（Telegram + 微信接口）、config recovery、backup/restore、品牌。
- 10-24：v1 freeze → 验收日四门槛通过；修 VAD 源/GitHub 网络/install.sh 超时/IPv4；发布 v0.1.0
  并部署 NAS；归档历史、重写 ROADMAP/HANDOFF；清理测试镜像与缓存。

## 7. 历史与文档地图

- 历史日志：`docs/archive/HANDOFF-history-to-2026-10-24.md`（旧 §1–§12.35，含所有 commit/实测）
- 验收：`docs/acceptance.md`（结果已填 §2–§7 + §9）
- 运维：`docs/runbook.md`（§9 备份恢复、§13 冷装网络排障）
- 部署：`docs/deploy.md`；渠道：`docs/channel-gateway.md`；配置恢复：`docs/config-recovery.md`
- 音箱接入：`docs/open-xiaoai-client.md`；剩余工作：`docs/remaining-work.md`；路线图：`docs/ROADMAP.md`

## 8. 工作规矩

1. 一步一提交；提交信息写“改了什么 + 实测到什么”。
2. 没有数字的“已完成”不算完成；没有判据的步骤不进路线图。
3. 老项目解过的问题先抄结论；参数优先参考老项目（`docs/recon-old-build.md`、`docs/vendor.md`）。
4. 机型差异只进 adapter 层；控制面/音频面不为品牌加分支。
5. 阶段结束更新 `ROADMAP` + 本文件；详细过程进归档，不往入口堆。
6. 生产变更（部署/重启/改 NAS）等 9 哥 明确指令；token/密码/LAN IP 不进聊天、日志、仓库。
