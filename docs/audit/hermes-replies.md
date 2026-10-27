# Hermes 23 个 OPEN issue 回复包（Lead 统一发帖）

> 生成：2026-10-27。依据：`docs/audit/hermes-triage.md` + 三仓实际 commit。
> **只写回复，不发帖、不关单**；动作由 Lead 执行。
> 基线（tag 指向的 commit）：rui `v0.7.4` = `13469a4`、`v0.7.8` = `a3205e9`；xiaoai `v0.2.2` = `749d4d3`；dot2 `v1.3.1` = `620426a`。

## 0. 动作分布

| 动作 | 数量 | issue |
|---|---|---|
| close（fixed） | **4** | rui#17、#18、#25、#28 |
| close（duplicate） | **1** | xiaoai#1（duplicate of rui#25，修复在 0.2.2） |
| close（wontfix / deferred） | **18** | 其余全部（未在 0.7.4 / 0.7.8 修复，去处见 `docs/ROADMAP-0.8.md` §8） |

## 1. 核对命令与结论（真实输出摘要）

```text
$ git log --oneline v0.7.3..v0.7.4
13469a4 release: 0.7.4 - P0/P1 fixes (413 body cap, greet, weixin, reconnect reset)
4468521 fix(0.7.4): weixin channel robustness and a player reset on reconnect
cf42695 fix(0.7.4): bound request bodies before auth and stop the greet busy loop

$ git log --oneline v0.7.4..v0.7.8 --grep='fix|feat' -i
2e9da6c fix(0.7.8): an unwritable music credentials path returns 409, not 500
efab571 feat(0.7.8): make the v2 console the default and retire the old entry points
5e16026 feat(health): per-device audio plane visibility and a single-plane warning
43d18df feat(install): ship TLS material in the two-speaker example
（无 rui#19/#20/#22/#23/#24/#26/#27/#29/#30/#31/#32/#33 的修复 commit）

$ git -C ../rui-xiaoai-bridge log --oneline v0.2.1..v0.2.2
8f3ac46 fix(xiaoai): reset the player when the connection is replaced
749d4d3 release: 0.2.2 - reset the player when the connection is replaced

$ git -C ../rui-dot2-bridge log --oneline v1.3.1..HEAD
1826e87 docs: add the TLS step ...
517623d docs: separate volume-control from loudness
18ad114 test(dot2): a stop during playback must not swallow the next reply
（仅 docs/test，无 #2–#6 修复）
```

**结论**：core 0.7.4 只修了 #17/#18/#28/#25；0.7.8 是控制台/安装/B 段，不包含其余 issue 的修复。因此 18 条按“未修复、转 backlog”回复，`docs/ROADMAP-0.8.md` §8 逐条给版本与理由。

---

## 2. rui（yes999zc/rui）

### rui#17 — 音频面启动：`_greet` 等 socket 的循环里没有 await，整个事件循环被同步阻塞最长 15 秒
- 结论：成立（triage P1，原报 P0 降级）
- 处置：已修：rui 0.7.4，commit `cf42695`；tag `v0.7.4`（`13469a4`）
- 证据：`src/rui/audio/client.py:536` `_greet` 改为 10 ms 让出事件循环，握手 `read_frame` 用单一绝对 deadline（`src/rui/audio/client.py:539`）；新增 `tests/test_audio_greet.py`；`cf42695` 含反向变异验证
- 建议动作：close（fixed）
#### 中文回复

```markdown
**结论**：成立，已在 rui 0.7.4 修复（commit `cf42695`）。
**证据**：`_greet` 现在每 10 ms 让出事件循环，握手 `read_frame` 使用绝对 deadline，子进程退出时保留 exit code 与 stderr 末行；新增 `tests/test_audio_greet.py`。版本：`v0.7.4`。
**前置条件**：原报告的影响只在子进程死亡/冷启动时出现，因此当时降为 P1，不是默认路径的 P0。
```
#### English reply

