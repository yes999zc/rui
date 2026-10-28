# voice reminder dispatch: minute claims + one room resolver (0.8.5, task B)

Status: implemented in the working tree (2026-10-28), not committed / tagged / deployed.
Real-device verification pending.

## 症状与事实核对

真机说「一分钟后提醒我喝水。」仍走 LLM `local.set_reminder`。先核对分派顺序：固定句式
`_voice_reminder_command()` 在 `app.py` 的 `runner.run` **之前**执行（约 3471 行），
所以问题不在顺序——是 `parse_voice` 的 claim 范围比文档窄：只认秒级相对时间和钟点，
`N分钟后` 被 `_looks_like_time()` 判成「交给 LLM」。

结论：**方案 a** 可行，无需退回方案 b（只写文档）。

## 固定句式现在接管什么（claim 形状）

`parse_voice`（`src/rui/tools/reminders.py`）的完整 claim 集合：

| 形状 | 例子 | 时刻来源 |
|------|------|----------|
| 绝对钟点 + 今天/明天/后天 + 时段 | 「提醒我明天早上七点吃药」 | `_voice_moment` |
| 钟点 + 半/一刻/三刻/N分 | 「叫我18点半下班」 | `_voice_moment` |
| 秒级相对时间 | 「10秒钟之后提醒我喝水」「十秒后…」 | `parse_at("+10s")` |
| **分钟级相对时间（本批新增）** | 「一分钟后提醒我喝水」「十分钟后提醒我关火」 | `parse_at("+1m")` / `("+10m")` |

分钟语法只认标准形状 `N分钟(之|以)后`（中文/阿拉伯数字，`N` 允许 1–3 位中文数字或
3 位阿拉伯数字），单位后必须是 后/之后/以后；`分钟钟` 这类多字不会误配（它仍被当作
时间线索，整句交 LLM，不会被存成分钟）。算术与工具入口
是**同一个** `parse_at`，不是第二份加法（测试用 spy 断言 actual spec 为 `"+1m"`）。
回复文案仍由 `humanize()` 从最终 `at` 生成（「1 分钟后」）。

## 仍交给 LLM 工具的形状（不扩 claim）

- 小时级/天级/周级：`N小时后`、`N天后`、`每周一`
- 不标准分钟形状：`过20分钟`、`半小时后`、`1个半小时以后`、`几分钟后`
- 刻/区间：`七点零一刻`、`七点以后`
- 弱触发词无时间：`叫我小王`、`叫我怎么相信你`
- **多句**（本批判定的边界，见下）

这些句子仍会命中 `_looks_like_time()` → 返回 `None` → `runner.run` 的 `set_reminder`
工具（`parse_at` 的 `+10m` / ISO 形状）。

## 单句边界（新增，防止分钟 claim 误吃歧义句）

动作段规范化（先 strip 尾部 `_TRAILING_JUNK`）后，若**内部仍含句末标点**
（`。！？!?；;`）且其后还有非空内容，`parse_voice` 返回 `None`，整句交 LLM。

理由：第二句通常是称呼/名字声明（「我是小志」）或另一条指令。窄语法无法结构化它，
存进 `Reminder.text` 会在到点时被念出来（「时间到了，喝水。我是小志。」）。这不只是
新分钟路径的问题，绝对时间路径同样受益。

- 「一分钟后提醒我喝水。」→ 接管（尾标点全被 strip，text=喝水）
- 「一分钟后提醒我喝水。我是小志。」→ **None**，走 LLM（task-32 真机原句的行为保持：
  `tests/test_app_fleet.py::test_an_llm_set_reminder_keeps_the_speaking_devices_room`
  继续绿，text=喝水、room=次卧）
- 「七点提醒我吃药。别忘了带伞」→ **None**（不静默吞掉第二句内容）
- 「提醒我七点吃药；顺便关灯」→ **None**

## 两条路径的房间同源

唯一解析器：`app.py::_reminder_room(cfg, client, named=None)`。

```
固定句式: _voice_reminder_command
  ├─ source_room = _reminder_room(cfg, client)              # 存储用（日志）
  └─ room        = _reminder_room(cfg, client, parsed.room) # 句子里「在<room>」优先
LLM 工具: _tool_context(cfg, client)
  └─ room = _reminder_room(cfg, client)                     # 说话设备房间
        └─ ToolContext.room → set_reminder handler → store.add(room=...)
```

`_reminder_room` 先取显式 `named`（精确匹配 `devices[].room`），否则取说话设备
（`_speaking_device_id`）配置的 room；未知设备/未配 room 保持 `None`，不猜默认。
本批**没有**为分钟路径复制任何解析逻辑；测试用 monkeypatch spy 断言两条路径都调用
`_reminder_room`（3 次调用：固定句式 source+named、tool context source）。

## 测试与证据

`tests/test_reminder_voice.py`（定向，2026-10-28）：

- 分钟 claim 参数化：1/10/30/99 分钟、中文/阿拉伯数字、`之后`/`以后`、时间前置/后置、
  带 `。`、带 `在<room>`；断言 `at - REF == timedelta(minutes=N)` 且**不等于**
  `timedelta(seconds=N)`。
- `parse_at` spy：分钟路径 actual spec `"+1m"`（与工具同一算术）。
- `_MINUTES_REL.match("十分钟钟后喝水") is None`（`分钟` 不误配 `分钟钟`），且
  `parse_voice("提醒我十分钟钟后喝水") is None`（作为时间线索交 LLM，不误问「什么时候」）。
- 单句边界：名字声明/第二指令返回 `None`；单句重标点仍接管。
- 仍交 LLM 的回归：半小时/1个半小时/过20分钟/3小时/3天/七点以后。
- 房间：次卧设备说「一分钟后提醒我喝水。」→ `Reminder.room == "次卧"`、
  `at == REF + 1min`、确认句「好，1 分钟后提醒你喝水。」；spy 证明两条路径共用
  `_reminder_room`。LLM 端到端 room=次卧 由既有
  `tests/test_app_fleet.py::test_an_llm_set_reminder_keeps_the_speaking_devices_room`
  覆盖（保持绿）。

命令与真实输出见任务报告；`uv run pytest tests/test_reminder_voice.py` 96 passed，
`tests/test_reminder_voice.py + test_reminder_delivery.py + test_app_fleet.py` 148 passed，
`uv run ruff check src/rui/tools/reminders.py src/rui/web/api/app.py tests/test_reminder_voice.py`
全绿。

## 风险 / 未做

- **真机未验证**：分钟级句式在真机 ASR（带噪声/口语填充）下的命中率未测。
- 单句边界按标点判断：一句里合法出现 `；` 且后有内容的句子会转 LLM（更慢，但
  不会丢内容/污染 text）；多句绝对时间提醒也因此改走 LLM（行为变化，已记录）。
- 不扩小时/天/周/刻/区间；`N个半小时`、`过N分钟` 仍交 LLM。
- 复核真实时间跨日：`parse_at("+Nm")` 是纯加法，跨午夜等价（秒级已测，分钟级同一函数）。
- 未改 `docs/known-limitations.md`（由 impl-obs 统一更新）。
