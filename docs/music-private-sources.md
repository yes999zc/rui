# 私有音乐源接入（RUI_MUSIC_SOURCES）

本文写的是 **rui 0.8.6 的“公版钩子”**：怎么把一个不在仓库里的私有 `MusicSource`
接进来。仓库只提供接口、加载器、示例骨架和测试；**任何真实第三方音源实现都不进
git**（见 §8）。

明确不做的事：不含 QQ 音乐 / 网易云 / VIP 换源 / 聚合解析 / 破解接口。这里没有
任何第三方平台适配器，也不接受把这类实现作为 PR 提交。rui 只负责“按运维声明的
allowlist 加载一个实现了五方法的对象”。

---

## 1. 加载方式：环境变量 allowlist

```bash
# 单个源
RUI_MUSIC_SOURCES="stub=examples.music_sources.stub_source:make"

# 多个源（逗号分隔，逗号两侧空白可有可无）
RUI_MUSIC_SOURCES="stub=examples.music_sources.stub_source:make, \
                   private=my_private_source:factory"
```

- 语法：`<id>=<module>:<factory>`，`factory` 支持点分属性（如 `build.make`）。
- `<id>` 会转小写匹配（和内置 preset 一致），可用字符：字母、数字、`_`、`.`、`-`。
- 未设置或空字符串 = 没有外部源，行为与 0.8.6 完全一致。
- 非法条目（缺 `=`、缺 `:`、模块/工厂名非法、id 重复）会报错，**错误消息里带原始
  条目文本**，例如：
  `invalid entry in RUI_MUSIC_SOURCES: 'oops' ; expected <id>=<module>:<factory>`。
- 也兼容旧前缀 `HOUSE_EAR_MUSIC_SOURCES`（`rui.env` 的统一迁移规则），新部署只用
  `RUI_`。
- **内置 preset 优先**：如果外部 id 与 `navidrome`/`jellyfin` 等重名，内置实现胜出，
  外部条目不会覆盖它。

### 为什么是环境变量，不扫描 `<data_dir>/music_sources/*.py`

刻意选择显式 allowlist：

1. 数据卷是备份/恢复/运维最常碰的地方，“放个文件进去就执行”等于把代码执行权限
   绑在文件系统权限上；
2. 和既有的 `RUI_ADAPTER_PLUGINS` / `RUI_PLUGINS_DIR` 运维习惯一致；
3. 容器里用 env 最直接，`docker compose` 改一行即可，也便于镜像审计。

代价是新增/修改外部源要重启进程（模块已经 import 后不会重新加载）。

---

## 2. 五方法接口

外部对象必须实现 `rui.music.source.MusicSource` 的五个方法（`name` 属性建议一并
提供；`source_id` 属性用于状态面板显示当前运行的源）：

```python
class MySource:
    name = "my-source"

    async def search(self, term: str, limit: int) -> list[Track]: ...
    async def random(self, limit: int) -> list[Track]: ...
    async def favorites(self) -> list[Track]: ...
    async def star(self, track_id: str, on: bool) -> bool: ...
    async def resolve(self, track: Track) -> Path: ...
```

- `search(term, limit)`：自由文本搜索（歌名/歌手/两者）；空 `term` 语义是“随便来
  一点”（服务端随机）。
- `random(limit)`：电台模式的一批可播曲目。
- `favorites()`：当前用户的收藏。
- `star(track_id, on)`：收藏/取消；服务端拒绝时返回 `False`。
- `resolve(track)`：把一首歌变成磁盘上的 **16 kHz mono s16 PCM** 文件并返回路径。
  有缓存就复用；`rui` 会把返回路径交给播放器。
- `Track`（`rui.music.model.Track`）字段：`id / title / artist / duration / genre /
  bpm`；`id` 应当是全局唯一字符串（内置实现用 `source_id:server_id` 前缀）。
- **`duration` 的单位是秒**。如果私有服务返回 `duration_ms`（毫秒，常见），构造
  `Track` 时要 `/ 1000`，否则一首 3 分钟的歌会被当成 50 小时，进度/续播全错。
- 服务不可用/曲库没找到时抛 `rui.music.source.SourceError`；上层会把它翻译成
  「曲库里没找到 / 连不上」这类话，而不会让一轮对话崩掉。

缺少方法或方法不可调用时，加载器会点名到方法，例如：

```
music source 'private' from my_private_source:factory is missing MusicSource method: star
music source 'private' from my_private_source:factory: MusicSource method 'resolve' is not callable
```

绝不会退化成 `AttributeError` / `TypeError`。

加载时还会校验工厂返回值不是 `None`，并逐个检查上述五个属性存在且可调用。

---