```markdown
**Conclusion**: valid; fixed in rui 0.7.4 (commit `cf42695`).
**Evidence**: `_greet` now yields the event loop every 10 ms, the handshake `read_frame` uses one absolute deadline, and a dead child keeps its exit code plus the last stderr lines; `tests/test_audio_greet.py` covers it. Version: `v0.7.4`.
**Scope note**: the impact required a dead child or a cold start, so it was re-rated P1 rather than a default-path P0.
```
### rui#18 — v1 的 31 个端点先读 body 再鉴权：不带 token 发一个大 body 就能把进程打满内存
- 结论：成立（triage P1，原报 P0 降级）
- 处置：已修：rui 0.7.4，commit `cf42695`；tag `v0.7.4`
- 证据：`src/rui/web/api/body_limit.py:39` 新增纯 ASGI `BodySizeLimitMiddleware`（默认 1 MiB，`RUI_MAX_REQUEST_BYTES` 可调）；31 个 handler 的解析位置未动，401/422 语义不变；`tests/test_request_body_limit.py`
- 建议动作：close（fixed）
#### 中文回复

```markdown
**结论**：成立，已在 rui 0.7.4 修复（commit `cf42695`）。
**证据**：新增 ASGI 中间件 `src/rui/web/api/body_limit.py:39`，Content-Length 超限直接 413，流式 body 累计超限也 413；GET/HEAD 穿透，安全响应头保留。handler 内解析位置未改，401/422 语义不变（C-002 A-7）。版本：`v0.7.4`。
**说明**：这是你建议的“在中间件限流”方案，而不是把 31 个 handler 全部改成先鉴权后解析。
```
#### English reply

```markdown
**Conclusion**: valid; fixed in rui 0.7.4 (commit `cf42695`).
**Evidence**: a pure ASGI `BodySizeLimitMiddleware` (`src/rui/web/api/body_limit.py:39`) returns 413 for a declared Content-Length or a streamed body above 1 MiB (`RUI_MAX_REQUEST_BYTES`); GET/HEAD pass through and the security headers stay. The 31 handlers were not re-ordered, so 401/422 semantics are unchanged (C-002 A-7). Version: `v0.7.4`.
**Note**: this is the middleware cap you proposed, not a rewrite of all 31 handlers.
```
### rui#25 — 小爱重连后 `_playing` 不复位，重连后的第一条回复被设备丢掉
- 结论：成立（triage P1；与 xiaoai#1 同一 bug）
- 处置：已修：rui 0.7.4，commit `4468521`；镜像到 xiaoai 0.2.2，commit `8f3ac46`（tag `749d4d3`）
- 证据：xiaoai `src/rui_xiaoai_bridge/adapter.py:273` `_reset_media_state()`；`adapter.py:256-264` 用连接/代际守卫阻止旧 play 复活、旧 socket 推进新队列；`tests/test_reconnect_reset.py`；rui 侧 commit `4468521`
- 建议动作：close（fixed）
#### 中文回复

```markdown
**结论**：成立，已在 rui 0.7.4 修复（commit `4468521`），并镜像到 xiaoai 0.2.2（commit `8f3ac46`）。
**证据**：重连替换 socket 时调用 `_reset_media_state()`（xiaoai `adapter.py:273`），`_playing` / `consumed` / `sink_generation` 一起复位；旧连接的 `play()` 在触碰新队列前返回，旧 socket 的 consumed 事件不再推进新队列（`adapter.py:256-264`）。`tests/test_reconnect_reset.py` 覆盖。
**版本**：`v0.7.4` + xiaoai `v0.2.2`。
```
#### English reply

