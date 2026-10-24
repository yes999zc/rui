# 设备管理与安装向导调研（P2.0）

> 状态：**调研稿 v1 · 2026-10-24**。对应 `ROADMAP` P2.0；只写文档，不改代码、不部署。
> 目标读者：9 哥 / Maintainer。上游参考：`wilbowes/EchoMuse`（MIT），controller 以 2026-10-24 的
> `main` 为准。本机部署数据实测 firmware **v2.18.1**、DB schema **v31**；DB 里记录的
> `latest_controller_version` 为 **v2.26.1**（运行版本未另行核对）。
> 约束：不引入 Home Assistant / Assist / ESPHome 依赖；RUI 自己就是助手；机型差异只进插头/adapter。
> 本文没有复制 EchoMuse 代码；后续若照抄，按 MIT 保留版权与许可声明，并进 `THIRD_PARTY.md`。

## 摘要 / Abstract

- EchoMuse 的 Device Management 是「**设备注册 + 审批 + 每设备配置 + 在线状态 + 多设备仲裁**」的
  控制面；真正值得 RUI 学的是它的**状态机、证据式向导、恢复动作和诊断包**，不是它的 HA/ESPHome 编排。
- 它的安装向导重（USB/ADB/WebUSB/刷机），**不能照搬**；RUI 的向导应该是「通用步 + 插头步」的
  **声明式流程**，每一步由服务端计算完成度，设备侧安装只给**链接/命令/参数表单**。
- RUI 今天已经有 `GET/PUT/DELETE /api/v1/devices`、`GET /devices/{id}/connect`、
  `POST /devices/probe`、`GET /api/v1/setup` 与五步向导；缺的是**插头 manifest、步骤协议、
  设备参数表单、连接测试和诊断**，不是缺一套数据库。
- 建议 P2.4 最小后台**继续用 `config.yaml` 作为设备注册表**，新增一个 `params` 字典；
  不引入 SQLite、不做 approve 队列、不做 OTA/mDNS/每设备证书。2 天可交付向导壳 + manifest 契约。
- 多设备「谁最响谁答」在 RUI **今天不适用**：音频面一个进程只驱动一台设备（`_pick_device` 取第一台），
  第二台设备/仲裁属于 P3.3/P3.4。EchoMuse 的仲裁器可以借鉴思路，但现在不要写。

---

## 1. EchoMuse Device Management 到底做了什么

### 1.1 部署形态与控制台 IA

**部署**

- Controller 是一个容器：dashboard/API 在 `API_PORT`（默认 `8768`），设备链路在 `SERVER_PORT`
  （默认 `8767`），TLS 设备链路在 `SERVER_TLS_PORT`（默认 `8770`）；Home Assistant add-on 与
  Docker 跑同一套代码（[EM-DEPLOY]、[EM-ENV]）。
- Docker 部署要求 `network_mode: host`，并挂载 `./data:/app/data`；数据库、TLS 证书、录音都在
  data 卷里（[EM-DEPLOY]）。这解释了为什么它可以给每台设备开一个 ESPHome TCP 端口。
- 首次使用：Docker 模式在日志里打印一次性 **setup token**，用它创建第一个 admin；HA add-on 模式
  走 HA ingress，第一个打开面板的人成为 admin。角色只有 `admin` / `readonly`（[EM-QS L122-150]、
  [EM-AUTH]）。RUI 是单用户 token 模型，**不需要抄账号/角色**。

**Dashboard IA（Fleet 页）**

- 页头：`EchoMuse / Device Management` + controller 版本 + 角色 + 主题切换 + 设置齿轮
  （[EM-UI L11364-11400]）。
- 摘要条：`Online x/y`、`Active`、`Updates`、`Pending` 四个计数（[EM-UI L11460-11490]）。
- 设备卡片/列表：在线/离线、label、IP、firmware、pending 姿态；点开是设备详情。
- 顶部 `Provision Device` 打开**安装向导**（[EM-UI L3192-3210]）。
- 设置模态：`Fleet`（全局配置）、`System`、`Users`、`Account`、`Support`（[EM-UI L10894-11100]）。
- 设备详情是模态，tabs 为 `Status / Activity / Config / Console / Updates / Logs`；未批准设备只有
  `approve`（[EM-UI L1614-1620]）。
- 设备 Config tab 是**分区**的 `Playback / Wake word / Microphones / Ring / Advanced / Bluetooth /
  Sendspin`，并有 `Fleet / Device` 切换（[EM-UI L2416] 起、[EM-CONF L30-35]）。

**对 RUI 的启示**：IA 可以砍到两层——`/setup`（一次性向导）+ `/panel/devices-admin`（日常管理）；
设备详情用 tabs（概览/参数/连接/诊断/删除）即可，不需要 fleet/updates/pending 三个摘要位。

### 1.2 安装向导：步骤、状态机、恢复

EchoMuse 的 provisioning wizard 是**浏览器端状态机**，有两套流程：

| 流程 | 步数 | 适用 |
|---|---|---|
| FireOS | 13 | 保留 Amazon userspace + Magisk root |
| emOS | 9 | 用 emOS 替换 userspace，TWRP 内完成 |

**FireOS 13 步**（[EM-UI L3828-3853]）：`Connect Device → Connect to TWRP → Patch Boot Image →
Install Magisk → Pre-seed Root DB → Reboot to Android → Reconnect → Verify Root → Disable Alexa →
Debloat → Configure WiFi → Install EchoMuse → Wake Word Assets`。

**emOS 9 步**（[EM-UI L3898-3912]）：`Connect Device → Connect to TWRP → Escrow Boot Image →
Install EchoMuse → Wake Word Assets → Build emOS → Flash and Verify → Reboot and Watch →
Configure WiFi`。

每步有三种执行方式：

- `auto`：连上后自动跑（FireOS：patch_boot/preseed_db/verify_root/disable_alexa/debloat/
  install_oww；emOS：escrow/install_oww/build）。
- `manual`：分区写入或重启类必须由人点（flash/reboot）。
- `input`：需要人选文件/填 Wi-Fi（`install_magisk / wifi / install_em / build_emos /
  wifi_register`，[EM-UI L3231-3233]）。

**状态机（已验证）**

- 步骤状态只有 `pending / running / done / error`，另有全局 `running / restored / stepError /
  diagnostics / log`（[EM-UI L8573-8710]）。
- **只能向前**：成功后 `setStep(step+1)`；没有「上一步」；失败停在当前步，提供 `Retry`。
- **流程在开跑后锁定**：FireOS/emOS 步数不同，跑过一步后禁止切换（[EM-UI L4318-4335]、
  L4426）。
- **每步开跑前校验设备模式**：`_STEP_MODE` 把步骤映射到 `android/twrp/serial`，模式不符直接拒绝，
  防止在 Android 下写 TWRP 分区（[EM-UI L3226-3270]、L8600-8625）。
- **拔线 = abandon，不是失败**：`stepEpoch` 递增，旧步骤的回调丢弃；UI 提示「重连后重试」，
  不会把「线断了」写成刷机失败（[EM-UI L4418-4425]、L8585-8590）。
- **失败面板固定三件事**：失败原因（`stepError`）、`Retry`、`Reconnect`、`Download diagnostics`
  （[EM-UI L9218-9300]）；诊断在失败当时采集设备状态，服务端生成（见 §1.2 后端）。
- **重复设备**：第 0 步发现设备已注册时，给两个选择——保留记录重新刷（保留端口/HA 实体），或从
  controller 删除（[EM-UI L9240-9285]）。
- **emOS 的 restore 是终态**：恢复 escrowed boot image 会撤销后续步骤依赖的写入，向导直接结束，
  不允许继续（[EM-UI L4385-4395]）。

**恢复/续跑的真实边界（已验证 + 推断）**

- 向导进度是 React state，**没有服务端 run/session 记录**（DB schema 里没有 provisioning 表；
  wizard 状态只在组件里）。因此刷新页面=从头开始；所谓「resume」是靠每步的 **Reconnect**
  重建设备连接，而不是恢复向导游标（代码可证；刷新行为未实测，标推断）。
- 向导**不写 controller 配置**；它只通过 `/api/provision/*` 取文件/构建镜像/打包诊断，设备侧动作
  全在浏览器 WebUSB/ADB/串口里完成（[EM-RP L38-49]）。
- 后端向导接口（全部 admin）：start/debloat 脚本、Magisk DB、latest binary、OWW 资源清单与字节、
  emOS init/image、分片上传、diagnostics、TLS 凭据、controller endpoints（[EM-RP L38-49]、
  [EM-LINK L28-32]）。

**为什么 RUI 不能照搬**：LX06（A 类）没有 root、不能刷机；Dot2（B 类）的刷机在
`rui-dot2-bridge`/设备侧仓库，不在 RUI。RUI 的向导必须把「设备侧动作」降级为
**说明 + 链接 + 参数表单**，由服务端用**连接证据**判断完成，详见 §3。

