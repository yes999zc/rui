# Changelog

All notable changes to **rui** are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Curated GitHub Release notes live in [`docs/releases/`](docs/releases/); this file is the
canonical per-version record.

> **Backfill note (2026-10-25).** Versions 0.1.0–0.4.0 were reconstructed from the git
> tags and commit history plus `docs/HANDOFF.md`, the archived development log
> (`docs/archive/HANDOFF-history-to-2026-10-24.md`), `docs/ROADMAP.md`,
> `docs/acceptance.md` and `docs/remaining-work.md`. Dates are tag dates in
> Asia/Shanghai (+08:00). Only shipped, verifiable behaviour is listed; anything that
> could not be confirmed from those sources is marked *(unverified)*.

## [Unreleased]

_Nothing yet._

## [0.8.13] - 2026-10-30

**Theme: 听见的和说出来的对齐。** 控制台「试一试」不再冒充房间说过话（也不再把正在进行的对话抹掉）、
数量词终于会被解析（中文与阿拉伯数字都算）、空唤醒第一次有可统计计数、`tools.ha.call_timeout_s`
上限收回到与工具超时一致的 20 秒。0.8.12 之后第一次把 CI/发布加固与运行时改动一起发版。

### Fixed

1. **控制台「试一试」不算「说过话」，且对房间会话零副作用**（`session/turn.py`、`web/api/app.py`）：
   `run(..., counts_as_speech=False)` 只影响「是否置 `_had_speech`」；`_run_debug_say` 改成
   快照 → 执行 → `finally` 恢复（覆盖 409、提前返回、异常）。现场：0.8.12 修好空唤醒道别之后，
   从电脑点试一试，15 秒后音箱仍会自己道别。
2. **数量词解析**（`music/query.py`、`model.py`、`tools.py`）：新增 `_COUNT` / `cn_number()` /
   `split_count()` / `QueryPlan.limit`，中文与阿拉伯数字都识别，上限 50（对齐 `queue_batch`），
   搜索深度随 limit 提高；顺带修掉 `放一?` 吃掉「放一生中最爱」里「一」的正则 bug。
3. **空唤醒计数**（`audio/vad.py`、`protocol.py`、`process.py`）：`silent_wakes` /
   `silent_wakes_session` 进 `Metrics`（`/api/v1/audio` 可见），日志带两个计数；重连时进程级计数
   carry-over、会话级归零。行为不变（仍 disarm、阈值未动）。
4. **恢复会话时的竞态守卫**（`session/turn.py`）：快照记录列表对象本身，恢复前 identity 不符就跳过，
   不把行写进通道重绑后的新列表。
5. **`tools.ha.call_timeout_s` 上限 60 → 20**（`config/schema.py` + example/template/catalog）：
   超过 turn 工具超时的值会被 turn 先取消，回查与「部分成功」回话永远跑不到。

### Note

- 只解析句首数量；倒装（「周杰伦三十首」）与字面歌名（`放《三首歌》`）按原样处理。
- `call_timeout_s=20` 时批预算 `×1.5` 会超过 turn 取消点：20 是硬顶，10 是推荐值。

## [0.8.12] - 2026-10-30

**Theme: 现场三连修，外加一条把家里的地址从公开文档里撤下来。** 0.8.11 上线当天 9哥 在真机上
撞到的三个问题各归各位：唤醒后一句话没说也会按旧规则道别；「下线」被当成闲聊，会话不关、音乐不停；
dot2 四台的 2.5 秒回声门把「你好小宝，现在几点」的后半句吞掉（这一条是配置，不是代码，记在这里
是让下一个部署者知道「默认 2500 不是定律」）。同批把文档里的真实内网地址换成占位符——
`secret-scan` 的软规则本来就为此而设，红着不是故障，是提醒。

### Fixed

1. **空唤醒不再道别**（`session/turn.py`）：会话内一句话都没说过时，空闲超时静默关闭，
   日志是 `silent close (no speech in this session)`；真的聊过的会话仍按时道别——那是 0.8.x
   一直以来被要求的「别一声不响消失」。9哥 现场：说「退下吧」→「好。」，12 秒后一条**空会话**
   又播「没事的话，我先下啦。」
2. **「下线」「下线吧」进默认结束词**（`config/schema.py`，21 → 23 条）：整句匹配语义不变，
   有音乐时走 `media_stop`（停音乐 + 关会话），`设备下线了吗` 这类疑问句仍不触发。

### Changed

1. **文档里不再出现真实内网地址**：`docs/HANDOFF.md` 的 `usb_snapshot_url` 示例改为
   `http://<mac-lan-ip>:8766/snapshot.jpg`。`secret-scan` 的 hard 规则（密钥、家目录绝对路径、
   口令字面量）与 soft 规则（私网地址）保持原样，不放宽、不加目录级豁免。

### Note

- dot2 的 `audio.voice.ack_echo_ignore_ms` 在代码里默认 `None`（继承机型 fact），
  现场把四台从 2500 调到 1200 属**部署配置**，不随本版下发；新装用户若要「一口气说完」
  的体感，参考 `docs/HANDOFF.md` 里同一段的记录。
- 镜像与插件版本：本版不改 `rui_compat`，两个桥仓零改动。

## [0.8.11] - 2026-10-30

**Theme: 身份、清单、画面都变成部署者能决定的事——但版本号不能。** 三块新能力：身份文案
里可以写版本号（值只能来自代码）、HA 实体清单从「全量注入」变成四个配置项、新增
`local.look` 让助手真的能看一眼摄像头。同批修掉 0.8.10 现场暴露的两件事：命名的场景
（`scene.turn_on`）可以执行，不再是 scene 域一律拒绝；多实体批量控制分批 + **回查后**如实
报「部分成功」，不再把 20+ 实体的失败说成「全都没关」。无协议破坏性变更，两个桥仓零改动。

### Added

1. **A — 身份占位符 `{version}` / `{config_version}`.** `assistant.persona` 与
   `assistant.system_prompt` 的 format 占位符新增两项：`{version}` = `rui.__version__`、
   `{config_version}` = 配置格式版本（`Config.version`）。`session/prompts.py` 的
   `build()` 用**同一份**映射渲染模板与 persona，两处都能用；手写花括号的老配置仍走原
   try/except 原样保留。热改动仍然生效：`PUT /api/v1/config {"assistant": {...}}` 换掉
   `runner.assistant` 后下一轮重建 prompt 即用新文案。
2. **B2 — `tools.ha.allowed_scenes`：命名场景白名单（默认空 = 仍然拒绝）.** 每项必须是
   `scene.<lowercase_digits_underscore>` 形式的 entity_id（配置校验期报错，重复项去重），
   匹配用 **entity_id 精确相等**——friendly name 只用于注入展示，改名不能把别的实体混进
   白名单。`lock` / `alarm` / `valve` / `script` / `automation` / `shell_command` /
   `python_script` / `hassio` 仍然一律拒绝，不因这个键放宽。白名单里的场景会在注入清单里
   单独成段（「可用场景：离家场景[全屋灯闭]=scene.…」），**不受 `context_domains` 影响**，
   名字过 `_clean_untrusted`、整段仍以「[不可信外部数据]」开头；白名单为空时不出现该段。
3. **C — 新只读工具 `local.look`：看一眼摄像头.** 参数 `question`（可空：想问画面里的
   什么）与 `camera`（可空 / `书房` / `这里` = 本机 USB；配置过的别名 = 对应的 HA 摄像头；
   也接受 `camera.xxx` 形式的 entity_id）。取**一帧**：USB 走
   `GET {vision.usb_snapshot_url}`（实时抓帧），HA 走
   `GET {tools.ha.base_url}/api/camera_proxy/<entity_id>`（HA 保存的**最近一帧**）。
   图在内存里转成 `data:image/jpeg;base64,` 交给**现有** LLM 链路（不新建 HTTP/供应商栈、
   不改对话路径），要求 1–2 句中文、≤80 字，提示词写明「只描述看到的，看不到就说看不到，
   不要编」。回复带标签区分实时与最近一帧（`vision.label_live` /
   `vision.label_latest`）。**画面会以 base64 发到配置的大模型端点，默认
   `api.deepseek.com`**；不落盘（不写 data 目录、不写 `/tmp`、不缓存）。
4. **C2 — `scripts/usb_snapshot_server.py`：Mac 侧快照服务.** 单文件、只用标准库，
   `ffmpeg -f avfoundation` 抓单帧走 stdout 管道（不落盘），
   `GET /snapshot.jpg` → `200 image/jpeg`；抓帧失败/超时 → `503` + 一行原因；其它路径 404。
   默认 `127.0.0.1:8766`（本机 8765 已被其它常驻服务占用），`--host --port --device --size
   --ffmpeg --timeout` 可调；**没有鉴权**，所以要跨机就绑内网、不要暴露公网（见
   `docs/usb-snapshot.md`）。

### Changed

1. **A — 默认 persona 与默认 system prompt 换了口径.** 默认 persona 现在逐字是
   「你是家里的小助理，叫{name}，跑在 rui {version} 上（家里自建的语音服务，不是 Home
   Assistant）。不知道就说不知道，别装懂。」默认 system prompt 追加一条歧义规则：用户问
   你是谁 / 你是什么系统 / 版本是多少时，说的是**你自己**（rui 的版本），不是 Home
   Assistant；提到 Home Assistant 的版本时必须明确说成它的版本。另加一句「你自己就是客厅
   那台音箱，不要把你自己当可控制设备」（模型过去会答应「把音箱关掉」）。
2. **B — HA 实体清单四个配置项，默认精简.** `tools.ha.context`（默认 `true`：是否注入）、
   `context_domains`（默认 `["light","switch","cover"]`）、`entity_cap`（默认 `120`，
   1–1000）、`context_exclude`（正则列表，默认空）。**默认去掉 `scene` / `script` 是有意的
   默认行为变化**：`tools/ha.py` 的 `EXCLUDED_DOMAINS` 本来就拒绝对它们的调用（0.8.11 起
   scene 只走白名单），列出来只会教模型去调一个必然失败的域。过滤顺序固定为「域 →
   `context_exclude`（`re.search`，大小写不敏感，实体名或 entity_id 任一命中即排除）→
   `entity_cap`」；取数预算按 cap 的 3 倍（200–1000）放宽，避免被排除项吃掉上限；注入文本
   仍带「[不可信外部数据]」标记与 `_clean_untrusted` 清洗；每轮日志打印
   `fetched N / injected M`；缓存带 domains 指纹，改配置立即生效（不用等 TTL）。
   生产实测口径 75 条 / 4121 字 → 预期 ~43 条 / ~1796 字（按部署配置的 exclude）。
3. **B2 — 批量控制 8 个一批、超时可配、回查后如实上报.** 多实体 `call_service` 改成每批
   ≤8 个（`SERVICE_BATCH`）、**串行**发送，总预算 `call_timeout_s × 1.5`（新配置
   `tools.ha.call_timeout_s`，默认 `10.0`，2–60），单批超时取「剩余预算」与
   `call_timeout_s` 的较小值。批次失败后**先回查受影响实体的状态再说话**：全部生效 →
   `ok`（话术不变）；部分生效 → 新状态 `partial`，payload 带 `ok_count` / `total` /
   `failed`（≤2 个名字 + 「等」），话术 `device.partial`「只做成了一部分：{total} 个里有
   {ok_count} 个照做了，{failed}没成功，别当成全好了。」；全部未生效 → `unreachable`
   （现有话术）；**回查本身失败** → 保守报 `partial`，绝不报 `ok`，也绝不声称「全都没关」。
   单实体路径行为不变。
4. **小修 — `/api/v1/debug/tool` 现在按 `spec.name` 或 `llm_name` 解析.** `runner._handlers`
   的键是 `spec.llm_name`（`local.look` → `look`），0.8.10 用注册名调用会被判
   `404 no such tool`，正是本地自检与静音验收要用的那条路。现在两种名字都能解析到 handler
   与渲染名，真不存在的名字仍 404。

### Notes

- **无协议破坏性变更**：新增的都是配置键、工具、短语与 `partial` 状态；`rui_compat` 不变
  （`>=0.8.1 <0.9.0`）；插件不变（dot2 1.3.5 / xiaoai 0.2.3）；
  `rui-xiaoai-bridge`、`rui-dot2-bridge` **零改动**。
- **「版本号本身不可配置」是设计取舍，不是遗漏**：配置只能决定文案里**哪里**出现版本，
  值只能来自 `rui.__version__`（`{config_version}` 同理来自配置格式版本）。要是版本能写进
  配置，升级后就会漂移、手写错还能对着用户撒谎；现在问「版本是多少」得到的永远是这一份
  代码的真实版本。
- **隐私口径（必读）**：`local.look` 取的这一帧以 base64 发到配置的大模型端点，默认是
  `api.deepseek.com`；`providers.llm.chain` 配了多级兜底时，**每一家**都会收到同一帧。
  不想让画面出网，就把 LLM 链指向本机的 OpenAI 兼容视觉服务，链路不用改。工具自身日志只有
  设备名 + 字节数 + 耗时（毫秒）；框架层既有的工具调用日志仍会记参数与渲染后的回复（全局
  行为，按 logger `rui.tools.look` 可单独过滤）。细节见 `docs/local-look.md`。
- **老配置兼容**：四个 `tools.ha.*` 新键与 `tools.vision.*`（`usb_snapshot_url` /
  `cameras` / `timeout_s` / `cooldown_s`）都有默认值，不写也能跑；不写新键时唯一的实际
  变化是清单默认不再列 `scene` / `script`（这是本版有意的默认变化）。`allowed_scenes` 默认
  空 = 与 0.8.10 一样拒绝一切 scene 调用。
- **`vision.timeout_s` 是一次 look 的总预算**（取一帧 + 视觉描述共用），不是每个环节一份；
  整轮工具超时是 20 s，所以上限压在 30 s 也不会让回合卡死。
- **未在真机验证**：本版发布时生产仍是 **0.8.10**；发声类验收（问版本/问身份/天奕门口
  画面/书房 USB 画面/关全屋灯）留白天由 9哥 现场做，`docs/releases/v0.8.11.md` 与
  `docs/HANDOFF.md` 写清了待验清单与回滚锚（0.8.10 index
  `sha256:30e8ed47f2ae0ce4281752c4130bfa58e53f63095f128eae5330e5e5703e1155`）。