```markdown
**Conclusion**: valid; fixed in rui 0.7.4 (`4468521`) and mirrored in xiaoai 0.2.2 (`8f3ac46`).
**Evidence**: replacing the socket calls `_reset_media_state()` (xiaoai `adapter.py:273`), resetting `_playing` / `consumed` / `sink_generation`; an in-flight old `play()` returns before touching the new queue, and the replaced socket's consumed events cannot advance it (`adapter.py:256-264`). `tests/test_reconnect_reset.py` covers both. Versions: `v0.7.4` + xiaoai `v0.2.2`.
```
### rui#28 — 微信渠道的语音链路：无字节上限、同步解码卡死事件循环、一条坏消息永久堵死渠道
- 结论：成立（triage P1，原报 P0 降级）
- 处置：已修：rui 0.7.4，commit `4468521`；tag `v0.7.4`
- 证据：`src/rui/channels/weixin.py:333` cursor 在 `finally` 保存；`:42` 语音下载 10 MiB 上限；`:423` pilk/ffmpeg 走 `asyncio.to_thread`；`tests/test_weixin_channel.py`
- 建议动作：close（fixed）
#### 中文回复

```markdown
**结论**：成立，已在 rui 0.7.4 修复（commit `4468521`）。
**证据**：getupdates cursor 放进 `finally`（`src/rui/channels/weixin.py:333`），一条坏消息不再导致同一条无限重放；语音下载流式累计上限 10 MiB（`:42`）；pilk/ffmpeg 解码移到 `asyncio.to_thread`（`:423`）。版本：`v0.7.4`。
**已知边界**：流式累计超限路径有实现、无单测；`getupdates` 响应本身仍未设上限。
```
#### English reply

