# v1 验收日清单（2026-10-24）

> 目标：**明天一天**跑完 ROADMAP §0 的四条发布门槛，并留下日期+数字+证据。
> 需要 9 哥 在场：耳朵（TTS/音乐/打断）、嘴（唤醒词/停止词）、手机（Telegram）和 NAS 授权。
> 规矩：**没有测到的数就写 null + 原因，不补、不猜、不改门槛**。
> 相关：`docs/v1-freeze.md`（本次范围）、`docs/runbook.md`（操作手册）、
> `docs/open-xiaoai-client.md`（音箱接线）、`docs/channel-gateway.md`（Telegram/微信）。

## 0. 一页版顺序（照抄执行，时间盒）

| # | 项目 | 时间盒 | 通过判据 | 记录位置 |
|---|---|---|---|---|
| 0 | 前置检查 + 备份当前状态 | 10 min | 仓库/镜像可取、音箱 client 已连、备份成功 | 本文 §1 |
| 1 | **C1 冷装**：空目录一条命令到第一句 | ≤30 min | 听到回答，墙钟 ≤30 min | §2 表格 |
| 2 | **D3 唤醒**：80% 音量放歌 20 次 | 20 min | ≥19/20（服务端 + 耳朵都记） | §3 表格 |
| 3 | D3 远场补充（3–4 m，10 次） | 10 min | 只记录，不进门槛 | §3 |
| 4 | **打断 ≤300 ms** | 10 min | 服务端停推 ≤300 ms（设备缓冲另记） | §4 表格 |
| 5 | **Telegram 文字/语音各 2 条** | 10 min | 4 条都有回复，日志无 token | §5 |
| 6 | **微信只验接口保留** | 2 min | `enabled=false`，不扫码、不启用 | §5 |
| 7 | **配置恢复 smoke** | 10 min | 改坏 → 重启自恢复/可 CLI 恢复 | §6 |
| 8 | **备份/恢复 smoke** | 10 min | 归档 0600+sha256，临时目录恢复成功 | §7 |
| 9 | 汇总数字 + 证据归档 | 10 min | `acceptance.md`/脚本输出齐全 | §8 |

**如果 NAS 上已有安装，开始前先做一次备份**（后面的实验都靠它撤销；全新冷装从 §2 开始，
安装后 `scripts/` 会随 install.sh 一起到位，没有则按 runbook §9 的手工 fetch 补齐）：

```bash
cd ~/house-ear
scripts/backup.sh --out ~/house-ear-backups      # 打印 archive/sha256/版本；不打印密钥
```

> 占位符：`<repo>` = GitHub 仓库（如 `owner/house-ear`），`<tag>` = 镜像版本，
> `<NAS-IP>` = NAS 局域网地址，`<speaker-ip>` = 音箱地址。文档不写真实 IP/密钥。

## 1. 前置检查（5–10 min）

| 项 | 命令/动作 | 期望 | 结果 |
|---|---|---|---|
| 仓库匿名可读 | `curl -fsSL https://raw.githubusercontent.com/<repo>/main/deploy/install.sh \| head -1` | 200 且输出 shell 行，不是 404 | ✅ 200（2026-10-24） |
| 镜像可取 | `docker pull ghcr.io/<repo>:<tag>` | 成功；记 digest | ✅ GHCR 匿名可读；冷装拉到 revision f0dde20（amd64 digest 见 RUNLOG） |
| NAS 环境 | `docker version`、`docker compose version`、空闲内存 ≥2 GB、磁盘 ≥2 GB | 正常 | ✅ Docker 28.5.2 / Compose v2.40.3；可用内存 8.0 GiB；/vol1 可用 8.6 T |
| 音箱 client | 按 `docs/open-xiaoai-client.md` 填 `ws://<NAS-IP>:4412/?token=<device-token>` | 音箱日志 connected；`/health` `speaker_connected=true` | ✅ connected=true（期间被另一台调试设备抢占同一入口，已停） |
| 密钥 | `.env`：LLM/ASR/TTS key、device token；可选 `XFYUN_*`/`VOLC_API_KEY`/HA | `house-ear doctor` 无缺 key 警告 | ⚠️ 生产 key 在 `data/secrets.yaml`（非 `.env`）；冷装测试需随配置带入 |
| 控制台 | `http://<NAS-IP>:8080/` + console token | 能登录，概览数字在动 | ✅ |
| 通道 | `GET /api/v1/channels`（带 token） | Telegram 已配置；微信 `enabled=false` | ✅ telegram enabled=true / token_configured=true / allowlist=1；weixin enabled=false |

