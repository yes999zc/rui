# HANDOFF 2026-10-26 · 0.6.0 已发布 / 控制台 v2 M1+M2 已合并

phase-close: task-16

## 会话身份
- 模式：A（双仓库：rui 上游 + rui-dot2-bridge 下游），单会话单任务板
- 仓库：`$HOME/AI_Workspace/01_Code/projects/house-ear`（rui）、`$HOME/AI_Workspace/01_Code/projects/rui-dot2-bridge`（dot2）
- 分支/commit：rui `main` @ `7bc342d`，tag `v0.6.0` @ `adc4c2b`；dot2 `main` @ `9c696cd`

## 版本与契约
| 项 | 值 | 证据 |
|---|---|---|
| 上游 rui 发布 | tag `v0.6.0` @ `adc4c2b`；镜像 `ghcr.io/yes999zc/rui@sha256:abef1f276c3c21e9155d97c98913ee6c2e98fdfe842e43b90f64a5db6c12b241`（tags `0.6.0`/`0.6`/`sha-adc4c2b`/`latest`，amd64+arm64）；Release <https://github.com/yes999zc/rui/releases/tag/v0.6.0> | `docker-publish` run 40209595988 success；`gh release view v0.6.0` |
| 下游 pin | dot2 的 `deploy/downstream/Dockerfile` 与 compose 默认值 pin 上述 digest；`docs/canary-0.6.0-official-image.md` 记录 arm64 + x86_64 金丝雀；`docs/contract-report-0.6.0.json` 增 `upstream_pin` 块 | commit `9c696cd` |
| 生产（NAS 168） | **v2 预览（M1–M8）** `rui-dot2:0.7.0-pre5-7bc342d`（底座 `ghcr.io/yes999zc/rui@sha256:e9889d2a…42a7`，main @ 7bc342d）；`/`、`/v2`、`/v2/{conversation,logs,memory,run,rooms,devices/add,setup}` 均 200；`/health` 0.6.0 / cv3 / devices 2 / errors 0 / 双 plane connected / `ack.wav_exists=true`；`/api/v1/memory` 空文本 422 生效；回滚：`pre4-642d40b`、`pre3-825b9e7`、`pre2-9e094e7`、`pre1-4ebf104`、`0.6.0-adc4c2b` 全在 | 构建需 `DOCKER_BUILDKIT=0`；备份 `/vol1/rui/backup/20261026-0726-v2m78/` |
| 契约 C-001 | `rui-device/v1`（manifest `audio_defaults` + `ack_echo_ignore_ms`） | `docs/socket-plug-v1.md` §3.2；`src/rui/config/schema.py` |
| 契约 C-002 | 控制台 v2 `/api/v2/*` 24 端点，1300 行（含 A-6），**冻结 PASS** | `docs/contracts/C-002-console-v2-api.md`；task-6 记录 |
| 兼容边界 | rui `>=0.6.0, <0.7.0`；0.6.x v1 唯一新增字段仍是 `/api/v1/turns` 每条可选 `device_id`；另有两处已记录修正：`POST /api/v1/memory` 非法输入改 422（不再 500/静默截断）、`/turns` 的 `stages.*.elapsed_ms`/`attempts` 改为真实值（既有字段不变，`attempts[]` 新增 `elapsed_ms`），见 C-002 A-6 | 契约 §10.1 / A-6 |

