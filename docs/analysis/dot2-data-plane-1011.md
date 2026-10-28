# dot2 数据面 1011 关闭诊断（0.8.5 / 儿童房）

> 批次：0.8.5 攒批 §11-3 · 任务 D（纯诊断，**不改任何行为**）。
> 桥仓：`rui-dot2-bridge`（1.3.4，`76b8947`）——本文件只读引用，未改动。
> 取证：生产 NAS 持久化日志 `/vol1/rui/data/service.log`（**只读**：`grep`/`awk`/`docker logs`，未做任何写操作、未重启容器）。
> 日志时区：**UTC**（容器时区）；本地中国时间 = UTC+8。HANDOFF 里的时间多为本地时间，对照时注意差 8 h。

## 0. 结论摘要（TL;DR）

1. **现象确认**：2026-10-28 **11:14:51–14:08:13 UTC（本地 19:14–22:08）** 的 173.4 分钟里，
   儿童房 dot2 的 `/data` 被 `code=1011` 关闭 **36 次**（同窗口 dot2-b 3 次；另外 22:47 本地
   dot2-c/主卧 1 次）。每次关闭后 **5.1 s** 自动重拨并恢复，`link lost: dropped 0 speech
   chunks (0.0 s)` 逐次出现——**没有丢已排队的播放块**。
2. **“每约 305 s”是众数，不是周期**：36 次关闭的 **uptime（connect→close）全部落在 20 s 网格上（±0.2 s）**
   （100/200/300/400 s 为主，另有 180/240/640.1 s 各 1 次，1 次 300.1 s；**641 是 §8 命令按整秒截断的伪值，
   原始 11:21:20.979→11:32:01.033 = 640.05 s**）。相邻两次关闭的间隔因此是
   `105.1 / 205.1 / 305.1 / 405.1 …`，其中 **305 s 档（305.1×13 + 305.2×1）占 14/35**——
   所以肉眼看到的是“约 305 s 一次”。
   真正的“节拍”是 **20 s**。
3. **20 s 是桥侧 WS keepalive 的 ping 周期**：`device_link.py` 的 `serve(..., ping_interval=20.0,
   ping_timeout=40.0)`（`KEEPALIVE_PING_INTERVAL_S` / `KEEPALIVE_PING_TIMEOUT_S`）。
   关闭时刻 = 一次未获 pong 的 ping + 40 s 超时，故 uptime 必为 20 s 的整数倍。
4. **1011 不是 `_abandon(...)` 的 6 条路径打出来的**：那 6+1 个站点各有专属日志行与前置换条件，
   窗口内逐条比对，**没有任何一条与该 36 次关闭对齐**（见 §3）。实际链路是：
   `/data` 的 keepalive ping 在 40 s 内没等到 pong → websockets 以 **1011** 关连接 →
   `_serve_data` 的 `finally` → `_lose_data(abort=False)` 打出
   `data plane closed (...) code=1011 ...; data plane down`。
   设备端固件（EchoMuse v2.19.0-ea.1）**没有主动发 Close 帧的代码**——`device/`、`porting/` 的 Go 代码里
   `FormatCloseMessage`/`CloseMessage`/1011 均 0 命中（`go.sum`/二进制除外），所以该 1011 是
   **桥侧本地发出**的关闭。
5. **pong 为什么丢，本轮只能给假设**：桥侧没有记录 `sent`/`rcvd` 两个 close 帧，也没有设备端
   `[data]` 日志（生产 INFO 级，设备 `log` 帧只在 DEBUG 转发）。上游 EchoMuse 控制器
   `controller/em_wsclose.py` 的注释对这种失败模式有现成解释（“server pings every 20 s …
   a link saturated by a burst of response audio can lose the pong”），但本窗口 **34/36 次 tx=0B**
   （该连接从未写过 PCM），所以不能只归因于“播放把链路打满”，**设备侧读循环/写路径的 pong 延迟
   仍是待验证项**。验证方案见 §7。
6. **影响**：每次 **5.1 s** 上行麦克风空洞（约 **1.77%** 时间聋），0 次丢已排队播放/0 chunks；
   主要风险是这 5 s 内的唤醒词/指令被漏掉、`speaker_connected` 瞬时 false 导致的投递回落，
   **不是播放故障**。0.8.5 不改行为（见 §6）。
7. **反证提示**：22:12 本地 0.8.4 部署重启后，儿童房 data plane 稳定了 33 分钟且未再出现 1011，
   但该窗口 **rx=0B**（设备没上行麦克风帧），且 HANDOFF 0.8.4 D5 已记该设备 `token_missing` /
   `disconnected`——**不能据此宣称已恢复**，22:45 本地重启后的新窗口也还不足以判断。

