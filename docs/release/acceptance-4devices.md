# 5 平面真实布局（LX06 ×1 + Dot2 ×4）接入与验收 Runbook

> 文件名保留 `acceptance-4devices.md`（外部链接已存在），内容按 **2026-10-28 真实布局**重写：
>
> **客厅 LX06 + 儿童房 / 次卧 / 主卧 / 卫生间 四台 Dot2 = 5 个 plane**，正好用满
> `multi_device.max_planes: 5`；**第 6 台不分配 plane**（写入成功，但被拒绝启动）并写进
> `/health.multi_device.refused`（不排队）。
>
> 目标（`docs/release/v0.7.0.md:36` DoD #1）【文档声称】：**多台音箱同时在线，逐台跑完
> 添加 → 唤醒 → 应答 → 播放 → 停止 → 删除，互不串台**。
>
> 适用：rui ≥ 0.7.0（含 Phase 2 `PlaneFleet`）+ **rui-dot2-bridge 1.3.2**（C-001 每设备参数，
> 见 `rui-dot2-bridge/docs/contracts/C-001-per-device-params.md`）+ rui-xiaoai-bridge 0.2.0。
>
> 阅读约定：每条结论都带标记。
> **【代码证实】** = 我读过仓库里的实现，给了 `file:line`；
> **【文档声称】** = 只出现在文档/README 里，代码里没有对应实现或我没核到；
> **【未证实】** = 仓库里查不到，需要真机/adb/9 哥 现场确认。
> 本文只写事实与命令，不写宣传。**没有测到的数写 null + 原因，不补、不猜。**

---

## 0. 硬前提与一页结论

### 0.1 真实布局（唯一权威，执行前用 `ss` 复核）

| # | 房间 | config `id` | 设备 | `plugin_id` / `model_id` | `listen_port` | `params.control_port` | `params.loudness_file` | token 来源 |
|---|---|---|---|---|---|---|---|---|
| 1 | 客厅 | `living` | LX06 | `xiaoai-lx06` / `lx06` | **4412** | —（不是 dot2） | —（不是 dot2） | `RUI_DEVICE_TOKEN` |
| 2 | 儿童房 | `dot2` | Dot2（儿童房，**现役**） | `dot2-echomuse` / `dot2` | **8870** | **0** | `/var/lib/rui/loudness-dot2.json` | `DOT2_EM_TOKEN` |
| 3 | 次卧 | `dot2-b` | Dot2（次卧，**现役，ready+connected**） | `dot2-echomuse` / `dot2` | **8871** | **0** | `/var/lib/rui/loudness-dot2-b.json` | 同 #2（共享，§2.3） |
| 4 | 主卧（新） | `dot2-c` | Dot2（主卧，新，待插） | `dot2-echomuse` / `dot2` | **8873** | **0** | `/var/lib/rui/loudness-dot2-c.json` | 同 #2（共享，§2.3） |
| 5 | 卫生间（新） | `dot2-d` | Dot2（卫生间，新，待插） | `dot2-echomuse` / `dot2` | **8874** | **0** | `/var/lib/rui/loudness-dot2-d.json` | 同 #2（共享，§2.3） |

> **dot2-b 已经在线，不要再 POST**：2026-10-28 实测它在 8871 `ready+connected`；重新
> `POST /api/v2/devices` 只会得到 **`409 device_exists`**。现役设备只做一次 `PATCH` 补 params
> （§0.2 / §7.1）。**本轮真正新建的只有 `dot2-c` 和 `dot2-d` 两台**；`living`、`dot2`、`dot2-b`
> 都已在生产配置里。

> **8872 永远不分配给设备**：它是 dot2 响度控制面的历史端口（`rui-dot2-bridge/plug.json:75`；
> 生产 `.env` 的 `DOT2_CONTROL_PORT=8872` 也用它），5 台方案要求每台 `params.control_port: 0`
> （详见 §0.2），把 8872 留给“万一要开一台控制面”的场景。
>
> `loudness_file` 必须落在**容器内可写卷**上（生产 `./data:/var/lib/rui`，root fs 是 `read_only: true`）。
> 写只读层时启动不报错，但 `PUT /loudness` 学习到的修正存不下来
> （`rui-dot2-bridge/src/rui_dot2_bridge/dsp.py:548-552` 会建父目录，写不了就抛错；调用点 `control.py:105`）。

### 0.2 每台 Dot2 必做：`params.control_port: 0` + **唯一** `params.loudness_file`

**这一条是 4 台 Dot2 能不能同时起来的分水岭，不是可选优化。现役的 `dot2` / `dot2-b`
也要补一次（它们建的时候 params 是 null）；新建的 `dot2-c` / `dot2-d` 在 POST 时就带上。**

```jsonc
// POST /api/v2/devices（v2 才能写 params；v1 setup 端点会丢掉）——以新建 dot2-c 为例
{
  "id": "dot2-c", "room": "主卧",
  "plugin_id": "dot2-echomuse", "model_id": "dot2",
  "host": "<dot2-c-ip>", "listen_port": 8873,
  "params": {
    "control_port": 0,                                  // 必须：关闭本台控制面
    "loudness_file": "/var/lib/rui/loudness-dot2-c.json" // 必须：本台独占，且路径唯一
  }
}
```

**为什么 `control_port` 必须写 0**

- `DOT2_CONTROL_PORT` 是**一个全局 env**（历史/生产值 8872；C-001 解析后无 env 时默认 0=关闭），
  4 个 plane 子进程整份继承（`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:393-400`、
  `:736-752`；rui 侧 subprocess 未传 env，见 `src/rui/audio/client.py:418-431`）。
- C-001 的优先级是 **`devices[].params` > env > 默认**。不写 `params.control_port` 时，
  4 台都落回同一个 env 值：只要生产 `.env` 还是 8872，第 2 台起 bind 失败。
  bridge 1.3.2 的行为是 **warning + 关闭该台控制面、plane 继续启动**（C-001 §4）；
  **1.3.1 及以前会 `open()` 抛错 → 整个房间 plane 起不来**。
- 因此：**每台都写 0**。需要响度 HTTP 控制时，只给一台写一个唯一端口（例如 8872），
  其余保持 0；不要让 4 台都落回默认 8872。

**为什么 `loudness_file` 必须唯一**

- `DOT2_LOUDNESS_FILE` 是**一个全局文件**，4 台共用（`adapter.py:178-189`、`:388-392`：
  显式值 > env > 默认）。覆盖前，响度学习状态（用户调音量学到的修正）会跨房间互相污染：
  主卧的校准被卫生间一次 PUT 覆盖。
- C-001 给 `loudness_file` 的 manifest 默认是空串，解析后回落 `dev/rui/<device_id>/loudness.json`
  （按设备隔离）。**容器里 `dev/` 不在数据卷内且 root fs 只读**，所以生产要显式写到
  `/var/lib/rui/...`，并保证 4 条路径互不相同。
- 现网 compose 就设了全局 `DOT2_LOUDNESS_FILE: /var/lib/rui/loudness.json`
  （`rui-dot2-bridge/deploy/downstream/docker-compose.yml:45`）——不改的话 4 台共用这一个文件。
  给每台写 `params.loudness_file` 后，env 只对没写 params 的旧设备生效。

**写完必须回读证明（v2 只保存 manifest 声明过的 key）**

```bash
# 1) 已安装插件是否声明了这两个 key（bridge ≥ c64acb5 / 1.3.2 才有）
curl -sS "$BASE/api/v2/plugins/dot2-echomuse" -H "$H" \
  | jq '[.devices[0].params[] | select(.key=="control_port" or .key=="loudness_file")]'

# 2) 每台详情里的 params / unknown_params / warnings
curl -sS "$BASE/api/v2/devices/dot2-b" -H "$H" \
  | jq '{params, unknown_params, warnings: [.warnings[].code]}'
```

- 期望：两个 key 都在 `params` 里且**值就是请求值**（`control_port` 是 int `0`，不是 `"0"`/env 8872）；
  `unknown_params` 为空；`warnings` 里**没有** `unknown_params_ignored`。
- 若出现 `unknown_params_ignored`：装的插件清单早于 C-001，v2 **不会**保存这两个 key
  （`src/rui/web/api/v2_api.py:1767-1810`，未知 key 在 `:1793` 被丢弃）。先升级 bridge 插件清单，再重试。
- 列表行（`GET /api/v2/devices`）**只有 `plugin_id`、没有 `params`**，这是 C-002 §7.12 的形状；
  证明必须看详情或配置文件，不要因为列表没显示就以为没写进去。