## 已完成（含证据）
- **0.6.0 发布**：内核去厂商命名（74 处文案/注释中性化 + 守卫测试 + scripts P0 修）→ tag → CI 多架构镜像绿 → digest 入 release notes → Release 建立。tag 内不含 v2：`git ls-tree -r --name-only v0.6.0 | grep -cE 'web/src/v2|api/v2'` → 0。
- **内核彻底剥离小爱**：`docs/audit/xiaoai-decoupling.md`（分类 575 命中、web 27 条转交表、独立建库 topic 结论「0.7 再议」）；守卫 `tests/test_kernel_decoupling.py`；门禁 1349/17（当时口径）+ ruff clean。
- **控制台 v2 M1+M2 合并**（`d550d95`）：设计 `docs/design/console-ia-v2.md`（708 行，PASS）、契约 C-002（1193 行，PASS）、后端 `src/rui/web/api/v2_api.py`（24 端点）、前端 `web/v2.html` + `web/src/v2/**`（三段导航、`/run` 全屋、`/devices` 按房间、设备详情四 tab：概览/测试与动作/活动/连接）。旧控制台文件零改动；`npm run build` 双 dist。独立 verifier 用真实进程 + 两设备 + headless Chrome 验到 POST /test、activity type 过滤、reveal/rotate 429 冷却、`/v2` 深链、旧 `/` 不变；全量 1434 passed / 17 skipped。
- **写盘不变量统一**（`b1d2e0c`）：新增 `src/rui/web/api/device_write.py`，v1（enable/disable、PUT、DELETE）+ v2 + 向导共用「repair → 校验 → 端口冲突」；`_write_devices` 在 `save_config` 前兜底，任何写者无法绕过。修前 v1 可在多设备房子写出 `load_config` 失败的配置（已复现），修后正常用例响应逐字节一致、非法用例 422 且文件不变。`led.set` 加入 live-gate（断连 409 `device_not_connected` + hint）。全量 **1445 passed / 17 skipped**，ruff clean。
- **生产切换 + hot 层清理**：切到官方 digest 底座，`/health` 双 plane connected、`dot2 registered`、无 Traceback；hot1–hot4 与 `/home/yes999zc/hot` 已删，回滚镜像保留。
- **下游 repo**：pin 改动 + canary 文档已提交并推送（`9c696cd`）。
- **v2 启动门修复**（`d89129c`）：`/meta` 未就绪前不挂载页面（`meta.ts` 初值 + `metaSettled`，`App.vue` 只在该门通过后渲染 `RouterView`）。修前详情页 5 个资源各请求 2 次、错误 token 仍发 7 个请求；修后每资源 1 次、错误 token 只剩 1 个 meta 请求。独立评审 PASS。
- **控制台 v2 M3**（`d73b0a1`）：设备配置 tab（房间/主机/启用/音量/唤醒词/端口）+ 字段级 diff（未改字段不发、`listen_port` 留空表示继承、清空发 null）+ 保存前确认「重建全屋音频面」+ 新增设备（409 device_exists / 422 port_conflict）+ 删除需手输 id。E2E 抓包验证 PATCH body 恰好 `{"room":...}` / `{"volume_target":...}` / `{"listen_port":null}`。
- **控制台 v2 M4**（`9e094e7`）：音频 tab，37 个 leaf 的 effective + 来源 + 四层原始值；16 个可覆盖 leaf 字段级 PATCH；「恢复继承」发 null 后重读校验来源（失败保持红色、重试重跑校验，不再静默吞错）；unresolved 插头给中性提示；全局影子提示。E2E 用注入 500 验证红/绿状态切换。
- **控制台 v2 M5**（`825b9e7`）：`/v2/rooms` 房间与全屋视图；多设备策略（enabled/max_planes/default_output/room_priority）字段级 PATCH + 明确「house 写入是 service 范围、不自动重启」并给音频面重启按钮；全局音频编辑（37 leaf、explicit 标记、null 清除、覆盖计数 N 台、插头 manifest 覆盖警告）；422 dotted path 行内标红、409/429 可读；脏稿离开确认 + `beforeunload`。E2E 抓包验证 `{"multi_device":{"max_planes":5}}`、`{"audio":{"capture":{"rate":48000}}}`、`null` 清除、422 标红、`POST /house/audio/restart` 恰好一次。
- **控制台 v2 M6**（`642d40b`）：八步向导（keys/models/prepare/params/wake/connect/test/verify），步骤/动作/完成度全部服务端驱动；create 前后续步骤禁用且不发请求；create 只用返回 id；draft 冲突可改 id 继续（真实 E2E 抓出的阻塞缺陷已修）；verify.say 出声前二次确认；逻辑失败不再显示绿色完成。`/v2/setup` 首装入口 + 设备列表空态链过去。
- **控制台 v2 M7+M8**（`7bc342d`）：M7 全屋概览“异常优先”（设备离线/未启用/配置问题集中列出并可点入）+ 房间行 + 设备页活动 tab；M8 `/v2/conversation`、`/v2/logs`、`/v2/memory` 三页 v2 重写（过滤/复制/空态/错误，过渡期复用 v1 `/turns`、`/logs`、`/memory`，不 import 旧 views）。同轮后端修正两项 v1 缺陷并写入契约 C-002 A-6：`POST /api/v1/memory` 的 `add` 空/纯空白文本与 `add`/`confirm` 超长文本改 422 且不落库（修前 500 或静默截断）、`/api/v1/turns` 的 `stages.*.elapsed_ms`/`attempts` 改为真实计时（ASR 完整转写、LLM TTFT、TTS 多句累计）；代码评审已 PASS，commit 后补。
- **生产 v2 预览（M3+M4）**：`rui-dot2:0.7.0-pre2-9e094e7`，healthy、双 plane connected、无 Traceback。
- **生产 v2 预览**：为让 9 哥 早上能评测新后台，生产临时切到 main 构建 `rui-dot2:0.7.0-pre1-4ebf104`（`/v2` 可用，旧 `/` 不变；`/health` 逐字段一致、无 Traceback、设备 25s 回连）。**这是可控的预览切换，不是正式发布版本**；回滚一行见 §下一步。

