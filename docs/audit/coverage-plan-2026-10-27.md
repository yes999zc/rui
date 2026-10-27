# rui 覆盖率基线对比与提升方案（打磨项 D，2026-10-27）

> **性质**：测量 + 排期建议。**不是门禁**：本卡不改 `.github/workflows/*`、不加 `--cov-fail-under`、
> 不改 pytest 配置、不写新测试、不改 `src/`。
> **对象**：`house-ear` @ `3ea6726`（0.7.8 线）+ 当时未提交工作树（`worktree_dirty: true`）。
> **基线**：`docs/audit/coverage-baseline.md` + `coverage-baseline.json`（0.7.3 收口，@`cd2d0a2` + dirty 工作树，
> 2026-10-26 21:27 测得）。
> **测量时间**：2026-10-27 10:49（Asia/Shanghai）。
> **机器**：macOS arm64，Python 3.13.13，coverage 7.17.0，pytest 9.1.1（与基线**同版本**，见 §9）。
> **口径**：证据 = 实际命令输出；所有数字来自 `coverage.json` 的真实产物，无估算（估算处单独标注）。

---

## 0. 一页结论

1. **总量在升**：行覆盖 **81.30%**（基线 80.95%，+0.35pp），分支覆盖 **69.77%**（基线 69.55%，+0.22pp）。
   测试从 1651 收集 / 1634 passed 变成 **1674 收集 / 1668 passed / 6 skipped / 0 failed**。
2. **但包级有 6 个、文件级有 6 个百分比"下降"**。逐个查了源码与测试历史后，只有 **3 个是真降**：
   `session/vendor.py`、`audio/process.py`、`plugins/contract.py`。这 3 个文件**自基线以来 `src/` 一行没改**
   （`git diff --stat cd2d0a2 HEAD -- <file>` 为空），下降 100% 来自**删测试**：
   `300b2a9`（0.7.6「把设备语义搬去 bridge」动过 8 个测试文件、共 1221 行，其中 7 个是整文件删除，`test_security_fixes.py` 只删了 10 行、文件保留）和
   `c713916`（删内置 lx06 插件时删了 `tests/test_xiaoai_plug.py`）。
3. 另外 3 个"下降"是**结构性**、不是覆盖倒退：删掉被完全覆盖的代码（`adapter_aliases.py`、
   `device/registry.py` 5 行、`plugins/resolve.py` 2 行）或新增未测代码（`config/legacy_adapters.py`、
   `audio/protocol.py` +12 行）改变了分母。证据见 §5.5。
4. **现在没有任何东西在检查"只准升不准降"**：`ci.yml` 里没有 `--cov-fail-under`，仓库唯一的比较器是
   `scripts/coverage_report.py --compare`，它**只在本地手动跑**，且按百分比判断（本次会 exit 1，见 §3）。
5. **建议**：先补 §6 的 P0/P1 前三项（`audio/process.py` 的 Credit、`plugins/contract.py` 的
   plug-test 分支、`channels/cli.py` 的 0% 覆盖），**仍不设阈值**，用 `--compare` 人工观察两版再谈门禁
   （§7）。本卡**不改 CI / 阈值**。

---

## 1. 测量：命令与真实输出

```bash
# 当前（HEAD 3ea6726 + 工作树）
uv run pytest --cov=rui --cov-branch --cov-report=term-missing \
    --cov-report=json:/tmp/coverage-now.json
```

真实输出（末尾摘要行，逐字）：

```text
TOTAL                                23082   4317   7516   1326    78%
Coverage JSON written to file /tmp/coverage-now.json
1668 passed, 6 skipped in 151.55s (0:02:31)

real	2m33.091s
```

- 列含义：`Stmts=23082`、`Miss=4317`、`Branch=7516`、`BrPart=1326`。由 `Miss` 可直接算出**行覆盖 81.30%**
  （18765/23082），由 JSON 的 `covered_branches / num_branches` 算出**分支覆盖 69.77%**（5244/7516）。
- `TOTAL` 行末尾的 `78%` 是 coverage.py 把**行 + 分支**合并后的展示值，不要拿它和基线 md 里的
  80.95% / 69.55% 对比——两者不是同一个口径（基线文档 §0 的括注说的就是这件事）。
- 没加 `-q`：`pyproject.toml` 的 `addopts = "-q"` 已经在生效，命令行再加一次会变成 `-qq`，
  汇总行会被吞掉。

为了拿到**逐文件**的基线侧数字（`coverage-baseline.json` 只存到一级子包，见 §4.1），另起一个
worktree 在基线 commit 上复跑同一条命令：

```bash
git worktree add /tmp/rui-base-cd2d0a2 cd2d0a2
PY="$(git rev-parse --show-toplevel)/.venv/bin/python"   # 主仓解释器：这条命令仍在主仓里执行
(cd /tmp/rui-base-cd2d0a2 && "$PY" -m pytest --cov=rui --cov-branch --cov-report=term-missing \
    --cov-report=json:/tmp/coverage-base.json --junit-xml=/tmp/pytest-base.xml)
```

```text
TOTAL                                22767   4343   7416   1304    78%
3 failed, 1631 passed, 17 skipped in 135.39s (0:02:15)
```

