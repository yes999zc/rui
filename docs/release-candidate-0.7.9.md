# 0.7.9 候选收口报告（release candidate）

> 性质：**决策材料**，不是宣传稿。9 哥 用它决定「这一批能不能发」。
> 写作时点：2026-10-27 12:18（Asia/Shanghai）；HEAD `f23e412`；上一个正式版 `v0.7.8`（`a3205e9`）。
> 本报告**不执行任何部署**；所有命令都可照抄复核。

## 1. 待发版内容（按可见变化分组）

### 1.1 行为变化（用户/运维能感觉到）

| 变化 | commit | 谁在什么场景会感觉到 | 需真机确认？ |
|---|---|---|---|
| **结束词 + 有歌 → 停歌且关会话（C1）** | `ddb7cbb` | 放歌时说「停止」「拜拜」「停止播放」的人：以前只停歌、会话继续听（`media_stop`），现在音乐停 **且** 会话关闭（`session_action="stop_and_dismiss"`） | **是**（验收 N1；9 哥 报的就是这个现象） |
| **`stop_intent` 只认整句（C2，有意收窄）** | `ddb7cbb` | 习惯说「请停止」「我要停止」「停止你好」「停止了吗」的人：这些句中/带疑问的说法**不再命中**，交给模型或澄清；`停止`/`停止播放`/`别放了吧` 仍命中（别名表） | 否（headless 可判定）；**日常说法变化需 9 哥 知情/接受** |
| **audio `close()` 不再掩盖真实错误** | `2977c3a` | 排障的人：子进程握手前死掉时，`/api/v1/audio.error` 显示 `audio plane refused: … RUI_MISSING_TOKEN …`，而不是 `[Errno 57] Socket is not connected` | 否 |
| **被吞的 `ENOTCONN` 留 DEBUG 行** | `8321099` | 开 DEBUG 排障的人：多一行 `audio plane: socket already gone on close: [Errno 57] …`；默认级别无变化 | 否 |
| **`mcp <name>: down` 日志节流** | `3ed312f` | 看日志的人：持续不可达的 MCP server 不再每 ~15 s 一条 INFO；首次/原因变化/恢复各一条，其余 DEBUG | 否 |
| **dot2 音量回读取 mixer 实际值** | `d2c9895` | 用 dot2 调音量的人：回读不再显示过期值 | 是（dot2） |
| **「唱首歌」走随机播放；旧链接落页有提示** | `e85b4bb` | 点歌的人与收藏旧链接的人：不再固定一首；旧入口不再死链 | 是（设备） |
| **控制台 HA 与 MCP 服务器分两段** | `91cac91` | 用 v2 控制台的人：Home Assistant 与 MCP 服务器分开呈现；`GET /api/v1/mcp` 语义未动 | 否（呈现层） |

### 1.2 有意收窄（单列，不能藏进正文）

**`stop_intent` 从「任意位置子串 + 总长最多 +2 字」改为「整句 = 结束词 / 显式别名 / 单个闭集语气词（吧了呀啊嘛）」**（`ddb7cbb`，见 `CHANGELOG.md` Unreleased「已知变更」）。

- 实测：`请停止=False`、`我要停止=False`、`停止你好=False`、`停止了吗=False`；`停止=True`、`停止播放=True`、`别放了吧=True`。
- 这是**有意行为变更**，不是回归：老用户可能觉得「以前能用，现在不理我」。发版说明必须写；9 哥 需知情。
- **需真机？** 否（语义已由 `tools/intent_matrix.py` 与 `tests/test_intent_stop_batch1.py` 判定）；但它是**日常说法变化**，需要 9 哥 明确接受。
- 同批补了三条别名：`停止播放→停止`、`别放了→别放`、`别放了吧→别放`（别名不是结束词表，结束词表未新增）。

### 1.3 测试与文档（开发者/CI 能感觉到）

