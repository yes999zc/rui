# 验收日清单（C1 + D3 + 灯环 + 控制台肉眼）

> 2026-10-23 整理。目标：**一天内**把 ROADMAP §0 的四条发布门槛跑完，每一步都留下数字。
> 需要 9哥 在场：耳朵（TTS/音乐/打断听感）、嘴（唤醒词、停止词）、以及同意动 NAS。
> 自测项（控制台、容器、看门狗）在验收日前应已由 Lead 跑过，见 `docs/console-audit.md`、
> `docs/runbook.md` §11。

## 0. 前置条件（验收前一天确认）

| 项 | 检查 | 命令/证据 |
|---|---|---|
| 仓库匿名可读 | `curl -fsSL .../deploy/install.sh \| head -1` 返回 200，不是 404 | 当前**未满足**：仓库仍私有 |
| 镜像已发布 | `docker pull ghcr.io/yes999zc/house-ear:<tag>` | CI `docker-publish.yml` 在 main/v* 上推 GHCR |
| NAS 可用 | SSH 进 NAS，`docker version` 正常，`docker compose` 可用，空闲内存 ≥ 2 GB | |
| 音箱可 SSH | 从 NAS `ssh root@<speaker-ip>` 能进（dropbear） | |
| 密钥齐全 | NAS `.env`：`HOUSE_EAR_LLM_KEY`/`ASR_KEY`/`TTS_KEY`（讯飞可选 `XFYUN_*`）、`HOUSE_EAR_DEVICE_TOKEN`、HA token（可选） | `docker compose exec house-ear house-ear doctor` |

## 1. C1 冷装：30 分钟到第一句

**记录开始时间。** 空目录、无旧配置：

```bash
curl -fsSL https://raw.githubusercontent.com/yes999zc/house-ear/main/deploy/install.sh | sh
# 或本机排练过的方式：HOUSE_EAR_IMAGE=ghcr.io/...:<tag> sh deploy/install.sh
```

随后：

1. 编辑 `~/house-ear/config/config.yaml` 的 `devices[]`：host=音箱 IP、room、wake_words。
2. 编辑 `~/house-ear/.env` 填 key。
3. `cd ~/house-ear && docker compose up -d`，等 `docker compose ps` 显示 healthy。
4. `docker compose exec house-ear house-ear doctor` 记下 verdict。
5. **对音箱说「你好小瑞，现在几点」** —— 听到回答即计时结束。

**判据**：从开始到第一句 ≤ 30 分钟。记录：install.sh 耗时 / health 变 healthy 耗时 / 第一句耗时（墙钟）。
本机容器排练数字（不含真下载）：install.sh **3 s 热 / 14 s 冷路径**、health 1 s、healthy 6 s；NAS 上主要成本是
镜像拉取 + 272 MB 模型下载，按 NAS 网速估算留足余量。失败就回来改 install.sh，不改门槛。

## 2. D3：80% 音量放歌 20 次唤醒 ≥ 19

准备：`GET /api/v1/device/volume` 记下起始值；用手机放歌/或让助手放歌（音乐面板「随便放点」）保持 **170–220 档**。

**测法**（同一首歌、同一位置）：

1. 站正常距离（2–3 m），说「你好小瑞」→ 听到「在呢。」（或提示音）记 1 次命中；
2. 每轮之间等 3 s，间隔中音乐继续；
3. 20 轮后统计 `19/20` 为通过；失败轮记录 `GET /api/v1/audio` 里 `voice_peak_db`、`wake_count`。

**记录表**：位置 / 音量档 / 20 次命中数 / 失败轮现象（没反应/只响提示音/被歌词干扰）。
另外补 B2 遗留：**3–4 m 远处**同法 10 次，只记数字不进门槛。

## 3. 打断：≤ 300 ms 完全静音

1. 让助手念一段长回答（`POST /api/v1/debug/say {"text":"背一遍出师表"}`），
2. 播报中直接喊「你好小瑞」（不要等句子结束），
3. 耳朵判断停声是否干脆；同时抓 `GET /api/v1/turns` 的 `barge` 日志行与 `playback_lead_ms`。

