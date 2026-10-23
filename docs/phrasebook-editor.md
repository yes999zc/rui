# P6 PhraseBook 编辑区设计（只设计，不实现）

> 2026-10-23，d1c。给 C-UI P6「PhraseBook 编辑区 + 剧本测试」做设计；**本文不含实现**，
> 等 Lead review 后再排期。范围以 `docs/interaction.md` §3/§5（V3）与 `docs/console.md` P6 行为准。
> 老项目引用均指 `xiaoai-voice-entry/server/`，只读。

## 0. 结论摘要（先看决定）

| 问题 | 决定 | 一句话理由 |
|---|---|---|
| 可编辑哪些键 | `session/phrases.py` 的 **49 个 DEFAULTS 键全部可编辑**；其中 3 个 `ha.*` 标「高级/模型提示片段」 | 所有用户能听到/模型能读到的系统话术都在这一张表；编辑器不能只覆盖一半 |
| 覆盖层放哪 | **`data_dir/phrases.yaml`**，不新增 config section | 话术是用户内容，不是部署意图；与 `memory.json`/`music_history.json`/`music_aliases.json` 同层，`setup --force` 不会冲掉 |
| 生效时机 | **保存后下一句话术立即生效**（替换模块级 `book`），不重启、不等唤醒 | 所有调用点都是 `phrases.book.render(...)`，换对象即可；比老项目「下次唤醒」更快且可解释 |
| 占位符防呆 | **保存时硬校验**：键必须存在；模板里的 `{slot}` 集合必须与默认模板完全一致（可换顺序，不可增删改名）；只允许 `{name}` 简单字段 | `render()` 对缺槽会静默 `_strip_slots`，用户删了 `{device}` 只会得到「好，开。」且没人报错；必须在写入前挡 |
| 格式/长度 | 空文本、控制字符、未知占位符、format spec（`{count:03d}`/`{x!r}`）一律 422；语音键 ≤300 字，`ha.*` ≤2000 字 | TTS 与提示词都有实际长度边界；错误要可读、按字段回显 |
| 运行时兜底 | 保留现有 `_strip_slots`，并补一条 warning + 控制台可见的 `last_errors` | 编辑器不是唯一写入口（NAS 上手改文件），渲染层仍不能炸 |
| 剧本测试 | 两层：**pytest 契约/场景**（无真机）+ 控制台**只读剧本运行器**（假设备/假 TTS，绝不碰真 HA） | 可重复、可在 CI 跑；语音/真机由验收日单独做 |
| P6 有效期 | 只做中文单表；不做 per-device / per-language / 多套话术包 | 先收口，别把 P6 做成 i18n 框架 |

预估：后端 0.5 天、前端 0.75–1 天、剧本/测试 0.5 天、验收 0.25 天，合计约 **2 天**。

## 1. 现状（设计的事实基础）

### 1.1 数据与渲染

- `session/phrases.py`：`DEFAULTS: dict[str, str]` 共 **49 键**；`PhraseBook(overrides)` 把
  defaults 与覆盖层合并；模块级单例 `book = PhraseBook()`；`render(key, **slots)` 用
  `str.format`，缺 key 返回 key 本身，槽位错误回退 `_strip_slots`（静默）。
- 渲染点（全部走同一张表，所以覆盖层一处生效）：
  - `session/turn.py:991` `_render_tool_result`：`ToolSpec.interaction.exec[status] -> key`；
  - `web/api/app.py` 确定性路径：`_music_transport`（音乐随机/传输词）、`_context_command`、
    `_volume_command`、`_stop_command`、`bare device name` 问回、轮级 `turn.failed`；
  - `music/tools.py:263-265`：`music.taste_item`/`music.taste_tail` 先渲染再嵌进
    `music.taste_top` 的 `{names}`/`{tail}`（**嵌套渲染**，编辑器要能 preview 组合结果）；
  - `tools/ha.py:76-78`：`ha.entity_list`/`ha.list_stale` 是**给模型的提示片段**，不是朗读句；
    `ha.multi_entity_rule` 同理（「多命中全部传入」的工具纪律）。
