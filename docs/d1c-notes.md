# D1c 调研落地笔记（阶段 1：语言索引 / 描述式点歌 / 点歌多候选）

> 2026-10-23，teammate `d1c`。范围是 `docs/ROADMAP.md` D1c 行的 ① ② ⑤ 与 ③ ④：
> ① ② ⑤ 在阶段 1（music 层），③ ④ 的纯逻辑在阶段 2A（§7）、接线在阶段 2B（§8，Lead 交接后）。
> 本文只写方案取舍、真实数字和剩余风险；代码注释里有每处细节的出处。

## 1. 结论（先看数字）

| 项 | 结果 |
|---|---|
| 全量测试 | **493 passed**（2026-10-23 13:0x，含 Lead 修好的 config 测试与并行新增）。D1c 新增 49 条：阶段 1 共 38——`test_music_index.py` 20、`test_music_semantic.py` 11、`test_music_tools.py` +7；阶段 2A 共 11——`test_briefing.py` 8、`test_session.py` +3；阶段 2B 共 11——`test_session.py` +5、`test_briefing.py` +6。`ruff` 对并行修改的 `tests/test_kws.py`/`test_turn_audio.py` 有 2 个外部 I001，本任务涉及文件单独 check clean |
| lint | 本任务涉及文件 `ruff check` clean；全量 `src tests scripts` 因并行修改的 `tests/test_kws.py`/`test_turn_audio.py` 报 2 个 I001（非本任务） |
| 真实 Navidrome 建索引 | **5736 首 / search3 12 页 / 0.78 s / 缓存 1189 KiB**（data_dir/library_index.json） |
| 语言分布（真实库） | ja 20 · ko 3 · zh 5333 · en 377 · other 3 · unknown 0 |
| 索引查询延迟 | `pick()` 0.5–2.8 ms（本地文件，无网络） |
| 首次「放几首日文歌」（缓存冷） | **790 ms**（含建索引），之后 1–3 ms |
| 描述式点歌（真实库） | 「来点安静的钢琴」4 首 20 ms；「放点摇滚」20 首 7 ms；「九十年代的粤语老歌」20 首 2 ms |
| 多候选搜索（真实库） | 「晴天」3 个候选（含周杰伦）；「富士山下」5 个版本；11–51 ms |
| 混合请求 | 「播放日文歌曲《そばにいるね》」→ 只排 1 首《そばにいるね》（标题胜出），17 ms |
| 2C 真机（dev :8091） | 日报 HTTP 19.64 s / 音频 1,886,182 B（≈58.9 s，playback_s +59.7）/ 1 LLM round；音乐「晴天」真播 8 s 后「停止播放」→ idle；设备音量 178→128→178 |

## 2. 老项目依据（只读参考）

- `xiaoai-voice-entry/server/library_index.py` + `docs/library-index.md`：每首歌语言索引的完整规则、
  7 天 TTL、`pick()/stats()`、artist override 表；实测 5738 首 12 请求 ~0.8 s。
- `server/semantic.py`：描述式请求 → `{genres, artists, keywords}` 计划；`tools/music.py:225-243`
  `semantic_pool()` 是“流派优先、命中就不再看的互斥”实现；`:664-696` 是描述式分支。
- `tools/music.py:472-489` `music_search`（只查不播，返回候选）；`:400-443` `by_mood` 的
  BPM→流派→“排除反情绪”→随机的降级阶梯。
- `docs/interaction.md` §6 明确列出的缺口：**「搜索/点歌多候选返回通道」**（新 music 工具连候选列表
  都不给模型）。

## 3. 改了什么

### 3.1 `src/rui/music/library_index.py`（新）

- 分类器 `classify()` 原样移植老项目规则：artist override → kana → hangul → 其它文字 →
  标题汉字（zh，低置信）→ 拉丁标题（有英文流派标签则高置信）→ unknown。**标题优先于歌手，
  专辑最后**；`・`/`ー` 不算 kana（张学友《你的名字・我的姓氏》保持 zh，老项目实测）。