- house 侧证据（新建）：`tests/test_real_layout_5devices.py::test_create_persists_per_device_params_and_hands_them_to_the_adapter`
  （写盘值 + adapter builder 收到的值）。**设备写入会重建 adapter**（`_sync_devices` →
  `build_adapters`，`src/rui/web/api/app.py:6049-6055`、`:6106-6108`），所以 params 立即生效、
  不需要为“参数没传下去”单独重启容器；adapter 侧 `params > env` 的证据在 bridge 的
  `test_per_device_params`（C-001 §6）。
- house 侧证据（**现役补齐**）：`tests/test_real_layout_5devices.py::test_patch_from_null_params_writes_both_and_hands_them_to_the_adapter`
  —— 建时只有 `plug_id` 的设备，PATCH 后文件与 builder 都拿到 `control_port=0` + 指定
  `loudness_file`；这正是 §7.1 对 `dot2`/`dot2-b` 的手工步骤。

### 0.3 其余硬前提（一页结论）

| 前提 | 值 / 判据 | 证据等级 |
|---|---|---|
| `multi_device.enabled` | 必须为 `true`；默认是 **`false`** | 【代码证实】`src/rui/config/schema.py` |
| 关掉 `multi_device` 的后果 | 只起 **一个** plane（第一台 enabled inbound），其余设备有配置但没人驱动，`/health.warnings` 出现 `multiple_enabled_inbound` | 【代码证实】`src/rui/web/api/app.py:2992-2993`、`:1774` |
| `multi_device.max_planes` | 默认 **5**（2026-10-28 由 4 上调）；本 runbook 的 5 个 plane **正好用满**。第 6 台**写入成功（201）但不起 plane**，`/health.multi_device.refused` 列出它的 id，`/health.warnings` 出现 `over_max_planes`——超限是明确告警，不是 4xx、也不排队 | 【代码证实】`src/rui/config/schema.py`、`src/rui/audio/fleet.py:129-135`、`src/rui/web/api/app.py:2253-2262`、`:3463-3490`；【自动化固定】`tests/test_real_layout_5devices.py::test_max_planes_five_accepts_the_fifth_and_names_the_sixth_refused` |
| 每台必须有 `devices[].room` | `multi_device.enabled=true` 时缺 room 直接 422 / 配置校验失败 | 【代码证实】`src/rui/config/schema.py:1144-1150`、`src/rui/web/api/v2_api.py:1947-1951` |
| 每台必须有**独立** `listen_port` | 两台 enabled inbound 用同一生效端口 → `422 port_conflict`（写盘前拒绝） | 【代码证实】`src/rui/web/api/device_rules.py:112-155`、`src/rui/web/api/device_write.py:258-269` |
| `listen_port` 不写会怎样 | 所有 Dot2 都回落到 manifest 的 `runtime.listen_port: 8870` → 第二台起必冲突 | 【代码证实】`src/rui/web/api/device_rules.py:54-69`、`rui-dot2-bridge/src/rui_dot2_bridge/devices/rs03qr.yaml:31` |
| `params.control_port` / `params.loudness_file` | **每台必写**：`control_port: 0`，`loudness_file` 唯一且可写（§0.2）。只有 manifest 声明过的 key 才会被 v2 落盘 | 【代码证实】`src/rui/web/api/v2_api.py:1767-1810`；C-001 §1/§2；【自动化固定】`tests/test_real_layout_5devices.py` |
| `control_port` 参与端口冲突吗 | **不参与**。核心只看 `listen_port`；`control_port` 是 adapter 自己的 loopback 控制面。`0` 会被原样保存为 `0`（不是“未设置”） | 【代码证实】`src/rui/web/api/device_rules.py:112-155`；【自动化固定】`tests/test_real_layout_5devices.py::test_control_port_zero_stays_zero_and_is_never_a_listener` |
| 每台独立 secrets 目录 | **默认不是**。一个 `DOT2_SECRETS_DIR` 被所有 plane 子进程继承 | 【代码证实】`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:950-956`、`house-ear/src/rui/audio/client.py:418-431`（subprocess 未传 env，整份继承） |
| 后台 API | 添加/测试/删除都在 **`/api/v2/devices`**；v1 的**添加**端点（`/api/v1/setup/device`）既不能指定 `listen_port`，也会丢掉 `params`，所以第 2–5 台只能用 v2 | 【代码证实】`src/rui/web/api/v2_api.py:2240/2297/2522`、`src/rui/web/api/app.py:5605/6106/6262` |

### 端口与身份分配表（= §0.1 的机器可读版，执行前用 `ss` 复核）

| # | 房间 | config `id` | `plugin_id` / `model_id` | `listen_port` | `params.control_port` | `params.loudness_file` | token 来源 |
|---|---|---|---|---|---|---|---|
| 1 | 客厅（现役） | `living` | `xiaoai-lx06` / `lx06` | **4412** | — | — | `RUI_DEVICE_TOKEN`（env/secrets） |
| 2 | 儿童房（现役） | `dot2` | `dot2-echomuse` / `dot2` | **8870** | 0 | `/var/lib/rui/loudness-dot2.json` | `DOT2_EM_TOKEN`（env 或 `dot2-secrets/device_token`） |
| 3 | 次卧（现役） | `dot2-b` | `dot2-echomuse` / `dot2` | **8871** | 0 | `/var/lib/rui/loudness-dot2-b.json` | 同 #2（共享）或 `DOT2_EM_TOKEN_B`（见 §2.3） |
| 4 | 主卧（新） | `dot2-c` | `dot2-echomuse` / `dot2` | **8873** | 0 | `/var/lib/rui/loudness-dot2-c.json` | 同 #2（共享）或 `DOT2_EM_TOKEN_C`（见 §2.3） |
| 5 | 卫生间（新） | `dot2-d` | `dot2-echomuse` / `dot2` | **8874** | 0 | `/var/lib/rui/loudness-dot2-d.json` | 同 #2（共享）或 `DOT2_EM_TOKEN_D`（见 §2.3） |

> 为什么跳过 8872：它是 dot2 响度控制面的历史默认端口（`rui-dot2-bridge/plug.json:75`）。
> 5 台方案要求每台 `params.control_port: 0`（§0.2）——这样即使生产还开着 `DOT2_CONTROL_PORT`，
> params 也压过 env，没有 plane 会去抢 8872。

---

## 1. rui 如何为每台设备创建 adapter 实例

### 1.1 每台一个 adapter 实例，key 是 `devices[].id`

**【代码证实】**

- 装配入口是 `build_adapters(cfg)`：遍历 `cfg.devices`，**跳过 disabled**，每台调一次插件
  builder，结果放进 `AdapterSet.adapters[dev.id]`
  —— `src/rui/device/registry.py:286-306`。
- builder 之前先做 manifest 运行时补齐（`listen_port` / `token_env`）
  —— `src/rui/device/registry.py:252-282`：`devices[].listen_port` 为 `None` 时用
  `runtime.listen_port`（Dot2 = 8870，LX06 = 4412）；`token_env` 仍是默认
  `RUI_DEVICE_TOKEN` 时才被 manifest 的 `runtime.token_env`（Dot2 = `DOT2_EM_TOKEN`）替换。
- Dot2 的 builder 是 `rui_dot2_bridge.adapter:build`
  （`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:907`）；LX06 是
  `rui_xiaoai_bridge.adapter:build`（`rui-xiaoai-bridge/src/rui_xiaoai_bridge/devices/lx06.yaml:57-58`）。
- 插件模型层的字段定义在 `src/rui/plugins/models.py:392-478`（`DeviceManifest`），
  `runtime.listen_port` 默认 4412、`runtime.token_env` 默认 `RUI_DEVICE_TOKEN`
  —— `src/rui/plugins/models.py:203-204`。

### 1.2 每台一个独立的音频面子进程（所以每台可以独立端口）

**【代码证实】**

- 多设备模式下启动 `PlaneFleet`：每台 enabled inbound 设备各一个
  `AudioPlaneSupervisor` —— `src/rui/audio/fleet.py:87-147`、`:197-220`。
- 子进程命令是 `python -m rui.audio.process --config <cfg> --device <id> --socket <sock>`
  —— `src/rui/audio/client.py:418-431`；端口**不在**父进程选，由子进程读自己的
  `devices[]` 条目决定 —— `src/rui/audio/fleet.py:12-16`、`src/rui/audio/process.py:3137-3160`。
- AF_UNIX socket 按设备命名：`audio-<id>.sock`（单设备仍是 `audio.sock`，两者不会互抢）
  —— `src/rui/audio/fleet.py:50-84`。
- LX06 + Dot2 混跑可行：两者都是 `transport: inbound`
  （`lx06.yaml:16`、`rs03qr.yaml:5`），都各占一个监听端口。
- 每台独立的还有：崩溃看门狗、重启计数、`/health` 行
  —— `src/rui/audio/fleet.py:19-32`、`src/rui/web/api/app.py:1704-1725`。

### 1.3 每台是否独立端口 / 独立 secrets 目录（直接回答）

