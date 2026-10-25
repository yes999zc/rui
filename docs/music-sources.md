# 音乐音源：预设、协议、凭据与测试

> 面向 RUI 0.3.4+。回答三个问题：**能选哪些音源**、**凭据怎么存**、**怎么确认它真的能用**。
> 分层设计（队列、工具、意图）见 [music.md](music.md)；本文只讲「源」这一层。

## 1. 一句话

`music.source` 不再是一个自由文本字符串，而是**预设 id**。预设注册表在
`src/rui/music/sources/presets.py`，运行时只有一套 Subsonic 适配器
（`src/rui/music/sources/subsonic.py`）。内置 5 个预设，全部走同一套 Subsonic API，
所以切换音源通常只需要换地址（凭据槽位相同）。

## 2. 预设清单

| id | 显示名 | 协议家族 | 默认端口 | 文档 |
|---|---|---|---|---|
| `navidrome` | Navidrome | `subsonic` | 4533 | <https://www.navidrome.org/docs/developers/subsonic-api/> |
| `airsonic` | Airsonic | `subsonic` | 4040 | <https://airsonic.github.io/docs/> |
| `gonic` | Gonic | `subsonic` | 4747 | <https://github.com/sentriz/gonic> |
| `ampache` | Ampache | `subsonic` | 80 | <https://ampache.org/api/> |
| `custom` | 自定义 Subsonic 兼容服务 | `subsonic` | — | <https://www.subsonic.org/pages/api.jsp> |

`custom` 是给自建/未列出的实现用的：只要是 Subsonic API（`/rest/*.view`），填根地址即可。
预设只改变显示名、默认端口/路径和文档链接；**不会**为每个品牌复制一套代码。

`GET /api/v1/music/sources` 返回同一张表（`sources[]`），外加当前值、凭据 presence 和运行中的源。
控制台的「音源与凭据」下拉就渲染这张表。

## 3. 协议

所有预设共用 `SubsonicSource`：

- 端点：`ping`（测试/鉴权）、`search3`（点歌）、`getRandomSongs`（随机/电台）、
  `getStarred2` / `star` / `unstar`（收藏）、`stream`（取流）、`getSongsByGenre`、
  `getAlbumList2` / `getAlbum`（全库索引兜底）。
- 鉴权：Subsonic 1.13+ **token+salt**（`u`、`t=md5(secret+salt)`、`s`），密码/token 不进 URL、
  不进日志。旧的明文 `p=` 参数不再使用。
- 曲目 id 统一前缀 `subsonic:<server-id>`；队列/历史/播放 API 不变，切音源不需要迁移数据。
- 取流：整首先下载，本地 ffmpeg 转 16 kHz mono s16 WAV，按 hash 文件名做 PCM LRU 缓存。
- 时长 <45 s 的短片会被过滤；如果过滤后为空则保留原列表（避免「曲库里没找到」的假空）。
- 重试只针对传输错误（2 次），API 返回 `status=failed` 立即报 `SourceError`，不会对挂掉的 NAS 重试风暴。

新增一个**协议家族**（如 Jellyfin/Emby）才需要新适配器；新增同家族品牌只需在注册表加一条预设。

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
现有只配置了 `secrets_path` 的部署**完全不受影响**，不需要手动迁移。

## 5. 凭据（只写）

凭据文件是 **flat JSON**，键就是当前预设的凭据槽位：

```json
{
  "url": "http://<nas-ip>:4533",
  "user": "bakey",
  "password": "……"
}
```

- 权限 `0600`，原子替换；**不**写进 `config.yaml`，**不**进日志，**不**由任何 GET 返回。
- 槽位：`url`（必填，`http://`/`https://`）、`user`（必填）、`password` 与 `token`（二选一）。
  某些反向代理/部署把固定 token 当密码用；填 `token` 时适配器把它作为同一套 token+salt 的 secret。
- 控制台只提交**改过的字段**；空字符串表示删除该字段；全部删空则删除凭据文件。
- `has_credentials` / `has_url` / `has_user` / `has_password` 只报存在，永不回显值。
  `GET /api/v1/music/sources` 的 `credentials.fields` 同样只有布尔值。

