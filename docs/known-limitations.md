# 已知限制累计（known-limitations）

> 用途：列出**当前代码真实存在**的限制（不是「未验证」）。发版说明、README「已知限制」段应引用本文件，不再各自维护一份。
> 维护约定：**只增不改语义**；解除时标「已解除 @ commit」并保留原条目，不删号。引用一律用**文件 + 符号/小节 + commit**，不写会漂移的行号。
> 统计口径：本文件只收「已确认存在」的行为缺口/取舍；「等真机才能确认」的 21 条（另有 HE-01 补充与 5 条新增）见 `docs/acceptance-pending.md`。

| # | 限制 | 影响 | 现状依据 | 计划指向 |
|---|---|---|---|---|
| L1 | **HE-02：cue 自回声在 lag 900–1200 ms 不削波、1200 ms 削波尾部、1200 ms overlap 时漏切** | 音箱可能把自己的应答/提示音当成新请求（自问自答、尾音误触发）；`lag=1200` 时 cue 尾部 200 ms 必漏 | task-9 的 HE-02 复现（4 failed / 2 passed，corr ≈ 1.00）；门禁常量 `src/rui/audio/vad.py` 的 `CUE_MATCH_START_MAX_MS`(800)、`CUE_LAG_INITIAL_MS`(740)、`CUE_OVERLAP_GUARD_MS`(1000)；commit `1c5583a`（HE-01 测试落地那轮把 HE-02 红了并撤掉） | **D-010**（选项 A 修 / B 登记 / C 只补 1740 尾部）；task-10（blocked）；无论选哪个都要一次真机窗口 |
| L2 | **C3：无歌时说「暂停 / 停一下」会结束会话** | 用户只想暂停音乐，实际被当成散场（`bare_stop`）；同一句在有歌/无歌时做相反的事 | `docs/audit/intent-collisions.md` C3（commit `f5d3f46`）；触发路径 `src/rui/web/api/app.py` 的 `_stop_command` 与 `src/rui/session/rules.py` 的 `stop_scope`/`end_words` | **C3 待 9 哥 拍板**（intent-collisions 批次 2）；拍板后随 task-14 后续卡落地 |
| L3 | **意图层其余冲突 C4–C11 未落地**（8 条：**P1 2** = C4/C5，**P2 6** = C6–C11；C3 单列 L2）：paused 时结束词仍只停歌；暂停词表仍有 6 个词且提示词未同步；catalog 文案与子串实现不一致；单字「停」/无歌 transport/「大一点」「小一点」/「退一下」/暂停回复与 interaction 文档口径 | 同词多义、模型幻觉/空操作、文案误导；维护者按旧文档改代码 | `docs/audit/intent-collisions.md`（commit `f5d3f46`）C4–C11 与「批次 2/3/4」；`tools/intent_matrix.py` 可复算矩阵 | 批次 2（C3/C4/C5）、批次 3（C6/C10/C11）、批次 4（C7/C8/C9）；C10 别名与 C7 澄清需 9 哥 先定 |
| L4 | **控制台「唤醒应答」仍是 v2 语音助手页第 8 组（末位）** | 9 哥 最常改的一项要滚动才看得到；改序未落地前，控制台首屏看不到 | `docs/audit/console-ordering.md` §0.1（commit `7fde6aa`）：`VoiceAssistantView.vue` 的 `SHOWN_GROUPS`/`showGroup`、`web/src/v2/config.ts` 的 `orderedGroups` 不排序、catalog tier 2 序 `… audio.* ack(200)` | **D-011**（tier 2→1 + order 5，或保留 tier2 改 v2 页内显式排序）；拍板后按 console-ordering §2.1 落地 |
| L5 | **控制台 tier 取舍：ack 要第 1 位必须动 tier，牵动测试与契约** | 只改 order 只能到第 3 位（assistant/session 是 tier1）；`tier=1` 的契约语义是「底子、不轻易动、前端弱化+警示」，与「最常改」冲突；legacy 面板按 tier2 过滤会丢 ack | `src/rui/config/catalog.py` 的 `TIERS` 定义与 `group_specs()` 排序；`docs/config-catalog-contract.md` 的 tier 契约；`tests/test_config_catalog.py` 的 tier-2 组断言；`docs/audit/console-ordering.md` §0.2（commit `7fde6aa`） | **D-011** 拍板；若走 tier1 需同步改既有断言与契约说明（另卡），或走「保留 tier2 + v2 显式排序」的前端方案 |
| L6 | **HE-01 正向控制的阈值绑定测试夹具**（T3）：`_retained_energy >= 0.8` 按 ack-cue 的 `HOLD_MS=1740` / `SKIP_MS=790` 标定 | 有人改生产 hold/skip 常量或换夹具时，正向控制可能**假红**（人声被误判为被削掉），浪费排查时间 | `tests/test_he01_goodbye_echo_no_extra_turn.py` 的 `test_a_person_right_after_the_goodbye_is_captured_once`；`tests/test_cue_echo_excision.py` 的 `HOLD_MS`/`SKIP_MS`；commit `f7b2484`（补正向控制） | 微卡：把阈值改为按夹具常量推导或加注释锁定；已登记在 HANDOFF「已知残留 nit」（T3 能量阈值按 1740/790 标定） |
| L7 | **HE-01 ②（idle-watch 告别的 `reply_finished` 记账）没有 headless 覆盖** | 只能靠真机「10 次告别」验证；测试层若回归（例如 idle 分支改了调用顺序）不会报警 | task-9 卡的裁决与 `tests/test_he01_goodbye_echo_no_extra_turn.py` 文件头：idle 路径调裸 `_session_client().reply_finished`，`_PlaneLink` 夹具不等价，硬写属假覆盖 | `docs/acceptance-pending.md` 的 **HE-01（补充）**行（真机 10 次告别）；若要做 headless，另立卡驱动 idle 循环（本阶段不做） |
| L8 | **旧面板与 v2 两套渲染并存** | 同一能力两处呈现/保存语义不同；legacy 面板的设备身份字段有 6 项重复写入口（adapter/host/room/enabled/wake_words/listen_port），`id` 仅 catalog 可改；运维/9 哥 容易在旧页面改到不生效的副本 | commit `c76b4ff`（panel registry 引入两套渲染）；`web/src/components/ConfigCenter.vue` 与 `DeviceManager.vue`（legacy）vs `web/src/v2/views/DeviceDetailView.vue` 的 `ConfigPanel`、`web/src/v2/components/config/DeviceConfigCard.vue`；`docs/audit/console-ordering.md` §3.2 | **0.8.0 移除 legacy 面板**（`docs/HANDOFF-0.8.md` / 已定决策「0.8.0 只做破坏性变更」）；在那之前旧页面按 legacy 处理，不再加新功能 |
| L9 | **结束词 + 有歌时只停歌、会话不关（task-14 修复中，尚未合入 main）** | 9 哥 真机遇到的原始问题：说「停止/拜拜/停止播放」在放歌时只停音乐、会话仍开；与目标策略「结束词 = 音乐清零 + 会话关闭」不符。**0.7.8 与 main HEAD 仍有此行为**；task-14 的修复目前只在工作树（未提交/未合入） | `docs/audit/intent-collisions.md` C1（commit `f5d3f46`）与 9 哥 策略 3；main HEAD `16ec422` 的 `src/rui/web/api/app.py` 仍无 `stop_and_dismiss`（工作树已有未合入改动） | **task-14**（批次 1，进行中）；合入 main 后解除本行，并按 `docs/acceptance-pending.md` 的 N1/N2 真机验收 |

## 与「未验证」的分界

- 本文件只收**已确认存在**的限制；「不知道有没有问题、要真机才知道」的 21 条（另含 HE-01 补充与本阶段 5 条新增）在 `docs/acceptance-pending.md`。
- 每条解除时：把「计划指向」改成「已解除 @ `<commit>`」，并在行首加 ✅；不要删除，便于发版说明回溯。
