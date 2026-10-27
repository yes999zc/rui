# 意图层触发词冲突审计（审计 A，2026-10-27）

> **只审不改**：本卡不改 `rules.py` / `app.py` / 词表 / `docs/music.md` / `docs/interaction.md`；
> 产出是这张报告 + 可复算脚本 `tools/intent_matrix.py`。
> 判据用 9 哥 定稿策略（`docs/HANDOFF.md` §「9 哥 定稿的目标策略（审计判据，修复时照此执行）」）：
> ① 暂停词默认只有「暂停」「停一下」，其余暂停词（先停这儿/停会儿/停住/停在这）从暂停表删除、不挪进结束词；
> ② 结束词表保持现状、不新增；③ 任何结束词被说出且音乐在放 → **音乐停止 + 会话结束，两者一起做**，
> 取消「有歌就 media_stop 保持会话」；④ 暂停=保留进度+会话继续，结束词=音乐清零+会话关闭，没有中间态。
>
> 计数器：**11 条冲突 = P0 2 + P1 3 + P2 6**（见 §4）。

## 0. 结论摘要

**根因不是某一条词，而是三层叠加：**

1. `rules.stop_scope` 把「有歌」映射成 `media_stop`（暂停语义、会话保持），而不是 9 哥 要求的
   「音乐停止 + 会话关闭」（`rules.py:291-317`，实现在 `app.py:966-984`）。
2. `app._stop_command` 的 `music_active` 把 **paused 也算「有歌」**（`app.py:940`），
   所以「歌暂停时说停止」同样走 `media_stop`。
3. `rules.stop_intent` 用「任意位置子串命中、总长最多 +2 字」容差（`rules.py:55`, `:281-283`），
   让「停止你好」这种非停止句也命中停止；再叠加判定顺序（`app.py:2797-2831`），
   「暂停/停一下」在有歌时先被 `_music_transport` 截走、无歌时又落到 `_stop_command`。

**Top5（短句 → 现状 → 9 哥 策略下的目标行为）**

| # | 短句 | 现状 | 目标 |
|---|---|---|---|
| 1 | `停止`（有歌） | `media_stop`，音乐停、**会话保持** | 音乐停止 **且** 会话关闭 |
| 2 | `停止你好` | `media_stop`（任意位置命中、总长 +2） | 不命中停止；交模型/澄清 |
| 3 | `暂停`（无歌） | `bare_stop`（结束会话） | **决策 1/2 在无歌时冲突，需 9 哥 补一句**（见 C3） |
| 4 | `拜拜`（有歌） | `media_stop`，会话保持 | 音乐停止且会话关闭 |
| 5 | `先停这儿`（有歌） | `music_pause`（仍暂停） | 从暂停表删除 → 不直接暂停（落模型） |

## 1. 复算方法与稳定性

```bash
uv run python tools/intent_matrix.py
```

- 纯函数：只 import `rui.config.schema.SessionConfig` 与 `rui.session.rules`；无网络/设备/服务/夹具/时间源。
- 维度：50 句 × {有歌 playing / 歌暂停 paused / 无歌 idle} × {会话开 / 会话关} × {同屋 owner / 异屋 other}。
- 审计标签（脚本标识；不全部等于 `session_action`/tool 名）：`music_random` / `music_pause|resume|next|prev` /
  `volume` / `context_on|off` / `media_stop` / `stop_elsewhere` / `bare_stop` / `clarify.verb` / `model`。
  映射：`music_random` → tool `music_play`；`context_on/off` → `context.on/.off`；
  `media_stop`/`bare_stop`/`stop_elsewhere` 与 `session_action` 同名。
- 脚本复刻的顺序是 `app.py:2797-2831`：`_music_transport`(:798) → `_volume_command`(:2297) →
  `_context_command`(:1017) → `_stop_command`(:923) → `verbless_device_name` → 模型。
- **稳定性证据**：连续两次运行 `diff -q` 为空，退出码 0（`/tmp/intent1.txt`、`/tmp/intent2.txt` 字节相同）。
- 脚本对每一句都断言「会话开 == 会话关」；50 句全部相等 ⇒ **会话开/关不是本矩阵的自变量**
  （这四个判定层不读取会话状态；会话状态只影响后续上下文与 idle 告别）。
