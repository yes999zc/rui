# 通用 MCP 服务器（多服务器 / http + stdio）

> 2026-10-23。此前 `tools.mcp_servers` 只有配置形状，`/api/v1/mcp` 固定返回
> `generic_wired: false`，真正接线的只有 Home Assistant 的 `HaBridge`。本文件说明现在
> 的通用实现：配置怎么写、工具叫什么名字、怎么热更新、以及**为什么加一个 stdio 服务器
> 等于允许它在本机执行代码**。

## 1. 它是什么

- 每条 `tools.mcp_servers[]` 是一个独立 MCP 服务器；每台服务器一个连接任务，
  HTTP（streamable HTTP）和 stdio 两种传输都支持。
- 服务器列出的工具注册进同一个 `ToolRegistry`，名字是 `mcp.<server>_<tool>`。
- 一台服务器连不上/中途挂掉：**只有它自己的工具降级**（调用会念「…这会儿连不上」），
  其它服务器、HA、音乐、语音回合都不受影响；后台按 1→15 s 退避自动重连。
- HA 仍是独立特例（`local.ha_*` 六工具白名单、实体清单、危险域硬拒），本轮没有改它的行为。
- 配置保存后**热更新**：不变的不重连，改了的重连，删掉的停掉。

## 2. 配置

写在 `config.yaml` 的 `tools.mcp_servers`（不是 secrets）：

```yaml
tools:
  mcp_servers:
    # --- HTTP（streamable HTTP），推荐：不执行本地代码 ---
    - name: filesystem
      transport: http
      url: http://mcp.example.lan:7001/mcp
      enabled: true

    # --- stdio：由 house-ear 启动一个本地子进程（注意 §5 安全） ---
    - name: notes
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-notes"]
      enabled: true

    # 暂时停用但保留配置：仍会出现在控制台列表里，显示 enabled=false
    - name: git
      transport: stdio
      command: ["uvx", "mcp-server-git", "--repository", "/data/repo"]
      enabled: false
```

约束（与 `config/schema.py` 的 `McpServerConfig` 一致）：

| 字段 | 说明 |
|---|---|
| `name` | 唯一名，1–32 字符（作为工具命名空间的一部分） |
| `transport` | `http` 或 `stdio` |
| `url` | `http` 必填，例如 `http://host:port/mcp` |
| `command` | `stdio` 必填，**列表**（第一个是程序，其余是参数；不经过 shell） |
| `enabled` | 默认 true；false = 保留配置但不启动 |

**stdio 安全闸（默认关闭）**：`tools.mcp_allow_stdio: bool = False`（catalog 字段同路径，
tier 4 / effect=restart）。关闭时：

- 启动阶段跳过所有 `transport=stdio` 的服务器，状态里显示
  `error: "stdio MCP is disabled: set tools.mcp_allow_stdio: true"`；
- `PUT /api/v1/mcp` 拒绝新提交的 stdio 服务器（422，loc `["servers", 0, "transport"]`）；
- `POST /api/v1/mcp/test` 对 stdio 只做配置校验，**不会 spawn 子进程**
  （返回 `attempted: false` 与原因）；
- 打开后立即生效（不用重启）：catalog 的 effect 标 restart 是保守标注，但热应用会在保存
  `PUT /api/v1/config/values` 时立刻启动/停止 stdio 子进程——关闭闸门会**当场停掉**运行中的
  本地服务器。

> **stdio 子进程只拿到最小环境变量**（安全审计第 2 轮 P0-1）：
> `PATH`、`HOME`、`LANG`/`LC_*`、`TZ`、`TMPDIR`、`TERM`，加上服务器配置里显式声明的
> `env`（schema 未来若增加 `env` 字段，`MCPManager` 已用 `getattr` 兼容）。
> **不会**继承 house-ear 的完整 `os.environ`——容器 `.env` 里的模型/HA/设备/火山密钥
> 对 stdio 子进程不可见。需要 token 的本地服务器，请在其自身配置/凭据文件里管理，或等
> schema 增加 `env` 字段后显式声明。http 服务器按 URL 直接连接（当前没有 headers 字段）。

## 3. 工具命名

`ToolRegistry` 只接受 `<source>.<tool>`（一个点），所以：

```
tools.mcp_servers[].name = filesystem
server 自带工具名           = read_file
注册名（控制台/API/registry） = mcp.filesystem_read_file
模型看到的函数名              = filesystem_read_file
ToolSpec.source              = mcp:filesystem
```

