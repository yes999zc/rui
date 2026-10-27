# CI 时长测量与缓存决定（四个 workflow，2026-10-27 快照）

> 目的：打磨项 e-2 / 打磨清单 C —— 四个 workflow 全绿但没人量过时长。本卡**只测量 + 写决定**，
> 不改任何 `.github/workflows/*`。数据全部来自 `gh run list` / `gh run view --json jobs`（只读，未触发新 run）。
>
> **决定：维持现状** —— 不加 `pytest cache dir`，不拆矩阵/分片；理由见 §6。
> 唯一值得单独立卡的候选（ci 的 `image` job 复用 gha 缓存）记录在 §7，本卡不做。

- 仓库：`yes999zc/rui`（public），默认分支 `main`
- 快照时点：**2026-10-27T02:31Z**（本地 10:31 +08:00）；快照之后产生的 run 不在样本内
- 样本口径：每个 workflow **最近 5 次 `success`** 的 run（run 级墙钟 + job 级耗时）
- 历史窗口：每 workflow 抓最近 40 次 run，覆盖 `2026-10-26T18:15Z` → `2026-10-27T02:31Z`（约 8h15m；
  这些 run 全部由 push 触发，即窗口内约 40 次 push，属于密集提交/排队偏重的时段，见 §8）

## 1. 结论速览

| workflow | 5 次 success 墙钟范围 | 均值 | 长杆在哪 |
|---|---|---|---|
| ci | 4:00 – 5:45 | **5:00** | `test (3.11)`/`test (3.12)` 的 pytest 步（233–278 s）与无缓存的 `image` docker build（211–341 s）并行，二者交替成为关键路径 |
| security | 0:42 – 1:55 | **1:14** | Trivy 镜像扫描 job（40–83 s，镜像构建已命中 gha 缓存）；其中 53 s 是排队（见 §5） |
| secret-scan | 0:07 – 0:09 | **0:08** | 纯 `git grep`，无外部依赖 |
| docker-publish | 3:28 – 5:07 | **4:18** | 串行链：`test`（均值 166 s）→ 两条缓存命中的 build（均值 49 / 39 s）→ merge（均值 18 s） |

四个 workflow 中没有单条超过 6 分钟。ci 与 docker-publish 的 5 分钟里，长杆是 pytest 与「先测后发」的串行链；
缓存还能再挤出的只有 ci `image` 的一部分（§7，平均约 30 s），改变不了量级（§4/§6）。

## 2. Run 级：最近 5 次 success

时间均为 **UTC**；耗时 = `updatedAt - createdAt`（含排队，口径见 §8）；conclusion 为该 run 结论。
本表按卡面「最近 5 次 **成功** run」取样；同一时段的非 success run 见 §5.2，不进本表。

| workflow | run id | created (UTC) | 耗时 (m:ss) | conclusion | sha7 |
|---|---|---|---|---|---|
| ci | 40435976985 | 2026-10-27 02:01:15 | 5:04 | success | 22df703 |
| ci | 40435918745 | 2026-10-27 02:00:43 | 4:32 | success | da91b57 |
| ci | 40433149505 | 2026-10-27 01:32:15 | 5:45 | success | 81be912 |
| ci | 40432569982 | 2026-10-27 01:26:24 | 4:00 | success | e85b4bb |
| ci | 40431326583 | 2026-10-27 01:13:49 | 5:37 | success | dc5b788 |
| security | 40435976819 | 2026-10-27 02:01:15 | 1:55 | success | 22df703 |
| security | 40433149495 | 2026-10-27 01:32:15 | 0:59 | success | 81be912 |
| security | 40432570245 | 2026-10-27 01:26:25 | 1:27 | success | e85b4bb |
| security | 40431326627 | 2026-10-27 01:13:49 | 1:09 | success | dc5b788 |
| security | 40430792417 | 2026-10-27 01:08:34 | 0:42 | success | e08dbf7 |
| secret-scan | 40438812727 | 2026-10-27 02:30:23 | 0:09 | success | 3ed312f |
| secret-scan | 40435976845 | 2026-10-27 02:01:15 | 0:08 | success | 22df703 |
| secret-scan | 40435918810 | 2026-10-27 02:00:43 | 0:09 | success | da91b57 |
| secret-scan | 40433149404 | 2026-10-27 01:32:15 | 0:07 | success | 81be912 |
| secret-scan | 40378826352 | 2026-10-26 18:33:46 | 0:09 | success | eb07549 |
| docker-publish | 40435977080 | 2026-10-27 02:01:15 | 5:07 | success | 22df703 |
| docker-publish | 40433149412 | 2026-10-27 01:32:15 | 3:28 | success | 81be912 |
| docker-publish | 40432570024 | 2026-10-27 01:26:24 | 4:32 | success | e85b4bb |
| docker-publish | 40431326604 | 2026-10-27 01:13:49 | 3:31 | success | dc5b788 |
| docker-publish | 40430792537 | 2026-10-27 01:08:34 | 4:52 | success | e08dbf7 |