### 1.3 注册与审批

**三条 WebSocket 平面**（[EM-CTRL L7-58]）：`/control`（JSON 控制面）、`/data`（麦克风/扬声器
PCM）、`/shell`（按需打开的 root shell）。设备拨入后第一条 `/control` 消息必须是
`{"type":"register","device_id","ip","version","capabilities"}`。

**注册流程**（[EM-CTRL L5056-5245]）：

1. 读 `register`，先做链路鉴权（§1.5）。
2. `device_id` 不在 DB：
   - `DEVICE_APPROVAL=auto` → 直接建行 + 批准（label 为 `Unknown <serial 前 8 位>`）；
   - `strict`（默认）→ 建行、回 `{"type":"pending"}`、关连接；设备隔几秒重试，审批后下一次重试
     即接入（文档写 within 30 s，[EM-RD L236-245]）。
3. 行存在但 `approved=0`：更新 `last_seen`/ip/version，仍回 `pending` 并关连接。
4. 已批准：创建内存 `Device`、写入 `last_seen`、回 `ack`（含 `features`、`time_ms`）、推送
   `config`（effective config）、开始控制循环（[EM-CTRL L5160-5260]）。

**审批动作**（`POST /api/devices/{id}/approve`，[EM-RD L236-265]）：

- 必填 `label`，**全 fleet 唯一**（重名 409，[EM-RD L557-571]）。
- 可带 `config` 覆盖。
- 批准同时是「发链路凭据」的唯一人工决定：`em_pairing.approve()` 打开 180 s 窗口，设备下次
  连上时签发新 token + CA（[EM-PAIR L1-21]、[EM-CRED L32-84]）。
- 删除设备：删行 → 清 BT/ESPHome/OWW 缓存 → **关掉 control 连接让设备重拨**（否则已连接设备
  会继续服务）→ 设备回来变成 pending（[EM-RD L171-210]）。

**对 RUI 的启示**：approve/pending 成立的前提是「设备能主动带一个未登记的 id 来注册 + 每设备
token」。RUI 现在是**共享 `HOUSE_EAR_DEVICE_TOKEN` + 单 4412 入口**，没有每设备凭据，也没有
pending 表。所以 §2 建议：**0.2 不做审批队列**；向导里的「添加设备」人工动作就是审批。

### 1.4 每设备配置模型

- 两层：`system_config.global_device_config`（fleet 默认）+ `devices.config`（设备覆盖）+
  `devices.config_sections`（覆盖了哪些分区）（[EM-DB L40-200]、[EM-SEC L1-120]）。
- `effective config = fleet config，按分区用设备值覆盖`；不是整份替换。
- 分区：`playback / wakeword / microphones / ring / advanced / bluetooth / sendspin`；
  `STATE_KEYS`（如 `startupVolume`）是设备状态，不参与继承；`FLEET_KEYS`
  （如 `controllerEndpoints`）永远不可按设备覆盖（[EM-SEC L80-120]）。
- 写入：`POST /api/devices/{id}/config` **是 replace 语义**；若 body 会删掉已存 key，回
  `409 would_drop_keys`，除非显式 `replace=true`。校验在写库前完成，失败不半写
  （[EM-RD L286-410]）。
- 写成功后把 **effective config 推给在线设备**；BT proxy 等副作用按 effective config 重算
  （[EM-RD L386-408]）。
- Wi-Fi **永远按设备**，不继承；`controllerEndpoints` 是 fleet-only；`consolePassword` 代码注释
  说明它“实际上按 fleet 用”，虽然技术上落在 `advanced` 分区（[EM-CONF L30-35]、[EM-SEC]）。
- 自定义唤醒词：`owwModel` 在 wakeword 分区，可全局/可按设备；模型文件另有上传/校验接口
  （[EM-CONF]、[EM-UI]）。

**对 RUI 的启示**：RUI 现在没有 fleet/device 两层（一台设备），但**「分区 + 覆盖 + replace 保护」**
值得在引入第二台设备（P3.3 config v3）时再抄。0.2 只加一个 `params` 字典即可（§4.1）。

### 1.5 在线/离线与重连

- 持久状态在 DB `devices` 行（`ip / firmware_ver / last_seen / config / approved / token ...`）；
  实时状态在内存 `em_state.devices[device_id]`（连接、speaking、muted、RTT、listen、OTA 进度等）。
- API 返回的是**合并视图**：`connected = live is not None`，离线时仍显示最后的 volume、baseOs、
  firmware 等持久值（[EM-VIEW L148-325]）。
- 状态页显示 `Online`，离线显示 `Offline` + 多久没见（[EM-CONF L50-58]）。
- 断线重连由设备端负责：controller 只维护 socket；`/data`/`/shell` 必须与 `/control` 同源
  （`em_linkauth.follows_control`，[EM-LINK L104-125]），防止别人抢音频/shell。
- 链路鉴权四规则（[EM-LINK L38-92]）：
  1. 有 token 且不匹配 → 拒绝；
  2. 库里有 token、请求没带 → 允许，除非 `REQUIRE_DEVICE_TLS` 或该设备曾带过 token
     （`confirmed`）——后者防止知道公开 device_id 的人冒充；
  3. 请求带 token 但库里没有 → **忽略**，按 pending 接入（删除后回来的设备才不会成单向门）；
  4. `REQUIRE_DEVICE_TLS=1` → 必须 TLS + 匹配 token。
- 首次带对 token 会写 `token_confirmed_at`，之后没 token 就拒绝（[EM-CTRL L4390-4410]）。
- TLS：controller 自建 CA + 一张 server leaf，设备 pin CA；设备从下次重连起走 `wss`
  （[EM-PKI L1-26]、[EM-CRED L56-80]）。
- 发现：默认 mDNS；跨 VLAN/隧道用 `controllerEndpoints`（设备侧
  `/data/local/etc/echomuse/controller.json`，有序列表，mDNS 仍保底）
  （[EM-EP L1-139]、[EM-CONF L870-945]）。
- Wi-Fi 切换是**设备自己 commit/rollback**：join → DHCP → 回到 controller 三道检查，任一失败
  自动回滚；掉电未确认的变更在下次启动回滚（[EM-CONF L850-870]）。

**对 RUI 的启示**：RUI 的「在线」已经由 `adapter.probe()` + 音频面
`speaker_connected` 合并计算，方向正确；**不要**引入 DB 行存状态，适配器/平面就是实时状态源。
RUI 的设备是**主动拨入**的 ws，没有 mDNS/TLS/CA，也不需要 Wi-Fi 切换 API（A 类由原厂管，
B 类由设备侧仓库管）。

### 1.6 多设备仲裁（「谁听得最响谁答」）

**结论先行**：EchoMuse 当前实现不是简单的「第一个检测到就答」，也不是永远等最响；它经历三次演化
（[EM-ARB L1-110]）：

1. 最早：first detector wins（零延迟，但设备时钟/帧网格会让远的那台先报）。
2. 2026-07-20：改成「等窗口 + 比 SNR」，实测 SNR 分不开（0.9/1.15/0.93），且每次唤醒都加
   ~364 ms 延迟，失败。
3. 现在（2026-10-05 起）：**有多个可 claim 的设备时，每个 utterance 都开一个 contest**，
   hold `hold_s` 收集所有 claim；`pick()` 在能比较时选 **loudest**，否则退回 **earliest heard**；
   tie 给先到的。

关键细节（都能在 [EM-ARB] 查到）：

- 默认窗口 `wakeArbitrationMs = 700`，`0` 关闭仲裁（[EM-CONF L303-313]、本机 DB
  `global_device_config`；该键属于 `wakeword` 分区，可按设备覆盖）。
- claim 按**听到音频的时间** `heard_at` 比较，不按到达时间；winner 的 claim 保持
  `window_s + slack_s`，晚到的 claim 仍能找到它。
- 只有「能服务一轮」的设备才参与：没有 HA pipeline 的设备在 claim 前就被 stand down
  （[EM-ARB L17-25]）。
- loudness 只在可信时使用：任何 claim 缺 level、某台是在自己的播放上听到、或 MICPGA/数字增益
  不同 → 退回 earliest heard（[EM-ARB L88-100]、L129-148）。
- 已知偏差：emOS 设备比 FireOS 5 低约 6 dB，混合 fleet 会偏向 FireOS（[EM-ARB L101-103]）。
- claim 一旦 grant 不撤销；`release()` 在一轮结束后释放，避免压制后续独立唤醒（[EM-ARB L247-255]）。

