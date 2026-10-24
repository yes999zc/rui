# HANDOFF — house-ear / 小瑞

> 接手入口：先读这一份。历史开发日志（2026-10-20 → 2026-10-24，旧 §1–§12.35）已归档在
> `docs/archive/HANDOFF-history-to-2026-10-24.md`；正文里出现的旧 § 编号都指那份归档。
> **目标载体是 NAS**（Bakey-NAS / fnOS）：生产形态是 NAS 上的 Docker 容器；Mac 只是开发/操作端。

## 0. 一分钟了解

| 项 | 当前值 |
|---|---|
| 项目 | 自托管语音终端：LX06 音箱 + FastAPI 控制面 + 独立音频面 + Vue 控制台；MIT；公开仓 `yes999zc/rui` |
| 版本 | **v0.1.0**（tag `v0.1.0`，release commit `1966db6`），2026-10-24 已部署 NAS |
| 生产镜像 | `ghcr.io/yes999zc/rui@sha256:694fc924…`（tag `0.1.0`；回滚 tag `acceptance-f2abcef`） |
| Release | https://github.com/yes999zc/rui/releases/tag/v0.1.0 |
| 质量 | 749 passed / 17 skipped（无模型环境）、765/1（设 `HOUSE_EAR_TEST_MODELS`）；ruff clean；web typecheck/build 绿 |
| v1 四门槛 | 冷装 423 s；唤醒 **19/20**；barge 24.2/64.5/205.2 ms；公开仓 ✅ |
| 附加验收 | Telegram 4/4、微信接口保留、配置恢复 smoke、备份/恢复 smoke ✅ |

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
- 设备差异全部收在 `src/house_ear/device/`（`DeviceAdapter` + `Capability` + `registry.register`）；
  控制面/音频面不为品牌加分支。新增机型 = 一个 adapter（详见 `remaining-work.md` §I/§K）。
- 播放仲裁：提示音 > 回答 > 提醒 > 音乐；同一时刻一个 owner。
- 渠道：`src/house_ear/channels/` 独立进程（Telegram / 微信 iLink），复用 `POST /api/v1/chat`，不依赖音频面。

## 2. 环境与部署

| 项 | 值 |
|---|---|
| 生产主机 | Bakey-NAS（fnOS，x86_64，Docker 28.5.2 / Compose v2.40.3） |
| 安装目录 | `/vol1/house-ear`（compose 项目）；配置 `config/config.yaml`；数据 `data/`；模型 `data/models/` |
| 备份 | `/vol1/house-ear-backups/`（0600，含 `.env`；最近：`house-ear-backup-20261024-125842.tar.gz`，sha256 `9c879d60…`） |
| 冷装证据 | `/vol1/house-ear-verify/`（269 MB，可删；`install.log` 与冷装 service.log 已拷到验收工作区） |
| 端口 | 控制台 `8080`、设备入口 `4412`（host network）；`allow_no_token: false` |
| 开发机 | Mac：仓库 + `.venv`（Python 3.13）+ Node 22；**无 docker CLI**（只有 OrbStack 的 `orb/orbctl`），compose/冷装只能在 NAS 上跑 |
| GHCR | `0.1.0` / `0.1` / `latest` / `sha-1966db6`（多架构 amd64+arm64） |

## 3. 已定决策（改前先问 9 哥）

1. **目标载体 NAS**：生产只跑在 NAS 容器里；Mac 不作为运行宿主。
2. **ASR**：本地 SenseVoice int8 优先；NAS 现配置为火山 ASR 单链路（省内存），本地模型保留可切回。
3. **TTS**：讯飞主用、火山兜底；`ack.mode: tts`（NAS `ack.max_ms: 800`），冷装模板同链。
4. **LLM**：`deepseek-flash`，thinking 关；冷装模板读 `HOUSE_EAR_LLM_KEY`，旧部署 `secrets.yaml` 用
   `DEEPSEEK_API_KEY`（待 P1.2 统一）。
5. **模型不进镜像**：`fetch-models` 下载到 data 卷；源顺序 hf-mirror → GitHub release → ghproxy 镜像
   （VAD 上游 hf 仓 2026-10-24 已失效，GitHub release 是当前主源）。
6. **设备音量默认不接管**：`devices[].volume: 0` 表示归原厂；读写只在显式 API/测试调用。
7. **唤醒应答**：当前 `tts` 模式（短句）；老项目 earcon 差异未完全解释，改动前先看归档 §6.5。
8. **厂家让位**：说「小爱同学」时不开我们的会话、不播提示音；我们的唤醒词立即接管。
9. **音量不自动写**，厂商会自己改 mysoftvol（见过 160→255→160）。
10. **渠道**：NAS 只启用 Telegram（托管 gateway，出网走本机 mihomo SOCKS）；微信只保留接口
    `enabled=false`。同一 bot 只能一个长轮询消费者；**同一 4412 入口只能一个音箱客户端**。