- **门禁**：`uv run pytest -q` 与 `uv run ruff check src tests` 由独立 verifier 在代码冻结后
  重跑并回填到 `docs/releases/v0.8.11.md`；本版不删不跳任何测试。

## [0.8.10] - 2026-10-29

**Theme: every wake and every barge now gets an answer, and a failed request stops
looking like a successful one.** Two silent paths closed (point-and-play failures,
noise/empty transcripts), the one-turn latch stops locking a room out for 20 s after a
silent wake, favourites get a real three-state answer, HA batch reads stop reporting
argument errors as "unreachable", and capture clipping becomes a metric instead of a
mystery. No protocol or `rui_compat` change.

### Changed

1. **T1 — 沉默唤醒不再把房间锁死 20 s.** cue 被切除的唤醒（唤醒词落在被 AEC/cue gate
   削掉的窗口里）不会增 `idle_events`，控制面收不到 `WakeIdle`，one-turn 闩一直开到
   20 s watchdog；期间同房间每次唤醒都被 `rejected: turn_open`（真机 8ch/16k 上表现为
   「唤醒后 20 秒内怎么喊都没反应」）。超时路径现在无条件上报 `WakeIdle`，房间立即
   可再唤醒；watchdog 保留为兜底并降为 debug 日志。**T1b**：已有 turn 在转写/播报时，
   迟到的 `WakeIdle` 被忽略（debug 日志、不动灯/不说话、不释放闩），不会拆掉正在
   进行的回合；该回合自己结束时仍走原释放路径。
2. **T2 — 点歌失败不再静默.** `local.music_play` 原先整条声明为 `silent`，runner 把每次
   silent 调用当成完整回答而不看状态：`not_found` / `unreachable` / `unresolved` 既不
   播确认也不报错，用户听到的是「什么都没发生」。现在 `silent` 只覆盖 `ok` / `ok_one`，
   失败保持回合打开并念出短语；播放入口要求结果非空，artist-pool 兜底按标题接受度过滤，
   模糊池子不再在点不到目标曲目时播一首无关的翻唱（现场「紫色的雨夜航线第三号」不再
   误播《云紫色》）。
3. **T3 — 收藏结果三态.** `MusicSource.star` 只能回 bool，不支持收藏的来源被说成「服务器
   没响应」。协议改为 `StarStatus(ok / unsupported / rejected)`：bool-only 来源保持原语义
   （`True`=ok、`False`=rejected），unsupported 说「这个来源不支持收藏」，未知返回值记
   debug 日志并按 rejected 处理，不再让回合崩。
4. **T4 — HA batch 读参数归一（#34）.** `ha_batch_get_state` 只接受复数
   `entity_ids` 列表，而模型（以及写工具）用单数 `entity_id`，值可能是字符串也可能是
   列表；参数错误被表面化成 `MCPUnavailable` 并被说成「设备连不上」。现在所有接受的形状
   先归一为 `entity_ids: list[str]` 再上线，参数/校验失败映射到新的 `bad_args` 状态，
   文案说「这次没查到」而不是「设备离线」；真正不可达的 HA 仍是 unreachable。
5. **T5 — barge 和失败都有反馈，削波可见.** barge 唤醒过去完全没有提示音，噪声/空转写
   被静默丢弃（站在削波中的 dot2 前喊两次，两次都没有回应）。现在：
   - barge 播一次短提示（`audio.ack.barge_ack`，默认 `true`；长度限制在 **80–160 ms**，
     不开 gate hold、不开 echo 窗口，避免盖住用户已经在说的第一句）；`ack.mode: none`
     或设备静音时同样静默；`ack.barge_ack: false` 恢复旧的静默 barge。
   - 噪声 / 空转写回「没听清，你再说一遍」并保持会话打开；barge 的 `WakeIdle` 通过新
     字段 `WakeIdle.barge` 说同一句，普通沉默唤醒仍然不说（follow-up 不会误说）。
   - 采集削波可见：`|x| ≥ 0.97` 的样本连续 5 帧超过 1% 记一次 overdrive burst（限频
     WARNING），Metrics 新增 `overdrive_count` / `overdrive_ratio_max` /
     `last_overdrive_at`，`[seg]` 行在削波超过 5% 时带 `clipped=NN%`。

### Notes

- **无协议破坏性变更**：`WakeIdle.barge` 与 Metrics 的三个 overdrive 字段都是 **additive**；
  `rui_compat` 不变（`>=0.8.1 <0.9.0`）；插件不变（dot2 1.3.5 / xiaoai 0.2.3）。
- `rui-xiaoai-bridge`、`rui-dot2-bridge` **零改动**。
- **部署新增**：dot2 override 环境变量 `DOT2_MIC_GAIN_DB: "12"`（把近讲从削波拉回正常
  电平；只改 override 的环境变量，不改桥代码）。
- **本版构建自 `main`，包含已发布未部署的 0.8.9**（多相 decimator、模板不再写
  `audio.capture.rate`）：**部署 0.8.10 即同时上线 0.8.9**。依据 **0.8.10 批次生产运行时
  基线（2026-10-29，9哥 任务书）**，现网显式 `audio.capture.rate: 16000`，显式值赢
  （`kind: negotiated`），manifest 48k 不生效。部署前先看
  `/api/v2/devices/living/audio` 的 `sources` 实测：16k 分支 → `global-config` +
  `factor = 1`（不执行多相滤波）；若实际为 48k（`device-manifest` / global 48000）→
  `factor = 3`，按 48k 分支与 L27 复核 CPU/ASR。
- `audio.aec.who` 的 `none` / `both` **仍无独立实现**（只有 `device` 有真实分支，
  `none`/`both` 当前等同 `controller`），已登记 `docs/known-limitations.md` **L28**；
  本版不改行为。
- **老部署配置不变**：模板/示例新增的 `audio.ack.barge_ack` 有默认值，不写也会生效；
  生产运行时参数（capture 16k/8ch、`mic_shift=10`/`ref_shift=16`、keywords、
  `wake_preroll_ms=500`、`standby_kws_gate=false`、AEC `mix`/4096）本批不动。

## [0.8.9] - 2026-10-29

**Theme: the 48 kHz capture path gets a polyphase decimator, and 8ch/48k devices now
follow their manifest's native rate by default.** No protocol or `rui_compat` change.

### Changed

1. **多相抽取（Decimator，48k 路径）.** 48-tap FIR 按 `factor` 拆成相位，只对输出样本
   做乘加，不再对每个输入样本卷积。语义与 0.8.8 逐样本一致（独立 oracle 覆盖
   factor 2/3/4/5 × 1/2/7/8 ch × 多帧/非整除尾帧，worst `|diff|` **2.98e-7**）；
   `factor == 1`（16k wire）不构造滤波器、`fir is None`，输出与 `astype(float32)`
   逐样本相等，行为不变。
   离线 10 s / 8 ch / 48 kHz（M2 Ultra，`time.process_time()`）：旧全速率约 **0.096 s**
   → 多相 **0.0263 s**（8 ch，3.66×）/ **0.0352 s**（生产 7 列 = 6 mic + ref，2.74×）；
   gate「10 s 音频 ≤ 0.2 s」PASS，`--seconds 30` 归一化 0.6 s 同样 PASS。
   bench 与剖面见 `docs/analysis/decimate-profile-0.8.9.md`。
2. **8ch/48k 默认跟随 manifest 原生率.** `src/rui/config/template.yaml` 与
   `config.example.yaml` 不再写 `audio.capture.rate`，`effective_audio()` 直接采用插件
   manifest 的 `audio_defaults.capture.rate`（LX06 = 48000）。显式 16k 仍可用，0.8.8
   行为保持不变；但显式 16k 会让设备端自己做 48k→16k 重采样，语言带实测约 **−15 dB**，
   后果是 ASR 空转/超时（唤醒照常成功，所以「能唤醒」查不出）。16k 因此降级为
   「想省带宽时显式写」的选项。文档见 `docs/audio-pipeline.md` 的「wire 率」一节与
   `docs/known-limitations.md` L27。

### Notes

- **无协议变更，`rui_compat` 不变**；`rui-xiaoai-bridge`、`rui-dot2-bridge` 零改动。
- **CPU 口径**：多相化只解释抽取器本身（48 kHz 路径上约占单核 0.3%，旧约 1%）。生产
  从 16k 换到 48k 多出来的 +2 核里，其余部分的归属**未**在离线证实——不要读成
  「CPU 已解决」；剖面文档已写明这条边界。
- 老部署若要吃到 48 kHz，需手动删掉配置里的 `audio.capture.rate`（或改成 48000）并重启
  音频平面；0.8.9 不会自动改写现网配置。本 release 只发版本，部署由 9哥 单独决定。

## [0.8.8] - 2026-10-29

**Hotfix: the standby KWS gate now learns the room floor, and `voice peak` only logs
interaction windows.** 0.8.7 shipped an absolute `-60 dBFS` threshold; the production
mics' ambient peaks measured `-35..-49 dBFS`, so the gate never suspended (container
idle stayed at 57.6%). Forcing it cold showed the real split: 57.6% -> 34.5%.
No protocol or `rui_compat` change.

### Fixed

1. **Relative-floor standby gate.** `StandbyKwsGate` keeps `floor_db` (instant down,
   0.01/frame EMA up, ~2 s); the silence line is
   `max(audio.voice.standby_kws_db, floor_db + 6 dB)`. Ambient can therefore go cold,
   while a speech onset resumes the spotter and replays the 500 ms preroll. The
   configured `standby_kws_db` is now the **absolute lower bound** (mainly for very
   quiet rooms); `standby_kws_hold_ms` is the main tuning knob.
2. **`voice peak` only in interaction windows.** The 30 s log is emitted only when
   `wake_count` / `utterances` / `device_wakes` grew or the VAD was speaking in the
   window, and the peak is visible (> -55 dBFS). Pure-ambient windows are silent.
   The line keeps `[level] voice peak … (active …)` and never prints `0.0 dBFS`.
3. **Console/example/schema text** for `standby_kws_db` now describes the absolute
   bound and the learned floor; the old "a noisy room never goes cold" claim is gone
   (`docs/standby-cpu-0.8.7.md` §1.1/§3.1/§5.1/§6 updated with the real numbers).

### Notes

- **<20% idle CPU is not reached by this hotfix.** 0.8.7's forced-cold measurement
  (34.5%) is the expected ceiling of the gate; the rest is per-frame AEC/resample
  (P2, not in this batch). The gate saves KWS work only.
- All 0.8.7 features are unchanged (private music sources, `local.tell_joke`,
  music aliases, short replies after barge-in).
- No `rui_compat` change; no plugin change.

## [0.8.7] - 2026-10-29

**Theme: private music sources, a joke tool that never fails, and a quieter standby.**
No protocol or `rui_compat` change (`>=0.8.1 <0.9.0`); plugins unchanged.

### Added

1. **External music sources (`RUI_MUSIC_SOURCES`).** An operator can register an
   out-of-tree `MusicSource` as `id=module:factory` (comma-separated allowlist).
   The factory receives a read-only `SourceContext`; the module must declare
   `music_sources_compat` in the plugin range grammar (`>=0.8.7 <0.9.0`) and the
   result is validated against the five methods by name. Unknown ids still raise
   `ValueError` (422 at every write path); an import/compat/factory failure leaves
   music off with one warning and an honest console panel. Docs:
   `docs/music-private-sources.md`; example: `examples/music_sources/stub_source.py`.
2. **`local.tell_joke`.** Calls the self-hosted joke service
   (`GET <url>/v1/joke?kind=&topic=&pid=`, `X-Internal-Token` from the
   `JOKE_SERVICE_TOKEN` secret), with 20 bundled samples, two-stage riddles
   (`action=new|answer`), and an optional `audio_url` that rui downloads to
   `<data_dir>/tmp`, validates as WAV and plays through the audio plane.
   Degradation is `audio_url -> text -> bundled sample`; no failure raises.
3. **Music aliases on the main intent path.** `AliasStore.apply` now runs before
   `q.plan()` for voice and console search (longest key first, music path only),
   the console passes the `NameResolver`, and the alias file is repaired/created
   `0640` (runbook: `docs/music-alias-0.8.7.md`).
4. **Short replies after a barge-in.** With music playing or paused, a closed set
   of short answers (`行/好/继续/别停/换一首/停止/放吧/听吧`) maps to
   resume/hold/next/stop instead of the anti-hallucination refusal; non-music
   questions are still refused.

### Fixed

1. **`voice peak` log on silence.** Silent windows no longer print `0.0 dBFS`
   (which reads as full-scale clipping). The aggregate starts at `-120 dBFS`,
   only voiced windows log, and the line is
   `[level] voice peak -20.5 dBFS (active 6 s)`.
2. **Standby KWS gate.** With no uplink audio and a quiet room, the spotter is
   suspended; on onset it replays 500 ms of preroll so the wake word's head is
   not lost. Playback, turns, cues, music and device wake keep it hot.
   Defaults: `audio.voice.standby_kws_gate=true`, `-60 dBFS`, `1000 ms`.
   Measured synthetic idle: 1500 steps -> 50 processed + 1450 skipped (96.7%).
   Device wake active disables the saving (documented limitation).
3. **WAV validation for joke audio.** Clips are checked with `wave` and the
   audio plane's own `read_wav_mono` before playback; invalid clips fall back to
   text instead of skipping TTS.

### Notes

- **P2 not done:** the living-room profile (py-spy) and the ref-channel/AEC
  standby evaluation stay on the backlog.
- **B1/B2/B3 not in this batch:** ASR hot words, three-way wording
  (not heard / answer / not in library), and the known-limitations entry.
- No `rui_compat` change; no plugin changes.

## [0.8.6] - 2026-10-29

**Theme: the channels page comes back to the v2 console, the landing pages stop
tracking versions, and the dot2 1011 evidence is recorded as data.** The console
page restores a capability removed with the legacy panel system in 0.8.0 (it is
not a new protocol surface); no protocol or `rui_compat` change
(`>=0.8.1 <0.9.0`). Plugins ship at **dot2 1.3.5** / **xiaoai 0.2.3**.

### Added