**对 RUI 的启示**：RUI 音频面是一个进程一个 device，`AudioPlaneSupervisor._pick_device()` 只取
第一台可构建的设备（[RUI-CLIENT L364-418]），且 ROADMAP 明确 0.2 前不做多设备同时出声
（[RUI-ROADMAP L102-105]）。所以「loudest wins」**在 0.2 不适用**；P3.4 真做多设备时，可借鉴
`WakeArbiter` 的「hold + 可比较才比响度 + 否则按听到时间」策略，但需要先有「N 个音频面 + 控制面
路由」，不是把 `em_arbiter.py` 搬过来就行。

### 1.7 每设备网络端口与证书

**端口**（[EM-ENV]、[EM-DB L2230-2380]）：

| 用途 | 默认 | 说明 |
|---|---|---|
| 设备链路 ws | `8767` | `/control`、`/data`、`/shell` |
| 设备链路 wss | `8770` | 自建 CA；`0` 关闭 |
| dashboard/API | `8768` | aiohttp |
| 每设备 ESPHome TCP | `16001` 起 | 一台设备一个端口，给 HA 的 ESPHome 集成拨入 |
| BLE proxy | `esphome_port + 1000` | 固定偏移，不单独分配 |

- 端口分配是**单调递增、永不复用**：`next_esphome_port` 存在 `system_config`，删除设备只
  `free_esphome_port()` 清空该行，不把号码还池。理由写在 [EM-DB L2320-2335]：HA 的旧 config
  entry 还指着老端口，复用会静默接错设备。
- `EM_ESPHOME_PORT_BASE` 是**下限**，用于同一网络跑两套 controller 时错开（[EM-ENV]、
  [EM-DB L2270-2310]）。
- 本机 DB 旁证：`next_esphome_port = 16002`（已分配过 1 个 16001），`device_approval = strict`。

**证书**（[EM-PKI]）：

- **不是每设备一张证书**。全 fleet 一套：
  - `ca.pem`/`ca.key`：controller CA，设备 pin 它；
  - `server.pem`/`server.key`：TLS listener 的 leaf，固定 DNS SAN `echomuse-controller`，
    设备不校验 IP，所以 controller 换地址不用换证书。
- 每设备只有 **token**（DB `devices.token`），由审批/配对推送；token 在设备侧
  `/data/local/etc/echomuse/token`（0600），CA 在 `/data/local/etc/echomuse/ca.pem`。
- 证书有效期故意 backdate 10 年 / 25 年，因为 Echo Dot 没有 RTC，开机时间可能错得离谱
  （[EM-PKI L18-22]）。**不要**按常规 CA 有效期去改。
- TLS 目录权限 0600（本机 `data/tls/*` 实测 0600）；DB 文件本机实测 0644（含 token 列），
  这是 RUI 要避免的（见 §4.2）。

**对 RUI 的启示**：RUI 的每设备 `listen_port` 字段已经在 `DeviceConfig` 里
（[RUI-SCHEMA L120-140] / [RUI-HANDOFF L44-46]），但当前只监听 4412、且只驱动一台设备。
**端口分配/证书在 0.2 都不需要**；等 P3.3/P3.4 做第二台设备时再引入「每设备端口/每设备 token」。

### 1.8 数据库模型

本机部署 `data/echomuse.db`（schema v31，WAL）实测表：

| 表 | 作用 | 关键列（节选） |
|---|---|---|
| `devices` | 设备注册表 | `device_id PK, label, approved, ip, firmware_ver, first_seen, last_seen, config JSON, config_sections JSON, esphome_api_port, token, token_confirmed_at, base_os, kernel_arch, emos_version` |
| `device_logs` | 设备日志 | `device_id, ts, level, source, message` |
| `device_boots` | 每次启动 | `device_id, boot_id, first_seen, firmware_ver, boot_reason` |
| `device_metrics` | 每小时硬件指标 | `device_id, hour_ts, cpu/mem/rssi/rtt/...` |
| `device_wear` | eMMC 磨损 | `device_id, day, emmc_*` |
| `wake_counters` | 每小时唤醒/近失/丢帧 | `device_id, hour_ts, ...` |
| `wake_samples` | 唤醒样本 | `device_id, ts, audio_file, label, score` |
| `turns` | 每轮时延 | `device_id, ts, outcome, stt_text, total_ms, ...` |
| `users` | 控制台账号 | `id, username, password_hash, role, ha_user_id` |
| `sessions` | 登录会话 | `token PK, user_id, expires_at` |
| `system_config` | KV | `schema_version, global_device_config, device_approval, next_esphome_port, ...` |

Schema 迁移在 `em_db.py` 里按 `schema_version` 顺序叠加（`CREATE TABLE IF NOT EXISTS` +
`ALTER TABLE`），本机已到 31（[EM-DB L390-1160]）。

**对 RUI 的启示**：

- RUI 的 `config.yaml` 已经是设备注册表（`devices[]`），且带原子写、0600、last-good、备份、
  控制台编辑、`ConfigError` 恢复——这些是 EchoMuse 的 DB 迁移给不了的。
- RUI 不需要 `users/sessions`（单 token），不需要 `metrics/boots/wear/wake_samples`（没有 HA
  历史面板，ROI 低），也不需要 `turns`（turn trace 已写日志/控制台）。
- 等 P3.4 真要做 approve、OTA、多设备历史时，再评估 SQLite；届时可只建
  `devices/device_state/events` 三张表，不要照搬 11 张。

### 1.9 可以照抄的交互细节 & 明确不抄的坑

**值得照抄（原则，不是代码）**

1. **状态由证据计算**：Setup 完成度从 key/模型/设备连接/试一句推导，不从 cookie/按钮点击算
   （RUI 已经这么做，[RUI-APP L3064-3210]）。
2. **每步都有「用户做什么 / RUI 做什么」**，失败有稳定原因 + 固定恢复动作（Retry / Reconnect /
   Diagnostics）。
3. **设备侧动作失败不要污染配置**：校验在写前，写失败不留半份。
4. **删除设备要「让设备重拨」**，否则已连接设备会成为幽灵（这条在 RUI 是断开 adapter/重启音频面）。
5. **端口/编号单调不复用**（将来多设备时）。
6. **诊断包服务端生成 + 脱敏**，浏览器只负责触发与下载。
7. **label 唯一**：两台设备同名在面板和下游都无法区分（RUI 是 `id` + `room`，也一样应该唯一）。

**明确不抄**

- USB/WebUSB/ADB/TWRP/串口刷机、emOS 镜像构建、Magisk、debloat、OTA/firmware 发布；
- HA Assist / ESPHome satellite / 每设备 ESPHome 端口；
- BLE proxy、Sendspin、录音/转写历史、资源指标 180 天；
- mDNS 发现、自建 CA、Wi-Fi 切换/回滚；
- users/roles/HA ingress、setup token；
- 多设备仲裁（RUI 还没有多音频面）。

### 1.10 核实状态（务必区分）

| 结论 | 状态 | 依据 |
|---|---|---|
| 向导 13/9 步、auto/manual/input、forward-only、每步模式校验、拔线=abandon | **已验证（源码）** | [EM-UI L3226-3270]、L3828-3912、L8573-8710 |
| 向导进度不落库，刷新即从头 | **已验证（schema 无表 + 组件 state）；刷新行为推断** | [EM-DB]、[EM-UI L4318-4395] |
| register→pending→approve→ack/config 流程 | **已验证（源码）** | [EM-CTRL L5056-5260]、[EM-RD L236-265] |
| fleet + 分区覆盖、replace 保护、live push | **已验证（源码）** | [EM-RD L286-410]、[EM-SEC] |
| 链路鉴权四规则、token confirmed、stale token→pending | **已验证（源码）** | [EM-LINK L38-92]、[EM-CTRL L4390-4410] |
| 每设备 ESPHome 端口单调不复用；CA 全 fleet 一套 | **已验证（源码 + 本机 DB）** | [EM-DB L2230-2380]、[EM-PKI]、本机 schema v31 |
| 仲裁 = contest + 可比较时 loudest，否则 earliest | **已验证（源码）** | [EM-ARB L1-148]、L200-245 |
| 本机部署版本 v2.26.1 / fw v2.18.1 / schema 31 | **已验证（本机只读查询）** | `services/echomuse-controller/data/echomuse.db`、`.env` |
| Docker 首次 setup token；HA add-on 走 ingress | **文档说明，auth 源码未逐行读** | [EM-QS L122-160] |
| OTA/firmware/emOS 构建细节 | **只读了向导/路由入口，未深入** | [EM-RP L420-577] |
| 上游 main 的行号会随版本漂移 | **已知** | 本文按 2026-10-24 的 `main` 记录 |

---

## 2. 映射到 RUI：照抄 / 改造 / 跳过

### 2.1 前提差异（决定了不能照搬）