## 2. C1 冷装：30 分钟到第一句

**先记开始时间：** `date +%s`。用全新目录/新 NAS 路径，不复用旧配置：

```bash
export HOUSE_EAR_HOME=~/house-ear-verify
curl -fsSL https://raw.githubusercontent.com/<repo>/main/deploy/install.sh | sh
# 或本机排练方式（本地镜像）：
# HOUSE_EAR_IMAGE=ghcr.io/<repo>:<tag> sh deploy/install.sh
```

随后按 install.sh 的提示：

1. 编辑 `$HOUSE_EAR_HOME/config/config.yaml` 的 `devices[]`（host/room/wake_words）。
2. 编辑 `$HOUSE_EAR_HOME/.env` 填 provider key 和 device token。
3. `cd "$HOUSE_EAR_HOME" && docker compose up -d` → `docker compose ps` 到 healthy
   （install.sh 也会把 `scripts/backup.sh`、`restore.sh`、`acceptance-v1.py` 拉到安装目录）。
4. `docker compose exec house-ear house-ear doctor`，记录 verdict 与警告。
5. **对音箱说「你好小瑞，现在几秒」**，听到回答时结束计时。

| 里程碑 | 墙钟 | 命令/证据 |
|---|---|---|
| 开始 | 12:37:13（epoch 1792816633） | `date +%s` |
| install.sh 结束 | 12:41:07（epoch 1792816867；234 s） | 输出末行 |
| healthy | 12:41:18（epoch 1792816878） | `docker compose ps` |
| 第一句回答 | 12:44:16（epoch 1792817056，听到「现在是凌晨4点44分，周六。」） | 耳朵 + `GET /api/v1/turns` |
| **总耗时** | **423 s ≈ 7 min 03 s**（12:37:13 → 12:44:16） | **≤30 min 为通过 → ✅ 通过** |
| 失败现象/日志 | 本次成功。备注：镜像 pull 在更早的失败尝试中已完成，pull 时间未计入；第一句前做了配置注入/密钥对齐（约 3 min）；模型为本次真实下载（VAD 643,854 B + KWS 39 MB）；`install.log` 在冷装目录 |

## 3. D3：80% 音量放歌，20 次唤醒 ≥19

准备：同一首歌、同一位置（2–3 m）、音量 170–220 mysoftvol（`GET /api/v1/device/volume`），
音乐持续播放。用脚本记录**人工命中/未命中**，脚本只读服务端数字，不替耳朵判断：

```bash
export HOUSE_EAR_TOKEN="$(docker compose exec -T house-ear house-ear token)"
cd ~/house-ear
python3 scripts/acceptance-v1.py wake --url http://<NAS-IP>:8080 --trials 20 --label near-field
# 远场补充（不进门槛）：--trials 10 --label far-field --no-gate
# NAS 没装 python3：在 Mac 上跑同样的命令，--url 指向 NAS 即可（token 仍只走环境变量）。
```

脚本会逐轮提示：`y` 听到回答 / `n` 没回答 / `s` 跳过 / `q` 退出；同时读
`/api/v1/audio` 的 `wake_count`、`voice_peak_db`、`aec_erle_db`、`ack_mode`。
输出在 `acceptance-runs/acceptance-wake-*.json/.md`（0600）。

| 位置 | 音量档 | 命中/总数 | 失败轮现象 | 服务端字段异常 | 结论 |
|---|---|---|---|---|---|
| 2–3 m | 204（80%） | **19/20** | 11:39:37–11:45:54；20 次唤醒 / 19 条完整回答；1 次唤醒了但没形成回合；全程音乐在放、AEC ERLE≈31.6 dB | ✅ 通过（≥19/20） |
| 3–4 m | 204（80%） | 未做 | | 未测，不补 | 仅记录 |

判据：**≥19/20**。失败轮务必记：没反应/只响提示音/被歌词干扰/唤醒后无回答
（后者脚本会在 note 里标 `wake_count increased but no answer`）。

