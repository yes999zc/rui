# 音乐音源：预设、协议、凭据与测试

> 面向 RUI 0.3.4+；Jellyfin/Emby 家族自 0.4.1 起提供。回答三个问题：**能选哪些音源**、
> **凭据怎么存**、**怎么确认它真的能用**。分层设计（队列、工具、意图）见 [music.md](music.md)；
> 本文只讲「源」这一层。

## 1. 一句话

`music.source` 不再是一个自由文本字符串，而是**预设 id**。预设注册表在
`src/rui/music/sources/presets.py`，运行时有两套适配器：

- `SubsonicSource`（`src/rui/music/sources/subsonic.py`）—— Navidrome/Airsonic/Gonic/Ampache；
- `JellyfinSource`（`src/rui/music/sources/jellyfin.py`）—— Jellyfin 与 Emby（同一套
  MediaBrowser HTTP API，一个家族两个预设）。

内置 7 个预设。同一家族内切换通常只需要换地址（凭据槽位相同）；跨家族切换会换一套
凭据槽位。

## 2. 预设清单

| id | 显示名 | 协议家族 | 默认端口 | 文档 |
|---|---|---|---|---|
| `navidrome` | Navidrome | `subsonic` | 4533 | <https://www.navidrome.org/docs/developers/subsonic-api/> |
| `airsonic` | Airsonic | `subsonic` | 4040 | <https://airsonic.github.io/docs/> |
| `gonic` | Gonic | `subsonic` | 4747 | <https://github.com/sentriz/gonic> |
| `ampache` | Ampache | `subsonic` | 80 | <https://ampache.org/api/> |
| `jellyfin` | Jellyfin | `jellyfin` | 8096 | <https://api.jellyfin.org/> |
| `emby` | Emby | `jellyfin` | 8096 | <https://dev.emby.media/> |
| `custom` | 自定义 Subsonic 兼容服务 | `subsonic` | — | <https://www.subsonic.org/pages/api.jsp> |

`custom` 是给自建/未列出的 Subsonic 实现用的：只要是 Subsonic API（`/rest/*.view`），
填根地址即可。预设只改变显示名、默认端口/路径、文档链接和**鉴权方式**；**不会**为每个
品牌复制一套代码。Emby 与 Jellyfin 共享 `jellyfin` 家族：控制台里 Emby 会显示
`Emby · JELLYFIN`，表示它走 MediaBrowser API，而不是另一套实现。

`GET /api/v1/music/sources` 返回同一张表（`sources[]`），外加当前值、凭据 presence 和
运行中的源。控制台的「音源与凭据」下拉就渲染这张表；凭据输入框也按注册表动态渲染，
不硬编码 user/password。

## 3. 协议

### 3.1 Subsonic 家族（`SubsonicSource`）

- 端点：`ping`（测试/鉴权）、`search3`（点歌）、`getRandomSongs`（随机/电台）、
  `getStarred2` / `star` / `unstar`（收藏）、`stream`（取流）、`getSongsByGenre`、
  `getAlbumList2` / `getAlbum`（全库索引兜底）。
- 鉴权：Subsonic 1.13+ **token+salt**（`u`、`t=md5(secret+salt)`、`s`），密码/token 不进 URL、
  不进日志。旧的明文 `p=` 参数不再使用。
- 曲目 id 统一前缀 `subsonic:<server-id>`；队列/历史/播放 API 不变，切音源不需要迁移数据。
- 取流：整首先下载，本地 ffmpeg 转 16 kHz mono s16 WAV，按 hash 文件名做 PCM LRU 缓存。
- 时长 <45 s 的短片会被过滤；如果过滤后为空则保留原列表（避免「曲库里没找到」的假空）。
- 重试只针对传输错误（2 次），API 返回 `status=failed` 立即报 `SourceError`，不会对挂掉的 NAS 重试风暴。

### 3.2 Jellyfin / Emby 家族（`JellyfinSource`）

Jellyfin 从 Emby 3.5 分叉，两者仍是同一套 MediaBrowser HTTP API；差异只在 API key 怎么传
（由预设的 `auth_style` 声明）。适配器实测覆盖的端点：