- 前提假设（生产恒满足）：`app.state.music` 服务存在；`app.state.volume_tool` 存在；
  transcript 已过唤醒词剥离、回声过滤、`is_noise`。`normalize()` 只剥标点/空格，不改字。

## 2. 判定矩阵（50 句；完整 TSV 以脚本输出为准）

> 列说明：同屋/异屋只影响 `media_stop`（异屋时变成 `stop_elsewhere`，只关本屋、不切别人的歌）；
> 「会话开/关」列恒为「无影响」（脚本逐句验证）。未列出的状态组合可从这两条推出。

| 句子 | 有歌（同屋） | 歌暂停（同屋） | 无歌（同屋） | 异屋差异 | 会话开/关 |
|---|---|---|---|---|---|
| `停止` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `停止播放` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `停止你好` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `停` | `model` | `model` | `model` | — | 无影响 |
| `停一下` | `music_pause` | `music_pause` | `bare_stop` | — | 无影响 |
| `停一下下` | `music_pause` | `music_pause` | `bare_stop` | — | 无影响 |
| `停下` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `停下来` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `停掉` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `别放` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `别放了` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `别说了` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `别唱` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `不听了` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `暂停` | `music_pause` | `music_pause` | `bare_stop` | — | 无影响 |
| `先停这儿` | `music_pause` | `music_pause` | `model` | — | 无影响 |
| `停会儿` | `music_pause` | `music_pause` | `model` | — | 无影响 |
| `停住` | `music_pause` | `music_pause` | `model` | — | 无影响 |
| `停在这` | `music_pause` | `music_pause` | `model` | — | 无影响 |
| `继续` | `music_resume` | `music_resume` | `model` | — | 无影响 |
| `接着放` | `music_resume` | `music_resume` | `model` | — | 无影响 |
| `恢复播放` | `music_resume` | `music_resume` | `model` | — | 无影响 |
| `下一首` | `music_next` | `music_next` | `model` | — | 无影响 |
| `上一首` | `music_prev` | `music_prev` | `model` | — | 无影响 |
| `换一首` | `music_next` | `music_next` | `model` | — | 无影响 |
| `前一首` | `music_prev` | `music_prev` | `model` | — | 无影响 |
| `切歌` | `music_next` | `music_next` | `model` | — | 无影响 |
| `放首歌` | `music_random` | `music_random` | `music_random` | — | 无影响 |
| `唱首歌` | `music_random` | `music_random` | `music_random` | — | 无影响 |
| `退下吧` | `bare_stop` | `bare_stop` | `bare_stop` | — | 无影响 |
| `退下` | `bare_stop` | `bare_stop` | `bare_stop` | — | 无影响 |
| `退一下` | `model` | `model` | `model` | — | 无影响 |
| `对一下吧` | `bare_stop` | `bare_stop` | `bare_stop` | — | 无影响 |
| `不聊了` | `bare_stop` | `bare_stop` | `bare_stop` | — | 无影响 |
| `够了` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `结束` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `退出` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `再见` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `拜拜` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `晚安` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `睡了` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `睡吧` | `media_stop` | `media_stop` | `bare_stop` | media_stop → stop_elsewhere | 无影响 |
| `大一点` | `model` | `model` | `model` | — | 无影响 |
| `小一点` | `model` | `model` | `model` | — | 无影响 |
| `静音` | `volume` | `volume` | `volume` | — | 无影响 |
| `音量调到50%` | `volume` | `volume` | `volume` | — | 无影响 |
| `关闭上下文` | `context_off` | `context_off` | `context_off` | — | 无影响 |
| `打开上下文` | `context_on` | `context_on` | `context_on` | — | 无影响 |
| `关掉客厅灯` | `model` | `model` | `model` | — | 无影响 |
| `关闭窗帘` | `model` | `model` | `model` | — | 无影响 |

**读表要点：**