11. **配置恢复**：last-good + 启动回退 + 控制台 banner + `house-ear config restore`；
    密钥/用户数据 0600；坏配置不静默覆盖（空文件例外，见 P1.1）。
12. **公开仓硬约束**：不硬编码 key / 内网 IP / 私人路径；MIT；发布走 GHCR 多架构。
13. **生产变更需 9 哥 明确点头**（部署/重启 NAS）；测试期间也不要同时跑 Mac 外部 gateway。

## 4. 运维手册（详见 `docs/runbook.md`）

```bash
cd ~/house-ear                                  # NAS 上
docker compose ps
curl -s http://127.0.0.1:8080/health
docker compose exec -T house-ear house-ear doctor
docker compose logs --tail=100 house-ear
docker compose exec -T house-ear house-ear token    # 只留在 shell 里，不要回显/上传
scripts/backup.sh --out ~/house-ear-backups
scripts/restore.sh <archive> --home /tmp/house-ear-restore-smoke
docker compose exec -T house-ear house-ear config restore --last-good
```

- 升级/回滚：改 `.env` 的 `HOUSE_EAR_IMAGE`（当前 `ghcr.io/yes999zc/rui:0.1.0`）→ `docker compose up -d`；
  回滚用 `ghcr.io/yes999zc/rui:acceptance-f2abcef`。
- 验收记录器：`python3 scripts/acceptance-v1.py wake|barge --url http://<NAS-IP>:8080 --out <dir>`（纯 stdlib）。
- 开发：`.venv/bin/python -m pytest -o addopts="" -q`；`ruff check .`；`cd web && npm run typecheck && npm run build`。
- 模型测试：`HOUSE_EAR_TEST_MODELS=/path/with/silero_vad.onnx+model dirs`。

## 5. 已知问题与欠账（与 `ROADMAP` §0.2 一一对应）

| ID | 问题 | 状态 |
|---|---|---|
| P1.1 | config 被清空成 0 字节 → schema defaults → `insecure_bind` 拒绝 → crash loop，未走 last-good | 待修 |
| P1.2 | 新模板 `HOUSE_EAR_LLM_KEY` vs 旧 `secrets.yaml` 的 `DEEPSEEK_API_KEY`，复用旧 secrets 会“唤醒后不回答” | 待修 |
| P1.3 | `fetch-models` 的 httpx `timeout=None`，直连 GitHub 失败会拖住 fallback | 待修 |
| P1.4 | 验收脚本输出路径曾在容器内 `/data/...`，容器重建后 barge JSON 丢失（本次从输出重建记录） | 待修 |
| P1.5 | GHCR 发布 workflow 不跑测试（tag push 直接构建镜像） | 待修 |
| P2.1 | **真·陌生人冷装未验证**：验收冷装用了缓存镜像 + 复制生产 config/secrets + 手工 key 对齐；真实用户路径（公开命令 → Setup 向导填 key → 加设备 → 自动下模型）未从头走 | 待做 |
| P2.2 | 远场 3–4 m 唤醒正式计数（20 次）未做 | 待做 |
| P2.3 | barge 设备缓冲只有耳朵“干脆”，无量化值 | 待做 |
| P2.4 | 微信 iLink 真机扫码/语音未验；QR 登录无服务端取消端点 | 待做 |
| P2.5 | 音乐：真实 NAS Navidrome 凭据确认、收藏/口味 UI、farewell 静音续歌边角、音量对账 | 待做 |
| P2.6 | 控制台手机卡片化、控件尺寸、CJK webfont、Logo GIF | 待做 |
| P2.7 | 备份/恢复真恢复 + 换机演练（install → restore → fetch-models） | 待做 |
| P2.8 | NAS→GitHub 网络仍偶发不稳；Telegram 依赖 mihomo 节点运行时状态；3 天长稳/断电自起未验证 | 已知 |
| P3.x | 第二台设备、多类型音箱标准/插座插头、EchoMuse 设备管理、Agent/新渠道/Doctor Fix、安全三项 | 见 ROADMAP §0.3 |

其它：README 的 Buy Me a Coffee 仍是占位链接（等 9 哥 给真实链接）；音箱 root 密码受 squashfs 只读限制。

## 6. 最近时间线（细节在归档）

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
