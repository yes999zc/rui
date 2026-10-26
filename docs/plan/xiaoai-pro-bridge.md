# 项目：小爱音箱 Pro bridge 剥离为独立项目

来源：9 哥 2026-10-26「小爱音箱 Pro bridge 剥离成独立项目，也可以做了」。

## 背景
- 内核已无小爱（`tests/test_kernel_decoupling.py` 守护）；小爱只剩 `src/rui/plugs/xiaoai-lx06/`（in-tree plug）+ `adapter_aliases.py` 别名。
- Dot2 已经跑通「out-of-tree 仓库 + pinned 镜像 + 下游 Dockerfile」的模式（`rui-dot2-bridge`），可作为模板。

## 目标形态
- 新仓库（kebab-case，未定名，建议 `rui-xiaoai-bridge`）：只含 LX06（及后续小爱机型）的 adapter/协议实现 + `plugin.yaml` + devices 清单 + 自己的 CI/发布；
- rui 侧留 `plugs/` 扩展点与最小 stub，删 `adapter_aliases.py`（与 0.7 里程碑合并做）；
- 兼容窗口：bridge 声明 `rui_compat`，rui 侧只认 `device` contract；
- 发布顺序：bridge 先发兼容版本 → rui pin 住 → 再删 in-tree。

## 待 9 哥 决策
1. 仓库名与是否公开；
2. 是否把 `deploy/downstream` 那套（Dockerfile + 镜像 pin）直接复制，还是抽成共享模板；
3. 剥离后 in-tree plug 是保留为「参考实现」还是彻底删除。

## Phase 1 进度（2026-10-26，已完成）

### 做了什么
- 独立仓库：<https://github.com/yes999zc/rui-xiaoai-bridge>（**private**），
  `main @ 6f5f4dd`，tag **v0.1.0**（CI 绿：main + tag）；未建远端前已 `git init`，`gh repo create --private` 后 push。
- 布局：`src/rui_xiaoai_bridge/`，manifest 与包同目录
  （`plugin.yaml`、`devices/lx06.yaml`、`adapter.py`、`protocol.py`、`led.py`、`services.py`），
  让 `RUI_PLUGINS_DIR` 扫描、co-located manifest 查找、data_dir 安装三条路径看到同一份清单。
- `plugin.yaml` 从 rui 实际的 `plugs/xiaoai/plugin.yaml`（非本文背景里写的
  `src/rui/plugs/...`）抬出：id 保持 `xiaoai-lx06`，`rui_compat` 抬到
  `>=0.6.0 <0.7.0`，`trust: trusted`（out-of-tree，不再自称 builtin），
  `audio_defaults` / `params` / `device_side` 原样保留。
- `devices/lx06.yaml` 仅改三处：`runtime.entrypoint` → `rui_xiaoai_bridge.adapter:build`、
  `compatibility` 窗口（`rui_min: 0.6.0`）、acceptance 证据；`audio_defaults`
  （8ch / 48kHz / 6 mic / 2 ref / shifts / ack 1200ms）逐字保留。
- adapter 四文件从 rui `4f00ef5`（0.6.0）复制，保留 MIT 头 + 每文件 provenance
  注释；线协议来自 open-xiaoai（MIT），没有复制 Apache-2.0 代码。
- tests 7 条（import / manifest / catalog 语义）；`docs/contract-report-0.6.0.json`；
  `deploy/downstream/Dockerfile` 预览镜像 + `deploy/install-plug-manifest.sh`；
  CI（ruff + pytest + 官方 plug-test，rui pin `v0.6.0`）。
- **明确未做**：未改 rui 内核 / `pyproject.toml`，未删 `plugs/xiaoai/`，未动
  `adapter_aliases.py`。

### 验证（真实命令与关键输出）
在 rui checkout（`4f00ef5`，venv Python 3.13.13）里执行，`$BRIDGE` 为新仓库路径：