- 除 dismiss 词（退下吧/退下/不聊了）外，所有结束词 + 有歌/歌暂停 → `media_stop`（C1）；+ 无歌 → `bare_stop`；dismiss 词恒 `bare_stop`。
- `暂停`/`停一下`/`停一下下`：有歌或暂停 → `music_pause`；无歌 → `bare_stop`（C3）。
- `先停这儿`/`停会儿`/`停住`/`停在这`：有歌/暂停 → `music_pause`（C5，决策要求删除）；无歌 → `model`。
- `继续`/`接着放`/`恢复播放`/`下一首`/`上一首`/`换一首`/`前一首`/`切歌`：有歌/暂停 → `music_*`；无歌 → `model`（C8）。
- 单字 `停` → `model`（C7）；`退一下` → `model`（C10，ALIASES 只有「退一下吧」）；
  `对一下吧` → `bare_stop`（别名生效）。
- `退下吧`/`退下`/`不聊了` → 恒 `bare_stop`；bare 分支会顺手停掉本屋的歌（`app.py:986-1013`），
  行为上已符合 9 哥「结束词=音乐清零+会话关闭」，只是 `session_action` 名字叫 `bare_stop`。
- `大一点`/`小一点` → `model`（C9）；`静音`/`音量调到50%` → `volume`；
  `关闭上下文`/`打开上下文` → `context_off/on`；`关掉客厅灯`/`关闭窗帘` → `model`（负向对照：未被误判为结束）。

## 3. 五条生产日志逐条解释

> 日志原文（9 哥 提供，`stop intent:` 由 `_stop_command` 打印）：

| 时间 | 句子 | 结果 | 分支链 |
|---|---|---|---|
| 01:00:44 | `停止播放。` | media stopped, session open | transport✗ → volume✗ → context✗ → stop: `stop_intent`✓ + `music_active`✓ → `MEDIA_STOP` → media 分支 |
| 02:37:36 | `停止。` | session closed | 同上，但 `music_active`✗ → `BARE_STOP` → bare 分支 |
| 03:04:50 | `拜拜。` | media stopped, session open | `拜拜` 在 `cfg.session.end_words`（非 `DISMISS_WORDS`）+ 有歌 → `MEDIA_STOP` |
| 03:13:27 | `停止。` | session closed | 同 02:37:36（无歌） |
| 03:24:56 | `停止你好。` | media stopped, session open | `stop_intent` 任意位置命中、总长容差（4 = 2+2）+ 有歌 → `MEDIA_STOP` |

1. **`停止播放。` → media stopped**：`normalize` 后 `停止播放`（4 字）。`rules.stop_intent`
   （`rules.py:258-286`）按长度降序匹配到「停止」，`word in t` 且 `len(t)=4 <= 2+2`
   （`rules.py:281-283`，容差常量 `rules.py:55`）⇒ True。`is_dismiss`（`rules.py:67-76`）为 False，
   `music_active` 为 True（`app.py:940`，playing 或 paused）⇒ `stop_scope` 返回 `MEDIA_STOP`
   （`rules.py:291-317`）。进入 media 分支 `app.py:966-984`：`service.stop()` + 「好，不放了。」
   + `reply_finished()`（会话保持）+ 日志「media stopped, session open」（`app.py:984`）。
2. **`停止。` → session closed**：同一 `stop_intent`，但当时无歌/歌 idle ⇒ `music_active` False
   （`app.py:940`）⇒ `BARE_STOP`（`rules.py:315`）⇒ bare 分支 `app.py:986-1013`：
   `runner.dismiss()` + `_release_turn` + 日志「session closed」（`app.py:1013`）。
3. **`拜拜。` → media stopped**：「拜拜」不在 `rules.STOP_WORDS`（`rules.py:27-30`），但在
   `SessionConfig.end_words` 默认表里（`schema.py:1003-1007`）；`_stop_command` 传给 `stop_intent`
   的正是 `cfg.session.end_words`（`app.py:937`），所以命中。`is_dismiss` 不含拜拜（`rules.py:60`），
   有歌 ⇒ `MEDIA_STOP` ⇒ 同日志 1 的 media 分支。
