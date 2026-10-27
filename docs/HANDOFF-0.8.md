# HANDOFF — 0.8.0 计划与未决问题（2026-10-27）

> 本文件**只写计划与未决问题**，不写历史流水账。历史看 `docs/HANDOFF.md`。
> 详细卡面与写范围在 `docs/ROADMAP-0.8.md`；本文件是接手 0.8.0 时先读的那一页。

## 1. 定位（已定，不讨论）

- **0.8.0 只做破坏性变更**：删兼容层、抬契约窗口、改 API 语义。不夹带新功能。
- **发布顺序不可反**：两个桥先发 **双窗口** 版本（`rui_compat` 同时接受 0.7.x 与 0.8.x），core 后发 0.8.0。
- **0.7.8 是上一个验收节点**；0.7.8 之后进 main 的修复在 0.8.0（或单独 patch）才进镜像，不为单条修复升 minor。

## 2. 范围（逐条可验收）

| # | 事项 | 判据 | 依赖 |
|---|---|---|---|
| 1 | 删 `/legacy` 面板与**全部 15 条**老链接 302 | `curl` 全矩阵：`/legacy` 与 `/legacy/**` 返回 404；15 条老链接不再 302；v2 单入口 | 前端与 `legacy_links.py` 同批 |
| 2 | 删 `adapter: xiaoai` 的**读入迁移与弃用告警** | `config/store.py` 无该改写；`/health.adapter_plugins.warnings` 不再因旧拼写出现；老配置显式报错并给出修复句 | 桥侧已全部 `lx06` |
| 3 | 契约窗口抬高 | 三仓 `rui_compat` 同窗口（上界 `0.9.0` 或按 C-003 重定）；三仓 CI 绿 | 桥先发 |
| 4 | 迁移与 breaking notes | 文档含「老配置会怎样报错」「如何改」；release notes 中英双语 | 1–3 完成后写 |
| 5 | **HA 从 MCP 实例模型摘出**（API 语义变更，只登记） | 若做：`GET /api/v1/mcp` 不再返回 `kind: "first_party"`，HA 走独立端点，契约 C-002 同步改 | 需 9 哥 重新拍板 |

## 3. 未决问题（需要 9 哥 拍板才动）

1. **HA 摘出要不要在 0.8.0 做**？做=改 API + 契约 + 前端；不做=控制台维持「两段展示、一个端点」的现状（0.7.8 就是这么做的）。
2. **在线音乐 provider 的配置字段名**：`MusicSource` Protocol 已有五个方法，新增 provider 挂在哪个配置键下未定（`music.providers[]`？`music.sources[]`？）。
3. **36 条「未验证」里 26 条真机协议**：继续测、转已知限制、还是删掉清单条目？影响 0.8.0 的 release notes 措辞。
4. **MCP server 端**：**已否决**，不在 0.8.x 范围。若以后要做需重新拍板，不要当成遗留缺口。

## 4. 打磨（与 0.8.0 并行或之后，按可验证项）

1. flaky：`tests/test_audio_api.py::test_plane_failure_is_reported_with_its_reason`（socket close 竞态）。
2. 日志噪音：`mcp ha: down` 每 15 s 一条，应节流。
3. 覆盖率：不得低于 `docs/audit/coverage-baseline.md`。
4. 错误文案中英一致性；phrasebook 目前只有中文（`phrases.py:31`），要做英文语音需 keyed 多语言表。
5. 升级/回滚演练按 `docs/deploy-runbook-0.7.8.md` §8 真跑一遍并记录。

## 5. 明确不做

- 不做对外宣传/公告（除非 9 哥 明说）。
- 不做 `runner.abort()` 跨房隔离（单会话限制写进已知限制）。
- 不把在线音乐做成 VIP 换源或聚合解析站（合规边界见 README 与调研结论）。
- 不动 `docs/security.md` 的 CIDR。

## 6. 出口条件（Release gate）

1. 三仓 tag + GH release + GHCR 镜像（记 amd64 平台摘要）。
2. 生产升级 + 回滚各一次，且有真实输出。
3. `docs/HANDOFF.md` 的「新会话入口」按本版事实重写。
4. review PASS + verify VERIFIED，无半绿卡。