### 为什么不做统一 secrets？

审计明确把「音乐凭据文件改成统一 secrets」列为大爆炸项。现有 `lan_media.json` 已被老部署使用，
保持路径/格式兼容，风险最低。凭据槽位由注册表描述，未来换存储只需改读写层，不改音源协议。

## 6. 测试连接

`POST /api/v1/music/test`：

```json
{"source": "airsonic", "url": "http://nas:4040", "user": "u", "password": "p"}
```

- `source` 缺省 = 当前配置；未知 id → **422**。
- body 里的凭据字段是可选草稿：有则用草稿（不落盘），无则用已保存文件。
- 真实调用 `ping.view`（同一套 token+salt 鉴权），返回 `{ok, latency_ms, source, error}`；
  失败时 `error` 经过脱敏（不出现密码/token/查询串）。
- 缺必填槽位时 `ok=false` 并明确说缺哪些字段，不会假装连上。

## 7. API 一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/music/sources` | 预设表 + 当前源 + 凭据 presence + 运行中源 |
| GET | `/api/v1/music` | 播放状态；新增 `source`/`source_known`/`credentials_ready` |
| PUT | `/api/v1/music/credentials` | 写 `source` 和/或当前预设的凭据槽位（只写） |
| POST | `/api/v1/music/test` | 对草稿或已保存凭据做真实 `ping` |
| POST | `/api/v1/music` | 播放控制（完全未变） |
| GET | `/api/v1/music/search` | 只搜不播（完全未变） |

配置中心仍通过 `PUT /api/v1/config/values` 写 `music.*`；两条写路径最终都落到同一个
`Config` 校验和同一个运行时装配函数。

## 8. 安全边界

- 音源 URL 与其他远端配置 URL 一样经过 `security.outbound` SSRF 防护；内网默认允许，
  回环默认拒绝。自建在 `127.0.0.1` 的服务需要显式 `security.outbound.allow_loopback: true`。
- 凭据只在请求参数里以 `t`/`s`（md5 token/salt）出现；`u` 是用户名。密码/token 本身不出现。
- 错误信息统一走 `_redact_error`；测试端点、状态端点、面板、catalog 均不含值。

## 9. 测试

`tests/test_music_sources.py` 启动一个真实的线程 HTTP fake（`127.0.0.1` 随机端口），
服务端**自己验证 token+salt**，覆盖：

- Navidrome + Airsonic 预设的 `ping` / `search3` / `getRandomSongs` / `getStarred2` / `star`；
- 错误密码、未知 source 422、面板不假启用；
- 凭据只写、token 替代密码、0600；
- 旧 `music.url/user/password` 迁移 + 配置重写后无明文；
- 测试端点成功/失败/脱敏；
- 运行期从 Navidrome 切到 Airsonic 后 `running_source` 真实变化。

```bash
pytest tests/test_music_sources.py tests/test_music_credentials.py \
       tests/test_music_source.py tests/test_config_catalog.py -q
```

## 10. 加一个新预设（同家族）

1. 在 `src/rui/music/sources/presets.py` 的 `PRESETS` 里加一条 `MusicSourcePreset`
   （id、双语名、`protocol_family="subsonic"`、文档、默认端口/路径、凭据槽位）。
2. `preset_ids()` 自动进入 `music.source` select、`GET /music/sources` 和保存校验。
3. 补一条 fake-server 参数化测试（Navidrome/Airsonic 已覆盖同一适配器）。

## 11. 已知边界

- 目前只实现 Subsonic 家族；Jellyfin/Emby/Plex 需要各自适配器，注册表可以预留但不会
  在 UI 里假装可用（`available` 字段为 false 的预设不提供运行时）。
- 切源不会迁移播放历史/缓存；旧缓存留在旧 `cache_dir`，历史仍在 `data_dir`。
- `music.cache_tracks` 之外的调优字段（队列阈值等）仍是重启生效，与 Playback 无关。