| 维度 | 结论 | 证据 |
|---|---|---|
| 监听端口 | **必须独立**，`devices[].listen_port`；`None` 会回落到 manifest 默认，四台 Dot2 全部 8870 → 冲突 422 | 【代码证实】`src/rui/config/schema.py:269`、`src/rui/web/api/device_rules.py:54-69` |
| `params.control_port` | **每台显式 0**；不写会一起落回全局 `DOT2_CONTROL_PORT`（生产 8872，无 env 时 0） | 【代码证实】`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:1308-1309`、C-001 §2 |
| `params.loudness_file` | **每台唯一**；不写时先落回全局 `DOT2_LOUDNESS_FILE`，再回落按 device_id 的 `dev/rui/<id>/loudness.json`（容器里通常不可写） | 【代码证实】`adapter.py:1305-1310`、`:178-189`、C-001 §1 |
| 传输 token 名 | 默认**同一个** `DOT2_EM_TOKEN`（四台 Dot2 都一样），`devices[].token_env` 可在配置文件里逐台改 | 【代码证实】`src/rui/device/registry.py:268-282`、`adapter.py:942-947` |
| secrets 目录 | 默认**同一个**：优先级 `dev.secrets_dir`（`DeviceConfig` **没有这个字段**，永远 None）→ `params.secrets_dir` → `$DOT2_SECRETS_DIR` → `"secrets"`（相对进程 cwd） | 【代码证实】`adapter.py:950-956`、`src/rui/config/schema.py:254-290`（无 `secrets_dir`） |
| TLS 证书 | 从 `secrets_dir` 读 `server.pem` / `server.key`；`ca.pem` 给设备侧信任链 | 【代码证实】`device_link.py:255-266` |
| 每台设备的 `X-EM-Token` | 先读 `$<token_env>`，再读 `<secrets_dir>/device_token`；两者都没有 → **鉴权被关闭**（只打 warning） | 【代码证实】`adapter.py:958-980`（warning 在 `:974-979`） |

> 结论：**监听端口必须一台一个；`control_port` 每台 0、`loudness_file` 每台唯一（§0.2）；
> secrets 默认是一个目录共享给四台**。要做到“每台独立 token”，
> 只能走 §2.3 的两条路（逐台 `token_env` 或逐台 `params.secrets_dir`）。

---

## 2. dot2 桥的多设备能力：同时跑 4 台要改什么

### 2.1 单实例能力：一个 `DeviceLink` 只收一台设备

**【代码证实】**

- `DeviceLink` 的文档写死：“**One device at a time**, the same rule the firmware follows”
  —— `rui-dot2-bridge/src/rui_dot2_bridge/device_link.py:13-14`。
- 它在 `open()` 里 `await self._link.start()`，自己 `serve()` 监听 TLS 端口
  —— `adapter.py:530-549`、`device_link.py:198-226`（bind `0.0.0.0`
  —— `device_link.py:92`）。
- 因此 **4 台 Dot2 = 4 个 adapter 实例 = 4 个 plane 子进程 = 4 个 TLS 端口**。
  rui 侧不需要改代码，只需要 4 条 `devices[]`（加上 LX06 共 5 个 plane）。

### 2.2 端口与全局环境变量（最容易踩的一处）

C-001 之后 **`params` 压过 env**，所以下表“每台怎么做”一列都以 `params` 为准；
env 只作为没写 params 时的 fallback（单台旧部署继续有效）。

| 变量 | 作用 | 4 台时的要求 | 证据 |
|---|---|---|---|
| `devices[].listen_port` / `params.port` | EchoMuse wss 监听端口。优先级：`dev.listen_port` > `params.listen_port` > `params.port` > `$DOT2_LISTEN_PORT`/`$DOT2_TLS_PORT` > `plug.json transport.port` > 8870 | **每台显式写** 8870 / 8871 / 8873 / 8874 | 【代码证实】`adapter.py:932-941`、`rs03qr.yaml:31,37` |
| `params.control_port`（旧：`DOT2_CONTROL_PORT`） | 响度控制面（127.0.0.1） | **每台 `params.control_port: 0`**。不写 params 时四台都落回同一个 env（生产 8872 / 无 env 时 0）：1.3.2 会 warning + 关闭该台控制面（plane 还能起），1.3.1 及以前 `open()` 抛错 → 房间 plane 起不来 | 【代码证实】`adapter.py:1308-1309`、`:736-752`、C-001 §4 |
| `params.loudness_file`（旧：`DOT2_LOUDNESS_FILE`） | 响度学习状态文件 | **每台唯一**，落在可写卷。不写 params 且生产设了 `DOT2_LOUDNESS_FILE` 时，所有 plane 共用那一个全局文件，学到的校准跨房间互相覆盖；两者都没有时才回落到按 device_id 的 `dev/rui/<id>/loudness.json`（容器内通常不可写） | 【代码证实】`adapter.py:1305-1310`、`:178-189`、`:388-402`、C-001 §1 |
| `DOT2_SECRETS_DIR` | TLS + `device_token` 目录 | 四台共享一个目录（默认 `/opt/rui-secrets`） | 【代码证实】`adapter.py:950-956`、`deploy/downstream/docker-compose.yml:32,43` |
| `params.eq_dir`（旧：`DOT2_EQ_DIR`） | 只读 EQ 曲线目录 | 默认共享（C-001 明确：只读厂商输入不按设备拆） | 【代码证实】`adapter.py:1310-1312`、C-001 §1 |
| `DOT2_INJECT_PORT` | 开发注入面 | 保持未设置（同上，会抢端口） | 【代码证实】`adapter.py:1039`、`:538-541` |
| `DOT2_PLAYBACK_GATE_MS` | 播放期静音上行（默认 2500） | 全局；决定“播放中能不能被唤醒词打断”，见 §6.6 | 【代码证实】`adapter.py:296-300`、`README.md:159-169` |

> **生产核对（必做，别猜）**：现网 `.env` 里的 `DOT2_CONTROL_PORT` 即使还开着，也不再决定结果——
> 只要每台写了 `params.control_port: 0`（§0.2），params 就压过 env。仍建议清掉它，
> 避免以后新加设备时漏写 params 又落回 8872。

```bash
# 只看变量名，不打印值
docker compose exec -T rui sh -lc 'printenv | sed -n "s/^\(DOT2_[A-Z_]*\)=.*/\1/p" | sort'
# 期望：DOT2_SECRETS_DIR / DOT2_EQ_DIR / DOT2_LOUDNESS_FILE / DOT2_PLAYBACK_GATE_MS ...
# 不期望：DOT2_CONTROL_PORT / DOT2_INJECT_PORT
```

### 2.3 四台的 token 方案（选一个，写进配置再动手）

**【代码证实】** `adapter.py:958-980`：`token = $<token_env> || <secrets_dir>/device_token`。

- **方案 A（最少改动，默认）**：四台共用 `DOT2_EM_TOKEN` / 同一个 `device_token` 文件。
  前提是**四台设备本身持有同一个 token**。适合“四台都是从同一个 EchoMuse 控制器配对出来的”。
- **方案 B（逐台 token，推荐）**：配置里给每台写不同 `token_env`，`.env` 里各放一个变量。

```yaml
# config.yaml
devices:
  - {id: dot2,   ..., token_env: DOT2_EM_TOKEN}
  - {id: dot2-b, ..., token_env: DOT2_EM_TOKEN_B}
  - {id: dot2-c, ..., token_env: DOT2_EM_TOKEN_C}
  - {id: dot2-d, ..., token_env: DOT2_EM_TOKEN_D}
```

```dotenv
# .env（值来自设备侧配对结果，不要写进仓库/日志）
DOT2_EM_TOKEN=...
DOT2_EM_TOKEN_B=...
DOT2_EM_TOKEN_C=...
DOT2_EM_TOKEN_D=...
```

- **方案 C（逐台 secrets 子目录）**：`params.secrets_dir` 指向 `/opt/rui-secrets/b` 等。
  ⚠️ **v2 API 写不进这个键**：manifest 没声明 `secrets_dir`，`POST/PATCH /api/v2/devices`
  会把它当 `unknown_params_ignored` 丢掉（`src/rui/web/api/v2_api.py:1782-1808`）。
  可用的写法只有两种：直接改 `config.yaml` 的 `devices[].params.secrets_dir`，或
  `PUT /api/v1/devices/{id}` 带 `{"params":{"secrets_dir":"..."}}`
  （v1 的 `_merge_params` 对未声明但格式合法的 key 是**存进 params**，`src/rui/web/api/app.py:6096`）。
  生效证据是容器日志 `dot2 adapter: X-EM-Token source: file|env`（`adapter.py:980`）。