## 未完成任务
| 项 | 状态 | 卡点 / 下一步 |
|---|---|---|
| 控制台 v2 M9a/M9b + M10 | pending | 设备增删改 + 配置 tab（M3）、音频覆盖编辑（M4）、房间/全屋 + 全局音频（M5）、八步向导 add/edit（M6）、运行类页面（M7–M8）、配置页组（M9a/M9b）、0.7 切默认（M10）。设计文档 §7 有每步验收 |
| verifier 记录的偏差（非阻断） | pending | ① `led.set` 已修；② disabled 设备的错误码不自洽（`device_not_connected` / `capability_unsupported` / `device_disabled` 并存）需产品裁决；③ C-002 §7.1 启动门禁在浏览器端未成立（`meta.ts:11` 初值 + `App.vue:225` 提前渲染导致资源重复请求） |
| v2 DELETE 的端口检查口径 | pending | v2 DELETE 仍 `check_ports=True`，与 v1 不一致；建议传 `check_ports=False` |
| ack 余量 | **已处理** | 生产 `ack.max_ms` 已 600→800（2026-10-26 06:21），`ack.wav` 550 ms 正常生成；若改 ack 文案仍要复看 `/health.ack.note` |
| data 卷 dot2 manifest | **已处理** | 已用 `install-plug-manifest.sh` 同步为 1.2.0；因镜像自带同字节 builtin，数据卷副本是冗余并会让 `/api/v1/plugs` 多一条失败条目，已移到 `/vol1/rui-backups/plugs-dot2-echomuse-disabled-*`（tar 备份可恢复）。**下游 README 里「data 卷清单必须有」的说法在 0.7 预览版已过时，需改** |
| AudioPanel 3 条 MINOR（评审） | pending | `saveErrorRaw` 死状态、save 未清 `restoreUnresolved`/`lastRestorePath`、`mapServerPath` 内联 ack 映射（应复用 `audio.ts` helper） |
| v2 `GET /devices/{id}` 缺「存储值」字段 | pending | 只有 effective `listen_port`，配置页无法区分「显式 8870」与「继承默认 8870」；建议 C-002 addendum 加 `listen_port_stored`（additive） |
| 控制台 v2 剩余页面 | pending | M7–M10：运行类页面（对话/日志/记忆）、配置页组、0.7 切默认与 `/legacy` |
| v2 外壳健壮性（评审 backlog） | pending | `/meta` 返回 200 但 body 空 → 页面空白无兜底；`/meta` 挂起无超时；未来若运行期 reload meta 需给 `metaSettled` 短路 |
| 生产临时跑 main | pending | 预览验收后二选一：回滚到 0.6.0 底座，或把 v2 正式发 0.6.1/0.7.0 并重新 pin |
| `PUT /api/v1/config/values` | pending | 可写 `devices.N.*` 但不走共享不变量（全量校验保证不写坏文件，只是不享受 repair） |
| 真机语音验收（两轮告别路由） | pending | 必须 9 哥 本人；0.6.0 release notes 已写明这一项未勾 |
| 第二台 Dot2 共存 | pending | 硬件未到货 |
| 生产磁盘 ~11.3GB 可回收 | pending | 0.1.x–0.4.x 老镜像 + 3 个未挂载 volume；要不要清由 9 哥 定 |