4. **`停止。` → session closed**：同 2。
5. **`停止你好。` → media stopped**：`normalize` 后 4 字；「停止」作为子串命中且 4 = 2+2
   （`rules.py:281-283`）⇒ 一个语义上不是停止的句子走进停止分支；有歌 ⇒ `MEDIA_STOP`。
   **这就是 C2 的现场复现**：只要「结束词 + ≤2 个字符」就会被当成结束，内容完全不看。

## 4. 冲突清单（11 条）

类别：① 同词多义；② 子串误匹配（含 `len(t)` 容差）；③ 无人覆盖（落模型）；④ 文案与行为不一致。
优先级：P0 = 当前生产已出现错误行为；P1 = 同词多义/顺序导致的可预期误判；P2 = 覆盖缺口或文案。

| ID | 类 | 级 | 现象 / 复现短句 | file:line | 当前行为 | 9 哥 策略下的目标行为 | 影响面 |
|---|---|---|---|---|---|---|---|
| C1 | ①/② | **P0** | 结束词 + 有歌：`停止` `停止播放` `拜拜` `再见` `晚安` `够了` `结束` `退出` `停下` `停下来` `停掉` `别放` `别放了` `别说了` `别唱` `不听了` `睡了` `睡吧` | `app.py:940`（paused 也算 active）、`:966-984`（media 分支；异屋走 `:948-963`）；`rules.py:291-317`（MEDIA_STOP） | 停歌 + 「好，不放了。」+ `reply_finished`，**会话保持**，日志 `media stopped, session open` | 音乐停止 **且** 会话关闭（两者一起做），走 dismiss/告别路径 | 9 哥 实测的原始现象；用户说停止后仍在听，下一句被当新请求 |
| C2 | ② | **P0** | `停止你好`（结束词作为任意位置子串、总长最多 +2 字）：`停止你好` `停止播放`（合法）`别放了吧` 等 | `rules.py:55`（`_STOP_TAIL_CHARS=2`）、`:281-283`（任意位置子串 + 总长容差）；`app.py:937` | 非停止语义的短句也命中结束词 → 有歌 `media_stop`、无歌 `bare_stop` | 只有整句=结束词、`ALIASES` 显式别名（**新增** `停止播放`→`停止`、`别放了`→`别放`、`别放了吧`→`别放`；别名不是结束词表，不违反决策 2），或纯语气词尾巴才算结束；删除「任意位置、总长 +2」的通配。「停止你好」不得命中 | 误结束会话/误停歌；ASR 噪声尾字触发 |
| C3 | ① | **P1** | `暂停` / `停一下` / `停一下下`（无歌） | `app.py:2797-2831`（transport 先于 stop）、`:846`（idle 时 transport 放弃）；`rules.py:92-98`、`:311-317`；`schema.py:1003-1007`（两者同时在 end_words） | 有歌/暂停 → `music_pause`；**无歌 → `bare_stop`（结束会话）** | 决策 1 说「暂停=暂停、会话继续」，决策 2 说「结束词表不变」（暂停/停一下 仍在表内）→ **无歌时两句决策冲突**；建议 9 哥 明确：无歌时暂停词不结束会话（澄清/忽略），或确认沿用结束词 | 无歌时说「停一下」会被当成散场；同词在不同音乐状态做相反的事 |
| C4 | ① | **P1** | 歌暂停时说 `停止` / `拜拜` | `app.py:940`（`state in ("playing","paused")`） | paused 也算「有歌」→ `media_stop`，只停歌、会话保持 | 决策 3 的「音乐在放」未区分 paused；按「结束词=音乐清零+会话关闭」，paused 也应 clear+close（C1 修完后 paused 自然也是 stop+close；C4 的独立价值是决策 3「音乐在放」是否含 paused 的措辞） | 暂停后想结束会话，结果只是「媒体停止」并继续听 |
| C5 | ④ | **P1** | `先停这儿` / `停会儿` / `停住` / `停在这`（有歌） | `rules.py:84-89`（pause 词表 6 个词）；`src/rui/music/tools.py:314`（模型提示词仍教这些词暂停） | 命中 `music_pause`；删词表后仍会被模型提示词引导去调 pause 工具 | 暂停默认词只有「暂停」「停一下」；其余 4 词从暂停表删除、不挪进结束词；提示词同步删除 | 9 哥 决策 1 未落地；只改 `rules.py` 不改 prompt 等于没改 |
| C6 | ④ | P2 | 控制台文案（短句：N/A，文案与行为不一致，非触发词） | `src/rui/config/catalog.py:543-551` | 帮助文本写「不是子串匹配」，实际是「任意位置子串 + 总长最多 +2 字」容差（`rules.py:281-283`） | 文案与实现一致（或随 C2 收紧实现） | 运维/9 哥 按文案理解会低估误判范围 |
| C7 | ③ | P2 | 单字 `停` | `rules.py:27-30`（STOP_WORDS 无「停」）、`:84-89`（pause 无「停」）；`app.py:2813`（`verbless_device_name` 为 False，不进 2813-2824 的澄清分支）+ `:2825-2831`（落模型） | `model`：交给模型自由发挥 | 决策 2 不加结束词、决策 1 暂停词只两个 → 目标仍是 `model`；**但这是明显会说却无人覆盖的句子**，建议 9 哥 决定是否补澄清 | 模型可能继续聊天而不是停；行为不确定 |
| C8 | ③ | P2 | 无歌时 `下一首` `上一首` `切歌` `继续` `接着放` `恢复播放` | `app.py:846-849`（idle 放弃 transport）；`rules.py:92-98` | `model`（模型可能声称切歌/续播，`claims_music_success` 只做事后重试） | 决策未覆盖；建议保留 model 但加「无歌时直接说明没有在放」的确定性兜底 | 模型幻觉/空操作 |
| C9 | ③ | P2 | `大一点` / `小一点` | `rules.py:123-128`（`volume_level` 要求含 音量/声音/喇叭/静音） | `model` | 决策未覆盖；可补「无名词时澄清」或保持 model | 家里常说，落模型不确定 |
| C10 | ③ | P2 | `退一下` | `rules.py:39-46`（ALIASES 只有「退一下吧」） | `model` | 按 ASR 别名表补「退一下」→「退下吧」（需 9 哥 确认：别名算不算新增词，踩决策 2 边界） | 同一个 slip 的两个变体覆盖不一致 |
| C11 | ④ | P2 | 暂停回复与文档口径（触发句：`暂停`） | `src/rui/session/phrases.py:43`（「好，先停这儿。」）、`docs/interaction.md:27`（「停止词三分流（停歌/退会话/让位）」）、`docs/HANDOFF.md` §「9 哥 定稿的目标策略（审计判据，修复时照此执行）」（新策略） | 暂停回复仍用待删词「先停这儿」；interaction 文档仍描述三分流 | 暂停回复改成不依赖具体触发词（如「好，暂停了。」）；文档更新为「结束词=音乐清零+会话关闭」 | 说的话与实际口径不一致；后续维护者按旧文档改 |