> **【未证实】** 两台新 Dot2（`dot2-c` / `dot2-d`）的 `X-EM-Token` 是否相同、`server.pem`/`ca.pem`
> 是否可复用同一套。（`dot2-b` 已现役，沿用现有 token/证书即可，不需要重新配对。）
> 这取决于设备侧配对过程，仓库里没有生成/配对脚本（`README.md:76` 只说“配对时生成”）。
> 需要 9 哥 用 adb 读 `controller.json` 与设备侧 token 确认（见 §3）。

### 2.4 Dot2 的 device_id / room / 证书归属

| 项 | 归属 | 证据 |
|---|---|---|
| rui 的 `devices[].id` | 配置侧，决定 plane、socket、日志、turn 归属 | 【代码证实】`src/rui/config/schema.py:255-290` |
| 设备自己上报的 `device_id`（如 `G090LF...`） | 设备侧 register 报文，只用于展示/日志 | 【代码证实】`device_link.py:309-335`（`DeviceInfoReport`） |
| `room` | 配置侧，同房间回声抑制与仲裁用 | 【代码证实】`src/rui/config/schema.py:1144`、`docs/multi-device-design.md:204-208` |
| 证书 / `X-EM-Token` | 设备侧 + 同一 `secrets_dir` | 【代码证实】`device_link.py:255-275` |

> 注意：rui 判“这台设备的 plane 通了”用的是 **plane 的 `device_id` = 配置 id**
> （`src/rui/audio/client.py:609-620` → `ready.device_id`），不是设备序列号
> （`src/rui/web/api/app.py:1669`、`:6203`）。所以配置 id 一旦改就必须删了重加。

---

## 3. 新 Dot2 的配对 / 入网流程（哪些证实、哪些没有）

### 3.1 【代码证实 + 文档声称】设备侧到底要什么

> 下表“设备侧要什么”里，**协议/端口/鉴权是代码证实**；`controller.json` 的确切路径与 adb 写法来自
> `rui-dot2-bridge` 的部署文档（【文档声称】，仓库里没有对应的自动化测试）。

| 要的东西 | 位置 | 证据 |
|---|---|---|
| `controller.json`：`endpoints` **有序回退**；本部署的 `tls_port` **必须等于该设备在 rui 的 `devices[].listen_port`** | `/data/local/etc/echomuse/controller.json` | 【设备端事实 2026-10-28】`docs/HANDOFF.md:51`；路径与 adb 写法 `rui-dot2-bridge/docs/deploy-honor-nas.md:63-80`、`rui-dot2-bridge/docs/agent-install.md:41` |
| TLS 材料 `ca.pem` / `server.pem` / `server.key` | 部署目录 `dot2-secrets/`，挂到容器 `/opt/rui-secrets`（只读） | `README.md:76`、`deploy/downstream/docker-compose.yml:32` |
| `X-EM-Token`（文件 `device_token`，0600） | 同上目录 | 【代码证实】`adapter.py:963-966` |
| 设备端 register（`/control`）与音频（`/data`）两条 wss | 同一端口（= `tls_port` = `devices[].listen_port`） | 【代码证实】`device_link.py:38-39`、`:277-291` |

**`controller.json` 的两个硬规则（设备端事实，2026-10-28 9 哥确认）**

1. **`endpoints` 是有序回退表**：设备按数组顺序尝试，`[0]` 是主地址，`[1]`、`[2]` … 只在
   前面的地址连不上时作为 fallback。**不要把 fallback 放在主地址前面**；本次每台设备的
   `[0]` 就是本宿主（NAS）的地址。`mdns:false` 表示不使用 mDNS，只走这份静态列表。
2. **`tls_port` 必须等于该设备在 rui 配置里的 `devices[].listen_port`**：plane 的
   TLS/WSS 监听口和 register/audio 是**同一个 TCP 口**；写错一个数字，设备会一直
   `connection refused`，而 rui 侧看到的是端口没人连。按 §0.1 的分配：
   `dot2`→8870、`dot2-b`→8871、`dot2-c`→8873、`dot2-d`→8874（`living`/LX06 不走这套流程）。

设备切换端口的命令（照 `rui-dot2-bridge/docs/deploy-honor-nas.md:78-80`，**每台只改自己的 `tls_port`**；
`dot2-c` 用 8873，`dot2-d` 用 8874）：

```bash
# 在能 adb 到该 Dot2 的机器上；<host> 用宿主局域网 IP；tls_port 必须 = 该设备的 listen_port
printf '%s' '{"endpoints":[{"host":"<host>","port":8867,"tls_port":8873}],"mdns":false}' > /tmp/controller.json
adb push /tmp/controller.json /data/local/tmp/controller.json
adb shell "su -c 'cp /data/local/tmp/controller.json /data/local/etc/echomuse/controller.json; chmod 644 /data/local/etc/echomuse/controller.json'"
adb shell "su -c 'kill -9 \$(pidof server)'"   # start_server.sh 会自动拉起并重读配置
```

> `port: 8867` 是明文 ws 端点，rui 默认不开；只填 `tls_port` 即可（`device_link.py:215-226`
> 只有 `plain_port is not None` 才监听）。别把 8867 当成必填。

### 3.2 【未证实】配对（pairing）本身

仓库里**没有**任何“从零配对一台新 EchoMuse 设备 / 生成新证书 / 生成新 token”的流程文档或脚本。
能查到的只有：

- `README.md:76`：「EchoMuse 的 TLS 材料（`ca.pem` / `server.pem` / `server.key`）+ `X-EM-Token`｜**配对时生成**」。
- `docs/agent-install.md:23`：安装前必须向用户确认这 4 个文件在哪。
- `docs/canary-0.6.0-official-image.md:152-154`：演练时用的是**一次性自签** `openssl req -x509 -days 1`，
  并明确说 `device_token` 缺失时 adapter 降级为「鉴权关闭」。

**本节结论：配对/证书签发流程 = 未证实，需 adb/设备侧确认。** 执行前必须先回答：

1. 两台新 Dot2 刷的是哪个 EchoMuse 版本？（manifest 要求 ≥ v2.19.0-ea.1，`README.md:73`；
   现网运行 `fw 20261024-1911-dev`，`docs/HANDOFF.md:12`）
2. 两台新设备的 `controller.json` 当前指向哪里？各自现有 `X-EM-Token` 是多少（**只看文件权限与存在性，
   不打印值**）？（`dot2-b` 已现役，只核对不重配）
3. 现有 `dot2-secrets/` 的证书能否被两台新设备接受（同一 CA）？还是每台一套？

```bash
# 仅确认存在性与权限，不读内容
docker compose exec -T rui sh -lc 'ls -l /opt/rui-secrets; stat -c "%a %n" /opt/rui-secrets/* 2>/dev/null'
# 设备侧（需 adb + root）
adb shell "su -c 'ls -l /data/local/etc/echomuse/; cat /data/local/etc/echomuse/controller.json'"
```

---

## 4. 后台（v2 控制台 / API）添加、测试、删除设备

### 4.1 认证与基础变量

**【代码证实】** 控制台 token 走 `X-Auth-Token` 头，常量时间比较 + 每 IP 失败预算
（`src/rui/web/api/app.py:3226-3247`；v2 复用同一实现并把错误包成 C-002 错误体
`src/rui/web/api/v2_api.py:941-957`）。

```bash
BASE="http://127.0.0.1:8080"
H="X-Auth-Token: $RUI_TOKEN"     # 占位；值从部署目录 .env / 控制台登录态取，不进日志
curl -sS "$BASE/api/v2/meta" -H "$H" | jq '.rui_version, .compat'
```

> **`adapter` 不是 create 的入参**：v2 的允许字段是
> `id / room / plugin_id / model_id / host / listen_port / wake_words / volume_target / enabled / params / audio`
> （`src/rui/web/api/v2_api.py:1863-1864`），`adapter` 由 manifest id 推导且创建后不可改
> （`v2_api.py:2259-2266`）。任务书里写的“id/adapter/room/host/params/transport”是 v1 时代的形状，
> **v2 用 `plugin_id` + `model_id`**。

### 4.2 添加新设备（只对 `dot2-c` / `dot2-d`）

> **现役 `living` / `dot2` / `dot2-b` 不要再 POST**：`dot2-b` 已在 8871 ready+connected，
> 再 POST 会 **409 `device_exists`**。它们只需要 §7.1 的 `PATCH` 补 params。