- `LibraryIndex`：`build()` 用 `search3` 空查询 500 行分页（Navidrome 上是 match-all），失败退回
  `getAlbumList2`+`getAlbum` 相册遍历；`data_dir/library_index.json` 原子写；7 天 TTL；
  **重建失败继续用旧缓存**（NAS 抖动不该让点歌变“没找到”）。
- `pick(language, genre, keywords, moods, years, limit, seed)`：所有条件**同时生效（交集）**，
  交集为空时按“情绪 → 流派 → 年代 → 关键词”逐个放宽；语言永不放宽；**不会放宽成“全库随便放”**
  （无语言条件下丢掉最后一个过滤条件就返回空，让上层诚实降级）。流派匹配是单向包含
  （`rock` 命中 Pop-Rock，`j-pop` 不会命中整个 Pop 货架，老项目实测）。
- 索引行比老项目多存 `bpm`、`moods` 相关字段来源：真实 Navidrome 的 song 自带 `bpm`，虽然本库
  随机 20 首里 0 首有 BPM（与老项目一致），仍先存着，tag 补齐后情绪过滤自动变准。
- `stats()`：按语言/来源/流派计数，给后续控制台和运维查缓存用。

### 3.2 `src/rui/music/semantic.py`（新）

- `plan(text) -> SemanticPlan | None`：把描述式中文映射成 **languages / genres / keywords /
  moods / years** 五个过滤条件。识别语言（日/韩/英/粤国华/小语种）、流派（摇滚/民谣/电子/爵士/
  古典/说唱/流行/轻音乐/乡村/蓝调/雷鬼/金属/朋克/灵魂/原声）、乐器（钢琴/吉他，同时进
  genres 和 keywords）、情绪（calm/hot）、年代（80/90/00/10/20 年代 → 年份区间）。返回 None
  表示“这是名字/歌名/随机”，交给字面搜索——`Rammstein`、`雪の華` 不会被误判成流派池。
- `pool(plan, source, index)`：索引存在时一次性按全部条件取候选；**索引没有命中才**退回
  `by_genre`/关键词搜索/随机 200 首按情绪过滤（老项目 `by_mood` 阶梯：BPM → 流派关键词 →
  排除反情绪 → 随便）。索引优先，避免“标签池的一首”混进“按歌语言判定的两首”。
- **不再在点歌路径调用大模型**：老项目每次描述式点歌都要一次 `deepseek-flash` JSON 计划。
  实际会出现在客厅里的词是封闭小集合，本地映射更快、无 key 也能用、也不会编出库里没有的流派
  （老项目 `_fallback` 也只有 9 个词）。若以后要支持开放式描述，可照 `names.NameResolver`
  的方式加可选 planner，不改变本模块的输出形状。

### 3.3 `src/rui/music/query.py`（改）

- `extract_title()`：描述词剥离改为调 `semantic.strip_descriptors()`，并保留老项目
  “混合请求标题优先”的规则：`播放日文歌曲留在我身边` → 标题 `留在我身边`。
  只有在**确实剥掉描述词**时才清掉残留的「歌曲/歌/的」，否则 `周杰伦的歌` 会被拆坏
  （artist-only 判定依赖它）。
- `language_genres()`：实现搬到 `semantic.LANGUAGE_GENRES`，这里只做委托，保证“索引的语言定义”
  和“没索引时的流派兜底”用同一张表。

### 3.4 `src/rui/music/tools.py`（改）

- `music_play` 新顺序：收藏/随机 → 引号标题（《》绝对优先）→ 混合请求先搜标题 → 描述式走
  `index.ensure()` + `semantic.pool()` → 旧的语言流派兜底 → 字面 `_search`。
