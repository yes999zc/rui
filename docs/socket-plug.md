# Socket & Plug — 音箱适配标准 v0（草案）

> 状态：**v0 草案（2026-10-24）**，用于讨论和实现安装向导/设备管理；字段会改，先定结构。
> 一句话：**RUI 是插座，只定义规则；插头由音箱维护者交付**（设备端交付物 + manifest，可选 adapter 代码）。
> 相关：`ROADMAP` §3 P2/P3、`remaining-work.md` §I/§J/§K、`open-xiaoai-client.md`、`device-management-research.md`（EchoMuse 调研）。

## 1. 术语

| 词 | 含义 |
|---|---|
| **插座（socket）** | RUI 侧的稳定契约：`DeviceAdapter` + `Capability`/`Transport` + 连接生命周期 + 参数 schema + 安装向导插槽 + 运行时加载 + 信任策略。核心代码不出现品牌分支。 |
| **插头（plug）** | 音箱维护者交付的包：设备端交付物说明（固件/client/App）+ RUI 侧 manifest + 可选 adapter 代码 + 验收记录。 |
| **A 类音箱** | 与原厂共存、低权限：不 root、不改固件；设备上跑厂商允许的 client（如 LX06 + open-xiaoai），必须处理厂家让位/接管。 |
| **B 类音箱** | 高权限/完整控制：可 root/刷机/自启动（如 Echo Dot 2），能力更全，但需要解锁流程和回滚路径。 |
| **插入** | 用户在安装向导里选/导入插头 → 填参数 → 验证连接 → 写入 config 并启用。 |

## 2. 设计原则

1. **声明优先**：插头必须能只靠 manifest 被 RUI 理解；adapter 代码是可选增强。
2. **核心无品牌分支**：机型差异全部在插头/adapter；控制面、音频面、控制台不写 `if xiaoai`。
3. **用户动作与 RUI 动作分开**：向导每一步都标明“用户要做什么 / RUI 自动做什么”。
4. **危险动作显式确认**：刷机/解锁必须用户确认，RUI 不代刷、不托管固件，只给链接/校验和/回滚说明。
5. **可回滚**：任何插头都要有“删除/停用”路径；B 类必须写原厂恢复方式。
6. **离线优先/可复现**：参数、版本、兼容范围必须写死；安装不依赖运行时的“最新版”。

## 3. 插头包结构（推荐：git 仓库 + manifest）

```
rui-plugin-<id>/
  plugin.yaml                # 插件级元数据（必需）
  devices/
    <model-id>.yaml          # 每个机型一个 manifest；单机型插件也可内联到 plugin.yaml
  adapter/                   # 可选：adapter 代码（Python 包 / 镜像引用）
  assets/                    # 可选：图标、截图、二维码
  README.md                  # 设备侧安装/回滚说明（人读）
  LICENSE
```

- 分发：**git 仓库 + tag**（推荐），也接受 release tarball 或本地目录。
- 校验：manifest 里写 `source.checksum`（sha256）；可选 `signature`（P2 先不做验签，只做 allowlist + 人工确认）。
- 一个仓库可以放多个机型（`devices/*.yaml`），但 **manifest 以机型为单位**；向导展示到机型粒度。
- 插头不得包含密钥/内网地址/用户数据；RUI 只读 manifest，不执行 `README.md` 里的命令。

## 4. `plugin.yaml`（插件级）

```yaml
schema: rui-plugin/v0
id: example-plugin            # 全局唯一，[a-z0-9-]
name: "Example Speaker Plug"
version: 1.2.0                # semver
rui_compat: ">=0.1 <0.3"      # RUI 版本范围
license: MIT                  # 插头自身许可证
maintainer: "name <contact>"  # 可公开的联系方式
homepage: "https://example.invalid/plugin"
trust: third-party            # builtin | trusted | third-party
devices:
  - devices/example-a.yaml
  - devices/example-b.yaml
```

- `trust`：`builtin`（RUI 仓库自带）、`trusted`（审核/签名过）、`third-party`。
- RUI 只加载 `rui_compat` 命中的插件；不匹配时向导显示“不兼容”，不静默尝试。

## 5. 机型 manifest（`devices/<model>.yaml`）