注：
- secret-scan 的第 5 次 success 要回溯到 `eb07549`（10-26 18:33），因为中间隔着 28 次 failure（真实泄漏，
  见 §5）；failure 不属 success 样本，只在 §5 定性说明。
- 快照时 `3ed312f` 的 ci / security / docker-publish 仍在跑，未计入本表（security 于 02:31:58Z 完成，
  刚好在快照之后；docker-publish 于 02:33Z 被更新的 push 取消）；其 secret-scan 已完成（0:09）计入。

## 3. Job / matrix 级耗时

### 3.1 ci：两腿 + image（本卡硬性要求）

`gh run view <run-id> --json jobs` 的 `startedAt → completedAt`（秒）：

| run id | test (3.11) | test (3.12) | image | run 墙钟 |
|---|---|---|---|---|
| 40435976985 | **291 s** (4:51) | **245 s** (4:05) | 301 s (5:01) | 5:04 |
| 40435918745 | **269 s** (4:29) | **217 s** (3:37) | 269 s (4:29) | 4:32 |
| 40433149505 | **287 s** (4:47) | **298 s** (4:58) | 341 s (5:41) | 5:45 |
| 40432569982 | **236 s** (3:56) | **212 s** (3:32) | 211 s (3:31) | 4:00 |
| 40431326583 | **238 s** (3:58) | **217 s** (3:37) | 333 s (5:33) | 5:37 |
| **均值** | **264.2 s (4:24)** | **237.8 s (3:58)** | **291.0 s (4:51)** | 299.6 s (5:00) |

- 两腿差异 26 s（3.11 均值 264 s vs 3.12 均值 238 s），**没有稳定占优的一腿**（`40433149505` 上 3.12 反而慢 11 s），
  差异量级与 hosted runner 抖动同阶；不构成「删掉一腿」的依据。
- run 墙钟 ≈ max(三 job) + 3–5 s 尾巴：三条 job 并行，关键路径由最慢那条决定。

原始输出（可复跑）：

```text
$ gh run view 40435976985 --json jobs --jq '.jobs[] | [.name, .startedAt, .completedAt, (((.completedAt|fromdateiso8601)-(.startedAt|fromdateiso8601))|tostring)] | @tsv'
test (3.12)	2026-10-27T02:01:17Z	2026-10-27T02:05:22Z	245
image	2026-10-27T02:01:17Z	2026-10-27T02:06:18Z	301
test (3.11)	2026-10-27T02:01:18Z	2026-10-27T02:06:09Z	291
```

### 3.2 其余三个 workflow 的 job 耗时

| workflow | run id | job 耗时（秒） |
|---|---|---|
| security | 40435976819 | Trivy image scan (amd64) 62；pip-audit 11 |
| security | 40433149495 | Trivy 56；pip-audit 12 |
| security | 40432570245 | Trivy 83；pip-audit 12 |
| security | 40431326627 | Trivy 66；pip-audit 13 |
| security | 40430792417 | Trivy 40；pip-audit 12 |
| secret-scan | 40438812727 / 40435976845 / 40435918810 / 40433149404 / 40378826352 | scan 5 / 5 / 7 / 5 / 5 |
| docker-publish | 40435977080 | test 173；build amd64 55；build arm64 50；merge 12 |
| docker-publish | 40433149412 | test 158；build arm64 23；build amd64 23；merge 19 |
| docker-publish | 40432570024 | test 168；build arm64 49；build amd64 74；merge 20 |
| docker-publish | 40431326604 | test 159；build amd64 27；build arm64 22；merge 17 |
| docker-publish | 40430792537 | test 171；build arm64 50；build amd64 67；merge 20 |