## 1. 取证范围与只读命令

- 生产主机：`ssh yes999zc@<NAS-LAN-IP>`（BatchMode 只读登录）。
- 日志源：`/vol1/rui/data/service.log`（current，约 1.7 MB）+ `service.log.1`（约 2 MB 轮转），
  bind-mount 持久化，跨容器重启保留；**优于** `docker logs`（只覆盖当前容器）。
- 本轮窗口：`2026-10-28 11:10:00–14:09:00 UTC`（含首次 1011 的前后上下文）；
  另查了 14:45–15:05 UTC 的重启后窗口。
- 仅执行 `grep` / `awk` / `sed` / `docker logs` / `docker ps` / `ls` / `find`（列目录与读日志）；
  **未改 NAS 文件、未重启容器、未执行 docker exec、未改配置**。

## 2. 证据（原文脱敏）

### 2.1 一条完整事件（11:14:51 UTC）

```
2026-10-28 11:14:51,681 INFO websockets.server: connection closed
2026-10-28 11:14:51,681 WARNING rui_dot2_bridge.device_link: dot2 link: data plane closed (dot2) code=1011 dir=data rx=5597592B tx=356440B; data plane down
2026-10-28 11:14:53,072 INFO __main__: link lost: dropped 0 speech chunks (0.0 s) for room 儿童房
2026-10-28 11:14:56,743 INFO websockets.server: connection open
2026-10-28 11:14:56,743 INFO rui_dot2_bridge.device_link: dot2 link: data plane connected (dot2)
```

要点：`code=1011`、`dir=data`、`; data plane down`（**abort=False** 的形态）、
随后 `dropped 0 speech chunks`、**5.06 s** 后重连。

### 2.2 36 次儿童房 dot2 关闭全表（UTC；uptime/rx/tx 均来自日志原文）

> 表中 uptime / 间隔用日志的**毫秒时间戳**计算并保留 0.1 s；§8 的只读命令按**整秒截断**，
> 会显示 641 / 925 之类 ±1 s 的伪值（例：#3 实际 640.05 s，命令显示 641）。两套口径都落在
> 20 s 网格/「~N×100+5」模式上，判读时以本表为准。

| # | close (UTC) | uptime (s) | rx (B) | tx (B) | 距上次关闭 (s) |
|---|---|---|---|---|---|
| 1 | 11:14:51 | 180.0 | 5597592 | 356440 | — |
| 2 | 11:16:36 | 100.0 | 3116608 | 0 | 105.1 |
| 3 | 11:32:01 | 640.1 | 19894006 | 0 | 924.3 |
| 4 | 11:38:46 | 400.0 | 12358786 | 0 | 405.1 |
| 5 | 11:43:51 | 300.0 | 9278060 | 0 | 305.1 |
| 6 | 11:47:16 | 200.0 | 6197334 | 0 | 205.1 |
| 7 | 11:50:41 | 200.0 | 6187082 | 0 | 205.1 |
| 8 | 11:54:06 | 200.0 | 6187082 | 0 | 205.1 |
| 9 | 11:59:11 | 300.1 | 9298564 | 0 | 305.2 |
| 10 | 12:05:58 | 240.0 | 7463456 | 0 | 406.7 |
| 11 | 12:11:03 | 300.0 | 9354950 | 0 | 305.1 |
| 12 | 12:16:08 | 300.0 | 9329320 | 0 | 305.1 |
| 13 | 12:21:13 | 300.0 | 9308816 | 0 | 305.1 |
| 14 | 12:27:58 | 400.0 | 12369038 | 0 | 405.1 |
| 15 | 12:33:03 | 300.0 | 9272934 | 0 | 305.1 |
| 16 | 12:38:08 | 300.0 | 9411336 | 0 | 305.1 |
| 17 | 12:43:14 | 300.0 | 9313942 | 0 | 305.1 |
| 18 | 12:55:01 | 100.0 | 3106356 | 0 | 707.5 |
| 19 | 13:01:46 | 400.0 | 12594582 | 475253 | 405.1 |
| 20 | 13:05:11 | 200.0 | 6325484 | 0 | 205.1 |
| 21 | 13:06:56 | 100.0 | 3101230 | 0 | 105.1 |
| 22 | 13:10:22 | 200.0 | 6289602 | 0 | 205.1 |
| 23 | 13:12:07 | 100.0 | 3111482 | 0 | 105.1 |
| 24 | 13:17:12 | 300.0 | 9344698 | 0 | 305.1 |
| 25 | 13:20:37 | 200.0 | 6217838 | 0 | 205.1 |
| 26 | 13:24:02 | 200.0 | 6202460 | 0 | 205.1 |
| 27 | 13:27:27 | 200.0 | 6176830 | 0 | 205.1 |
| 28 | 13:32:32 | 300.0 | 9283186 | 0 | 305.1 |
| 29 | 13:37:37 | 300.0 | 9267808 | 0 | 305.1 |
| 30 | 13:44:22 | 400.0 | 12461306 | 0 | 405.1 |
| 31 | 13:46:07 | 100.0 | 3085852 | 0 | 105.1 |
| 32 | 13:51:13 | 300.0 | 9278060 | 0 | 305.1 |
| 33 | 13:56:18 | 300.0 | 9395958 | 0 | 305.1 |
| 34 | 13:59:43 | 200.0 | 6212712 | 0 | 205.1 |
| 35 | 14:04:48 | 300.0 | 9324194 | 0 | 305.1 |
| 36 | 14:08:13 | 200.0 | 6269098 | 0 | 205.1 |