- 键的 49 个默认值与槽位清单见 §9 附录；`music.*` 26、`turn.*` 2、`volume.*` 4、
  `memory.*` 6、`context.*` 2、`device.*` 6、`ha.*` 3。

### 1.2 测试现状

- `tests/test_music_tools.py`：每个工具的 `exec[status]` key 必须存在，且经
  `phrases.book.render(...)` 断言；`test_the_music_module_carries_no_wording_of_its_own`
  用 AST 钉住 music 工具无中文字面话术。
- `tests/test_ha_bridge.py`、`tests/test_turn.py:515` 等直接断言 `phrases.book.render(...)`。
- 也就是说：**默认表已经被测试覆盖**；P6 的风险不在默认表，而在「用户改过以后」的行为。

## 2. 老项目参考（带行号）

| 老代码/文档 | 可继承/可避开 |
|---|---|
| `assistant_v3.py:836-895` | 音乐 `@@PLAY/@@ONLINE/@@VOL/@@FAVCUR/@@NEXT/@@STOP...` 字符串标记；四套方言的起点，正是 PhraseBook 要消灭的 |
| `assistant_v3.py:906-913` | `@@BRIEF` 标记：工具结果需 system 层翻译，模型只拿 notice |
| `prompts.py:36`、`:76-77` | `REQUIRED_HINTS = ("entity_id","不要假装成功")` + `missing_hints()`：老项目已意识到「用户改提示词会删掉关键约束」，但只在 prompt 层检查 |
| `webapp.py:328-330` | `GET /api/v1/prompt/preview`：返回**拼装后原文** + `missing`；控制台预览与线上字节一致 |
| `webapp.py:392-410` | 保存系统提示词缺关键约束 → **409 `need_confirm`**，前端二次确认后才写 |
| `web/index.html:77-104` | 「人设与提示词」页：核心提示词 + 个性化人设 + 预设 chips + 拼装预览 + 「改坏会让工具失灵」标签 |
| `web/index.html:92-95`、`web/app.js:1232-1246` | 人设预设（`personas.json`）：点一下替换 `assistant.persona`，不动核心规则 |
| `docs/console-effects.md:15-18` | 老控制台的「生效时机」审计：人设是「下次唤醒」，因为 `begin_session` 拍快照；**新 PhraseBook 可以做到下一句立即生效** |
| `docs/console.md` §2.5 | 新控制台的「逃生舱」：PanelSpec 声明 `component` → 懒加载手写 Vue；P4 SetupView 已证明这个模式可用 |

**结论**：老项目的「预览 + 缺失提示 + 二次确认」是对的直觉，但它检查的是提示词里的
**字符串片段**，不是模板占位符；P6 要把这个思路升级成结构化校验（§6）。

## 3. 可编辑范围（明确边界）

### 3.1 可编辑（P6 范围）

- `session/phrases.py::DEFAULTS` 的 **全部 49 键**。按 UI 分组：
  - **音乐**（26）：`music.*` —— 朗读确认/失败/收藏/口味；
  - **轮级兜底**（2）：`turn.failed`、`turn.stuck`；
  - **音量**（4）：`volume.*`；
  - **记忆 / 上下文**（8）：`memory.*`、`context.*`；
  - **设备**（6）：`device.done/unreachable/excluded/missing_target/ask_verb/not_done`；
  - **模型提示片段（高级）**（3）：`ha.entity_list`、`ha.list_stale`、`ha.multi_entity_rule`。
    警告：这三条进 prompt，删规则会改变模型行为；占位符同样必须保留。

### 3.2 不在 PhraseBook 里、P6 不要顺手改的

`assistant.persona` / `system_prompt`（设置面板已有）、`assistant.filler_text`、
`ack.text`、`session.farewell_text*`、`rules.STOP_REPLIES`、工具 `description`（模型契约）、
`tools/info.py` 的新闻内容、`session/briefing.py` 的播报结构、`session/prompts.py` 的
`SEARCH_HINT` 等。P6 只做「系统话术表」，避免同一句话出现两个编辑入口。

