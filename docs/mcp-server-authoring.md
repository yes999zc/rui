# 写一个给 Rui 用的 MCP 服务器

> 适用版本：RUI 0.8.13+。面向**服务端作者**——你要写一个 MCP server，让 Rui 把它接进来当工具用。
> 已有文档讲的是「怎么接一个现成服务」：`docs/integrations-mcp.md`（操作步骤）、
> `docs/mcp-generic.md`（运行时/隔离/热更新）。本文件补的是另一半：**你的服务要满足什么，接进来才好用。**

## 1. 三条硬约束（先看这三条，再动手）

| 约束 | 值 | 出处 | 你该怎么办 |
|---|---|---|---|
| 单次工具结果文本上限 | **1500 字符** | `tools/mcp_manager.py:MAX_TOOL_TEXT` | 一句话一动作；长内容自己截断或分页，别指望 Rui 帮你截 |
| 单次调用超时 | **8 秒** | `CALL_TIMEOUT_S` | 重活异步化：先返回「我去查了」，结果另行播报或缓存 |
| 连接握手超时 | **8 秒**（握手）/ **10 秒**（等待连上） | `HANDSHAKE_TIMEOUT_S`、`tools/mcp_client.py` | 启动要快；初始化阶段别做重活 |

超时不是失败重试的问题：8 秒拿不到结果，这一轮对话就按「连不上」处理了。

## 2. 返回值会被加工，你要按加工后的样子设计

Rui 不会把 MCP 的原始结果直接交给模型，中间有三道加工：

1. **只取文本**：`content[].text` 会被拼接；非文本块（图片、音频、资源引用）统一变成
   `[non-text content omitted]`。`structuredContent` 会被 `json.dumps` 成一串 JSON 塞进来。
   → 想让模型念得自然，就**只返回一句普通文本**，不要返回结构化对象当答案。
2. **打上不可信标记**：整段前面加 `[不可信外部数据]`（`tools/ha.py:UNTRUSTED_PREFIX`）。
   模型看到的是带标记的文本；实测模型不会把标记念出来，但它会影响模型对这段内容的态度。
3. **清洗整行**：以下两类行会被**整行丢弃**（`_clean_untrusted`）：
   - 以 `system:` / `assistant:` / `user:` / `developer:` 开头的行
   - 含明显指令注入短语的行（如「忽略之前的指令」「从现在起你是…」）
   → 你的文案里**不要出现这些形状**，否则这句会被悄悄删掉，模型收到的是残句。

## 3. 工具命名与元数据

- 注册名固定为 **`mcp.<服务器名>_<工具名>`**（`tools/mcp_manager.py:tool_name`）：
  小写、非字母数字折叠成下划线、**只允许一个点**（`ToolRegistry` 只接受 `<source>.<tool>` 形状）。
  例：服务器 `cairn` + 工具 `next_question` → 模型看到的是 `mcp.cairn_next_question`。
- 工具描述上限 **500 字符**（`MAX_DESCRIPTION`）：写成「什么时候用我」，不要写实现细节。
- 状态面板最多列 **50 个工具名**（`MAX_TOOL_NAMES_IN_STATUS`）：工具别贪多，模型也会挑花眼。

## 4. 网络与安全

- **HTTP 传输（streamable HTTP）是推荐姿势**：不在 Rui 主机上执行任何代码。
- **stdio 传输等于允许它在 Rui 主机上执行任意命令**：需显式打开 `tools.mcp_allow_stdio`，
  默认拒绝。除非你完全信任那个命令，否则别走这条。
- 出站有 SSRF 保护（`security/outbound`）：
  - 局域网地址默认**允许**（`allow_private: true`），因为家里服务常在 LAN 上；
  - **回环地址默认禁止**（`allow_loopback: false`）。如果你的服务与 Rui 同机，
    要么监听 LAN 地址，要么把 `allow_loopback` 打开——这是显式的安全取舍，不是 bug。
- 鉴权用 `token_env` 指向一个密钥名，值放 `secrets.yaml`/环境变量，**不要写进 config.yaml**。

## 5. 失败语义（按这个设计你的可用性预期）

- 一个服务器连不上或中途挂掉，**只有它自己的工具降级**（调用时会说「…这会儿连不上」），
  其它服务器、HA、音乐、语音回合都不受影响；后台按 1→15 秒退避自动重连。
- 工具级错误（你返回 `isError`）不会让连接断掉，但会让模型收到错误文本——把错误写成
  人话（「题目库这会儿打不开」），别吐堆栈。

## 6. 自测顺序（照这个走一遍再交付）

```bash
TOKEN=$(docker exec rui rui token)

# 1) 只探测，不落盘：确认能连、工具数与名字对
curl -s -X POST -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"cairn","transport":"http","url":"http://127.0.0.1:18130/mcp","enabled":true}' \
  http://127.0.0.1:8080/api/v1/mcp/test

# 2) 正式注册（写入 tools.mcp_servers，热更新，不重启）
curl -s -X PUT -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"servers":[{"name":"cairn","transport":"http","url":"http://127.0.0.1:18130/mcp","enabled":true}]}' \
  http://127.0.0.1:8080/api/v1/mcp

# 3) 看状态：connected / tools / latency_ms
curl -s -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8080/api/v1/mcp

# 4) 静默跑一个工具（不出声），看模型将要看到的文本
curl -s -X POST -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"mcp.cairn_today_progress","args":{}}' \
  http://127.0.0.1:8080/api/v1/debug/tool

# 5) 走一轮真实对话，确认模型自己会挑工具
curl -s -X POST -H "X-Auth-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"今天还有几道题要做？","channel":"api","peer_id":"smoke","session_id":"smoke-1","want_audio":false}' \
  http://127.0.0.1:8080/api/v1/chat
```

第 4 步最关键：它展示的就是模型**实际收到**的文本（含不可信前缀与清洗结果）。
如果第 4 步的文本不像你想让它念的话，第 5 步一定不对。

## 7. 一个真实例子（Cairn，2026-10）

小学生错题系统 Cairn 用上面这套接进 Rui，三个工具：

| 工具 | 返回（一句普通话） |
|---|---|
| `next_question(subject?)` | 「第一题，数学。小明有 3 个苹果，妈妈又给了他 5 个，一共有几个？」 |
| `submit_answer(error_item_id, learner_answer)` | 「答对了」／「再想想，提示：先把 3 和 5 加起来」 |
| `today_progress()` | 「今天计划 6 道，已完成 2 道，还剩 4 道。」 |

要点：题干**原文照念**（不改写、不省略数字单位）、中文数字归一化（「八」=8）、
空状态也要给一句完整的话（「今天的题都做完了」），而不是空字符串。