同窗口其他设备（不计入上述统计）：

- dot2-b（次卧）3 次：`12:29:55 rx=740707B tx=0B`、`12:31:02 rx=902176B tx=0B`、`12:32:07 rx=971377B tx=0B`
  （间隔 66.3/65.6 s，与 dot2 的 100 s 网格不同，见 §4 的“低置信度”说明）。
- 重启后窗口 1 次：`14:47:50 dot2-c（主卧）rx=2470732B tx=442478B`，uptime 80.0 s，随后 5.1 s 重连。

### 2.3 统计

| 指标 | 值 |
|---|---|
| 窗口 | 11:14:51.681 → 14:08:13.429 UTC = 10 401.7 s（173.4 min） |
| dot2 关闭次数 | 36（平均 **288.9 s 一次**） |
| dot2 uptime 分布 | 300 s×13、200 s×10、100 s×5、400 s×4、180 s×1、240 s×1、640.1 s×1、300.1 s×1 |
| uptime 是否落在 20 s 网格 | **36/36**（偏差 ≤0.2 s；640.1 = 32×20 + 0.1） |
| 相邻关闭间隔（35 个，0.1 s 精度） | 105.1×4、205.1×10、305.1×13、305.2×1、405.1×4、406.7×1、707.5×1、924.3×1；中位数 **305.1 s** |
| tx=0B 的次数 | 34/36（tx>0 的 2 次是连接生命周期内曾有播放，不代表关闭瞬间在播） |
| 关闭→重连时长 | **36 次全部 5.1 s**（精确到 0.1 s） |
| `link lost: dropped …` | 每次 1011 后 1–2 s 内都出现，全部 **dropped 0 speech chunks (0.0 s)** |
| 上行速率（rx/uptime） | ≈30.9 KB/s ≈ 16 kHz mono S16（mic 流真实在走） |
| 估算“聋”的时间占比 | 36×5.1 s / 10 401.7 s ≈ **1.77%** |
| 同窗口 `_abandon` 族日志 | 10 条（全部是 dot2-b 的 write-stalled/superseded 或 12:01:48 dot2 的一次 write-stalled），**无一条与 36 次 1011 对齐** |

> 对照：窗口内**唯一一次非零丢块**是 `12:01:49` 的 `link lost: dropped 17 speech chunks (1.4 s) for room 儿童房`，
> 紧跟在 `12:01:48` 的 `/data write stalled > 1.00 s ...; aborting /data so the device reconnects` 之后——
> 那是 `_abandon` 写超时路径（会丢已排队播放），**不是** 1011 事件。这条对照正好说明：
> 36 次 1011 的代价是 5 s 的“上游空洞”，而写超时路径的代价才是“丢队列里的 1.4 s 语音”。
>
> 另：`#3` 的 **924.3 s** 长间隔里还夹着一次**非 1011 的关闭**——`11:21:15.941` 的
> `data plane closed (dot2) code=1006 dir=data rx=8227230B tx=1901013B; data plane down`，
> 随后 `11:21:20.979` 重连（5.0 s）。所以「924.3 s」是两个 1011 之间的墙钟差，不是一次
> keepalive 超时能持续 924 s；统计“节拍”时只看 uptime 列。

### 2.4 与部署/重启的交叉

- `12:53:0x UTC`（本地 20:53）0.8.3 部署重启；重启后 12:53:21 data plane 连接，12:55:01 又按
  `100.0 s` uptime 关闭——**重启没有改变节拍**。