| 维度 | EchoMuse | RUI |
|---|---|---|
| 助手 | 外部 Home Assistant Assist | **RUI 自己**（turn/ASR/LLM/TTS/播放仲裁） |
| 设备 | 只有 Echo Dot 2（B 类，root，EchoMuse 固件） | LX06（A 类，无 root，open-xiaoai client）+ Dot2（B 类，外部插头） |
| 设备连接 | 设备主动拨 controller，三条 ws 平面 | 设备主动拨 4412；端口内协议由 adapter 决定（LX06 一条 ws；dot2 是 `/control` + `/data`） |
| 状态源 | 内存 live + SQLite 行 | `adapter.probe()` + 音频面指标（**无 DB**） |
| 配置 | fleet + 每设备分区覆盖 | 单一 `config.yaml`，`devices[]` |
| UI | Fleet dashboard + 设备模态 | Vue 控制台 + PanelSpec + SetupView/DeviceManager |
| 多设备 | 完整 fleet/仲裁/端口/证书 | **一个音频面一台设备**，多设备是 P3 |
| 认证 | users/roles/HA ingress | 单 `X-Auth-Token` |

### 2.2 三类映射

**A. 可以干净映射（概念直接可用）**

| EchoMuse | RUI 现状 | 做什么 |
|---|---|---|
| 设备列表 + 单设备详情 | `GET /api/v1/devices`、`GET /devices/{id}`（[RUI-APP L3011-3062]、L3437-3457） | 在响应里加 `plug/identity/capabilities/status_reason/checked_at`（只加字段，不改语义） |
| 在线/离线 | `_device_link()`：adapter health + `speaker_connected`（[RUI-APP L837-860]） | 保留；加 `reason` 解释为什么离线 |
| 连接信息 | `GET /devices/{id}/connect` 返回 ws_url + token（[RUI-APP L3459-3515]） | 保留；向导里做成「复制」步骤 |
| 探测 | `_probe_host()`：TCP 22 + SSH banner（[RUI-APP L182-207]） | 通用化为 allowlist 探测（TCP/HTTP/ws），P3.5 再收紧 SSRF |
| 删除设备 | `DELETE /devices/{id}` + 重启音频面（[RUI-APP L3572-3584]） | 保留；补「删除后设备会重拨」的提示 |
| 每设备配置合并 | `PUT` 只改传入字段、保留 LED/volume（[RUI-APP L3517-3570]） | 保留；manifest 参数表单也走这条 |
| 诊断包 | 无 | 新增服务端脱敏 JSON（§3.6） |
| 状态由证据计算 | `_wizard_state()`（[RUI-APP L3064-3210]） | 保留并扩展为 manifest 步骤 |

**B. 需要改造（原则对，形态不对）**

| EchoMuse | 改造后 |
|---|---|
| USB/ADB 安装向导 | **插头声明 + 通用步 + 插头步**：设备侧安装只给链接/命令/表单，完成度靠连接测试 |
| 每设备配置 = fleet + 分区覆盖 | 0.2 只加 `DeviceConfig.params: dict`；等第二台设备再上分区 |
| approve/pending 队列 | 0.2 **不做**；向导「添加设备」即人工审批。等每设备 token + 设备主动 register 时再上 |
| 每设备端口/CA | 0.2 **不做**；保留 `listen_port` 字段，第二台设备时再做 |
| Fleet dashboard（Online/Updates/Pending） | 单设备时只显示「设备在线/离线 + 音频面」；多于 1 台再加摘要 |
| 设备日志 DB + Activity | 复用现有 `service.log`/控制台日志页，不建表 |
| 多设备仲裁 | P3.4；先有 N 个音频面再说 |

**C. 明确跳过**

HA Assist/ESPHome、WebUSB/ADB/TWRP/刷机/emOS、OTA/firmware 发布、BLE proxy、Sendspin、
录音/转写历史、硬件指标 180 天、mDNS 发现、自建 CA/TLS 设备链路、Wi-Fi 切换、users/roles、
HA ingress、setup token、per-device ESPHome port。

### 2.3 对 `rui-dot2-bridge` 盲开契约的回应

Dot2 仓库已有一份「盲开」manifest（[DOT2-CONTRACT L37-82]）和「adapter 顶替 controller」的
ADR（[DOT2-ADR]）。本文建议的契约与它**兼容但需要三处补充**：

1. **保留**它们的顶层字段名（`plug/version/requires_socket/identity/transport/audio/volume/
   capabilities/health/limits/control/entrypoint`）——这些已经够描述运行时。
2. **新增** `manifest: "rui-plug/v1"`、`class: "A"|"B"`、`params`（JSON Schema 子集）、
   `setup.slots`（向导步骤）——这三样是 RUI 插座需要的。
3. **明确** `entrypoint` 的信任边界：内置/签名可信插头才 in-process；第三方默认 sidecar
   （与 HANDOFF §3.14 一致，[RUI-HANDOFF L67-68]）。`entrypoint` 不允许由 HTTP 传参或从
   `data/plugs` 直接 import 执行。

对它们「待确认」五问的暂定答案（写进 P2.1 标准时可改）：

| 问题 | 暂定 |
|---|---|
| 包 + manifest 还是纯 JSON | **manifest 必需；代码包可选**（与 HANDOFF §3.14 一致） |
| 参数注入入口 | console/`POST /api/v1/setup/step` → `config.devices[].params`；环境变量只做部署覆盖 |
| `requires_socket` 比较 | semver 区间（如 `>=0.2.0 <1.0.0`）；不满足则 manifest 可见但不可安装 |
| 健康/遥测字段 | `connected/last_seen/latency_ms/error/identity/capabilities`；单位固定 ms |
| 安装/卸载/回滚 | RUI loader 负责；卸载=移目录；回滚=保留上一版 manifest + `params` 不动 |

---

## 3. RUI 安装向导设计（P2.2 输入）

### 3.1 设计原则

1. **服务端是唯一事实源**：完成度由 key/模型/配置/连接实测计算；前端只渲染。
2. **声明式优先**：插头只提供数据（manifest），不提供可执行脚本/命令；所有动作是服务端
   allowlist 里的具名函数。
3. **设备侧动作不可执行就降级为「说明 + 链接 + 证据」**：A 类（LX06）给 open-xiaoai client
   安装说明和 ws URL；B 类（Dot2）给设备侧仓库链接 + 参数，完成度由 `adapter.connected` 判定。
4. **每一步幂等**：重复提交不产生第二台设备/第二份配置；写配置原子、0600。
5. **失败可恢复**：稳定错误码 + 人话 hint + 固定按钮（重试/重测/复制/诊断）。
6. **刷新不丢进度**：进度由服务端重算，浏览器不需要保存向导状态。
7. **不引入新存储**：0.2 复用 `config.yaml` + `secrets.yaml` + `data/plugs/`（只读 manifest）。

### 3.2 Manifest 契约 v0（建议）

> 正式规范由 P2.1 `docs/socket-plug.md` 定稿；下面只是本调研建议的最小可用形态。
> 完整示例（原创，非上游代码）：

```json
{
  "manifest": "rui-plug/v1",
  "id": "dot2",
  "version": "1.0.0",
  "requires_socket": ">=0.2.0 <1.0.0",
  "class": "B",
  "identity": {
    "model": "Echo Dot 2 (RS03QR)",
    "vendor": "Amazon",
    "firmware": "EchoMuse v2.18.1",
    "source": "rui-dot2-bridge"
  },
  "adapter": "dot2",
  "transport": {
    "direction": "inbound",
    "schemes": ["wss", "ws"],
    "default_port": 4412,
    "auth": { "kind": "query_token", "param": "token" }
  },
  "capabilities": [
    "playback", "led", "keys", "volume"
  ],
  "audio": {
    "capture": { "rate": 16000, "channels": 1, "format": "s16le", "frame_ms": 20 },
    "playback": { "rate": 48000, "channels": 1, "format": "s16le" }
  },
  "params": {
    "type": "object",
    "properties": {
      "room": { "type": "string", "title": "房间", "maxLength": 32 },
      "wake_words": { "type": "array", "items": { "type": "string" }, "maxItems": 3 },
      "volume_max": { "type": "integer", "minimum": 0, "maximum": 127, "default": 100 },
      "mic_gain_db": { "type": "integer", "minimum": 0, "maximum": 42, "default": 18 }
    },
    "required": ["room"],
    "additionalProperties": true
  },
  "setup": {
    "slots": {
      "prepare": {
        "kind": "instructions",
        "title": "在设备侧安装/确认 client",
        "body": "Dot2 需要先由设备侧仓库完成解锁与 EchoMuse 设备端安装。RUI 不刷机、不改固件。",
        "links": [
          { "label": "rui-dot2-bridge 部署说明", "url": "https://github.com/yes999zc/rui-dot2-bridge" }
        ]
      },
      "test": {
        "kind": "verify",
        "run": "adapter.connected",
        "timeout_s": 30
      }
    }
  },
  "health": {
    "fields": ["connected", "last_seen", "latency_ms", "error", "firmware"]
  },
  "trust": { "tier": "builtin", "code": "in-process" },
  "entrypoint": null
}
```

