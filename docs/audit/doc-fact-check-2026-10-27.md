# 文档 ↔ 代码 事实一致性抽样核查（2026-10-27）

> 打磨项 G。目的不是写通稿，而是给「文档说的事」留下可复查的证据：每条声明给
> `file:line` + 原话摘要 + 代码/配置证据，结论只有四种：**一致 / 已修正 / 疑似 /
> 需 9 哥 拍板**。
>
> 基线：`663997f`（核查开始时的工作树；`src/` 与 `tests/` 未改动）。行号以核查后的
> 工作树为准；`docs/audit/unverified-inventory.md`、`docs/HANDOFF.md`、`docs/待拍板.md`
> 归其他任务/Lead，不在本卡范围，未触碰。
>
> 抽样文件：`README.md`、`docs/deploy.md`、`docs/deploy-runbook-0.7.8.md`、
> `docs/console.md`、`docs/music.md`、`docs/integrations-mcp.md`、
> `docs/socket-plug-v1.md`、`docs/install-wizard-design.md`（8 个文件，40 条声明）。

## 1. 结论分布

| 结论 | 条数 |
|---|---|
| 一致 | 32 |
| 已修正 | 5 |
| 疑似 | 2 |
| 需 9 哥 拍板 | 1 |
| **合计** | **40** |

「已修正」共落在 7 个 hunk 上（README 中英各 1、`console.md` 1、`socket-plug-v1.md` 2、
`deploy.md` 1、`install-wizard-design.md` 1），全部是事实值/行号/时态替换，见
`git diff -- README.md docs/console.md docs/socket-plug-v1.md docs/deploy.md docs/install-wizard-design.md`。

## 2. 核查表

