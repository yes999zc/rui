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
| 签名强制 / 安全认证 | 信任仍为 `builtin`/`trusted`/`third-party` + 人工 allowlist |
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
| `volume` | `set_volume()` / `read_volume()` 可读写设备混音器 | 隐藏音量 UI；RUI 用固定增益 | RUI 0–100，adapter 映射（LX06 为 0–255） |
| `device_wake` | 设备本地检测 RUI 唤醒词，经 `drain_wakes()` 上报 `DeviceWake` | RUI KWS 路径完全不变（LX06） | 要求 `wake.source: device`；与 vendor wake 分离 |
| `barge_via_device` | 设备在播放期间能检测打断并给出 `barge=true` 建议 | 只在 RUI 空闲时接受 `device_wake`；播放中忽略，回退 KWS 或无 barge | 依赖 `device_wake` + `playback`；建议非决定 |

**旧值**：`native_kws` 在 v1 弃用，不得用于新 manifest；厂商唤醒仍走 `drain_signals()` 的 `kind="wake"`。`streaming_asr` 保留为未启用值，v1 不定义行为。

**有效能力与降级**：

1. manifest 是上限：adapter 不得执行未声明能力；向导/控制台不展示未声明能力。
2. `mic_array`/`reference`/`playback`/`device_wake` 以 manifest ∩ `probe().capabilities` 为准；probe 缺少即降级。
3. `led`/`volume`/`keys` 可由 `DeviceServices` 实际可调用性佐证，不强制出现在 `probe()`；服务抛 `NotImplementedError` 时按运行时降级。
4. probe 多出但 manifest 未声明 → 忽略并记日志，不扩权。
5. **LX06 例外**：manifest 声明 `keys`/`volume`，当前 `probe()` 未列（`src/rui/device/xiaoai/adapter.py`）；v1 不得用简单交集误降级，按第 3 条判定。

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
device_side:
  artifact: firmware          # firmware | client | app | none
  name: "Example device image"
  source: "https://example.invalid/device/install"
  version_min: "v2.18"
  install_steps: ["完成外部解锁与镜像安装", "确认能回连 RUI 并记录回滚方式"]
  rollback: "恢复原厂镜像/分区的步骤或链接"
