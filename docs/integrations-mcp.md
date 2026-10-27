# MCP 集成：添加一个新服务 / Adding an MCP service

> 适用版本：RUI 0.3.4+ · 控制台「工具与集成 → 家庭助理（独立第一方桥）/ MCP 服务器」
> 相关：`docs/mcp-generic.md`（运行时/隔离）、`docs/security.md`（出站 SSRF）、
> `src/rui/tools/mcp_manifest.py`（第一方实例清单）

MCP 标签页里有两类东西，**不要混用**：

| 类型 | 例子 | 存储 | 工具名 | 写入路径 |
|---|---|---|---|---|
| 第一方桥（不是 MCP） | Home Assistant | `tools.ha` | `local.ha_*` | `PUT /api/v1/mcp {ha}` |
| 通用 MCP 服务 | 文件系统、笔记、Git | `tools.mcp_servers[]` | `mcp.<server>_<tool>` | `PUT /api/v1/mcp {servers}` |

Home Assistant 作为**独立的第一方桥**单独成段（`tools/ha.py`），**不是 MCP 实例**，也**不进入**
`tools.mcp_servers`：它保留六工具白名单、危险域硬拒、实体上下文与 7 天缓存。UI 的卡片
只展示这些策略，不会放宽或改名。

## 1. 通用服务的最短路径（HTTP）

1. **准备凭据**（如需鉴权）。不要把 token 写进 `config.yaml`；控制台会把表单里的
   token 写进只写密钥库 `data_dir/secrets.yaml`（0600），配置里只保留变量名。
2. **添加**（控制台）：MCP 服务器 → 添加服务器。
   - `transport`: `http`
   - `url`: 服务的 MCP 端点（如 `https://notes.example/mcp`）
   - `headers`（可选）：每行 `Name=value`，仅放非敏感元数据（版本头、`Accept`）
   - `token`（可选）：只写；保存后输入框清空，页面只显示「令牌已配置」
   - `token_env`（可选）：密钥名；留空时后端按服务名生成 `MCP_<NAME>_TOKEN`
3. **测试连接（不保存）**：会真的打开一次性会话并列出工具；未保存的 token 只用于
   这次探测，不会落盘。
4. **保存**：热重连；其他服务不受影响。工具以 `mcp.<server>_<tool>` 注册。

等价的 API（示例不含任何真实密钥）：

```bash
# 写入一个带版本头和只写 token 的服务
curl -sS -X PUT http://127.0.0.1:8080/api/v1/mcp \
  -H 'Content-Type: application/json' \
  -d '{
    "servers": [{
      "name": "notes",
      "transport": "http",
      "url": "https://notes.example/mcp",
      "headers": {"X-Api-Version": "1"},
      "token": "REPLACE_WITH_A_REAL_TOKEN",
      "enabled": true
    }]
  }'

# 只探测，不保存；token 只用于这一次会话
curl -sS -X POST http://127.0.0.1:8080/api/v1/mcp/test \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "notes",
    "transport": "http",
    "url": "https://notes.example/mcp",
    "token": "REPLACE_WITH_A_REAL_TOKEN"
  }'
```

响应的 `servers[]` 里有 `headers`、`token_env`、`has_token`，**没有 token 值**。
`token_env` 也可以直接由部署环境注入（环境优先于 `secrets.yaml`），例如在 `.env`
里放 `MCP_NOTES_TOKEN=...`，配置中只写 `"token_env": "MCP_NOTES_TOKEN"`。

### 配置形态（手写 `config.yaml` 时）

```yaml
tools:
  mcp_servers:
    - name: notes
      transport: http            # http | stdio
      url: https://notes.example/mcp
      headers:                   # HTTP only; non-secret request metadata
        X-Api-Version: "1"
      token_env: MCP_NOTES_TOKEN # name in secrets.yaml / environment
      enabled: true
```

`token_env` 与显式 `Authorization` 头只能二选一；`headers` 里的值会写进
`config.yaml`，所以敏感值请走 `token_env`。

## 2. stdio 服务（本机命令）

stdio 等于在这台主机上运行一个程序，因此默认关闭：

```yaml
tools:
  mcp_allow_stdio: true   # 显式开启；控制台会二次确认
  mcp_servers:
    - name: local-files
      transport: stdio
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"]
      env:                  # 子进程只拿到最小环境 + 这里声明的变量
        LOG_LEVEL: info
```

安全约束：

- 关闭闸门时，`PUT` 会返回 422（`servers.<i>.transport`），探测返回
  `attempted=false`，**不会启动任何进程**。
- 子进程环境是白名单（`PATH/HOME/LANG/...`）+ `env`，不是服务端 `os.environ`。
- stdio 不能用 `headers` / `token_env`；鉴权请放进 `env`。

## 3. 第一方实例清单（manifest）

Home Assistant 的卡片由后端 `GET /api/v1/mcp` 的 `first_party[]` 驱动：

```json
{
  "id": "ha",
  "name": "Home Assistant",
  "kind": "first_party",
  "transport": "http",
  "config_path": "tools.ha",
  "tool_prefix": "local.ha_",
  "tool_names": ["local.ha_get_state", "local.ha_call_service",
                 "local.ha_list_areas", "local.ha_search_entities",
                 "local.ha_batch_get_state", "local.ha_get_system_status"],
  "whitelist": ["get_state", "call_service", "list_areas",
                "search_entities", "batch_get_state", "get_system_status"],
  "excluded_domains": ["lock.", "alarm_", "valve.", "script.", "scene.",
                       "automation.", "shell_command.", "python_script.", "hassio."],
  "has_token": true,
  "href": "/panel/integrations?tab=mcp"
}
```

新增一个第一方实例（需要新代码，而不是配置）时：

1. 在 `src/rui/tools/<name>.py` 实现协议/安全策略；工具名保持稳定前缀。
2. 在 `src/rui/tools/mcp_manifest.py` 里加一个描述符（策略、前缀、配置路径、文档）。
3. 在 `src/rui/web/api/app.py` 的 `_mcp_state()` 里填它的运行状态（只给 presence）。
4. 在 `McpManager.vue` 用 `first_party[]` 渲染；**不要**把它写进
   `tools.mcp_servers`，除非它确实放弃自己的安全语义。
5. 补契约测试：白名单/危险域/工具名与旧版本一致，API 不回显 token。

普通第三方 MCP 服务不需要第 1–5 步：只走本文第 1 节即可。

### HA 卡片的「测试连接」也接受表单里的一次性令牌

填 `家庭助理地址` + `家庭助理访问令牌` 后可以直接点「测试连接」：

- 表单里填的 token 只用于这一次探测（`POST /api/v1/mcp/test {"base_url", "token"}`），
  **不落盘、不替换已保存的令牌**，测试通过后再点「保存」才写入 `data_dir/secrets.yaml`；
- 不填 token 时回落到已保存的 `RUI_HA_TOKEN`（旧行为），所以「先保存再测试」仍然可用；
- 想换令牌：在令牌框粘贴新的，可先测试再保存；保存后旧值被覆盖，输入框清空；
- 失败信息现在会给出真实原因（例如 `ClientError: 401 Unauthorized`），而不是
  `unhandled errors in a TaskGroup (1 sub-exception)`。

