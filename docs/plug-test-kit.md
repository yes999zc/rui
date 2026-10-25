# Plug 契约测试工具（P3.1）

`docs/socket-plug-v1.md` §6.2/§6.4 的落地工具。给**插头作者**（dot2 / Echo Dot 2 等）在
自己的仓库里验证 `plugin.yaml`、`devices/*.yaml` 和可选 adapter，不需要改 RUI 核心、
不需要真机、不需要真音频设备。

工具只做两件事：

1. **manifest 验收**：读 `plugin.yaml` / `devices/*.yaml`，调用现有
   `rui.plugins.loader.load_plugin()`（只读、不 import `entrypoint`），再按 v1 §2 检查
   capability / `wake` / runtime / trust 规则；错误输出 `文件:字段:原因`。
2. **adapter 冒烟**（仅 `--adapter` 时）：懒加载 Python entry point，用 fake device /
   audio sink 跑 probe、帧、播放、`drain_signals()` / `drain_wakes()`、一次安全唤醒注入、
   幂等 close。它不是 turn/barge 策略测试，不调用 ASR/LLM/TTS。

## 快速开始

```bash
# 只验 manifest（CI 默认）
python -m rui plug-test path/to/rui-plugin-dot2

# 同时验 adapter（in-process；开发/验收用）
python -m rui plug-test path/to/rui-plugin-dot2 \
  --adapter path/to/rui-plugin-dot2/adapter.py:build

# 机器可读报告，可直接作为 acceptance.evidence 附件
python -m rui plug-test path/to/rui-plugin-dot2 \
  --adapter path/to/rui-plugin-dot2/adapter.py:build \
  --json > contract-report.json
```

安装后也可以直接运行 `rui plug-test ...` 或 `rui-plug-test ...`（`pyproject.toml`
里的 console script）。

## 下游镜像配方（issue #7 Q1）

0.3.x 的官方钩子是 `RUI_ADAPTER_PLUGINS`（语义见 `docs/socket-plug-v1.md` §4.4）。
官方镜像 + 只加 adapter 层的 downstream 镜像是支持路径：**不 fork 核心**，升级时
只重建 adapter 层。

```dockerfile
# 固定补丁 tag；下面 0.3.x 只是示意，生产请写具体版本（如 0.3.0）
FROM ghcr.io/yes999zc/rui:0.3.x

# 插件代码**和 manifest** 随镜像发布；不要从用户/设备可控路径动态导入
COPY my_plugin /opt/rui-plugins/my_plugin

ENV PYTHONPATH=/opt/rui-plugins
# adapter 名默认取模块名；显式命名时写成 dot2=my_plugin.adapter:build
ENV RUI_ADAPTER_PLUGINS=my_plugin.adapter:build
```

**manifest（必需）**：0.3.2 起 env/entry point 是 manifest-gated。`plugin.yaml` 必须
随 adapter 模块/包放在同一目录（`_module_plugin_roots` 会向上找一层），或安装到
`<data_dir>/plugs/*` 并设置 `RUI_DATA_DIR=<data_dir>`；manifest 的 `trust` 必须是
`builtin` 或 `trusted`。没有 manifest 的 entry 只记 warning、不注册；`third-party`
不得 in-process；`mock` 与镜像自带插头的名字不可覆盖。

镜像自带插头（0.6 M2）：把目录放到 `/opt/rui-plugins/<plug>/`（manifest + 可选
adapter 包），核心的 image-plug catalog 会扫描它，所以 `GET /api/v1/plugs`、向导和
`rui doctor` 都能看到，不再需要复制到 `<data_dir>/plugs`。开发时可用
`RUI_PLUGINS_DIR`（冒号分隔）追加别的根目录（镜像的 `/opt/rui-plugins` 始终保留，
不会被覆盖掉）；官方镜像自己就是这么加载
`plugs/xiaoai` 的，adapter 由 manifest 的 `runtime.entrypoint` 注册，不需要
`RUI_ADAPTER_PLUGINS`。下行镜像继续用 `RUI_ADAPTER_PLUGINS` 注册自己的 adapter。