| 变化 | commit | 谁感觉到 | 需真机？ |
|---|---|---|---|
| HE-01 告别回声回归测试 + 正向控制 | `1c5583a`、`f7b2484` | 开发者/CI：告别回灌不再开 turn，且不吞掉紧随其后的人话；真机 10 次告别仍待验（见 §4） | 否（headless 已覆盖；真机项另列 §4） |
| 意图层审计（11 条冲突 + 可复算矩阵） | `f5d3f46` | 9 哥/维护者：C1–C11 的判据与批次计划 | 否 |
| 控制台排序审计（v2 唤醒应答第 8 组） | `7fde6aa` | 9 哥/前端：ack 现状、tier 取舍、D-011 | 否 |
| CI 时长测量与缓存决定 | `3ea6726`（+`8321099` 的措辞修正） | 维护者：维持现状、不加缓存 | 否 |
| 覆盖率基线与提升方案 | `44fc0c7` | 维护者：行 81.30% / 分支 69.77%，6 个"下降"里 3 个真降 | 否 |
| 文档事实抽查 40 条（修 5 条） | `ed3b648` | 文档维护者：README/console/deploy/install/socket 的事实值 | 否 |
| 真机验收累计 + 已知限制累计 | `4f75079` | 9 哥：`docs/acceptance-pending.md`、`docs/known-limitations.md` | 否 |
| 待拍板 D-001…D-011 | `30ab5f2`、`5aa0073`、`b067b37`、`717df2a`、`cdc92cc` | 9 哥：发版/真机窗口/HE-02/ACK 位置等取舍 | 否 |

### 1.4 未进本批（不要宣称已修）

- **控制台「唤醒应答」顺序**：仍待 D-011 拍板（tier 2→1 的契约/测试代价）+ 前端改动；v2 现状仍是第 8 组。
- **意图层批次 2（C3/C4/C5）**：C3「无歌说暂停是否算结束」仍需拍板；暂停词表收窄、paused 语义未动。
- **HE-02 回声切除缺口**（lag 900–1200 ms 不削波、1200 ms 尾部/overlap）：task-10 被 D-010 阻塞，未修。

## 2. 稳定性

### 2.1 测试与静态检查（真实命令）

```text
$ uv run pytest
1708 passed, 6 skipped in 111.57s (0:01:51)
```

- **本批（`v0.7.8` → `HEAD f23e412`）**：`1644 passed, 6 skipped` → `1708 passed, 6 skipped`，**+64**。
- 其中 task-14 的 `tests/test_intent_stop_batch1.py` 收集 **36** 例；它的直接基线是 `ddb7cbb` 之前的 `4f75079`（`1672 passed, 6 skipped`），`1672 → 1708 = +36`。
- 其余 **+28** 来自 HE-01 回归、音量、MCP 日志、音乐随机等前序测试。
- 两个基线都实测（命令见下）；`tests/test_session.py` 的 2 条旧断言随行为改写（`今天下课` True→False；`media_stop` 用例改为 `stop_and_dismiss`、`dismissed=1`、`reply_finished=0`），**不新增用例**。

```bash
MAIN=/path/to/house-ear
git -C "$MAIN" worktree add /tmp/rui-0.7.8 v0.7.8
(cd /tmp/rui-0.7.8 && "$MAIN/.venv/bin/python" -m pytest --tb=no | tail -1)
# 1644 passed, 6 skipped in 112.40s (0:01:52)

git -C "$MAIN" worktree add /tmp/rui-4f75079 4f75079
(cd /tmp/rui-4f75079 && "$MAIN/.venv/bin/python" -m pytest --tb=no | tail -1)
# 1672 passed, 6 skipped in 114.70s (0:01:54)

git -C "$MAIN" worktree remove /tmp/rui-0.7.8
git -C "$MAIN" worktree remove /tmp/rui-4f75079
```
- 本批**没有放宽断言**：删除的断言均有更强/更精确的替代（短语本 50→51 是新增默认话术；`stop_intent` 由 True 改 False 是收紧；`media_stop` 改为 `stop_and_dismiss` 是行为对齐）。证据：`git diff v0.7.8..HEAD -- tests`。

```text
$ uv run ruff check src tests
All checks passed!
$ uv run python tools/intent_matrix.py > /tmp/im1.txt; uv run python tools/intent_matrix.py > /tmp/im2.txt
$ diff /tmp/im1.txt /tmp/im2.txt   # 无输出（byte-identical）
md5 /tmp/im1.txt = 992b198dc950bfc481b2616acc06f7be
```

### 2.2 CI（写作时 12:18 快照；收口时已复测四绿）

写作时 `gh run list` 原文：

