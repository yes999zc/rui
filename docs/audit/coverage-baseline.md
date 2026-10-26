# rui 覆盖率与测试基线（0.7.3，A 段 1）

> **性质**：基线记录，不是门禁。本卡（0.7.3 A 段 1）只建立「可复跑的数字 + 机器可读的比较基准」，
> 不设覆盖率阈值；后续版本用 `--compare` 判断是否回退。
> **对象**：`house-ear` @ `aad18a5` + 本卡未提交工作树（`worktree_dirty: true`）。
> **测量时间**：2026-10-26 20:33（Asia/Shanghai，UTC 12:33）。
> **机器**：macOS 27.0.1 arm64，Python 3.13.13，uv 0.13.0。
> **口径**：证据 = 实际命令输出；机器可读版本见同目录 `coverage-baseline.json`。

---

## 0. 摘要

| 项 | 数字 |
|---|---:|
| 总体行覆盖率 | **80.93%**（18425 / 22767） |
| 总体分支覆盖率 | **69.54%**（5157 / 7416） |
| 测试 collected / passed / skipped / xfail | **1644 / 1627 / 17 / 0**（failed 0） |
| 测量范围 | `src/rui` 全部 120 个 .py（source 枚举，含未导入；其中 5 个 0 覆盖） |
| 覆盖率门槛 | 无（本卡只建基线） |
| 比较基准 | `docs/audit/coverage-baseline.json`（schema `rui-coverage-baseline/v1`） |

> **为什么看不到 78%**：`pytest --cov-branch` 的 `TOTAL` 列会把「部分覆盖的分支」也算作未覆盖，
> 显示的是行 + 分支的合并值（本次 78.13%）。本基线按验收标准拆开记录：行 80.93% / 分支 69.54%。
> 两者不是矛盾，是 coverage.py 的两种呈现。

---

## 1. 各一级子包行覆盖率

由 `scripts/coverage_report.py` 从 `coverage.json` 生成（`(root)` = `src/rui/*.py` 顶层模块，
不是子包，单独列出以便加总核对）：

| scope | line % | covered / statements | branch % |
| --- | ---: | ---: | ---: |
| `(root)` | 82.94 | 851 / 1026 | 71.03 |
| `audio` | 80.72 | 3248 / 4024 | 65.60 |
| `channels` | 52.81 | 461 / 873 | 35.64 |
| `config` | 94.05 | 1139 / 1211 | 86.22 |
| `device` | 82.35 | 737 / 895 | 74.34 |
| `doctor` | 88.08 | 377 / 428 | 78.29 |
| `music` | 88.03 | 1529 / 1737 | 80.39 |
| `plugins` | 75.73 | 1470 / 1941 | 63.01 |
| `providers` | 72.83 | 839 / 1152 | 60.11 |
| `security` | 87.03 | 208 / 239 | 80.00 |
| `session` | 90.66 | 1573 / 1735 | 83.17 |
| `tools` | 77.54 | 1077 / 1389 | 64.77 |
| `web` | 80.37 | 4916 / 6117 | 68.40 |
| **total** | **80.93** | **18425 / 22767** | **69.54** |

最低的三个一级子包是 `channels`（52.81%）、`providers`（72.83%）、`plugins`（75.73%）；
它们自然是后续补测试的首选，但本卡**不**据此排期。

---

## 2. 测试计数口径（四个数，结束三口径漂移）

此前文档里 1512 / 1518 / 1571 三个数并存，但它们根本不是同一种东西：

- **1512** = 0.7.0 发布记录里的 passed 数（`docs/release/v0.7.0.md:39`）；
- **1518** = `pytest --collect-only` 的收集数（`docs/audit/2026-10-code-health.md:472`）；
- **1571** = `grep "def test_" tests --include=*.py | wc -l` 的静态函数计数，与
  parametrized 展开后的收集数不可比。

其中只有第二种是「测试节点数」，第三种连同一份代码的 pytest 汇总都对不上；从 0.7.3 起
不再把 1571 当口径。统一用本次命令的 JUnit XML 计算，只报四个数：

| collected | passed | skipped | xfail |
|---:|---:|---:|---:|
| **1644** | **1627** | **17** | **0** |

- `collected` = `pytest-report.xml` 中全部 `<testcase>`（含 skip/xfail），与
  `pytest --collect-only -q | awk -F': ' '/^tests\//{s+=$2} END{print s}'` 的 1644 一致。
- `skipped` 的 17 条全部是 `pytest.skip`（可选依赖/环境不满足），`xfail` 为 0。
- **1644 比本卡开工前的 1643 多 1**：`tests/test_plugin_compat_guard.py` 会扫描仓库内每个
  `plugin.yaml` 做参数化，本卡新增的 `tests/fixtures/sample-plug/plugin.yaml` 因此自动多出
  1 条 guard 测试。这是预期变化，不是口径漂移。
- 本次所有测试统一在 `uv sync --extra dev` 的环境下运行（与 CI 一致）；`kws` /
  `weixin-voice` 等可选 extra 未安装，相关用例按 skip 计。

---

## 3. 复跑命令

```bash
# 1) 产出覆盖率 + 四个测试计数（CI test job 跑的就是这条）
uv sync --extra dev
uv run pytest --cov=rui --cov-branch --cov-report=term-missing \
    --cov-report=json:coverage.json --junit-xml=pytest-report.xml

# 2) 从产物生成表格，并写出机器可读基线
uv run python scripts/coverage_report.py \
    --coverage-json coverage.json --junit-xml pytest-report.xml
# → 打印 §1 的表格；写入 docs/audit/coverage-baseline.json
```

