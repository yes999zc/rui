# 配置恢复（最小版）

目标：配置改坏、服务起不来时，用户还能进控制台，且**不会被静默覆盖配置**。

## 行为

1. **成功启动后**：把当前 `config.yaml` 复制为 `config.last-good.yaml`（0600）。
2. **启动时配置校验失败**：
   - 原文件保留，并复制为 `config.failed-<timestamp>`；
   - 自动尝试 `config.last-good.yaml`；
   - 没有 last-good 时使用 schema 默认配置（`Config()`）；
   - 在 `system.data_dir/config-recovery.json` 写一个恢复标记；
   - 服务继续启动，控制台顶部显示恢复 banner。
3. **banner 操作**：
   - `恢复上次可用`（只有 last-good 存在时可用）；
   - `恢复默认`（二次确认，只重置 config，不删 secrets/models/data）；
   - `下载坏配置`；
   - 恢复后提示“重启服务生效”（这一版不自动重启）。
4. **CLI**：
   ```bash
   rui config restore --last-good
   rui config restore --defaults
   ```
   两者都会先保留当前文件/备份，再写配置；之后需要重启服务。

## 文件

| 文件 | 说明 |
|---|---|
| `config.last-good.yaml` | 最近一次成功启动的配置快照 |
| `config.failed-<时间>` | 启动失败时的原始坏配置（保留下载/排查） |
| `config-recovery.json` | 控制台 banner 的恢复标记，0600 |

## 明确不做

- 不自动逐项修复 provider/device；
- 不自动重启服务；
- 不做完整 Safe Mode；
- 不删除 secrets、模型、音乐/记忆数据；
- 不用 `config.example.yaml` 覆盖真实配置。

## 安全

- 坏配置、last-good、恢复标记都按 0600 写入；
- 恢复 API 需要控制台 auth token；
- 恢复默认会先经过 `save_config` 的备份逻辑；
- `config.failed-*` 不会被自动清理。