```text
$ gh run list -L 8 --json name,headSha,status,conclusion
f23e412  security        completed  success
f23e412  secret-scan     completed  success
f23e412  ci              in_progress
f23e412  docker-publish  in_progress
ddb7cbb  secret-scan     completed  success
ddb7cbb  security        completed  success
ddb7cbb  docker-publish  completed  cancelled   # 被后续 push 顶掉，不是失败
ddb7cbb  ci              in_progress
```

- 上一个文档提交 `4f75079` 四个 workflow **全部 success**（ci/security/secret-scan/docker-publish）。
- **收口时已复测**：`f23e412` 四项全部 **success**（security 12:18:07、secret-scan 12:17:25、ci 12:22:53、docker-publish 12:22:11）。`ddb7cbb` 的 docker-publish 是 **12:17:43 cancelled**（被后续 push 顶掉，非失败），ci 12:20:28 success。
- **口径保留**：`cancelled` 是并发 run 被顶掉，不算失败，但也不能当作绿灯。

### 2.3 已知 flaky

- 本批修掉了唯一已知 flaky：`test_plane_failure_is_reported_with_its_reason`（`2977c3a`）。证据（task-1）：单跑 10/10、全量 3/3 绿；变异回退后测试必红；另用修复前文件复跑同样红。其余无已知 flaky。

## 3. 文档

| 文档 | 解决什么问题 | 谁该读 |
|---|---|---|
| `docs/audit/intent-collisions.md`（`f5d3f46`） | 11 条意图层冲突 = P0 2 / P1 3 / P2 6，含可复算 `tools/intent_matrix.py` 与批次计划 | 9 哥、改意图层的开发者 |
| `docs/audit/console-ordering.md`（`7fde6aa`） | v2 语音助手页 8 组现状、ack 第 8、tier 取舍（D-011）、双入口结论 | 9 哥、前端 |
| `docs/audit/ci-timings.md`（`3ea6726`） | 四个 workflow 的真实时长与「维持现状」决定 | 维护者 |
| `docs/audit/coverage-plan-2026-10-27.md`（`44fc0c7`） | 覆盖率 81.30% / 69.77% 与基线对比、6 个下降中 3 个真降、提升排期 | 维护者 |
| `docs/audit/doc-fact-check-2026-10-27.md`（`ed3b648`） | 40 条文档↔代码事实核查，修正 5 条 | 文档维护者 |
| `docs/acceptance-pending.md`（`4f75079`） | 21 条「只能真机」+ HE-01 补充 + 5 条本阶段新增，含可照抄命令/判据/窗口顺序 | **9 哥（发版前必读）** |
| `docs/known-limitations.md`（`4f75079`） | 9 条已确认限制（HE-02、C3、C4–C11、ack、tier、T3 阈值、HE-01②、legacy/v2、结束词+有歌旧行为） | 9 哥、发版说明维护者 |
| `docs/待拍板.md`（`30ab5f2`/`5aa0073`/`b067b37`/`717df2a`/`cdc92cc`） | D-001…D-011 的背景/选项/代价/建议 | 9 哥 |
| `CHANGELOG.md` Unreleased（`599bb71`、`ddb7cbb`） | 本批「已知变更」与修复；发版说明的母本 | 发版执行者 |

## 4. 验收清单（引用，不复制）

**本批未做任何真机测试。** 完整清单与命令见 `docs/acceptance-pending.md`：

- **21 条「只能真机」**（A 14 / B 3 / C 1 / D 3）：音量、LED、LX06 指令/唤醒、Dot2 矩阵、首声延迟、远场、音乐中指令、primeWait、ERLE/RT60、长稳、部署、TLS、盲听、NAS 断电/重连/24 h。
- **HE-01 补充**（不计入 21）：真机 10 次告别，判据「0 个额外 turn」。
- **本阶段新增 5 条**：**N1** 有歌说「停止/拜拜/停止播放」→ 停歌且关会话；**N2** `停止你好` 不再误命中；**N3**「暂停/停一下」只暂停（C3 拍板后）；**ACK-1/2** 唤醒应答首屏第 1 组（D-011 拍板后）。
- **推荐窗口**：A 组 4–5 h 清 14 条（顺带 HE-01/N1/N2）；+X-07 0.5 h；B 组 2–3 h 清 3 条；D 组现场 1 h 清 2 条 + HE-08 过夜 24 h 读 1 条；控制台 ACK 5–10 min（拍板后）。
- **N1/N2 需要 9 哥 上手**；其中「有歌说结束词」正是他报的那个现象。

