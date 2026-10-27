# 语音交互系统层（V 线设计）

2026-10-23 定。原则（9哥 原话）：**交互分成两层——系统层定义好所有交互，应用层继承系统层的
交互**；新增功能/工具时零新增交互设计。核心战场是**语音交互**，控制台是同一状态机的镜像。

归属：架构图（docs/architecture.md）**L4 协调层 ④ rules and policy**——本设计把该格子从散件
升格为成体系的协议层；L3 应用层工具通过 `ToolSpec.interaction` 声明（不写话术）。

## 1. 老项目的病灶（差距报告 §8，全部带行号）

四套互不相通的工具方言：音乐 `@@` 标记协议（assistant_v3.py:840-895）、HA 错误字符串协议
（:824-831）、搜索 notice 协议（:849-856）、提醒自成一套（tools/reminders.py:209-231）。
当年两层化的改动**只到 prompt 文字层**（prompts.py:14/23 规则上收 + REQUIRED_HINTS :36 检查），
工具返回值仍是各家方言——「改了但没确认」属实：结构化保证与逐工具测试从未建立。

## 2. 十个语音交互原语（现状盘点）

| # | 原语 | 语义 | 现状 |
|---|---|---|---|
| 1 | ack 唤醒应答 | 唤醒即时反馈（none/earcon/tts 三档+回声忽略窗） | ✅ AckConfig+预渲染 |
| 2 | filler 进行中掩护 | 慢轮次桥接短语（三短语按轮次特征选、双阈值、预渲染） | ✅ session/filler.py |
| 3 | exec 执行反馈 | 一句确认+关键对象+数量；ok/empty/unreachable 三态模板 | 🟡 传输词已集中，工具话术仍内嵌 |
| 4 | degrade 失败降级 | 工具级/轮级/系统级三层话术 + 能力诚实声明（NO_TOOL/NO_MUSIC） | 🟡 工具级各说各话；诚实声明已系统化 |
| 5 | clarify 澄清问回 | 信息不足反问（动词缺失问回/谎报检测强制重试） | ❌ 老 :3432-3475 是规格来源 |
| 6 | choose 多结果选择 | 声明式三策略 first/top3/ask | ❌ 新老皆无 |
| 7 | confirm 危险确认 | excluded 硬拒（lock/alarm/valve）或 confirm 复述确认 | ❌ contract.needs_confirm 有挂点无实现 |
| 8 | interrupt 打断取消 | ≤300ms 静音 + 结束词 = 音乐清零 + 会话关闭 | ✅ |
| 9 | close 会话收尾 | idle 超时/两档告别/退出词 | ✅ |
| 10 | proactive 主动出声 | 提醒等空闲再发声（两路 TTS 会毁 AEC） | ✅ |

## 3. 应用层声明协议

- `ToolResult`（结构化）：`status: ok|empty|unreachable|denied|options` + `payload`（槽位
  title/count/device/at…）+ `options`（choose 消费）。**handler 不再返回中文字符串**。
- `InteractionSpec` 进 `ToolSpec`：`exec`（三态模板键）、`choose: first|top3|ask`、
  `danger: none|confirm|excluded`、`filler: auto|none`。
- `PhraseBook`（`session/phrases.py` + 配置覆盖）：全局一张话术表，键如
  `music.started="已开始播放《{title}》，共 {count} 首"`、`device.unreachable="{device}这会儿连不上，
  没能执行，别当我成功了"`、`clarify.verb="要开还是关？我只听到设备名…"`、`confirm.ask="{action}，确定吗？"`。
- 渲染双轨：**直通 spoken**（确定性词/传输/确认对话——`_music_transport` 模式推广，不进模型）
  与 **喂模型**（保留润色，空答时 PhraseBook 模板兜底）。

## 4. 控制台 = 交互状态镜像（详见 docs/console.md 镜像表）

对话 timeline：每轮显示 ToolResult.status 色点 + 实际使用的话术键 + filler 触发记录；
「试一试」升级为剧本测试（走完 clarify/confirm 全流程）；PhraseBook 全表可编辑。

## 5. 分期

- **V1 地基**：ToolResult + InteractionSpec + PhraseBook + exec/degrade；迁移音乐 9 工具与搜索；
  轮级/系统级兜底进 TurnRunner。验收：工具代码 grep 无中文字面话术；每工具 pytest 断言话术
  出自 PhraseBook。
- **V2 三原语**：clarify（问回+谎报检测）、choose（三策略）、confirm（两档）——HA/MCP 装配时
  作为首个验证用例（choose=ask、danger=confirm 都在 HA 兑现）。
- **V3 镜像**：ToolResult/filler 状态进 trace + 控制台 timeline + 剧本测试（并入 C-UI P3/P6）。

## 6. 差距核对结论（2026-10-23 核对 agent）

调研报告总体可信（核心声明 5/11 全对，其余为计数精度）。修正两条：①电台去重/taste 并非差距
——新项目 `music/history.py` 本就是 dedup+taste 一体设计且已接入；②老项目谎报检测/动词反问
有专门断言（test_llm_rounds.py:93-119）。**新发现的差距**（候选后续项）：LAN 只读工具
（lan.py）、摄像头视觉问答（vision.py，Qwen3-VL）、固定设备动作白名单+远程 shell 工具、
工具标记泄漏防线（clean_reply 剔标记）、**搜索/点歌多候选返回通道**（新 music 工具连候选列表
都不给模型——比「多结果选择」更基础，choose 原语设计时一并解决）。