```markdown
**Conclusion**: valid; fixed in rui 0.7.4 (`4468521`).
**Evidence**: the getupdates cursor is saved in `finally` (`src/rui/channels/weixin.py:333`), so one poison message cannot replay forever; voice downloads are capped at 10 MiB while streaming (`:42`); pilk/ffmpeg decoding runs in `asyncio.to_thread` (`:423`). Version: `v0.7.4`.
**Known edges**: the streamed over-limit path has no unit test, and `getupdates` responses still have no size cap.
```
### rui#19 — 音频面三处小缺口：播放 claim 在异常路径泄漏、候选裁决可能走失、ring 丢帧不告警
- 结论：成立 3/3（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/audio/coordinator.py:117` forget 路径、`src/rui/audio/arbiter.py:315` 裁决、`src/rui/audio/process.py:155` 丢帧计数
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立 3/3（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/audio/coordinator.py:117` forget 路径、`src/rui/audio/arbiter.py:315` 裁决、`src/rui/audio/process.py:155` 丢帧计数。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid 3/3 (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `audio/coordinator.py:117` forget path, `audio/arbiter.py:315` verdict, `audio/process.py:155` drop counter. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#20 — config 的恢复路径救不回来：version 解析抛裸异常绕过 recovery，落默认值又必然拒绝启动
- 结论：部分成立（triage P1）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/config/store.py:89-97` version 解析；`src/rui/config/recovery.py:101-107` 只捕获 `ConfigError`
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立（triage P1）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/config/store.py:89-97` version 解析；`src/rui/config/recovery.py:101-107` 只捕获 `ConfigError`。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: partially valid (P1).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `config/store.py:89-97` version parsing can raise a bare exception, and `config/recovery.py:101-107` catches only `ConfigError`. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#22 — Subsonic 的 token 进日志且可重放；另附三条凭证/落盘的加固建议（P2）
- 结论：成立（triage P1，安全）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/music/sources/subsonic.py:64-68` 的 `t=md5(secret+salt)`；请求 URL 未做日志脱敏，404 未先判状态
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立（triage P1，安全）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/music/sources/subsonic.py:64-68` 的 `t=md5(secret+salt)`；请求 URL 未做日志脱敏，404 未先判状态。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid (P1, security).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `music/sources/subsonic.py:64-68` builds `t=md5(secret+salt)`; request URLs are not scrubbed before logging and 404 is not checked before parsing. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#23 — 回合生命周期：超时硬截断且不收尾，ASR 不在任何超时内，TTS 半句时错误信息丢失
- 结论：部分成立（triage P1）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/session/turn.py:292-293` 的 tool/turn 超时、`:552-595` 的 retries；ASR 调用未包超时
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立（triage P1）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/session/turn.py:292-293` 的 tool/turn 超时、`:552-595` 的 retries；ASR 调用未包超时。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: partially valid (P1).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `session/turn.py:292-293` tool/turn timeouts and `:552-595` retries; the ASR call is not wrapped in a timeout. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#24 — 插件层：`register` hook 抛异常时内置 adapter 不复原；镜像 adapter 名可被 env 通道静默顶替
- 结论：部分成立：①③ 成立；② 不成立（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1（①③））
- 证据：`src/rui/plugins/resolve.py` 的 registry/facade 拒绝路径、`src/rui/plugins/models.py:398-427`（manifest 解析）；①③ 无 0.7.x 修复 commit
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立：①③ 成立；② 不成立（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1（①③））。
**证据**：`src/rui/plugins/resolve.py` 的 registry/facade 拒绝路径、`src/rui/plugins/models.py:398-427`（manifest 解析）；①③ 无 0.7.x 修复 commit。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：② 的“`0.5` 会被当成 `0.5.0` 从而遮蔽”不成立：`0.5` 与 `0.5.0` 在 PEP 440 下语义相同，且 entry-point 名称匹配不是 semver 比较。故 ② 关闭、不改。
```
#### English reply

```markdown
**Conclusion**: partially valid: ① and ③ are valid, ② is not a defect (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1 (①③)).
**Evidence**: registry/facade rejection in `plugins/resolve.py`; manifest parsing in `plugins/models.py:398-427`. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: ② is not a defect: `0.5` and `0.5.0` are equivalent under PEP 440, and entry-point name matching is not a semver comparison. ② is closed without a change.
```
### rui#26 — 控制台：空串麦克风增益写成 0 dB、401 弹窗反复、零 AbortController、日志滚动失效
- 结论：成立 3/3 + 2（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`web/src/api.ts:67-69` 的 401 全局事件、`web/src/App.vue:196` 的监听（无节流/无 AbortController）；空串增益在 v1 表单落 0 dB
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立 3/3 + 2（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`web/src/api.ts:67-69` 的 401 全局事件、`web/src/App.vue:196` 的监听（无节流/无 AbortController）；空串增益在 v1 表单落 0 dB。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid 3/3 + 2 (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: global 401 event in `web/src/api.ts:67-69` with no throttle or AbortController (`App.vue:196`); an empty mic gain is persisted as 0 dB by the v1 form. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#27 — 音频门的 cue 回声匹配会误切用户回复：非负包络余弦 + 0.75 门限没有选择性
- 结论：部分成立（triage P1）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/audio/vad.py:52` 的 `CUE_MATCH_MIN=0.75`、`:336` 的 `heard_any`；仅在 `ack.mode=tts` + 设备硬静音 + cue 尾 ~100 ms 内开口时触发
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立（triage P1）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/audio/vad.py:52` 的 `CUE_MATCH_MIN=0.75`、`:336` 的 `heard_any`；仅在 `ack.mode=tts` + 设备硬静音 + cue 尾 ~100 ms 内开口时触发。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：不采纳“去均值”修法：真实语音/回声场景会把回声分 0.855 降到 0.434，更低 SNR 甚至 -0.129，反而放大「在呢」泄漏；采用 `heard_any=False` 且命中在缓冲区内时不切除的小修。
```
#### English reply

```markdown
**Conclusion**: partially valid (P1).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `audio/vad.py:52` `CUE_MATCH_MIN=0.75` and `:336` `heard_any`; it only triggers with `ack.mode=tts`, device hard-mute, and speech within ~100 ms of the cue tail. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: the proposed de-mean fix is rejected: on real speech/echo it drops the echo score 0.855→0.434 and at lower SNR to -0.129, which is exactly the wake-word leakage it tries to prevent. The planned fix is the smaller “do not cut when `heard_any=False` and the hit lies inside the buffer” rule.
```
### rui#29 — 5xx 的错误响应是空白的：`_from_http` 只取 detail，全仓也没有 500 handler
- 结论：部分成立（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/web/api/v2_api.py:1634` 的 `_from_http`；裸异常没有 500 JSON handler（`HTTPException` 路径仍带 detail）
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/web/api/v2_api.py:1634` 的 `_from_http`；裸异常没有 500 JSON handler（`HTTPException` 路径仍带 detail）。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: partially valid (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `web/api/v2_api.py:1634` `_from_http`; bare exceptions have no 500 JSON handler (`HTTPException` still keeps its detail). `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#30 — 采集循环同步等仲裁，最长 0.9 s 不消费帧
- 结论：成立（triage P1）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/audio/process.py:794` 的 `wait_for(future, timeout=self._admit_timeout_s)` 与 `:844 _capture`；`multi_device.enabled` 默认 False，单设备不触发
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立（triage P1）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/audio/process.py:794` 的 `wait_for(future, timeout=self._admit_timeout_s)` 与 `:844 _capture`；`multi_device.enabled` 默认 False，单设备不触发。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid (P1).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `audio/process.py:794` waits for the arbitration verdict with `wait_for(future, timeout=self._admit_timeout_s)` and `:844 _capture` consumes frames; only when `multi_device.enabled` is on (default false). `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#31 — ring 满了丢新帧且从不追赶：一次突发之后永久晚 200 ms
- 结论：成立（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）
- 证据：`src/rui/audio/process.py:351-372` 的 ring 满策略与 overflow 计数（`:155`）；当前满后丢新帧，没有 drop-oldest/追赶
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1）。
**证据**：`src/rui/audio/process.py:351-372` 的 ring 满策略与 overflow 计数（`:155`）；当前满后丢新帧，没有 drop-oldest/追赶。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1).
**Evidence**: `audio/process.py:351-372` (ring-full policy and overflow counter, see also `:155`); the ring drops new frames after a burst and never catches up. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### rui#32 — `audio_defaults` 只对被扫到的插件根生效：pip/entry-point 布局下 dot2 会整机不出声
- 结论：部分成立（triage P2）：结构缺口成立，整机不出声的因果链不成立
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1（文档 + doctor））
- 证据：`src/rui/plugins/builtin.py:46-71` 的扫描根、`src/rui/audio/process.py:390` 的 `device_audio_defaults`
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立（triage P2）：结构缺口成立，整机不出声的因果链不成立。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1（文档 + doctor））。
**证据**：`src/rui/plugins/builtin.py:46-71` 的扫描根、`src/rui/audio/process.py:390` 的 `device_audio_defaults`。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：本仓不存在 `rs03qr.yaml`、不存在 `rate!=48000` 校验、lx06 manifest 无 playback 块，且 ack 窗口与全局同值；因此“dot2 整机不出声”的链条断了，实际影响是 capture 布局回落。0.8.1 只补插件根文档与 doctor 检查，不按 pip 语义改死。
```
#### English reply

```markdown
**Conclusion**: partially valid (P2): the structural gap is valid, the “dot2 is silent” chain is not.
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1 (docs + doctor)).
**Evidence**: scan roots in `plugins/builtin.py:46-71`, `device_audio_defaults` in `audio/process.py:390`. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: this repo has no `rs03qr.yaml`, no `rate!=48000` check, no playback block in the lx06 manifest, and the ack window matches the global value; the “dot2 is silent” chain does not hold. The real effect is a capture-layout fallback. 0.8.1 adds plugin-root docs and a doctor check; we will not hard-code pip/entry-point semantics.
```
### rui#33 — 一批小的但真实的缺陷（音频/DSP/渠道/控制面）
- 结论：21 成立 / 7 部分 / 1 误报 / 1 无缺陷（triage P2-P3）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1 + 0.8.2 + backlog）
- 证据：P30（`src/rui/channels/supervisor.py:83` token 进 argv）、P32（`src/rui/providers/xfyun_tts.py:49` api_key 进 URL）；其余 31 项按 `docs/audit/hermes-triage.md:60-65` 分批
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：21 成立 / 7 部分 / 1 误报 / 1 无缺陷（triage P2-P3）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 0.8.1 + 0.8.2 + backlog）。
**证据**：P30（`src/rui/channels/supervisor.py:83` token 进 argv）、P32（`src/rui/providers/xfyun_tts.py:49` api_key 进 URL）；其余 31 项按 `docs/audit/hermes-triage.md:60-65` 分批。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：A9 只是描述性内容，没有独立缺陷，按“无缺陷”关闭；其余子项的具体分批见 triage §3（22 项 0.8.1、听感类 0.8.2、10 项 backlog）。
```
#### English reply