| 用途 | 请求 |
|---|---|
| 测试连接 | `GET /System/Info`（需鉴权，用它验证 key 有效） |
| 找用户 | `GET /Users`（取第一个未禁用的用户；收藏是按用户的） |
| 搜索 | `GET /Items?searchTerm=<term>&IncludeItemTypes=Audio&Recursive=true&Limit=N` |
| 随机 | `GET /Items?...&SortBy=Random` |
| 收藏列表 | `GET /Users` → `/{userId}/Items?...&Filters=IsFavorite` |
| 星标/取消 | Jellyfin：`POST\|DELETE /UserFavoriteItems/{id}?userId={userId}`；Emby：`POST\|DELETE /Users` → `/{userId}/FavoriteItems/{id}` |
| 全库分页 | `GET /Items?...&StartIndex=&Limit=`（`IncludeItemTypes=Audio` 或 `MusicAlbum`） |
| 专辑曲目 | `GET /Items?ParentId={albumId}&IncludeItemTypes=Audio&Recursive=true` |
| 取流 | `GET /Audio/{id}/stream?static=true` |

**鉴权（实测）**：

- `jellyfin` 预设：API key **只能**放在请求头
  `Authorization: MediaBrowser Token="<key>"`。同一个 key 以 `?api_key=`、`X-Emby-Token`
  或 `X-Api-Key` 发送都会被 401（在 10.10.x 上逐项验证）。
- `emby` 预设：API key 走 `?api_key=<key>`。
- 收藏/星标需要 userId；Jellyfin 对 API key 访问 `Me` 用户端点返回 400，所以适配器
  统一先 `GET /Users` 取第一个启用用户并缓存。
- key 是只写凭据：不进 URL 日志、不进异常文本、不随任何 GET 返回。

**标签容错**（两个真实服务实测）：

- 艺术家优先 `Artists[0]`，为空回退 `AlbumArtists[0].Name`，再回退 `AlbumArtist`；
  `Artists` 既可能是 `["A"]` 也可能是 `[{"Name": "A"}]`，`AlbumArtists` 是对象数组；
  标量 `Artist` 在两个服务上都是 `null`。
- `Name` 为空时用文件路径的 stem 兜底；没有 `Id` 的条目直接跳过。
- `Genres` 可能为空数组，`Bpm`/`ProductionYear` 可能缺失；时长取 `RunTimeTicks`（100 ns/格）。
- 布局/长度过滤与 Subsonic 相同：<45 s 过滤，过滤后为空则保留原列表。

**曲目 id**：`jellyfin:<server-id>` / `emby:<server-id>`。全库索引
（`library_index.py`）会给行加 `subsonic:` 前缀，`resolve`/`star` 因此按「最后一个冒号
之后」取服务器 id，换源后旧队列、旧索引条目仍能播放，不需要重建缓存。

**取流与缓存**：整首先下载 `/Audio/{id}/stream?static=true`，本地 ffmpeg 转 16 kHz mono
s16 WAV，按 hash 文件名做 PCM LRU 缓存。缓存名里带预设 id：Emby 的 id 是小整数，而
控制台换源时复用同一个 `music_cache`，不带前缀会和 Subsonic 的数字 id 撞缓存、播错歌。

**重试**：只重试传输错误与 5xx（2 次）；4xx 立即 `SourceError`。错误信息只含端点名和
HTTP 状态，不含 URL/查询串（Emby 的查询串里有 key）。

## 4. 配置

```yaml
music:
  enabled: true
  source: navidrome          # 预设 id；未知值保存时 422
  secrets_path: /var/lib/rui/lan_media.json
  cache_dir: /var/lib/rui/music_cache
```

- `music.source` 在配置中心是 **select**，选项来自注册表，不再能输入自由文本。
- 保存 `music.source` / `secrets_path` / `cache_dir` 会**运行期重建**音乐服务，无需重启进程。
- 选择任意受支持预设时，后端自动把 `secrets_path` 指向 `<data_dir>/lan_media.json`、
  `cache_dir` 指向 `<data_dir>/music_cache`（保持老 Navidrome 部署路径不变）。
