# 音箱客户端接入（open-xiaoai）

小瑞不修改音箱固件；音箱上运行 **open-xiaoai client**，主动拨回小瑞的音频面。服务端部署完成后，按下面顺序接线。

> 不同 open-xiaoai 版本的安装脚本/路径略有差异；以你手上版本的 README 为准。本文只约定小瑞需要的连接参数。

## 1. 需要的连接参数

控制台 **设置 → 设备 / Setup → Speaker** 保存音箱后，会显示：

| 参数 | 示例 | 说明 |
|---|---|---|
| WebSocket URL | `ws://<server-lan-ip>:4412/?token=<device-token>` | 音箱 client 回连地址 |
| Device token | `<HOUSE_EAR_DEVICE_TOKEN>` | 服务端 `.env` / secrets 里同一个值 |
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

## 4. 常见问题

| 现象 | 检查 |
|---|---|
| 401 / close 1008 | token 不一致：NAS `.env` 的 `HOUSE_EAR_DEVICE_TOKEN` 与 URL 的 `?token=` 必须相同 |
| 连接被拒绝 | NAS 防火墙/端口未放行；`listen_port` 不是 4412；容器 host network 是否生效 |
| 连上但没声音 | 先看 NAS `/health` 的 `speaker_connected` 和 `audio_plane`；再看音箱 client 日志 |
| 频繁断线 | 音箱与 NAS 不在同一稳定 LAN；检查 Wi-Fi/路由器；client 是否被 watchdog 重启 |
| 换 NAS/换 IP | 重新从控制台复制 URL，更新 client；旧地址会一直重连失败 |

## 5. 安全

- `allow_no_token` 仅用于开发调试，生产必须关闭；
- token 泄露后立即在 NAS `.env` 里换新值，并更新 client；
- 不要把 `4412` 直接暴露到公网；远程访问优先 Tailscale/WireGuard。