复现质量：`num_statements=22767`、`num_branches=7416`、收集数 **1651** 与 `coverage-baseline.json`
**完全一致**；3 个失败是旧测试在当前依赖下的漂移（`test_audio_api.py::test_plane_failure_is_reported_with_its_reason`、
`test_playback.py::test_play_file_reaches_the_speaker_and_stop_play_follows`、
`test_xiaoai_e2e.py::test_fake_speaker_feeds_the_audio_plane`），代价是 covered 少 5 行 / 2 分支
（18424 vs 官方 18429、5156 vs 5158）。因此：

- **总量与包级**用 `docs/audit/coverage-baseline.json` 的官方值（§2、§3）；
- **逐文件**用这次重建的 `/tmp/coverage-base.json`（§4），并记住它比官方基线**略低**——
  也就是说真降只可能被**低估**，不会被高估。

---

## 2. 总量：当前 vs 基线

| 指标 | 基线（0.7.3 @cd2d0a2） | 当前（@3ea6726） | Δ |
| --- | ---: | ---: | ---: |
| 行覆盖 | **80.95%**（18429 / 22767） | **81.30%**（18765 / 23082） | **+0.35pp** |
| 分支覆盖 | **69.55%**（5158 / 7416） | **69.77%**（5244 / 7516） | **+0.22pp** |
| 语句分母 | 22767 | 23082 | +315 |
| 分支分母 | 7416 | 7516 | +100 |
| 测试 collected / passed / skipped / failed | 1651 / 1634 / 17 / 0 | 1674 / 1668 / 6 / 0 | +23 / +34 / −11 / 0 |
| 测量范围 | `src/rui` 120 个 .py（基线文档记 5 个 0 覆盖） | `src/rui` 122 个 .py（coverage 产物内 2 个 0 覆盖） | +3 新 −1 删 |

净增量：**+336 行、+86 分支被覆盖**，同时新增 315 语句 / 100 分支需要覆盖，行覆盖净 +0.35pp。
`skipped` 从 17 降到 6（可选 extra 装得更多），会顺带抬高若干模块的数字，这条在 §9 单列。

---

## 3. 包级：13 个一级子包

`scripts/coverage_report.py --compare` 是仓库现有的比较器（`--compare` 模式不写文件，只打印差异）：

```bash
uv run python scripts/coverage_report.py \
    --coverage-json /tmp/coverage-now.json --junit-xml /tmp/nonexistent.xml \
    --compare docs/audit/coverage-baseline.json
```

真实 stderr（逐字，`COMPARE_EXIT=1`）：

```text
coverage regression against docs/audit/coverage-baseline.json:
  (root) line_percent: 82.89% < baseline 82.94%
  audio line_percent: 80.46% < baseline 80.72%
  audio branch_percent: 65.47% < baseline 65.60%
  config branch_percent: 86.13% < baseline 86.22%
  device line_percent: 82.36% < baseline 82.46%
  device branch_percent: 74.16% < baseline 74.67%
  plugins line_percent: 75.58% < baseline 75.89%
  plugins branch_percent: 62.43% < baseline 63.01%
  session line_percent: 90.32% < baseline 90.66%
  session branch_percent: 82.83% < baseline 83.17%
```

| scope | 基线行% | 当前行% | Δ行 | 基线分支% | 当前分支% | Δ分支 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `(root)` | 82.94 | 82.89 | -0.05 | 71.03 | 71.03 | +0.00 |
| `audio` | 80.72 | 80.46 | -0.26 | 65.60 | 65.47 | -0.13 |
| `channels` | 52.81 | 56.60 | +3.79 | 35.64 | 38.94 | +3.30 |
| `config` | 94.05 | 94.07 | +0.02 | 86.22 | 86.13 | -0.09 |
| `device` | 82.46 | 82.36 | -0.10 | 74.67 | 74.16 | -0.51 |
| `doctor` | 88.08 | 89.33 | +1.25 | 78.29 | 79.87 | +1.58 |
| `music` | 88.03 | 88.03 | +0.00 | 80.39 | 80.39 | +0.00 |
| `plugins` | 75.89 | 75.58 | -0.31 | 63.01 | 62.43 | -0.58 |
| `providers` | 72.83 | 72.83 | +0.00 | 60.11 | 60.11 | +0.00 |
| `security` | 87.03 | 87.03 | +0.00 | 80.00 | 80.00 | +0.00 |
| `session` | 90.66 | 90.32 | -0.34 | 83.17 | 82.83 | -0.34 |
| `tools` | 77.54 | 79.34 | +1.80 | 64.77 | 65.21 | +0.44 |
| `web` | 80.37 | 80.98 | +0.61 | 68.40 | 69.08 | +0.68 |
| **total** | **80.95** | **81.30** | **+0.35** | **69.55** | **69.77** | **+0.22** |

解读：涨得最多的是 `channels`（+3.79pp，语句 873→887、covered 461→502，**+41 行全部来自
`channels/weixin.py`**）、`tools`（+1.80pp，`mcp_client.py` +27.93pp）、`doctor`（+1.25pp）。
掉的是 `session`（-0.34pp，全是 `vendor.py`）、`plugins`（-0.31pp，`contract.py` 真降 +
`resolve.py` 平移）、`audio`（-0.26pp，`process.py` 真降 + `protocol.py` 新码）、`device`（结构性，§5.5）、
`config`（新文件的分支）、`(root)`（删了一个文件）。