- 新增只读工具 **`local.music_search`**（对齐老项目 `music_search`）：返回候选列表给模型。
  它**没有 exec 话术映射**，`TurnRunner._render_tool_result` 会把
  `{"status":"ok","want":...,"count":...,"candidates":[...]}` 原样交给模型——这就是
  `docs/interaction.md` §6 缺的“候选返回通道”。同时 `ToolResult.options` 也填了结构化候选
  （未来 `choose` 原语的输入）。描述式请求调它时返回 `status="descriptive"` +
  `suggest="local.music_play"`，把模型引回播放工具（老项目同款防呆：模型把「日文歌」当歌名逐条搜）。
- `_started()` 的结果带 `options`（最多 8 个候选）：老项目只把第一首的 brief 给模型，模型既看不到
  同名多版本，也没法说清实际放了谁。
- `spec` 的 `local.music_play` 描述补了一句：描述式点歌也直接调它、query 传原话。

### 3.5 `src/rui/music/sources/navidrome.py`（改）

- `Track` 增加可选 `genre`/`bpm`（`music/model.py`），Navidrome 返回的字段直接带上，情绪过滤和
  关键词判断不再需要额外请求。
- 新增 `songs_page()` / `albums_page()` / `album_songs()` 三个公开分页方法给索引构建用；
  仍走同一套 token+salt 认证、httpx 重试和日志脱敏（密码从不出现在 URL 参数之外）。

## 4. 真实 Navidrome 验证记录

凭据来自 `music.secrets_path` 指向的凭据 JSON（只引用变量/文件，不写值）；验证只走 HTTP 与索引/工具
函数，**没有对真机下发任何 say/music 指令**。

1. 建索引（`LibraryIndex.build()`）：5736 首，`search3` 分页 12 次，0.78 s，缓存 1189 KiB。
   语言分布：ja 20 / ko 3 / zh 5333 / en 377 / other 3；来源：cjk 4907、override 799、
   latin_genre 12、latin 11、hangul 3、其它 3。
   **说明**：这个库本身只有 18 个含假名的标题、J-Pop 标签 16 首，所以 ja=20 是真实结果，不是漏判；
   override 表里的日本人歌手（中島みゆき、宇多田ヒカル 等）把汉字标题的日文歌捞了回来。
2. 语言点歌：`放几首日文歌` → 20 首（如《残酷な天使のテーゼ》《Lemon》《そばにいるね》），
   首次 790 ms（含建索引），第二次 1–3 ms。
3. 描述式点歌：`来点安静的钢琴` → 4 首（含《Lydia（钢琴演奏版）》），20 ms；
   `放点摇滚` → 20 首；`九十年代的粤语老歌` → 20 首（1990–1999 年份过滤生效）；
   `来点英文歌` → 20 首。
4. 多候选：`music_search("晴天")` → 3 个候选（含周杰伦《晴天》同名/近名版本），15 ms；
   `music_search("富士山下")` → 5 个版本，12 ms；`music_search("Cold Shoulder")` → 1 条，51 ms。
5. 混合请求：`播放日文歌曲《そばにいるね》` → 只排《そばにいるね》1 首（标题胜出），17 ms；
   `放周杰伦的晴天` → 1 首，78 ms。

## 5. 取舍与理由

| 决定 | 理由 |
|---|---|
| 索引放 `data_dir`，7 天 TTL | 重建是 12 次 NAS 请求；放 `/tmp` 会在 NAS 重启/容器更新后丢掉，点歌首句变慢。灰度：重建失败继续用旧缓存。 |
| 语言索引替代 J-Pop 兜底 | 语言是歌的属性，不是文件标签。真实库 J-Pop 只有 16 首，索引给出 20 首且覆盖未打标日文歌。流派兜底保留为“索引未建好”的路径。 |
| 确定性语义计划，不调 LLM | 词表封闭、延迟为 0、无 key 可用、不会编流派；老项目模型只做“理解”，取歌仍在本地库，这一层用规则可覆盖。 |
| 过滤器取交集，空交集才放宽 | 老项目 `semantic_pool` 是“有流派结果就不看关键词/歌手”的互斥；新实现 `日文+安静`、`流派+关键词` 同时生效。放宽顺序按信号强弱，且不会放宽成“全库”。 |
| 候选列表走 `ToolResult` JSON + `options` | exec 话术一渲染，模型就看不到列表了；JSON 是数据不是话术，符合 V-line（工具不吐中文）。结构化 `options` 留给 `choose`。 |
| 情绪数据先行、tag 后补 | 本库 0/20 随机样本有 BPM（老项目同样），所以按流派关键词 + “排除反情绪” + 随机兜底，保证「来点安静的」永远有歌。 |