```bash
# 第 2 台起必须显式 listen_port；room 在 multi_device 开启后必填
# params.control_port / loudness_file 是硬要求（§0.2），缺一不可
curl -sS -X POST "$BASE/api/v2/devices" -H "$H" -H 'Content-Type: application/json' -d '{
  "id": "dot2-c",
  "room": "主卧",
  "plugin_id": "dot2-echomuse",
  "model_id": "dot2",
  "host": "<dot2-c-ip>",
  "listen_port": 8873,
  "wake_words": ["你好小瑞"],
  "volume_target": 60,
  "enabled": true,
  "params": {
    "control_port": 0,
    "loudness_file": "/var/lib/rui/loudness-dot2-c.json"
  }
}' | jq '{status: .device.status, ready: .device.ready, port: .device.listen_port,
         params: .device.params, unknown: .device.unknown_params,
         ignored: [.device.warnings[].code], restart: .restart}'
```

> `dot2-d`（卫生间）同样调用，只改 `id` / `room` / `host` / `listen_port`（8874）和
> `loudness_file`（`/var/lib/rui/loudness-dot2-d.json`）。**不要**复用同一个 `loudness_file`。
> 新增两台时 `restart.scope="fleet"`、`restart.auto=true`，已在线的 `living`/`dot2`/`dot2-b`
> 会短暂重连——这是预期，不是故障（§4.2 末尾）。

期望（【代码证实】`v2_api.py:2241-2256`、`1721-1734`、C-002 §7.11 `docs/contracts/C-002-console-v2-api.md:597-629`）：

- HTTP **201**，body `{"device": {...DeviceDetail...}, "restart": {...}}`；
- `restart.scope="fleet"`、`restart.auto=true`、`restart.required=true`、`restart.status="ok"`；
- `device.listen_port` = 你写的端口，`device.status` 在设备未连上时是 `not_connected`（不是错误）；
- `device.params.control_port` 回读为 **int 0**，`device.params.loudness_file` 就是你写的路径，
  `device.unknown_params == []`，`device.warnings` 里没有 `unknown_params_ignored`。

LX06（第 1 台，通常已在配置里；新加时）：

```bash
curl -sS -X POST "$BASE/api/v2/devices" -H "$H" -H 'Content-Type: application/json' -d '{
  "id": "lx06", "room": "客厅", "plugin_id": "xiaoai-lx06", "model_id": "lx06",
  "host": "<LX06-IP>", "listen_port": 4412, "wake_words": ["你好小瑞"],
  "volume_target": 60, "enabled": true
}' | jq '.device.listen_port, .device.status'
```

常见错误与含义：

| HTTP / code | 原因 | 证据 |
|---|---|---|
| `422 port_conflict` | 两台 enabled inbound 端口相同（漏写 `listen_port` 最常见） | `v2_api.py:408`、`device_rules.py:158-168` |
| `409 device_exists` | id 重复 | `v2_api.py:1916-1926` |
| `409 plug_unavailable` | 插件未安装/不可用（`data/plugs` 或镜像内） | `v2_api.py:1897-1902` |
| `422 invalid device fields`（`room required`） | `multi_device.enabled=true` 但 room 空 | `v2_api.py:1947-1951` |
| `device.warnings[].code="unknown_params_ignored"` | 写入了 manifest 未声明的 param。**对 `control_port`/`loudness_file` 出现这条 = 装的插件清单早于 bridge c64acb5（C-001）**，v2 不会保存，必须升级插件清单后重写 | `v2_api.py:1767-1810`（`:1793`）、`:1053-1061` |

> **每次添加/删除都会重建整个 fleet**（不是只动这一台）：
> `_restart_audio` 在多设备模式下 `fleet.stop()` → `_start_fleet()`
> —— `src/rui/web/api/app.py:3526-3534`、`:6002-6018`。所以加下一台时已在线的所有房间也会短暂掉线重连，
> 预期 ≤60 s（`README.md:180`，【文档声称】；现场请以实际重连时间为准）。逐台验收时把这一点写进记录，别当成故障。

### 4.3 单台测试（证据式，不播放、不写配置）

```bash
curl -sS -X POST "$BASE/api/v2/devices/dot2-c/test" -H "$H" | jq
```

【代码证实】返回形状见 `v2_api.py:2399-2516`：`{ok, ready, waited_ms, checked_at, transport,
checks[], errors[]}`，`checks[].id ∈ {plugin, link, token, capabilities}`，
`checks[].status ∈ {ok, fail, not_applicable}`，`checks[].code` 如 `connected` /
`device_not_connected` / `token_configured` / `token_missing` / `plug_resolved`。
inbound 设备会先等链路最多 **3 s**（`DEVICE_TEST_WAIT_S`，`app.py:1835`），`waited_ms` 会体现。

v1 等价端点（字段形状不同：`checks[].name/.ok/.status`）：

```bash
curl -sS -X POST "$BASE/api/v1/devices/dot2-c/test" -H "$H" | jq '{ok, transport, listen_port, checks}'
```

（`src/rui/web/api/app.py:6182-6255`）

> `token` 检查在 Dot2 上通常是 **通过但 `managed_by_adapter=true`**：rui 看不到
> `DOT2_EM_TOKEN`，只要链路活着就判通过（`v2_api.py:991-1011`）。
> **不要**为 Dot2 调 `POST /api/v2/devices/{id}/token/rotate`：env 提供 token 时返回
> `409 token_from_environment`，adapter 托管时返回 `409 token_managed_by_adapter`
> （`v2_api.py:2346-2397`、C-002 §7.19）。Dot2 的 token 在设备侧轮换。

### 4.4 删除（只删本次新增的 `dot2-c` / `dot2-d`）

```bash
curl -sS -X DELETE "$BASE/api/v2/devices/dot2-c?confirm=dot2-c" -H "$H" | jq
# → {"removed": true, "device_id": "dot2-c"}
```

> **现役 `living` / `dot2` / `dot2-b` 不要删**：本次验收只回滚新增的两台。若必须删除
> 现役设备，先确认它们不在 0.8.0 门禁的四台清单里，并单独记录。

【代码证实】`v2_api.py:2297-2317`：必须带 `?confirm=<device id>`，否则 `409 confirm_required`
并列出影响（fleet 重启、设备不会自动回来、删最后一台会停 plane）。删除后 fleet 重建，
其余房间自动重连。

v1 等价（无 confirm）：

```bash
curl -sS -X DELETE "$BASE/api/v1/devices/dot2-c" -H "$H" | jq
# → {"removed": "...", "devices": [...], "plane_error": ""}
```

### 4.5 v1 端点能力边界（为什么加设备要用 v2）

| 端点 | 能设 `listen_port` | 能设 `plugin_id/model_id` | 证据 |
|---|---|---|---|
| `POST /api/v2/devices` | ✅ | ✅ | `v2_api.py:1863-1864` |
| `POST /api/v1/setup/device` | ❌（payload 只合并 id/adapter/host/room/wake_words/enabled） | ❌（adapter 名，例如 `dot2`） | `app.py:5605-5663` |
| `PUT /api/v1/devices/{id}` | ✅ | ❌（`adapter` 可改，但 `plugin_id` 只能通过 `params.plug_id`） | `app.py:6106-6180` |
| `POST /api/v1/devices/{id}/enabled` | — | — | `app.py:5814-5854` |

---

## 5. 逐台验收：添加 → 唤醒 → 应答 → 播放 → 停止 → 删除

> 每台都按这 6 步跑一遍，**一台跑完再做下一台**（fleet 每次重建会牵连所有房间，
> 一次只动一台才能把“谁掉线”归因清楚）。第 2 台起先做 §0 的端口/env 复核。

### 5.1 步骤与判据

| # | 动作 | 命令 | 通过判据（证据字段） |
|---|---|---|---|
| 1 | 添加 | §4.2 的 POST | 201；`restart.status="ok"`；`ss -ltn` 出现该端口；`/health.audio_devices[]` 多一行且 `running=true` |
| 2 | 入网 | 设备侧 `controller.json` 指向该端口 → 重启设备 server | 容器日志出现 `dot2 link: <id> registered (fw ..., ip ..., caps ...)`（`device_link.py:335`）；`GET /api/v2/devices/{id}` → `connected=true`、`status="ready"` |
| 3 | 唤醒 | 9 哥 在该房间说「你好小瑞」 | 日志 `wake candidate <id>-<ns> admitted after N ms`（`process.py:794`）；`plane.wake_candidates` +1、`wake_admitted` +1 |
| 4 | 应答 | 说一句问题 | 日志 `turn {"turn_id":...,"device_id":"<id>","first_response_ms":...,"stages_ms":{...}}`（`trace.py:128-149`、`app.py:2653`）；`GET /api/v2/devices/{id}/activity` 出现 `id="turn:..."` 且 `matched_by="device_id"`（`v2_api.py:1417-1447`） |
| 5 | 播放 | `POST /api/v2/devices/{id}/actions` `audio.say`（或 `/api/v1/debug/say`） | `{"ok":true,"result":{"reply":"...","audio_bytes":>0},"device_status":{"ready":true}}`（`v2_api.py:2588-2635`）；声音只从该房间出 |
| 6 | 停止 | 播放中在该房间说「停」；或 `audio.stop` | 日志 `barge: N wake during playback, dropped X s queued`（`process.py:1758`）或 action 返回 `result.playing=null`；**播放立即停** |
| 7 | 删除 | §4.4 的 DELETE | `{"removed":true}`；`ss -ltn` 该端口消失；其余设备在 ≤60 s 内 `connected=true` 恢复 |

