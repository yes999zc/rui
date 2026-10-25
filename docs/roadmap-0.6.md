# rui 0.6.0 路线图：多音箱完成 + 小爱剥离

> 目标（9 哥 定义）：**多音箱系统搭建完毕，小爱音箱从 rui 内核剥离干净。**
> 完成定义（DoD）见 §1；每个里程碑都有可复现判据，不满足不进下一个。
> 本文件取代 plan-to-0.6.md 作为施工路线（plan 保留为背景与 0.5.3 记录）。

## 1. 0.6.0 的完成定义（DoD）

1. **内核零设备代码**：src/rui 下不再有 xiaoai 专属实现；grep xiaoai 只剩「兼容别名 + 文档」两处白名单。
2. **LX06 是 plug**：plugs/xiaoai 走与 dot2 相同的 rui-device/v1 manifest + RUI_ADAPTER_PLUGINS 加载；现有安装 adapter: xiaoai 零迁移。
3. **中性默认**：核心默认 capture 16000/1ch/0ref、playback 16000、ack earcon；LX06 的 8ch/6mic/2ref 由它自己的 manifest 的 audio_defaults 声明。
4. **多音箱完整**：session / output owner / reminder / music 全部按房间；向导第一步选设备；控制台逐设备可见。
5. **真机验收**：LX06 + 2×Dot2 共存，各自唤醒、同词单 turn、barge 归属、故障隔离、回滚，全部通过。
6. **下游跟版**：dot2 plugin 1.2.0（rui_compat <0.7.0），plug-test on 0.6.0 = 15/0/2/5。

## 2. 里程碑与任务

### M0 — 0.5.4 等价：Dot2 自听收口（dot2 侧，1 天）

- 背景：0.5.3 已给 rui 侧 ack_echo_ignore_ms，但 vad.hold 的 buffer-and-replay 会把回声回放成请求；根因在「播放期间麦克风仍开着」。
- 任务：dot2 adapter 默认在**播放期间静音麦克风**（PLAYBACK_LIVE_MS=0 + 播放后 gate 2500 ms），barge 继续走设备唤醒事件；plugin 1.1.3。
- 判据：.34 合成客户端回灌 cue 音，窗口内 0 turn；真机连续 5 次唤醒 0 次自听。
- 注意：**不需要 rui 发 0.5.4**（rui 0.5.3 已含设备级窗口），只有 dot2 镜像要重建。

### M1 — 0.6 契约与中性默认（rui，3–4 天）

- manifest `rui-device/v1` 增 `audio_defaults: {capture, playback, ack_echo_ignore_ms?}`；`runtime` 不变。
- 核心默认改中性；启动时「设备默认 + 全局配置 + devices[].audio 覆盖」三层合成，缺一层不报错。
- 兼容 shim：没有 audio_defaults 的旧 plug 用中性默认；xiaoai 的 manifest 声明 8ch/6mic/2ref/16000。
- 判据：现有 config v3 原样启动；LX06 链路单测全过；新字段 schema/catalog/doctor 测试；plug-test 旧报告不回归。

### M2 — 小爱 plug 化（rui，3–4 天，依赖 M1）

- `src/rui/device/xiaoai/` → `plugs/xiaoai/`（镜像自带，manifest 加载），`adapter: xiaoai` 保持不变。
- 删除 core 硬引用：plugins/builtin、registry、catalog 文案、web 默认值、setup 默认设备；保留一个 minor 的白名单别名。
- 判据：`git grep -l xiaoai src/` 只剩白名单；全量测试；旧 config 安装零迁移；镜像内 plug 列表含 xiaoai 与 dot2。

### M3 — 全屋路由与控制台（rui，3–4 天，依赖 M1）

- session / owner / reminder / music 全部按房间：告别、提醒、音乐、停止都落到发起房间或 default_output。
- 向导第一步：从已安装 plugs 选设备；控制台「设备与音频」逐设备状态、连接测试、音频覆盖。
- 判据：两设备 mock 集成测试（告别房间、提醒房间、音乐房间、barge 归属）；控制台 typecheck+build；真机 8 项中的非硬件项通过。

### M4 — 0.6.0 发布与真机验收（4–5 天，依赖 M2、M3、dot2 1.2.0）

- dot2 1.2.0：rui_compat <0.7.0 + manifest audio_defaults + 回归测试；plug-test on 0.6.0。
- .34 金丝雀 2 天（合成客户端 + 真 Dot2），生产灰度（先 0.6.0 一台 LX06，再加 Dot2），最后 LX06+2×Dot2 真机验收。
- 判据：DoD 1–6 全绿；回滚演练；release notes 与 CHANGELOG 更新。

## 3. 依赖与顺序

```
M0 (dot2 1.1.3)  ──┐
                  ├─> M4 (0.6.0) ──> 真机验收
M1 (契约/默认) ──┬─┴─> M2 (小爱 plug 化)
                 ├─> M3 (全屋路由)
                 └─> dot2 1.2.0 (M3 并行)
```

## 4. 每步的固定门禁

- 全量 pytest / ruff / sh -n / web；
- 改动带回归测试，没有测试不合并；
- 一个里程碑一次提交批次、一次发布；不按小修重建镜像；
- 真机/生产变更前备份，切换后可一键回滚。

## 5. 风险与应对

| 风险 | 应对 |
|---|---|
| LX06 回归（plug 化） | 保留兼容别名一个 minor；先 .34 金丝雀再生产；旧 config 零迁移测试 |
| 契约改动打断下游 | audio_defaults 可选；旧 plug 不声明时走中性默认；dot2 1.2.0 同批发布 |
| 真机窗口不足 | 合成客户端覆盖管线；真机只验收硬件相关（麦克风/AEC/音量/LED） |
| 上下文/人力 | 每个里程碑派独立实现者，Lead 只做评审/门禁/发布；状态落盘，随时可 fork |

## 6. 现在开始的第一件事

M0（dot2 1.1.3 播放期静音）与 M1（契约 + 中性默认）可以并行开工；M1 先落地契约，M2/M3 才能动。