---

## 4. 逐文件对比

### 4.1 口径说明：基线侧没有逐文件数据

`docs/audit/coverage-baseline.json` 的 schema 是 `rui-coverage-baseline/v1`，只有 `overall` + `packages` 两级，
**没有 files 段**；`coverage-baseline.md` §1 也只有 13 行包级表。所以"逐文件 vs 基线"不可能从已提交文件直接得出。
本文的处理是：在基线 commit `cd2d0a2` 上重建一次测量（§1 第二条命令），并用它做逐文件基线侧。
重建结果与官方基线的语句/分支**分母完全相同**（22767 / 7416），covered 差 5 行 / 2 分支，
差异方向使基线偏低（所以下面的"下降"是保守判断）。

### 4.2 统计

共同文件 **119** 个（当前 122、基线 120；新增 3、消失 1）：

| 维度 | 升 | 降 | 持平 |
| --- | ---: | ---: | ---: |
| 行覆盖 | 10 | 6 | 103 |
| 分支覆盖 | 8 | 4 | 107 |

### 4.3 下降的 6 个文件

| 文件 | stmts | 行% 基线→当前 | Δ行pp | 分支% 基线→当前 | Δ分支pp | 新增未覆盖行数 |
| --- | ---: | --- | ---: | --- | ---: | ---: |
| `session/vendor.py` | 25 | 100.00 → 72.00 | -28.00 | 100.00 → 25.00 | -75.00 | 7 |
| `audio/process.py` | 1713 | 74.55 → 73.85 | -0.70 | 61.39 → 60.71 | -0.68 | 13 |
| `plugins/contract.py` | 1111 | 68.50 → 67.96 | -0.54 | 56.10 → 55.16 | -0.94 | 6 |
| `device/registry.py` | 152 | 84.71 → 84.21 | -0.50 | 80.36 → 78.00 | -2.36 | 8（纯行号平移） |
| `audio/protocol.py` | 260 | 99.60 → 99.23 | -0.37 | 75.00 → 80.00 | **+5.00** | 1 |
| `plugins/resolve.py` | 54 | 96.43 → 96.30 | -0.13 | 88.89 → 88.89 | +0.00 | 2（纯行号平移） |

> 只有前三个是真降。判定与证据见 §5。

### 4.4 上升 Top 10 与新增文件

| 文件 | 行% 基线→当前 | Δ行pp | 分支% 基线→当前 | Δ分支pp |
| --- | --- | ---: | --- | ---: |
| `tools/mcp_client.py` | 31.78 → 59.70 | +27.93 | 11.11 → 25.00 | +13.89 |
| `channels/weixin.py` | 63.04 → 72.62 | +9.58 | 42.68 → 50.00 | +7.32 |
| `doctor/probes.py` | 88.03 → 89.28 | +1.25 | 78.29 → 79.87 | +1.58 |
| `web/api/app.py` | 78.56 → 79.57 | +1.01 | 68.36 → 69.48 | +1.12 |
| `config/store.py` | 88.00 → 88.78 | +0.78 | 80.00 → 81.43 | +1.43 |
| `session/rules.py` | 92.99 → 93.63 | +0.64 | 86.49 → 87.84 | +1.35 |
| `audio/client.py` | 74.42 → 74.82 | +0.39 | 52.42 → 53.91 | +1.49 |
| `config/catalog.py` | 94.32 → 94.49 | +0.17 | 84.94 → 84.94 | +0.00 |
| `tools/mcp_manager.py` | 88.47 → 88.53 | +0.06 | 72.12 → 72.12 | +0.00 |
| `plugins/models.py` | 90.68 → 90.73 | +0.05 | 78.70 → 78.70 | +0.00 |
| `web/api/legacy_links.py`（新增） | — → 97.50 | new | — → 100.00 | new |
| `config/legacy_adapters.py`（新增） | — → 92.31 | new | — → **50.00** | new |
| `web/api/body_limit.py`（新增） | — → 84.13 | new | — → 75.00 | new |

> Δ 列用**未取整**的百分比相减后取两位，因此可能与左右两列"看起来的"四舍五入相减差 0.01
> （例：`mcp_client` 31.7757 → 59.7015 = **+27.93**，`audio/client` 74.4246 → 74.8166 = **+0.39**）。

消失的文件：`src/rui/adapter_aliases.py`（9 语句 / 8 已覆盖，由 `c713916` 删除——下降的 `(root)` 包 0.05pp 全部来自这里）。

---

## 5. 下降点专节（优先级高于提升方案）

### 5.1 判定方法

对每个"百分比下降"的文件问三个问题：

1. **源码改了吗？** `git diff --stat cd2d0a2 HEAD -- src/rui/<file>` 是否为空。
2. **被覆盖的绝对行/分支少了吗？** 看 `covered_lines` / `covered_branches` 的绝对值，而不是百分比。
3. **未覆盖行号只是平移吗？** 若"新增未覆盖行"与"新增已覆盖行"相差一个固定偏移量，就是删行/加行造成的行号平移。