- 手改 YAML 写入未知 id：配置仍可加载，但面板/`GET /api/v1/music` 明确报告
  `enabled=false` + `source_known=false`，不会出现「面板绿的、服务没起」。
  `/api/v1/music/sources` 会给出未知值和可选列表。

### 从旧配置迁移

旧配置可能把凭据直接写在 `music.url` / `music.user` / `music.password`（或 `music.token`）。
启动时 `migrate_legacy_music_credentials()` 会：

1. 把值写入 `music.secrets_path`（默认 `<data_dir>/lan_media.json`，`0600`，原子写）；
2. 补上 `secrets_path`，避免 `enabled: true` 下次加载时报「没有凭据文件」；
3. 清空内存中的内联字段，并重写一次 `config.yaml`（备份照常保留）。

schema 里的这四个字段标记为 `exclude=True`，因此 `save_config` 永远不会再把明文写回配置。
现有只配置了 `secrets_path` 的部署**完全不受影响**，不需要手动迁移。切到 Jellyfin/Emby
时旧文件里的 `user/password` 会被忽略，填入 `api_key` 保存即可。

## 5. 凭据（只写）

凭据文件是 **flat JSON**，键就是当前预设的凭据槽位：

```json
// Subsonic 家族
{
  "url": "http://<nas-ip>:4533",
  "user": "bakey",
  "password": "……"
}
```

```json
// Jellyfin / Emby
{
  "url": "http://<nas-ip>:8096",
  "api_key": "……"
}
```

- 权限 `0600`，原子替换；**不**写进 `config.yaml`，**不**进日志，**不**由任何 GET 返回。
- Subsonic 槽位：`url`（必填，`http://`/`https://`）、`user`（必填）、`password` 与
  `token`（二选一）。某些反向代理/部署把固定 token 当密码用；填 `token` 时适配器把它
  作为同一套 token+salt 的 secret。
- Jellyfin/Emby 槽位：`url`（必填）、`api_key`（必填，`kind=password`，只写）。
  API key 在服务端后台创建：Jellyfin 在「控制台 → API 密钥」，Emby 在
  「设置 → 高级 → API 密钥」。适配器自己发现 userId，不需要填用户名。
- 控制台只提交**改过的字段**；空字符串表示删除该字段；全部删空则删除凭据文件。
- `has_credentials` / `has_url` / `has_user` / `has_password` 只报存在，永不回显值；
  `GET /api/v1/music/sources` 的 `credentials.fields` 按预设槽位返回布尔值
  （Jellyfin/Emby 是 `url`、`api_key`）。

## 6. 测试连接

`POST /api/v1/music/test`：

```json
{"source": "airsonic", "url": "http://nas:4040", "user": "u", "password": "p"}
```

```json
{"source": "jellyfin", "url": "http://nas:8096", "api_key": "……"}
```

- `source` 缺省 = 当前配置；未知 id → **422**。
- body 里的凭据字段是可选草稿：有则用草稿（不落盘），无则用已保存文件。字段集合来自
  预设注册表，所以 `api_key` 与 `user/password` 走同一条校验。
- 真实调用该家族的鉴权往返：Subsonic 是 `ping.view`，Jellyfin/Emby 是
  `GET /System/Info`（用 key 鉴权）；超时 8 s，返回 `{ok, latency_ms, source, error}`。
- 失败时 `error` 经过脱敏（不出现密码/token/api_key/查询串）；Jellyfin 把 key 放进
  `?api_key=` 会得到一个明确的 401，而不是假装连上。
- 缺必填槽位时 `ok=false` 并明确说缺哪些字段。

## 7. API 一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/music/sources` | 预设表 + 当前源 + 凭据 presence + 运行中源 |
| GET | `/api/v1/music` | 播放状态；新增 `source`/`source_known`/`credentials_ready` |
| PUT | `/api/v1/music/credentials` | 写 `source` 和/或当前预设的凭据槽位（只写） |
| POST | `/api/v1/music/test` | 对草稿或已保存凭据做真实鉴权往返 |
| POST | `/api/v1/music` | 播放控制（完全未变） |
| GET | `/api/v1/music/search` | 只搜不播（完全未变） |