均值：security Trivy 61.4 s / pip-audit 12 s；docker-publish test 165.8 s / amd64 49.2 s / arm64 38.8 s / merge 17.6 s。

docker-publish 的 job 原始输出：

```text
$ gh run view 40435977080 --json jobs --jq '.jobs[] | [.name, .startedAt, .completedAt, (((.completedAt|fromdateiso8601)-(.startedAt|fromdateiso8601))|tostring)] | @tsv'
test	2026-10-27T02:02:17Z	2026-10-27T02:05:10Z	173
build (amd64, ubuntu-latest)	2026-10-27T02:05:12Z	2026-10-27T02:06:07Z	55
build (arm64, ubuntu-24.04-arm)	2026-10-27T02:05:15Z	2026-10-27T02:06:05Z	50
merge	2026-10-27T02:06:09Z	2026-10-27T02:06:21Z	12
```

## 4. 时间花在哪个 step（抽样 3 个 run 的 7 个 job）

命令：`gh api repos/yes999zc/rui/actions/jobs/<job-id> --jq '.steps[] | ...'`（job id 从 `gh run view --json jobs` 取）。

| job | 总耗时 | 最大 step | 其余 |
|---|---|---|---|
| ci `test (3.11)` @40435976985 | 291 s | **pytest 278 s**（95%；日志行首尾 277 s） | checkout 2 / setup-uv 3 / uv sync **1** / ruff <1 / plug-test 1 / artifact 2 |
| ci `test (3.12)` @40435976985 | 245 s | **pytest 233 s**（95%） | checkout 2 / setup-uv 3 / uv sync **1** / 其余约 6 |
| ci `image` @40435976985 | 301 s | **docker build-push 268 s**（89%，无 gha 缓存） | setup-buildx 20 / checkout 2 / posts 8 |
| docker-publish `test` @40435977080 | 173 s | **pytest 159 s**（92%） | checkout 3 / setup-uv 3 / uv sync **3** / ruff <1 |
| docker-publish `build (amd64)` @40435977080 | 55 s | **docker build 39 s**（gha 缓存命中） | setup-buildx 4 / checkout 2 / posts 8 |
| docker-publish `build (arm64)` @40435977080 | 50 s | **docker build 33 s**（gha 缓存命中） | setup-buildx 5 / checkout 2 / posts 8 |
| security `Trivy image scan` @40435976819 | 62 s | build 20 / Trivy scan 11 | setup-buildx 6 / SARIF upload 8 / posts 12 |

结论：**ci 的 test 腿就是 pytest 本身**（233–278 s），装依赖只花 1–3 s；**docker build 是最重的单步**，
而它在 docker-publish 里已经命中缓存（33–39 s），在 ci 的 `image` job 里没有（268 s）。

## 5. 缓存现状（实测）与排队/取消的干扰

### 5.1 缓存已经在生效

- ci / docker-publish 的 `setup-uv`：日志里 `enable-cache: auto`、`cache-dependency-glob: **/uv.lock`，
  `uv sync --extra dev` 实测 **1 s（ci）/ 3 s（docker-publish）**。缓存键随 `uv.lock` 走，命中即秒级。
- security 的 pip-audit job 也开 `enable-cache: true`（workflow 里显式写死 uv 版本 0.13.0）。
- docker-publish / security 的 `docker/build-push-action` 有 `type=gha` 缓存；日志实测
  `importing cache manifest from gha:...` + 多层 `CACHED`，build 步 **23–74 s**（历史注释里的 arm64 模拟 40+ 分钟已不存在）。
- **唯一没有 docker 缓存的**是 ci.yml 的 `image` job（没有 `cache-from`/`cache-to`），每次全量 build 步 268 s（job 211–341 s），见 §7。

### 5.2 排队和取消会污染 run 墙钟

最近一条非 success run（每 workflow 一条，快照时点）：

| workflow | run id | created (UTC) | conclusion | 说明 |
|---|---|---|---|---|
| ci | 40421456600 | 2026-10-26 23:41:30 | failure | 开发期失败（当日更早还有 13 次），最近 5 次已完成 run 均 success |
| security | 40435918704 | 2026-10-27 02:00:43 | cancelled | 被 32 s 后推送的 22df703 取代（concurrency） |
| secret-scan | 40432570093 | 2026-10-27 01:26:24 | failure | 真实泄漏 finding（见下） |
| docker-publish | 40435918826 | 2026-10-27 02:00:43 | cancelled | 被 32 s 后推送的 22df703 取代（concurrency） |