三类结果：**真降**（源码没动、绝对覆盖变少 → 测试侧退化）、**结构性·删码**（删掉的是被完全覆盖的代码，
百分比降但内容覆盖没丢）、**结构性·新码**（新增代码拉大分母）。

### 5.2 真降一：`session/vendor.py`（100% → 72%，分支 100% → 25%）

- **证据**：`git diff --stat cd2d0a2 HEAD -- src/rui/session/vendor.py` → **空**（源码一行没改）。
  covered 25 → 18（`-7` 行），covered branches 4 → 1（`-3`）。
- **下降点**：31-35（`vendor_spoke()` 里"只有 final recognition 才开窗"的 `if kind != "wake": return False`
  分支）、44-45（`muted()` 的窗口内分支）。blame 指向 `91de4af feat(session): yield to the speaker's own assistant`
  ——**这行代码基线时就在**，说明是覆盖它的测试没了。
- **根因**：`300b2a9 test(0.7.6): drop the device-semantics suites the bridge now owns` 删除了
  `tests/test_vendor.py`（152 行，`grep -c VendorWindow` = 4）。
- **性质**：这是"用户自己的助手已回答时，rui 该闭嘴"的策略。删测试不等于策略不需要验证——
  它现在仍被控制面使用：`src/rui/web/api/app.py` 2660 构造、2754 `muted()`、2758 `remaining()`、
  2777 与 2951 `vendor_spoke()`。而 `tests/` 里**没有任何测试构造 `VendorWindow`，也没有一处调用
  它的 `vendor_spoke` / `muted` / `remaining`**（全仓唯一提及是 `tests/test_device_wake.py:483`
  的一句注释，讲的是音频侧"本地镜像了同样的窗口"）。
- **处置建议**：S 级，恢复 3 条核心用例即可（见 §6 P1-2）。

### 5.3 真降二：`audio/process.py`（74.55% → 73.85%，分支 61.39% → 60.71%）

- **证据**：`git diff --stat` → **空**。covered 1277 → 1265（`-12` 行），covered branches 361 → 357（`-4`）。
- **下降点**：255-280，全部在 `Credit` 类：`wait()` 的 `self.window <= 0`（"不设泄漏上限时只靠时钟"）分支，
  以及 `wait_consumed()` 超时一次就 `dead = True` 并回退时钟的路径（268-280）。blame 指向
  `2c246bd feat(audio): pace by the device's own play head, not the wall clock` + `0717fa2`
  ——同样是基线前就有的代码。
- **根因**：`300b2a9` 删除 `tests/test_credit.py`（106 行，`grep -c Credit` = 6）。
- **性质**：播放节奏的用户可见症状是"卡顿/静音后突然加速/设备缓冲越堆越深"。这是**唯一一条**
  现有测试完全不碰的核心音频策略路径。
- **处置建议**：M 级（§6 P0-1）。Credit 的两个分支都可以用假 adapter 在毫秒内测完，不需要真设备。

### 5.4 真降三：`plugins/contract.py`（68.50% → 67.96%，分支 56.10% → 55.16%）

- **证据**：`git diff --stat` → **空**。covered 761 → 755（`-6` 行），covered branches 239 → 235（`-4`）。
- **下降点**（新增未覆盖的 6 行 = 5 个 check_id 的 SKIP 行 + 1 个 `return`）：
  1334 = `device.open` SKIP；1346-1347 = `capture.frames` / `capture.cadence` SKIP；
  1353 = `playback.sink` SKIP；1737 与 1742 = `wake.inject` 的 `if not wake_enabled:` 分支
  （reason `device_wake is not effective (manifest/probe)…`；1737 是 `report.add(`、1742 是 `return`）。
  blame 指向 `d8899a9` / `223c747`——**这几行基线时就在**，说明覆盖它们的是被删掉的测试。
- **既有缺口（不是下降点）**：`adapter.open()` 抛异常的 FAIL 分支（1339-1341）和
  `if not callable(inject)` 的 SKIP 分支（1743-1750，reason `adapter exposes no inject_wake() hook…`）
  在基线时**就已经未覆盖**（重建基线的 `missing_lines` 里本来就有 1339-1341、1744、1750）。
- **根因**：`c713916 refactor(plugins): drop the built-in lx06 plug and the adapter alias` 删除了
  `tests/test_xiaoai_plug.py`，其中 `test_the_plug_passes_the_contract_kit` 末尾那个
  「5 个 check_id 全 SKIP」的循环（`git show c713916^:tests/test_xiaoai_plug.py` 第 219-226 行）
  正是覆盖这 6 行的断言。
- **性质**：`rui plug-test` 是插件作者唯一的质量关口，这 5 条 SKIP 判定与 detail 文案直接决定
  插件作者在 `live_device_required` 场景下看到什么。现在这 5 条路径零测试。
- **处置建议**：M 级（§6 P0-2）。给 `tests/fixtures/sample-plug/` 加一个声明
  `kit_live_device_required = True` 的测试 adapter（`contract.py:1294` 就是读这个属性），
  跑一遍 `rui plug-test` 即可覆盖 4 条 SKIP；`wake.inject` 的 SKIP 要 manifest 声明 `device_wake`
  而 probe 未生效。全程不需要接真设备。