播放/停止的 HTTP 写法：

```bash
# 播放一段合成语音（走完整 TTS+播放链路，不需要人说话）
curl -sS -X POST "$BASE/api/v2/devices/dot2-b/actions" -H "$H" \
  -H 'Content-Type: application/json' \
  -d '{"action":"audio.say","args":{"text":"这是次卧音箱的验收语音，请听到后说停。"}}' | jq

# 停止
curl -sS -X POST "$BASE/api/v2/devices/dot2-b/actions" -H "$H" \
  -H 'Content-Type: application/json' -d '{"action":"audio.stop","args":{}}' | jq

# 等价 v1：文本回合（device 名房间；不给则走 default_output）
curl -sS -X POST "$BASE/api/v1/debug/say" -H "$H" \
  -H 'Content-Type: application/json' -d '{"text":"报一下现在几点","device":"dot2-b"}' | jq
```

【代码证实】action 目录：`audio.say{text 1..500}` / `audio.play{file,loop?,gain?}` /
`audio.stop{}` / `volume.read|set` / `led.set`（`v2_api.py:153-190`、`:315-339`）；
未连接返回 `409 device_not_connected`，能力缺失返回 `409 capability_unsupported`
（`v2_api.py:2552-2586`）。

### 5.2 串台（互不干扰）检查

| 检查 | 做法 | 证据 |
|---|---|---|
| 只从说话房间出 | 在次卧说唤醒词，客厅/儿童房正在播放的音乐不应停 | 播放房间的 plane **收不到任何消息**；次卧 `admitted` 且 `decided_by != "barge"`（`arbiter.py:384-436`）；`arbitration_cross_echo_suppressed` 不增加 |
| 同时命中只出一个 turn | 同一走廊 4 台同时听到同一唤醒词 | **恰好 1 个 `admitted`**，其余 `rejected: lost_same_word`（`arbiter.py:481-517`）；`/health.audio_devices[]` 各行 `wake_rejected` 有计数；控制面 `wake_admitted == 1` |
| 已有回合 + 同词（同房间） | 回合 owner 所在 room 又说同一唤醒词 | `rejected: turn_open`（`arbiter.py:_in_open_turn`）；**不产生第二个 admit** |
| 弱间隔（尾音） | 儿童房已开始应答，次卧 0.6 s 后才听到尾音 | `rejected: duplicate`（2 s 同词窗口，`arbiter.py:571-576`）；次卧 plane 不 arm；儿童房继续播 |
| 同房间交叉回声 | 同一 room 两台设备时，A 播放 B 误唤醒 | 期望 `rejected: cross_echo`（`docs/multi-device-design.md:204-208`；代码 `arbiter.py:412`）；本次 5 台是 5 个 room，此条属“同房组合另开一轮” |
| 每台归属 | 逐台看 activity | `GET /api/v2/devices/{id}/activity?type=turn`，事件 `matched_by="device_id"`（`v2_api.py:1429-1446`） |

### 5.3 记录表（复制到验收记录，没测到的写 null）

| 设备 id | 添加(201) | `params` 回读 OK（§0.2） | 入网(ready) | 唤醒(admitted) | 应答(turn 的 device_id) | 播放(audio_bytes) | 停止(barge) | 删除(removed) | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| living (lx06) | | n/a | | | | | | | |
| dot2（儿童房） | | | | | | | | | |
| dot2-b（次卧） | | | | | | | | | |
| dot2-c（主卧） | | | | | | | | | |
| dot2-d（卫生间） | | | | | | | | | |

---

## 6. 风险与前置

### 6.1 端口冲突（最高频失败）

- 不写 `listen_port` 时，四台 Dot2 的生效端口都是 8870
  （`device_rules.py:54-69` + `rs03qr.yaml:31`）→ 第 2 台 `422 port_conflict`。
- `params.control_port` **不参与**这条规则（核心只看 `listen_port`）；`0` 原样保存，
  不会变成“未设置”，也不会去 bind 任何端口（house 侧证据
  `tests/test_real_layout_5devices.py::test_control_port_zero_stays_zero_and_is_never_a_listener`）。
- 冲突只比对 **enabled + inbound** 设备；disabled 不参与，`null` 与显式默认值等价
  （`device_rules.py:118-131`）。
- 启动时不拒绝冲突配置，只在 `/health.warnings` 报 `port_conflict`
  （`app.py:1727-1750`）——所以“服务起来了”不等于“端口没冲突”，**必须看 warnings**。

```bash
ss -ltn | grep -E ':(4412|8870|8871|8872|8873|8874)\b'
curl -sS "$BASE/health" | jq '.warnings, .multi_device'
```

### 6.2 `DOT2_CONTROL_PORT` / `DOT2_INJECT_PORT` 全局抢占

见 §2.2 / §0.2。**每台写 `params.control_port: 0`** 就压过全局 env（C-001 §2）。

- bridge 1.3.2：`adapter.py:736-752` 控制面 bind 失败 → warning + 关闭该台控制面，
  **plane 继续启动**；4 台漏写 params 也不会再拖垮房间。
- bridge 1.3.1 及以前：`adapter.py:542-545` 的 `start_control_server` 重复 bind 抛 `OSError`，
  `open()` 失败 → 该 plane 起不来、看门狗反复重试。
- `DOT2_INJECT_PORT` 没有 per-device 参数（C-001 未覆盖），**必须保持未设置**。

### 6.3 secrets 权限与只读容器

- 期望：`dot2-secrets/` 目录 `700`，`ca.pem` / `server.pem` / `server.key` / `device_token` **0600**
  —— 【文档声称】`docs/agent-install.md:80`（代码只要求可读，不校验权限位）。
- 容器 `read_only: true`（`deploy/downstream/docker-compose.yml:46`），secrets 只能 bind mount，
  且当前是**只读挂载**（`:32`）——逐台子目录方案（§2.3 方案 C）要先在宿主建好再重启容器。
- 容器内 uid 是 `1000:1000`（compose `user`），宿主文件权限要让它可读。

```bash
docker compose exec -T rui sh -lc 'stat -c "%a %U:%G %n" /opt/rui-secrets /opt/rui-secrets/*'
```

### 6.4 同房间 vs 不同房间

- `multi_device.enabled=true` 时 room **必填**（`schema.py:1144-1150`）。
- 同一 room 的两台：A 播放时 B 的候选默认按 `cross_echo` 抑制
  （`docs/multi-device-design.md:204-208`）；跨房间不做这种抑制。
- **本次真实布局就是 5 个 plane 分属 5 个不同 room**（§0.1）：正常路径先跑通；
  “同一 room 两台”的组合本次没有设备，仍另开一轮。

### 6.5 同名唤醒词：`lost_same_word` 与“唤醒词不是按设备隔离的”

- 仲裁窗口 `multi_device.arbitration.candidate_merge_window_ms` 默认 **400 ms**
  （`schema.py:1048`）；同词重复窗口 `duplicate_window_ms` 默认 2000 ms（`:1050`）。
- 同一句话被多台听到 → 一个赢家、其余 `lost_same_word`（`arbiter.py:481-517`）；
  赢家按 `source_priority`（device_wake 优先）→ `room_priority` → 最近活跃 打分
  （`schema.py:1041-1050`）。`lost_same_word` **只表示同一 merge window 内同词竞争的 loser**，
  不是跨房间/回合中唤醒的期望值。
- **house 侧已固定的三个同走廊最不利用例**（`tests/test_wake_coordinator.py`）：
  `test_worst_case_turn_open_plus_same_word_is_one_speech`（1 admit + turn_open/lost_same_word）、
  `test_worst_case_a_playing_b_same_word_does_not_barge_another_room`（admit 但不夺 owner、播放房间零消息）、
  `test_worst_case_weak_gap_b_hears_only_the_tail`（尾音 → `duplicate`，只有 1 个 admit）。
- **同房间交叉回声的计数**：`cross_echo` 拒绝会累计到控制面
  `WakeCoordinator.metrics()["arbitration_cross_echo_suppressed"]`（每条 +1，`wake_rejected` 同步 +1），
  控制面日志写 `wake candidate <id> rejected: cross_echo (playing_device=<id>, suppressed_device=<id>, room=<room>)`。
  真机验收在 `/health.audio_devices[]` 的 `wake_rejected.cross_echo` 看每台 plane 收到的拒绝数。