- 同一时间 4 个 workflow 各起 job（一次 push ≈ 11 个 job）抢 hosted runner：
  - ci 的 3 个 job 通常在 run 创建后 **2–3 s** 内开始；
  - security 在 02:01 那波排队 **53–54 s**（01:32 那波只有 3 s），所以它 1:55 的墙钟里有半分钟不是扫描；
  - docker-publish 的 build 腿必须等 `test`（`needs`），start 时间 = test 结束 + **2–5 s**；merge 再 +2–3 s。
    串行链本身是设计（先测后发布、双架构都成功才 merge tag）。
- **cancelled 是设计行为，不是失败**：`docker-publish`/`security` 都有
  `concurrency: cancel-in-progress: true`。窗口内 docker-publish 40 次 run 里 23 次 cancelled，例如
  `40435918826`（02:00:43 的 da91b57）在 22df703 于 02:01:15 推送后被取消；快照之后 `3ed312f` 的 publish
  也在 02:33Z 被更新的 push 取消。语义正确：只有最新提交需要发布镜像。
- **secret-scan 的 failure 也不是 flaky**：窗口内 28 次 failure 集中在 23:41–01:26，抽查 4 次
  （`40421435684` 23:41 / `40425670304` 00:19 / `40430498828` 01:05 / `40432570093` 01:26）**均为同一个
  真实发现**：`docs/deploy-runbook-0.7.8.md:4`（另含 `:30`）的 RFC1918 地址（soft 类），由 `81be912`
  「replace the real addresses with placeholders」修复后才转绿。`gh run view <id> --log-failed` 里可见
  `::error::possible credential material in tracked files`。扫描本身只要 5–7 s，删/缩这条检查没有收益。

## 6. 决定：维持现状

**不加 `pytest cache dir`，不拆矩阵/分片，不改任何 workflow 的缓存配置。**

理由（都对应上面的实测数字）：

1. **时长已被压到可接受区间**：四条 workflow 由同一 push 触发、并行执行，样本内同一 sha 最慢一条 5:45
   （81be912 的 ci；22df703 上四条分别是 5:04 / 1:55 / 0:09 / 5:07）。接近 5 分钟的两条，
   长杆都是 pytest 或「先测后发」的串行链，不是缺失的缓存。
2. **缓存该加的都加了，且已命中**：`uv` 依赖 1–3 s、docker 层命中时 build 33–39 s。再加缓存能省的只是
   ci `image` 那 268 s 的一部分（§7），而它常常不是关键路径（test 腿 236–291 s 与之交替）。
3. **`pytest cache dir` 对 CI 没有收益**：`.pytest_cache` 里只有 `lastfailed`/`nodeids`，价值在 `--lf/--ff`；
   fresh runner 上要么没有可恢复状态，要么恢复出上一提交的 `lastfailed`，用它跳测等于**少跑测试**，不安全。
   上传/下载 artifact 反而给每个腿加 I/O。pytest 步 233–278 s 是整份测试套件的真实执行时间，缓存改不了。
4. **拆矩阵/分片的账不划算**：要把 278 s 的 pytest 砍到 ~140 s，需要在 2 个 Python 腿上再各切 2 片
   （4 个 test job + image），runner 分钟数约翻倍，还要做 coverage 分片合并、JUnit 合并；
   换来的是 ci 关键路径约 2 分钟。而当前 push 是密集爆发型（8h15m 内约 40 次），
   排队已经会随机吃掉 0–60 s，分片收益会被排队噪声部分抵消。单开发者项目现阶段不值得。
5. **不改 docker-publish 的发布门禁**：`test` job 166 s 是「镜像发布前必须过测试」的保证；
   去掉它能省 2 分半，但会让镜像在 ci 还没绿时就推上 GHCR —— 安全换速度，不做。
6. **保留 3.11 + 3.12 双解释器**：26 s 的腿间差异与 runner 抖动同阶，没有证据支持砍掉 3.11。

