# TLS 是否必需：三组安装矩阵实测（0.7.5）

> 状态：**已实测**（2026-10-26，本地容器；不接真机、不出声）。
> 镜像：`ghcr.io/yes999zc/rui:0.7.4`，digest **`sha256:de26c5bacc00fbbb807da11d9eb1d7427125aab154703997d5a0131c23d8e749`**。
> 配置模板：`examples/two-speakers/`；设备为测试地址（`192.0.2.x`），无真实设备。
> 复现命令见文末；本文所有结论都来自下面 6 次实跑。

## 结论（可判定）

**dot2 平面在 `listen_port`（= `tls_port`）开启时必须有 `server.pem`/`server.key`；缺失则 `audio_plane=false`，错误为 `missing TLS material`。**

**平面数量取决于 `multi_device`**：

- **默认 `multi_device.enabled=false`**：单个 `AudioPlaneSupervisor` + `_pick_device()` 只启动**配置里第一台**设备（`app.py:3077`、`schema.py:1067`）。因此第一台是谁决定先暴露哪个错误：xiaoai 在前且 `RUI_DEVICE_TOKEN` 为空 → `RUI_DEVICE_TOKEN is empty`；dot2 在前且无证书 → `missing TLS material`。
- **`multi_device.enabled=true`**：`_start_fleet` 按 `max_planes` 为每台设备起一个平面，单台失败不拖垮其他（`app.py:2965-2999`、`fleet.py:140-146`）；两台都起需 `max_planes >= 2`。

证书 + 两个 token 齐备、且 dot2 在前（默认单平面）时 → `audio_plane=true`，dot2 `wss listening on 8871`。

## 必须写清的风险句

**自签证书只解决「音频平面能启动」；真机是否接受，取决于设备信任的 CA，自签不能保证。**
设备以固定 ServerName `echomuse-controller` 校验；原 controller 证书无法重建，丢失时设备需要重新配网。
本矩阵只证明「平面侧不缺材料」，不证明「真机链路可用」。

## 实测结果（每组 2 次）

环境：同一台 Mac、同一镜像 digest；`docker run` 映射 18081–18086；每次 `down` 后重建 data 目录。
`/health` 取 `devices` / `device_errors` / `audio_plane` / `adapter_plugins`；日志取第一条相关错误。

| 组 | 设备顺序 | secrets | token | devices | device_errors | audio_plane | registered | 第一错 |
|---|---|---|---|---:|---:|---|---|---|
| ①-a | dot2 | 无 | DOT2 有 / RUI 空 | 1 | 0 | **false** | dot2, lx06 | `missing TLS material: secrets/server.pem / secrets/server.key` |
| ①-b | dot2 | 无 | 同上 | 1 | 0 | **false** | dot2, lx06 | 同上 |
| ②-a | lx06, dot2 | 无 | DOT2 有 / RUI 空 | 2 | 0 | **false** | dot2, lx06 | `RUI_DEVICE_TOKEN is empty: refusing to accept an unauthenticated speaker connection`（lx06 先启动） |
| ②-b | lx06, dot2 | 无 | 同上 | 2 | 0 | **false** | dot2, lx06 | 同上 |
| ③-a | dot2, lx06 | 自签 | 两个都有 | 2 | 0 | **true** | dot2, lx06 | 无；dot2 `wss listening on 8871` |
| ③-b | dot2, lx06 | 自签 | 两个都有 | 2 | 0 | **true** | dot2, lx06 | 同上 |

> 以上 6 次均为**默认 `multi_device.enabled=false`（单平面）**；`devices` 数量来自配置，`audio_devices` 里只有第一台有 `running/error` 状态。两台都起需启用 `multi_device`，不在本卡实测范围。

关键日志（原样）：

- ①：`RuntimeError: missing TLS material: secrets/server.pem / secrets/server.key`，`audio plane refused: missing TLS material`
- ②：`RuntimeError: RUI_DEVICE_TOKEN is empty: refusing to accept an unauthenticated speaker connection`
- ③：`dot2 link: wss listening on 8871`；`/health` 的 `audio_devices[0] = {id: dot2, running: true, error: ""}`

## 复现命令（可直接执行）

> 下面假设三个仓都在同一父目录；先进入 house-ear 根目录，再显式取三个绝对路径，不依赖 cwd。
> 容器名固定 `rui-tls-1/2/3`，端口 `18081/18082/18083`；每组可重复执行（先 `down`）。