## 6. 剩余风险

1. **首次描述式点歌要等建索引**：LAN 实测 0.78 s，NAS 忙或库更大时可能更久；工具总超时 20 s。
   失败会降级到流派/字面搜索，不会报错。TTL 暂未做成配置项（`MusicConfig` 没有 `index_*` 字段），
   如需控制台可调，后续加配置字段即可。
2. **语种识别上限**：汉字标题的日文歌与中文歌无法区分（老项目已知弱点），靠 `ARTIST_OVERRIDES`
   补数据；新增日本歌手要加表并强制重建。泰语/俄语等落到 `other`（本库 3 首），「放泰语歌」
   可能没有足够结果。
3. **粤语/国语的区分**：元数据没有方言字段，两者都归 `zh`（老项目同）；「粤语歌」实际是
   中文歌池，只是年份/流派过滤能让结果更接近。
4. **情绪标签**：依赖 BPM/流派 tag，本库这两类信息少；「安静摇滚」这类交集在无 tag 时会按规则
   放宽（先放宽情绪）。不会空，但可能不“准”。
5. **索引文件无控制台入口**：目前只能删文件/等 TTL 重建；C-UI 阶段可加“曲库索引状态/重建”面板。
6. **真机听感未验收**：按分工本阶段不对真机下发声音；D1c 的“语言/描述式点歌各 1 例”目前是
   HTTP + 单元测试证据，等 Lead 调度一次真机点歌做耳朵确认。
7. **阶段 2B 已接线但真机未验**：③④ 的代码级/进程内/HTTP 证据见 §8，真机声音（两个停止分支）
   仍需和 Lead 约声音流再验。另外本机 PATH 没有 `/opt/homebrew/bin/ffmpeg`，真实音频 resolve 前
   须补 PATH，否则每首歌都会静默跳过（Lead 实测）。

## 7. 阶段 2A：停止词二分 / 日报直通的纯逻辑（已完成，未接线）

Lead 把阶段 2 拆成两半：先做不碰 `app.py` 的纯逻辑（本节），接线等交接。已完成：

### 7.1 ③ 停止词二分（`session/rules.py`）

- 新增 `stop_scope(transcript, music_playing, words=None) -> "media_stop" | "bare_stop" | ""`
  （常量 `MEDIA_STOP` / `BARE_STOP`）：
  - 不是停止请求 → `""`（长句、含「关掉」的别的问题都不算）；
  - **没在放歌 → `bare_stop`（散场）**；放歌中 → `media_stop`（停歌）；
  - 「退下吧/退下/关机/不聊了」无论是否在放歌都归 `bare_stop`：用户是让助手走，不是让喇叭闭嘴；
    ASR 错听「对一下吧」现在也被 `is_dismiss` 认成 `退下吧`（老项目实测）。
- 判据原话写进 docstring：「没在放歌时说『停止播放』= 散场」。
- 纯函数、无 service/speaker/config；`words` 直接复用 `stop_intent` 的配置词表。
- 测试：`tests/test_session.py` +3（放歌/没放歌两义、dismiss 恒散场、非停止请求返回空）。

### 7.2 ④ 日报直通（`session/briefing.py`，新）

老项目行号（都指 `xiaoai-voice-entry/server/`）：