- server 名/工具名都会 slug（小写、非字母数字压成 `_`）：`My Notes` + `list-all`
  → `mcp.my_notes_list_all`。
- 模型函数名是唯一键（runner 的 handler map 按它索引），所以「服务器 + 工具」都编码进
  名字；两台服务器都有 `search` 也不会互相覆盖。
- 同一服务器内两个原始名 slug 后相同（如 `do-it` 与 `do it`）时保留第一个并打 warning，
  不会让整次同步失败。

## 4. API

### GET `/api/v1/mcp`

```json
{
  "ha": { "...": "保持原样：base_url/token_env/has_token/wired/connected/tools/entities/error" },
  "servers": [
    {"name": "filesystem", "transport": "http", "url": "http://mcp.example.lan:7001/mcp",
     "command": [], "enabled": true,
     "connected": true, "tools": 2, "tool_names": ["read_file", "list_directory"],
     "latency_ms": 12.4, "error": "", "last_seen": 1761200000.0}
  ],
  "generic_wired": true,
  "generic": {"enabled": 1, "connected": 1, "tools": 2}
}
```

- `servers` 按配置顺序返回（控制台的编辑顺序），每项 = 配置字段 + 实时状态。
- `connected/tools/latency_ms/error/last_seen` 来自该服务器当前的会话；禁用服务器仍会出现，
  `enabled=false, connected=false`。
- `latency_ms` 的含义：刚连上时是「initialize + list_tools」的耗时，之后每次工具调用会被
  替换成该次调用的往返耗时；`last_seen` 是最近一次成功收发的 unix 时间。
- `generic_wired` 现在恒为 `true`（通用实现已接线）。
- `stdio_enabled`（顶层与 `generic` 内各一份）= `tools.mcp_allow_stdio` 的当前值；
  控制台用它渲染安全开关，关闭时给 stdio 条目显示门禁原因。
- 实测（本机 2026-10-23，真 MCP SDK 2.3 streamable HTTP，一个 `add` 工具）：
  handshake+list 229 ms；首次工具调用 524 ms；GET /api/v1/mcp 1 个 server/1 tool。

### PUT `/api/v1/mcp`

保持 HA 的旧形状（`{"ha": {"base_url": ..., "token": ...}}`），并支持
`{"servers": [...]}` 整份替换 `tools.mcp_servers`：

```json
{"servers": [
  {"name": "filesystem", "transport": "http", "url": "http://mcp.example.lan:7001/mcp"},
  {"name": "notes", "transport": "stdio", "command": ["npx", "-y", "..."]}
]}
```

整份 `Config` 校验通过才落盘（原子写 + 备份），随后**热重连**：未变更的服务器保持
现有会话，不重启。返回新的 `GET /api/v1/mcp` 结构。校验失败 422 按字段回显。

### POST `/api/v1/mcp/test`

一键探测，不保存：

- HA（旧形状不变）：`{"base_url": "http://ha.local:8123"}` → `HaBridge` 探测结果。
- 通用：`{"name": "probe", "transport": "http", "url": "..."}`
  或 `{"name": "probe", "transport": "stdio", "command": ["npx", "..."]}`。

通用返回：

```json
{"ok": true, "connected": true, "name": "probe", "transport": "http",
 "tools": 2, "tool_names": ["read_file", "list_directory"],
 "latency_ms": 18, "error": ""}
```

失败时 `ok=false`，`error` 是脱敏后的原因（URL query 里的 token/password 会被打码）。
探测会话在返回前关闭；stdio 子进程会被回收。

## 5. 安全（重要）

1. **stdio = 代码执行，且默认关闭。** `command` 是 house-ear 服务进程直接启动的子进程，
   权限等于服务进程（Docker 里就是容器权限）。必须先把 `tools.mcp_allow_stdio` 设为
   true（catalog 里有警告文案），PUT/测试端点才会接受/启动 stdio。只添加你**完全信任**
   的服务器；不要在 `command` 里拼用户输入。命令用列表、不经过 shell，避免把配置笔误变成
   shell 注入。子进程只拿到最小环境变量（见 §2），看不到 `.env` 里的密钥。
2. **http 只应指向可信地址。** 内网 / 本机 / 你自建的服务；不要把控制台暴露到公网后再
   配一个内网 MCP URL（SSRF 面）。
3. **token 不写 config。** 需要认证的 HTTP 服务器目前请用带 token 的 URL 或前置反向代理
   （未来 schema 增加 `headers` 后从环境变量填）；stdio 服务器的 token 放 compose
   `.env`/`environment`，子进程继承。任何 token 都不要写进 `config.yaml`（公开仓 + 控制台
   可读）。