### 5.5 结构性下降（不算回退，但会让 `--compare` 变红）

| 文件 | 现象 | 判定依据 | 结论 |
| --- | --- | --- | --- |
| `device/registry.py` | 行 84.71→84.21，分支 80.36→78.00 | 语句 157→152（`-5`）、covered 133→128（`-5`）；新增未覆盖行 221-222/242-244/302-304 与新增已覆盖行 226-227/247-249/307-309 恰好相差 +5 → 同一段源码整体平移。分支 56→50，covered 45→39 同样整体消失 | 删掉的 5 行/6 分支**全是已覆盖**的，百分比被"高分项"减少拉低。内容覆盖没丢 |
| `plugins/resolve.py` | 行 96.43→96.30 | 语句 56→54、covered 54→52；未覆盖/已覆盖行号相差 +5 | 同上，纯平移 |
| `audio/protocol.py` | 行 99.60→99.23 | 语句 248→260（`+12`）、covered 247→258（`+11`）；分支 4→10、covered 3→8（**分支 +5.00pp**） | 新码只漏 1 行（line 98，blame `cf42695` 的 body-limit 修复），净增 11 行/5 分支 |
| `(root)` 包 | 行 82.94→82.89 | `adapter_aliases.py`（9 语句 / 8 已覆盖）被 `c713916` 删除：851−8=843、1026−9=1017 | 删掉一个高分小文件，纯分母效应 |
| `config` 包 | 分支 86.22→86.13 | 新增 `config/legacy_adapters.py`（13 语句、**2 分支只覆盖 1 个**）。把它排除后 config 为 **行 94.09 / 分支 86.32**（都高于基线） | 新文件的分支缺口，不是老代码退化 |

### 5.6 那么"只准升不准降"现在合法吗？

按仓库现有比较器的口径：**不合法**。`scripts/coverage_report.py --compare` 只看百分比，
本次会 exit 1（§3 的 10 行输出）：其中 4 行来自 §5.5 的结构性变化（`(root)` line、`config` branch、
`device` line + branch），6 行由 §5.2-5.4 的真降驱动（`session` 两行、`plugins` 两行、`audio` 两行；
`audio` 的 line% 同时被 `protocol.py` 的新码拉低）。

这件事本身值得决策，但**不在本卡**改脚本。给 Lead 的两个可选项（本文建议 B）：

- **A. 保持严格百分比**：每次结构性变动都要人工写豁免说明，`--compare` 会经常 exit 1 变成噪音。
- **B. 双轨口径**（建议）：`covered_lines` / `covered_branches` **绝对值**不得下降 + 新增/修改的
  `src/` 文件要求"新行被覆盖"；百分比只作展示。这样"删已测代码"不再误报，"新码不测"仍会被抓。
  若采纳，需要另立一张卡改 `scripts/coverage_report.py` 与 docs（本卡不动）。

---

## 6. 缺口排序（12 个候选）

### 6.1 判据

**不按行数排序**。三个维度，先看前两个：

- **用户可见影响（I，1-5）**：5 = 失败时用户直接感知（听不到声、发不出消息、进程起不来）；
  4 = 安装/配置向导里能看见的错报；3 = 工具类结果错误。
- **无测试强度（T，1-5）**：5 = 0 覆盖或全仓无引用；4 = 只有间接覆盖/无专属测试；
  3 = 有测试但只覆盖 happy path。
- **分支密度（B）**：未覆盖分支数，作为同分时的次序依据。

同等条件下**真降文件优先**（§5.2-5.4），因为它们是"能力在、验证没了"。

### 6.2 排序表

| # | 优先级 | 文件 | 行% | 分支% | 未覆盖 行/分支 | 缺口性质 | 用户可见影响 | 工作量 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |
| 1 | **P0** | `audio/process.py`（`Credit`） | 73.8 | 60.7 | 12 / 4（真降部分） | 分支 + 错误路径（超时回退） | 播放卡顿/静音后加速，用户能听出来 | M |
| 2 | **P0** | `plugins/contract.py`（`device.open` / `wake.inject`） | 68.0 | 55.2 | 6 / 4（真降部分） | 分支 + 错误路径（FAIL/SKIP 判定） | `rui plug-test` 是插件作者唯一关口，错报误导作者 | M |
| 3 | **P1** | `channels/cli.py` | **0.0** | **0.0** | 107 / 18 | 全无测试 | Telegram/微信网关进程入口，起不来就"机器人不回话" | M |
| 4 | **P1** | `session/vendor.py` | 72.0 | **25.0** | 7 / 3 | 分支（真降） | 用户自己的助手回答后 rui 该闭嘴，现在是零测试 | **S** |
| 5 | **P1** | `channels/telegram.py` | 44.3 | **17.9** | 113 / 46 | 分支 + 错误路径 | 遥控通道；长消息切分、语音转写都在这里 | M |
| 6 | **P2** | `providers/keycheck.py` | 67.2 | 61.2 | 164 / 80 | 错误路径（各家 key 探测） | 安装向导误报"key 无效"，用户会去重置一个没问题的 key | M |
| 7 | **P2** | `web/api/setup_api.py` | 83.7 | 73.7 | 102 / 79 | 分支 + 幂等/重入 | 八步安装向导后端，`confirm_state` 重复提交 | M |
| 8 | **P2** | `channels/supervisor.py` | 61.4 | 37.5 | 34 / 5 | 错误路径（子进程起不来/退出） | 通道进程守护；失败表现是"突然不响应" | **S** |
| 9 | **P2** | `providers/openai_compat.py` | 38.1 | **20.6** | 70 / 27 | 分支（SSE 半包/非 200） | LLM/TTS/ASR 三阶段客户端，流解析错会吞半句 | M |
| 10 | **P3** | `tools/mcp_client.py` | 59.7 | **25.0** | 54 / 15 | 生命周期 + 错误路径 | MCP 工具服务器连不上时插件整体失效 | M |
| 11 | **P3** | `plugins/semver.py` | 80.8 | 61.7 | 25 / **23** | 边界（预发布/范围边界） | `rui_compat` 兼容判定，判错=装错插件 | **S** |
| 12 | **P3** | `audio/ack_render.py` | 46.2 | 37.5 | 43 / 15 | 边界（空文本/采样参数） | 唤醒应答音；坏了就是"唤醒没反应" | **S** |