## 4. 覆盖层放哪：三选一

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| A. 新 `config.assistant.phrases: dict[str,str]` | 复用 `PUT /api/v1/config`、已有备份/校验/422 | config.yaml 是部署意图（`setup` 生成、Docker 挂载说明都围绕它），49 条用户话术塞进去很脏；改话术要 bump 配置心智/模板；`config` 版本迁移多一类内容；控制台通用 form 不适合表格编辑 | 否 |
| B. `data_dir/phrases.yaml`（推荐） | 与 `memory.json`/`music_history.json`/`music_aliases.json` 同层；`setup --force` 不覆盖 data；可 NAS 上手改、可导出；独立 API/面板；不动 config schema | 多一个文件与加载路径；需要单独的校验/备份 | **是** |
| C. SQLite/DB | 查询强 | 过度设计；现有 data 全是小文件 | 否 |

文件格式（`data_dir/phrases.yaml`，YAML 便于手改）：

```yaml
version: 1
updated_at: "2026-10-23T12:00:00+08:00"
overrides:
  music.stopped: 好，停。
  device.done: 好，{device}已{action}。
```

规则：

- 只存**覆盖项**，不存整张表；默认值永远来自代码（升级后新键自动出现）。
- 写入：`tmp` + `os.replace` 原子替换；保留最近 1 个 `.bak-<ts>`（用户误删可回滚）；
  权限 0600（内容不敏感，但避免 LAN 上别的用户改你的助手）。
- 启动加载失败（YAML 坏、未知键、占位符不合法）：**不阻止启动**，记 warning，
  继续用默认表，并在 `GET /api/v1/phrases` 返回 `error`/`invalid` 供控制台显示；
  旧文件保留不覆盖（用户可修）。
- 代码删掉某键后，文件里的旧覆盖保留但标 `unused`（GET 返回列表；可一键清理），
  不静默删除用户内容。

## 5. 运行时生效机制

```python
# session/phrases.py（设计接口，不实现）
def load(path: Path) -> list[str]:   # 返回加载错误；成功时替换模块级 book
def reset() -> None:                 # 测试/无文件环境恢复默认
def snapshot() -> dict:              # 给 GET /api/v1/phrases
```

- `create_app` 拿到 `cfg.system.data_dir` 后、注册工具/构造 runner 前调用
  `phrases.load(Path(cfg.system.data_dir) / "phrases.yaml")`。
- 保存端点校验通过后写文件 → `phrases.load(...)` 重新构造 `PhraseBook(overrides)`
  并赋给模块级 `book`。所有调用点都是 `phrases.book.render`（属性查找），**下一次渲染**
  即用新表；进行中的一轮已经渲染过的句子不变（不重播），之后的新句子用新表。
- 不重启、不等下一次唤醒；与老项目 `console-effects.md` 的「下次唤醒」形成对比，
  控制台标签写「**下一句话说生效**」。
- 多进程注意：当前是单 uvicorn worker；如果以后上多 worker，保存只在写进程生效，
  其他 worker 需要重启或文件监听（P6 不做，写进 runbook 边界）。
- 测试：默认（无 `phrases.yaml`）行为完全不变；任何测试若加载了覆盖，必须在 fixture
  teardown `phrases.reset()`，避免全局 `book` 污染（加入 `conftest.py` autouse 或
  在 phrase 测试内显式 reset）。

## 6. 防呆：占位符、格式与长度

### 6.1 问题

`PhraseBook.render` 的容错是**为线上不崩**设计的：缺槽 → `_strip_slots` 静默删 `{...}`；
未知槽 → `KeyError` 也走同一路径。用户把
`device.done = "好，{device}{action}。"` 改成 `"好，开了。"` 不会报错，但从此不知道
控制的是哪个设备；把 `{count}` 删掉，`共 N 首` 就永远没了。

### 6.2 注册表 + 保存时硬校验（推荐）

- `session/phrases.py` 增加（由默认表**自动生成**，不手写）：
  `DEFAULT_SLOTS: dict[str, frozenset[str]] = {key: parse slots of DEFAULTS[key]}`，
  只认 `string.Formatter().parse` 里的 field name。