- `14:12:3x UTC`（本地 22:12）0.8.4 部署重启；之后 14:12:51 的 data plane 到 14:45:35 才随容器重启关闭，
  **33 分钟没有 1011**。但该连接 `late data close (dot2) code=1000 dir=data rx=0B tx=471156B`：
  `rx=0B`（该连接没有收到任何上行麦克风帧）而 `tx=471156B`（桥侧向它写过播放）——设备在**收**、
  不在**发**；HANDOFF 0.8.4 D5 同时记录该设备 `token_missing` / `disconnected`。**因此“1011 停止”
  不能当作修复证据**——更可能是设备当时没在正常上行（数据面没有可被 keepalive 判死的流量/读循环）。本地 22:45 重启后的当前窗口（14:45–15:05 UTC）
  dot2 仍未出现 1011，同样不足以定论。
- 桥版本交叉：窗口前半段 write-stall 日志阈值是 `0.75 s`，`12:01:48` 起变成 `1.00 s`——
  与桥 1.3.4（20:01 本地部署）一致，说明 1011 现象**跨 1.3.3/1.3.4 都存在**，不是某个 bridge 版本独有的回归。

## 3. 代码路径：这 1011 是谁打的？

### 3.1 日志形态先分类

`device_link.py` 里关闭 /data 的出口只有两类：

1. **`_lose_data(...)`**（`_serve_data` 的 `finally` 或写路径调用）。日志模板
   `dot2 link: <reason> (<device>) code=<code> dir=data rx=<n>B tx=<n>B; <abort ? "aborting /data so the device reconnects" : "data plane down">`。
   - `abort=False`（观察到的形态）：**没有主动 abort**，只是记录 close 帧里的 code 并让设备重拨。
   - `abort=True`：日志一定是 `; aborting /data so the device reconnects`，并由 `_abandon(..., 1011, "write stalled")`。
2. **`_abandon(conn, 1011, ...)` 的显式调用点**（共 7 处，任务卡列的 6 条 + 写超时 1 条）。

36 次事件的日志全部是 **`; data plane down`**，即 `_lose_data(abort=False)`，code 来自
`_close_code(closed, conn)`——也就是 **WebSocket close 帧**。

### 3.2 7 个 `_abandon(…, 1011, …)` 站点逐条对照

| 行（1.3.4） | 函数 | 触发前置条件 | 它会打的日志 | 窗口内是否出现/对齐 |
|---|---|---|---|---|
| 525 | `_force_clean_cycle` | `_control is None` 且存在孤儿 `/data`（register 未到/握手失败/坏 register） | `dropped the orphaned data plane: <reason>` | 否 |
| 548 | `_adopt_control` | 已有旧 `/control` 又被新 register 顶替 | `retired the superseded control plane (<id>)` | 仅 12:53:05 dot2-b（部署重启），与 1011 不对齐 |
| 566 | `_adopt_data` | 已有旧 `/data` 又被新 `/data` 顶替 | `retired the superseded data plane (<id>)` | 仅 12:28:55 / 12:33:06 dot2-b，不对齐 |
| 584 | `_teardown_control` | `/control` 掉线后清理其孤儿 `/data` | 先有 `control closed (...) code=...` 再有本函数 | 否（逐条核对该 36 条的紧邻上文，未出现 control closed） |
| 604 | `_lose_control` | `/control` 写阻塞 `> 3.0 s` | `... dropping the link` | 否 |
| 743 | `_register_control` | `/control` 首帧 10 s 未到 | `register did not arrive ... dropping the link ...` | 否 |
| 1134 | `_write_data_frame` | `/data` 写阻塞 `> 1.0 s` 或 outbound watermark `> 256 KiB` | `... aborting /data so the device reconnects` | 仅 12:01:48 dot2 一次（`rx=271678B tx=200753B`）；同窗口其余 9 条 abandon 族日志均为 dot2-b 的 write-stalled/superseded，不动 dot2 的 1011 |

结论：**36 次 1011 没有任何一次命中上述分支**。

### 3.3 真正的 1011 出口：websockets keepalive

- `device_link.py` 起服务端时显式设置：
  `serve(..., ping_interval=KEEPALIVE_PING_INTERVAL_S(20.0), ping_timeout=KEEPALIVE_PING_TIMEOUT_S(40.0), ...)`
  （TLS 与明文两个 `serve` 各一处）。
- websockets 在 keepalive ping 未于 `ping_timeout` 内收到 pong 时，会以 **1011（internal error，
  reason `keepalive ping timeout`）主动关闭**本地连接；该 close 帧的 code 经
  `_close_code()` 打印出来就是 `code=1011`。若设备回了 close 回显，`rcvd` 也是 1011，日志无法区分
  “我们发的 / 收到的”——这正是本轮不能进一步定死“pong 丢在哪一段”的原因。