```bash
cd /path/to/01_Code/projects/house-ear      # 换成你的 house-ear 根目录
HOUSE=$(pwd)
DOT2=$(cd ../rui-dot2-bridge && pwd)
XIAO=$(cd ../rui-xiaoai-bridge && pwd)
T=/tmp/rui-tls-matrix
docker pull ghcr.io/yes999zc/rui:0.7.4
rm -rf "$T"; mkdir -p "$T"/{config,data-1,data-2,data-3,plugins,secrets}

# 插件：dot2 包 + xiaoai 包 + xiaoai 的 plugin.yaml/devices（缺失会报 no plug manifest）
cp -r "$DOT2/src/rui_dot2_bridge" "$T/plugins/"
cp -r "$HOUSE/plugs/xiaoai/xiaoai_plug" "$T/plugins/"
cp "$HOUSE/plugs/xiaoai/plugin.yaml" "$T/plugins/"
cp -r "$HOUSE/plugs/xiaoai/devices" "$T/plugins/"

# 三份最小配置（multi_device 默认关闭 = 单平面只起第一台）
cat > "$T/config/config-1.yaml" <<'YAML'
version: 2
system: {data_dir: /var/lib/rui, log_level: info, timezone: Asia/Shanghai}
web: {host: 0.0.0.0, port: 8080, auth_token: "test-token"}
devices:
  - {id: dot2, adapter: dot2, host: 192.0.2.10, room: Lab, listen_port: 8871, token_env: DOT2_EM_TOKEN}
YAML

cat > "$T/config/config-2.yaml" <<'YAML'
version: 2
system: {data_dir: /var/lib/rui, log_level: info, timezone: Asia/Shanghai}
web: {host: 0.0.0.0, port: 8080, auth_token: "test-token"}
devices:
  - {id: lx06, adapter: lx06, host: 192.0.2.11, room: Lab2, listen_port: 8872, token_env: RUI_DEVICE_TOKEN}
  - {id: dot2, adapter: dot2, host: 192.0.2.10, room: Lab, listen_port: 8871, token_env: DOT2_EM_TOKEN}
YAML

# ③ 与 ② 同设备，但顺序换成 dot2 在前（让单平面先起 dot2 以验证 TLS）
cat > "$T/config/config-3.yaml" <<'YAML'
version: 2
system: {data_dir: /var/lib/rui, log_level: info, timezone: Asia/Shanghai}
web: {host: 0.0.0.0, port: 8080, auth_token: "test-token"}
devices:
  - {id: dot2, adapter: dot2, host: 192.0.2.10, room: Lab, listen_port: 8871, token_env: DOT2_EM_TOKEN}
  - {id: lx06, adapter: lx06, host: 192.0.2.11, room: Lab2, listen_port: 8872, token_env: RUI_DEVICE_TOKEN}
YAML

# ③ 的自签证书（SAN=DNS:echomuse-controller）
bash "$HOUSE/scripts/make_dev_tls.sh" "$T/secrets"

common=(-v "$T/plugins:/opt/rui-plugins:ro" -e RUI_CONFIG=/config/config.yaml
  -e PYTHONPATH=/opt/rui-plugins
  -e RUI_ADAPTER_PLUGINS='dot2=rui_dot2_bridge.adapter:build,lx06=xiaoai_plug.adapter:build')

# ① dot2 only，无 secrets
docker run -d --name rui-tls-1 -p 18081:8080   -v "$T/config/config-1.yaml:/config/config.yaml:ro" -v "$T/data-1:/var/lib/rui"   "${common[@]}" -e DOT2_EM_TOKEN=testtoken -e RUI_DEVICE_TOKEN=   ghcr.io/yes999zc/rui:0.7.4

# ② 两桥，无 secrets（lx06 在前 -> 先报 token 错）
docker run -d --name rui-tls-2 -p 18082:8080   -v "$T/config/config-2.yaml:/config/config.yaml:ro" -v "$T/data-2:/var/lib/rui"   "${common[@]}" -e DOT2_EM_TOKEN=testtoken -e RUI_DEVICE_TOKEN=   ghcr.io/yes999zc/rui:0.7.4

# ③ 两桥 + 自签 + 两个 token（dot2 在前 -> 验证 TLS 可起）
docker run -d --name rui-tls-3 -p 18083:8080   -v "$T/config/config-3.yaml:/config/config.yaml:ro" -v "$T/data-3:/var/lib/rui"   -v "$T/secrets:/opt/rui-secrets:ro" "${common[@]}"   -e DOT2_EM_TOKEN=testtoken -e RUI_DEVICE_TOKEN=testtoken   -e DOT2_SECRETS_DIR=/opt/rui-secrets   ghcr.io/yes999zc/rui:0.7.4

sleep 7
for n in 1 2 3; do
  echo "== rui-tls-$n"
  curl -s -H 'X-Auth-Token: test-token' "localhost:1808$n/health"     | python3 -c 'import json,sys;d=json.load(sys.stdin);print({k:d[k] for k in ("devices","device_errors","audio_plane","adapter_plugins")});print([(x["id"],x["running"],x["error"][:100]) for x in d["audio_devices"]])'
  docker logs "rui-tls-$n" 2>&1 | grep -iE "missing TLS|is empty|listening on" | head -3
done

# 清理
docker rm -f rui-tls-1 rui-tls-2 rui-tls-3
rm -rf "$T"
```

> 若要两台都起：配置加 `multi_device: {enabled: true, max_planes: 2}`，再重复 ③；默认单平面只起第一台。

## 与 README/HANDOFF 的统一表述（建议句）

> 「dot2 的 `listen_port`/`tls_port` 开启时，`secrets/server.pem|key` 是音频平面启动前提；缺它 `/health` 的 `audio_plane=false`，日志为 `missing TLS material`。默认 `multi_device.enabled=false` 时只启动配置里的第一台设备；两台都起需 `multi_device.enabled=true` 且 `max_planes >= 2`。自签只保证平面启动，真机是否接受取决于设备信任的 CA（实测见 `docs/audit/tls-required-2026-10.md`）。」

## 本卡不做 / 清理

- 不接真机、不调 TTS/音乐/唤醒、不出声；未验证真机接受度。
- 测试容器 `rui-tls-1/2/3`（每组建 1 个、共 6 次实跑）与 `/tmp/rui-tls-matrix` 已删除；仓库无 secrets/缓存残留。
- 未改 `src/`、版本号、契约窗口。