**字段规则（建议写进 P2.1）**：

- `manifest` 版本化；未知顶层字段**忽略**（前向兼容），未知枚举值（如新 `kind`）**报错并隐藏该
  步骤**，不阻塞整个向导。
- `id` = 插头 id；`adapter` 必须在 `device/registry.py` 已注册；`id` 与 `adapter` 可以不同
  （一个 adapter 服务多个 manifest 时）。
- `class` 只用于 UI 提示/文档，不做核心分支。
- `capabilities` 是 `DeviceInfo.capabilities` 的子集；缺省=不支持，UI 显示降级原因。
- `params` 用 JSON Schema 的**子集**：`type/properties/required/default/enum/minimum/maximum/
  minLength/maxLength/maxItems/items`；不接受 `$ref`、`patternProperties`、`if/then` 等。
- `setup.slots` 只允许 `prepare`、`test`（0.2）；后续可加 `post`。不允许 `run` 任意命令。
- `trust` 由 loader 赋值，manifest 自述不可信；`entrypoint` 只在 `trust.code != "none"` 时使用。
- **manifest 里不得出现任何 secret**；参数默认值也不能是 token。

### 3.3 通用步 vs 插头步

RUI 0.2 建议用**固定骨架 + 插头槽位**，比「manifest 完全自由编排步骤」更容易在 2 天做完，也更
安全。骨架：

| # | 步骤 id | 谁做 | 类型 | 内容 | 完成证据 |
|---|---|---|---|---|---|
| 1 | `keys` | 用户填，RUI 存 | generic form | LLM/ASR/TTS key（现有 `/setup/keys`） | 每个 provider 链可用（现有逻辑） |
| 2 | `models` | RUI 检查 | generic check | SenseVoice/VAD/KWS 文件（现有逻辑） | 文件齐全（现有逻辑） |
| 3 | `prepare` | **用户**（按插头说明操作设备侧） | plug instructions | 插头给的链接/命令/最低 client 版本 | 用户点「已完成」；或插头声明 `evidence: adapter.connected` |
| 4 | `params` | 用户填，RUI 校验并写入 | plug form + action | `params` schema + 通用 `host/room/wake_words`，校验后 `device.upsert` | schema 校验通过且设备记录存在 |
| 5 | `connect` | 用户复制，设备侧填 | generic transport | 用第 4 步生成的设备 id 调 `GET /devices/{id}/connect`，得到 URL/token；inbound 显示端口 | 设备侧已保存（用户确认） |
| 6 | `test` | RUI 测 | plug verify | `adapter.connected`（或 `tcp.connect`/`http.get` allowlist） | 连接成功 + 音频面 `speaker_connected` |
| 7 | `wake` | 用户填，RUI 写 | generic form | 唤醒词（现有 `/setup/wake`） | `keywords.txt` 存在 |
| 8 | `verify` | 用户说一句，RUI 听 | generic verify | 现有 `POST /api/v1/debug/say`（SetupView 的 `api.say`） | 一轮有 `audio_bytes` |

**插头只填 3、4、6 三个槽**：

- `prepare`：A 类 = open-xiaoai client 安装说明（[RUI-OX L19-31]）；B 类 = 设备侧仓库链接。
- `params`：插头 schema；RUI 自动合并通用字段（`host` 必填、`room` 可空、`wake_words` 最多 3）。
- `test`：`run` 只能是 allowlist：`adapter.connected`、`tcp.connect`、`ssh.banner`、`http.get`
  （仅限私网 + 端口 allowlist）。

这样 P2.1 的标准可以先定「骨架 + 槽位」，等第三方插头多了再演进到「完整步骤数组」。

### 3.4 状态机

**步骤状态（服务端每次 GET 重算，不持久化）**

```
pending ──▶ ready ──▶ running ──▶ done
                 │          └──▶ failed ──▶ (retry) ready
                 └──▶ skipped  (optional only)
```

| 状态 | 含义 | 何时进入 |
|---|---|---|
| `pending` | 前置步骤未完成 | 初始/前序未 done |
| `ready` | 可执行，等用户或等待运行 | 前置 done |
| `running` | 正在执行 | `POST /setup/step` 开始 |
| `done` | 证据满足 | 服务端检查通过 |
| `failed` | 本次执行失败 | 异常/校验失败；`last_error` 只在内存 |
| `skipped` | 可选步骤跳过 | `optional: true` 且用户点跳过 |

**全局状态**：`next_step` = 第一个非 done 的 required 步骤；`complete` = 所有 required done；
`blocked` = manifest 不满足 `requires_socket` 或 adapter 不可构建（文案给原因）。

**规则**

- 只前进，但**允许重跑任意已 done 步骤**（幂等），不做「上一步」。
- 同一时刻一个向导一个 `asyncio.Lock`，防双击/多标签并发。
- 每个 `step` 有 `timeout_s`（默认 30 s；模型下载不在此向导内）。
- 校验/执行失败**不写配置**；配置已写入后音频面重启失败，按 `plane_error` 返回（不假装成功）。
- 失败不持久化：刷新后回到 `ready`（错误信息丢失，但可从日志/诊断找）。

**与 EchoMuse 的差异**：EchoMuse 的 `error` 是持久在本次向导里的；RUI 用服务端重算更简单，
代价是刷新后看不到上次错误——用「诊断包/日志」补。

### 3.5 API 草案

**复用现有（不改语义）**

| 方法/路径 | 用途 |
|---|---|
| `GET /api/v1/setup` | 现有向导状态（[RUI-APP L3298-3301]） |
| `POST /api/v1/setup/keys` | 写 key（[RUI-APP L3303-3317]） |
| `POST /api/v1/setup/device` | 加/改设备（[RUI-APP L3362-3406]） |
| `POST /api/v1/setup/wake` | 写唤醒词（[RUI-APP L3408-3423]） |
| `POST /api/v1/setup/probe` | host 探测（[RUI-APP L3425-3428]） |
| `POST /api/v1/debug/say` | 试一句（现有 SetupView 调用；见 [RUI-SETUPVUE L196-230]） |

**新增（0.2 最小）**

| 方法/路径 | 请求 | 响应 | 说明 |
|---|---|---|---|
| `GET /api/v1/plugs` | — | `[{id,version,class,identity,capabilities,source,available,error}]` | 内置 + `data/plugs/`；坏 manifest 进 `error` 不致命 |
| `GET /api/v1/plugs/{id}` | — | `{manifest, available, error}` | 详情，含 `params`/`setup`/`health` |
| `GET /api/v1/setup?plug={id}` | query | `SetupState`（扩展 `plug` + 8 步） | 不传 `plug` = 现有默认（xiaoai） |
| `POST /api/v1/setup/step` | `{plug, step, values}` | `SetupState` | 执行一个 allowlist 步骤；返回新状态 |
| `POST /api/v1/devices/{id}/test` | — | `{ok, connected, latency_ms, error, detail}` | 连接测试（adapter probe + 可选中继） |
| `GET /api/v1/setup/diagnostics` | `?plug=&step=` | `application/json` 附件 | 脱敏诊断包（可选，0.5 天） |

**`POST /api/v1/setup/step` 的 allowlist**（manifest 只能引用这些名字，不能内联命令）：

| action | 作用 |
|---|---|
| `keys.save` | 写 `secrets.yaml`（现有逻辑） |
| `device.upsert` | 合并写 `devices[]`、写 keywords、重启音频面（现有 `_write_devices`） |
| `device.test` | `adapter.probe()` + 平面状态 |
| `wake.set` | 写唤醒词（现有逻辑） |
| `manual.confirm` | 仅把 `prepare` 标记完成（用户确认） |

**状态响应示例（原创）**

```json
{
  "plug": { "id": "dot2", "version": "1.0.0", "class": "B", "source": "builtin" },
  "next_step": "prepare",
  "complete": false,
  "blocked": null,
  "steps": [
    { "id": "keys", "kind": "generic", "done": true, "required": true, "detail": {} },
    { "id": "models", "kind": "generic", "done": true, "required": true, "detail": {} },
    { "id": "prepare", "kind": "plug", "done": false, "required": true,
      "detail": { "links": [ { "label": "部署说明", "url": "https://github.com/yes999zc/rui-dot2-bridge" } ] } },
    { "id": "params", "kind": "plug", "done": false, "required": true,
      "detail": { "schema": { "type": "object", "properties": {} } } },
    { "id": "connect", "kind": "generic", "done": false, "required": true,
      "detail": { "ws_url": "ws://<server-lan-ip>:4412/?token=<device-token>", "configured": true } },
    { "id": "test", "kind": "plug", "done": false, "required": true,
      "detail": { "run": "adapter.connected" } },
    { "id": "wake", "kind": "generic", "done": false, "required": true, "detail": {} },
    { "id": "verify", "kind": "generic", "done": false, "required": false, "detail": {} }
  ]
}
```