**判据**：用户开口后 ≤ 300 ms 完全静音（服务端停推 + 设备缓冲分开记）。
历史数字：丢排队 7.6 s、剩余 lead ≈420 ms；本轮要的是「按 300 ms 口径量到的新数」。

## 4. 灯环（音乐态）

1. 放歌 → 灯环应为 **music 颜色**（`#481860` 紫），暂停应为 `music_paused`（`#602800` 橙）；
2. 说话中断 → 唤醒白圈 → 思考短脉冲 → 说话青 → 播完绿；
3. `POST /api/v1/debug/led {"effect":"off"}` 后灭，`{"effect":"listening"}` 后绿。

记录：每个状态肉眼确认 ✓/✗，异常抓设备截图或日志 `led:` 行。

## 5. 控制台肉眼（手机 + 桌面各一遍）

| 页面 | 看什么 | 通过标准 |
|---|---|---|
| 概览 | 状态/音频面/试一试 | 数字在动；打字一句能真出声 |
| 设备 | 客厅 · living 那行 + 音量 | 音量滑块写回后设备真变 |
| 设备管理 | 点设备 → 表单；改房间保存 | 保存后重启、房间名变、灯环配色没丢 |
| 对话/日志 | 最近对话、日志 36 行有效（隐藏 level 噪声） | 翻页不卡、无 console 报错 |
| 音乐 | 播放/暂停/继续/下一首/停止 | 每个按钮真生效（听） |
| 记忆/设置/话术 | 记忆条目、设置字段、话术搜索 | 改一条话术保存 → 下一句立刻变 |
| 安装向导 | `/setup` 五步 | 全绿；手机 390px 无横向滚动 |
| MCP / HA | HA 状态 | 已连接、工具/实体数合理 |
| 深链 | 刷新 `/panel/music`、收藏 `/panel/devices` | 不再 404/不被弹回向导 |

## 6. 收尾

- `docker compose logs --tail=200 house-ear` 无 ERROR 级异常；
- `docker compose restart` 后 1 s HTTP、6 s healthy；
- 回滚演练：`.env` 里换上一版 tag → `docker compose up -d` → healthy（记录耗时）；
- 把上面所有数字按日期补进 `docs/HANDOFF.md`（新一节）和 `docs/ROADMAP.md` 状态；
- 若任一条未过：**记下现象+证据+下一步**，不修改门槛。

## 7. 控制台 2.0 与安全（2026-10-23 新增）

| # | 检查 | 期望 |
|---|---|---|
| 7.1 | `house-ear setup` 的输出 | 打印 `console token: …`；`docker compose restart` 后控制台要求 token（401 时页面有输入框） |
| 7.2 | 不带 token 访问 `http://NAS:8080/api/v1/turns` | 401；带 `X-Auth-Token` 200 |
| 7.3 | 空 token + `0.0.0.0` 启动 | 服务拒绝启动并打印原因（或 `allow_anonymous: true` 显式放行） |
| 7.4 | 配置中心 4 类 | 底子/音箱/外部资源/工具四组都有字段；每个字段有 hint（改这个能达到什么效果）与「保存生效/重启生效」徽章 |
| 7.5 | 改一个 save 字段（如 `assistant.reply_max_chars` 120→160） | 立即保存，下一句生效；无需重启 |
| 7.6 | 改一个 restart 字段（如 `audio.playback.queue_ms`） | 提示重启字段；点「仅重启音频面」后 3 s 内音箱恢复（`/health` speaker_connected true） |
| 7.7 | 语言切换 中文/EN | 全站文案与 `<html lang>` 切换，刷新记住 |
| 7.8 | 手机 390 px 打开概览/助手与会话/音箱参数 | 无横向滚动，保存条在底部，抽屉可滚动 |
| 7.9 | 音乐源切到 navidrome | `secrets_path`/`cache_dir` 自动填 `<data_dir>/…`；「测试连接」返回 ok/延迟 |
| 7.10 | 安全烟测 | `POST /api/v1/debug/play {"file":"/etc/passwd"}` → 404；`ha_call_service` 对 `script.*`/`scene.*` → denied；`service.log`/config 权限 0600 |

