# D1 音乐：多源接口设计（首源 NAS Navidrome/Subsonic）

2026-10-22 9哥 定：D1 先于 C1；新项目要支持**后台多个音乐源**（接口做好），本次先走 NAS 的
sonic 服务（Navidrome，与老项目同款）。本文是两份老项目调研（源与数据层、播放与控制层，
2026-10-22 子 agent 完成）蒸馏出的设计；所有「老项目 xxx」都带 文件:行号，可直接对照源码。

## 1. 范围

**做**：源接口抽象 + Navidrome 首源；点歌（歌名/歌手/描述/随机/收藏/口味）；电台模式（边界补货）；
暂停/继续/上下首/停止；与对话互让（今晚 `2bfd356`+`3be4825` 已实测的语义）；LED 音乐态；
音量对账；V1–V5 厂家让位收尾；B2 遗留（gate-over-music、远处唤醒正式计数）。

**不做（留接口位）**：在线兜底源（老项目 netease+kuwo，见 §6）；NAS 入库 scp；封面；硬件键。

## 2. 分层与接口

```
tools/music.py        意图 → 命令对象（不碰设备）
music/query.py        QueryPlan 管道：clean_query → extract_title → 别名 → 校验
music/sources/        MusicSource 协议 + NavidromeSource（本次）+ 将来的 OnlineSource
audio plane           MusicQueue：owner=music 通道，边界补货/跳曲/清场/持久历史
```

### 2.1 Track 与 MusicSource（源接口，扩展点）

```python
@dataclass
class Track:            # = 老项目 _brief 形状（tools/music.py:156-159），跨源通用
    id: str             # 前缀寻址："subsonic:<id>" / 将来 "online:<sid>"、"kuwo:<rid>"
    title: str
    artist: str
    duration: int       # 秒

@dataclass
class ResolvedTrack:
    track: Track
    pcm_path: Path      # 16 kHz mono s16，先下后播（老项目方案 A，plan-online-music.md）

class MusicSource(Protocol):
    name: str
    async def search(self, plan: QueryPlan, limit: int) -> list[Track]: ...
    async def random(self, limit: int) -> list[Track]: ...
    async def resolve(self, track: Track) -> ResolvedTrack: ...
```

id 前缀寻址原样继承（老项目 `online:`/`kuwo:`，online_music.py:172,208）：队列里混合多源曲目，
resolve 按 id 前缀路由回所属源。

### 2.2 NavidromeSource（本次实现）

- **端点**（老项目实测过的集合）：`search3`（songCount=25 点歌 / 500×N 分页全库索引）、
  `getRandomSongs`（随机/电台）、`getStarred2`/`star`/`unstar`（收藏）、`getSongsByGenre`。
- **认证升级**：老项目明文 `u&p` 进 URL（music.py:149）→ 换 **token+salt**（Subsonic 1.13+ 标准），
  账密不进 URL 与日志。
- **客户端**：老项目每请求重读 secrets、urllib 无重试（music.py:143-153）→ httpx 复用连接 +
  有限重试；同步调用经 `asyncio.to_thread`（老项目同款，assistant_v3.py:1593）。
- **取流**：沿用「整首先下后播 + 本地 ffmpeg 转 16k mono s16」（music.py:450-469，30s 下载/
  180s 转码超时）；**新增 PCM LRU 缓存**（老项目 NAS 源转完即删、重播要重新下载几十 MB flac，
  是硬伤；在线源反有 72h 缓存先例）。
- **凭据**：`~/.hermes/.secrets/lan_media.json` 的 `url/user/password`（只写名字不写值，沿用）。

### 2.3 意图管道（拆掉老项目 music_play 的 200 行 if 链）

`Intent → QueryPlan → [source.search(plan)] → 合并去重 → Queue`。源只是管道末端的可插拔角色，
第二源接入时不再像老项目那样在 5 处手写 fallback 判断（music.py:541,588,624,678,730）。

从老项目原样保留的金子组件（都有实测教训背书）：
- `clean_query`/`extract_title` 中文口语剥壳（music.py:267-313）；
- `_accepts` 模糊命中校验——「搜『留在我身边』返回谢霆锋《Without Me》」的防线（music.py:330-343）；
- 别名表（ASR 错听名，如「高强」→「高旗」，taste.py:86-92）；
- 时长 ≥45s 过滤 + 全被剔则不过滤兜底（music.py:395-396）；
- 「先本地别名后上网」原则（music.py:711-727）——将来接在线源时生效。

### 2.4 播放层（plane 侧 MusicQueue，复用今晚已实测的机制）

owner 仲裁（cue/dialogue/reminder/music）、Credit 节奏、`PLAY_LAG 0.55s` 暂停回退、
「歌等完整个 turn、ReplyFinished 后续、4s 无答兜底」全部已存在并实测，直接挂队列进 `_stream_file`
通道。新增：

- **边界补货**（老项目 `_top_up` 四规则，assistant_v3.py:1617-1637，test_radio.py 钉死）：
  队列未播 ≤2 首补 20；只在曲目边界取数（~1s 网络延迟不被听成空隙）；失败只打日志当前歌照播；
  去重 = 播放历史 ∪ 在队曲目。