> 同分未进表的观察名单（不是本轮建议，但值得记）：`web/api/v2_api.py`（1700 语句、分支 63.4%、
> 337 行未覆盖——分母太大，单卡吃不下）、`audio/kws.py`（48.7%，受可选依赖/模型影响，环境相关）、
> `tools/info.py`（52.4%，天气/搜索属外部网络，测试要靠录制的响应）。

### 6.3 每条的测试点（≤3）

1. **`audio/process.py` — `Credit.wait()`**
   - `window <= 0` → 只按 `(sent - acked) * seconds` 算 `lead` 并直接放行，不调 `adapter.wait_consumed`。
   - `wait_consumed()` 第一次超时（返回 ≤ acked）→ `dead = True`、记一条 `credit: no play-head report`
     日志、`wait()` 返回 `False`，且**只等一次**（用假时钟断言没有第二次等待）。
   - `ack(n)` 收到比 `sent` 大的旧播放器报告 → 被 clamp 到 `sent`（252-255 的 `ack()`，注释写的就是这条契约）。
2. **`plugins/contract.py` — plug-test 的 5 条 SKIP 路径（真降的 6 行全在这里）**
   - 照抄被删的断言即可一次补回全部 6 行：`test_xiaoai_plug.py:219-226` 里的
     `for check_id in ("device.open", "capture.frames", "capture.cadence", "playback.sink", "wake.inject"): assert checks[check_id]["status"] == "SKIP"`
     —— 覆盖 1334 / 1346-1347 / 1353 / 1737 / 1742。
   - 同上循环里再断言 4 条 `live_device_required` 的 SKIP（`device.open` / `capture.frames` /
     `capture.cadence` / `playback.sink`）把 `reason` 原样透传（`live_device_reason`），
     并保留末尾 `assert "open-xiaoai client" in checks["device.open"]["detail"]` 这类 detail 断言。
   - 区分 `wake.inject` 的两条 SKIP：`manifest`/probe 未生效 → 1737 的
     `device_wake is not effective (manifest/probe)…`；adapter 没有 `inject_wake` hook → 1743-1750 的
     `adapter exposes no inject_wake() hook…`（后者属既有缺口，不是本次下降，但同一次补测试顺手覆盖）。
3. **`channels/cli.py`**（当前 0 覆盖，先求"跑起来"；`main` 是 103-140，argparse 在 104-122，
   **没有子命令**，只有 flag）
   - `main()`（103-140）：未知 flag → argparse `SystemExit(2)`；`--login-weixin` 分支（129-133）
     把 `_login_weixin()` 的 0/1 原样透传；`KeyboardInterrupt` → 0（136-137）、
     `httpx.HTTPError` → stderr 一条 + 返回 1（138-140）。
   - `_run()`（55-100）：一个 adapter 都没启用 → stderr
     「没有启用任何 channel：至少给 --telegram-token 或 --weixin-data」+ `control.close()` + 返回 2（87-90）；
     取消（`asyncio.CancelledError`）时 `finally` 里 `gateway.stop()` 与 `control.close()` 各执行一次（95-99）。
     注意 `--channels-config` 里的未知键是**被忽略**的，`cli.py` 没有 channel 选择器，
     所以不存在"指定未知 channel → 报错"这条路径。
   - `_read_channel_config()`（45-52）与 `_login_weixin()`（25-42）：路径为空 / JSON 坏 / 非 dict
     一律回落到 `{}`（于是退回 env 与 flag）；`qr_login` 抛错 → stderr「微信登录失败：…」+ 返回 1，
     且 `finally` 里仍然 `adapter.stop()`；`show()`（27-32）只是二维码回调（有 image 打 image，
     否则打 qrcode 内容），不做任何缺 token 校验。
4. **`session/vendor.py` — `VendorWindow`**
   - 非 `wake` 类型信号 → `vendor_spoke()` 返回 `False` 且不计数、不开窗（31-32）。
   - `wake` 后窗口内 `muted()` 为 `True` 且 `yielded_turns` +1；`ours_spoke()` 立即关窗（44-45）。
   - `remaining()` 在窗口过后返回 0（不返回负数）。