| # | 声明（file:line） | 文档原话摘要 | 证据（命令 / 代码位置） | 结论 |
|---|---|---|---|---|
| 1 | `README.md:54` | core 0.7.8 当前；dot2 桥 1.3.1、xiaoai 桥 0.2.2 | `pyproject.toml:3` = 0.7.8；`src/rui/__init__.py:1` = 0.7.8；dot2 `pyproject.toml:7` = 1.3.1、`plugin.yaml:4` = 1.3.1；xiaoai `pyproject.toml:7` = 0.2.2、`plugin.yaml:4` = 0.2.2 | 一致 |
| 2 | `README.md:84` | `/` 默认进 v2；15 条旧链接（11 个 `/panel/*` + 4 条顶层） | `src/rui/web/api/legacy_links.py:109` 明确「15 old console links」；`LEGACY_PATHS`（:58-72）为 11 个 panel id + `/setup`/`/secrets`/`/diagnostics`/`/channels`；`app.py:6968-6974` `/` 302 `/v2/`；`schema.py:125` `console` 默认 `"v2"` | 一致 |
| 3 | `README.md:103` | `RUI_ADAPTER_PLUGINS=dot2=…,lx06=…` 显式注册语法 | `src/rui/device/loader.py:7-9`：逗号分隔 `module:build`，可选 `name=` 前缀；`ENV_VAR`（:48）= `RUI_ADAPTER_PLUGINS` | 一致 |
| 4 | `README.md:112` | Python **3.11+** | `pyproject.toml:6` `requires-python = ">=3.11"` | 一致 |
| 5 | `README.md:131`（英文镜像 :313） | 「dot2 用的是 0–100（`DOT2_VOLUME_CAP`）」 | dot2 桥 `src/rui_dot2_bridge/volume.py:16-24`：`DEVICE_LEVEL_MAX = 127`，`percent_to_level()` 把 0–100 % 映射到设备 0–127；`adapter.py:843` `DEFAULT_VOLUME_CAP_PERCENT = 100.0`（`DOT2_VOLUME_CAP` 是百分比上限，不是刻度）。原话把「百分比输入」当成了「设备刻度」 | 已修正 |
| 6 | `README.md:142` | `/health` 的 `status/version/devices/enabled_devices/device_errors/audio_plane/adapter_plugins` 字段 | `src/rui/web/api/app.py:3515-3538`（`health()` 返回体逐字段）；`adapter_plugins.warnings` 还并入 `legacy_adapter_rewrites()`（:3536） | 一致 |
| 7 | `README.md:161`（英文 :343） | 升级后设备掉线：「用下游组合镜像（`rui-dot2:x.y.z`），不要用纯核心镜像」 | 与同文件 `:99`/`:281`「核心镜像不再内置设备实现…**不需要造下游镜像**」及 0.7.6+ 外置插件路径相矛盾；属对外操作建议，按卡片要求只登记不改 | 需 9 哥 拍板 |
| 8 | `README.md:131-134`、`:170/:172`、`:352/:354` | 音量刻度引用块插在表格中间；「## 致谢」「## 致谢」重复；英文「## Credits」「## Acknowledgements」并列 | `sed -n '129,134p;168,174p;350,356p' README.md`：表格被 `>` 打断后继续表行；两个同名二级标题相邻。属结构缺陷，不是事实值，按卡片「只改事实值」未改 | 疑似 |
| 9 | `docs/deploy.md:5` | 生产 `--network host`、默认 `:8080`、配置在 `~/rui/config` | `docker-compose.yml:12` `network_mode: host`；`src/rui/config/schema.py:111` `port = 8080`；`deploy/install.sh:8` `DIR=$HOME/rui`；`docker-compose.yml:25` `./config:/config` | 一致 |
| 10 | `docs/deploy.md:39` | 安装目录默认 `~/rui`，`RUI_HOME` 可改 | `deploy/install.sh:8` `DIR="${RUI_HOME:-…:-$HOME/rui}"` | 一致 |
| 11 | `docs/deploy.md:42` | 容器/卷属主默认 1000，可用 `RUI_UID`/`RUI_GID` 覆盖 | `deploy/install.sh:22-23` 默认 1000；`docker-compose.yml:19` `user: "${RUI_UID:-1000}:${RUI_GID:-1000}"` | 一致 |
| 12 | `docs/deploy.md:108` | 健康后写 `console-token.txt`（0600，已存在不覆盖）；120 s 仍不健康则 `exit 1` | `deploy/install.sh:363-372` 120 次 `sleep 1` 探针；`:389-415` token 文件写入 + `chmod 600` + 已存在不覆盖 | 一致 |
| 13 | `docs/deploy.md:660` | 11 条 `/panel/<id>` + 4 条顶层共 15 条旧链接转 v2 | `legacy_links.py:58-72`（15 条元组）；`app.py:6989-6994` 注册 302 | 一致 |
| 14 | `docs/deploy-runbook-0.7.8.md:64` | 0.7.8 已删仓内 `xiaoai` 与 `adapter_aliases.py` | `ls src/rui/device` 无 `xiaoai/`；`test -e src/rui/adapter_aliases.py` 为假；兼容逻辑现存于 `src/rui/config/legacy_adapters.py` | 一致 |
| 15 | `docs/deploy-runbook-0.7.8.md:87` | xiaoai `plugin.yaml` id `xiaoai-lx06`、`rui_compat >=0.6.0 <0.8.0`；`devices/lx06.yaml` id `lx06`、entrypoint、`listen_port: 4412` | xiaoai 桥 `plugin.yaml:2,9`；`devices/lx06.yaml:14`（`id: lx06`）、`:59`（`entrypoint`）、`:60`（`listen_port: 4412`） | 一致 |
| 16 | `docs/deploy-runbook-0.7.8.md:111-113` | `/api/v2/meta` 可查版本；`/` 302 `/v2/`；`/panel/logs` 302 `/v2/logs` | `src/rui/web/api/v2_api.py:1301-1309`（meta 返回 `rui_version`/`compat`）；`app.py:6968-6974`；`legacy_links.py:43`（`logs → /v2/logs`）+ `:130-134`（`/panel/{id}` 走 302） | 一致 |
| 17 | `docs/deploy-runbook-0.7.8.md:116` | meta `compat 0.6.0..<0.8.0`；`planes[].state` 取 `running`/`error`/`not_started` | `v2_api.py:1307` `{"min": "0.6.0", "max_exclusive": "0.8.0"}`；`app.py:1846`（`not_started`）、`:1855`（`running` / `error`） | 一致 |
| 18 | `docs/console.md:18` | 11 个面板：run 4 + configure 6 + hidden credentials | `app.py` 的 `_build_panels` nav：run = overview(485)/turns(562)/logs(674)/memory(692)；configure 可见 = devices(521)/voice-assistant(583)/providers(602)/integrations(618)/system(645)/music(704)；hidden credentials(665) | 一致 |
| 19 | `docs/console.md:106` | 非 `/api/*` 回 `index.html`；`/api/*` 打错仍回 JSON 404 | `app.py:6999-7010` catch-all：`full_path.startswith(("api/", "ws"))` → 404；否则回 `index.html`/`FALLBACK` | 一致 |
| 20 | `docs/console.md:136-141` | `GET /api/v1/panels`；`POST /api/v1/music` 动作 play/pause/resume/stop/next/prev；`GET /api/v1/music/search?q=`；`GET /api/v1/logs?lines=` | `app.py:5367`（panels）、`:4462-4487`（动作集与 422 文案）、`:4519`（search）、`:6932-6937`（logs，`lines` 默认 200、上限 2000） | 一致 |
| 21 | `docs/console.md:220` | 「`GET /api/v1/config/catalog` 返回 4 类（tier）**22 组**」 | `uv run python -c "from rui.config.catalog import TIERS, group_specs; print(len(TIERS), len(group_specs()))"` → `4 23`；`catalog.py:43` `TIERS` 4 项、`:70` 静态 `_GROUPS` 23 项 | 已修正 |
| 22 | `docs/console.md:220,233` | :220「dev 157 个字段」、:233「38 个 save 字段热生效」 | `field_specs(Config())`（`catalog.py:1631`）→ 136 个字段、`effect` 计数 save 39 / restart 97；文档数字来自 dev 实例配置，仓库内无该配置，无法独立复核 | 疑似 |
| 23 | `docs/music.md:61-62` | 取流 30 s 下载 / 180 s 转码超时；新增 PCM LRU 缓存 | `src/rui/music/sources/subsonic.py:48-49`（`DOWNLOAD_TIMEOUT_S=30`、`TRANSCODE_TIMEOUT_S=180`）、`:312-321`（`_touch`/`_evict` LRU）；`sources/__init__.py:24` `cache_tracks=8` | 一致 |
| 24 | `docs/music.md:75` | 时长 ≥45 s 过滤；全被剔则不过滤兜底 | `subsonic.py:52` `MIN_DURATION_S = 45`；`:205-206` `good = ...>= MIN_DURATION_S`，`batch = good if (good or not out) else out`；`semantic.py:400` 同阈值 | 一致 |
| 25 | `docs/music.md:84` | 边界补货：未播 ≤2 首补 20；只在曲目边界取数 | `src/rui/music/service.py:197-205`（`_schedule_topup` 用 `queue_min_tracks` 判阈值，`_topup` 取 `queue_batch`）；`schema.py:918-919` 默认 2 / 20 | 一致 |
| 26 | `docs/music.md:135` | `music_play` 结果带 `options`，最多 8 条 | `src/rui/music/tools.py:123-124` `result.options = [...] for t in tracks[:8]` | 一致 |
| 27 | `docs/integrations-mcp.md:11-12` | HA 走第一方桥 `tools.ha`、工具名 `local.ha_*`；通用 MCP 存 `tools.mcp_servers[]`、工具名 `mcp.<server>_<tool>` | `schema.py:869-877`（`ToolsConfig.ha` / `mcp_servers`）；`mcp_manager.py:81-82` `f"mcp.{_slug(server)}_{_slug(tool)}"`；`tools/ha.py:246,262` `local.ha_*` | 一致 |
| 28 | `docs/integrations-mcp.md:15` | HA 六工具白名单、危险域硬拒、7 天缓存 | `tools/ha.py:39-42`（6 项 `DEFAULT_WHITELIST`）、`:47-51`（`EXCLUDED_DOMAINS` 9 项）、`:54` `CACHE_TTL_S = 7 * 86400` | 一致 |
| 29 | `docs/integrations-mcp.md:25` | `headers` 每行 `Name=value`，仅放非敏感元数据 | `web/src/components/McpManager.vue:323`（`Name=value` 每行，冒号亦可）；`schema.py:784-800` 头名/CR-LF 校验 | 一致 |
| 30 | `docs/integrations-mcp.md:27` | `token_env` 留空时后端生成 `MCP_<NAME>_TOKEN` | `src/rui/tools/mcp_manager.py:85-92` `default_token_env()` | 一致 |
| 31 | `docs/integrations-mcp.md:98` | 关闭 stdio 闸门时 `PUT` 返回 422（`servers.<i>.transport`），探测 `attempted=false` | `app.py:6749-6756`（逐条 422，`loc: ["servers", i, "transport"]`）；`mcp_manager.py:670-676`（未开闸直接 `attempted: False`，不 spawn） | 一致 |
| 32 | `docs/integrations-mcp.md:142-144` | HA 测试接受 `{base_url, token}` 一次性令牌；不填回落已保存的 `RUI_HA_TOKEN` | `app.py:6825-6845`（`base_url` 必填、`token` 仅本次探测）；`schema.py:831` `HaConfig.token_env = "RUI_HA_TOKEN"` | 一致 |
| 33 | `docs/socket-plug-v1.md:30` | v1 规范能力 8 个；`native_kws` 拒绝、`streaming_asr` 保留不启用 | `src/rui/device/contract.py:26-39`（10 个枚举值，其中 v1 canonical 8 个）；`src/rui/plugins/contract.py:815-838`（v1 `native_kws` → error；`streaming_asr` → warning） | 一致 |
| 34 | `docs/socket-plug-v1.md:145` | 核心默认 capture 16000/1ch/0ref、playback 16000、ack `earcon` | `schema.py:305-308`（16000/1/1/0）、`:390-391`（16000/1）、`:440`（`AckConfig.mode = "earcon"`） | 一致 |
| 35 | `docs/socket-plug-v1.md:196` | `AudioFrame`：int16 little-endian、按通道交织、reference 在尾部；默认 20 ms 一块 | `src/rui/device/contract.py:293-308`（`pcm` 注释 + `reference_channels` 尾通道）；`schema.py:330` `frame_ms = 20` | 一致 |
| 36 | `docs/socket-plug-v1.md:200` | `read_volume()` / `set_volume()` 为设备混音器 **0–255** | `src/rui/device/contract.py:338-350`（读/写 0–255，配置/UI 百分比在调用前映射） | 一致 |
| 37 | `docs/socket-plug-v1.md:251` | `BoundedWakeQueue` 默认 `MAX_PENDING_WAKES = 32`；音频面每 tick 最多 64 条 | `src/rui/device/contract.py:127-139`；`src/rui/audio/process.py:156` `MAX_DRAIN_EVENTS = 64` | 一致 |
| 38 | `docs/socket-plug-v1.md:420,485` | :420 旧 `adapter: xiaoai` 的别名模块引用（0.6.x 语境）；:485「计划 0.7.0 移除」 | :420 的旧路径在 0.6.x 成立（git `1fa96bd` 引入 `src/rui/adapter_aliases.py`），0.7.8 起移入 `src/rui/config/legacy_adapters.py`（`_LEGACY_ADAPTERS` 在 :17 = `{"xiaoai": "lx06"}`）；:485 的 docstring 明确「the whole module is deleted in 0.8.0」（`legacy_adapters.py:1-13`） | 已修正 |
| 39 | `docs/deploy.md:373` | 旧配置 `adapter: xiaoai`「仍可用一个 minor（`src/rui/adapter_aliases.py`）」 | 该路径 0.7.8 起已移入 `src/rui/config/legacy_adapters.py`（git `c713916`，`git tag --contains` 仅 `v0.7.8`），且 `legacy_adapters.py:1-13` 写明整个模块 0.8.0 删除 | 已修正 |
| 40 | `docs/install-wizard-design.md:11` | 旧配置 `adapter: xiaoai` 通过 `src/rui/adapter_aliases.py` 的兼容别名继续解析 | 同上：现行为 `src/rui/config/legacy_adapters.py`（`c713916` 起），旧路径文件已不存在 | 已修正 |