1. **v2 console `/channels` page.** Telegram and WeChat cards (enable switch,
   `text`/`voice`/`both` reply mode, per-id allowlist add/remove with trimming),
   the managed channel gateway's `start`/`stop`/`restart`, and read-only token /
   login state (`token_configured`, `logged_in`/`account_id`/`user_id`). Saving
   issues `PUT /api/v1/channels`, then re-reads with `GET` and reports whether the
   read-back matches; 503 copy is split by context (gateway action vs. channels
   load/save), 422 shows the backend detail, network failures keep the shared
   fallback, and zh/en stay in sync. Reached from the Config sub-nav; no legacy
   `/legacy` route is revived and no design system is added. Covered by
   `tests/test_channels_view.py` (21 cases: source contract + real `TestClient`
   read-back/422/503 behaviour, no mocked HTTP errors). *(D8)*
2. **Version-agnostic landing pages.** The three repo READMEs (core + both
   bridges) now describe the project instead of the version history: per-version
   sections are gone and the only version pointer is "Latest release". The
   one-command AI onboarding path stayed. *(D6)*
3. **arm64 runtime verification.** The published 0.8.5 image was pulled and
   checked on `linux/arm64`: `ffmpeg` runs, `rui` + `numpy` import, and the
   service starts with `/health` returning 200 and `version=0.8.5`. Runtime layer
   only — no ARM performance/stability claim. Evidence:
   `docs/arm64-verification-0.8.5.md`. *(D7)*

### Fixed

1. **dot2 1011 bridge-side close counters documented (dot2 bridge 1.3.5).** The
   bridge's single close line now carries `close#N interval=…s dropped=N` (pure
   diagnostics; keepalive/abort/reconnect behaviour unchanged). §10 of
   `docs/analysis/dot2-data-plane-1011.md` shows one real event (table #12:
   `rx=9329320B tx=0B close#12 interval=305.1s`) instead of splicing two events,
   and keeps the field-by-field reading guide. *(D4)*
2. **Channel page 503 copy is contextual.** A failed gateway
   `start`/`stop`/`restart` says the channel supervisor is not ready; a failed
   channels load/save says the service is temporarily unavailable. 422 still
   carries the validation detail and network errors keep the shared fallback.
   `v2.channels.saveFailed` is now the save-failure title instead of dead copy.
   *(task-19)*
3. **Analysis example consistency.** The §10 sample line and its `close#N`
   explanation now agree with the §2.2 table, so the documented log shape can be
   re-checked against production output. *(task-19)*

### Deployment and plugins

- **0.8.6 is planned for production** (the first deploy since 0.8.4; 0.8.5 was
  tag/release only). The tag/release performs no deployment itself: the rollout
  follows the `docs/HANDOFF.md` checklist after 9哥's approval, with backup and
  rollback anchors.
- **Plugins**: dot2 bridge **1.3.5** (adds the 1011 close counters) / xiaoai bridge
  **0.2.3**. `rui_compat` is unchanged (`>=0.8.1 <0.9.0`); xiaoai is README-only
  in this batch. See `docs/releases/v0.8.6.md` for the deployment matrix.
- **Not verified**: the channels page was not point-checked in a real browser
  (typecheck/build + API tests are the evidence); D7 is runtime-only.

## [0.8.5] - 2026-10-28

**Theme: the line at the time you asked for, the minute you actually said, and a CI
that can no longer hang.** A small release: no protocol change and no `rui_compat`
change, plugins stay at **1.3.4** / **0.2.3**, and **nothing is deployed** by this tag
(production remains 0.8.4).

### Fixed

1. **A due reminder is spoken as a reminder.** The online branch of `_reminder_line()`
   returned the stored action text bare, so the room heard only 「起床」. It now uses
   `reminder.due` (「时间到了，{text}。」); a leading setter trigger
   (「提醒我 / 叫我 / 让我 / 叫你」) is stripped, trailing sentence marks are
   normalised to one 「。」, and empty text degrades to 「时间到了。」. The offline
   `reminder.offline` line is byte-for-byte unchanged. *(A)*
2. **Minute-scale reminders are claimed by the fixed-sentence path.** 「一分钟后提醒我
   喝水。」 used to fall through to the LLM tool because `parse_voice` only computed
   seconds. It now claims `N分钟(之)后 / N分钟以后` (Chinese or Arabic digits) with the
   same `parse_at("+Nm")` arithmetic as the tool path. The action must be **one
   sentence**: a second sentence (「…。我是小志。」) still goes to the LLM, so the
   task-32 behaviour and the speaking room are preserved. Both paths share the single
   `_reminder_room()` resolver. Hours/days/weeks/quarters/intervals,
   `过N分钟`/`半小时` and multi-sentence utterances stay with the model. *(B)*
3. **A 17–20 character comma reply is one TTS request.** The 16-char clause window
   still cut a reply whose comma sat at the 16th character into "16 chars + 1–4 char
   tail" — the same vendor head/tail seam task-32 removed one band lower. A comma is
   now a split point only when more than `SOFT_MERGE_MAX_CHARS` (20) characters of the
   current sentence are buffered, so ≤20-char replies are one request while longer
   answers still stream their first clause (release on the 21st buffered character).
   Sentence ends, the ≤10-char hold and the 45-char cap are unchanged. *(E)*
4. **CI `test (3.11)` no longer hangs.** CPython 3.11's `asyncio.wait_for` swallows a
   cancellation that lands in the same event-loop tick as the inner future's completion
   ([python/cpython#96764](https://github.com/python/cpython/issues/96764) — the
   `wait_for` rewrite landed in 3.12 and was not backported to 3.11); `_speak_loop` waiting
   on `wait_for(queue.get(), 0.5)` therefore stayed `cancelling` forever after shutdown,
   which is why every push since 0.8.3 kept the 3.11 leg `in_progress`. The wait is now
   `asyncio.timeout(0.5)` (same timeout, same `TimeoutError`, stdlib on 3.11+), and the
   `test` job carries `timeout-minutes: 20` so a future hang fails fast instead of
   burning GitHub's 6 h default. Tests were not weakened. *(G)*

### Changed

1. **LX06 客厅 example volume 60 → 45.** The shipped `config.example.yaml` lowers the
   living speaker's `volume` (60 was measured too loud; dot2 units are fine and are not
   touched). The schema default stays 60 on purpose, so a dot2 install that omits
   `volume` keeps its old level. *(C)*
2. **Docs**: a read-only diagnosis of the dot2 data-plane `code=1011` closes (bridge-side
   WS keepalive, ~1.77% uplink gaps, **no behaviour change**, bridge untouched); the
   sample-collection criteria for task-2 (「停止」 while music plays) / task-3
   (158–221 ms gap after barge) as L25/L26 — **no code change** for either; and L21 was
   refreshed to the 0.8.5 claim range (minute-scale relative times are now fixed-sentence,
   multi-sentence utterances stay with the model). *(D/F)*

### Not verified / not done

- A/B/C/E are **not real-device verified** (wording, minute-claim hit-rate under ASR
  noise, and the audible seam are all inference from unit tests).
- **D is diagnosis only**; the bridge (`rui-dot2-bridge` 1.3.4) was not modified and no
  runtime behaviour changed.
- **task-2 / task-3 unchanged** (no stable sample); their observation criteria are in
  `docs/known-limitations.md` L25/L26 and `docs/backlog-0.8.md`.
- **H (console reply-segment metrics)** deferred to the backlog.
- Production is **not** updated: 0.8.5 is tagged and released only.

## [0.8.4] - 2026-10-28

**Theme: every reminder remembers the room, and a comma is not a seam.** Two field
reports from 21:13: the LLM tool path still dropped the room of an unnamed reminder, and
a 13-character reply was still split at its comma because the merge window was 10 chars.

### Fixed

1. **The LLM `set_reminder` path carries the speaking room.** A per-turn
   `ToolContext` (ContextVar, so one runner can serve every room) is scoped by
   `TurnRunner.run`; the handler stores `room=context.room`. The voice fixed-sentence
   path and the tool path share one `_reminder_room(cfg, client, named)` resolver;
   channel/console turns have no speaking device and stay room-less. An unnamed
   reminder from 次卧 now stops and speaks only in 次卧, never in 客厅.
2. **Ultra-short merge window 10 → 16 characters.** 「一分钟后提醒你喝水，小志。」
   (13 chars) is now one TTS request; the cap releases only at/after 16 chars, so the
   extra wait is bounded by the next punctuation. 19-character two-sentence replies
   still split, and first audio does not get later.

No protocol or `rui_compat` change (`>=0.8.1 <0.9.0`); plugins unchanged.

## [0.8.3] - 2026-10-28

**Theme: the seams you can hear, and the room that asked.** Two field reports from the
same evening: a short reply paused at its comma, and an unnamed reminder rang in the
wrong room. Both are fixed without touching barge/AEC/volume/follow-up semantics or
the compat window (`>=0.8.1 <0.9.0`).

### Fixed

1. **Ultra-short replies are one TTS request.** When the model paused >300 ms after
   the comma (`在，\n` … `你说。`), the short-hold cap used to release by *splitting*,
   so the device played two renditions and the vendor's head/tail silence became an
   audible ~160 ms seam. The cap/force/tail release now merges the whole ultra-short
   buffer into one request; literal `在，你说。` was already one request. First audio
   is unchanged (~400 ms in the measured case), and longer replies still stream.
2. **Reminder provenance.** An unnamed voice reminder ("十秒钟后提醒我喝水") now keeps
   the room of the device that heard it instead of falling back to `default_output`.
   It rings/stops music only there; an offline source still falls back with
   "<room>那台没在线" and the existing health counters.
3. **Diagnostics:** each TTS call logs `tts_call#k chars/audio/gap`, the turn trace
   records call count + inter-call silence, and the plane records per-reply segment
   count, per-segment audio and seam/inner silence (additive `Metrics`/trace fields).
   The segment ledger is closed on barge/stop, so the next reply cannot inherit it.

## [0.8.2] - 2026-10-28

**Theme: reminders hear seconds.** 「十秒钟之后」 is now ten seconds, not ten minutes.
No protocol change: `rui_compat` stays `>=0.8.1 <0.9.0`, and 0.8.1 plugins load
unchanged.

### Behaviour changes

1. **`when` accepts second-scale relative times.** `+10s`, `+1m30s` and `+90s` join
   `+10m` / `+1h30m` / `HH:MM` / ISO. The tool schema now names them and warns that
   「十秒钟」 is `+10s`, not `+10m` — the model previously saw only minute/hour
   examples and rounded a ten-second request up.
2. **The spoken path claims 「N秒后 / N秒钟后 / N秒之后」 without an LLM.** The fixed
   sentence grammar handles Arabic and Chinese numerals (`10秒`, `十秒`, `九十秒`),
   including the time-first form 「10秒钟之后提醒我喝水」, and computes the moment
   with `parse_at("+Ns")` — the same parser the tool path uses. Minute/hour relative
   times still go to the model, as before.
3. **The reply renders from the final scheduled `at`.** Both the voice path and
   `local.set_reminder` speak/list the `humanize(item.at)` of the stored reminder, so
   the words cannot disagree with what is actually scheduled. A missing action asks
   for the action; an unparseable time or an unsupported unit (`+10秒`, `+10d`) asks
   again and creates nothing.
4. **Midnight is arithmetic, not a special case.** `23:59:45` + `+30s` lands at
   `00:00:15` the next day through the same Shanghai-aware relative path.

### Tests

`tests/test_reminders.py` and `tests/test_reminder_voice.py`: `+10s` / `+1m30s` /
`+90s` arithmetic, ten seconds ≠ ten minutes, Chinese numerals, cross-midnight,
time-first sentences, reply text == stored `at`, the voice/tool parser spy, and
unsupported units creating nothing.

## [0.8.1] - 2026-10-28

**Theme: the follow-up window becomes honest.** A closed session no longer reopens
itself, the two per-model voice timings become manifest facts, the console names them,
and a playback gap now says which chunk stalled. No breaking API; two additive
protocol fields.

> **Tree version — not tagged, not deployed.** This section describes the tree only:
> `pyproject.toml` / `src/rui/__init__.py` still say 0.8.0 until the release bump, and
> production runs **0.7.9**. `v0.8.0` stays tagged as history but was never deployed.

### Behaviour changes