本地为保持工作树干净，`coverage.json` / `pytest-report.xml` 曾写到 `/tmp/rui-cover/`；
CI 写到仓库根目录并作为 artifact 上传，路径不影响数字。

```bash
# 只算 collected（与 JUnit 的 testcase 数一致）
.venv/bin/python -m pytest --collect-only -q | awk -F': ' '/^tests\//{s+=$2} END{print s}'
# 1644
```

---

## 4. 后续版本如何比较（不得回退）

规则：**总体（行 + 分支）和每个已记录一级子包的（行 + 分支）都不得低于本基线**；
提升不设上限。比较值按两位小数取整，避免浮点噪声。

```bash
uv run pytest --cov=rui --cov-branch --cov-report=json:coverage.json \
    --junit-xml=pytest-report.xml
uv run python scripts/coverage_report.py \
    --coverage-json coverage.json --junit-xml pytest-report.xml \
    --compare docs/audit/coverage-baseline.json
# exit 0 = 无回退；exit 1 = 有回退（逐条打印 overall/包级差异）；exit 2 = 输入缺失
```

`--compare` 模式**不写文件**：检查动作永远不会覆盖基线。要更新基线，先跑不带
`--compare` 的生成命令，并把 `docs/audit/coverage-baseline.json` 与
`docs/audit/coverage-baseline.md` 的表格一起更新（脚本输出可直接粘贴到 §1）。

注意事项（比较前先读）：

1. 本基线在 **macOS / Python 3.13.13** 上测得；CI 在 ubuntu-latest 的 **3.11 / 3.12** 上跑，
   同一 commit 的绝对百分比可能有零点几的差异。跨 Python 版本比较时以 CI artifact 内的
   `coverage.json` 为准，或用同一 Python 版本重测。
2. 基线里没有 `failed` 门槛：`pytest` 本身非 0 退出应由 CI 拦住；`failed > 0` 时
   `scripts/coverage_report.py` 仍会输出数字，但那不是可比较的基线。
3. 包级比较只覆盖基线里已记录的包；新增一级子包不会被自动要求覆盖率，但删除/重命名
   已记录包会触发 `--compare` 的 "package missing" 回退。

---

## 5. CI 产出（待 Lead push 后复核）

`.github/workflows/ci.yml` 的 test job（Python 3.11、3.12 矩阵）现在：

- `setup-uv` 显式传 `with: python-version: ${{ matrix.python-version }}`（本卡补的；
  此前矩阵只写在名字里、没有交给解释器），并在 pytest 前执行
  `uv run python -V | tee python-version.txt`；
- 用 `--cov=rui --cov-branch --cov-report=term-missing` 跑全量测试，并额外写
  `coverage.json` + `pytest-report.xml`；
- 用 `actions/upload-artifact@v4` 上传 `coverage-${{ matrix.python-version }}`
  （内容为 `coverage.json`、`pytest-report.xml`、`python-version.txt`；`if: always()`，
  `if-no-files-found: warn`，避免测试失败时 artifact 报错盖住真实原因）；
- 新增 `rui plug-test` 冒烟（见 §6）。`image` job 未改动。

复核方式：push 后在该次 run 的 Artifacts 下载 `coverage-3.11` / `coverage-3.12`，
先读各自的 `python-version.txt`，确认分别为 `Python 3.11.x` 与 `Python 3.12.x`
（证明矩阵真的生效；`coverage.json` 的 `meta` 只记 coverage.py 版本，JUnit XML 没有
解释器属性，所以用这个文件作为证据），再用
`scripts/coverage_report.py --coverage-json coverage.json --junit-xml pytest-report.xml`
复算；数字应与 §0 同量级（允许平台/版本差异）。本地无法证明两个 CI job 的解释器，
这一条只能 push 后由 Lead 复核。

---

## 6. plug-test 冒烟（不依赖 plugs/xiaoai）

`plugs/xiaoai` 会在 0.7.5 删除，因此冒烟使用测试夹具
`tests/fixtures/sample-plug/`：

- `plugin.yaml`：`trust: trusted`、`rui_compat: ">=0.3.0 <0.8.0"`；
- `devices/demo.yaml`：`rui-device/v1`，只声明 `playback/volume/led`，wake 走 RUI KWS；
- `demo_adapter.py`：一个 16 kHz 单声道假设备，20 ms 静音帧 + 内存播放 sink，
  不含任何硬件/网络依赖。

CI 命令与真实输出：

```text
$ uv run rui plug-test tests/fixtures/sample-plug \
      --adapter tests/fixtures/sample-plug/demo_adapter.py:build
...
summary: pass=20 fail=0 warn=0 skip=1 -> PASS
exit=0
```

反向检查（故意删掉 `devices/demo.yaml` 后复制到 `/tmp`，不改仓库）：

```text
$ rui plug-test /tmp/rui-broken-plug --adapter /tmp/rui-broken-plug/demo_adapter.py:build
FAIL manifest.load    manifest not found: /private/tmp/rui-broken-plug/devices/demo.yaml
summary: pass=0 fail=1 warn=0 skip=0 -> FAIL
exit=1
```

`wake.inject` 为 SKIP 是设计选择：夹具不声明 `device_wake`，就不注入假唤醒事件，
避免为了「全绿」而伪造设备能力。该 SKIP 不影响退出码。