```markdown
**Conclusion**: 21 valid / 7 partial / 1 false / 1 no-defect (P2-P3).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target 0.8.1 + 0.8.2 + backlog).
**Evidence**: P30 (`channels/supervisor.py:83`, token in argv) and P32 (`providers/xfyun_tts.py:49`, api_key in the URL); the remaining items are split in triage §3. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: A9 is descriptive only and has no independent defect, so it is closed as such. The per-item split is in triage §3 (22 items in 0.8.1, listening-related in 0.8.2, 10 in backlog).
```

---

## 3. rui-dot2-bridge（yes999zc/rui-dot2-bridge）

### dot2#2 — 没配 `DOT2_EM_TOKEN` 时，控制面在 0.0.0.0 上完全无鉴权
- 结论：部分成立（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2）
- 证据：`src/rui_dot2_bridge/device_link.py:92` 默认 `bind_host=0.0.0.0`；`:273-275` 无 token 时 `_authorized` 直接放行；`:255-269` 无 secrets 时 TLS context 直接拒绝启动
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2）。
**证据**：`src/rui_dot2_bridge/device_link.py:92` 默认 `bind_host=0.0.0.0`；`:273-275` 无 token 时 `_authorized` 直接放行；`:255-269` 无 secrets 时 TLS context 直接拒绝启动。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：不是“默认部署裸奔”：文档部署强制 token（README 与 deploy 指南），且完全没有 secrets 时服务因 TLS 材料缺失直接拒绝启动；真实暴露态是“有证书、忘配 token”。修复会让 fail-open 必须显式 `DOT2_ALLOW_OPEN_LINK=1`，并用 `compare_digest`。
```
#### English reply

```markdown
**Conclusion**: partially valid (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target dot2 1.3.2).
**Evidence**: `device_link.py:92` defaults to 0.0.0.0, `:273-275` allows all when the token is empty, and `:255-269` refuses to start without TLS secrets. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: it is not a default-deployment fail-open: the docs require a token, and with no secrets the service refuses to start because TLS material is missing. The real exposure is “certificate present, token forgotten”. The fix makes fail-open explicit via `DOT2_ALLOW_OPEN_LINK=1` and compares with `compare_digest`.
```
### dot2#3 — `/loudness` 配置零校验：一次 PUT 就能永久静音，重启不自愈
- 结论：成立（triage P2，原报 P0 降级）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2）
- 证据：`src/rui_dot2_bridge/control.py:95` 的 PUT 直接应用；`src/rui_dot2_bridge/dsp.py:518` 的 `trim_db` 没有 NaN/inf/范围校验；端点为 loopback-only 且默认关闭
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立（triage P2，原报 P0 降级）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2）。
**证据**：`src/rui_dot2_bridge/control.py:95` 的 PUT 直接应用；`src/rui_dot2_bridge/dsp.py:518` 的 `trim_db` 没有 NaN/inf/范围校验；端点为 loopback-only 且默认关闭。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid (P2, downgraded from P0).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target dot2 1.3.2).
**Evidence**: `control.py:95` applies the PUT directly and `dsp.py:518` applies `trim_db` without NaN/inf/range checks; the endpoint is loopback-only and off by default. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
### dot2#4 — 设备链路的八处状态机缺陷：双连接、握手尾部、畸形消息、共享锁、丢 80ms、play/stop 竞态
- 结论：部分成立 7/8（triage P1）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2（2/3/6）+ 1.3.3（1/4/7/8））
- 证据：`src/rui_dot2_bridge/device_link.py:210/222` ping、`:588` 音频超时、`:640` barrier；第 5 条无修复
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立 7/8（triage P1）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2（2/3/6）+ 1.3.3（1/4/7/8））。
**证据**：`src/rui_dot2_bridge/device_link.py:210/222` ping、`:588` 音频超时、`:640` barrier；第 5 条无修复。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：第 5 条（丢 80 ms）不成立：按 triage 的复现，对超时分支注入 30,000 次，0 次丢帧（`docs/audit/hermes-triage.md:45`）；timeout 分支清 waiters，不清音频队列。第 8 条「20 Hz 死转」在本仓不可达。
```
#### English reply