### 3.6 失败与诊断 UX

**错误形状**（所有新增端点统一）：

```json
{
  "code": "adapter_not_connected",
  "message": "音箱还没连上 RUI 音频面",
  "hint": "确认设备侧 client 已启动、端口 4412 可达、token 与连接信息一致。",
  "detail": { "connected": false, "plane_error": "" },
  "retryable": true
}
```

- `code` 稳定、可被 i18n 映射；`message/hint` 中文 + 英文（符合仓库双语交付要求）。
- `detail` 只放可公开的运行时信息，**不放 token/key**。

**面板固定动作**：

| 按钮 | 行为 |
|---|---|
| 重试 | 重跑当前 `setup/step` 或 `devices/{id}/test` |
| 重新检查 | 重算 `GET /setup`（不执行） |
| 复制连接信息 | 复制 `ws_url`/token（token 仍只在 connect 端点返回） |
| 下载诊断 | `GET /setup/diagnostics`（脱敏 JSON） |

**诊断包内容（allowlist，服务端生成）**：RUI 版本、config 路径/是否存在、设备摘要（id/room/
adapter/plug/enabled/listen_port/params 脱敏后）、step 状态与 `detail`、当前 adapter 错误、平面状态、
最近 50 行 `service.log`（token/key 正则脱敏）、探测结果（host 可保留或掩码）。**禁止**包含：
`web.auth_token`、`secrets.yaml` 值、device token、完整 config、个人路径、LAN IP（或只给掩码）。
原则抄 EchoMuse 的「服务端打包 + 脱敏」，不抄它的文件实现。

### 3.7 幂等与恢复

| 场景 | 保证 |
|---|---|
| 重复点「保存参数/添加设备」 | 按 `device.id` 合并；不会出现第二台设备（现有 `_write_devices` 语义） |
| 重复 `POST /setup/step` | 同参数同结果；`device.upsert` 是 merge |
| 写 config 崩溃 | `save_config` 原子写 + 0600 + last-good（[RUI-STORE L116-141]、[RUI-DEPLOY]） |
| 写完后音频面起不来 | 配置已写入但 `plane_error` 回传；向导显示「已保存，音频面未启动」并给重启/日志入口（现有 `plane_error` 语义，[RUI-APP L3265-3296]） |
| 刷新/重启服务 | `GET /setup` 从磁盘重算；用户只需重填 key（secret 不回显） |
| 插头被删/不可用 | 设备仍在 `config.devices[]`；`GET /devices` 返回 `unavailable` + `error`（现有 registry 行为，[RUI-REG L90-110]） |
| 多标签并发 | 每 plug/device 一把 `asyncio.Lock`；第二个请求 409 `step_in_progress` |

### 3.8 1–2 天落地范围（建议）

**Day 1（后端契约 + 只读）**

1. `src/house_ear/device/manifest.py`：`PlugManifest`（pydantic）、loader（内置目录 +
   `data/plugs/`）、`available()`、`requires_socket` 校验、错误收集（~200 行）。
2. `src/house_ear/device/plugs/xiaoai.plug.json`、`dot2.plug.json`：两个最小 manifest（~80 行/个）。
3. `app.py`：`GET /api/v1/plugs`、`GET /api/v1/plugs/{id}`；`GET /api/v1/setup?plug=` 并入现有
   `_wizard_state()`（~150 行）。
4. `tests/test_plug_manifest.py`：合法/非法 manifest、版本不满足、未知字段、坏文件不致命（~6 条）。

**Day 2（向导壳 + 设备参数/测试）**

5. `POST /api/v1/setup/step`（4 个 action）+ `POST /api/v1/devices/{id}/test`（~120 行）。
6. `web/src/views/SetupView.vue`：插头选择 + 8 步骨架 + `params` 表单渲染 + 失败面板（~250 行）。
7. `web/src/components/DeviceManager.vue`：状态 chip、参数表单、连接测试按钮（~120 行）。
8. `web/src/api.ts` 类型/方法；i18n 中英 key；`tests/test_setup_plug.py`（~8 条）。
9. 验收：`GET /plugs` 两个内置插头；`/setup?plug=dot2` 八步；重复提交不重复建设备；
   现有 5 步向导行为不回归；`npm run typecheck && npm run build` 绿。

**不在这 2 天里**：第三方代码加载、sidecar、SQLite、approve/OTA、多设备、TLS、mDNS。

---

## 4. RUI 设备管理后台设计（P2.4 输入）

### 4.1 数据模型

**结论：0.2 继续用 `config.yaml`，不建 DB；新增一个 `params` 字典。**

```text
PlugManifest（只读，磁盘）
  id, version, requires_socket, class, identity, adapter,
  transport, capabilities[], audio, params(JSON Schema),
  setup.slots, health, trust, entrypoint?

DeviceConfig（config.yaml devices[]，现有 + 1 字段）
  id, adapter, host, room, enabled, wake_words[], volume,
  listen_port, token_env, allow_no_token, led{...}
  + params: dict = {}        # 新增；按 manifest params 校验

DeviceRuntime（内存，不持久化）
  plug, identity, capabilities[], transport,
  status(ready|connecting|disconnected|unavailable),
  connected: bool|null, last_seen, latency_ms, error, checked_at

DeviceView = DeviceConfig + DeviceRuntime（API 返回）
```

- `adapter` 保持现有含义（registry 的 builder key）；`plug` 由 manifest 反查，**不新增 config
  字段**，避免两份事实。
- 老配置没有 `params` → 默认 `{}`，兼容。
- `params` 里的值只放**设备行为参数**（音量刻度、mic gain、eq 预设、连接覆盖），不放 secret；
  secret 仍走 `token_env`/`secrets.yaml`。
- 为什么不用 SQLite：设备数量小（1–5）、现有配置有原子写/last-good/备份/恢复/控制台编辑、
  `config.yaml` 可以直接被人看懂；DB 会多一套迁移/备份/恢复路径，而 P2.4 的判据只是「不用手改
  YAML」。等 P3.4 需要 approve 历史/OTA/多设备指标时再评估。

### 4.2 存储与权限

| 内容 | 路径（容器内） | 权限 | 说明 |
|---|---|---|---|
| 设备注册表 | `config/config.yaml`（`system.data_dir` 外） | **0600** | 现有 `save_config` 保证（[RUI-STORE L116-141]、[RUI-DEPLOY L121]） |
| 设备/服务 key | `data/secrets.yaml` | **0600** | 现有 `secrets.put`（[RUI-SECRETS L52-80]） |
| 内置 manifest | `src/house_ear/device/plugs/*.json` | 0644（镜像内只读） | 随镜像发布 |
| 外部 manifest | `data/plugs/<plug-id>/plug.json` | 0644，目录 0755 | 只读加载；拒绝 symlink/路径穿越；单文件 ≤64 KiB |
| 可选插头代码 | `data/plugs/<plug-id>/` | 0644/0755 | **0.2 不加载**；P3 由 sidecar 读 |
| 状态/诊断 | 不落盘（诊断按需生成后下载） | — | 无新敏感文件 |
| 日志 | `data/service.log` | 0600 | 现有（[RUI-DEPLOY L294]） |

**权限纪律**：任何含 token 的文件（config 目前不含 token 值，secrets 含）必须 0600；manifest
永远不含 secret；诊断包不写盘。EchoMuse 本机 DB 文件是 0644（含 token 列），这是 RUI 不应
复制的细节（标记为上游/本机观察，不作为上游缺陷结论）。

### 4.3 API surface

**保留**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/devices` | 列表；扩展字段 |
| GET | `/api/v1/devices/{id}` | 详情；扩展 `live` |
| GET | `/api/v1/devices/{id}/connect` | ws_url + token（唯一返回 token 的端点） |
| PUT | `/api/v1/devices/{id}` | 局部更新；加 `params` |
| DELETE | `/api/v1/devices/{id}` | 删除 + 重启音频面 |
| POST | `/api/v1/devices/probe` | host 探测 |

**新增（最小）**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/plugs` | 插头列表（含不可用原因） |
| GET | `/api/v1/plugs/{id}` | 插头详情 |
| POST | `/api/v1/devices/{id}/test` | 连接测试（不写配置） |
| GET | `/api/v1/setup/diagnostics` | 脱敏诊断包 |