## 5. 回滚步骤（可直接照抄；本报告不执行任何部署）

### 5.1 只回代码（部署上一个正式版）

```bash
git fetch --tags
git checkout v0.7.8        # 上一个正式版；或 git worktree add /tmp/rui-0.7.8 v0.7.8
# 按仓库 build 流程重建镜像/二进制；本报告不执行
```

> 不建议逐条 `git revert` 本批：行为改动（`ddb7cbb`）同时改了测试与 CHANGELOG，单条 revert 容易冲突；回 0.7.8 tag 是可复现路径。若必须保留 main 历史，可只 revert 行为提交：`git revert --no-edit ddb7cbb`，并重新跑 `uv run pytest`。

### 5.2 回镜像（从候选 0.7.9 回 0.7.8）

```bash
cd /vol1/rui
# 1) 拉上一个正式版，并取本机看到的 amd64 平台摘要
docker pull ghcr.io/yes999zc/rui:0.7.8
docker image inspect ghcr.io/yes999zc/rui:0.7.8 --format '{{index .RepoDigests 0}}'
# 已知的生产 0.7.8 amd64 摘要（HANDOFF 记录，可用上面命令复核）：
#   ghcr.io/yes999zc/rui@sha256:1f074bb3af1ef4854ef040b12c9146ed8a6ef2b1c4a5f6cc7858ac6ccac3ecaa

# 2) 备份并替换 override 的 image
cp rui-dot2-override.yml rui-dot2-override.yml.bak.beforerollback
sed -i 's#^\(\s*image:\s*\).*#\1ghcr.io/yes999zc/rui@sha256:<填上面摘要>#' rui-dot2-override.yml
grep -n "image:" rui-dot2-override.yml

# 3) 必须两个 -f
docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d --force-recreate
curl -s localhost:8080/health | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['version'],d['devices'],d['device_errors'],d['console'])"
```

**警告（按 `docs/deploy-runbook-0.7.8.md` §8 与生产实测）**：

- **NAS 的老 docker 只认 amd64 平台摘要**。不要用 GHCR 页面上的 OCI index 摘要（`sha256:ca3b40e…` 那类）——按索引 `pull` 会报 `unsupported media type`。用 `docker image inspect … RepoDigests` 在 amd64 NAS 上取到的那一条。
- `docker compose` **必须带两个 `-f`**；少一个会读 `.env` 里过期的 `RUI_IMAGE`，起错版本。
- 期望回读：`0.7.8`、`devices=2`、`device_errors=0`（0.7.4 起两个桥都走外挂目录，无需换镜像结构）。
- 只回滚控制台入口（保留新版本）：把 config 的 `web.console` 改回 `legacy` 再 `up -d`。
- **本报告不执行任何部署**；上述命令由 9 哥 在窗口内执行。

## 6. 能不能发（一句结论 + 条件）

**可以发；但以下 3 条任一不满足就退回 0.7.8。**

1. **CI 在最终提交四绿**。写作时快照是「在跑」，**收口时已复测 `f23e412` 四项全部 success（12:22）**；`cancelled` 不算绿（`ddb7cbb` 的 docker-publish 是被后续 push 顶掉的），这条口径保留。
2. **N1/N2 在真机窗口验过**（有歌说「停止/拜拜/停止播放」→ 停歌且关会话；`停止你好` 不误命中）。若窗口排不进来，就**不能宣称意图层已修**，只能作为「未验行为变更」发布——建议不要这样发。
3. **接受 C2 的有意收窄**：`请停止`/`我要停止`/`停止你好`/`停止了吗` 不再命中。同时发版说明**不得**写「唤醒应答已提到第 1 位」或「HE-02 回声已修」——那两项分别卡在 D-011 与 D-010。

> 补充：本批不含控制台 ACK 顺序（D-011）、意图层批次 2（C3/C4/C5）、HE-02 修复（D-010）；它们都在 `docs/known-limitations.md` 与 `docs/acceptance-pending.md` 里有对应条目，不要在本批发版说明里越界承诺。