1. 官方契约测试（manifest + adapter 面）：
   `PYTHONPATH=$BRIDGE/src python -m rui plug-test $BRIDGE/src/rui_xiaoai_bridge --adapter rui_xiaoai_bridge.adapter:build`
   → `summary: pass=16 fail=0 warn=0 skip=5 -> PASS`
   （`manifest.load/devices/compatibility/runtime`、`adapter.import/construct/methods`、
   `device.ready/probe_layout` 全 PASS；5 个 SKIP 是需要真机 open-xiaoai client
   的 `device.open/capture.frames/capture.cadence/playback.sink/wake.inject`）
   交叉验证：在 **发布 tag `v0.6.0`**（CI 安装的版本）上为
   `pass=15 fail=0 warn=1 skip=5 -> PASS`；唯一 WARN 是 `device.probe_layout`
   （tag 的 contract kit 用假 config，读不到 manifest 的 audio_defaults，
   probe 报 1 mic / 0 ref），非 adapter 失败。
2. 单测与 lint：
   `python -m pytest -q` → `7 passed`；`ruff check .` → `All checks passed!`
3. `GET /api/v1/plugs`（TestClient，user 安装路径，`RUI_PLUGINS_DIR` 指向空目录）：
   → `source=user available=true trust=trusted adapter=lx06 devices=['lx06']`；
   detail 里 `runtime.entrypoint=rui_xiaoai_bridge.adapter:build`、
   `audio_defaults.capture={rate:48000, channels:8, mic_channels:6, ref_channels:2}`。
   验收脚本：`pytest -q /tmp/test_xiaoai_bridge_integration.py` → `2 passed`。
4. 运行期注册：
   - `RUI_PLUGINS_DIR=$BRIDGE/src` → `registry.available()` 含 `lx06`（`xiaoai`
     别名仍由 rui 提供），runtime defaults
     `{listen_port:4412, token_env:RUI_DEVICE_TOKEN}`。
   - user 路径 `RUI_PLUGINS_DIR=<empty> RUI_DATA_DIR=<data>
     RUI_ADAPTER_PLUGINS=lx06=rui_xiaoai_bridge.adapter:build`
     → `registered: {'lx06': 'RUI_ADAPTER_PLUGINS ...'}`，`warnings: []`。
5. wheel 实测包含 `plugin.yaml` + `devices/lx06.yaml`（`uv build` 后 `unzip -l`）。

### 与 in-tree 的共存结论（0.6.x，in-tree 仍在时）
- 同 plugin id：**builtin 胜出**。user 副本不会被静默丢弃，`GET /api/v1/plugs`
  额外返回一条 `available=false`：`duplicate plugin id 'xiaoai-lx06'; the image
  plug wins`；实测 winner 为 in-tree `0.3.0`（source=builtin）。
- builtin adapter 名不可覆盖：in-tree 注册 `lx06` 后，
  `RUI_ADAPTER_PLUGINS=lx06=...` 会被 `BuiltinAdapterName` 拒绝（只留 warning）。
  所以「只把 user manifest 装进 data_dir」在 0.6.0 官方镜像里**不生效**。
- Phase 1 预览镜像走显式优先级：本插件放独立 `RUI_PLUGINS_DIR` 根
  （`/opt/rui-xiaoai-plugins`，env 根先于镜像根扫描）。用
  `RUI_PLUGINS_DIR="$BRIDGE/src:<rui>/plugs"` 实测 winner 为本仓库 `0.1.0`，
  in-tree 变成 `duplicate plugin id ... skipped` warning；不依赖目录字母序。
- 正解仍是 Phase 2 删 in-tree，冲突从根上消失。

### 下一步 Phase 2
1. 真机回归（open-xiaoai client）：唤醒 / 播放 / barge / 音量 / 灯环，补
   `acceptance` 与 report；Phase 1 只验证了 host-free 契约。
2. rui 侧删除 `plugs/xiaoai/`，更新 `tests/test_xiaoai_plug.py` 与
   `pyproject.toml` 的 `pythonpath`；0.7 再删 `adapter_aliases.py`。
3. 下游镜像 publish + digest 回写；Phase 2 后去掉额外的 `RUI_PLUGINS_DIR` 根，
   清单回到 `/opt/rui-plugins`。
4. 配置迁移：`adapter: xiaoai` → `adapter: lx06`（0.6.x 靠别名过渡）。