**负向对照（无冲突，证明现有整句规则并非全错）**：`关掉客厅灯`、`关闭窗帘` → `model`（HA 命令，未被
`stop_intent` 误判）；`静音`、`音量调到50%` → `volume`；`关闭上下文`/`打开上下文` → `context_off/on`；
`放首歌`/`唱首歌` → `music_random`。

## 5. 建议修复批次

> 顺序原则：先修「用户已经听到错误行为」的 P0，再修同词多义（会持续产生误判），最后补覆盖缺口与文案。
> 每批都要求先跑脚本把对应行改成目标值，再写回归用例。

### 批次 1（P0：C1 + C2）——先修，因为它就是 9 哥 实测的现象

- **C1**：`_stop_command` 中删除 `MEDIA_STOP` 的「停歌但保持会话」语义：`stop_intent` 命中 + 有歌 →
  先 `service.stop()` 再 `runner.dismiss()`（复用 bare 分支的 dismiss/告别），`session_action` 记为
  `stop_and_dismiss`（或沿用 `bare_stop` 但保证两者都做）。涉及 `app.py:940-984`、`rules.py:291-317` 的
  `stop_scope` 调用处。
- **C2**：收紧 `stop_intent` 匹配：整句等于结束词、`ALIASES` 显式别名，或仅允许纯语气词/标点尾巴；
  删除 `_STOP_TAIL_CHARS` 的「任意位置 +2 总长」通配（`rules.py:55`, `:281-283`）。为保住今天合法的
  「停止播放」「别放了」「别放了吧」，在 `ALIASES` 显式加 `停止播放`→`停止`、`别放了`→`别放`、
  `别放了吧`→`别放`（ALIASES 不是结束词表，不违反决策 2）。
