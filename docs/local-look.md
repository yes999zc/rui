# `local.look` — 看一眼摄像头（0.8.11）

> 一句话：用户说「看看书房」「门口有人吗」，助手取**一帧**画面交给大模型描述，
> 用 1–2 句话（≤80 字）回答。工具只读，不改设备、不录像、不落盘。

## 1. 两台摄像头，两条路

| 说法（`camera` 参数） | 来源 | 回复标签 | 实时性 |
|---|---|---|---|
| 留空 / `书房` / `这里` / `本机` | 本机 USB 摄像头，经 `scripts/usb_snapshot_server.py` 的 `GET /snapshot.jpg` | `现在（实时）：…` | 现场抓一帧 |
| `门口` 这类**配置过的别名** | 家庭助理 `GET <ha.base_url>/api/camera_proxy/<entity_id>` | `最近一次（HA 代理）：…` | HA 保存的最近一帧 |
| `camera.xxx` 实体 ID | 同上（直通，不需要别名） | `最近一次（HA 代理）：…` | 同上 |

「最近一次」和「现在」是**说出来的**区别，不是装饰：`camera_proxy` 返回的是 HA 缓存里
的最后一帧，可能已经过去几秒，用「现在」描述它就是在撒谎。

别名 → 实体 ID 的映射写在配置里，默认空；空的时候任何别名都解析不到，回复
「这台现在看不到画面。」：

```yaml
tools:
  vision:
    cameras:
      "门口": "camera.example_door"     # 部署时写自己家的 entity id，不要提交真实值
```

## 2. 配置项（`tools.vision`）

| 键 | 默认 | 说明 |
|---|---|---|
| `usb_snapshot_url` | `""` | 本机快照服地址，例 `http://<mac-lan-ip>:8766/snapshot.jpg`。**留空 = 这台没有 USB 摄像头**，回复「书房那台摄像头还没接上。」——不会编画面。 |
| `cameras` | `{}` | 别名 → HA 摄像头实体 ID。留空 = 所有别名都看不到。 |
| `timeout_s` | `15.0`（1–30） | **一次「看画面」的总预算**：取一帧、视觉描述、以及大模型的**整条兜底链**共用这一个上限（不是每个环节/每个 provider 各一份）。用完按失败回话，不拖整轮对话（整轮的工具超时是 20 s，所以「两段各 15 s」会被外层先砍掉，用户就会听到模型说「工具超时了」而不是我们的话术）。剩余不足 0.5 s 时不再发起视觉调用（免得白发送画面）。 |
| `cooldown_s` | `10.0`（0–120） | 两次看画面之间至少间隔多少秒，**全局**（不分摄像头）。0 = 不限。 |

HA 的地址/token 复用现有 `tools.ha.base_url` 与 `tools.ha.token_env`
（默认 `RUI_HA_TOKEN`，值在环境变量或 `secrets.yaml`），本工具**不新增任何凭据配置**。

## 3. 隐私口径（重要）

- 画面**只在内存里**：取图到 `bytes`（读完后按 8 MB 上限检查），转成
  `data:image/jpeg;base64,`，**不写** `<data_dir>`、不写 `/tmp`、不缓存、不录像。
- 描述走**现有** LLM 链路（`providers.llm.chain` → `providers/chain.py` →
  `providers/openai_compat.py`），也就是配置里的主用大模型。默认部署是
  **`api.deepseek.com`**：一次「看画面」= 这一帧会以 base64 的形式
  **离开本机发到该地址**。不想让画面出网，就把 `providers.llm.chain` 指向本机的
  OpenAI 兼容服务（例如自建的视觉模型），链路本身不用改。
- **兜底链也会收到这张图**：如果 `providers.llm.chain` 配了多个 provider，主用失败时
  下一家会拿到同一帧（它们都是你自己配的端点）；链越长，画面去的地方越多。
- **工具自身的日志**只有 `设备名 + 字节数 + 耗时(ms)`，不含 base64、画面内容或用户
  的问题（设备名对 USB 是 `usb`、对 HA 是 entity id）。但请注意框架层既有的
  tool 调用日志：`session/turn.py` 会以 INFO 记一行
  `tool look({"camera": …, "question": …}) -> <渲染后的回复>`，**参数和描述文本都在
  里面**——这是全局行为（所有工具都一样，语音助手要能复盘说了什么），不是本工具的
  额外日志。只想要工具自己那几行时，按 logger `rui.tools.look` 过滤。
- 限频 1 次 / 10 s（`cooldown_s`）本身就是保护：摄像头对着的是私人空间，
  而且每看一次都要付一次云端视觉调用。

## 4. 失败时说什么（话术都在 `session/phrases.py`，工具里零中文）

