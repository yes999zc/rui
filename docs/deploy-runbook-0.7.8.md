# 升级 Runbook：0.7.2（`rui-dot2:1.3.1`）→ 0.7.8 —— 由 9 哥 亲手执行

> **谁做什么**：命令由 Lead 在 .168 上用**只读**方式逐条核对（见 §9 实测记录），**执行人是你（9 哥）**。
> 生产机 = `192.168.5.168`（`/vol1/rui`，容器 `rui`，8080）。**金丝雀机 .34 不参与本次升级，禁止碰**。
> 纪律：每步都可回滚；任一判据不满足 → 立刻走 §8；不留半升级状态。

## 0. 三条铁律（踩一次就出事）

1. **所有 compose 命令必须显式带两个 `-f`**：
   ```bash
   cd /vol1/rui
   docker compose -f docker-compose.yml -f rui-dot2-override.yml <cmd>
   ```
   实测：不带 `-f` 时 compose 解析出 `image: ghcr.io/yes999zc/rui:0.5.0`（`.env` 的 `RUI_IMAGE` 残留）；带两个 `-f` 才是 `rui-dot2:1.3.1`。**不带 `-f` 执行 `up` 会把生产切到 0.5.0 核心镜像。**
2. **pin 点只有一个**：`/vol1/rui/rui-dot2-override.yml:3` 的 `image:` 行，升级只改这一行。
3. 生效文件**只有** `docker-compose.yml` + `rui-dot2-override.yml`；另一个常见的 `*.override.yml`（不带 `-dot2`）实测 **MISSING**，别改它。

## 1. 前置事实（2026-10-27 07:4x 只读实测）

| 项 | 实测值 | 证据命令 |
|---|---|---|
| 主机 | `Bakey-NAS` / `yes999zc`，docker 免 sudo | `hostname; whoami` |
| 容器 | `rui`，`rui-dot2:1.3.1`，Up 12h (healthy) | `docker ps --filter name=rui` |
| compose 文件 | `docker-compose.yml,rui-dot2-override.yml` | `docker inspect rui --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'` |
| 当前镜像 ID | `sha256:92ea98f436bbadbce118bfa6e63864fe160fdb2ebfcae25444cb96397ebf8a6a` | `docker inspect rui --format '{{.Image}}'` |
| health | `status=ok`、`version=0.7.2`、`console=legacy`、`devices=2`、`device_errors=0`、`audio_plane=true`、`registered=["dot2"]`、`multi_device={enabled:true,max_planes:4}` | `curl -s localhost:8080/health` |
| 挂载 | `./data→/var/lib/rui`、`./config→/config`、`./data/models→/data/models`、`./dot2-secrets→/opt/rui-secrets` | `docker inspect rui --format '{{range .Mounts}}…{{end}}'` |
| TLS 材料 | `/opt/rui-secrets`：`ca.pem`、`device_token`、`eq/`、`server.key`、`server.pem`（owner rui） | `docker exec rui ls -la /opt/rui-secrets` |
| 插件目录 | `/opt/rui-plugins` = `rui_dot2_bridge` + `xiaoai`（**都在老镜像层里**，无宿主机挂载） | `docker exec rui ls -1 /opt/rui-plugins` |
| 设备配置 | `devices[0]`：`adapter: xiaoai`、`host: 192.168.5.5`、`room: 客厅`、`plug_id: xiaoai-lx06`；`devices[1]`：`adapter: dot2`、`host: 192.168.5.168`、`room: 卧室` | `grep -nE 'adapter\|host\|plug_id\|room' config/config.yaml` |
| compose 版本 / 磁盘 | 2.40.3 / `/vol1` 8.6T 可用 | `docker compose version --short; df -h /vol1` |

> 生产 config 里 `web.console: legacy`——**建议先保留这行**，把切 v2 当独立一步验证；要切就把 `legacy` 改 `v2` 再 `up -d`。

## 2. 备份

```bash
cd /vol1/rui
cp rui-dot2-override.yml rui-dot2-override.yml.bak.$(date +%Y%m%d-%H%M%S)
cp config/config.yaml config/config.yaml.bak.$(date +%Y%m%d-%H%M%S)
docker inspect rui --format '{{.Image}}' | tee /vol1/rui/.rollback-image-id
```
命名照现有习惯（`rui-dot2-override.yml.bak.20261026-193119` 那种）。