- 保存校验（对每个被修改的键）：
  1. key 必须在 `DEFAULTS` 中，否则 422 `unknown_key`；
  2. 文本去空白后非空；
  3. 解析出的槽位集合 **== `DEFAULT_SLOTS[key]`**：允许换顺序、改前后措辞，
     **不允许删除/新增/改名**；错误消息点名缺哪个，如
     `「music.started」需要保留 {title}、{artist}、{count}，当前缺少 {artist}`；
  4. 只接受简单字段 `{name}`；拒绝 conversion/format spec（`{x!r}`、`{count:03d}`），
     拒绝嵌套 `{a{b}}`；
  5. 字面大括号必须写 `{{`/`}}`（`parse` 返回 `None` 字段，视为合法文本）；
  6. 长度：语音键 1–300 字符；`ha.*` 1–2000 字符；禁止 `\x00` 等控制字符
     （`\n` 只允许在 `ha.entity_list`/`ha.multi_entity_rule`，它们本来就多行）；
  7. 全部 or 全不写：任一条不合法 → 422，文件保持原样（不能半套覆盖）。
- 为什么不用老项目式「warning + 二次确认」：老项目只有 2 条关键片段，误删难察觉；
  P6 有 49 条，逐条硬校验 + 一键恢复默认更简单，也避免用户点「确认」放行后静默丢信息。
- 默认表自身也由测试保护：每个 `DEFAULTS[key]` 解析出的槽位 == `DEFAULT_SLOTS[key]`，
  且每个 `ToolSpec.exec` 指向的 key 都存在（已有测试）。

### 6.3 运行时兜底（纵深）

- 保留 `_strip_slots`；额外 `log.warning("phrase %s missing slots %s", key, missing)`，
  并把最近 N 条错误放进 `PhraseBook.last_errors`，`GET /api/v1/phrases` 暴露给控制台。
- `render` 永不抛异常（现有约定不变），避免话术错误吃掉一轮语音。

## 7. 控制台交互（P6 UI 设计）

- 后端注册一个 PanelSpec：`id="phrases"`, `title="话术"`, `group="config"`, `order=45`,
  `source="/api/v1/phrases"`, `component="phrasebook-editor"`（走 §2.5 逃生舱；
  P4 SetupView 已证明手写组件可行），`enabled=true`。
- 页面结构：
  - 顶部：生效说明「保存后下一句话生效，不需要重启」、`error` 横幅（文件坏/未加载）、
    「全部恢复默认」「导入/导出」。
  - 分组（音乐/轮级/音量/记忆/设备/高级提示片段），支持搜索 key 或文本。
  - 每行：`key`、当前文本（可编辑 textarea）、默认值（折叠/「恢复默认」）、
    槽位 chips（不可删除，只能看着它出现在文本里）、「预览」渲染结果、`已修改` 标记。
  - 保存：一次 `PUT` 全量 overrides（只含改过的键）；422 按 `loc` 标红到具体键，
    与 `useConfigForm` 的 422 回显一致；保存成功刷新 `overridden` 状态。
  - 高级组（`ha.*`）默认折叠 + 黄条：「这些是模型提示片段，改动会影响工具调用行为」。
- 不做：逐设备话术、逐语言/多语言、TTS 试听（P6 可选后置；若要试听，复用
  `render_filler_pcm` 的 TTS 路径但别占真机声音）。
- 鉴权：与其它写端点一致 `Depends(auth)`；401 复用现有 token UI。

## 8. 剧本测试（P6 的「能追问/能回归」工具）

### 8.1 两层

1. **pytest 契约层（必须，进 CI）**
   - `DEFAULT_SLOTS` 与 DEFAULTS 一致；所有 `exec` key 存在；每个键 `render` 示例槽位不报错；
   - 覆盖层：写临时 `phrases.yaml` → `load()` → 断言特定 key 渲染变化；reset 后回默认；
   - 非法覆盖：缺槽/多槽/未知键/空文本/format spec → `ValueError`（API 映射 422），
     文件不变；
   - 嵌套：改 `music.taste_tail` 后 `music.taste_top` 的组合结果变化；
   - 启动坏文件：默认表 + `error` 可见，不崩。