1. **A close path no longer arms a follow-up window.** `ReplyFinished` gains an
   additive `followup: bool = True`; the dismissal / stop-word goodbye and the idle
   farewell send `followup=False`. The plane still drains the goodbye and applies
   `post_playback_mute_ms` (so the speaker's own tail cannot become a request), and it
   clears the pending music hold, but it arms **no** wake-free window
   (`close reply drained: no follow-up window`). The old behaviour turned a session
   that had just ended back into a listener: speech 20 s later started a turn without
   the wake word. With the default `true`, an older control plane and every ordinary
   answer keep the pre-0.8.1 behaviour.

2. **The model manifest may declare the voice timings.** `audio_defaults` gains an
   optional `voice` block (`followup_delay_ms`, `post_playback_mute_ms`), merged like
   the other hardware facts: `devices[].audio` > manifest > explicit global. The Dot 2
   manifest (`rui-dot2-bridge` 1.3.3) declares **300 ms / 500 ms** — its device-side
   AEC leaves almost no echo tail, and the reference speaker's window left a listening
   window behind after a dismissal. A disagreeing global value is reported as a
   conflict, never silently replaced. `lx06` declares no `voice` block, so its
   effective values are unchanged (production keeps its global/device values).

3. **The console names the two fields.** The device audio panel renders
   `voice.followup_delay_ms` and `voice.post_playback_mute_ms` with human labels
   (`续听窗口` / Follow-up window, `播放后静音窗口` / Post-playback mute) while keeping
   the technical path visible. Their help text now says an empty device value inherits
   the **model manifest first, then the global value**. Both remain per-device
   writable and still require a plane restart.

4. **A playback gap records its chunk (additive).** `Metrics.playback_gap_chunks` is a
   per-reply ledger of `[chunk_index, gap_ms]` pairs, filled only while that reply is
   sounding and cleared when the next reply starts; the turn trace copies it into
   `meta.playback_gap_chunks` (an empty ledger is not recorded). The existing
   `playback_gap_count` / `playback_max_gap_ms` counters are unchanged. Additive with a
   default, so an older plane or control plane on either side still parses. Because the
   ledger is per-reply, the control plane takes a fresh metrics snapshot before the
   trace copy rather than the 2 s periodic one — a stale snapshot could attribute the
   previous answer's chunks to this turn.

5. **The LX06 48 kHz question is recorded, not "fixed".** `docs/adr/0008-lx06-capture-negotiation.md`
   explains why `lx06.yaml` keeps `capture.rate: 48000` while production asks the client
   for 16 kHz: `capture.rate` is the documented negotiated exception, the 48 kHz figure
   is the hardware, and 16 kHz is the deliberate wifi decision (~1 MB/s versus ~6 MB/s).
   No manifest or config change; the living speaker stays at 16 kHz. The same ADR
   records the cold-start observability conclusion: gated manifests are registered
   before the plane is built, the first probe re-resolves before Ready, and a late ack
   read retries up to 3 times. Residual: a capture layout that resolves late still
   needs a plane restart (the warning says so).

### Compatibility and upgrade

- **`rui-dot2-bridge` 1.3.3 is 0.8.1-only**: `rui_compat` is now
  `>=0.8.1 <0.9.0`, because 0.8.0 and the whole 0.7.x line reject the unknown
  `audio_defaults.voice` key (`AudioDefaults` is `extra="forbid"`). Deploy the core
  and the bridge **together**: core 0.8.1 + bridge 1.3.3, or stay on core 0.7.9 +
  bridge 1.3.2. The two mismatches are not equivalent: a **1.3.3 plug on an older
  core is refused** by the loader (compat window, then the unknown key), so every Dot 2
  loses its adapter; a **0.8.1 core with a 1.3.2 plug still loads**, but the Dot 2 keeps
  the global voice timings instead of its 300/500 model defaults. `rui-xiaoai-bridge`
  0.2.3 is unaffected (`rui_compat >=0.7.0 <0.9.0`, no `voice` block) and needs no
  change.
- No config migration: existing `audio.voice` values keep working, and a device
  without a manifest `voice` block resolves exactly as before.

## [0.8.0] - 2026-10-28

**Theme: the 0.8.0 breaking release.** Three breaking changes, in the order they bite:
(1) the legacy console and its 15 old links are gone — plain **404**, (2) a config that
still says `adapter: xiaoai` (or a plug manifest that omits `runtime.listen_port`) is a
**load error**, and (3) the compatibility window moves to **`>=0.7.0 <0.9.0`** in both
`/api/v2/meta` and the two bridge manifests. Everything else in this version is the
P0/P1 batch that landed on `main` after 0.7.9.

> **0.8.0 — tagged and released, never deployed.** The version bump
> (`pyproject.toml`, `src/rui/__init__.py`, `uv.lock`) and this section shipped as
> annotated tag `v0.8.0` → `26659b0` and a GitHub release on 2026-10-28, but the
> release was not deployed: **production stayed on 0.7.9**. 0.8.1 supersedes it on the
> deploy path (see the section above).

### Breaking changes

1. **The legacy console is gone — `/legacy` and the 15 old links are 404.** `/legacy`,
   its SPA shell, the root catch-all that served it and the **15 old-link 302s**
   (`/panel/*` × 11 plus `/setup`, `/secrets`, `/diagnostics`, `/channels`) are deleted.
   Those paths answer **404**; `/` is a 302 to `/v2/` (query preserved) and nothing else.
   The frontend keeps only the v2 tree plus the shared `AppIcon.vue` / `theme.ts` /
   `style.css` / `vite-env.d.ts` / `public/*`; root static assets (`/favicon.svg`,
   `/fonts/*`, `/logo*.svg`, `/assets/*`) are explicit mounts now. `web.console` stays in
   the config schema for compatibility but its value is ignored — there is no rollback
   switch. `/api/v1/panels` remains (v1 is frozen); `/api/v2` and `/v2/assets` are
   untouched. Anything bookmarked under `/panel/*`, `/setup`, `/secrets`, `/diagnostics`
   or `/channels` must be re-pointed at its `/v2/*` page.

2. **A stale adapter spelling and an implicit listen port are now load errors.**
   `src/rui/config/legacy_adapters.py` and the on-load rewrite are gone.
   `devices[].adapter` is required and has no model default, and a config that still says
   `adapter: xiaoai` fails on load with
   `devices[i] (room): adapter 'xiaoai' is no longer accepted in 0.8.0; change devices[].adapter to 'lx06'`.
   Nothing is rewritten and no deprecation warning is emitted; change that line to
   `lx06`. The 4412 compatibility port for `rui-device/v0` manifests is gone too:
   `runtime.listen_port` is required, so a custom plug that omitted it is rejected
   instead of silently binding 4412. `/health.adapter_plugins.warnings` no longer counts
   old spellings, and `/health.warnings` no longer has a `legacy_adapter_spelling` row.
   `config/recovery.py` keeps its policy: the bad file is copied aside, the service starts
   from last-good/defaults, and the note exposed by `GET /api/v1/config/recovery` carries
   the same fix sentence. 中文一句话：老配置不会被悄悄改写，启动即报错并告诉你把
   `adapter: xiaoai` 改成 `lx06`；自制插件的 `runtime.listen_port` 也必须写明。

3. **The compat window is now `>=0.7.0 <0.9.0`.** `GET /api/v2/meta` returns
   `"compat": {"min": "0.7.0", "max_exclusive": "0.9.0"}`
   (`src/rui/web/api/v2_api.py`). Both bridges ship the same window in their manifests:
   `rui-dot2-bridge` **1.3.2** (`4536968`) and `rui-xiaoai-bridge` **0.2.3** (`0d82250`)
   both declare `rui_compat: ">=0.7.0 <0.9.0"`. Core 0.8.0 **refuses** any manifest whose
   ceiling is still `<0.8.0`, and a core still on 0.6.x refuses the new dual-window
   bridges — upgrade the core and the bridges together. Device manifests are untouched:
   `compatibility.rui_min` stays `0.3.0` (dot2) / `0.6.0` (xiaoai). 中文一句话：0.6.x
   core 不再被支持（桥拒绝加载）；自制插件若还写 `rui_compat: ">=0.6.0 <0.8.0"`，
   0.8.0 会拒绝并报出 manifest 窗口与当前构建（报错里不列 core 支持的区间）。

### P0/P1 fixes

- **P0 — the catalog now reports the plug the process actually loaded** (task-25,
  tree only). Production showed `dot2-echomuse 1.3.1` as `source: builtin` while the
  mounted `PYTHONPATH` adapter was `1.3.2`. The adapter loader now reads a co-located
  `plugin.yaml` **before** the image catalog, registers which manifest gated the
  adapter, and `PlugCatalog` applies the explicit same-id rule
  `adapter > builtin > user`, exposing the winning `source_path` and the losing
  candidates as `shadowed[]`. `GET /api/v2/plugins` also returns `errors[]` for
  manifests that exist but cannot be parsed. The device page shows manifest
  declaration / global config / **device-reported probe** values side by side, with
  manifest-vs-global (`conflicts`) and probe-vs-effective (`reported_conflicts`)
  disagreements highlighted. No config migration, no bridge change.
- **P0 — the wake acknowledgement is no longer replayed as a user turn** (`82d45d9`;
  known limitation L17). The echo gate computed the cue length with the *device* playback
  rate (48 kHz) against a 16 kHz rendered cue, so a 460 ms "在呢" became 153 ms of cue and
  the rest was replayed: `transcript='在呢？'` → `reply='在，你说。'`, a 4.18 s
  self-answer. The window is now derived from the rendered rate, the hold starts when the
  cue finishes (`ACK_PLAYOUT_LEAD_MS=850` + cue + `ack_echo_ignore_ms`), and the VAD's
  first wake uses a measured prior (740 ms) instead of an unlearned lag. Cost, by design:
  **+850 ms** of buffered suppression on every wake (audio is buffered, not dropped).
  Covered by `tests/test_ack.py` and `tests/test_ack_echo_gate.py`; **hardware
  re-verification is still pending** (L17).
- **P0b — a dropped speaker link discards queued speech** (`88400aa`; bridge `9065d19`).
  A Dot2 reconnect no longer replays the tail of the sentence that was being spoken when
  the link died (measured: ~0.9 s @48 kHz left in the queue after unplugging). The bridge
  side adds a send watermark, a write timeout and half-open detection, and
  `speaker_connected` is reported fresh instead of sticky; the any-room health semantics
  are pinned in `tests/test_health_planes.py` (`d9d4b76`).
- **P1 — short replies are synthesised once** (`8344085`). Replies of ≤8 characters with
  ≤1 sentence-final punctuation are no longer split into two utterances, removing the
  217 ms gap after 「在，你说。」.
- **P1 — a closed session stays closed** (`925eb18`). After a stop intent, an end word or
  a farewell the LED no longer goes back to `listening`; the follow-up window is finite
  (N=8 s) and disarms to `led: off` when it expires; the last LED event of a turn is
  always `off`, and repaint attempts are bounded.
- **P1 — stop matching strips a fixed wake-word tail** (`b91599a`). `停止你好` /
  `停止小爱同学` now hit the stop intent instead of falling through to the model. This
  **reverses** the 0.7.9 expectation recorded as N2 in `docs/acceptance-pending.md`; the
  old wording said such utterances must not match.
- **P1 — volume reconcile tolerates one mixer step** (`8961cbf`, backlog d.7).
  `_reconcile_volume` no longer re-sends `set_vol` every ~16 s when the mixer reports a
  one-step difference on the 0–255 scale. The cost is explicit: a genuine one-step drift
  is not corrected.

### Added

- **Physical mute and volume keys are honoured** (`de89e65`, `58c845e`; bridge
  `0698460`). Dot2 key events now reach the core: mute is applied (the acknowledgement is
  skipped, transcription and LED still run) and exposed in `/health` and `/v2`; volume
  up/down write back `devices[].volume`, persist it, and suppress the reconciler for 10 s
  (5 % steps).

### Evidence

- `uv run pytest -o addopts=""` — **1752 passed / 6 skipped** (unchanged from the
  pre-change baseline of 1752/6, no regressions); `uv run ruff check src tests` green;
  `uv lock --check` green.
- Probe: `GET /api/v2/meta` → `rui_version=0.8.0`,
  `compat={"min":"0.7.0","max_exclusive":"0.9.0"}`; `rui.__version__ == "0.8.0"`; a
  temp manifest declaring `rui_compat: ">=0.6.0 <0.8.0"` is refused by the loader:
  `plugin 'sample-plug' is incompatible: it requires RUI >=0.6.0 <0.8.0, but this build
  is 0.8.0; refusing to load it`.
- Bridges: dot2 `4536968` (1.3.2) and xiaoai `0d82250` (0.2.3) both declare
  `rui_compat ">=0.7.0 <0.9.0"`; neither is bumped by this release.

### Known limitations

- L17 (ack echo) is still headless-only: the fix (`82d45d9`) landed at 11:47 local on
  2026-10-28, i.e. **after** the 08:54–09:33 hardware run, so the +850 ms suppression has
  not been exercised on a speaker. The trade itself is deliberate.
- The 0.7.9 intent-layer changes **were** checked on hardware on 2026-10-28 (08:54–09:33
  local, 00:54–01:33 UTC): N1, N3, ACK-1 and ACK-2 are ticked in
  `docs/acceptance-pending.md`. **N2 is the exception** — `b91599a` (d.6) inverted its
  criterion to "matches the end word", so the 09:32 run used the now-void wording and N2
  needs a ~2 min re-check (`停止你好` / `停止播放你好` → stop + close;
  `停止你好吗` / `你好停止` → no match).

## [0.7.9] - 2026-10-27

**Theme: patch release. Two behaviour changes in the intent layer (headless-verified
only - see the honesty note), one log-noise fix, one audio error-reporting fix, plus
the wake-acknowledgement ordering. No contract change, no bridge release: dot2 1.3.1
and xiaoai 0.2.2 stay compatible (`rui_compat >=0.6.0 <0.8.0` unchanged).**

> **真机未验 / not verified on hardware.** The two intent-layer behaviour changes
> below are covered by headless tests only (`uv run pytest`, 1716 passed). They have
> **not** been exercised on a speaker. Hardware verification of 0.7.9 is folded into
> the 0.8 window (`docs/acceptance-pending.md`, items N1/N2/N3/R79-*), together with
> the second same-model dot2 (`docs/backlog-0.8.md` §c). Do not read this release as
> "the intent layer is fixed on device".
>
> **0.7.9 未部署本仓生产**；生产仍是 0.7.8，部署由 9 哥 亲手做。

### 已知变更（有意的行为变更，需 9 哥 知情）

- **结束词 + 音乐在放 → 停歌且关会话**（C1）。以前「有歌时说停止/拜拜/停止播放」只停歌、
  **会话保持**（`media_stop`），这就是「问完星期几说『停止』，它只把音乐停了」的那条分支；
  现在两者一起做（`session_action="stop_and_dismiss"`）。playing 与 paused 都算「在放」——没有中间态。
- **`stop_intent` 只认整句**（C2）。删掉「任意位置子串 + 总长最多 +2 个字」的兜底，
  改为「整句 = 结束词 / 显式别名 / 单个闭集语气词（吧了呀啊嘛）」；疑问语气（吗/呢/么）显式排除。
  影响：`停止你好`、`停止了吗` 不再被当命令（交模型/澄清）；**`请停止`、`我要停止` 这类「句中出现结束词」
  的说法也不再命中**——这是有意收窄，老用户可能会觉得「以前能用现在不理我」。
- **三条别名补进 `ALIASES`**：`停止播放→停止`、`别放了→别放`、`别放了吧→别放`（别名不是结束词表，
  不违反「结束词表保持现状」）；否则收紧容差后这几句最常用的说法会掉进模型。
- **暂停词收窄到两个**（C5，`34e3629`）：`MUSIC_TRANSPORT["pause"]` 只剩「暂停」「停一下」；
  `先停这儿 / 停会儿 / 停住 / 停在这` **不再触发暂停**（交模型），也**没有**被挪进结束词表。
  暂停回复改为「好，暂停了。」。
- **没在放音乐时说「暂停」不再结束会话**（C3，`34e3629`）：回一句「现在没在放音乐。」，
  **会话保持**（`session_action="pause_no_music"`）。以前这两个词同时是结束词，无歌时会被
  `bare_stop` 吃掉、直接把会话关掉。
- **唤醒应答在控制台里被提前**（D-011=A，`6117fb7`）：catalog 的 `ack` 组 `order 200 → 5`，
  **`tier` 未动**。v2「配置 → 语音助手」页里它从第 8 组升到**第 3 组（首屏可见）**；
  旧面板「音频与唤醒」tab 里是第 1 位。要 v2 第 1 位需要改 `tier` 或页内显式排序，属 0.8。

- **`mcp <name>: down` 重连日志降价** (`3ed312f`). A server that stays unreachable no
  longer logs one INFO per reconnect attempt (~15 s apart). First failure, a changed
  failure reason, and a successful reconnect each log one INFO; an unchanged reason
  logs at most once every 5 minutes, everything else drops to DEBUG. `--log-level
  DEBUG` still shows the full reconnect cadence. Threshold lives in
  `src/rui/tools/mcp_client.py` (`DOWN_LOG_INTERVAL_S`).
- **被吞掉的 `ENOTCONN` 现在留一行 DEBUG** (`8321099`). When a plane socket is already
  gone at close time the exception is still swallowed (that is what keeps the real
  refusal reason visible), but at DEBUG level you now see
  `audio plane: socket already gone on close: [Errno 57] ...` instead of silence.
  Nothing changes at the default log level.
- **audio `close()` no longer masks the plane's real error** (`2977c3a`). A child that
  died before the handshake used to turn `audio plane refused: ... RUI_MISSING_TOKEN ...`
  into `[Errno 57] Socket is not connected` in `/api/v1/audio`, and skipped
  `proc.terminate()` on the way out.

### Fixed（进 `main` 未发版）

- **dot2 volume read-back** (`d2c9895`): answer the level the mixer reports, never the
  stale one.
- **「唱首歌」routes to random play** (`e85b4bb`), and a legacy link now explains
  itself instead of dead-ending.
- **控制台把 Home Assistant 与 MCP 服务器分两段** (`91cac91`) — presentation only;
  `GET /api/v1/mcp` still returns HA with `kind: "first_party"`.

## [0.7.8] - 2026-10-27

**Theme: four cards in one release — the TLS install path, per-device plane
visibility, the LX06 plug leaving core, and the v2 console becoming the default.
No API contract change.**

> **0.7.5–0.7.7 ship inside 0.7.8; none of them gets its own tag.** Their exit
> conditions (Done 6 on hardware, the built-in-vs-external dual run, and the
> upgrade/rollback pass) all need the complete 0.7.8 tree and one hardware window.
> Tagging them after the fact would claim a verification that never happened.
> **0.7.8 is the single acceptance node**; this section is the per-version record for
> 0.7.5–0.7.8.
>
> （口径原文：0.7.5–0.7.7 的开发合并进 0.7.8，未单独发版；原因是它们的验收项依赖 0.7.8 的完整代码与真机窗口。）

**Release artifacts:** core **0.7.8** (`pyproject.toml`, `src/rui/__init__.py`,
`uv.lock`); `rui-dot2-bridge` stays **1.3.1** and `rui-xiaoai-bridge` stays **0.2.2**
(no code change in this release).

### 0.7.5 — TLS install path and per-device plane visibility

**Added**

- **TLS material ships with the two-speaker example** (`43d18df`):
  `examples/two-speakers/compose.yaml` mounts `./secrets` at `/opt/rui-secrets`
  (`DOT2_SECRETS_DIR`), `scripts/make_dev_tls.sh` self-signs `server.pem` /
  `server.key`, and the install prompts of all three repos gained the TLS step.
  When dot2's `listen_port` / `tls_port` is on, certs are mandatory: missing material
  gives `audio_plane=false` with `missing TLS material`, not a silent half-start.
- **Per-device plane visibility** (`5e16026`): `GET /health.planes` returns one row
  per enabled device (`device`, `room`, `state` = `running|error|not_started`,
  `reason` = `multi_device_disabled|plane_not_started|audio_not_started`, `error`,
  `multi_device`); the old `audio_plane` boolean stays as the aggregate. `rui doctor`
  warns when 2+ devices are enabled and `multi_device` is off ("only the first
  configured device gets an audio plane; set `multi_device.enabled: true` and
  `max_planes >= 2`"), and `config.example.yaml` carries the example. Contract C-002
  A-8. 7 new tests; the 0.7.5 close-out baseline was **1659/17**.

**Measured** (`258d72a`; `docs/audit/tls-required-2026-10.md`, three matrices × 2 runs)

- dot2 with `tls_port` on and no `secrets/server.pem|key` → `audio_plane=false`,
  first error `missing TLS material`.
- lx06 + dot2 with no TLS and an empty `RUI_DEVICE_TOKEN` → `false`,
  `RUI_DEVICE_TOKEN is empty` (the default single plane starts only the first
  configured device).
- Certs + both tokens, dot2 first → `true`, `dot2 link: wss listening on 8871`.
- Self-signed only proves the audio plane can start; whether the speaker accepts it
  depends on the CA the device trusts, and the original controller certificate cannot
  be recreated.

### 0.7.6 — the LX06 bridge leaves core

**Changed**

- **No in-repo device plug, no adapter alias module.** `plugs/xiaoai/` is gone
  (moved to `tests/fixtures/legacy_lx06`, which is not shipped) and
  `src/rui/adapter_aliases.py` is deleted (`c713916`). The Dockerfile no longer
  `COPY plugs`; `/opt/rui-plugins` is owned by `rui:rui` and its writability is
  checked at build time. The 0.7.6 B1a close-out baseline was **1654/17**.
- **`adapter: xiaoai` migrates on load** (`c713916`): rewritten to `lx06` with a
  deprecation WARNING, surfaced by `/health.adapter_plugins.warnings` and, per device,
  as `legacy_adapter_spelling`. The default `adapter` is now `lx06` (same behaviour,
  no warning). The compatibility module `rui/config/legacy_adapters.py` (old spellings
  plus the implicit 4412 listen-port default) is the only file allowed to mention the
  old names and is deleted in 0.8.0.
- **Vendor wake-word fallback is data-driven** (`c2ed7fd`): the new optional
  `devices[].wake_words_residual` list declares the phrases the device itself says;
  ASR matching early-yields them. **Known behaviour change:** a manifest that does not
  declare the field no longer early-yields vendor phrases — only `is_echo` / `is_noise`
  apply. The built-in lx06 manifest declares three, so its behaviour is unchanged.
  Documented in `docs/socket-plug-v1.md`.
- **54 device-semantics tests moved to the bridge repo** (`300b2a9` / bridge
  `575b443`), byte-identical: credit 5, device-services 7, kws 5, led 18, playback 1,
  turn-audio 6, vendor 11, console-secret 1. Core went 1655/17 → **1612/6**; the bridge
  suite went 44 → **87 passed / 11 skipped** (+54 collected); the 20 generic
  `test_security_fixes` stay in core.

**Verified**

- Core grep gate `xiaoai|lx06|小爱|4412` over `src/**/*.py` is **0 hits** outside
  `legacy_adapters.py` (re-run on this tree).

### 0.7.7 — console audit (C)

**Fixed** (`db179f8`)

- **Element-by-element v2 audit**: **172 interaction rows / 159 usable / 12
  unverified / 0 unusable** (172 = 159 usable + 1 copy-error row + 12 unverified +
  0 unusable), 4 instances, 76 screenshots, no real device and no sound
  (`docs/audit/console-functional.md`). Four copy/validation groups fixed: setup
  first-run copy claiming an empty house, the stale legacy-console hint,
  dead `v2.config.stub*` i18n keys, and seven Chinese validation messages on device
  writes turned into English technical sentences per C-002 §4.4.

### 0.7.8 — the v2 console becomes the default (D)

**Changed** (`efab571`)

- **`web.console` defaults to `v2`.** A fresh install — and **an existing
  `config.yaml` that never wrote a `console` key — now silently serves v2 at `/`**
  (302 to `/v2/`, query preserved). Nothing in the config file changes; only the
  landing page does. `config.example.yaml` and `src/rui/config/template.yaml` now
  spell `console: v2` out. `CONFIG_VERSION` stays 3 and the contract window stays
  `>=0.6.0 <0.8.0`.
- **The 15 old console links answer 302 instead of the old shell**
  (`/panel/overview` … `/panel/music`, plus `/setup`, `/secrets`, `/diagnostics`,
  `/channels`). Mapping table and per-link evidence: `docs/audit/console-functional.md`
  §8.2; implementation `src/rui/web/api/legacy_links.py`. The three v1 pages v2 does
  not have yet (`providers`, `system`, `channels`) land on `/v2/?from=<old path>`,
  which the v2 SPA does not consume today (it redirects to `/v2/devices`).
- **The old console is now `/legacy` and marked deprecated.** A bilingual banner at
  the top says it is removed in 0.8.0; the shell is otherwise unchanged. With
  `web.console: legacy`, `/` and all 15 old links keep answering the old shell — that
  is the one-release rollback.
- **Docs**: `docs/deploy.md` §3.8 rewritten for the new default; README zh/en heroes
  both show the v2 devices page.

**Added**

- **Tests**: `tests/test_legacy_links.py` (the 15-path matrix plus query strings, tab
  sub-paths, unknown panel ids and the `legacy`-mode 200s) and an updated
  `tests/test_console_switch.py` (default v2, rollback, `/health.console` in both
  modes, shipped config examples).

### Evidence (final tree)

- house-ear **1642 passed / 6 skipped**; `ruff check .` green; `cd web && npm run
  typecheck && npm run build` green; `git diff --check` clean.
- Gates: 0.7.5 task-9/10/11 **PASS + VERIFIED**; 0.7.6 task-12/13/14 **PASS +
  VERIFIED**; 0.7.7 audit **PASS + VERIFIED** (reviewer-audit rounds R1/R2 closed);
  0.7.8 task-17 reviewer-d **PASS** + verifier-tests **VERIFIED** (`efab571`).
- Rollback verified live: with `web.console: legacy`, `/health.console = legacy` and
  `/` plus all 15 old links return 200 with the old shell (no redirect).

### Known limitations

- `providers`, `system` and `channels` have no v2 page in 0.7.8; their old links stop
  at the device page with `?from=` in the URL (marker only, not consumed — audit §8.9).
- Unchanged from 0.7.4: cue-echo edges, the single-session runner, non-recallable
  goodbye tails, per-room wake words, concurrent multi-device untested.
- Music credentials on a non-writable path now return **409** with a fixable message (was 500; fixed in 0.7.8)
  (conditional); v1-only gaps (phrase editor, provider cards/audition, music playback
  buttons, system/web/security fields, channels, tool registry, device probe) stay out
  of v2 for now; the setup id-collision editor is unreachable in the normal flow.
- From 0.7.6: a third-party manifest that omits `wake_words_residual` may let vendor
  phrases into a turn (see the behaviour change above).

## [0.7.4] - 2026-10-26

**Theme: P0/P1 fixes. One new status code (413); no other contract change.**

### Fixed

- **rui#18 - request bodies are bounded before parsing.** A pure ASGI middleware
  rejects a declared `Content-Length` over the cap (1 MiB default,
  `RUI_MAX_REQUEST_BYTES`) with **413**, and counts streamed bodies as they arrive
  so a chunked upload cannot grow memory past the cap either. Small bodies still
  reach auth first: missing/bad token stays **401**, invalid JSON with a valid token
  stays **422**. The 31 v1 handlers were not moved or rewritten.
- **rui#17 - `_greet` no longer blocks the event loop.** The wait for the plane's
  socket now yields every 10 ms, and the handshake `read_frame()` takes the remaining
  greet deadline as one absolute timeout (header + payload share it). A child that
  accepts the socket and then drops it (Errno 57 / reset / short read) is retried
  within the budget; if it exited, the error keeps its exit code and the last stderr
  lines. Measured: ticker 875 turns/s while waiting; cold-start overhead stays within
  the <=50 ms budget (repeated runs 1.5-3.6 ms).
- **rui#28 - the WeChat channel survives one bad message.** The getupdates cursor is
  saved in `finally` (a bad message no longer replays forever), voice downloads are
  capped at 10 MiB with a warning, and `pilk`/`ffmpeg` decoding runs in
  `asyncio.to_thread` instead of blocking the channel loop.
- **rui#25 / xiaoai#1 - reconnecting resets the player.** Replacing the speaker
  socket now resets `_playing` / `consumed` / `sink_generation`; a `play()` that was
  suspended in `start_play` cannot resurrect the old generation, and events from the
  replaced socket cannot advance the new player. Mirrored in
  `rui-xiaoai-bridge` **0.2.2** (version bumped; code only, no contract change).

### Changed

- `docs/contracts/C-002-console-v2-api.md` A-7 documents the 413/401/422 boundary.
- `rui-xiaoai-bridge` is **0.2.2** (reconnect reset, mirrored); `rui-dot2-bridge` stays **1.3.1** (no code change).
- No contract/window change. 413 is the only new status code; the runtime
  behaviour changes are the four fixes above. `rui_compat` stays
  `>=0.6.0 <0.8.0`.

### Evidence

- house-ear **1652 passed / 17 skipped**; rui-xiaoai-bridge **13 passed**;
  rui-dot2-bridge **145 passed**; `ruff` green in all three.
- Reverse mutations: removing any guard makes its targeted test fail (413 cap,
  cursor `finally`, `to_thread`, reconnect reset, suspended-play generation,
  replaced-socket events).

### Known limitations

Unchanged from 0.7.3: cue-echo edges, single-session dialogue runner, non-recallable
spoken goodbye, per-room wake-word isolation, untested concurrent multi-device
behaviour. New in 0.7.4: the WeChat **streamed** over-limit path is not covered by a
test (only `Content-Length` is); `getupdates` responses have no size cap; the 413
response carries the usual security headers except `Permissions-Policy`.

## [0.7.3] - 2026-10-26

**Theme: test baseline and reproducibility. No behaviour change.**

### Added

- **Coverage is now produced by every CI test job.** `pytest-cov` is in the `dev`
  extra; the Python 3.11 / 3.12 test legs upload `coverage.json`,
  `pytest-report.xml` and `python-version.txt` as `coverage-<py>` artifacts.
  `scripts/coverage_report.py` turns them into the package table and the
  machine-readable `docs/audit/coverage-baseline.json`; the 0.7.3 baseline is
  **80.95% line / 69.55% branch** (local Python 3.13, 120 files).
- **An independent `rui plug-test` smoke** runs in CI against
  `tests/fixtures/sample-plug` (a minimal manifest + fake adapter). It does not
  depend on `plugs/xiaoai`, which is scheduled for removal in 0.7.5.
- **Batch-1 unverified items are now automated tests**: HE-03 (a null
  `first_response_ms` stays "evidence insufficient", never 0), HE-12 (the
  source-run `plugs/` fallback authorises an in-process adapter) and D2-06
  (dot2 `stop_playback` on an active stream must not swallow the next reply).

### Changed

- Test counts are reported in one canonical form: collected / passed / skipped /
  xfail. For the 0.7.3 tree that is **1651 / 1634 / 17 / 0** for house-ear,
  **145 passed** for rui-dot2-bridge and **9 passed** for rui-xiaoai-bridge.
- `docs/audit/unverified-inventory.md` gained the per-package coverage ledger
  with a three-way gap classification, 26 real-device protocols (command /
  criterion / record table), and the eight document-contradiction resolutions.
- No runtime behaviour change. `rui_compat` stays `>=0.6.0 <0.8.0`; both bridges
  keep their current versions (1.3.1 / 0.2.1).

### Evidence

- Coverage: 80.95% line (18429/22767) / 69.55% branch (5158/7416); CI artifacts
  at `d21bb5f` reported 76.59% (3.11) and 77.59% (3.12) on their own interpreters.
- `ruff` green in all three repos; full suites green (numbers above).
- CI run at `d21bb5f`: `test (3.11)`, `test (3.12)`, `image` and the workflow all
  succeeded; `coverage-3.11` / `coverage-3.12` artifacts exist.

### Known limitations

Unchanged from 0.7.2 (see `docs/release/v0.7.2.md` §已知限制): cue-echo edges,
single-session dialogue runner, non-recallable spoken goodbye, per-room wake-word
isolation, untested concurrent multi-device behaviour.

## [0.7.2] - 2026-10-26

### Fixed

- **ack cue no longer reaches ASR.** The gate matches the cue envelope across the skip
  window *and* the hold buffer, learns the device's playback lag (EMA, updated only on
  first-pass hits), retries a narrow `EMA±200 ms` window when the first pass misses, and
  requires silence right after the cue (`_quiet_after`) so a TTS reply is never mistaken
  for it. Clipped captures fall back to a conservative `cue+1000 ms` excision. Measured on
  the device: cue leakage 0/10 (previously 13 of 26 turns).
- **Playback state and turn ownership are scoped per room.** One room playing music used to
  reject every other room's wake with `lost_playing` (18 rejections in six minutes of
  two-room sampling). Cross-room wakes are now admitted, `cross_echo` still guards a second
  plane inside the playing room, and `turn_open` carries the room that owns it.
