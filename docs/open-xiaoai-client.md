# 音箱客户端接入（open-xiaoai）

小瑞不修改音箱固件；音箱上运行 **open-xiaoai client**，主动拨回小瑞的音频面。服务端部署完成后，按下面顺序接线。

> 不同 open-xiaoai 版本的安装脚本/路径略有差异；以你手上版本的 README 为准。本文只约定小瑞需要的连接参数。

## 0. 刷机/装 client 时要记下什么

这些值现在记在手机备忘录里，比以后 SSH 不进去再猜省事：

- [ ] **音箱局域网 IP**，以及路由器里的 **DHCP 固定租约**（IP 一变，客户端回连和 SSH 全断）。
- [ ] **SSH / root 登录方式**：`ssh root@<speaker-ip>`、root 密码、刷机时用的工具和版本。
- [ ] **client 安装路径**：常见 `/data/open-xiaoai/`；保留原始安装包/commit，方便回滚。
- [ ] **client 配置文件位置**：保存 ws 地址/token 的是 `server.txt`、`config.json`，还是启动脚本里的 `WS_URL`。
- [ ] **服务端回连地址**：`ws://<server-lan-ip>:4412/?token=<device-token>`。
- [ ] **device token**：由控制台 **Setup → connect 步骤**给出（也可用部署环境里的 `RUI_DEVICE_TOKEN`），客户端和服务端必须一致。
- [ ] **重启 client 的命令**和**日志位置/查看命令**（例如重跑 `/data/open-xiaoai/` 下的启动脚本，`tail -f` 对应日志）。
- [ ] **回滚方法**：停止/移除 client、恢复原启动脚本；原厂固件和分区不要动。

## 1. 需要的连接参数

控制台 **Setup → connect（接线）** 步骤会显示这组值；**设备** 页也能查看同一台设备的连接信息：

| 参数 | 示例 | 说明 |
|---|---|---|
| WebSocket URL | `ws://<server-lan-ip>:4412/?token=<device-token>` | 音箱 client 回连地址 |
| Device token | `<RUI_DEVICE_TOKEN>` | 服务端 `.env` / secrets 里同一个值 |
| 端口 | `4412`（默认） | `devices[].listen_port`，容器需放行 |

URL 和 token 是**等价于音箱控制权**的凭证，不要发到公开群/截图里。

## 2. 安装 client

1. 按 open-xiaoai 项目说明把 client 安装到音箱（通常放在 `/data/open-xiaoai/`）。
2. 确认 client 能访问 NAS 的 IP 和 `4412` 端口：
   ```bash
   # 在音箱上（如果镜像带 nc/curl）
   nc -vz <server-lan-ip> 4412
   ```
3. 把回连地址写进 client 配置：
   - 有的是 `server.txt`；
   - 有的是启动脚本里的 `WS_URL` / `SERVER_URL`；
   - 统一填控制台给出的完整 `ws://.../?token=...`。

## 3. 启动并确认

重启 client 后：

- 音箱 client 日志应出现 `connected` / `已连接 ws://<server>:4412`；
- NAS 后台 `/health` 的 `speaker_connected` 变为 `true`；
- `设备` 页显示在线；
- 说「你好小瑞」应听到唤醒提示并进入对话。

向导 connect 步骤的 `connection_message` 和它对应的处理：

| `connection_message` | 含义 | 先查什么 |
|---|---|---|
| `waiting_for_client` | 服务端在等客户端回连 | client 是否启动、ws 地址是否指向本机 LAN IP/4412 |
| `connected` | 已连上（实时音频面为准） | 可以试唤醒词 |
| `token_mismatch` | 服务端没配 token，或两边 token 不同 | 客户端日志是否 401 / close 1008；connect 步骤给出的 token 是否写进 client |
| `port_blocked` | 端口没有监听 | 4412 是否被占用/映射，防火墙，音频面是否启动 |
| `not_applicable` | 服务端主动连接（outbound） | 连接状态由 test 步骤检查 |

## 4. 常见问题

| 现象 | 检查 |
|---|---|
| 401 / close 1008 | token 不一致：NAS `.env` 的 `RUI_DEVICE_TOKEN` 与 URL 的 `?token=` 必须相同 |
| 连接被拒绝 | NAS 防火墙/端口未放行；`listen_port` 不是 4412；容器 host network 是否生效 |
| 连上但没声音 | 先看 NAS `/health` 的 `speaker_connected` 和 `audio_plane`；再看音箱 client 日志 |
| 频繁断线 | 音箱与 NAS 不在同一稳定 LAN；检查 Wi-Fi/路由器；client 是否被 watchdog 重启 |
| 换 NAS/换 IP | 重新从控制台复制 URL，更新 client；旧地址会一直重连失败 |

## 5. 安全

- `allow_no_token` 仅用于开发调试，生产必须关闭；
- token 泄露后立即在 NAS `.env` 里换新值，并更新 client；
- 不要把 `4412` 直接暴露到公网；远程访问优先 Tailscale/WireGuard。