2. **场景层（剧本）**
   - 目标：不依赖真 LLM/真设备，按「用户说 → 工具 → status → 话术 key → 最终朗读文本」
     逐步断言；也能跑控制台「剧本测试」按钮。
   - 运行器：复用 `tests/test_turn.py` 的 fake provider + `ToolSpec` 机制；脚本化 LLM
     返回 tool_calls，fake TTS 记录文本，`TurnTrace` 记录阶段。
   - 文件格式（`tests/scenarios/*.yaml` 或 `data_dir/scenarios/`，P6 先放 tests）：

```yaml
name: stop-while-a-song-plays
steps:
  - say: "放一首晴天"
    expect:
      tool: music_play
      phrase: null          # answer=silent，歌就是答案
      music_state: playing
  - say: "停止播放"
    expect:
      tool: music_stop
      phrase: music.stopped
      speech: "好，不放了。"
```

```yaml
name: briefing-third-item
steps:
  - say: "念一下今天的新闻"
    expect:
      tool: news
      briefing: true        # 1 轮 LLM，直接播报
      speech_contains: "第三条"
  - say: "第三条再说说"     # 真实会话里 history 已含原文；剧本用 fake history
    expect:
      speech_contains: "第三条"
```

   - 控制台「剧本」页：列出 allowlist 场景，点击运行，逐步显示
     `transcript / tools / status / phrase key / 渲染文本 / audio_bytes`；
     后端端点（设计）`POST /api/v1/debug/scenario {name}`，**只用 fake 设备/假 TTS**，
     不连真 HA、不出声；与 `/debug/tool` 一样受 auth 保护。
   - 话术回归：改完一句话术，跑对应场景应看到新文本；改坏占位符连保存都过不了。

### 8.2 验收判据（P6 done 的定义，可测）

1. `GET /api/v1/phrases` 返回 49 个键，每个含 `default/text/slots/example/overridden/group`；
   `error` 字段能呈现坏文件。
2. 在 UI 改 `music.stopped` → 保存 → 下一次停止路径朗读新文本（fake TTS/`book.render` 断言），
   不需要重启。
3. 删除任一必需占位符 / 加未知占位符 / 未知 key → **422 按字段报错**，`phrases.yaml`
   字节不变；改回后保存成功。
4. 「恢复默认」（单键/全部）后在响应对应话术中回到原文。
5. `phrases.yaml` 坏文件启动：服务正常起、默认表生效、控制台能看到原因。
6. 至少 4 个剧本：音乐停止、日报直读、设备 done、音量设置；全部通过；改话术后对应
   剧本断言新文本。
7. `pytest` 全量 + `ruff` + 前端 `vue-tsc/build` 全绿；现有 AST「工具无中文字面话术」测试
   仍通过（编辑器只改数据，不该引入新的中文到 tools）。
8. 文档：`docs/interaction.md` §5 V3 勾掉镜像项；`docs/console.md` P6 行补验收记录；
   `docs/d1c-notes.md` 或新 `docs/phrasebook.md` 记录文件格式与回滚方法。

## 9. 附录：49 键槽位清单（由 DEFAULTS 自动解析，P6 以此为注册表）

```
music.started              {title, artist, count}
music.started_one          {title, artist}
music.not_found            {want}
music.empty                —
music.unreachable          —
music.unresolved           —
music.paused               —
music.resumed              —
music.not_playing          —
music.not_paused           —
music.next                 {title}
music.last_track           —
music.prev                 {title}
music.first_track          —
music.stopped              —
music.starred              {title}
music.unstarred            {title}
music.star_failed          —
music.no_current           —
music.favorites_empty      —
music.favorites_read_failed—
music.favorites_count      {count, names}
music.taste_empty          —
music.taste_top            {names, tail}
music.taste_item           {artist, plays}
music.taste_tail           {title}
turn.failed                —
turn.stuck                 —
volume.set                 {pct}
volume.read                {pct}
volume.failed              —
volume.unknown             {pct}
memory.saved               {text}
memory.empty_text          —
memory.forgotten           {text}
memory.not_found           —
memory.recall_empty        —
memory.recall              {items}
context.off                —
context.on                 —
device.done                {device, action}
device.unreachable         {device}
device.excluded            {device}
device.missing_target      —
device.ask_verb            —
device.not_done            —
ha.entity_list             {list, note}
ha.list_stale              —
ha.multi_entity_rule       —
```