- **静默跳曲**：单曲取流/转码失败 `idx+=1` 不说话（assistant_v3.py:1645-1648）。
- **队列清场**：播完必须清空 queue/idx/pcm/pos——老项目「播放键播一首就死」的实测教训
  （assistant_v3.py:1694-1703，test_music_sink.py 钉死）。
- **持久播放历史**：合并老项目两套 recent（`MUSIC.recent` 内存 60 条去重 +
  `taste.recent` 磁盘 40 条统计，互不相干是坑）→ 单一持久化文件（data_dir），同时服务
  电台去重与口味统计（`record_play` 按「起播次数」计数，taste.py:47-59）。
- **暂停三分法 → 恢复责任归属**（今晚已映射）：
  - 说话压低（唤醒/提醒/告别）= dialogue 抢占，答完自动续（`2bfd356`+`3be4825`）；
  - 唤醒打断（barge）= 同上，答完自动续（老项目 3097-3107 最终行为一致）；
  - **显式暂停**（「暂停」）= music 自持 paused，任何自动续不得碰（老项目 3160-3167）；
  - 继续播放（「继续/接着放」）= 显式恢复；切歌破掉显式暂停的粘性（老项目 880-884 实测教训）。
  - 续歌前 sleep 0.25s 等喇叭收尾（老项目 3095）；**farewell 在「即将续歌」时静音**（2842-2844）。
- **vendor/音量**：起歌前 `vendor_stop_player`（守卫式压制，test_vendor_stop.py 规则）+
  `ensure_device_volume` 对账（老项目 connect/music start/answer 三个调用点，2281-2306）。

### 2.5 工具层：字符串标记 → 命令对象

老项目工具返回 `@@PLAY/@@NEXT/...` 字符串、`lat` 字典隐式传状态（有 `lat["resume_music"]`
这类无消费者的遗留，assistant_v3.py:896）→ 换成类型化命令对象，session 层在 turn 固定阶段执行。
保留的行为语义：

- **`@@PLAY` 起播后跳过答句**——「歌就是答案，说话是噪音」（assistant_v3.py:3523-3526）；
- **继续播放不在工具里直接起播**，交给答句播完钩子——否则答句与歌重叠（889-897）；
- **音乐类请求跳过 filler**（`_FILLER_SKIP`，3590-3610：工具 ~1s 就返回，过渡语压过歌头几拍）；
- 音乐播放中打断问话：只答那件事、≤20 字（MUSIC_HINT，916-922）。

工具清单首版：`music_play`（含随机/描述/歌名点播）、`music_pause`、`music_resume`、`music_next`、
`music_prev`、`music_stop`、`music_favorite`(add/remove/list)、`music_play_favorites`、
`music_taste`；`music_volume` 并入既有音量路径。

## 3. 相对老项目的明确改动（为什么）

| 老项目做法 | 问题 | 新做法 |
|---|---|---|
| `@@` 字符串标记 + lat 字典 | 隐式状态、有遗留死键 | 类型化命令对象 |
| 明文 u&p 进每个 URL（含 stream） | 日志/缓存泄漏面 | token+salt |
| 每请求重读 secrets、无连接复用、无重试 | NAS 抖动即失败 | httpx + 重试 |
| NAS 曲目 PCM 转完即删 | 重播重复全量下载+转码 | PCM LRU 缓存 |
| 整首歌 bytes 进内存（self.pcm） | 4 分钟歌 ~7.7MB | 分块读文件（plane 本就按 chunk 推） |
| recent 两套（内存去重/磁盘统计） | 重启归零、语义重复 | 单一持久化历史 |
| music_play 200 行 if 链 | 源选择与意图解析耦合 | QueryPlan 管道，源可插拔 |
| 在线兜底散在 5 处 env 判断 | 第二源接入成本高 | 管道末端统一 fallback 位 |

## 4. 实施顺序（子 PR，每步测试先行）

1. `Track/MusicSource/ResolvedTrack` 协议 + `NavidromeSource`（token+salt、httpx、缓存）+ 单测
   （fake HTTP；端点/认证/转码/缓存/失败路径）。
2. plane `MusicQueue`：边界补货、静默跳曲、清场、持久历史 + 单测（移植 test_radio/
   test_music_sink 的断言）。
3. 意图管道 + 工具命令对象 + filler 跳过 + LED 音乐态（music 紫/music_paused 琥珀，B1 补两个效果）。
4. vendor 压制 + 音量对账 + farewell 静音续歌 + V1–V5 收尾。
5. 真机验收：点歌/暂停/继续/上下首/停止/收藏/口味；D3 的 20 次唤醒 ≥19；
   B2 遗留（gate-over-music、3-4 米唤醒正式计数）一并出数。

## 5. 老项目调研结论索引

- 源与数据层报告 + 播放与控制层报告全文见 2026-10-22 会话记录（两份子 agent 报告）；
  本文档引用的所有 文件:行号 均指 `~/AI_Workspace/01_Code/projects/xiaoai-voice-entry/server/`（只读参考）。
- 老项目音乐相关测试清单（迁移验收基线）：test_radio / test_rewind / test_music_sink /
  test_credit / test_vendor_handoff / test_vendor_stop / test_music_random / test_music_mood /
  test_music_search / test_songname / test_online_play / test_stop_intent / test_keys / test_filler。