```yaml
schema: rui-device/v0
id: example-model             # 机型 id（adapter 名/设备 id 的默认来源）
name: "Example Speaker"
vendor: "Example Inc."
class: A                      # A = 原厂共存/低权限；B = 高权限/完整控制
transport: inbound            # inbound = 设备拨回 RUI；outbound = RUI 连设备
protocol:
  kind: ws                    # 协议族；具体字段由 adapter 解释
  version: "1"
capabilities:                 # 与 core Capability 对齐；没有的能力必须显式缺省
  - mic_array
  - reference
  - playback
  - led
  - keys
  - volume
device_side:                  # 用户需要在音箱上做的事
  artifact: client            # firmware | client | app | none
  name: "open-xiaoai client"
  source: "https://github.com/coderzc/open-xiaoai"
  version_min: "v0.x"         # 我们验证过的最低版本
  install_steps:
    - "开启 SSH（见项目 README）"
    - "安装 client 并确认能回连"
  rollback: "恢复原厂固件/卸载 client 的步骤或链接"
runtime:                      # adapter 怎么跑
  mode: in-process            # in-process | sidecar
  entrypoint: "rui_plugin_example:build"   # in-process 必填
  image: "ghcr.io/example/rui-plugin-example:1.2.0"  # sidecar 必填
  listen_port: 4412           # inbound 设备端口（默认 4412）
  token_env: RUI_DEVICE_TOKEN   # 连接令牌的环境变量名
params:                       # 向导要渲染的表单（用户可填）
  - key: host
    label: "音箱地址"
    type: string
    required: true
    placeholder: "<speaker-ip>"
  - key: room
    label: "房间名"
    type: string
    default: "客厅"
  - key: wake_words
    label: "唤醒词"
    type: list
    default: ["你好小瑞"]
    max: 3
  - key: device_token
    label: "Device token"
    type: secret
    required: true
    help: "由 RUI 生成或向导写入；不要发到公开渠道"
compatibility:
  rui_min: "0.2.0"
  tested_with: ["0.1.0"]
  notes: "A 类需要 open-xiaoai client ≥ v0.x；固件 1.94.x 验证"
acceptance:                   # 插头作者自己的验收记录（人读 + 向导展示）
  verified: true
  last_verified: "2026-10-24"
  evidence: "真机唤醒/播放/让位；见插头 README"
```

字段规则：
- `class`、`capabilities`、`transport`、`params` 必填；`device_side`、`runtime` 视情况。
- 能力缺省即降级：例如无 `led` 不显示灯效配置；无 `reference` 不能承诺放歌打断。
- 参数类型：`string | int | bool | list | secret | choice`；`secret` 只写不读，日志不回显，且 manifest 不得写 `default`（只能由用户或 RUI 写入）；`choice` 必须提供非空 `options`，`default` 必须在其中。
- 端口/令牌默认值来自 RUI 约定（`listen_port: 4412`、`token_env: RUI_DEVICE_TOKEN`），manifest 可覆盖但要在向导里显式提示。

## 6. 安装向导契约（RUI 读 manifest 后渲染）

固定骨架 + 插头步：

| 步 | 通用/插头 | RUI 做什么 | 用户做什么 |
|---|---|---|---|
| 0 环境预检 | 通用 | 检查 Docker/端口/磁盘/内存/LAN | 确认 |
| 1 模型 | 通用 | 显示 ASR/VAD/KWS 模型下载状态与体积 | 等待/重试 |
| 2 密钥 | 通用 | 写 `.env`/`secrets.yaml`（0600，只回显 configured） | 填 LLM/TTS key（ASR 可本地） |
| 3 选插头 | 通用 | 内置插头 + 导入 git/目录；显示 A/B 类与能力 | 选一个机型 |
| 4 设备侧准备 | 插头 | 渲染 `device_side` 链接/最低版本/回滚 | 在音箱上装 client/刷固件 |
| 5 参数 | 插头 | 渲染 `params` 表单，校验，写 config | 填地址/房间/唤醒词 |
| 6 接线 | 通用 | 生成 `ws://<host>:<port>/?token=…`，等待 connected | 填进设备 client |
| 7 验证 | 通用 | probe 能力、试一句、写 last_verified | 说一句听回答 |
| 8 保存 | 通用 | 落盘 + 启用 + 记录插头版本 | 完成 |