- **Our own stale speech is dropped when a wake arrives.** A plain wake that landed while
  the goodbye TTS was still synthesised left those chunks queued, so the next ASR segment
  began with our own voice. The plane now flushes queued speech on an accepted wake, blocks
  further `PLAY_PCM` until that turn's utterance lands, and clears the block on every gate
  close path (idle, timeout disarm, our-own-audio disarm). Farewell synthesis no longer
  clears the abort flag, so an aborted goodbye stays aborted.

### Changed

- Release tooling: `rui_compat` window stays `>=0.6.0 <0.8.0`.

### Known limitations

- Cue echo can still reach ASR on a cold start with no clipping and >800 ms device latency,
  or when the user overlaps the cue by less than 110 ms.
- The dialogue runner is single-session: a wake on one device aborts a reply being
  synthesised for another.

## [0.6.0] - 2026-10-26

The device-agnostic core: rui names no speaker any more. The LX06 adapter became an image
plug with the same manifest loader as the Dot 2, the core audio defaults are neutral, and a
device's manifest, the global config and the per-device override merge into one effective
audio block that every consumer (plane, AEC, doctor) reads.

### Added

- rui-device/v1 `audio_defaults` (capture, playback, ack_echo_ignore_ms): the bottom layer of
  a three-layer merge (device default < global config < devices[].audio).