| 老代码 | 作用 |
|---|---|
| `tools/news.py:126-136` `_briefing()` | 生成 `{date, count, source, text}`：日期头 + 「第一，…。第二，…。」 + 可选「最后一句微语」 |
| `assistant_v3.py:906-913` | 工具返回 `@@BRIEF` 标记；dispatcher 把 payload 放进 `lat["brief"]`，**只给模型**「播报已准备好（N 条，…），直接念给用户听。」 |
| `assistant_v3.py:1017-1024` | `lat["brief"]` 路径：直接返回脚本原文（不做第二轮 LLM），并把 user 原话 + `assistant text[:800]` 原样写进 history |
| `assistant_v3.py:3492-3512` | 播报按原文流式朗读（comment：「read the daily briefing as written」），barge-in 照常 |
| `assistant_v3.py:3496` | 同上，强调不重新总结 |
| `prompts.py:24` | prompt 只要求「问新闻时调用 news 工具」，不要求模型总结 |

新模块（纯逻辑，不 fetch、不发声、不碰 session）：

- `Briefing`（frozen dataclass）：`date`(ISO) / `items`(已编号、已补句号) / `tip` / `source`；
  `count`、`speech_text`（真正朗读的原文，含「今天是X月X日，星期X」+ 第一/第二… + 微语）。
- `from_news(news, date=None, tip="", source="60s", limit=5) -> Briefing | None`：
  接受 `list[str]` / `list[{"title": ...}]` / 整个 digest `{"news": [...], "tip": ...}`；
  空白折叠、缺句号补「。」、limit 夹在 1–10、空日报返回 `None`（让调用方说「没取到」，不念空头）。
- `to_payload()`：**故意不含 `text`**，只有 `{status:"briefing", date, count, source}`——
  防止 payload 被 `_render_tool_result` JSON 化后把播报原文喂给模型。
- `model_notice()`：老 dispatcher 原话「播报已准备好（N 条，date），直接念给用户听。」
- `history_messages(transcript)`：`[user 原话, assistant speech_text[:800]]`，这是「第三条再说说」的依据。
- `item_at(n)` / `item_index("第三条再说说")`：1-based、支持中文数字和「第3条」、越界返回 None，
  给将来确定性追问留口子（history 路径仍覆盖自由问法）。
- 测试：`tests/test_briefing.py` 8 条（脚本形状、输入形状/标点/上限、空日报、payload 不含原文、
  history 支持第三条、800 字截断、中文/数字序号、日期默认与覆盖）。

接线时（阶段 B）的硬约束：`briefing` 状态必须在 `_render_tool_result` **之前**被 system 层拦下；
朗读 `speech_text`；把 `history_messages()` 追加进会话；同一轮的 LLM 绝不看到 `speech_text`。

## 8. 阶段 2B：app.py 接线（已完成，代码级/进程内实测）

### 8.1 ③ 停止词分支（`web/api/app.py::_stop_command`）

把原 stop_intent 块抽成可测的 `_stop_command(app, clean, runner, client, trace, traces) -> bool`，
on_utterance 只负责调用；判据全部走 2A 的 `rules.stop_scope`：

- **media_stop**（放歌中，非 dismiss 词）：`service.stop()` 清队列 → PhraseBook `music.stopped`
  「好，不放了。」→ `reply_finished()`、灯灭 → `session_action="media_stop"`，**session 不关**
  （`runner.idle.touch()`，不调用 dismiss）。
- **bare_stop**（没放歌，或「退下吧/关机/不聊了」）：先 `service.stop()`（dismiss 词在放歌时也必须
  停歌，09:17 实测歌 12 s 后回来是 bug），无 service 但 plane 仍占 music 时发 `StopAudio`，
  再 `runner.dismiss()`（保留告别/灭灯语义），`session_action="bare_stop"`。
- 停止词回复只从 PhraseBook 取；app.py 没有新增中文字面话术。

实测（进程内、真实 `MusicService`、假 speaker，脚本见 §9）：
`media_stop` → 队列 3→0、state=idle、session_active=True、回复 `好，不放了。`；
`bare_stop` → 回复 `好。`、session_active=False；`退下吧` 放歌中 → 先停歌再 `好。`、会话关闭；
非停止句返回 False、零副作用。

### 8.2 ④ 日报直通（`tools/info.py` + `session/turn.py` + `app.py`）

