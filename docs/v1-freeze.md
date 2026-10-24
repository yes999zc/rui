# v1 freeze — 2026-10-24

> 目标：冻结新功能，只做 v1 四道发布门槛。
> **状态：2026-10-24 验收日四道门槛全部通过；等待部署 NAS 新镜像 + 打 tag/Release。**

## 结果（验收日）

| 门槛 | 结果 | 证据 |
|---|---|---|
| 1. NAS 冷装 30 min 到第一句 | ✅ **423 s ≈ 7 min 03 s**（12:37:13→12:44:16，镜像 `e3ea158`） | `docs/acceptance.md` §2；`/vol1/rui-verify/install.log` |
| 2. 80% 音量放歌 20 次唤醒 ≥19 | ✅ **19/20**（音量 204，2–3 m，AEC ERLE≈31.6 dB） | `docs/acceptance.md` §3；验收工作区 `runs/`、`evidence/wake-monitor.log` |
| 3. 打断 ≤300 ms | ✅ **24.2 / 64.5 / 205.2 ms**（max ≤300）；设备缓冲听感干脆 | `docs/acceptance.md` §4 |
| 4. 公开仓可用 | ✅ repo/GHCR public、README 双语+截图、MIT、无 secret | — |

附加（同日通过）：Telegram 文字/语音 4/4、微信接口保留、配置恢复 smoke、备份/恢复 smoke。

## 验收日修复/发现

- 修复：VAD 上游仓失效 → GitHub fallback（`f0dde20`）；GitHub release 被 reset → ghproxy 镜像 fallback（`e3ea158`）；
  install.sh fetch 无超时/未强制 IPv4 → 卡死（`39af43f`/`4aa8f9e`，并支持 `RUI_RAW` 换源）。
- 待修（不阻塞发布）：config 被清空成 0 字节时会 crash loop（未走 last-good）；冷装测试时新模板
  `RUI_LLM_KEY` 与老 `secrets.yaml` 的 `DEEPSEEK_API_KEY` 命名差异（向导不受影响）。
- 未做：3–4 m 远场唤醒正式计数（只记录项）。

## 部署 ✅（2026-10-24 13:05 完成）

1. ✅ NAS `docker pull ghcr.io/yes999zc/rui:0.1.0`（digest `sha256:694fc924…`，revision `1966db6`）
2. ✅ prod `.env` 的 `RUI_IMAGE` 切到 `:0.1.0` → `docker compose up -d --force-recreate`
3. ✅ 验证 `/health`（version 0.1.0）、doctor、音箱在线、Telegram gateway；回滚 tag 保留
4. ✅ tag `v0.1.0` 已推；GitHub Release + README Buy Me a Coffee 真实链接待补

## 冻结期间不做

- Agent/OpenClaw/Hermes 接入；第二台设备；完整 Doctor Fix；新渠道；大重构。
