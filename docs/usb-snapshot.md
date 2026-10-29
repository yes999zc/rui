# Mac USB 摄像头快照服务（usb-snapshot）

给 NAS 容器里的 rui 提供一个 Mac 侧最小 HTTP 快照接口：`GET /snapshot.jpg` 现场抓一帧
USB 摄像头返回 `image/jpeg`，抓帧走 ffmpeg stdout 管道，**不落盘**、不录音、不出声。

## 依赖与设备确认

- macOS + ffmpeg（`brew install ffmpeg`，默认路径 `/opt/homebrew/bin/ffmpeg`）。
- 确认摄像头索引与支持的分辨率：

```bash
ffmpeg -f avfoundation -list_devices true -i ""
# [0] 2K USB Camera
ffmpeg -hide_banner -f avfoundation -i 0   # 打印 Supported modes，Ctrl-C 退出
```

用到的抓帧命令（等价于脚本内部实现；ffmpeg 没有 `imagejpeg` 这个 muxer，JPEG 用 `-f mjpeg`）：

```bash
ffmpeg -hide_banner -loglevel error -f avfoundation \
  -framerate 30 -pixel_format uyvy422 -video_size 1280x720 -i 0 \
  -frames:v 1 -f mjpeg -q:v 3 pipe:1 > /tmp/frame.jpg
```

首次运行 macOS 会询问摄像头权限，允许「终端」（或运行脚本的宿主程序）访问相机即可；
被拒绝时服务会在超时后返回 503。

## 启动

```bash
cd <house-ear 仓库根目录>
python3 scripts/usb_snapshot_server.py
# 监听 http://127.0.0.1:8766/snapshot.jpg
```

> 本机 8765 已被 pensieve 常驻服务占用，故默认 8766，可用 `--port` 覆盖。

常用参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--host` | `127.0.0.1` | 绑定地址；NAS 要访问时改 `0.0.0.0` 或本机内网 IP |
| `--port` | `8766` | 端口；被占用就换一个 |
| `--device` | `0` | avfoundation 视频设备索引或名称 |
| `--size` | `1280x720` | 抓帧分辨率，需在设备 Supported modes 内 |
| `--framerate` | `30` | 输入帧率 |
| `--pixel-format` | `uyvy422` | 默认 uyvy422；其余支持模式待白天用 `ffmpeg -f avfoundation -i 0` 确认 |
| `--ffmpeg` | `/opt/homebrew/bin/ffmpeg` | 不存在时回退 `PATH` 里的 ffmpeg |
| `--timeout` | `10` | 单次抓帧超时（秒），超时 kill 子进程并返回 503 |

自测：

```bash
curl -s -D- -o /tmp/snap.jpg http://127.0.0.1:8766/snapshot.jpg | head -5
file /tmp/snap.jpg   # JPEG image data, 1280x720
```

真机 200 待白天在 Terminal 授权相机后确认。`1280x720@30` / `uyvy422` 为当前默认值，
尚未在真机出图验证（当前环境被 macOS TCC 拦截，只能验证到 503 超时路径）；
设备实际 Supported modes 需白天用 `ffmpeg -f avfoundation -i 0` 查看。

## 常驻方式一：手动（tmux / 后台）

适合临时使用或 launchd 拿不到相机权限时：

```bash
cd <house-ear 仓库根目录>
nohup python3 scripts/usb_snapshot_server.py --host 0.0.0.0 \
  >> ~/Library/Logs/usb-snapshot.log 2>&1 &
# 停止
pkill -f usb_snapshot_server.py
```

日志只记录「设备 + 帧字节数 + 耗时」和错误，不记录画面内容。

## 常驻方式二：launchd（登录后自动启动）

新建 `~/Library/LaunchAgents/com.house-ear.usb-snapshot.plist`（路径替换成实际仓库路径，
Python 用 `command -v python3` 的结果；`/usr/bin/python3` 也可，脚本只依赖标准库）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.house-ear.usb-snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$HOME/AI_WorkSpace/01_Code/projects/house-ear/scripts/usb_snapshot_server.py</string>
    <string>--host</string><string>0.0.0.0</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/usb-snapshot.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/usb-snapshot.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.house-ear.usb-snapshot.plist
launchctl list | grep usb-snapshot
# 卸载
launchctl unload ~/Library/LaunchAgents/com.house-ear.usb-snapshot.plist
```

launchd 启动的进程可能不在「相机」权限列表里：先在终端手动跑一次并在弹窗里允许；
若仍拿不到画面（请求一直 503），改用上面的手动/tmux 方式，或在
「系统设置 → 隐私与安全性 → 相机」里为对应程序勾选。

## 与 rui 配置的对应

rui 通过 `vision.usb_snapshot_url` 指向这个服务，路径固定 `/snapshot.jpg`：

```yaml
vision:
  # rui 与 Mac 在同一台机器
  usb_snapshot_url: "http://127.0.0.1:8766/snapshot.jpg"
```

```yaml
vision:
  # rui 跑在 NAS 容器里，Mac 的内网地址用 TEST-NET-1 占位（换成你自己的）
  usb_snapshot_url: "http://192.0.2.10:8766/snapshot.jpg"
```

服务本身无鉴权，URL 里**不要**带 token；需要鉴权请在反向代理层加。

## 安全与排障

- 默认只绑 `127.0.0.1`。要让 NAS 访问，绑内网地址并在 macOS 防火墙放行该端口；
  **不要**把端口映射到公网，也不要用 `0.0.0.0` + 公网入口的组合。
- `503 camera capture failed (busy, unauthorized, or timed out)` 的常见原因：
  摄像头被其它 App 占用、macOS 相机权限被拒、`--size/--framerate/--pixel-format`
  不在设备支持列表内、抓帧超过 `--timeout`。
- 端口被占用（`Address already in use`）：换 `--port`，或先确认占用进程。
- 服务不写任何临时图片文件，也不访问麦克风。