## 下一步（按顺序，接手方可直接执行）
0. **生产回滚一行**（若要放弃 v2 预览）：`sed -i 's#image: rui-dot2:0.7.0-pre1-4ebf104#image: rui-dot2:0.6.0-adc4c2b#' /vol1/rui/rui-dot2-override.yml && cd /vol1/rui && docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d --force-recreate`
   **v2 入口**：`http://<NAS>:8080/v2`，首次打开要输入与旧后台相同的 console token（`/vol1/rui/console-token.txt` = config `web.auth_token`）。
1. 门禁基线：`pytest -o addopts='' -q` → 0 failed / 17 skipped（当前 1445 passed）；`ruff check src tests` clean；`cd web && npm run typecheck && npm run build` → 双 dist。
2. 核对生产：`/health`（version 0.6.0、devices=2、errors=0、双 plane connected）；镜像 `rui-dot2:0.6.0-adc4c2b`；回滚镜像在位。
3. 控制台 v2：先让 9 哥 打开 `/v2` 试；再按 M3 → M4 → M5 → M6 推进写操作（每步独立可回滚，`meta.features` 可关）。动手前先修上面偏差 ③（meta 启动门禁），否则每个页面会重复请求。
4. 处理偏差 ②（disabled 设备错误码优先级）——建议契约层裁决：`device_disabled` 优先于 capability，随后同步测试与 UI 文案。
5. 等 9 哥 真机两轮验收；通过即 0.6.0 验收闭环，否则 0.6.1。
6. 0.7 规划：`/` → `/v2`、旧壳挂 `/legacy`、删 `src/rui/adapter_aliases.py`、评估小爱独立建库（审计报告 §4 的触发条件）。

## 约束与禁区
- 真机语音验收必须 9 哥 本人（家人睡觉时不能测）。
- 生产变更允许（已授权），但每次先备份 `/vol1/rui/.env`、`config/`、override，并保留回滚镜像。
- 不打印/不提交 token、密码、证书、内网 IP（secret-scan 会扫 docs：绝对家目录路径与 RFC1918 内网地址都会让检查变红）。
- 不 fork rui 源码；dot2 只做 out-of-tree adapter + 下游镜像层。
- **已产出的 tag/镜像不移动**（v0.6.0 已出镜像；审计报告的 6 条 MINOR 修订跟 main 走、不进 tag）。
- 飞牛上 docker build 用 `DOCKER_BUILDKIT=0`。