注意：调用方可能多传槽位（如 `ha.entity_list` 还被传 `count`，模板里没有即忽略），
校验只以**模板实际占位符**为准；不要把「多传」当错误。

## 10. 风险、未决与开放问题

- **未决 1（需 Lead 拍板）**：49 键全开 vs 只开语音键、`ha.*` 只读。本设计推荐全开 +
  高级标识；若担心模型纪律被改坏，可先把 `ha.*` 设为只读，P6 只编辑 46 键。
- **未决 2**：缺占位符是硬拒绝（推荐）还是老项目式 409 二次确认。硬拒更容易测试，
  但用户若真想删 `{count}` 就做不到；建议先硬拒，收集反馈再说。
- **未决 3**：剧本 UI 是否纳入 P6，还是 P6 只交付 pytest 场景、控制台剧本留 P7？本设计
  建议至少交付 4 个 pytest 场景 + 控制台列表（只读运行），不做可视化编辑器。
- 多 worker/多实例：文件变更只在写实例生效；P6 单 worker 假设，Docker 现在就是单进程。
- 覆盖层漂移：代码改默认话术不会覆盖用户已改的键（预期）；代码**重命名键**时旧覆盖变
  `unused`，不自动迁移；控制台显示提示，用户手动复制。
- 编辑后 TTS 读音：文本变化可能导致多音字/断句变化，P6 不做语音试听验证；真机听感在
  验收日抽查。
- 安全：话术文件不含密钥，但控制台写端点必须 auth；长度上限防止把 volume 写满；
  Vue 渲染转义由框架负责，后端仍按纯文本返回。
- 与 `PhraseBook` 单例的测试隔离：见 §5；这是实现时最容易踩的坑。

## 11. 实现落地（task-4，2026-10-23）

