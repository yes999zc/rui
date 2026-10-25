# 控制台重构契约（2026-10-23，Lead 定）

> 9哥 要求：控制台重构为**国际化 + 现代风格**；所有可从前台修改的参数都要有输入口，
> 标注「改这个能达到什么效果」和「保存生效 / 重启生效」；参数分四类：
> ① 底子（不轻易动）② 跟随音箱变化 ③ 外部资源（LLM/ASR/TTS）④ 工具类（MCP/新闻/
> 音乐音源（**音源确定后路径自动更新**）/搜索/天气/其他默认工具）。
>
> 后端负责人 `console-api`（task-5），前端负责人 `console-ui`（task-6），回合硬截断
> `turn-core`（task-7）。本文是两端对齐用的最小契约；实现可以更丰富，但下列字段/端点
> 不得改名。

## 1. 端点

### GET `/api/v1/config/catalog`

```json
{
  "tiers": [
    {"id": 1, "title": {"zh": "底子", "en": "Foundation"},
     "hint": {"zh": "一般不动，改错会影响整条语音链路", "en": "..."}},
    {"id": 2, "title": {"zh": "音箱相关", "en": "Speaker"}, "hint": {...}},
    {"id": 3, "title": {"zh": "外部资源", "en": "Providers"}, "hint": {...}},
    {"id": 4, "title": {"zh": "工具", "en": "Tools"}, "hint": {...}}
  ],
  "groups": [
    {"id": "assistant", "title": {"zh": "助手", "en": "Assistant"},
     "tier": 1, "order": 10, "effect": "save", "hint": {"zh": "...", "en": "..."}}
  ],
  "fields": [
    {"path": "assistant.reply_max_chars", "group": "assistant", "tier": 1,
     "kind": "number", "label": {"zh": "日常回答上限", "en": "Everyday reply budget"},
     "hint": {"zh": "改这个能达到：超过上限的回答会被截断…", "en": "..."},
     "effect": "save", "unit": {"zh": "字", "en": "chars"},
     "min": 10, "max": 300, "options": null, "advanced": false, "writable": true}
  ],
  "values": {"assistant.reply_max_chars": 120},
  "secrets": {
    "RUI_LLM_KEY": {
      "has_value": true,
      "category": "provider",
      "consumers": [
        {"id": "llm:llm", "kind": "provider", "stage": "llm", "provider": "llm",
         "label": {"zh": "llm（LLM 链）", "en": "llm (llm chain)"},
         "href": "/panel/providers"}
      ],
      "used_by": ["llm:llm"],
      "hint": {"zh": "...", "en": "..."},
      "writable": true
    }
  },
  "restart_command": "docker compose restart",
  "writable_root": ["assistant", "session", "audio", "ack", "system", "web",
                    "devices", "providers", "tools", "music"]
}
```

约定：

- `kind` ∈ `text | textarea | number | bool | select | list | secret`；`secret` 字段只出现在
  `secrets`（`has_value` 布尔），**永不回值**，写入走 `/api/v1/secrets`。
- 每个 secret 还带类型化元数据：`category` ∈ `provider | search | music | ha | device |
  console | other`，`consumers[]` 每项含 `id`/`kind`/`label`/`href`（provider 另带
  `stage`/`provider`，device 带 `device`），`writable=false` 表示该值由专门页面写入
  （目前只有音乐的 `music.credentials`，链到 `/panel/music`）。`GET /api/v1/secrets`
  返回同样的 `secrets` 表（外加兼容用的 `names[]`），前端不得再从变量名猜 consumer。
- `effect` ∈ `save | restart`。`save` = 保存后立即/下一轮生效；`restart` = 需要重启服务
  （`docker compose restart`），或者音频类字段由前端调用音频面重启端点。
- `tier` 与 9哥 的四类一一对应；`tier=1` 前端要弱化 + 警示。
- `hint` 必须回答「改这个能达到什么效果 / 什么时候用」，不是字段名的复述。
- providers 的 catalog 条目（kind/base_url/model/api_key_env/voice/timeout/retries/thinking）
  与三条 chain 都进目录（tier=3）。