## 已知风险与未决问题
- **混合房子必须写每设备覆盖**：全局 `audio.capture`/`ack.echo_ignore_ms` 会盖过 Dot2 manifest 默认 → `docs/deploy.md §7.4` + 生产配置。
- **adapter 静音窗**：`DOT2_PLAYBACK_GATE_MS=400` 是生产规避值，默认值仍待真机再定。
- **`lost_playing`**：告别播放瞬间唤醒会被仲裁拒绝。
- **`adapter_aliases.py` 只能活到 0.7**；web 面 27 条小爱字样仍在（旧控制台，随 v2 退役）。
- **v2 未切默认**：旧控制台仍是 `/`，v2 在 `/v2`；0.7 才切。
- **`/health` 离线即 unhealthy**：`rui-healthcheck` 在无音箱连上时 exit 1，属上游既有语义（切换后等设备回连约 12–60s）。
- **第二台 Dot2 行为** *(unverified)*：无硬件。
- **NAS 密码建议轮换**：2026-10-26 一次运维子代理的 `sudo -S` 曾把密码回显到本机会话日志（未写入仓库/未外传），建议轮换 `yes999zc` 的 NAS 密码并更新 `01_Code/device-inventory.md`。

## 关键文件
- `docs/releases/v0.6.0.md` —— digest 与未验证项。
- `docs/contracts/C-002-console-v2-api.md` —— 控制台 v2 契约（冻结）。
- `docs/design/console-ia-v2.md` —— IA v2 设计（含 M1–M10 与 0.7 切默认 runbook）。
- `src/rui/web/api/v2_api.py` —— `/api/v2/*` 24 端点。
- `src/rui/web/api/device_write.py` —— v1/v2/向导共用的写盘不变量（repair → 校验 → 端口冲突）。
- `web/v2.html`、`web/src/v2/**` —— v2 控制台（`/run`、`/devices`、`/devices/:id` 四 tab）。
- `docs/audit/xiaoai-decoupling.md` —— 小爱剥离审计 + 独立建库 topic。
- dot2：`deploy/downstream/`、`docs/canary-0.6.0-official-image.md`、`docs/contract-report-0.6.0.json`。
- 内部：`04_Docs/rui-2026-10-26-night-report.md`（给 9 哥 的夜间报告）。

## 核验命令（接手方先跑一遍，再相信上面的版本号）
```bash
cd $HOME/AI_Workspace/01_Code/projects/house-ear
git status --short && git log --oneline -3 && git describe --tags
.venv/bin/python -m pytest -o addopts='' -q
.venv/bin/python -m ruff check src tests scripts
cd web && npm run typecheck && npm run build
gh release view v0.6.0 --json url,tagName
# 生产（paramiko + sudo -S，见 04_Docs 交接文档 §3）：
#   docker ps --filter name=rui --format '{{.Image}}|{{.Status}}'
#   curl -s http://127.0.0.1:8080/health
```

## 没做的事 / 不要相信的地方
- **没做**：真机两轮告别路由（等 9 哥）；第二台 Dot2（无硬件）；v2 M3–M10；0.7 切默认。
- **不要相信**：`/tmp/rui-v2-evidence/`（mock harness）与 `/tmp/rui-m2-verify/`（真实进程但无真机）里的截图当生产证据。
- **不要相信**：`.34` 上的实例当作生产状态（它是金丝雀机，跑 `rui-dot2:0.6.0-official`）。
- **不要相信**：v0.6.0 tag 内的 `docs/audit/xiaoai-decoupling.md` 是最新修订（6 条 MINOR 修订在 tag 之后进 main，tag 不移动）。
- **未验证**：合成客户端证明管线，不证明真机麦克风/AEC/TLS 重连。

> 本会话停在：0.6.0 已发布并切到官方镜像底座；控制台 v2 的 M1（只读）+ M2（设备动作）已合并进 main 并通过独立评审与真实进程 E2E；写盘不变量已统一到 v1/v2/向导。下一个会话第一步：读本文件 → 跑门禁与 `/health` → 修偏差 ③（meta 启动门禁）→ 按 M3 起继续 v2。