- **跨房间「A 应答中 B 唤醒」的判据**：仲裁按房间隔离，B 的候选会被 admit（既不是 `turn_open`，
  也不是 `lost_same_word`）。
  - **文本/调试/渠道回合**：`runner.busy` 时返回 `409 {"detail": "a turn is already running"}`
    （`app.py:4112-4113` 的 `/api/v1/debug/say`、`:4370-4371` 的 `/api/v1/chat`），不产生第二回合（已测）。
  - **语音链路**：`on_utterance`（`app.py:2765` 起）**没有** busy 检查，`runner.run` 也没有重入保护；
    且 `on_wake`（`app.py:3029`）对任何被 admit 的 wake 都会 `runner.abort()`。
    因此跨房 B 唤醒**当前行为是可能打断 A 正在合成的回答**——是否打断 A、是否出现双声，
    属**真机录音 + 人耳**判据，本轮只记录实际，不预设结论；若出现打断/双声，升级为 0.8 缺陷单
    （不要在本卡改 app/runner）。
- 「实际谁出声、是否双声」只能靠**录音/人耳**判定：本节所有代码判据（计数、reason、409）只保证逻辑，
  不替代听感验收。
- ⚠️ **每台的 `wake_words` 最终会合并成一份 keywords 文件**：
  `_sync_devices` 把所有设备的词拼成一个列表再写盘
  （`app.py:1980-1998`、`:5440-5444`）。所以“给 A 房间配词1、B 房间配词2”**并不能**让
  A 的 plane 只听词1——每个 plane 都加载同一份词表。想让某台只听自己的词，目前**不支持**。
- Dot2 的 `device_wake` 默认关闭（`params.oww_model` 为空 → `oww_on_device=off`，
  `adapter.py:984-988`、`README.md:191-193`），此时唤醒由每个 plane 内的 RUI KWS 完成，
  天然会“多台同时听到”。

### 6.6 打断/停止能不能过，取决于全局 env（不是每台可配）

- adapter 默认在播放期静音上行、并在结束后保持 2500 ms（`DOT2_PLAYBACK_GATE_MS`，
  `adapter.py:296-300`）。静音期间 KWS 听不到「停」，只能靠设备侧 `device_wake`（需要 oww 模型）。
- 现网 override 是 `DOT2_PLAYBACK_GATE_MS=0` + `DOT2_OWW_ON_DEVICE=off` + `DOT2_BARGE_IN_ENABLED=0`
  （`docs/HANDOFF.md:9`；该组合在单台上打断 5/5 成功，`docs/HANDOFF.md:124`）。这套值对四台 Dot2 **同时生效**。
- `/api/v2/devices/{id}/test` 的 `capabilities.detail.missing_for_barge_in` 会告诉你缺什么；
  `reference` 缺失是 Dot2 的常态（`ref_channels=0`），不等于坏
  （`src/rui/device/contract.py:185-203`）。
- 【代码证实】如果 `audio.voice.barge_in=false`，播放中的唤醒会被直接忽略并记录
  `wake during playback ignored: voice.barge_in is off`（`process.py:1716-1720`）。

### 6.7 资源

- **5 个 plane**（LX06 + 4×Dot2），每个都加载 VAD/KWS（Dot2 无 oww 时 KWS 在 plane 内跑）。
  `multi_device.max_planes` 默认就是 **5**（`docs/multi-device-design.md:347-348`、`:453`），
  本布局**正好用满**：再加第 6 台就没有 plane，只会在 `/health.multi_device.refused` 里出现。
- 验收时记录：`docker stats --no-stream rui` 的 CPU/RSS、每台 `plane.wake_admit_latency_ms`、
  `first_response_ms`。**没有基线就不要下“性能没问题”的结论。**
- house 侧已固定：5 台时 `/health.audio_devices` 有 5 行、`refused == []`；第 6 台写入后
  `refused == ["dot2-e"]` 且 `over_max_planes` warning 带 `max_planes=5`
  （`tests/test_real_layout_5devices.py::test_max_planes_five_accepts_the_fifth_and_names_the_sixth_refused`）。

### 6.8 回滚

```bash
# 1) 回到单平面（设备仍单台可用）
curl -sS -X PATCH "$BASE/api/v2/house" -H "$H" -H 'Content-Type: application/json' \
  -d '{"multi_device":{"enabled":false}}' | jq '.restart'
# 注意：返回 restart.required=true / auto=false —— 必须手工重启容器
cd ~/rui && docker compose restart rui

# 2) 或整体回滚镜像/配置
#    docs/runbook.md:144-167（回滚）+ :206-263（备份与恢复）
```

【代码证实】`PATCH /api/v2/house` 只写配置并返回 `restart.required=true, auto=false`
（`v2_api.py:2044-2109`），**不会**自动重建 fleet——这与设备增删（auto=true）不同，别混。

---

## 7. 执行顺序（时间盒，给 9 哥 的清单）

| # | 动作 | 时间盒 | 通过判据 |
|---|---|---|---|
| 0 | 读 §0；`docker compose ps` 确认 healthy；`GET /health` 存基线 JSON | 5 min | 有基线文件 |
| 1 | 核对 env 变量名（§2.2）+ 端口占用（§6.1）+ secrets 权限（§6.3）+ 插件清单声明（§0.2 第 1 条） | 10 min | 4412/8870/8871/8873/8874 未被占；`control_port`/`loudness_file` 在插件 params 里 |
| 2 | `PATCH /api/v2/house {"multi_device":{"enabled":true}}`（`max_planes` 已是 5）→ `docker compose restart rui` | 5 min | `/health.multi_device.enabled=true`、`max_planes=5`；`audio_devices` 行数 = enabled inbound 数 |
| 3 | **现役三台逐台核对/补齐 params**：`living`（lx06，params N/A）+ `dot2` + `dot2-b`；见 §7.1 的 checklist 与逐条 PATCH body | 10 min | 三台详情：lx06 无 dot2 params 属正常；dot2/dot2-b 回读 `control_port=0` + 各自 `loudness_file`；PATCH 返回 `restart.scope="fleet"`、`status="ok"`，`/health.audio_devices` 对应行恢复 `running=true` |
| 4 | **两台新 Dot2**（`dot2-c` / `dot2-d`）逐台改 `controller.json` 指向 **8873 / 8874**（+ 确认 token/证书，§3） | 30 min（需 adb） | 设备日志出现 `Static endpoint ... wss://<host>:<port>`；`tls_port` 与该设备 `listen_port` 相同 |
| 5 | 添加 `dot2-c` / `dot2-d`（§4.2，**带 §0.2 的两个 params**）→ 入网 → 测试（§4.3）→ 唤醒/应答/播放/停止（§5）；`dot2-c` 先跑 8873 one-liner 验收（§7.2） | 2 × 10 min | §5.3 表逐格有证据（含 params 回读列）；`dot2-c` 的 `/test` 返回 `ok=true` |
| 6 | 串台检查（§5.2）：同走廊同词 4 台 1 次、跨房间播放中唤醒 2 次、弱间隔 1 次 | 10 min | 恰好 1 个 admit；播放房间不被夺 owner；尾音 `duplicate` |
| 7 | 第 6 台“拒绝”演练：`POST` 一台临时设备 → 看 `refused` | 3 min | 201 但 `refused` 含它、`over_max_planes` 有 `max_planes=5`；随后删除临时设备 |
| 8 | 删除**本次新增**的 `dot2-c` / `dot2-d`（§4.4）→ 每次确认其余设备恢复；现役 `living`/`dot2`/`dot2-b` **不删** | 2 × 3 min | 端口消失 + 其余 `connected=true` |
| 9 | 收尾：`docker stats` 记录、`/health` 归档、写 §5.3 表、HANDOFF 更新 | 10 min | 表填满或 null+原因 |

> 第 4 步是**唯一必须 9 哥 动手**的环节（adb + 设备侧；本 runbook 不连设备）。

### 7.1 现役三台 params 补齐 checklist（先做，再插新设备）

`living` 是 LX06，**不存在** `control_port`/`loudness_file` 这两个 dot2 参数，不要给它 PATCH 这两个
key（会得到 `unknown_params_ignored`）。`dot2` 和 `dot2-b` 建的时候 params 是 null（HANDOFF 决策：
**明确不兜底**，读配置不隐式补齐），各执行一次 PATCH。

- [ ] **living（客厅，LX06，4412）**：params N/A。只确认 `GET /api/v2/devices/living` 的
      `listen_port=4412`、`enabled=true`、`params` 里**没有** dot2 键（这是正常的）。
      **不发 PATCH**；LX06 没有响度控制面，也没有 `loudness_file`。