- 工具类（tier=4）：weather（city/latitude/longitude/days_max/timeout）、news（enabled/source/
  timeout）、search（enabled/kind/base_url/api_key_env/count/timeout）、ha（base_url/token_env）、
  mcp_servers（列表，只读展示即可）、music（enabled/source/secrets_path/cache_dir/
  cache_tracks/queue_min_tracks/queue_batch/history_max）。
- 音乐源路径自动更新：`music.source` 是注册表驱动的 select（Subsonic 家族：navidrome /
  airsonic / gonic / ampache / custom），变化时后端自动填 `music.secrets_path`
  （`<data_dir>/lan_media.json`）与 `cache_dir`（`<data_dir>/music_cache`），并运行期重建
  音乐服务；未知源保存时 422，catalog hint 里写明。详见 `docs/music-sources.md`。

### PUT `/api/v1/config/values`

```json
{"values": {"assistant.reply_max_chars": 160,
            "audio.playback.queue_ms": 200}}
```
→
```json
{"config": { ...redacted... },
 "applied": ["assistant.reply_max_chars"],
 "restart_required": true,
 "restart_fields": ["audio.playback.queue_ms"]}
```
未知 path / 类型 / 范围 → 422，`detail` 按字段：`[{"loc": ["audio.playback.queue_ms"], "msg": "..."}]`。
整份 Config 校验通过才写（沿用 `save_config` 的原子写 + 备份）。

### POST `/api/v1/system/audio/restart`
只重启音频面（`AudioPlaneSupervisor.stop/start/watch`），返回 `{restarted: bool, error: ""}`。
音频类「重启生效」字段保存后，前端可调它替代整机重启。

## 2. 面板

- 删除旧 `settings` 面板；新增配置类面板，全部走 `component: "config-center"`：
  - `assistant-session` 助手与会话（groups: assistant, session）tier1
  - `speaker` 音箱参数（groups: devices；并给「设备管理」页链接）tier2
  - `providers` 外部资源（groups: providers.*）tier3
  - `tools` 工具（groups: tools.*, music.*）tier4
  - `foundation` 底子·高级（groups: system, web, audio.*, ack）tier1，带警告
- PanelSpec 增加 `title_en`（英文标题）与 `component_args`（对象，透传给组件作为 props）。
- `PanelSpec.component` 机制已有（`web/src/components/panelComponents.ts`），`config-center`
  由前端注册。
- 旧面板/端点契约保持不变：`tests/test_panels.py`、`test_api.py`、`test_setup.py`、
  `test_devices_admin.py`、`test_mcp_admin.py`、`test_phrases_api.py`。

## 3. 前端（`console-ui`）

- `web/src/i18n.ts`：zh/en 字典，侧栏切换，localStorage 记忆，`<html lang>` 同步。
- 设计系统：CSS 变量 token（颜色/间距/字体/圆角/阴影/动效），light+dark（prefers-color-scheme
  + 手动切换），语义色，focus 可见，`prefers-reduced-motion` 降级。
- 导航：运行（概览/对话/日志）· 生活（音乐/记忆）· 配置（助手与会话/音箱参数/外部资源/工具/
  底子·高级）· 维护（设备管理/MCP-HA/话术/密钥/体检/安装向导）。
- 配置中心：按 catalog 自动渲染；字段行显示 label、hint、效果徽章（保存生效/重启生效）、
  单位/范围/选项；tier1 折叠警告；dirty/save/reset；422 字段红框；`restart_required` 时
  显示重启命令，音频字段提供「仅重启音频面」按钮。
- 手机 390px 无横向溢出；深链/刷新/旧路径重定向可用。

## 4. 验收

- 后端：`uv run pytest -q` 全量 + `uv run ruff check src tests scripts` 全绿。
- 前端：`npm run typecheck` + `npm run build` 绿；deep-design-qa 循环截图（桌面 1440 / 手机 390）。
- 集成：Lead 重启 dev 后逐页截图 + 抽查保存/重启标记 + 真实语音回归（唤醒/打断/点歌/新闻/HA）。