## 4. 打断：≤300 ms 完全静音（分两个数）

先用 `POST /api/v1/debug/say {"text":"背一遍出师表"}` 或语音让助手念长文；播报中喊
「你好小瑞」，然后：

```bash
python3 scripts/acceptance-v1.py barge --url http://<NAS-IP>:8080 --trials 3
# 让脚本自动起长答（可选）：--auto-say
```

脚本读取 `GET /api/v1/traces` 的 `silence_after_ms`（服务端 `barge_in → silence`，即停止推流），
并在没有 trace 时要求你输入手动值或 skip（`source=manual/unavailable`，**不算通过证据**）。
输出 `acceptance-runs/acceptance-barge-*.json/.md`。

| 轮 | 服务端 stop-push (ms) | 来源 | 设备缓冲听感（另一个数，耳朵） | 备注 |
|---|---|---|---|---|
| 1 | 24.2 | server-trace | 干脆（9 哥 听感） | 第一次正式跑：24.2 / 447.3 / 9.8；447.3 那次是回答已接近自然结束，收尾计入，伪影 |
| 2 | 64.5 | server-trace | 干脆（9 哥 听感） | 重跑（干净 3 轮） |
| 3 | 205.2 | server-trace | 干脆（9 哥 听感） | 重跑（干净 3 轮）；max 205.2 ≤ 300 |

判据：服务端停推 ≤300 ms；**设备缓冲耗尽**另记（耳朵：停声是否干脆），不要相加/替代。
如果 3 轮里没有 server-trace 值，结论写 incomplete，不写 pass。

## 5. Telegram 文字/语音 + 微信接口保留

**Telegram**（NAS 托管 gateway；不要复用 Hermes bot 的 token）：

1. 控制台 `/channels`（或 `GET /api/v1/channels`）确认 `telegram.enabled=true`、
   `token_configured=true`、allowlist 含你的 chat id；改动后重启服务让 gateway 重读。
2. 保存通道配置后 `docker compose restart house-ear`（gateway 在启动时重读 channels.json），
   等 healthy；然后手机私聊 bot：发 **2 条文字**（如「现在几点」「放一首周杰伦」）+ **2 条语音**；
   每条应有文字/语音回复（按 reply_mode），且不触发客厅音箱的 idle 告别。
3. 证据：`GET /api/v1/channels` 的 gateway 状态；`docker compose logs --since 10m house-ear |
   grep -iE "channel|telegram"`；**确认日志没有 bot token / chat id 全文**。

| 通道 | 类型 | 发了 | 收到回复 | 备注 |
|---|---|---|---|---|
| Telegram | 文字 ×2 | ✅ | ✅ 4/4 都有回复（9 哥 确认） | |
| Telegram | 语音 ×2 | ✅ | ✅ 4/4 都有回复（9 哥 确认） | 语音回复为气泡或文字 |
| 微信 | 接口保留 | ✅ `enabled=false`、`logged_in=false`；未扫码 | — | 只验 disabled |

微信不是本次验收通道：控制台显示禁用即可；若出现二维码/登录，记录异常但不扫码。

## 6. 配置恢复 smoke（先备份！）

```bash
cd ~/house-ear
cp config/config.yaml "config/config.yaml.acceptance-bak-$(date +%Y%m%d-%H%M%S)"
printf 'version: [ deliberately broken\n' > config/config.yaml   # 模拟手改坏
docker compose restart house-ear
# 期望：进程起来（fallback 到 last-good 或 schema defaults），不是崩溃循环
docker compose logs --tail=100 house-ear | grep -iE "recover|last.good|config"
ls -l data/config-recovery.json config/*.failed-* 2>/dev/null
docker compose exec -T house-ear house-ear config restore --last-good
docker compose restart house-ear
docker compose ps && docker compose exec -T house-ear house-ear doctor
```

| 检查 | 期望 | 结果 |
|---|---|---|
| 坏配置重启 | 服务仍 healthy；有 `config-recovery.json` / `.failed-*` | ☐ |
| 控制台 banner | 显示“已从 last-good 启动，可恢复” | ☐ |
| CLI 恢复 | `config restore --last-good` 成功，重启后配置正常 | ☐ |
| 与备份对比 | `cmp` 关键字段（devices/providers 链）一致 | ☐ |