**暂不做**（P3.4 再评估）：`/approve`、`/pending`、`/logs`、`/activity`、`/ota`、`/wifi`、
`/shell`、`/metrics`。

**返回字段扩展（只加不改）**

```json
{
  "id": "<device-id>",
  "adapter": "dot2",
  "plug": "dot2",
  "identity": { "model": "...", "vendor": "...", "firmware": "..." },
  "room": "客厅",
  "host": "<lan-ip>",
  "transport": "inbound",
  "status": "disconnected",
  "connected": false,
  "last_seen": null,
  "latency_ms": null,
  "capabilities": ["playback", "led", "keys", "volume"],
  "missing_for_barge_in": ["reference"],
  "params": { "volume_max": 100 },
  "error": "",
  "checked_at": 0
}
```

`status` 语义：`ready`（连接且可用）、`connecting`（已配置未连上，短暂）、
`disconnected`（已配置但当前无连接）、`unavailable`（adapter/manifest 不可构建）。
`connected` 仍可为 `null`（没有平面时无法判断），不要把它强制成 `false`。

### 4.4 安全与信任模型

**四层信任（与 HANDOFF §3.14 一致）**

| 层 | 来源 | 运行形态 | 默认 |
|---|---|---|---|
| `builtin` | 随镜像 `device/plugs/` + `device/<name>/` | in-process | ✅ 开 |
| `local-trusted` | 本机安装、人工审过的 manifest + 代码包（如 dot2 当前形态） | in-process | ⚠️ 默认只开内置；本机需显式开启 |
| `manifest-only` | `data/plugs/*/plug.json`，引用已注册 adapter | 无代码执行 | ✅ 开 |
| `external` | 第三方包（含代码） | **sidecar 容器** | ❌ 默认关 |

**规则**

1. **manifest 是数据，不是代码**：不 `eval`、不 `exec`、不拼 shell；`run`/`action` 只接受
   allowlist 名字；HTTP 请求不能传入命令/路径。
2. **`entrypoint` 不 import 执行**：0.2 只允许内置代码；第三方代码走 sidecar（进程隔离、
   JSON/HTTP 契约、资源限制、无控制面文件写权限），由 P2.1 标准定义协议。
3. **路径安全**：`data/plugs/<id>/` 用 `realpath` 校验在根内；拒绝 symlink；manifest ≤64 KiB；
   只读加载。
4. **探测 SSRF**：`host` 只允许私网/链路本地（`10/8`、`172.16/12`、`192.168/16`、`fc00::/7`、
   `fe80::/10`、loopback 视部署而定）；端口 allowlist（现有 22、4412、设备插头声明的端口）；
   connect timeout 2 s；不跟随重定向。ROADMAP P3.5 已排期，0.2 先做最小。
5. **token**：每设备 `token_env`；只在 connect 端点返回值；不日志、不进诊断；`secrets.yaml`
   0600；`allow_no_token` 仅开发。
6. **控制台认证**：沿用 `X-Auth-Token` + 失败预算（[RUI-APP L1737-1758]）；不做角色。
7. **公开仓约束**：manifest/代码/文档不出现 LAN IP、个人路径、真实 token；示例用占位符
   （[RUI-HANDOFF L65]）。

**in-process vs sidecar（建议）**

| 维度 | in-process | sidecar |
|---|---|---|
| 隔离 | 无；插头崩溃可拖垮控制面 | 进程/容器隔离，可限资源 |
| 契约 | 直接 `DeviceAdapter` | 需定义 IPC/HTTP 协议 |
| 部署 | 一个镜像 | NAS 多一个镜像/进程 |
| 适合 | 内置 LX06、审过的 dot2 | 第三方/未签名插头 |
| 结论 | **0.2 只做 in-process 内置 + manifest-only** | **P3 再做，默认第三方** |

### 4.5 生命周期

- **安装插头**：0.2 仅内置；外部 manifest 放到 `data/plugs/<id>/plug.json` 后重启服务（或不重启，
  由 loader 按 mtime 重载——建议先只支持重启，简单可预期）。
- **升级**：`requires_socket` 不满足 → `available=false` + 原因；满足 → 新 manifest 覆盖，
  `params` 不动；adapter 名不变就不迁移设备。
- **卸载**：移目录；引用它的设备变 `unavailable`，`GET /devices` 给可读错误（现有 registry 行为）。
- **删除设备**：现有 `DELETE` → 写 config → 重建 adapters → 重写 keywords → 重启音频面。
  与 EchoMuse 不同，RUI **没有 pending 队列**：设备会持续重拨但不会被自动重新登记；控制台要
  明确提示「删除后设备不会自己回来，需重新添加/重新导入」。如果将来要保留「删除后重新出现」，
  必须先有每设备 token + 发现/pending（P3.4）。
- **并发**：setup 步骤、设备写、音频面重启共用一把 `asyncio.Lock`（或现有串行化），避免两个请求
  同时写 config/重启。

### 4.6 控制台 IA（P2.4 最小）

- `/setup`（现有）：顶部插头选择（`LX06（A 类，内置）` / `Dot2（B 类，外部插头）`）→ 8 步骨架；
  每步卡显示「你要做什么 / RUI 做什么」、完成标记、失败 hint。
- `/panel/devices-admin`（现有）：列表行 = id + room + plug + status chip + 在线时间；
  详情 tabs：
  - **概览**：identity、transport、capabilities、连接状态、错误；
  - **参数**：manifest `params` 表单 + 高级（原始 `config.yaml` 字段只读/可编辑但提示风险）；
  - **连接**：ws_url/token 复制、`test` 按钮、最近一次测试结果；
  - **诊断**：下载诊断包、查看 adapter 错误；
  - **删除**：二次确认 + 影响说明。
- 不新增独立「插件页」；在 Setup 的插头选择和 DeviceManager 的「添加设备」里体现即可。
- i18n：中英双语（`zh-Hans`/`en`），错误 code 也要有英文文案。

### 4.7 兼容与迁移

- 老 `config.yaml`：`params` 缺省 `{}`；`adapter: xiaoai` 自动匹配内置 manifest；无需迁移。
- `GET /api/v1/devices`：只加字段，不改 `status/connected` 语义；前端旧代码不受影响。
- `SetupView` 默认不传 `plug` → 现有五步行为不变（回归测试钉住）。
- 新增 `POST /devices/{id}/test` 不改 PUT；测试失败不改配置。
- 文档：P2.1 写 `docs/socket-plug.md`，P2.2 写向导设计（可引用本文 §3），P2.4 写运维说明。

---

## 5. 建议与开放问题

### 5.1 给 Maintainer 的建议（按优先级）

1. **先把 manifest 契约定死，再写向导**：P2.1 `docs/socket-plug.md` 以本文 §3.2/§4.1 为输入；
   契约里明确「manifest 必需、代码可选、run 只能 allowlist」。
2. **0.2 不做 approve/OTA/多设备/TLS**：RUI 的「人工添加设备」就是审批；等每设备 token +
   设备主动 register 再上 pending 队列（对应 `rui-dot2-bridge` 的 register 能力）。
3. **保留 `config.yaml`**：加 `params` 字段即可；不要为了「像 EchoMuse」引入 SQLite。
4. **抄交互，不抄实现**：状态由证据计算、每步恢复动作、服务端诊断包、删除后让设备重拨、
   label/编号唯一——这五条足够。
5. **向导先服务 LX06 + Dot2 两类**：A 类给安装说明 + ws URL；B 类给外部链接 + 参数 + 连接测试；
   两者共用 8 步骨架，插头只填 3 个槽。
6. **第三方代码默认 sidecar**（HANDOFF §3.14 已定），0.2 不实现；本文的 `trust` 字段先占位。
7. **合规**：EchoMuse 是 MIT；若后续照抄代码/文案，保留版权声明并进 `THIRD_PARTY.md` +
   README 致谢（P1.6 已排）。

### 5.2 需要 9 哥 拍板的 Top 3

1. **插头信任模型**：0.2 是否只允许「内置/本机审过的 in-process + manifest-only」？
   第三方可执行代码是否一律 sidecar、默认关闭、需要显式开关/签名？——这决定 P2.1 标准的边界，
   也决定 P2.4 要不要在 UI 里做「安装插头」。
2. **设备注册表形态**：确认继续用 `config.yaml` + `params`，还是趁 P2 直接上 SQLite？
   以及 0.2 是否接受「没有 approve/pending、没有每设备 token」？——这决定 P2.4 的 API 和
   P3.3 config v3 的迁移成本。
3. **P2.4「设备管理最小版」的边界**：只做单设备 CRUD + 参数 + 连接测试 + 删除，还是把
   「第二台设备/每设备端口/每设备 token/多设备仲裁」也塞进 P2？——本文建议前者，后者留 P3.3/P3.4。

### 5.3 其它待确认（不阻塞）