`devices[].adapter` 必须与注册名一致。上例未写 `name=`，注册名就是模块名
`my_plugin.adapter`；推荐写成 `ENV RUI_ADAPTER_PLUGINS=dot2=my_plugin.adapter:build`，
配置里用 `adapter: dot2`。builder 签名与内置相同：`build(dev, cfg) -> DeviceAdapter`。
`build(dev=None, cfg=None)` 这种“所有参数都有默认值”的 callable 仍是 per-device
builder，启动时不会被调用；只有零参数（或显式 `factory` 角色）才是工厂。

构建/升级：

```bash
docker build -t my-registry/rui-dot2:0.3.0 .
docker compose up -d          # compose 里把 image 换成这个 tag
```

**manifest / 信任**：adapter 在 RUI 进程内导入，等价于允许执行代码，`RUI_ADAPTER_PLUGINS`
只能由 root/容器运维方设置，**不得**接受用户输入或 `devices[].params`；插件能读到容器
环境变量（含密钥），镜像与插件必须由你方审计、维护。manifest 的 `trust` 是自述元数据，
真正的门是“运维方把代码+manifest 放进镜像/数据目录”；0.3.2 的机械门要求 manifest
存在且 `trust: builtin|trusted`，`third-party` 不得 in-process，内置名不可覆盖。
`rui plug-test` 会验收 manifest 与 adapter 表面：

```bash
python -m rui plug-test /opt/rui-plugins/my_plugin \
  --adapter /opt/rui-plugins/my_plugin/adapter.py:build
```

0.4 会把该钩子收敛为正式插件加载（trusted in-process entrypoint / third-party
sidecar runner）；当前 env 是 0.3.x 的稳定过渡接口。**0.3.x 不能运行 sidecar**：
`runtime.mode: sidecar` 能通过 manifest 校验，但向导和 `build_adapters()` 都会返回
明确错误，不会静默改成 in-process。

本仓库自带一个能跑通的参考插头：

```bash
python -m rui plug-test examples/plugin-example
python -m rui plug-test examples/plugin-example \
  --adapter examples/plugin-example/rui_plugin_example.py:build --json
```

真实设备的参考插头（LX06，v1/RUI KWS）就是镜像自带的 `plugs/xiaoai/`
（0.6 M2：核心不再有 `src/rui/device/xiaoai/`）：

```bash
python -m rui plug-test plugs/xiaoai
python -m rui plug-test plugs/xiaoai \
  --adapter xiaoai_plug.adapter:build --json
```

预期 16 PASS / 0 FAIL / 0 WARN / 5 SKIP。它是 inbound 设备：`probe()` 和 adapter
表面可在无音箱时验收，`open()`/帧/播放在 client 拨入前不存在，因此这些 live-I/O
检查为 SKIP；详见下文 `kit_live_device_required`。退出码仍是 `0`。
（Dot 2 那种 `device_wake` 插头的报告是 15/0/2/5：多出的两条 WARN 来自
`device.probe_layout` 与 `device.device_wake_effective`，不是所有插头的固定目标。）

退出码：`0` = 没有 `FAIL`（`WARN`/`SKIP` 不阻断）；`1` = 至少一个 `FAIL`，或目录/
adapter 无法读取/加载。

## 检查项