- **怎么验**：
  ```bash
  uv run python tools/intent_matrix.py | grep -E '^(停止|停止播放|停止你好|拜拜)\b'
  # 目标：停止/停止播放/别放了/拜拜 在 playing 行 = stop_and_dismiss；停止你好 在 playing 行 = model；
  #       停止播放/别放了 在 idle 行 = bare_stop（与当前矩阵的 idle 值一致）
  # 注意：今天的脚本输出是 media_stop；`stop_and_dismiss` 是 C1 修复时新增的 session_action，
  #       修完要同步脚本标签清单再跑本断言。
  ```
  再加设备/集成回归序列：放歌 → 说「停止」→ 断言音乐停 **且** 下一句不再被接（会话已关）；空闲 →
  说「停止你好」→ 断言不进入 stop 分支。
- **为什么先修**：这是唯一已在生产日志里造成用户可见错误的分支，且修复面小（一个分支 + 一个匹配函数）。

### 批次 2（P1：C3 + C4 + C5）——同词多义与词表落地

- **C3**：给「暂停/停一下」定无歌语义。若按决策 1 的「暂停=会话继续」，则无歌时不应落 `bare_stop`；
  需要 9 哥 在 D-005 类决策里补一句（这是本报告唯一的**决策内部冲突**，不是实现 bug）。
- **C4**：`music_active` 与决策 3 对齐：paused 时也说结束词时，必须「停歌 + 关会话」；不再用 paused
  触发 `MEDIA_STOP` 的「保持会话」。
- **C5**：`rules.MUSIC_TRANSPORT["pause"]` 收窄为 `("暂停","停一下")`；同步删除
  `src/rui/music/tools.py:314` 提示词里的「先停这儿」（只删暂停词表不够）。
- **怎么验**：`uv run python tools/intent_matrix.py` 中 C3/C4/C5 对应行；`rg "先停这儿" src/rui/music/tools.py`
  应无命中；真机序列：暂停 → 说「停止」→ 音乐清零且会话关闭。
- **为什么第二批**：不先定 C3 的语义，改了 C4/C5 也可能在无歌/paused 边界再冲突。

### 批次 3（P2：C6 + C10 + C11）——低风险文案与别名

- **C6**：`catalog.py:543-551` 文案改为与 C2 后的实现一致。
- **C10**：`ALIASES` 补「退一下」→「退下吧」（先由 9 哥 确认：别名是否算新增触发词，踩决策 2 边界）。
- **C11**：`phrases.py:43` 暂停回复改为「好，暂停了。」；`docs/interaction.md:27` 更新为「结束词=音乐清零+会话关闭」。
- **怎么验**：`rg -n "不是子串匹配" src/rui/config/catalog.py`（待改文案）、`rg -n "先停这儿" src/rui/session/phrases.py`（待改回复）应命中；矩阵对应行不变。本卡只审不改，pytest 留到实现卡。

### 批次 4（P2：C7 + C8 + C9）——覆盖缺口（需要 9 哥 决定是否补）

- **C7** 单字「停」、**C8** 无歌 transport、**C9** 大一点/小一点：都不在已定决策里，建议先记录为
  「已知限制」，或由 9 哥 决定补澄清分支；不建议在未拍板前加词（决策 2 明确不新增结束词）。
- **怎么验**：矩阵对应行保持 `model` 即符合「未改」现状；若将来补分支，脚本行会给出 `clarify.*`。

## 6. 三个专项回答

### 6.1 `is_dismiss` 词表里有没有「停止/再见/拜拜」？