- Image plugs under /opt/rui-plugins are imported from their runtime.entrypoint and listed
  beside each other; RUI_PLUGINS_DIR adds roots without replacing the image directory.
- The setup wizard asks which device to configure when more than one plug is installed
  (409 plug_required carries the list); a single plug auto-selects.
- /api/v1/devices[+id] expose the merged per-device audio and the explicit override paths.

### Changed

- Core capture defaults are neutral (16000/1ch/0ref); the LX06 manifest declares
  48000/8ch/6mic/2ref. Existing configs keep their effective values (golden tests).
- Every output is routed by room: session/LED follow the turn, reminders go to
  multi_device.default_output, music starts where it was asked and transport follows the
  owner; stop only releases the owner from its own room.
- save_config no longer materializes neutral audio defaults over the plug layer.

### Removed

- src/rui/device/xiaoai and the v0 reference plugin package. `adapter: xiaoai` still resolves
  to the same plug through adapter_aliases.py (one minor, gone no earlier than 0.7.0).

## [0.5.3] - 2026-10-25

Stabilization release for the first multi-device production install (LX06 + Echo Dot 2).
Every item below was measured on that install and has a regression test. No config-schema
break (one nullable per-device field), no plug-contract change.

### Fixed

- **A device's own audio settings are the ones that play.** The plane's playback path read
  the global `audio.playback.rate`, so a per-device override (Dot 2 = 48 kHz) was raised at
  speak time: the turn finished, the ring said `speaking`, no sound. The play path, the wake
  cue and the pacer now use the device's effective audio; the pre-rendered cue is resampled.
- **The house-wide session speaks in the room that spoke.** The idle goodbye and its LED used
  the primary client, so the LX06 said farewell for a Dot 2 turn. Session speak / LED / stop
  follow `turn_device`, and a new wake cancels the goodbye instead of racing it.
- **A goodbye can no longer lock the house.** A goodbye between wake detection and admission
  disarmed the new wake gate, so `on_wake_idle` never ran and `turn_open` stayed true: every
  later wake was rejected. The idle loop skips while a wake is in flight or a turn is open,
  and clears `turn_open` after the goodbye.
- **A speaker no longer answers itself.** The wake cue used a global 1.2 s echo window; a
  device-AEC speaker leaks longer, and the Dot 2 turned its own `在呢。` into a request.
  `devices[].audio.voice.ack_echo_ignore_ms` lets a device declare its own window (Dot 2:
  2500 ms), and the goodbye now sends `reply_finished` so the post-playback mute arms.

### Added

- **Doctor rows for the three failures this install hit**: `audio_rates` (wire rate vs
  effective playback rate per device), `channels` (gateway last error, e.g. a Telegram 409
  from a second poller), `console_token` (`console-token.txt` vs `web.auth_token`).
- `docs/deploy.md`: multi-device template (rooms, per-device rate/AEC/mute, `max_planes`,
  `default_output`, one bot per poller).

## [0.5.2] - 2026-10-25

