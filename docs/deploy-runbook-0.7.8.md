# 升级 Runbook：0.7.2（`rui-dot2:1.3.1`）→ 0.7.8

> 性质：**人肉部署手册**（Lead 亲手执行并回填实测输出）。本文件是 0.7.8 验收的一部分。
> 状态：命令已就绪；「实测输出」段由 Lead 在**白天**执行后回填（夜间不部署、不出声）。
> 原则：每步都可回滚；**任一步失败立刻回滚到 `rui-dot2:1.3.1`**，不留半升级状态。

## 0. 前置事实（执行前先核）

| 项 | 期望值 | 怎么核 |
|---|---|---|
| 生产主机 | NAS 168，目录 `/vol1/rui` | `ssh` 后 `pwd` |
| 当前镜像 | `rui-dot2:1.3.1` = `sha256:92ea98f4…`（核心 0.7.2） | `docker image inspect --format '{{.Id}}' rui-dot2:1.3.1` |
| 当前 health | `devices=2`、`device_errors=0`、`console=legacy` | `curl -s localhost:8080/health \| jq '{devices,device_errors,console}'` |
| 新 core 镜像 | `ghcr.io/yes999zc/rui:0.7.8` = `sha256:__填__` | `docker manifest inspect ghcr.io/yes999zc/rui:0.7.8 \| jq -r .manifests[].digest` |
| 外置 lx06 插件 | `rui-xiaoai-bridge` 0.2.2 已挂 `/opt/rui-plugins` | `docker exec rui ls /opt/rui-plugins` |
| TLS 材料 | `secrets/server.pem` + `server.key`（或原 controller 证书） | `docker exec rui ls /opt/rui-secrets` |

## 1. 备份（缺一不可）

```bash
cd /vol1/rui
cp docker-compose.override.yml docker-compose.override.yml.bak.$(date +%Y%m%d-%H%M%S)
cp config/config.yaml config/config.yaml.bak.$(date +%Y%m%d-%H%M%S)
docker image inspect --format '{{.Id}}' rui-dot2:1.3.1 | tee /vol1/rui/.rollback-image-id
```

## 2. 构建带 0.7.8 底座的下游镜像（不动生产）

**注意**：`ghcr.io/yes999zc/rui:0.7.8` 只有在打了 `v0.7.8` tag 之后才存在于 registry（CI 在 tag 上构建镜像）。**验收前用本地候选镜像**（已验证可构建）：

```bash
# 在 house-ear 源码目录构建候选（本地 tag，不推 registry）
docker build -t rui:0.7.8-rc1 .          # 已验证：产出 sha256:fe65d3a2…

# 用候选做下游镜像（rui-dot2-bridge 的 Dockerfile 在 deploy/downstream/）
cd /vol1/rui/rui-dot2-bridge               # 本机无源码就先在 Mac 构建后 docker save | ssh docker load
docker build -f deploy/downstream/Dockerfile \
  --build-arg RUI_IMAGE=rui:0.7.8-rc1 -t rui-dot2:1.3.1-r078 .

# 版本检查（rui 没有 --version，用 python 读 __version__）
docker run --rm --entrypoint python rui-dot2:1.3.1-r078 -c "import rui;print(rui.__version__)"   # 期望 0.7.8
docker run --rm --entrypoint sh rui-dot2:1.3.1-r078 -c 'ls /opt/rui-plugins'                      # 期望 rui_dot2_bridge
```

> 本地已实测：`rui 0.7.8`、`/opt/rui-plugins` 含 `rui_dot2_bridge`。打完 tag 后可用 `ghcr.io/yes999zc/rui:0.7.8` 复现同一条链。

## 3. 配置前置（0.7.8 的三个行为）

1. **`multi_device`**：两台都要出声必须开，否则默认只起第一台平面。
   ```yaml
   multi_device: {enabled: true, max_planes: 2}
   ```
2. **`web.console`**：0.7.8 默认 `v2`；不写该键也会变 v2（**这是行为变更**）。要先用旧壳就显式写 `console: legacy`。
3. **`adapter: xiaoai`**：会在读入时迁移成 `lx06` 并打 WARNING（`/health.adapter_plugins.warnings`），不用手改。

## 4. 切换

```bash
cd /vol1/rui
sed -i 's#image: rui-dot2:1.3.1#image: rui-dot2:1.3.1-r078#' docker-compose.override.yml
docker compose up -d --force-recreate
```

## 5. 验收（只查，不出声）

```bash
curl -s localhost:8080/health | jq '{devices,device_errors,audio_plane,console,planes}'
curl -s localhost:8080/health | jq '.adapter_plugins'
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' localhost:8080/
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' localhost:8080/panel/logs
curl -s localhost:8080/api/v2/meta | jq '{rui_version,compat}'
docker compose logs --tail=50 rui | grep -E "adapter plugins|wss listening|migrated|deprecated"
```

判据：`devices=2`、`device_errors=0`、**`planes` 两台都 `state=running`**、`console=v2`、`/` 302 `/v2/`、`/panel/logs` 302 `/v2/logs`、`meta.rui_version=0.7.8`。

## 6. 回滚（任一判据不满足立即执行）

```bash
cd /vol1/rui
cp docker-compose.override.yml.bak.<时间戳> docker-compose.override.yml
docker compose up -d --force-recreate
curl -s localhost:8080/health | jq '{devices,device_errors,console}'   # 期望回到 legacy / 0.7.2
```

只回滚控制台入口（保留 0.7.8）：在 config 里写 `web.console: legacy` 后 `docker compose up -d --force-recreate`。

## 7. 实测记录（白天执行后回填）

| 步骤 | 命令/判据 | 实测输出 | 结论 |
|---|---|---|---|
| 备份 | 三个文件存在 | 待填 | |
| 构建 | `python -c "import rui"` = 0.7.8 | 已本地实测 `rui 0.7.8` | |
| 切换 | `docker compose up -d` | 待填 | |
| health | 五字段 | 待填 | |
| 老链接 | `/` 与 `/panel/logs` | 待填 | |
| meta | `rui_version` | 待填 | |
| 升级耗时 | 起止时间 | 待填 | |
| 回滚 | 回到 1.3.1 | 待填 | |

> 真机出声项（唤醒→指令→播放、音质/距离/打断）**不在本手册**，见 `docs/acceptance.md` 的 Done 6 清单。