三个开放项已由 Lead 拍板（49 键全开 + `ha.*` 高级；占位符硬拒绝；P6 只交 pytest 场景 +
只读列表），本节记录后端交付物。**未接 app.py/web/**（端点和面板由 Lead 接）。

### 11.1 文件

| 文件 | 内容 |
|---|---|
| `src/house_ear/session/phrases.py`（改） | `DEFAULT_SLOTS`（由 DEFAULTS 自动解析，不手写）；`validate_override(key, template) -> list[str]`；`validate_overrides(mapping) -> {key: [errors]}`；`apply_overrides(mapping)`（全有或全无，非法抛 `PhraseOverrideError(errors)`）；`load_overrides(path) -> (applied, errors)`（永不抛，坏文件保留默认）；`reset(key | None)`；`current_overrides()`；`snapshot()`；`PhraseBook.template()`；`render()` 缺槽改为 **warning + `book.last_errors`**（仍返回 `_strip_slots` 的兜底文本，行为不炸） |
| `src/house_ear/session/phrasebook.py`（新） | `data_dir/phrases.yaml` 读写：`read(path)`、`load(data_dir)`、`save(data_dir, overrides)`、`clear(data_dir, keys=None)`、`document(data_dir)`、`path_for()`；YAML 复用 `config.store._yaml`；原子 tmp+replace；0600；保留 1 个 `.bak-*`；只存 `version/updated_at/overrides` |
| `tests/test_phrasebook.py`（新，15 条） | 49 键槽位契约；改措辞/换顺序通过、删槽/加槽/未知键/坏括号/format spec/换行/控制字符/超长拒绝；apply/reset；缺槽记录 last_errors；嵌套 taste；文件 round-trip/0600/备份/坏文件/单键非法全不生效/未知键 unused/clear/document；`load_overrides` 入口；两条 YAML 剧本契约 |

### 11.2 最终槽位规则（代码事实）

- `{slot}` 集合必须与 `DEFAULT_SLOTS[key]` **完全相等**：换顺序、改措辞 OK；增/删/改名 → 422。
- 只接受 `{name}`：拒绝 `{a.b}`、`{list[0]}`、`{count:03d}`、`{x!r}`、空 `{}`。
- 字面大括号写 `{{`/`}}` 合法；`
` 只允许在 `ha.*`；控制字符一律拒绝。
- 长度：普通话术 ≤300 字；`ha.*` ≤2000 字；空/纯空白拒绝。
- 未知 key 在**控制台保存**时 422；在**磁盘文件**里视为 `unused`（保留、忽略、不阻止加载）。
- 文件里只要有一条非法覆盖 → **整份不应用**（`load` 后默认表生效 + errors 返回），
  不出现“一半新一半旧”。

### 11.3 接线建议（Lead 的端点/面板）

```python
from ...session import phrasebook

# create_app: cfg 已知、注册工具/构造 runner 之前
phrasebook.load(Path(cfg.system.data_dir))          # 坏文件只会 warning + 默认表

# GET /api/v1/phrases
return phrasebook.document(Path(cfg.system.data_dir))
# -> {effect:"next_phrase", keys:[{key,default,text,example,slots,overridden,group,advanced}],
#     overrides, file/file_exists/file_error, errors, unused, file_overrides, last_errors}

# PUT /api/v1/phrases（全量 overrides；空对象 = 全部恢复默认）
try:
    phrasebook.save(data_dir, body["overrides"])
    return phrasebook.document(data_dir)
except phrases.PhraseOverrideError as exc:
    raise HTTPException(422, detail=exc.errors)     # {key: [msg, ...]}

# POST /api/v1/phrases/reset  {"keys": [...]} / {} = all
phrasebook.clear(data_dir, body.get("keys"))
```

- `save()` 内部已做 **写文件 + 立即 apply**（替换 `phrases.book`），所以下一句话术就生效，
  不需要重启/唤醒；`clear()` 复用 `save()`。
- UI 的 422 直接按 `exc.errors` 的 key 回显；`errors` 里的 key 也用于文件坏时展示。
- 若端点想自己读文件而不立即生效：`phrasebook.read(path)` / `phrases.validate_override`。
- 启动顺序很重要：`phrasebook.load` 必须在第一个工具/话术渲染之前；`phrases.book` 是模块级，
  `load` 会替换它，所有 `phrases.book.render` 调用点自动看到新表。
- 多 worker：写只发生在收到 PUT 的进程，其他 worker 不会自动 reload（当前单进程假设）；
  若上多 worker，需在 runbook 写「保存话术需重启/统一 worker」。

### 11.4 剧本契约（P6 交付形态）

`tests/test_phrasebook.py::SCENARIOS` 是只读 YAML 列表（当前 2 条），由 pytest 解释执行：

```yaml
- name: stop-while-a-song-plays
  kind: tools
  steps:
    - {call: music_play, args: {query: "放一首晴天"},
       expect: {status: [ok, ok_one], tool: music_play, music_state: playing}}
    - {call: music_stop, args: {},
       expect: {status: ok, phrase: music.stopped, speech: "好，不放了。", music_state: idle}}
- name: briefing-third-item
  kind: turn
  turns:
    - {say: "念一下今天的新闻", expect: {tools: [news], llm_rounds: 1}}
    - {say: "第三条再说说", expect: {history_item: 3, llm_rounds: 1}}
```

- `kind: tools` 直接调工具 handler，断言 `status`、`exec[status]` 的 phrase key、渲染后朗读文本；
- `kind: turn` 用脚本化 LLM + fake TTS + `direct_hook` 跑真实 `TurnRunner`，断言
  `llm_rounds`、tools、朗读原文、history 里能追问第 3 条；
- 两条都覆盖新行为（停止词工具路径、日报直读），可作为 P6 的回归基线。
- 控制台只读剧本列表后续可把这组 YAML 移到 `data_dir/scenarios/` 或接口下发，不影响本实现。

### 11.5 验收状态

- `pytest -q`：**516 passed**（task-4 新增 15 条，全绿）；`ruff check src tests scripts` clean。
- 未接线项（按分工归 Lead）：app.py 的 GET/PUT/reset 端点、`phrases` 面板/组件、编辑 UI。
  接线后用 `document()` 的 49 条 keys 渲染即可，不需要再改 session 层。