## 3. 工厂与只读 `SourceContext`

`<module>:<factory>` 指向的工厂接收**一个**只读 dataclass
`rui.music.sources.external.SourceContext`：

| 字段 | 含义 |
| --- | --- |
| `source_id` | 环境变量里注册的 id（小写归一后的值） |
| `secrets_path` | `music.secrets_path` 解析后的路径；外部源**自己**读、自己解析格式 |
| `cache_dir` | `music.cache_dir`，放 PCM 缓存用 |
| `cache_tracks` | `music.cache_tracks`（缓存曲目上限） |
| `policy` | `security.outbound` 对应的 `OutboundPolicy`，出网前请传给 `rui.security.netguard` 的 guarded client |
| `base_url` / `user` / `password` / `token` / `api_key` | 控制台“测试连接”的未保存草稿值；正常文件模式下为空 |

工厂只应做**轻量构造**：读配置、建 client、校验自己的凭据格式；不要在工厂里做
网络请求或长时间阻塞（启动路径会调用它，控制台渲染不会）。

`SourceContext` 是 frozen dataclass；需要额外配置请读 `secrets_path` 或自己的环境
变量，不要修改它。

---

## 4. 版本契约 `music_sources_compat`

模块级**必须**声明：

```python
music_sources_compat = ">=0.8.6 <0.9.0"
```

- 语法与插件 manifest 的 `rui_compat` 相同（`rui.plugins.semver`）：空格或逗号分隔
  的比较式，支持 `>= <= > < == !=` 和 `*`，不支持 `^` / `~` / `||`。
- 缺失 → 报错点名 `music_sources_compat`；无法解析 → 报错带声明原文；不匹配 →
  报错同时给出**声明范围**与**当前 rui 版本**，例如：
  `music source 'private' (module 'my_private_source') is incompatible: it declares
  music_sources_compat = '>=0.9.0', but this rui build is 0.8.6`
- **校验顺序：先 `import`，再校验 compat，最后才调用 factory。** 也就是说 compat
  不匹配时，模块**顶层代码（import 期副作用）已经执行**，只是 factory 不会被调用、
  也不会产生实例。因此 `RUI_MUSIC_SOURCES` 里只应注册你完全信任的模块（它和
  `RUI_ADAPTER_PLUGINS` 一样等于进程内代码执行权限）；不要把“不兼容会被拦下”理解
  成“不兼容的代码不会运行”。
- 这个字段是给未来接口变更用的：0.9 若改五方法语义，老的外部源会明确拒绝加载，
  而不是运行到一半出错。

---

## 5. 凭据放在哪

- 外部源仍然使用 **`music.secrets_path`**（默认 `<data_dir>/lan_media.json`）这个
  路径，但**文件格式由外部源自己决定**；rui 不校验、不回显、不在日志里打印。
- **rui 仍会对同一路径做一次容错 JSON 读取**（`music_credentials()`）：文件不存在、
  不是 JSON 或读不出来时当空对象处理，不会因此报错。这不会妨碍外部源按自己的格式
  解析；但如果你把二进制/非 JSON 内容放在这里，rui 的凭据面板只会显示“没有值”。
- 推荐该文件 `chmod 600`，只放在数据卷/宿主受控目录，不要进 git、不要进镜像。
- 控制台的“音源与凭据”页只处理**内置 preset** 的字段；外部源的凭据请用你自己的
  脚本/运维流程写入 `secrets_path`。`PUT /api/v1/music/credentials` 对外部 id 只接受
  `{"source": "<id>"}` 做切换，附带其他字段会 422；响应的 `has_credentials` 是
  `true`（外部源自管），`has_url`/`has_user`/`has_password` 会是 `false`。
- **切回内置 preset 时要注意**：内置源的“音源与凭据”保存会写/删**同一个**
  `music.secrets_path`。如果外部源在该文件里存了不可恢复的内容，切换前先备份，或
  在切换前把外部源的 `music.secrets_path` 指到别处。
- `GET /api/v1/music` 与 `/api/v1/music/sources` 对外部源的
  `credentials_ready` 为 `true`、`credentials_missing` 为空：rui 没有可检查的槽位，
  不会误报“缺 url/user/password”。
- **内部 token 走 secrets，不进配置**：私有服务若要求 `X-Internal-Token` 之类的
  内部凭据，用 `rui.secrets.get("MUSIC_SERVICE_TOKEN")` 读取（环境变量优先，其次
  `<data_dir>/secrets.yaml`，与控制台“只写”凭据页同一套存储）。不要写进
  `config.yaml` 明文，也不要提交进 git；缺失时由外部源自己决定是拒绝还是匿名调用。

---