- manifest id/字段命名是否完全沿用 `rui-dot2-bridge` 盲开稿（`plug/version/requires_socket/...`），
  还是 RUI 另起 `rui-plug/v1` 并在 P2.1 做一次映射？
- 诊断包里 host/IP 是保留、掩码还是只给哈希？（安全 vs 可诊断）
- 向导失败后是否要持久化 `last_error`（小文件）？本文默认不持久化（刷新丢错误，靠日志/诊断）。
- 中英文案由谁维护（RUI 仓库 vs 插头作者）？建议 RUI 提供通用文案，插头只给 title/body 双语。
- 是否为 inbound 设备链路加 TLS（EchoMuse 有；RUI 现在是明文 ws + token）？建议 0.2 不加，
  但 P3.5 安全项里评估。

---

## 6. 参考与核实清单

### 6.1 EchoMuse（MIT，main @ 2026-10-24）

| 编号 | 文件 / URL | 本文用到的内容 |
|---|---|---|
| EM-README | https://github.com/wilbowes/EchoMuse/blob/main/README.md | 项目定位、MIT、Docker/add-on、setup 步骤 |
| EM-QS | https://github.com/wilbowes/EchoMuse/blob/main/docs/quickstart.md | 首次 admin/setup token、pending→approve、向导、角色（L122-160） |
| EM-CONF | https://github.com/wilbowes/EchoMuse/blob/main/docs/configuration.md | 配置分区、仲裁窗口（L303-313）、在线/离线、TLS/端口/endpoints（L850-945） |
| EM-DEPLOY | https://github.com/wilbowes/EchoMuse/blob/main/controller/docker-compose.deploy.yml | host network、data 卷 |
| EM-ENV | https://github.com/wilbowes/EchoMuse/blob/main/controller/.env.example | 端口默认值、`DEVICE_APPROVAL`、`REQUIRE_DEVICE_TLS`、`EM_ESPHOME_PORT_BASE` |
| EM-RD | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_routes_devices.py | 路由（L43-55）、设备列表/详情、approve（L236-265）、config（L286-410）、delete（L171-210）、label 唯一（L557-571） |
| EM-RP | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_routes_provision.py | 向导文件接口（L38-49）、diagnostics（L242-277）、emOS image（L420-577） |
| EM-UI | https://github.com/wilbowes/EchoMuse/blob/main/controller/static/dashboard.jsx | 步骤表（L3828-3912）、模式校验（L3226-3270）、runStep（L8573-8710）、失败面板（L9218-9300）、IA（L10894-11490） |
| EM-CTRL | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_controller.py | 三平面协议（L7-58）、register/pending/ack（L5056-5260）、link auth（L4320-4410） |
| EM-LINK | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_linkauth.py | 四规则（L38-92）、follows_control（L104-125） |
| EM-PAIR | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_pairing.py | 配对请求/批准窗口（L1-88） |
| EM-CRED | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_credentials.py | 发 token/CA（L32-84） |
| EM-ARB | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_arbiter.py | 仲裁演化（L1-110）、pick（L129-148）、contest（L200-245） |
| EM-DB | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_db.py | 默认配置（L40-200）、schema（L390-1160）、端口分配（L2230-2380） |
| EM-PKI | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_pki.py | 全 fleet CA/leaf、SAN、有效期（L1-145） |
| EM-EP | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_endpoints.py | controllerEndpoints 语义（L1-139） |
| EM-SEC | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_config_sections.py | 分区/STATE_KEYS/FLEET_KEYS（L1-120） |
| EM-VIEW | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_device_view.py | DB+live 合并（L148-325） |
| EM-AUTH | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_routes_auth.py | users/roles/登录 |
| EM-API | https://github.com/wilbowes/EchoMuse/blob/main/controller/em_api.py | API/dashboard 组成 |

> 说明：GitHub API 在调研时被限流，文件用 `raw.githubusercontent.com` 直接取；目录结构用 GitHub
> HTML tree 读。行号以本次取到的 `main` 为准，上游更新后会漂移。

### 6.2 本机 EchoMuse 部署（只读，未修改）

| 路径 | 用途 |
|---|---|
| `01_Code/services/echomuse-controller/data/echomuse.db` | 实测 schema v31、表结构、`next_esphome_port=16002`、`device_approval=strict`、1 台已批准设备 |
| `01_Code/services/echomuse-controller/.env` | 端口/开关值（8767/8770/8768/16001、strict） |
| `01_Code/services/echomuse-controller/data/tls/` | `ca.*`/`server.*` 权限 0600 |
| `01_Code/services/echomuse-controller/docker-compose.deploy.yml` | host network / data 卷 |

### 6.3 RUI 仓库

| 编号 | 文件 | 本文用到的内容 |
|---|---|---|
| RUI-HANDOFF | `docs/HANDOFF.md` | 架构（L22-34）、端口/数据（L41-46）、决策 10/11/12/14/18（L57-72）、P2/P3（L105-116） |
| RUI-ROADMAP | `docs/ROADMAP.md` | P2.0-P2.4（L64-74）、P3.1-P3.4（L76-85）、in-process vs sidecar（L90-92）、不做多设备（L102-105） |
| RUI-OX | `docs/open-xiaoai-client.md` | LX06 client 安装、ws URL/token、常见故障（L7-49） |
| RUI-APP | `src/house_ear/web/api/app.py` | devices（L3011-3062）、wizard state（L3064-3210）、setup 端点（L3298-3428）、device admin（L3430-3589）、auth（L1737-1758）、probe（L182-207） |
| RUI-SETUPVUE | `web/src/views/SetupView.vue` | 现有向导 UI 与 5 步（L1-64） |
| RUI-DEVMGR | `web/src/components/DeviceManager.vue` | 现有设备管理 UI（L1-15） |
| RUI-CONTRACT | `src/house_ear/device/contract.py` | Capability/Transport/DeviceInfo（L23-59）、DeviceAdapter（L140-187） |
| RUI-REG | `src/house_ear/device/registry.py` | `_BUILDERS`、`register()`、`build_adapters()`（L20-40、L90-110） |
| RUI-SCHEMA | `src/house_ear/config/schema.py` | `DeviceConfig`（L120-147）、`Config.devices`（L578-610） |
| RUI-CLIENT | `src/house_ear/audio/client.py` | 单设备 `_pick_device()` / 一个平面（L364-418） |
| RUI-STORE | `src/house_ear/config/store.py` | config 原子写/0600/备份（L116-141） |
| RUI-SECRETS | `src/house_ear/secrets.py` | 写-only secret、0600（L1-15、L52-80） |
| RUI-DEPLOY | `docs/deploy.md` | secrets 0600（L121）、data 卷内容（L294） |
| RUI-REMAIN | `docs/remaining-work.md` | §I A/B 类（L89-102）、§J EchoMuse 参考（L104-114）、§K 插座/插头（L116-132） |
| DOT2-CONTRACT | `../rui-dot2-bridge/docs/plug-socket-contract.md` | 盲开 manifest、待确认五问（L37-82） |
| DOT2-ADR | `../rui-dot2-bridge/docs/adr-0001-adapter-as-controller.md` | 为什么用 adapter 顶替 controller（L1-55） |
| DOT2-README | `../rui-dot2-bridge/README.md` | 设备协议、TLS/controller.json、现状（L25-48） |

### 6.4 明确假设（未验证）

- EchoMuse 向导**刷新页面后从头开始**：由「状态只在 React state + DB 无 provisioning 表」推断，
  未在真机/浏览器实测。
- EchoMuse Docker 首次 setup token 的校验代码未逐行读，按 quickstart 文档描述记录。
- EchoMuse 的 OTA/firmware/emOS 构建只读了入口与路由，未深入每一个实现。
- RUI NAS 生产目录的权限未重新实测；按 `docs/deploy.md` 与 `save_config` 代码记录为 0600。
- RUI 现阶段「一个音频面只驱动第一台设备」由代码推断（`_pick_device` 返回第一个 adapter），
  未做第二台设备的真机实验；这与 HANDOFF §3.10「同一 4412 入口只能一个音箱客户端」一致。
- 本文的 manifest/API/JSON 示例是**设计建议**，不是已实现接口；P2.1/P2.2 评审后可改名。

---

## 7. 一句话结论

EchoMuse 值得 RUI 借的是**「证据式状态机 + 每步恢复 + 服务端诊断 + 设备生命周期纪律」**；
它的 USB 刷机、HA/ESPHome、每设备端口/CA、approve 队列和多设备仲裁都属于 RUI 的另一半问题，
0.2 不抄。RUI 用一份**声明的 manifest + 8 步骨架 + `config.yaml` 的 `params`** 就能在 1–2 天
做出可用的向导壳和设备管理最小版，然后由 P2.1 把 manifest 正式标准定下来。