- [ ] **dot2（儿童房，8870）**：PATCH 两个 key（缺一不可）：
      ```bash
      curl -sS -X PATCH "$BASE/api/v2/devices/dot2" -H "$H" \
        -H 'Content-Type: application/json' -d '{
          "params": {
            "control_port": 0,
            "loudness_file": "/var/lib/rui/loudness-dot2.json"
          }
        }' | jq '{params: .device.params, unknown: .device.unknown_params,
                 ignored: [.device.warnings[].code], restart: .restart}'
      ```
- [ ] **dot2-b（次卧，8871，已 ready+connected）**：**不要再 POST**（`409 device_exists`），
      只 PATCH。生产实测它建时 params 留 null，日志出现
      `loudness control on 127.0.0.1:8872 unavailable (Errno 98)`；PATCH 后即修：
      ```bash
      curl -sS -X PATCH "$BASE/api/v2/devices/dot2-b" -H "$H" \
        -H 'Content-Type: application/json' -d '{
          "params": {
            "control_port": 0,
            "loudness_file": "/var/lib/rui/loudness-dot2-b.json"
          }
        }' | jq '{params: .device.params, unknown: .device.unknown_params,
                 ignored: [.device.warnings[].code], restart: .restart}'
      ```
- [ ] **控制台路径**：新增设备走 `/v2/devices/add` → “设备参数”步骤；表单会拦截空的
      dot2 `control_port`/`loudness_file`（task-22 前端校验，`SetupParamsStep.vue`）。
      已有设备的详情页 `/v2/devices/<id>` 里 `ParamsCard` 是**只读**的，没有 params 编辑器；
      现役 `dot2`/`dot2-b` 的补齐只能用上面的 PATCH。
- [ ] **重启平面的证据（每次 PATCH 都要看，不能只看 200）**：PATCH 的 `restart` 必须是
      `{"scope":"fleet","auto":true,"required":true,"status":"ok"}`；随后
      `curl -sS "$BASE/health" -H "$H" | jq '{multi_device, audio_devices, warnings}'`
      里该 id 的 `running=true`、其余房间不受影响；容器日志出现该 plane 的启动/重连行，
      且**不再**出现 `loudness control ... unavailable (Errno 98)`。
      params 是平面启动时读取的，所以这一步的 fleet 重建就是“补齐生效”的证明。
- [ ] **0.8.0 门禁值核对**：`dot2`=8870、`dot2-b`=8871、`dot2-c`=8873（`dot2-c` 在步骤 5 后才有）。

### 7.2 `dot2-c` 8873 one-liner 验收（添加后立即跑）

```bash
curl -sS -X POST "$BASE/api/v2/devices/dot2-c/test" -H "$H" \
  | jq '{ok, ready, waited_ms, checks: [.checks[] | {id, status, code}]}'
```

通过判据：`ok=true`（`ready=true` 更好）；`checks[]` 里 `plugin`/`link` 为 `ok`，
`link.code="connected"`；若设备还没入网，`ok=false` + `link.code="device_not_connected"`，
先回到 §3 的 `controller.json`（`tls_port` 必须 = 8873）再重跑，**不要**靠重启 rui 掩盖。

---

## 8. 未证实清单（不要当成已完成）

1. 两台新 Dot2（`dot2-c` / `dot2-d`）的固件版本、`controller.json` 现状、`X-EM-Token` 是否相同 —— **未证实**，需 adb。
   `dot2-b` 已在 8871 ready+connected，只补 params，不需要重新配对。
   - 现网已安装的 `dot2-echomuse` 插件清单是否已含 `control_port`/`loudness_file`
     （bridge ≥ c64acb5）—— **未证实**（装在 NAS `/vol1/rui/data/plugs/`）；
     用 §0.2 的两条 curl 回读确认。缺声明时 v2 不落盘。
2. EchoMuse 配对/证书签发的完整流程（从零到可用）—— **未证实**，仓库无脚本/文档。
3. 现网 `multi_device.enabled` 的真实值 —— **未证实**（配置在 NAS，不在仓库）；用
   `GET /health | jq .multi_device` 与 `PATCH /api/v2/house` 的返回确认。
4. 现网是否设置 `DOT2_CONTROL_PORT` —— **未证实**（HANDOFF 只有 `/loudness` 可用的旁证）。
5. 5 个 plane 同跑时的 RSS/CPU 与唤醒→首字延迟 —— **未证实**，需现场 `docker stats` + trace 记录。
6. 逐台 `params.secrets_dir` 经 v1 PUT 落盘后的实际生效 —— 【代码证实】路径存在，
   但无自动化验收；以日志 `X-EM-Token source: file` 为准。
7. 生产 `DOT2_PLAYBACK_GATE_MS=0` 下四台是否出现自听假回合 —— **未证实**，需 5 台同时播放观察
   `turn` 日志里是否有多余 turn（`docs/release/v0.7.0.md` 已知遗留第 1 条）。

---

## 附：证据速查（一条命令看全部）

```bash
BASE="http://127.0.0.1:8080"; H="X-Auth-Token: $RUI_TOKEN"
curl -sS "$BASE/health" -H "$H" | jq '{multi_device, audio_devices, warnings}'
curl -sS "$BASE/api/v2/devices" -H "$H" | jq '.items[] | {id, room, status, ready, connected, listen_port}'
curl -sS "$BASE/api/v1/audio" -H "$H" | jq '.planes | to_entries[] | {id: .key, running: .value.running,
  connected: .value.metrics.speaker_connected, candidates: .value.metrics.wake_candidates,
  admitted: .value.metrics.wake_admitted, rejected: .value.metrics.wake_rejected,
  utterances: .value.utterances}'
docker compose logs --since 30m rui | grep -E 'wake candidate|turn \{|turn:|barge:|dot2 link:|wake arbiter:'
```

字段来源：`/health`（`app.py:3250-3305`）、`/api/v1/audio` 的 `planes`
（`app.py:5129-5158`）、`/api/v2/devices`（`v2_api.py:1373-1378`）、
日志行（`process.py:794/798/1758`、`trace.py:149`、`device_link.py:335`、`app.py:2937`）。

---

## 9. 房间名硬编码审计（2026-10-28，task-22）

**结论：文档里的“客厅/书房”全是示例，没有一条是断言；代码里只有 D-013 的两条契约常量。
测试里出现的具体房间名必须来自该测试自己的配置。**

| 位置 | 原文 | 判定 | 处理 |
|---|---|---|---|
| `docs/multi-device-design.md:121` | ASCII 架构图 `客厅 LX06` / `书房 Dot2` | **示例**（画两个 plane） | 保留；§0.1 才是真实房间权威表 |
| `docs/multi-device-design.md:245` | 配置样例 `room: 书房` | **示例**（YAML 片段） | 保留；真实布局见本文件 §0.1 |
| `docs/multi-device-design.md:384` | 验收表“在客厅说 → 客厅答；在书房说 → 书房答” | **示例/判据模板**（写成“在 X 房间说 → X 房间答”同样成立） | 保留；本次按 §0.1 的 5 个房间执行 |
| `docs/multi-device-design.md:388` | 故障隔离“只有书房掉线” | **示例** | 保留 |
| `docs/contracts/C-002-console-v2-api.md`（多处：`:409/:604/:638/:688/:974/:1016`） | `/api/v2/house` 响应样例里的 `"room": "客厅"/"书房"` | **示例**（响应形状） | 保留；契约约束的是“rooms 数组的形状”，不是房间名 |
| `docs/design/console-ia-v2.md`（`:32/:51/:64/:70-74` 的「书房」） | 审计某次真实安装（客厅 + 书房）的界面问题 | **示例/现场记录** | 保留 |
| `src/rui/config/schema.py` `IMPLICIT_DEFAULT_DEVICE_ID="living"` / `IMPLICIT_DEFAULT_ROOM="客厅"` | 无 `default_output` 时的三级落点第一级 | **契约**（D-013 已拍板：`default_output: living`） | 不改；属产品决策，不是硬编码缺陷 |
| `tests/test_reminder_voice.py` 等 | 用 `room="客厅"/"书房"` 构造配置并断言路由 | **配置驱动**：期望值来自同一个 `_cfg(...)` 的入参，不是第二份字面量 | 保留 |
| `tests/test_real_layout_5devices.py` / `tests/test_wake_coordinator.py`（task-22 新增） | 真实 5 房间 | **配置驱动**：房间从 `REAL_LAYOUT`/`_rooms(devices)` 读取，测试内不再写第二份房间映射 | 新规则 |

**新规则（写进评审清单）**：测试可以出现具体房间名，但必须是“配置里写了什么、断言就读什么”
（config → expected），不得在断言里出现配置中没有的房间字面量；代码里只允许 D-013 的
`living`/`客厅` 这一条契约级常量。

**证据（可重跑）**

```bash
uv run pytest -o addopts="" tests/test_real_layout_5devices.py tests/test_wake_coordinator.py -q
# 7 + 22 passed
```
