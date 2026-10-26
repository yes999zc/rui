# TLS 是否必需：三组安装矩阵（0.7.5，方法与预期）

> 状态：**方法已定，实测待下一张卡执行**（夜间纪律：不起容器、不拉镜像、不碰真机、不出声）。
> 本文只定义「怎么测、看什么、怎么判」，不替实测下结论。
> 相关事实：`rui-dot2-bridge/src/rui_dot2_bridge/device_link.py:261`（`tls_port` 开启时要求
> `secrets/server.pem|key`，缺失抛 `RuntimeError: missing TLS material`）；`adapter.py:953`
> 读 `DOT2_SECRETS_DIR`（compose 挂到 `/opt/rui-secrets`）。`examples/two-speakers/compose.yaml`
> 本版已加 `./secrets:/opt/rui-secrets:ro` + `DOT2_SECRETS_DIR`。

## 必须写清的风险句

**自签证书只解决「音频平面能启动」；真机是否接受，取决于设备信任的 CA，自签不能保证。**
设备以固定 ServerName `echomuse-controller` 校验；原 controller 证书无法重建，丢失时设备需要重新配网。
因此本矩阵即使三组全绿，也只能说明「平面侧不缺材料」，不能说明「真机链路可用」。

## 三组矩阵

统一前置：

- 同一台机器、同一份 `examples/two-speakers/`、同一镜像 digest（记录 `docker image inspect` 的 RepoDigests）。
- 配置里 dot2 设备**开启 `tls_port`**（例如 4433），并设 `DOT2_EM_TOKEN`；lx06 按需配置。
- 每组开始前 `docker compose down`，清理 `./secrets/`（除非该组要求有证书）。
- 观察点：`docker compose up -d` 退出码、`docker compose logs rui` 中 `missing TLS material`/`audio_plane` 行、
  `curl -s localhost:8080/health` 的 `audio_plane`、`adapter_plugins`、`device_errors`。
- 每组重复 2 次（排除首次拉镜像/初始化噪声），记录两次是否一致。

### 矩阵 ①：只挂 dot2（`tls_port` 开启），无 secrets

| 项 | 内容 |
|---|---|
| 文件 | 只保留 `plugins/rui_dot2_bridge`；`./secrets/` 不存在或为空 |
| 命令 | `docker compose up -d`；`docker compose logs --tail=120 rui`；`curl -s localhost:8080/health` |
| 预期（待实测） | 平面启动失败或 `audio_plane=false`，日志出现 `missing TLS material`（若实际为 true，则说明 TLS 不是启动必需，需改 README 表述） |
| 判定 | 以日志与 `/health` 为准；只记录，不调设备 |

### 矩阵 ②：两桥都挂，无 secrets

| 项 | 内容 |
|---|---|
| 文件 | `plugins/` 同时含 `rui_dot2_bridge` 与 `rui_xiaoai_bridge`；`./secrets/` 为空 |
| 命令 | 同 ①；额外看 `adapter_plugins.registered` 是否含 `["dot2","lx06"]` |
| 预期（待实测） | 与 ① 相同（dot2 仍因 TLS 失败）；lx06 不应因 dot2 的 TLS 缺失而被拖垮，需看日志归属 |
| 判定 | 分清「dot2 平面失败」与「整个 audio_plane 失败」；记录 `device_errors` |

### 矩阵 ③：两桥都挂，有自签 secrets

| 项 | 内容 |
|---|---|
| 文件 | `plugins/` 两桥；`./secrets/server.pem` + `server.key` 由 `bash scripts/make_dev_tls.sh` 生成（SAN=`DNS:echomuse-controller`） |
| 命令 | `bash scripts/make_dev_tls.sh`（在 `examples/two-speakers/` 下用 `bash ../../scripts/make_dev_tls.sh`）→ `docker compose up -d` → `docker compose logs --tail=120 rui` → `curl -s localhost:8080/health` |
| 预期（待实测） | 平面可启动（`audio_plane=true`）；日志无 `missing TLS material`；真机是否连上**不在本组判定**（需白天的真机项） |
| 判定 | 只判「平面启动」；真机信任另立真机卡，按 §风险句记录 |

## 记录表（每组一行）

| 组 | 日期 | 镜像 digest | secrets | up 退出码 | audio_plane | registered | device_errors | 日志关键行 | 结论 |
|---|---|---|---|---|---|---|---|---|---|
| ① |  |  | 无 |  |  |  |  |  |  |
| ② |  |  | 无 |  |  |  |  |  |  |
| ③ |  |  | 自签 |  |  |  |  |  |  |

判定规则：

1. 只有 ③ 出现 `audio_plane=true` 且日志无 `missing TLS material`，才能写「TLS 材料是平面启动前提」。
2. 若 ①/② 也 `audio_plane=true`，说明当前构建并不要求 TLS 才能起平面；README 与 HANDOFF 的表述按此统一。
3. 若 ③ 仍失败，先看是证书路径/权限（`/opt/rui-secrets` 只读、`server.key` 600）还是 SAN/ServerName 不匹配，再决定是否改脚本。
4. 真机连不上不属于本矩阵失败；按风险句记录「设备不信任自签 CA」。

## 本卡不做

- 不启动容器、不拉镜像、不接真机、不让设备出声（夜间纪律）。
- 不修改 `src/` 行为、不动版本号/契约窗口。
- 不在本文件写任何 digest/镜像 tag 占位符；实测时回填真实值。

## 后续

- 下一张卡按本文件执行 ①②③，把结果填进记录表，并据结论修订 house README 的 TLS 段。
- 真机接受度单独一张卡（白天），判据是「设备能完成唤醒→指令→播放」，不是 `/health`。