配置中心仍通过 `PUT /api/v1/config/values` 写 `music.*`；两条写路径最终都落到同一个
`Config` 校验和同一个运行时装配函数。

## 8. 安全边界

- 音源 URL 与其他远端配置 URL 一样经过 `security.outbound` SSRF 防护；内网默认允许，
  回环默认拒绝。自建在 `127.0.0.1` 的服务需要显式 `security.outbound.allow_loopback: true`。
- Subsonic 凭据只在请求参数里以 `t`/`s`（md5 token/salt）出现；`u` 是用户名，
  密码/token 本身不出现。
- Jellyfin 的 API key 只在 `Authorization` 请求头里；Emby 的 key 在 `?api_key=` 查询串里，
  但适配器抛出的错误只带端点名和状态码，日志不打印 URL；端点层的
  `_redact_error` 也会把 `api_key=` 一类参数替换成 `***`。
- 错误信息统一走 `_redact_error`；测试端点、状态端点、面板、catalog 均不含值。

## 9. 测试

两套适配器都有离线测试：

- `tests/test_music_sources.py` 启动一个真实的线程 HTTP fake（`127.0.0.1` 随机端口），
  服务端**自己验证 token+salt**，覆盖 Navidrome/Airsonic 的
  `ping` / `search3` / `getRandomSongs` / `getStarred2` / `star`、错误密码、未知 source 422、
  凭据只写/0600、旧配置迁移、测试端点成功/失败/脱敏、运行期切换源。
- `tests/test_music_jellyfin.py` 用 `httpx.MockTransport` 模拟两个服务端，并分别**验证**
  header 与 query 两种鉴权：两种预设的 `ping` / 搜索 / 随机 / 收藏 / 星标、空搜索词回落随机、
  `songs_page` 分页与缺标签兜底、`albums_page`/`album_songs`、`LibraryIndex` 建库与
  `resolve` 下载/缓存（transcode 打桩）、缓存名按预设隔离、401 不泄露 key、文件模式读取
  `api_key`。真实 ffmpeg 的 16 kHz mono 转码测试在 `ffmpeg` 不可用时自动 skip。

```bash
pytest tests/test_music_jellyfin.py tests/test_music_sources.py \
       tests/test_music_credentials.py tests/test_music_source.py \
       tests/test_config_catalog.py -q
```

## 10. 加一个音源

**同家族、新品牌**（例如另一个 Subsonic 兼容服务器）：

1. 在 `src/rui/music/sources/presets.py` 的 `PRESETS` 里加一条 `MusicSourcePreset`
   （id、双语名、`protocol_family`、文档、默认端口/路径、凭据槽位）。
2. `preset_ids()` 自动进入 `music.source` select、`GET /music/sources` 和保存校验。
3. 补一条 fake-server 参数化测试。

**新协议家族**：加适配器类（实现 `MusicSource`，可选 `songs_page`/`albums_page`/
`album_songs` 供全库索引），把家族加进 `SUPPORTED_FAMILIES`，并在 `build_source()` 里
分支装配。Jellyfin/Emby 就是这样接进来的：一个 `JellyfinSource`、两个预设、一个
`auth_style` 字段。

## 11. 已知边界

- 已实现 Subsonic 与 Jellyfin/Emby 家族；Plex 等仍需要各自的适配器，注册表里没有对应
  预设，不会在 UI 里假装可用。
- Jellyfin/Emby 的收藏使用 `/Users` 里**第一个启用用户**（多用户家庭里不是「当前登录
  用户」；rui 没有用户名槽位）。收藏列表一次最多取 500 首。
- 全库索引分页每页 500；Jellyfin/Emby 的专辑信息可能不带 `Genres`，此时索引行回退到
  歌曲自身标签。
- 切源不会迁移播放历史/缓存；旧缓存留在旧 `cache_dir`，历史仍在 `data_dir`。
- `music.cache_tracks` 之外的调优字段（队列阈值等）仍是重启生效，与 Playback 无关。