- **设备固件不会发 1011**：EchoMuse `v2.19.0-ea.1`（含 `patches/0001-ref-uplink` 同基线）的
  **`device/`、`porting/` 的 Go 代码** grep：`FormatCloseMessage` / `CloseMessage` / `1011`
  均为 0 命中（**`go.sum`、二进制、依赖代码除外**——`go.sum` 里存在 “1011” 子串是哈希/版本号，
  不是关闭逻辑）；设备侧错误路径是 `conn.Close()`（无 close 帧）→ 桥会记 `1006`，与观察到的
  1011 不符。
- 上游同族实现自己的注释就是这种失败模式：
  `EchoMuse/controller/em_wsclose.py` 文件头写 “The case this was written for is
  `sent 1011 keepalive ping timeout`: the server pings every 20s and closes if no pong arrives
  within 10s … so a link saturated by a burst of response audio can lose the pong and take the
  connection with it.”（上游 ping_timeout=10；本项目 1.3.4 是 40，所以关闭时点=ping+40 s）。
- **库级核对（本地桥仓 dev 环境，`websockets 17.3`；`pyproject.toml` 约束 `websockets>=13`）**：
  `websockets/asyncio/connection.py` 的 `keepalive()` 实现是
  `await asyncio.sleep(ping_interval - latency)` → `ping()` → `timeout(ping_timeout)` 等 pong；
  超时时执行 `protocol.fail(CloseCode.INTERNAL_ERROR, "keepalive ping timeout")`。
  两个关键细节与观测吻合：
  1. **`sleep(ping_interval - latency)` 会补偿 RTT**，所以 ping 时刻钉在连接建立后的
     20/40/60… s 网格上、不随 RTT 漂移 → 关闭时刻（ping+40 s）必然是 **20 s 的整数倍**；
  2. 超时路径发出的 code 是 **1011**（INTERNAL_ERROR），reason 是 `keepalive ping timeout`。
  另：该路径的 `AssertionError` 会在 `connection_lost()` 取消 keepalive 任务时被吞掉，
  所以日志里**不会**出现 `keepalive ping failed` 的 ERROR 行——生产 `service.log` 里该字符串
  0 命中，与正常超时路径一致（不构成反证）。生产容器内没有执行 `docker exec` 查版本，
  版本以桥仓 dev 环境 + 依赖约束为据；`uptime` 网格证据本身不依赖具体版本。

**20 s 整数倍的 uptime 就是 ping 网格的指纹**：连接建立后第 20/40/60… s 各一次 ping，
某次 pong 没在 40 s 内回来 → 在该 ping 时刻 +40 s 关闭，所以 uptime 必为 20 的倍数；
`100 s` 档 = 第 60 s 的 ping 失败，`200/300/400` 分别对应第 160/260/360 s 的 ping 失败。
`305 s` 只是“300 s uptime + 5.1 s 重拨”后的相邻间隔。

## 4. 根因假设与排除

| 假设 | 判定 | 依据 |
|---|---|---|
| H1 **桥侧 WS keepalive 超时（1011 由 bridge 本地发出）** | **主假设** | uptime 36/36 为 20 s 整数倍；关闭行是 `data plane down`（非 abort 形态）；设备固件无 Close 帧代码；上游 `em_wsclose.py` 同族实现有同一失败模式的注释 |
| H2 设备主动发 Close(1011) | **排除** | EchoMuse 设备源码无任何 1011/Close 帧；设备错误路径是 `conn.Close()`（应为 1006） |
| H3 `_abandon` 的 6 条路径 | **排除** | 7 个站点的前置条件与专属日志逐条比对（§3.2），窗口内 10 条 abandon 族日志均不对齐 |
| H4 写超时 / 发送水位（`aborting /data`） | **排除** | 该形态日志会写 `aborting /data so the device reconnects`，36 次全部是 `data plane down`；12:01:48 那次写超时是独立事件且未伴随 1011 |
| H5 播放把链路打满导致丢 pong（上游注释的解释） | **不能解释多数样本** | 34/36 次 tx=0B（该连接从未写过 PCM）；故“播放饱和”最多解释少数 |
| H6 设备侧读循环/写路径阻塞导致 pong 迟到 | **待验证（最可能的二级原因）** | 固件 `device/internal/client/data.go` 的 `/data` 读循环是唯一处理 control 帧（含回 pong）的路径；`PumpPeriod`（扬声器）与 mic 写都在同一连接上，读循环被阻塞时 pong 必然迟到。生产 INFO 看不到设备 `[data]` 日志，需下一轮插桩/设备侧日志确认 |
| H7 2.4G/Wi-Fi 抖动（重传导致 pong 晚到） | **待验证** | 同窗口 dot2-b 有 write-stalled（2.4G 抖动已知），但 dot2 的 1011 没有伴随 write-stalled；需 RSSI/重传数据 |
| H8 掉电/重启/设备离线 | **排除** | 每次精确 5.1 s 重拨、control 面大多数时间保持；无 register 失败日志 |