| 情况 | 话术 |
|---|---|
| 没配 USB 快照服 | 「书房那台摄像头还没接上。」 |
| 取不到图（非 200 / 超时 / 不是 `image/*` / 空 body / >8 MB / HA 地址空 / 别名未知） | 「这台现在看不到画面。」 |
| 图拿到了，描述失败（厂商报错 / 预算超时 / 空回复 / 没有可用 provider / 取图已用完预算） | 「画面看到了，但没描述出来，等下再试。」 |
| 冷却期内又让它看 | 「刚看过一次，等一下再看。」 |

工具**永不抛异常**：任何失败都是「一行日志 + 一句话」，不会把整轮对话打死。

## 5. 和 `scripts/usb_snapshot_server.py` 配合

脚本只用标准库，`ffmpeg` 从 `avfoundation` 抓一帧走 stdout 管道（同样不落盘）。
**它默认只绑 `127.0.0.1`（`--host` 默认值），而且完全没有鉴权**：任何能连到这个端口
的人都能取到书房画面。两种接法二选一：

### ① 跨机（rui 跑在容器 / 另一台机器上，本部署就是这种）

```bash
# Mac 上：绑到局域网（0.0.0.0），端口 8766，路径固定 /snapshot.jpg
python3 scripts/usb_snapshot_server.py --host 0.0.0.0 --device "0:none" --size 1280x720
# 在 rui 那台机器上自测（换成 Mac 的局域网地址）
curl -sI http://<mac-lan-ip>:8766/snapshot.jpg | head -3   # 期望 200 + image/jpeg
```

```yaml
tools:
  vision:
    usb_snapshot_url: "http://<mac-lan-ip>:8766/snapshot.jpg"
```

⚠ `--host 0.0.0.0` + 无鉴权 = **把书房摄像头公开给整个局域网**。要求：只在可信
网段/VLAN 里开这个端口，macOS 防火墙只放行 rui 那台主机，不要把 8766 端口转发到
公网；不用的时候把脚本停掉。想更稳妥就把脚本绑到某个具体内网地址
（`--host 192.0.2.10`），或改用只允许本机的反代加 token。

- `security.outbound.allow_loopback` 默认 `false`：局域网私有地址默认放行
  （`allow_private: true`），所以上面的局域网地址不需要额外开关。

### ② 同机（rui 直接跑在这台 Mac 上，原生/非容器）

```bash
python3 scripts/usb_snapshot_server.py --device "0:none"    # 默认 127.0.0.1:8766
```

```yaml
security:
  outbound:
    allow_loopback: true      # 默认 false：不加这个，127.0.0.1 会被 SSRF 守卫拦下
tools:
  vision:
    usb_snapshot_url: "http://127.0.0.1:8766/snapshot.jpg"
```

- 抓图失败时脚本返回 503，rui 侧就是「这台现在看不到画面。」，不会重试轰炸摄像头。

## 6. 静默调试（不发声、不需要真摄像头）

```bash
# 注册名和模型看到的名字都接受：local.look / look
# 端口是仓库默认 web.port=8080（改过就换成你自己的）
# 设了 web.auth_token 时带上 -H "X-Auth-Token: $TOKEN"（同 docs/acceptance-pending.md）
curl -s -X POST http://127.0.0.1:8080/api/v1/debug/tool \
  -H 'Content-Type: application/json' \
  -d '{"name":"local.look","args":{"camera":"门口","question":"门外有人吗"}}' | jq
# 返回 {"name":"local.look","status":"ok","payload":{"say":"最近一次（HA 代理）：…"},"rendered":"…"}
```

`POST /api/v1/debug/tool` 从 0.8.11 起同时接受 `ToolSpec.name`（`local.look`）和
`llm_name`（`look`）：`runner._handlers` 的键是后者，之前用注册名调用会 404。
注意这条路**会真的取图并真的调大模型**（这正是它存在的意义），只是不出声；
夜间自检请把 `tools.vision.usb_snapshot_url` 留空、或用未配置的别名，
这样只会拿到「这台现在看不到画面。」而不会碰相机和云端。

## 7. 代码落点

- 工具：`src/rui/tools/look.py`（`ToolSpec(name="local.look")`，`Permission.READ`）
- 装配：`web/api/app.py` 里 `local_tools.extend(build_look_tools(cfg))`，与其它本地工具
  共用同一个 `runner.set_tools(...)` 列表
- 配置：`config/schema.py` `VisionConfig` + `ToolsConfig.vision`，控制台条目在
  `config/catalog.py` 的「摄像头」组
- 话术：`session/phrases.py` 的 `vision.*`（含给模型的提示词 `vision.prompt`）
- 测试：`tests/test_local_look.py`（全 mock：不真调 DeepSeek、不真调 HA、不碰摄像头）
