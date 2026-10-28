# LX06 客厅默认音量 60 -> 45（0.8.5 / task-4 / C）

Status: 只改工作树里的示例配置与文档（2026-10-28），未 commit、未打 tag、未部署，也未改桥仓。

## 现象

9哥 真机反馈（0.8.5 攒批 §11-4）：

- 客厅那台 **LX06 声音老大**（默认配置 `living.volume: 60` 下偏吵）；
- **dot2 三台音量合适**，不需要跟着调。

本批只处理「LX06 客厅默认音量」这一个口径，不动运行时行为。

## 改动

`config.example.yaml` 的 living 设备：

```yaml
    # 45 ≈ 设备刻度 115（0-100 -> 0-255），仍可按遥控/按键在设备侧再调。
    volume: 45             # LX06 客厅实测 60 偏吵；dot2 不需要跟着降；生产改配置需 9哥 批准
```

检查过的其它位置（结论：无需改 / 按任务要求不动）：

- `src/rui/config/template.yaml`：`devices: []`，模板不含任何设备音量字段，未改。
- `examples/`：`two-speakers/compose.yaml` 只挂配置目录，`plugin-example/devices/example.yaml`
  是设备清单（`capabilities` 里有 volume 能力，但没有音量值），都不含 living 音量样例，未改。
- dot2 示例值：本仓 `examples/` 里没有带 `volume` 的 dot2 示例；dot2 值只存在于桥仓
  `rui-dot2-bridge`，**本批不碰桥仓**。`config.example.yaml` 里也只有 living 一台设备。
- `docs/deploy.md:377` 仍写着 schema 默认 `volume: 60`，那是全局默认值、本批未改，所以保持正确。
- `docs/multi-device-design.md` 等设计/契约文档里的 `volume: 60` 是 schema 默认值/示例，
  不是本批要改的 living 示例，未动。

## 为什么是 45

- 配置面是 **0–100**，设备混音刻度是 **0–255**，映射是
  `round(pct * 255 / 100)`（`src/rui/device/contract.py:percent_to_device_volume`）。
  所以 `60 -> 153`、`45 -> 115`（`round(114.75)`），即比原来低 38 个混音步进（约 -25%）。
- 45 是一次**保守的一档下调**：既明显低于「声音老大」的 60，又不会掉到需要凑近听的
  40 以下；它是本批的可回退起点，不是真机测出来的「黄金值」。
- **不是锁死**：LX06 的音量键会被 task-19 采纳并写回 config target（`_apply_volume_key`
  改 `dev.volume`，控制面持久化），所以真机上按键/遥控还能继续微调并保留。
  （`volume: 0` 才是「不接管、沿用原厂」。）
- 没有选更激进的 35/40：本轮只降一档、方便 9哥 听感后继续往回调；45 若仍偏吵，
  下一档是 40（-> 102），不需要改代码，只改配置。

## 为什么不动 schema 全局默认、不动 dot2

- `src/rui/config/schema.py:266` 的 `DeviceConfig.volume` 默认 **60**，作用于**所有**
  没显式写 `volume` 的设备。dot2 的新装/示例常常不写 `volume`（桥仓示例即如此），
  把它改成 45 会**静默调低 dot2 三台**——而 dot2 的音量是合适的，属于改坏。
- 反馈是 LX06 客厅单台偏吵，因此口径落在「living 这一条示例配置」，不落在全局默认。
- 本仓 `examples/` 没有 dot2 音量值可改；桥仓 `rui-dot2-bridge` 不在本批写范围，
  按任务要求不改。

## 生产怎么生效（需要 9哥）

`config.example.yaml` 只是模板：真实安装用的是 `rui setup` 生成的模板
（`src/rui/config/template.yaml`，不含设备音量）或部署目录里的 `config.yaml`。
因此：

1. 部署 0.8.5 后，**手动**把生产 `config.yaml` 的 `devices[].id == living` 那条
   `volume` 改为 `45`（或走部署清单的同一项）；未改的存量配置仍是 60。
2. 也可以不改 YAML：控制台 `/v2` 的设备音量（`volume_target`）或 LX06 物理按键
   同样会写回 config，效果一致。
3. 该改动是配置项，不需要动桥仓/设备固件；按配置变更 SOP，生产落地前需 9哥 批准。

## 测试与证据

- 测试面：`rg 'volume' tests/` 没有任何断言 `config.example.yaml` 的 living
  `volume == 60`（`tests/test_app_fleet.py` 的 `saved_volume() == 60` 断言的是
  **schema 默认值**，按任务要求不动）。为防示例被改回去，在
  `tests/test_config.py::test_shipped_example_config_parses` 增加一条守卫断言
  `cfg.devices[0].volume == 45`（不是删/改既有断言）。

验证（真实输出）：

```
$ uv run pytest tests/test_config*.py -q
113 passed in 3.68s

$ uv run ruff check src tests
All checks passed!

$ uv run ruff check tests/test_config.py
All checks passed!
```

配置面自检：

```
raw living volume = 45 | adapter = lx06
parsed living volume = 45
schema default volume = 60
45 -> device units = 115 | 60 -> 153
```

## 风险 / 未完成

- **无真机听感验证**：45 是保守初值；9哥 部署后需实听确认，仍吵则按上面「下一档 40」
  继续调，仍属配置改动。
- 生产生效依赖 9哥 手动改配置（见上）；本批不改 schema 默认、不改 dot2、不改桥仓、
  不改运行时逻辑，因此不影响未显式配置音量的 dot2 新装。
- 未跑全量 pytest（全量由 verifier 一次性执行）；未 git commit。