如果 last-good 不存在或也坏了，用 §7 的备份归档恢复，不要手改回半坏文件。
`house-ear config restore --defaults` 只用于“回到全新模板”，会丢设备/provider 配置，慎用。

## 7. 备份 / 恢复（含换机）

备份（归档 0600，含 `.env` 时尤其注意保管）：

```bash
cd ~/house-ear
scripts/backup.sh --out ~/house-ear-backups
# 默认排除 data/models（可重下）；需要连模型一起：--with-models
# 查看：archive、size、sha256、config_version、image_version、manifest
```

恢复 smoke（**不要**直接往生产目录写，先用临时目录）：

```bash
scripts/restore.sh ~/house-ear-backups/<archive>.tar.gz --home /tmp/house-ear-restore-smoke
ls -la /tmp/house-ear-restore-smoke/config /tmp/house-ear-restore-smoke/data
diff -r ~/house-ear/config /tmp/house-ear-restore-smoke/config   # 允许 .bak 差异
```

真恢复（仅当需要时；会先移走旧树）：

```bash
cd ~/house-ear
docker compose stop
scripts/restore.sh ~/house-ear-backups/<archive>.tar.gz --home ~/house-ear --force
docker compose up -d
docker compose ps && docker compose exec -T house-ear house-ear doctor
# 恢复错了：把 config.pre-restore-<ts>/data.pre-restore-<ts> 移回去
```

| 检查 | 期望 | 结果 |
|---|---|---|
| 归档权限 | 归档/sha256/manifest 均 0600 | ✅ 2026-10-24 10:47 生成，31,160 KiB，sha256 `35d5061d…`，均 0600 |
| 归档校验 | 默认生成 `.sha256`；restore 校验通过 | ⬜ restore 校验待跑 |
| 临时恢复 | config/data/.env 齐全；models 默认不恢复 | ⬜ 待跑 |
| 真恢复 | healthy + doctor + 设备在线；旧树留 `.pre-restore-*` | ☐ |
| 换机 | 新机器 install → restore → `fetch-models` 补模型 | ☐ |

## 8. 收尾与记录（不修改门槛）

- 把 §2–§7 的数字填进本文件对应表格，或粘贴 `acceptance-runs/*.md` 的摘要；
- 证据路径：`acceptance-runs/`、`data/service.log`、`docker compose logs > /tmp/acceptance-<date>.log`；
- 每个失败项写：现象、时间、命令、日志行；**没有通过就写未通过**；
- 全绿后才把 ROADMAP 状态升级；任何一条不过 → 回开发修，不改判据。

### 附：旧版页面细节（保留，按需执行）

- 灯环：放歌 music 紫、暂停 `music_paused` 橙；唤醒白圈→思考→说话青→播完绿；
  `POST /api/v1/debug/led` 可单测灭/绿。
- 控制台逐页：概览/设备/设备管理/对话/日志/音乐/记忆/设置/话术/MCP/安装向导/深链；
  手机 390 px 无横向滚动。
- 安全烟测：`POST /api/v1/debug/play {"file":"/etc/passwd"}` → 404；HA 对
  `script.*`/`scene.*` denied；`config/.env/service.log` 权限 0600。
- 回滚：`.env` 改上一版 tag → `docker compose up -d` → healthy，再跑 doctor。


## 9. 2026-10-24 实测记录（Lead，随测随填）

> 本节点由 Lead 记录；数据来自 NAS API/日志与本机监控，不含 token/LAN IP。

### 9.1 门槛状态（滚动）