## 3. dot2 / xiaoai 疑似问题（只登记，不跨仓修改）

以下不在本仓写范围，未改动；每条给出 `repo:file:line` 与理由，供另立卡处理。

| # | 位置 | 现象 | 理由 |
|---|---|---|---|
| X1 | `rui-dot2-bridge:src/rui_dot2_bridge/devices/rs03qr.yaml:38-39` | `tested_with` 止于 `0.6.0`，`acceptance.verified: false` / «Live-device acceptance pending» | 与 rui README:126-129 引用的 2026-10-26/27 真机结果（唤醒 30/30、barge 8/8、指示灯实测）互相矛盾；疑似验收元数据未随实测更新 |
| X2 | `rui-xiaoai-bridge:src/rui_xiaoai_bridge/devices/lx06.yaml:68-73` | `tested_with: ["0.6.0"]`、notes 称 «0.6.x is the verified window»、验收证据锚在 0.6.0；但 `plugin.yaml:9` 声明 `rui_compat >=0.6.0 <0.8.0` | 支持窗口 0.6–0.7，验证窗口只写 0.6；与 rui README:126-129 的 0.7.8 实测（19/20、停播 205.2 ms）不一致，疑似兼容/验收元数据过期 |
| X3 | `rui-xiaoai-bridge:src/rui_xiaoai_bridge/devices/lx06.yaml:45` | `device_side.version_min: "v0.x"` | 不可执行的版本下限（占位符），安装步骤却要求具体 client；疑似漏填，不影响运行 |