```markdown
**Conclusion**: partially valid: items 5 and 8 do not hold, the rest is accepted (P1).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target dot2 1.3.2 (2/3/6) + 1.3.3 (1/4/7/8)).
**Evidence**: `device_link.py:210/222` ping, `:588` audio timeout, `:640` barrier. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: item 5 (the 80 ms loss) does not hold: the triage run injected 30,000 timeouts and lost 0 frames (`docs/audit/hermes-triage.md:45`); the timeout branch clears waiters, not the audio queue. Item 8 (“20 Hz spin”) is unreachable in this repo.
```
### dot2#5 — DSP 播放期切档爆音、`PUT /loudness` 掉音 11 ms、全新部署整条曲线是平直
- 结论：部分成立 4/5（triage P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2（1/2/5）+ 1.3.3（3））
- 证据：`src/rui_dot2_bridge/dsp.py:518` 的 trim 应用；切档跳变 0.6151（24.3×）与 `PUT /loudness` 250 个零样本（5.2 ms）已复现；第 4 条需要 vendor cfg 才能“corrected”
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：部分成立 4/5（triage P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2（1/2/5）+ 1.3.3（3））。
**证据**：`src/rui_dot2_bridge/dsp.py:518` 的 trim 应用；切档跳变 0.6151（24.3×）与 `PUT /loudness` 250 个零样本（5.2 ms）已复现；第 4 条需要 vendor cfg 才能“corrected”。`git log v0.7.4..v0.7.8` 无对应修复 commit。
**反证**：第 4 条不是代码缺陷：全新 clone 没有 vendor 曲线，必须由部署侧提供 `vendor cfg` 才能得到 corrected 曲线；已列 backlog，不在 1.3.2 假装修好。
```
#### English reply

```markdown
**Conclusion**: partially valid 4/5 (P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target dot2 1.3.2 (1/2/5) + 1.3.3 (3)).
**Evidence**: `dsp.py:518` trim application; the 0.6151 switch jump (24.3x) and 250 zero samples on `PUT /loudness` (5.2 ms) were reproduced; item 4 needs a vendor curve. `git log v0.7.4..v0.7.8` has no fix commit for it.
**Counter-evidence**: item 4 is not a code defect: a fresh clone has no vendor curve, and the corrected curve requires the deployment's vendor cfg. It is backlog, not something 1.3.2 will pretend to fix.
```
### dot2#6 — 交付与运维面：`rm -rf` 无校验、doctor 假绿、契约报告过期、端口是死旋钮
- 结论：成立（大多 P2/P3，triage P2/P3）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2 quick wins / 1.3.3 / 1.4.0）
- 证据：`scripts/make_deploy_bundle.sh:16` 的 trap `rm -rf $WORK`、`scripts/build_p3_firmware.sh:27`、`src/rui_dot2_bridge/adapter.py:315` 的 `DOT2_CONTROL_PORT`
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立（大多 P2/P3，triage P2/P3）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 dot2 1.3.2 quick wins / 1.3.3 / 1.4.0）。
**证据**：`scripts/make_deploy_bundle.sh:16` 的 trap `rm -rf $WORK`、`scripts/build_p3_firmware.sh:27`、`src/rui_dot2_bridge/adapter.py:315` 的 `DOT2_CONTROL_PORT`。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid (mostly P2/P3).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target dot2 1.3.2 quick wins / 1.3.3 / 1.4.0).
**Evidence**: `scripts/make_deploy_bundle.sh:16` trap `rm -rf $WORK`, `scripts/build_p3_firmware.sh:27`, and `adapter.py:315` `DOT2_CONTROL_PORT`. `git log v0.7.4..v0.7.8` has no fix commit for it.
```