5. **`channels/telegram.py`**
   - `split_text()` 长文本按上限切分且不切断多字节字符/不丢字符。
   - `_handle_update()` 收到非文本/非语音 update → 静默丢弃、不抛异常。
   - `ffmpeg_path()` 缺失 → 语音消息走明确降级提示，而不是崩在 subprocess。
6. **`providers/keycheck.py`**
   - LLM 探测收到 401 → 报"key 无效"而不是"网络错误"（错误分类是这张文件的全部价值）。
   - 同一个 volc key 分别打 TTS/ASR 两个端点，错误码不同时的归类。
   - 探测超时 → 结果记 warn（不阻断安装），并保留可读 detail。
7. **`web/api/setup_api.py`**
   - `confirm_state` 同一步重复提交 → 幂等，不重复触发副作用。
   - `_coerce_params` 类型错 → 400 且带字段名。
   - 跳步/越界 step → 拒绝并保持状态机一致。
8. **`channels/supervisor.py`**
   - 子进程起不来 → `start()` 返回错误并带上 stderr 首行。
   - 子进程异常退出 → `watch()` 计一次重启，不无限重启（断言退避/计数）。
   - 未启动时 `stop()` 幂等。
9. **`providers/openai_compat.py`**
   - `chat()` 的 SSE：一块里多个 `data:`、空行心跳、`[DONE]` 收尾都不丢内容（147-171 全未覆盖）。
   - 非 200 → 结构化错误里带 provider 名与状态码。
   - `aclose()` / `warm()` 在从未 open 的情况下幂等。
10. **`tools/mcp_client.py`**
    - `start()` 时 server 命令不存在 → 抛错带 stderr，且不留孤儿进程。
    - 调用中 server 退出 → `call()` 返回错误而不是挂到超时。
    - `stop()` 幂等；`_run()` 的读循环收到 EOF 后不再重连。
11. **`plugins/semver.py`**
    - 预发布排序：`1.0.0-rc.1 < 1.0.0`；`1.0.0-rc.1 < 1.0.0-rc.2`。
    - 范围 `>=0.3.0 <0.8.0`：0.3.0 包含、0.8.0 排除、0.7.99 包含。
    - 非法输入（前导零 `01.2.3`、缺段）→ 明确 ValueError，而不是静默返回 False。
12. **`audio/ack_render.py`**
    - `render_ack_wav()` 输出采样率/声道/时长符合配置（读 WAV 头断言）。
    - 空文本/空配置 → 不产出 0 长文件（返回 None 或跳过）。
    - 同一目标的重复调用命中缓存，只渲染一次。

**若只做 P0 + P1（前 5 条）**：直接补上的是 `channels/cli.py` 107 行 + 真降的 25 行 + `telegram.py` 的一部分，
按当前分母 23082 算，仅 `channels/cli.py` 全绿一条就 **+107 行 ≈ +0.46pp**（算术，不含分支）。
这在数字上不惊人，但它把「完全没测的入口」变成「有回归保护」，价值大于百分比。

---

## 7. 本卡不改 CI / 阈值 + 建议下一步

### 7.1 本卡的边界（明确写死）

- **没有**改 `.github/workflows/ci.yml`（里面依旧没有 `--cov-fail-under`；`ci.yml` 的注释也仍写着
  "0.7.3 只建立基线，coverage-baseline.json 是比较点"）。
- **没有**改 `pyproject.toml`（`addopts = "-q"` 未动）、没有改 `scripts/coverage_report.py`、
  没有改 `docs/audit/coverage-baseline.{md,json}`。
- **没有**写新测试、没有改 `src/`、没有 commit / push。本卡唯一产出就是本文档。

### 7.2 建议的下一步（按顺序，每步一张独立卡）

1. **补 §6 的 P0/P1 5 条**（`Credit`、plug-test 两个分支、`channels/cli.py`、`VendorWindow`、
   `telegram.py`），**仍不设阈值**。理由：这 5 条里有 3 条是"能力在、验证没了"的真降，
   另 2 条是用户直接感知的通道入口。
2. **观察两版**（0.7.9、0.8.0）：每个版本收口时手动跑一次
   `scripts/coverage_report.py --compare`，把 exit 1 的条目按 §5 的三类归因写进版本记录。
   **先不接 CI**——按 §5.6，现在的百分比口径会把结构性变动误报成回退，接进 CI 只会训练大家忽略红灯。
3. **再谈阈值卡**（建议 0.9.0 之前）：前置条件是先定 §5.6 的口径（建议 B：绝对行/分支数 + 新码补充规则）。
   阈值形态建议 `--cov-fail-under` 取「基线 −0.5pp」这种留出结构性变动余量的形式，
   并显式排除 §5.5 那类"删已测代码"的豁免清单。
4. **长期**：`channels` 包（56.6% 行 / 38.9% 分支）仍是唯一低于 60% 的一级子包，
   若通道要对外交付，应单独开一张 "channels 覆盖率到 70%" 的卡，而不是混进打磨项。

---

## 8. 复算方法（reviewer / verifier 可直接照抄）