## 4. 未改项与理由

- 第 8 条：README 的表格被引用块打断、`## 致谢` 重复、中英标题并列——是排版/结构缺陷，不是
  事实值；本卡只允许改版本号/路径/命令/参数名/默认值/端口/行为描述，故只登记。
- 第 22 条：`dev 157 个字段`（:220）/ `38 个 save 字段`（:233）依赖 dev 实例配置（provider、设备数、音源），
  仓库内没有这份配置，无法离线复核；不作为错误改数字。
- 第 7 条：README 故障排查仍在推荐下游组合镜像，与 0.7.6+「外置插件、不需要下游镜像」的
  对外说法冲突；改哪一边属于对外口径，按卡片要求交 9 哥 拍板。
- 未核项（超出抽样范围）：GHCR 镜像 digest、三仓发布 tag、真机测量数字、CI run 状态。

## 5. 复核命令（可复制）

```bash
# 结论分布与表内一致（对第 2 节表格逐行计数）
grep -c '| 一致 |$' docs/audit/doc-fact-check-2026-10-27.md
grep -c '| 已修正 |$' docs/audit/doc-fact-check-2026-10-27.md
grep -c '| 疑似 |$' docs/audit/doc-fact-check-2026-10-27.md
grep -c '| 需 9 哥 拍板 |$' docs/audit/doc-fact-check-2026-10-27.md

# 第 21 条：catalog 组数
uv run python -c "from rui.config.catalog import TIERS, group_specs; print(len(TIERS), len(group_specs()))"

# 第 38 条：路径是否存在（旧路径应「不存在」，新路径应「存在」）
test -e src/rui/adapter_aliases.py || echo "src/rui/adapter_aliases.py: absent"
test -e src/rui/config/legacy_adapters.py && echo "src/rui/config/legacy_adapters.py: present"

# 第 5 条：dot2 刻度与上限（跨仓只读）
grep -n "DEVICE_LEVEL_MAX\|percent_to_level" \
  ../rui-dot2-bridge/src/rui_dot2_bridge/volume.py
grep -n "DEFAULT_VOLUME_CAP_PERCENT" \
  ../rui-dot2-bridge/src/rui_dot2_bridge/adapter.py

# 第 39/40 条：失效路径不应再作为「当前行为」出现（socket-plug 历史句除外）
grep -rn "src/rui/adapter_aliases.py" docs/deploy.md docs/install-wizard-design.md \
  || echo "no stale alias path in the two current docs"

# 第 23-26 条：音乐常量
grep -n "DOWNLOAD_TIMEOUT_S\|TRANSCODE_TIMEOUT_S\|MIN_DURATION_S" \
  src/rui/music/sources/subsonic.py
grep -n "queue_min_tracks\|queue_batch" src/rui/config/schema.py
```