**低置信度样本**：dot2-b 的 3 次（12:29–12:32）间隔 66/65 s、uptime 不可靠（当时该设备正经历
write-stalled/superseded 的混乱窗口），不能用来推 keepalive 网格；dot2-c 的 1 次 uptime 80 s
（4×20）符合网格，但样本不足以单独下结论。

## 5. 对可用性的影响评估

- **播放**：36 次里 34 次 `tx=0B`，没有“正在播报时被 1011 打断”的证据；`link lost` 均报
  `dropped 0 speech chunks (0.0 s)`。仅 2 次 tx>0 是连接生命周期内曾有播放（不是关闭瞬间在播）。
  下一句播放会自动走新连接，**不会重放半句**（`_notify_link_lost` → adapter 丢弃缓冲）。
- **上行（真正代价）**：每次关闭到设备重拨 **5.1 s** mic 空洞。按窗口 36 次、10 401.7 s 计，
  约 **1.77% 时间听不见**；换算 **≈12.5 次/小时**，一天约 **0.43 h 的累计聋区**。落在唤醒词/指令上的
  概率虽低，但对“低频但确实发生”的漏唤醒投诉，这是一条可复核的候选解释。
- **健康/路由**：`_lose_data` 会 `connected.clear()`，这 5 s 内 `/health` 的 `speaker_connected`
  为 false；若提醒/播放落到该房间，可能触发“离线回落”路径（是否命中取决于投递时的瞬时状态）。
- **控制面**：窗口内 `/control` 大多数时间保持（12:00–12:01 有两次设备侧重拨，另 0.8.3/0.8.4
  部署重启），不是“整链路掉线”；1011 只杀 `/data`。

## 6. 为什么 0.8.5 不改行为

1. 1011 是**桥仓**（`rui-dot2-bridge` 1.3.4）的 websockets keepalive 行为，**0.8.5 的写范围明确
   不含桥仓**；改 ping 参数/关闭码不是本批可做的事。
2. 现有证据表明它不是 rui core 的缺陷：rui 只是被动收到 `/data` 关闭并如实记录；`dropped 0 chunks`
   与 5 s 重拨说明 rui 侧已按设计降级。
3. 唯一能“立刻改”的候选（把 `ping_timeout` 调大/关掉 keepalive）会**牺牲半开连接检测**——那正是
   0.8.4 前 887 s 僵尸连接的根因（`device_link.py` 注释与 HANDOFF P0b）。没有 A/B 证据前不动。
4. 本批性质是“诊断 + 固定观察口径”；行为修复留给 0.8.6/桥侧（见 §7），避免把发布门禁变成
   未验证的行为变更。

## 7. 下一步（0.8.6 / 桥仓验证方案）

按成本从低到高，**不改生产行为**的只读/插桩优先：

1. **补齐 sent/rcvd 区分（桥侧，日志级）**：`_lose_data` / `_drop_control` 的关闭行增加
   `sent=<code> rcvd=<code> reason=<...>`（从 `ConnectionClosed.sent/rcvd` 取）；这样下一轮日志
   能直接证明 1011 是“我们发的 keepalive timeout”还是“设备回的”。这是最小、零行为变化的改动。
2. **打开 websockets 的 DEBUG（仅一次真机窗口）**：库在 DEBUG 下会打 `sent keepalive ping` /
   `received keepalive pong` / `timed out waiting for keepalive pong`；与第 1 项同时开即可闭环
   “pong 是没来还是来晚了”。
3. **设备侧日志**：固件 `[data]` 行（`keepalive ping failed`、`PumpPeriod error`）当前生产 INFO 看不到；
   要么临时把设备日志级别/转发调高一次，要么 adb 抓一次，确认 pong 迟到是读循环阻塞还是 Wi-Fi。
4. **A/B（确认根因）**：桥侧临时把 `ping_timeout` 40→10 或临时关闭 `/data` keepalive（`None`），
   观察 1011 是否变为/消失；**必须限定一次窗口并记录副作用**（半开检测回退到 watchdog 45 s）。
   如确认是设备 pong 迟到，长期修法是“`/data` 不用 WS keepalive 判活，改用业务帧/写超时 + 桥侧
   watchdog”（现有 `HALF_OPEN_TIMEOUT_S=45` 已能覆盖 887 s 僵尸场景）。