| check id | 级别 | 内容 |
|---|---|---|
| `manifest.load` | FAIL | 目录、YAML、schema、字段、路径逃逸、`rui_compat`；由现有 loader 校验 |
| `manifest.devices` | FAIL | 至少一个机型、id 唯一 |
| `manifest.compatibility` | FAIL | `rui_compat` 与当前 RUI 版本匹配 |
| `manifest.capabilities` | FAIL/WARN | 8 个规范值、重复、`native_kws`（v1 弃用）、`barge_via_device` 依赖 |
| `manifest.wake` | FAIL | `device_wake` ↔ `wake.source: device`、barge 依赖、未知字段、v0 不得带 wake |
| `manifest.wake_words` | WARN | `source: device` 时没有 `params.wake_words` 或 install_steps 提示 |
| `manifest.runtime` | FAIL/WARN | third-party 不得 in-process；sidecar 需 image；in-process 需 entrypoint |
| `adapter.import` | FAIL | 懒加载 `module:attr` / `path/to/file.py:attr` |
| `adapter.construct` | FAIL | 支持 `build()`、`build(device)`、`build(device, config)` |
| `adapter.methods` | FAIL | `probe/open/close/frames/play/stop_playback/set_volume/set_led/drain_signals/drain_wakes`；两个 drain 必须同步。音量：`set_volume` 0–100；`services.set_volume/read_volume` 0–255（kit 目前只检查方法存在，生产以 `services` 为准） |
| `device.ready` | FAIL | `probe()` 返回有效 id/channels/sample_rate |
| `device.probe_layout` | FAIL/WARN | `channels == mic_channels + ref_channels`、manifest ∩ probe 降级提示 |
| `device.open` | FAIL | `open()` 可完成 |
| `capture.frames` | FAIL | 3 帧异步帧，PCM 长度/通道/采样率/时间戳合法 |
| `capture.cadence` | FAIL/WARN | 帧间隔 >500 ms 失败；>100 ms 警告（§4.1） |
| `playback.sink` | FAIL/SKIP | `play()` 接受 20 ms PCM（探测布局为多声道时用 2ch，否则 1ch），fake sink 收到相同字节；`stop_playback()` 两次幂等 |
| `poll.drain_signals` | FAIL/WARN | 两次调用返回 list；队列/耗时异常时 WARN |
| `poll.drain_wakes` | FAIL/WARN | 同上，且注入前为空 |
| `wake.inject` | FAIL/SKIP | 调用 fixture 的 `inject_wake()`（kit-only hook） |
| `wake.idle_trigger` | FAIL | 事件字段合法（finite、0–1、threshold>0、source=device、时间窗），空闲分类为 `arm_gate` |
| `wake.drain_once` | FAIL | 第二次 drain 不重复返回同一事件 |
| `wake.barge_policy` | FAIL/SKIP | 播放中 `barge=true` 且能力生效 → `barge_stop`；只是建议，策略仍归 RUI |
| `lifecycle.close` | FAIL | `close()` 幂等 |
| `lifecycle.after_close` | FAIL | `close()` 后 `drain_wakes()` 为空 |

## JSON 报告

`--json` 输出 `contract-report/v1`，字段固定：

```json
{
  "schema": "contract-report/v1",
  "kit": "rui-plug-test",
  "rui": "0.3.0",
  "plugin": {
    "dir": "...",
    "id": "dot2",
    "trust": "third-party",
    "loader": "core",
    "devices": [{"id": "dot2", "schema": "rui-device/v1", "capabilities": ["..."], "wake": {"source": "device"}}]
  },
  "adapter": "adapter:build",
  "checks": [{"id": "manifest.wake", "status": "PASS", "detail": "..."}],
  "summary": {"pass": 24, "fail": 0, "warn": 0, "skip": 0, "verdict": "pass"},
  "warnings": []
}
```

`status` 只会是 `PASS` / `FAIL` / `WARN` / `SKIP`；`summary.fail == 0` 与退出码 0 等价。

## Adapter entry point 约定

`--adapter` 支持两种写法：

- `package.module:attr`：先正常 `importlib.import_module()`；若模块只存在于插件目录
  （例如 `adapter.py` 与 `plugin.yaml` 同级），再用插件目录临时重试一次。也支持
  `PYTHONPATH` / 已安装包。
- `./adapter.py:build` / `examples/plugin-example/rui_plugin_example.py:build`：按文件路径
  懒加载；加载期间把其父目录临时加入 `sys.path`，结束后恢复。适合独立示例和 CI。

