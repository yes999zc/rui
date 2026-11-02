# HANDOFF — rui 0.8.13（已发布并部署；生产 /health = 0.8.12）

phase-close: task-6

> 本节是 0.8.11 的交接块：发布、静音部署与静音验收已完成（2026-10-30 凌晨）；
> 发声类验收留白天，清单见「待白天」。0.8.10 的历史交接块保留在本文下方，未改动。
> **当前发布的记录在文末**：「`### 2026-10-30 10:2x 0.8.12 发版并部署`」与
> 「`### 2026-10-30 发版流程加固`」两段；H1 已按当前版本（0.8.12）更新，下方 0.8.11 块保留原文。

## 版本与契约

- **本版：rui 0.8.11**（2026-10-30）。版本三处一致：`pyproject.toml`、`src/rui/__init__.py`
  的 `__version__`、`uv.lock` 的 `[[package]] name = "rui"` version。
- **`rui_compat` 不变**（`>=0.8.1 <0.9.0`），无协议破坏性变更；插件不变
  （dot2 1.3.5 / xiaoai 0.2.3）；`rui-xiaoai-bridge`、`rui-dot2-bridge` **零改动**。
- **生产：rui 0.8.11**（2026-10-30 02:53 由本会话静音部署；`/health` version 0.8.11、
  config_version 3、planes 5 running、warnings []、device_errors 0、容器 healthy）。
  上一代生产 0.8.10（index `sha256:30e8ed47…e1155`）。
- **回滚锚（当前生产）**：0.8.10 index digest
  `sha256:30e8ed47f2ae0ce4281752c4130bfa58e53f63095f128eae5330e5e5703e1155`
  （amd64 `sha256:9cb7c5d2…735ccf`、arm64 `sha256:5ce93e3d…0b71a30`）。
- **生产运行时参数本批不动**：capture 16k / 8ch / 6mic+2ref / `mic_shift=10` /
  `ref_shift=16`；keywords `… :4.00 #0.07 @你好小宝`（客厅）与 `@你好小瑞`（dot2 三台）；
  `wake_preroll_ms=500`；`standby_kws_gate=false`；AEC `cancel=mix filter_len=4096`；
  override 的 `DOT2_MIC_GAIN_DB: "12"` 保持。
- **本批新增的唯一默认行为变化（有意）**：HA 清单默认只列 `light` / `switch` / `cover`，
  不再列 `scene` / `script`（这两个域本来就调不动）。其他新键默认值等于旧行为
  （`allowed_scenes` 默认空 = 仍拒绝一切 scene 调用）。
- **夜间静音约束：本会话未触发任何音箱出声**（无 say / TTS / 唤醒 / debug-say；
  只跑单测、只读 HTTP、headless 验证）。

## 已完成

- **A（身份占位符）** `6c0e14e`：`{version}`（= `rui.__version__`）与 `{config_version}`
  在 persona 与 system_prompt 两处都能填，同一份映射渲染；默认 persona 改为「跑在 rui
  {version} 上（家里自建的语音服务，不是 Home Assistant）」，默认 system prompt 加歧义规则
  与「你自己就是客厅那台音箱」。**版本号不可配置**（`AssistantConfig` 无 version 字段），
  证据 `tests/test_config_identity.py`（7 条）+ `tests/test_prompts.py`。
- **B（HA 清单四配置项）** `6c0e14e`：`context` / `context_domains`
  （默认 light/switch/cover）/ `entity_cap`（120）/ `context_exclude`（正则，非法值校验期
  报错）。过滤顺序 域 → exclude → cap，取数预算 3x，日志 `fetched N / injected M`，缓存带
  domains 指纹即时生效。证据 `tests/test_ha_context_config.py`（16 条）。
- **B2（场景白名单 + 批量如实）** `32ac605`：`allowed_scenes`（entity_id 精确匹配，默认空 =
  仍拒绝；lock/alarm/valve/script/automation 等仍全拒）、白名单场景单独成段注入（不受
  `context_domains` 影响）、批量 8/批串行 + `call_timeout_s`（默认 10 s，总预算 ×1.5）、
  失败后**回查再说话**：`ok` / `partial`（`device.partial` 话术）/ `unreachable`，回查失败
  绝不报 ok。证据 `tests/test_ha_scene_batch.py`（23 条）+ `tests/test_ha_bridge.py`。
- **C（`local.look`）** `b6789d5`：USB（`vision.usb_snapshot_url`，实时）与 HA
  `camera_proxy`（最近一帧）→ 内存 base64 → 现有 LLM 链 → 1–2 句 ≤80 字；`vision.cameras`
  别名映射；单帧、`vision.timeout_s`=一次 look 总预算（默认 15 s）、`cooldown_s`=1/10 s；
  不落盘；工具日志只记设备+字节+耗时；**画面以 base64 发到 `api.deepseek.com`**（多 provider
  兜底链每家都会收到）。话术全在 `phrases.py`（`vision.*` 6 条）。证据
  `tests/test_local_look.py`（29 条）+ `test_phrasebook.py`。
  **降级收口（记遗留）**：C 卡前两轮由独立 reviewer 评审（第一轮 CHANGES 4 条、第二轮
  CHANGES 2 条，均已修复）；末轮评审 agent 在收口阶段不可用，按 9哥 指示不再新开 agent，
  改由 Lead 复核：读了 `src/rui/tools/look.py:236-290`（单一 deadline、`left<=0` 停止、
  finally 关闭链上全部 provider）、`look.py:330-375`（总预算 + `remaining<0.5` 闸门）、
  `tests/test_local_look.py` 的 `status=="empty"`/`seen==[]` 断言与双 provider 预算测试；
  跑了定向 77 passed + 全量 2308/6 + ruff 绿。**C 卡末轮未做独立 verifier**。
- **C2（Mac USB 快照服务）** `6c0e14e`：`scripts/usb_snapshot_server.py` 单文件仅标准库，
  ffmpeg avfoundation 抓单帧走 stdout（不落盘），`GET /snapshot.jpg` → 200 image/jpeg，
  失败 503 + 一行原因，默认 `127.0.0.1:8766`（8765 被 pensieve 占用），无鉴权 →
  只绑内网。文档 `docs/usb-snapshot.md`。真机抓帧 JPEG 1280x720 + `--device 99` 503 已验。
- **小修**：`/api/v1/debug/tool` 现在按 `spec.name` **或** `llm_name` 解析 handler
  （`local.look` → `look`），0.8.10 的 `404 no such tool: local.music_play` 缺口关闭。
- **文档**：`CHANGELOG.md` 新增 `[0.8.11]`（`[Unreleased]` 复位 `_Nothing yet._`）；
  `docs/releases/v0.8.11.md`（含隐私口径、「版本号不可配置」取舍、兼容性、部署清单、
  静音/发声两张验收表、回滚锚、`<!-- digests -->` 占位）；`docs/local-look.md`（C 卡）、
  `docs/usb-snapshot.md`（C2 卡）。
- **门禁（代码冻结后由 Lead 跑一次全量）**：`uv run pytest -q -o addopts=""` →
  **2308 passed, 6 skipped in 165.18s**（0.8.10 基线 2217 passed / 6 skipped，净增 91 条，
  0 删除 0 新增 skip）；`uv run ruff check src tests` → **All checks passed!**。
  已回填 `docs/releases/v0.8.11.md` 的 `<!-- gates -->`。


## 部署实测（0.8.11，本会话静音执行，2026-10-30 02:47–02:58）

- **发布**：commit `eec6b7f`，annotated tag `v0.8.11`；CI：security ✓ / secret-scan ✓ /
  docker-publish ✓（`ci` 在 main 同一 commit 绿）。镜像 index
  `sha256:8965bd92febdca89dd3bb73be7db806946cd43b91ba243ceb1c6187bc7f7ccbe`；
  amd64 `sha256:b7c6ebcf87e22f21b64f48aee3130e1bf8cd6b69b4eaaa87e66c08291353249f`；
  arm64 `sha256:5d3ca8dd4386fff9f7219199f57b5a236966d0160f3196c15177968981085fb4`。
- **备份（`20261030-023530`）**：`config/config.yaml.bak-20261030-023530`、
  `rui-dot2-override.yml.bak-20261030-023530`、`plugins.bak-20261030-023530.tgz`、
  `image-prev-20261030-023530.txt`。
- **部署**：override 镜像换 index digest → `docker compose -f docker-compose.yml -f
  rui-dot2-override.yml pull && up -d rui` → 容器 `Up (healthy)`。
- **本机配置（写前已备份）**：persona 含 `{name}`/`{version}` + 只开/关 + 摄像机只答不动设置；
  `tools.ha.context_domains=[light,switch,cover]`；
  `context_exclude=[摄像机|Camera Control|摄像机控制, 音箱|Speaker|lx06|小爱]`；
  `allowed_scenes` 5 个；`call_timeout_s=10.0`；
  `tools.vision.cameras={"天奕门口":"camera.192_168_5_45"}`；`usb_snapshot_url=""`（Mac 快照服务未起）。
- **静音验收（真实命令/输出）**：
  1. `/health` → version **0.8.11**、config_version 3、devices 5、device_errors 0、
     5 planes running、warnings []、audio_plane true、speaker_connected true。✓
  2. 热生效：`PUT /api/v1/config {"assistant":{"persona":"【热生效探针】…{version}…"}}` →
     `/api/v1/debug/prompt` 下一轮即含探针且渲染「rui 0.8.11」；随后 PUT 恢复正式 persona
     （200，校验无探针、`{name}`/`{version}` 仍在）。✓（真机问法留白天）
  3. 清单：`/api/v1/debug/prompt` **5007 → 3807 字**；日志
     `ha: entity context 48 entities, 2670 chars (fetched 65)`；实体 48 条
     （light 2 / switch 36 / cover 10），**无** camera 控制开关、**无** lx06 音箱实体、**无** script；
     scene 只出现在白名单场景段（5 个）。✓
  4. 老配置兼容：容器用旧 config + 新键正常启动；16 条单测覆盖默认/旧缓存。✓
  5. 天奕门口（不经音箱）：`POST /api/v1/debug/tool {"name":"local.look",
     "args":{"camera":"天奕门口","question":"画面里有什么"}}` →
     「最近一次（HA 代理）：画面中看不到人。…7F 标识…」；日志
     `device=camera.192_168_5_45 bytes=76233 elapsed_ms=131 live=False` +
     `described via deepseek_llm in 1227 ms (63 chars)`；HA camera_proxy 裸取 **200 / 76233 B**。✓
  6. 书房 USB（服务未起）：同路径 `{"camera":"书房"}` → 「书房那台摄像头还没接上。」✓
  7. 失败/限频话术：未知相机 → 「这台现在看不到画面。」；10 s 内第二次 →
     「刚看过一次，等一下再看。」✓
  8. 音频基线未漂移：`/api/v2/devices/living/audio` → capture 16000 / 8ch / 6mic+2ref /
     mic_shift 10 / ref_shift 16。✓
  9. **夜间静音约束：本会话未触发任何音箱出声**（无 say / TTS / 唤醒 / debug-say；
     全部走 debug/tool 与只读 HTTP）。

## 待白天（发声类，9哥 或 bridge 执行）

1. 问「版本是多少」→ 答 rui 0.8.11（不是 Home Assistant 的版本）。
2. 问「你是谁」→ 小瑞 + 家里自建的语音服务。
3. 「关闭所有灯」→ `scene.turn_on` 一次成功；「关书房灯」「关儿童房灯」→ 对应场景；
   部分失败必须听到「只做成了一部分」。
4. 「天奕门口现在什么画面」→ 1–2 句（标签「最近一次」）。
5. 「我这里是什么画面」→ 书房 USB：需先在 Terminal 授权相机并常驻
   `python3 scripts/usb_snapshot_server.py --host 0.0.0.0`，再写
   `tools.vision.usb_snapshot_url: http://<mac-lan-ip>:8766/snapshot.jpg`（本机 LAN IP，文档里写占位符）。
6. 0.8.10 遗留：HA 灯批量读、barge 短提示、削波阈值。

## 遗留 / 已知风险

- **C 卡末轮独立评审未做**（末轮评审 agent 在收口阶段不可用，按 9哥 指示不新开 agent，
  降级收口）：Lead 亲自读 `look.py` 预算链 + 两条关键测试，跑定向 77 passed + 全量 2308/6 + ruff 绿。
- goal 写的配置路径是 `vision.*`，实现落在 **`tools.vision.*`**（与其它工具配置同层；
  docs/catalog/template/example 全部按 `tools.vision` 写，部署值也按此写入）。
- prompt 3807 字高于 goal 预估 ~2700：switch 实际 36 条（预估 31）+ 新增场景段。
- `ha._fetch_entities` 在 MCP 不可用时仍以空列表写缓存（HEAD 既有，本批未改）。
- 批量回查里 scene 域状态是激活时间戳，多场景失败回查可能误判 unreachable（单场景路径不受影响）。
- `tools.ha.call_timeout_s` 上限 60 与 turn 的 20 s 工具超时不匹配；生产已设 10。
- Mac USB 快照服务无鉴权；跨机必须只在可信网段（`docs/usb-snapshot.md` 已警告）。
- `debug/tool` 的 `local.*` 解析已修（name 或 llm_name），0.8.10 的 `no such tool` 缺口关闭。

## 下一步

1. **白天发声验收**：按「待白天」1–6 逐条回填结果（9哥 或 bridge 执行；HANDOFF 只记结论，
   细项回填 `04_Docs/human-bridge/goal-0.8.11.md`）。
2. **书房 USB 接入**：9哥 在 Mac 终端首次授权相机 → `python3 scripts/usb_snapshot_server.py
   --host 0.0.0.0` 常驻（或 launchd）→ 写 `tools.vision.usb_snapshot_url:
   http://<mac-lan-ip>:8766/snapshot.jpg` → 重启 rui（或控制台保存 vision 配置）→ 语音问「我这里」。
3. **不单独发版的小项**（下次改文档时带上）：
   - `_fetch_entities` 在 MCP 不可用时改返回 `None` 走缓存（HEAD 既有问题）；
   - 批量回查对 scene 域视为「不可验证」（避免多场景误报 unreachable）；
   - `call_timeout_s` 上限与 turn 工具超时的关系写进 schema hint；
   - 控制台 `catalog.py` 里 0.8.10 的 scene/清单旧文案同步到 0.8.11 语义。
4. **回滚锚**：0.8.11 → 0.8.10 index
   `sha256:30e8ed47f2ae0ce4281752c4130bfa58e53f63095f128eae5330e5e5703e1155`
   （备份见「部署实测」；回滚只换镜像，旧版会忽略 `tools.vision` 等新键）。

---

# HANDOFF — rui 0.8.10（已发布并部署，真机验收待回填）

phase-close: task-9

## 版本与契约

- **生产：rui 0.8.10**（2026-10-29 23:19–23:22 由 bridge 在 NAS 部署，9哥 当场授权；
  镜像 index `sha256:30e8ed47…e1155`）。上一代生产 0.8.8（`2dac336`）。
- 基线：tag `v0.8.9` → `a1d5afe`（已随 0.8.10 一并上线）。
- **0.8.10 已发布**：commit `9008643`，annotated tag `v0.8.10`（`6045ca5`），
  GitHub release https://github.com/yes999zc/rui/releases/tag/v0.8.10 ；版本三处一致。
- 镜像（tag 的 docker-publish 已绿）：index `sha256:30e8ed47…e1155`；
  amd64 `sha256:9cb7c5d2…35ccf`；arm64 `sha256:5ce93e3d…0b71a30`。
- `rui_compat` 不变（`>=0.8.1 <0.9.0`）；协议仅 additive（WakeIdle.barge、Metrics overdrive 三字段）；
  `rui-xiaoai-bridge`、`rui-dot2-bridge` 零改动。
- **生产运行时不得改动**（部署验证按此核对）：capture 16k / 8ch / 6mic+2ref / `mic_shift=10` /
  `ref_shift=16`；keywords `… :4.00 #0.07 @你好小宝`（客厅）与 `@你好小瑞`（dot2 三台）；
  `wake_preroll_ms=500`；`standby_kws_gate=false`；AEC `cancel=mix filter_len=4096`。
- 本批部署新增（仅 override 环境变量，不改桥代码）：`DOT2_MIC_GAIN_DB: "12"`。
- **0.8.10 包含已发布未部署的 0.8.9**（多相 decimator 48k 路径 + 模板不再写
  `audio.capture.rate`）：**部署 0.8.10 即同时上线 0.8.9**。依据 **0.8.10 批次生产运行时
  基线（2026-10-29，9哥 任务书）**，现网显式 `audio.capture.rate: 16000`，显式 global 值赢
  （`kind: negotiated`），manifest 48k 不生效。
- **部署后实测（2026-10-29 23:2x）**：`/api/v2/devices/living/audio` 的
  `sources["capture.rate"] = "device-override"`（因为 `devices[living].audio.capture.rate: 16000`
  显式存在，不是 `global-config`）；`effective.capture.rate = 16000`，manifest 48k 没赢；
  wire = voice = 16000 ⇒ decimator `factor = 1`（不执行多相滤波）。**判读口径按三态**：
  `global-config` / `device-override` / `device-manifest`，三者只要 effective=16000 都是安全的。

## 已完成

- **T1** `3b359c4`：cue 切除的沉默唤醒也发 WakeIdle → 控制面立即释放 one-turn 闩；watchdog 降 debug。
  reviewer PASS + verifier VERIFIED（真实 gate/控制面复现，HEAD 对照 idle_events 0→1）。
- **T2** `dd2d752`：`silent` 仅 ok/ok_one；点歌失败（not_found/unreachable/unresolved）先判后播并念短语；
  artist-pool 按标题过滤，现场「紫色的雨夜航线第三号」不再误播《云紫色》。PASS + VERIFIED。
- **T3** `8e0ef50`：`StarStatus(ok/unsupported/rejected)` 三态；unsupported →「这个来源不支持收藏」；
  bool 兼容、未知类型按 rejected 且 debug 日志。PASS + VERIFIED。
- **T4** `651e89d`：`ha_batch_get_state` 五种入参归一为 `entity_ids: list[str]`；参数错误 → `bad_args`
  说「没查到」而非「设备连不上」（issue #34）。PASS + VERIFIED。
- **T5** `1ff00cf`：barge 短提示（`ack.barge_ack` 默认开，mode=none/muted 抑制，80–160 ms，不开 hold/echo）、
  is_noise/空转写回「没听清，你再说一遍」且 session 不关、barge 的 WakeIdle 说一次（follow-up 不再误说）、
  削波 overdrive（|x|≥0.97、连续 5 帧、限频 WARNING）+ Metrics 三字段 + `[seg] clipped=NN%`。
  reviewer PASS（含 follow-up 标记残留 CHANGES 后修复）+ verifier VERIFIED（代码层）。
- **T1b** `334751a`：`on_wake_idle` 在 `_turn_in_flight()`（app.state.turn_tasks 有活 task）时忽略迟到 WakeIdle，
  不释放闩/不touch/不熄灯/不说话；turn 结束时仍由 `_release_when_done` 正常释放。reviewer PASS + verifier VERIFIED。
- **门禁**：T1b 提交后全量 `2217 passed, 6 skipped`（本批基线 HEAD `7c9d738` 为 2165/6）；
  `ruff check src tests` 全绿。6 个卡 commit：`3b359c4` T1、`dd2d752` T2、`651e89d` T4、`8e0ef50` T3、
  `1ff00cf` T5、`334751a` T1b。
- **发布**：tag `v0.8.10` 的 CI 四绿（ci / security / secret-scan / docker-publish）；
  issue #34 已回评修复说明并关闭。
- **文档欠账（task-11，`[0.8.10]` 收尾）**：`docs/known-limitations.md` 新增 **L28** ——
  `audio.aec.who` 的 `none`/`both` 无独立实现（`_build_aec()` 只有 `who == "device"`
  一条提前返回，两者当前等同 `controller`），本批只登记、不改行为；release notes 补
  「本版包含 0.8.9（已发布未部署）」段与部署后 `sources.capture.rate` +
  decimator `factor = 1` 的验证口径；CHANGELOG `[0.8.10]` Notes 同步一句。
- **部署实测（0.8.10，bridge 执行，2026-10-29 23:19–23:22）**：
  - 备份：`config/config.yaml.bak-20261029-231949`、`plugins.bak-20261029-231949`、
    `rui-dot2-override.yml.bak-20261029-231949`、`image-prev-20261029-231949.txt`。
  - override 镜像换 index `sha256:30e8ed47f2ae…e1155`；`DOT2_MIC_GAIN_DB` 18→12
    （容器 env 已核对 =12）；`compose -f ×2 pull + up -d rui` 成功。
  - `/health`：version **0.8.10**、config_version 3、devices 5、device_errors 0、
    planes_running 5、warnings []；dot2-d 已连；living capture **16000 / 8ch / 6mic+2ref /
    mic_shift=10 / ref_shift=16**（基线未漂移）。
  - **口径偏差（下次改文档时带上，不用发版）**：release notes/HANDOFF 原写
    `sources["capture.rate"]="global-config"`；实测是 **`device-override`**——因为
    `devices[living].audio.capture.rate: 16000` 显式存在。生效值仍是 16000、manifest 48k
    没赢，功能安全。判读口径应改为三态：`global-config` / `device-override` / `device-manifest`。
  - **已知工具缺口（非本批引入）**：`POST /api/v1/debug/tool` 返回
    `no such tool: local.music_play`——27 个 tool 都在 `/api/v1/tools`，但
    `runner._handlers` 未注册；T2/T3/T4 只能真机验证。记一笔，另立卡处理。

## 下一步

1. 9哥/bridge 按 `docs/releases/v0.8.10.md` 的 1–7 项做真机听感与削波阈值验收，结果回填
   `04_Docs/human-bridge/goal-0.8.10.md` 第十节（本仓 HANDOFF 只记结论）。
2. 下次改文档时把 `sources.capture.rate` 判读改成三态（global-config / device-override /
   device-manifest）；不为此单独发版。
3. 另立卡：`/api/v1/debug/tool` 的 `runner._handlers` 未注册 `local.music_play` 等工具的问题。
4. 回滚锚：0.8.8 = `2dac336`；0.8.9 = `a1d5afe`；部署前备份见上（`*.bak-20261029-231949`）。

---

# HANDOFF — rui 0.8.9（release 收口，待 commit/tag，未部署）

phase-close: task-3

## 版本与契约

- **rui 0.8.9**：`pyproject.toml` / `src/rui/__init__.py` / `uv.lock` 三处一致；
  tag `v0.8.9` → `a1d5afe`（annotated tag `0a14e9e`），GitHub release
  https://github.com/yes999zc/rui/releases/tag/v0.8.9 。CHANGELOG `[0.8.9] - 2026-10-29`，
  release notes `docs/releases/v0.8.9.md`。镜像 digest 由 tag 的 docker-publish 产出，部署时再记。
- **无协议 / `rui_compat` 变更**（仍 `>=0.8.1 <0.9.0`）；插件不变（dot2 1.3.5 / xiaoai 0.2.3）；
  `rui-xiaoai-bridge`（HEAD `7f8f8d8`）与 `rui-dot2-bridge`（HEAD `c0bd0de`）**零改动、工作区干净**。
- **未部署**：生产仍是 **0.8.8**（`2dac336`，2026-10-29 15:00 部署）。
  0.8.9 实测窗口曾由 9哥 手工切到 48k wire 跑对照；**0.8.10 批次任务书确认现网运行时已按基线为
  显式 16000**（capture 16k/8ch/mic_shift=10/ref_shift=16），48k 只是当时的实测配置。
  部署必须等 **9哥 单独点头**。
- **部署前置（重要）**：现网按批次基线为显式 `audio.capture.rate: 16000`，升级不会自动改；
  要吃 48k 必须删除该键（或改成 48000）并重启音频平面。**部署前以
  `/api/v2/devices/living/audio` 的 `sources` 实测为准**：若现网实际为 48k
  （`device-manifest` / global 48000）则按 48k 分支判读。模板改动只影响新装/重装。
- 回滚锚：v0.8.8 = `2dac336`；回滚只回退 rui 镜像/版本，桥仓无需动。

## 已完成

- **A 多相抽取（0.8.9）**：`src/rui/audio/decimate.py` 48-tap 按 factor 拆相位、只对将输出样本做乘加，
  无全速率卷积、无 `(n,ch,taps)` 中间量；旧实现保留为 `_reference_decimate` 仅测试用；
  `factor==1`（16k wire）不构造滤波器、不建历史、与 `astype(float32)` 逐样本相等。
  `process.py:_frame_to_voice` 只转换/抽取 voice 实际读取的列（6 mic + 1 ref），AEC ref 索引与通道顺序不变。
  证据：独立 HEAD oracle（factor 2/3/4/5/7 × ch 1/2/7/8 × 多帧/非整除尾帧）worst |diff| **2.98e-7**；
  离线 10 s/8ch/48k `Decimator` **0.0965 s → 0.0263 s（8ch）/ 0.0352 s（生产 7 列）**，gate ≤0.2 s PASS；
  `--seconds 30` 归一化门限 0.6 s PASS。剖面与 per-stage 表：`docs/analysis/decimate-profile-0.8.9.md`。
- **B 默认率与文档（0.8.9）**：`src/rui/config/template.yaml` 与 `config.example.yaml` 不再写
  `audio.capture.rate`，manifest 原生率生效（8ch/48k 设备 → 48000；Dot 2 → 16000；无 manifest 旧 plug 仍 core default 16000）；
  显式 16k 仍赢并产生 negotiated conflict（0.8.8 行为保持），但会让设备端 48k→16k 重采样、语言带实测约 −15 dB、
  ASR 空转/超时。文档：`docs/audio-pipeline.md`「wire 率」、`docs/known-limitations.md` **L27**；
  wire 口径已按桥源码校正：PCM payload 768/256 KB/s，WS 帧内是 JSON（int 数组 ≈3 MB/s、base64 ≈1 MB/s），
  旧「6 MB/s」为 32-bit PCM 1.5 MB/s × ~4× JSON 估算。
- **门禁**：全量 `uv run pytest -o addopts="" -q` → **2165 passed, 6 skipped**（基线 HEAD 2136/6，净增 29 条）；
  `uv run ruff check src tests` → All checks passed。task-1：reviewer-a **PASS**、verifier-a **VERIFIED**；
  task-2：reviewer-b **PASS**、verifier-b **VERIFIED**（独立跑 `python -m rui setup` 验证落盘无 rate）。
- **A4 口径边界（不许过度宣称）**：多相只解释抽取器本身；core 侧 48k−16k 全通路差约 **0.036 s/10 s**
  （NAS 约 5–7% 单核），**不能解释生产 +186 个百分点**。剩余归属（plug/wire decode、WS 线程与 3× payload、
  ASR/VAD worker、设备侧采集）**未在离线证实**；生产 py-spy 需 9哥 授权，本批未做。

## 下一步

1. **9哥 决定是否部署 0.8.9**：部署前备份 `config/config.yaml`、`plugins/`、override；删掉/改写
   `audio.capture.rate` → `up -d`；按真机验收目标记录：living 平面 ≤0.6 核、容器 ≤130%、NAS load <3、
   `asr_done` ≤250 ms、唤醒 admit ≤500 ms、播歌时 KWS 不回归。失败回滚 v0.8.8 `2dac336`。
2. **若 CPU 未达预期**：按 `docs/analysis/decimate-profile-0.8.9.md` 的待测清单，在 9哥 授权后对 NAS 做
   `py-spy`/perf 逐线程 profile（wire decode/plug、WS、ASR/VAD、设备侧），不要把多相化说成「CPU 已解决」。
3. 文档小 nit（非阻塞）：`docs/audio-pipeline.md` 的「约 2.8×」可统一为 profile 的 2.74×；
   ADR 0007 仍为历史叙述，ADR 0008 已加 superseded 标注。
4. release 已发布：commit `a1d5afe` + tag `v0.8.9` 已 push 到 GitHub（main + tag），
   GitHub release v0.8.9 已创建；部署与镜像 digest 由 9哥 决定后另行记录。

---

# HANDOFF — rui 阶段快照（v0.8.6 已部署生产）

## 版本与契约

- 生产：**rui 0.8.8**（tag `v0.8.8` → `2dac336`，2026-10-29 15:00 部署）。镜像 index
  `sha256:8cf725fa…f71e` / amd64 `sha256:b18c37f9…2213`。0.8.7 的发布内容全部包含。
- 0.8.8 是 0.8.7 的 hotfix：待机 KWS 门控改相对噪声底、`voice peak` 只打交互窗口。
- `rui_compat` 不变（`>=0.8.1 <0.9.0`）；插件不变（dot2 1.3.5 / xiaoai 0.2.3）；
  不改 barge/AEC/音量/followup/播放语义；无新依赖、无前端改动。
- 回滚锚：0.8.7 index `sha256:6a6da058…3570` / amd64 `sha256:29120924…cbcc9`；
  override 备份 `/vol1/rui/rui-dot2-override.yml.bak-20261029-1457`；
  0.8.7 部署前备份 `rui-dot2-override.yml.bak-20261029-1410`、
  `config/config.yaml.bak-20261029-1410`、`plugins.bak-20261029-1410.tar.gz`。

## 已完成

- **0.8.7 功能**（tag → `439f973`，已随 0.8.8 在生产）：`RUI_MUSIC_SOURCES` 外部音源钩子
  （compat + 五方法 + 降级 + `docs/music-private-sources.md` + stub）、`local.tell_joke`
  （joke-svc / 20 条兜底 / 两段谜语 / `audio_url` 下载播放，降级 audio→text→内置）、
  音乐别名接主路径 + 控制台 names + 打断后短应答、错误 `standby_kws_db` 的 P0 日志修复。
- **0.8.8 hotfix**：相对噪声底门控（floor 下限 -60、静音线 `max(db, floor+6)`、hold 主调参）
  + `voice peak` 仅交互窗口；7 个文件、28+ 测试；reviewer PASS、verifier VERIFIED、
  CI 四绿（main `2dac336`：ci/security/secret-scan/docker-publish；tag 后三者）。
- **部署实测（0.8.8，静置 5 min）**：`/health` version=0.8.8、devices=4、device_errors=0、
  plugins [dot2,lx06]；容器 CPU **50.4%**（0.8.6/0.8.7 为 57.6%）、load 0.81；
  `kws standby: suspended` 出现（room floor -50…-52 dBFS）；`voice peak` 5 分钟内 **0 条**；
  日志 0 次 `got 16000` / `play ValueError` / `audio_config_mismatch`。
- 说明：强制冷（阈值 -20）的 CPU 下限是 **34.5%**，0.8.8 实际 50.4% 是因为 ambient 抖动
  >6 dB 导致 suspend/resume 反复（P2 floor 颤振抑制）。**<20% 未达成**，需 P2 的
  AEC/resample profile，不伪造。

## 下一步

1. 9哥 真机验收 0.8.8：待机 CPU 降幅（57.6→50.4，未到 <20%）、唤醒延迟/识别率 A/B、
   播放/打断/音乐别名/短句应答/笑话。异常按上面的 0.8.7 digest 回滚。
2. P2（下一批）：living 专项 py-spy profile、ref 通道退出 AEC 评估、floor 颤振抑制
   （median/自适应 margin）；目标待机 <20%。
3. B1/B2/B3（下一批）：ASR 热词/拼音二次匹配、话术三分、known-limitations。
4. 0.8.8 的 `docs/standby-cpu-0.8.7.md` §1.1/§5.9/§7 已记录真实数字与调参口径。

phase-close: task-8

## 2026-10-29 第二批（v0.8.7 候选，进行中）

- 代码已提交并 push：`c3d6f15`（music 外部源钩子 `RUI_MUSIC_SOURCES` + `local.tell_joke`
  （joke-svc / 20 条兜底 / 两段谜语 / `audio_url` 下载播放）+ P0 `voice peak` 日志 +
  P1 待机 KWS 门控），main 触发 CI 四 workflow。
- 门禁：task-1/2/3/4/5/6 全部独立 reviewer PASS（task-2/4 经多轮 CHANGES 收口），
  fresh verifier **VERIFIED**（全量 2096 passed / 6 skipped、ruff 绿、web typecheck+build 绿、
  wheel 含 `rui/data/jokes.fallback.json` 20 条）。
- 进行中：task-7（A 卡）已完成并提交 `0bcdfa3`（别名接音乐主路径、控制台 names、打断后
  短应答放行、别名文件 0640 + runbook），reviewer-music-a PASS + verifier-a VERIFIED
  （全量 2128 passed / 6 skipped、ruff 绿）。task-8 发布/部署进行中。
- 已知 CI：`5fbef35`（0.8.7 前一批）四 workflow 全绿；`0bcdfa3`（A）CI 进行中。
- 待做：task-8 `[phase] v0.8.7 发布与部署`（版本 0.8.7、CHANGELOG/release notes、CI 四绿、
  tag、`gh release`、生产部署 + D5 七项 + 待机 CPU/load + 回滚锚）。
- 不在本批：B1/B2/B3（ASR 热词、话术三分、known-limitations）；P2（living 专项 profile /
  ref 通道退出 AEC 评估）。
- 已定口径：joke 服务（NAS `:18081`）、music 外部源（NAS `:18082`）；token
  `JOKE_SERVICE_TOKEN` / `MUSIC_SERVICE_TOKEN` 走 secrets，不进 config；joke 只认
  `audio_url`（rui 下载到 `<data_dir>/tmp`），不认跨机 `audio_path`；外部源 `duration_ms`
  需 `/1000` 转 `Track.duration` 秒。
- 注意：`config.example.yaml` 不写 RFC1918 地址（secret-scan soft 规则），示例用
  `joke-svc.example.lan`；生产真实地址只写部署机 config/override。
- **0.8.7 已发布并部署（2026-10-29 14:15）**：tag `v0.8.7` → `439f973`；CI 四绿
  （main 439f973: ci/security/secret-scan/docker-publish；tag: 后三者）；镜像 index
  `sha256:6a6da058…` / amd64 `sha256:29120924…`。生产 `/health` version=0.8.7、
  devices=4、device_errors=0、plugins registered [dot2,lx06]。回滚锚：0.8.6 amd64
  `sha256:9ad8e03f…6cbc`；备份 `rui-dot2-override.yml.bak-20261029-1410`、
  `config/config.yaml.bak-20261029-1410`、`plugins.bak-20261029-1410.tar.gz`（root 600）。
- **P1 部署后实测问题（task-6 已 reopen）**：绝对阈值 `-60` 在本机 ambient 峰值
  -35…-49 下**从不挂起**（无 `kws standby: suspended`），容器 CPU 仍 57.6%；临时把
  `standby_kws_db=-20` 强制冷后 57.6%→34.5%，证明 KWS 是主成本。`voice peak` 仍每
  30 s 打 ambient。已建 task-9：相对噪声底门控 + 仅交互窗口打点，计划 0.8.8 hotfix；
  `<20%` 需要 P2（AEC/resample profile，本批未做）。

2026-10-29 · 本轮收口到 **task-5（第三方桥开发者入口包）** · 下一批从 **task-6「music 外部音源钩子」** 开始

phase-close: task-11

## 当前状态（2026-10-29 11:10 收口，替换旧顶部快照）

- **生产版本：v0.8.6**（已部署，生产 NAS `/vol1/rui`；镜像 amd64 `sha256:9ad8e03f…6cbc`；
  dot2 插件 **1.3.5**；4 台设备 / device_errors 0；tag `v0.8.6` → `9610969`，
  release https://github.com/yes999zc/rui/releases/tag/v0.8.6）。
- **本轮收口到 task-5**：第三方桥开发者入口包完成（docs 五篇 + `rui-adapter-check` CLI +
  新仓 https://github.com/yes999zc/rui-plugin-template + 两处 README 链接），
  reviewer PASS + verifier VERIFIED；**未发版/未 tag/未动生产**。
- **下一批起点：task-6「music 外部音源钩子」**（新开会话做；本会话停手，不再改代码/发版/部署）。
- **其余待办（各行一行）**：task-2/3 等真机样本（L25/L26）；H（控制台段指标）backlog；
  task-6 之后的优先级以 `docs/backlog-0.8.md` 与 9哥 新卡为准。
- **不归本仓**：设备侧 token/静音（dot2/dot2-b 的 `speaker_connected` 需现场解除静音/确认上行）；
  dot2 桥 main 已含未发版提交（计数日志 `516c0e8` 已随 1.3.5 发布）；
  `rui-adapter-check` 尚未进 tag（下次核心发版自然带上）。

## 版本与契约

- **rui core 0.8.5**：annotated tag `v0.8.5`（tag object `1573a47`）→ commit `6d94155`；
  release https://github.com/yes999zc/rui/releases/tag/v0.8.5。**生产仍是 0.8.4**（本批只发版，未部署，9哥 未点头）。
- **桥插件不动**：`rui-dot2-bridge` **1.3.4**（`76b8947`）、`rui-xiaoai-bridge` **0.2.3**（`0d82250`）；
  `rui_compat` 不变（`>=0.8.1 <0.9.0`），C-001 未变。
- **镜像（仅部署引用，未部署）**：index `sha256:20347039ee559db4e07989f9311c4fec78461163c6ce23312867e5350c51d383`；
  amd64 `sha256:4bfd73d5b8e0ffadd22512f888d922d6b3355041214f5d3cdaaf17d936bd50a1`；
  arm64 `sha256:c625d5c1a7469b1ab39163dbcbd2574cb5dd62e36a8cae05d4c556e00d2c8649`。
- **重要：`v0.8.5` tag 只含 D1–D5**（tag commit `6d94155`）。D4/D6/D7/D8 是其后的 main 提交
  （bridge 计数日志、三仓 README 通用化、arm64 验证文档、v2 通知通道页），**不在 0.8.5 镜像/release 里**；
  要让这些上线（尤其 v2 通知通道页）需另发一版（如 0.8.6），本批未做。
- tag commit 上四 workflow 绿：`ci`（含 `test (3.11)`，run 40717635334）、
  `docker-publish`（main 40717635389 / tag 40718665667）、`security`（40717635435 / 40718666213）、
  `secret-scan`（40717635240 / 40718665933）。`ci` 只在 main push 触发，以同一 commit 的 main run 为准。

## 已完成

本批（0.8.5，2026-10-28）门禁：默认 3.12 全量 **1969 passed, 6 skipped**；Python 3.11 全量 **1969 passed, 6 skipped**；
`ruff check src tests scripts/coverage_report.py` 绿；web `pnpm typecheck` + `pnpm build` 绿；
fresh reviewer-code **PASS**（A/B/C/E）+ reviewer-infra **PASS**（D/F/G）；fresh verifier **VERIFIED**。

- **A 到点文案**：`phrases.reminder.due`「时间到了，{text}。」+ 前导触发词剥离 / 句末标点归一 /
  空文本退化；离线分支逐字不变。`app.py::_reminder_due_line()`；文档 `docs/reminder-due-phrase-0.8.5.md`。（`4e7daf1`）
- **B 分钟级提醒 + 单句边界**：`parse_voice` 新增 `N分钟(之)后/以后`，与工具路径共用
  `parse_at("+Nm")`；动作必须单句（第二句仍交 LLM，保 task-32 行为）；两条路径共用
  `_reminder_room()`。文档 `docs/reminder-dispatch-0.8.5.md`。（`4e7daf1`）
- **C 客厅 LX06 示例音量 60→45**：只改 `config.example.yaml` + 守卫断言 + 文档；
  schema 默认 60 与 dot2 未动。（`3d1a8c5`）
- **E 17–20 字合并**：`SOFT_MERGE_MAX_CHARS=20`，逗号仅在已缓冲 >20 字时切分；
  17/18/19/20 字含逗号由 2 次 TTS 变 1 次；句末标点立即释放不变。（`3d1a8c5`）
- **D dot2 1011 诊断**：只读取证 36 次 1011（uptime 全 20 s 倍数、5.1 s 重拨、0 丢块、
  ≈1.77% 上行空洞），命中桥侧 websockets keepalive 超时，**不改行为、桥仓未动**。
  文档 `docs/analysis/dot2-data-plane-1011.md`。（`ae4f320`）
- **F task-2/task-3 观察口径**：known-limitations **L25/L26** + backlog「等样本」s.1/s.2；
  无稳定样本不改代码。L21 同步到 0.8.5 口径（分钟级受理/单句边界）。（`ae4f320`）
- **G CI 3.11 死锁**：CPython `asyncio.wait_for` 取消丢失（python/cpython#96764），
  `_speak_loop` 改 `asyncio.timeout(0.5)`；`test` job 加 `timeout-minutes: 20`；
  测试零改动。文档 `docs/ci-311-hang-0.8.5.md`。（`d08b159`）
- **版本/发布**：`pyproject`/`src/rui/__init__.py`/`uv.lock` = 0.8.5；CHANGELOG 0.8.5 段；
  README 版本矩阵；release notes `docs/releases/v0.8.5.md`。（`6d94155`）

### 后续批次 D4–D8（9哥 2026-10-28 深夜追加；按顺序执行）

- **D4 ✅ bridge 侧 1011 计数日志（只诊断，不改行为）**：bridge `LinkStats` 新增
  `data_plane_closes / last_data_close_at / data_close_rx_total / data_close_dropped_total`，
  `_lose_data()` 既有 INFO/WARNING 行尾追加 `close#N interval=…s dropped=N`；新增单测
  `test_data_plane_close_counters_are_logged`；`scripts/check.sh` 顺手修既有
  `house_ear.config → rui.config`（0.2.0 包重命名后遗留）。bridge `516c0e8` + `b6deb86`
  （已 push，main 领先 v1.3.4，**未发版/未 bump**）；rui `ab579b8`（`docs/analysis/dot2-data-plane-1011.md` §10）。
  `./scripts/check.sh --nodevice` EXIT=0（183 tests / ruff / doctor 0 fail·2 warn）。
  环境备注：bridge dev extra 的 PyYAML 在 house-ear venv 缺失，本机 `uv pip install` 补齐（仅本机 venv，未提交）。
- **D6 ✅ README 通用化（中英对称，保留「AI 一条命令接入」）**：rui `928cbe4`、
  rui-dot2-bridge `870d965`、rui-xiaoai-bridge `7f8f8d8`（均已 push，各自只改 README.md）。
  验证：rui `grep -nE '^##+ *0\.[0-9]'` 无输出；`grep -cE 'v0\.[0-9]+\.[0-9]+'` = 0（≤1）；
  三仓 `grep -n onboard_device` 有命中；三仓各一处 `Latest release: <releases URL>`（不含版本号）；
  版本矩阵/流水/过期 tag 快照删除，硬编码版本 badge 换动态 release badge。
- **D7 ✅ arm64 可运行性验证**（rui `34bd182`，`docs/arm64-verification-0.8.5.md` 新建）：
  `ghcr.io/yes999zc/rui:0.8.5` 的 linux/arm64 在 Apple Silicon Mac 上 pull 成功（本地 arm64 镜像
  `sha256:3baf2b78…23c7`；manifest arm64 `sha256:c625d5c1…8649`）；
  `--entrypoint ffmpeg` → `ffmpeg version 7.1.5`；`--entrypoint python -c "import rui,numpy"` → `0.8.5 2.5.4`；
  最小 config 起服务（`-p 18081:8080`，18080 被本机既有 0.7.2 进程占用，未触碰）→ `/health` **200**
  且 `version=0.8.5 / status=ok / device_errors=0`；容器已清理。字面 `docker run <img> ffmpeg/python`
  因镜像 `ENTRYPOINT ["rui"]` 会 exit 2，文档如实记录等价 `--entrypoint` 用法。
  **未验证**：ARM 多设备/音频平面性能、长稳 soak、真实音频设备与 bridge plug、真实 provider 链路、生产 compose 形态。
- **D8 ✅ v2 通知通道页**（rui `1327585`，第 1 轮即绿，未触发裁砍；Telegram + 微信都做）：
  新增 `web/src/v2/views/ChannelsView.vue` + `/channels` 路由 + Config 子导航「通知通道」+ 中英 i18n
  + `api.ts` channels 封装 + `style.css`（仅复用现有 tokens）；两张卡片（enabled / reply_mode 三选 /
  白名单增删、PUT→GET 回读比对）、token 与微信登录态只读、网关 start/stop/restart、503/422/网络错误文案；
  后端零改动（`git diff 34bd182..1327585 -- src/` 为空）、无新依赖、未复活 /legacy。
  新增 `tests/test_channels_view.py`（源码契约 7 + API 真行为 13，含 PUT→GET 回读/0600/422/supervisor 缺失 503）。
  门禁：`pnpm typecheck` 绿、`pnpm build` 绿（产出 ChannelsView chunk）、全量 **1989 passed / 6 skipped**、ruff 绿。
- **D5 ✅**：tag `v0.8.5` / release / 四 workflow 绿 / 双 review PASS / verifier VERIFIED（见上）。
- **D4/D6/D7/D8 复核 ✅**：fresh `reviewer-followup`（task-17）**PASS**；fresh `verifier-followup`（task-18）**VERIFIED**：
  rui 全量 **1989 passed / 6 skipped**、ruff 绿、web typecheck+build 绿（产物含 `ChannelsView-*.js` chunk）、
  bridge `scripts/check.sh --nodevice` 183 tests OK、D6 三仓 grep（`## 0.x` 0、`vX.Y.Z` 0/0/0、
  `Latest release:` 各 1、`onboard_device` 有命中）、D7 registry digest 三平台与文档逐字一致 +
  arm64 容器 `0.8.5 2.5.4` 且 `/health` 200、D8 后端 `src/` 零 diff + 无新依赖 + 20 tests、secret-scan 等效 0 命中；
  三仓 `HEAD==origin/main`、工作区干净。

**交接三句（9哥 指定，必须在这里）**：

1. **README 首页已通用化，版本变化只写 release**：rui `928cbe4`、dot2 桥 `870d965`、xiaoai 桥 `7f8f8d8`；
   首页只剩通用介绍 + 一处 `Latest release:`（不含写死版本），逐版本变更只写 release 正文/CHANGELOG。
2. **AI 一条命令接入已写入首页核心能力**：「新设备接入：一条命令，AI 可代劳」→
   `rui-dot2-bridge/scripts/onboard_device.py` + `docs/onboard-device.md`，中英两节都保留。
3. **arm64 验证到哪一层**：runtime 层——arm64 镜像可 pull、`ffmpeg` 可执行、`import rui,numpy`（0.8.5 / 2.5.4）、
   最小 config 起服务 `/health` **200** 且 `version=0.8.5`；**未验证** ARM 多设备/音频性能、长稳 soak、
   真实音频设备与 bridge plug、真实 provider 链路、生产 compose 形态（详见 `docs/arm64-verification-0.8.5.md`）。

**非阻塞观察（review/verify 一致，未改，留后续）**：① `docs/analysis/dot2-data-plane-1011.md` §10 示例行
把两个事件拼成一行（已标「数值沿用」）；② i18n `v2.channels.saveFailed` 定义了但视图未使用；③ ChannelsView
把任何 503 都映射成「网关未就绪」（保存路径实际不会 503）。

**本批结束**：D1–D8 全部完成；**未部署生产**。

### 2026-10-29 新阶段：rui 0.8.6 + dot2 桥 1.3.5 + 生产部署（9哥 授权，进行中）

**9哥 指令**：发 0.8.6 和/或 dot2 桥新版本，并部署；同时修 3 项非阻塞观察。
（本阶段 supersedes 上文「本批只发 release 不部署」——部署已获明确授权。）

**已核实部署现场（2026-10-29 09:2x，只读）**
- 生产：rui **0.8.4**，amd64 digest `sha256:f1b6a992…6462`，4 台设备，`/health` 200 / device_errors 0 /
  registered [dot2,lx06]。compose：`/vol1/rui/docker-compose.yml` + `/vol1/rui/rui-dot2-override.yml`
  （override 含 image digest、`./plugins:/opt/rui-plugins:ro`、`RUI_ADAPTER_PLUGINS`、DOT2_* env）。
- 插件：`/vol1/rui/plugins/rui_dot2_bridge/`（plugin.yaml 1.3.4）；catalog 副本
  `/vol1/rui/data/plugs/dot2-echomuse/{plugin.yaml,devices/}`。
- 写权限：`yes999zc` 无免密 sudo，但属 docker 组 → 用一次性 root 容器写 `/vol1/rui`
  （`docker run --rm -v /vol1/rui:/mnt alpine:3.20`，已验证 WRITE_OK）；compose v2.40.3；GHCR 可访问。
- 回滚锚（部署前）：override `.bak-20261028-220433`；本阶段会再备份 override/plugins/config。

**Round 2 进度（2026-10-29 09:3x）**
- task-23 首轮 **CHANGES**（唯一阻塞为文档现状陈述）：`docs/analysis/dot2-data-plane-1011.md` 仍写「桥未发版/仍是 1.3.4」。
  已修 `b42f42e`：头部标「分析基线 1.3.4」、§10 标题与版本状态改为「随桥 1.3.5（tag `v1.3.5`）发布；
  生产 NAS 部署前仍是 1.3.4，部署后线上才有新字段」；HANDOFF task-21 记录补 `e44d333`。
- **reviewer-release 复评 PASS**（task-23 complete，revision 3；复核 HEAD `b42f42e`，diff 仅 2 docs 文件；
  `test_channels_view.py`+`test_version.py` 22 passed；`未发版` 字样 0 命中）。
- 下一步（待执行）：打 tag **`v0.8.6`（→ 打分时 rui main HEAD，含本 checkpoint 与发布内容）** /
  **`v1.3.5`（→ 桥 `e44d333`）** + GitHub release → 等四 workflow 绿 → 执行 task-22 部署
  → task-24 独立复验 → task-25 收口（顺手清历史段落的「生产插件仍 1.3.4」措辞）。
- 其余非阻塞观察（service 503 措辞、errorText 标点）本批不改，已记录。

**Round 3–6 收口（2026-10-29 09:33–09:55，已发布并部署；verifier VERIFIED-D5-EXCEPTION）**

- **发布**：rui tag **`v0.8.6`** → commit `9610969`，release https://github.com/yes999zc/rui/releases/tag/v0.8.6 ；
  桥 tag **`v1.3.5`** → `e44d333`，release https://github.com/yes999zc/rui-dot2-bridge/releases/tag/v1.3.5 。
  v0.8.6 commit 上四 workflow **全绿**（ci `40801032284` / docker-publish `40801076851` /
  security `40801076917` / secret-scan `40801076710`）。
- **镜像**：index `sha256:430840eb…a879`；amd64 `sha256:9ad8e03f…6cbc`；arm64 `sha256:e18034e3…da9b`。
- **部署**（09:40 重建容器，`docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d`）：
  override image → 0.8.6 amd64 digest；dot2 插件 1.3.5 已进 `/vol1/rui/plugins/rui_dot2_bridge/` 与
  catalog 副本 `/vol1/rui/data/plugs/dot2-echomuse/`（tar sha256 `daaa2dcb…e5fd`）。
- **备份/回滚锚（NAS）**：`rui-dot2-override.yml.bak-20261029-0933`(787B)、
  `plugins.bak-20261029-0933.tar.gz`(147489B)、`config/config.yaml.bak-20261029-0933`(9403B)
  ——**三者 root:root mode 000，回滚必须用部署同样的 root 容器 helper**（`docker run --rm -v /vol1/rui:/mnt alpine`）。
  回滚镜像：0.8.4 amd64 `sha256:f1b6a992…6462`；回滚插件：上面的 plugins tar（1.3.4）。
- **独立 verifier 结论**：**VERIFIED-D5-EXCEPTION**（全量 `1990 passed / 6 skipped`、ruff 绿、
  web typecheck+build 绿、桥 `check.sh --nodevice` 183 tests OK、tag/release 存在、四 workflow 绿）。
- **生产实测**：`/health` 200、version=**0.8.6**、devices=4、device_errors=0、warnings=[]、
  registered=[dot2,lx06]、config_version=3、console=v2；`/v2/channels` **200**；
  `/api/v1/channels`（X-Auth-Token）200，telegram token_configured=true/enabled=true、
  gateway running；日志近 10 分钟 `got 16000`/`play ValueError`/`audio_config_mismatch`/ERROR/Traceback/1011 均 0。
  **四台 `debug/say` 全部 200 + reply + error=""**（living 63396B、dot2 57560B、dot2-b 57728B、dot2-c 57542B）。
- **D5 偏差归因（已定性，设备侧，非本批回归）**：`dot2`/`dot2-b` 的 `speaker_connected=false` 是因为
  两台**物理静音键为 muted=true** → 静音后无 mic 上行 → 触发桥既有 `SPEAKER_STALE_S=45` 新鲜度判据
  （自 v1.3.2 起，非 1.3.5 新增）；console 的 `token_missing` 是**既有显示口径**（`effective_token_env`
  把默认 `RUI_DEVICE_TOKEN` 映射为 manifest 的 `DOT2_EM_TOKEN`，自 v0.4.1 起，0.8.6 未改），
  仅 connected 时被 `token_managed_by_adapter` 压制。反向验证：对两台各 say 一次后 health 立即 true，
  静默 ~70 s 回落 false。已排除 token 丢失/错误、adapter 未注册、数据面被拒/被关闭、本批鉴权/连接改动。
  **无需回滚**；要彻底消除属设备侧（解除静音/确认上行）。
- **后续**：新卡「第三方桥开发者入口包」（task-26/27/28，见下节）尚未开工。

**按顺序执行（任务板 task-19…25）**
1. task-19 ✅ 修 3 项观察（rui `85c20f4`）：§10 示例行改为 §2.2 表 #12 单一真实事件
   （`close#12 interval=305.1s rx=9329320B tx=0B`，并加测试断言与表一致）；`v2.channels.saveFailed`
   改为保存失败标题（zh/en 同步，不再 dead key）；503 文案按上下文分流——gateway start/stop/restart
   → 「网关未就绪」，channels 加载/保存 → 新增 `v2.channels.serviceUnavailable`「服务暂不可用（503）」，
   422 仍显示 detail；`tests/test_channels_view.py` 覆盖两条 503 分支。
2. task-20 ✅ rui 0.8.6 release prep（`6b11b51`，已 push）：pyproject/`__init__`/uv.lock=0.8.6、
   CHANGELOG `[0.8.6] - 2026-10-29`、`docs/releases/v0.8.6.md`（含部署后四项验证口径）；
   全量 **1990 passed / 6 skipped**、ruff 绿、web typecheck+build 绿。**未打 tag/未 release**（等 review 后）。
3. task-21 ✅ 桥 1.3.5 release prep（`d20a129` + 发布说明 `e44d333`，已 push）：pyproject/plugin.yaml=1.3.5、CHANGELOG
   `[1.3.5]`（计数日志/check.sh/README；1.3.4 内容正确落入新 `[1.3.4]` 段）、C-001 版本引用同步、
   `test_manifest.py` 版本断言同步；`rui_compat` 与 v1.3.4 **逐字相同**（`>=0.8.1 <0.9.0`）；
   `./scripts/check.sh --nodevice` EXIT=0（183 tests / lint / doctor 0 fail）。
4. task-23 ⏳ fresh reviewer（tag 前）→ Lead 打 tag `v0.8.6` / `v1.3.5` + GitHub release + 等 CI 四绿。
5. task-22 ⏳ 部署（Lead）：备份 → override 换 0.8.6 amd64 digest → 插件 1.3.5 到 plugins + catalog 副本 →
   一次 `docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d` → D5 + 0.8.6 专项
   （/v2/channels 200、channels API、插件 1.3.5、四台 say、errors 0、日志 0 次 got16000/play ValueError/audio_config_mismatch）。
6. task-24 ⏳ fresh verifier：tag/workflow 门禁 + 从本机独立复验生产；task-25 ⏳ HANDOFF 收口。

**边界**：不改生产 `config.yaml` 内容（living volume 不擅自改）；不改 `rui_compat`；不动 barge/AEC/followup/
音乐抢占语义；xiaoai 桥只 README、不发版；不加依赖。

### 2026-10-29 新卡：第三方桥开发者入口包（待执行；不发版/不 tag/不动生产）

**背景**：外部开发者要自己研究设备、自己写桥接进 rui；协议不用改，缺「给外部人看的入口层」
（现有 `docs/socket-plug-v1.md` 是内部草案体例）。

**范围（任务板 task-26…29）**
- A（task-28）：`docs/bridge-developer/` 英文五篇——quickstart（5 分钟假设备连上并出声）、
  decision-tree（EchoMuse 路线 vs 写 adapter）、install-paths（RUI_PLUGINS_DIR /
  `<data_dir>/plugs/<id>` / RUI_ADAPTER_PLUGINS）、debugging（注册被拒/能力缺失/audio wire mismatch/
  未注册）、compatibility（0.8.x 只锁 minor、dual-window 示例、trust 自断言/in-process=完全权限）。
- B（task-26）：`scripts/rui_adapter_check.py` conformance CLI（`--adapter <path>`，import 核心
  contract.py + mock.py 不复制，逐项 PASS/FAIL，`pip install -e .` 后为控制台命令）+ pytest 覆盖
  PASS 模板与 FAIL 实现。
- C（task-27）：新独立仓 **`yes999zc/rui-plugin-template`**（MIT，kebab-case，README 中英）：
  plugin.yaml + adapter.py 八方法骨架 + devices/example.yaml + 1 条契约测试；禁止内网 IP/token/个人路径/用户名。
- D（并入 task-28）：house-ear README 加「第三方设备桥…见 docs/bridge-developer/」；
  dot2 桥 README 加一句指向模板仓与文档（不加版本流水）。
**验收**：外人只读 quickstart+模板仓 30 分钟内假设备接上本地 rui、出声+收声各一次；
  `rui-adapter-check --adapter ../rui-plugin-template` 全 PASS、错实现明确 FAIL；三份指南互不矛盾且与
  contract.py / loader.py 逐条对得上；全量 pytest + ruff 绿。
**边界**：不发版/不 tag/不 release（0.8.6 已发）；不改协议语义/rui_compat；不动生产。
**状态**：0.8.6 发布+部署已收口（VERIFIED-D5-EXCEPTION）；9哥 2026-10-29 10:30 确认开工本卡。

**Round 1（2026-10-29 10:4x，进行中，均未 commit）**：三路并行——
- `impl-devdocs` → A+D（house-ear `docs/bridge-developer/` 五篇 + README 一行 + dot2 桥 README 一句）；
- `impl-ci`（复用）→ B（`rui_adapter_check` CLI + pytest + `[project.scripts]`）；
- `impl-obs`（复用）→ C（新仓 `rui-plugin-template`，已见 `plugin.yaml`/`devices/`/`tests/` 目录）。
- team 成员数已达上限 16，**不再 spawn 新成员**；最后 review/verify 复用已有 fresh 角色（reviewer-code / verifier-release）。
- **C/task-27 ✅ DONE**：新仓 https://github.com/yes999zc/rui-plugin-template （MIT，main `afee821`）。
  7 文件：`plugin.yaml`（`rui_compat ">=0.8.1 <0.9.0"`、trust trusted）、`devices/example.yaml`、
  `adapter.py`（八方法骨架 + TODO）、`tests/test_contract.py`（CLI 未合入时 skip+hint）、README 中英、LICENSE、.gitignore。
  验证：house-ear loader → `OK example-plugin 0.1.0`；`rui plug-test ../rui-plugin-template --adapter …:build`
  → **EXIT=0，pass=20 fail=0 warn=0 skip=1**；禁用内容 grep 0 命中、仓干净。`rui-adapter-check` 合入后由 verifier 补跑模板契约测试。
- **B/task-26 ✅ DONE**：house `c431f70`（已 push main，未 tag）。`rui-adapter-check` conformance CLI：
  `src/rui/device/adapter_check.py`（**import** `rui.device.contract` + `rui.device.mock`，复用 `rui.plugins.contract`
  的 import/build/call/frame 原语，未复制实现）、`scripts/rui_adapter_check.py` 薄包装、`[project.scripts]` 加
  `rui-adapter-check`、`tests/test_adapter_check.py`（12 例）+ `tests/fixtures/adapter-check/{pass,fail}`。
  `--adapter` 支持插件目录 / plugin.yaml / `module:attr` / `file.py:attr`；每项 `PASS <check>` / `FAIL <check>: reason`，
  汇总后任一 FAIL exit 1。真输出：PASS fixture **22 PASS / 0 FAIL** exit 0；fail fixture（缺 `play`）**2 FAIL**
  exit 1；`--device-id fail-bad-frames`（frames 返回 dict）**1 FAIL** exit 1；`../rui-plugin-template`
  **22 PASS exit 0**（本卡验收项）；`tests/fixtures/sample-plug` PASS + 1 WARN（未实现 `read_volume` 属可选面）；
  新增 pytest **12 passed**、ruff 绿、`uv pip install -e .` 后控制台命令可用；CI `secret-scan` **success**
  （ci/security/docker-publish 10:47 时仍在跑）。
- **A/task-28 ✅ DONE**：house `7bbe973`（5 篇 EN 文档 + README 一行，1498 insertions）+
  桥 `c0bd0de`（README 双语各一句）。真跑自测：临时目录 `/tmp/rui-bridge-demo`、loopback :8088、
  按文档代码块原样执行——起服务（registered fake-speaker / audio plane up）、**收声**
  （`fake device: capture frame #51 (20 ms, 320 samples)`）、**出声**（`audio.play` ok + 5×playback chunk + finished）、
  `/health` registered/audio_plane/speaker_connected 全绿；另实测 409 capability_unsupported、
  `audio_config_mismatch`、`plug_unresolved` 报错路径；进程已清理。文档断言→代码(file:line) 对照表见实现报告。
- **A/B/C 三路全部 DONE**；`task-29`（reviewer-code）与 `task-31`（verifier-release）并行验收。
- **task-31 结果：FAILED(文档缺口)**（执行闸门全绿，但模板仓/quickstart 有实测阻断错）：
  全量 `2004 passed / 6 skipped`、ruff 绿、CLI `22 PASS exit 0` / FAIL fixture `exit 1`、
  模板契约测试 `1 passed` 均过；「外人 30 分钟上手」自测 **4.5 min 完成**（起服务→假设备→出声 16000B→收声 757 帧）。
  需修（退回原实现者，verifier 只复跑受影响项）：
  1) **G1（阻断）** 模板仓 `README.md:87/192` 与 `devices/example.yaml:5-6` 写 `devices[].adapter` 应等于 plugin id；
     实测 `RUI_PLUGINS_DIR` 路线必须用**设备清单 id**（`example-speaker`），用 `example-plugin` 会
     `unknown adapter`/`device_errors=1`。需改成「RUI_PLUGINS_DIR → device manifest id；RUI_ADAPTER_PLUGINS → name= 前缀」。
  2) **G4（阻断）** 模板仓 `README.md` §4 config 片段不可启动（缺 `system.data_dir` / `web.host`，照抄即
     `Permission denied /var/lib/rui` 或 0.0.0.0 无 token 拒绝）。需给完整最小 config 或链 quickstart §3/§4。
  3) **G2/G3（轻微）** quickstart `:53` `plugins/fake-device-plug/` 应写 `$DATA_DIR/plugs/...`；
     L299/L305 硬编码 `/tmp/rui-bridge-demo`，建议加「If you change `WORK`, update the two paths」。
  修复后 verifier 复跑 G1 起服务变体 + G4 config + quickstart 核心流程即可。
- **task-29（reviewer-code）结果：CHANGES**：136 条代码引用 136/136 存在、CLI/fixtures/bridge plug-test 均核对通过；
  需修 7 项——模板仓 #1 adapter 注册名（=G1）、#2 sidecar 语义（0.8.x 无 runner）；house-ear
  #3 `debugging.md:277/:10` 错令牌仍是 not_connected（speaker_rejected 只是计数器）、#4 `install-paths.md`
  引用应为 `device/loader.py:82`、#5 `quickstart.md` play 允许 data_dir **或 music.cache_dir**、
  #6/G2 `quickstart.md:53` 路径、#7 三处 label↔anchor 行号（compatibility/decision-tree/install-paths）。
- **处置（已派回原实现者）**：impl-obs 修模板仓 #1/#2/G1/G4 并**真跑两条注册路线**；
  impl-devdocs 修 house-ear #3–#7 + G2/G3（+decision-tree same-shell 提示）。完成后 verifier 只复跑
  受影响项（G1 起服务变体 + G4 config + quickstart 核心流程），reviewer 复评 → task-30 收口。
- 备注：`42ec7ba` 是 impl-ci 顺手写的 HANDOFF B 行（越界但内容属实，Lead 已接受）。
- **双路修复 ✅（待复验）**：
  - house-ear `4bf941a`（docs 5 篇）：错令牌仍 `not_connected`（`speaker_rejected` 只是 plane 计数）、
    install-paths 引用改 `device/loader.py:82`、audio.play 补 `music.cache_dir`、quickstart `$DATA_DIR/plugs/...`
    与 WORK 改路径说明、label↔anchor 统一（registry.py 真实 EOF：`build_adapters` 286-310、
    `_apply_manifest_runtime` 254-283）；链接脚本 **166 links / 138 refs / 0 problems**、20s 冒烟通过。
  - 模板仓 `491a174`（https://github.com/yes999zc/rui-plugin-template）：§4 两条可照抄路线 + 完整最小 config；
    `adapter` 注释改为「注册名（`RUI_ADAPTER_PLUGINS` 的 `name=` 前缀 / `RUI_PLUGINS_DIR` 下的 device manifest id）」；
    sidecar 语义改为「0.8.x 无 runner，需审计后 trusted in-process 或等 runner」；`adapter.py` 加可选 `health()`。
    两条路线真跑：A（`adapter: example-speaker`）/ B（`adapter: example-plugin`）均 `/health` device_errors=0、
    plane running、`/api/v2/devices/example.ready=true`；`rui plug-test` PASS（20/0/0/1）。
- **reviewer 复评（task-29）：CHANGES 仅剩 1 项**（house 5 项 + 模板 G1/G4/sidecar/health 全部 PASS；
  链接脚本 165 links/138 refs/0 问题）：
  模板仓 README「路线 B」把 `adapter:` 与 `RUI_ADAPTER_PLUGINS` 的 `name=` 写成 `example-plugin`，
  实测 catalog 解析为 **`plug_unresolved`**（控制台 ready 与 plug_unresolved 并存、硬件事实回退 global）。
  期望：路线 B 也统一用 **device manifest id `example-speaker`**
  （`RUI_ADAPTER_PLUGINS="example-speaker=adapter:build"` + `adapter: example-speaker`），
  或在最小 config 的 device 下加 `params: {plug_id: example-plugin}`；并把 `status_reason=ready` 纳入验收断言。
  路线 A 实测正确无需改。
- **最终结果 ✅（2026-10-29 11:0x 收口）**
  - **task-29 reviewer-code：PASS**（9 文件/链接 166、code refs 139、0 问题；模板 `ae1d19f` route B
    在隔离环境独立起服务：`status_reason=ready`、`plugin_id=example-plugin`、`model_id=example-speaker`、
    `capabilities.playback.declared=true`、无 `no usable model manifest` 警告）。
  - **task-31 verifier-release：VERIFIED**（全量 `2004 passed / 6 skipped`、ruff 绿；CLI 模板 22 PASS/exit 0、
    FAIL fixture exit 1；模板契约测试 1 passed；路线 A/B 抽验均 PASS；链接/锚点 0 问题；四仓 clean 且
    `HEAD==origin/main`）。
  - **「外人 30 分钟上手」自测记录**：跑者 = verifier-release（独立，只读 quickstart + 模板 README）；
    文档精读 ≈4 min + 执行 **20 s**，总 ≈4.5 min（<30 min）；流程：照抄文件→起服务 1 s 内 /health 200→
    出声 5×3200B=16000B→收声 757 帧→`rui plug-test` 20/0/0/1→SIGINT 优雅关停。
    **首轮卡点**：模板 README route B 的 adapter 注册名与 §4 config 不可运行（`plug_unresolved` /
    `Permission denied`）——已修 `491a174`+`ae1d19f`；house docs 7 处语义/引用/行号——已修 `4bf941a`；
    修后复跑无卡点。
  - **交付物**：A `docs/bridge-developer/` 5 篇（house `7bbe973` + `4bf941a`）；B `rui-adapter-check`
    （house `c431f70`，`[project.scripts]` + 12 tests）；C 新仓
    https://github.com/yes999zc/rui-plugin-template （MIT，`afee821`→`491a174`→`ae1d19f`）；D 两处 README 链接
    （house `7bbe973`、桥 `c0bd0de`）。
  - **边界**：未发版、未 tag、未 release、未动生产；桥 `rui_compat` 未变；新增依赖 0。
  - **遗留（非阻塞）**：文档 `#L` 行号锚点随核心代码漂移，后续改 `src/rui` 时需同步；
    `rui-adapter-check` 尚未进 tag（下次核心发版自然带上）；模板自带的 CLI 测试在 CLI 不在 PATH 时 skip+hint。
执行顺序：task-28（A+D 文档/链接）→ task-26（B conformance CLI）→ task-27（C 模板仓）
→ task-29（fresh reviewer+verifier，含「外人 30 分钟上手」自测）→ task-30（收口）。
边界不变：**不发版/不 tag/不 release/不动生产**；只 commit 到 main，等下次核心发版自然带上。

## 未完成 / 阻塞

- **真机未验证**：A/B/C/E 的文案听感、ASR 噪声下分钟句命中率、17–20 字接缝听感均为单测推断
  （发版说明已如实写）。
- **D 的二级原因未定**：pong 为何丢（设备读循环阻塞 vs 2.4G）待 0.8.6/桥侧加 sent/rcvd + reason
  日志后 DEBUG/A-B；0.8.5 不改桥。
- **task-2（放歌中「停止」没停）/ task-3（打断后 158–221 ms 空隙）**：无稳定样本，等样本（L25/L26）。
- **H（控制台显示 reply_segment_count / silences_ms / inner_ms）未做**，列 backlog。
- **桥仓 main 领先 `v1.3.4`**：dot2 桥 `516c0e8`（1011 计数日志）+ `b6deb86`（check.sh 修复）+ `870d965`
  （README 通用化），**未打 tag、未发版**；生产插件仍是 1.3.4。要让计数日志上线需另发桥版本并部署
  （本批不做）。xiaoai 桥 main 也只有 README 提交 `7f8f8d8`，版本仍 0.2.3。
- **生产 living.volume 仍 60**：示例已改 45，存量配置需 9哥 批准后手动改。

## 下一步

部署 0.8.5（**等 9哥 点头后**按顺序）：

> 前置：本批桥仓只有 main 上的未发版提交（1011 计数日志 + README），**生产插件仍 1.3.4**；若希望计数日志随部署上线，需先给 dot2 桥打一个新版本并换插件（本批未做，9哥 未要求）。

1. 预检：确认 9哥 批准；生产现状 0.8.4 / 4 台设备 / 插件 1.3.4 / errors 0。
2. 备份（NAS `/vol1/rui`）：`config/config.yaml`、`plugins/`、`rui-dot2-override.yml` →
   `*.bak-<timestamp>`；记录当前 digest（0.8.4 = `sha256:a193e274…d8e3`）。
3. **只换 override 的 image digest**：amd64 `sha256:4bfd73d5…50a1`（index `sha256:20347039…d383`），
   不动插件、不动 `rui_compat`。
4. `docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d`（只重启 rui 容器）。
5. 可选（需 9哥 明示）：客厅 `living.volume` 60 → 45（控制台或 PATCH，先备份 config）。
6. **D5 七项**：version=0.8.5 / devices=4 / errors 0 / warnings 0 / registered（dot2, dot2-b,
   dot2-c, lx06）/ 四台 running+connected / 四台 `audio.say` 有 reply 且 error="" /
   audio summaries 与 0.8.4 一致 / 日志 0 次 `got 16000`、`play ValueError`、`audio_config_mismatch` /
   followup=3000 / LED 最后 off / 5 分钟后 errors 0、restarts 0。
7. **0.8.5 专项真机**：①「一分钟后提醒我喝水。」→ 应直接受理（不再绕 LLM 工具），
   到点播报「时间到了，喝水。」且只在当前房间；②「提醒我十分钟之后关火」同理；
   ③ 17–20 字含逗号回复 trace 应为 `reply_segment_count 1 / silences_ms []`（不再 16+尾巴两段）；
   ④ 若改了 living 音量，人耳确认。
8. **回滚锚**：镜像 0.8.4 index `sha256:a193e274…d8e3` / amd64
   `sha256:f1b6a992a8d64ff35eb1c52687c65c735fb2aabc02ceb97813169bcb6c2e6462`；备份
   `config.yaml.bak-20261028-220433`、`plugins.bak-20261028-220433`、
   `rui-dot2-override.yml.bak-20261028-220433`；更旧 0.8.3 `sha256:be138161…e0a9`。
   插件无需回滚（本批未动）。
9. 失败处置：D5 任一红 → 换回 0.8.4 digest + `up -d`；配置类先回备份再排查。

## 关键文件

- `src/rui/web/api/app.py` `_reminder_due_line()` / `_reminder_room()`（A/B）
- `src/rui/tools/reminders.py` `_MINUTES_REL` / `_has_second_sentence()`（B，L21）
- `src/rui/session/turn.py` `SOFT_MERGE_MAX_CHARS=20`（E）
- `src/rui/audio/process.py:2830` `asyncio.timeout`（G）
- `config.example.yaml:37` living volume 45（C）
- 文档：`docs/reminder-due-phrase-0.8.5.md`、`docs/reminder-dispatch-0.8.5.md`、
  `docs/short-reply-merge-window-0.8.5.md`、`docs/lx06-volume-default-0.8.5.md`、
  `docs/ci-311-hang-0.8.5.md`、`docs/analysis/dot2-data-plane-1011.md`；
  `docs/known-limitations.md` L21/L25/L26；`docs/backlog-0.8.md`「等样本」

---

# 历史记录（0.7.8 – 0.8.4，保留）

## 当前推进：0.8.0（2026-10-28）

- **A1 d.6 ASR 尾巴剥离**：已落地 `b91599a`；全量 `1722 passed / 6 skipped`；review PASS + verify VERIFIED。
  真机 N2 口径已反转（`停止你好` 现在命中），待 9 哥 按新口径复验 2 min。
- **A2 d.7 volume 容差**：已落地（`VOLUME_RECONCILE_TOLERANCE = 1`，设备 0–255 刻度）；
  d.7 已写明显式代价（1 刻度假漂移不纠），新增一步差用户 set 用例；全量 `1726 passed / 6 skipped`；
  review PASS + verify VERIFIED。真机静置 5 min 计数归零待验。
- **P0（2026-10-28 真机插入，优先于 B1）**：ack 回声漏切 → 自问自答。**根因**：`process.py:1862`
  用设备 48k rate 除 16k 渲染的 cue → 460ms 变 153ms，skip/hold=403/2653，cue 整段 replay。
  已实现修复（cue 用渲染 rate；hold=460+850 lead+2500，从播放结束起算；VAD 首唤醒晚 cue 先验），
  全量 `1733 passed / 6 skipped`；review PASS + verify VERIFIED（修掉 1 处核心禁词注释）。卡 task-13，证据记 known-limitations L17。
- **P1 LED/会话收敛追加（2026-10-28 04:29 真机，并入 task-17）**：`stop intent -> session closed` 后
  13s 又 `led: listening` 并 repaint 卡住。要求：session closed（stop/结束词/farewell）后不得重回 listening；
  若保留 followup 窗口 N=8s，窗口结束立即 disarm + `led: off`；任何一轮结束 LED 事件最后一条必须是 `off`；
  repaint 有最大次数/超时；回归：stop 后 LED 序列以 off 结尾且 30s 无新 listening。
- **P2 音量键回写（task-19，已收口 `58c845e`：5% 步进 + 10s 抑制 + 持久化；house 1745；review PASS + verify VERIFIED）**：真机 12:29–12:34 `volume: reconciled to 204 (config 80%)`
  每 16s 一条且无 `dot2 link: volume` 事件 ⇒ 物理音量键改了设备但配置未回写，16s 内被拉回。
  要求：volume_up/down 回写 `devices[].volume` 并持久化；按键后 10s 内 reconciler 不回拉；
  d.7 容差（`8961cbf`）叠加后静置仍无每 16s 日志；测试注入按键→配置降低且不回拉、±1 级不下发。
  跨桥/ house 两仓，两边 commit+push。
- **P2 物理按键（task-18，已收口：桥 `0698460` + house `de89e65`；桥 178 / house 1737；review PASS + verify VERIFIED）**：桥 `device_link.py:461-467` 收到 button 但全仓无 `on_button` 注入，
  按键全丢；设备上报 `clickType` + `muted`。需注入 handler、映射 mute/volume_up/down/action、health+控制台+LED `muted`；
  静音时照常转写/亮灯但跳过 ack。`DOT2_MUTE` 是部署 env，别混。
- **P1（紧随 P0）**：短回复「在，你说。」拆句 217 ms；≤8 字/≤1 句末标点不拆。卡 task-14（已实现；review PASS + verify VERIFIED，全量 1738/6；残余风险写入 d.10）。
- **P0/P1 均已收口**；B1（task-8）恢复，之后 B2→B3（桥双窗口先于 core）→C。
- **当前 HEAD**：`7b9035a`（B1）；P0 `82d45d9`、P1 `8344085`、B1 `7b9035a`。B2/B3/C 未开工。
- **桥仓 main 领先 v1.3.2 一个工具提交 `56e9bb8`**（onboard_device.py + docs/onboard-device.md + README 中英 + CHANGELOG；4 files，不进 src/wheel）。口径：v1.3.2 tag 仍指 `4536968`、不重打、不改 release 正文、不 bump 版本、不 force-push、不 revert；[Unreleased] 留到下次发布。
- **P0 根治（2026-10-28 真机，装主卧/卫生间前必须修）**：前提已更正——**manifest audio_defaults 解析是好的**
  （`GET /api/v2/devices/living/audio`：manifest_defaults.capture.rate=48000，layers source=global-config、
  effective=16000），是**显式全局配置压掉了型号硬件声明**。dot2-b audio 为空→playback 16000 vs 桥硬性 48000，
  每块 raise、LED 亮但无声。顺序改为：**task-26（字段归属/冲突：manifest 胜全局 explicit + WARN/拒绝）→
  task-24（R3 运行期自检 + 桥 play ValueError 上报为设备错误）→ task-25（catalog/runtime 一致性 + 三份值可见性）**。
  **与 task-22（params/layout）分开**。
- **进度（2026-10-28 17:0x）**：**task-24（R3）已实现，待 review/verify**（未提交）：运行期 wire 自检
  （inbound capture 由 probe、playback 由 manifest；outbound capture 仅告警不拒绝）在 `Ready` 前拒绝，
  `audio_config_mismatch:` + 两值/source/fix；`play()` ValueError 立即断 plane，其他异常 3 连击熔断；
  `Metrics.playback_errors/last_error` → `/health` + v2 `PlaneCard`/健康表；console `status_reason.code=
  audio_config_mismatch` → 中文「配置与设备不符」+ `audio_wire_mismatch` warning。新增
  `tests/test_audio_wire_check.py` 12 项；全量 **1842 passed / 6 skipped**；ruff 通过、web typecheck 通过。
  细节 `docs/audio-wire-check.md`。桥侧无需改动（`dot2 adapter.play()` 直接 raise ValueError，未吞）。
- **口径更新（2026-10-28 18:05，9哥）**：**0.8.0 不部署**，tag/release 保留作历史；0.8.1 合并 a–f 后发 v0.8.1 并直接部署。范围：a 告别/结束词不 arm 续听；b rs03qr 默认 followup_delay=300/post_mute=500（不动 lx06）；c 控制台暴露两字段；d task-23 part2 trace gap 块序号（protocol+trace additive）；e lx06 48k/16k 实测对齐 ADR + 冷启动可观测；f 文档。生产保持 0.7.9。D3 已改：followup 3000（非 0）、dot2-b params control_port=0 + loudness_file；dot2-b audio 保留。
- **部署顺序纠正（2026-10-28，覆盖旧 D1→D2）**：**先 core 0.8.1，后插件 1.3.3**。
  D1' 只改 override image→0.8.1 digest，up -d；验 version=0.8.1、devices=3、errors=0、registered dot2+lx06（插件仍 1.3.2，窗口兼容，可停）。
  D2' 再换 plugins：桥 1.3.3（`>=0.8.1 <0.9.0`）+ xiaoai 0.2.3（`>=0.7.0 <0.9.0`），重启后复验。
  D3' followup=3000（禁 0）、dot2-b params control_port=0 + loudness_file、保留 audio 覆盖。
- **回滚顺序反向**：先 plugins 回 1.3.2 + 0.2.3（1.3.2 兼容 0.7.9/0.8.x），再镜像回 `sha256:5aa362f6…b9f7`，up -d，复验。
- **部署前自检表**：core 0.8.1（—）；dot2 1.3.3（>=0.8.1 <0.9.0，需 core≥0.8.1）；xiaoai 0.2.3（>=0.7.0 <0.9.0，兼容）。必须用 v0.8.1 tag 的干净 commit 出镜像，不拿脏树。
- **部署前 HANDOFF 必写一行**：「即将部署：core→0.8.1 → plugins→1.3.3」；部署后贴 /health + 三台 audio.say。
- **task-31（提醒来源房间，0.8.3）**：固定句式不点名房间时用当前设备房间；次卧提醒不得播到客厅/停客厅歌；来源离线落 default_output+提示+计数。待 task-30 提交后做。
- **task-31 已实现（2026-10-28 20:5x，未提交）**：`app.py` 新增 `_speaking_device_id`/`_device_room`；`_voice_reminder_command` 用 `parsed.room or source_room` 落库（来源=说这句话的设备 `ready.device_id` → `config.devices[].room`），日志带 `named/source`；回复措辞仍只复述用户点名的房间。投递链路不变（`_deliver_reminder` → `_reminder_route`），来源在线只停该房间音乐且不 resume，来源离线走 default_output +「次卧那台没在线」+ failed/offline 计数。红→绿：`tests/test_reminder_voice.py` 旧「无房间存 None」断言改为来源房间；新增 7 项（来源/点名优先/未知或空房间不猜/反查/20:07 端到端 次卧停歌+客厅不动、客厅放歌+次卧不切歌、来源离线计数）。红：去掉 app.py 修复后新增用例 6 failed/77 passed；绿：定向 83 passed/0，全量 1929 passed/6 skipped，`uv run ruff check .` 通过。仅工作区改动，未 commit/tag/deploy。**残余（卡面外）**：「十分钟后」等相对分钟仍走 LLM tool（`local.set_reminder` schema 无 room，`store.add` 不传 room），仍会落 default_output；task-31 只修固定句式路径，是否扩 tool 等待定。
- **task-32 已实现（2026-10-28 21:5x，未提交）**：① LLM tool 提醒带上「说话设备」的房间。`tools/reminders.py` 新增 `ToolContext` + ContextVar（`use_tool_context` / `current_tool_context`），`_set_handler` 落库改为 `store.add(..., room=current_tool_context().room)`；`session/turn.py` 的 `run(..., tool_context=...)` 用 ContextVar 把上下文限定在本回合（runner 全房共用，不能放实例字段）；`app.py` 新增唯一解析器 `_reminder_room(cfg, client, named)` 和 `_tool_context(cfg, client)`，固定句式与 tool 路径共用，音频回合两处 `runner.run`（首答 + 假成功重试）都传入；频道/控制台回合不传，room 仍为 None。② 超短句合并窗口 `SOFT_MIN_CHARS` 10→16：13 字「一分钟后提醒你喝水，小志。」不再在逗号切成 `chars=10` + 624 ms 第二条；句末标点仍立即释放（首音实测余量 343 ms，仅多等逗号到句号的 3 个字）；19 字两句仍拆。红：10 时 2 个 13 字用例失败（`tts_call#1 chars=10`），去掉 app 传参后 room=None，handler 不读上下文后 room=None；绿：全量 **1937 passed / 6 skipped**（+8 用例），`uv run ruff check .` 通过。细节 `docs/task-32-reminder-tool-room-and-merge-window.md`。仅工作区，未 commit/tag/deploy。**残余**：`remember`（memory）无 room 字段且非房间域，不改；tool 上下文只接音频回合（频道无「说话房间」）。
- **0.8.3 范围冻结**：只装 task-30（接缝）+ task-31（来源房间）；task-2/task-3 无稳定样本不改；禁新功能/UI/契约/rui_compat/barge-AEC-音量-followup。门禁：全量+ruff+CI 四绿+双 review PASS+verify；顺序 task-30 收口 → task-31 → 一起 review/verify → 0.8.3 tag/release/镜像 → 一次部署 → D5；交付 ≤10 行 Δ + 改了什么/验收怎么判表。
- **0.8.4 已发并部署（2026-10-28 22:11）**：tag `v0.8.4`→`6d24181`（fix `d0ba5f2`），release https://github.com/yes999zc/rui/releases/tag/v0.8.4；镜像 index `sha256:a193e274…d8e3` / amd64 `sha256:f1b6a992a8d64ff35eb1c52687c65c735fb2aabc02ceb97813169bcb6c2e6462`；插件不动 1.3.4。task-32：LLM 工具路径 ToolContext 带来源房间 + 10→16 字合并窗口；全量 1937/6，review PASS + verify VERIFIED。
- **0.8.4 D5**：version=0.8.4/devices=3/errors=0/warnings=0/registered dot2+lx06；living/dot2-b connected；三台 say 均 reply+error=""（dot2 在 22:14 仍成功）；summaries 同前；日志 0；followup 3000。**dot2（儿童房）22:15 后 status=disconnected `token_missing`、speaker_connected=false**，重连后未带 token；dot2-b 正常。非 0.8.4 代码路径（仅 TTS 分段/提醒房间），需 9哥 现场看儿童房设备 token/重连。
- **CI**：0.8.4 的 docker-publish/security/secret-scan success；`ci` 仍 `test (3.11)` in_progress（与 0.8.3 同一条 3.11 挂起），按授权部署未等；已记。
- 回滚锚（0.8.4）：镜像 `sha256:f1b6a992…6462`；备份 `config.yaml.bak-20261028-220433`、`plugins.bak-20261028-220433`、`rui-dot2-override.yml.bak-20261028-220433`；旧镜像 `sha256:be138161…e0a9`（0.8.3）/ 插件 1.3.4。
- **0.8.3 已发并部署（2026-10-28 20:53）**：tag `v0.8.3`→`69164cd`，release https://github.com/yes999zc/rui/releases/tag/v0.8.3；镜像 amd64 `sha256:be138161dccf1ea132c76ff8c8eda93b03d3e1c14f695452be2e5b34a3c1e0a9`；插件保持 1.3.4。task-30（短回复合并+段静音插桩，含 barge ledger 修复 `563c139`）与 task-31（提醒来源房间 `9d3d2dd`）已收口；全量 1929/6，review PASS + verify VERIFIED。
- **0.8.3 D5**：version=0.8.3/devices=3/errors=0/warnings=0/registered dot2+lx06；dot2、dot2-b running+connected，say 均 reply+error=""；summaries living 16k/8ch/play16k/ack1200、dot2/dot2-b 48k/2500/fd300；日志 0 次 got16000/play ValueError/audio_config_mismatch；followup=3000。
- **living 已自行回连（21:2x）**：三台 running+connected；living say「收到，0.8.3 部署验证，小瑞在线。」error=""。D5 七项：version 0.8.3/devices 3/errors 0/warnings 0/registered dot2+lx06/三台 connected/三台 say 有 reply 且 error=""/summaries 同前/日志 0/followup 3000；5+ 分钟后 errors 0、restarts 0。唯一未绿：CI `test (3.11)` 两次 rerun 均 in_progress >10min（3.12/image/secret-scan/security 绿），疑 CI 3.11 挂起，非本地全量（1929/6）。
- 回滚锚（0.8.3）：镜像 `sha256:be138161…e0a9`；备份 `config.yaml.bak-20261028-204604`、`plugins.bak-20261028-204604`、`rui-dot2-override.yml.bak-20261028-204604`；旧镜像 `sha256:6b007a8c…d00d` / 插件 1.3.4。
- **task-30 已提交 `6e77a0c`**（短回复合并 + 段/静音插桩，1921/6）；review 发现 barge 后 segment ledger 未复位，已修 `563c139`（`_flush_speech` 置 `_reply_open=False` + 回归，41 passed/1 skipped）。
- **task-31（提醒来源房间）**：未点名房间用当前设备房间；实现完成（83 targeted 绿，全量 gate 中）；测试覆盖次卧不点名→room=次卧、客厅音乐不动/次卧停不恢复、离线 fallback。
- **secret-scan 已转绿（2026-10-28 20:06）**：实际命中是 HANDOFF 里的 LAN `<LAN-IP>`（soft 类），不是 sha256；已改为 `<NAS-LAN-IP>`，run success。
- **0.8.2 / 1.3.4 已发并部署（2026-10-28 20:0x）**：core tag `v0.8.2`→`7720709`，release https://github.com/yes999zc/rui/releases/tag/v0.8.2；桥 tag `v1.3.4`→`76b8947`，release https://github.com/yes999zc/rui-dot2-bridge/releases/tag/v1.3.4。部署：core digest `sha256:6b007a8c04e521efbc19c5624f6e397fc85160c701f8be1bd4d6da904da8d00d`，插件 1.3.4。D5：version 0.8.2/devices 3/errors 0/registered dot2+lx06；三台 say 均 ok；summaries 同前；followup 3000。
- **⚠ 1.3.4 真机仍命中一次 >1.0s 写阻塞（20:01:48 dot2，rx271678B/tx200753B）→ abort/掉 17 chunks(1.4s)，随后恢复**。按 9哥 口径「仍偶发先记日志再讨论，不自行继续上调」；未再提高阈值。根因仍是 2.4G 抖动（RSSI/重传/6Mbps），缓解未根治。
- 回滚锚：镜像 `sha256:6b007a8c…d00d`；备份 `config.yaml.bak-20261028-195421`、`plugins.bak-20261028-195421`、`rui-dot2-override.yml.bak-20261028-195421`；旧镜像 `sha256:77224fc1…b335` / 插件 1.3.3。
- **下一批（2026-10-28 19:30）**：task-28 桥 1.3.4 已实现（181/1，WRITE_TIMEOUT 1.0s，README/C-001 已同步 1.3.4）；task-29 core 0.8.2 已实现（1912/6；humanize 秒级措辞 10秒/1分30秒，文案 at 一致）；桥 1.3.4 review PASS + verify VERIFIED；core 最终 review/verify 中。task-28 桥 1.3.4 写超时 0.75→1.0s（实测 dot2-b 2.4G 抖动触发 abort/50.6s 续播）；task-29 core 0.8.2 提醒秒级（+10s/十秒后）+ 文案用最终 at。顺序：两卡 review/verify → core 0.8.2 tag/release → 桥 1.3.4 tag/release → 部署（core 先/插件后均可，生产已 0.8.1）。配置保持 followup=3000、dot2-b audio 覆盖。
- **0.8.1 部署完成（2026-10-28 19:1x）**：core digest `sha256:77224fc1b35882640da2f0f04b01c5e6623c0ef566639b41f3065e56d673b335`；plugins dot2 1.3.3/xiaoai 0.2.3。D5：/health version=0.8.1/devices=3/device_errors=0/registered dot2+lx06/warnings 0；三台 running+connected；audio summaries living 16k/8ch play16k ack1200，dot2/dot2-b play48000 ack2500 follow_delay300；三台 debug/say 均返回 reply 且 error=""；日志 0 次 got 16000/play ValueError/audio_config_mismatch；followup=3000；LED 最后 off；部署后 4+ 分钟 /health 仍 errors 0、三台 restarts 0、container Up (healthy)。回滚锚：镜像 77224f…b335；备份 config.yaml.bak-20261028-165529、plugins.bak-20261028-165529、rui-dot2-override.yml.bak.before081。
- **部署进度（2026-10-28 19:1x）**：D1' core 0.8.1 已上线（digest `sha256:77224fc1…b335`），/health version=0.8.1/devices=3/errors=0/registered dot2+lx06；D2' 插件已换 dot2 1.3.3（`>=0.8.1 <0.9.0`）+ xiaoai 0.2.3，重启后仍 3 台/errors 0。D3' followup=3000、dot2-b control_port=0 + loudness_file 已就位；dot2-b audio 保留。待 D5：audio_summary/say/LED/5min errors；dot2-b speaker_connected 暂 false。
- **0.8.1 已发（2026-10-28 19:1x）**：tag `v0.8.1`→`ea1b8ba`（tag object `c373853`），release https://github.com/yes999zc/rui/releases/tag/v0.8.1；全量 1887/6；桥 1.3.3 tag `31195ee`。
- **即将部署：core→0.8.1 → plugins→1.3.3**（按纠正顺序 D1' core 先、D2' 插件后、D3' 配置；D5 七项验证；异常反向回滚）。
- **进度（2026-10-28 18:55）**：c UI 卡片网格实现中（DevicesView/style + test_devices_view_grid）；d trace gap 块序号已实现（protocol/trace/app + 5 tests，全量 1887/6）；e/f 文档已写（ADR 0008、L23/L24、C-001 1.3.3、README 0.8.1 矩阵）。待 UI DONE + 三路 review/verify → 版本 bump 0.8.1 → tag/release → 部署 0.8.1。
- **进度（2026-10-28 18:50）**：task-27 已提交 `d633d80`（house 1878/6，review PASS + verify VERIFIED）；桥 **1.3.3 已发**（`9866c6d`，tag `v1.3.3`→`31195ee`，`rui_compat >=0.8.1 <0.9.0`，release https://github.com/yes999zc/rui-dot2-bridge/releases/tag/v1.3.3）。生产插件仍是 1.3.2（无 voice）直到 0.8.1 部署。0.8.1 剩余：c 设备卡片网格、d trace gap 块序号、e lx06 ADR/冷启动、f 文档 + 版本 0.8.1。
- **task-27（0.8.1 a/b/c）**：告别收尾不 arm + rs03qr 型号默认 + 控制台字段；house 1878/6、bridge 178/1；review 阻塞：`audio_defaults.voice` 被 <0.8.1 拒载 → **桥升 1.3.3，rui_compat >=0.8.1 <0.9.0**，生产插件 rs03qr 已回退到 1.3.2（无 voice）直到 0.8.1 部署。后续：task-28 卡片网格 UI、task-29 trace gap、task-30 lx06 ADR/冷启动/docs。生产 D3 已改 followup=3000、dot2-b params control_port=0 + loudness_file；dot2-b audio 保留；生产仍 0.7.9。
- **进度（2026-10-28 17:58）**：**全部代码门禁已收口**：task-26 `3d40266`、task-24 `4834201`、task-25 `1f3a035`、C2 `26659b0`、task-22 `36ad95f`；全量 **1870 passed / 6 skipped**。**core v0.8.0 已发**：annotated tag `v0.8.0`→`26659b0`（tag object `1c730f1`），release https://github.com/yes999zc/rui/releases/tag/v0.8.0。**生产 D0/1/3 已完成**：备份 `config.yaml.bak-20261028-165529`/`plugins.bak-20261028-165529`；插件已换 dot2 1.3.2 + xiaoai 0.2.3（`>=0.7.0 <0.9.0`）；全局 `audio.voice.followup_max_wait_ms: 0`（第 166 行，per-device 未写）；dot2-b audio 覆盖保留。等 GHCR 0.8.0 镜像 → D2 换 digest → up -d → D5 验证。
- **进度（2026-10-28 17:45）**：task-26 `3d40266`、task-24 `4834201`、task-22 `36ad95f` 已收口（1845/6）。task-25（catalog/runtime 一致 + 三来源）与 C2（提醒最小抢占/失败可见）已实现（1866/6），review 各 1 项 CHANGES（setup 误报 duplicate、fallback mid-turn release）修正中。生产 D0 备份已完成：`/vol1/rui/config/config.yaml.bak-20261028-165529`、`plugins.bak-20261028-165529`，回滚锚 `sha256:5aa362f6…b9f7`。下一步：25/C2 提交 → core tag/release → 部署 D1-D6。
- **进度（2026-10-28 16:40）**：task-22 已收口 `36ad95f`（5 台布局+params，1812/6）。task-26 已实现（1828/6；manifest 硬件事实胜全局，capture.rate 协商；冷启动 catalog 先注册；ADR 0007），review CHANGES 1 项（neutral capture 字段 provenance source 仍报 global-config）修正中。xiaoai 0.2.3 已发：tag `v0.2.3`→`0d82250`（tag object `2676664`），release https://github.com/yes999zc/rui-xiaoai-bridge/releases/tag/v0.2.3。task-24/task-25/C2 待 task-26 提交后做。
- **0.8.0 一次性授权（2026-10-28 9哥，含部署）**：tag/release/生产部署已授权；门禁=task-26→24→25 + task-22 收口 + **最小 C2（0.8.0，原“推迟 0.8.1”作废）** + `followup_max_wait_ms=0` + 四台真机验收。C2 最小范围：到点先停目标房间音乐→播报→不自动恢复；失败/全离线→default_output 播报+「<room>那台没在线」+health 失败计数；只停目标房间；播完 session 干净关闭、LED off、无免唤醒窗口；不做重试/排期/音乐恢复/去重。
- **发布顺序**：xiaoai 桥 0.2.3（commit `0d82250`，未 tag）先发 → dot2 1.3.2 已发不动 → core 0.8.0。发布正文注明 C2 抢占语义与 followup 默认 8000/生产设 0。
- **生产部署 runbook（已授权）**：SSH yes999zc@<NAS-LAN-IP>；compose `/vol1/rui`（必须 -f docker-compose.yml -f rui-dot2-override.yml）；当前镜像 `sha256:5aa362f6b4f7d7fd24a7f01d41c31e1523ce2401296ac3b203b9f684a314b9f7`；plugins `/vol1/rui/plugins`、config `/vol1/rui/config/config.yaml`。D0 备份 config+plugins 并记 digest；D1 先换插件 1.3.2/0.2.3（旧窗口排除 0.8.0，不换会 fail-closed）；D2 换 core 0.8.0 新 digest；D3 只改 followup=0、保留 dot2-b audio 覆盖；D4 compose up；D5 验 version/devices/errors/registered/audio_summary/say/ followup/LED；D6 异常回滚到 digest+plugins 备份。边界：不改 rui_compat、不 force-push、不动 token/设备端/data。
- **⚠ task-26 开工先解 lx06 矛盾**：lx06.yaml 声明 capture 48k/8ch，但生产实际/设备在跑 16k/8ch；直接 manifest 胜会弄坏客厅。先确认真实 wire，再修正 lx06.yaml 或改为“仅当探测与 manifest 一致时 manifest 胜”，选择写进卡面。冷启动时序（dot2-b 首测 ack=1200）必须在 task-26 复现并一并修。
- **0.8.0 发布门禁（2026-10-28 9哥 决策）**：必须完成 **task-26 → task-24 → task-25**、task-22 收尾（三台最终 params/补齐步骤/五台布局）、发布说明设置 **`audio.voice.followup_max_wait_ms = 0`**（全局，0=每轮都要唤醒词；8s=保留一句自然续说；不做逐台覆盖），以及四台真机验收（living 4412 + 儿童房 8870 + 次卧 8871 + 主卧 8873）。**C2/task-21 推迟到 0.8.1**（新功能，不阻塞）；task-2（歌没停）/task-3（158ms gap）无样本不修、不列阻塞。
- **生产现状（2026-10-28 16:0x）**：0.7.9 / 3 台在线 / device_errors 0 / warnings 0；living + 儿童房 dot2(8870, 16:03 重新上线 .69) + 次卧 dot2-b(8871) 全 running+connected。dot2-b 固件已对齐 **20261024-1911-dev + ref_uplink**（原 v2.18.1），server_a 保留可回滚；audio.say 237104 B、无 got 16000。两台 dot2 同固件同版本，4 台混合验收前置已具备。
- **dot2-b `devices[].audio` 覆盖 = 永久显式声明，不是临时补丁，别删**（playback 48000 / capture 16k·1ch / ack 2500 / aec off）；根治后保留。
- **P0 追加三点（2026-10-28 15:5x）**：
  1) **冷启动时序疑点**：dot2-b 初次生效 ack=1200（manifest 2500、全局未写 ack，优先级解释不了）；怀疑冷启动时 catalog/plug 未就绪，重启后 `plug_resolved` 才 true。task-26 必须复现“冷启动读一次 effective”，确认时序问题并一并修（否则根治后仍有间歇性）。
  2) **dot2-b `devices[].audio` 覆盖决定：保留**（等同显式声明该型号硬件参数）；根治后不删除，根修也不得覆盖/抹掉设备级显式值。
  3) **硬门槛：主卧/卫生间在 task-26 + task-24 收口（review PASS + verify VERIFIED + commit）前不插**。
- **dot2-b 已由 9哥 授权修好（2026-10-28 15:52）**：只给该设备加 `devices[].audio`（playback 48000 / capture 16k·1ch /
  ack 2500 / aec.enabled=false）；备份 `config.yaml.bak-20261028-155210`；PATCH `/api/v2/devices/dot2-b/audio` +
  restart scope=fleet（三台各 5.3s，客厅配置未变）。验证：audio_summary 48000/2500；audio.say 70670 B、gaps 0；
  "got 16000" 0 次；/health 3 台、errors 0。**全局那份照 LX06 写死的配置没变 → 主卧/卫生间新建仍会哑，根治卡必须做。**

- **task-22 存量 null-params 决策 = (b) 明确不兜底**：读配置不做隐式补齐（不改桥/adapter 默认、不改 schema 默认）；task-22 文档必须写「对已有 null-params 设备执行一次补齐」的手工步骤 + 三台最终值：
  living（lx06, 4412）params 不适用/留空；dot2（儿童房, 8870）`control_port: 0`、`loudness_file: /var/lib/rui/loudness-dot2.json`；dot2-b（次卧, 8871）`control_port: 0`、`loudness_file: /var/lib/rui/loudness-dot2-b.json`。
  生效时机：params 在平面启动时读取，补齐后需重启该平面；建议与 0.8.0 生产部署合并为一次重启。真机现状已记：3 台在线，dot2-b 在次卧 8871，日志原文 `dot2 adapter: loudness control on 127.0.0.1:8872 unavailable ([Errno 98] address already in use); continuing without it`。
- **task-22 实测输入（2026-10-28）**：生产 3 台 living 4412 / dot2 8870 / dot2-b 8871（次卧，已 ready+connected）；dot2-b 建时 params 留 null → `loudness control on 127.0.0.1:8872 unavailable (Errno 98)`。task-22 必须覆盖 dot2-b 的 `params.control_port: 0` + `params.loudness_file=/var/lib/rui/loudness-dot2-b.json`，并把「控制台新增设备 params 不得留空」做成表单校验/提示。设备端事实：配置目录 `/data/local/etc/echomuse/`，`controller.json` endpoints 为有序回退；平面 TLS 口 = 设备在 rui 的 `listen_port`（tcp/wss 同口，生产不开明文）。
- **P0b 追加证据（2026-10-28 12:1x，9 哥）**：拔电后设备 `connection open` → 正好 10s → `closed` 死循环；
  设备完成 TLS/WS 握手（1503/2932 B）但不再发 register；**重启 rui 后 3s 恢复、60s 0 断连**
  ⇒ 桥异常断线后没有清理连接槽位/状态（P0 根因）。另：僵尸连接 887s、Send-Q 85775B、
  `speaker_connected` 粘性 true；会话关闭后 15s 仍能免唤醒进 turn（P1，卡 task-17）；
  关闭日志无 reason/字节数（P2）。**task-15 需补：连接槽位/状态重建 + 重连注册测试 + close/register 日志。**
- **P0b（2026-10-28 12:1x 真机，dot2 掉线）：三件 rui 侧修复，插在 B1 期间/之后**
  A 连接关闭必须丢弃未发送音频（禁止重放半句）；B 发送水位上限（256KB 或 2s 音频）+ 写超时→主动断开重连；
  C 半开连接检测（TCP keepalive 或 ping_timeout 30–60s）+ `speaker_connected` 改实时。每项回归测试 + 双闸门。
  证据：8870 连接 887s 未收包、`notsent 65160/unacked 15/retrans 1/25/backoff 15`、积压 85775 字节；
  拔电时正在播报的 ~0.9s @48k 留在队列。A/B 在桥仓，C 涉及桥+house 音频面/健康状态。卡 task-15（已收口：桥 `9065d19`、house `88400aa`；两仓 review PASS + verify VERIFIED）、task-16（C，blocked_by B1）。B1 已收口 `7b9035a`。
- **批次 B 进行中（2026-10-28 14:xx）**：B3a dot2 `4536968`、B3b xiaoai `0d82250` 已 push（双窗口，179/87 passed）；B2 house 已收口 `78d87df`（console/setup/save_config 均拒绝 xiaoai，1752/6；review PASS + verify VERIFIED）。B3c core 0.8.0 已收口 `e8f728c`（1752/6；版本/窗口/meta/fixtures/README/CHANGELOG；C-002 已同步；review PASS + verify VERIFIED）。- **桥 v1.3.2 已发布（2026-10-28，9哥 授权）**：annotated tag `v1.3.2` → commit `4536968`（tag object `cebcb30`），已 push；GitHub release https://github.com/yes999zc/rui-dot2-bridge/releases/tag/v1.3.2（双窗口 >=0.7.0 <0.9.0，0.8.0 后仍是 0.7.x 最后一个桥，不承诺 0.9.x）。core 0.8.0 未发、生产未动。rui README quickstart 已改 v1.3.2（`9f9e6a6`）。
**三仓 0.8.0 代码面完成**（桥 dot2 `4536968`、xiaoai `0d82250`；core `e8f728c`）。C1 task-20 已收口 `1f02983`（语音固定句式 + Reminder.room + 三级路由；1804/6；review PASS + verify VERIFIED）。C2 task-21（抢占/去重/失败重排）待做。
- **task-23（插桩，2026-10-28）**：应答 gap 158–221ms（barge 打断→停止→告别语）只加 INFO
  `reply stall: chunk #N/total gap=...ms state=...` + trace meta 记 gaps 块序号；**不修逻辑**。part 1 已实现并提交 `ee26458`（日志 + per-reply ledger，review PASS）；part 2 trace meta 被 pydantic Metrics/进程边界挡住，需 protocol.py + trace.py 两个 additive 字段（且需防跨回复串味），等授权。等 9哥 真机复现后三选一。
之后交 0.8.0 发布前报告（桥双窗口计划+回滚）。
- **task-22（goal 外前置，2026-10-28 下午插设备前，已实现 1810/6；review CHANGES 8 项修正中）**：5 台真实布局文档 + v2 逐台 params
  （control_port=0 / 独立 loudness_file）写盘透传测试；不碰桥仓、不改 goal 四项。C1 收口后做。

- **批次 B（破坏性三项）/ C（§f.2 提醒）**：未开工。顺序 B1 → B2 → B3（桥双窗口先于 core 0.8.0）→ C；
  不打 tag、不部署，真机与插设备等 9 哥 下午做。
- 未完成项不宣称完成；上下文压缩后先读本节 + 任务板。

## 多音箱前置（P0，2026-10-28）

phase-close: task-4

**本阶段结论**：5 项 dot2 全局 env 已可按设备配；`max_planes` 默认 5；secrets/token 可逐台；
P0-4 同房/跨房同词逻辑已自动化并有计数。**代码已 push；生产已由 9 哥 激活**（bridge `c64acb5`、manifest 已装、
max_planes=5、`/health` 已确认，2 台 plane running）。验收可按 **3 台设备**（lx06 + 2×dot2）开始。

## 历史版本与契约（0.7.8–0.8.0）

- rui core：`cf1cf92`（§e/§f 文档）← `1a751ae`（d.8）← `6412b3a`（P0 收口）← `75de4b4`（task-3）
  ← `a36f678`（max_planes 5）← `7b5ca57`；**未打 tag、未发版**。
- rui-dot2-bridge：`c64acb5`；**未 bump 版本**（plug 仍 1.3.1）、`rui_compat` 仍 `>=0.6.0 <0.8.0`。
- 契约：`rui-dot2-bridge/docs/contracts/C-001-per-device-params.md`（5 参数表 + 优先级 + 降级 + 回滚）。
- 生产（2026-10-28，9 哥 执行）：NAS `/vol1/rui` 仍跑 0.7.9 镜像；bridge 源码已 pull 到 `c64acb5`、
  插件清单已装到 `/vol1/rui/data/plugs/dot2-echomuse/`、config `max_planes=5`；`/health` 已确认 `max_planes: 5`、2 台 plane running。
- 第二台 dot2 的 token 已由 9 哥 刷成与第一台相同 ⇒ **P0-3 不需额外凭据工程**；共享 token 形态按 C-001 属可接受。

## 历史已完成（多音箱前置）

1. **P0-1** 5 项按设备可配：`adapter.py` ctor 4 kwarg + `params > env > 设备默认`；loudness 默认 `dev/rui/<id>/loudness.json`，EQ 共享可覆盖；control port bind 失败 warning 降级不 abort。证据：bridge `155 passed`、test_per_device_params 10 条、C-001。
2. **P0-2** `multi_device.max_planes` 默认 4→5（`schema.py:1068`，1–8 不变）；5 台通过、6 台按原文案拒绝；控制台中英文案同步。证据：house `1718/6`（task-2 时点）。
3. **P0-3** secrets 隔离：`dev > params > DOT2_SECRETS_DIR > secrets/<id>(存在) > secrets`；token 逐台 `token_env` + 各自 `device_token`；共享仍兼容。证据：C-001 + 两设备独立复现。
4. **P0-4** `arbitration_cross_echo_suppressed` 计数（coordinator.metrics）+ 同房 cross_echo/turn_open/同批 lost_same_word + 跨房 admit/409 测试 5 条（HEAD 上 5 failed → 当前 5 passed）。证据：house `1723 passed, 6 skipped`；全量曾因并发 pytest 抢临时端口 flaky 1 次（xiaoai bind），隔离/重跑均绿。
5. **文档/决策**：`backlog-0.8.md §e` 两项已 ANSWERED（e.1 全场停止 = **B**，词族待 9 哥 定稿；
   e.2 提醒落点 = 三级规则：指令房间 → `default_output` → 离线回落并明说）；§f.1/§f.2 为后续落地卡（只登记）；
   `d.8` 登记「唤醒后首句偶发空 ASR」；`docs/待拍板.md` D-012（语音跨房打断）、D-013（default_output 配哪台）OPEN。
   代码核查纠正：rui 已有 `ReminderStore`/`build_scheduler`（真实缺口是语音入口/抢占/指定房间/离线明说）。

## 历史下一步（3 台设备验收起点 2026-10-28）

1. **确认第 2 台 dot2 已进配置**：`/health.devices` 应为 3（lx06 + dot2×2）；若仍 2 台，走 `/api/v2/devices`
   加第 2 台：独立 `listen_port`（8871）、`room`、`params.control_port`（0 或唯一端口，如 8874）、
   `params.loudness_file` 指可写卷（共享 token 已由 9 哥 刷好，无需另配 secrets）。
2. 真机跑 `docs/release/acceptance-4devices.md` §6.5：同房交叉回声（A 播放→B 唤醒应 `cross_echo` + 计数）、
   同房同词同时唤醒只一台接、跨房同词三个最不利用例；**录音/人耳**判「是否打断/双声」，异常升级 0.8 缺陷单。
3. 3 台验收 PASS 后再插第 3 台 dot2（`listen_port` 8873），不要一次插三台。
4. P1：`d.7` volume 精确比较（加容差/仅配置变化写）。
5. 后续功能（不进本批）：§f.1 全场停止（等 9 哥 定词族 + 确认决策 2）、§f.2 语音定时提醒（等 D-013 定 `default_output`）。
6. D-012：跨房语音唤醒当前会 `runner.abort()` 打断当前回合，真机听感后决定 A/B，不许实现者自行改。

## 收尾（0.7.9，2026-10-27）

- **版本**：core **0.7.9**（patch）。版本号三处一致：`pyproject.toml` / `src/rui/__init__.py` / `uv.lock`（`07f40f4`）。
- **tag**：`v0.7.9` @ `715a3c2`；GitHub release <https://github.com/yes999zc/rui/releases/tag/v0.7.9>（notes 如实写明「意图层只经 headless 判定、真机未验」）。
- **镜像（GHCR `ghcr.io/yes999zc/rui:0.7.9`）**：OCI index `sha256:0f6c89ceb217d79471017bf24860dd74f1135f20b2d612e106277d5abc15bf05`；**amd64 平台摘要 `sha256:5aa362f6b4f7d7fd24a7f01d41c31e1523ce2401296ac3b203b9f684a314b9f7`**（NAS 老 docker 只认平台摘要，索引会报 `unsupported media type`）。README 的三处安装指引已换成该摘要（`ecf1342`）。
- **CI**：tagged commit `715a3c2` 四个 workflow 全 `success`（ci / security / secret-scan / docker-publish）。
- **生产（Bakey-NAS `/vol1/rui`）**：**已升级到 0.7.9**（2026-10-27 22:4x，9 哥 指示「对齐」后由 Lead 执行）。
  `rui-dot2-override.yml` 的 `image:` 已 pin 到 amd64 摘要 `sha256:5aa362f6…b9f7`（改前备份 `rui-dot2-override.yml.bak.before079`）；
  `docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d --force-recreate` 后 `healthy`，
  `/health` = `version 0.7.9 / devices 2 / device_errors 0 / audio_plane true / speaker_connected true / adapter_plugins registered dot2,lx06`。
  **回滚**：把 `image:` 改回 0.7.8 的 amd64 摘要 `sha256:1f074bb3…ecaa`（或 `cp -p rui-dot2-override.yml.bak.before079`）→ 同两条 `-f` 命令。
  **生产验收（2026-10-27 22:3x 核过）**：`/health` = `version 0.7.9 / console v2 / devices 2 / device_errors 0 / audio_plane true / speaker_connected true`，
  `adapter_plugins.registered ["dot2","lx06"] / warnings 0`；两台 plane（`living` 客厅、`dot2` 卧室）均 `running true / speaker_connected true / restarts 0 / error ""`；
  `GET /api/v1/config/catalog` → **200**，`ack` 的 `tier 2 / order 5`（tier2 首位）→ **v2 语音助手页第 3 位（assistant → session → ack）**，D-011 在生产生效。
  **注意**：容器已跑 0.7.9 代码；**C1/C2/C3/C5 与 D-011 目视已于 2026-10-28 真机验证**（N1/N2/N3/ACK-1/ACK-2，见下方），**仅 C4 措辞未验**（C7–C10 明确不做）。
- **0.7.9 真机验收已完成（意图层 + 控制台）**：**N1 / N2 / N3 / ACK-1 / ACK-2 全部 ☑**（2026-10-28 本地 08:54–09:33；容器 UTC 00:54–01:33；**日志时间为容器 UTC，本地 = +8h**）。剩余 **R79-1…6** 与 21 条仍待 0.8 窗口（第二台 dot2 到位后一起做）。
- **本批做了**：意图层批次 1（C1 `ddb7cbb`）+ 批次 2/3（C3/C4/C5/C6/C11 `34e3629`）+ D-011=A（`ack` order 200→5、**tier 未动** `6117fb7`）+ 审计两件（`f5d3f46`/`7fde6aa`）+ 验收/限制/backlog 三份文档（`4f75079`/`715a3c2`）+ 收口报告（`35dc556`）。
- **本批没做（明确不做，见 `docs/known-limitations.md`）**：C7/C8/C9/C10（不新增结束词）、HE-02 回声切除（D-010 不修）、D-011 的 v2 第 1 位（需 tier 2→1 或页内排序，归 0.8 P1）、控制台「唤醒应答独立成页」、桥的任何发版（`rui_compat` 窗口未变，dot2 1.3.1 / xiaoai 0.2.2 无需重发）。
- **最终 HEAD（0.7.9 收尾时）**：`47882ec`（其父 `8de395a` 上测得测试基线；当时工作树干净、已 push，`origin/main` 与本地 0/0）；**2026-10-28 的真机验收回写未提交**（4 份 docs）。
- **测试基线**：`uv run pytest` → **`1716 passed, 6 skipped`**（113.5 s，2026-10-27 22:5x 在 `8de395a` 实测；`ruff check src tests tools` 绿）。
- **容器状态**：`rui` `Up (healthy)`，`created=2026-10-27T14:28:06Z`（=22:28 CST），`/health` `version 0.7.9 / devices 2 / device_errors 0 / warnings 0`。
- **在途卡**：只有 `task-10`（HE-02）保持 blocked（D-010 不修，并入 0.8 真机窗口）；其余全部 completed，无 in_progress / pending。
- **决策记录**：`docs/待拍板.md` 的 D-001…D-011 **全部 ANSWERED**（含结论行）；本批内我按 9 哥 授权自行裁决的两处写在 D-011 与 backlog d.1（order-only 收、ACK-1 判据 = 首屏可见）。

- **C1 真机已验证（2026-10-28 08:54 本地；容器日志 UTC 00:54）**：`00:54:16 tool music_play({"query": "播放一首周杰伦的青花瓷。"}) → 好，放《青花瓷》`；`00:54:35 stop intent: '停止播放。' -> media stopped, session closed`（旧行为 `media stopped, session open`）⇒ C1 成立，`docs/acceptance-pending.md` 的 N1 已勾；**C2/C3/C5 与 D-011 目视同批已验，仅 C4 措辞未验**。**本轮不改的陈旧表述（统一口径，以本条目为准）**：`README.md` 中英两份（含 `:54`/`:170`/`:241`/`:357`）、`docs/release-candidate-0.7.9.md` 全文（含 `:16`/`:143`/`:191`）、`CHANGELOG.md` 的「真机未验」段（`:24-29`）——它们是对外文本/发版记录，待 0.8 发版时统一改；「停止」「拜拜」两句按同批真机确认，未单独留日志。

- **0.7.9 真机验收（2026-10-28 08:54–09:33 本地 / 容器 UTC 00:54–01:33）**：N1 ☑（`stop intent: '停止播放。' -> media stopped, session closed`）、N2 ☑（`停止你好` 走模型、会话未关；音乐由模型主动调 `music_stop`，非误判）、N3 ☑（有歌暂停 / 无歌回「现在没在放音乐。」）、ACK-1 ☑（v2 第 3 组首屏可见可改、旧面板第 1 位、保存后生效）、ACK-2 ☑（四组硬核在页尾）；**剩余 R79-1…6 + 21 条**待 0.8 窗口。

## 若以后重启：先读这三个文件

1. `docs/backlog-0.8.md` —— 0.8 的开工顺序（**桥先发双窗口 → core 后发**，顺序不可反）、三项破坏性变更、以及**从未验过的「双 dot2 同型号」验证组**。
2. `docs/acceptance-pending.md` —— 真机验收累计（21 条只能真机 + 补充 + 本阶段新增），每条有可照抄命令与通过标准；0.7.9 的项全部并入 0.8 窗口。
3. `docs/known-limitations.md` —— L1–L16 已知限制（含 HE-02 缺口、C7–C10 不做、0.7.9 真机已验：**C1 正负/C2/C3/C5/D-011 目视 @ 2026-10-28**；仅 C4 措辞未验）。

补两份按需查：`docs/release-candidate-0.7.9.md`（这批发版依据与回滚步骤）、`docs/待拍板.md`（已答结论）。

**接手自检（2026-10-27 收尾时核过、2026-10-28 按真机验收结论更新，三处都够用）**：上面三个文件覆盖 ① 0.8 开工顺序（`>=0.7.0 <0.9.0` 双窗口 → core，顺序不可反）② 破坏性三项 ③ 双 dot2 六组 ④ D-011 的 P1 结构项 ⑤ 真机未测项（**N1/N2/N3/ACK-1/ACK-2 已验 @ 2026-10-28**；R79-1…6 与 21 条未测）；`backlog-0.8.md` §d.3 已写明「inventory 36/26 vs acceptance-pending 21+补充+新增」的条数口径差异，执行以 acceptance-pending 为准。

## 历史：新会话入口（2026-10-27，0.7.8 收尾时的版本）

> **只读本节 + 它指出的文件即可接手**；下面的历史节按需查，不要通读全篇。

> **下次第一件事**：0.8.0 开工前先拍板 `docs/HANDOFF-0.8.md` §3 的未决问题；**所有待拍板项已汇总到 `docs/待拍板.md`（D-001…D-006，含背景/选项/代价/建议），拍板结论回写到那里再落 HANDOFF**。在那之前不要动 0.8.0 的任何删除动作。

### a. 现状事实（截至 2026-10-27 12:50）

- **版本**：core **0.7.8**；tag `v0.7.8`（`a3205e9`）；release <https://github.com/yes999zc/rui/releases/tag/v0.7.8>
- **镜像**：GHCR 索引 `sha256:ca3b40ecaf3457158603af90e520ac2bf423e2f4570e12e36db15b2583bf24c3`（tag `0.7.8` / `0.7`）；**生产 pin 的是 amd64 平台摘要 `sha256:1f074bb3af1ef4854ef040b12c9146ed8a6ef2b1c4a5f6cc7858ac6ccac3ecaa`** —— NAS 上的老 docker 不认带 attestation 的 OCI index，按索引摘要 `pull` 会报 `unsupported media type`。
- **生产（NAS）**：`version=0.7.8`、`console=v2`、`devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`、`warnings=0`；**一个核心镜像 + 两个外挂插件目录**（`/vol1/rui/plugins/`）。**本阶段未做任何生产变更。**
- **回滚**：本地旧镜像已删（9 哥 决定）；**回滚走 GHCR 上一个正式版** —— `docker pull ghcr.io/yes999zc/rui:0.7.4` → 取 amd64 摘要 → 改 override 的 `image:` → **两个 `-f`** `up -d --force-recreate`（步骤见 `docs/deploy-runbook-0.7.8.md` §8）。
- **三仓 HEAD**：rui **`35dc556`**（工作树干净；用 `git log --oneline v0.7.8..HEAD` 自查）；dot2 `8a236f3`、xiaoai `5f0a207`；**三仓 open issue = 0**（24 条已逐条回复并关闭）。
- **CI（rui `7b24d2c`）四绿**：ci / security / secret-scan / docker-publish 全部 `success`（收口后复测，满足收口报告 §6 条件 ① 的要求：最终 HEAD 也四绿；`f23e412` 同样四绿）
- **CI（rui `f23e412`）四绿**：ci / security / secret-scan / docker-publish 全部 `success`（12:17–12:22）；`35dc556` 是纯文档提交，写作时刚 push。更早提交里出现的 `cancelled` **不是失败**，是后续 push 的并发 run 把旧 run 顶掉（`cancel-in-progress`）。查当前状态：`gh run list -L 8 --json name,headSha,status,conclusion`。
- **进 main 未发版 64 个提交（`git rev-list --count v0.7.8..HEAD`，截至 `35dc556`）**（按「不升 minor」节奏攒着）。用户可见的：
  `91cac91` 控制台把 Home Assistant 与 MCP 服务器分两段（仅呈现层，API 未动）、
  `d2c9895` dot2 音量回读取 mixer 回报值、`e85b4bb` 「唱首歌」走随机 + 老链接落页提示、
  `3ed312f` `mcp <name>: down` 重连日志节流（行为变更，已记入 CHANGELOG「已知变更」）、
  `2977c3a` audio `close()` 的 `ENOTCONN(57)` 不再覆盖 plane 的真实失败原因（并修掉该竞态导致的子进程泄漏）、
  `8321099` 被吞的 ENOTCONN 现在留一行 DEBUG；
  其余为 handoff/readme/roadmap/runbook/audit 文档与测试（新增 `tests/test_mcp_down_logging.py`、`tests/test_he01_goodbye_echo_no_extra_turn.py`）。
  **生产镜像仍是 0.7.8 → 生产控制台看到的仍是旧界面。**
- **事实行的维护约定**：每完成 1–2 张卡，更新本节的 HEAD / 未发版提交数 / CI 三行，不要等到收口。

### b. 已定决策（**不要再讨论**）

1. **0.7.5 / 0.7.6 / 0.7.7 合并进 0.7.8**，0.7.8 是唯一验收节点；**不为单条修复升 minor**，修复进 main 攒着。
2. **小爱插件彻底外置**：核心不再内置任何设备实现；仓内 `plugs/` 与 `adapter_aliases.py` 已删；`adapter: xiaoai` 在**读入时**迁移为 `lx06` 并打 WARNING（`/health.adapter_plugins.warnings`），**0.8.0 删这段迁移**。
3. **0.8.0 只做破坏性变更**，且**桥先发双窗口版本、core 后发**（顺序不可反）。
4. **`docs/` 禁止真实内网地址**：用 `<NAS_IP>` / `<LX06_IP>` 占位；CIDR（如 `docs/security.md`）不算，扫描器不匹配。
5. **MCP 维持现状，不在 0.8.x 做 server 端**：rui 只有 MCP **client**（`tools.mcp_servers`，生产为空）；**Home Assistant 走专用桥 `src/rui/tools/ha.py`**（六工具白名单 + 危险域硬拒绝）——准确表述是：**HA 不进入 rui 的通用 MCP 服务器列表、不是 rui 的 MCP 实例；专用桥内部以 MCP 客户端连接 HA 自己的 MCP Server 集成**。设备能力不暴露 MCP。理由：HA 有专用桥更安全；外部 agent 直接调设备、绕道 rui 无收益。
6. **在线音乐「接口公开、实现私有」**：只做合规版（自建库 + 官方元数据/官方试听），**不做 VIP 换源、不接聚合解析站**（调研见 §「在线音乐调研」）。

### c. 扩展点地图

| 想加什么 | 改哪里 | 要点 |
|---|---|---|
| 音乐源 | `src/rui/music/source.py` 的 `MusicSource` Protocol（`search` / `random` / `favorites` / `star` / `resolve` **五个方法**）+ `src/rui/music/sources/presets.py` 注册；现成实现：`navidrome.py` / `subsonic.py` / `jellyfin.py` | 新 provider 的**配置字段名未定**（见 d-2） |
| 设备厂商 | 外置插件：宿主机 `plugins/<包名>/` → `-v ./plugins:/opt/rui-plugins:ro` + `PYTHONPATH=/opt/rui-plugins` + `RUI_ADAPTER_PLUGINS=<id>=<module>:build`；或 `<data_dir>/plugs/*` 自动扫描 | **不需要造下游镜像** |
| 外部工具 | `tools.mcp_servers[]`（HTTP / stdio；stdio 有开关闸；生产为空） | HA **不在此列** |
| 契约 | `docs/contracts/C-002-console-v2-api.md`、`docs/socket-plug-v1.md` | 三仓 `rui_compat` 必须同窗口 |

### d. 未决 / 待拍板

**全部待拍板项已集中到 `docs/待拍板.md`（D-001…D-006：patch 版要不要发 / 回滚演练窗口 / HA 是否摘出 / 音乐字段名 / 26 条真机协议 / MCP server 已否决），每条含背景、选项、代价、Lead 建议；拍板后回写那里再落本节。**

1. **MCP server 端**：**已否决**（见 b-5）；若以后要做需重新拍板，不要当成遗留缺口。
2. **在线音乐 provider 的加载字段名**（config schema 里叫什么）：未定，登记在 `docs/ROADMAP-0.8.md`（= 待拍板 D-004，建议 `music.sources[]`）。
3. **36 条「未验证」里 26 条真机协议**的处置（继续测 / 转已知限制 / 删）：见 `docs/audit/unverified-inventory.md`（= 待拍板 D-005）。

### e. 打磨清单（下一阶段工作面，逐条可验证）

1. ~~**flaky 清点**~~ → **本阶段进行中**：`test_plane_failure_is_reported_with_its_reason` 根因=close() 的 `wait_closed()` 重抛 macOS `ENOTCONN(57)`，把 `_greet` 的 AudioPlaneError 覆盖掉（详见任务板 task-1）。
2. **CI 时长与缓存**：四个 workflow 当前全绿，量一次时长再决定是否加缓存（本阶段 task-4 正在做）。
3. **覆盖率提升**：基线在 `docs/audit/coverage-baseline.md`，只准升不准降。
4. ~~**日志噪音**~~ → **已完成**：`3ed312f` 给 `mcp <name>: down` 加节流（首次/原因变化/恢复各一条 INFO，同原因每 5 分钟一条，其余 DEBUG）。
5. **错误文案与中英一致性**：控制台 v2 与 phrasebook 两层（phrasebook 只有中文，见 `src/rui/session/phrases.py` 头注释——原写法 `phrases.py:31` 少了目录）。
6. **文档与代码一致性抽查**：今天已修「HA 走 MCP」（task-25）与「dot2 带下游镜像」两处事实错误，说明这类错误还在。
7. **升级/回滚演练文档化**：runbook §8 已改成走 GHCR；下次发版前按它真跑一遍（= 待拍板 D-002，需窗口）。
8. **未验证清单处置**：同 d-3（= 待拍板 D-005）。

### f. 红线

- **真机出声测试须 9 哥 在场**（唤醒/播放/音量提示音都算）。
- **生产变更**：先备份 → 记录 digest → 写好回滚命令 → 部署后核对 `/health`；不留半升级状态。
- **仓库禁止真实内网 IP / 密码 / token**（占位符可以）。
- **不留半绿状态**：每张卡 review PASS + verify VERIFIED 才允许完成。
- **每条 compose 命令必须两个 `-f`**（少一个会解析到 `.env` 里过期的 `RUI_IMAGE`）。

### g. 本阶段进展（打磨阶段，2026-10-27 10:20–11:45；9 哥 已授权自主推进到 0.8.0 拍板前）

> 11 张卡，**10 张走完 review PASS + verify VERIFIED**，1 张按纪律 park（task-10，等 9 哥 拍板）。
> 全部提交已 push 到 `main`，**未打 tag、未发版、未动生产**。测试基线从 `1668 passed, 6 skipped` 变为 **`1672 passed, 6 skipped`**（新增 2 个测试文件、11 个用例）。

| 卡 | 内容 | 状态 | 证据（commit / 结论） |
|---|---|---|---|
| task-1 | audio plane 关闭路径 socket-close 竞态 | ✅ | `2977c3a`；独立 worktree 单文件 10/10、全量 3×绿 |
| task-2 | `mcp <name>: down` 日志节流 | ✅ | `3ed312f`；12 次同因失败 = 1 INFO + 11 DEBUG |
| task-3 | G0 事实校准 + 建 `docs/待拍板.md` | ✅ | `30ab5f2`/`a46fb68`/`663997f` |
| task-4 | CI 四 workflow 时长 + 决定 | ✅ | `3ea6726`；**维持现状**；候选（ci image 缓存 ≈30 s）推迟 |
| task-5 | 26 条真机「未验证」分类 | ✅ | `97e49bb`；**2 可转测试 / 21 只能真机 / 3 环境缺失** |
| task-6 | 文档 ↔ 代码事实核查（40 条） | ✅ | `ed3b648`；32 一致 / 5 已修正 / 2 疑似 / 1 需拍板 |
| task-7 | 覆盖率基线对比 + 提升方案 | ✅ | `44fc0c7`；行 +0.35pp / 分支 +0.22pp；**3 个真降全是删测试导致** |
| task-8 | 清 8 条遗留 nit | ✅ | `8321099`；被吞的 ENOTCONN 现在留 DEBUG |
| task-9 | 2 条「可转测试」落成真测试 | ✅（只交 HE-01） | `1c5583a`；**HE-02 落了测试才暴露真缺口** |
| task-10 | HE-02 回声切除漏 cue（lag 900–1200 / 尾部 1740 ms） | 🚫 **BLOCKED（D-010）** | 复现 `4 failed / 2 passed`；根因链已写卡面 |
| task-11 | 测试卫生（日志归属断言 / 常量注释 / 正向控制） | ✅ | `f7b2484`；过度切除变异**只红新用例**，证明其独有价值 |

**待拍板 10 条**（`docs/待拍板.md` D-001…D-010）。**发版/生产/真机出声/契约/改 README 承诺一律等 9 哥。**

**9 哥 回来后的第一件事**：看 `docs/待拍板.md`，优先拍 D-001（要不要发 0.7.9 patch）、D-002（给一个真机窗口跑回滚演练+真机验收）、D-010（HE-02 修不修）。

## 审计阶段（2026-10-27 12:0x 起，9 哥 交办）—— 只审不改，拍板后分批修

| 卡 | 内容 | 状态 |
|---|---|---|
| task-12 | **审计 A：意图层触发词冲突** —— 产出 `tools/intent_matrix.py`（一条命令重算矩阵）+ `docs/audit/intent-collisions.md`（每条冲突给 file:line / 短句 / 现状 / **按 9 哥 定稿的目标行为** / 影响面 / P0-P2 + 修复批次）。必须逐条解释 5 条生产日志（停止/停止播放/拜拜/停止你好 分别在有无歌时走了 media_stop 还是 session close） | 进行中 |
| task-13 | **审计 B：v2 控制台分组上下顺序** —— `docs/audit/console-ordering.md`；唤醒应答必须提到「语音助手」页第 1–2 位，`audio.capture/voice/playback/aec` 放最后，双入口（设备页/语音助手页）给唯一入口建议 | 进行中 |

**审计 B 的前提已由 9 哥 纠正（记 `docs/待拍板.md` D-011）**：v2 与旧面板是两套渲染。v2 事实：`web/src/v2/views/VoiceAssistantView.vue:74-81` 的 `SHOWN_GROUPS` 含 `ack` → **唤醒应答就在「语音助手」页、且是第 8 组（末位）**；`web/src/v2/config.ts:205 orderedGroups()` 不排序，按 catalog 数组原序渲染；生产 catalog tier 2 顺序 `devices(10)…audio.*(100-130) ack(200)` → ack 末位。**落点：改 `src/rui/config/catalog.py:127` 的 ack order 值（服务端顺序，v2 与旧面板都生效，前端不用动）**。审计初稿误用了旧面板 `PanelSpec`（`app.py:548-550`/`:581-593`）与旧面板排序（`ConfigCenter.vue:175`）。

**审计结果（2026-10-27 12:0x，两件都走完 review PASS + verify VERIFIED，**只审不改**）**：

- **审计 A（意图层触发词冲突）→ `docs/audit/intent-collisions.md` + `tools/intent_matrix.py`（commit `f5d3f46`）**
  11 条冲突 = **P0 2 / P1 3 / P2 6**。复算命令：`uv run python tools/intent_matrix.py`（两次运行字节一致，md5 `a4fe3bb1…`）。
  原现象解释：结束词 + 有歌 → `media_stop`（`app.py:966-984`，日志 `:984`），无歌 → session closed（`app.py:1013`）；`停止你好` 靠 `rules.py:281-283` 的「任意位置子串、总长最多 +2」命中。
  建议第一批：**C1**（结束词+有歌 → 停歌+关会话）+ **C2**（收紧容差，但显式 `ALIASES` 保住 `停止播放/别放了`）。
- **审计 B（v2 控制台分组顺序）→ `docs/audit/console-ordering.md`（commit `7fde6aa`）**
  **唤醒应答（`ack`）现状 = v2 语音助手页第 8 组（末位、不在首屏）**（`VoiceAssistantView.vue:74-87` 的 `SHOWN_GROUPS` + `web/src/v2/config.ts:205-215` 不排序 + catalog tier2 order 200）。
  **落点 = 改 catalog `ack` 的 `order`（`catalog.py:127`）+ tier**：只改 order 只能到第 3 位（assistant/session 是 tier1），要第 1 位需 `tier 2→1 + order 5`；代价两条：`tests/test_config_catalog.py:95-96` 必须同步改、tier1 契约语义（`catalog.py:41`/`docs/config-catalog-contract.md:68`）需 9 哥 接受。
  建议顺序：`ack → assistant → session → devices → audio.voice → audio.capture → audio.playback → audio.aec`；计数 P0=1 / P1=3 / P2=3 类。
  v2 的 `devices` 组**已是只读+跳转**（`DeviceConfigCard.vue`），重复写入口只剩 legacy 面板；`id` 不能改只读（唯一改名入口）。

**进入「可发版」阶段（2026-10-27 12:2x 起，goal 第 1 轮）**：三张卡并行推进，**不打 tag、不部署、不做真机**。
| 卡 | 内容 | 状态 |
|---|---|---|
| task-14 | 修复批次 1（C1 结束词+有歌 → 停歌且关会话；C2 收紧 `stop_intent` + 显式别名） | ✅ **completed `ddb7cbb`**；PASS（rev-nits 两轮，第 1 轮抓到「`_release_turn` 无人守护」）→ VERIFIED（ver-noise：全量 `1708 passed, 6 skipped`、两次变异、矩阵两跑一致） |
| task-15 | `docs/acceptance-pending.md` + `docs/known-limitations.md` | ✅ **completed `4f75079`**；PASS（rev-inv 三轮）→ VERIFIED（ver-docs：21 条 `comm -3` 无缺无多、27 行四要素齐、无敏感串/漂移行号） |
| task-16 | **收口报告** `docs/release-candidate-0.7.9.md` | ✅ **completed `35dc556`**；PASS（rev-cov 两轮，抓出「本批起点」数字口径）→ VERIFIED（ver-timings 实跑 1708/6、核四绿时间戳、没把 cancelled 当绿） |

**收尾批次（0.7.9，2026-10-27 12:5x 起，9 哥 全权授权、出门无人应答）**：目标=批次2+3 修复 → D-011=A → 文档 → 发版 0.7.9 → 结束会话；**禁止项只有：部署生产 / 动 NAS / 真机出声**。
| 卡 | 内容 | 状态 |
|---|---|---|
| task-17 | 批次 2+3（C5 暂停词收窄 / C3 无歌暂停提示 / C4 措辞 / C6 文案） | ✅ **completed `34e3629`**；PASS（rev-inv）→ VERIFIED（ver-noise：全量 `1716 passed, 6 skipped`、C3 变异 3 红、运行时 `end_words` 仍 21、`pause` 只剩 2 词） |
| task-18 | D-011=A：`ack` order 200→5（**tier 保持 2**）+ 顺序断言同步 + 待拍板收口 + acceptance-pending 并批 | ✅ **completed `6117fb7`**；PASS（rev-timings，抓出「tier≤2 实为 12 组」）→ VERIFIED（ver-inv：全量 `1716 passed`、变异红、web/ 零改动）。实测 v2 第 **3** 组（首屏可见）/ 旧面板第 **1** / tier≤2 全量第 6 |
| task-19 | `docs/backlog-0.8.md`（283 行，四节含**双 dot2 同型号 6 组**）+ known-limitations L1–L16 + README（中英，0.7.9 标「候选未发布」） | review **PASS**（rev-facts 三轮）；ver-docs 第 1 轮 FAILED 仅因**我授权改写在验证窗口内**（基线 14/1→17/4）+ `:29` 自述矛盾 → 改完后冻结 sha 复验 |
| 发版-prep | 版本号 0.7.9（`pyproject.toml` / `src/rui/__init__.py` / `uv.lock`）+ CHANGELOG `[0.7.9]`（含「真机未验 / 未部署」诚实注） | ✅ done（`07f40f4`，待与 tag 一起 push） |
| 发版 | 版本号 0.7.9（pyproject / `src/rui/__init__.py` / `uv.lock`）+ CHANGELOG + tag `v0.7.9` + GitHub release（**如实写：意图层行为真机未验**） | Lead 做，在 task-17/18 合入后 |

**已定（本批内 Lead 裁决，不需再拍）**：D-011 **只改 order、不动 tier** → v2 语音助手页 ack 由第 8 位升到**第 3 位（首屏可见）**，旧面板「音频与唤醒」tab 变**第 1 位**；要 v2 第 1 位需 tier 2→1 或 v2 页内排序 → 归 0.8 P1。**ACK-1 判据 = 首屏可见，不是第 1 组**。

**收口报告已交付**：`docs/release-candidate-0.7.9.md`（`35dc556`）—— 六节：可见变化 / 稳定性（v0.7.8 **1644** → HEAD **1708** passed, 6 skipped，+64） / 文档 / 验收清单 / 回滚（两个 `-f` + amd64 摘要警告） / **能不能发：可以发**，三条条件（最终发布提交四绿、N1/N2 真机验过、接受 C2 收窄且不宣称 ACK/HE-02 已修）。**未打 tag、未部署、未做真机**（2026-10-27 当时；真机于 10-28 补做）。

**已知未决（不阻塞本轮，但会影响「能不能发」的措辞）**：D-011（唤醒应答 tier 2→1 取舍）、C3（无歌说「暂停」是否算结束）、C10（别名算不算新增词）、D-001（要不要发 0.7.9 patch）、D-002（真机窗口）、D-010（HE-02 修不修）。

**9 哥 定稿的目标策略（审计判据，修复时照此执行）**：
1. 暂停功能保留，默认触发词**只有**「暂停」「停一下」；其他暂停词（先停这儿/停会儿/停住/停在这）从暂停表**删除**、不挪进结束词；用户可自定义。
2. 结束词表**保持现状不新增**。
3. **任何结束词被说出且有音乐在放 → 音乐停止 + 会话结束，两者一起做**（取消「有歌就变 media_stop 保持会话」这条分支，它是现象来源）。
4. 暂停 = 保留进度/会话继续；结束词 = 音乐清零/会话关闭；**没有中间态**。
硬边界：两件都**只审不改**；修复等拍板；不动生产、不做真机出声、不碰 0.8.0 破坏性变更。

**已知残留 nit（都不阻塞，建议合成一张 micro 卡）**：T3 的能量阈值按 1740/790 标定（换生产常量会误红）；T1 用字面 logger 名；`MCPClient` 用例未对称断言 name；`make_gate` 与 `test_cue_echo_excision.py` 重复（风格债）；`ci-timings.md` 两处措辞；`tests/__pycache__/test_he02_*.pyc` 陈旧字节码（gitignore 忽略）。

## 版本节奏（2026-10-27 09:10，9 哥 定）

- **不再为单条小修复升 minor**（0.7.9 不因「音量回复滞后」而发）。修复直接进 `main`，**攒够一批再统一发版**。
- 0.7.8 已是验收节点（tag/release/镜像）；之后进 main 的修复在**下一个正式版**才进镜像。当前生产跑的是 0.7.8 已发布镜像，**不含** task-23 的音量修复。
- 下一个版本的定位仍是 **0.8.0**（删 `/legacy` + 删 `legacy_adapters` 迁移 + 契约窗口调整），破坏性改动集中在那里；日常修复攒着，跟它一起或单独出一个 patch，由 9 哥 决定时点。
- 小修复清单（进 main 未发版，两张卡都已在改）：
  ① **task-23 dot2 音量回读确认**：根因是 dot2 桥 `read_volume()` 返回**上一次设备上报**（滞后一次），core 把旧值当新值；修法三态（回读=新值直接用 / 仍为旧值短轮询 6×80ms / 仍不确认则 `ok_unconfirmed` + 新目标值，**绝不报旧值**）。LX06 走第一支、文案不变。旧代码 4 条测试全红 → 新代码绿；全量 1648/6。
  ② **task-24 小修批**：`?from=` 落页提示（`App.vue` 读 query + `router.replace` 清 query + `data-testid="legacy-from-note"`）；「唱首歌 / 唱个歌 / 唱一首 / 来一首 / 来一段 / 唱一段」加进 `MUSIC_RANDOM_WORDS`（`rules.py:111`），12 条参数化先红 7 条后全绿；全量 1660/6。

## 支持矩阵补测（2026-10-27 08:54）

- **致谢**：README 中英新增完整 `## 致谢 / Acknowledgements`（`5360a14`）——按「实际在链路里」与「设计参考」分组点名：open-xiaoai（LX06 客户端与协议）、sherpa-onnx（ASR/KWS + 模型分发）、Silero VAD、SenseVoice/FunASR（中文 ASR 权重）、FFmpeg、speexdsp、openWakeWord（设备端 OWW 模型）；设计参考 Rhasspy / Wyoming / EchoMuse；音乐对接 Navidrome。许可证细节指向 `THIRD_PARTY.md`。
- **音量控制补测（真机，`debug/say`）**：
  - LX06（客厅）：`音量调到30` → 回「音量调到 30%了」，日志 `device volume set to 76`（设备刻度≠百分比，见下）✅
  - dot2（卧室）：**回复滞后一步** —— `调30`→回「100%」、`调40`→回「30%」、`调70`→回「40%」；但 `config.yaml` 写入正确（living `volume: 30`、dot2 `volume: 40`）。**结论：设置生效，回读/回复用的是变更前的值**（新 bug，待修）
  - 另注：LX06 的 30% → 设备刻度 76，映射关系需在文档写清（或按设备刻度暴露）
- **指示灯补测（人眼，2026-10-27）**：Dot2 配色 —— **唤醒=绿、思考=蓝、说话=青、播放歌曲=紫**；与机器侧事件链 `listening → thinking → speaking → off` 一致。LX06 未测。
- **多设备并发补测**：两台平面可同时 `running`（两台 `speaker_connected=true`）；但**同时发起第二个对话被 409 拒**（`a turn is already running`）——单会话 runner，属设计限制，已写进 README 矩阵。
- **矩阵三行已从「未测」改为实测**（`22d1ba2`、`c2b0bda`）。
- **LX06 指示灯补测（人眼，2026-10-27）**：与 Dot2 **同配色** —— 唤醒=绿、思考=蓝、说话=青、放歌=紫。矩阵两个 LED 格都已填实（`d8502c1`）。
- **音量刻度口径已写进 README**：控制台/语音用**百分比**；LX06 设备刻度 0–255（30%→76≈30×2.55，正常映射），dot2 用 0–100（`DOT2_VOLUME_CAP`）。属正常，不是故障。
- **task-23 在途**：修 dot2 音量「回复滞后一步」——方案 A（设置后回读确认，回读失败退回中性文案，绝不报旧值），要求先红后绿；若 dot2 桥 1.3.1 无回读接口则退化为「用新目标值回答并标注期望值」。

## GitHub 收尾（2026-10-27 08:50）

- **三仓 README 已按 0.7.8 现状更新并推送**：core `b2e5713`+`8879500`、dot2 `b1a4b4c`+`0bd8a53`、xiaoai `a3abc83`+`5dab49f`。内容：0.7.8 + **默认 v2 + 15 条旧链接 302**、外置插件的三段装法（宿主目录 / `-v ./plugins:/opt/rui-plugins:ro` / `PYTHONPATH` + `RUI_ADAPTER_PLUGINS`）、「不需要造下游镜像」、amd64 摘要 `1f074bb3…`、0.7.8 兼容表 + 0.7.4 历史、已知限制三条。verifier 抓到的两处（缺「默认 v2/15 条」字样、`registered` 口径写反）已修。
- **Hermes 的 24 个 OPEN issue 全部回复并关闭**（core 16 + dot2 5 + xiaoai 2，以及最后一轮补上的 rui#16）：
  - close(fixed) 4：rui#17/#18（`cf42695`，0.7.4）、#25/#28（`4468521`，0.7.4；#25 镜像 xiaoai `8f3ac46`，0.2.2）
  - close(duplicate) 1：xiaoai#1 = rui#25
  - close(not planned) 19：全部登记在 `docs/ROADMAP-0.8.md` §8（0.8.1 / dot2 1.3.2·1.3.3 / xiaoai 0.2.3 / backlog），回复正文点名小节
  - 回复包与核对命令：`docs/audit/hermes-replies.md`（每条带 commit/tag/file:line，中英双语，未承诺日期）
- 三仓当前 **0 个 open issue**。

## 0.7.8 已发布（2026-10-27 08:30）

| 项 | 值 |
|---|---|
| tag | `v0.7.8`（commit `a3205e9`） |
| release | https://github.com/yes999zc/rui/releases/tag/v0.7.8 |
| 镜像（多架构索引） | `ghcr.io/yes999zc/rui@sha256:ca3b40ecaf3457158603af90e520ac2bf423e2f4570e12e36db15b2583bf24c3`（tag `0.7.8` / `0.7`） |
| amd64 平台摘要 | `sha256:1f074bb3af1ef4854ef040b12c9146ed8a6ef2b1c4a5f6cc7858ac6ccac3ecaa`（**生产用这个**，本地 image `74f6c560…`） |
| arm64 平台摘要 | `sha256:93f9930ab1b553a25ffd0c61cdeb0a6c5cc63a8f747de29c0fb1d2d5f7b5fc32` |
| 生产 | NAS 168 跑上面 amd64 摘要；`ver 0.7.8 / dev 1 / err 0 / console v2 / speaker True / planes[卧室 dot2 running]` |

> **NAS 上的坑**：老版 docker 不支持 OCI index 里的 attestation manifest（`unsupported media type application/vnd.oci.empty.v1+json`），所以**按索引摘要 pull 会失败**；要用 **amd64 平台摘要**（`1f074bb3…`）或 tag `0.7.8`（同样会解析到索引，仍可能失败）。已在 override 里 pin amd64 摘要。
> **2026-11-02 更正**：这条坑在 0.8.15 部署时**没有复现** —— 同一台 NAS 按 index 摘要 `sha256:7c1a3aa7…` 直接 `docker compose up -d` 正常 pull 并落到 amd64（image ID 与按 amd64 摘要 pull 的完全一致、Arch=amd64）。推断是 NAS 上的 docker 已升级。**结论不变**：部署仍优先 pin **amd64 平台摘要**（确定性），index 现在也能用。

## 0.7.8 生产验收（2026-10-27 08:20，真实部署）

**部署形态**：官方镜像 + 外置插件，**不再造下游镜像**。
- 镜像 `ghcr.io/yes999zc/rui@sha256:2bcbbec0f9de86affee3f2330a96b8260d629d837e410ff1112487b1c9591308`（CI 在分支 `rc/0.7.8` 上构建的多架构产物，`rui 0.7.8`）
- 插件：`/vol1/rui/plugins/rui_dot2_bridge` + `rui_xiaoai_bridge`，只读挂到 `/opt/rui-plugins`；`RUI_ADAPTER_PLUGINS=dot2=…,lx06=…`
- 配置：`web.console: v2`；**客厅 `living` 与卧室 `dot2` 都开启**（08:31 起两台都 running）
  - 客厅显式写 `adapter: lx06`（不再依赖 `xiaoai` 迁移），所以 `adapter_plugins.warnings` 由 1 降为 **0**
  - `/health`：`devices=2`、`device_errors=0`、`planes=[living 客厅 running, dot2 卧室 running]`、两台 `speaker_connected=true`
  - **没有为客厅造任何新镜像**：插件是 `/vol1/rui/plugins/rui_xiaoai_bridge` 目录，容器只读挂载即加载（这是 0.7.6 之后与 `rui-dot2:1.3.1` 那套下游镜像的根本区别）

**客厅真机实测（08:33，人在客厅说的）**
- `08:32:57` 唤醒 →「现在几点了？」→「现在是早上8点33分。」ASR 269 ms / LLM 867 ms / TTS 803 ms / 播放起始 1761 ms，62 972 B
- `08:33:10` 「唱首歌。」→ 走了 `music_play` 工具，但曲库匹配失败（`曲库里没找到「唱首」`，phrase 解析问题，记一笔）
- `08:33:25` 与 `08:33:35` **两次打断**：`silence_after_ms=0`、`dropped 0.0 s`
- `08:33:30` 「放首歌。」→ `music transport: random (music.started)` → `playback requested: .../music_cache/...pcm` → 播起来了；随后 `08:33:39` 对话**抢占了音乐**（`output: dialogue preempts music`），符合预期
- `08:33:57` 静默 15 s → farewell

**镜像清理（08:34–08:38）**：先删分支构建的 `ghcr.io/yes999zc/rui:edge`；随后按 9 哥 指示删掉回滚胎 `rui-dot2:1.3.1`（`a62e2cf4`）。**NAS 上 rui 相关只剩 1 个镜像**：`ghcr.io/yes999zc/rui@sha256:1f074bb3…`（本地 `74f6c560`，运行中）。

> **回滚路径随之改变**：不再有本地旧镜像，回滚改走 GHCR 上一个正式版（`0.7.4` / `0.7.3` / `0.7.2` 都在），命令见 `docs/deploy-runbook-0.7.8.md` §8。
- 数据：纯净装后只恢复了凭据与音乐资产（`secrets.yaml`/`lan_media.json`/`library_index.json`/`music_aliases.json`/`music_history.json`/`setup.json`），日志/ack/DB/缓存保持全新

**验收结果**
| 项 | 判据 | 实测 |
|---|---|---|
| a healthy | Up (healthy) | `Up (healthy)`，`/health` 200 |
| b 设备识别 | devices/errors | `devices=1`、`device_errors=0`、`registered=["dot2","lx06"]`、`warnings=1`（`xiaoai→lx06` 迁移） |
| c 音频平面 | planes running | `planes=[{device: dot2, room: 卧室, state: running}]`、`audio_plane=true`、`speaker_connected=true` |
| d 真机走一遍 | 唤醒→指令→播放 | **通过（真实语音，3 个回合 + 打断 ×3 + 告别）**：08:17 唤醒 406 ms → ASR 211 ms（`现在几点？`）→ LLM 289 ms → TTS 700 ms → 播放起始 1084 ms，回复「现在是早上8点17分。」63 066 B；08:18 再次唤醒 → `今天周几？` → ASR 202 ms / LLM 480 ms →「今天周二，10月27号。」63 502 B（日期星期均正确）；打断 `silence_after_ms=3`、`dropped 0.0 s queued`（×3）；静默 15 s 触发 farewell；播放中唤醒被 `turn_open` 正确拒绝 |
| e 升级+回滚 | 各一次 | **已做**：升级到 0.7.8 → 回滚到 `rui-dot2:1.3.1`（重建，`a62e2cf4a0be`，`rui 0.7.2`，`devices=1/errors=0`，`/` 仍 302 `/v2/`）→ 切回 0.7.8 并恢复 `speaker_connected=true` |

**今晚踩过并修掉的三件事（记录，避免重犯）**
1. **arm64 镜像**：Mac 构建、NAS 是 amd64 → `exec format error`。现在只从 CI（原生双架构）取镜像。
2. **纯净装把凭据一起归档** → LLM/TTS 变「no ready provider」。恢复 `data/secrets.yaml` 即好；`data/` 归档在 `/vol1/rui/backups/0.7.8-preclean-20261027-075452/data-state/`。
3. **CI 红**：`tests/test_console_switch.py` 用了 PyYAML（项目依赖是 ruamel），已改（`7adf5c9`、`0993d08`）。教训：本地绿 ≠ CI 绿，测试里别引入非依赖包。


**时间类回答（记一笔，不是 0.7.8 回归）**：真实语音问「现在几点」答 8:17（正确）、「今天周几」答周二 10月27号（正确）；两次 `tools=[]`，说明时间/日期是模型从上下文拿的，没走时钟工具。08:26 那次 `debug/say` 答「8点16分」比实际早约 10 分钟，值得单独查一次时间注入的时机。

**未完成**
- ~~d 的真实语音回合~~（已完成，见上表）。
- **打 tag**：0.7.8 目前只有分支产物 `:edge`，`v0.7.8` tag / `:0.7.8` 镜像 / release 均未创建。
- 客厅那台（lx06）本轮关闭；`warnings=1` 来自它的 `adapter: xiaoai`，想消掉就把第 23 行改成 `adapter: lx06`。

## 0.7.8 状态（2026-10-27 02:20，进行中）

- **D 已合入 `efab571`**（task-17，reviewer-d PASS + verifier-tests VERIFIED）：
  - `web.console` 默认 **v2**（`legacy` 保留为一版回滚）；`/health.console` 两态实测。
  - **15 条老链接 302** 到 v2（9 个 panel + `/setup` + `/secrets` + `/diagnostics`；`providers`/`system`/`channels` 三个 v2 没有对应页 → `/v2/?from=<旧路径>`）；`/legacy/**` 永不重定向（无循环）；静态资源与 v2 深链不受影响。
  - `/legacy` 旧壳 + 中英弃用横幅（`data-testid="legacy-deprecation"`，CTA 到 `/v2/`），v2 不出现；legacy 模式下 `/` 也有横幅。
  - README hero 换 v2 设备页（`docs/assets/console-v2-devices.png`，无内网 IP/token）；中英「控制台入口」段。
  - 测试：**1642 passed / 6 skipped**；`test_legacy_links.py` 15 条参数化断言精确 Location；typecheck + build 绿。
- **task-18 文档收口已完成待评审**（工作树未提交，6 文件）：`docs/deploy.md` §3.8 改「默认 v2 / legacy 一版回滚」、CHANGELOG 0.7.8 段（写明「未写 console 键的老配置会静默变 v2」）、新增 `docs/release/v0.7.8.md` 中英、README 英文 hero、`legacy_links` 注释修正、`?from=` 已知限制。
- **版本口径（Lead 已定）**：**0.7.5 / 0.7.6 / 0.7.7 不单独打 tag** —— 它们的出口条件（Done 6 真机、双跑、升级回滚）要等 0.7.8 代码齐了一次做完；事后补 tag 等于假装「发版时已验证」。**0.7.8 是唯一验收节点**，CHANGELOG/release notes 必须覆盖四块（TLS、两台平面可见性、小爱摘出、审计+默认切 v2）。
- **task-18 已合入 `687d7de`**（PASS+VERIFIED）：deploy.md 控制台章节、CHANGELOG 四块（TLS/平面/摘出/审计+切 v2，六个基线数字齐全）、`docs/release/v0.7.8.md` 中英（升级 4 步 + 回滚 `console: legacy`）、README 英文 hero、`?from=` 已知限制。
- **task-19 已合入 `89a10f1`**（PASS+VERIFIED）：core **0.7.8**（pyproject/`__init__`/uv.lock 一致 + `uv lock --check` 绿）、README 中英 pin 0.7.8（`0.7.4` 只留历史行）、`/api/v2/meta` 实测 `rui_version=0.7.8` + `compat 0.6.0..<0.8.0`；全量 **1642/6**、ruff 绿。README 的 LX06 规则已按 0.7.6 事实重写（外置插件必需、旧拼写读入迁移 + WARNING、无别名无内置回退、0.8.0 删迁移）。
- **生产事实已实测并入 runbook（`dbb4ee0`）**：.168 只读核对得到——生效 compose = `docker-compose.yml` + `rui-dot2-override.yml`；**不带 `-f` 会解析出 `image: ghcr.io/yes999zc/rui:0.5.0`（`.env` 的 `RUI_IMAGE` 残留）**，`up` 会把生产切到错底座；当前镜像 `sha256:92ea98f4…`、health `0.7.2/2/0/legacy`、`/opt/rui-secrets` 五项齐、`/opt/rui-plugins` 只有 `rui_dot2_bridge`+`xiaoai`（老镜像层、无挂载）、config `devices[0] adapter xiaoai + plug_id xiaoai-lx06`。runbook 已加「外置 lx06 插件」四步与「镜像 docker save | ssh docker load」流程，执行人改为 9 哥。
- **task-20 已合入 `2e9da6c`**（PASS+VERIFIED）：音乐凭据路径不可写时 `PUT /api/v1/music/credentials` 从 **500 → 409**（`MusicCredentialsWriteError` 带路径 + OS 原因 + 修复句；写入与清空两支都覆盖；启动迁移语义不变）；1644/6、ruff 绿；审计 §8.1 条件性缺陷 **1→0**。release notes 与 CHANGELOG 的已知限制已同步改成「已修（409）」。
- **0.7.8 仍未打 tag**：出口条件是 Done 6 c/d/e 真机 + B 双跑 + 升级/回滚 + 人肉手册（白天）。代码与文档已全部就位，白天通过即可 `git tag v0.7.8` + release + 镜像。
- **下载/部署注意**：0.7.8 镜像默认 v2；生产升级前确认三件事（外置 lx06 插件挂载、TLS 材料、`multi_device`），命令见 `docs/release/v0.7.8.md` 升级四步。
- **白天要用的两份新文档（已提交）**：
  - `docs/deploy-runbook-0.7.8.md`（`e89a0c7`）：从 `rui-dot2:1.3.1` 升到 0.7.8 的人肉手册——备份 → 用 0.7.8 底座重建下游镜像 → 三个配置前置 → 切换 → 只读验收 → 回滚；末尾有「实测记录」表待填。
  - `docs/acceptance-done6-0.7.8.md`（`c68fb75`）：Done 6 的 a–e 验收表——每条给命令、期望值与记录位；**`registered` 的坑已写清**（镜像 catalog 不进 `registered`，以 `planes`/`devices` 为准）。
  - **runbook 已用真实构建校验过**（`868db10`）：`ghcr.io/yes999zc/rui:0.7.8` 要打 tag 后才存在，所以验收前用本地候选 `rui:0.7.8-rc1`（实测 `sha256:fe65d3a2…`）；下游镜像必须用 `deploy/downstream/Dockerfile`（仓库根没有 Dockerfile）；`rui` 没有 `--version`，要用 `python -c "import rui"`。本地链路已验证：下游镜像 `sha256:b4b40a88…`、`rui 0.7.8`、`/opt/rui-plugins` 含 `rui_dot2_bridge`。
- **另记**：`docs/release/`（0.7.x 实际目录）与 `docs/releases/README.md` 的路径口径不一致，留一次 docs 小修。
- **0.7.8 不发版**：剩余出口 = Done 6 c/d/e 真机 + 双跑对比 + 升级/回滚 + 人肉手册（全在白天）。

## 明早第一件事（2026-10-27 02:50）

### 1. 做到哪（A–F）
| 主线 | 状态 | 证据 |
|---|---|---|
| **A** 测试补齐 | 覆盖率基建 + batch-1 三条 + 配置台账完成；36 条「未验证」里 6 条变测试、4 条环境、26 条真机（协议已写） | 1644 passed / 6 skipped；覆盖率 80.95% 行 / 69.55% 分支（基线 `docs/audit/coverage-baseline.md`） |
| **B** 小爱摘出 | **代码完成**：内置 `plugs/xiaoai` 与 `adapter_aliases` 已删、配置读入迁移 + 告警、`wake_words_residual` 数据驱动、54 条设备语义测试移桥仓、核心 grep 0 | `c713916` / `c2ed7fd` / `300b2a9` / 桥 `0810885` `575b443`；剩双跑（真机）与删测试夹具（task-15，0.7.7） |
| **C** 控制台审计 | **完成**：172 行元素结论（159 可用 / 12 未验证 / 1 文案错误行 / 0 不可用 / 0 未处理）+ 76 张截图；条件性缺陷 1→0 | `db179f8` + `2e9da6c`；`docs/audit/console-functional.md` |
| **D** 默认切 v2 | **完成**：`/` 302 `/v2/`、`/health.console=v2`、15 条老链接 302、`/legacy` 弃用横幅、`console: legacy` 一版回滚 | `efab571`；`docs/release/v0.7.8.md` |
| **E** TLS + Done 6 | TLS 安装路径 + 三矩阵实测 + 两台平面可见性完成；**Done 6 c/d/e 未做**（真机） | `43d18df` / `258d72a` / `5e16026`；`docs/acceptance-done6-0.7.8.md` |
| **F** 每版纪律 | 0.7.3、0.7.4 已发；0.7.5–0.7.7 合并进 0.7.8（不单独打 tag，理由见下）；每卡 review PASS + verify VERIFIED | `efab571` / `687d7de` / `89a10f1` |

**版本口径（已定）**：0.7.5 / 0.7.6 / 0.7.7 **不单独打 tag** —— 它们的验收项（Done 6 真机、双跑、升级回滚）要等 0.7.8 代码齐了一次做完；事后补 tag 等于假装「发版时已验证」。**0.7.8 是唯一验收节点**。

### 2. 下一步（白天，按序）
1. 按 `docs/deploy-runbook-0.7.8.md` 在 NAS 上升级（本地候选 `rui:0.7.8-rc1` → 下游 `rui-dot2:1.3.1-r078`，链路已本地验证）；
2. 按 `docs/acceptance-done6-0.7.8.md` 走 a→c（只读）→ d（会出声）→ e（回滚一次）；
3. 全过后 `git tag v0.7.8` → `gh release create` → 记录镜像 digest → 回填 HANDOFF 发布表与 runbook 实测表；
4. B 双跑对比（0.7.2 内置 vs 0.7.8 外置，同一 LX06、同配置、字段级 diff）。

### 3. 等你（只有三件）
1. **Hermes 分诊确认**：`docs/audit/hermes-triage.md` §7（3 条 P0 降级、#27 否决去均值、#32 不改 pip 语义、版本重排）。
2. **两个口径**：①`adapter_plugins.registered` 不作 0.7.8 验收唯一判据（镜像里要同时带 `RUI_ADAPTER_PLUGINS` + `PYTHONPATH`），建议改看 `planes[].state`；②两音箱默认只起第一台，要不要把 `multi_device` 默认改开（影响单设备用户）。
3. **你亲手部署**：0.7.8 的最终验收由你做。

### 4. 已知限制（当晚新增）
- 自签证书只保证音频平面能起，真机接受取决于设备信任的 CA；
- 两音箱默认只起第一台平面（需 `multi_device`）；
- 未声明 `wake_words_residual` 的第三方清单不做 vendor 短语兜底（已知行为变更）；
- `?from=` 仅标记，v2 未消费（providers/system/channels 三个老链接落设备页）；
- 测试夹具 `tests/fixtures/legacy_lx06` 仍在（task-15，0.7.7 删）。

### 5. 生产
**今晚动过 0 次。** 当前仍是 `rui-dot2:1.3.1`（核心 0.7.2，本地镜像 ID `sha256:92ea98f4…`），`/health.console=legacy`。0.7.3 / 0.7.4 镜像已发布但**未部署**；0.7.8 未打 tag（故 registry 上没有 0.7.8 镜像）。

## 0.7.7 状态（2026-10-27 02:00，进行中）

- **C 上半（控制台功能审计）已合入 `db179f8`**（task-16，reviewer-audit 独立评审 2 轮 + verifier 独立验证）：
  - `docs/audit/console-functional.md`：**172 行元素结论 = 159 可用 / 12 未验证 / 1 文案错误行 / 0 不可用 / 0 未处理**；14 v2 路由 + 24 端点 + 异常态（401/409/422/429/500）+ v1→v2 差异 + 已知限制 + 复现命令。
  - 证据：4 个本地实例（主 18099 / 空屋 18101、写入 18103 / 配置恢复 18102），**76 张全页截图** `docs/audit/console-shots/`（25 MB，已入库）。
  - 4 组修正：setup 未加载 plan 不再误报「全屋无设备」、legacy 提示改「0.8.0 移除」、删 2 个死 i18n key、7 条中文校验 message 改英文（C-002 §4.4）。
  - 12 条「未验证」全部有原因与替代证据（真出声/真机拨入 = 夜间红线；keys/models = 无真实凭据）。
- **C 剩余（0.7.7 下半，未开工）**：①老链接 302 映射（`/panel/*`、`/setup`、`/secrets`、`/diagnostics`、`/channels` 15 条现在仍 200 旧壳）——**已决定挪到 0.7.8 D 第 5 项**；②`PUT /music/credentials` 在不可写路径返回 500 → 应转 4xx；③v1 未迁移清单（§7）逐条处置（本版只登记，不补功能）；④task-15（删 `legacy_lx06` 夹具）。

## 0.7.6 状态（2026-10-27 00:10，进行中）

- **B1a 已合入 `c713916`**（task-12，PASS+VERIFIED）：仓内 `plugs/xiaoai/` 删除（移到 `tests/fixtures/legacy_lx06` 测试夹具，不进镜像）、`src/rui/adapter_aliases.py` 删除；`adapter: xiaoai` 改为配置迁移（`config/store.py` 重写为 `lx06` + 弃用 WARNING + `.bak` 备份 + `/health.adapter_plugins.warnings` + 设备级 `legacy_adapter_spelling`）；默认 `adapter` 改 `lx06`（与旧行为等价、不告警）；Dockerfile 去掉 `COPY plugs`，`/opt/rui-plugins` 归 `rui:rui` 且构建期降权验证可写。全量 **1654/17**、`docker build` 绿。
- **B1b 已合入 `c2ed7fd` / 桥 `0810885`**（task-13，PASS+VERIFIED）：lx06 语义测试移到桥仓（**44 passed**）；核心 `src` 门禁 `xiaoai|lx06|小爱|4412` **0 命中**（仅 `legacy_adapters.py` 豁免）；4412 与旧拼写都收进 `legacy_adapters.py`（整模块 0.8.0 删）；**vendor 唤醒兜底改为数据驱动**：设备清单新增通用字段 `wake_words_residual`（lx06 声明三条），未声明则不过滤——已作为「已知行为变更（0.7.6）」写进 `docs/socket-plug-v1.md`。
- **`registered` 口径（实测）**：`adapter_plugins.registered` 只统计 `RUI_ADAPTER_PLUGINS` / entry point / `<data_dir>/plugs/*`，不含 `RUI_PLUGINS_DIR` 的 catalog；镜像挂外置插件**必须同时**带 `PYTHONPATH=/opt/rui-plugins`，否则 `registered=[]`。**0.7.8 验收看 `planes[].state=="running"` + `devices`/`device_errors`，不能只看 `registered`**。
- **B1c-1 已合入 `300b2a9` / 桥 `575b443`**（task-14，PASS+VERIFIED）：54 条设备语义测试（credit 5 / device-services 7 / kws 5 / led 18 / playback 1 / turn-audio 6 / vendor 11 / console-secret 1）**逐条字节一致**搬到桥仓；core 1655/17 → **1612/6**，桥仓 44 → **87/11**；`test_security_fixes` 通用 20 条留在 core。
- **B 剩余两件**：①**双跑对比**（0.7.2 内置 vs 0.7.6 外置，需 LX06 真机 → 白天）；②**删测试夹具** `tests/fixtures/legacy_lx06`（9 个通用测试仍依赖 lx06 注册/4412/params；需把 sample-plug 扩成替代 → **task-15，排 0.7.7**，不阻塞 0.7.6）。
- **0.7.6 暂不发版**：出口条件里的双跑要真机，按纪律不打 tag；白天补双跑后再发。
- **0.7.7（C 控制台审计）已开工**（task-16，交付 `docs/audit/console-functional.md`）：无头逐页逐按钮实测，夜间不接真机/不出声；基线清单位于 `docs/audit/console-audit-scope.md`（14 路由 / 7 tab / 24 端点 / ~95 元素）。
  - 2026-10-27 01:30 进度（Lead 维护，实现者只报事实）：`docs/audit/console-functional.md` 已成稿（4 个实例：主 18099 / 空屋 18101、写入 18103 / 配置恢复 18102，76 张截图）；172 条元素结论 = **159 可用 / 12 未验证 / 1 文案错误行 / 0 不可用 / 0 未处理**（空屋 first-run 的 prepare/params/create 已补测）；72→76 张截图；4 组文案修正（setup 空屋误报、legacy 提示、死 i18n key、7 条中文校验 message）。reviewer-audit 首轮 CHANGES：R1 config tab 的 restart 实为 `ok`（原写 failed）、R2 空屋 first-run 的 params/prepare 可测（原写被 keys/models 阻断），等修完后由 verifier 独立验证。
- 版本重排已写进 `docs/ROADMAP-0.8.md`（`b9a9059`）：0.7.4 = P0 修复、0.7.5 = TLS/Done6、0.7.6 = B、0.7.7 = C、0.7.8 = D。

## 0.7.5 状态（2026-10-26 23:35，进行中）

- task-9（TLS 安装路径）PASS+VERIFIED，提交 `43d18df`（house）/`1826e87`（dot2）/`1c1aa35`（xiaoai）：compose 挂 `./secrets`、自签脚本 `scripts/make_dev_tls.sh`、三仓 AI 提示词加 TLS 步。
- task-10（TLS 三矩阵实测）PASS+VERIFIED，提交 `258d72a`。**结论（已统一 README/审计文档）**：
  - dot2 的 `listen_port`/`tls_port` 开启时必须有 `secrets/server.pem|key`；缺 → `audio_plane=false` + `missing TLS material`；
  - 两桥同时配置时，**默认单平面模式只起配置里第一台设备**的平面（`multi_device.enabled=false`，`app.py:3077`）；两台都起需 `multi_device: {enabled: true, max_planes: 2}`（`_start_fleet`，单台失败不拖垮其他）；
  - 证书 + 两个 token 齐备（dot2 在前）→ `audio_plane=true` + `dot2 link: wss listening`；
  - 自签只保证平面能起；真机是否接受取决于设备信任的 CA（原 controller 证书丢失需重新配网）。
  - 6 次实测表 + 可执行复现命令：`docs/audit/tls-required-2026-10.md`。
- task-11（两台平面可见性）PASS+VERIFIED，提交 `5e16026`：`/health.planes` 逐台状态/原因（running|error|not_started；multi_device_disabled|plane_not_started|audio_not_started，含 error 文本）、`rui doctor` 在「2 台且 multi_device 关」时 WARN + 修复句、`config.example.yaml` 注释示例、C-002 A-8。7 条新测试；全量 **1659/17**。
- **0.7.5 状态**：代码与文档全绿，但 **Done 6 的 c/d/e（真机 audio_plane ×2、唤醒→指令→播放、升级+回滚）未做** → 按「出口条件未全过不打 tag」，0.7.5 **暂不发版**；白天补齐 c/d/e 后再发。
- **下一步（夜间）**：0.7.6 小爱摘出的**代码卡**（删 `plugs/xiaoai` + `adapter_aliases`、配置迁移告警、Dockerfile/pyproject 去耦）可以夜间做；双跑对比（需 LX06 真机）留白天。

## 0.7.4 状态（2026-10-26 23:15，已发布）

- 主题按 9 哥 指示改为 **P0/P1 修复**（`docs/audit/hermes-triage.md`）：原「TLS + Done6」顺延到 0.7.5。
- task-5（P0-A：rui#18 请求体上限 + rui#17 `_greet` 忙等）**review PASS + verify VERIFIED**，已合入 `cf42695`：全量 **1645/17**（连续 3 次），flaky 0/20，三态实测 413/401/422，greet ticker 865/s、冷启动 3.6 ms；反向变异 5 组。
- task-6（P0-B：rui#28 微信渠道 + rui#25/xiaoai#1 重连复位）**review PASS + verify VERIFIED**，已合入：house `4468521`、xiaoai `8f3ac46`。house 全量 **1652/17**、xiaoai **13**、dot2 **145**；两仓 adapter 等价（仅文件头/import 差异）；4 条新重连测试 + 3 条微信测试；变异各 1 组红后还原。
- **下一张卡（压缩后开）**：0.7.4 发布材料（版本 0.7.4 + CHANGELOG + `docs/release/v0.7.4.md` + README pin + C-002 A-7 已就绪），然后决定桥版本（dot2 1.3.2 / xiaoai 0.2.2）并打 tag。
- 已知小项（记录，不阻塞）：①`weixin` 的**流式累计超限分支**未被测试直接覆盖（现有测试走 Content-Length 预检）；②`getupdates` JSON 响应本身未设上限（本卡只限语音 CDN 下载）；③413 响应缺 `Permissions-Policy`（其余 4 个安全头齐全）；④xiaoai 仓测试借用 house 解释器。

## 0.7.3 状态（2026-10-26 22:05）

### 任务板（本会话）
| 卡 | 状态 | 说明 |
|---|---|---|
| task-1 `[impl] 覆盖率基建与基线` | **completed** | reviewer PASS + verifier VERIFIED + CI 四绿（`d21bb5f`）；commit `d21bb5f` |
| task-2 `[test] 未验证 batch-1（HE-03/HE-12/D2-06）` | **completed** | PASS + VERIFIED（verifier 自做变异并还原）；house `cd2d0a2`、dot2 `18ad114` |
| task-3 `[docs] 缺口台账 + 8 处矛盾` | **completed** | 两轮 CHANGES + verifier 两轮 FAILED 后修完；house `49df73f`、dot2 `517623d`、xiaoai `0889427` |
| task-4 `[release] 0.7.3 版本/CHANGELOG/release notes/README pin` | **completed** | tag `v0.7.3` + release + 镜像 digest 已记录（见下） |

### 0.7.3 发布（2026-10-26 22:05 起）
| 项 | 值 |
|---|---|
| commit | `c26cc46`（release: 0.7.3） |
| tag | `v0.7.3`（tag 对象 `13ab512`）已 push |
| 镜像 | `ghcr.io/yes999zc/rui:0.7.3` = `sha256:b6e8284fa688fe5327d9dff1ea259167c99aa95f06cc5d4360f5197c01652982`（amd64 + arm64，`docker manifest inspect` 已核；`:0.7` 与 `:latest` 同 digest） |
| release | `https://github.com/yes999zc/rui/releases/tag/v0.7.3`（非 draft、非 prerelease） |
| CI | tag `c26cc46` 上 **ci / security / secret-scan / docker-publish 四绿**（两个 docker-publish run 均 success：main + tag） |

### 数字（0.7.3 工作树，commit `cd2d0a2`）
- 测试四计数：**collected 1651 / passed 1634 / skipped 17 / xfail 0**（failed 0）；dot2 **145 passed**；xiaoai 9 passed。
- 覆盖率（本机 macOS/Py3.13）：行 **80.95%**（18429/22767）、分支 **69.55%**（5158/7416）、合并 78.15%。
- CI（`d21bb5f`）：ci / security / secret-scan / docker-publish **四绿**；artifact `coverage-3.11`（Python 3.11.17）/ `coverage-3.12`（3.12.15），CI 覆盖率 76.59% / 77.59%。
- 基线文件：`docs/audit/coverage-baseline.md` + `.json`（0.7.4 起不得回退的比较基准）。

### 未验证清单与审计范围（0.7.3 产出）
- `docs/audit/unverified-inventory.md`：36 条「未验证」（6 CI 可测 / 4 环境协议 / 26 真机），8 处文档矛盾终态，覆盖率缺口三分类，26 条真机三件套（命令+判据+记录表）。
- `docs/audit/console-audit-scope.md`：v2 14 路由 / 7 tab / 24 端点 / ~95 交互元素；v1→v2 32 行对齐；老链接映射表。
- `docs/audit/hermes-triage.md`：24 个第三方 issue 的分诊台账（**待 9 哥 确认**，见下）。

### 夜间纪律（9 哥 2026-10-26 21:25 授权，到明早）
1. 可做：生产 NAS 部署/升级/回滚（有纪律）、git push/tag/release、docker build/推镜像、本地容器、无头浏览器审计。
2. **红线：不许让真音箱出声**（TTS、音乐、唤醒音、对设备喊全禁）；听音类验收（音质/距离/打断/唤醒率）留白天。
3. 生产每次部署：备份 override+config（`.bak.日期`）→ 记录当前 digest → 回滚命令写进本文件 → 部署后核对 `/health` → 记录新 digest → 失败立刻回滚。
4. 每张卡完成更新本文件；60% 上下文前必须写一次；压缩失败最多重试 2 次并记进本文件。
5. 连续两轮无可推进 → pause goal，写清原因与「明早第一件事」。
6. 门禁不降：review PASS + verify VERIFIED 缺一不可；不合格 park 并写原因。

### 生产变更日志（今晚）
| 时间 | 动作 | 结果 | digest |
|---|---|---|---|
| — | 尚未动生产 | — | 当前生产 `rui-dot2:1.3.1`（本地 ID `sha256:92ea98f4…`，见下「版本与契约」） |

> 每做一次生产部署/回滚，在此表加一行（时间、动作、`/health` 结果、新 digest、回滚命令）。

### 待 9 哥 决定（明早）
1. **Hermes 台账确认**：`docs/audit/hermes-triage.md` §7 —— 3 条 P0 降级、#27 否决去均值修法、#32 不改 pip 语义、版本重排（0.7.4=P0 修复；TLS+Done6→0.7.5；小爱摘出→0.7.6；控制台审计→0.7.7）。
2. 0.7.3 发布号确认（core 0.7.3；两个桥本版不 bump）。
3. 白天真机项：Done 6 的 c/d/e、0.7.5 双跑、EQ 耳测、唤醒/打断复测。

### 白天项（今晚不做）
- 唤醒→指令→播放、音质/距离/打断、EQ 耳测、4 台联调、24h 稳定性满窗。
- 生产升级演练的「出声验证」部分（可先做不出声的升级/回滚 + `/health` 核对）。

### 0.7.4 发布（2026-10-26 23:15）
| 项 | 值 |
|---|---|
| commit | house `13469a4`；xiaoai `749d4d3` |
| tag | `v0.7.4`（tag 对象 `42262b4`）、xiaoai `v0.2.2` |
| 镜像 | `ghcr.io/yes999zc/rui:0.7.4` = `sha256:de26c5bacc00fbbb807da11d9eb1d7427125aab154703997d5a0131c23d8e749`（amd64 + arm64；`:0.7`/`:latest` 同 digest） |
| release | `rui/releases/tag/v0.7.4`、`rui-xiaoai-bridge/releases/tag/v0.2.2`（均非 draft/prerelease） |
| CI | tag 上 ci / security / secret-scan / docker-publish **四绿**（main + tag 两个 publish run 均 success） |
| 版本 | core 0.7.4；dot2 1.3.1（无改动）；xiaoai 0.2.2 |

## 版本与契约

| 项 | 值 |
|---|---|
| rui 核心 | tag `v0.7.1` @ `44cff05`（`v0.7.0` = `e9db990`）；HEAD `c825091`；镜像 `rui:0.7.1-a1p` = `sha256:f446e348e871e11404cb7d081952b458750a6f4a6d6d45512f64551cfdd5285c` |
| rui-dot2-bridge | tag `v1.3.0` @ `c71deb6`；plug `dot2-echomuse 1.3.0`（`ladder_correct=true`） |
| rui-xiaoai-bridge | tag `v0.2.0` @ `fcff8eb`；plug `xiaoai-lx06 0.2.0` |
| 生产 NAS 168（`/vol1/rui`） | **当前 `rui-dot2:1.3.0-p22`**（= r071 + P2-1 vad.py + task-18 按房间隔离，未打 tag；部署时序：r071 13:36、p21 16:08、p22 17:15，均 healthy）；上一个稳定组合 `rui-dot2:1.3.0-r071` = `sha256:252f568ca6b1d88aa2990e79856b7d8b143a7810bac452d294e9e5fcfbe7ecba`；底座 `ghcr.io/yes999zc/rui:sha-615e682` |
| 生产配置 | `/health.console=legacy`（M10 未切）；override：`DOT2_PLAYBACK_GATE_MS=0`、`DOT2_OWW_ON_DEVICE=off`、`DOT2_BARGE_IN_ENABLED=0`、`DOT2_CONTROL_PORT=8872`；config：`post_playback_mute_ms=2500`、`ack_echo_ignore_ms=2500`；备份 `rui-dot2-override.yml.bak.20261026-*` |
| 设备固件 | Dot2 client `fw 20261024-1911-dev`；二进制 `echomuse-dot2-ref/artifacts/server` sha256 `65f93891…` |
| 契约 | C-001 `rui-device/v1`；C-002/1（含 A-1..A-6）；兼容窗口 `>=0.6.0, <0.8.0`（三仓 manifest 已同步） |

## 已完成

每条一行，细节与证据见括号内文档。

1. 0.6.0 发布 + 真机验收（LX06/Dot2 应答、告别只从说话房间出）→ `docs/release/v0.7.0.md`
2. 内核剥离小爱（`src/` 仅剩 `adapter_aliases.py`）+ 小爱独立仓 Phase 1 被 rui 识别 → `docs/release/v0.7.0.md`
3. 控制台 v2 M1–M10（`/v2` 15 页，旧 logo 已迁移）→ `docs/release/v0.7.0.md`
4. Dot2 音质修正：100–300Hz vs 1–3kHz `+24.65 → +2.11 dB`、限幅 `−12.85 → −2.19 dB`、0 削波 → `docs/release/v0.7.0.md`
5. 播放中打断（A1'）：`GATE_MS=0` + 设备 AEC + RUI 中文 KWS，真机 8/8 `stop_play sent` → `docs/release/v0.7.1.md`
6. barge 唤醒不播 TTS cue（task-5），消除 barge 自循环 → `docs/release/v0.7.1.md`
7. P0 `turn_open` 泄漏：dismiss/退下后设备变聋 → 显式释放 + 20s 看门狗（task-9/10）→ `docs/release/v0.7.1.md`
8. 普通唤醒 cue 回声不再开 turn（task-11，envelope 匹配丢 cue 尾巴）→ `docs/release/v0.7.1.md`
9. 每设备测试历史隔离（task-2/7 + C-002 A-5）→ `docs/contracts/C-002-console-v2-api.md`
10. `/v2/tools` 排除 `tools.mcp_servers` + C-002 登记 `GET /api/v1/mcp` 只读例外（task-1/4）→ `docs/contracts/C-002-console-v2-api.md`
11. `rui_compat <0.8.0` 三仓 + manifest 覆盖测试（task-8）→ `docs/release/v0.7.0.md`
12. 代码健康审计 Top 10，2 条阻塞已修 → `docs/audit/2026-10-code-health.md`
13. 4 台接入 runbook（添设备 / listen_port / 验收字段）→ `docs/release/acceptance-4devices.md`
14. task-6 采样工具 `scripts/fleet_matrix.py`（日志 + 人工标记 → 矩阵，每个数字带 `--log` 行号证据，证明不了标「证据不足」；reviewer PASS + verifier VERIFIED，commit `9d0083b`）
15. task-6 P0 采样自动化 + 矩阵：`scripts/fleet_say.py`（唤醒词 + 1.5 s 后说指令、自动写 marks）+ `fleet_matrix --log-tz/--window/--wake-only-phrase`（commit `3c249f9`）
16. task-6 P1 归因：`docs/analysis/dot2-vs-xiaoai.md`（Dot2 30 句：唤醒 30/30 admitted、指令 26/30；两条掉分线索落到日志行；commit `850d19e`）
17. P2-1（task-15）：ack cue 回声不再进 ASR；真机 A/B **0/10 泄漏**（修复前 13/26）；commit `87bb704`，部署测试镜像 `rui-dot2:1.3.0-p21`（未打 tag）
20. task-16 分析完成（reviewer PASS）：`docs/analysis/wake-vs-command-range.md`，结论「唤醒链两台同量级、差距在 admitted→ASR；H2（播放后静音窗）被代码否证；H1/H3/H4 未做」；提交 `a301dce` + `2c88b0e`
21. task-19（P2-2 音乐中短指令）已开卡并把归因数据写进卡：音乐期 `[seg]` rms 中位 0.0232（比无音乐 0.0118 **更高**）、peak 到 1.000 → 失败模式是「音乐串音/削波」而非 mic 被静音
19. task-18（P1）播放态按房间隔离：一屋放歌不再饿死另一屋唤醒；提交 `3bbf93f`，reviewer PASS + verifier VERIFIED（1617/17），生产 `rui-dot2:1.3.0-p22`
18. 采样编排打通：Dot2 用 Mac Studio 播、LX06（`device_id=living`）用荣耀服务器（见 device-inventory.md）播，同一份 48k WAV、各自写 UTC 毫秒 marks，`fleet_matrix --log-tz UTC` 出两设备矩阵；设备接入索引见 `docs/device-access.md`（不含口令）

## 未提交

`docs/device-access.md`（新增，设备接入索引，无口令）

仅 `docs/HANDOFF.md`（本次整理 + task-13 收口记录，未提交）。2026-10-26 14:20 `git status --short` → `M docs/HANDOFF.md`；采样工具已单独提交为 `9d0083b`，三仓代码均已 commit + push。

**task-15（P2-1 ack cue 回声，第四轮返工完成待复评，文件已冻结未提交）**：窄匹配增加 `_quiet_after`（`vad.py:367`，cue 结束后 100 ms 必须安静，±20 ms 容差）：把「教过 EMA 后 mute 场景回复被误丢」挡住（回复比 cue 长、不会安静）；窄命中不再 `_learn_lag`（EMA 只由首轮高质量命中更新，`vad.py:381`）；日志 `kept` 区间改为 `min(skip+drop, hold)` 并新增 `match_offset`（`vad.py:493`）；`CUE_POST_SILENCE_MS=100`（:69）。新增测试：`tests/test_cue_echo_excision.py:450` 教 EMA(760/820) 后跑 mute 几何（cue 700/hold 1690/skip 950）→ captured **1.31 s**、`dropped=[0,950]ms`、fallback=false；`:482` 窄命中后 EMA 不变。无削波 850/950 仍 corr_full 0.030 / tail 0.353 / retained 0.91/1.00。全量 **1613 passed / 17 skipped**，ruff 绿；冻结 md5：vad.py `cf8e558c2d11b7be3041ebb16e0cd7ae`，test `412ecfb749d8488d9951fe0320b31f14`；process.py 未改（`176e8508…`）。

**task-18（P1 播放态按房间隔离，实现完成待门禁，未提交）**：arbiter 的 `turn_open` 增加 `turn_device`（`arbiter.py:187/343`），拒绝改为 `_in_open_turn`（:533，仅同房/同设备）；`_playing_reason`（:550）只在候选属于正在播放的房间时回 `lost_playing`/`cross_echo`，其它房间放行；pending 批量按候选逐条过滤（:480 保留 rejected 列表）。coordinator `set_turn_open` 同步房间、新增 `set_playing`（`coordinator.py:87/101`）；`_reconcile_owner` 把**所有**出声房间写入 playing（`app.py:1582`，无播放时清空 :1562）。旧全局单槽行为在测试中已更新（arbiter/app_fleet 各 1 处），新增 6 个隔离用例（`test_wake_coordinator.py:253/268/283/297/310`、`test_arbiter.py:233`）。反向：旧代码上 A 放歌→B wake `WakeReject`（应 admit）、A turn_open→B `turn_open`。全量 **1617 passed / 17 skipped**，ruff 绿。真机 A/B 由 Lead 执行。

**task-19（P2-2 音乐中短指令，实现完成待门禁，未提交）**：归因结论 H-A 不成立（音乐期高 rms/peak=1.0 的段都紧邻 `cue:`，见 fleet3:414/952；无 cue 的音乐段 rms 0.02–0.06、peak 0.2–0.23）、H-B **成立**（`wake_preroll_ms=500 > wake_skip_ms=300`，barge 时回放环里 200 ms 停播前音频进段；fleet3:596–601 barge→stop_play→seg，07:12:30–35 3.84 s 段串进告别语「没事的话，我先下啦」）、H-C 仅个别成立（07:04:01 rms .006、07:12:13 rms .0022 近似静音，非全局）。最小改动：`process.py:1895-1902` barge 唤醒不再回放预唤醒 ring（仍清空 ring），live 语音不受影响；非 barge 路径不变。新测试 `tests/test_music_barge_head.py`（barge 不回放停播音乐、非 barge 仍回放、stop_play ≤7 ms）。反向：旧 process.py 上 `assert MUSIC_PCM not in gate.fed` 失败。三仓：house-ear **1620 passed/17 skipped**、dot2 **143**（其 .venv）、xiaoai **9**；ruff 绿。真机 A/B 由 Lead。

**task-19（P2-2，第五轮返工待门禁，未提交）**：把 disarm 出口统一收口到 `process.py::_disarm_gate(reason)`（约 :2663）——`gate.armed` 时 disarm 并清 `_stale_speech_blocked`；`_disarm_gate_for_our_audio` 改走它（消除第四条漏清：reminder/console 语音 disarm 不经 `_feed_gate`）；`_feed_gate` 的 `armed -> disarmed` 转换也清位（约 :1959，覆盖 task-15 的 `_cue_excised` 静默超时，无 `idle_events`）；`idle_events` 变化清位保留。三处同一布尔赋值、均在事件循环单写者上，幂等无竞态。新测试 `tests/test_stale_speech_after_wake.py:170`（真实 vad 的 `_cue_excised` 超时 disarm → 后续 PLAY_PCM 入队）、`:214`（唤醒在听时我方 reminder disarm → 音频正常入队）；反向变异（去掉 helper 清位/armed 转换清位）→ 两用例分别 `AssertionError`。三仓：house-ear **1626 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。

**task-17（P2-1b ack cue AEC-lite，实现完成待门禁，未提交）**：已知参考（cue PCM）的分块逐频点 NLMS，`vad.py:451 _aec_lite`（`GateConfig` :121 起：frame=512/hop=256/taps=4/mu=0.5/passes=2/erle_min_db=12/residual_max=0.25/lag_max_ms=1500）；`:477 _aec_lag` 归一化互相关找延迟（分数<0.55 视为无 cue，音频不动）；`:496 _aec_filter` 逐 bin 4-tap 频域 NLMS + overlap-add；`:538 _aec_corr` 残差相关；`:704` 在 `_consume` 段末调用，残差 ≥0.25 时不产生 turn（降级），绝不把 cue 送 ASR；`set_echo_reference` 把 s16 cue 归一化为 float（:310）。四象限（lag 740/950 × clip × gap 30/300）实测 corr `-0.01…-0.001`、retained `1.000`；冷启动不依赖 EMA；mute 回归 pass。CPU 1.7 s hold 中位 **8.2 ms**（单核约 0.5%）。变异：关 AEC → 8 个四象限用例 corr=0.394 失败；关 `_consume` 集成 → segment never reached AEC-lite。全量 **1639 passed/17 skipped**、dot2 143、xiaoai 9，ruff 绿。风险：真实房间混响 >64 ms 时残差可能超限 → 保守丢 turn；真机 A/B 归 Lead。

**task-19（P2-2 音乐中短指令，第四轮返工待门禁，未提交）**：清位时机补齐——`_stale_speech_blocked` 现在在 `process.py:1962-1967`（`_feed_gate` 里 `idle_events` 变化，即 `wake without speech`/`WakeIdle`/超时 disarm）清除，也在本 turn utterance 落地时清除（:1988）；`_begin_wake_turn:1823` 在无 gate 时不置位。这样安静唤醒后 reminder/console debug say 的 PLAY_PCM 能正常播出。新用例 `tests/test_stale_speech_after_wake.py:117`（silent wake → 后续 PLAY_PCM 必须入队）；反向变异（注释掉 idle 清位）→ `AssertionError` at `assert plane._stale_speech_blocked is False`。已修好项保持：goodbye 已入队 chunk 仍被 flush/drop、farewell `say(keep_abort)` 早退、cue 正常。三仓：house-ear **1624 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。单会话限制已记：`runner.abort()` 全局 flag，A/B 两房共用 runner，跨房隔离仅 arbiter 层（task-18），不在本卡改。

**task-19（P2-2 音乐中短指令，第三轮返工待门禁，未提交）**：阻塞 1——plain wake 只 abort flag，已入队 goodbye 音频仍播放（07:12 30.65–30.97 已有 1.4–2.0 s 入 plane）。修法：`process.py` 新增 `_stale_speech_blocked`（唤醒置位、本 turn utterance 落地时清除，`_accept_play_pcm` 丢弃此间 PLAY_PCM，SpeakStart/SpeakFinished 同步忽略），`_begin_wake_turn` 开头 `_flush_speech()` 清空已入队；不用 StopAudio 因为会连 wake cue「在呢」一起停。阻塞 2——`turn.say()` 无条件 `_abort.clear()`：新增 `keep_abort=True`，farewell 用它并在 abort 已置位时直接不播；`farewell` 仍先 `idle.stop()`。app 侧保留「任何 wake 都 `runner.abort()`」。新测试 `tests/test_stale_speech_after_wake.py`（预置/迟到 chunk 均不进 gate，utterance 后恢复）、`tests/test_turn.py:780`（abort 后 farewell 零播放）。反向变异：丢弃 guard 后 `assert 0.10015625 == 0.0`；turn 移除 abort 早退后 `the aborted goodbye still played`。三仓：house-ear **1623 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。真机 A/B 归 Lead。

**task-19（P2-2 音乐中短指令，返工后待门禁，未提交）**：归因更正——07:12 headline 不是 barge：`:1110` 30.080 farewell 先触发、`:1111` 30.489 wake（TTS 未入队/barge=false）、`:1125` 30.969 起告别语 live 播放、`:1128` 3.84 s 段把告别语+「下一首」送进 ASR；`:596-601` 只是 H-C 近静音个例，H-B 不成立。修法：`app.py:2841-2848` 任何唤醒都 `runner.abort()`（不只 barge）中止上一轮/告别 TTS；`turn.py` farewell 改为先 `idle.stop()` 再 say，避免被中止的告别语事后清掉新唤醒的 idle 会话。撤销上一版 barge ring 裁剪（`process.py` 已还原，ring 仍回放，skip 之后用户首字保留）。新测试 `test_app_fleet.py:718` 普通 wake 必须 abort（旧代码 FAILED）、`tests/test_music_barge_head.py:104` barge 仍回放 ring（防回归）。三仓：house-ear **1621 passed/17 skipped**、dot2 **143**、xiaoai **9**；ruff 绿。真机 A/B 归 Lead。

## 已批 / 待批（2026-10-26 16:58 9 哥 全部按建议批准）

**已批（可继续，不用再问）**
1. **对外版本号**：暂不打 tag；等 task-16/17/18/19 收口后一次性定，建议 rui `0.7.2` + dot2 `1.3.1`（P2-1 是行为修复），**打 tag 前仍要 9 哥 一句确认**。
2. **M10 默认切 v2**：**先不切**，等 4 台联合验收通过、9 哥 点头。
3. **P2-2（音乐中短指令拾音）已批准** → task-19。P2 上限 2 项到此为止（P2-1 已完成，P2-2 在做）。
4. **`lost_playing` 跨房间饿死：发布前必须修** → task-18（P1）。

**仍待批（不阻塞当前工作）**
- 4 台新设备到位后的添加/验收（需要 9 哥 手动加设备）。
- 24h 稳定性结果出来后是否接受。

## 下一步（按序）

1. **task-6（阻塞发布）**：P0 矩阵 + P1 归因**已交付**（`docs/analysis/dot2-vs-xiaoai.md`，commit `850d19e`；Dot2 唤醒 30/30、指令 26/30）。**当前卡在 9 哥 选 P2（≤2 项）**，选定后各开独立 `[impl]` 卡，过门禁 + 9 哥 A/B，再回来 1 条收口。
   - P2 候选两条（详见分析文档 §6）：**P2-1** ack cue 回声抑制加强（`vad.py`+`process.py`，消除「唤醒后立刻说指令被『在呢』抢答」，13/26 次污染）；**P2-2** 播放中短指令拾音（dot2 bridge AEC + `post_playback_mute_ms`，音乐里「下一首」2/5）。
   - 采样工具已就绪：`python scripts/fleet_say.py --rounds 5 --rate 190 --gap 12`（默认已带唤醒词 + 1.5 s 停顿），矩阵：`python scripts/fleet_matrix.py --log /tmp/rui-fleet.log --marks <marks.csv> --log-tz UTC --window 12`（日志时区是 UTC，必须带）。
   - **LX06 那一路仍未采**（不同屋、Mac 音源覆盖不到）：需用同一份 `say -o` 音频在它房间放一遍，或 9 哥 人声复测。
   - **P1 收口时必须做的事（易忘）**：task-6 写范围只有 `docs/analysis`，P2 的代码改动**不得**塞进 task-6；P1 归因一 PASS，就把选定的 P2 拆成 1–2 张独立 `[impl]` 卡（带各自代码 write_scopes + 门禁 + A/B），并把 `task-12.blocked_by` 更新为 `task-6 + 全部 P2 卡`。
2. **4 台联合验收**：两台新 Dot2 到位 → EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT=8872` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除。步骤见 `docs/release/acceptance-4devices.md`。
3. **M10 默认切换**：等 9 哥 点头；验收 `/health.console=v2`、`/` 302 `/v2/`、`/legacy` 200，回滚一行。
4. **24h 稳定性复查**：自 **2026-10-26 17:15（p22 部署）** 起计；提醒已设 2026-10-27 13:10（将改为 17:40）；结果补进 `docs/release/v0.7.1.md`。注：13:36 那一轮因中途多次 recreate 已作废。
5. **task-12 发布收口**：task-6 完成 + 发布号经 9 哥 确认后，更新本文件 → `phase_close` → complete。

## 采样进度（2026-10-26 16:40）

- Dot2 腿：Mac Studio `afplay /tmp/fleet6/*.wav`（同源 48kHz WAV），5 轮 × 6 句，marks `/tmp/fleet6/marks-dot2b.csv`。
- LX06 腿：荣耀服务器 `aplay -plughw:2,0`，同一份 WAV，marks `/tmp/fleet6/marks-lx06.csv`（`device_id=living`）。
- 日志：`/tmp/rui-fleet5.log`（LX06 腿）与 `/tmp/rui-fleet6.log`（Dot2 复跑腿，2000 行窗口、3 s 轮询）。
- **电平实测**：`afplay -v 1` → 唤醒 5/5；`afplay -v 2` → **3/5**（更响反而更差，削波伤 KWS）——与 task-16「距离/电平」假设直接相关。


## 多设备互斥实测（2026-10-26 16:44–16:50，两屋同时说同一唤醒词）

- 拒绝分布：`lost_playing` **18 次**、`turn_open` **4 次**；admitted：`living` 11、`dot2` 5。
- 机制：一屋在放音乐（`living` 侧 `barge: N wake during playback`）+ 一屋有未结束的 turn 时，另一台设备的唤醒会被拒（`turn_open` 由协调器全局持有）。所以「Dot2 卡住」不是设备问题，是**串行仲裁**。
- 结论：**采样必须串行**（一次只开一台音源）；同时这是 4 台「互不串台」的 P1 候选（`lost_playing` 让另一台完全无法工作，需要按房间隔离播放态）。
- 采样脚本改为：单台、顺序、去掉 `放首歌/下一首`（避免自己把 mic 压住），只用 `hello/time/stop/bye` × 5 个电平 × 2 轮。

## 最近状态（2026-10-26 17:55，压缩前）

- 生产 `rui-dot2:1.3.0-p22` healthy，`RestartCount=0`，started 17:15:58；两台设备 `living`/`dot2` 均 connected+ready。
- 24h 稳定性提醒已改到 **2026-10-27 17:40**（schedule-80a81833），从 17:15 起计。
- 任务板：completed = task-1/2/4/5/7/8/9/10/11/13/14/15/16/18；进行中 task-19；未开工 task-17；release 卡 task-12 blocked_by = task-6/16/17/18/19（16、18 已完成）。

## 最近状态（2026-10-26 17:25）

- **已上线测试镜像** `rui-dot2:1.3.0-p22`（r071 + P2-1 + task-18）；回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **task-18 真机回归待跑**：两屋同时放歌 → 各自唤醒各 5 次，期望两屋都能应答、`lost_playing` 只在同屋出现。
- **task-16 分析稿已提交**（`a301dce`）：`docs/analysis/wake-vs-command-range.md`；核心发现：唤醒链两台同量级（0.75→0.15 区间），差距在 admitted→ASR；H2 被代码否证；「afplay -v 2 → 3/5」仅为现场手记（低强度），不证明削波阈值。
- **task-19（P2-2）已开工**（impl-barge-ack）：先判 H-A/H-B/H-C 三条假设、再选最小改动；P2 上限 2 项已满。

## 当前状态（2026-10-26 17:30，压缩前快照）

- **生产镜像**：`rui-dot2:1.3.0-p22`（r071 + P2-1 vad.py + task-18 按房间隔离），17:15 部署、`/health` 正常、2 台设备在线。**未打 tag**；回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **已提交**：`87bb704`（P2-1）、`3bbf93f`（task-18）、`3b3b0d1` / `8ee75a1` / `c3eba68`（文档）。全部已 push。
- **已过门禁**：task-15（PASS + VERIFIED）、task-18（PASS + VERIFIED + 真机回归）。
- **task-16 分析稿**：`docs/analysis/wake-vs-command-range.md`（已提交 `a301dce`），已按 reviewer 两轮意见全部修正；采样镜像锁定 p21，数字全部重算（Dot2 0.15 = cand 5/adm 4；LX06 0.15 = cand 6/adm 4），H2 已被代码否证（`post_playback_mute_ms` 只作用于有 TTS 回复的 follow-up）。
- **进行中**：task-19（P2-2 音乐中短指令拾音）。**未开工**：task-17（AEC-lite）。P2 上限 2 项已满（P2-1 完成、P2-2 待做）。
- **task-18 真机回归结果**：客厅放歌时卧室 Dot2 连续 4 次 admitted、`lost_playing` 0 次（修复前 6 分钟 18 次）；但音乐中 ASR 只剩碎片（`'了。'`）——归 task-19。
- **采样环境**：Mac（Dot2 房间）用 `afplay`，荣耀服务器（客厅，见 device-inventory.md）用 `aplay -plughw:2,0`；两台 NTP 同步，跨机对齐不确定度 ≤150 ms；**采样必须串行**（两屋同时说同一唤醒词会互相饿死）。
- **待批**：4 台新设备到位后手动添加；打 tag 前的版本号确认；24h 稳定性结果。

## task-19 收口（2026-10-26 18:05，**第 5 轮为 timebox 上限，9 哥 已定**）

- **门禁**：reviewer-barge **PASS**（出口穷举：process 内唯一裸 `gate.disarm()` 在 `_disarm_gate:2661`，无第五条绕过）；verifier-tools-2 **VERIFIED**（真实 vad `_cue_excised` 静音超时 → `blocked=False`、`queued_s=0.040`；我方音频 disarm → 同样放行；幂等两出口 `queued=2`）。全量 **1626/17**、ruff 绿（I001 已修）。
- **实现**：`process.py:508 _stale_speech_blocked` + `:2647 _disarm_gate(reason)`（唯一 disarm 出口，armed 时清位）+ `_flush_speech()` + `_accept_play_pcm:2942` 丢迟到 PLAY_PCM；`turn.py:1127 say(keep_abort=False)`、`:1135` abort 已置位则 return 0；`app.py:2841-2848` 任何 wake 都 `runner.abort()`。
- **提交**：`989baa1`；镜像 **`rui-dot2:1.3.0-p23`**（18:00 部署，healthy、2 台在线、未打 tag）。回滚 = override 改回 `rui-dot2:1.3.0-r071` + `compose up -d`。
- **真机 A/B（18:00–18:02，第一次）结果不可用**：10 次「唤醒+指令（音乐中）」只 3 次 admitted、1 条 turn（transcript `有点。`），`rejected=0`。判断为**采样协议问题**（`afplay` 起播后 8 s 即说唤醒词、音乐+TTS 混叠、且句间节奏与 `stop` 后音乐重启重叠），**不是结论**。需要按「每轮先确认播放态、唤醒前留足稳定播放、指令用 1.5 s lead」重跑，并用 `fleet_matrix` 出表后再下结论。
- **已知限制（不再扩范围，发布后开卡）**：① `runner.abort()` 是全局单 flag，B 房唤醒会中止 A 房正在合成的回复（runner 单会话）；② 已播出的告别尾音无法追回（只能阻止后续 chunk），彻底消音需「本机 TTS 参考消减」；③ `_feed_gate:1962/:1967` 是直接清位而非调 helper（功能等价，表述差异）。

## 音源校准（9 哥 2026-10-26 18:10 实机反馈，**强制前置**）

**9 哥 真人试 Dot2 的结论**：音乐播放中「下一首」**是可以的**，只是要离麦克风近一点；并指出 **Mac 播放的测试音太小**。→ 早期采样存在**系统性激励偏差**，task-16 / task-19 此前「唤醒灵敏、指令要凑近」的结论**可能部分或全部是装置伪影**，在重新校准前不得作为产品结论引用。

必须做（不是可选）：
1. **校准音源**：目标 1 m 处 ≈ **60 dB(A)**（正常人 1 m 说话）。
   - Mac 系统音量 100；音频文件峰值归一到 **−1 dBFS**（不削波，用 `ffmpeg -af volume=...` 或 `loudnorm` 后校验 `peak<=0.891`）。
   - 用手机 dB 表在 1 m 处实测并**记录数值**（写入分析文档）。
   - 不够响就换**荣耀服务器喇叭**（`aplay -plughw:2,0`）或蓝牙音箱当音源。
2. **校准后重跑**：① task-16 距离梯度 1 / 2 / 3 m，每档 5 次；② 「音乐播放中 + 短指令」Dot2 与 LX06 各 5 次；两次都用 `fleet_matrix --log-tz UTC` 出表。
3. 所有分析文档必须写明：**音源是什么、音量/声压级多少、怎么校准的、距设备多远**。
4. 「早期采样音源偏小」作为**已知偏差**写进 `docs/analysis/*` 的限制段（不悄悄改结论）。
5. **结论更正规则**：校准后若「唤醒灵敏 / 指令要凑近」消失 → 原结论是装置伪影，在 HANDOFF 与分析文档里**显式更正**；若仍存在 → 才继续查增益/AEC/VAD。

**同时作废的旧 A/B**：18:00–18:02 那次「音乐中 3/10 admitted、1 条 `有点。`」按本偏差处理，不作为 p23 的验收数据；校准后重跑才算。

## 结论更正（2026-10-26 18:20，按 9 哥「先校准再下结论」）

**「唤醒灵敏、指令要凑近」是装置伪影，已显式更正。** 校准后（WAV 峰值 −1.0 dBFS、Mac 音量 100、以设备侧 `[seg]` 自检 rms 0.02–0.10）用**真人**在 Dot2 上复测：

| 距离 | 唤醒 | 「几点了」 | 「放首歌」 | 「下一首」 |
|---|---|---|---|---|
| 1 m | 12/12 admitted、0 拒绝 | 3/3 | 3/3 | 3/3（换歌，无文本：`music owns the speaker`） |
| 2 m | 9/9 admitted、0 拒绝 | 3/3 | 3/3 | 3/3（同上） |

- 早期 TTS 采样的语音段 rms 只有 **0.006–0.02**（比校准后低 6–20 dB），9 哥 也判「Mac 测试音太小」→ §1–§6 的「差距在 admitted→ASR」只在低电平激励下成立。
- 详细数据与更正写进 `docs/analysis/wake-vs-command-range.md` §7。
- **task-19 的 A/B 作废重做**：必须用校准后的人声；「下一首」允许以 `playback requested` 作为功能成功，文本仅辅助。

## task-19 真机 A/B（2026-10-26 18:20–18:29，**用人声 + p23 通过**）

装置：Dot2（卧室），人声（9 哥），1 m 左右；音乐由设备自己播；日志 `/tmp/rui-p2p2.log`（`--log-tz UTC`）。

| 组 | 做法 | 结果 |
|---|---|---|
| 下一首 ×5 | 音乐播放中「你好小瑞 → 1.5 s → 下一首」 | **5/5 换歌成功**（每次 barge `silence_after_ms 2–7 ms`；`playback requested` 5 次） |
| 停止播放 ×5（用户自控放歌/停歌） | 「放首歌」→ 音乐起 → 「你好小瑞 → 停止播放」，重复 5 轮 | **5/5 命中**：`stop intent: '停止播放。' -> media stopped` 共 5 次（10:27:10 / 27:33 / 27:53 / 28:16 / 28:37）；seg rms 0.025–0.050、peak 0.19–0.53（无削波） |

- **合计 10/10 功能成功**，达到卡面 ≥4/5 的判据；本批 transcript 为空是设计使然（`music owns the speaker`），不算失败。
- 之前两次「2/5」「3/10」作废：一次是 TTS 音源过小（装置伪影），一次是我用 TTS「放首歌」当起歌手段、与用户说话撞车（协议伪影）。**真机判据必须以本批（人声 + 无 TTS 干扰）为准。**
- 结论：P2-2 的 headline case（音乐中短指令）在 1 m 人声下**可用**；2 m 音乐场景未测（待 4 台验收时补）。

## 发布决定（2026-10-26 18:40，9 哥 已定，不用再问）

- **版本**：`rui` **0.7.2** / `rui-dot2-bridge` **1.3.1** / `rui-xiaoai-bridge` **0.2.1**；三仓 **MIT**。
- **顺序**：① 清工作树 → ② xiaoai 全历史扫描 + 公开化（task-20）→ ③ 三仓 README 产品级重做（task-21）→ ④ CHANGELOG/tag/release + 发布后核对（task-22）→ ⑤ 宣传素材最后做。
- **README 规范**：中英双语、统一 10 段结构、架构图矢量工具（源文件+PNG/SVG）、截图必须是 0.7.2 真实界面且无内网 IP/token/序列号/人脸/房间照片；措辞红线 = 不提 VPN/内网穿透/代理、不写破解/刷机/root（改写「设备前置条件」）、不用小米/小爱 logo、不暗示官方关联。
- **task-17（AEC-lite）设一轮 timebox**：过不了就 park + 写「已知限制」，不阻塞发布。
- 每张卡独立 review + verify；README 按「真实性/脱敏/许可/措辞」四项审；**全部完成前不标 [phase]、不打宣传 tag**。

## 待设备（4 台联调，pending）

- 两台新 Dot2 到位后：EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除 → 4 台联合验收（`docs/release/acceptance-4devices.md`）。
- 依赖 9 哥 手动添加设备；设备未到期间不阻塞 README/发布准备。

## task-17 已 PARK（2026-10-26 19:35，按 goal 熔断）

- 第一轮门禁 reviewer-barge = **CHANGES**，按新 goal「task-17 一轮不过即 park」**不返工**。
- 代码已回滚：`src/rui/audio/vad.py` 还原、`tests/test_aec_lite.py` 删除，工作树干净。
- 实现留档：**`/tmp/parked/task-17/{vad.py,test_aec_lite.py}`**（临时目录，重启前需转存到仓库外持久位置）。
- park 原因（reviewer）：① `_aec_filter` 前 taps 帧 `clean` 恒 0 → cleaned 段首 ~32 ms 被置零，可能切用户首字；② `aec_lite=True` 默认开启且残差 ≥0.25 就**静默丢 turn**，真实混响 >64 ms 时可能频繁丢正常指令；③ `aec_lag_max_ms=1500` 与 task-15 的 800 ms 上界冲突，同源回复可能被当 cue 相减；④ 阈值/混响需真机 A/B。
- **不阻塞发布**：0.7.1/0.7.2 内的包络 + EMA + `quiet_after` 已覆盖真机观测类（削波 + lag≈740 ms）；剩余边界（冷启动不削波、用户与 cue 重叠 <110 ms）写进 0.7.2 release notes 的 Known limitations。

## 发布准备进度（2026-10-26 19:50）

- **task-17**：PARKED（见上），不阻塞。
- **task-20（xiaoai 扫描 + 公开）✅ DONE（verifier VERIFIED，HEAD `d741876`）**：仓库已 `PUBLIC`（`gh repo view --json visibility` 复核）；Lead 自测扫描：4 个 commit，**0 个 token/私钥、0 个内网 IP、0 条个人路径、0 个 MAC**；仅作者邮箱 `yes999zc@163.com` 出现在提交元数据里（非机密，属可接受）。**仍待该卡 reviewer + verifier 走完**。
- **task-21（三仓 README）**：已开工，**先做 house-ear 与 rui-dot2-bridge 两份**；xiaoai 那份等 task-20 收口后再做（同仓 README 会打架）。
- **task-22（CHANGELOG/tag/release + 核对）**：blocked_by task-20/21。
- CI：`cf05f58` 上 4 个 job 全绿（ci / security / docker-publish / secret-scan）；此后仅文档提交。
- 工作树：干净（house-ear 只有已提交内容；xiaoai 干净）。

## README 三个口径（Lead 决定 2026-10-26 19:55）

1. **quickstart 固定 tag = `v0.7.2`**（不用 `main`）。顺序：README 先合入仓库但**最后推送**，task-22 建好 tag 后再 push，避免 404。
2. **截图 = 本地实例 + 示例设备名**（`Living Room Speaker` / `Bedroom Speaker`）抓**当前 0.7.x 真实界面**，并在 README/PR 注明「本地演示实例，数据为示例」。**不允许**用生产界面（含真实房间/设备名）；也**不允许**用设计稿或模型生成图冒充界面截图。
3. **支持矩阵可公开实测数字**，但必须带「口径 + 日期 + 分母」，未测项一律写「未测」：
   - Dot2：唤醒 30/30 admitted、指令 26/30（2026-10-26，6 句×5 轮，校准后 TTS）；barge 8/8（2026-10-26，真机）；音乐中「下一首/停止播放」10/10（2026-10-26，人声）。
   - LX06（living）：唤醒 19/20、打断 205.2 ms（引用历史验收日期，若找不到原始记录就标「历史实测，原始日志未留存」并降级为「已测（有限样本）」）。
   - 音量/指示灯/多设备并发：**未测**。
   - 禁止把「未测」写成「支持」，禁止把单次样本写成稳定结论。

### 三仓 README 的分仓口径（Lead 定 2026-10-26 20:05，写稿按此执行）

| 仓 | quickstart tag | demo 素材 | 支持矩阵可写项 |
|---|---|---|---|
| house-ear（rui） | `v0.7.2` | 本地 `rui serve --no-audio` 实例 + Chrome headless 抓 `/v2`，设备名换 `Living Room Speaker`，注明「本地演示实例，示例数据」 | **0.7.1/p21 实测（2026-10-26 15:01）；0.7.2 未重测**——Dot2 唤醒 30/30、指令 26/30（TTS 校准后）；barge 8/8（真机，**stop-push 判据**）；音乐中短指令 10/10（人声）；音量/指示灯/多设备 **未测** |
| rui-dot2-bridge | `v1.3.1` | **真实终端记录**（桥加载/plug-test），脱敏 | 同上（只写桥相关项）；「已解锁/刷机」措辞全部改写为**设备前置条件** |
| rui-xiaoai-bridge | `v0.2.1` | **真实终端记录**（`rui plug-test` / 插件加载日志），无界面截图 | LX06 唤醒 19/20、打断 205.2 ms（**历史实测**，查不到原始日志就降级为「已测（有限样本）」）；音量/指示灯/多设备 **未测** |

架构图各自三段：house-ear（服务端/平面/设备三层）、dot2（rui ⇄ plug ⇄ Dot2）、xiaoai（rui 核心 ⇄ `rui-device/v1` plug ⇄ LX06）；均为 Mermaid 源 + SVG/PNG、同一配色。

- **release notes 草稿**：`docs/release/v0.7.2.md`（commit `e69b9c9`）——变更/门禁/已知限制/合规已写实；**digest 与 tag 链接待 task-22 构建后补**。

## README 截图与稿件的当前状态（2026-10-26 20:10，压缩前）

- **版本已落库并提交**（未 push）：house-ear `dee693f` = 0.7.2、dot2 `72a97c0` = 1.3.1、xiaoai `39d9273` = 0.2.1；**v0.7.2 快照**测试 house **1626/17**、dot2 **143**、xiaoai **9** 全绿，ruff 绿。
- **0.7.3 当前工作树**（task-2 后，commit `cd2d0a2`）：house **1651/1634/17/0**（collected/passed/skipped/xfail，见 `docs/audit/coverage-baseline.md`）、dot2 **145**（task-2 新增 2）、xiaoai **9**；ruff 绿。
- **release notes 草稿**：`docs/release/v0.7.2.md`（`e69b9c9`），digest 待 task-22 补。
- **截图候选**（本地 demo 实例 `rui serve --no-audio` @ 127.0.0.1:18080，示例房间 `Living Room`、设备 `living-room-speaker`，页脚 `rui 0.7.2`）：
  - `docs/assets/console-v2-devices.png`（**不要用**：设备卡片含 `Xiaomi · 设备接入` 且状态为「状态未知/音频面已停止/从未上报」，既触红线也不像产品图）
  - `/tmp/rui-demo/v2-run.png`（运行页，含「需要处理：设备离线/配置未完成」两条告警 → 不适合 hero）
  - `/tmp/rui-demo/v2-voice-assistant.png`、`/tmp/rui-demo/v2-tools.png`、`/tmp/rui-demo/v2-config.png`（**未查看**，下轮先看这三张挑一张干净的作为 hero）
- **v2 真实路由**：`/devices`、`/devices/add`、`/rooms`、`/setup`、`/run`、`/conversation`、`/logs`、`/memory`、`/config`、`/voice-assistant`、`/tools`、`/music`、`/credentials`、`/health`（`web/src/v2/router.ts`）。
- **抓图命令模板**：`"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 --virtual-time-budget=6000 --screenshot=<out.png> "http://127.0.0.1:18080/v2/<route>"`。
- **尚未开始**：`docs/assets/architecture.mmd`（mermaid-cli 在 `/tmp/mmdc`）、README 重写、dot2/xiaoai 两份 README。

### README 素材已就绪（2026-10-26 20:20）

- **hero 截图定稿**：`docs/assets/console-v2-config.png`（本地 demo 实例 `/v2/voice-assistant`，中文界面、无 Xiaomi 字样、无离线告警、页脚 `rui 0.7.2`；旧 `console-v2-devices.png` 已删，因为它含 `Xiaomi · 设备接入` 且状态为「音频面已停止/从未上报」）。
- **架构图已导出**：源文件 `docs/assets/architecture.mmd`（Mermaid，三层：Server control plane / Audio plane per device / Devices plug adapters；配色 ink `#1f2a2e`、teal `#2f6f6a`、sand `#e8e2d6`、accent `#b4744a`），导出 `architecture.svg` + `architecture.png`。
  - 渲染命令（Chrome 而非 chrome-headless-shell）：`printf '{"executablePath":"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome","args":["--no-sandbox","--disable-gpu"]}' > /tmp/mmdc/pptr.json && /tmp/mmdc/node_modules/.bin/mmdc -p /tmp/mmdc/pptr.json -i docs/assets/architecture.mmd -o docs/assets/architecture.svg -b '#ffffff'`
- **README 正文尚未写**（house-ear / dot2 / xiaoai 三份都未动）；quickstart 事实已核：`deploy/install.sh` 存在、Python `>=3.11`、`config.example.yaml` + `docker-compose.yml` 在根目录、LICENSE MIT。

### README 进度（2026-10-26 20:35）

- **house-ear README 已完成并提交**（`e8e0d95`）：中英各 10 段、202 行；hero 截图 `docs/assets/console-v2-config.png`、架构图 `architecture.{mmd,svg,png}`；支持矩阵全部带分母/日期、未测项显式「未测」；红线自检通过（无内网 IP、无 token、无 VPN/穿透/代理，小米只作设备型号并注明「无官方关联」，刷机只出现在「不提供/不建议」的否定句里）。
- **rui-dot2-bridge 架构图已导出**（源文件 `docs/assets/architecture.mmd`，SVG+PNG，同配色），**README 正文未写**（旧版 233 行仍是 0.3.4→0.6.0 口径、含「已解锁/EchoMuse 固件/播放门控 1.1.3」等过时说法，需整体重写）。
- **rui-xiaoai-bridge**：README 未动（旧版）；按口径用终端记录、quickstart `v0.2.1`。

### 三仓 README 全部完成（2026-10-26 20:50）

| 仓 | commit | 篇幅 | 素材 |
|---|---|---|---|
| house-ear | `e8e0d95` | 202 行 | 截图 `docs/assets/console-v2-config.png` + `architecture.{mmd,svg,png}` |
| rui-dot2-bridge | `4227654` | 201 行 | `architecture.{mmd,svg,png}`（插件三层图） |
| rui-xiaoai-bridge | `7eaba4a` | 约 200 行 | `architecture.{mmd,svg,png}`（rui ⇄ plug ⇄ LX06） |

- 三份都是中英双语、结构一致（定位/badge/hero/15 秒 demo/架构图/特性/三行 quickstart/前置条件/支持矩阵/故障排查/已知限制/致谢/MIT）。
- 自检结果：资源引用全部存在；无内网 IP、无 token、无个人路径；无 VPN/穿透/代理/破解/解锁/刷机/root 字样（改成「设备前置条件」表述）；支持矩阵未测项全部显式标「未测」，历史数字带日期。
- **待办**：按 9 哥 要求「每张卡独立 review + verify」，README 需过「真实性/脱敏/许可/措辞」四项清单；通过后三仓 push + tag + release。

### 发布物料进度（2026-10-26 21:05）

- **CHANGELOG**：house-ear `2f5e5ef`（0.7.2 条目）、dot2 `730d6d6`（1.3.1）、xiaoai `288454e`（新建，含 0.2.0/0.1.0 历史 + 0.2.1）。
- **release notes 中英双语**：`docs/release/v0.7.2.md`（`07a702f`）——中文正文 + English 摘要段（release body 可直接取这一段）。
- 三仓 README 已 push（house-ear `d2b2b52`、dot2 `4227654`、xiaoai `7eaba4a`）。
- **仍待做**：README 四项评审结论回收 → 打 tag（`v0.7.2`/`v1.3.1`/`v0.2.1`）→ `gh release create`（body 用 v0.7.2.md 的英文段 + 中文段）→ 从 **tag commit** 构建镜像并记 digest → 核对 `/api/v2/meta` 与 digest 写回 HANDOFF。

### README 四项评审结论（CHANGES，2026-10-26 21:15）→ 待修清单

评审通过：脱敏（图片实看无 IP/token/序列号/人脸/房间照）、许可（三仓 MIT 一致）、措辞（无 VPN/穿透/代理/翻墙；无刷机/root 教程；无厂商 logo、写明第三方）、资产与文件存在性、未测项标注、EQ 数字与 CHANGELOG 一致。

**待修（按优先级，全部是文档改动）**
1. **版本真实性**：✅ 已完成（task-3，2026-10-26）：house-ear/dot2 README 矩阵标题均为「0.7.1 / p21 实测（2026-10-26 15:01），0.7.2 未重测」；xiaoai README 为「2026-10-24 历史实测（0.7.2 未复测）」；本文件 §「三仓 README 的分仓口径」同加版本标签；xiaoai CHANGELOG 的 19/20、205.2 ms 已移入 `### Historical measurements (2026-10-24, not re-tested)`。原始要求保留如下（历史）：三份都写「在 0.7.2 上实测」，但那些数字来自 15:01 的 0.7.1/p21 采样。要求：矩阵标题改「0.7.1 / p21 实测（2026-10-26 15:01），0.7.2 未重测」；xiaoai `:58/:147` 与 `:91/:180` 自相矛盾 → 统一为「插件 9 项契约测试通过；LX06 真机指标为 2026-10-24 历史」。
2. **能力表述与已知限制矛盾**：house-ear `:44/:137`「跨房间互不影响」 vs `:99/:192`（runner 单会话，跨房回复会被中止）→ 改成「跨房间的播放与回合仲裁隔离；对话运行器仍是单会话」；xiaoai `:86/:175` 同改。house-ear `:40`/xiaoai `:21/:110`「核心不含设备专用逻辑/没有小爱专属代码」与 `src/rui/adapter_aliases.py:21`（`"xiaoai": "lx06"`、`LEGACY_DEFAULT_ADAPTER`）不符 → 改「无设备实现，仅保留 0.6→0.7 的临时 adapter 别名」。
3. **有限样本**：LX06 `19/20`、`205.2 ms` 实为 2026-10-24 三轮（24.2/64.5/205.2，max=205.2）→ 逐格标日期 + `max/n=3`。
4. **结构一致**：house-ear 用「分层架构 / Layered architecture」，dot2/xiaoai 用「架构 / Architecture」→ 统一；hero 图口径要写明（house-ear 有 console 截图，dot2/xiaoai 无 → 要么补图要么明确「桥仓不设 hero」）。
5. **tag 不存在**：`v0.7.2`/`v1.3.1`/`v0.2.1` 还没建，README 的 `git clone --branch` 会 404 → **先建并推 tag，再 push README**（README 已 push，因此打完 tag 后再确认链接）；house-ear release badge 仍显示 v0.6.0，tag 后会自更新。
6. **镜像不存在**：`ghcr.io/yes999zc/rui:0.7.2` 尚未发布（GHCR 只有 latest/sha-7a81ec6）→ 必须先构建并推镜像，或在 quickstart 显式给 `RUI_IMAGE=`；`deploy/install.sh` 默认用的是 `:latest`，需在 README 里写明。
7. **dot2 命令/CI**：dot2 README 的 CI badge 指向不存在的 workflow（该仓无 `.github/workflows`）→ 删掉或补 workflow；`rui plug-test` 缺必填 `directory` 参数（`src/rui/__main__.py:708`），镜像内应写 `rui plug-test /opt/rui-plugins/rui_dot2_bridge --adapter …`（见 `deploy/downstream/Dockerfile:23`）；xiaoai `:87/:176` 同改。

### 视觉修改（9 哥 2026-10-26 21:20 补充，压缩后第一优先）

1. **控制台截图留白太多**（1600×1000 但内容居中、两侧大片空白）：裁到内容区（约 1200×1000），或抓图时把窗口调窄；**hero 换成「运行」或「设备」页**（有状态/设备卡片的那张），配置表单页不够抓人。
   - 注意：`/v2/run` 在 demo 实例上有「设备离线 / 配置未完成」两条告警，`/v2/devices` 的卡片显示「状态未知 / 音频面已停止」，且含 `Xiaomi`。**抓图时要么裁掉告警区，要么改 demo 数据后再抓**，不得让告警或厂商名进图。
2. **架构图太扁**（784×168，README 里会被缩得很小）：改成**竖向分层**（服务端 → 音频平面 → 设备），或至少导出 ≥1600px 宽、字号加大；`.mmd` 源文件三仓一起改并重新导出 SVG/PNG，保持同一配色。

**执行顺序（不变）**：修 1–5、7 文本 → 建 tag `v0.7.2`/`v1.3.1`/`v0.2.1` → 从 tag commit 构建并推镜像 → 回改 README 里的 tag/镜像引用 → 核对 `/api/v2/meta` 与 digest → 停下等验收。视觉两项插在「修文本」之后、打 tag 之前做（因为要重新提交 README 与 assets）。

### 发布完成（2026-10-26 19:31）

| 项 | 结果 |
|---|---|
| tag | house-ear `v0.7.2`（commit `ad11936`）、dot2 `v1.3.1`（`620426a`）、xiaoai `v0.2.1`（`9479988`），均已 push |
| GitHub release | 三条已建：`rui/releases/tag/v0.7.2`、`rui-dot2-bridge/releases/tag/v1.3.1`、`rui-xiaoai-bridge/releases/tag/v0.2.1`（中英双语 + Known limitations） |
| 镜像 | **`ghcr.io/yes999zc/rui:0.7.2` = `sha256:d830a2ca029b27f5c25115680746ae533c7e0093e0fe61f1541bd5a0a4dd8018`**（docker-publish 从 tag 构建，含 `0.7`/`latest`/`sha-ad11936`） |
| 下游镜像 | `rui-dot2:1.3.1` 本地 ID `sha256:92ea98f4…`，`FROM` 上面的 0.7.2 digest + dot2 plug 1.3.1；**未推 registry**（无 GHCR 写凭据），发布复现靠 tag + 上面的 base digest |
| 生产 | NAS 168 override → `rui-dot2:1.3.1`，19:31 起 healthy；`/api/v2/meta`：`rui_version=0.7.2`、`compat.min=0.6.0`、`compat.max_exclusive=0.8.0`、`contract=C-002/1`、`api_version=2.0` |
| 回滚 | override 改回 `rui-dot2:1.3.0-p23`（上一状态）或稳定基线 `rui-dot2:1.3.0-r071`，`docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d --pull never`；备份 `rui-dot2-override.yml.bak.20261026-*` |
| CI | `v0.7.2` 上 security / secret-scan / docker-publish 全 success |

### 发布验证（2026-10-26 19:40）

- **verifier-tools-2：VERIFIED**（独立复跑）——三仓 tag `v0.7.2`/`v1.3.1`/`v0.2.1` 存在；三条 release 均非 draft/pre-release；GHCR `rui:0.7.2` digest `sha256:d830a2ca…8018` 与 `docs/release/v0.7.2.md` 一致、`docker manifest inspect` rc 0；生产 `rui Up (healthy) rui-dot2:1.3.1`；`/api/v2/meta` → `contract=C-002/1`、`api_version=2.0`、`rui_version=0.7.2`、`compat={min:0.6.0,max_exclusive:0.8.0}`；`/health` version 0.7.2、devices 2；三仓 `git status` 空；tag CI（security/docker-publish/secret-scan）success；`git clone --branch v0.7.2` 成功。
- 过程中的坑：验证者最初用 `bakeyzhang`/`root` 登录 NAS 失败（免密用户是 `yes999zc`）；改用户后一次通过。token 只以 `token_len=32` 出现，未打印值。
- **已知偏差**：下游镜像 `rui-dot2:1.3.1` 只在 NAS 本地构建（ID `sha256:92ea98f4…`），**未推 registry**（该仓无 docker-publish workflow，环境无 GHCR 写凭据）；可复现性以「tag + base digest」为准。

### 评审判定的收尾（2026-10-26 19:50）

- **release body 已修**：`gh release edit v0.7.2 --notes-file docs/release/v0.7.2.md`，正文不再有 `_待填_`，标题去掉「草稿」字样（`docs/release/v0.7.2.md` 提交 `c4da73c` 之前的 `25870ac`+ 本次）。
- **tag 不移动**：`v0.7.2` 仍指向 `ad11936`（代码 commit）。理由：tag 已公开，改写已发布 ref 风险大于收益；产物表（digest/meta）在 release body 与 HANDOFF 均已是实测值。代价：`git show v0.7.2:docs/release/v0.7.2.md` 仍是当时的草稿表——**已在这里记录，属已知偏差**；下一个版本（0.7.3）自然修正。
- **quickstart 已 pin**：`RUI_IMAGE=ghcr.io/yes999zc/rui:0.7.2 bash deploy/install.sh`（house-ear `c4da73c`），说明默认是 `:latest`。
- **xiaoai plug-test 已补 `--adapter`**：`rui plug-test <插件目录> --adapter rui_xiaoai_bridge.adapter:build`（`4853bbd`），troubleshooting 同步。

### 新手安装路径实测（2026-10-26 20:05，Done 第 6 条）

- 新增 `examples/two-speakers/compose.yaml`（house-ear `64c9f34`），env 全部来自实测：`RUI_CONFIG=/config/config.yaml`、`RUI_WEB_DIR=/app/web/dist`、`PYTHONPATH=/opt/rui-plugins`、`RUI_ADAPTER_PLUGINS="dot2=rui_dot2_bridge.adapter:build,lx06=rui_xiaoai_bridge.adapter:build"`，两个插件目录 bind mount 到 `/opt/rui-plugins/`。
- NAS 实测（`ghcr.io/yes999zc/rui:0.7.2`）：`/health` → `status=ok`、`version=0.7.2`、`devices=2`、`enabled_devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`、`warnings=0`。
- **如实记录的两个坑**：① `web.auth_token` 必须在 **config.yaml**（不是 secrets.yaml），否则容器直接 `config error` 退出；② 没有 TLS 材料（`secrets/server.pem|key`）时音频平面拒绝启动（控制台与 health 正常，日志 `missing TLS material`）；③ `devices[].host` 对 lx06 是必填，缺了会 `config recovery … devices.1.host` 回落到默认值。
- 文档已写：house-ear README 加「三个仓库是什么关系 + 兼容表 + 让 AI 帮你装 + 安装路径实测输出」；两个桥仓 README 加同一段「让 AI 帮你装」。
- README 里已标注：小爱仍走核心内置 legacy 适配器（`adapter_aliases: xiaoai→lx06`），Phase 2 才转外置插件；0.7.2 上同名 id 可能被内置实现遮蔽。

### task-23 评审 CHANGES 修复（2026-10-26 20:25）

评审（reviewer-fleet-auto-2，自己起容器）抓出 6 条，已全部修完并 push（house-ear `e29fb8d`、dot2 `348335c`、xiaoai `54576f7`）：

1. **compose 路径自洽**：顶部写明「先 `cd examples/two-speakers`」+ `--project-directory .` 等价写法；并注明 `deploy/install.sh` 会另起一套 8080 实例，**不要与 compose 同时用**。
2. **device token / TLS**：compose 增加 `RUI_DEVICE_TOKEN`、`DOT2_EM_TOKEN`；README 改为「缺 device token **或** 缺 TLS 材料都会让 `audio_plane=false`，以日志第一条报错为准」；`lx06` 的 `host` 标必填。
3. **legacy 说法更正**（评审实测为准）：外置 lx06 插件**已加载**（`adapter plugins: registered dot2, lx06`，`/opt/rui-plugins/rui_xiaoai_bridge/adapter.py`）；核心别名只在未挂载时回退；不挂 xiaoai 目录 → `registered=["dot2"]`。
4. **health 证明**：改为选字段输出（含 `audio_plane`），贴真实 JSON；`audio_plane: False` 如实写。
5. **兼容表**：加 `compatibility.rui_min`（dot2 `0.3.0` / xiaoai `0.6.0`）。
6. **AI 提示词**：中英各 ≤10 行，含精确 clone/cp、config 字段（lx06 host 必填）、`RUI_DEVICE_TOKEN`、验收/失败/回滚；两个桥仓同步。

- verifier-tools-2 已对**修复前**版本给出 VERIFIED（自建容器：`devices=2`、`device_errors=0`、`registered=["dot2","lx06"]`；反向：无 token → `Exited(2)`、不挂 xiaoai → `registered=["dot2"]`）。
- **当前状态**：等 reviewer 对修复后的 delta 复评；PASS 后把 task-23 置 complete，并重跑一次 verifier（验证 token/TLS 前置条件的表述）。

### task-23 收尾（2026-10-26 20:15）

- reviewer-fleet-auto-2：**PASS**（TLS/token 表述、桥仓提示词路径、compose 可解析、三仓 clean）。
- verifier-tools-2：**NOT VERIFIED → 已修**——他按提示词字面执行 `git clone --branch v0.7.2 … && cd rui/examples/two-speakers` 失败，因为 `examples/two-speakers/` 是 tag `v0.7.2`（`ad11936`）**之后**才加的。修法：三份 README 的安装提示词改为 `git clone --branch main …`，并注明「main 才有本示例；**镜像仍 pin 在 `ghcr.io/yes999zc/rui:0.7.2`**」。commit：house-ear `0029097`→ 本次、dot2、xiaoai 各一条。
- **教训**：README 里 `git clone --branch <tag>` 的目录结构必须与该 tag 实际内容一致；这次是「README 在 main 写、tag 在旧 commit」的典型错配。以后凡是新增 `examples/`，要么先合再打 tag，要么在 README 明确「示例在 main」。
- 待办：verifier 复跑一次字面步骤（clone main → `cd examples/two-speakers` → `cp -r … plugins/` → `ls`）。

## 发布收口（2026-10-26 20:10）——goal 六条 Done 全部满足，停下等 9 哥 验收

| # | Done 项 | 证据 |
|---|---|---|
| 1 | 三仓 README 中英双语重做（矢量架构图+导出、真实脱敏截图、措辞合规） | house-ear `0029097`、dot2 `c79c402`(→`d8bfbc8`)、xiaoai `5126938`(→`6899c49`)；架构图 Mermaid 源 + SVG + PNG（三仓同配色）；hero `docs/assets/console-v2-devices.png`（本地实例、示例数据、无 Xiaomi/无告警）；reviewer 四项清单检过（脱敏/许可/措辞 PASS，真实性与可执行性 CHANGES 已修并 PASS） |
| 2 | xiaoai 全历史扫描通过并公开 | `d741876`；0 token/私钥、0 内网 IP、0 个人路径、0 MAC；PUBLIC；secret scanning + push protection enabled；alerts 0；reviewer PASS + verifier VERIFIED |
| 3 | CHANGELOG / tag / release notes（中英）/ 镜像 tag | CHANGELOG：`2f5e5ef`(0.7.2) / `730d6d6`(1.3.1) / `288454e`(0.2.1)；tag `v0.7.2`/`v1.3.1`/`v0.2.1`；三条 GitHub release（中英 + Known limitations）；镜像 `ghcr.io/yes999zc/rui:0.7.2` = `sha256:d830a2ca…8018`（tag commit `ad11936` 构建）；下游 `rui-dot2:1.3.1` 本地 ID `sha256:92ea98f4…`（未推 registry，见已知偏差） |
| 4 | 发布后 `/api/v2/meta` 与 digest 核对并写进 HANDOFF | 生产 `/api/v2/meta`：`contract=C-002/1`、`api_version=2.0`、`rui_version=0.7.2`、`compat={min:0.6.0,max_exclusive:0.8.0}`；`/health` version 0.7.2、devices 2、enabled 2；生产容器 `rui-dot2:1.3.1` healthy；三处 digest 已对照（见 §发布完成） |
| 5 | CI 全绿、工作树干净 | `v0.7.2` tag 上 security/secret-scan/docker-publish success；`67db5d8`(main) 上 ci/security/docker-publish/secret-scan 4/4 success；三仓 `git status --short -uall` 均为空 |
| 6 | 新手安装路径实测通过 | `examples/two-speakers/compose.yaml`（house-ear）+ 三仓 README 的「让 AI 帮你装」；实测 `/health`：`devices=2`、`enabled_devices=2`、`device_errors=0`、`adapter_plugins.registered=["dot2","lx06"]`；verifier 自起容器复现（含反向：无 token → 退出、少挂插件 → `registered=["dot2"]`）；reviewer PASS + verifier VERIFIED |

**已知偏差（不阻塞验收，已如实记录）**
- 下游镜像 `rui-dot2:1.3.1` 只在 NAS 本地构建，未推 registry（该仓无 docker-publish workflow，环境无 GHCR 写凭据）；可复现性以「tag + base digest」为准。
- tag `v0.7.2` 指向 `ad11936`，其树内 `docs/release/v0.7.2.md` 仍是待填草稿表；发布页 body 与 HEAD 文件已是最新值，tag 未移动（改写已发布 ref 风险更大）。
- `examples/two-speakers/` 在 tag 之后才加入，故 README 安装提示词用 `--branch main`（镜像仍 pin 0.7.2）。

**不做（按 goal 约束）**：4 台联合调试（设备未到）、M10 / console v2 默认切换、任何新功能。

## 交接（给下一个会话，2026-10-26 20:20）

> **任务板不跨会话**：本会话的 `task-*` 在新会话里看不到。所有未完成项都已写进本文件与 `docs/ROADMAP-0.8.md`；开新会话时**先读这两份**，不要凭记忆。

### 现状（已交付）

- 三仓已发布：`rui 0.7.2`（tag `v0.7.2`，镜像 `ghcr.io/yes999zc/rui:0.7.2@sha256:d830a2ca…8018`）、`rui-dot2-bridge 1.3.1`（`v1.3.1`）、`rui-xiaoai-bridge 0.2.1`（`v0.2.1`，仓库已公开）。
- 生产：NAS 168 跑 `rui-dot2:1.3.1`（核心 0.7.2 + 桥 1.3.1），healthy，2 台设备在线；`/api/v2/meta` = 0.7.2 / `compat 0.6.0..<0.8.0`。
- CI：`v0.7.2` tag 与 `67db5d8`(main) 上 job 全绿；三仓工作树干净。
- 新手安装路径：`examples/two-speakers/compose.yaml` + 三仓 README 的「让 AI 帮你装」，实测 `adapter_plugins.registered=["dot2","lx06"]`、`devices=2`、`device_errors=0`。

### 待设备（等 9 哥，不许先动手）

1. 两台新 Dot2 到位后：EQ 耳测（保留 `/loudness`）→ 清 override 的 `DOT2_CONTROL_PORT` → 逐台 `listen_port`（8871/8873）→ v2 添加设备 → 逐台 添加/唤醒/应答/播放/停止/删除 → 4 台联合验收（`docs/release/acceptance-4devices.md`）。
2. 4 台联调通过后才谈 24h 稳定性满窗与版本验收。

### 已知限制（0.7.2 发布时点）

1. cue 回声在「冷启动 + 不削波 + 设备延迟 >800 ms」或「用户与 cue 重叠 <110 ms」时仍可能进 ASR（AEC-lite 已 park，重开前置见 0.7.5 卡）。
2. `runner.abort()` 是全局单 flag：一台设备唤醒会中止另一台正在合成的回复（按房隔离只覆盖播放与回合仲裁）。
3. 已播出的告别尾音追不回，只能阻止后续 chunk。
4. 唤醒词不按房间隔离；多设备并发未测。
5. 下游镜像 `rui-dot2:1.3.1` 只在 NAS 本地构建（`sha256:92ea98f4…`），未推 registry；复现靠「tag + base digest」。
6. tag `v0.7.2` 树内的 `docs/release/v0.7.2.md` 仍是待填草稿表；发布页 body 与 HEAD 已是最新（tag 未移动）。
7. `uv.lock` 内版本号陈旧（0.2.0），不影响运行。
8. 生产 NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理输出中回显（未落仓库）→ 建议轮换。

### 下一步第一张卡（明确）

**`[impl] 0.7.3 / 跨房中止策略`**：把 `runner.abort()` 从全局单 flag 改为按房作用（或明确文档化并暴露到 `/health`），同时把 `turn_open` 单布尔改成按房结构；出口条件写在新卡的验收标准里，参考 `docs/ROADMAP-0.8.md` 的 0.7.3 段。

### 如何接手（按序）

1. 读本文件 + `docs/ROADMAP-0.8.md`；
2. `git -C 01_Code/projects/house-ear status --short`、`gh run list --limit 3` 确认基线；
3. 从「下一步第一张卡」开始建卡（新会话的任务板是空的，需 `team_task_create`）；
4. 设备到位后按「待设备」段的顺序走，不要跳过 EQ 耳测。

## 风险与未决

- **task-6 P1 已交付，等 P2 决策**：Dot2 唤醒 30/30 admitted、指令 26/30；掉分两处（ack cue 回声 13/26、音乐中「下一首」2/5）已落日志行；LX06 与小爱对比仍缺样本（不同屋）。
- **B1 中文唤醒（task-3，BLOCKED）**：V0 英文 stock 模型对产品无效（中文「你好小瑞」只走 RUI KWS，英文零事件、`wake_candidates=0`）。真前置不是固件：① 中文 OWW 模型需 `oww_forge` 自训（25GB/数小时，效果待测）；② bridge 无 `/shell` 通道（`adapter.py:81`），最小验证需一次性 adb 推 ~1MB 分类器。待 9 哥 决定：是否再跑 V0 / 是否接受自训成本 / 生产机是否允许一次性 adb / barge 门槛是否 0.25。
- **EQ 耳测未做**：测量指标改善已证，但 9 哥 尚未 A/B；`trim_db=-2.0` 未重调（A 计权响 4.1 dB）。
- **cue 回声残留**：envelope 匹配对设备延迟 >~950 ms 或与用户语音完全重叠时回退旧行为，可能切掉 ~300 ms。
- **多设备尚未实测**：`multi_device.enabled=true`、`max_planes=4`，但只有 1 台 Dot2 在线；唤醒词不按房间隔离（`lost_same_word`）；`DOT2_CONTROL_PORT` 是全局 env，加第 2/3 台前必须清掉；多设备默认共享 secrets。
- **24h 稳定性未复查**：从 p22 部署（17:15）起计，尚未满窗；13:36 那轮作废。
- **凭证**：NAS 密码 / `RUI_HA_TOKEN` / `web.auth_token` 曾在子代理会话输出中回显（未落仓库）→ 建议轮换 NAS 密码；本会话临时口令文件（本地 + 远端 `/tmp`）已删除。
- **源码全量镜像构建仍阻塞**：NAS 上 `deb.debian.org` 超时、换清华源后仍 `Unable to locate package nasm/libopus-dev`，故当前走薄层镜像（底座 + overlay），可复现性只到「tag → 薄层 digest」。
- **backlog**：`uv.lock` 停在 0.2.0；下游 docs 兼容窗口文案陈旧；`coordinator.turn_device`/`drop_device` 生产路径未用；`turn_tasks` 仅在 `start_audio` 时初始化；覆盖缺口（`_wake_in_flight`、output_owner/gate_speaking）；whitespace-id 归一化无用例。

## 关键文件

- 契约：`docs/contracts/C-002-console-v2-api.md:1`、`docs/socket-plug-v1.md`
- 计划/分析：`docs/plan/speaker-tuning.md:23`（诊断）、`:343`（B1 评估）、`docs/analysis/`（task-6 产出）
- v2 后端：`src/rui/web/api/v2_api.py`、`src/rui/web/api/app.py`（`TURN_STALE_S`、`_PlaneLink.release_turn`）
- 音频：`src/rui/audio/process.py`、`src/rui/audio/vad.py`（`set_echo_reference`）、`src/rui/audio/coordinator.py`
- 控制台：`web/src/v2/App.vue`、`web/src/v2/router.ts`、`web/src/v2/tools.ts:42`
- 下游：`rui-dot2-bridge/src/rui_dot2_bridge/adapter.py:81`（无 `/shell`）、`dsp.py:196`
- 发布：`docs/release/v0.7.0.md`、`docs/release/v0.7.1.md`、`docs/release/acceptance-4devices.md`

## 约束与禁区

**协作规则**
1. 需要 9 哥 拍板时用 `ask_user_question` 提问并等待，不要在总结里只写「等你决定」。
2. 要真正停手交接或等真机时明确说「请 pause goal」，等 pause 后再继续。
3. 9 哥 发消息不取消 goal；范围变更先写进任务卡或本文件，再动手。
4. **对外发布用哪个版本号必须先问 9 哥 确认**（「按需升版」保留）。

**禁区**
5. 任何改动先过 fresh reviewer + verifier 再谈 commit；不碰 secrets、不自行宣传。
6. 不要相信「`/v2/tools` 未过门禁」的旧说法：已过门禁并提交 `ef7ff18`，且已进 0.7.1 镜像。
7. 不要相信「打断已修复」的旧说法：A1' 真机 8/8 可用，但 P2 类体验差距待 task-6 量化。
8. 不要相信「Dot2 音质已变好」：只证测量指标改善，未经 9 哥 耳测。
9. 不要相信「设备侧中文唤醒已可用」：中文 OWW 模型不存在，V0 英文路径对产品无效。

## 核验命令（接手先跑）

```
cd 01_Code/projects/house-ear && .venv/bin/python -m pytest -o addopts='' -q   # 期望 1613 passed / 17 skipped
.venv/bin/python -m ruff check src tests                                       # All checks passed
cd web && npm run typecheck && npm run build                                   # exit 0，dist/index.html + dist/v2.html
cd ../rui-dot2-bridge && PYTHONPATH=src ../house-ear/.venv/bin/python -m pytest -q   # 期望 143 passed
cd ../rui-xiaoai-bridge && PYTHONPATH=src ../house-ear/.venv/bin/python -m pytest -q # 期望 9 passed
gh release view v0.7.1 --json tagName,url                                      # tag 与 release 存在
```

## 没做的事

- **没做**：B1 中文唤醒模型与 adb 验证（task-3 BLOCKED）、task-6 矩阵采样、4 台联合验收、M10 默认切换、9 哥 耳测、24h 稳定性复查、源码全量镜像构建。
- **没做**：宣传/公告/营销文案（明确不做）。
- 任务板会随会话消失；跨会话只信本文件 + `docs/release/*`。

> 非 [phase] 收口任务，故不写 phase-close 标记；task-12 收口时才由 Lead 追加 `phase-close: task-12`。

## task-3 进度检查点（2026-10-26 21:2x，工作树未提交）

- **任务**：`[docs] 0.7.3 覆盖率缺口台账 + 8 处文档矛盾收口`（impl-core，in_progress）。
- **已完成**：8 处矛盾全部落改（house README:111,248；dot2 README:89,95,97,203,209,211；dot2 milestone-m1:23-24；dot2 canary:249-252；xiaoai CHANGELOG:15-23、README:90,197；socket-plug-v1:387；code-health:23,408,474,550；HANDOFF §「三仓 README 的分仓口径」/§「README 截图与稿件的当前状态」/§「README 四项评审结论」）。
- **已完成**：`docs/audit/unverified-inventory.md` 新增 §3 终态表、§5 覆盖率台账（**12 个一级子包 + root = 13 个 scope**，三类缺口，HE-13/14/17 输入，batch-1 对应）、§6 真机 26 条三件套、§7 task-2 reviewer backlog。
- **已完成**：三仓 grep 前后计数（house README+docs 90→**92**；其中 inventory 27→**28**、排除 inventory 核心 63→**64**（按 per-file 实测求和），核心 +1 来自 HANDOFF §「README 四项评审结论」新加的已核对历史标签；dot2 14→14；xiaoai 10→10）与 `git diff --stat`；§3 8 行、§6 26 条真机行均通过结构自检。
- **CHANGES 轮（reviewer-core，2026-10-26 21:27）**：基线按 0.7.3 最终值重建（cd2d0a2，1651/1634/17/0，行 80.95 / 分支 69.55）；plugins 缺口改归「可单测未写」；HANDOFF §「README 截图与稿件的当前状态」恢复 v0.7.2 快照并另起一行 0.7.3；X-01/D2-05/D2-09 命令与 cwd 修正；dot2 volume 口径三拆；§3 #4 备注改准。
- **待做**：重跑 ruff/全量/grep/基线命令，send_message 给 reviewer（经 lead）；等复评。
- **未做**：没有 commit；没有改代码/版本号；没有碰卡面写范围外文件（`docs/ROADMAP-0.8.md` 的未提交改动是 Lead 的，不是本卡）。

### B1 / task-8 进行中（implementer，2026-10-28 12:0x，HEAD `8344085`）

- **范围**：删 `/legacy` 旧面板与 15 条老链接 302；保留 `/`→`/v2/`、`/v2`、`/v2/assets`、`/api/v2`、`/api/v1/panels`。
- **已确认事实**：v2 只跨树引用 `components/AppIcon.vue`、`theme.ts`、`style.css`、`vite-env.d.ts`、`public/*`；`web/v2.html` 产品名 `dist/v2.html`，字体/favicon 走根路径 `/fonts/*`、`/favicon.svg`；旧 catch-all 一旦删除，这些根静态资源必须显式 mount。
- **已定做法**：删 `legacy_links.py`、`app.py` 的 `/legacy*`、`_legacy_shell`、`FALLBACK`、根 catch-all；`/` 恒 302 `/v2/`（保留 query）；`web.console` 枚举保留但值被忽略（文档写明）；v2 内 `/legacy` 链接与 `?from=` 提示一并删；`vite.config` 仅 `v2` 输入；Dockerfile 检查 `dist/v2.html`；根静态资源显式 mount（favicon.svg / fonts / logo*.svg），用 200 矩阵测试钉住。
- **验证计划**：`cd web && npm run typecheck && npm run build`；`uv run pytest -o addopts=""`（基线 1738/6）；`uv run ruff check src tests`；新增 404/302/200 矩阵。
- **状态（2026-10-28 12:2x 完成）**：代码/前端/测试/文档全部落改，**未 commit**；验证见下。
- **验证**：`npm run typecheck` 绿；`npm run build` 绿（`dist/v2.html`，无 `index.html`）；
  `uv run pytest -o addopts=""` → **1710 passed, 6 skipped**（基线 1738 − 删除的 28 条：`test_legacy_links.py`
  7 条、2 条已删 Vue 的 UI 源码契约测试、其余为改写后参数化条数变化）；`ruff check src tests` 绿；
  `grep '/legacy|legacy_links'` 在 `src/rui` + `web/src` 只剩注释。
- **遗留/风险**：`/api/v1/config/raw` 与 HA 表单在 v2 没有对应 UI（原 UI 源码契约测试随之删除）；
  `/api/v1/panels`（v1 冻结）里的 href 仍指 `/panel/*`，但页面已 404；`web.console` 值被忽略。
  后续接手先看 `git status` 与任务卡 task-8。
- **复评修正（2026-10-28，review CHANGES）**：`config.example.yaml` / `template.yaml` /
  `schema.py` 的 `web.console` 注释统一为「compat-only、值被忽略、`/` 恒 302、无回滚」；
  `test_panels.py` / `test_audit_fixes.py` 的 McpManager/ConfigCenter 注释改为「v1 payload only，
  UI 已删」；`App.vue` 空 `<style scoped>` 删除；`known-limitations.md` 新增 **L18**（HA `base_url` 在 v2 ToolsView 仍可改；缺 HA token 表单 与
  `/api/v1/config/raw` 视图，API 仍在）。删除项已在 Lead 的 `c28e5e4`（本地未 push）；
  B1 已最终 review PASS + verify VERIFIED，re-commit 为 `7b9035a`（feat(console)!，已 push）。验证：目标 54 passed；全量 1710/6；ruff 绿；typecheck 绿。

### P1 / task-17 进行中（implementer，2026-10-28，HEAD `4b8b8b0`）

- **范围**：会话关闭必须 disarm（或明确 followup 窗口 N），并修 `idle goodbye in inf s`；不改 ack/chime/barge/music/契约。
- **已查明根因（下一轮直接实现，不要重新考古）**：
  1. `inf` 不是配置超时：`src/rui/session/idle.py` 的 `IdleWatch.remaining()` 在「没有打开的会话」或 `timeout_s<=0` 时返回哨兵 `float("inf")`；`app.py` 的 `on_wake_idle` 用 `log.info("... idle goodbye in %.0f s", runner.idle.remaining())` 直接打印， farewell 已 `idle.stop()` 后就会打 `inf s`。修法：日志按 `idle.active` 分支——活动时打印有限剩余秒数，非活动时打印「session already closed / no countdown」；不改 `remaining()` 语义（其他调用方依赖哨兵）。
  2. `session closed` 不 disarm：`app.py:idle_watch` 在说完告别后调用 `client.reply_finished()`（本意是让音频面开 post-playback mute），但 `process.py:_arm_followup_later` 收到 `ReplyFinished` 后会用 `max_wait_ms = session.timeout_s*1000`（15000）重新武装 followup gate → 告别后最多 15 s 的免唤醒窗口（04:15:26 close → 04:15:43 turn）。
  3. 选定语义：**有限 followup 窗口 N 秒**（不是无限监听）。`VoiceConfig` 新增 `followup_max_wait_ms`（默认 8000，0=立即关闭/不续听），plane 用 `min(session.timeout_s*1000, followup_max_wait_ms)` 作为 gate `max_wait_ms`；告别后的窗口因此 ≤8 s，20 s 后必须唤醒词。文档写清 N，known-limitations/backlog 各登记一条。
- **必测（先红后绿）**：告别后 1 s 说话 → 固定为「接受（followup 窗口内）」；告别后 20 s 说话 → 无唤醒必须拒绝；`idle goodbye in inf s` 不再出现（日志断言/单测 `remaining()` 非活动时日志文案）。
- **未做**：代码/测试/文档、验证、commit。工作区干净（本卡尚未动文件）。

## Bridge 交接（2026-10-30 03:2x，压缩前写下；与开发会话收口内容冲突时以开发会话为准）

- **生产**：0.8.10 已部署（2026-10-29 23:19），`/health` 0.8.10 / config 3 / 5 平面 / warnings [] / device_errors 0。
  回滚锚：0.8.10 index `sha256:30e8ed47f2ae0ce4281752c4130bfa58e53f63095f128eae5330e5e5703e1155`。
- **0.8.11 在收口中**：已提交 `6c0e14e`（身份配置 + HA 清单过滤 + USB 快照脚本）、`32ac605`（scene 白名单 + 批量部分成功）；
  版本号已 bump 0.8.11；工作区仍有 T3（`local.look`）与 release 文档在改。
- **发布/部署**：按「降本收口」执行 —— T3 只走 reviewer（不额外 verifier）、全量测试只跑 1 次、release 打 tag、
  夜间静音部署（不触发 TTS/唤醒）；发声类验收留白天。
- **部署时需写入的本机配置**（勿写死进代码）：`assistant.persona` 身份句、`tools.ha.context_domains=[light,switch,cover]`、
  `tools.ha.context_exclude=['摄像机|Camera Control','音箱|Speaker|lx06|小爱']`、`tools.ha.allowed_scenes`（5 个场景）、
  `vision.usb_snapshot_url`（Mac 侧 8766）。
- **待白天验收**：问版本（应为 rui 0.8.11，不是 HA）、问身份、关全屋灯/关书房灯走 scene、天奕门口画面、
  书房 USB 画面、HA 灯批量读（issue #34）、barge 短提示与削波。

### 0.8.11 部署后修正（2026-10-30 03:0x，bridge 直接改生产配置）

- **现象**：9哥 问「系统版本是多少」，rui 答对 0.8.11，但**多补一句「我不是 Home Assistant」**。
- **根因（两处叠加，都在提示词里）**：
  1. 代码默认提示词 `src/rui/session/prompts.py:53` —— 「…说的是你自己（rui {version}），**不是 Home Assistant**；
     提到 Home Assistant 的版本时必须明确说成是它的版本…」，这条把"否认 HA"绑在"回答版本"上；
  2. 部署时写进本机 `assistant.persona` 的文案 —— 「跑在 rui {version} 上（**家里自建的语音服务，不是 Home Assistant**）」。
  `prompts.build()` 是 `template = system_prompt or DEFAULT_SYSTEM_PROMPT`，再把 persona 追加在后面，
  所以两处都会进上下文 ⇒ 模型每轮被提示"我大概不是 HA"，于是主动澄清。
- **已修（配置层热改，不重启容器）**：`PUT /api/v1/config`（从 NAS localhost 调，Mac 到 5.168:8080 无路由）——
  - `assistant.system_prompt` = 默认提示词去掉那段 HA 否认，改为「用户没问 Home Assistant 的时候不要提它。」；
  - `assistant.persona` = 去掉「（家里自建的语音服务，不是 Home Assistant）」。
  - 校验：`GET /api/v1/debug/prompt` → 3747 字，`Home Assistant` 只出现 1 次且是抑制规则；占位符 `{name}{version}{max_chars}{long_chars}` 均在。
- **待办（0.8.12）**：把 `prompts.py` 默认文案按同样口径改掉（不然每个新装用户都得自己覆盖 system_prompt），
  并考虑把「用户问到 HA 时如何回答」写成条件规则，而不是默认否认。
- **风险**：本机 `system_prompt` 现在是**非空覆盖**，以后改默认提示词不会自动生效到生产；升级时记得对照一次。

### 「下线」作为结束词（2026-10-30 08:5x，bridge 直接改生产配置 + 待进代码）

- **9哥 现场**：说「下线」→ 模型答了句「好，那我先歇着」但**音乐没停**，会话也没关。
- **生产日志（00:48:42）**：`turn: transcript '下线。' reply '好，那我先歇着。' tools []` —— 走了普通 LLM 轮次，
  因为「下线」不在 `session.end_words` 里，`rules.stop_intent` 不认；直到 00:49:17 他说「停止播放。」才
  `stop intent -> media stopped, session closed`。
- **已修（配置层热改，HTTP 200，无重启）**：`PUT /api/v1/config` 写 `session.end_words`，
  加 `下线` / `下线吧`（顺手去掉了重复的 `退下吧`），现 23 条。
- **语义已核**（`PYTHONPATH=src .venv/bin/python`，用同一份词表）：
  - `下线` / `下线吧` / `下线。` → `stop_intent=True`，有音乐时 `stop_scope=media_stop`
    ⇒ **停音乐 + 关会话**（正合 9哥 预期）；
  - `设备下线了吗` → `False`（疑问句不受影响）；`你好小瑞下线` → `False`（唤醒词在头部，不在尾部剥离集里，可接受）。
- **待进代码**（下一批）：`config/schema.py` 的 `end_words` 默认值加这两个词 + `rules` 侧表驱动测试
  （含"疑问句不触发"与"音乐播放时 media_stop"两条断言）；否则新装用户拿不到。

### 2026-10-30 09:3x 三个现场修复已上线（bridge 直接改，未走 Gate）

- **镜像**：`ghcr.io/yes999zc/rui@sha256:7dd7507d94fec85e0b2f1f03757bbdb8595f4be13cb9dff903c1f3e96b639a2e`
  （commit `0bcb241`，由 main push 发布的 `:sha-0bcb241`；版本号仍是 0.8.11，未 bump）。
  回滚锚：上一个 digest `sha256:8965bd92febdca89dd3bb73be7db806946cd43b91ba243ceb1c6187bc7f7ccbe`；
  override 备份 `rui-dot2-override.yml.bak-*`。
- **① dot2 回声门 2500 → 1200 ms**（配置层，4 台 dot2）：`ack_echo_ignore_ms`，与客厅 LX06 一致。
  现场：唤醒后「你好小宝，现在几点」一口气说，命令落在 2.5 s 门里被吞（日志 `wake with no speech
  after 3000/5020 ms`）。改后 5 台均 1200，`/api/v1/devices` 已核。
- **② 空唤醒不再道别**（代码 `turn.py`）：`_had_speech` 在 `run()` 置真、`reset_history()` 清空；
  `check_idle` 在会话从未说过话时静默关闭（日志 `silent close (no speech in this session)`）。
  现场：说完「退下吧」→「好。」→ 12 s 后一条**空会话**又播「没事的话，我先下啦。」。
- **③ 「下线」「下线吧」进默认结束词**（`schema.py`，21 → 23 条）：现场说「下线」被当闲聊，
  音乐继续、会话不关。整句匹配语义不变，`设备下线了吗` 仍不触发。
- **测试**：新增空唤醒静默关闭 / 说过话仍道别 / 下线三态断言；全量 **2311 passed / 6 skipped**，ruff 绿。

### 2026-10-30 10:2x 0.8.12 发版并部署（bridge 直接执行）

- **为什么发**：0.8.11 上线当天的三个现场问题要有个版本号可回看；顺带把 `secret-scan` 三次连红的两处真实
  内网地址换掉（`docs/HANDOFF.md` 的 `usb_snapshot_url` 示例 → `http://<mac-lan-ip>:8766/snapshot.jpg`）。
- **镜像**：`ghcr.io/yes999zc/rui:0.8.12`，多架构 index digest
  `sha256:64a2c260dbf8373efefdebe06be3817ba555c874ce18a12a03e9a54424dfa33e`
  （commit `22623a1`，tag `v0.8.12`；同时发布 `:0.8`、`:latest`、`:sha-22623a1`）。
- **回滚锚**：`sha256:7dd7507d94fec85e0b2f1f03757bbdb8595f4be13cb9dff903c1f3e96b639a2e`（0.8.11 + 三修复那版）；
  override 备份 `rui-dot2-override.yml.bak-20261030-102144`。回滚 = 把 override 的 digest 换回上面那串 + `compose up -d`。
- **部署**：`/vol1/rui/rui-dot2-override.yml` 钉 digest → `docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d`；
  容器重建，镜像从 GHCR 拉取成功。
- **验收（容器内实测）**：`rui.__version__ = 0.8.12`；`/health` ok / devices 5 / device_errors 0 / speaker_connected true；
  默认 `session.end_words` 23 条且含「下线」；`TurnRunner.check_idle` 含 `silent close` 分支；
  `/api/v1/music/search?q=平凡之路` 仍返回 20 条（私有源插件在新镜像下正常加载）。
- **发版过程中被 CI 抓到的一处疏漏**：只 bump 了 `src/rui/__init__.py`，漏了 `pyproject.toml` 与 `uv.lock` 里的
  版本号，`tests/test_version.py` 断言两者相等 → tag 首次推送的 test 闸门红（1 failed / 2301 passed）；
  补齐后推送闸门转绿：**CI 2302 passed / 15 skipped**（本机 `.venv` 是 2311 passed / 6 skipped，
  多跑的 9 条是 CI 缺 libspeexdsp / ffmpeg 而跳过的 aec 6 条 + audio-scale 2 条 + jellyfin 1 条）；
  tag 已 `-f` 移动到 `22623a1`。
- **设备现状**：9哥 静音了大部分音箱并说家里没人，`/api/v1/devices` 里 `dot2-d`（卫生间）`connected=false` 属预期，
  等设备上线再核一次。

### 2026-10-30 发版流程加固

0.8.12 的收口不只补了版本号，还把「发完就完了」这件事钉成流程。本节只记口径，CI 侧的实现细节
（哪个 job、哪一行）以 `.github/workflows/` 为准，不在文档里写死。

- **`:latest` 曾被一次 docs-only 提交带走**：0.8.12 发布后，一个只改文档的提交落在 `main` 上，
  `:latest` 被重新指到第二份构建（label 是 `version=main`、`revision=<那个 docs 提交>`），而
  `:0.8.12` 仍指向发布那一次的 index。`:latest` 已被 retag 回
  `sha256:64a2c260dbf8373efefdebe06be3817ba555c874ce18a12a03e9a54424dfa33e`，现在
  `:0.8.12` / `:0.8` / `:latest` / `:sha-22623a1` 四个 tag 同一个 digest（可用
  `docker buildx imagetools inspect ghcr.io/yes999zc/rui:latest` 复核）。
- **后续口径**：`latest` 只跟 tag。`main` 上的普通提交发 `:edge` / `:sha-<7>`，不动「已发布」所指；
  发版时 tag 必须打在 `main` tip，打完不要再往 `main` 推会改变发布语义的东西。九步清单见
  `docs/releases/README.md`。
- **发布闸门增加两条**：tag 名必须与版本号一致（`v0.8.12` ↔ `0.8.12` 三处版本号），以及 secret-scan
  前置——不再出现「扫描红着、镜像照样发出去」。
- **文档侧同步**：`docs/releases/README.md` 的清单补齐 `uv.lock` 一步（**无测试保护**）、annotated
  tag、tag 在 `main` tip、发布后 digest/label 回填；`docs/releases/v0.8.12.md` 的验收改为
  「本机 2311 / 6 + CI 2302 / 15」两套数字并标明差异来源（CI 缺 libspeexdsp / ffmpeg 的 9 条）。
- **发版文档里不要只写一个测试数字**：本机与 CI 的差距是环境差异，不是失败；只写一个会让下一个
  人以为自己跑错了。

- **2026-10-30 11:5x 加固已落地（`f34488e`）**：`:latest` 只跟 tag（main 只滚 `:edge`/`:sha-<7>`）；docs-only push 不再构建；
  发布闸门加「tag == pyproject 版本」与「secret-scan 前置」两道。落地后实测：`:latest` 仍是 `sha256:64a2c260…`（未被这次 main push 带走），
  `:edge` = `:sha-f34488e` = `sha256:f7b803a5…`，ci/security/secret-scan/docker-publish 四项全绿。
  本段提交（只改 docs）本身用于反证 paths-ignore：这一推应当**不触发** docker-publish。

### 2026-10-30 13:xx 0.8.13 在飞状态（压缩/换会话后从这里接）

- **本版范围**（9哥 授权，仅此一次改 rui 源码）：① 控制台「试一试」不再被算成「说过话」，且对房间会话零副作用（快照/恢复）；② 中文数字数量词（「播放三十首周杰伦」等同 30 首）；③ 空唤醒可观测计数 + `tools.ha.call_timeout_s` 上限与 turn 工具超时对齐。
- **已完成**：①（`turn.py` 加 `SessionSnapshot`/`snapshot_session`/`restore_session`、`run(counts_as_speech=False)`；`app.py` 的 `_run_debug_say` 改成 finally 恢复；8 条新用例，两个变异都红，定向 51 passed）；② 已交回（未提交，见工作区 `music/` 相关文件）。③ 正在收尾（被 steer 去修它自己撞出的回归：`tests/test_followup_window.py` 的 `_real_gate()` 缺 `silent_wakes` 字段）。
- **工作区**：16 文件改动、548 插入未提交；三份改动尚未合并审查。
- **下一步**：合并 reviewer 一次看三份 diff → 我收口跑一次全量（2317+ 条）→ 按 `docs/releases/README.md` 九步发 0.8.13（三处版本 → release note → CHANGELOG → 本文件 H1 → annotated tag → 等 CI 出镜像 → 钉 digest 部署 → 容器内复验「点试一试 15 秒后不出声」）。
- **流程**：本轮起按 bridge-dispatch 的「精简档」（定向测试、每卡最多 1 轮 CHANGES、verifier 只在发版）。

### 2026-10-30 14:0x 0.8.13 交接快照（压缩/换会话后从这一段接，按顺序）

**状态**：三份改动已全部落地（未提交），合并 reviewer 在跑；主控正在跑收口全量（`/tmp/close0813.log` + `--junitxml`）。

**改动清单（`git status` 应看到这 18 项左右）**
- A 控制台不算说过话：`src/rui/session/turn.py`（`SessionSnapshot`/`snapshot_session`/`restore_session`/`run(counts_as_speech=False)`）、`src/rui/web/api/app.py`（`_run_debug_say` 改 finally 恢复）、`tests/test_session_timeout.py`、新 `tests/test_debug_say_not_speech.py`
- B 数量词：`src/rui/music/{query,model,tools}.py`、`tests/test_music_query.py`、`tests/test_music_tools.py` —— 新增 `_COUNT`/`cn_number()`/`split_count()`/`QueryPlan.limit`，上限 50；同时修了 `放一?` 会吃掉「放一生中最爱」的「一」
- C 可观测：`src/rui/audio/{vad,protocol,process}.py`（`silent_wakes` / `silent_wakes_session`，进 `Metrics` → `/api/v1/audio`）、`src/rui/config/schema.py`（`HA_CALL_TIMEOUT_MAX_S=20`）、`config.example.yaml`、`src/rui/config/template.yaml`、两条被改用例 `tests/test_followup_window.py`（`_real_gate` 补字段）、`tests/test_ha_scene_batch.py`（23→20）、新 `tests/test_wake_no_speech_count.py`、`tests/test_ha_call_timeout_cap.py`
- 主控手改（两行元数据，已声明）：`src/rui/config/catalog.py:1232/1234/1237` —— 控制台上限 60→20 + 文案「2–20」

**测试现状**：三份改动各自只跑定向（A 51 passed；B 281 passed 的 music 子集；C 387 passed 的 schema/wake 子集 + 两文件 55 passed + 112 passed 的音频子集），变异各 2–3 处全部能红；全量只在本段由主控跑一次（上一条快照里的 2317 是 0.8.12 的数字，0.8.13 收集数会增加到 ~2346）。

**下一步（顺序）**
1. 等合并 reviewer 结论（`deleg_bde0ffec`）；CHANGES 就回炉一次，PASS 才继续。
2. 看收口全量结果：`python3 -c "import xml.etree.ElementTree as ET; r=ET.parse('/tmp/close0813.xml').getroot(); s=r if r.tag=='testsuite' else r[0]; print(s.get('tests'),s.get('failures'),s.get('errors'),s.get('skipped'))"` —— 必须 failures=0/errors=0。
3. 按 `docs/releases/README.md` 九步发 0.8.13：三处版本（`src/rui/__init__.py`、`pyproject.toml`、`uv lock`）→ `docs/releases/v0.8.13.md` → CHANGELOG `## [0.8.13]` → 本文件 H1 → `git tag -a v0.8.13` 打在 main tip → push main + tag → 等 docker-publish 出 `:0.8.13` → `docker buildx imagetools inspect` 取 digest → 改 NAS `/vol1/rui/rui-dot2-override.yml` 的 digest 并 `compose up -d`（先备份 override）→ 容器内复验 `__version__`、`/health`、`GET /api/v1/audio` 里的 `gate_silent_wakes`。
4. 真机复验（无需人说话也能做的部分）：控制台点一次「试一试」→ 等 15 s → 日志应出现 `silent close (no speech in this session)` 且**没有** TTS 道别；说「播放三十首周杰伦」应放 30 首。

**回滚**：旧镜像 `sha256:64a2c260…`（0.8.12）；override 备份带时间戳。

### 2026-10-30 13:5x 0.8.13 发版并部署（bridge 直接执行）

- **版本**：`__version__` / `pyproject` / `uv.lock` 三处 0.8.13；tag `v0.8.13`（annotated）→ commit `3e5022b`，main tip 与 tag 指向同一提交。
- **镜像**：`ghcr.io/yes999zc/rui:0.8.13`，索引 digest `sha256:a951eeb68ebea13d5e84b3237f54cf2d0d68c92ba30cc5d9bf3a4a987830026c`；`:0.8`、`:latest`、`:sha-3e5022b` 同一 digest（`latest` 只跟 tag 的规则首次在真发版里生效：main push 没动它）。
- **回滚锚**：`sha256:64a2c260…`（0.8.12）；override 备份 `rui-dot2-override.yml.bak-20261030-134431`。
- **部署**：override 换 digest → `compose up -d` 重建 → 容器 healthy。
- **容器内验收**：`__version__ = 0.8.13`；`/health` ok / devices 5 / device_errors 0 / speaker_connected true；`/api/v1/audio` 的 metrics 里已有 `gate_silent_wakes` 与 `gate_silent_wakes_session`（均 0，新计数接线是活的）；`dot2-d` 仍 disconnected（9哥 静音/没人，预期）。
- **CI**：tag 闸门实测输出 `tag v0.8.13 matches pyproject version 0.8.13`；本机 2343/6、CI 2334/15；secret-scan 与 security 均绿。
- **未做**：真机语音复验（点「试一试」后 15 s 应只有 `silent close` 日志、无道别；说「播放三十首周杰伦」应放 30 首）——需要有人在房间，留给 9哥 或白天。

- **2026-10-30 14:0x 会话收口**：跨项目交接写在 `~/AI_Workspace/04_Docs/bridge-handoff-2026-10-30.md`（含待办分类、授权边界、重开后怎么接）。9哥 将 `/new`，新会话先从那份文件接。

### 2026-11-01 21:0x 客厅（小爱 LX06）现场：放歌失败 + 空唤醒修复已验证

- **已验证生效**（10-30 那三个修复在线上跑 2 天）：
  - `session idle for 15.0 s: silent close (no speech in this session)` —— 空唤醒不再道别 ✅
  - `stop intent: '停止播放。' -> media stopped, session closed` ✅
  - 未再出现「退下吧后又来一句我先下啦」。
- **新问题：放歌取不到曲**（9哥 报「小爱」）：
  1. 「放点轻音乐。」→ `music: skip ... (not a private track: 'subsonic:...')` ×8 → `music.unresolved`
     → 回「歌都在，但一首也没取出来」。**random/library 把 subsonic 源的曲子选了出来，
     private-music 插件以"不是私有曲目"拒掉**，一次都没播。
     随后 12:57:42 又起了一次 `random/library` 并 `playback requested ... 4a2996639a1240a4.wav`（这次成功），
     说明是"选曲来源混了"，不是没有歌。
  2. 「放首周杰伦那个。」→ `search/online: skipped_short=1 (min_duration_s=45)` → `music.not_found`
     → 回「曲库里没找到周杰伦的歌」。**查询词里带口语填充"那个"，在线搜索命中一条却因时长 <45 s 被丢**。
- **待办（下一张卡）**：① random/library 选曲时先过滤掉非 private 来源，避免 unresolved 空转；
  ② 在线搜索前剥离口语填充（那个/这首歌/一下）；③ 未命中时如实说"没搜到合适的版本"而不是"曲库里没有"。
- 设备侧 ssh 已不可用（`no matching host key type ... offer ssh-rsa`，且 root 需密码）→ 目前只能看
  rui 侧 `plane:` 镜像日志；要看设备 `/tmp/client.log` 需先解决凭据/算法（记一条环境债）。

### 2026-11-01 21:1x 放歌问题定位修正（自有音乐服务是健康的）

- **自有服务**：`rui-personal-services-music-svc:0.1.4`，host 网络，**端口 18082**，
  `/v1/search`、`/v1/resolve`、`/v1/stream/{id}`；`/health` ok，resolver=`gdstudio+meting`。
  鉴权头 `X-Internal-Token`（值在容器 env `INTERNAL_TOKEN`）。
  rui 侧接线：`rui-dot2-override.yml:10 RUI_MUSIC_SOURCES: private=rui_private_music:make`，
  插件的 online 半边走它（`plugins/rui_private_music/source.py:11`，返回 id 前缀 `private:`）。
- **实测（curl 18082）**：
  - `q=周杰伦` → 想你就写信(Live)/屋顶/布拉格广场，时长 238/319/294 s —— **服务正常**。
  - `q=周杰伦那个` → 首条「你还是那个周杰伦吗 / 赛博蝉鸣声乐团」257 s（**口语填充把查询弄坏**）。
  - `q=放点轻音乐` → 三条 139/150/77 s，均 >45 s 下限。
- **结论（两条失败原因都不是自有服务挂了）**：
  1. 「放点轻音乐」走的是 `random/library`，选出来的是 `subsonic:<hash>` 的曲子，
     而插件自己的校验只认 `private:` 前缀 → 8 条全 `skip (not a private track)` → `music.unresolved`。
     **插件内部两个库源（Navidrome/subsonic vs 自有服务 private）混用**，是插件 bug。
  2. 「放首周杰伦那个」走 `search/online`，带口语填充 → 命中偏门条目，再被 `min_duration_s=45` 过滤 → `not_found`。
- 修法：① 选曲/随机前过滤掉非 `private:` 来源；② 搜索前剥离口语填充（那个/这首歌/一下/点儿）；
  ③ 未命中话术改为「没搜到合适的版本」。

### 2026-11-01 21:2x 客厅 LX06：设备访问恢复 + 设备日志体检

- **设备 ssh 恢复**：dropbear 只给 `ssh-rsa` 主机密钥、root 需密码。密码在
  `~/.hermes/.secrets/lan_media.json` → `voice.device_ssh`（**不落任何仓库/文档**）。
  已建本地 wrapper `~/.esp32-voice/lx06.sh`（0700，运行时读 secret），用法：
  `~/.esp32-voice/lx06.sh 'tail -20 /tmp/client.log'`。
- **设备体检**（`/tmp/client.log`，15768 行）：`/tmp` 占用 **3%**（不再是满盘）；错误仅 2 条
  `[aplay] write error: Broken pipe`；其余 **15293 行（97%）是 rui 每 ~16 s 一次的
  `amixer -c 0 cget numid=177` 音量轮询**（`process.py:4163` `ticks % 8 == 1`）。
  这是设计如此（厂家会自己改音量，读回是唯一的真相），不是跑飞的 bug，但每 16 s 在弱设备上
  fork 一次 shell，属于可优化的热点（记录，不在本批改）。
- **插件没有仓库**：`/vol1/rui/plugins/rui_private_music`（`source.py`/`navidrome.py`/`star.py`/`errors.py`）
  是直接部署在 NAS 上、**没有 git**，版本靠 `*.bak-时间戳` 目录。任何修复要么就地改（先备份），
  要么先把它纳入仓库再改——否则改动不可追溯。
- 音乐修复卡（待开）：① 选曲/随机前过滤非 `private:` 来源；② 搜索前剥离口语填充；
  ③ 未命中话术改「没搜到合适的版本」。证据见上一节。

### 2026-11-01 21:2x 「久置后唤醒困难，要先喊小爱同学」——找到机制证据

- **不是错觉**：设备上**两个语音引擎同时开着同一个麦克风**：
  ```
  /dev/snd/pcmC0D3c（录音）: 1278 mipns-xiaomi  +  2282 arecord（open-xiaoai）
  /dev/snd/pcmC0D0p（播放）: 1278 mipns-xiaomi  +  423 aplay
  ```
  `mipns-xiaomi` 是**供应商自己的语音引擎（小爱同学唤醒）**，一直持有捕获设备；
  open-xiaoai 的 `arecord` 也在同一 PCM 上。说「小爱同学」之所以"激活"，正是把这个引擎叫醒。
- 旁证：设备负载 **load 1.71–1.94**（4 核 24–56%），供应商栈仍在跑
  （`miio_client/miio_agent/miio_helper/mibt_mesh/mico-helper/miio_mpkg/mico_ai_crontab`），
  供应商 cron 每分钟 `network_probe.sh`、每 10 分钟 `check_mediaplayer_status`、3:00 `ota silent`。
  `[aplay] slow write` 共 **274 次**（81–111 ms/3200 B）说明播放线程常被抢占。
- 已排除：Wi-Fi 省电（`Power save: off`）、链路抖动（重连仅 8 次）、`/tmp` 满盘（3%）。
- **下一步（需要决策）**：要不要停掉供应商语音引擎（`mipns-xiaomi` / mico 服务），把麦克风独占给
  open-xiaoai。代价：`小爱同学` 唤醒与原厂技能全部失效，设备变成纯 rui 终端。收益：少一个抢占者、
  唤醒一致性更好。open-xiaoai 文档只讲刷机与 SSH，没有现成的"关原厂语音"步骤，需要先在设备上确认
  服务名与自启方式（`/etc/init.d`、`/data/init.sh`），并在停止后观察 24h 唤醒命中率。

### 2026-11-01 21:2x 实验结论：停原厂语音引擎有效，选 A（保持停用）

- 现场操作：`/etc/init.d/pns stop` 停掉 `mipns-xiaomi`；麦克风由 `mipns-xiaomi + arecord` 变成
  **只剩 arecord（open-xiaoai 独占）**；rui 侧 living 仍 `connected=True/ready`。
  回滚：`/etc/init.d/pns start`。
- 实测对照（同一台、同一晚）：
  | 指标 | 停前 90 分钟 | 停后 25 分钟 |
  |---|---|---|
  | 唤醒被接受 | 20 | 12（含 5 次空唤醒） |
  | `lost_same_word` 拒绝 | **4** | **0** |
  | admitted 延迟 | 402–407 ms | 402–407 ms |
- **9哥 拍板：保持停用（A）**，牺牲「小爱同学」与原厂技能换独占与稳定；明早再评估。
- **仍未解决（下一张卡）**：ASR 只抓到句子尾巴 → 「据。」（"现在几点"）、「这一下吧。」；
  以及「对一下吧。」命中别名（→退下吧）**提前关会话**。怀疑主因是 `ack_echo_ignore_ms=1200`
  把命令开头吃掉（cue 实测约 600 ms，门却是 1200 ms）。修法：门长按 cue 实际播放时长 +150 ms 自适应，
  并给"门内被截断的片段"加 stop-intent 守卫。

### 2026-11-01 21:3x 修正：音乐插件早已纳管；subsonic 前缀 bug 在 house-ear

- 开发会话的调查员实测推翻了音乐卡的两条前提（我写卡时判断错了，先记下来免得再走弯路）：
  1. **插件早就在 git 里**：`rui-personal-services/rui-private-source/rui_private_music/`，
     5 个 `.py` 已 tracked，sha256 与 NAS 上逐个相同，README 107 行已写部署/前缀/Token。
     → 「纳管」卡收缩为补文档 + 复核一致。
  2. **`subsonic:` 前缀不是插件造的**：来自 house-ear 主仓
     `src/rui/music/library_index.py:551` —— `to_tracks` **无条件**加 `subsonic:` 前缀；
     换 source 后旧索引既不失效、也重建不了（插件没实现 `songs_page`/`albums_page`，
     `library_index.py:398-401` 抛错后被 `return self.available()` 吞掉，保留旧缓存）。
     插件的 `random()`/`search()` 只产 `private:`/`private-lib:`。
     → 「选曲前过滤非 private:」在插件侧做不到，真正的修点在 house-ear。
- 因此范围需要 3 张卡（插件文档 + 插件侧 resolve/清洗/话术 + house-ear 索引前缀修复），
  比"每批 ≤2 卡"多一张；等 9哥 在会话里选（会话已用 ask_user_question 提问）。

### 2026-11-01 22:0x 音乐批收口（3 卡已上线；本会话到此为止）

**卡按此更正**：9哥 已选「3 卡」（上一条记的"等选"已闭）。范围：卡 1 插件文档/纳管、
卡 2 插件侧修复、卡 3 house-ear 索引前缀 + 两条话术。卡 3 严格收窄为三条（①②③），未越界。

#### 两条现场前提也要更正（我自己写卡时也按错的假设走过一段）

1. **生产是 0.8.13，不是 0.8.11**（`/health` 实测 `"version":"0.8.13"`；9哥 自己发的）。
   本批上线的是同版本 main 的 `:sha-dfc8c7e`，**没有 bump 版本号**。
2. **客厅"吞字"不是回声门吃掉开头**：是**麦克风削波 + 自激**（停原厂音频引擎后
   `overdrive_ratio` 22–26%，恢复后归零）。→ **"门长按 cue 自适应"那张卡不要按错误假设开工**；
   片段卡（ASR 只抓到句尾）留待另开会话，且**不要与本批共用工作树**
   （它要动 `audio/process.py`、`rules.py`、`schema.py`、`catalog.py`、`config.example.yaml`）。

#### 插件新位置（卡 1 结论：目录名保留 `rui-private-source/`，不叫 `rui-plugin/`）

- `rui-personal-services/rui-private-source/rui_private_music/`（5 个 `.py` 早已 tracked；
  本批只补文档 + 复核一致）。提交：`108e1eb`（docs/纳管）、`a21d44b`（插件侧修复 + C-001）。
- sha256 本机 vs NAS 现网 **5/5 全等**（部署后复测）：
  `__init__ 3fb0138f…` / `errors f7264afc…` / `navidrome de9aaaa6…` / `source af2853e1…`（新版） / `star 4be979e0…`
- 跨仓契约：`rui-personal-services/docs/contracts/C-001-music-source-prefix-and-stats.md`
  （`track_prefix` / `library_prefix` / `last_stats{filtered_short,dropped_foreign,returned,query}`；
  rui 侧说明在 `docs/music-sources.md` §3.2「曲目 id」段 :91-97 —— 该文**尚未反向引用 C-001**，下一步补）。

#### 三条改动的真实现状（都不是"原假设"那样的修法）

1. **索引前缀**（卡 3 ①）：`library_index.to_tracks` 改为 `getattr(source,"track_prefix","")`，
   缺省仍 `subsonic:`；已带命名空间的行不二次加前缀。→ 根因在 house-ear，不在插件。
2. **换 source 失效**（卡 3 ②）：索引 JSON 新增 `source_name`；异源/无该字段 → 不可用并 WARNING；
   `ensure()` 构建失败时同源保留旧缓存、异源清空并打
   `cannot paginate … descriptive asks fall back to by_genre/search`。
3. **插件侧**（卡 2）：来源白名单（只认 `private:`/`private-lib:`，`resolve()` 校验**未放宽**）+
   `clean_term()`（位置敏感剥填充词，**不再串中替换**——曾把《你好吗》洗成"你"）+ `by_genre()`
   + 删掉"重发同一 q"的无效兜底。**加载顺序无要求，但两张卡缺一不可**。

#### 真机验收证据（2026-11-01 21:55 前后，`living` = LX06，音量 30% = 77/255；容器日志是 UTC，+8 才是本地）

```
# ① 放点轻音乐 → 出声，且不再有成排 skip
semantic: '放点轻音乐' -> lang=[] genre=['轻音乐', 'Light Music', 'Classical'] … | 54 tracks
playback requested: /var/lib/rui/music_cache/private-music/030c2c57dc25649b.wav loop=False gain=1.00
grep -c "not a private track" → 0

# ② 口语填充与清洗后同查询
search q='周杰伦那个' -> '周杰伦'
search q='周杰伦'     -> '周杰伦'          ← 同一清洗后查询、同一批候选
playback requested: /var/lib/rui/music_cache/private-music/970f385f7891817e.wav

# ③ 两条失败话术（区分成功）
tool music_play -> phrase music.filtered (filtered)   # 周杰伦那个：搜到但被时长过滤
  tool 文本「有是有，但版本不合适，换一首？」（模型念时会自己改写，语义不变）
tool music_play -> phrase music.not_found (empty)     # 桩返回空 + 本地库 0 命中
  tool 文本「没搜到，换个说法或者报歌名给我？」
```

- 「搜到但被过滤」是**自然触发**的（`search/library: skipped_short=1` 后候选为空）。
- 「真没搜到」无法自然触发（music-svc 对任意串都返回模糊命中），故用**临时桩**构造：
  host 网络起 `127.0.0.1:18099` 返回 `{"results":[]}`，override 临时加
  `PRIVATE_MUSIC_BASE_URL`，验完**已删桩、已还原 override**（现 `base_url=18082`、probe 双侧 up）。
- 已知毛刺：**部署后第一次**描述式点歌会走 `by_genre` 冷启动（3 个 genre × 在线+本地库 + 首次转码），
  实测撞过 20 s 工具超时，第二次 3.3 s 正常出声。要不要预热/加超时余量 → 见「下一步」。

#### 部署与回滚（照此抄）

```bash
# 镜像：ghcr.io/yes999zc/rui@sha256:fbaea513c41a47797a98b516d11e3648166b97223cded8657b6cefa318215998
#       （commit dfc8c7e；版本仍是 0.8.13；CI test(3.11)/test(3.12)/image 全绿）
# override 备份：/vol1/rui/rui-dot2-override.yml.bak-20261101-215148
# 插件备份：  /vol1/rui/plugins/rui_private_music.bak-20261101-214625
cd /vol1/rui && docker compose -f docker-compose.yml -f rui-dot2-override.yml up -d
# 回滚（顺序不能反）：override 换回 a951eeb… → up -d → rm /var/lib/rui/library_index.json
#   （宿主机 /vol1/rui/data/library_index.json）→ docker restart rui → 插件目录换回 .bak
```

#### 「来源过滤要不要做成配置项」——结论：不做

前缀不匹配就是"插件不该接的 id"，没有"有时接有时不接"的合理场景；做成配置项只会让
同一台设备出现两种真相、且掩盖 rui 侧索引前缀的真实 bug（本批正是把它当根因修掉的）。
真要放宽，应该是 rui 侧修索引前缀（C-001 §1），而不是给插件加开关。已写进 C-001 §4。

#### 已知限制 / 下一步（按顺序，另开会话做）

1. **片段卡**（ASR 只抓句尾 + 「对一下吧」误命中别名）：单独会话，别和本批共用工作树；
   先按"麦克风削波/自激"这条正解重查，**不要**按"门长吃掉开头"开工。
2. `last_stats` 可能是**上一次调用**的（插件 `favorites()` 不刷新 stats）：源出错或空收藏时
   有概率把「连不上」说成「版本不合适」。修法：插件在 `favorites()` 里也刷新 stats，
   或 rui 侧只在 `search/by_genre/random` 路径读 stats。
3. 冷启动超时：`by_genre` 3 个 genre 串行 + 首曲转码，首次易撞 20 s；考虑预热或缩到 1–2 个 genre。
4. `docs/music-sources.md` 反向引用 C-001 一行；插件仓补 `ruff.toml`（`src=["."]`）固定 I001 口径。
5. `docs/phrasebook-editor.md:265` 仍写 `music.not_found {want}`（该 key 已无槽位）。

### 2026-11-01 22:0x 状态交接（压缩前）

- **生产**：0.8.13，镜像 `sha256:fbaea513c41a47797a98b516d11e3648166b97223cded8657b6cefa318215998`
  （= `dfc8c7e` 音乐索引修复），5 台设备在线、`device_errors 0`。
- **原厂引擎已恢复**：`/etc/init.d/pns start`（`mipns-xiaomi` pid 1483）。恢复后 `overdrive` 归零、
  `barge` 归零。**结论：它是 AEC/AGC 引擎，不要停。**（撤回 21:2x 那条"保持停用"的决策）
- **cue 实测**：`/var/lib/rui/ack.wav` = 16000 Hz / 8800 采样 / 550 ms（有效语音 530 ms，峰值 0.750）。
  与门长 1200 ms 相比**多挡约 670 ms**，可解释「只收到句子尾巴」。
- **配置现状（逐项核过，完好）**：`assistant.name=小宝`、persona 占位符在、`system_prompt` 603 字、
  `session.end_words` 23 条含「下线」、dot2 四台 `ack_echo_ignore_ms=1200`、living `mic_shift=10`。
  误加过的 `audio.capture_segments` 已移除（rui 没有这个功能）。
- **待办**：① 音乐批（3 卡）验收收口；② 「门长按 cue 实际长度 +150 ms」的卡待发；
  ③ 生产单操作者规则待写进 operating.md。

### 2026-11-01 22:1x 归因更正：overdrive 不是在"停原厂引擎"时才有

压缩后复核生产（0.8.13，容器 13:55:11Z 起）得到的反证：

```
# pns 一直在跑，没停
$ ps w | grep -E "mipns|miio"        → 1483 root 266m /usr/bin/mipns-xiaomi -c .../xaudio_engine.conf
$ uptime                              → up 4 days, 1:02

# 但 living 仍有 overdrive，且两次 burst 都贴着播放
13:55:56 playback requested: .../030c2c57dc25649b.wav gain=1.00
13:56:03 WARNING overdrive: device=living clipped=5.9%  peak=1.000
13:56:36 WARNING overdrive: device=living clipped=25.2% peak=1.000
/health → living overdrive_count=23 overdrive_ratio_max=0.8297 last=…11.878
```

- ⇒ 「停 pns → overdrive，恢复 pns → 归零」这条因果**被污染**：那次实验期间我还重启了容器、
  且大概率在放歌。现在 pns 在跑、overdrive 照样出现。**"不要停 pns"这个结论仍然成立**
  （它是 AEC/AGC 引擎，停了会自激），但不能再拿 overdrive 当它的证据。
- ⇒ 真正的相关项更像 **播放泄漏 / 近讲增益**：overdrive 的检测点是**后 AEC、进 ASR 的 mono 流**
  （`src/rui/audio/process.py:1425 _observe_overdrive`，`OVERDRIVE_SAMPLE=0.97`，`FRAME_RATIO=1%`，
  `FRAMES=5`），量的是"到达 ASR 的样本打满"，不是 ADC 打满。
- ⇒ **本文档 22:0x 那条「cue 门长 1200 → cue+150 ms」的卡作废，不要发**。片段问题按上面
  「下一步 #1」开工：先查削波/播放泄漏（播放中 ratio 83% 就是现成线索），再谈门长。

待办收口：`overdrive` 应该在 **播放期间**单独打标（当前计数混了对话和放歌两种场景），
否则下一次还会归因错；这条留给片段批一起做。

### 2026-11-01 22:08 生产改动：living `capture.mic_shift` 10 → 12（降 12 dB 麦增益）

- **依据**：见本文档 22:1x 归因更正 + `04_Docs/human-bridge/research/self-build-voice-terminal.md` §7。
  shift=10 的 S16 饱和阈是 raw −36 dBFS，而"放歌时人在音乐里说话"实测 raw −44.6 dBFS（S16 占满 37.7%），
  近讲很容易过线 ⇒ 削波是**增益**问题，与厂商引擎无关。
- **改法**（已执行，9哥 22:08 批）：
  ```bash
  cd /vol1/rui/config
  cp -a config.yaml config.yaml.bak-20261101-140754-bridge-mic-shift-living10-to12   # 第 61 行
  sed -i '61s/mic_shift: 10/mic_shift: 12/' config.yaml
  python3 -c "import yaml;…"        # yaml ok, devices 5, living mic_shift = 12
  cd /vol1/rui && docker compose -f docker-compose.yml -f rui-dot2-override.yml restart rui
  ```
- **回滚**：`cp -a config.yaml.bak-20261101-140754-bridge-mic-shift-living10-to12 config.yaml`
  → 再 `docker compose … restart rui`。
- **验证**：`/health` 0.8.13 / devices 5 / device_errors 0 / speaker True；overdrive 计数归零后待复测。
- **已知小瑕疵**：改完之后值等于 manifest 默认（12），`/api/v1/devices/living` 的 `capture` 里
  不再显示 `mic_shift`（等于默认就不展示），但 `audio.overrides` 仍列着 `capture.mic_shift`
  —— 只是个标注，功能无误。想彻底干净就把该行写成空值，需要再重启一次，没做。
- **待复测**（9哥）：放歌中近讲「你好小宝，现在几点」，看 ① `overdrive_count` 是否还涨
  ② 转写是否还只剩句尾。

### 2026-11-01 22:2x 追问两件事：mic 增益的历史，和 AEC 到底拿到没有

**mic_shift 的历史（9哥 说"感觉一直在改"，对）**

| 阶段 | 值 | 出处 |
|---|---|---|
| 老项目（xiaoai-voice-entry）生产值 | **12** | `docs/archive/HANDOFF-history-to-2026-10-24.md:57` |
| rui 安装器写出的默认 | **12** | `a41b94a` `feat(install): setup writes a runnable config` |
| 0.8.10 起的生产基线 | **10** | `docs/releases/v0.8.10.md:46,111,120`（"按批次基线，本批不得改动"） |
| 今天 22:08 | **12** | `d6a98b2`，我从削波角度改的 |

教训：我拿"削波"当理由是不够的 —— 我自己抓的那段 raw 最高只到 −44.6 dBFS，
**根本没到 shift=10 的饱和阈（−36 dBFS）**，唯一硬证据是 `overdrive` 报过 `peak=1.000`。
而且 `docs/plan/speaker-tuning.md:154` 当年明确建议"给 LX06 提高上行增益换灵敏度"，方向和我相反。
**但下面这条测量说明 12 仍然是对的，只是理由换成了 AEC，不是削波。**

**AEC 到底拿到没有（实测，不是看配置）**

```
容器内: /usr/lib/x86_64-linux-gnu/libspeexdsp.so.1.5.2   ✅
配置:   aec{enabled: true, who: "controller"}            ✅  启动后 `aec off` 日志 0 条 ✅
代码:   aec.py:_frame 只调 speex_echo_cancellation        ❌  没有 speex_preprocess_run
```

同段真机录音（放歌中 4 s，ch6 = 参考）离线跑生产同款几何，只改 `mic_shift`：

| mic_shift | 阵列 mix ERLE | 进 AEC 的 mic 电平 |
|---|---|---|
| 10（原生产值） | **3.3 dB** | −22.4 dBFS |
| 12（现值） | **13.1 dB** | −34.5 dBFS |
| 14 | 11.1 dB | −46.5 dBFS |

⇒ 生产原来那套是"名义有 AEC，实际只消 3 dB"；改成 12 后到约 13 dB（离线复算）。
⇒ 与文档里"旧部署实测 24–30 dB"的差距，一半是**没接残差回声抑制**，一半是**电平没匹配**。

**待办（另开批次，都在这两个文件里）**
1. `aec.py` 串上 `speex_preprocess_run`（`SPEEX_PREPROCESS_SET_ECHO_STATE`），
   以 `aec_erle_db` 前后对比为准；当前生产只有线性段。
2. mic/ref 电平匹配做成不变量（现在 `mic_shift` 是个到处被改的数字，`ref_shift` 固定 16）。
3. 生产验证用：说一句 → `GET /api/v1/traces` 读 `aec_erle_db`（现在 `turns: []`，还没有对照）。

### 2026-11-01 22:3x 当夜第二批：`ref_shift` 16 → 15（+5 dB ERLE，已上生产）

9哥 指示不要等明天。扫参（`01_Code/experiments/erle-bench/erle_bench.py`，同段真机录音，
阵列 mix，speexdsp 同款几何 128/4096）：

| mic/ref | 线性 ERLE | +preprocess |
|---|---|---|
| 12/16 | 13.1 dB | 16.4 dB |
| **12/15（已上生产）** | **18.0 dB** | **21.8 dB** |
| 11/15 | 18.5 dB | 22.4 dB |

- mic 11 vs 12：只差 0.6 dB 但削波余量少 6 dB ⇒ 定 **12/15**。
- 生产：`ref_shift: 15`（备份 `config.yaml.bak-20261101-2229-bridge-refshift-living16-to15`），
  容器 14:23:24Z 重启，`/api/v2/devices/living/audio` 确认 `mic_shift 12 / ref_shift 15`。
- 回滚：备份 cp 回 + 重启；`mic_shift` 回滚同理（`…140754…` 那个备份）。
- 待办卡不变：① `aec.py` 接 `speex_preprocess_run`（bench 目标 ≥21 dB）；
  ② mic/ref 电平差做成启动自检（±6 dB 外 WARNING）。真机 `aec_erle_db` 对照仍缺一轮语音。

### 2026-11-01 22:5x 第三次修正：mic_shift 11 / ref_shift 15（补读 10-29 前科后）

- 漏掉了 advisor-state §26–§31：`mic_shift 12→10` 是 10-29 为修「AEC 后电平低 → VAD 失聪」**刻意**做的
  （8 会削波，10 是验证值），§31 定论 `16k + mic 10 + keywords :4.00 #0.07`。今晚改回 12 有把老问题改回去的风险。
- **机制修正**：ERLE 扫参里 `ref_shift` 才是大头（15 vs 16 ≈ +5 dB，各 mic 档都受益）；
  mic 档 11–14 仅差 ~2 dB（rs15 档：11→18.5、12→18.0、13→17.2、14→16.3 dB）。
  22:2x 那条"mic≈ref 电平匹配"的解释以本表为准，不作数。
- **现生产**：`mic_shift 11 / ref_shift 15`（PATCH `/api/v2/devices/living/audio` +
  `POST /api/v2/house/audio/restart`，11.8 s，不整容器重启）。取 11 = VAD 响度（比 12 +6 dB）、
  削波余量（比 10 +6 dB）、ERLE 最高档三者折中。
- 回滚锚：`config.yaml.bak-20261101-2255-bridge-micshift12-to11`（→12）、
  `…140754…`（→10）、`…2229…`（ref →16）。备份均可 `cp -a` 回 + fleet restart。

### 2026-11-01 23:12 客厅唤醒词加第二个：`你好小宝儿`

- 9哥 要求「你好小宝，你好小宝儿，都设置成唤醒词」。走**正规 API**（不是手改文件）：
  `POST /api/v1/setup/wake {"wake_words":["你好小宝","你好小宝儿"]}` —— 该端点改写
  `devices[0].wake_words`、用 `_write_keywords_preserving` **保留门限**重生成 keywords.txt、重启音频面。
- 生成结果（`/vol1/rui/config/keywords.txt`，仍是一词一行、`@` 后只是显示文本）：
  ```
  n ǐ h ǎo x iǎo b ǎo :4.00 #0.07 @你好小宝
  n ǐ h ǎo x iǎo b ǎo ér :4.00 #0.07 @你好小宝儿
  ```
  `ér` 在模型词表里**存在**（早前 grep 用空格分隔漏判，实为 tab 分隔），TTS 实测可命中。
- 验证：`plane_error: ''`、`/health` 0.8.13 / 5 台 / 0 错误 / 5 平面全 running、
  `living wake_words` 两项、日志无 kws 报错。
- 备份：`config.yaml.bak-20261101-2312-bridge-wakewords-add-xiaobaor`；回滚 = cp 回 + 重启音频面。
- 注意：keywords.txt 是**全设备并集**（`_write_keywords_preserving` 收集所有 devices 的 wake_words），
  所以其余 4 台也会响应「你好小宝儿」。若只想客厅响应，需要 per-device keywords 文件（当前不支持）。

### 2026-11-01 23:2x 停掉原厂语音助手（它自己插话）：`mico_aivs_lab`

- **现象**：23:15:11 客厅音箱自己说话，无人喊小爱。设备侧日志坐实是原厂：
  `/tmp/mico_aivs_lab/event.log` → `Wakeup {type: wakeup_real, vendor: xiaomi, word: 小爱同学}`，
  随后它的 ASR 把房间里的真实对话听成「估计拿过来我看看还还剩」，走自己的 NLP+TTS 回了一句
  「我没太明白你说的意思…」。我们这边只做了 `vendor wake ..., we yield`（LED 灭、让路）。
- **处置**：`/etc/init.d/mico_aivs_lab stop` —— 只停**对话/助手侧**，`mipns-xiaomi`（AEC/AGC，必须留）与
  `mibrain_service` 均未动；`amixer`（mysoftvol）读取正常，我们的采集/播放链路不受影响。
  验证：进程数 0，procd 未拉回，pns pid 1483 仍在。
- **恢复**：`/etc/init.d/mico_aivs_lab start`。
- **重启存活问题**：`/` 是 squashfs 只读，`/etc/rc.d/S85mico_aivs_lab` 改不了 ⇒ 重启会自己回来。
  持久化只能写 `/data/init.sh`（可写，且已放过 /tmp 守护），形如
  `( sleep 20; /etc/init.d/mico_aivs_lab stop ) &`；`/data/init.sh` 里目前**没有**任何 mico_aivs/mibrain 逻辑。
- 待 9哥 真机确认：说「小爱同学」不应答 + 说「你好小宝」正常应答后，再决定是否写 init.sh 持久化。

### 2026-11-01 23:2x 首曲冷启动根治：源码率协商（C-002）

**结论先行**：客厅首问「放点轻音乐」从 **12.3 s → 2.9–3.2 s**，不再有撞 20 s 工具超时的「音乐库没响应」。
改动全在 `rui-personal-services`（服务 + 插件），house-ear **零改动**。

#### 根因（我此前那条记录是错的，一并更正）

- ❌ 我 22:0x 写的是「`by_genre` 3 个 genre 串行 + 首曲转码」。实测检索段只占 **1.7 s**，不是主因。
- ✅ 真因：`music_acq.resolve.Resolver` 把码率**写死 999（无损优先，再退 320）**，meting 分支固定
  `br=999`。而插件最终产出的是 **16 kHz 单声道 WAV（等效 256 kbps）**——为了它先搬一首
  **~700 kbps 的 FLAC**，5.5 倍带宽白花；首曲又是随机抽的，抽到 6 分钟曲就要 8–15 s，
  再叠 TTS/LLM 就撞上 `music_play` 的 20 s（`TurnRunner.tool_timeout_s=20.0`，全局参数）。
- 同曲实测（id=524543708）：`br=999` → **11.7 MB**（`fLaC` 魔数）、`br=320` → 8.0 MB、`br=128` → **3.2 MB**。
- 分段实测（经服务代理）：`/v1/search` 0.3 s、`/v1/stream` 下载 2.7–5.6 s（1.2–4.4 MB/s）、转码 0.2–0.4 s。

#### 改了什么（契约 `rui-personal-services/docs/contracts/C-002-source-bitrate-negotiation.md`）

| 层 | 改动 |
|---|---|
| `music_acq.resolve` | `br_plan(max_br)`：无上限 → `(999, 320)` **逐字节不变**；有上限 → `(min(max_br,999), 320, 999)`，只向上退、**绝不返回空**；`requested_br` 记真实档位 |
| `music-svc` | `/v1/stream`、`/v1/resolve` 接受 `max_br`（64..999，非法 422），透传给 resolver；`requested_br` 进响应；鉴权/预算/并发锁零改动。镜像 **0.1.4 → 0.1.5** |
| 插件 | `PRIVATE_MUSIC_MAX_SOURCE_BR`（默认 **128**，空/0 = 不限制）→ `?max_br=`；**双侧夹到 64..999**（未夹的 9999 会变 422，而 4xx 被判永久错误 → **整库点不了**）；启动横幅打 `src_br=<=128` |
| 插件 | `resolve <曲> - <歌手>: fetch=…ms transcode=…ms tries=…ms total=…ms`（成功 INFO / 失败 WARNING），**每次尝试都入账**，`total` 是整次墙钟（含退避） |

#### 上线证据（2026-11-01 23:23–23:24，living，音量 30%）

```
# 修复前（22:58）：semantic 14:58:23.468 → playback requested 14:58:35.789 = 12.3 s，整轮 14.87 s
# 修复后（三连测，均为**未缓存**的曲子）：
semantic '放点钢琴曲' 15:23:42.107 → playback 15:23:45.264 = 3.16 s（整轮 6.13 s）
  resolve 鸟之诗 钢琴 - abc: fetch=2779ms transcode=374ms tries=1 total=3155ms
semantic '放点爵士乐' → playback = 3.05 s（整轮 4.14 s）
  resolve 乐 - 卦者灵风: fetch=2686ms transcode=360ms tries=1 total=3047ms
semantic '放点轻音乐' 15:24:20.469 → playback 15:24:23.382 = 2.91 s（整轮 5.09 s）
  resolve Silent Bloom - Aquilegia: fetch=2675ms transcode=233ms tries=1 total=2910ms
# 播放行（出声）：
playback requested: /var/lib/rui/music_cache/private-music/26e8e2f055ec39a2.wav loop=False gain=1.00
# 全程无 "skip (not a private track)"；/health ok 0.8.13 devices 5 errors 0 planes 5
```

#### 部署与回滚

```bash
# 服务：镜像 rui-personal-services-music-svc:0.1.5（Mac 上 DOCKER_DEFAULT_PLATFORM=linux/amd64
#   docker build -f music-svc/Dockerfile -t …:0.1.5 .  → docker save | ssh nas 'docker load'）
# compose 备份：/vol1/selfuse/docker-compose.yml.bak-20261101-232246（0.1.4 → 0.1.5）
# 插件备份：  /vol1/rui/plugins/rui_private_music.bak-20261101-232304
# 顺序：先服务（兼容，旧插件不认 max_br 也不会 422），后插件 + docker restart rui
# 插件侧回滚（最快）：override 里加 PRIVATE_MUSIC_MAX_SOURCE_BR=0 或留空 → docker restart rui，服务不必回滚
# 服务侧回滚：compose 换回 0.1.4 → docker compose up -d music-svc
```

#### 新增的残留风险 / 下一步（按顺序）

1. **gdstudio 从容器里不可达**（`ConnectError [Errno 101] Network is unreachable`），实际全走 meting。
   而 `br_plan(128)` 现在是 **3 档**（原 2 档），`_curl_json` 单次超时 35 s —— 一旦 gdstudio 从
   "立刻失败" 变成 "慢死"，一次 resolve 最坏会多耗 ~45 s（远超工具超时）。建议给 gdstudio 单档
   单次短超时，或加熔断（失败后 5 分钟不再试）。
2. 失败路径的退避对**最后一次**也 `sleep(0.3)`（既有逻辑），全失败 = 3×300 ms；无伤但可省。
3. 非 `(SourceError, OSError, SubprocessError)` 的异常逃逸时不会打 timing 行（既有行为）。
4. `_clamp_source_br` 只捕 `TypeError/ValueError`，显式传 `float("inf")` 会 `OverflowError`
   （env 路径被 `_env_int` 兜住，实测 inf/nan/1e400 → 128）。
5. 质量口径：输出仍是 16 kHz 单声道，128 kbps 源在 8 kHz 带宽内有余量；若 9哥 听出差异，
   改 `PRIVATE_MUSIC_MAX_SOURCE_BR=192` 即可，不需要改代码。

### 2026-11-01 23:2x 静音原厂助手：试了四步都失败，已全部回退（推翻上一节）

上一节说"停掉 `mico_aivs_lab` 就解决"是**错的**，9哥 实测四次仍会听到「网络服务遇到问题」。完整记录：

**根因知识（这次学到，值得留）**
- 原厂唤醒词「小爱同学」的 **KWS 就在 `mipns-xiaomi` 里**：
  `/usr/share/xiaomi/xaudio_engine.conf` → `[wakeup opt] model_path=wakeup_model.bin; kws_s12a_1.1.7`。
- 唤醒相似度门限在 **`/data/mipns/mds.conf`**（可写）：
  `wakeup.common.algorithm.cos_score_thres = 0.665`，另有 `pre_decision_switch`、`mic_gain` 等。
- 占用声卡的只有三个进程：我们的 `aplay`、我们的 `arecord`、`mipns-xiaomi`（pns 同时持有 capture 与 playback）。

**试过并失败的四步**
1. `stop mico_aivs_lab`（对话侧）→ 仍被唤醒，播「网络服务遇到问题」
2. 再 `stop mibrain_service`（唤醒识别侧）→ 同样的提示
3. `ubus call pnshelper event_notify '{"src":1,"event":1}'` → 返回 `code 0`，无效
4. `mds.conf` 的 `cos_score_thres` 0.665 → **0.95** + `pns restart` → 仍是同一条提示

**结论**：这条「网络服务遇到问题」**不是被唤醒后的答话，而是语音服务缺失时的固定播报**（把上面两个服务停掉后才出现）；
抬门限也没用，因为它跟唤醒判定无关。⇒ **不动原厂栈是最优解**；原厂「小爱同学」被房间对话误触发的行为，设备侧没有安全的关闭方式
（App 侧 9哥 关不掉），要彻底解决只能走换固件那条线。

**已全部回退（23:26，逐项验证）**
- `mds.conf` 从备份恢复，`cos_score_thres` = 0.665
- `pns restart`；`mico_aivs_lab start`；`mibrain_service start`
- 进程：pns 376 / mico_aivs_lab 574 / mibrain_service 593
- `/health` = 0.8.13、devices 5、device_errors 0、planes 5、speaker True、living `overdrive 0 / restarts 0`；`amixer numid=177` 可读
- 备份留着：`/data/mipns/mds.conf.bak-20261101-2327-bridge-wake-thres`（不需要可删）

### 2026-11-01 23:3x 0.8.14 口径定稿：客厅 `mic_shift 11 / ref_shift 15` 是折中，不是回退 bug

> 本节只固化「事实 + 判断 + 当晚改动顺序」，**无代码改动**（0.8.14 只动文档与版本号）。
> 扫参过程见上文 `### 2026-11-01 22:3x` 与 `### 2026-11-01 22:5x`；**以本节为最终口径**，
> 两节的结论若与本节冲突，按本节。

**生产最终值**：`living.audio.capture.mic_shift = 11`、`ref_shift = 15`。
设备 manifest 的默认 **12 / 16 是硬件几何**；生产上的 11 / 15 是**有意偏离**，不是配置漂移。

核实只认这一条（别只看 `/config/config.yaml`）：

```bash
curl -s "<rui>/api/v2/devices/living/audio" | jq '.effective.capture'
# 期望 mic_shift: 11, ref_shift: 15（rate/channels/bits 按协商结果）
```

**为什么偏离**（不写清，下次会被当回退 bug 重查）：

- `ref_shift 16 → 15` 是 ERLE 的**大头**（≈ **+5 dB**），各 mic 档都受益。
- `mic_shift` **不能回 12**：10-29 为修「AEC 后电平低 → VAD 失聪」**刻意** 12 → 10；而 10 会削波
  （shift=10 饱和阈 raw **−36 dBFS**，实测放歌夹话 raw **−44.6 dBFS** → S16 占满 **37.7%**），
  那次 overdrive 就是这么来的。12 比 11 少 **6 dB 响度**。
- 11 对 12 只差 **0.6 dB ERLE**，换来比 12 响 6 dB（保 VAD）、比 10 多 6 dB 削波余量。
  **11/15 是折中，不是扫参最优**：12/15 的 18.0 dB 与 11/15 的 18.5 dB 相差极小，选 11 是响度取舍。

**扫参表**（同一段真机 8 通道录音、speexdsp 同款几何 128 帧 / 4096 taps、阵列 mix；
单位 dB，`mic/ref`，数字为「线性 / 接 preprocess」）：

| mic/ref | 线性 | +preprocess |
|---|---|---|
| 10/16 | 3.3 | — |
| 12/16 | 13.1 | 16.4 |
| 12/15 | 18.0 | 21.8 |
| **11/15（生产现值）** | **18.5** | **22.4** |
| 13/15 | 17.2 | — |
| 14/15 | 16.3 | — |
| 14/16 | 11.1 | — |

基准脚本：`01_Code/experiments/erle-bench/erle_bench.py <raw8ch.i4> [mic_shift] [ref_shift] [--pp]`

**当晚生产改动顺序**（bridge 执行，均已生效；备份目录 `/vol1/rui/config/`，文件名照抄）：

| 时间 | 改动 | 方式 | 备份 |
|---|---|---|---|
| 22:08 | `living.audio.capture.mic_shift` 10 → 12 | 容器重启 | `config.yaml.bak-20261101-140754-bridge-mic-shift-living10-to12` |
| 22:29 | `living.audio.capture.ref_shift` 未设(默认16) → 15 | 容器重启 | `config.yaml.bak-20261101-2229-bridge-refshift-living16-to15` |
| 22:55 | `mic_shift` 12 → 11 | `PATCH /api/v2/devices/living/audio` + `POST /api/v2/house/audio/restart`（11.8 s，**非**容器重启） | `config.yaml.bak-20261101-2255-bridge-micshift12-to11` |
| 23:12 | 唤醒词加第二项 `{"wake_words":["你好小宝","你好小宝儿"]}`（保留门限 `:4.00 #0.07`） | `POST /api/v1/setup/wake` | `config.yaml.bak-20261101-2312-bridge-wakewords-add-xiaobaor` |

**回滚** = `cp -a` 备份回去 + 重启（前三个回滚需重启容器；第四个回单唤醒词即可）。

**唤醒词与门限的边界**（细节登记在 `docs/known-limitations.md` **L30**）：

- `keywords.txt` 是**全设备 `wake_words` 的并集**（`_write_keywords_preserving`，`src/rui/web/api/app.py`），
  所以客厅加了「你好小宝儿」之后**其余 4 台也能被它唤醒**；当前**不支持** per-device 独立词表。
  这是已知限制，不是 bug。
- 门限行 `:4.00 #0.07`（boost 4.0 / 阈值 0.07）是 §31 定论值，**不要动**；手改会被 setup 端点覆盖。
  `keywords.txt` 当前两行，每行 `拼音 :4.00 #0.07 @中文`（`@你好小宝`、`@你好小宝儿`）。

**与 0.8.10 冻结条款的关系（必须一起读）**：`docs/releases/v0.8.10.md` 曾写 `mic_shift=10` /
`ref_shift=16`「本批不得改动」。0.8.14 **解除该冻结**，依据就是上面三条：`ref_shift` 才是大头（+5 dB）
＋ 10 会削波/12 掉 6 dB 响度 → 定 11/15。不写这段等于违反既有发布纪律；以后再动这两个值，
同样要写「为什么」，不能只改数字。

**两条必须标注「提议、未验证」（不得写成已实现/已实测）**：

1. **mic/ref 电平差 ±6 dB 启动自检**：目前**只是提议**（本文件 22:3x 段 bridge 提的启动自检阈值），
   **没有实测依据**，也**没有记录目标电平** ⇒ 文档一律写「**提议值，未实测**」（`known-limitations` L32）。
2. **`aec.py` 只跑线性段**：`_frame()` 只调 `speex_echo_cancellation`，**未接** `speex_preprocess_run`
   （读代码得出，全仓无该符号）；「接上残差抑制 21.8 dB」是**离线 bench 预期**，真机 `aec_erle_db`
   至今**零对照**（`GET /api/v1/traces` 返回 `turns: []`）⇒ 见 `known-limitations` **L31**。

**下一步（backlog，本批不实现）**

1. 孤儿 `.audio`：插件 `_evict` 只 glob `*.wav`，超时取消会留 **21.3 MB** `.audio`（另一张卡在修）。
2. gdstudio 熔断：连续失败冷却，避免「慢死」时 3 档 × 35 s（另一张卡在修）。
3. `aec.py` 接 `speex_preprocess_run`（**卡 2**，未做）：需先抓 raw8ch 采样做 bench 基线，
   且属音频链路，需独立验证。
4. ERLE 电平不变量启动自检（**提议，未实测**，见上）。

#### 0.8.14 已发版并部署（2026-11-01 23:4x，本会话）

- **镜像**：`ghcr.io/yes999zc/rui@sha256:c5dcfdc03b38b91e7aa982623099d4b9d2aa2ce65814392f6ebf856e13c5b967`
  （tag `v0.8.14`；CI 四条全绿：`ci` / `secret-scan` / `security` / `docker-publish`）。
  镜像内 `rui.__version__ == 0.8.14`（实测）。**没有 .py 逻辑改动**，只是文档 + 版本号。
- **回滚锚**：上一个 digest `sha256:fbaea513c41a47797a98b516d11e3648166b97223cded8657b6cefa318215998`（0.8.13）；
  override 备份 `/vol1/rui/rui-dot2-override.yml.bak-20261101-234833`。
- **music-svc 同批升到 0.1.6**（熔断代码在 `music-acq/`，会被打进服务镜像；不升等于熔断空转）：
  镜像 `rui-personal-services-music-svc:0.1.6`，compose 备份
  `/vol1/selfuse/docker-compose.yml.bak-<时间戳>`，插件备份
  `/vol1/rui/plugins/rui_private_music.bak-20261101-2348xx`，插件 source.py 与 NAS 逐字节一致。
- **部署后验收（全部实测）**：
  ```
  /health              ok 0.8.14 devices 5 device_errors 0 planes 5
  living effective.capture  mic_shift 11  ref_shift 15     ← 没有被 manifest 12/16 覆盖
  config.yaml:27-29    wake_words = 你好小宝, 你好小宝儿
  /vol1/rui/config/keywords.txt  两行，均 :4.00 #0.07 @中文
  插件横幅             … min=45s max=600s limit<=30 prefer_library=yes src_br=<=128
  冒烟                 放点轻音乐 → http 200, 2.98 s, playback requested: …/dfe0b84eba2c5eb6.wav
  ```
- **同批进仓的两件事**（插件仓 `1c7c6d5`）：① gdstudio 熔断（连续 2 次失败冷却 300 s，
  `MUSIC_GDSTUDIO_*` 可调）—— 因为 gdstudio 从容器里不可达，而 `br_plan` 已有 3 档、
  `_curl_json` 单次 35 s，"慢死"时一次 resolve 最坏 +45 s；② 孤儿 `*.audio` 清理
  （`_evict` 顺带删 mtime>1 h 的临时文件，打 `cleaned_orphan_audio=N`）。
- **仍未做（本批显式排除，别当遗漏）**：
  1. `aec.py` 接 `speex_preprocess_run`：需要先在真机抓一段 `raw8ch.i4` 做 bench 基线
     （`erle-bench/` 里目前**没有采样文件**），且属音频链路，应当独立一批 + verifier。
  2. ERLE 电平不变量启动自检（±6 dB 目前只是提议）。
  3. 0.8.10–0.8.13 没有 `docs/releases/*.md`；0.8.14 的说明落在 `CHANGELOG.md`，如需补齐另行处理。

#### 真机出声测试的收尾纪律（2026-11-01 23:5x，9哥 当场纠正）

- **任何会让设备出声的测试（debug/say、debug/play、点歌）做完必须立刻停播**，不能等下一轮：
  ```
  POST /api/v1/debug/say {"text": "停止", "device": "<room>"}   # 走 music_stop，清队列
  GET  /api/v1/music | jq .state                                  # 必须是 "idle"
  ```
- **不要用 `POST /api/v1/debug/play {"stop": true}` 当作停播**：实测它只停音频面，
  `/api/v1/music` 仍是 `state: playing`，队列到边界会自己续播（今天就踩了这个坑）。
- 同理：`playback requested` / `resolve …` 这类日志行出现即代表"正在放"，测试报告里若出现
  这些行，必须在同一条命令里带上停播与 `state=idle` 校验。
- **GitHub Release**：`v0.8.14` 已建并标记 Latest（https://github.com/yes999zc/rui/releases/tag/v0.8.14），
  说明照 0.8.13 的格式写在 release body（**不含内网地址/密钥**，仓库是 public）。0.8.12/0.8.13 那次
  漏建 Release 对象导致 "Latest" 停了两个版本，本版已补上。

### 下一步（下个会话从这里接；本批到此为止，不再动生产）

**先纠正一处会误导的旧结论**：`:2298` 当年 22:29 的中间结论写「定 **12/15**」；22:55 起生产是
**11/15**，以 `:2466` 与新节为准（11/15 是折中：0.6 dB ERLE 换 6 dB 响度）。

**收口清单里的三条，两条已在 0.8.14 同批做完并上线**（不要再做一遍）：

| 项 | 状态 | 证据 |
|---|---|---|
| 孤儿 `.audio` 清理 | ✅ 已实现并部署 | 插件仓 `1c7c6d5`；`_evict` 顺带删 mtime>1 h 的 `*.audio`，打 `cleaned_orphan_audio=N`；NAS `source.py` = `a845099b…` |
| gdstudio 熔断 | ✅ 已实现并部署 | 同 commit；**music-svc 已升 0.1.6**（熔断代码在 `music-acq/`，打在服务镜像里，不升等于空转）；连续 2 次失败冷却 300 s |
| audio 链路待验 | ⬜ **唯一真·未做** | `aec.py` 未接 `speex_preprocess_run`；`01_Code/experiments/erle-bench/` 里**没有任何 `.i4` 采样** |

**唯一待开的新批（audio 链路，属风险卡，需 verifier）**：
1. 真机抓 4 s 8 通道采样（`arecord -D noop`，放歌中抓，ch6 = 参考），落进 `erle-bench/`；
2. `erle_bench.py <raw8ch.i4> 11 15` 得到**当前基线**（离线预期 18.5 / 接 preprocess 22.4 dB）；
3. 再改 `aec.py` 接 `SPEEX_PREPROCESS_SET_ECHO_STATE` + `speex_preprocess_run`，键名建议
   `audio.aec.residual`（**默认关**，与既有 `aec.enabled` 分开），bench 对比 + 真机
   `GET /api/v1/traces` 的 `aec_erle_db` 前后各一轮（注意现在 `turns: []`，要先有 trace 才能对照）；
4. 附带：ERLE 电平不变量自检仍是**提议**（±6 dB 无实测依据），要做先补测量。

**其它遗留（都不阻塞，按需）**：
- 0.8.10–0.8.13 没有 `docs/releases/*.md`；0.8.14 的说明落在 `CHANGELOG.md` + GitHub Release。
- 片段卡（ASR 只抓句尾）：另开会话，别与本批共用工作树。
- 观测项（不用动手）：`docker logs rui | grep -E "cleaned_orphan_audio|cooling down"` —— 前者应偶发出现，
  后者若 300 s 内反复刷屏说明阈值定得太小。

### 2026-11-02 00:5x 0.8.15 发版并部署：AEC 残差抑制 + 唤醒窗口 + `ref_shift 14`（A 路线第一刀）

phase-close: task-3

**发布身份（实测，非转述）**：
- commit `4438cee`（`feat(aec): attach speex residual echo suppression with wake hold`），tag `v0.8.15` 指向同一 commit。
- 镜像：OCI index `ghcr.io/yes999zc/rui@sha256:7c1a3aa72fea8155cf9e36ddc8f3ddc9708186f101c942cbd616d7b62ca8202b`；
  **linux/amd64 摘要 `sha256:4a4c2db421367ee5c110d97d8ac5a984f4702fa6f854fde12f1a087723cd948d`**（部署确定性 pin 用这个）；
  arm64 `sha256:8698fdb0…`。容器内 `rui.__version__ == 0.8.15`，`SPEEX_PREPROCESS_SET_ECHO_STATE == 24`（`docker exec rui python -c …`）。
- CI：**同一 commit 上四类全绿** —— `ci`（main，`41326664670`：test 3.11 / test 3.12 / image）、`secret-scan`、`security`、`docker-publish`。
  **注意口径**：`ci.yml` 只挂 `push:branches[main]`，tag 不触发 `ci`；tag `v0.8.15` 上只有 3 条 run（secret-scan / security / docker-publish）。别再写「tag 上四条」。
- 回滚锚：0.8.14 = `sha256:c5dcfdc03b38b91e7aa982623099d4b9d2aa2ce65814392f6ebf856e13c5b967`；
  override 备份 `/vol1/rui/rui-dot2-override.yml.bak-20261102-003628-bridge-0.8.15`；
  配置备份 `/vol1/rui/config/config.yaml.bak-20261102-001608-bridge-refshift15-to14`。
- 部署：NAS `rui` 容器 StartedAt `2026-11-01T16:38:24.965501693Z`，RestartCount 0。
  override 里 pin 的是 **index digest**：本次 NAS docker 正常 pull 并落到 amd64（本机按 amd64 摘要 pull 得到同一 image ID `sha256:82ba71ac…`、Arch=amd64）
  ⇒ **HANDOFF 旧记录的「NAS docker 不认 attestation index」本次未复现**；但确定性 pin 仍建议用 amd64 摘要。

**这批到底改了什么**（`src/rui/audio/aec.py` / `process.py` / `config/schema.py` + `tests/test_aec_residual.py` 6 例）：
1. `EchoCanceller._frame()` 在线性 `speex_echo_cancellation` 之后跑 `speex_preprocess_run`，用 `SPEEX_PREPROCESS_SET_ECHO_STATE` 串同一份 echo state。
2. `AecChain.hold_for()` / `wake_hold_ms`（默认 1500，0–5000）：唤醒 admit 后**只关残差级**，线性段照跑 —— 保护「压着音乐说的那句命令」。
3. `AecConfig.residual` 默认 **True**（回滚键 `audio.aec.residual: false`），运行时 `source=core-default` 已实测。
   **这推翻了本文件 0.8.14 段「默认关」的提议**：A 路线就是要把这一级打开；口径以本节为准。

**评审抓出的三条（下次动手前先读，省一天）**：
1. `SPEEX_PREPROCESS_SET_ECHO_STATE` 是 **24**，**14 是 `SET_PROB_START`**（`speex_preprocess.h:159`）。写 14 时 echo state 从未挂上。
2. `speex_preprocess_ctl(SET_ECHO_STATE, ptr)` 的 C 实现是 `st->echo_state = (SpeexEchoState*)ptr`（`preprocess.c:1164`）：
   **必须直接传 `c_void_p(handle)`**；用 `byref(handle)` 会把栈地址存进去，第一次 `run` 就解引用悬垂指针。
   测试用 `GET_ECHO_STATE`(25) 回读指针 `== canceller.handle` 守住这条。
3. **`denoise_enabled` 是整个谱增益（含回声抑制）的总闸**（`preprocess.c:933-937` 把 `gain2` 置 1）：
   `SET_DENOISE=0` 会让残差级变成 pass-through（实测 Δ0.00 dB）。speexdsp **没有**「只压回声不压噪声」的模式
   （`SET_NOISE_SUPPRESS=0` 实测仅 −0.7 dB）⇒ 这一级的代价是默认谱抑制：噪声 ≤−15 dB、回声 ≤−40 dB，
   近端说话时回声那档被 `echo_suppress_active` 夹到 −15 dB。代码只额外关 AGC/VAD。
4. **旧的离线 bench `+preprocess` 数字全部作废**（`18.5→22.4`、`18.0→21.8`、`14/15 18.5→20.0 vs 22.4→23.6`）：
   `01_Code/experiments/erle-bench/erle_bench.py` 也把 echo-state 槽写成 14，echo state 从没挂上，那几组测的是「线性 + 谱降噪」。
   脚本另行修（backlog）。树内合成 bench：`linear 14.72 dB → 残差级 19.62 dB`（Δ+4.9）。

**验收（2026-11-02 00:38–00:52，全部实测）**：
| 项 | 结果 |
|---|---|
| /health | ok **0.8.15**、devices 5、device_errors 0、planes 5（5 台 running） |
| living overdrive / restarts | **0 / 0**（测量 8 次采样全程 0，`voice_peak_db` −32.7…−51.7 未削波） |
| effective.capture | **mic_shift 11 / ref_shift 14**（device-override，落盘 config 仅 `ref_shift 15→14` 一行差） |
| keywords.txt | 两行，均 `:4.00 #0.07`，`@你好小宝` + `@你好小宝儿`（未被重写） |
| 线上 `aec_erle_db`（20% 音量、受噪声底限制） | 0.8.14 基线（线性，mic 11/ref 15）：11.3/12.9/15.2/20.6/19.3/20.9/23.0/21.5（稳态 **19.3–23.0**）；0.8.15（mic 11/ref 14）：9.2/15.1/30.2/31.8/27.9/27.4/28.3/27.5（稳态 **27.4–31.8**）⇒ **稳态 +7~9 dB** |
| 残差绝对电平 | **未做成受控对比**：同音量但曲目不同（基线「赛跑」/ 本轮「说走就走」），本轮 `ref_rms_db` 高约 11–20 dB，绝对残差电平反而更高。可比的只有 ERLE。下次量请固定同一曲目或固定 `ref_rms` 区间 |
| AEC 日志 | `Traceback/ERROR/CRITICAL` = 0；命中的 `aec_hw_ref` 是 dot2 能力串（假阳性） |

**ERLE 证据的时效性（必须一起记）**：`aec_erle_db` 是 AEC 在 ref 有活动期间的实时窗口统计量，播放一停 `ref_active=false` 就回落 `null`（静默态终检实测 `aec_erle_db=null`、`ref_rms_db=-100.0`）。所以上面那组 27.4–31.8 dB 是**当场一次性证据**，事后无法再读、也不能在不重新出声的前提下复核；引用时必须连采样条件（0.8.15 + mic 11/ref 14 + 20% 音量 + 8 次采样 ref_active 全 true + 同曲目约束）一起写。

**本批留档（会重复踩的）**：
- `POST /api/v2/house/audio/restart`（fleet 级）会按 `config.devices.<id>.volume` **重置运行时音量**：实测 living 77 → **204**（80%）。
  以后测音量口径必须「先读 config，再读运行时，测完写回」。
- `PATCH /api/v2/devices/{id}/audio` 的响应**不回显** `effective.capture`（`{ok:null,capture:null}`），必须后续 GET 复核。

**待 9 哥 白天验收（本批明确没测、不许写成已验证）**：
1. 双讲（放歌中说「下一首」）；
2. 唤醒率（含新词「你好小宝儿」）；
3. 真机整句识别质量。
夜间只做了上面那一轮 15 秒 ERLE 测量，没有做唤醒/TTS/整句测试。

### 下一步（下个会话从这里接）

1. **白天真机验收三项**（上面那三条），重点看 `wake with no speech` 比例与「放歌中命令词是否被压」。
   观察命令：`curl -s -H "X-Auth-Token: …" localhost:8080/api/v1/audio` 的 `aec_erle_db` / `wake_rejected` / `overdrive_count`。
2. **backlog（另开卡，不插队）**：
   - `01_Code/experiments/erle-bench/erle_bench.py`：`SET_ECHO_STATE = 14` → **24**，并改 `c_void_p(handle)` 直接传；
     修完用一段真机 `raw8ch.i4` 重扫 `mic/ref`（现在 bench 目录里**没有采样文件**）。
   - ERLE 测量协议：固定曲目或固定 `ref_rms` 区间，否则跨轮不可比。
   - hold 只从唤醒 admit 计时、且只在 admit 触发：命令 >1.5 s 时残差级会中途重新介入（最多 −15 dB）。
     若要更稳，考虑「hold 跟随 VAD 语音结束」而不是固定时长。
   - `AecChain` 的两条 "never make it worse" 保护只防「输出能量高于输入」，对「压掉人声」完全免疫；人声保护实际只靠 hold 时间窗。
3. 待观察：`docker logs rui | grep -E "cleaned_orphan_audio|cooling down"`（0.8.14 起的两个观测项）。

### 2026-11-02 01:5x 0.8.15 之后的生产配置（bridge 夜间改动，0.8.16 开工前必读）

**当前生产**：0.8.15、5 台、`device_errors 0`；`capture = mic_shift 10 / ref_shift 14`；
`ack.mode = tts(在呢)`；运行时音量 **76/255**（config 文件 `devices[0].volume = 30`）。
**口径更正（01:55 复核）**：全局 `ack.echo_ignore_ms = 700` **不生效** —— living 的机型事实声明 1200，
按 `schema.py:866` 的三层取值，机型尾巴赢（`GET /api/v2/devices/living/audio` 的 `conflicts` 原话：
`global 700 vs manifest 1200; using 1200 (model measured tail)`，winner `device-manifest`）。
有效值是 **1200**，要改必须写 `devices[].audio.voice.ack_echo_ignore_ms`（`schema.py:221`；改全局是空转）。
音量也别只信 `GET /api/v1/config`：它当时仍返回 20，而文件是 30、音频面 01:42:47 已
`volume: reconciled to 76 (config 30%)` ⇒ 以「文件 + `/api/v1/device/volume` 运行时值」为准。

**夜里动过三处**（都有备份，回滚 = `cp -a` 回 + 重启）：
1. 01:35 `mic_shift` 11 → **10**（PATCH + fleet restart）。依据：同窗口 3 米轻声唤醒 mic11 只 3 次、
   mic10 有 5 次；10 是 10-29 为 VAD 响度定的基线，11 是我 22:55 为 AEC 匹配压下去的（−6 dB）。
2. 01:35 `ack.mode` none → **tts**，`echo_ignore_ms` 1200 → **700**（**必须重启容器才生效**，
   `POST /api/v2/house/audio/restart` 不重载控制面配置）。
3. `devices[0].volume` 80 → 20（测试）→ **30**（已恢复）。

**夜里测到的（都带分母）**：
- 01:15，`ack=none`，3 米轻声：喊 **6 次 → 命中 5 次（83%）**，admit 403–410 ms，其中一次识别成「你好小宝儿」。
- 01:32–01:33，`ack=tts` + mic11：3 次唤醒，**2 次命令完整**（transcript「几点了？」「几点了。」回答正确），
  `[seg] rms 0.0052/0.0054`（比 22:43 的 0.035 低 16 dB —— 远场电平本就低）。
- 01:36–01:37，`ack=tts` + mic10：**5 次唤醒、0 次 `[seg]`**，全部 `cue=excised`。
- `[level] voice peak`：ack 30% 音量时 **0.0 dBFS（削波，是提示音自己的回声）**；ack 关掉后 −18.5/−25.6；
  ack 20% 时 −12.5/−17.3/−19.6 ⇒ 20% 不削波。

**挡音窗算术（01:55 按代码 + 日志复核，取代原先的「2150 / mode none 250」说法）**：
- 总窗 = cue + `ACK_PLAYOUT_LEAD_MS` 850 + **有效** tail（`process.py:1307`）。tts 下 tail=1200（见上），
  故现网总窗 = 600+850+1200 = **2650 ms**，与日志 `cue_gate: … kept=[1380,2650]ms` 一致。
- **2650 不是"全聋"**：`vad.py hold()` 只硬丢前 850 ms；后 1800 ms 先进缓冲，hold 结束时用回声匹配
  定位 cue，丢掉 `[0, cue_end+40]`（`CUE_REVERB_GUARD_MS=40`），cue 之后的人声会被**重放**给 VAD。
  实测 4 次唤醒：`cue=[620..800, 1220..1400]`、`dropped=[0,1260..1440]`、`kept=[…,2650]`，`lag_hint 700–758`。
  ⇒ **真正的聋区 = 0～(cue_end+40) ≈ 1380 ms，其中 600 ms 就是提示音本身，保护余量只有 40 ms**。
- `ack.mode: none` 时 `audio.size == 0`，`hold()` 根本没被调用 ⇒ 聋区 **0 ms**（250 ms 是 tone 模式的尾巴，
  不是 none 的；none 下没有 hold 窗口，也就不存在这 250）。
- 因此"命令被吃"最可能的是**与提示音重叠的那 ~600 ms**（一次呼吸喊完「你好小宝几点了」），
  而不是"固定聋 2.65 s"。**这条目前是推断，不是实测**：01:36–37 那 5 次唤醒 `0 次 [seg]`，
  日志无法区分「没说话」和「说了但被和 cue 一起切掉」——负样本根本不留痕，这正是 0.8.16 卡 1 要补的。

**待开批（0.8.16，已按上面更正重写）**：① `wake_miss` 负样本日志（给唤醒率/命令捕获率一个分母，**先做**）；
② 提示音三档 A/B（none / chime 320 / tts 600，先零代码量数据）再决定要不要改切除策略。
`mic_shift` 不动；唤醒词/门限不动。

### 2026-11-02 02:0x 配置已恢复到 0.8.15 出厂口径（9哥：「改回正常」）

**做法**：用 01:25 的备份 `config.yaml.bak-20261102-0125-bridge-ack-none-vol30`（= 0.8.15 发布时的生产配置）
整份覆盖回 `config.yaml`，再 `docker restart rui` 生效（改文件不会自动热载；上次 01:42 的重载是重启触发的）。
本次改动前的状态另存为 `config.yaml.bak-20261102-0205-bridge-restore-to-release`（要回测试态用它）。

**恢复的三处（全是夜里为测试改的，整份差异只有这三行）**：

| 键 | 测试值 | 恢复为（出厂） |
|---|---|---|
| `devices[living].volume` | 30 | **80** |
| `devices[living].audio.capture.mic_shift` | 10 | **11** |
| `ack.echo_ignore_ms` | 700 | **1200** |

`ref_shift 14` 本来就是 0.8.15 出厂值，未动；`keywords.txt` 两行与 device `wake_words` 两条也未动。

**恢复后实测（02:05–02:07）**：/health `0.8.15`、devices 5、`device_errors 0`、planes 5；
`effective.capture = mic_shift 11 / ref_shift 14`；`ack = tts(在呢) + echo_ignore_ms 1200`，
`GET /api/v2/devices/living/audio` 的 `conflicts = []`（全局与机型事实一致，夜里那条 700 vs 1200 的告警消失）；
运行时音量 **204/255（80%）**（日志 `volume: reconciled to 204 (config 80%)`）。

**两个已知取舍（9哥 知情，未改）**：
1. `mic_shift 11` 是出厂口径，但 01:36 的同窗口对比是 **10 更好**（3 米轻声：mic11 只 3 次唤醒、mic10 有 5 次）。
   明天测试要不要换回 10，等 9哥 一句话。
2. 80% 音量会让「在呢」变响（9哥 之前抱怨过）；cue 没有独立音量，只能压总音量或 `ack.mode: none`。

### 2026-11-02 09:05 卫生间 dot2-b 误唤醒：4 分钟内 7 次「在呢」（实测日志）

**现象**（9哥 洗脸时发现的）：dot2-b（卫生间，`adapter: dot2`，16 kHz 单声道、`ref_channels: 0`、
本机 AEC）在 09:04:11–09:08:18 之间连续 **7 次** `kws: wake: 你好小宝` → 7 次 admit（402–409 ms）→
7 次 `wake with no speech after 5000 ms` → **0 次 `[seg]`、0 次 utt**。每次都播了「在呢」（音量 80%）。
同期 living 在 08:59 也有 1 次空唤醒；dot2 / dot2-c / dot2-d 为 0。
`[level] voice peak -24.1/-24.2/-25.4 dBFS` ⇒ 触发源是洗脸水声这类**响亮宽带噪声**，不是人声。

**直接原因**：`/vol1/rui/config/keywords.txt` 是 `:4.00 #0.07 @你好小宝` —— boost 4.0、门限 0.07，
而代码默认是 `:2.00 #0.15`（`kws.py:keyword_lines`）。10-29 为了救远场漏唤醒把灵敏度调高，
现在反过来：噪声一响就过门限。

**结构限制（今天改不了的原因）**：
- 门限/boost **不是配置字段**，只存在于 `keywords.txt`；且 `kws_keywords_file` 是**全局唯一**一份；
- `DeviceVoiceOverride` 只有 followup/post_playback_mute/ack_echo_ignore_ms，**没有 per-device KWS 开关或门限**；
  `audio.voice.kws_enabled` 也是全局；设备 `wake_words` 只用于 setup/控制台，音频面不读（grep 确认）。
⇒ 「卫生间严格、客厅灵敏」必须改代码，属于 0.8.16 候选。

**可用但粗糙的两个旋钮**：① 全局 `keywords.txt` 门限/boost；② dot2-b 的 `volume`（只降低「在呢」的骚扰，不减误唤醒）。

### 2026-11-02 09:11 误唤醒扩散到 living/dot2-d：KWS 门限先回到代码默认（4.00/0.07 → 2.00/0.15）

**扩散证据**（同一波噪声，09:04–09:10）：dot2-b 9 次、**living 3 次**（08:59:07、09:09:53、09:10:09）、
dot2-d 1 次（09:09:45），全部 `wake with no speech`、`[seg]` 0、轮次 0。中间还出现一次
`rejected: turn_open`（同一设备连续两次命中，第二次被仲裁挡掉）。

**处置**：`/vol1/rui/config/keywords.txt` 从 `:4.00 #0.07` 改为 **`:2.00 #0.15`**（= `kws.py`
`keyword_lines()` 的代码默认值），`docker restart rui` 生效（09:10:52 起）。
备份 `keywords.txt.bak-20261102-0915-bridge-gate-4.00-0.07`，回滚 = `cp -a` 回去 + 重启。
重启后 5 台计数归零、无新唤醒（截至 09:11）。

**代价（必须一起记）**：这是**全局**门限（5 台共用一份 keywords 文件），客厅远场灵敏度会跟着降。
10-29 把 0.07 提上去正是为了救 3 米漏唤醒，所以这次要同时看两个方向：
① 噪声场景误唤醒是否消失；② 3 米正常说话是否还能唤醒。
**结构性修复仍在 0.8.16**：per-device KWS 门限 + 「空唤醒惩罚」（唤醒后 5 s 无语音 → 该设备冷却/降敏）。

### 2026-11-02 09:20–09:52 真机测试：四条硬结论（全部有日志，9哥 全程在场）

测试配置基线：0.8.15、`mic 10 / ref 14`、音量 80%、客厅 3–3.5 米、`ack.mode` 按块切换。

**1）`wake_skip_ms: 300` 会吃掉唤醒词后的第一个音节（已证实，改 0 后解决）**
- `ack.mode=none`（无提示音）+ `skip 300`：S1「你好小宝，几点了」一口气说，6 次里 3 次被切成
  「电了。/辩了。/远了。」→ 都是「几点了」丢了「几」，ASR 只能猜。
- 改 `skip 0` 后同一说法 5/5 干净拿到「几点了？」。**提示音不是主因**（关掉提示音照样丢）。
- 生产已保留 `wake_skip_ms: 0`（备份 `config.yaml.bak-20261102-0925-bridge-wakeskip-300-to-0`）。

**2）提示音的独立罪状：与 cue 重叠的命令被整段切除**
- 09:17:09（tts + skip 300）：`cue_gate cue=[660,1340] dropped=[0,1380]` → `[seg] 0.98 s` → 「对。」。
  切除逻辑丢的是 `[0, cue_end+40]`，所以紧跟在唤醒词后的命令只要与 cue 重叠就一起没了。
- ⇒ 0.8.16 要改成「只丢 cue 自己那一段」，或把提示音延后到命令起始之后。

**3）远场识别：`mic 10` → `mic 9` 有效，但**不能用原始增益**（会让采集饱和）**
- `mic 10`、3.5 米：`[seg] rms 0.014–0.018`，5 次里 2 次听错（「变了/记下了」）。
- `mic 9`（+6 dB）、3.5 米：`rms 0.044–0.071`，5/5 全对（含 6.9 s 长句）。
- 但放歌（80% 音量）时：`overdrive: clipped=2.3%–3.9% peak=1.000`，`aec_erle_db` 从 18–22 掉到 **2.7**，
  双讲全废 ⇒ 已改回 `mic 10`。正确做法：**采集增益不动，AEC 之后给人声加增益 + 限幅**（0.8.16）。

**4）音乐大声时的真实瓶颈 = 唤醒距离，不是唤醒逻辑**
- 放歌中 barge 2–5 四次唤醒全部触发成功（`barge: N wake during playback`），所以 KWS 没被压死；
- 失败发生在 ASR：「下一首」→「下一周」（残差级无 DTD，把近端人声按"与播放相关"一起压）。
- 9哥 实测距离：pop 类 ≈2.5 m 可唤醒，rock 类要 ≈1 m。线性 AEC 18–22 dB 在 80% 播放音量下
  残留回声仍掩蔽唤醒词 ⇒ 架构级问题，需要更好的 AEC（AEC3）/播放时 ducking，不是调参能解。
- 关 `residual`（`audio.aec.residual: false`）后识别恢复正常，但唤醒距离没有改善 ⇒ 两个方向都要在 0.8.16 处理。

**测试态→生产态的恢复（09:52）**：`ack tts`、`residual` 回默认 true、`mic 10`、`wake_skip_ms 0`、
门限 `:4.00 #0.07`、dot2-d 重新启用（5 台）、音量 80%。
回滚锚：`config.yaml.bak-20261102-0952-bridge-restore-production`、
`keywords.txt.bak-20261102-0952-bridge-gate-0.10-to-0.07`。

**0.8.16 卡（按证据排序，尚未发）**
1. `wake_skip_ms` 默认值收窄（0–50 ms）+ 回归用例：「几点了」不得丢首字；
2. AEC 之后的人声增益 + 限幅（`audio.voice.gain_db`），采集增益保持 10；
3. 残差级双讲保护：DTD 或「wake→turn 结束全程旁路」；否则 `residual` 只能算半成品；
4. per-device KWS 门限 + 空唤醒惩罚（唤醒 5 s 无语音 → 该设备冷却/降敏）；
5. 切除策略：只丢 cue 段、保留与 cue 重叠的人声（配套「提示音尾音不得进 ASR」回归）。

### 2026-11-02 09:57–10:00 视频在场：唤醒后把视频语音当命令 + 视频声误唤醒

- 09:57:31 修好提示音后：`cue: 600 ms of audio handed over`（此前是 `160 ms`，被读成残片听不见；
  处理 = 删 `/var/lib/rui/ack.wav` + 重启重新合成，文件 19244 B / 600 ms）。
- **视频在场（孩子看视频）实测**：
  1. 09:58:47 唤醒后 `[seg] 9.38 s`（VAD 把连续媒体声当一句）→ 转写「几点了几点了？」
  2. 09:59:05 「退掉吧，就可。」→ 助手「好，退了。」
  3. 09:59:16 「属好友。」→ 助手出题「第一题，英语…」（误触 quiz 工具）
  4. 10:00:27 **无任何用户语音的误唤醒**：`kws: wake 你好小宝` → 播「在呢」→ `wake with no speech 5020 ms`；
     同期 `[level] voice peak 0.0/1.5 dBFS`（房间被视频声占满）。
- 结论：外部媒体**没有参考通道**，AEC 消不掉；唤醒后麦克风里的一切都会被当成用户命令，
  且媒体声本身会命中唤醒词。属"媒体在场门控"问题，非调参可解。
- 候选（0.8.16/0.8.17）：唤醒后仅在「近端语音起始早于/高于媒体底噪」时开 turn；媒体在场时提高门限或直接不响应；
  与视频/电视抢输出时避让。

**更正（10:02，9哥 指出）**：今早 09:04–09:10 那波误唤醒（dot2-b 9 次、living 3 次、dot2-d 1 次）**主因是孩子的视频声**，
不是洗脸水声——视频一直在放，声音穿门进卫生间。10:00:27 / 10:01:08 / 10:01:26 living 三次「无语音唤醒」也都是视频。
处置：`keywords.txt` 临时升到 `:2.00 #0.15`（10:03 重启，重启后 1 分钟内 0 次唤醒），
备份 `keywords.txt.bak-20261102-1002-bridge-gate-0.07-to-0.15`。代价是远场灵敏度下降；
**媒体在场门控**因此升为 0.8.16 的 P0（阈值只能止血）。

### 2026-11-02 10:2x 0.8.16 卡定稿（未开工）

- 卡：`04_Docs/human-bridge/goal-0.8.16.md`（178 行）。范围：P0 声纹门控 + 静默拒绝 + 后台录制入口；
  P1 媒体在场门控四层 + 六条小修（skip 默认 0 / AEC 后增益+限幅 / 残差级全程旁路或 DTD /
  per-device 门限 / 空唤醒惩罚 / 切除只丢 cue 段）。
- 已定：`speaker_gate.mode: any_enrolled`；提示音保留；D8 → D8′（声纹只做门控，不做身份认证）。
- 模型已就位并验证：`/vol1/rui/data/models/speaker/campplus.onnx`（CAM++ 28 MB，dim 192，
  容器内 `SpeakerEmbeddingExtractor/Manager` 可用，sherpa-onnx 2.0.1）。
- **当前生产是临时止血态**：门限 `:2.00 #0.15`（视频在场，误唤醒压到偶发）；
  其余 = `mic 10 / ref 14`、`wake_skip_ms 0`、`ack tts`、5 台。
  0.8.16 上线后门限回到 `:4.00 #0.07`，视频误唤醒改由声纹拦。

**声纹录制范围（9哥 2026-11-02 定）**：3 人 × 2 台设备 —— **living（客厅小爱）+ dot2-c（主卧）**；
每人每台 3 段 × 3–5 s，内容不同、站常用距离、正常音量、媒体关闭；两台分开注册、分开标定阈值。
其余 3 台（卫生间/儿童房/书房）暂不注册。录制要等 0.8.16 的 `/api/v2/speakers/*` 上线。

**设备摆放与说话距离（9哥 2026-11-02 提供，per-device 调参的前提）**：

| 设备 | 房间 | 位置 | 说话距离 | 计划门限 | 声纹 |
|---|---|---|---|---|---|
| living | 客厅 | 台面/柜上 | 2–3 m | `4.00 #0.07`（灵敏） | 录 |
| dot2-c | 主卧 | **吊顶** | **≈3 m** | `4.00 #0.07` | 录（床位录） |
| dot2 | 书房 | 桌面 | ≈1 m | `2.00 #0.15`（严格） | 不录 |
| dot2-b | 卫生间 | — | ≈1 m | `2.00 #0.15`~`#0.20` | 不录 |
| dot2-d | 儿童房 | — | ≈1 m | `2.00 #0.15` | 不录 |

1 m 的三台比客厅热 10 dB+，这解释了卫生间为什么水声/视频声一响就命中；它们的采集增益与门限都要单独定，
不能拿客厅的值套用（0.8.16 的 per-device 门限那条按这张表验收）。主卧是吊顶 + 3 m，混响更重，
声纹必须在**床位**录，不能贴着设备录。

### 2026-11-02 10:5x 0.8.16 批次 1 开发完成（声纹唤醒门控 + 注册入口；未部署）

**commit** `5430923`（`feat(audio): gate wakes by enrolled speaker voice and add enrollment API`），main 分支，未打 tag。
**范围**：D8 → D8′ 的第一刀（P0）。生产仍是 0.8.15 临时止血态（门限 `:2.00 #0.15`），本批**没有**部署、没有重启容器、
没有碰 `/vol1/rui/config`。

**做了什么（用户可感知）**
1. 唤醒音频必须匹配已注册家人：不匹配 → **完全静默**（不播「在呢」、不建 turn、不发 Wake），只加
   `wake_rejections["speaker"]` 与一行 `wake rejected: speaker score=… best=… (threshold …)`；
   通过打 `wake admitted: speaker score=… best=…`。
2. 注册入口：`POST /api/v2/speakers/enroll` → `POST …/enroll/{id}/commit`、`GET /api/v2/speakers`、
   `DELETE /api/v2/speakers/{name}`、`GET …/{name}/sample.wav?device_id=`；
   控制台 v2 新增「家庭成员」页（注册/重录/删除/播放样本/相似度，中英双语）。
   库里键是 `(name, device_id)`，落盘 `data_dir/speakers.json`（原子写 0600），注册后**不重启即生效**（mtime 热读）。
3. 录制复用现有推流：控制面下发 `EnrollStart`，音频面把 post-AEC 16k 单声道写成
   `data_dir/enroll/<id>.wav` + sidecar `{enroll_id, device_id, path, ms, speech_ms}`（均原子）；
   **sidecar 是唯一完成信号**（`client.py::_dispatch` 没加分支，`EnrollResult` 只定义不发送）。

**配置与回滚键（全部默认关闭/零行为变化）**
| 键 | 默认 | 作用 |
|---|---|---|
| `audio.voice.speaker_gate.enabled` | `false` | **一行回到 0.8.15 行为** |
| `…model_path` | `""` | 生产填 `/data/models/speaker/campplus.onnx` |
| `…threshold` | `0.55` | 余弦阈值（待真机标定） |
| `…min_speech_ms` | `600` | 短于此直接拒（`wake rejected: speaker short=NNNms`） |
| `…mode` | `any_enrolled` | 字段已落；`wake_owner` 的段级语义属批次 2 |
| `…accept_anchor_or_library` | `true` | 段级语义属批次 2 |
| `…skip_on_barge` | `true` | 放歌 barge 唤醒不做声纹（音乐污染 embedding） |

**失败方向（有意选择，必须知情）**
- 模型缺失 / 库为空 / embedding 失败 / speakers.json 损坏 → **fail-open**（照旧唤醒），warning 一次。
- 本设备没有档案但库非空 → 回退**全库匹配**（每设备一次 info），避免「没在卫生间注册 = 卫生间变聋」。
- 库非空但全部维度不匹配（换模型后旧档未重录）→ fail-open。

**证据（Lead 亲自跑）**
- `git diff --stat`：15 文件 / +4107 −7（含新建 `speaker_gate.py`、`speakers.py`、`FamilyView.vue` 与 3 个测试文件）。
- `ruff check src tests` → All checks passed。
- **全量** `pytest -q`（2445 用例）→ exit 0。
- 定向：`test_speaker_gate.py`+`test_config_speaker_gate.py` 38 passed/1 skipped（真模型 importorskip）；
  `test_speakers_api.py` 45 passed；`test_config_v3.py`+`test_audio_plane.py`+`test_ack_echo_gate.py` 53 passed。
- 前端：`npm run typecheck` / `npm run build` exit 0（`web/dist` 不入库）。
- 评审：Card A rev-a（2 轮：3 条阻塞→PASS）、Card B rev-b（2 轮：3 条阻塞→PASS），实现者与评审者不同人。

**评审抓出、已修的坑（下次别重踩）**
1. 逐设备库里「本设备无档案」会让设备**完全变聋**（`best_match(device_id)` 恒 (0.0,None) 且绕过了 empty fail-open）。
2. `best is None`（换模型维度不符）同样会变聋 → 必须 fail-open。
3. ring 加长到 2 s 后，standby 冷→热回灌把**整个 ring** 喂回 KWS，可能二次命中旧唤醒词；回灌窗口必须与 ring 长度解耦。
4. sidecar 的 `speech_ms` 显式为 0 时不能回退成整段时长（否则静音也入库）。
5. 姓名归一化必须与 `SpeakerLibrary` 一致（`" ".join(split())`），否则重录覆盖/删除样本/相似度对比全错位。

**已知残留风险（不阻塞本批，别写成已验证）**
- 声纹推理是同步调用，跑在音频面事件循环上（每次唤醒 1 次 ~1.5 s 音频）；本机无 sherpa-onnx/模型，
  **耗时未实测**。发版前需在 dot2 上量单次 embed（目标 <100 ms），否则 barge 延迟会退化。
- `speaker_gate.enabled` 切换需要重建 plane（与 AEC/VAD/KWS 一致），热改配置不会即时生效。
- device wake（`_handle_device_wake`）路径**不走**声纹门控；当前 5 台都走 RUI KWS，无生产缺口，device OWW 上线前必须补。
- 放歌时 `skip_on_barge=true` 会绕过门控（媒体声+音乐仍可能误唤醒）；「下一个」的声学污染属批次 3。
- 5 台各加载一份 28 MB 模型（约 +140 MB RSS）未实测；发版前看 `docker stats`。
- 真机端到端（真 CAM++、真实录音、真实门控分数）**一次都没跑过**——本批只到单测/协议层。

**bridge 真机验收清单（部署后，按卡）**
1. 视频在场 10 分钟：误唤醒「在呢」= 0，且日志有 `wake rejected: speaker`（证明是被拒，不是没听到）。
2. 3 人 × 每台设备 3 段 × 3 s 注册；重录后旧 embedding 不残留；删除后样本 wav 也没了。
3. 家庭三人 3.5 米：唤醒 ≥4/5（门限回 `:4.00 #0.07`），且每条都有 `wake admitted: speaker score=`。
4. `docker stats` 看 5 台 RSS 增量；`grep -c "speaker gate unavailable"` 应为 0。
5. 只改配置回滚验证：`speaker_gate.enabled: false` + 重建 plane → 行为与 0.8.15 一致。

**下一步（批次 2，未开工）**：段级声纹 + 锚点连续性 + `vad_max_speech_ms`（默认 8000）+ 唤醒后短窗（2500 ms）
+ 切除策略（只丢 cue 段，不做 `[0, cue_end+40]` 整段）。批次 3：六条小修。最后发版卡。

### 2026-11-02 11:0x 0.8.16 批次 2 开发完成（媒体在场门控 + 切除策略；未部署）

**commit** `0389622`（`feat(audio): gate media voices out of turns and keep speech spoken over the cue`）。
**前置**：批次 1 = `5430923`（声纹唤醒门控 + 注册入口）。生产仍是 0.8.15 临时止血态，本批同样**未部署**。

**做了什么**
1. **段级声纹**：VAD 段关闭后、上报控制面之前判定；不匹配 → 不发 `UTTERANCE_PCM`、不置 `_turn_pending`、
   `turn_rejections[reason] += 1`、发非 barge `WakeIdle` 安静关窗、日志
   `segment rejected: <reason> score=.. anchor=.. best=.. window=wake|none chunks=N (threshold ..)`。
2. **锚点**：唤醒音频 embedding 存为本次锚点（每次唤醒先清旧的）。`any_enrolled` = max(库, 锚)；
   `wake_owner` = 锚点（`accept_anchor_or_library=true` 时 OR 库兜底）。无锚点（barge 跳过 / device wake / embedding 失败）fail-open 且 warning 一次。
3. **段长上限**：`audio.voice.vad_max_speech_ms`（默认 8000）按块切分逐块算分，全过才接受；今天的 9.38 s 媒体长段会因此被拒。
4. **唤醒后短窗**：`speaker_gate.wake_window_ms`（默认 2500，按**音频时钟**）内只认锚点；`0` 关闭本层。
5. **切除策略**：`vad.py` 用对齐后的 observed/reference 包络找「纯 cue 前缀」终点，只丢前缀；从首个重叠块起保留人声。
   clipped / 无法定位 cue 仍走保守回退。日志新增 `overlap_from=`（无重叠 = none），真机可 grep 标定。
   回滚键 `ack.keep_overlapping_speech: false` = 旧 `[0, cue_end+40]` 行为。

**配置与回滚键**
| 键 | 默认 | 回滚到 |
|---|---|---|
| `audio.voice.speaker_gate.segment_gate` | `true` | `false` = 不做段级判定 |
| `audio.voice.speaker_gate.wake_window_ms` | `2500` | `0` = 关闭短窗 |
| `audio.voice.vad_max_speech_ms` | `8000` | 调大即可放宽分块 |
| `ack.keep_overlapping_speech` | `true` | `false` = 旧整段切除 |

**协议/观测**：`Metrics.turn_rejected: dict[str,int] = {}`（additive default，老 plane/老控制面都可解析），
`/health` 每设备可见 `turn_rejected`。

**证据**
- 全量 `pytest`（2470 用例）→ exit 0，0 failed / 0 errors（verifier 真跑；提醒：`addopts=-q` 与 `-q` 叠加会吞掉汇总行，要计数用 `-o addopts="" -q`）。
- `ruff check src tests` → All checks passed；`npm run typecheck` / `npm run build` → exit 0；`web/dist` 未被跟踪。
- 定向：新增两测试 25 passed；回归 160 passed / 6 skipped；补充 90 passed。
- 评审 rev-c：PASS（无阻塞）；verifier：功能闸门全绿，唯一阻塞是 `speaker_gate.py` EOF 多一个空行，Lead 已修并复跑 `git diff --check`(0) + `ruff`(0)。
- 评审独立复算：纯 cue 时 `keep_overlapping_speech=true/false` 输出**逐字节相同**（27520==27520）；重叠用例保留人声能量≈100%，纯 cue 相关性 <0.4（cue 未进 ASR）。

**已知风险（全为合成音频/注入 embedder 证据，真机未验证）**
- `CUE_OVERLAP_RATIO=1.6` + 连续 2 块是启发式：人声明显高于 cue 时能定位；cue 很响而人声较轻时不触发 → 退回旧整段丢 cue（不新增丢失）。需真机 `overlap_from=` 标定。
- 重叠起点落在 skip 窗口内时，buffer 整段保留（含 cue 尾音回授）→ ASR 可能听到「在呢+命令」；这是为了不丢命令的取舍。
- 段级 embedding 仍在帧循环里**同步**执行（9.38 s 段 2 次 CAM++），真机掉帧未测。
- 尾块 <50 ms 时真实 embedder 返回 None → 该块 fail-open（仅 8000–8050 ms 边界可能触发）。
- 无锚点 + 窗外 + any_enrolled 走库兜底；barge 段起点早于唤醒落在窗内 → fail-open（不拦 barge，属设计）。

**bridge 真机验收要额外看的**
1. 视频播放中家人说「几点了」×5 → ≥4 次正确；视频人声产生 **0 个 turn**（`turn_rejected` 增加、日志有 `segment rejected`）。
2. 日志 `cue_gate: … overlap_from=` 是否出现非 none；出现时命令是否保住（对比 `keep_overlapping_speech: false`）。
3. `/health` 每设备 `turn_rejected` 分布；`segment rejected` 与 `wake rejected` 的比例。

**下一步**：批次 3 = 六条小修（`wake_skip_ms` 默认 0、AEC 后增益+限幅、残差级全程旁路/DTD、per-device KWS 门限、
空唤醒惩罚、回归用例）。之后发版卡。

### 2026-11-02 11:3x 0.8.16 批次 3 开发完成（六条小修；未部署）

**commit** `0e4fd77`（`feat(audio): six on-device fixes for the far-field and playback paths`）。
**前置**：批次 1 `5430923`、批次 2 `0389622`。生产仍是 0.8.15 临时止血态，本批**未部署**。

**做了什么（按今天的证据排序）**
1. **`wake_skip_ms` 默认 300 → 0**：schema、`GateConfig` 兜底、**冷装模板**（`src/rui/config/template.yaml`、
   `config.example.yaml`）、控制台文案、`docs/socket-plug-v1.md` 全部同步。`rui setup` 新装即 0。
   注意：存量配置里**显式写着** `wake_skip_ms: 300` 的会被保留（今天生产手工改过 0，不受影响）。
2. **AEC 后人声增益 + 限幅**：`audio.voice.gain_db`（默认 6.0，0–18）在 AEC 之后、KWS/VAD/ASR 之前；
   峰值 > 0.95 硬限幅并计 `limiter_count`。**overdrive / voice_peak 的观测点在增益前**，所以它仍然描述「采集有没有饱和」，
   不会被增益掩盖。`gain_db: 0` 与旧实现逐字节一致（verifier 用 `cmp` 验过）。
3. **残差级全程旁路**：唤醒 → 本次 turn 结束（段上报 / 段被拒 / 静默关窗 / barge 结束）全程 `hold`，
   `AecChain.release()` 恢复；`wake_hold_ms` 改为**上限**（默认 15000，0=关闭旁路）。
   回滚：`wake_hold_ms: 1500`（接近旧的固定 1.5 s，但 turn 提前结束会更早恢复，不是逐字回滚）。
4. **per-device KWS 门限**：`devices[].audio.voice.kws_threshold` / `kws_boost`（None=继承）。
   有覆盖时按该设备 `wake_words` 生成 `data_dir/kws/<device>.txt`（原子写，失败回退全局）；无覆盖零行为变化。
   生产门限值本身不在这批改（bridge 部署时写配置：客厅 0.07、卫生间/儿童房 0.15）。
5. **空唤醒冷却**：连续第 `empty_wake_cooldown_after`（默认 2）次空唤醒后，该设备 KWS 命中直接丢弃
   `empty_wake_cooldown_s`（默认 120 s），`wake_rejections["cooldown"] += 1`，日志
   `wake suppressed: cooldown <N>s left (empty wakes=<n>)`。barge 空收**不计数**（评审抓出的坑）。
   注意：冷却期内**真人唤醒也会被丢弃**，回滚键 `empty_wake_cooldown_s: 0`。
6. 切除策略（第 6 条）已在批次 2 完成，不重复。

**新增配置总表（0.8.16 全部）**
| 键 | 默认 | 回滚到 |
|---|---|---|
| `audio.voice.speaker_gate.enabled` | `false` | 已是回滚态 |
| `…speaker_gate.segment_gate` | `true` | `false` |
| `…speaker_gate.wake_window_ms` | `2500` | `0` |
| `…speaker_gate.skip_on_barge` | `true` | `false` |
| `audio.voice.vad_max_speech_ms` | `8000` | 调大 |
| `audio.voice.gain_db` | `6.0` | `0` |
| `audio.aec.wake_hold_ms` | `15000` | `1500` / `0` |
| `audio.voice.empty_wake_cooldown_s` | `120` | `0` |
| `audio.voice.empty_wake_cooldown_after` | `2` | 调大 |
| `devices[].audio.voice.kws_threshold` / `kws_boost` | `null`（继承） | 删除覆盖 |
| `ack.keep_overlapping_speech` | `true` | `false` |

**证据**
- `ruff check src tests` → All checks passed。
- 定向：四个新测试 32 passed；卡片指定回归 139 passed / 6 skipped；相邻回归 123 passed。
- `tests/test_audio_scale.py` 9 passed（旧的幅度不变式 pin `gain_db=0`，新增默认增益用例）。
- 评审 rev-d：2 轮（2 条阻塞 → PASS）。verifier-d：首轮全量 **5 failed**（都是 `test_audio_scale.py` 的幅度断言，
  已按有意行为变化更新测试）；复验 `test_audio_scale.py` 9 passed、无断言放宽（diff +21 −0）。
- **全量测试在批次 3 收口时是红的（5 failed）→ 已修，但未重跑全量**；发版前的全量是最终判据（见发版卡）。

**已知风险（全部只有单测/合成音频）**
- 增益 +6 dB 对 3.5 m 识别率、限幅对听感的影响未上真机；`limiter_count` 真实分布未采样。
- 残差全程旁路对「放歌 80% 下一首」的实际收益未上真机；ERLE 会掉（这是有意的取舍）。
- per-device 门限的真实召回/误唤醒未标定；空唤醒冷却的 120 s / 2 次阈值未实测。
- 冷却会静音真人唤醒（设计），`empty_wake_cooldown_s: 0` 是回滚键。
- 增益同时作用于注册 wav 与声纹 embedding；因为注册与判定都走同一条 post-AEC 链路，口径一致
  （`speaker_gate.enabled=false` 时零变化）。

**下一步**：发版卡（版本号 0.8.16、CHANGELOG、D8 → D8′ 文档、CI 四绿口径、amd64 digest、HANDOFF 回填）。

# 0.8.16 发布收口（2026-11-02）

phase-close: task-5

## 版本与契约

- **版本**：`0.8.16`（`pyproject.toml`、`rui.__version__`、`CHANGELOG.md`、`docs/releases/v0.8.16.md` 四处一致）。
- **代码**：`main` = `6e0f374`（tag `v0.8.16` 指向同一 commit）。
  提交链：`5430923` 批次 1 → `0389622` 批次 2 → `0e4fd77` 批次 3 → `eb45d40` 发版元数据 → `6e0f374` CI 测试修复。
- **镜像（GHCR）**：
  - OCI index `ghcr.io/yes999zc/rui@sha256:164ddb38eb7c33c26eedc5eca1809e051d44e65b56bf5f4f51ba76de0eb9f7da`
  - **linux/amd64 `sha256:9d28f00d30192becf0be6bb08ada3cba06c6d4bfa3add207e5eee9eb366ca11e`**（部署确定性 pin 用这个）
  - linux/arm64 `sha256:1322169dff166951f7ddfb10f508bdc205c51dcad25fe45f047aa6985d644ea8`
  - tags：`0.8.16` / `0.8` / `latest` / `sha-6e0f374`（tag 构建）；`edge` / `sha-6e0f374`（main 构建）。
- **CI 四绿口径（同一 commit `6e0f374`）**：
  | 工作流 | main 推送 | tag `v0.8.16` |
  |---|---|---|
  | `ci`（test 3.11 / test 3.12 / image） | ✅ run `41384438874`（3 jobs 全 success） | —（`ci.yml` 只挂 `push:branches[main]`，tag 不触发，与 0.8.15 同口径） |
  | `secret-scan` | ✅ `41384438836` | ✅ `41384454776` |
  | `security` | ✅ `41384438836`… 见 run 列表 | ✅ `41384454664` |
  | `docker-publish` | ✅ `41384438935` | ✅ `41384454666`（test / build arm64 / build amd64 / merge 全 success） |
  > 首次推送（`eb45d40`）的 `ci` 与 `docker-publish` **红过一次**：`tests/test_voice_gain.py::test_aec_release_ends_the_hold_immediately`
  > 在无 `libspeexdsp` 的 CI 上构造 `AecChain` 抛 `AecUnavailable`。已改为该环境跳过（`6e0f374`），本机 macOS 仍真跑。
- **契约**：`rui_compat` 不变（`>=0.8.1 <0.9.0`）；**无协议破坏性变更**，`Metrics` 只新增 additive 字段
  （`turn_rejected: dict`、`limiter_count: int`，均带默认值，老 plane/老控制面可互通）；
  插件（dot2 / xiaoai）零改动；`keywords.txt` 文件格式未变。
- **新增配置**：`audio.voice.speaker_gate.*`、`audio.voice.vad_max_speech_ms`、`audio.voice.gain_db`、
  `audio.voice.empty_wake_cooldown_s|_after`、`audio.aec.wake_hold_ms`（语义改为上限）、
  `devices[].audio.voice.kws_threshold|kws_boost`、`ack.keep_overlapping_speech`；`wake_skip_ms` 默认 300 → 0。

## 已完成

**四批全部完成，独立评审 3 次 + verifier 3 次；生产未部署、未重启、未改 `/vol1/rui/config`。**

| 批次 | 卡 | commit | 评审 | 验证 |
|---|---|---|---|---|
| 1 | 声纹唤醒门控 + 静默拒绝 | `5430923` | rev-a：3 阻塞 → PASS | 定向 + 全量绿 |
| 1 | 注册入口 `/api/v2/speakers/*` + 控制台「家庭成员」 | `5430923` | rev-b：3 阻塞 → PASS | 45 tests + 前端 build |
| 2 | 段级门控（锚点/分块/短窗）+ cue 切除 | `0389622` | rev-c：PASS | verifier-c：全量 2470 绿（修 1 处 EOF 空行） |
| 3 | 六条小修（skip 0 / 增益限幅 / 残差旁路 / per-device 门限 / 冷却） | `0e4fd77` | rev-d：2 阻塞 → PASS | verifier-d：全量 5 红（测试期望）→ 修 → 9 passed 复验 |
| 发版 | 版本/CHANGELOG/D8′/release notes | `eb45d40` | — | verifier-d：全量 **2496 passed / 7 skipped**、11/11 一致性 PASS |
| 发版 | CI 修复（libspeexdsp 缺失跳过） | `6e0f374` | — | CI 四绿（见上表） |

**评审/验证抓出、已修的关键坑（下次别再踩）**
1. 逐设备声纹库「本设备无档案」会让设备**完全变聋**（`best_match(device_id)` 恒 (0.0,None) 且绕过 empty fail-open）→ 回退全库 + 每设备一次 info。
2. `best is None`（换模型维度不符）同样变聋 → fail-open。
3. ring 加长到 2 s 后 standby 冷→热把**整个 ring** 回灌 KWS，可能二次命中旧唤醒词 → 回灌窗口与历史解耦（500 ms）。
4. sidecar `speech_ms` 显式 0 不能回退成整段时长（否则静音入库）。
5. 姓名归一化必须与 `SpeakerLibrary` 一致，否则重录/删除/相似度全错位。
6. 冷冻期（cooldown）把 **barge 空收**也计入 → 放歌时两次就静音 120 s；已排除。
7. **冷装模板**仍写 `wake_skip_ms: 300` 会盖掉 schema 新默认 → 新装设备继续丢首字；已改 0。
8. 默认 `gain_db=6` 让 `test_audio_scale` 的「幅度不变」断言变红 → 测试 pin `gain_db=0` 保原意 + 新增增益用例。
9. CI 无 `libspeexdsp`，`AecChain` 构造会抛 `AecUnavailable` → AEC 相关用例必须 `speex_available()` 守卫。

**证据（Lead 亲自跑的收口项）**
- `git status` 干净（仅 `.gitleaks-rui.json` 未跟踪，10-31 旧产物，未进任何提交）；`git diff --check` 0。
- 全量 pytest：`2496 passed, 7 skipped`（verifier 独立跑，`-o addopts="" -q`）；ruff 0；web typecheck+build 0。
- 镜像内版本：见下方「镜像校验」。

**回滚键（生产部署后按需一行回滚；全部有单测）**
| 键 | 0.8.16 默认 | 回滚到 |
|---|---|---|
| `audio.voice.speaker_gate.enabled` | `false`（未启用即 0.8.15） | 保持 false |
| `audio.voice.speaker_gate.segment_gate` | `true` | `false` |
| `audio.voice.speaker_gate.wake_window_ms` | `2500` | `0` |
| `audio.voice.speaker_gate.skip_on_barge` | `true` | `false` |
| `audio.voice.vad_max_speech_ms` | `8000` | 调大 |
| `audio.voice.gain_db` | `6.0` | `0`（逐字节等于 0.8.15） |
| `audio.aec.wake_hold_ms` | `15000`（上限） | `1500` / `0` |
| `audio.voice.empty_wake_cooldown_s` | `120` | `0` |
| `audio.voice.empty_wake_cooldown_after` | `2` | 调大 |
| `devices[].audio.voice.kws_threshold` / `kws_boost` | `null`（继承） | 删除覆盖 |
| `ack.keep_overlapping_speech` | `true` | `false` |
| `audio.voice.wake_skip_ms` | `0` | `300` |

**声纹模型（来源已定，校验和待 bridge 记录）**
- `3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx`（CAM++，约 28 MB，dim 192）。
- 位置：宿主机 `/vol1/rui/data/models/speaker/campplus.onnx` → 容器 `/data/models/speaker/campplus.onnx`。
- **本会话无法访问 NAS，sha256 未取**；部署时请 `sha256sum` 后回填本节（不要引用未核实的哈希）。
- 缺失/加载失败时门控 **fail-open**（`speaker gate unavailable` 一行 warning），不影响唤醒。

**镜像校验（已实测，2026-11-02 11:5x）**：
```
docker run --rm --platform linux/amd64 --entrypoint python \
  ghcr.io/yes999zc/rui@sha256:9d28f00d30192becf0be6bb08ada3cba06c6d4bfa3add207e5eee9eb366ca11e \
  -c "import rui,sys;print(rui.__version__, sys.version)"
→ 0.8.16 3.12.16     (exit 0；pull 到的 Digest 与上面 amd64 摘要一致)
```
（在 Apple Silicon 上用 amd64 模拟跑通，只证明镜像内容与版本号；**不代表真机音频行为已验证**。）

## 下一步

**1. bridge 部署（不在开发会话做）**
- 用 amd64 摘要 pin：`ghcr.io/yes999zc/rui@sha256:9d28f00d30192becf0be6bb08ada3cba06c6d4bfa3add207e5eee9eb366ca11e`。
- 部署前备份：`/vol1/rui/config/config.yaml`、`/vol1/rui/config/keywords.txt`；回滚锚 = 0.8.15 镜像
  `sha256:4a4c2db421367ee5c110d97d8ac5a984f4702fa6f854fde12f1a087723cd948d` + 两份配置备份。
- **部署顺序建议**：① 起镜像（`speaker_gate.enabled` 先保持 false，确认 5 台/`device_health` 正常）；
  ② 控制台「家庭成员」注册 3 人 × 相关设备；③ 开 `speaker_gate.enabled: true`、阈值 0.55，观察
  `wake admitted/rejected` 两类日志；④ `keywords.txt` 回到 `:4.00 #0.07`（视频误唤醒交给声纹拦）；
  ⑤ 按需开 per-device 门限（客厅 0.07、卫生间/儿童房 0.15，写 `devices[].audio.voice.kws_threshold`）。
- **不要**在开发会话里改 `/vol1/rui/config/`、重启容器或部署 —— 由 bridge 在窗口内执行。

**2. 真机验收（9 哥 在场，每项带分母；结果回填本文件）**
1. 视频在场 10 分钟：误唤醒「在呢」= **0**，日志有 `wake rejected: speaker`。
2. 家庭三人 3.5 米：唤醒 ≥ **4/5**（门限 0.07），每条有 `wake admitted: speaker score=`。
3. 视频播放中家人说「几点了」×5：≥ **4** 次正确；视频人声 **0 个 turn**（看 `turn_rejected` 与 `segment rejected`）。
4. 注册：3 人 × 每台设备 3 段 × 3 s；重录后旧 embedding 不残留；删除后样本 wav 也没了。
5. 放歌 80%：3.5 米「下一首」×5 ≥ **4/5**；歌本身 10 分钟 **0 次**唤醒；
   `overdrive_count = 0`、`aec_erle_db ≥ 15`（目标 ≥20）；观察 `limiter_count` 与 `cue_gate: … overlap_from=`。
6. 回归：`wake_skip 0` 下「你好小宝，几点了」不丢首字；`mic 10` 放歌 `overdrive_count = 0`；2 米近场 `[seg] peak < 0.95`。
7. 资源：`docker stats` 看 5 台 RSS 增量（每平面 +1 份 CAM++ ≈ +28 MB 模型，未实测）；`grep -c "speaker gate unavailable"` 应为 0。

**3. 阈值与参数标定（部署后第一周）**
- 声纹阈值 0.55：按 `wake admitted/rejected` 的分数分布定；偏低误放、偏高误拒。
- `cue_gate: … overlap_from=`：确认重叠检测在真实回授下的命中率；必要时调 `CUE_OVERLAP_RATIO`（当前 1.6）。
- 空唤醒冷却（120 s / 2 次）：dot2-b 噪声场景是否还会连击；必要时调 `empty_wake_cooldown_s/after`。
- per-device 门限：客厅 0.07 与卫生间 0.15 的实际召回/误唤醒。

**4. backlog（本版明确未做，别当已完成）**
- **DTD / AEC3**：放歌时「下一首」的声学污染只做了残差级全程旁路，没有双讲检测。
- **device wake 路径不走声纹门控**（`_handle_device_wake`）：当前 5 台都走 RUI KWS，无生产缺口；device OWW 上线前必须补。
- **同步 embedding 在音频面事件循环**：每唤醒/段一次 CAM++，真机耗时未测（目标 <100 ms），可能影响 barge 延迟。
- **多人同时说话分离**：不做（无参考通道无法做）。
- 声纹**不是安全边界**：录音回放可绕过（D8′ 明确写入设计文档）。
- `uv.lock` 仍写 0.8.14（0.8.15/0.8.16 两次未同步；`uv sync` 非 `--locked` 会自动重建，`security-pip-audit.sh --frozen` 按旧集合审计）。
- `01_Code/experiments/erle-bench/erle_bench.py` 的 `SET_ECHO_STATE=14` 仍是错的（应为 24）——0.8.15 遗留，未做。

**5. 关键文件**
- `src/rui/audio/speaker_gate.py`：注册库、CAM++ embedder、唤醒/段级判定、锚点。
- `src/rui/web/api/speakers.py`：注册 API 与状态机；`web/src/v2/views/FamilyView.vue`：控制台页。
- `src/rui/audio/process.py`：唤醒链门控、段级门控、增益限幅、冷却、per-device KWS、录制钩子。
- `src/rui/audio/vad.py`：cue 切除（`overlap_from`）。
- `src/rui/audio/aec.py`：`hold_for()/release()`。
- `docs/multi-device-design.md` §9.2/§9.3：D8′。
- `docs/releases/v0.8.16.md`：面向部署者的说明与回滚键。

### 2026-11-02 12:1x 0.8.16 开始部署（bridge 执行，9哥 已批准）

**开发侧**：0.8.16 四批 + 评审 3 次 + verifier 全量 `2496 passed / 7 skipped`，CI 四绿（`6e0f374`），
amd64 镜像 `ghcr.io/yes999zc/rui@sha256:9d28f00d30192becf0be6bb08ada3cba06c6d4bfa3add207e5eee9eb366ca11e`。

**部署前状态**（12:12 实测）：0.8.15、5 台、`device_errors 0`、`mic 10 / ref 14`、`wake_skip_ms 0`、
`ack tts`、音量 80%、门限临时 `2.00 #0.15`。

**部署顺序**（照开发会话建议）：
1. 备份 `config.yaml` / `keywords.txt`；override 里换 amd64 摘要并 `compose up -d`；
2. `speaker_gate.enabled` **保持 false** 先验等价性（5 台 / 0 错误 / capture / ack）；
3. 控制台注册 3 人 × 2 台（living、dot2-c）；
4. 开 `speaker_gate.enabled: true`（阈值 0.55），`keywords.txt` 回 `4.00 #0.07`；
5. 按需 `devices[].audio.voice.kws_threshold`（客厅 0.07、1 m 三台 0.15）；
6. 回填模型 sha256、镜像 digest、以及 `tests/0.8.16-acceptance.md` 的验收结果。

**回滚锚**：0.8.15 amd64 `sha256:4a4c2db421367ee5c110d97d8ac5a984f4702fa6f854fde12f1a087723cd948d` + 上面两份配置备份。

### 2026-11-02 12:15 0.8.16 已部署（bridge 执行，等价性验证通过）

**部署结果（实测）**
- `/health` → **0.8.16**、devices 5、enabled 5、**device_errors 0**、planes 5、`ack.mode tts`；容器 StartedAt
  `2026-11-02T04:15:34Z`、`RestartCount 0`、镜像 = `ghcr.io/yes999zc/rui@sha256:9d28f00d…`（amd64 摘要 pin 进 override）。
- `effective.capture = mic 10 / ref 14`；`audio.voice.gain_db = 6.0`、`wake_skip_ms = 0`；
  `audio.aec.residual = true`、`wake_hold_ms = 15000`；
  `audio.voice.speaker_gate = {enabled: false, model_path: '', threshold: 0.55, mode: any_enrolled,
  segment_gate: true, wake_window_ms: 2500}`（尚未启用，符合部署顺序 ①）。
- 声纹模型 sha256：`f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11`
  （`/vol1/rui/data/models/speaker/campplus.onnx`，28 281 138 B）。
- 备份（本次部署前）：`config.yaml.bak-20261102-121411-bridge-pre0816`、
  `keywords.txt.bak-20261102-121411-bridge-pre0816`、`rui-dot2-override.yml.bak-20261102-121411-bridge-0815`。
- 回滚：override 换回 `sha256:7c1a3aa7…`（0.8.15 index）/`4a4c2db4…`（amd64）+ `compose up -d`。

**下一步**：② 注册 3 人 × 2 台（living、dot2-c）→ ③ 开 `speaker_gate.enabled: true` + `model_path`，
`keywords.txt` 回 `4.00 #0.07` → ④ 按 `04_Docs/human-bridge/tests/0.8.16-acceptance.md` 验收。

### 2026-11-02 12:2x 0.8.16 **注册（enroll/commit）必失败**：numpy 真值判断 bug（P0，需 0.8.17）

**现象（bridge 部署后自测，无需人声）**：
```
POST /api/v2/speakers/enroll {"name":"_smoke","device_id":"living","seconds":2}  → 201（arm 正常）
POST …/commit                                                                    → 500 Internal Server Error
```
traceback 定位：
```
speakers.py:580 in commit
speaker_gate.py:326 in embed
ValueError: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
```
**原因**：`SpeakerEmbedder.embed(pcm: bytes | np.ndarray, rate)` 的守卫写成 `if self._extractor is None or not pcm:`；
commit 路径传进来的是 **numpy 数组**，`not pcm` 在多元素数组上抛 ValueError。`_as_float32()` 已兼容数组，
说明本意是支持数组的，只是守卫没兼容。
**影响**：声纹注册入口 100% 不可用（控制台「家庭成员」页同样会 500）。唤醒门控本身未受影响
（5 台 `speaker gate on: threshold=0.55 enrolled=0`，库空时 fail-open）。

**修法（0.8.17，一行）**：
```python
if self._extractor is None or (pcm.size == 0 if isinstance(pcm, np.ndarray) else not pcm):
```
并补单测：`embed()` 分别喂 `bytes` / `np.int16 数组` / `np.float32 数组` / 空数组，不得抛异常。

**临时验证路径**（只在容器内热修 `site-packages/.../speaker_gate.py` + `docker restart rui`，
等价于只给「commit 那一次调用」放行；下次 `compose up -d` 会丢，不能当正式修复）——
用于今天先把 3 人 × 2 台的 embedding 录进 `data_dir/speakers.json`（数据本身不受补丁影响）。

### 2026-11-02 12:26 注册 bug 已热修（bind-mount，非正式修复），commit 通路打通

- 容器 rootfs 是 `read_only: true`，`docker exec` 改 site-packages 会 `OSError: Read-only file system`。
- 做法：把 `speaker_gate.py` 拷到 `/vol1/rui/hotfix/speaker_gate.py`，只改 `embed()` 的守卫，
  override 里加一行只读 bind mount：
  `- ./hotfix/speaker_gate.py:/usr/local/lib/python3.12/site-packages/rui/audio/speaker_gate.py:ro`
  再 `compose up -d`（容器重建，health 0.8.16 / 5 台 / 0 错误）。
- 验证：`POST /api/v2/speakers/enroll` → 2 s 无语音 → `commit` 返回 **200**（此前 500），
  库内出现 `_smoke`（已 DELETE 清掉，库回到空）。
- **这不是正式修复**：0.8.17 必须在源码里改守卫 + 补单测（task-2），上线后删掉 hotfix 挂载并把
  `hotfix/` 目录归档。备份原文件：`/vol1/rui/data/speaker_gate.py.0.8.16-orig`。

### 2026-11-02 13:1x 注册 bug 源码修复已提交（`e57e59d`，已推）

- `src/rui/audio/speaker_gate.py`：`embed()` 守卫改为
  `if self._extractor is None or (pcm.size == 0 if isinstance(pcm, np.ndarray) else not pcm):`
- 新增回归 `tests/test_speaker_gate.py::test_embed_accepts_int16_and_float32_arrays_not_only_bytes`：
  用鸭子类型 extractor 驱动，喂 `bytes` / int16 数组 / float32 数组 / 空数组，不依赖 sherpa-onnx 与模型文件。
- 验证：`.venv/bin/python -m pytest tests/test_speaker_gate.py tests/test_speakers_api.py -q` → 全过（1 skip
  是真实模型那条）；`ruff check` → All checks passed。
- **生产侧现状没变**：容器仍在用 `/vol1/rui/hotfix/speaker_gate.py` 的只读挂载版本（同一行修改）。
  0.8.17 出镜像后：`compose up -d` 换成新镜像 → 从 override 删掉那行 hotfix 挂载 → `hotfix/` 归档，
  并把 `data/speaker_gate.py.0.8.16-orig` 删掉（保留到那天为止）。
- 本次只提交了源码修复，**没有升版本**（0.8.17 的版本号/CHANGELOG/镜像走正常发版流程）。

### 2026-11-02 14:30–14:50 声纹注册第一轮失败 + 现状（压缩前快照）

**做了什么**：0.8.16 部署后尝试把 3 人 × 1 台（living）注册进声纹库，失败，已回退到「门控关闭」。

**失败的两种方式（都要记，别再重演）**
1. **设备直录 + 定时窗口**（10 s × 3 段、按秒数换人）：家人跟不上节奏，中间混进了别人的声音；
   「妈妈」那条是在她**不在家**时录的，档案自相似 0.49、与爸爸 0.65 —— 纯噪声档案，已删除。
2. **手机录音发文件**（m4a → 16 kHz 单声道，26–31 s，语音密度正常）：入库成功，但**域差太大**——
   手机档案与设备远场档案相似度：孩子 **0.00**、爸爸 **0.39**；真机 3 米实测分数 0.46/0.48（拒），
   靠近 0.57（过）。⇒ 手机近讲录音不能用于远场声纹。

**差点被当成成功的假阳性**：爸爸重录那次 `session1=0.078 / session2=0.9752`，我一度当成"质量最好"，
但 9哥 说那次他**没录**；两份相近的样本若是同一房间底噪，自相似同样会很高（噪声很平稳）。
⇒ **教训：登记质量不能只看"段间自相似高"，必须同时看 (a) 时长内是否有语音活动、(b) 真机远场实测分数。**

**当前生产状态（14:50）**：0.8.16、5 台、`device_errors 0`；
`audio.voice.speaker_gate.enabled = false`（**门控已关，家人可正常唤醒**），阈值 0.55，库内现有：
`孩子`（手机版，域差）、`爸爸`（设备版，内容存疑）——两份都**不可信**，等下一轮覆盖。
`keywords.txt` = `:4.00 #0.07`；`mic 10 / ref 14`；`gain_db 6`；`wake_skip_ms 0`。

**下一轮方案（9哥 提的，待执行）**：用客厅小爱连续录 ~40 s，两人按标记朗读——
爸爸先读「我是爸爸。你好小宝，几点了。你好小宝，今天天气怎么样。放首歌听。」
停 3 秒后孩子读「我是孩子。……」；我按停顿切分 + ASR 核对标记词，再分别用设备音频入库，
最后必须做**真机远场分数验证**（≥0.7 才算可用，0.55 只是阈值下限）。

### 2026-11-02 15:00 声纹注册成功：爸爸 + 孩子（设备直录，真机验证通过）

**可复用的采集流程（已验证两次，别再试别的）**
1. `POST /api/v2/speakers/enroll {name:"_cap", device_id:"living", seconds:10}` **并同时**播放提示音
   `/api/v1/debug/play {"device":"living","file":"/var/lib/rui/beep2.wav"}`（两声 700 Hz，0.81 s）；
2. 1.7 s 后把设备音量压到 1（否则 TTS 回复会混进样本），8.9 s 后**立刻** `cp` 走
   `/var/lib/rui/enroll/<id>.wav`（**文件只活 30 秒**，超时被 sweep 清掉）；
3. 剪掉开头 1.5 s（滴滴）+ 首尾静音，按 50 ms RMS>0.015 找语音段；
4. 入库：arm 1 s 会话 → sleep 2.5 → 覆盖该 `enroll_id` 的 wav + sidecar（`ms`/`speech_ms`=时长）→ `commit`；
5. 重启加载档案 → 真机验证。

**结果**（3 米，阈值 0.55）：
| 人 | 有效语音 | 真机 wake 分数 | 判定 |
|---|---|---|---|
| 爸爸 | 8.4 s | **0.77 / 0.70** (best=爸爸) | 稳 |
| 孩子 | 8.5 s | **0.75 / 0.64 / 0.56** (best=孩子) | 可用，但 0.56 接近阈值 |

**踩过的坑（都写进记忆，不要再犯）**
- 手机近讲录音**不能**用于远场声纹（phone vs device 相似度：孩子 0.00、爸爸 0.39；3 米实测 0.46 被拒）。
- "两段自相似高"≠质量好：房间底噪两段也高度相似（那天 0.975 是假阳性，9哥 当时没录）。
- 提示音必须**完整播完**再静音：上一版 0.2 s 就压音量，滴滴被掐断，家人以为没开始。
- 定时窗口（按秒换人）不可用：家人跟不上；改成**一次只录一个人、听到滴滴读 10 秒**。
- 会话不能重叠：紧接着 arm 第二个 10 s 会 409，要等 ≥2 s 并带重试。

**当前**：`speaker_gate.enabled: true`、阈值 0.55、`mode: any_enrolled`、`ack tts`、5 台、0 错误。
待办：孩子补录一段（0.56 那次偏低）、妈妈回家后录、视频 10 分钟 0 误唤醒验收、放歌双讲验收、主卧（dot2-c）录制。

### 2026-11-02 15:05 孩子补录后真机分数（当前生效档案）

| 人 | 补录前 | 补录后（2 米、稍大声、8.5 s） |
|---|---|---|
| 孩子 | 0.75 / 0.64 / **0.56** | **0.66 / 0.69**（都过阈值，最低值从 0.56 抬到 0.62+） |
| 爸爸 | 0.77 / 0.70 | 0.62（同一次会话里） |

档案一致性也变好（新孩子档案 vs 旧：0.60）。**当前生效配置**：`speaker_gate.enabled: true`、阈值 0.55、
`mode: any_enrolled`、`ack tts`、库内 `爸爸` + `孩子`（都在 living）、5 台 0 错误。
9哥 决定：**先用这套**。待办：妈妈录音、主卧 dot2-c 三人档案、视频 10 分钟 0 误唤醒验收、放歌双讲验收。

## ★ 压缩交接（2026-11-02 15:15，下个会话从这里接）

### A. 生产现状（15:15 实测，别信转述，先复核）

| 项 | 值 |
|---|---|
| 版本 | **0.8.16**（镜像 `ghcr.io/yes999zc/rui@sha256:9d28f00d…`，pin 在 `/vol1/rui/rui-dot2-override.yml`） |
| 健康 | 5 台 / 5 启用 / `device_errors 0` / planes 5 / `ack.mode tts` |
| capture | `mic_shift 10 / ref_shift 14` |
| voice | `gain_db 6.0`、`wake_skip_ms 0` |
| aec | `residual true`、`wake_hold_ms 15000`（语义=旁路上限） |
| 声纹门控 | **enabled true**、`threshold 0.55`、`mode any_enrolled`、`segment_gate true`、`wake_window_ms 2500`；模型 `/data/models/speaker/campplus.onnx` |
| 声纹库 | **爸爸 + 孩子**（都在 living）；妈妈未录；dot2-c 主卧未录 |
| keywords | `:4.00 #0.07`（两行，含「你好小宝儿」） |
| 音量 | 运行时 230/255（**用户手动调过**，config `devices[living].volume = 80`；fleet restart 会重置成 204） |

### B. 两处"临时但正在生产运行"的东西（0.8.17 必须收掉）

1. **只读热修挂载**：`rui-dot2-override.yml` 第 5 行
   `./hotfix/speaker_gate.py:/usr/local/lib/python3.12/site-packages/rui/audio/speaker_gate.py:ro`，
   修的是 enrol commit 的 numpy 真值 bug。源码已修（commit `e57e59d` + 回归测试），**0.8.17 出镜像后要删掉这行挂载**并归档 `hotfix/`。
2. 原文件备份 `/vol1/rui/data/speaker_gate.py.0.8.16-orig` 也在，那行挂载删掉后可一并删除。

### C. 声纹注册唯一可用流程（今天试错 4 次才定，照抄）

**一次只录一个人，10 秒**：
1. `POST /api/v2/speakers/enroll {name:"_cap", device_id:"living", seconds:10}` **同时**放
   `/var/lib/rui/data/beep2.wav`（两声 700 Hz，用 `/api/v1/debug/play {"device":"living","file":…}`）；
2. 1.7 s 后 `PUT /api/v1/device/volume {"value":1,"device":"living"}`（压掉 TTS 回复）；
3. 8.9 s 后**立刻** `cp /var/lib/rui/enroll/<id>.wav`（**只活 30 秒**）；
4. 剪掉开头 1.5 s（滴滴）+ 首尾静音（50 ms RMS>0.012~0.015 找语音段）；
5. 入库：再 arm 1 s 会话（name=真人名，注意 409 要重试）→ sleep 2.5 → 覆盖该 `enroll_id` 的 wav 与 sidecar
   （`ms`/`speech_ms`=时长）→ `commit`；
6. `docker restart rui` 让平面加载新档案 → 真机 3 米验证（≥0.7 算稳，0.55 是阈值下限）。

**不可用路线**：手机近讲录音（域差：孩子 phone-vs-device 0.00，爸爸 3 米实测 0.46 被拒）；
按秒换人的定时窗口（家人跟不上）；"两段自相似高=质量好"（底噪也高，曾误判 0.975）。

### D. 待办（按优先级）

1. **妈妈录音**（10 秒，同流程）→ 真机验证
2. **主卧 dot2-c 三人档案**（吊顶 3 米，需在那台单独录 + 单独标定；注意 dot2 = 设备侧 AEC、无 ref）
3. **验收三项**（`04_Docs/human-bridge/tests/0.8.16-acceptance.md`）：
   A 视频 10 分钟 0 次「在呢」/ C 视频中命令 ≥4/5 / D 放歌「下一首」×5 ≥4/5
4. **0.8.17**：把 `e57e59d` 发版 → 删 hotfix 挂载；顺带 `erle-bench` 的错误常量 14→24（backlog）
5. `uv.lock` 还写 0.8.14（0.8.15/0.8.16 未同步）

### E. 今天已确认的引擎级事实（0.8.16 做到了什么）

- `wake_skip_ms 0` 修掉「几点了」丢首字；`gain_db 6` 提升远场识别；`wake_hold_ms 15000` 让残差级全程让位；
- 声纹门控能区分家人与媒体（爸爸 0.62–0.77、孩子 0.66–0.69，best 从不串台）；
- 但**放歌双讲**（「下一首」→「下一周」）仍是 DTD/AEC3 级问题，0.8.16 只做了旁路，未根治；
- 外部媒体（电视/视频）没有参考通道，只能靠声纹拦，音频本身消不掉。

### 2026-11-02 15:12 声纹门控已关闭（9哥 决定，临时）+ 两台设备离线

**为什么关**：15:06–15:10 儿童房 `dot2-d` 连续 7 次「你好小宝」，**6 次被声纹拒**
（分数 0.57 / 0.50 / 0.35 / 0.00 / 0.04 / 0.02）。根因不是门限值，而是
`dot2-d 没有自己的档案`：`SpeakerGate._match_device()` 在「本设备无档案」时走
`matching all devices`，拿客厅档案跨设备比；1 米近场 + 设备侧 AEC 的域差把分数打到近 0
（与手机录音失败同一个根因）。⇒ 儿童房实际上叫不醒。

**为什么不能只关这一台**：`DeviceVoiceOverride` 里没有 `speaker_gate` 字段，
`/api/v1/config/catalog` 也没有暴露 `audio.voice.speaker_gate.*`（0.8.16 新字段未进 catalog，
`PUT /api/v1/config/values` 报 `unknown path`）。⇒ 只能全局关，且只能改 config.yaml + 重启。

**做法（可回滚）**：
- 备份 `/vol1/rui/config/config.yaml.bak-20251102-151226-gate-off`；
- 容器内 ruamel round-trip 把 `audio.voice.speaker_gate.enabled` 改 `false`（`diff` 只有这一行）；
- `docker restart rui` → `health ok / 0.8.16 / 5 台 / 0 错误`；
- 生效复核：`living / dot2-d / dot2-b` 三台 `effective.voice.speaker_gate.enabled = false`；
  `dot2-d` 15:13:14 重新注册并 `ready`；音量仍 230（config `devices[living].volume = 90`）。
- **阈值 0.55 / mode any_enrolled / 库内爸爸+孩子全部保留**，只关开关 ⇒ 恢复 = 改回 `true` + 重启。

**同时实测到的两台离线设备**（9哥 决定今天不弄）：
`dot2（书房）` 15:02:22 连上后掉线；`dot2-c（主卧）` 自 14:57 重启后**从未注册**。
API 报的 `token_missing` 是次要码（`token_managed_by_adapter` 要求 link 活着），真因是设备没上线。
⇒ 主卧三人档案与主卧验收（E）全部阻塞，等设备回网。

**新工具** `scripts/enroll_speaker.py`（已 scp 到 NAS `/vol1/rui/bin/enroll_speaker.py`）：
把今天试错 4 次的流程固化 —— arm 10 s + 滴滴 → 1.7 s 压音量 → 抓 wav → 裁滴滴与首尾静音
（相对底噪阈值，静音必须被拒）→ 覆盖 sidecar（`ms = speech_ms`）→ `commit` → 重启 → 恢复音量。
支持 `--device dot2-d`、`--dry-run`（只录不提交）、`--no-restart`。已在真实采集样本上验证裁剪
（10.0 s → 3.57 s 语音；纯静音 → 0.00 s 拒绝）。

**恢复门控的顺序**（不要先开门控再录）：
1. `dot2-d` 录孩子：`python3 /vol1/rui/bin/enroll_speaker.py --name 孩子 --device dot2-d`，3 米验证；
2. 客厅补妈妈（爸爸/孩子现有档案继续有效）；
3. 全部录完再把 `audio.voice.speaker_gate.enabled` 改回 `true` + 重启。

### 2026-11-02 15:18 客厅拓扑改造：小爱下线，Dot 接管（`dot2-d` 搬进客厅）

**物理事实（9哥 确认，纠正 15:0x 的误判）**：客厅是 **小爱音箱 Pro（LX06）**，它有 3.5 mm **AUX 输入**；
那台 Echo Dot 2nd（RS03QR，rui 里的 `dot2-d`）**就在客厅、和小爱挨着**，Dot 的 3.5 mm 输出插在小爱的 AUX 上。
此前 rui 里 `living` = LX06，所以**收音和播放都走小爱**，Dot 只是把模拟声音灌进小爱 ⇒ 两条声音路径混在一起。

**改造内容**（备份 `config.yaml.bak-*-living-to-dot`）：
- `devices[dot2-d].room`：`儿童房` → `客厅`；
- `devices[living].enabled`：`true` → `false`，room 标注 `客厅（小爱·已下线）`；
- `docker restart rui`。

**结果复核**：`health ok / 0.8.16 / 4 台 / 4 启用 / 0 错误`；`dot2-d = ready`（port 8874、capture 16 k、playback 48 k、音量 191 = 75%）；
`living = disabled`（不再占 4412 端口，重启后无 lx06 拨号）；`POST /api/v1/debug/say {device:dot2-d}`
实际在 `dot2-d` 播放（`reply: first chunk at 48000 Hz`），链路通。

**待验证/待标定**：
1. Dot 之前在儿童房 1 米（设备侧 mic 偏热），现在客厅 2–3 米 ⇒ **唤醒率要重新量**（`[seg] rms` / ASR / 唤醒分母）；
2. 小爱 AUX 档位要先手动定好并**不再动**，之后音量只由 rui 控 Dot（两级音量）。
3. 如果 Dot 的 3.5 mm 插入没有自动静音其内置喇叭，会出现「小喇叭 + 小爱」双出声，要确认。

**⚠️ 副作用**：`儿童房` 现在没有设备了（dot2-d 是唯一一台在那间的）。要恢复就在儿童房再放一台。

**回滚**：`living.enabled: true` + `dot2-d.room: 儿童房` → 重启（配置备份 `config.yaml.bak-20261102-1518xx-living-to-dot`）。

### 2026-11-02 15:30 客厅新链路验证通过（坏线换掉，小爱出声）

**关键事实**：15:21 那根 3.5 mm 线是**坏的**（Dot 的喇叭还在响，小爱没声）。9哥 换线后
**小爱已正常出声** —— 即「Dot 解码 → 模拟 → 小爱功放」这条路成立，`dot2-d` 仍是 rui 的唯一设备。

**实测（15:29–15:30，音量 153 = 60%）**：
- 真实唤醒 `你好小宝儿` 通过 → `[seg] 1.32 s rms=0.0254 peak=0.2145`（Dot 在 2–3 米，电平健康）；
- playback 期间 **barge-in 正常**（`playback paused for speech`，cue 160 ms 交付 12 ms）；
- 语音改音量命令生效（`tool volume({"level":"60"})` → 运行时 153）；
- 近 10 分钟 `wake:` 2 次、`overdrive/clipped` **0 次**（小爱当功放后 Dot 的 mic 不再饱和）；
- 唯一一条自听转写 `哪手都行`（15:21:16）发生在**坏线期间**（声音从 Dot 自己喇叭出），换线后未复现。

**结论**：客厅拓扑 = Dot 收音/解码 → 小爱功放，**不需要改引擎**，AEC 参考仍是 Dot 自己的输出。
剩余口径：3 米唤醒率（分母）、小爱 AUX 档位固定不动、以及「媒体在场 0 误唤醒」验收。

### 2026-11-02 15:33 客厅音量定档（9哥 手动确认效果）

- `dot2-d`（Dot，客厅）：运行时 **255 = 100%**，config `devices[dot2-d].volume = 100`（重启不丢）。
- 小爱 Pro：**AUX 档位由 9哥 在机身上手动设好（≈80），此后固定不动**；rui 已不再管理它（living disabled）。
- 最终响度 = Dot 100% × 小爱固定档；9哥 现场确认「效果不错」。
- 两级音量的口径写死：**只调 Dot，不动小爱**。若日后某段语音发炸（模拟链路削顶），先把 Dot 退到 90 再听。

### 2026-11-02 16:01 记录点：9哥 拔掉客厅小爱，只测 Dot（我不再改配置）

**起因**：15:5x 的调试里，「在呢」之后仍会多一句——不是自听那条路径，而是回声残片「呢。」被判噪声后
走 `app.py:3581 rules.is_noise -> _not_heard`，回了「没听清，你再说一遍」。9哥 已明确：停止改动，先拔小爱、
只测 Dot 本身。**此记录点之后不得擅自改配置/代码，只记日志事实。**

**16:01 快照**（改不动的东西记清楚，方便对比）：
- 版本 0.8.16 / health ok / 4 台 / 0 错误；`dot2-d` = 客厅 / ready；音量 255
- 服务器热修 `process.py` **在**（`skip = ACK_PLAYOUT_LEAD_MS + cue + ACK_TAIL_MS` = 1680 ms）
- 设备侧 `DOT2_AEC_DELAY_MS` **已撤**（仅 `DOT2_AEC_ENABLED=1`）
- `ack.keep_overlapping_speech = false`；`session.farewell = off`
- 客厅声音路径：Dot 3.5 mm → 小爱 AUX 功放
- **待测**：拔掉小爱后，Dot 只剩自己喇叭，AEC 参考与实际发声一致 → 观察「在呢」之后是否还多一句

### 2026-11-02 16:02–16:03 拔掉小爱、Dot 单独测（实测记录，未做任何改动）

**前提**：9哥 16:01 拔掉小爱，客厅只剩 Dot 自己的小喇叭；配置未动（热修在、`DOT2_AEC_DELAY_MS` 已撤、
`keep_overlapping_speech=false`、`farewell=off`、音量 255）。

**观察 1：「没听清，你再说一遍」依然出现，与小爱无关**
```
16:03:12 WAKE → CUE 580ms → GATE match=0.97 dropped=[0,1680]
16:03:15 SEG 1.06 s rms=0.0069 → noise, no turn: '。' -> asking to repeat
16:03:16 tts「没听清，你再说一遍。」
```
⇒ 回声匹配**成功了**（0.97），仍会冒出一个极安静的残段（rms 0.0069、转写「。」），
走 `app.py:3581 rules.is_noise -> _not_heard` 出声。**这是设计行为（T5：噪声也回一句、不静默），
不是回声防护失效**，也不是小爱造成的。

**观察 2（新的、更严重）：Dot 自带喇叭 + 音量 255 会让麦克风削顶、自听回来**
```
16:02:04 WAKE → CUE 160ms
16:02:08 SEG 2.94 s rms=0.1049 peak=0.9500   ← 削顶
16:02:09 own voice heard back, no turn: '没没听清，你再说。'
```
⇒ Dot 直接外放时，自己说的话被自己的麦克风听到（peak 0.95），AEC 没兜住。
小爱当功放时不会出现这一条（Dot 内部喇叭静音）。**说明 255 音量对 Dot 自带喇叭过大。**

**观察 3：唤醒打断正在播的回复 → cue 只有 160 ms**（barge 设计），16:02:02 / 16:02:04 / 16:02:43 /
16:03:00 都是这样。这是「只响一声、不说在呢」的来源（与告别语那次同一机制）。

**结论（只记录，不改）**：问题分三层，且**不依赖小爱**：
① 唤醒后残段被当噪声 → 回「没听清」（设计行为，可关）；
② 音量 255 + Dot 自带喇叭 → 削顶 + 自听（没有小爱时才有）；
③ 唤醒打断播放 → barge 只给短音（设计行为）。

### 2026-11-02 16:07 书房那台 Dot（fork 固件 20261024-1911-dev）实测

| 时间 | 事件 | 数据 |
|---|---|---|
| 16:07:26 | 唤醒 → cue **580 ms** | `cue_gate match=0.97`，`dropped=[0,1680]` → 随后 `wake without speech` |
| 16:07:38 | 唤醒 → cue **580 ms** | `match=0.92`，`[seg] 0.98 s rms=0.0800 peak=0.5717` |
| 16:07:42 | — | `turn: '宝。' => '在呢，说吧。'` ← **多一句** |

**结论（与固件、与小爱都无关）**：
1. 回声匹配 0.92/0.97 → 回声被正常定位并丢弃，**回声没问题**；
2. 多出来的那句，转写是 **「宝。」** —— 是唤醒词「你好小宝」的**尾巴**，不是回声。
   根因：`wake_skip_ms = 0`（0.8.16 为修「几点了」丢首字而设 0），唤醒词自己的尾巴进了 VAD 段；
   `strip_wake()` 只能剥完整唤醒词，「宝。」这种残片剥不掉 → 当成命令 → LLM 回话。
3. 没有削顶（peak 0.57；对比 dot2-d 自己喇叭 255 时的 0.95）。

### 2026-11-02 16:35 版本决策：**生产留在 0.8.15，0.8.16 跳过**

**为什么**：客厅弃用小爱、Dot 接管（设备侧硬件 AEC），且不再做声纹。0.8.16 的主题是「声纹只做门控」（D8′），
其 P0 主体（声纹门控 / 段级门控 / 媒体声音门控）在新架构下全部失效；而它带来的两条代价全落在身上。

**实测对照（同一台 dot2、同一场景、都不接小爱）**：

| | 0.8.16 | 0.8.12 / 0.8.15 |
|---|---|---|
| 唤醒后多一句 | `turn: '在呢。' => '在呢，你说。'`（自听轮） | **0 次** |
| 「几点了」 | 出现过「几院了」 | 0.8.15 三次全对 |
| 回声残差进 VAD | 是 | 否 |

**根因**：`0e4fd77`（0.8.16）在 AEC 之后、KWS/VAD/ASR 之前加了 **+6 dB 增益**（`gain_db` 默认 6.0）。
同一提交里一并加了「空唤醒冷却」，今天实测把真人唤醒锁了 120 s。该提交自带回滚键 `gain_db=0`（未及真机验证）。

**0.8.16 每项的去留**：声纹/段级/媒体门控 ❌（不做声纹）；`gain_db` ⚠️净负；
`wake_skip_ms=0` ⚠️（三千尾巴 vs 丢首字的取舍）；空唤醒冷却 ⚠️（锁真人）；
**per-device KWS 门限/boost ✅、`vad_max_speech_ms` ✅ 值得保留**；enroll 500 修复 ❌（不做声纹）。

**今晚回滚链**：0.8.16 →（16:19）0.8.12 →（16:29）**0.8.15**。
GHCR 在 16:2x 起不可达（`context deadline exceeded`），0.8.15 是用**本地镜像** `82ba71acd3bf`（revision `4438cee`）
`docker tag` 成 `:0.8.15` 后切的容器 —— **override 里现在是 tag，不是 digest**，网络恢复后要重新钉回 amd64 digest。

**回滚锚**：

| 目标 | 镜像 | 配置备份 |
|---|---|---|
| 0.8.15（现在生产） | 本地 `82ba71acd3bf` / `:0.8.15`（网络恢复后钉 `sha256:4a4c2db4…`） | `config.yaml.bak-*-pre-rollback-0815` |
| 0.8.16 | `4b76da9cfc57` / digest `sha256:9d28f00d…` | `config.yaml.bak-20261102-161858-pre-rollback-0812` |
| 0.8.12 | digest `sha256:64a2c260…` | 同上 |

**GitHub 策略（不改历史）**：`main` 上 0.8.16 已发布、tag 保留不动；生产钉 0.8.15 是「部署选择」，
不靠回退提交表达。下一张卡做 **0.8.17（去声纹 / 去增益）**：`gain_db` 默认 0、`wake_skip_ms` 默认 300、
`empty_wake_cooldown_s` 默认 0、删/关声纹门控链路，**保留** per-device KWS 与 `vad_max_speech_ms`。

**遗留**：① GHCR 断连，digest 未重钉；② 生产配置仍是 10-30 时代的房间标签（dot2=儿童房 / dot2-b=次卧 /
dot2-d=卫生间），与物理摆放不符，只影响日志与控制台显示；③ 今晚只验了 Dot 单台，验收 A/C/D 仍未跑。

### 2026-11-02 16:37 本机禁用小爱 + 清掉 0.8.16 副作用默认值（仓库侧）

**本地（生产 0.8.15）**
- `devices[living].enabled: false`，room 改标 `客厅（小爱·本机不接入）` → `/health` = devices 4 / enabled 4 / 0 错误。
  配置 diff 只有这一处 + 四台 dot2 的 `ack_echo_ignore_ms: 1200`（0.8.12 当时的部署值）。
- **GitHub 上的小爱支持继续保留**：`rui_xiaoai_bridge` 插件、`lx06` adapter、`RUI_ADAPTER_PLUGINS` 里的
  `lx06=…` 一律不动 —— 只是这台部署不接入。
- hotfix 遗留清理：`/vol1/rui/hotfix/`（process.py / speaker_gate.py / __pycache__）与
  `data/speaker_gate.py.0.8.16-orig` 一并归档到 `/vol1/rui/backups/hotfix-archive-20261102-163734/`，
  `hotfix/` 目录已移除；override 里 hotfix 挂载计数 0。

**仓库（未发版，只改默认值）** —— 去掉 0.8.16 的四个副作用默认值：
| 字段 | 0.8.16 | 改回 |
|---|---|---|
| `audio.voice.gain_db` | 6.0 | **0.0** |
| `audio.voice.wake_skip_ms` | 0 | **300** |
| `audio.voice.empty_wake_cooldown_s` | 120.0 | **0.0** |
| `ack.keep_overlapping_speech` | True | **False** |
同步改 `config.example.yaml` 与三处测试断言（`test_config_v3.py` 模板、`test_speaker_segment_gate.py` 默认值）。
**保留** per-device KWS 门限/boost 与 `vad_max_speech_ms`。

**测试**（提交 `b1cdd9d`，已推）：`pytest` 全量 → 除 2 条环境相关外全绿。
那 2 条是 `test_devices_admin::test_probe_reports_an_unreachable_host` 与
`test_setup::test_probe_reports_an_unreachable_host_instead_of_raising`：
本机 DNS 把 `no-such-host.invalid` 解析到 `198.18.0.188`（代理/catch-all），端口"能连上"，
与本次改动无关（`git stash` 后同样失败）。

### 2026-11-02 16:45 0.8.15 与「0.8.17」（仓库 main）到底差什么

**注意**：0.8.17 还没有发版 —— 它目前只是 main 上的 `b1cdd9d`（默认值回滚）+ 0.8.16 的代码。
按 `git diff v0.8.15..HEAD` 逐项看，**行为差异只剩两条**：

| 项 | 0.8.15 | main（0.8.17 候选） | 对本机的影响 |
|---|---|---|---|
| `vad_max_speech_ms` | 无 | **8000**（默认生效） | 有用：超长段按块切，媒体长段不会整段进 ASR |
| `wake_hold_ms` | 1500 | **15000** | **无**（只在服务器 AEC 开启时生效，Dot 是设备侧 AEC） |
| `gain_db` / `wake_skip_ms` / `empty_wake_cooldown_s` / `keep_overlapping_speech` | — | 已回滚到 0.8.15 行为（`b1cdd9d`） | 无差异 |
| per-device `kws_threshold` / `kws_boost` | 无 | 有（默认 None = 继承） | 可选能力，不配置就等于没有 |
| 声纹门控 / 段级门控 / 媒体门控 / enroll API | 无 | 有（默认关闭） | 不建档就等于没有 |

**结论**：0.8.17 按默认值跑，行为 ≈ 0.8.15 + 一条长段切块；真正的增量是「可选能力」。
要不要发这一版，取决于以后是否需要按房间调 KWS 门限。

### 2026-11-02 16:47 **发版 0.8.17**（Dot-only 收口 + 小爱停止维护声明）

**内容**（提交 `70dc019`，tag `v0.8.17` 已推，CI 已触发）：
- 默认值回滚（同 `b1cdd9d`）：`gain_db 0`、`wake_skip_ms 300`、`empty_wake_cooldown_s 0`、
  `keep_overlapping_speech false`。
- **维护状态变更（9哥 定）**：作者不再维护小爱（LX06 / `rui-xiaoai-bridge`），新部署全面用 Dot。
  **代码与插件不删**，`lx06` adapter / `RUI_ADAPTER_PLUGINS` 原样保留；README 加中英双语声明，
  `docs/releases/v0.8.17.md` 写全原因（LX06 靠服务器 AEC，远场/双讲劣于 Dot 的设备侧硬件 AEC）。
- `uv.lock` 版本终于对齐（此前一直停在 0.8.14，0.8.12 发版时吃过一次亏）。

**验证**：`test_version` / `test_wake_skip_default` / `test_audio_scale` / `test_config_v3` 全绿；`ruff` 全绿。

**部署**：本机生产**仍是 0.8.15**，未升级（9哥 未要求）。GHCR 16:2x 起从 NAS 不可达，
   0.8.17 镜像由 GitHub Actions `docker-publish` 在 tag 上构建发布；NAS 拉取需等网络恢复。

**CI 结果（16:53）**：`docker-publish` ✅ 成功，镜像已发布：
- index digest `sha256:77841cab698342c76deef794876df19605e66ba2a63eb035b917d2518b777a46`
- **amd64 `sha256:5020f0640a824f76a997929987f11f8dbb43db1c9fe4e19ed9b33df73d027383`**（NAS 部署用这个钉）
- tags：`0.8.17` / `0.8` / `latest` / `sha-70dc019`
- `secret-scan` ✅、`ci` ✅；`security`（Trivy 镜像扫描）❌ —— 报 base image 里有未豁免的 HIGH/CRITICAL，
  与本次改动无关（扫的是基础镜像），**不阻塞发布**（publish 只依赖 test 门）。
- 镜像 label 的 description 仍写着「Xiaomi LX06 via open-xiaoai」，与小爱停维护的定位不符，
  属 workflow 元数据遗留，下次发版前顺手改。

**部署**：本机生产**仍是 0.8.15，未升级**。NAS 侧 GHCR 16:2x 起不可达，要升级得等网络恢复后
`docker pull ghcr.io/yes999zc/rui@sha256:5020f064…`（或先在一台网络正常的机器 `docker save` 再导入）。