Patch release for the install path and for satellite management in the console. Found
by dot2's 0.5.1 clean-install retest (#15) and by adding Dot 2 satellites next to the
LX06 in the device manager (#7). The audio/arbitration code and the plug contract are
identical to 0.5.1: no config-schema change, no migration.

### Fixed

- **The documented `curl … | sh` install no longer dies halfway (dot2 #15).**
  `docker compose exec -T` attaches stdin, and with a piped installer stdin *is* the
  rest of the script, so the health probe ate the remaining bytes: the run printed
  `healthy`, then `Syntax error: Unterminated quoted string`, exit 2, and no
  `console-token.txt`. Both exec sites (the probe and the `rui token` read) now
  redirect `</dev/null`. A regression test runs the shipped `install.sh` from a pipe
  against a fake docker that drains stdin and asserts exit 0, the closing block and a
  0600 token file; a second test fails if any future `docker compose exec` forgets the
  redirect. The one-liner is unchanged, because it now works.
- **A connected satellite is `ready` even when its token lives outside rui (dot2 #7).**
  `GET /api/v1/devices/{id}/status` used to report `ready=false` + `token_missing` for
  a device whose adapter owns the credential (the dot2 plug reads a 0600 file). A live
  link is proof that authentication succeeded, so the row now carries
  `token_managed_by_adapter: true` instead of a blocking error.

### Added

- **The connection test follows the transport.** An inbound speaker (LX06, Dot 2)
  dials us and runs no SSH on the Dot 2, so probing `:22` used to fail a perfectly
  healthy satellite. The host row is now `not_applicable` for inbound devices (the
  port and the live link are the evidence), the test waits up to 3 s for a device that
  is still dialling in after a save/restart, and the response reports `transport`,
  `listen_port` and `waited_ms`. Outbound adapters keep the TCP + SSH-banner probe.
- **Adding a second speaker prefills a free id.** The console used to start every new
  device at `living`, which collides with the main speaker; it now picks `living`, then
  `satellite`, then `satelliteN`.

## [0.5.1] - 2026-10-25

Patch release for the install path, found by dot2's clean 0.5.0 install. There is no
runtime, config-schema or plug-contract change: the audio/arbitration code is identical
to 0.5.0. `install.sh` no longer executes backticks from its `.env` template (which could
start an unrelated compose project from the caller's directory) and never prints the
console token; `fetch-models` retries transient 429/5xx, the installer fails non-zero
instead of booting without ASR, and `rui doctor` reports local ASR/VAD/KWS readiness.
The README also gains a copy-paste agent-install prompt and refreshed 0.5.0 console
screenshots.

### Added

- **Agent-friendly install entry point.** The README now carries a copy-paste prompt
  (zh + en): a command-capable agent is told to read the public docs first, install with
  the official `install.sh` at a pinned tag, ask the operator for the required settings
  and keys, and verify with `rui-healthcheck` plus one real turn.
- **Doctor reports model readiness.** A `models` row checks the local sherpa ASR model +
  tokens, the configured VAD file and the KWS `tokens.txt`; missing models surface as
  `degraded` instead of only a WARNING in the install log (dot2 #14.3).

### Fixed

- **`install.sh` wrote `.env` from an unquoted heredoc.** Backticks in the template
  comments were command-substituted during install - on a machine whose CWD was another
  compose project that started it - and the printed setup output leaked the console token
  to stdout. The template is now a quoted heredoc (values appended by `printf`),
  `rui setup` never prints the token, the installer still persists it to
  `console-token.txt` (0600), and tests pin the byte-identical template + no canary
  execution + no token on stdout (dot2 #13).
- **`fetch-models` could fail silently and the installer still exited 0.** A transient
  hf-mirror 429 aborted the ASR download with only a WARNING, so the container looked
  green while ASR was missing. Downloads now retry 429/5xx up to 4 times (1/2/4 s
  backoff, 404/401 fail fast to the next mirror); the installer fails non-zero after
  printing remediation, with `RUI_SKIP_MODELS=1` as the documented opt-out (dot2 #14).
## [0.5.0] - 2026-10-25

Multi-device 0.5: one control plane, one audio plane per room, one arbiter for the
house. Config v3 adds per-device audio overrides and the `multi_device` block (off by
default), Phase 2 wires the fleet and the wake candidate -> admit/reject chain, and the
console gains per-plane health plus the restart-effect knobs behind it. Adapter and
manifest contracts, `drain_wakes()` timing and the `DeviceWake` six fields are
unchanged; downstream plugs only need their `rui_compat` ceiling raised to `<0.6.0`.

### Added

- **Config v3, Phase 1 — per-device audio overrides, still one active plane.**
  `devices[].audio` can now override the global `audio` block for one device: `aec`
  (including the new `aec.who: device｜controller｜both｜none`), `voice.followup_delay_ms`
  / `voice.followup_replay`, `capture` and `playback.prebuffer_ms`. Unknown keys are
  rejected (`extra="forbid"`; EQ/limiter stay in plug `params`, not in the core).
  `effective_audio()` merges an override over the global defaults and re-validates the
  merged block, so a partial capture override that no longer adds up fails at load time
  instead of crashing the plane. Frozen decisions: `docs/multi-device-design.md` §9.
- **`multi_device` config block (parsing and validation only).** `enabled` (default
  `false`), `max_planes` (default 4), `default_output`, and `arbitration`
  (`source_priority` default `[device_wake, kws]`, `candidate_merge_window_ms` default
  400, `duplicate_window_ms` default 2000, `room_priority`, `prefer_playing_room`,
  `last_active_window_s`, `on_no_speech`). Unknown ids in `default_output` /
  `room_priority`, duplicate priorities and `room`-less devices with
  `multi_device.enabled=true` are rejected; `config_warnings()` reports legal-but-wrong
  combinations (double AEC cancellation, and more enabled devices than `max_planes`).
- **v2 configs stay v2.** A v2 file loads with identical behaviour and is only written
  as `version: 3` once a v3-only field is actually used, so rolling back to a 0.4.x
  image keeps working without editing the file.
- **Read-only config view on the System page.** `GET /api/v1/config/raw` returns the
  effective config (schema defaults + `config.yaml`) as YAML with credential values
  replaced server-side by `<redacted>` and named in `redacted_keys`; the System panel
  shows it in a read-only monospace block with copy, path + mtime and a redaction note.
  The endpoint is GET-only — there is no write path — and the payload is capped at
  200,000 characters with a valid YAML notice instead of a truncated document.
  `tests/test_config_raw.py`.
- **Home Assistant "Test connection" accepts the token typed in the form.** The probe
  used to resolve only the *stored* secret, so a first-time setup — or a replacement
  token — always failed with 401 until the operator saved a token they could not test.
  `POST /api/v1/mcp/test {"base_url", "token"}` now probes with the one-shot token and
  never stores it; an empty token still falls back to `RUI_HA_TOKEN`. The console passes
  the typed value, so "fill in address + token → Test" works before saving.
  MCP transport failures are also unwrapped for the UI: `401 Unauthorized` instead of
  `unhandled errors in a TaskGroup (1 sub-exception)`.
- **Save-and-reconnect keeps its update semantics.** The HA card's save writes the URL
  and (when typed) the token, replaces the stored secret, closes the old session and
  reconnects — that card is the one place to rotate the HA token. New regression tests
  pin it: a replacement token overwrites the old one, leaving the token field empty
  keeps the stored value, and a new URL + token apply in a single save.

- **Phase 2 — one audio plane per room, arbitrated in the control plane.** With
  `multi_device.enabled: true` the app starts a `PlaneFleet` (one supervised child per
  enabled inbound device, each with its own socket and crash watchdog) and wires every
  plane into the turn path through `_wire_plane()`; `false` keeps the 0.4.x
  single-plane path unchanged. Planes report `wake_candidate` without arming anything;
  the `WakeArbiter` (room priority -> source group -> score margin -> last active ->
  device order) and `WakeCoordinator` answer `wake_admit`/`wake_reject`, so one house
  has one turn and one output owner. Barge is only accepted from the playing owner; a
  second device in the same room is suppressed as cross-echo; the same word inside
  `duplicate_window_ms` is a duplicate; a plane that gets no verdict drops its
  candidate (`admit_timeout`) instead of starting a turn on its own; `max_planes`
  refuses extras with an explicit warning (no queueing), and one crashed plane does not
  take the other rooms down.
- **Per-device audio that the plane can win with.** `devices[].audio.aec.who` decides
  which side cancels echo (`device`/`controller`/`both`/`none`), and
  `devices[].audio.voice.post_playback_mute_ms` drops the microphone for a window after
  the speaker drains (default 0; the plane skips the follow-up gate when the window is
  at least `followup_delay_ms`). Barge during playback is unaffected.
- **Health and status per plane, without breaking the old contract.** `/health` keeps
  every old top-level field (`devices` is still the count) and adds `audio_devices[]`
  rows (`running`, `speaker_connected`, `restarts`, `error`, `wake_candidates`,
  `wake_admitted`, `wake_admit_timeouts`, `wake_rejected`, `utterances`) plus a
  `multi_device` block. A room whose plane is down produces
  `plane_not_running`/`fleet_start_failed`/`over_max_planes` warnings instead of the
  old `multiple_enabled_inbound` notice, and `/api/v1/audio` gained a `planes` map.
  `speaker_connected` means *any* room is connected, so one dead room does not mark the
  container unhealthy. Device rows, status and the connection test read that device's
  own plane instead of the aggregate.
- **Console visibility for multi-device, and the knobs behind it.** The Health page now
  has a Multi-device card: "off" with the 0.4.x single-plane explanation when
  `multi_device.enabled` is false, and otherwise one row per plane (running/connected,
  restarts, wake candidates/admitted), the devices refused over `max_planes`, and any
  fleet start error. The config directory gained the `multi_device` group (`enabled`,
  `max_planes`, `default_output`, source priority, both arbitration windows, room
  priority, last-active window) and per-device `audio.*` overrides (AEC ownership and
  switch, post-playback mute, follow-up window/replay, playback prebuffer, capture
  layout) - all restart-effect. `arbitration.mode`, `prefer_playing_room` and
  `on_no_speech` are deliberately not exposed: the arbiter freezes them
  (score/true/end) and a switch that changes nothing is a fake knob. An empty override
  means "inherit", and writing one into a device with no audio block now creates it.
- **Healthcheck names the root cause instead of suggesting a restart.** A downstream
  adapter that cannot be built (`device_errors > 0`), a fleet that failed to start
  (`multi_device.error`) and a specific room whose plane is down (`audio_devices[]` with
  the child's error) each produce their own sentence; only a plain dead plane still
  suggests `docker compose restart rui`. The success line also reports
  `planes=up/total connected=n`. `GET /api/v1/doctor` gained a
  `container_healthcheck` row that re-evaluates the same `/health` body, so the doctor
  and the container probe cannot disagree (dot2 #12 C3/C4).
- **Upgrade hints that cannot silently move the pin.** Re-running `deploy/install.sh`
  over an existing `.env` now prints the pinned `RUI_IMAGE` and says that upgrading is
  an explicit edit (`docker compose pull && docker compose up -d`); the final summary
  points at `docker image prune -f` for old rollback images (dot2 #12 C2/K2).
- **Doctor annotates `container_runtime: none`** as expected inside the image (it ships
  no docker CLI) instead of telling the operator to install Docker in the container
  (dot2 #12 K5). The 0.4.2 release note's leftover verification placeholder comment is
  gone (K1).
- **Downstream upgrade guide.** `docs/deploy.md` §7.1 covers upgrading an install with
  an out-of-tree adapter/plug: healthcheck failure semantics, `rui_compat` alignment
  with a compatibility table, customised compose files, vanilla ↔ downstream image
  switching (idle `DOT2_*` variables are harmless on the vanilla image), and the one
  allowed volume conversion helper (dot2 #12 C1/C5).

### Changed

- **`rui_compat` ceiling raised to `<0.6.0`** for the built-in LX06 plug, the example
  plug and their tests: without it a 0.5.0 core would refuse the bundled plug and fail
  to start. The matching semantics (version range) are unchanged.

### Fixed

- **`PlaneHandle` lost `on_wake_idle` on every plane restart.** The callback was set on
  the facade but never re-applied to the new connection, so the "wake heard nothing"
  path went silent after a crash; `on_wake_candidate` is rebound the same way now.

<!-- Add changes here as they land; move them under the next version at release time. -->

## [0.4.2] - 2026-10-25

Console IA Phase 4 cleanup. Navigation now comes from one backend manifest, the seven
compatibility-only panels and their redirects are retired, and the homepage
documentation is slimmer. No config-schema change and no audio/runtime change.

### Added

- **One navigation manifest.** Every `PanelSpec` from `GET /api/v1/panels` now carries
  `nav: {section, order, hidden, icon}` (sections `run` / `configure`). The rail and
  the page kicker read it directly, and a panel this build does not know is placed by
  the backend instead of being guessed into "configure". Nav icons are validated
  against the shell's `AppIcon` vocabulary.
- **Multi-device design document.** `docs/multi-device-design.md` is a design-only
  proposal for rooms, the primary/satellite question and double-wake arbitration:
  current 0.4.1 code facts, four options, a recommended hub arbitration with one audio
  plane per device, a `config v3` sketch, a phased plan and open decisions. No runtime
  change ships with it.
- **README 0.4.2 section** covering the Phase 4 result and the new navigation model.

### Changed

- **Eleven panels, seven retired.** The compatibility-only declarations
  `assistant-session`, `speaker`, `devices-admin`, `tools`, `mcp`, `foundation` and
  `phrases` were removed after their one-release window in 0.4.0/0.4.1. Their content
  lives on the merged destinations (voice-assistant, devices, integrations, system);
  `/panel/credentials` and `/secrets` remain for the write-only ledger.
- **Legacy console URLs are gone, intentionally.** Old `/panel/<legacy-id>` URLs
  (including `/panel/settings`) no longer redirect; they render the console's
  "No such panel" state with a link back to the overview. The removed top-level
  aliases (`/overview`, `/devices`, `/logs`, `/assistant`, `/settings`, `/voice`,
  `/models`, `/tools`) fall through the SPA catch-all to `/panel/overview`. Update
  bookmarks; this ends the compatibility window announced in 0.4.0.
- **Providers page renamed** from "外部资源 / Providers" to
  "模型与语音服务 / Models & voice services"; the id `/panel/providers` is unchanged.
- **Duplicate navigation code removed.** The id→group map, hidden-panel set, active
  alias map and per-id title overrides (`PANEL_NAV_GROUPS`, `HIDDEN_NAV_PANELS`,
  `NAV_ACTIVE_ALIASES`, `NAV_GROUPS`, `PANEL_TITLE_KEYS`, `GROUP_KEY`) are deleted.
  `web/src/nav.ts` keeps only the section titles and the three hand-written utility
  routes (channels, diagnostics, first-run setup).
- **README slimmed** from 512 to 366 lines: install, wizard and troubleshooting detail
  moved to `docs/first-run.md`; the screenshot refresh is deferred to the next content
  pass. The compact docs index and the 0.4.2 "what's new" block replace the long page.

### Fixed

- **Navigation drift.** A panel's rail placement, page kicker and title used to be
  declared in three places (backend group, frontend mapping, i18n override), so a
  backend change could leave the rail and the page disagreeing (the Logs case found in
  the IA audit). The backend `nav` object and backend titles are now the only inputs.
- **No setting lost its home.** The fields the deleted pages used to edit still render
  on the merged pages: assistant/session on the voice-assistant reply tab, speaker
  hardware on the devices audio tab, process/console/SSRF in `/panel/system`, and
  `system.log_level` on the logs page.
- **Unknown nav icons** now fail the panel contract tests instead of silently rendering
  a blank icon in the rail.

### Upgrade notes

- **From 0.4.1:** no config migration, no chown and no config-schema change. Pull and
  recreate; this release changes the console and docs only. Audio, providers, music and
  device behaviour are unchanged.
- **Bookmarks:** the old panel URLs now show "No such panel" (intentional). Use
  `/panel/voice-assistant` (assistant/session/phrases), `/panel/devices`
  (status/audio/manage), `/panel/integrations` (tools/MCP/HA) and `/panel/system`
  (process/console/SSRF).
- **API:** `/api/v1/*` paths are unchanged. `GET /api/v1/panels` gains the additive
  `nav` object and no longer lists the seven retired panel ids; integrations that
  enumerate panels should read `nav` and tolerate a smaller list.
- **Rollback:** the config is unchanged, so a 0.4.1 image can run the same data
  directory; old bookmarks regain their redirects if you roll back.

## [0.4.1] - 2026-10-25

Compatibility polish for the 0.4.0 console: Jellyfin and Emby join the music-source
registry, the first-login console token can be recovered without scrolling back through
the install log, and external plugs such as dot2 report the port and token environment
they actually use.

### Added

- **Jellyfin and Emby music sources.** A second protocol family
  (`JellyfinSource`, MediaBrowser HTTP API) behind two presets: `jellyfin` (API key
  only in the `Authorization: MediaBrowser Token="…"` header) and `emby` (API key in
  `?api_key=`). Search, random, favourites, star/unstar, library paging, album songs and
  stream/resolve; the adapter discovers the user id itself and needs only `url` plus a
  write-only `api_key`. The registry grows to seven presets. Verified against live
  Jellyfin 12.3.0 and Emby 4.10.2 servers.
- **Console-token recovery (issue #4).** After the service is healthy,
  `deploy/install.sh` saves the console token to `~/rui/console-token.txt` (mode 600).
  A rerun keeps an existing file byte for byte and warns when it no longer matches the
  running token. The 401 login panel now shows the `rui token` /
  `cat console-token.txt` recovery commands and links to the deploy docs.
- **Follow-up window controls.** `audio.voice.followup_delay_ms` (0–10000 ms, default
  1000) and `audio.voice.followup_replay` (default on) are editable in the config
  center. Defaults keep the measured LX06 behaviour; a device-AEC plug (no reference
  channel, RUI AEC off) can use a longer window and skip replaying the gated tail.
- **Cold-start device_wake re-probe.** When a trusted plug manifest declares
  `wake.source=device` but the first probe ran before the device dialled in, the audio
  plane now re-probes in the background (bounded: 12 attempts, 5 s apart) and arms the
  v1 device-wake path once the adapter proves it.
- **First-run documentation.** New bilingual `docs/first-run.md` covers the device
  checklist, install commands, the 8-step wizard, where each parameter comes from,
  connect troubleshooting, post-install commands and common failures. README rewritten
  around the current 0.4 state.

### Changed

- **Device management reports effective plug defaults.** `GET /api/v1/devices`,
  `/{id}`, `/{id}/status`, `/{id}/connect` and the setup wizard's `connect` evidence
  resolve `listen_port` and `token_env` the same way the adapter builder does:
  explicit config → plug manifest `runtime` → loader-cached default → LX06 literals
  (4412 / `RUI_DEVICE_TOKEN`). Dot2 therefore reports `8870` / `DOT2_EM_TOKEN` even
  when the config leaves both unset.
- **Token rotation writes to the effective environment name**, so rotating a dot2 device
  stores the secret under `DOT2_EM_TOKEN` rather than `RUI_DEVICE_TOKEN`.
- **Port-conflict detection uses the effective manifest port.** Dot2 on 8870 and an LX06
  on 4412 no longer report a false conflict; two devices resolving to the same manifest
  port still conflict.
- **README, deploy, runbook and music docs refreshed** for the current release, the
  pinned image version, the token file and both music protocol families (auth styles,
  credential slots, cache isolation, test commands).

### Fixed

- **First-login console token:** the only copy used to be one line in the install log; it
  is now persisted 0600 and reachable from the 401 dialog.
- **Dot2 status/connect/rotation** fell back to `RUI_DEVICE_TOKEN` and `4412` when the
  plug manifest carried its own runtime defaults.
- **Dot2 cold start** could leave RUI's own KWS in charge for the whole process lifetime
  when `probe()` only reports device_wake after the device connects.
- **Follow-up gate** no longer hard-codes a 1 s delay and a 2 s capture cap, so a
  device-AEC plug's longer window is not truncated.
- **Jellyfin cache collisions:** the PCM cache key includes the preset id, so Emby's
  small integer ids cannot collide with Subsonic numeric ids when switching sources.

### Security

- **Jellyfin/Emby API keys are write-only** and sent only where the preset requires
  (header for Jellyfin, query string for Emby); error text and logs redact `api_key`
  and query strings, and no GET returns the value.
- **The console token file is mode 600**, is never overwritten by a rerun, and is
  explicitly separate from the device token in `.env`.

### Upgrade notes

- **From 0.4.0:** no config migration and no chown. Pull and recreate; the new
  `followup_*` fields stay absent until set and their defaults preserve the LX06
  behaviour (1 s gate, replay on).
- **Existing installs that want the token file:** create it once with
  `cd ~/rui && docker compose exec -T rui rui token > console-token.txt && chmod 600 console-token.txt`.
  Re-running `install.sh` will not overwrite an existing file and warns on a mismatch.
- **Music:** Subsonic credentials keep working. Switching to Jellyfin/Emby changes the
  credential slots to `url` + `api_key`; the old `user`/`password` fields are
  ignored for those presets.
- **Dot2:** update the plug and restart the service so the manifest runtime defaults are
  picked up. Set `audio.voice.followup_delay_ms` to about 2500 (or turn
  `followup_replay` off) if device-side AEC leaves residual echo in the follow-up
  window.
- **Rollback:** back up `config/config.yaml` first. A 0.4.0 build ignores the new
  optional fields; `console-token.txt` is not read by 0.4.0.

## [0.4.0] - 2026-10-25

Console information-architecture refactor Phase 3. The console can now create providers,
configure Subsonic-family music sources, and manage integrations, credentials and system
settings without hand-editing YAML.

### Added

- **Provider presets and creation.** `GET /api/v1/providers/presets`,
  `POST /api/v1/providers`, `POST /api/v1/providers/probe` and
  `GET /api/v1/providers/status`. 11 LLM presets (OpenAI, DeepSeek, SiliconFlow,
  Moonshot, Zhipu, DashScope, OpenRouter, Groq, Ollama, LM Studio, vLLM), 4 ASR/TTS
  presets (Volc ASR, local sherpa, Volc TTS, XFYUN TTS), and custom OpenAI-compatible
  endpoints. Creation writes the catalog block and chain entry atomically; the draft
  probe writes nothing.
- **Provider readiness and inline keys.** Ready/missing reasons instead of a green badge
  that lies; keys are write-only and held in memory; preset credential names are
  server-owned and custom providers may only reference `RUI_*` environment names.
  Provider changes report `restart_required: true`.
- **Music source presets.** `music.source` is a preset id backed by one Subsonic-family
  adapter. Five presets ship: Navidrome, Airsonic, Gonic, Ampache and custom Subsonic.
  `GET /api/v1/music/sources` and `POST /api/v1/music/test` expose the registry and a
  real `ping` test.
- **Write-only music credentials.** Per-source credential slots are stored in
  `lan_media.json` (flat JSON, `0600`, atomic writes) and are never returned by a GET.
  The console submits only changed fields. Legacy inline `music.url/user/password`
  values are migrated to that file on startup.
- **Integrations, credentials and system pages.** `/panel/credentials` is a typed,
  write-only audit page with consumer metadata; `/panel/system` owns the 13
  runtime/console/outbound fields; MCP servers accept non-secret `headers` and a
  write-only `token_env`; Home Assistant keeps its first-party card and safety policy.

### Changed

- **Console IA Phase 3 completes the two-group navigation.** Merged pages from 0.3.4
  gain their final model, music, credentials and system content; legacy routes and panel
  ids stay reachable as redirects/aliases for at least one release.
- **Provider cards** can set a provider as primary or remove it from the chain by
  editing the chain draft; the normal stage save applies it.
- **Unknown music sources** now load but are reported as `enabled=false` /
  `source_known=false` instead of showing a healthy panel for a service that never
  started.

### Fixed

- **Built-in/reference/example plug manifests** declared a `rui_compat` ceiling below
  0.4.0, so a 0.4.0 image failed to load its own plugs at startup. The ranges now accept
  0.4.x (re-cut).
- **Plugin-compatibility CI fixtures** hard-coded the same sub-0.4.0 ceiling, causing 26
  failing tests; the re-cut derives the ceiling from the running version and adds a
  regression test so future bumps cannot silently break it.

### Security

- No new security-relevant changes in 0.4.0. The release ships the 0.3.2 P3.5 baseline
  unchanged: non-root uid/gid 1000, read-only root filesystem, `cap_drop: ALL`,
  `no-new-privileges`, outbound SSRF guard, and pip-audit/Trivy scanning in CI.

### Upgrade notes

- **From 0.3.2 – 0.3.4:** no data migration and no chown. Pull and recreate; existing
  provider chains, devices and music credentials are untouched.
- **From 0.3.0 – 0.3.1:** images before 0.3.2 ran as root. Back up, then run
  `sudo chown -R 1000:1000 config data` once before recreating.
- **From 0.2.x:** run the same one-time chown. Config is already v2 and the
  house-ear → rui rename is done.
- **From 0.1.x:** run the chown and follow
  [`docs/migration-house-ear-to-rui.md`](docs/migration-house-ear-to-rui.md). A
  `version: 1` config is migrated to v2 in memory and persisted as v2 on the first
  save; the upgrade itself does not rewrite it.
- **Rollback:** back up `config/config.yaml` first. When returning to a pre-0.3.2 root
  image, set `RUI_UID/RUI_GID=0` in `.env`; the volumes do not need to be chowned
  back.

## [0.3.4] - 2026-10-25

Console Phase 2: duplicate pages are merged, with legacy routes redirected.

### Added

- **Merged console pages:** Devices & Audio (`/panel/devices`), Tools & Integrations
  (`/panel/integrations`), Music (`/panel/music`) and Voice Assistant
  (`/panel/voice-assistant`), each with tabs/sections instead of a second destination.
  Legacy routes redirect and old panel ids remain for one release.

### Changed

- **CI builds each architecture on a native runner** (PR #10). arm64 is no longer
  emulated through QEMU, so a tag publish is no longer a 40-minute job that the next
  push cancels.

### Upgrade notes

- No config or API changes. Old panel URLs and ids keep working during the transition.

## [0.3.3] - 2026-10-25

Console IA Phase 1 (P0 fixes) plus the first round of Socket/Plug audit fixes.

### Added

- **Two-group console navigation:** *Run* and *Configure & Maintain*; the setup wizard
  only appears while setup is incomplete.
- **Independent audits** for console IA and for the Socket/Plug contract and
  implementation (`docs/console-ia-audit.md`, `docs/socket-plug-audit.md`,
  `docs/socket-plug-impl-audit.md`).

### Changed

- **Models & voice services** replaces the wizard's step-01 label (step id unchanged).
  Readiness filtering means an unready provider is no longer tried on every sentence;
  the saved provider fallback chain is preserved.
- **Music** no longer hides credentials and switches when playback is disabled;
  `music.source` is a validated select and an unknown value is shown honestly.
- **Socket/Plug audit follow-ups:** trust gate before in-process loading, capability
  intersection with the device manifest, volume contract, DeviceWake behaviour, audio
  supervision, WebSocket limits and timestamp alignment.

### Fixed

- Provider save no longer drops the fallback chain.
- XFYUN is no longer logged as a failure on every sentence when another TTS provider is
  configured; readiness is reported once and the fallback is used silently.
- A disabled music page is no longer a dead end.

### Upgrade notes

- No config migration. Review the new two-group navigation; old routes still resolve.

## [0.3.2] - 2026-10-25

Security release P3.5: outbound SSRF guard, non-root container, CVE scanning.

### Added

- `make security`, `make security-deps` and `make security-image` targets.
- `docs/security.md`, `docs/security-scanning.md`, `scripts/security-pip-audit.sh`.
- `RUI_UID`/`RUI_GID` overrides and an ownership migration in `deploy/install.sh`.

### Security

- **Outbound SSRF guard** (`security.outbound`) is applied to Home Assistant, MCP,
  music and web-search URLs, and to redirects. It always refuses cloud metadata
  (169.254.169.254 et al.), link-local, multicast, unspecified and reserved addresses;
  hostnames are resolved immediately before the request and every A/AAAA answer is
  checked. Defaults allow private LAN peers and refuse loopback.
- **Non-root container:** the image runs as `rui` (uid/gid 1000), with a read-only root
  filesystem, `/tmp` as tmpfs, `cap_drop: ALL` and `no-new-privileges`. State moved
  under the data volume; `install.sh` chowns `config/` and `data/` to the runtime
  user.
- **CVE scanning in CI:** pip-audit over `uv.lock`, Trivy image scan with SARIF upload,
  and dated allowlists (`security/pip-audit-ignore.txt`,
  `security/trivy-ignore.yaml`).

### Upgrade notes

- **One-time, for existing installs:** back up, run
  `sudo chown -R 1000:1000 config data`, then pull and recreate. A root-owned volume
  otherwise fails to write and the container restart-loops.
- **Rollback to a root image:** set `RUI_UID/RUI_GID=0`, recreate; no chown back.

## [0.3.1] - 2026-10-24

Official downstream adapter hook for external device bridges.

### Added

- `RUI_ADAPTER_PLUGINS` environment variable and `rui.adapters` entry points, with
  fail-soft loading. External projects can ship a device adapter out of tree instead of
  forking rui core; the standard targets bridges such as dot2.
- Updated `docs/socket-plug-v1.md` and plug test-kit guidance for the hook.

### Upgrade notes

- No config change for built-in adapters. Plugin authors should read the adapter
  contract before moving an adapter out of tree.

## [0.3.0] - 2026-10-24

Socket/Plug v1 and the first real device-management release.

### Added

- **Socket/Plug v1:** manifest v0/v1, the `device_wake` contract, audio
  policy/metrics/trace, the `rui plug-test` contract kit, the built-in LX06 reference
  plug (`plugins/rui-plugin-xiaoai`) and an example plug. Sidecar-declared plugins fall
  back to RUI KWS because v1 has no standardised wake RPC.
- **Device management:** status, enable/disable, token rotation, port-conflict detection,
  multi-device warnings, and the matching console UI.

### Changed

- Docker base pinned to Debian **bookworm**; trixie dropped `libspeexdsp1`.

### Upgrade notes

- Existing device configs keep working. External adapters should target the v1 contract;
  the official out-of-tree hook arrives in 0.3.1.

## [0.2.2] - 2026-10-24

Installer and setup-wizard hardening; the console starts using human labels.

### Added

- End-to-end install-flow audit (`docs/install-flow-audit.md`).

### Fixed

- **Setup wizard P0/P1:** prepare-before-token deadlock, server-driven setup nudge,
  any-provider key evidence, local ASR test, loopback connect URL, truthful connection
  status, speaker parameter controls and local-key tests.
- **Console labels:** human labels instead of raw environment/config identifiers.
- **Installer:** re-runnable install, pinned-source fallback, model repair, and a
  failing health check now exits non-zero.

### Upgrade notes

- Re-running `deploy/install.sh` is now safe and repairs a partial install; otherwise
  pull the image as usual.

## [0.2.1] - 2026-10-24

Setup-wizard UX redesign.

### Added

- Provider catalog with **real key smoke tests**, connect hints, grouped key labels and
  a prepare checklist (issue #5).
- Read-only connect card and speaker parameter controls.

### Changed

- The wizard is reorganized as a provider table: **use → provider → credentials →
  voice**. Fresh LX06 templates now enable the LED ring by default (previously off).

### Upgrade notes

- No schema migration. If the wizard was left incomplete, re-run it. Existing configs
  keep their current LED setting.

## [0.2.0] - 2026-10-24

**BREAKING:** rename from house-ear to rui, and config schema v2.

### Changed

- **Renamed** the Python package, CLI, environment prefix, host paths and container to
  `rui`; the GitHub repository and image were already named `rui`. Legacy
  `HOUSE_EAR_*` variables resolve through a one-release compatibility bridge.
- **Config schema v2:** the flat audio block is split into `capture`/`voice`/
  `playback`, and provider blocks become catalog entries behind named chains. A v1 file
  is migrated in memory on load and persisted as v2 on the first save.
- Default paths move to `/var/lib/rui` (data), `~/.config/rui` (config),
  `~/.local/state/rui` (state) and `~/rui` (install dir).

### Upgrade notes

- Follow [`docs/migration-house-ear-to-rui.md`](docs/migration-house-ear-to-rui.md):
  update `.env` keys and `config.yaml` paths, replace the compose file, then recreate.
- The environment fallback is one-way: an old image does not understand a config that
  only uses `RUI_*` names, so keep a pre-upgrade copy of `config/`.

## [0.1.3] - 2026-10-24

Model delivery and doctor reliability.

### Added

- VAD and KWS models are baked into the image; own model URLs can be supplied through
  `RUI_ASR_URL`/`RUI_VAD_URL`/`RUI_KWS_URL`.
- `rui doctor` probes the sherpa runtime and surfaces real KWS/VAD load failures
  (issue #2).

### Changed

- The official `latest` image now contains the baked models.

### Fixed

- Model downloads force IPv4 (the NAS has no global IPv6 route).
- The ghproxy mirror is tried before a stalling GitHub release asset; host proxy
  variables are forwarded to `fetch-models`.
- `install.sh` fails cleanly when a container already exists and pins the compose
  project directory.
- Removed the dead hf-mirror VAD URL and prefer the working mirror.

## [0.1.2] - 2026-10-24

Install wizard UI, device management and playback pacing.

### Added

- Manifest-driven 8-step installation wizard UI.
- Device management page: parameter validation, plug resolution, connection test and
  delete.
- Configurable playback prebuffer, gap metrics and ordered start/stop.

### Fixed

- Cloud-only ASR chains no longer require local ASR files.
- The device manifest class is exposed through its documented alias.
- CI cancels superseded runs per ref.

## [0.1.1] - 2026-10-24

Infrastructure reliability, the first Socket & Plug slice, and a licensable ffmpeg.

### Added

- **Socket & Plug v0:** manifest schema and loader, built-in LX06 plug, device params and
  the evidence-driven setup-step API.
- **LGPL-only ffmpeg** built from pinned source, with `THIRD_PARTY.md` and README
  credits.
- EchoMuse device-management research and the install-wizard design.

### Changed

- Image publishing is gated on the test job (P1.5).

### Fixed

- Empty or whitespace-only config now raises `ConfigError`/uses last-good instead of
  schema defaults followed by an `insecure_bind` crash loop.
- Legacy `DEEPSEEK_API_KEY` is accepted when the new LLM key is missing.
- Model downloads have bounded timeouts (connect 10 s / read 60 s) and fall back quickly.
- Acceptance output defaults to the mounted data directory, so results survive a
  container recreate.

## [0.1.0] - 2026-10-24

First public release, after all four v1 acceptance gates passed.

### Added

- **Acceptance baseline:** cold install to first reply in 423 s (gate ≤30 min); wake
  19/20 at 80% volume with music playing (gate ≥19); barge-in 24.2 / 64.5 / 205.2 ms,
  max ≤300 ms; public repository and GHCR.
- **Audio plane:** 16 kHz capture (6 mic + 2 reference channels from 48 kHz hardware),
  AEC (ERLE ≈31.6 dB), KWS/VAD, turn segmentation with preroll and idle goodbye,
  barge-in, playback arbitration and the LED ring.
- **Providers and tools:** SenseVoice local ASR and Volc ASR, DeepSeek LLM, XFYUN and
  Volc TTS, provider chains, Home Assistant, generic MCP, and Navidrome music.
- **Console 2.0:** install wizard, device management, phrasebook editor, providers,
  tools/MCP/HA, channels, turns/memory/logs and the config-recovery banner.
- **Channels:** Telegram and WeChat iLink gateway, managed gateway process and text chat
  API.
- **Operations:** `rui doctor`, backup/restore, last-good config recovery, acceptance
  tooling, runbook and deploy docs.
- **Packaging:** MIT, public repo, multi-arch GHCR `0.1.0`/`0.1`/`latest`.

Quality at release: 749 passed / 17 skipped, ruff clean, web typecheck/build green.

[Unreleased]: https://github.com/yes999zc/rui/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/yes999zc/rui/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/yes999zc/rui/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/yes999zc/rui/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/yes999zc/rui/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/yes999zc/rui/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/yes999zc/rui/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/yes999zc/rui/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/yes999zc/rui/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/yes999zc/rui/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/yes999zc/rui/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/yes999zc/rui/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/yes999zc/rui/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/yes999zc/rui/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/yes999zc/rui/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/yes999zc/rui/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/yes999zc/rui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yes999zc/rui/releases/tag/v0.1.0