**没有。** `DISMISS_WORDS = ("退下吧", "退下", "不聊了", "关机")`（`rules.py:60`），
`is_dismiss` 只做 `any(word in t ...)` 子串判断（`rules.py:67-76`）。因此：

- 「退下吧/退下/不聊了」无论有没有歌都是 `bare_stop`；bare 分支在 owner 房间会先 `service.stop()`
  再 dismiss（`app.py:986-1013`），行为上已等于「音乐清零 + 会话关闭」——这是目标行为的参照实现。
- 「停止/再见/拜拜」不在 dismiss 表，`stop_scope` 只看 `music_playing`（`rules.py:311-315`），
  所以有歌时一律 `media_stop`（保持会话），无歌时才 `bare_stop`。**C1 的根因就在这里。**

### 6.2 `len(t)` 容差 + 子串匹配如何让「停止你好」改变判定？

`stop_intent`（`rules.py:258-286`）先 `normalize`（去标点/空格，`rules.py:238-240`），再按长度降序
遍历结束词；命中条件是（`rules.py:281-283`）：

```python
if t == word:                                  # 整句
    return True
if word in t and len(t) <= len(word) + _STOP_TAIL_CHARS:  # 任意位置子串 + 总长 <= +2 字
    return True
```

`停止你好` → `t="停止你好"`（4 字），`word="停止"`（2 字）：`"停止" in t` 且 `4 <= 2+2` ⇒ **True**。
它不检查多出来的两个字是什么，所以「停止你好」「停止播放」（合法）「停止了吗」（疑问句）都同路。
现场日志第 5 条就是这么来的（有歌 → `media_stop`）。**目标行为：整句或别名精确匹配；「停止你好」不得命中。**

### 6.3 单字「停」是不是无人覆盖（落模型）？

**是。** 归一化后 `t="停"`：不在 `STOP_WORDS`（`rules.py:27-30`，最短的是「停止」「停下」），
不在 `MUSIC_TRANSPORT["pause"]`（`rules.py:84-89`），`volume_level` 要求含音量名词（`rules.py:123-128`），
`context_command` 不匹配，`verbless_device_name` 也不匹配（无设备名词）；`is_music_random` 不匹配 ⇒
顺序走到底，落 `model`（`app.py:2813` 澄清判定 False → `:2825-2831` 的 `runner.run`）。矩阵行 `停 → model` 可复算。
按 9 哥 决策 2（结束词不新增）与决策 1（暂停词只两个），**目标行为仍是 model**；但这是明显会说的句子，
建议在后续决策里明确是否补澄清（见 C7）。

## 7. 口径与局限

1. 脚本镜像 `app.py:2797-2831` 的顺序，但**不启动 FastAPI**、不模拟 `_music_transport` 的具体播放结果
   （只给分支名）；`music_random` 视为服务存在时的确定性分支。
2. 矩阵假设 transcript 已通过唤醒词剥离、回声过滤、`is_noise`；这些层不在本卡范围。
3. `music_active` 按代码取 `("playing","paused")`（`app.py:940`）；`paused` 是否算「在放」是 C4 的决策点。
4. 异屋只影响 `media_stop` → `stop_elsewhere`（`app.py:948-963`）；`bare_stop` 在异屋时只关本屋，
   不改别的房间的音乐（`app.py:986-1013`）——多房间策略不在 9 哥 本次决策内。
5. 结束词默认 21 个（`schema.py:1003-1007`）；用户改配置后矩阵需用同一脚本重算（脚本读 `SessionConfig()` 默认值）。
6. 本卡只审不改：`git diff --name-only -- src` 为空；报告与脚本之外的任何文件都未改。
7. 不含真实内网地址/凭证。

## 8. 复现命令与文件清单

```bash
uv run python tools/intent_matrix.py            # 整张矩阵（TSV + diagnostics）
uv run python tools/intent_matrix.py > /tmp/a
uv run python tools/intent_matrix.py > /tmp/b
diff -q /tmp/a /tmp/b && echo deterministic
```

- 新增：`tools/intent_matrix.py`、`docs/audit/intent-collisions.md`
- 未改：`src/**`、`docs/music.md`、`docs/interaction.md`、任何词表/config。
