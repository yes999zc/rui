# 设备接入索引（不含任何口令）

> 本文件只记录「设备 → 清单条目 / 用途 / 地址 / 用户」。
> **所有口令、token、私钥一律在 `01_Code/device-inventory.md`（工作区，非本仓库）**，本文件永不复制凭证。
> 采集/测试脚本读设备只允许通过 SSH key；需要密码时由 9 哥 操作或临时授权，不写进仓库。

## 生产与服务

| 设备 | 清单条目 | 用途 | 地址 / 用户 |
|---|---|---|---|
| NAS 168（fnOS，rui 生产宿主） | `device-inventory.md` → `Bakey-NAS` 一节 | rui 容器、`/vol1/rui` 部署目录、容器日志、console token | `192.168.x.x` / `yes999zc`（docker 组已授权） |
| Mac Studio | 本机 | 开发、构建、矩阵工具、**Dot2 房间的播放音源** | 本机 / `bakeyzhang` |
| 荣耀服务器（horner-nas） | `device-inventory.md` → `horner-nas (荣耀服务器)` 一节 | **李箱客厅 LX06 房间的播放音源**（ALSA `plughw:2,0`，ALC256 模拟输出） | `192.168.x.x` / `yes999zc`（SSH key 可用） |

## 被采样本箱

| 音箱 | rui `device_id` | 房间 | 传输 / 端口 | 备注 |
|---|---|---|---|---|
| Dot2（EchoMuse RS03QR） | `dot2` | 卧室（与 Mac Studio 同屋） | inbound，`listen_port=8870` | 设备侧音频可调（AEC/mic gain/OWW） |
| 小爱 LX06 | `living` | 客厅（与荣耀服务器同屋） | inbound，`listen_port=4412`（`host` 见 device-inventory.md） | 受限，作对照 |

## 采集约定

- 两台音源都用**同一份 WAV**（48 kHz / 16-bit / mono），`scripts/fleet_say.py` 生成或 `say -o` + `ffmpeg` 转换。
- 每句间隔 ≥10 s；播放脚本在**播放前**写 marks（`ts` 用 UTC 毫秒），矩阵用 `--log-tz UTC --window <gap>` 对齐。
- 两台时钟均 NTP 同步（`timedatectl` → `System clock synchronized: yes`）；跨机对齐的不确定度约等于 SSH RTT 的一半（实测 ≤150 ms），小于窗口宽度一个数量级。
- 荣耀服务器上**只做播放**：不动 `mihomo`、透明代理、iptables、fnOS 服务。

## 相关文档

- `docs/analysis/dot2-vs-xiaoai.md`：P0 矩阵 + P1 归因
- `docs/analysis/wake-vs-command-range.md`：唤醒 vs 指令距离梯度（task-16）
- `docs/release/acceptance-4devices.md`：4 台接入与验收 runbook