4. **MCP 输出/描述按不可信外部数据处理。** 工具描述与结果会：
   - 去掉 `system:`/`assistant:` 这类角色行、去掉「忽略之前的指令」类句子；
   - 结果整体加 `[不可信外部数据]` 前缀再给模型；
   - 描述截断到 500 字（防止一个服务器挤爆工具表）。
   但这些只是缓解：**不要接来源不明的 MCP 服务器**。
5. **危险工具不自动暴露。** `annotations.destructiveHint=true` 的工具标记为
   `DANGEROUS + needs_confirm`，`registry.expose()` 不会自动放进模型工具表；
   `readOnlyHint=true` 排到前面。未声明 annotations 的一律按 WRITE 处理。
6. **工具数量竞争。** 模型最多看 50 个工具（`registry.expose`），排序是「只读优先、
   `local.*` 在 `mcp.*` 前」，所以本地工具优先；一个服务器工具太多会挤掉别的服务器。
   只接需要的服务器、必要时用 `tools.expose: whitelist`。
7. **stdio 子进程的生命周期**由 manager 管理：服务关闭时取消任务并回收子进程；SDK 的
   stderr 直接继承服务 stderr（可在日志/`docker logs` 里看到）。

## 6. 手动测试步骤

```bash
# 1) 单测（无网络、无子进程，open_session 用 fake）
uv run pytest tests/test_mcp_manager.py -q

# 2) 配置一个无副作用服务器（http 优先；stdio 要先打开安全闸）
#    tools:
#      mcp_allow_stdio: true       # 仅当确实要跑本地命令
#      mcp_servers: [...]
#    编辑 dev config，然后让 Lead 重启 :8091（控制面启动时接线）

# 3) 控制台/API 检查
curl -s http://127.0.0.1:8091/api/v1/mcp | jq '.generic, .servers'
curl -s http://127.0.0.1:8091/api/v1/tools | jq '.names | map(select(startswith("mcp.")))'

# 4) 探测一个新服务器（不写盘）
curl -s -X POST http://127.0.0.1:8091/api/v1/mcp/test \
  -H 'Content-Type: application/json' \
  -d '{"name":"probe","transport":"http","url":"http://mcp.example.lan:7001/mcp"}' | jq

# 5) 热更新（会写 config.yaml + 备份 + 只重连变化项）
curl -s -X PUT http://127.0.0.1:8091/api/v1/mcp \
  -H 'Content-Type: application/json' \
  -d '{"servers":[{"name":"fs","transport":"http","url":"http://mcp.example.lan:7001/mcp"}]}' | jq

# 6) 语音验证（等 Lead 重启并接线后）：唤醒后说「列出家目录的文件」
#    模型应调用 fs_read_file/list_directory 类工具；看日志 `mcp: <server> ...`。

# 7) 静默调试一个工具（debug/tool 用的是「模型看到的名字」，不是 registry 名）
curl -s -X POST http://127.0.0.1:8091/api/v1/debug/tool \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo_add","args":{"a":2,"b":3}}' | jq
# 返回 {"status":"ok","payload":{"say":"[不可信外部数据]\n5"},...}
```

真实 stdio 例子（需要 node/npx 在服务环境里）：

```yaml
tools:
  mcp_servers:
    - name: fs
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data/notes"]
```

注意：容器里默认没有 node/npx；要用 stdio 服务器请在镜像里装好运行时，或改用 http 版本。

## 7. 已知限制（本轮未做）

- 控制台 UI（`McpManager.vue` 的添加/编辑/删除表单）由 Lead 后续接；后端字段已就绪。
- schema 还没有 `env`/`headers` 字段；manager 已 `getattr` 兼容，schema 加了就生效
  （`env` 会与最小环境合并，而不是替换它）。
- 没有按服务器限制工具数量（全局仍受 50 个暴露上限约束）。
- 工具调用结果只取文本/结构化摘要，图片/音频内容以 `[non-text content omitted]` 呈现。
- 不支持 MCP resources/prompts/sampling，只做 tools。
- 依赖库是 `mcp>=2.3`；本实现用 v2 的 `mcp.server.mcpserver.MCPServer`（v1 的 FastMCP 在 v2
  已改名）。若未来降级到 mcp 1.x，`streamable_http_client` / `stdio_client` 的路径与签名需要
  重新核对（客户端侧代码集中在 `open_session()` 一个函数里）。