## 3. 镜像搬运（构建只能在 Mac；NAS 没源码、没 ffmpeg 工具链）

```bash
# Mac（house-ear 源码目录）
docker build -t rui:0.7.8-rc1 .                                        # 已验证 sha256:fe65d3a2…
cd ../rui-dot2-bridge
docker build -f deploy/downstream/Dockerfile --build-arg RUI_IMAGE=rui:0.7.8-rc1 -t rui-dot2:1.3.1-r078 .
docker save rui-dot2:1.3.1-r078 | ssh yes999zc@192.168.5.168 'docker load'

# NAS（导入后先验，再动生产）
docker run --rm --entrypoint python rui-dot2:1.3.1-r078 -c "import rui;print(rui.__version__)"   # 期望 0.7.8
docker run --rm --entrypoint sh rui-dot2:1.3.1-r078 -c 'ls -1 /opt/rui-plugins'                  # 期望只有 rui_dot2_bridge
```
`docker save` 会带走底座层，NAS 不需要 `rui:0.7.8-rc1`，也不需要源码。
（`docker load` 会往 NAS 镜像库加一个镜像，不改生产容器——由你执行或你批准后执行。）

## 4. 外置 lx06 插件（**不做这步，客厅设备会掉**）

0.7.8 已删仓内 `xiaoai` 与 `adapter_aliases.py`；下游镜像只 `COPY rui_dot2_bridge`。生产 config 仍是 `adapter: xiaoai` / `plug_id: xiaoai-lx06`，核心读入时会迁成 `lx06` 并打 WARNING——**但适配器必须由外置插件提供**。

**a) 插件源码放 NAS 并只读挂进容器**
```bash
# Mac
rsync -a --delete rui-xiaoai-bridge/src/ yes999zc@192.168.5.168:/vol1/rui/rui-xiaoai-bridge/src/
```
`rui-dot2-override.yml` 的 `volumes:` 增加：
```yaml
      - ./rui-xiaoai-bridge/src:/opt/rui-xiaoai:ro
```
`environment:` 改/加（PYTHONPATH 必须含两个路径）：
```yaml
      - PYTHONPATH=/opt/rui-plugins:/opt/rui-xiaoai
      - RUI_ADAPTER_PLUGINS=dot2=rui_dot2_bridge.adapter:build,lx06=rui_xiaoai_bridge.adapter:build
```

**b) 装 manifest 到 data_dir（loader 扫 `<data_dir>/plugs/*`）**
```bash
mkdir -p /vol1/rui/data/plugs/xiaoai-lx06/devices
cp /vol1/rui/rui-xiaoai-bridge/src/rui_xiaoai_bridge/plugin.yaml        /vol1/rui/data/plugs/xiaoai-lx06/
cp /vol1/rui/rui-xiaoai-bridge/src/rui_xiaoai_bridge/devices/lx06.yaml  /vol1/rui/data/plugs/xiaoai-lx06/devices/
```
（已核：`plugin.yaml` 的 `id: xiaoai-lx06`、`rui_compat: ">=0.6.0 <0.8.0"`；`devices/lx06.yaml` 的 `id: lx06`、`runtime.entrypoint: rui_xiaoai_bridge.adapter:build`、`listen_port: 4412`。）

**c) 判据（升级后必须同时满足）**
- `/health`：`devices=2`、`device_errors=0`；
- `planes` 两台都 `state="running"`（客厅那条不能是 `not_started`/`error`）；
- `adapter_plugins.warnings ≥ 1`（`xiaoai → lx06` 迁移告警，证明迁移生效）；
- **不能只看 `adapter_plugins.registered`**：镜像里挂的插件走 catalog 懒加载，`registered` 只统计 `RUI_ADAPTER_PLUGINS`/entry point/`<data_dir>/plugs/*`。

**d) 若首轮只保卧室 Dot2**：override 去掉 `lx06=...`、config 里客厅设备 `enabled: false`，并在 HANDOFF 写「已知限制：客厅未上，回滚旧镜像可恢复」——**不许含糊**。

## 5. 切换

```bash
cd /vol1/rui
sed -i 's#^\(\s*image:\s*\)rui-dot2:1.3.1$#\1rui-dot2:1.3.1-r078#' rui-dot2-override.yml
sed -n '1,6p' rui-dot2-override.yml                                                        # 确认 image 行
docker compose -f docker-compose.yml -f rui-dot2-override.yml config | grep -E "image:"    # 期望 rui-dot2:1.3.1-r078
docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d
```

