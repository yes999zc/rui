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
| 仓库匿名可读 | `curl -fsSL https://raw.githubusercontent.com/<repo>/main/deploy/install.sh \| head -1` | 200 且输出 shell 行，不是 404 | ☐ |
| 镜像可取 | `docker pull ghcr.io/<repo>:<tag>` | 成功；记 digest | ☐ |
| NAS 环境 | `docker version`、`docker compose version`、空闲内存 ≥2 GB、磁盘 ≥2 GB | 正常 | ☐ |
| 音箱 client | 按 `docs/open-xiaoai-client.md` 填 `ws://<NAS-IP>:4412/?token=<device-token>` | 音箱日志 connected；`/health` `speaker_connected=true` | ☐ |
| 密钥 | `.env`：LLM/ASR/TTS key、device token；可选 `XFYUN_*`/`VOLC_API_KEY`/HA | `house-ear doctor` 无缺 key 警告 | ☐ |
| 控制台 | `http://<NAS-IP>:8080/` + console token | 能登录，概览数字在动 | ☐ |
| 通道 | `GET /api/v1/channels`（带 token） | Telegram 已配置；微信 `enabled=false` | ☐ |

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
| 开始 | | `date +%s` |
| install.sh 结束 | | 输出末行 |
| healthy | | `docker compose ps` |
| 第一句回答 | | 耳朵 + `GET /api/v1/turns` |
| **总耗时** | | **≤30 min 为通过** |
| 失败现象/日志 | | `docker compose logs --tail=200 house-ear` |

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
| 2–3 m | | /20 | | | ☐ |
| 3–4 m | | /10 | | | 仅记录 |

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
| 1 | | server-trace/manual | | |
| 2 | | | | |
| 3 | | | | |

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
| Telegram | 文字 ×2 | ☐ | ☐ | |
| Telegram | 语音 ×2 | ☐ | ☐ | 语音回复为气泡或文字 |
| 微信 | 接口保留 | `enabled=false`、`logged_in=false`；**不扫码** | — | 只验 disabled |

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
| 归档权限 | 归档/sha256/manifest 均 0600 | ☐ |
| 归档校验 | 默认生成 `.sha256`；restore 校验通过 | ☐ |
| 临时恢复 | config/data/.env 齐全；models 默认不恢复 | ☐ |
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