---

## 4. rui-xiaoai-bridge（yes999zc/rui-xiaoai-bridge）

### xiaoai#1 — 连接替换时不复位播放器状态：重连后的整首歌静音，还被当成「播完了」
- 结论：成立（duplicate of rui#25，triage P1）
- 处置：已修：xiaoai 0.2.2，commit `8f3ac46`；tag `749d4d3`（与 rui 0.7.4 `4468521` 同一修复）
- 证据：`src/rui_xiaoai_bridge/adapter.py:273` `_reset_media_state()`、`:256-264` 连接/代际守卫；`tests/test_reconnect_reset.py`；`git -C ../rui-xiaoai-bridge log --oneline v0.2.1..v0.2.2` → `8f3ac46`、`749d4d3`
- 建议动作：close（duplicate of yes999zc/rui#25）
#### 中文回复

```markdown
**结论**：duplicate of yes999zc/rui#25；同一 bug 已修复。
**证据**：xiaoai 0.2.2（commit `8f3ac46`，tag `749d4d3`）在连接替换时调用 `_reset_media_state()`（`adapter.py:273`），旧 socket 的 `play()`/consumed 事件被代际守卫拦住（`adapter.py:256-264`）；对应 rui 修复见 rui#25（0.7.4 `4468521`）。
**动作**：请关闭本 issue，并在 rui#25 留链接。
```
#### English reply