```bash
# 1) 当前值（约 2.5 分钟；不要加 -q，addopts 已经带了）
uv run pytest --cov=rui --cov-branch --cov-report=term-missing \
    --cov-report=json:/tmp/coverage-now.json
#    期望 TOTAL 行：23082  4317  7516  1326  78%
#    期望末行：1668 passed, 6 skipped

# 2) 包级对比（exit 1 = 有百分比回退，输出见 §3）
uv run python scripts/coverage_report.py --coverage-json /tmp/coverage-now.json \
    --compare docs/audit/coverage-baseline.json

# 3) 逐文件基线侧（本文的做法；复用主仓 .venv 的解释器，避免在 worktree 里再 sync 一次）
MAIN="$(git rev-parse --show-toplevel)"        # 在仓库根目录执行
git worktree add /tmp/rui-base-cd2d0a2 cd2d0a2
(cd /tmp/rui-base-cd2d0a2 && "$MAIN/.venv/bin/python" -m pytest --cov=rui --cov-branch \
    --cov-report=json:/tmp/coverage-base.json)
#    期望 TOTAL 行：22767  4343  7416  1304  78%（含 3 个旧测试因依赖漂移而失败）

# 4) 抽查某一行覆盖率（例：session/vendor.py）
python3 -c "import json;d=json.load(open('/tmp/coverage-now.json'));\
print([ (k,e['summary']['covered_lines'],e['summary']['num_statements']) \
for k,e in d['files'].items() if k.endswith('session/vendor.py')])"
#    期望 (18, 25) → 72.0%

# 5) 代码风格未受影响
uv run ruff check src tests    # All checks passed!
```

**复现固定点**：本文所有数字在 `3ea6726` 上测得。测量期间仓库 HEAD 已推进到 `3b02930` 且有并发编辑
（`src/rui/audio/client.py`、`tests/`），所以在**当前工作树**重跑会得到略不同的总量——那不是本文的数字错了，
而是被测量的树变了。要逐字复现本文，请用固定 commit：

```bash
MAIN="$(git rev-parse --show-toplevel)"
git worktree add /tmp/rui-verify 3ea6726
(cd /tmp/rui-verify && "$MAIN/.venv/bin/python" -m pytest \
    --cov=rui --cov-branch --cov-report=json:/tmp/coverage-verify.json)
# 期望：TOTAL 23082 4317 7516 1326 78% / 1668 passed, 6 skipped
```

---

## 9. 风险与口径差异

1. **`TOTAL` 的 78% ≠ 本文的 81.30% / 69.77%**。coverage.py 的 term 报告把行与分支合并展示，
   基线文档和本文都按 `scripts/coverage_report.py` 的公式拆开（行 = covered/statements，
   分支 = covered_branches/num_branches）。跨文档对比时只认拆开后的数。
2. **逐文件基线的来源是重建，不是官方产物**。`coverage-baseline.json` 没有 files 段，
   重建的 covered 比官方少 5 行 / 2 分支（3 个旧测试在当前依赖下失败）。误差方向使**基线偏低**，
   所以 §4 的"下降"是保守判定；但**小文件的高百分比差会被放大**——`session/vendor.py` 只有 25 语句，
   1 行 = 4pp，看它时要看绝对行数（-7 行）而不是 -28pp。
3. **分支统计方式未变**：基线与本次都是 coverage 7.17.0 + `--cov-branch`，Python 都是 3.13.13，
   排除了"版本导致分支口径变化"这一可能（`meta.version` 两处都是 7.17.0）。
4. **平台差异**：本文在 macOS arm64 + CPython 3.13.13 测得；CI 矩阵是 3.11 / 3.12（ubuntu-latest），
   同 commit 的绝对百分比可能差零点几。跨环境比较要以同 commit 重跑为准。
5. **`skipped` 17 → 6**：部分可选依赖/夹具在当前环境下可用，会抬高相关模块（音频类）的数字。
   这部分不是"补了测试"，属于**环境差异**，不应算作覆盖率改善。
6. **"无测试"的判定是静态 grep**：本文的"无引用"指 `tests/*.py` 里没有
   `rui.<pkg>.<mod>` 的直接导入（`channels/cli.py` **0 处**——全仓唯一引用是 `pyproject.toml:36`
   的 entry point `rui-channels = "rui.channels.cli:main"`；`web/api/setup_api.py` 0 处；
   `channels/supervisor.py` 0 处）。它**不排除**通过 HTTP 客户端/子进程的间接覆盖——
   `setup_api.py` 的 83.7% 就是被 HTTP 层测试间接覆盖来的，所以它虽然 0 直接引用，
   优先级仍排在"0 覆盖"的 `channels/cli.py` 之后（`cli.py` 是 0 直接引用**且 0 覆盖**）。
7. **本文不含任何内网地址、凭证或设备序列号**；所有命令都在本机仓库内复跑。
8. **测量是一张快照，会过期**。HEAD 在测量期间从 `3ea6726` 推进到 `3b02930`，期间有并发编辑
   （`src/rui/audio/client.py`、`tests/test_audio_api.py`、`tests/test_mcp_down_logging.py` 等）。
   本文的数字只对 `3ea6726` 成立；下一张补测试卡开工时，先按 §8 的固定点复跑一次再对比，
   不要拿本文的绝对值去要求一个已经变化的树。