## 6. 启用与运行期行为

```yaml
music:
  enabled: true
  source: stub            # 环境变量注册的外部 id
  secrets_path: /data/lan_media.json
  cache_dir: /data/music_cache
```

或在控制台 `PUT /api/v1/config/values`：

```json
{"values": {"music.source": "stub"}}
```

- 配置中心的 `music.source` select 会自动包含**当前进程 env 里注册的外部 id**（内置
  preset 在前）。保存时自动把 `secrets_path` 补成 `<data_dir>/lan_media.json`、
  `cache_dir` 补成 `<data_dir>/music_cache`；未注册的 id 仍然 422。
- `music.enabled=true` 且 id 已注册时：服务会 import 模块、校验 compat、调用工厂，
  注册音乐工具；面板不再报“不受支持”；`/api/v1/music/sources` 和 `/api/v1/music` 的
  `source_known` 为 `true`（`current_preset` 为 `null`，因为外部源不在 preset 表里）。
- `/api/v1/music/test` 对外部 id 返回 **200 + `ok: false`**，`error` 明确写
  「外部音源不支持控制台测试连接」：MusicSource 协议里没有 `ping`，rui 不会假装探测
  成功。未知 id 仍是 422。

---

## 7. 失败与降级

加载失败一律是 `ValueError`，消息包含 source id 和原始异常类型/消息
（`raise ... from exc`）：

| 场景 | 消息要点 |
| --- | --- |
| import 失败 | `cannot import module '<module>': ModuleNotFoundError: ...` |
| compat 缺失 | `does not declare music_sources_compat; add ...` |
| compat 不匹配 | 声明范围 + `but this rui build is 0.8.6` |
| 工厂不存在/不可调用 | `has no attribute '<factory>' (the factory)` / `is not callable` |
| 工厂抛异常 | `factory '<name>' from module '<module>' raised <Type>: <msg>` |
| 返回值不是对象/缺方法 | `is missing MusicSource method: <name>` |

运行期（`create_app` 启动、保存配置触发的重建）**不会因为外部源失败而崩**：

- `_new_music_source()` 捕获 `ValueError`，打**一条** `log.warning`
  （`music: source '...' cannot be assembled; leaving music off: ...`），返回 `None`；
- 音乐层降级为 off，其他功能（对话、设备、提醒）照常；修好 env/代码后重启即可；
- `PUT /api/v1/config/values` 在这种情况下不会 500：音乐相关字段会出现在
  `restart_fields` 里，提示需要重启；
- 运行中服务不可达（外部源内部抛 `SourceError`）由音乐工具按既有语义回答
  「曲库里没找到 / 连不上」，不影响进程。

环境变量本身写错（缺 `=` 等）时，`/api/v1/panels`、`/api/v1/config/catalog` 这类
只读渲染路径会忽略坏条目并只打一条 warning；显式 `build_source(<id>)` 会抛出带原始
条目的 `ValueError`。

日志里不会出现 `SourceContext` 里的凭据草稿或 `secrets_path` 文件内容。外部源自己
也不要把 secret 写进日志。

---

## 8. 仓库边界

- 本仓库只放：`MusicSource` 协议、外部加载器、示例骨架
  `examples/music_sources/stub_source.py`、测试与本文档。
- **真实实现永不进 git**：私有源应放在自己的私有仓库/镜像层/部署目录（通过
  `PYTHONPATH` 或安装成包），只把 `<module>:<factory>` 写进运维的 env。
- 不要提交任何真实站点的地址、账号、cookie、token、签名算法或抓取代码。示例文件里
  也不能出现这些内容。
- 不加依赖：外部加载器只用标准库 + 仓库已有模块（`importlib`、`rui.plugins.semver`）。
- 不改 `subsonic`/`jellyfin` 行为，不改前端，不改 `rui_compat` 契约。

---

## 9. 最小自检

```bash
# 1) 用仓库自带 stub 走一次加载（在仓库根目录）
PYTHONPATH=src:. RUI_MUSIC_SOURCES="stub=examples.music_sources.stub_source:make" \
.venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from rui.music import build_source

source = build_source("stub", None, Path("/tmp/rui-stub-cache"), 1)
print(type(source).__name__, source.source_id)
print(asyncio.run(source.search("x", 5)), asyncio.run(source.favorites()))
PY

# 2) 看不到外部源时行为不变
env -u RUI_MUSIC_SOURCES .venv/bin/python -c \
  "from rui.music import build_source; build_source('nope', None, __import__('pathlib').Path('/tmp'))"
# -> ValueError: unknown music source: 'nope'
```

跑测试：

```bash
.venv/bin/python -m pytest tests/test_music_external.py -q
```