runtime: {mode: sidecar, image: "ghcr.io/example/rui-plugin-dot2:1.0.0",
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
| `capabilities` | list / 是 | 只能取 §2 的 8 个值；不得重复；顺序无语义 |
| `wake` | mapping / 否 | 仅 v1；`source` 默认 `rui`，`barge_via_device` 默认 `false`；规则见 §2 |
| `device_side` | mapping / 否 | `artifact` 必填；`source` 只允许 http(s) 或空；`install_steps` ≤20 条、每条 ≤500 字符；B 类 `rollback` 非空 |
| `runtime` | mapping / 否 | 省略 = 纯声明；`third-party` 不得 `in-process`；sidecar 必须有 `image`；in-process 必须有 `entrypoint` |
| `params` | list / 是 | 表单 schema；类型 `string/int/bool/list/secret/choice` |
| `compatibility` | mapping / 否 | `rui_min`/`tested_with` 为 semver；`notes` 人读 |
| `acceptance` | mapping / 否 | `verified` + `last_verified`（`YYYY-MM-DD`）+ `evidence`；向导不把它当完成证据 |

### 3.3 `wake` 块与校验

| 字段 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `source` | `device` \| `rui` | `rui` | `device` = 设备本地唤醒并上报；`rui` = RUI KWS 触发 |
| `barge_via_device` | bool | `false` | 是否允许设备在播放中给 `barge=true` 建议；仍受 RUI 策略约束 |

- `source: device` 时，manifest **应当**在 `params` 中声明 `wake_words` 项，或在 `device_side.install_steps` 要求用户在设备端配置同一组词；v1 没有 adapter 侧“写入设备唤醒词”的标准方法（§8 Q2）。
- 能力 `barge_via_device` 表示“设备做得到”，`wake.barge_via_device` 表示“本插头启用”；两者都真才允许设备 barge 建议。
- `wake` 块不得携带 token、IP、密钥；地址仍走 `params.host`；上报事件的 `source` 固定 `"device"`。

**校验规则**：

| 类别 | 规则 |
|---|---|
| 路径 | `devices:` 条目必须相对、解析后位于插件目录内；拒绝 `..`、绝对路径、目录外 symlink（`src/rui/plugins/loader.py`） |
| 大小 / 编码 | 每个 manifest ≤1 MiB；UTF-8；顶层 mapping；YAML 安全加载；空文件报错 |
| 未知字段 | 除 `protocol` 外 `extra=forbid`；报 `文件:字段:原因`，不静默忽略 |
| `params` | key `^[a-z][a-z0-9_]*$`；`secret` 只写不读且无 `default`；`choice` 非空 `options` 且 default 在其中；`list.max` 生效；manifest 不得写 token/key 值 |
| `device_side` | `source` 只允许 http(s)/空，不得含用户名/密码；B 类缺 `rollback` 报错；内容只展示、不执行 |
| `runtime` | `entrypoint` 形如 `package.module:function`，只校验不 import；`image` 是镜像引用而非路径；`listen_port` 1–65535；`token_env` 是环境变量名 |
| 信任 | `builtin`/`trusted` 可 in-process；`third-party` 默认且只能 sidecar；`trust` 不因 `device_wake` 提升；manifest 不能声明 secret 值 |
| 版本 | `rui_compat` 是 semver 范围；`rui_min`/`tested_with` 是 semver；不匹配拒绝加载，不静默尝试 |

## 4. Adapter 契约

### 4.1 保留的现有方法

| 方法 | v1 要求 |
|---|---|
| `probe() -> DeviceInfo` | 返回实际能力与音频布局；不得乐观猜测 |
| `open()` / `close()` | 生命周期幂等；`close()` 后不得再上报事件 |
| `frames()` | 连接期间不得无限阻塞；**应当**至少每 100 ms 让出一次（可空帧），否则 `drain_wakes()` 可能被饿死 |
| `play()` / `stop_playback()` | 按实际布局播放；`stop_playback()` 停止并丢弃设备缓冲、幂等 |
| `set_volume()` / `set_led()` | 无能力时不得假装成功；`NullServices` 可抛 `NotImplementedError` |
| `drain_signals()` | 厂商助手事件；语义不变，默认 `[]` |
| `reports_consumption` / `wait_consumed()` | 可选播放头回报；v1 不改语义 |

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

**`drain_wakes()` 规则**：同步、非阻塞、不 `await`、不做网络/磁盘 I/O，单次 <1 ms；RUI 在音频面帧循环中每帧调用一次（默认 20 ms），不为 adapter 单独起线程/定时器；RUI 保证单调用者串行调用；每次返回自上次调用以来的全部事件，不重复、`close()` 后不返回；队列必须有界（建议 32），溢出丢最旧并限频日志；可跨线程生产，但只能用锁/线程安全队列，不得从其他线程直接操作 asyncio 原语；异常由 RUI 捕获降级，不得在方法内回调 RUI。RUI 用 `getattr(adapter, "drain_wakes", None)` 调用，缺失等价 `[]`（与 `drain_signals()` 一致）。

### 4.3 与 `drain_signals()` 的关系 / 声明支持

| 通道 | 语义 | 事件 | RUI 反应 |
|---|---|---|---|
| `drain_signals()` | 厂商助手在做什么 | `kind="wake"` / `"dialog_end"` / `"playing"` | 让路/静默窗口；`wake` 是**厂商**唤醒 |
| `drain_wakes()` | 设备检测到**我们的**唤醒词 | `DeviceWake` | 启动/打断**我们的** turn |

- 两条通道**不得合并**；不得把 `DeviceWake` 塞进 `VendorSignal.kind`，也不得把厂商唤醒伪装成 `device_wake`。
- 每个帧 tick 先 `drain_signals()` 后 `drain_wakes()`；同 tick 同时出现厂商 `wake` 与 `device_wake` 时，**厂商让路优先**，`device_wake` 记 `device_wake_conflict` 丢弃。
- 启用条件必须同时满足：manifest 声明 `device_wake` 且 `wake.source: device`；`probe()` 含 `device_wake`（及 `barge_via_device`）；adapter 实现 `drain_wakes()`。任一缺失：manifest 不合法则拒绝；运行时缺失则降级 KWS 并记一次日志。

adapter 只实现 `drain_wakes()`；音频面到控制面的 `DeviceWakeMsg` 是 RUI 内部消息（`src/rui/audio/protocol.py`），字段对齐 `DeviceWake`，只传事件、不做策略。

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

**VAD 门是硬要求**：`device_wake` 只负责“打开耳朵”，不负责“提交问题”；没有后续语音（`wake_idle`）就关会话，不产生 turn、不产生 ASR/LLM 成本。事件到达后回放 `wake_preroll_ms` 并 arm gate；`device` 来源的 `wake_skip_ms` 默认 **0**（不同于 RUI KWS 的 300 ms，设备延迟用 `ts_ms` 对齐，必要时配置补偿）。barge 的“播放中”以 RUI 自己的 `_speak_queued_s`/播放任务为准；设备说 barge 但 RUI 已空闲时按空闲唤醒处理，不执行 stop。

**barge 允许条件**（全部满足）：`voice.barge_in: true`（`src/rui/config/schema.py`；半双工/关闭打断时 barge 直接抑制）；不在厂商静默窗口、硬件/ACK mute 或 gate hold 内；manifest `wake.barge_via_device: true` 且能力已生效；事件 `barge=true`；RUI 确认正在播放；未触发去抖/限频。任一条不满足则**不停止播放**，只记录建议。停止路径与 KWS barge 共用：flush + `stop_playback()` + 暂停音乐；服务端停推目标 ≤300 ms（设备 ALSA 缓冲另计），trace 记 `silence_after_ms`。

**去抖与限频**：

| 规则 | 默认 | 行为 |
|---|---|---|
| 同事件去抖 | 500 ms | `ts_ms` 重复或与上次接受差 < `debounce_ms` → 丢弃，`device_wake_debounced++` |
| 单设备限频 | 5 次 / 10 s | 超出丢弃，`device_wake_rate_limited++`；不回退 KWS（避免双 turn） |
| 无效事件 | — | 不更新“上次接受”时间，避免坏事件堵住好事件 |
| 同 tick 冲突 | — | 厂商 `wake` 优先；`device_wake` 丢弃计数 |

**无效条件**：`score`/`threshold` 非有限、`score` 不在 0–1、`threshold<=0`、`score<threshold`、`ts_ms` 过期 >1500 ms 或超前 >500 ms、`source!="device"`、`barge` 无能力、`level` 非有限。处理：丢弃、计数、限频日志；**不抛异常、不停 KWS**。

| 条件 | 行为 |
|---|---|
| 未声明 `device_wake` | RUI KWS 完全不变（LX06） |
| 声明但 probe/方法缺失 | 启动警告一次；`device_wake_fallbacks++`；KWS 继续 |
| `drain_wakes()` 抛异常 | 捕获；30 s 只记一次；KWS 继续；连续失败可标记本会话降级 |
| 有效事件但 VAD 不可用 | 不产生 turn；KWS 继续；`device_wake_no_vad++` |
| 有效事件但无后续语音 | `WakeIdle` 关会话；不回退 KWS（避免重复 turn） |
| 连续 10 个无效事件 | 本会话标记 `device_wake_degraded`，KWS 继续；重启/重连后重试 |

**指标/trace**：`Metrics` 新增 `device_wakes`、`device_wake_turns`、`device_wake_barges`、`device_wake_debounced`、`device_wake_invalid`、`device_wake_suppressed`、`device_wake_fallbacks`、`device_wake_last_score`；`wake_count` 仍只统计 KWS。`TurnTrace.meta` 写 `wake_source`、`device_wake_score`、`device_wake_threshold`、`device_wake_barge_hint`、`device_wake_action`、`device_wake_debounced`。日志只记事件与决策，不记原始音频、token、完整设备地址。

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
| 8 | 发布 tag + checksum；声明 `rui_compat` | 可复现安装；回滚说明齐全 |

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

工具是开发/验收工具，不是运行时；第三方 adapter 在子进程/fake 环境加载，不进核心。

### 6.3 验收标准

建议值（待 §8 Q7 确认）：manifest validator 全绿；注入 20 次空闲唤醒 + 有语音 → ≥19 次形成 turn，静音 20 次 → 0 turn；播放中 `barge=true` 20 次 → `silence_after_ms ≤300 ms`；issue #1 的 400 ms 双批源 → `max_gap_ms <100`、`underruns=0`，额外延迟短回复 ≤150 ms、长回复可配 ≤300 ms；无 `device_wake` 的 LX06 行为不变；A 类完成厂商让位/接管，B 类完成回滚演练。

### 6.4 dot2 检查单

控制/数据两条 socket 若存在，起播/flush 屏障按 `docs/playback-path.md` §6.4 的 P3 设计并通过 gap harness；无 `reference` 合法（依赖设备侧 AEC）；`wake: {source: device, barge_via_device: true}` + 对应能力，`drain_wakes()` 映射 EchoMuse `oww_wake` 的 `score/threshold/ageMs/capturedMono/barge/level`；barge 只是建议，RUI 按 §5 最终判定；0.3 前可先用 v0 manifest 跑 KWS，`drain_wakes()` 可先实现但不会被 0.2 调用。

### 6.5 参考插头：LX06（`plugins/rui-plugin-xiaoai/`）

P3.1 把已内置的 LX06 能力落成一个可被 `load_plugin()` 读取的**参考插头**，用来证明 v1 契约不需要核心品牌分支。它**不是新的运行时路径**：0.2.x/0.3.x 仍以内置 manifest + `src/rui/device/xiaoai/` 为默认，参考包只用于对照和 kit 验收；同 id 时 builtin 优先。

| 部件 | 内容 | 对应规范 |
|---|---|---|
| `plugin.yaml` | id `xiaoai-lx06`，trust `builtin`，`rui_compat: ">=0.2 <0.4"` | §3.1 / §3.2 |
| `devices/lx06.yaml` | `rui-device/v0`，class A，inbound，`ws`，in-process，能力 `mic_array` / `reference` / `playback` / `led` / `keys` / `volume`，参数 `host` / `room` / `wake_words` | §3、§7 |
| `rui_plugin_xiaoai/adapter.py` | `build(spec, config)` 转调 `rui.device.xiaoai.build`，不复制 LX06 行为 | §4.1 |
| `wake` | 无；v0 默认 `source: rui`，LX06 继续走 RUI KWS | §2、§7 |

```bash
python -m rui plug-test plugins/rui-plugin-xiaoai
python -m rui plug-test plugins/rui-plugin-xiaoai \
  --adapter rui_plugin_xiaoai.adapter:build --json
```

预期：`manifest.*`、`adapter.import/construct/methods`、`device.ready/probe_layout`、两个 `poll.*`、`lifecycle.*` 通过；`device.open`、`capture.frames`、`capture.cadence`、`playback.sink` 记为 **SKIP**，因为 LX06 是 inbound，必须等 open-xiaoai client 拨入才能跑帧/播放；`wake.inject` 也 SKIP（v0/RUI KWS）。这些 SKIP 由 adapter 的 kit-only 提示 `kit_live_device_required` 标明（`docs/plug-test-kit.md`），不改变生产行为、不伪造设备，也不把 SKIP 记成 PASS。

0.4 才把 `src/rui/device/xiaoai/` 提取进本包；见 §7。

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
| 验收 | 手工 + 既有 pytest | 增加 manifest validator、wake injection、playback gap 工具 |

**兼容规则**：0.3 loader 接受 v0，按 `wake.source=rui`、`barge_via_device=false` 处理；v0 不得声明 `wake`/`device_wake`（否则报错，不静默升级）。LX06 内置 manifest 继续用 v0，`src/rui/device/xiaoai/` 不实现 `drain_wakes()`，KWS 路径不变。v0 + 新字段、v1 + 未知字段、`rui_compat` 不匹配都报文件/字段。`devices[].params` 仍是值字典，`params.plug_id` 继续作为 adapter→plug 兜底（`src/rui/plugins/resolve.py`）。sidecar 只标准化 RUI 侧代理 adapter 的 `drain_wakes()`；sidecar 内部 RPC 由插头定义，v1 不标准化（§8 Q8）。

**版本协商**：

| RUI | manifest | 结果 |
|---|---|---|
| 0.2.x | v0 | 正常加载；无 `device_wake` |
| 0.2.x | v1 | 拒绝 schema；向导显示不兼容 |
| 0.3.x | v0 | 正常加载；RUI KWS |
| 0.3.x | v1 | 正常加载；按 §5 启用 |
| 任意 | `rui_compat` 不匹配 | 拒绝；显示要求范围与当前版本 |

`rui_compat` 是唯一插件级版本门；能力可用性在运行时按 §4.3 判定，不用版本号猜能力。dot2 v1 发布建议 `rui_compat: ">=0.3.0 <0.4.0"`；0.3 前用 v0 分支/开发分支验证，不把 v1 manifest 发给 0.2 用户。

**弃用**：`rui-device/v0` 在 0.3.x 继续支持，最早 0.5.0 移除，且至少提前一个 minor 在日志/控制台提示；`native_kws` 自 v1 弃用；`streaming_asr` 保留为未定义值；`VendorSignalMsg`/`drain_signals()`/厂商让路窗口不弃用、不改语义；任何字段移除都“先警告、后 minor、再移除”，不得在 patch 移除。

**0.4 提取计划（P3.1 只留参考包，不搬代码）**：`plugins/rui-plugin-xiaoai/` 现在是可验收的参考插头；`src/rui/device/xiaoai/` 与 `src/rui/plugins/builtin.py` 的内置 manifest 仍是 0.2.x/0.3.x 的默认回退（同 id 时 builtin 优先）。0.4 计划：

1. 把 `src/rui/device/xiaoai/` 的实现移入插件包（或独立发行包），核心 registry 只保留发现与兼容 shim；
2. 把 `builtin.py` 的内置 manifest 改为“参考包缺失时的最小回退”，至少提前一个 minor 在日志/控制台提示迁移；
3. adapter entrypoint 从 `rui.device.xiaoai:build` 切到 `rui_plugin_xiaoai.adapter:build`，`devices[].adapter` 仍为 `xiaoai`，配置不变。

v0 schema 的最早移除仍是 0.5（见上），0.4 不删除 v0 支持。本切片不移动核心目录、不改默认行为；参考包仍通过 `rui_plugin_xiaoai.adapter:build` 转调核心 builder。

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
- 不做签名强制/安全认证/渗透测试；v1 信任仍是 allowlist + 人工确认。
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