5. **观察面固化**：把 §8 的只读统计命令纳入 0.8.6 的验收脚本（只读取 service.log，不新增服务）。

## 8. 可复现的只读统计命令

在内网任一台能 SSH 到 NAS 的机器上（**只读**）：

```bash
ssh yes999zc@<NAS-LAN-IP> \
  "grep -E 'code=1011|data plane connected' /vol1/rui/data/service.log" \
| sed -E 's/192\.168\.[0-9]+\.[0-9]+/<NAS-LAN-IP>/g' \
| perl -ne '
    sub sec { my @a = split /[ :]/, $_[0]; return $a[1]*3600 + $a[2]*60 + $a[3] }
    if (/^(\S+ \S+),\d+.*data plane connected \((\S+)\)/) { $conn{$2} = $1; next }
    if (/^(\S+ \S+),\d+.*data plane closed \((\S+)\) code=1011 dir=data rx=(\d+)B tx=(\d+)B/
        && exists $conn{$2}) {
      my $up  = sec($1) - sec($conn{$2});
      my $gap = defined $last ? sprintf("%.1f", sec($1) - $last) : "-";
      printf "%s  dev=%-7s uptime=%6.1fs rx=%9dB tx=%7dB gap=%s\n",
             $1, $2, $up, $3, $4, $gap;
      $last = sec($1);
    }'
```

输出即 §2.2 的表：`uptime` 是本次 connect→close 的秒数（判 20 s 整数倍），`gap` 是相邻两次关闭的
间隔（判 105/205/305/405 的模式），`rx/tx` 直接来自日志。三个判据一次跑完；命令对 NAS 只做 `grep`。

## 9. 本轮没有取得 / 不能下的结论

- **没有设备端日志**：生产桥以 INFO 运行，设备的 `log` 帧只在 DEBUG 转发；`[device ...]` 在
  `service.log` 中 0 行。因此“pong 为什么没回来”（读循环阻塞 / Wi-Fi 重传 / 设备忙）**无法定死**，
  §4 的 H6/H7 仍是待验证假设。
- **没有 sent/rcvd 区分**：`code=1011` 无法从现有日志区分“桥发出的 keepalive 关闭”与
  “设备回显的 close”，只能靠设备固件无 Close 帧这一旁证 + 20 s 网格推断是前者。
- **没有网络层数据**：RSSI / 重传 / 信道没有采集；“2.4G 抖动”只能引用 dot2-b 的历史 write-stalled，
  不能直接套到 dot2。
- **不能断言已恢复**：22:12 本地重启后 1011 消失，但同窗口 `rx=0B` + HANDOFF 的 `token_missing`
  说明设备那时根本没在正常上行；22:45 重启后的新窗口（约 20 min）尚无 1011，也不足以证明节拍消失。
- **没有改任何东西**：本文件是诊断，0.8.5 不含行为改动；桥仓、`.github/`、`src/` 均未触碰。

> 补充：桥仓后续（**未发版**）已加“关闭计数日志”，见 §10；§0–§9 的结论全部保留、口径不变。

## 10. 0.8.5 桥侧计数日志（已加，**未发版**）

§2 的 36 次样本是靠事后脚本（§8）从全文日志重算 uptime/gap 的；桥侧原日志只有单行
`data plane closed (...) code=1011 ... rx=..B tx=..B`，**没有“第几次关闭 / 距上次多久 / 累计丢帧”**，
线上排查必须先下载日志再 awk。D4 在桥仓 main 补了这几个只读计数（**纯诊断，零行为变化**）。

- 桥仓提交：`516c0e8`（`feat(link): count data-plane closes in the close log`，日志/统计）+
  `b6deb86`（`fix(check): use the renamed rui package in the config probe`，0.2.0 包重命名后
  遗留的 `house_ear.config` → `rui.config` 一行修复，与日志无关）。
- 版本状态：**未发版、未打 tag**；`pyproject.toml`/`CHANGELOG.md` 仍是 **1.3.4**，
  `rui_compat`/manifest 未动。生产 NAS 当前仍是 1.3.4——**线上日志暂时看不到新字段**，
  要等下一次桥侧发版部署后才生效。
- 改动位置：`src/rui_dot2_bridge/device_link.py` 的 `LinkStats` 与 `_lose_data()`；
  新增单测 `tests/test_link.py::AbnormalCloseResetTest::test_data_plane_close_counters_are_logged`。
- **没有改**：keepalive 参数（`ping_interval=20.0` / `ping_timeout=40.0`）、任何 abort/丢弃/重连
  判定、关闭码；也**没有**新增第二条同义日志，仍是原来那一条行（`quiet` 时 INFO，否则 WARNING），
  只在行尾追加字段。