- `tools/info.py::news()` 成功时返回 `Briefing`（失败仍是普通句子，让模型诚实说没取到）；
  `from_news` 负责日期/编号/补句号/微语，沿用老 `tools/news.py:126-136` 的形状。
- `session/turn.py` 新增 **opt-in** `direct_hook` 与 `DirectSpeech(text, notice, history)`：
  `_call_tool` 在短语层之前把 handler 原始输出交给 hook；hook 返回 `DirectSpeech` 时，
  这次工具结果只给模型 `notice`，该轮工具跑完后直接按句朗读 `text`（`speech_pieces()`：
  只按句号切、不按逗号切，避免 350 ms 句间停顿变碎），**不再发起下一轮 LLM**；
  history 用 hook 给的 `history`（`Briefing.history_messages()`，user 原话 + 原文[:800]）。
  **不注入 hook 时该路径一行不跑**，旧行为保持不变（有回归测试）。
- `app.py::_direct_speech` 只认 `Briefing`：转成 `DirectSpeech`，并把 runner 以
  `direct_hook=_direct_speech` 构造。`Briefing.__str__` 故意返回 `model_notice()`，
  即使哪天 hook 漏接，模型也只会看到「播报已准备好」，不会把原文灌进 prompt。
- 真实 60s 日报实测（2026-10-23，5 条）：`Briefing` 260 字；整轮 **1 次 LLM round**、
  9 个 TTS 片、`result.reply == speech_text`、history = user + 原文 260 字、
  `item_index("第三条再说说") == 3` 且原文在 history 里；模型工具消息是
  「播报已准备好（5 条，2026-10-23），直接念给用户听。」，**不含原文**。
- HTTP 级：`TestClient` + 假 audio plane 走 `POST /api/v1/debug/say`「念一下今天的新闻」，
  返回 `reply == speech_text`、`tools == ["news"]`、LLM 只跑 1 round（测试已进 `test_briefing.py`）。

### 8.3 改动文件

`src/rui/web/api/app.py`（`_direct_speech`、`_stop_command`、runner 构造；未碰 panels 表 /
console 静态路由 / debug 端点 / audio supervisor）、`src/rui/session/turn.py`
（`DirectSpeech`/`DirectHook`/`speech_pieces`/hook 分支/`_record_direct_history`）、
`src/rui/tools/info.py`（news 返回 Briefing）、`tests/test_session.py` +5、
`tests/test_briefing.py` +6、本文件。

### 8.4 未验 / 外部问题

- **真机声音未验**：③ 两个分支是进程内真实 service + 假 speaker；④ 是真实日报 + 假 TTS/audioplane
  与 debug/say HTTP。没有对真机下发声音（按约定需先约声音流）。
- 并行外部失败：`tests/test_config.py::test_defaults_are_sane` 断言 `playback.rate == 48000`，
  但 `config/schema.py` 在 12:26 被改成默认 16000/1ch（非本任务改动；已报 Lead）。D1c 自身所有
  测试通过。

## 9. 2C 真机声音验证（2026-10-23，dev 服务 :8091）

Lead 12:39 用最新代码（e8a821a 等）重启 dev 服务（PID 31458，plane pid 31498，音箱
connected=True），声音由本任务独占；全程用 `X-Auth-Token` 调 debug HTTP，未重启服务、未改代码。
结束状态：music=idle、output_owner=""、设备音量恢复 178。

### 9.1 ④ 日报真机（debug/say）

`POST /api/v1/debug/say {"text":"念一下今天的新闻"}`：