失败处理：每一步都有“复制诊断”按钮（命令/字段/日志行）；允许从任一步返回重试；不通过的项不静默忽略。

## 7. 运行形态（in-process vs sidecar）

| | in-process | sidecar |
|---|---|---|
| 适用 | `builtin`/`trusted` 插头；LX06 参考插头 | 第三方插头默认 |
| 部署 | 代码进 RUI 镜像或运行环境 | 独立容器镜像，RUI 通过本地 socket/ws 连它 |
| 隔离 | 无（同进程） | 有（资源/权限/崩溃隔离） |
| 代价 | 简单、无额外镜像 | NAS 多一个镜像/端口/健康检查 |
| 安全 | 需要 allowlist/签名 | 默认更安全，仍建议签名 |

规则：`trust=third-party` 默认 sidecar；`builtin/trusted` 可 in-process。向导必须在安装前展示“将运行第三方代码”的警告与信任依据。

v0 loader 策略（`rui.plugins.load_plugin`）：
- `runtime.mode` 省略时按信任级别解析：`third-party` → `sidecar`，`builtin/trusted` → `in-process`；`sidecar` 必须有 `image`，`in-process` 必须有 `entrypoint`。
- `third-party` 显式写 `runtime.mode: in-process` **在加载时直接拒绝**，不静默降级；想同进程运行必须先审核为 `builtin/trusted`。
- 省略整个 `runtime` 块 = 纯声明插头，允许；loader 不导入/执行任何代码，`entrypoint`/`image` 只做字符串校验。

## 8. A/B 类最小成功清单

**A 类 · 小爱音箱 Pro（LX06，原厂共存）**
1. 音箱：LX06（固件版本按 `open-xiaoai`/`xiaoai-protocol.md` 要求），开启 SSH；
2. 安装/刷 `open-xiaoai` client（上游 https://github.com/coderzc/open-xiaoai；我们验证的 fork/最低版本写在插头 manifest）；
3. NAS：Docker/Compose、与音箱同 LAN、空闲内存 ≥2GB、磁盘 ≥2GB；
4. 密钥：LLM + TTS；ASR 可本地；
5. 装 RUI → 向导选 LX06 插头 → 生成 ws URL+token → 填进 client；
6. 向导确认 connected → 唤醒 → 第一句。
> 不 root、不改固件；必须验厂家让位/接管；回滚 = 卸载 client/恢复原状态。

**B 类 · Echo Dot 2（root/刷机）**
1. 解锁/root（参考 firebreak/amonet 等上游流程，风险自担）；
2. 刷/装插头提供的设备端固件或 client；
3. 其余同 A（NAS、密钥、向导、接线、验证）；
4. **必须**提供原厂恢复路径；RUI 不代刷、不托管固件。

**共同最小参数**：设备地址、房间名、唤醒词（1–3 个）、device token；其余由 manifest `params` 扩展。

## 9. 插头验收（作者侧）

插头作者需在 `acceptance` 里留下：
- 真机型号/固件/client 版本；
- 覆盖的动作：唤醒、单轮问答、放歌/打断（若有能力）、灯效（若有）、厂家让位（A 类必须）；
- 失败已知项与降级行为；
- 回滚步骤。
RUI 向导只把 `acceptance.verified=true` 的插头标“已验证”，否则提示“未验证，风险自担”。

## 10. 待定/后续（v0 不锁死）

1. manifest 签名/allowlist 的具体机制（P3 安全项）。
2. sidecar 与 RUI 的通信协议（localhost ws vs unix socket）。
3. 多机型共用 adapter 的打包方式。
4. 插件市场/索引（先不做，git 分发足够）。
5. 设备管理后台如何展示“插件来源/信任级别/更新”。
6. 插头版本与 RUI 版本的兼容矩阵自动化校验。

## 11. 非目标

- RUI 不分发/托管固件镜像（只给链接、校验和、安装/回滚说明）。
- 不承诺“任意音箱零成本接入”；A/B 类的最低门槛见 §8。
- 不在 0.1.x 内动核心适配器；本文件先作为 P2/P3 的实现契约。