## 6. 改动清单（最小事实修正）

| 文件:行 | 改前 → 改后 | 理由 |
|---|---|---|
| `README.md:131`、`:313` | 「dot2 用的是 0–100（`DOT2_VOLUME_CAP`）」→「设备刻度 0–127（0.5 dB/step），`DOT2_VOLUME_CAP` 是百分比上限（adapter 默认 100）」 | dot2 桥 `volume.py` 的 0–127 映射；中英同步 |
| `docs/console.md:220` | 「22 组」→「23 组」 | `group_specs()` 实际 23 组（4 个 tier） |
| `docs/socket-plug-v1.md:420` | 0.6.x 别名路径改回历史事实：`src/rui/adapter_aliases.py`（0.6.x）→ `src/rui/config/legacy_adapters.py`（0.7.8 起，0.8.0 删除） | git `1fa96bd`/`c713916`；`legacy_adapters.py:1-13` |
| `docs/socket-plug-v1.md:485` | 「计划 0.7.0 移除」→「0.8.0 随该模块一并移除」 | `legacy_adapters.py:1-13` docstring |
| `docs/deploy.md:373` | 「仍可用一个 minor（`src/rui/adapter_aliases.py`）」→「0.8.0 前可用（`src/rui/config/legacy_adapters.py`）」 | 同上；当前行为以 `legacy_adapters.py` 为准 |
| `docs/install-wizard-design.md:11` | `src/rui/adapter_aliases.py` → `src/rui/config/legacy_adapters.py` | 同上 |

> #38 一条核查对应 `socket-plug-v1.md:420`/:485 两个 hunk，所以「已修正」是 5 条核查 / 7 个 hunk。

除上表外未修改任何 `docs/**` 或 `README.md`；`src/`、`tests/`、`.github/`、`docs/contracts/**`
零改动。本文件不含真实内网地址或凭证。
