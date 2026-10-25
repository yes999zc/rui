# Socket & Plug v1：设备唤醒与插头契约（草案）

> 状态：**v1 草案（2026-10-24）**，目标 RUI **0.3**。本文是插座侧规范；插头作者（dot2 / Echo Dot 2 等）按此实现，不需要改 RUI 核心。
> 依据：`docs/socket-plug.md` v0、`docs/install-wizard-design.md`、`docs/playback-path.md`、`src/rui/device/contract.py`，以及 issue [#3](https://github.com/yes999zc/rui/issues/3)（device_wake）、[#1](https://github.com/yes999zc/rui/issues/1)（播放空档）。
> 用语：**必须**（MUST）、**不得**（MUST NOT）、**应当**（SHOULD）、**可以**（MAY）。本文只定义契约，不含实现/提交/部署。

## 1. 范围

| 角色 | 交付物 | 不负责 |
|---|---|---|
| **RUI（插座）** | `DeviceAdapter` + `Capability`/`Transport`、manifest schema、连接生命周期、`device_wake` 策略、向导槽位、加载与信任 | 不刷机、不 root、不托管固件、不执行设备端命令 |
| **插头（plug）** | `plugin.yaml` + `devices/*.yaml` + 可选 adapter + 设备端交付物说明 + 验收记录 | 不定义 RUI 的 turn/barge 策略，不绕过 RUI 门控 |
| **设备端** | 固件 / client / App（A 类原厂共存，B 类高权限） | 不属于 RUI；RUI 只给链接、最低版本、校验和、回滚 |

**v1 保证**：接口在 0.3.x 内稳定；只靠 manifest 即可被理解；能力缺失即降级、不假装；`device_wake` 是一等事件但只是建议输入；v0 / LX06 行为不变；有契约测试工具可验收。**核心不出现品牌分支**（不得 `if xiaoai` / `if dot2`）。

**不在 v1 内**：

| 不包含 | 去向 |
|---|---|
| 越狱 / root / 刷机 / OTA | 外部流程；RUI 只展示链接、版本、校验和、回滚，不代刷 |
| 音频面内部实现 | AEC/KWS/VAD/Pacer/TTS 的算法与时序不是插头契约；插头只看到 `AudioFrame`、`play()`、`stop_playback()`、`DeviceWake` |
| 设备端代码执行 | manifest 只读；不执行 `README` / `install_steps` |
| 多设备 / 多房间 / 云端唤醒 | v1 单设备单音频面；唤醒只在设备或 RUI 本地 |
| 签名强制 / 安全认证 | 信任仍是 manifest 自述的 `builtin`/`trusted`/`third-party` + 运维方镜像审计；0.3.x 无签名/allowlist（`trust` 不是授权，见 §4.4） |
| 厂商助手语义 | `VendorSignalMsg`（让路）不变；`device_wake` 不是厂商唤醒 |

## 2. 能力模型

`capabilities[]` 的规范值只有下面 8 个；大小写敏感，重复即 manifest 错误。RUI 代码对应 `Capability.DEVICE_WAKE = "device_wake"`、`Capability.BARGE_VIA_DEVICE = "barge_via_device"`，其余沿用 v0 枚举。

| 能力 | 语义（插头必须做到） | 缺省 / 降级 | 备注 |
|---|---|---|---|
| `mic_array` | `DeviceInfo.mic_channels > 1`，帧内携带多麦克风通道 | 单麦采集，不做阵列处理；设备仍可自行波束成形 | 不隐含 `reference` |
| `reference` | `AudioFrame.reference_channels > 0`，回采参考与麦克风同帧 | RUI AEC 不可用，`missing_for_barge_in()` 非空；不承诺 RUI barge | 设备侧 AEC 不要求此项（dot2 可无） |
| `playback` | `play()` 接受 PCM；`stop_playback()` 可停止并丢弃设备缓冲 | 无语音输出；文本通道可用，但不得把超时当设备故障 | `stop_playback()` 必须幂等 |
| `led` | `set_led(effect, color, repaint)` 可接受并立即返回 | 隐藏灯效配置；不报错、不阻塞 turn | fire-and-forget |
| `keys` | adapter 可通过 `drain_signals()` 上报物理按键（厂商中立） | 无按键配置；不得把按键当唤醒替代 | v1 不标准化键码 |
| `volume` | `services.set_volume()` / `read_volume()` 可读写设备混音器 | 隐藏音量 UI；RUI 用固定增益 | **两层单位**：config/UI 0–100；`DeviceServices`/混音器 0–255（§4.1）。`DeviceAdapter.set_volume` 是 0–100 高层方法 |
| `device_wake` | 设备本地检测 RUI 唤醒词，经 `drain_wakes()` 上报 `DeviceWake` | RUI KWS 路径完全不变（LX06） | 要求 `wake.source: device`；与 vendor wake 分离 |
| `barge_via_device` | 设备在播放期间能检测打断并给出 `barge=true` 建议 | 只在 RUI 空闲时接受 `device_wake`；播放中忽略，回退 KWS 或无 barge | 依赖 `device_wake` + `playback`；建议非决定 |

**旧值**：`native_kws` 在 v1 弃用，不得用于新 manifest；厂商唤醒仍走 `drain_signals()` 的 `kind="wake"`。`streaming_asr` 保留为未启用值，v1 不定义行为。

**有效能力与降级**：

1. manifest 是上限：adapter 不得执行未声明能力；向导/控制台不展示未声明能力。
2. 五种可探测能力（`mic_array`/`reference`/`playback`/`device_wake`/`barge_via_device`）一律取 **manifest ∩ `probe().capabilities`**：任一侧没有声明就不生效。共享实现是 `src/rui/device/contract.py` 的 `effective_capabilities()` 与常量 `PROBE_BOUND_CAPABILITIES`；音频策略、向导和控制台必须使用同一个 helper，不得各自求并集。
3. `led`/`volume`/`keys` 由 manifest 决定、`DeviceServices` 的实际可调用性佐证，不要求出现在 `probe()`；服务抛 `NotImplementedError` 时按运行时降级。LX06 的 v0 probe 未列 `volume`/`keys`，这是合法的，v1 不得用简单交集误降级。
4. probe 多出但 manifest 未声明 → 丢弃并记日志，不扩权；`probe_only_capabilities()` 只用于诊断展示，不得并入有效能力。
5. 没有 manifest 的开发/mock adapter 没有上限，`probe()` 即有效能力（`effective_capabilities(None, probe)`）；一旦解析到 manifest，就按第 2/3 条执行。第三方的“manifest-only”声明不是运行许可：0.3.x 的 in-process 入口见 §4.4。

**能力在代码里控制什么**（0.3.2）：

| 能力 | 生效位置 | 缺失时 |
|---|---|---|
| `mic_array` | `DeviceInfo.mic_channels > 1` 时的多通道路径 | 单通道采集，不做阵列处理 |
| `reference` | `AudioFrame.reference_channels > 0` 才跑 RUI AEC | AEC 关闭；`missing_for_barge_in()` 非空 |
| `playback` | `play()` / `stop_playback()` / barge 前置 | 不推 TTS；barge 不生效 |
| `device_wake` | `_configure_device_wake()` 的 manifest ∩ probe 门 | RUI KWS 不变 |
| `barge_via_device` | 播放中 `barge=true` 的建议路径（还要 `wake.barge_via_device`） | barge 建议被抑制并计数 |
| `led`/`volume`/`keys` | `DeviceServices` 调用点；缺失时 UI/配置隐藏或跳过 | 不报错、不阻塞 turn |

**依赖校验**：

| 声明 | 必须同时满足 | 否则 |
|---|---|---|
| `wake.source: device` | `capabilities` 含 `device_wake`，adapter 实现 `drain_wakes()` | 缺能力报 manifest 错；运行时缺失降级 KWS |
| `wake.barge_via_device: true` | `source: device` + `device_wake` + `barge_via_device` + `playback` | manifest 报错 |
| `device_wake` 能力 | `wake.source: device` | manifest 报错（避免声明了永不用） |
| `barge_via_device` 能力 | `device_wake` + `playback` | manifest 报错 |
| `wake.source: rui` | 不得含 `device_wake`/`barge_via_device`，且 `barge_via_device: false` | manifest 报错 |
| 省略 `wake` | 等价 `source: rui`、`barge_via_device: false` | 合法（v0 默认） |

## 3. Device manifest

### 3.1 布局与 `rui-device/v1` 示例

与 v0 相同：`plugin.yaml`（保持 `rui-plugin/v0`）+ `devices/<model>.yaml`（每机型一个）+ 可选 `adapter/`、`assets/`、`README.md`、`LICENSE`。

```yaml
schema: rui-device/v1
id: dot2-model                # 小写 slug；也是 devices[].adapter 默认值
model: "RS03QR"               # 可选：硬件型号，仅展示，不参与身份
name: "Example Open Speaker"
vendor: "Example / EchoMuse"
class: B                      # A = 原厂共存；B = 高权限/完整控制
transport: inbound            # inbound = 设备拨回；outbound = RUI 连设备
protocol: {kind: ws, version: "1"}   # 具体字段由 adapter 解释
capabilities: [mic_array, playback, volume, device_wake, barge_via_device]
wake: {source: device, barge_via_device: true}
# 0.6：本机型的硬件音频默认（可选，合并的最底层；见 §3.2.1）
audio_defaults:
  capture: {rate: 48000, channels: 1, mic_channels: 1, ref_channels: 0}
  playback: {rate: 48000, channels: 1}
  ack_echo_ignore_ms: 2500
device_side:
  artifact: firmware          # firmware | client | app | none
  name: "Example device image"
  source: "https://example.invalid/device/install"
  version_min: "v2.18"
  install_steps: ["完成外部解锁与镜像安装", "确认能回连 RUI 并记录回滚方式"]
  rollback: "恢复原厂镜像/分区的步骤或链接"
# 0.3.x 运行 in-process；sidecar 只有 schema/向导占位，runner 在 0.4
runtime: {mode: in-process, entrypoint: "example_plugin.adapter:build",
          listen_port: 4412, token_env: RUI_DEVICE_TOKEN}
params:
  - {key: host, label: "音箱地址", type: string, required: true, placeholder: "<speaker-host>"}
  - {key: room, label: "房间名", type: string, default: "客厅"}
  - {key: wake_words, label: "唤醒词", type: list, default: ["你好小瑞"], max: 3}
compatibility: {rui_min: "0.3.0", tested_with: ["0.3.0"], notes: "device_wake 需要设备端 oww_wake；无 reference"}
acceptance: {verified: false, last_verified: "", evidence: "待真机验收；契约测试报告见 README"}
```

### 3.2 字段规则

| 字段 | 类型 / 必填 | v1 规则 |
|---|---|---|
| `schema` | literal / 是 | `rui-device/v1`；0.3 loader 兼容读取 v0（§7） |
| `id` | string / 是 | `^[a-z0-9][a-z0-9_-]*$`；插件内唯一；默认作为 adapter 名 |
| `model` | string / 否 | 硬件型号（如 `RS03QR`）；只展示，不参与路由/去重 |
| `name` / `vendor` | string / 是 | 非空；展示用，不得含 secret |
| `class` | `A` \| `B` / 是 | A 原厂共存；B 高权限。只影响向导警告/确认，不产生核心分支 |
| `transport` | `inbound` \| `outbound` / 是 | inbound 生成回连信息；outbound 由 `probe()` 连接 |
| `protocol` | mapping / 是 | `kind` 必填、`version` 可选；其余 key 归 adapter，核心不校验 |
| `capabilities` | list / 是 | v1 只能取 §2 的 8 个值（`native_kws` 拒绝、`streaming_asr` 保留但不启用）；不得重复；顺序无语义。v0 仍可含 `native_kws`/`streaming_asr`，不得含 `device_wake`/`barge_via_device` |
| `wake` | mapping / 否 | **仅 v1**；`source` 默认 `rui`，`barge_via_device` 默认 `false`；规则见 §2。v0 声明 `wake`（哪怕 `{source: rui}`）、`device_wake`、`barge_via_device` 或 `model` 一律报错，不静默升级 |
| `device_side` | mapping / 否 | `artifact` 必填；`source` 只允许 http(s) 或空、不得含用户名/密码/空白；`install_steps` ≤20 条、每条 ≤500 字符且非空；B 类 `rollback` 非空（去空白后）。内容只展示、不执行 |
| `runtime` | mapping / 否 | 省略 = 纯声明；`third-party` 不得 `in-process`；sidecar 必须有 `image`；in-process 必须有 `entrypoint`。`listen_port`/`token_env` 是**缺省值**：设备配置显式设置时以配置为准（`src/rui/device/registry.py`）。**0.3.x 没有 sidecar runner**：sidecar manifest 可以校验，但设备构建会给出明确错误（§7） |
| `params` | list / 是 | 表单 schema；类型 `string/int/bool/list/secret/choice`。`list.max` 必须覆盖 `default` 的长度；`secret` 无默认、只写不读；向导只接受 manifest 已声明的 key/类型/options（declared-only） |
| `compatibility` | mapping / 否 | `rui_min`/`tested_with` 为 semver；`notes` 人读 |
| `acceptance` | mapping / 否 | `verified` + `last_verified`（`YYYY-MM-DD`）+ `evidence`；`verified: true` 必须带 `last_verified`；向导不把它当完成证据 |
| `audio_defaults` | mapping / 否 | **仅 v1（0.6）**；本机型的硬件音频默认：`{capture?, playback?, ack_echo_ignore_ms?}`；未知键报错；v0 声明它一律报错。省略 = 中性核心默认（16000/1ch/0ref、playback 16000、ack earcon），所以旧 plug 不写也照常加载 |

### 3.2.1 `audio_defaults` 与三层合并（0.6）

```text
设备默认（manifest audio_defaults，本文档）   ← 最底层
  < 全局 config（config.yaml 的 audio / ack，显式写下的值才覆盖）
    < devices[].audio（单台设备的覆盖，最高层）
```

- 每一层都可缺省，缺一层不报错；三层合成后再做一次 schema 校验（`mic_channels +
  ref_channels == channels`），失败指向具体设备而不是在音频面里崩帧长。
- 核心默认是**中性**的：capture 16000 Hz / 1 ch / 0 ref、playback 16000 Hz / 1 ch、ack `earcon`。
  LX06 的 8 ch / 6 mic / 2 ref / 48 kHz 由它自己的 manifest 声明
  （`plugs/xiaoai/devices/lx06.yaml`，镜像内为 `/opt/rui-plugins/xiaoai/devices/lx06.yaml`），
  核心不再硬编码任何机型数字。
- 「全局 config」按**文件里是否写了该字段**判定：没写的字段继续落到设备默认；显式写下的值
  （例如生产模板把 capture.rate 写成 16000 以省 wifi 带宽）永远赢。`save_config` 因此
  不会把核心默认值（audio 各字段与 `ack.echo_ignore_ms`）写回文件，避免一次控制台保存
  就把设备默认「冻结」成显式覆盖。
- `ack_echo_ignore_ms` 属于唤醒应答（`ack.echo_ignore_ms`）而不是 DSP 参数，所以放在
  `audio_defaults` 顶层，由 `effective_ack_echo_ignore_ms()` 单独合成；它不进
  `effective_audio()` 的 `audio` 块，避免看起来生效实际被忽略的假旋钮。
- LX06 manifest 自 0.6 起是 `rui-device/v1`（唤醒仍走 RUI KWS：省略 `wake` 即
  `source: rui`），随镜像放在 `plugs/xiaoai/`；没有 `audio_defaults` 的旧 plug
  兼容样本由 `examples/plugin-example` 与测试 fixture 覆盖（v0 参考包在 0.6 M2 随
  核心目录一起移除）。

### 3.3 `wake` 块与校验

| 字段 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `source` | `device` \| `rui` | `rui` | `device` = 设备本地唤醒并上报；`rui` = RUI KWS 触发 |
| `barge_via_device` | bool | `false` | 是否允许设备在播放中给 `barge=true` 建议；仍受 RUI 策略约束 |

- `source: device` 时，manifest **应当**在 `params` 中声明 `wake_words` 项，或在 `device_side.install_steps` 要求用户在设备端配置同一组词；v1 没有 adapter 侧“写入设备唤醒词”的标准方法（§8 Q2）。
- 能力 `barge_via_device` 表示“设备做得到”，`wake.barge_via_device` 表示“本插头启用”；两者都真才允许设备 barge 建议。
- `wake` 块不得携带 token、IP、密钥；地址仍走 `params.host`；上报事件的 `source` 固定 `"device"`。
- **v0 更正（2026-10-25）**：issue #3 旧答复说“v0 里 `wake` 是 optional、加这个块合法”是错的。冻结口径与 0.3.2 实现都是 **v0 不得声明 `wake`/`device_wake`/`barge_via_device`/`model`**；要设备唤醒请发布 `rui-device/v1`，否则继续用 v0 并走 RUI KWS。错误信息会直接给出这两个选择。

**校验规则**：

| 类别 | 规则 |
|---|---|
| 路径 | `devices:` 条目必须相对、解析后位于插件目录内；拒绝 `..`、绝对路径、目录外 symlink（`src/rui/plugins/loader.py`） |
| 大小 / 编码 | 每个 manifest ≤1 MiB；UTF-8；顶层 mapping；YAML 安全加载；空文件报错 |
| 未知字段 | 除 `protocol` 外 `extra=forbid`；报 `文件:字段:原因`，不静默忽略 |
| `params` | key `^[a-z][a-z0-9_]*$`；`secret` 只写不读且无 `default`；`choice` 非空 `options` 且 default 在其中；`list.max` 必须 ≥ default 长度；manifest 不得写 token/key 值 |
| `device_side` | `source` 只允许 http(s)/空，不得含用户名/密码；`install_steps` ≤20 且每条 ≤500 字符；B 类缺 `rollback` 报错；内容只展示、不执行 |
| `runtime` | `entrypoint` 形如 `package.module:function`，只校验不 import；`image` 是镜像引用而非路径；`listen_port` 1–65535；`token_env` 是环境变量名 |
| 信任 | `builtin`/`trusted` 可 in-process；`third-party` 默认且只能 sidecar（**0.3.x 没有 runner，实际不可运行**）；`trust` 不因 `device_wake` 提升；manifest 不能声明 secret 值 |
| 版本 | `rui_compat` 是 semver 范围；`rui_min`/`tested_with` 是 semver；不匹配拒绝加载，不静默尝试。矩阵见 §7 |

## 4. Adapter 契约

### 4.1 Adapter 一览（v0 原有 + v1 必需）

一个 adapter 是 **数据 + 异步方法**，不继承具体基类（`DeviceAdapter` 是 `Protocol`）。完整 surface：

| 成员 | 单位 / 类型 | 语义与硬要求 |
|---|---|---|
| `probe() -> DeviceInfo` | `DeviceInfo` | 实际能力与音频布局；不得乐观猜测。`id/adapter/model/host/room` 展示用，`channels/sample_rate/bit_depth/mic_channels/ref_channels` 描述帧；`capabilities` 是**声明**，有效能力还要与 manifest 求交（§2） |
| `open()` / `close()` | — | 幂等；`close()` 后 `frames()` 结束、`drain_*()` 不再返回事件。异常只允许以普通异常冒泡，由 RUI 的 fail-soft 边界处理 |
| `frames() -> AsyncIterator[AudioFrame]` | — | **int16 little-endian、按通道交织、reference 通道在尾部**；`ts_ms` 与 `DeviceWake.ts_ms` 同一单调时钟原点；默认 20 ms 一块；连接期间不得无限阻塞，至少每 100 ms 让出一次 |
| `play(pcm, *, sample_rate, channels)` | PCM | 按实际布局播放；`channels` 必须匹配 `probe()`；不得同步阻塞帧循环 |
| `stop_playback(fade_ms=120)` | ms | 停止并丢弃设备缓冲；**幂等**；barge 路径要求服务端停推 ≤300 ms |
| `set_volume(percent)` | **0–100** | 高层音量，与 config/UI 同单位；adapter 自己映射到混音器。0.3 核心优先调用低层 `services`（见下） |
| `read_volume()` | **0–255** | 混音器值；可选，默认委托 `services.read_volume()`，返回 `None` 表示不可读 |
| `services` | `DeviceServices` 属性 | 可选低层对象：`shell()`、`read_volume()`、`set_volume(0–255)`、`set_led()`。**0.3 音频面实际调用的是 `adapter.services.set_volume/read_volume`（0–255）**；config/UI 0–100 在 `audio/process.py` 与 `device/contract.py:percent_to_device_volume()` 处换算 |
| `set_led(effect, color, repaint)` | — | fire-and-forget；无灯/无能力时不得假装成功（`NullServices` 抛 `NotImplementedError`） |
| `drain_signals()` | `list` | 厂商助手事件（vendor wake / dialog_end / playing）；语义与 v0 相同，默认 `[]` |
| `drain_wakes()` | `list[DeviceWake]` | 设备检测到**我们的**唤醒词；默认 `[]`；规则见 §4.2 |
| `reports_consumption` / `consumed` / `sink_generation` / `wait_consumed(since, timeout)` | 可选播放头 | v1 不改语义；不能回报的设备保持 `reports_consumption=False`，Pacer 按墙钟 |
| `health()` | 可选鸭子类型 | 只用于诊断；不是必需方法，异常被忽略 |

**音量单位（唯一口径，P0-2）**：

| 层 | 单位 | 说明 |
|---|---|---|
| config `devices[].volume` / 控制台 UI | **0–100** | `0` 表示“不托管，交给设备/厂商” |
| `DeviceAdapter.set_volume()` | **0–100** | 高层方法；参考 adapter 用它演示映射 |
| `DeviceServices.set_volume()` / `read_volume()` | **0–255** | 设备混音器；实现必须拒绝越界而不是静默 clamp。`0` 在范围内，但 LX06 把 0 视为“保留给厂商”并拒绝写入；核心不会发送 0（config `volume=0` = 不托管） |
| `DeviceVolume` IPC（`src/rui/audio/protocol.py`） | **0–255** | 0.3 的历史内部单位；控制面/API 若直接暴露它，须在文档中标注并在 0.4 收敛 |

换算只允许通过 `device/contract.py` 的 `percent_to_device_volume()` / `device_volume_to_percent()`；adapter 不应各自复制 magic number（LX06 混音器就是 0–255）。

**错误策略**：adapter 方法抛异常由 RUI 的 fail-soft 边界记日志/计数并降级，不允许让适配器输入杀死帧循环或控制面；`NotImplementedError` 表示“本设备没有该服务”，不是故障。

### 4.2 `DeviceWake` 与 `drain_wakes()`

```python
@dataclass(frozen=True)
class DeviceWake:
    ts_ms: float
    score: float
    threshold: float
    barge: bool = False
    level: float | None = None
    source: Literal["device"] = "device"

def drain_wakes(self) -> list[DeviceWake]:
    """Return every pending device wake since the last call; default []."""
    return []
```

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `ts_ms` | float | 是 | 单调时钟毫秒，原点与 `AudioFrame.ts_ms` 一致；不得用 wall-clock epoch |
| `score` | float | 是 | 有限值，归一化 0–1；adapter 从模型原始分数映射 |
| `threshold` | float | 是 | 有限值、>0；本次判定门限（advisory，RUI 记录用） |
| `barge` | bool | 否 | 设备建议“唤醒发生在播放中且可打断”；RUI 不直接采信 |
| `level` | float \| None | 否 | 可选 dBFS；仅诊断，不参与策略 |
| `source` | `"device"` | 是 | v1 固定；其他值视为无效 |

**字段更正（P0-3，2026-10-25）**：v1 的 `DeviceWake`/`DeviceWakeMsg` **只有以上六项**，没有 `device_id`/`room`/`playing`/`last_active`/`last_active_ms`。多设备由“一设备一音频面”隐式确定；RUI 在 trace 里用自己的配置补齐 `device_id`/`room` 并写入 `TurnTrace.meta`（**RUI enriches，adapter 不填**）。0.4 若需要仲裁字段，会以新增可选字段或独立仲裁事件的方式提前一个 minor 通知；dot2 等插头不得按旧 Q5 答复往事件里塞这些字段（dataclass 会 `TypeError`，pydantic 会静默丢弃）。

**校验分层**：`DeviceWake.__post_init__` 在构造时拒绝非 bool 的 `barge`（`"false"`/`1`/`None`）和非 `"device"` 的 `source`；`device/contract.py:wake_validation_error()` 提供纯函数版本（finite/0–1/threshold>0/level/staleness），供 adapter、契约工具与音频面共用同一套措辞。`score` 越界仍由 RUI 的策略层丢弃并计数，不在 dataclass 里做会改变策略语义的范围检查。

**`drain_wakes()` 规则**：同步、非阻塞、不 `await`、不做网络/磁盘 I/O，单次 <1 ms；RUI 在音频面帧循环中每帧调用一次（默认 20 ms），不为 adapter 单独起线程/定时器；RUI 保证单调用者串行调用；每次返回自上次调用以来的全部事件，不重复、`close()` 后不返回；队列必须有界，**推荐直接使用 `device/contract.py` 的 `BoundedWakeQueue`（默认 `MAX_PENDING_WAKES = 32`，溢出丢最旧并计 `dropped`）**；可跨线程生产，但只能用锁/线程安全队列，不得从其他线程直接操作 asyncio 原语；异常由 RUI 捕获降级，不得在方法内回调 RUI。RUI 用 `getattr(adapter, "drain_wakes", None)` 调用，缺失等价 `[]`（与 `drain_signals()` 一致），并且每 tick 最多处理 64 条、超出丢弃计数（§5）。

### 4.3 与 `drain_signals()` 的关系 / 声明支持

| 通道 | 语义 | 事件 | RUI 反应 |
|---|---|---|---|
| `drain_signals()` | 厂商助手在做什么 | `kind="wake"` / `"dialog_end"` / `"playing"` | 让路/静默窗口；`wake` 是**厂商**唤醒 |
| `drain_wakes()` | 设备检测到**我们的**唤醒词 | `DeviceWake` | 启动/打断**我们的** turn |

- 两条通道**不得合并**；不得把 `DeviceWake` 塞进 `VendorSignal.kind`，也不得把厂商唤醒伪装成 `device_wake`。
- 每个帧 tick 先 `drain_signals()` 后 `drain_wakes()`；同 tick 同时出现厂商 `wake` 与 `device_wake` 时，**厂商让路优先**，`device_wake` 记 `device_wake_conflict` 丢弃。
- 启用条件必须**同时**满足：(a) 解析到 manifest 且 `trust in {builtin, trusted}`、`runtime.mode != sidecar`；(b) manifest 声明 `device_wake` 且 `wake.source: device`；(c) 有效能力（manifest ∩ `probe()`）含 `device_wake`，barge 还需 `barge_via_device` + `playback`；(d) `wake.barge_via_device: true`（仅 barge 路径）；(e) adapter 实现 `drain_wakes()`。任一缺失：manifest 不合法则拒绝加载；运行时缺失则降级 KWS 并记一次日志。**解析失败必须 fail-closed**：请求了 `params.plug_id` 但 manifest 缺失/损坏时按 KWS 处理（`frozen Q8`/P2-4），不得因为“没有上限”而放行 device_wake。没有配置任何 plug 的显式开发 adapter（mock）才是“manifest 不存在 → probe 为准”的例外。

adapter 只实现 `drain_wakes()`；音频面到控制面的 `DeviceWakeMsg` 是 RUI 内部消息（`src/rui/audio/protocol.py`），字段对齐 `DeviceWake`，只传事件、不做策略。

### 4.4 Downstream adapter 加载钩子（0.3.x，issue #7 Q1）

0.3.x 提供两个**显式、fail-soft** 的 out-of-tree adapter 入口。它们只加载运维方在环境变量或已安装发行版 entry point 里声明的代码，**不读取** `devices[].params`、`plugin.yaml`、控制台输入、配置文件或网络内容，也不启动子进程。`sitecustomize` / `PYTHONPATH` 注入不再是官方路径。

**环境变量**（逗号分隔，按书写顺序处理）：

```bash
# 1) 最简：adapter 名默认取模块名
RUI_ADAPTER_PLUGINS=my_plugin.adapter:build

# 2) 显式 adapter 名（devices[].adapter 必须用这个名字）
RUI_ADAPTER_PLUGINS=dot2=my_plugin.adapter:build

# 3) 注册钩子：插件自己调用 registry.register()
RUI_ADAPTER_PLUGINS=my_plugin.adapter:register

# 4) 多个入口，env 优先于 entry points
RUI_ADAPTER_PLUGINS=dot2=my_plugin.adapter:build,lx06x=other:build
```

| 写法 | 含义 |
|---|---|
| `module:build` | `build(dev, cfg) -> DeviceAdapter`，与内置 builder 同签名；启动时导入模块，按设备调用 |
| `module:register` | 启动时以**受限 registry facade**（`register`/`available`）为参数调用一次；钩子可 `registry.register("dot2", "my_plugin.adapter:build")`。facade 执行同样的 builtin 保护与 `allow_override` 规则，不能绕过 |
| `module` | 先找 `build`，再找 `register` |
| `name=module:attr` | `name` 是注册名；未写时优先用插件返回的 `adapter_name`/`name`，否则用模块名 |
| 零参数 callable | 视为工厂，在启动时调用；返回值可以是 builder、带 `build`/`name` 的映射/对象，或 `module:attr` 字符串。**注意**：`build(dev=None, cfg=None)` 有位置参数（即使都有默认值），是 per-device builder，启动时绝不调用；`factory`/`make`/`create_factory` 才是显式工厂角色 |

**Python entry points**（setuptools）：

```toml
[project.entry-points."rui.adapters"]
dot2 = "my_plugin.adapter:build"
```

启动时扫描 group `rui.adapters`。entry point 名即 adapter 名；值既可以是普通 builder，也可以是返回 builder 的零参数 callable。完全相同的 `(name, value)` 只处理一次；entry point **不覆盖**环境变量或内置同名项；单条加载失败只记一条 warning。环境变量同名项之间后者覆盖前者（记 warning），因为那是运维方显式顺序。

**fail-soft**：模块导入失败、属性缺失/不可调用、builder 工厂抛错、注册钩子抛错、entry point 的 `load()` 失败，都只记一条有界 warning 并继续启动。控制面仍会起来，`/health` 只暴露 `adapter_plugins.registered`（已注册名）和 `warnings`（数量），不返回异常文本；完整原因看日志。

**信任要求（P0-1/P1-9，0.3.2 实际行为）**：

- `RUI_ADAPTER_PLUGINS` / `rui.adapters` 都是**运维方通道**：只能由 root/容器运维方设置，**不得**把用户输入、设备上报值、控制台输入或 `params` 拼进这个变量/group。`pip install` 或加进镜像等价于把代码放进 RUI 进程。
- 两条通道都**必须由 manifest 背书**：解析到的 `plugin.yaml` 的 `trust` 必须是 `builtin` 或 `trusted`；`third-party` 一律拒绝 in-process（0.3.x 没有 sidecar runner，见 §7）。没有 manifest → 拒绝注册，不再“因为运维方写了 env 就信任”。manifest 可以随 adapter 模块/包放在同一目录，或安装到 `<data_dir>/plugs/*` 并用 `RUI_DATA_DIR` 指向 data_dir；downstream 配方因此**必须**同时交付 `plugin.yaml` + `devices/*.yaml`。
- `trust` 本身仍是 manifest **自述的元数据**，不是签名/授权边界；真正的授权是“运维方审计后把代码和 manifest 放进镜像/目录”。0.3.x 没有 allowlist/签名，`local` 不是合法值（只有 `builtin`/`trusted`/`third-party`）。
- **内置名字不可覆盖**：`xiaoai`/`mock` 等 image 自带 adapter 不能被 env 或 entry point 替换；要不同行为请注册新名字并改 `devices[].adapter`。env 同名项之间后者覆盖前者（运维方显式顺序），entry point 永远 `allow_override=False` 且不覆盖 env/builtin。
- `third-party` manifest 即使没有 `runtime` 块也不能用来放行 `device_wake`/barge：运行期按 `trust` + `runtime.mode` fail-closed（§4.3）。
- 0.4 才会把该钩子收敛为正式插件加载（trusted in-process entrypoint / third-party sidecar runner）；当前 env 是 0.3.x 的稳定过渡接口。镜像配方见 `docs/plug-test-kit.md`。

## 5. RUI 的 `device_wake` 策略

| RUI 状态 | 事件 | 动作 |
|---|---|---|
| 空闲（无 turn、无播放） | 有效 `device_wake` | 校验 → arm `UtteranceGate` → 等 VAD 出句；**不直接调 LLM** |
| 空闲 | 无效/低分/过期 | 丢弃计数；RUI KWS 照常 |
| 播放中 | `barge=true` 且允许 | 冲刷队列 + `stop_playback(fade_ms)` + 暂停音乐 → arm gate → 起 turn |
| 播放中 | `barge=true` 但不允许 | 抑制，继续播放，记 `device_wake_barge_suppressed` |
| 播放中 | `barge=false` | 视为误报忽略；不停止播放 |
| turn 已打开 | 新的 `device_wake` | 丢弃当前 gate 缓冲并重新 arm；记 `device_wake_interrupt` |
| 厂商静默窗口 | 有效 `device_wake`（无同 tick 冲突） | 清除窗口并 arm gate；同 tick 冲突则厂商优先 |
| 任意 | VAD 不可用 | 不形成 turn；记 `device_wake_no_vad`，回退 KWS；不得凭事件直接调用 LLM |

**VAD 门是硬要求**：`device_wake` 只负责“打开耳朵”，不负责“提交问题”；没有后续语音（`wake_idle`）就关会话，不产生 turn、不产生 ASR/LLM 成本。事件到达后，**以 `ts_ms` 为锚点**回放 `[ts_ms - wake_preroll_ms, now]` 内仍在缓冲的音频并 arm gate（缓冲保留 `wake_preroll_ms + DEVICE_WAKE_STALE_MS`；锚点比保留的音频更旧时计 `device_wake_replay_missed++`，不错误回放最新窗口）；`device` 来源的 `wake_skip_ms` 默认 **0**（不同于 RUI KWS 的 300 ms）。barge 的“播放中”以 RUI 自己的 `_speak_queued_s`/播放任务为准；设备说 barge 但 RUI 已空闲时按空闲唤醒处理，不执行 stop。

**barge 允许条件**（全部满足）：`voice.barge_in: true`（`src/rui/config/schema.py`；半双工/关闭打断时 barge 直接抑制）；不在厂商静默窗口、硬件/ACK mute 或 gate hold 内；manifest `wake.barge_via_device: true` 且能力已生效；事件 `barge=true`；RUI 确认正在播放；未触发去抖/限频。任一条不满足则**不停止播放**，只记录建议。停止路径与 KWS barge 共用：flush + `stop_playback()` + 暂停音乐；服务端停推目标 ≤300 ms（设备 ALSA 缓冲另计），trace 记 `silence_after_ms`。

**去抖与限频**：

| 规则 | 默认 | 行为 |
|---|---|---|
| 同事件去抖 | 500 ms | `ts_ms` 重复或与上次接受差 < `debounce_ms` → 丢弃，`device_wake_debounced++` |
| 单设备限频 | 5 次 / 10 s | 超出丢弃，`device_wake_rate_limited++`；不回退 KWS（避免双 turn） |
| 无效事件 | — | 不更新“上次接受”时间，避免坏事件堵住好事件 |
| 同 tick 冲突 | — | 厂商 `wake` 优先；`device_wake` 丢弃计数 |

**无效条件**：`score`/`threshold` 非有限、`score` 不在 0–1、`threshold<=0`、`score<threshold`、`ts_ms` 过期 >1500 ms 或超前 >500 ms、`source!="device"`、`barge` 不是 bool、`level` 非有限。处理：丢弃、计数、限频日志；**不抛异常、不停 KWS**。`barge=true` 但能力未生效不是无效事件（空闲时仍可正常唤醒）；播放中按上表抑制并记 `device_wake_barge_suppressed`。

| 条件 | 行为 |
|---|---|
| 未声明 `device_wake` | RUI KWS 完全不变（LX06） |
| 声明但 probe/方法缺失 | 启动警告一次；`device_wake_fallbacks++`；KWS 继续 |
| `drain_wakes()` 抛异常 | 捕获；30 s 只记一次；KWS 继续；连续失败可标记本会话降级 |
| `drain_wakes()`/`drain_signals()` 超量 | 每 tick 最多处理 64 条；超出按“丢最旧”丢弃并计 `device_wake_dropped`/`signal_dropped`（收到 list 时），帧循环不被拖慢 |
| 有效事件但 VAD 不可用 | 不产生 turn；KWS 继续；`device_wake_no_vad++` |
| 有效事件但无后续语音 | `WakeIdle` 关会话；不回退 KWS（避免重复 turn） |
| 连续 10 个无效事件 | 本会话标记 `device_wake_degraded`，KWS 继续；重启/重连后重试 |

**指标/trace**：`Metrics` 新增 `device_wakes`、`device_wake_turns`、`device_wake_barges`、`device_wake_debounced`、`device_wake_invalid`、`device_wake_suppressed`、`device_wake_fallbacks`、`device_wake_last_score`、`device_wake_dropped`、`device_wake_replay_missed`；`wake_count` 仍只统计 KWS。采集侧另有 `capture_degraded`/`capture_errors`/`capture_restarts`/`capture_last_error`、`signal_errors`/`signal_dropped`/`signal_invalid`/`signal_last_error`、`task_errors`/`task_last_error`：`probe()`/`frames()` 失败会降级并在重试预算耗尽时发 `Error` 关闭控制 socket，由 supervisor 重建进程，不会留下“进程在、耳朵聋”。`TurnTrace.meta` 写 `wake_source`、`device_wake_score`、`device_wake_threshold`、`device_wake_barge_hint`、`device_wake_action`、`device_wake_debounced`。日志只记事件与决策，不记原始音频、token、完整设备地址。

**安全/信任**：adapter 输入是建议，不能绕过 VAD、`voice.barge_in`、厂商静默窗、限频、鉴权或工具权限；不能仅凭事件调用 LLM/TTS。`device_wake` 不授予 shell/led/volume，也不改变 `trust` 级别；每次接受/抑制/丢弃都有计数与 trace；事件做类型/范围校验；限频 + 有界队列防滥用；事件不含文本/音频，manifest 不含 secret。

## 6. 新插头参考流程

### 6.1 步骤

| 步 | 插头作者做什么 | 产出 / 证据 |
|---|---|---|
| 0 | 读本文 + v0 的 A/B 门槛；确认权限等级与回滚方式 | 选 A/B；B 有恢复路径 |
| 1 | 建仓库：`plugin.yaml`、`devices/<model>.yaml`、`README`、`LICENSE` | 可被 `load_plugin()` 读取 |
| 2 | 实现 adapter；有 device_wake 时实现 `drain_wakes()` | `probe()` 与 manifest 一致 |
| 3 | 声明能力、`wake`、`params`、`runtime`、`compatibility` | manifest validator 通过 |
| 4 | 跑契约测试（§6.2） | 报告全绿并保留 JSON |
| 5 | 接入 RUI：放入 `data/plugs/<id>/`，重启/扫描 | `GET /api/v1/plugs` 可见 |
| 6 | 向导：选插头 → 设备侧准备 → 参数 → 唤醒词 → 接线 → 测试 → 第一句 | `SetupState` 完成；有连接/探测证据 |
| 7 | 真机验收：唤醒、barge、播放、灯/音量、厂商让位（A 类） | 填入 `acceptance`，附报告链接 |
| 8 | 发布 tag（可附外部 checksum；RUI 0.3.x 不验签/不校验 checksum）；声明 `rui_compat` | 可复现安装；回滚说明齐全 |

> adapter 随 downstream 镜像发布时，注册走 §4.4 的 `RUI_ADAPTER_PLUGINS` 或 `rui.adapters` entry point；`data/plugs/` 只承载 manifest/声明，不负责执行 adapter 代码。

### 6.2 契约测试工具

计划 0.3 P3.0 交付：

| 组件 | 内容 | 通过标准 |
|---|---|---|
| Manifest validator | 包装 `src/rui/plugins/loader.py` 的 `load_plugin()`；CLI 输出 `文件:字段:原因`，非零退出 | 合法样例通过；坏路径/能力/`wake`/`params` 均拒绝 |
| Fake device / adapter | 可配置 capabilities、帧布局、播放状态；实现 `drain_wakes()` 注入队列 | 无需真机跑完整 turn 骨架 |
| Wake injection harness | `inject_wake(ts_ms, score, threshold, barge, level)`；覆盖空闲、播放、mute、重复、过期、低分、同 tick vendor | 每个场景的 turn/barge/ignore 与 §5 一致 |
| Playback gap harness | 模拟 issue #1 的“首块后 ~400 ms 才到下一批”；断言 `playback_gap_count` 不增长、`max_gap_ms <100` | 参数可复现；LX06 基线不回归 |
| Boundary harness | probe 缺能力、`drain_wakes()` 抛异常、`stop_playback()` 失败、VAD 缺失 | 不崩溃；降级 KWS；计数正确 |
| Report schema | `contract-report.json`：manifest、场景、结果、指标、环境 | 可作为 `acceptance.evidence` 附件 |

工具是开发/验收工具，不是运行时。**0.3.2 的 kit 以 in-process 方式加载 `--adapter`**（等价于运维方信任该代码），不是沙箱；不要用 kit 跑未审计的第三方代码。kit 不测 playback gap/boundary 场景（未交付，见 P1-11），这些由插头/音频面自测。

### 6.3 验收标准

建议值（待 §8 Q7 确认）：manifest validator 全绿；注入 20 次空闲唤醒 + 有语音 → ≥19 次形成 turn，静音 20 次 → 0 turn；播放中 `barge=true` 20 次 → `silence_after_ms ≤300 ms`；issue #1 的 400 ms 双批源 → `max_gap_ms <100`、`underruns=0`，额外延迟短回复 ≤150 ms、长回复可配 ≤300 ms；无 `device_wake` 的 LX06 行为不变；A 类完成厂商让位/接管，B 类完成回滚演练。

### 6.4 dot2 检查单

控制/数据两条 socket 若存在，起播/flush 屏障按 `docs/playback-path.md` §6.4 的 P3 设计；**gap/boundary harness 在 0.3.2 未交付**，dot2 需保留自己的 flush+pong/预缓冲（0.4 再评估）。无 `reference` 合法（依赖设备侧 AEC）；`wake: {source: device, barge_via_device: true}` + 对应能力，`drain_wakes()` 映射 EchoMuse `oww_wake` 的 `score/threshold/ageMs/capturedMono/barge/level`；barge 只是建议，RUI 按 §5 最终判定；0.3 前可先用 v0 manifest 跑 KWS，`drain_wakes()` 可先实现但不会被 0.2 调用。

### 6.5 镜像自带插头：LX06（`plugs/xiaoai/`）

0.6 M2（`docs/roadmap-0.6.md`）把 `src/rui/device/xiaoai/` 搬成与 dot2 同构的插头：核心没有 LX06 分支，镜像把 `plugs/` 复制到 `/opt/rui-plugins`，manifest loader 扫描该目录，registry 再从 manifest 的 `runtime.entrypoint` 注册 adapter。它**是**生产运行时路径，不再是参考包。

| 部件 | 内容 | 对应规范 |
|---|---|---|
| `plugin.yaml` | id `xiaoai-lx06`，trust `builtin`，`rui_compat: ">=0.3.0 <0.7.0"` | §3.1 / §3.2 |
| `devices/lx06.yaml` | `rui-device/v1`，id `lx06`，class A，inbound，`ws`，in-process，能力 `mic_array` / `reference` / `playback` / `led` / `keys` / `volume`，`audio_defaults` 48000/8 ch/6 mic/2 ref/32 bit/>>12/>>16 + `ack_echo_ignore_ms: 1200`，参数 `host` / `room` / `wake_words` | §3、§7 |
| `xiaoai_plug/` | adapter / wire protocol / LED ring / mixer services；`build(dev, cfg)`，`kit_live_device_required = True` | §4.1 |
| `wake` | 无；v1 默认 `source: rui`，LX06 继续走 RUI KWS | §2、§7 |

```bash
python -m rui plug-test plugs/xiaoai
python -m rui plug-test plugs/xiaoai \
  --adapter xiaoai_plug.adapter:build --json
```

预期 16 PASS / 0 FAIL / 0 WARN / 5 SKIP（0.5.3 + 0.6 M2，同步记录在 `plugs/xiaoai/README.md`）：`manifest.*`、`adapter.import/construct/methods`、`device.ready/probe_layout`、两个 `poll.*`、`lifecycle.*` 通过；`device.open`、`capture.frames`、`capture.cadence`、`playback.sink` 记为 **SKIP**，因为 LX06 是 inbound，必须等 open-xiaoai client 拨入才能跑帧/播放；`wake.inject` 也 SKIP（RUI KWS，没有 device_wake）。这些 SKIP 由 adapter 的 kit-only 提示 `kit_live_device_required` 标明（`docs/plug-test-kit.md`），不改变生产行为、不伪造设备，也不把 SKIP 记成 PASS。

> 15/0/2/5 是 **device_wake 类插头**（Dot 2）的报告：多出的两条 WARN 是
> `device.probe_layout` 与 `device.device_wake_effective`。LX06 走 RUI KWS 且
> probe 报全布局，所以没有这两条 WARN；不能用伪造 WARN 去对齐数字。

迁移：`adapter: xiaoai` 在 0.6.x 仍可用（`src/rui/adapter_aliases.py` 的单 minor
别名），新配置写 manifest 的机型 id `adapter: lx06`。旧 config 零迁移即启动。

## 7. 迁移与兼容

| 项 | v0 | v1 |
|---|---|---|
| 机型 schema | `rui-device/v0` | `rui-device/v1`（0.3 同时接受 v0） |
| 机型型号 | 无独立字段 | 新增可选 `model`（展示用） |
| 唤醒声明 | 无 | 新增可选 `wake: {source, barge_via_device}` |
| 能力 | 含 `native_kws` | 8 个规范值；新增 `device_wake`、`barge_via_device`；`native_kws` 弃用 |
| Adapter | `drain_signals()` | 新增 `drain_wakes()` 与 `DeviceWake` |
| 策略 | RUI KWS 唯一入口 | device_wake 可入口，RUI 拥有最终 turn/barge 决策 |
| 内部 IPC | `VendorSignalMsg` | 新增 `DeviceWakeMsg`；vendor 语义不变 |
| 音频默认 | 无（核心写死 LX06） | 新增可选 `audio_defaults`（0.6）：`capture`/`playback`/`ack_echo_ignore_ms` |
| 验收 | 手工 + 既有 pytest | 增加 manifest validator、wake injection、playback gap 工具 |

**兼容规则**：loader 接受 v0，按 `wake.source=rui`、`barge_via_device=false` 处理；v0 不得声明 `wake`/`device_wake`/`barge_via_device`/`model`/`audio_defaults`（否则报错，不静默升级）。0.6 起 LX06 的 manifest（`plugs/xiaoai`，镜像内 `/opt/rui-plugins/xiaoai`）升为 `rui-device/v1` 以声明 `audio_defaults`（唤醒仍走 RUI KWS，省略 `wake` 即 `source: rui`）；LX06 adapter 不实现 `drain_wakes()`，KWS 路径不变。v0 兼容样本改由 `examples/plugin-example` 与测试 fixture 覆盖。v0 + 新字段、v1 + 未知字段、`rui_compat` 不匹配都报文件/字段。`devices[].params` 仍是值字典，`params.plug_id` 继续作为 adapter→plug 兜底（`src/rui/plugins/resolve.py`）。

**sidecar（0.3.x 明确未实现）**：manifest 仍可写 `runtime.mode: sidecar` 并通过 schema 校验，但 0.3.x **没有 sidecar runner/RPC**。向导返回 `sidecar_not_supported`；`build_adapters()` 对解析到 sidecar 的设备返回明确错误 `runtime.mode 'sidecar' is not implemented in 0.3.x; run the plug in-process with trust: trusted (or wait for the 0.4 sidecar runner)`，不会静默降级成 in-process，也不会把它当成“未知 adapter”。第三方插头在 0.3.x 因此不可运行，这是有意的最小安全面（§Q8）。sidecar 内部 RPC 由插头定义，v1 不标准化。

**版本矩阵（唯一口径，P1-6/S8）**：

| 层 | 字段 | v0 | v1 |
|---|---|---|---|
| 插件级 | `plugin.yaml.rui_compat` | `>=0.2.0 <0.7.0` | `>=0.3.0 <0.7.0` |
| 机型级 | `compatibility.rui_min` | `0.2.0`（可更低） | `0.3.0` |
| 允许的 schema | `rui-device/*` | v0 | v1；0.3 loader 同时读 v0 |

- 镜像 LX06 插头 `plugs/xiaoai`：v1 矩阵 `rui_compat: ">=0.3.0 <0.7.0"`、`rui_min: "0.3.0"`。它由 `load_plugin()` 加载，兼容上下限由 loader 强制（0.6 M2 之前这是绕过 loader 的 in-code builtin，上限 `<0.7.0`）。
- 旧的 v0 参考包已随 0.6 M2 移除；v0 manifest 的支持本身仍在（synthetic fixtures + `examples/plugin-example` 的 v1 对照）。
- 示例包 `examples/plugin-example/`：v1 矩阵 `>=0.3.0 <0.4.0` / `rui_min: "0.3.0"`，不再对 0.2 宣称可用。
- `rui_compat` 是**唯一插件级**版本门；`rui_min` 是机型下限。能力可用性在运行时按 §4.3 判定，不用版本号猜能力。

**版本协商**：

| RUI | manifest | 结果 |
|---|---|---|
| 0.2.x | v0 | 正常加载；无 `device_wake` |
| 0.2.x | v1 | 拒绝 schema；向导显示不兼容 |
| 0.3.x | v0 | 正常加载；RUI KWS |
| 0.3.x | v1 | 正常加载；按 §5 启用 |
| 任意 | `rui_compat` 不匹配 | 拒绝；显示要求范围与当前版本 |

`rui_compat` 是唯一插件级版本门；能力可用性在运行时按 §4.3 判定，不用版本号猜能力。dot2 v1 发布建议 `rui_compat: ">=0.3.0 <0.4.0"`；0.3 前用 v0 分支/开发分支验证，不把 v1 manifest 发给 0.2 用户。

**v0 → v1 迁移示例**（只有需要 `device_wake` 时才迁移；否则留在 v0 走 RUI KWS 是合法的）：

```yaml
# plugin.yaml：不要继续用 v0 的 >=0.2 <0.3
rui_compat: ">=0.3.0 <0.4.0"     # v1 矩阵

# devices/<model>.yaml：从 v0 改成 v1，只加 v1 字段
schema: rui-device/v1             # v0 声明 wake/device_wake 会直接报错
model: "RS03QR"                   # 可选，展示用
capabilities: [mic_array, playback, device_wake, barge_via_device]
wake: {source: device, barge_via_device: true}
compatibility: {rui_min: "0.3.0", tested_with: ["0.3.2"]}
```

不要给 v0 manifest 加 `wake` 试图“渐进升级”：loader 会拒绝（§3.3 更正）。0.2 用户只能收到 v0 分支/开发分支的 manifest，不能收到 v1。

**弃用**：`rui-device/v0` 在 0.3.x–0.5.x 继续支持，最早 0.6.0 移除（0.5.0 保留 v0，因为当时的 LX06 参考插头仍是 v0；0.6 M2 起自带插头已是 v1）。0.3.2 起 `load_plugin()` 每个 v0 manifest 路径每进程记一次 warning（`src/rui/plugins/loader.py:_warn_v0_once`），避免每请求刷屏；`native_kws` 自 v1 弃用（v1 manifest 直接拒绝）；`streaming_asr` 保留为未定义值；`VendorSignalMsg`/`drain_signals()`/厂商让路窗口不弃用、不改语义；任何字段移除都“先警告、后 minor、再移除”，不得在 patch 移除。

**提取已完成（0.6 M2，取代旧的 0.4 计划）**：`src/rui/device/xiaoai/` 的实现已移入 `plugs/xiaoai/`，核心 registry 不再有任何设备适配器表项；`plugins/builtin.py` 只扫描镜像插头目录（`/opt/rui-plugins`）并交给同一个 manifest loader，`src/rui/plugins/builtin.py` 里没有任何机型数据；adapter entrypoint 为 `xiaoai_plug.adapter:build`，manifest 的 device id 是 `lx06`，而旧配置的 `devices[].adapter: xiaoai` 通过 `src/rui/adapter_aliases.py` 的单 minor 别名继续工作（计划 0.7.0 移除）。本切片不删除 v0 schema 支持。

清单：

1. ✅ manifest 加载：`plugs/xiaoai/{plugin.yaml,devices/lx06.yaml}`，镜像内 `/opt/rui-plugins/xiaoai`；
2. ✅ registry/loader 只发现不硬编码：image plug 从 manifest `runtime.entrypoint` 注册；
3. ✅ 名称别名：`xiaoai` → `lx06`，一个 minor，旧 config 零迁移。

**0.3 实现落点（计划）**：

| 文件 | 变化 |
|---|---|
| `src/rui/device/contract.py` | `Capability.DEVICE_WAKE`/`BARGE_VIA_DEVICE`；`DeviceWake`；`drain_wakes()` 默认方法 |
| `src/rui/plugins/models.py` | 接受 v1；`model`；`WakeSpec`；能力/依赖校验 |
| `src/rui/plugins/loader.py` | 双 schema；v0 defaults；保持文件/字段错误格式 |
| `src/rui/audio/protocol.py` | `DeviceWakeMsg`；`Metrics` 新计数 |
| `src/rui/audio/process.py` | 帧循环 drain；barge/去抖/限频；VAD 门；fallback。**现状注意：0.2 的 `_on_wake` 尚未读取 `voice.barge_in`，0.3 必须补上** |
| `src/rui/config/schema.py` | `device_wake.debounce_ms`/`max_per_10s`/`stale_ms`（建议值） |
| `tests/` | manifest v1、wake injection、playback gap、LX06 回归 |

### 7.1 P3.0/P3.1 实现说明（2026-10-24）

- **已落地**：`Capability.DEVICE_WAKE`/`BARGE_VIA_DEVICE`、`DeviceWake`、`drain_wakes()`
  默认 `[]`；`rui-device/v1` 的 `model`/`wake` 与依赖表；`DeviceWakeMsg`；音频面
  空闲/播放/去抖/限频/冲突/降级策略；`Metrics.device_wake_*`；`TurnTrace.note_wake()`。
  `_on_wake` 现在读取 `voice.barge_in`：关闭打断时，播放中的 KWS 唤醒被忽略，
  不停止播放、不 arm gate。音频面本地镜像 `VendorWindow`（厂商 wake 打开、
  我们的 wake 清除），窗口内的设备 barge 提示按 §5 抑制。
- **v0**：仍按 `wake.source=rui` 处理；v0 声明 `model`、`wake`、`device_wake` 或
  `barge_via_device` 直接报错（§7：v0 + 新字段不静默升级）。v1 中的 `native_kws`
  报错而不是仅警告（§7 的“弃用”按“拒绝新 manifest”执行；旧 v0 不受影响）。`wake`
  中的未知字段（含 token/地址）由 `extra=forbid` 拒绝。
- **运行期启用**：音频面先解析设备对应的 plug manifest（`params.plug_id` 或 adapter，
  `src/rui/plugins/resolve.py`），要求 `wake.source: device` 且声明
  `device_wake`，再看 `probe()` 与 `drain_wakes()`。解析不到 manifest 的 adapter
  （mock / 开发用 in-process adapter）没有可对照的上限，按 `probe()` 为准；
  manifest 声明了 device 但 `probe()` 缺失时同样降级 KWS 并计
  `device_wake_fallbacks`。`runtime.mode: sidecar` 的插件即使声明
  `device_wake` 也按 Q8 降级为 RUI KWS（sidecar 内部 RPC 尚未标准化）。
- **常量而非配置**：`debounce=500 ms`、`5 次/10 s`、`stale>1500 ms`、`future>500 ms`、
  `10 个无效事件降级`、`30 s drain 异常日志` 目前是 `src/rui/audio/process.py`
  的模块常量；`device_wake.*` 配置项留到后续小版本，默认值与本文一致。
- **IPC/观测**：每个有效 `DeviceWake`（含随后被去抖/限频/抑制/冲突丢弃的）发送一次
  `DeviceWakeMsg`；只有真正 arm gate 或执行 barge 时才发 `Wake`，其
  `source="device"`、`action`、`score`、`threshold`、`barge_hint` 供控制面写
  `TurnTrace.meta`。控制面在消费 `last_wake` 时调用 `TurnTrace.note_wake()`（与
  `note_playback_metrics()` 同类的鸭子类型钩子）；`WakeIdle`/`drain_wakes()` 异常/
  VAD 缺失均不形成 turn、不调用 LLM。


## 8. 开放问题与明确非目标

### 8.1 需 9 哥 决策

> 每条都影响 dot2 的实现节奏或验收口径；未定时按“建议”执行。

1. **schema 形态（Q1）**：最终用 `rui-device/v1`，还是在 `rui-device/v0` 加可选 `wake`？issue #3 回复倾向后者；本文选 v1 以便 `extra=forbid` 下显式区分。建议：**v1 + 0.3 兼容 v0**。
2. **设备唤醒词归属（Q2）**：设备本地唤醒词由谁写入？v1 只要求 manifest `params` 的 `wake_words` 或安装步骤人工同步，没有 adapter `configure()`。是否增加“RUI 下发唤醒词”标准方法？建议：**v1 人工同步，v2 再做**。
3. **barge 权威（Q3）**：设备说 `barge=true` 但 RUI 认为已空闲（设备还有缓冲音频）时谁赢？建议：**RUI 状态为准，设备 barge 仅建议**。
4. **同 tick 冲突（Q4）**：厂商 `wake` 与 `device_wake` 同一 tick 到达，谁优先？建议：**厂商优先，下一 tick device_wake 可接管**。
5. **score/threshold 口径（Q5）**：强制归一化 0–1，还是允许原始分数？是否要求跨设备可比？建议：**adapter 归一化，RUI 只校验 0–1，不跨设备比较**。
6. **mute / 半双工（Q6）**：v1 只有 `voice.barge_in`；是否新增每设备 `wake.half_duplex`？ACK/TTS mute 窗口内的 barge 是排队还是丢弃？建议：**窗口内丢弃并计数，窗口后下一次唤醒正常**。
7. **验收数字（Q7）**：§6.3 的 20 次/≥19、静音 0 次、barge ≤300 ms、`max_gap_ms <100`、额外延迟短 ≤150 ms/长 ≤300 ms 是否作为 dot2 正式门槛？建议：**先按此执行，真机后再收紧**。
8. **sidecar 的 device_wake（Q8）**：第三方 sidecar 是否允许声明？RUI 侧代理 adapter 的 `drain_wakes()` 由谁实现？建议：**v1 只对 in-process/可信 adapter 完整支持；sidecar 先 degraded，RPC 契约后续**。

### 8.2 非目标

- 不提供越狱/root/刷机/OTA/固件分发；不代刷、不托管镜像、不执行设备命令。
- 不定义 AEC/KWS/VAD/Pacer/TTS 的内部算法或时序；这些是 RUI 音频面实现。
- 不保证任意音箱零成本接入；A/B 最低门槛仍按 `docs/socket-plug.md` §8。
- 不做多设备仲裁、每设备证书、approve 队列、插件市场、云端唤醒。
- 不做签名强制/安全认证/渗透测试；v1 信任是 manifest 自述 + 运维方镜像审计（0.3.x 无 allowlist/签名/验签）。
- 不改变厂商助手让路/接管语义；`VendorSignalMsg` 不是 `device_wake` 的替代。
- 不承诺硬实时 SLO；验收数字是目标，不是对所有硬件的保证；不分发唤醒词模型。

### 8.3 参考

- `docs/socket-plug.md`（v0 契约、A/B 类、向导骨架）
- `docs/install-wizard-design.md`（8 步实现、manifest 槽位、错误码）
- `docs/playback-path.md` §6（issue #1、预缓冲、gap、P3 屏障）
- `src/rui/device/contract.py`；`src/rui/plugins/{models,loader,builtin,resolve}.py`
- `src/rui/audio/{protocol,process}.py`；`src/rui/session/vendor.py`
- issue [#3](https://github.com/yes999zc/rui/issues/3)；issue [#1](https://github.com/yes999zc/rui/issues/1)

## 9. Lead 决策（2026-10-24，9 哥 休息期间按建议冻结）

§8.1 的 8 个开放问题按“建议”执行，作为 0.3 的冻结口径；9 哥 醒来后如需更改，按 minor 兼容规则处理。

| Q | 决定 | 理由 |
|---|---|---|
| Q1 schema | `rui-device/v1` + 0.3 兼容 v0；v0 不得声明 `wake`/`device_wake` | 显式区分，避免 `extra=forbid` 下静默升级 |
| Q2 唤醒词归属 | v1 人工同步（manifest params/安装步骤）；v2 再考虑 `configure()` | 不增加 adapter 方法面 |
| Q3 barge 权威 | RUI 状态为准；设备 `barge` 仅建议 | 安全与一致性；避免设备缓冲误判 |
| Q4 同 tick 冲突 | 厂商 `wake` 优先，下一 tick device_wake 可接管 | 不破坏厂商让路语义 |
| Q5 score 口径 | adapter 归一化 0–1；RUI 只校验范围，不跨设备比较 | 简单、可验证 |
| Q6 mute/半双工 | v1 只有 `voice.barge_in`；静音窗口内丢弃并计数，窗口后正常 | 最小契约 |
| Q7 验收数字 | §6.3 作为 0.3 的 dot2 门槛，真机后再收紧 | 先能量化 |
| Q8 sidecar | v1 只完整支持 in-process/可信 adapter；sidecar 降级为 KWS，RPC 后续 | 不扩大安全面 |

另外：0.2 的 `_on_wake` 尚未读取 `voice.barge_in`，0.3 必须补上（已列入 §7 实现落点）。

### 9.1 0.3.2 审计更正（2026-10-25）

冻结决策不变，下面是对旧答复/旧文档的**文字更正**，实现已按此收紧：

| 项 | 旧说法（需废弃） | 0.3.2 口径 |
|---|---|---|
| schema | issue #3：v0 里 `wake` optional、可以加 | v0 **不得**声明 `wake`/`device_wake`/`barge_via_device`/`model`；要 device wake 必须 v1（§3.3、§7） |
| trust | `trust=trusted/local` 可作为入口许可；`local` 合法 | `TrustLevel` 只有 `builtin`/`trusted`/`third-party`，没有 `local`；`trust` 是自述元数据，不是授权。env/entry point 必须由 `builtin|trusted` manifest 背书，`third-party` 不得 in-process（§4.4） |
| 注册入口 | `RUI_ADAPTER_PLUGINS` 不读 manifest | 现在**必须**有 manifest；内置名字不可覆盖；register hook 收受限 facade（§4.4） |
| DeviceWake | issue #7 Q5：`device_id/room/playing/last_active` | v1 只有六项：`ts_ms/score/threshold/barge/level/source`；`device_id/room` 由 RUI enrich，0.4 才考虑仲裁字段（§4.2） |
| 音量 | RUI 0–100、adapter 映射；`services` 未定义 | 两层单位固定：config/UI 0–100；`services` 0–255；换算 helper 在 `device/contract.py`（§4.1） |
| barge | probe 有能力即可 barge | **AND**：有效 `barge_via_device` 能力 + `wake.barge_via_device: true` + `voice.barge_in` + 播放中 + 未限频（§5） |
| sidecar | 第三方默认 sidecar（可运行） | 0.3.x **没有 sidecar runner**：明确报 `sidecar_not_supported`/构建错误；0.4 才实现（§7） |
| 兼容范围 | 各文档 `rui_compat` 不一致（`>=0.2 <0.3`、`>=0.2 <0.4`、`>=0.5`…） | 唯一矩阵见 §7；builtin/参考/示例已对齐并在 import/测试中自检 |

## 10. Q1–Q6 FAQ（issue #7 答复摘要，2026-10-25）

Dot2 桥在 issue [#7](https://github.com/yes999zc/rui/issues/7) 提出的接入问题，0.3.x 的口径如下；详细契约以上文各节为准，这里只做索引，不重复展开。

| Q | 问题 | 0.3.x 结论 | 0.4 方向 |
|---|---|---|---|
| Q1 | 官方 out-of-tree adapter / plug 入口 | 用 §4.4 的 `RUI_ADAPTER_PLUGINS` 或 `rui.adapters` entry points；官方镜像 + 只加 adapter 层的 downstream 镜像受支持，前提是不 fork 核心、**随 adapter 交付 `plugin.yaml`+`devices/*.yaml`** 且 `trust: trusted`（或 builtin）、镜像由插件方维护。没有 manifest 的 env/entry point 不注册；`third-party` 不 in-process；内置名字不可覆盖。`trust` 是自述元数据，不是签名/授权 | 收敛为正式 entrypoint（trusted in-process）/ sidecar（third-party） |
| Q2 | 多设备与端口 | 每设备 `devices[].listen_port` 已支持；manifest `runtime.listen_port`/`token_env` 在设备配置未显式设置时作为缺省值应用（`build_adapters()`），配置值优先。**4412 不保留给 LX06**，Dot2 用 8870/wss 不冲突；0.3 仍是**一个音频面实例驱动一台设备**，多设备在线显式 warning，不做假仲裁 | 每个 enabled 设备独立音频面 + 独立端口；多设备唤醒仲裁 |
| Q3 | 设备凭据 | 每设备 `token_env`（默认 `RUI_DEVICE_TOKEN`），0.3 设备管理支持逐设备 token 轮换；RUI token 是传输层准入，EchoMuse `X-EM-Token` 由 adapter 内层校验，**两层分离** | 不变 |
| Q4 | AEC 归属 | 0.3 `audio.aec` 仍全局；`who=device` 表示 RUI 只消费 mono、不跑 AEC，barge 依赖设备唤醒/门控 + RUI 播放状态；`devices[].aec` 进 config v3 | config v3 参数化 + 每设备覆盖 |
| Q5 | device_wake 多设备 | **更正**：v1 事件只有 `ts_ms/score/threshold/barge/level/source` 六项，不含 `device_id/room/playing/last_active`；`device_id` 由当前音频面隐式确定，`device_id/room` 由 RUI enrich 到 trace。turn/barge 最终由 RUI 决策，0.3 单设备语义完整。多设备仲裁字段是 0.4 的独立事件/可选字段 | RUI 侧多设备仲裁 |
| Q6 | 响度 / EQ / 播放统计 | **音量更正**：config/UI `volume` 0–100；`DeviceServices.set_volume/read_volume` 0–255，换算是唯一的 `percent_to_device_volume()`/`device_volume_to_percent()`；`DeviceAdapter.set_volume` 是 0–100 高层方法，参考 adapter 已实现。核心只有每设备 volume 和 LED；EQ/limiter/trim 留在 plug `params`，由 adapter 自己执行；无 per-block 播放头的设备可按 EOS/固定间隔回报粗粒度统计，`played_ms` 不是 v1 契约字段 | 每设备参数覆盖；Pacer 继续容忍粗粒度 |

Q7（文档）：契约就是本文，自测工具见 `docs/plug-test-kit.md`；多设备 / AEC / config v3 的落地排在 `docs/ROADMAP.md` 的 0.4。

Q8（sidecar）：0.3.x 只完整支持 in-process/`builtin|trusted` adapter；`runtime.mode: sidecar` 能通过 manifest 校验但**不能运行**，向导返回 `sidecar_not_supported`、设备构建返回明确错误。0.4 才提供 sidecar runner 与 RPC 契约。

## 11. 审计处置记录（2026-10-25，0.3.2）

来源：`docs/socket-plug-audit.md` §8.1 S1–S16 / §8.2 C1–C11、
`docs/socket-plug-impl-audit.md` P1-1/P1-5/P1-6/P1-9/P2-1/P2-2/P2-9/P2-13/P2-15。
状态：**已实现** = 代码/文档/测试都在本切片；**文档** = 本切片只收口文字；
**音频面** = 由 `src/rui/audio/{process,protocol}.py` 的工作流负责（不在本切片修改）；
**控制台** = `src/rui/web/api/*` 的工作流；**延后** = 0.4 或明确不做，已在本文写明。

| ID | 处置 | 状态 |
|---|---|---|
| S1 | §4.1 增加 adapter 一览：`DeviceInfo`/`AudioFrame`（int16 LE、交织、reference 尾部、`ts_ms` 原点、20 ms 节奏）、`services`、play-head、生命周期/错误策略 | 已实现（文档） |
| S2 | 音量两层单位固定；`percent_to_device_volume()`/`device_volume_to_percent()`；参考 adapter 实现 `services` 0–255 | 已实现 |
| S3 | §2 有效能力 = manifest ∩ probe（五个可探测能力），共享 `effective_capabilities()`；8 规范值 + `streaming_asr` 保留；`native_kws` v1 拒绝；能力门控表 | 已实现 |
| S4 | barge 双开关（能力 + `wake.barge_via_device`）写入 §4.3/§5；`missing_for_barge_in()` 支持 reference 与 device_wake 两条路径 | 已实现（contract；音频策略由音频面落地） |
| S5 | `DeviceWake` 保持六字段；RUI enrich `device_id/room`；0.4 才加可选仲裁字段 | 已实现（文档+contract；`DeviceWakeMsg` 由音频面保持对齐） |
| S6 | v0 无 `wake`、v1 接受；v0→v1 迁移示例；v0 每路径一次弃用 warning | 已实现 |
| S7 | `trust` 明确为自述元数据；env/entry point 必须 manifest 背书（builtin/trusted）；内置名不可覆盖；sidecar 0.4 | 已实现（文档+loader）；签名/allowlist 仍未做，明确列为 0.4 |
| S8 | 唯一版本矩阵（§7）；builtin/reference/example 全部对齐；builtin import 自检 | 已实现 |
| S9 | connect/TLS 契约（P1-8）：0.3 仍由 adapter 自己拥有；本文只说明 `/connect` 硬编码 ws(s)/token，不新增 `runtime.connect` 字段 | 文档（明确未实现）；延后到 0.4 |
| S10 | `device_side`/`params` 规则转为硬校验：http(s)、无 userinfo、≤20 步 ≤500 字、B 类 rollback、list.max、acceptance 日期 | 已实现 |
| S11 | 指标/trace 名称与 14 字段表：§5 与音频面代码对齐（音频面负责最终口径） | 音频面 |
| S12 | playback gap/boundary harness 与 `Ready.ordered_start` 屏障：未交付；§6.2/§6.4 明确标为计划/dot2 自备 | 延后（0.4） |
| S13 | 能力驱动 UI 隐藏：控制台仍按现有端点展示；"hide in 0.3" 未实现，本文不承诺 | 控制台（延后） |
| S14 | 移除规范正文的品牌例外；LX06 仅作参考插头；默认端口/参数不再表述为品牌特权 | 文档 |
| S15 | 多设备：0.3 只有一个音频面/当前设备端口 bind；manifest 的 `listen_port` 作为缺省值；0.4 仲裁 | 文档（实现属控制台/音频面） |
| S16 | `source.checksum`/`signature` 未实现：manifest `extra=forbid`，本文不宣称可校验签名；`rui_compat` 矩阵已对齐 | 文档（明确未实现） |
| C1 | `plugins/models.py`：`DeviceSide` URL/步数/长度、B rollback、`ParamSpec.list.max`、`Acceptance.verified` 日期 | 已实现 |
| C2 | `plugins/loader.py`：v0 弃用 warning；trust/runtime 校验保留；`listen_port`/`token_env` 缺省值由 `build_adapters()` 应用 | 已实现（应用点在 `device/registry.py`） |
| C3 | `device/loader.py`：env/entry point 必须 builtin/trusted manifest；third-party/in-process 拒绝；builtin 名保护 | 已实现 |
| C4 | 音频面：effective capabilities、AEC/reference/mic_array/playback 门控、barge AND、suppression 顺序、drain cap、音量 | 音频面（本切片只提供 `effective_capabilities()` 等 helper） |
| C5 | 控制台：单一音量单位、manifest runtime/connect、ParamSpec 校验、能力隐藏、多设备选择 | 控制台（延后；本切片已提供 manifest runtime 缺省值） |
| C6 | `device/contract.py`：`services`/`read_volume` 纳入 adapter surface；`DeviceWake` 仍六字段；barge 路径 | 已实现 |
| C7 | `audio/protocol.py`：`DeviceWakeMsg`/`Ready`/指标 | 音频面 |
| C8 | 契约工具：`services`/`read_volume`、双 barge flag、gap/boundary、能力感知 SKIP | 部分（参考/示例 adapter 已实现 `services`；kit 本身未扩展，延后） |
| C9 | `plugins/builtin.py`：`rui_compat >=0.2.0 <0.4.0`、`tested_with` 含 0.3.2、import 自检 | 已实现 |
| C10 | `examples/plugin-example`：v1 矩阵 `>=0.3.0 <0.4.0` / `rui_min 0.3.0`；adapter 音量/服务契约 | 已实现 |
| C11 | 测试：v0+wake 拒绝、DeviceSide 非法 source/B rollback、list.max、音量两端、barge flag、manifest 缺失、DeviceWake 额外字段/类型 | 已实现（`tests/test_{contract,plugins,adapter_plugins,reference_plugin,plug_contract}.py`） |