新格式（数值沿用 §2.1 同一条事件，便于对照）：

```
dot2 link: data plane closed (dot2) code=1011 dir=data rx=5597592B tx=356440B close#7 interval=305.1s dropped=0; data plane down
```

| 字段 | 含义 | 判读 |
|---|---|---|
| `close#N` | 本进程启动以来第 N 次“**仍持有 `/data` 槽位**”的关闭（含 1011/1000/1006 等） | 进程重启后从 1 重计；被新连接顶替的旧 socket 的 `late data close` **不计**，避免重拨竞争把节律算乱 |
| `interval` | 距上一次计数关闭的 monotonic 间隔（0.1 s 精度）；首次关闭没有前序，打 `n/a` | 对 keepalive 1011 而言 = **5.1 s 重拨 + 本次 uptime**，与 §2.2「距上次关闭」同口径（305.1 s 档 = 300 s uptime + 5.1 s 重拨） |
| `rx=` / `tx=` | 本条连接收/发字节（**原有字段，未变**） | 与 §2.2 表同源；`LinkStats.data_close_rx_total` 为历次关闭 rx 之和（另提供，供进程内/健康面读取） |
| `dropped=` | 自上一次计数关闭以来，**桥侧 mic 队列满而丢掉的 80 ms 上行块数**（`LinkStats.dropped_audio` 的增量） | `0` = 这段期间没有因 rui 消费不及时丢上行块；`data_close_dropped_total` 为各期间累计。**注意**：它与 rui 侧 `link lost: dropped N speech chunks`（播放队列丢块）**不是同一个计数器**，两者不要混读 |

线上怎么读（NAS 只读，`grep` 即可，不必跑 §8 的 perl）：

```bash
# 每次关闭一行：次数 / 间隔 / rx / tx / 丢帧
grep 'data plane closed' /vol1/rui/data/service.log | tail -20

# 只看 1011 的节律：抽出 close#N 与 interval（305.1 s 档是否复现）
grep -E 'code=1011.*close#' /vol1/rui/data/service.log \
  | grep -oE 'close#[0-9]+ interval=[^ ]+' | tail -20

# interval 分布（看众数是否仍是 305.1 / 205.1 / 105.1…）
grep -E 'code=1011.*close#' /vol1/rui/data/service.log \
  | grep -oE 'interval=[^ ]+' | sort | uniq -c | sort -rn | head
```

判读规则：

- `interval` 若持续出现 **305.1 / 205.1 / 105.1 / 405.1 s**，即与 §2.2 的节律完全同构
  （= 20 s 网格上的某次 ping 失败 + 5.1 s 重拨），§3.3 的 keepalive 指纹直接被单行日志复现；
- `interval=n/a` 反复出现 → 进程在重启（`close#` 从 1 起算），**不是**新的节律；
- `close#` 最大的那条 = 本进程生命周期内的关闭总数，可直接替代 §8 脚本里的“36 次”统计；
- `dropped>0` 与关闭同现时，先确认是**桥侧 mic 队列丢块**（本字段）还是 **rui 播放队列丢块**
  （看相邻的 `link lost: dropped N speech chunks`），后者才对应 §2.3 的 12:01:49 例外。

与既有 36 次样本结论的关系：

- **不改** §0/§2 的任何结论：20 s 网格、305 s 众数、5.1 s 重拨、0 丢已排队播放、约 1.77% 上行
  空洞仍以 1.3.4 的只读日志为准；新字段只是把同一批事实**固化进单行**，让下一轮真机窗口不用再
  下载全文/跑脚本即可验证节律。
- **不能**回答“pong 为什么丢”：计数日志仍没有 `sent`/`rcvd` close code 区分（§7 第 1 项未做），
  也看不到 keepalive DEBUG。**口径保持**：1011 由桥侧 websockets keepalive（`keepalive ping
  timeout` → INTERNAL_ERROR=1011）发出，是**强推断**（20 s 网格 + 设备固件无 Close 帧 +
  库实现对照），**不是日志实证**；**pong 丢失（§4 H6/H7）仍未证明**。本改动不改变 §5 的影响
  评估（关闭仍 5.1 s 重拨、播放不重放、上行空洞仍在）。
- 验证：该单测用 `assertLogs` 断言 `close#1 interval=n/a`、`close#2 interval=<数字>s`、
  `close#N` 递增及 `data_close_rx_total` / `data_close_dropped_total` 累计；桥仓
  `./scripts/check.sh --nodevice` 全绿（183 tests / config probe / ruff / doctor 0 fail）。