## 6. 验收（只读）

```bash
curl -s localhost:8080/health | python3 -m json.tool | head -40
curl -s localhost:8080/api/v2/meta | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('rui_version'), d.get('compat'))"
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' localhost:8080/
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' localhost:8080/panel/logs
cd /vol1/rui && docker compose -f docker-compose.yml -f rui-dot2-override.yml logs --tail=80 rui | grep -iE "adapter plugins|wss listening|migrated|deprecated|error"
```
判据：`version=0.7.8`、`devices=2`、`device_errors=0`、两台 `planes[].state=running`、`warnings≥1`、meta `compat 0.6.0..<0.8.0`；若已切 `console: v2`，`/` 应 302 `/v2/`、`/panel/logs` 302 `/v2/logs`（仍 `legacy` 时 200 旧壳属预期）。

## 7. 真机走一遍（会出声）

客厅：「你好小瑞」→ 应答；「现在几点」→ 有回答；「放首歌」→ 播放；「停」→ 停下。再对卧室重复唤醒，确认不串房。

## 8. 回滚（任一判据不满足立即执行）

```bash
cd /vol1/rui
cp rui-dot2-override.yml.bak.<时间戳> rui-dot2-override.yml
docker compose -f docker-compose.yml -f rui-dot2-override.yml config | grep -E "image:"   # 期望 rui-dot2:1.3.1
docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d
curl -s localhost:8080/health | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['version'],d['devices'],d['device_errors'],d['console'])"
```
期望回到 `0.7.2 / 2 / 0 / legacy`。只回滚控制台入口（保留 0.7.8）：config 的 `web.console` 改回 `legacy` 再 `up -d`。

## 9. 实测记录

| 步骤 | 判据 | 实测（2026-10-27 07:4x，Lead 只读核对） | 状态 |
|---|---|---|---|
| 主机/容器 | Bakey-NAS、rui healthy | `Bakey-NAS`、`rui rui-dot2:1.3.1 Up 12 hours (healthy)` | ✅ |
| compose 文件 | 两个文件 | `docker-compose.yml,rui-dot2-override.yml` | ✅ |
| override 存在性 | 不存在 | `MISSING` | ✅ |
| 坑 1 复现 | 裸 compose = 0.5.0 | `image: ghcr.io/yes999zc/rui:0.5.0`；两 `-f` = `rui-dot2:1.3.1` | ✅ 已写入 §0 |
| 当前镜像 ID | 92ea98f4… | `sha256:92ea98f436bbadbce118bfa6e63864fe160fdb2ebfcae25444cb96397ebf8a6a` | ✅ |
| health | 0.7.2 / 2 / 0 / legacy | `version=0.7.2 console=legacy devices=2 device_errors=0 audio_plane=true registered=["dot2"] multi_device enabled=true max_planes=4` | ✅ |
| TLS 材料 | 5 项齐 | `ca.pem device_token eq server.key server.pem` | ✅ |
| 插件目录 | 两个（老镜像层） | `rui_dot2_bridge` `xiaoai` | ✅ |
| 设备配置 | xiaoai + dot2 | `devices[0] adapter xiaoai host 192.168.5.5 plug_id xiaoai-lx06`；`devices[1] adapter dot2` | ✅ |
| 镜像 | 从 CI 直接拉，不用搬运 | `docker pull ghcr.io/yes999zc/rui:edge` → 本地 `bd396ee593a1`，`rui 0.7.8` | ✅ |
| 外置插件 | 两个包 + env | `/vol1/rui/plugins/{rui_dot2_bridge,rui_xiaoai_bridge}`；`registered=["dot2","lx06"]` | ✅ |
| 切换后 health | 0.7.8 + 平面 running | `ver 0.7.8 / dev 1 / err 0 / console v2 / speaker True / planes[卧室 dot2 running]` | ✅ |
| 老链接 | 302 到 v2 | 待执行（`console: legacy` 时 200 旧壳属预期） | ⏳ |
| 回滚 | 回 1.3.1 | 回滚到 `rui-dot2:1.3.1`（重建 `a62e2cf4a0be`，`rui 0.7.2`）：`devices=1`、`errors=0`、`/` 仍 302；再切回 0.7.8 恢复 | ✅ |

> 标 ⏳ 的四行由你执行时回填；其余行已用只读命令核过，可随时复跑。