`attr` 可以是 adapter 实例、类、或 factory。factory 按**位置参数个数**调用：0 个
→ `build()`；1 个 → `build(fake_device)`；2 个及以上 → `build(fake_device, fake_config)`
（必填位置参数超过 2 个直接 FAIL）。`fake_device` 至少有 `id`、`adapter`、`model`、
`host`、`room`、`params`。

**可选注入 hook**（不属于 `DeviceAdapter`，只给契约工具用）：

```python
def inject_wake(self, *, ts_ms=None, score=0.9, threshold=0.5,
                barge=False, level=None) -> None: ...
```

没有这个 hook 时 `wake.inject` / `wake.idle_trigger` 记为 `SKIP`（不会 FAIL）；如果
adapter 来自 sidecar 容器，可以只跑 manifest，或在 RUI 侧代理 adapter 上实现该 hook。
真实 adapter 应由设备侧唤醒引擎把事件推进 `drain_wakes()`，不要在生产代码里暴露这个
hook 给外部调用者。

参考 fixture：`examples/plugin-example/rui_plugin_example.py`（16 kHz 单声道 fake，
`drain_wakes()` 有界队列 32，`inject_wake()` 只做测试注入）。

**可选 live-device 提示**（不属于 `DeviceAdapter`，只给契约工具用）：

```python
kit_live_device_required = True
kit_live_device_reason = "inbound: wait for the real client to dial in"
```

inbound 插头（如 LX06）的 `probe()` 和 adapter 表面可以在没有设备时验收，但
`open()`/`frames()`/`play()` 要等真实 client 拨入。设置该提示后，`device.open`、
`capture.frames`、`capture.cadence`、`playback.sink` 记为 `SKIP`（不是 PASS），
`probe()`、方法表面、两个 drain 和幂等 `close()` 仍照常检查。提示没有运行时含义，
也不改变 `DeviceAdapter`；不要在提示里写地址、token 或 secret。参考实现：
`plugs/xiaoai/xiaoai_plug/adapter.py`。

## CI（第三方插头仓库）

```yaml
name: rui-plug-contract
on: [push, pull_request]
jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: python -m pip install "rui==0.3.*"
      - run: python -m rui plug-test . --json > contract-report.json
      - run: python -m rui plug-test . --adapter ./adapter.py:build --json > adapter-report.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: contract-report
          path: "*-report.json"
```

只做 manifest 的插头（没有 adapter）删掉第二条命令即可。third-party 且 sidecar 的插头
仍可跑 adapter 第二条：工具只把 RUI 侧代理 adapter 在本地进程里冒烟，不改变生产信任/
运行方式；如果代理会连真实 sidecar，请在 CI 里指向 fake 或设置
`runtime.mode: in-process` 的测试专用 manifest。

## 边界与已知缺口

- 不测 turn/barge 策略、VAD、去抖/限频、播放 gap harness；这些属于 §5 / §6.2 的其余
  harness（`playback_gap_count`、`max_gap_ms` 等），本工具只验证 adapter 表面。
- 不执行 sidecar 容器，不验证镜像/协议 RPC；只验证 manifest 与 in-process adapter。
- `WARN` 不会让 CI 失败；`device_wake` 在 `probe()` 缺失时按规范降级为 KWS，只 WARN。
- 构造 adapter 时不会传真实设备参数（host/secret）；需要真机连接才能 `probe()` 的
  adapter，请提供 fake/loopback 模式，或只跑 manifest 检查。若只有帧/播放依赖真机
  （典型 inbound 设备），可设置 `kit_live_device_required` 把 live-I/O 检查降为 SKIP；
  这是插头自述，工具不会据此把任何检查记成 PASS，真机验收仍必需。
- 旧 RUI（0.2.x）没有 v1 模型时，工具会启用 `kit-v1-compat` 兼容层校验 v1 manifest，
  报告里的 `plugin.loader` 会标明；0.3 起由核心 `load_plugin()` 作为唯一权威。