| 指标 | 实测 |
|---|---|
| HTTP | 200，**19.64 s**（返回时全部 TTS 已入队，不等到播完） |
| reply | **260 字**，与独立 `info.news(Config(), count=5)` 生成的 `Briefing.speech_text` **逐字相等** |
| audio_bytes | **1,886,182**（16 kHz mono ≈ **58.9 s**） |
| 实际播放 | `/api/v1/audio` 的 `playback_s` 5.3 → **65.0 s**（约 59.7 s），播完 owner 回到空 |
| 工具 | `tools=["news"]` |
| LLM | 日志 **`llm round:` 只有 1 行**（624 ms，1 tool call）；`tool news -> direct speech (260 chars)`；`direct speech: 260 chars (model round skipped)` |
| 模型看到的内容 | 日志：`tool news({"count": 5}) -> 播报已准备好（5 条，2026-10-23），直接念给用户听。`（无原文） |

`GET /api/v1/turns?limit=1` 中 reply 与独立 Briefing 完全一致，证明真机路径没有被二次总结。
**history 真机未验**（原因）：debug/say 每次先 `runner.reset_history()`，`GET /api/v1/debug/prompt`
只返回系统提示词、不返回 history；不改生产代码就无法观测。该点在单测 + 进程内实测中已证明
（§8.2：history = user 原话 + 260 字原文，`item_index("第三条再说说")==3`）。

### 9.2 ③ 工具式音乐停止（debug/say，非停止词分支）

- `debug/say "放一首晴天"`：HTTP 1.62 s，reply `""`（歌就是答案，静默），`tools=["music_play"]`；
  `/api/v1/music` → playing《一直是晴天_2》-许茹芸，queue_left 3；8 s 内 `playback_s` +8.0。
- `debug/say "停止播放"`：HTTP 1.64 s，`tools=["music_stop"]`，音乐 → **idle**，回复 `"停了。"`
  （debug/say 不进停止词分支，这是模型在工具结果后自己说的一句，不是 media_stop 的 PhraseBook
  「好，不放了。」）；`audio_bytes=29866`；随后 owner 从 dialogue 回空。
- 收尾 `POST /api/v1/debug/play {"stop":true}`，最终 music=idle、owner=""。
- **`media_stop`/`bare_stop` 真唤醒分支未验**：debug/say 不经过 `_stop_command`，真机验收需真人说
  「停止播放」；按 Lead 指示留给 9哥 的嗓子。

### 9.3 音量真机

| 步骤 | 实测 |
|---|---|
| 初始 | `GET /api/v1/device/volume` → **178**（0–255） |
| `debug/say "音量调到50%"` | HTTP **0.52 s**，reply「音量调到 50%了。」，`tools=["volume"]`，`audio_bytes=74240` |
| 读回 | **128** |
| 恢复 | `PUT /api/v1/device/volume {"value":178}` → 200；读回 **178** |
| 收尾 | 硬停音频，music=idle、owner="" |

### 9.4 2C 未验项

1. ③ 两个停止分支的真唤醒声音行为（media_stop 会话不关 / bare_stop 散场 + 停歌）——需真人嗓子。
2. ④ 真机 history 原文（debug/say reset_history，HTTP 无读数口）；单测/进程内已证。
3. 真机「好不好听/断句」听感（本任务只能用 audio_bytes/playback_s/owner 遥测证明出声，
   没有耳朵或回环录音）。
4. 并行外部状态（非本任务）：跑 2C 收尾时 `tests/test_kws.py`、`tests/test_turn_audio.py`
   在 12:43 被并行修改，`ruff check` 报 2 个 I001（导入顺序）；`pytest` 仍 **493 passed**。
   本任务改动的文件单独 ruff check 全 clean。

## 10. 复现/运维片段

```bash
# 重建索引（等价于首次描述式点歌触发；7 天 TTL，--force 语义用 force=True）
cd <repo> && uv run python - <<'PY'
import asyncio, pathlib
from rui.music.sources.navidrome import NavidromeSource
from rui.music.library_index import LibraryIndex
secrets = pathlib.Path(cfg.music.secrets_path)
idx = LibraryIndex(pathlib.Path("/var/lib/rui/library_index.json"),
                   NavidromeSource(secrets, pathlib.Path("/tmp/rui-music")))
print(asyncio.run(idx.build(force=True))["count"], idx.stats()["languages"])
PY
```

（真实凭据只从该 json 读取，文档与日志不写值。）