```markdown
**Conclusion**: duplicate of yes999zc/rui#25; the same bug is fixed.
**Evidence**: xiaoai 0.2.2 (commit `8f3ac46`, tag `749d4d3`) calls `_reset_media_state()` when the connection is replaced (`adapter.py:273`), and the generation/connection guards stop the old socket's `play()`/consumed events (`adapter.py:256-264`). The rui side is rui#25 (0.7.4 `4468521`).
**Action**: close this issue as duplicate and link rui#25.
```
### xiaoai#2 — 一批真实缺陷：Response.code 解析、token 解析、并发 play、frames 不结束、音量假成功
- 结论：成立 10/10（triage P1/P2）
- 处置：未修：不在 0.7.4 / 0.7.8 范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 xiaoai 0.2.3）
- 证据：`src/rui_xiaoai_bridge/adapter.py:440` 的 `parse_qs` 会把 base64 token 的 `+` 变成空格（→1008）；`:220` 的 `frames()` 永不结束；0.2.2 只含 #1 的重连复位，不含本批
- 建议动作：close（wontfix / deferred，理由见处置）
#### 中文回复

```markdown
**结论**：成立 10/10（triage P1/P2）。
**处置**：未在 0.7.4 / 0.7.8 修复，不在本周期范围；已登记 `docs/ROADMAP-0.8.md` §8（计划 xiaoai 0.2.3）。
**证据**：`src/rui_xiaoai_bridge/adapter.py:440` 的 `parse_qs` 会把 base64 token 的 `+` 变成空格（→1008）；`:220` 的 `frames()` 永不结束；0.2.2 只含 #1 的重连复位，不含本批。`git log v0.7.4..v0.7.8` 无对应修复 commit。
```
#### English reply

```markdown
**Conclusion**: valid 10/10 (P1/P2).
**Disposition**: not fixed in 0.7.4 / 0.7.8 and out of this cycle; tracked in `docs/ROADMAP-0.8.md` §8 (target xiaoai 0.2.3).
**Evidence**: `adapter.py:440` `parse_qs` turns a base64 token's `+` into a space (→1008); `:220` `frames()` never ends; 0.2.2 only carries the #1 reconnect reset. `git log v0.7.4..v0.7.8` has no fix commit for it.
```