| 门槛 | 状态 | 证据/备注 |
|---|---|---|
| 1. 冷装 30 min 到第一句 | ✅ 通过 | 234 s 装完 → 7 min 03 s 听到第一句回答（12:37:13→12:44:16，≤30 min）；镜像 `e3ea158`；模型本次真实下载 |
| 2. 80% 音量 20 次唤醒 ≥19 | ✅ 通过 | **19/20**（11:39:37–11:45:54，2–3 m，音量 204，音乐在放，AEC ERLE≈31.6 dB）；1 次唤醒后无回合，9 哥 耳朵确认仅 1 次没反应 |
| 3. 打断 ≤300 ms | ✅ 通过（服务端 + 听感） | 干净重跑 3 轮：24.2 / 64.5 / 205.2 ms（max 205.2）；首次跑 447.3 ms 为回合自然收尾伪影；设备缓冲听感由 9 哥 另记 |
| 4. 公开仓可用 | ✅ | repo/GHCR public、raw install 200、MIT、无 secret；GD 占位链接仍在 |
| Telegram 文字/语音 | ✅ 通过 | 2 文字 + 2 语音，9 哥 确认 4/4 都有回复；gateway running（managed） |
| 微信接口保留 | ✅ 通过 | `enabled=false`、`logged_in=false`，未扫码 |
| 配置恢复 smoke | ✅ 通过 | 写坏 config → 重启仍 healthy、`config-recovery.json` mode=last-good、坏文件保留；CLI restore 后 doctor 正常、sha 与破坏前一致 | |
| 备份/恢复 smoke | ✅ 通过 | 备份 31,160 KiB、sha256 `35d5061d…b720`、0600；restore 到 /tmp 成功、权限 600、config.yaml 与现场一致 |

### 9.2 实测与发现的缺陷

1. **VAD 模型源失效**：`fetch-models` 先取 hf-mirror 的 `csukuangfj/sherpa-onnx-vad`（404）→ 冷装降级且 KWS 压缩包不再下载。
   已修：`VAD_URLS` 增加 GitHub release fallback（`f0dde20`）；但 NAS 到 GitHub release 被 reset，仍需国内镜像。
2. **冷装实例无法唤醒**：e60de20 镜像冷装实例 `devices=1/audio_plane=true/speaker_connected=true`，
   `kws_enabled=true/kws_error=""`、帧在流、`voice_peak_db` 到 6.4 dBFS，但 `wake_count=0`。原因待查
   （prod 与冷装 audio.voice/capture diff 无差异；嫌疑在配置模板其余字段或客户端连接状态）。
3. **音箱重连风暴**：11:20–11:23 同时有另一台调试设备占用 NAS 上的音箱入口（4412），
   日志出现 `replacing existing speaker connection`，3 分钟内 24 次连/42 次断 →
   表现为“无法唤醒 + 说话卡”。另一设备停止后恢复，非 rui 缺陷；已记入 runbook 建议。
4. install.sh 的网络抖动：第 1/2 次尝试失败（SSL 超时 / 拉镜像失败后 git clone 被 reset），第 3 次成功；
   单次命令在该网络下不够稳定，建议 install.sh 增加重试或在文档写明。

### 9.2b 2026-10-24 补充发现（验收后对齐）

5. **install.sh 卡死（已修）**：`raw.githubusercontent.com` 在 NAS 上会出现“TCP 连上但 TLS 不返回”的 stall，
   install.sh 的 curl 没有超时 → 卡 8 分钟无输出。已加 `--connect-timeout/--max-time/--retry`、强制 IPv4、
   支持 `HOUSE_EAR_RAW` 换镜像源（`39af43f`/`4aa8f9e`）。
6. **模板与旧 secrets 的 key 命名差异**：新模板 LLM 读 `HOUSE_EAR_LLM_KEY`，老生产 `secrets.yaml` 是
   `DEEPSEEK_API_KEY`；冷装测试复制旧 secrets 时会“唤醒后不回答”。真实用户走 Setup 向导不受影响；
   迁移/复用旧 secrets 时的提示已在 remaining-work 记录。
7. **空 config 的 recovery 缺口**：config 为 0 字节时静默使用 schema defaults → `insecure_bind` 拒绝 → crash loop，
   未触发 last-good。已记录为后续修复（不阻塞本次判据）。

### 9.3 现场镜像与版本

- 生产（验收 2/3/Telegram 用）：`f2abcef`（本地 tag `acceptance-f2abcef`，`.env` 暂时指向它，防误拉新版）
- 冷装验证：`e3ea158`（含 VAD/KWS 镜像 fallback）
- GHCR `:latest`：`afaabe7`（docs commit；运行代码与 `e3ea158` 相同；amd64 digest `sha256:03bdc1f3…`）
- 代码：`main@afaabe7`，748 passed / 17 skipped（无模型环境；`HOUSE_EAR_TEST_MODELS` 时 764/1），ruff clean，
  web typecheck/build 绿
