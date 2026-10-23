# D1c 调研落地笔记（阶段 1：语言索引 / 描述式点歌 / 点歌多候选）

> 2026-10-23，teammate `d1c`。范围是 `docs/ROADMAP.md` D1c 行的 ① ② ⑤；③（停止词二分）与
> ④（日报直通）要改 `src/house_ear/web/api/app.py`，按分工等 Lead 交接，见 §7。
> 本文只写方案取舍、真实数字和剩余风险；代码注释里有每处细节的出处。

## 1. 结论（先看数字）

| 项 | 结果 |
|---|---|
| 全量测试 | **456 passed**（本次新增 38 条：`test_music_index.py` 20、`test_music_semantic.py` 11、`test_music_tools.py` +7） |
| lint | `uv run ruff check src tests scripts` clean |
| 真实 Navidrome 建索引 | **5736 首 / search3 12 页 / 0.78 s / 缓存 1189 KiB**（data_dir/library_index.json） |
| 语言分布（真实库） | ja 20 · ko 3 · zh 5333 · en 377 · other 3 · unknown 0 |
| 索引查询延迟 | `pick()` 0.5–2.8 ms（本地文件，无网络） |
| 首次「放几首日文歌」（缓存冷） | **790 ms**（含建索引），之后 1–3 ms |
| 描述式点歌（真实库） | 「来点安静的钢琴」4 首 20 ms；「放点摇滚」20 首 7 ms；「九十年代的粤语老歌」20 首 2 ms |
| 多候选搜索（真实库） | 「晴天」3 个候选（含周杰伦）；「富士山下」5 个版本；11–51 ms |
| 混合请求 | 「播放日文歌曲《そばにいるね》」→ 只排 1 首《そばにいるね》（标题胜出），17 ms |

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

### 3.1 `src/house_ear/music/library_index.py`（新）

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

### 3.2 `src/house_ear/music/semantic.py`（新）

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

### 3.3 `src/house_ear/music/query.py`（改）

- `extract_title()`：描述词剥离改为调 `semantic.strip_descriptors()`，并保留老项目
  “混合请求标题优先”的规则：`播放日文歌曲留在我身边` → 标题 `留在我身边`。
  只有在**确实剥掉描述词**时才清掉残留的「歌曲/歌/的」，否则 `周杰伦的歌` 会被拆坏
  （artist-only 判定依赖它）。
- `language_genres()`：实现搬到 `semantic.LANGUAGE_GENRES`，这里只做委托，保证“索引的语言定义”
  和“没索引时的流派兜底”用同一张表。

### 3.4 `src/house_ear/music/tools.py`（改）

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

### 3.5 `src/house_ear/music/sources/navidrome.py`（改）

- `Track` 增加可选 `genre`/`bpm`（`music/model.py`），Navidrome 返回的字段直接带上，情绪过滤和
  关键词判断不再需要额外请求。
- 新增 `songs_page()` / `albums_page()` / `album_songs()` 三个公开分页方法给索引构建用；
  仍走同一套 token+salt 认证、httpx 重试和日志脱敏（密码从不出现在 URL 参数之外）。

## 4. 真实 Navidrome 验证记录

凭据来自 `~/.hermes/.secrets/lan_media.json`（只引用变量/文件，不写值）；验证只走 HTTP 与索引/工具
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
7. **阶段 2 未动**：③ 停止词二分、④ 日报直通都要改 `web/api/app.py`，与 Lead 控制台任务同文件，
   等显式交接。

## 7. 阶段 2 待交接（③ ④）

- ③ `media_stop` vs `bare_stop`：放歌中「停止播放」= 停歌（现有 `_music_transport` 已覆盖大部分）；
  **没在放歌时说「停止播放」= 散场**（结束会话），需要在 app.py 的确定性词分支里按
  `app.state.music.state` 二分。验收要能看到两种语义的不同处理路径。
- ④ 日报直通 `briefing`：播报文本不进 LLM、原文进 history 支持「第三条再说说」。现有
  `tools/info.py:news()` 返回字符串进模型，需要 app.py 加确定性直通状态与 history 原文槽位。
- 这两项都在 Lead 的 `app.py` 写作用域内，本任务不动；Lead 明确交接后再做。

## 8. 复现/运维片段

```bash
# 重建索引（等价于首次描述式点歌触发；7 天 TTL，--force 语义用 force=True）
cd <repo> && uv run python - <<'PY'
import asyncio, pathlib
from house_ear.music.sources.navidrome import NavidromeSource
from house_ear.music.library_index import LibraryIndex
secrets = pathlib.Path.home() / ".hermes/.secrets/lan_media.json"
idx = LibraryIndex(pathlib.Path("/var/lib/house-ear/library_index.json"),
                   NavidromeSource(secrets, pathlib.Path("/tmp/house-ear-music")))
print(asyncio.run(idx.build(force=True))["count"], idx.stats()["languages"])
PY
```

（真实凭据只从该 json 读取，文档与日志不写值。）
