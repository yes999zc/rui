# HANDOFF — rui / 小瑞

> 接手入口：先读这一份。历史开发日志（2026-10-20 → 2026-10-24，旧 §1–§12.35）已归档在
> `docs/archive/HANDOFF-history-to-2026-10-24.md`；正文里出现的旧 § 编号都指那份归档。
> **目标载体是 NAS**（Bakey-NAS / fnOS）：生产形态是 NAS 上的 Docker 容器；Mac 只是开发/操作端。

## 0. 一分钟了解

| 项 | 当前值 |
|---|---|
| 项目 | 自托管语音终端：LX06 音箱 + FastAPI 控制面 + 独立音频面 + Vue 控制台；MIT；公开仓 `yes999zc/rui` |
| 版本 | **v0.4.0**（控制台 IA 重构 Phase 1–3、Socket/Plug v1、安全 P3.5），2026-10-25 已部署 NAS |
| 生产镜像 | `/vol1/rui` 跑 `ghcr.io/yes999zc/rui:0.4.0-local`（本机构建兜底）；**官方 0.4.0 已于 2026-10-25 重切发布**：`0.4.0`/`0.4`/`latest` 同一 manifest，digest `sha256:c4cd1a61…`，amd64+arm64；非 root uid 1000，待切换 |
| 生产路径 | `/vol1/rui`（compose 项目 rui）；config/data 已 chown 1000；旧 `/vol1/house-ear` 保留回滚 |
| 质量 | **1104 passed / 17 skipped**（含 5 个 compat 回归测试）；ruff clean；web typecheck/build 绿；CI 原生架构 runner（PR #10）已合入 |
| v1 四门槛 | 冷装 423 s；唤醒 19/20；barge 24.2/64.5/205.2 ms；公开仓 ✅（历史基线） |
| 当前主线 | 0.4.1（首次登录 token A、README/CHANGELOG 发布线、dot2 两项修复）→ Phase 4 清理（0.4.2）→ 代码视图；Jellyfin/Emby 与多设备/sidecar/播放 gap 提前 |

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
| GHCR | `0.4.0` / `0.4` / `latest`（同一 manifest，digest `sha256:c4cd1a61…`，amd64+arm64）；历史 tag 保留 |

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

- 升级/回滚：改 `.env` 的 `RUI_IMAGE`（当前 `ghcr.io/yes999zc/rui:0.1.0`）→ `docker compose up -d`；
  回滚用 `ghcr.io/yes999zc/rui:acceptance-f2abcef`。
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
