# v1 freeze — 2026-10-24

> 目标：冻结新功能，只做 v1 四道发布门槛。  
> 9哥 明天一次性完成需要真机/手机的部分；其余由 Lead/agents 今晚完成。

## 今晚完成（不需要 9哥）

- [x] 配置恢复最小版：last-good + 启动回退 + 控制台 banner + CLI restore。
- [x] 音箱接入信息后端/前端：`GET /api/v1/devices/{id}/connect` + Setup 页复制 URL/token（agent）。
- [x] open-xiaoai client 分步文档：`docs/open-xiaoai-client.md`。
- [x] 用户数据文件权限：memory / reminders / music aliases / library index / history / HA entities → 0600。
- [x] backup/restore 脚本 + acceptance 辅助脚本 + runbook（p15）。
- [x] 安全响应头、鉴权失败日志、安装脚本连接信息提示。
- [x] 全量测试 **762 passed / 1 skipped**、ruff clean、UI typecheck/build 绿；已提交并 push。
- [x] 不部署 NAS（按 9哥 要求）；NAS 当前仍是上一版镜像。

## 明天需要 9哥（验收日）

1. **冷装**：全新目录 + 一键安装，记录 30 分钟内到第一句。
2. **D3 唤醒**：80% 音量放歌，20 次唤醒 N/M；3–4 米远场再一组。
3. **打断 ≤300 ms**：按服务端停推 + 设备缓冲耗尽分开量。
4. **Telegram**：文字 + 语音各 2 条（NAS 托管 gateway）。
5. **微信**：只验证接口保留（当前 enabled=false，不需要扫码）。
6. **配置恢复 smoke**：临时把 config 改坏 → 重启 → banner/恢复；或只跑 CLI 单测。
7. **验收记录**填入 `docs/acceptance.md` / acceptance 脚本输出。

## 冻结期间不做

- Agent/OpenClaw/Hermes 接入；
- 第二台设备；
- 完整 Doctor Fix；
- 新渠道；
- NAS 部署/升级（等验收后统一）。

## 结果（2026-10-24 12:45，验收日）

- 门槛 1 冷装：✅ 423 s（7 min 03 s）到第一句（镜像 `e3ea158`；见 `acceptance.md` §2 备注）
- 门槛 2 唤醒：✅ 19/20（80% 音量放歌、2–3 m）
- 门槛 3 打断：✅ 服务端 24.2 / 64.5 / 205.2 ms（max ≤300）；设备缓冲听感干脆
- 门槛 4 公开仓：✅ 维持通过
- 附加：Telegram 4/4、微信接口保留、配置恢复 smoke、备份/恢复 smoke 均通过
- 未做/后续：远场 3–4 m 唤醒计数；清空 config 的 recovery 缺口修复；NAS 新镜像部署（等 9 哥 明确指令）