**不做的事**（本卡明确不碰）：不改 `.github/workflows/*`；不加 pytest cache；不拆矩阵/分片；
不改 docker-publish 的 `concurrency`、`needs` 或 test 门禁；不删 ci 的 `image` job（PR 仍需要 docker 构建验证）；
不缩短 secret-scan / security。

## 7. 唯一值得单独立卡的候选（记录，不在本卡做）

`ci.yml` 的 `image` job 没有 `cache-from`/`cache-to`，每次从零构建（**211–341 s，均值 291 s**，
run 40435976985 上 build 步 268 s）。同一个 Dockerfile 在 docker-publish 里命中 `type=gha` 缓存时 build 只需
**33–39 s**，security 的 Trivy job 也复用了默认 scope。

- 预期收益：若 ci `image` 复用 `type=gha,scope=amd64`（与 docker-publish 同 scope），ci 关键路径从
  「max(test 腿, image)」变为「test 腿」；按 5 次样本回算，单次省 **10/0/43/0/95 s，平均约 30 s**（ci 墙钟均值 5:00 → 约 4:30），
  并省掉一次全量构建的 CPU/网络。
- 为什么现在不做：收益约 10%（约 30 s / 5:00），且实现要改 ci.yml，属于「改 CI」——按本卡已定决策另立卡。
- 触发条件：ci 反馈时延成为日常瓶颈，或 gha 缓存命中率明显下降时，另立卡实施。

## 8. 数据口径与局限

1. **run 墙钟** = `updatedAt - createdAt`（GitHub 的 run 起止，含排队、抢 runner、job 间依赖等待）；
   **job 耗时** = `startedAt - completedAt`（不含排队）。两者不可混比：security 的 1:55 里有 53 s 排队，job 本身仅 62 s。
2. **只对 success 计时**（按卡面要求）；cancelled（设计内并发取消）与 failure（开发期）只定性说明，不进均值。
3. 样本是 hosted runner（`ubuntu-latest` / `ubuntu-24.04-arm`），跨 run 有 10–30 s 抖动（如 secret-scan 5–7 s、
   security 40–83 s）；§3 的均值只代表本快照窗口。
4. 窗口 `2026-10-26T18:15Z → 2026-10-27T02:31Z` 是密集提交期，排队偏重；安静时段墙钟可能更低，
   因此本文的结论对「要不要缓存/分片」是偏保守的（没有低估收益）。
5. 快照后（`30ab5f2` 起）的新 run 不在样本内；文中 run id 均可单独用 `gh run view` 复核。
   同一 40-run 窗口里更早还有开发期失败（ci 14 次、docker-publish 6 次，集中在 26 日 18:1x–18:33
   与 23:41–23:54），不属于本表的 success 样本，本文不逐一分析。
6. 未验证项：cache hit rate 的长期趋势只有 GitHub Actions 的 cache 页面能看，本文只记录了单次日志证据
   （`enable-cache: auto`、`importing cache manifest from gha:...`、`CACHED`），未做多日命中率统计。

## 9. 复跑命令

```bash
# run 级：每个 workflow 最近 7 次（含 in-progress/cancelled 上下文）
for wf in ci security secret-scan docker-publish; do
  gh run list --workflow "$wf.yml" --limit 7 \
    --json databaseId,createdAt,updatedAt,conclusion,status,headSha \
    --jq '.[] | [.databaseId, .createdAt, (if .conclusion == "" then .status else .conclusion end), .headSha[0:7]] | @tsv'
done

# job / matrix 级（把 <run-id> 换成 §2 的任意一行）
gh run view <run-id> --json jobs \
  --jq '.jobs[] | [.name, .startedAt, .completedAt,
        (((.completedAt|fromdateiso8601)-(.startedAt|fromdateiso8601))|tostring)] | @tsv'

# step 级（job id 来自上一条输出里的 .databaseId）
gh run view <run-id> --json jobs --jq '.jobs[] | [.name, .databaseId] | @tsv'
gh api repos/yes999zc/rui/actions/jobs/<job-id> \
  --jq '.steps[] | [.number, .name, .startedAt, .completedAt, .conclusion] | @tsv'
```

只读约定：以上命令均不触发 workflow、不改仓库文件。本文不含任何真实内网地址或凭证（泄漏样例以
「RFC1918 地址」描述，不复制原值）。
